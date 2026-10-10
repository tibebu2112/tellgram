"""BDU Student Union complaints dashboard + Telegram bot."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from bot import BOT_URL, BOT_USERNAME, bot_status, start_bot_background
from constants import CAMPUS_COORDS, PRIORITY_AM, STATUS_AM
from data_manager import COLUMNS, CSV_PATH, load_secrets, update_complaint

st.set_page_config(
    page_title="BDU የተማሪዎች ህብረት ቅሬታ ዳሽቦርድ",
    page_icon="🎓",
    layout="wide",
)

REFRESH_SECONDS = 10

st.markdown(
    """
    <style>
    html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
        background: #f4f7fb !important;
        color: #0f172a !important;
    }
    .block-container { padding-top: 1.2rem; color: #0f172a !important; }
    h1, h2, h3, h4, p, label, span, li, .stMarkdown, .stCaption, [data-testid="stWidgetLabel"] {
        color: #0f172a !important;
    }
    [data-testid="stSidebar"] {
        background: #ffffff !important;
        color: #0f172a !important;
        border-right: 1px solid #d7dee8;
    }
    [data-testid="stSidebar"] * { color: #0f172a !important; }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #c5d0de;
        border-radius: 12px;
        padding: 12px 14px;
        color: #0f172a !important;
    }
    div[data-testid="stMetric"] label, div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #0f172a !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-weight: 700;
        font-size: 1.35rem;
    }
    [data-testid="stDataFrame"], [data-testid="stDataEditor"] {
        color: #0f172a !important;
        background: #ffffff !important;
    }
    .stAlert, .stExpander { color: #0f172a !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


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
    if "complaint" in df.columns and df["description"].eq("").all():
        df["description"] = df["complaint"]
    return df[COLUMNS]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    if "complaint" in df.columns and "description" not in df.columns:
        df["description"] = df["complaint"]
    if "urgency" in df.columns and "priority" not in df.columns:
        df["priority"] = df["urgency"]
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
            return _clean(conn.read(**kwargs)), "Google Sheets"
        except Exception as exc:
            st.sidebar.warning(f"Google Sheets read failed, using CSV. ({exc})")

    if _has_sql_secrets():
        try:
            conn = st.connection("complaints_db", type="sql")
            df = conn.query(
                "SELECT * FROM complaints ORDER BY created_at DESC",
                ttl=REFRESH_SECONDS,
            )
            return _clean(df), "Database"
        except Exception as exc:
            st.sidebar.warning(f"Database read failed, using CSV. ({exc})")

    return _from_csv(), "CSV / SQLite fallback"


bot_state = start_bot_background()
status, bot_error = bot_status()

st.title("🎓 የባህር ዳር ዩኒቨርሲቲ የተማሪዎች ህብረት")
st.caption(
    "ለዋና ሥራ አስፈጻሚ እና የክፍል ኃላፊዎች — ዳሽቦርዱን በማንኛውም ጊዜ ይክፈቱ። "
    "ተማሪዎች 24/7 በቦቱ ቅሬታ ያስገባሉ። Telegram Bot → Database → Streamlit"
)

with st.expander("ቦቱን እንዴት እንደሚሞከር / How to submit", expanded=False):
    st.markdown(
        f"Telegram ክፈት → [@{BOT_USERNAME}]({BOT_URL}) → `/start` ወይም `/new`\n\n"
        "1. ካምፓስ (ፔዳ፣ ፖሊ/BiT፣ ዘንዘልማ፣ ሰባታሚት፣ ጥበበ ጊዮን፣ ይባቤ፣ ሰላም)  "
        "2. ዘርፍ  3. መግለጫ + ማስረጃ  4. ስም ወይም ምስጢር"
    )
    st.link_button("Open Telegram bot", BOT_URL)

col_a, col_b, col_c = st.columns(3)
col_a.metric("Telegram bot", f"@{BOT_USERNAME}")
col_b.metric("Bot status", status)
col_c.metric("ዳሽቦርድ", "ሁልጊዜ ክፍት / Always on")
if bot_state == "missing_token" or status == "missing_token":
    st.error("Token የለም። Streamlit Cloud Secrets ውስጥ `TELEGRAM_BOT_TOKEN` ያስገቡ።")
elif bot_error:
    st.warning(bot_error)

st.sidebar.markdown("### የህብረት አመራር")
st.sidebar.caption("ይህን ገጽ በማንኛውም ሰዓት መክፈት ይችላሉ። ለአዲስ ቅሬታ የአሳሽ Refresh ይጫኑ።")

df, source = load_live_complaints()
df = df.copy()
df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
st.sidebar.success(f"Data source: {source}")

campuses = ["All"] + sorted([x for x in df["campus"].dropna().unique() if x])
categories = ["All"] + sorted([x for x in df["category"].dropna().unique() if x])
statuses = ["All"] + sorted([x for x in df["status"].dropna().unique() if x])
priorities = ["All"] + sorted([x for x in df["priority"].dropna().unique() if x])

campus = st.sidebar.selectbox("ካምፓስ / Campus", campuses)
category = st.sidebar.selectbox("ዘርፍ / Category", categories)
status_filter = st.sidebar.selectbox("ሁኔታ / Status", statuses)
priority = st.sidebar.selectbox("ቅድሚያ / Priority", priorities)
search = st.sidebar.text_input("Search ticket, location, text")

filtered = df
if campus != "All":
    filtered = filtered[filtered["campus"] == campus]
if category != "All":
    filtered = filtered[filtered["category"] == category]
if status_filter != "All":
    filtered = filtered[filtered["status"] == status_filter]
if priority != "All":
    filtered = filtered[filtered["priority"] == priority]
if search.strip():
    needle = search.strip().lower()
    mask = (
        filtered["id"].str.lower().str.contains(needle, na=False)
        | filtered["description"].str.lower().str.contains(needle, na=False)
        | filtered["location"].str.lower().str.contains(needle, na=False)
        | filtered["full_name"].str.lower().str.contains(needle, na=False)
        | filtered["campus"].str.lower().str.contains(needle, na=False)
    )
    filtered = filtered[mask]

status_norm = df["status"].astype(str)
resolved_count = int(status_norm.str.lower().isin(["resolved", "ተፈትቷል"]).sum()) if not df.empty else 0
progress_count = int(status_norm.str.lower().isin(["in progress", "በሂደት ላይ"]).sum()) if not df.empty else 0
high_count = int(df["priority"].astype(str).str.lower().eq("high").sum()) if not df.empty else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("ጠቅላላ የተመዘገቡ ቅሬታዎች", len(df))
m2.metric("የተፈቱ ቅሬታዎች", resolved_count)
m3.metric("በሂደት ላይ ያሉ", progress_count)
m4.metric("አጣዳፊ ችግሮች", high_count)

if filtered.empty:
    st.warning("ገና ቅሬታ የለም። በስልክ Telegram ክፈት፣ ቦቱ ላይ /start ላክ።")
else:
    st.subheader("የመረጃ ምስላዊ መግለጫዎች")
    c1, c2 = st.columns(2)
    by_campus = filtered.groupby("campus", dropna=False).size().reset_index(name="count")
    fig_campus = px.bar(
        by_campus,
        x="campus",
        y="count",
        color="campus",
        title="የካምፓስ ንጽጽር",
    )
    fig_campus.update_layout(
        showlegend=False,
        xaxis_title="",
        yaxis_title="ቅሬታዎች",
        font_color="#0f172a",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
    )
    c1.plotly_chart(fig_campus, use_container_width=True)

    by_cat = filtered.groupby("category", dropna=False).size().reset_index(name="count")
    fig_pie = px.pie(
        by_cat,
        names="category",
        values="count",
        hole=0.45,
        title="የችግሮች ስርጭት",
    )
    fig_pie.update_layout(font_color="#0f172a", paper_bgcolor="#ffffff")
    c2.plotly_chart(fig_pie, use_container_width=True)

    dated = filtered.dropna(subset=["created_at_dt"]).copy()
    map_left, map_right = st.columns(2)
    if dated.empty:
        map_left.info("የጊዜ መረጃ ገና የለም።")
    else:
        dated["day"] = dated["created_at_dt"].dt.date
        daily = dated.groupby("day").size().reset_index(name="count")
        fig_line = px.line(daily, x="day", y="count", markers=True, title="የጊዜ ገጽታ (በቀን)")
        fig_line.update_layout(font_color="#0f172a", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff")
        map_left.plotly_chart(fig_line, use_container_width=True)

    map_rows = []
    for campus_name, count in filtered.groupby("campus").size().items():
        coords = CAMPUS_COORDS.get(str(campus_name))
        if coords:
            map_rows.append(
                {"campus": campus_name, "count": int(count), "lat": coords[0], "lon": coords[1]}
            )
    if map_rows:
        map_df = pd.DataFrame(map_rows)
        try:
            fig_map = px.scatter_mapbox(
                map_df,
                lat="lat",
                lon="lon",
                size="count",
                color="campus",
                hover_name="campus",
                hover_data={"count": True, "lat": False, "lon": False},
                zoom=11,
                title="የካምፓስ ካርታ",
                height=420,
            )
            fig_map.update_layout(mapbox_style="open-street-map", margin=dict(l=0, r=0, t=40, b=0))
            map_right.plotly_chart(fig_map, use_container_width=True)
        except Exception:
            map_right.plotly_chart(
                px.bar(map_df, x="campus", y="count", title="የካምፓስ ካርታ (መጠባበቂያ)"),
                use_container_width=True,
            )
    else:
        map_right.info("ካምፓስ መጋጠሚያ የለም።")

    st.subheader("የቅሬታዎች መቆጣጠሪያ ሠንጠረዥ")
    table = filtered.sort_values("created_at", ascending=False).copy()
    table["ሁኔታ"] = table["status"].map(lambda x: STATUS_AM.get(str(x), str(x)))
    table["ደረጃ"] = table["priority"].map(lambda x: PRIORITY_AM.get(str(x), str(x)))
    table["ማንነት"] = table["is_anonymous"].map(
        lambda x: "🕵 ምስጢር" if str(x).lower() in {"yes", "true", "1"} else "👤 የተገለጸ"
    )
    display_cols = [
        "id",
        "campus",
        "category",
        "ሁኔታ",
        "ደረጃ",
        "status",
        "priority",
        "action",
        "description",
        "location",
        "ማንነት",
        "full_name",
        "phone",
        "department",
        "created_at",
    ]
    editor = st.data_editor(
        table[display_cols],
        use_container_width=True,
        hide_index=True,
        disabled=["id", "campus", "category", "ሁኔታ", "ደረጃ", "description", "location", "ማንነት", "full_name", "phone", "department", "created_at"],
        column_config={
            "id": "Ticket ID",
            "campus": "ካምፓስ",
            "category": "ዘርፍ",
            "status": st.column_config.SelectboxColumn("ሁኔታ (እርምጃ)", options=["Open", "In Progress", "Resolved"]),
            "priority": st.column_config.SelectboxColumn("ቅድሚያ", options=["High", "Medium", "Low"]),
            "action": "እርምጃ (Action)",
            "description": "መግለጫ",
            "location": "ቦታ",
        },
        key="complaints_editor",
    )

    original = table.set_index("id")[["status", "priority", "action"]]
    edited = editor.set_index("id")[["status", "priority", "action"]]
    changed_ids = [
        ticket_id
        for ticket_id in edited.index
        if any(
            str(original.loc[ticket_id, col]) != str(edited.loc[ticket_id, col])
            for col in ("status", "priority", "action")
        )
    ]
    if changed_ids:
        for ticket_id in changed_ids:
            update_complaint(
                str(ticket_id),
                status=str(edited.loc[ticket_id, "status"]),
                priority=str(edited.loc[ticket_id, "priority"]),
                action=str(edited.loc[ticket_id, "action"]),
            )
        st.success("የሁኔታ/ቅድሚያ/እርምጃ ለውጦች ተቀምጠዋል።")
        st.rerun()
