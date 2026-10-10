"""Student Union complaints dashboard + Telegram bot (Streamlit Cloud)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from bot import BOT_URL, BOT_USERNAME, bot_status, start_bot_background
from data_manager import COLUMNS, CSV_PATH, load_secrets

st.set_page_config(
    page_title="Union Student Complaints",
    page_icon="🎓",
    layout="wide",
)

REFRESH_SECONDS = 10


def _secret_block(name: str) -> dict:
    try:
        block = st.secrets.get("connections", {}).get(name, {})
        return dict(block) if block else {}
    except Exception:
        return dict(load_secrets().get("connections", {}).get(name, {}))


def _has_gsheets_secrets() -> bool:
    block = _secret_block("gsheets")
    spreadsheet = str(block.get("spreadsheet") or "")
    if not spreadsheet:
        return False
    dummy = ("YOUR_SHEET_ID" in spreadsheet) or ("xxxxxxx" in spreadsheet)
    return not dummy


def _has_sql_secrets() -> bool:
    block = _secret_block("complaints_db")
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
    if _has_gsheets_secrets():
        try:
            from streamlit_gsheets import GSheetsConnection

            conn = st.connection("gsheets", type=GSheetsConnection)
            kwargs: dict = {"ttl": REFRESH_SECONDS}
            worksheet = _secret_block("gsheets").get("worksheet")
            if worksheet and not str(worksheet).isdigit():
                kwargs["worksheet"] = worksheet
            return _clean(conn.read(**kwargs)), "Google Sheets (st.connection)"
        except Exception as exc:
            st.sidebar.warning(f"Google Sheets read failed, using CSV. ({exc})")

    if _has_sql_secrets():
        try:
            conn = st.connection("complaints_db", type="sql")
            df = conn.query(
                "SELECT * FROM complaints ORDER BY created_at DESC",
                ttl=REFRESH_SECONDS,
            )
            return _clean(df), "Database (st.connection)"
        except Exception as exc:
            st.sidebar.warning(f"Database read failed, using CSV. ({exc})")

    return _from_csv(), "CSV"


bot_state = start_bot_background()
status, bot_error = bot_status()

st.title("🎓 የተማሪዎች ህብረት ቅሬታ ዳሽቦርድ")
st.caption("Student Union complaints — Telegram + Streamlit")

st.info(
    f"**ቦቱን ለመሞከር:** Telegram ክፈት → [@{BOT_USERNAME}]({BOT_URL}) ፈልግ → `/start` ላክ።\n\n"
    "1. Streamlit Cloud → **Manage app** → **⋮** → **Settings** → **Secrets** ላይ "
    "`TELEGRAM_BOT_TOKEN` አስገባ።\n"
    "2. **Main file path** `app.py` መሆኑን አረጋግጥ (bot.py አይደለም)።\n"
    "3. Save / Reboot app። ከዚያ ዳሽቦርዱ ጥቁር/ባዶ አይሆንም፣ ቦቱም ይመልሳል።"
)

col_a, col_b = st.columns(2)
col_a.metric("Telegram bot", f"@{BOT_USERNAME}")
col_b.metric("Bot status", status)
if bot_state == "missing_token" or status == "missing_token":
    st.error(
        "Token የለም። Streamlit Cloud Secrets ውስጥ ይህን ጨምር:\n\n"
        "TELEGRAM_BOT_TOKEN = \"your-token-from-BotFather\""
    )
elif bot_error:
    st.warning(bot_error)

st.link_button("Open Telegram bot", BOT_URL)

auto = st.sidebar.toggle("Auto-refresh", value=True)
if auto:
    st.sidebar.caption(f"Every {REFRESH_SECONDS} seconds")

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
status_filter = st.sidebar.selectbox("Status", statuses)
urgency = st.sidebar.selectbox("Urgency", urgencies)
search = st.sidebar.text_input("Search")

filtered = df
if department != "All":
    filtered = filtered[filtered["department"] == department]
if category != "All":
    filtered = filtered[filtered["category"] == category]
if status_filter != "All":
    filtered = filtered[filtered["status"] == status_filter]
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
today_count = int((df["created_at_dt"].dt.normalize() == today).sum()) if not df.empty else 0
high_count = int(df["urgency"].str.lower().isin(["high", "emergency"]).sum()) if not df.empty else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total", len(df))
c2.metric("Open", open_count)
c3.metric("Today", today_count)
c4.metric("High / Emergency", high_count)

if filtered.empty:
    st.warning("ገና ቅሬታ የለም። በስልክህ Telegram ክፈት፣ @unionstudent_complaints_bot ላይ /start ላክ።")
else:
    left, right = st.columns(2)
    by_category = filtered.groupby("category", dropna=False).size().reset_index(name="count")
    fig_cat = px.bar(by_category, x="category", y="count", title="By category", color="category")
    fig_cat.update_layout(showlegend=False)
    left.plotly_chart(fig_cat, use_container_width=True)

    dated = filtered.dropna(subset=["created_at_dt"]).copy()
    if dated.empty:
        right.info("No dates yet.")
    else:
        dated["day"] = dated["created_at_dt"].dt.date
        daily = dated.groupby("day").size().reset_index(name="count")
        right.plotly_chart(
            px.line(daily, x="day", y="count", markers=True, title="Over time"),
            use_container_width=True,
        )

    st.plotly_chart(
        px.pie(
            filtered.groupby("urgency", dropna=False).size().reset_index(name="count"),
            names="urgency",
            values="count",
            title="Urgency",
        ),
        use_container_width=True,
    )
    st.subheader("Live feed")
    st.dataframe(
        filtered.sort_values("created_at", ascending=False)[
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
        ],
        use_container_width=True,
        hide_index=True,
    )

if auto:
    st.markdown(
        f"<meta http-equiv='refresh' content='{REFRESH_SECONDS}'>",
        unsafe_allow_html=True,
    )
