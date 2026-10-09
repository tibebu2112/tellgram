"""Real-time Student Union complaints dashboard.

Reads live data with st.connection from Google Sheets or a SQL database.
Falls back to complaints.csv only when those connections are not configured.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from data_manager import COLUMNS, CSV_PATH, backend_name, load_secrets

st.set_page_config(
    page_title="Union Student Complaints",
    page_icon="🎓",
    layout="wide",
)

REFRESH_SECONDS = 8


def _has_gsheets_secrets() -> bool:
    secrets = load_secrets()
    try:
        block = st.secrets.get("connections", {}).get("gsheets", {})
    except Exception:
        block = secrets.get("connections", {}).get("gsheets", {})
    return bool(block.get("spreadsheet"))


def _has_sql_secrets() -> bool:
    secrets = load_secrets()
    try:
        block = st.secrets.get("connections", {}).get("complaints_db", {})
    except Exception:
        block = secrets.get("connections", {}).get("complaints_db", {})
    return bool(block.get("url") or block.get("dialect"))


def _from_csv() -> pd.DataFrame:
    if not CSV_PATH.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=COLUMNS)
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[COLUMNS]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = ""
    df = df[COLUMNS].fillna("")
    df = df[df["id"].astype(str).str.strip() != ""]
    return df.reset_index(drop=True)


def load_live_complaints() -> tuple[pd.DataFrame, str]:
    """Prefer Google Sheets or SQL via st.connection — not a static sample CSV."""
    if _has_gsheets_secrets():
        from streamlit_gsheets import GSheetsConnection

        conn = st.connection("gsheets", type=GSheetsConnection)
        kwargs: dict = {"ttl": REFRESH_SECONDS}
        try:
            worksheet = st.secrets["connections"]["gsheets"].get("worksheet")
        except Exception:
            worksheet = None
        if worksheet and not str(worksheet).isdigit():
            kwargs["worksheet"] = worksheet
        df = conn.read(**kwargs)
        return _clean(df), "Google Sheets (st.connection)"

    if _has_sql_secrets():
        conn = st.connection("complaints_db", type="sql")
        df = conn.query(
            "SELECT * FROM complaints ORDER BY created_at DESC",
            ttl=REFRESH_SECONDS,
        )
        return _clean(df), "Database (st.connection)"

    return _from_csv(), f"CSV fallback ({backend_name()})"


st.title("🎓 Student Union Complaint Dashboard")
st.caption("ቴሌግራም ቦት የተቀበላቸው የተማሪዎች ቅሬታዎች — real-time view")

auto = st.sidebar.toggle("Auto-refresh (real-time)", value=True)
st.sidebar.markdown(
    "This app reads **Google Sheets** or a **database** with "
    "`st.connection`. `complaints.csv` is only a fallback, not sample data."
)


def render_dashboard() -> None:
    df, source = load_live_complaints()
    df = df.copy()
    df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)

    st.sidebar.success(f"Data source: {source}")

    departments = ["All"] + sorted([x for x in df["department"].dropna().unique() if x])
    categories = ["All"] + sorted([x for x in df["category"].dropna().unique() if x])
    statuses = ["All"] + sorted([x for x in df["status"].dropna().unique() if x])
    urgencies = ["All"] + sorted([x for x in df["urgency"].dropna().unique() if x])

    department = st.sidebar.selectbox("Department", departments)
    category = st.sidebar.selectbox("Category", categories)
    status = st.sidebar.selectbox("Status", statuses)
    urgency = st.sidebar.selectbox("Urgency", urgencies)
    search = st.sidebar.text_input("Search name / ID / text")

    filtered = df
    if department != "All":
        filtered = filtered[filtered["department"] == department]
    if category != "All":
        filtered = filtered[filtered["category"] == category]
    if status != "All":
        filtered = filtered[filtered["status"] == status]
    if urgency != "All":
        filtered = filtered[filtered["urgency"] == urgency]
    if search.strip():
        needle = search.strip().lower()
        mask = (
            filtered["full_name"].str.lower().str.contains(needle, na=False)
            | filtered["student_id"].str.lower().str.contains(needle, na=False)
            | filtered["complaint"].str.lower().str.contains(needle, na=False)
            | filtered["id"].str.lower().str.contains(needle, na=False)
        )
        filtered = filtered[mask]

    today = pd.Timestamp.now(tz="UTC").normalize()
    open_count = int((df["status"].str.lower() == "open").sum()) if not df.empty else 0
    today_count = (
        int((df["created_at_dt"].dt.normalize() == today).sum()) if not df.empty else 0
    )
    high_count = (
        int(df["urgency"].str.lower().isin(["high", "emergency"]).sum()) if not df.empty else 0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total complaints", len(df))
    c2.metric("Open", open_count)
    c3.metric("Today", today_count)
    c4.metric("High / Emergency", high_count)

    if filtered.empty:
        st.info("No complaints yet. Start the Telegram bot and send /start to file one.")
        return

    left, right = st.columns(2)
    by_category = filtered.groupby("category", dropna=False).size().reset_index(name="count")
    fig_cat = px.bar(
        by_category,
        x="category",
        y="count",
        title="Complaints by category",
        color="category",
    )
    fig_cat.update_layout(showlegend=False)
    left.plotly_chart(fig_cat, use_container_width=True)

    dated = filtered.dropna(subset=["created_at_dt"]).copy()
    if dated.empty:
        right.info("No dated rows to chart yet.")
    else:
        dated["day"] = dated["created_at_dt"].dt.date
        daily = dated.groupby("day").size().reset_index(name="count")
        fig_time = px.line(daily, x="day", y="count", markers=True, title="Complaints over time")
        right.plotly_chart(fig_time, use_container_width=True)

    urgency_pie = filtered.groupby("urgency", dropna=False).size().reset_index(name="count")
    fig_pie = px.pie(urgency_pie, names="urgency", values="count", title="Urgency mix")
    st.plotly_chart(fig_pie, use_container_width=True)

    st.subheader("Live complaint feed")
    display = filtered.sort_values("created_at", ascending=False)[
        [
            "id",
            "created_at",
            "full_name",
            "student_id",
            "department",
            "category",
            "urgency",
            "status",
            "complaint",
            "telegram_username",
        ]
    ]
    st.dataframe(display, use_container_width=True, hide_index=True)


interval = timedelta(seconds=REFRESH_SECONDS) if auto else None
if auto:
    st.sidebar.caption(f"Refreshing every {REFRESH_SECONDS} seconds.")
else:
    st.sidebar.button("Refresh now")


@st.fragment(run_every=interval)
def live_view() -> None:
    render_dashboard()


live_view()

st.markdown("---")
st.markdown(
    "Bot: run `python bot.py` on an always-on computer. "
    "Dashboard: deploy `app.py` on [Streamlit Cloud](https://share.streamlit.io)."
)
