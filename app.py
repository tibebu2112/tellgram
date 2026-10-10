"""BDU Student Union complaints dashboard + Telegram bot."""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from bot import BOT_URL, BOT_USERNAME, bot_status, start_bot_background
from constants import CAMPUS_COORDS, PRIORITY_AM, STATUS_AM, STATUSES
from data_manager import COLUMNS, CSV_PATH, EVIDENCE_DIR, load_secrets, update_complaint

st.set_page_config(
    page_title="BDU የተማሪዎች ህብረት ቅሬታ ዳሽቦርድ",
    page_icon="🎓",
    layout="wide",
)

REFRESH_SECONDS = 10

# ── Global CSS: force light theme on every widget ──────────────────────────────
st.markdown(
    """
    <style>
    /* ═══ PAGE BASE ═══ */
    html, body,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stMain"],
    .main {
        background: #f0f4fb !important;
        color: #0a0e1a !important;
    }
    .block-container { padding-top: 1.2rem; }

    /* ═══ ALL TEXT ═══ */
    h1, h2, h3, h4, h5, h6,
    p, label, span, li, div,
    .stMarkdown, .stCaption,
    [data-testid="stWidgetLabel"],
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li {
        color: #0a0e1a !important;
    }
    h1 { font-size: 1.95rem !important; font-weight: 800 !important; }
    h2 { font-size: 1.45rem !important; font-weight: 700 !important; }
    h3 { font-size: 1.2rem  !important; font-weight: 700 !important; }

    /* ═══ SIDEBAR ═══ */
    [data-testid="stSidebar"] {
        background: #ffffff !important;
        border-right: 2px solid #c5d0de;
    }
    [data-testid="stSidebar"] * { color: #0a0e1a !important; }

    /* ═══ METRIC CARDS ═══ */
    div[data-testid="stMetric"] {
        background: #ffffff !important;
        border: 2px solid #2563eb !important;
        border-radius: 14px !important;
        padding: 16px 18px !important;
    }
    div[data-testid="stMetric"] label {
        color: #1e3a5f !important;
        font-size: 0.9rem  !important;
        font-weight: 600   !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #1e3a5f !important;
        font-weight: 800   !important;
        font-size: 1.75rem !important;
    }

    /* ═══ SELECT-BOX ═══ */
    div[data-testid="stSelectbox"] > div > div,
    div[data-testid="stSelectbox"] input,
    div[data-testid="stSelectbox"] span {
        background: #ffffff !important;
        color: #0a0e1a !important;
    }
    /* the dropdown list */
    div[data-baseweb="popover"] li,
    div[data-baseweb="popover"] [data-testid="stSelectboxOption"],
    div[data-baseweb="menu"] li {
        background: #ffffff !important;
        color: #0a0e1a !important;
    }
    div[data-baseweb="popover"] li:hover,
    div[data-baseweb="menu"] li:hover {
        background: #e8eef8 !important;
    }

    /* ═══ TEXT INPUT ═══ */
    div[data-testid="stTextInput"] input,
    div[data-testid="stTextInput"] > div {
        background: #ffffff !important;
        color: #0a0e1a !important;
        border: 1.5px solid #94a3b8 !important;
        border-radius: 8px !important;
    }
    div[data-testid="stTextInput"] input::placeholder {
        color: #64748b !important;
    }

    /* ═══ TEXT AREA ═══ */
    div[data-testid="stTextArea"] textarea,
    div[data-testid="stTextArea"] > div {
        background: #ffffff !important;
        color: #0a0e1a !important;
        border: 1.5px solid #94a3b8 !important;
        border-radius: 8px !important;
    }
    div[data-testid="stTextArea"] textarea::placeholder {
        color: #64748b !important;
    }

    /* ═══ TABS ═══ */
    [data-testid="stTabs"] button {
        color: #1e3a5f !important;
        font-size: 1rem  !important;
        font-weight: 600 !important;
    }
    [data-testid="stTabs"] button[aria-selected="true"] {
        border-bottom: 3px solid #2563eb !important;
        color: #2563eb !important;
    }

    /* ═══ BUTTONS ═══ */
    .stButton > button {
        background: #2563eb !important;
        color: #ffffff    !important;
        border: none      !important;
        border-radius: 8px !important;
        font-size: 1rem   !important;
        font-weight: 700  !important;
        padding: 0.55rem 1.4rem !important;
    }
    .stButton > button:hover { background: #1d4ed8 !important; }

    /* ═══ ALERTS / EXPANDERS ═══ */
    [data-testid="stAlert"] p  { color: #0a0e1a !important; font-size: 1rem !important; }
    [data-testid="stAlert"]    { color: #0a0e1a !important; }
    .stExpander                { color: #0a0e1a !important; }

    /* ═══ DATA TABLE ═══ */
    [data-testid="stDataFrame"],
    [data-testid="stDataEditor"] {
        background: #ffffff !important;
        color: #0a0e1a !important;
        border: 1px solid #c5d0de;
        border-radius: 10px;
    }

    /* ═══ PROGRESS BAR ═══ */
    [data-testid="stProgressBar"] > div > div {
        background: #2563eb !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

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
    return not (("YOUR_SHEET_ID" in spreadsheet) or ("xxxxxxx" in spreadsheet))


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


def _chart_layout(fig, title: str = ""):
    """Apply readable light layout to any Plotly figure."""
    fig.update_layout(
        title=dict(text=title, font=dict(size=17, color="#0a0e1a"), x=0),
        font=dict(size=14, color="#0a0e1a"),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f7f9fc",
        xaxis=dict(
            tickfont=dict(size=13, color="#0a0e1a"),
            title_font=dict(size=14, color="#0a0e1a"),
        ),
        yaxis=dict(
            tickfont=dict(size=13, color="#0a0e1a"),
            title_font=dict(size=14, color="#0a0e1a"),
        ),
        legend=dict(font=dict(size=13, color="#0a0e1a")),
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


def _render_evidence(evidence_path: str, evidence_file_id: str) -> None:
    """Show photo/video evidence inline in the dashboard."""
    if not evidence_path and not evidence_file_id:
        st.caption("ማስረጃ አልተሰጠም።")
        return
    path = Path(evidence_path) if evidence_path else None
    if path and path.exists():
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            st.image(str(path), width=320, caption=path.name)
        elif suffix in {".mp4", ".mov", ".avi", ".mkv"}:
            st.video(str(path))
        else:
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            st.markdown(
                f'<a href="data:application/octet-stream;base64,{b64}" '
                f'download="{path.name}">📎 {path.name} ወርዶ</a>',
                unsafe_allow_html=True,
            )
    elif evidence_file_id:
        st.caption(f"📎 Telegram file_id: `{evidence_file_id[:40]}…`")
    else:
        st.caption(f"📎 ፋይሉ ቦታ: `{evidence_path}`")


# ── Boot bot ───────────────────────────────────────────────────────────────────
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
        "1. ካምፓስ  2. ዘርፍ  3. መግለጫ + ማስረጃ (ፎቶ/ቪዲዮ)  4. ስም ወይም ምስጢር"
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

# ── Sidebar filters ────────────────────────────────────────────────────────────
st.sidebar.markdown("## 🏛 የህብረት አመራር")
st.sidebar.caption("ይህን ገጽ በማንኛውም ሰዓት መክፈት ይችላሉ። ለአዲስ ቅሬታ Refresh ይጫኑ።")

df, source = load_live_complaints()
df = df.copy()
df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
st.sidebar.success(f"Data source: {source}")

campuses       = ["All"] + sorted([x for x in df["campus"].dropna().unique()   if x])
categories     = ["All"] + sorted([x for x in df["category"].dropna().unique() if x])
statuses_opts  = ["All"] + sorted([x for x in df["status"].dropna().unique()   if x])
priorities_opts= ["All"] + sorted([x for x in df["priority"].dropna().unique() if x])

campus        = st.sidebar.selectbox("ካምፓስ / Campus",   campuses)
category      = st.sidebar.selectbox("ዘርፍ / Category",  categories)
status_filter = st.sidebar.selectbox("ሁኔታ / Status",    statuses_opts)
priority      = st.sidebar.selectbox("ቅድሚያ / Priority", priorities_opts)
search        = st.sidebar.text_input("🔍 Search ticket, location, text")

filtered = df.copy()
if campus        != "All": filtered = filtered[filtered["campus"]   == campus]
if category      != "All": filtered = filtered[filtered["category"] == category]
if status_filter != "All": filtered = filtered[filtered["status"]   == status_filter]
if priority      != "All": filtered = filtered[filtered["priority"] == priority]
if search.strip():
    n = search.strip().lower()
    mask = (
        filtered["id"].str.lower().str.contains(n, na=False)
        | filtered["description"].str.lower().str.contains(n, na=False)
        | filtered["location"].str.lower().str.contains(n, na=False)
        | filtered["full_name"].str.lower().str.contains(n, na=False)
        | filtered["campus"].str.lower().str.contains(n, na=False)
    )
    filtered = filtered[mask]

# ── Summary metrics ────────────────────────────────────────────────────────────
sn = df["status"].astype(str)
resolved_count = int(sn.str.lower().isin(["resolved", "ተፈትቷል"]).sum())    if not df.empty else 0
progress_count = int(sn.str.lower().isin(["in progress", "በሂደት ላይ"]).sum()) if not df.empty else 0
high_count     = int(df["priority"].astype(str).str.lower().eq("high").sum()) if not df.empty else 0

st.markdown("---")
m1, m2, m3, m4 = st.columns(4)
m1.metric("📋 ጠቅላላ ቅሬታዎች",    len(df))
m2.metric("🟢 የተፈቱ",            resolved_count)
m3.metric("🟡 በሂደት ላይ",         progress_count)
m4.metric("🔴 አጣዳፊ (High)",     high_count)
st.markdown("---")

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_dash, tab_chief, tab_student = st.tabs([
    "📊 ዳሽቦርድ / Dashboard",
    "👑 ዋና ሊቀ-መንበር / Chief Panel",
    "🎓 ተማሪ ሁኔታ / My Progress",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CHARTS + TABLE
# ═══════════════════════════════════════════════════════════════════════════════
with tab_dash:
    if filtered.empty:
        st.warning("ገና ቅሬታ የለም። Telegram ክፈት፣ ቦቱ ላይ /start ላክ።")
    else:
        st.subheader("📈 የመረጃ ምስላዊ መግለጫዎች")
        c1, c2 = st.columns(2)

        # Bar: by campus
        by_campus = filtered.groupby("campus", dropna=False).size().reset_index(name="count")
        fig_b = px.bar(
            by_campus, x="campus", y="count", color="campus",
            color_discrete_sequence=px.colors.qualitative.Bold, text="count",
        )
        fig_b.update_traces(textposition="outside", textfont_size=14)
        _chart_layout(fig_b, "የካምፓስ ቅሬታ ንጽጽር")
        fig_b.update_layout(showlegend=False, xaxis_title="", yaxis_title="ቅሬታዎች")
        c1.plotly_chart(fig_b, use_container_width=True)

        # Pie: by category
        by_cat = filtered.groupby("category", dropna=False).size().reset_index(name="count")
        fig_p = px.pie(
            by_cat, names="category", values="count", hole=0.42,
            color_discrete_sequence=px.colors.qualitative.Bold,
        )
        fig_p.update_traces(textinfo="label+percent+value", textfont_size=13)
        _chart_layout(fig_p, "የችግሮች ስርጭት")
        c2.plotly_chart(fig_p, use_container_width=True)

        # Line: daily trend  |  Bar: by status
        ll, rl = st.columns(2)
        dated = filtered.dropna(subset=["created_at_dt"]).copy()
        if not dated.empty:
            dated["day"] = dated["created_at_dt"].dt.date
            daily = dated.groupby("day").size().reset_index(name="count")
            fig_l = px.line(
                daily, x="day", y="count", markers=True,
                color_discrete_sequence=["#2563eb"],
            )
            fig_l.update_traces(
                marker=dict(size=10), line=dict(width=3),
                text=daily["count"], textposition="top center", textfont_size=13,
            )
            _chart_layout(fig_l, "📅 የቀን ገጽታ")
            fig_l.update_layout(xaxis_title="ቀን", yaxis_title="ቅሬታዎች")
            ll.plotly_chart(fig_l, use_container_width=True)
        else:
            ll.info("የጊዜ መረጃ ገና የለም።")

        by_st = filtered.groupby("status", dropna=False).size().reset_index(name="count")
        fig_s = px.bar(
            by_st, x="status", y="count", color="status",
            color_discrete_map={"Open": "#ef4444", "In Progress": "#f59e0b", "Resolved": "#22c55e"},
            text="count",
        )
        fig_s.update_traces(textposition="outside", textfont_size=14)
        _chart_layout(fig_s, "📌 ሁኔታ ንጽጽር")
        fig_s.update_layout(showlegend=False, xaxis_title="", yaxis_title="ቁጥር")
        rl.plotly_chart(fig_s, use_container_width=True)

        # Map
        map_rows = [
            {"campus": cn, "count": int(ct),
             "lat": CAMPUS_COORDS[str(cn)][0], "lon": CAMPUS_COORDS[str(cn)][1]}
            for cn, ct in filtered.groupby("campus").size().items()
            if str(cn) in CAMPUS_COORDS
        ]
        if map_rows:
            mdf = pd.DataFrame(map_rows)
            try:
                fig_m = px.scatter_mapbox(
                    mdf, lat="lat", lon="lon", size="count", color="campus",
                    hover_name="campus", hover_data={"count": True, "lat": False, "lon": False},
                    zoom=11, height=400,
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                fig_m.update_layout(
                    mapbox_style="open-street-map",
                    margin=dict(l=0, r=0, t=40, b=0),
                    title=dict(text="🗺 የካምፓስ ካርታ", font=dict(size=17, color="#0a0e1a")),
                    legend=dict(font=dict(size=13, color="#0a0e1a")),
                )
                st.plotly_chart(fig_m, use_container_width=True)
            except Exception:
                st.plotly_chart(
                    _chart_layout(px.bar(mdf, x="campus", y="count", color="campus", text="count"),
                                  "🗺 ካምፓስ (fallback)"),
                    use_container_width=True,
                )

        # ── Table ─────────────────────────────────────────────────────────────
        st.subheader("📋 የቅሬታዎች መቆጣጠሪያ ሠንጠረዥ")
        tbl = filtered.sort_values("created_at", ascending=False).copy()
        tbl["ሁኔታ"]  = tbl["status"].map(lambda x: STATUS_AM.get(str(x), str(x)))
        tbl["ደረጃ"]  = tbl["priority"].map(lambda x: PRIORITY_AM.get(str(x), str(x)))
        tbl["ማንነት"] = tbl["is_anonymous"].map(
            lambda x: "🕵 ምስጢር" if str(x).lower() in {"yes", "true", "1"} else "👤 የተገለጸ"
        )
        disp = [
            "id", "campus", "category", "ሁኔታ", "ደረጃ",
            "status", "priority", "action", "description",
            "location", "ማንነት", "full_name", "phone", "department", "created_at",
        ]
        editor = st.data_editor(
            tbl[disp],
            use_container_width=True,
            hide_index=True,
            disabled=[
                "id", "campus", "category", "ሁኔታ", "ደረጃ",
                "description", "location", "ማንነት",
                "full_name", "phone", "department", "created_at",
            ],
            column_config={
                "id": "Ticket ID",
                "campus": "ካምፓስ",
                "category": "ዘርፍ",
                "status": st.column_config.SelectboxColumn(
                    "ሁኔታ (እርምጃ)", options=["Open", "In Progress", "Resolved"]
                ),
                "priority": st.column_config.SelectboxColumn(
                    "ቅድሚያ", options=["High", "Medium", "Low"]
                ),
                "action": "እርምጃ (Action)",
                "description": "መግለጫ",
                "location": "ቦታ",
            },
            key="complaints_editor",
        )
        orig   = tbl.set_index("id")[["status", "priority", "action"]]
        edited = editor.set_index("id")[["status", "priority", "action"]]
        changed = [
            tid for tid in edited.index
            if any(str(orig.loc[tid, c]) != str(edited.loc[tid, c])
                   for c in ("status", "priority", "action"))
        ]
        if changed:
            for tid in changed:
                update_complaint(
                    str(tid),
                    status=str(edited.loc[tid, "status"]),
                    priority=str(edited.loc[tid, "priority"]),
                    action=str(edited.loc[tid, "action"]),
                )
            st.success("✅ ለውጦች ተቀምጠዋል!")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CHIEF PANEL
# ═══════════════════════════════════════════════════════════════════════════════
with tab_chief:
    st.subheader("👑 ዋና ሊቀ-መንበር / ኃላፊ የቅሬታ አስተዳደር")
    st.info(
        "ከዚህ ፓኔል ኃላፊው ቅሬታን ካጣራ በኋላ ሁኔታ ይቀይራሉ፣ "
        "መፍትሄ ይጽፋሉ፣ እና ፎቶ/ቪዲዮ ማስረጃ ይቃኛሉ።",
        icon="ℹ️",
    )

    if df.empty:
        st.warning("ምንም ቅሬታ አልተመዘገበም።")
    else:
        ticket_ids  = sorted(df["id"].astype(str).tolist(), reverse=True)
        selected_id = st.selectbox("📌 ቲኬት ይምረጡ / Select Ticket",
                                   ticket_ids, key="chief_select")
        row = df[df["id"].astype(str) == selected_id]

        if row.empty:
            st.error("ቲኬቱ አልተገኘም።")
        else:
            r = row.iloc[0]
            st.markdown("---")

            d_col, m_col = st.columns([3, 2])
            with d_col:
                st.markdown(f"**🆔 Ticket:** `{r['id']}`")
                st.markdown(f"**📍 ካምፓስ:** {r['campus']}")
                st.markdown(f"**🗂 ዘርፍ:** {r['category']}")
                st.markdown(f"**📝 መግለጫ:** {r['description']}")
                st.markdown(f"**📌 ቦታ:** {r['location'] or '—'}")
                st.markdown(f"**👤 ስም:** {r['full_name'] or '(ምስጢር)'}")
                st.markdown(f"**📞 ስልክ:** {r['phone'] or '—'}")
                st.markdown(f"**🎓 ክፍል:** {r['department'] or '—'}")
                st.markdown(f"**📅 ቀን:** {r['created_at']}")
                current_status = str(r["status"])
                st.markdown(f"**ሁኔታ (አሁን):** {STATUS_AM.get(current_status, current_status)}")

            with m_col:
                st.markdown("**📎 ማስረጃ (ፎቶ/ቪዲዮ)**")
                _render_evidence(
                    str(r.get("evidence_path", "")),
                    str(r.get("evidence_file_id", "")),
                )

            # ── Update form ────────────────────────────────────────────────────
            st.markdown("---")
            st.markdown("### ✏️ ሁኔታ ይቀይሩ / Update Status")

            f1, f2 = st.columns(2)
            with f1:
                new_status = st.selectbox(
                    "አዲስ ሁኔታ / New Status",
                    STATUSES,
                    index=STATUSES.index(current_status) if current_status in STATUSES else 0,
                    key="chief_status",
                )
            with f2:
                pri_opts = ["High", "Medium", "Low"]
                new_priority = st.selectbox(
                    "ቅድሚያ / Priority",
                    pri_opts,
                    index=pri_opts.index(str(r["priority"])) if str(r["priority"]) in pri_opts else 1,
                    key="chief_priority",
                )

            new_action = st.text_area(
                "እርምጃ / Action taken (ምን ተደረገ?)",
                value=str(r["action"]) if r["action"] else "",
                height=85,
                key="chief_action",
            )
            resolution_note = st.text_area(
                "📋 መፍትሄ / Resolution Note (ለተማሪው የሚታይ)",
                value=str(r.get("resolution_note", ""))
                      if "resolution_note" in r.index else "",
                height=110,
                placeholder="ለምሳሌ: ካፌ ኃላፊ ጋር ተነጋግረናል። ችግሩ ይፈታል።",
                key="chief_note",
            )

            if st.button("💾 ሁኔታ ቀይር / Save Update", key="chief_save"):
                fields: dict[str, str] = {
                    "status":   new_status,
                    "priority": new_priority,
                    "action":   new_action,
                }
                if resolution_note.strip():
                    fields["resolution_note"] = resolution_note.strip()
                update_complaint(str(selected_id), **fields)
                st.success(f"✅ ቲኬት {selected_id} → **{new_status}** ተቀምጧል!")
                st.balloons()
                st.rerun()

            # ── Bulk update ────────────────────────────────────────────────────
            st.markdown("---")
            with st.expander("🔄 ብዙ ቲኬቶችን አንድ ጊዜ ቀይር / Bulk Status Update"):
                bulk_raw    = st.text_input(
                    "Ticket IDs (ኮማ ተለያይቶ)", placeholder="#BDU-2026-112, #BDU-2026-113",
                    key="bulk_ids")
                bulk_status = st.selectbox("አዲስ ሁኔታ", STATUSES, key="bulk_status")
                bulk_note   = st.text_area("Resolution Note (ለሁሉም)", height=70, key="bulk_note")
                if st.button("✅ ሁሉንም ቀይር / Apply to All", key="bulk_save"):
                    ids_list = [x.strip() for x in bulk_raw.split(",") if x.strip()]
                    if not ids_list:
                        st.warning("Ticket IDs ያስገቡ።")
                    else:
                        ok = 0
                        for bid in ids_list:
                            bf: dict[str, str] = {"status": bulk_status}
                            if bulk_note.strip():
                                bf["resolution_note"] = bulk_note.strip()
                            try:
                                update_complaint(bid, **bf)
                                ok += 1
                            except Exception as e:
                                st.warning(f"{bid}: {e}")
                        st.success(f"✅ {ok} ቲኬቶች ተዘምነዋል!")
                        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — STUDENT PROGRESS
# ═══════════════════════════════════════════════════════════════════════════════
with tab_student:
    st.subheader("🎓 ቅሬታዬ ደረሰ? / Track My Complaint")
    st.info("ቲኬት ቁጥርዎን ያስገቡ — ህብረቱ ምን ሁኔታ ላይ እንደሆነ ይረዱ።", icon="🔍")

    lookup_id = st.text_input(
        "🆔 ቲኬት ቁጥር ያስገቡ / Enter Ticket ID",
        placeholder="#BDU-2026-112",
        key="student_lookup",
    )

    if st.button("🔍 ሁኔታ ፈልጉ / Check Status", key="student_check"):
        if not lookup_id.strip():
            st.warning("ቲኬት ቁጥር ያስገቡ።")
        else:
            match = df[df["id"].astype(str).str.strip() == lookup_id.strip()]
            if match.empty:
                st.error(
                    f"❌ '{lookup_id}' አልተገኘም። "
                    "ቁጥሩን አረጋግጡ (ለምሳሌ #BDU-2026-112)።"
                )
            else:
                r = match.iloc[0]
                sv = str(r["status"])
                sl = STATUS_AM.get(sv, sv)
                pct = {"Open": 5, "In Progress": 50, "Resolved": 100}.get(sv, 5)

                if sv == "Resolved":
                    st.success(f"🟢 ቅሬታዎ ተፈትቷል! ({sl})")
                elif sv == "In Progress":
                    st.warning(f"🟡 ቅሬታዎ በሂደት ላይ ነው። ({sl})")
                else:
                    st.info(f"🔴 ቅሬታዎ ተቀብሏል፣ ገና ምላሽ አልተሰጠም። ({sl})")

                st.markdown(f"**ሂደት / Progress:** {pct}%")
                st.progress(pct)

                st.markdown("---")
                ci, ce = st.columns([3, 2])
                with ci:
                    st.markdown(f"**🆔 Ticket:** `{r['id']}`")
                    st.markdown(f"**📍 ካምፓስ:** {r['campus']}")
                    st.markdown(f"**🗂 ዘርፍ:** {r['category']}")
                    st.markdown(f"**📝 ቅሬታ:** {r['description']}")
                    st.markdown(f"**📅 ቀን:** {r['created_at']}")
                    res = (str(r.get("resolution_note", ""))
                           if "resolution_note" in r.index else "")
                    st.markdown("---")
                    if res.strip():
                        st.markdown("#### 💬 ከህብረት / Union Response")
                        st.success(res)
                    else:
                        st.caption("ህብረቱ ገና ምላሽ አልሰጠም። ቆይ።")
                with ce:
                    st.markdown("**📎 ያስቀመጡት ማስረጃ**")
                    _render_evidence(
                        str(r.get("evidence_path", "")),
                        str(r.get("evidence_file_id", "")),
                    )

    st.markdown("---")
    st.markdown(
        f"**ቅሬታ ለማስገባት:** Telegram → [@{BOT_USERNAME}]({BOT_URL}) → `/start`"
    )
