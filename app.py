"""BDU Student Union — Complaint Dashboard + Telegram Bot."""

from __future__ import annotations

import base64
import os
import threading
import time
import urllib.request
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from bot import BOT_URL, BOT_USERNAME, bot_status, notify_student, start_bot_background, token_from_anywhere
except ImportError as _e:
    st.error(
        f"❌ Failed to import bot module: `{_e}`\n\n"
        "Make sure `python-telegram-bot==21.6` is in requirements.txt and redeploy."
    )
    st.stop()

from constants import CAMPUS_COORDS, PRIORITY_AM, STATUS_AM, STATUSES
from data_manager import COLUMNS, CSV_PATH, EVIDENCE_DIR, load_secrets, update_complaint

st.set_page_config(
    page_title="BDU Student Union Dashboard",
    page_icon="🎓",
    layout="wide",
)

REFRESH_SECONDS = 10

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
html,body,[data-testid="stAppViewContainer"],[data-testid="stHeader"],
[data-testid="stMain"],.main{background:#f0f4fb !important;color:#0a0e1a !important;}
.block-container{padding-top:1.2rem;}
h1,h2,h3,h4,h5,h6,p,label,span,li,div,.stMarkdown,.stCaption,
[data-testid="stWidgetLabel"],[data-testid="stMarkdownContainer"] p{color:#0a0e1a !important;}
h1{font-size:2rem !important;font-weight:800 !important;}
h2{font-size:1.5rem !important;font-weight:700 !important;}
h3{font-size:1.2rem !important;font-weight:700 !important;}
[data-testid="stSidebar"]{background:#ffffff !important;border-right:2px solid #c5d0de;}
[data-testid="stSidebar"] *{color:#0a0e1a !important;}
div[data-testid="stMetric"]{background:#ffffff !important;border:2px solid #2563eb !important;
  border-radius:14px !important;padding:16px 18px !important;}
div[data-testid="stMetric"] label{color:#1e3a5f !important;font-size:.9rem !important;font-weight:600 !important;}
div[data-testid="stMetric"] [data-testid="stMetricValue"]{color:#1e3a5f !important;font-weight:800 !important;font-size:1.75rem !important;}
div[data-testid="stSelectbox"]>div>div,div[data-testid="stSelectbox"]>div>div>div{
  background:#ffffff !important;color:#0a0e1a !important;
  border:1.5px solid #94a3b8 !important;border-radius:8px !important;}
div[data-testid="stSelectbox"] span,div[data-testid="stSelectbox"] input{color:#0a0e1a !important;background:transparent !important;}
ul[role="listbox"],div[role="listbox"],[data-baseweb="popover"] ul,[data-baseweb="menu"]{background:#ffffff !important;}
ul[role="listbox"] li,[data-baseweb="menu"] [role="option"]{background:#ffffff !important;color:#0a0e1a !important;}
ul[role="listbox"] li:hover,[data-baseweb="menu"] [role="option"]:hover{background:#dbeafe !important;color:#1e3a5f !important;}
ul[role="listbox"] li[aria-selected="true"],[data-baseweb="menu"] [role="option"][aria-selected="true"]{
  background:#bfdbfe !important;color:#1e3a5f !important;font-weight:700 !important;}
div[data-testid="stTextInput"] input,div[data-testid="stTextArea"] textarea{
  background:#ffffff !important;color:#0a0e1a !important;
  border:1.5px solid #94a3b8 !important;border-radius:8px !important;}
div[data-testid="stTextInput"] input::placeholder,
div[data-testid="stTextArea"] textarea::placeholder{color:#64748b !important;}
[data-testid="stTabs"] button{color:#1e3a5f !important;font-size:1rem !important;font-weight:600 !important;}
[data-testid="stTabs"] button[aria-selected="true"]{border-bottom:3px solid #2563eb !important;color:#2563eb !important;}
.stButton>button{background:#2563eb !important;color:#ffffff !important;border:none !important;
  border-radius:8px !important;font-size:1rem !important;font-weight:700 !important;padding:.55rem 1.4rem !important;}
.stButton>button:hover{background:#1d4ed8 !important;}
[data-testid="stAlert"] p,[data-testid="stAlert"]{color:#0a0e1a !important;}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{
  background:#ffffff !important;color:#0a0e1a !important;
  border:1px solid #c5d0de;border-radius:10px;}
</style>
""", unsafe_allow_html=True)


# ── Keep-alive thread ──────────────────────────────────────────────────────────
def _keep_alive_worker() -> None:
    """Ping this Streamlit app every 90 seconds so Community Cloud stays awake."""
    time.sleep(20)

    app_url = ""
    try:
        app_url = str(st.secrets.get("STREAMLIT_APP_URL", "")).strip()
    except Exception:
        pass
    if not app_url:
        app_url = str(os.getenv("STREAMLIT_APP_URL", "")).strip()

    if not app_url:
        return

    ping_url = app_url.rstrip("/") + "/?keepalive=1"
    while True:
        try:
            urllib.request.urlopen(
                urllib.request.Request(ping_url, headers={"User-Agent": "BDU-KeepAlive/1.0"}),
                timeout=20,
            )
        except Exception:
            pass
        time.sleep(90)


if not st.session_state.get("_keepalive_started"):
    st.session_state["_keepalive_started"] = True
    threading.Thread(target=_keep_alive_worker, daemon=True, name="keepalive").start()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _secret_block(name: str) -> dict:
    try:
        block = st.secrets.get("connections", {}).get(name, {})
        return dict(block) if block else {}
    except Exception:
        return dict(load_secrets().get("connections", {}).get(name, {}))


def _has_gsheets_secrets() -> bool:
    block = _secret_block("gsheets")
    ss    = str(block.get("spreadsheet") or "")
    return bool(ss) and "YOUR_SHEET_ID" not in ss and "xxxxxxx" not in ss


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
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    # legacy column renames
    if "complaint" in df.columns and df.get("description", pd.Series()).eq("").all():
        df["description"] = df["complaint"]
    if "location" in df.columns and "building" not in df.columns:
        df["building"] = df["location"]
    # legacy status
    if "status" in df.columns:
        df["status"] = df["status"].replace("Open", "Pending")
    return df[[c for c in COLUMNS if c in df.columns]]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if "complaint" in df.columns and "description" not in df.columns:
        df["description"] = df["complaint"]
    if "location" in df.columns and "building" not in df.columns:
        df["building"] = df["location"]
    if "urgency" in df.columns and "priority" not in df.columns:
        df["priority"] = df["urgency"]
    if "status" in df.columns:
        df["status"] = df["status"].replace("Open", "Pending")
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUMNS].fillna("")
    df = df[df["id"].astype(str).str.strip() != ""]
    return df.reset_index(drop=True)


def load_live_complaints() -> tuple[pd.DataFrame, str]:
    if _has_gsheets_secrets():
        try:
            from streamlit_gsheets import GSheetsConnection
            conn   = st.connection("gsheets", type=GSheetsConnection)
            kwargs: dict = {"ttl": REFRESH_SECONDS}
            ws     = _secret_block("gsheets").get("worksheet")
            if ws and not str(ws).isdigit():
                kwargs["worksheet"] = ws
            return _clean(conn.read(**kwargs)), "Google Sheets"
        except Exception as exc:
            st.sidebar.warning(f"Google Sheets failed, using CSV. ({exc})")
    if _has_sql_secrets():
        try:
            conn = st.connection("complaints_db", type="sql")
            df   = conn.query("SELECT * FROM complaints ORDER BY created_at DESC", ttl=REFRESH_SECONDS)
            return _clean(df), "Database"
        except Exception as exc:
            st.sidebar.warning(f"Database failed, using CSV. ({exc})")
    return _from_csv(), "CSV / SQLite fallback"


def _chart_layout(fig, title: str = ""):
    fig.update_layout(
        title      = dict(text=title, font=dict(size=17, color="#0a0e1a"), x=0),
        font       = dict(size=14, color="#0a0e1a"),
        paper_bgcolor = "#ffffff",
        plot_bgcolor  = "#f7f9fc",
        xaxis      = dict(tickfont=dict(size=13, color="#0a0e1a"), title_font=dict(size=14, color="#0a0e1a")),
        yaxis      = dict(tickfont=dict(size=13, color="#0a0e1a"), title_font=dict(size=14, color="#0a0e1a")),
        legend     = dict(font=dict(size=13, color="#0a0e1a")),
        margin     = dict(l=10, r=10, t=50, b=10),
    )
    return fig


def _render_evidence(evidence_path: str, evidence_file_id: str) -> None:
    if not evidence_path and not evidence_file_id:
        st.caption("No evidence uploaded.")
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
                f'download="{path.name}">⬇️ Download {path.name}</a>',
                unsafe_allow_html=True,
            )
    elif evidence_file_id:
        st.caption(f"Telegram file_id: {evidence_file_id[:40]}…")
    else:
        st.caption(f"File: {evidence_path}")


# ── Boot bot ───────────────────────────────────────────────────────────────────
bot_state = start_bot_background()
status, bot_error = bot_status()

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("🎓 BDU Student Union — Complaint Dashboard")
st.caption("For union leadership. Students submit 24/7 via Telegram Bot.")

with st.expander("How students submit a complaint", expanded=False):
    st.markdown(
        f"Open Telegram → [@{BOT_USERNAME}]({BOT_URL}) → `/start`\n\n"
        "**8 steps:** Campus → Department → Identity → Category → "
        "Location → Description → Photo/Video → Confirm"
    )
    st.link_button("Open Telegram Bot", BOT_URL)

col_a, col_b, col_c = st.columns(3)
col_a.metric("Telegram Bot", f"@{BOT_USERNAME}")
col_b.metric("Bot Status",   status)
col_c.metric("Dashboard",    "Always On ✅")

if not token_from_anywhere():
    st.error("⚠️ Token missing. Add TELEGRAM_BOT_TOKEN to Streamlit Cloud Secrets.")
elif status == "bad_token" or (bot_error and "401" in bot_error):
    st.error(bot_error or "Telegram rejected the bot token.")
    st.markdown(
        "1. Open [@BotFather](https://t.me/BotFather) → `/mybots` → your bot → **API Token**.\n"
        "2. If Telegram says unauthorized, tap **Revoke current token** and copy the **new** token.\n"
        "3. Paste it only in **Streamlit Cloud → App settings → Secrets** as "
        "`TELEGRAM_BOT_TOKEN = \"...\"`.\n"
        "4. Click **Reboot app**. Wait until this page shows Bot Status **running**.\n"
        "5. In Telegram send `/start` (with the slash), not only the word start."
    )
elif bot_error:
    st.warning(bot_error)

_app_url = ""
try:
    _app_url = str(st.secrets.get("STREAMLIT_APP_URL", "")).strip()
except Exception:
    pass
if not _app_url:
    st.warning(
        "Set **STREAMLIT_APP_URL** in Streamlit Cloud Secrets to your app URL "
        "(example: `https://your-app.streamlit.app`) so the Telegram bot stays awake 24/7. "
        "Also add the same URL to a free uptime ping (UptimeRobot / cron-job.org) every 5 minutes."
    )

# ── Sidebar filters ────────────────────────────────────────────────────────────
st.sidebar.markdown("## 🔎 Filters")
st.sidebar.caption("Refresh the page to see new complaints.")

df, source = load_live_complaints()
df = df.copy()
df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
st.sidebar.success(f"Data source: {source}")

campuses_opts   = ["All"] + sorted([x for x in df["campus"].dropna().unique()   if x])
categories_opts = ["All"] + sorted([x for x in df["category"].dropna().unique() if x])
statuses_opts   = ["All"] + sorted([x for x in df["status"].dropna().unique()   if x])
dept_opts       = ["All"] + sorted([x for x in df["department"].dropna().unique() if x])
anon_opts       = ["All", "Named", "Anonymous"]

campus_f   = st.sidebar.selectbox("Campus",     campuses_opts)
category_f = st.sidebar.selectbox("Category",   categories_opts)
status_f   = st.sidebar.selectbox("Status",     statuses_opts)
dept_f     = st.sidebar.selectbox("Department", dept_opts)
anon_f     = st.sidebar.selectbox("Identity",   anon_opts)
search     = st.sidebar.text_input("🔍 Search ticket / text / name")

filtered = df.copy()
if campus_f   != "All": filtered = filtered[filtered["campus"]     == campus_f]
if category_f != "All": filtered = filtered[filtered["category"]   == category_f]
if status_f   != "All": filtered = filtered[filtered["status"]     == status_f]
if dept_f     != "All": filtered = filtered[filtered["department"] == dept_f]
if anon_f == "Anonymous":
    filtered = filtered[filtered["is_anonymous"].str.lower().isin(["yes", "true", "1"])]
elif anon_f == "Named":
    filtered = filtered[~filtered["is_anonymous"].str.lower().isin(["yes", "true", "1"])]
if search.strip():
    n    = search.strip().lower()
    mask = (
        filtered["id"].str.lower().str.contains(n, na=False)
        | filtered["description"].str.lower().str.contains(n, na=False)
        | filtered["building"].str.lower().str.contains(n, na=False)
        | filtered["full_name"].str.lower().str.contains(n, na=False)
        | filtered["campus"].str.lower().str.contains(n, na=False)
    )
    filtered = filtered[mask]

# ── Summary metrics ────────────────────────────────────────────────────────────
sn              = df["status"].astype(str)
resolved_count  = int(sn.str.lower().eq("resolved").sum())    if not df.empty else 0
progress_count  = int(sn.str.lower().eq("in progress").sum()) if not df.empty else 0
pending_count   = int(sn.str.lower().isin(["pending", "open"]).sum()) if not df.empty else 0
high_count      = int(df["priority"].astype(str).str.lower().eq("high").sum()) if not df.empty else 0

st.markdown("---")
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total",       len(df))
m2.metric("Pending",     pending_count)
m3.metric("In Progress", progress_count)
m4.metric("Resolved",    resolved_count)
m5.metric("High Priority", high_count)
st.markdown("---")

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_dash, tab_chief, tab_student = st.tabs([
    "📊 Dashboard",
    "👑 Admin Panel",
    "🎓 Student Tracker",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CHARTS + TABLE
# ═══════════════════════════════════════════════════════════════════════════════
with tab_dash:
    if filtered.empty:
        st.warning("No complaints yet matching your filters.")
    else:
        st.subheader("📈 Analytics")
        c1, c2 = st.columns(2)

        # Bar: by campus
        by_campus = filtered.groupby("campus", dropna=False).size().reset_index(name="count")
        fig_b = px.bar(by_campus, x="campus", y="count", color="campus",
                       color_discrete_sequence=px.colors.qualitative.Bold, text="count")
        fig_b.update_traces(textposition="outside", textfont_size=14)
        _chart_layout(fig_b, "Complaints by Campus")
        fig_b.update_layout(showlegend=False, xaxis_title="", yaxis_title="Complaints")
        c1.plotly_chart(fig_b, use_container_width=True)

        # Pie: by category
        by_cat = filtered.groupby("category", dropna=False).size().reset_index(name="count")
        fig_p  = px.pie(by_cat, names="category", values="count", hole=0.42,
                        color_discrete_sequence=px.colors.qualitative.Bold)
        fig_p.update_traces(textinfo="label+percent+value", textfont_size=12)
        _chart_layout(fig_p, "Top Problem Categories")
        c2.plotly_chart(fig_p, use_container_width=True)

        ll, rl = st.columns(2)

        # Line: daily trend
        dated = filtered.dropna(subset=["created_at_dt"]).copy()
        if not dated.empty:
            dated["day"] = dated["created_at_dt"].dt.date
            daily = dated.groupby("day").size().reset_index(name="count")
            fig_l = px.line(daily, x="day", y="count", markers=True,
                            color_discrete_sequence=["#2563eb"])
            fig_l.update_traces(
                marker=dict(size=10), line=dict(width=3),
                text=daily["count"], textposition="top center", textfont_size=13,
            )
            _chart_layout(fig_l, "Daily Trend")
            ll.plotly_chart(fig_l, use_container_width=True)
        else:
            ll.info("No date data yet.")

        # Bar: by status
        by_st  = filtered.groupby("status", dropna=False).size().reset_index(name="count")
        fig_s  = px.bar(by_st, x="status", y="count", color="status",
                        color_discrete_map={
                            "Pending":     "#ef4444",
                            "Open":        "#ef4444",
                            "In Progress": "#f59e0b",
                            "Resolved":    "#22c55e",
                        }, text="count")
        fig_s.update_traces(textposition="outside", textfont_size=14)
        _chart_layout(fig_s, "Resolution Rate")
        fig_s.update_layout(showlegend=False)
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
                    hover_name="campus",
                    hover_data={"count": True, "lat": False, "lon": False},
                    zoom=11, height=400,
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                fig_m.update_layout(
                    mapbox_style="open-street-map",
                    margin=dict(l=0, r=0, t=40, b=0),
                    title=dict(text="Campus Map", font=dict(size=17, color="#0a0e1a")),
                )
                st.plotly_chart(fig_m, use_container_width=True)
            except Exception:
                pass  # map optional

        # Table
        st.subheader("📋 Complaints Table")
        tbl = filtered.sort_values("created_at", ascending=False).copy()
        tbl["Status Label"]   = tbl["status"].map(lambda x: STATUS_AM.get(str(x), str(x)))
        tbl["Priority Label"] = tbl["priority"].map(lambda x: PRIORITY_AM.get(str(x), str(x)))
        tbl["Identity"]       = tbl["is_anonymous"].map(
            lambda x: "Anonymous 🕵" if str(x).lower() in {"yes", "true", "1"} else "Named 👤"
        )
        tbl["Location"] = tbl.apply(
            lambda r: " / ".join(p for p in [r["building"], r["floor"], r["room"]] if p), axis=1
        )
        disp = [
            "id", "campus", "department", "category", "Status Label",
            "Priority Label", "status", "priority", "action",
            "description", "Location", "Identity",
            "full_name", "student_id", "phone", "created_at",
        ]
        editor = st.data_editor(
            tbl[disp], use_container_width=True, hide_index=True,
            disabled=[
                "id", "campus", "department", "category",
                "Status Label", "Priority Label",
                "description", "Location", "Identity",
                "full_name", "student_id", "phone", "created_at",
            ],
            column_config={
                "id":         "Ticket ID",
                "campus":     "Campus",
                "department": "Department",
                "category":   "Category",
                "status":     st.column_config.SelectboxColumn(
                    "Status", options=STATUSES
                ),
                "priority":   st.column_config.SelectboxColumn(
                    "Priority", options=["High", "Medium", "Low"]
                ),
                "action":     "Action Taken",
                "description":"Description",
            },
            key="complaints_editor",
        )
        orig    = tbl.set_index("id")[["status", "priority", "action"]]
        edited  = editor.set_index("id")[["status", "priority", "action"]]
        changed = [
            tid for tid in edited.index
            if any(str(orig.loc[tid, c]) != str(edited.loc[tid, c]) for c in ("status", "priority", "action"))
        ]
        if changed:
            for tid in changed:
                update_complaint(
                    str(tid),
                    status   = str(edited.loc[tid, "status"]),
                    priority = str(edited.loc[tid, "priority"]),
                    action   = str(edited.loc[tid, "action"]),
                )
            st.success(f"✅ {len(changed)} ticket(s) updated!")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ADMIN PANEL (with Telegram notification)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_chief:
    st.subheader("👑 Admin Panel")
    st.info(
        "Select a ticket → review → update status → send a Telegram notification to the student.",
        icon="ℹ️",
    )

    if df.empty:
        st.warning("No complaints registered yet.")
    else:
        ticket_ids  = sorted(df["id"].astype(str).tolist(), reverse=True)
        selected_id = st.selectbox("Select Ticket", ticket_ids, key="chief_select")
        row         = df[df["id"].astype(str) == selected_id]

        if not row.empty:
            r = row.iloc[0]
            st.markdown("---")

            # Location display
            loc_parts = [str(r.get(f, "")) for f in ("building", "floor", "room")]
            location  = " / ".join(p for p in loc_parts if p) or "—"

            d_col, m_col = st.columns([3, 2])
            with d_col:
                st.markdown(f"**🎫 Ticket:**       `{r['id']}`")
                st.markdown(f"**🏫 Campus:**        {r['campus']}")
                st.markdown(f"**🎓 Department:**    {r.get('department', '—')}")
                st.markdown(f"**🗂 Category:**     {r['category']}")
                st.markdown(f"**📍 Location:**     {location}")
                st.markdown(f"**📝 Description:**  {r['description']}")
                st.markdown("---")
                is_anon = str(r.get("is_anonymous","")).lower() in {"yes","true","1"}
                if is_anon:
                    st.markdown("**👤 Identity:** Anonymous 🕵")
                else:
                    st.markdown(f"**👤 Name:**        {r.get('full_name','—')}")
                    st.markdown(f"**🪪 Student ID:**   {r.get('student_id','—')}")
                    st.markdown(f"**📞 Phone:**        {r.get('phone','—')}")
                st.markdown(f"**📅 Submitted:**    {r['created_at']}")
                sv = str(r["status"])
                st.markdown(f"**📊 Status:**       {STATUS_AM.get(sv, sv)}")

            with m_col:
                st.markdown("**📸 Evidence**")
                _render_evidence(
                    str(r.get("evidence_path", "")),
                    str(r.get("evidence_file_id", "")),
                )

            st.markdown("---")
            st.markdown("### ✏️ Update Ticket")
            f1, f2 = st.columns(2)
            with f1:
                new_status = st.selectbox(
                    "New Status", STATUSES,
                    index=STATUSES.index(sv) if sv in STATUSES else 0,
                    key="chief_status",
                )
            with f2:
                pri_opts     = ["High", "Medium", "Low"]
                cur_priority = str(r.get("priority", "Medium"))
                new_priority = st.selectbox(
                    "Priority", pri_opts,
                    index=pri_opts.index(cur_priority) if cur_priority in pri_opts else 1,
                    key="chief_priority",
                )

            new_action = st.text_area(
                "Action Taken (internal note)",
                value=str(r.get("action","")) if r.get("action") else "",
                height=80, key="chief_action",
            )
            resolution_note = st.text_area(
                "📢 Resolution Note (visible to student via Telegram)",
                value=str(r.get("resolution_note","")) if r.get("resolution_note") else "",
                height=110,
                placeholder="e.g. We spoke to the cafeteria manager. The issue will be fixed by Friday.",
                key="chief_note",
            )

            # Telegram notification toggle
            send_notif = st.checkbox(
                "📲 Send Telegram notification to student",
                value=True,
                key="chief_notify",
                help="Only works for non-anonymous complaints with a known Telegram chat ID.",
            )

            if st.button("💾 Save & Notify", key="chief_save"):
                fields: dict = {
                    "status":   new_status,
                    "priority": new_priority,
                    "action":   new_action,
                }
                if resolution_note.strip():
                    fields["resolution_note"] = resolution_note.strip()
                update_complaint(str(selected_id), **fields)

                # Send Telegram notification
                notif_sent = False
                if send_notif:
                    chat_id = str(r.get("telegram_id", "")).strip()
                    if chat_id and not is_anon:
                        sl    = STATUS_AM.get(new_status, new_status)
                        pct   = {"Pending": 5, "Open": 5, "In Progress": 50, "Resolved": 100}.get(new_status, 5)
                        bar   = "🟩" * (pct // 10) + "⬜" * (10 - pct // 10)
                        notif_msg = (
                            f"📢 *Update on your complaint*\n\n"
                            f"🎫 Ticket: `{selected_id}`\n"
                            f"📊 New Status: {sl}\n"
                            f"🔢 Progress: {bar} {pct}%"
                        )
                        if resolution_note.strip():
                            notif_msg += f"\n\n💬 *Union Response:*\n{resolution_note.strip()}"
                        notif_sent = notify_student(chat_id, notif_msg)

                st.success(f"✅ Ticket {selected_id} → {new_status}")
                if send_notif:
                    if notif_sent:
                        st.info("📲 Telegram notification sent to student.")
                    elif not is_anon:
                        st.warning("⚠️ Notification failed — check token or chat ID.")
                    else:
                        st.info("🕵 Anonymous complaint — no notification sent.")
                st.balloons()
                st.rerun()

            # Bulk update
            st.markdown("---")
            with st.expander("📦 Bulk Status Update (multiple tickets)"):
                bulk_raw    = st.text_input(
                    "Ticket IDs (comma-separated)",
                    placeholder="#BDU-2026-112, #BDU-2026-113",
                    key="bulk_ids",
                )
                bulk_status = st.selectbox("New Status", STATUSES, key="bulk_status")
                bulk_note   = st.text_area("Resolution Note (applied to all)", height=70, key="bulk_note")
                if st.button("Apply to All", key="bulk_save"):
                    ids_list = [x.strip() for x in bulk_raw.split(",") if x.strip()]
                    if not ids_list:
                        st.warning("Enter at least one ticket ID.")
                    else:
                        ok = 0
                        for bid in ids_list:
                            bf: dict = {"status": bulk_status}
                            if bulk_note.strip():
                                bf["resolution_note"] = bulk_note.strip()
                            try:
                                update_complaint(bid, **bf)
                                ok += 1
                            except Exception as e:
                                st.warning(f"{bid}: {e}")
                        st.success(f"✅ {ok} tickets updated!")
                        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — STUDENT TRACKER
# ═══════════════════════════════════════════════════════════════════════════════
with tab_student:
    st.subheader("🎓 Track My Complaint")
    st.info("Enter your Ticket ID to see the current status.", icon="🔍")

    lookup_id = st.text_input(
        "Ticket ID", placeholder="#BDU-2026-112", key="student_lookup"
    )

    if st.button("Check Status", key="student_check"):
        if not lookup_id.strip():
            st.warning("Please enter your ticket ID.")
        else:
            match = df[df["id"].astype(str).str.strip() == lookup_id.strip()]
            if match.empty:
                st.error(
                    f"Ticket `{lookup_id}` not found. "
                    "Check the number — example: #BDU-2026-112"
                )
            else:
                r   = match.iloc[0]
                sv  = str(r["status"])
                sl  = STATUS_AM.get(sv, sv)
                pct = {"Pending": 5, "Open": 5, "In Progress": 50, "Resolved": 100}.get(sv, 5)

                if sv == "Resolved":
                    st.success(f"✅ Your complaint has been *RESOLVED*! ({sl})")
                elif sv == "In Progress":
                    st.warning(f"🔄 Your complaint is *IN PROGRESS*. ({sl})")
                else:
                    st.info(f"⏳ Your complaint was received. Awaiting review. ({sl})")

                st.markdown(f"**Progress: {pct}%**")
                st.progress(pct)
                st.markdown("---")

                ci, ce = st.columns([3, 2])
                with ci:
                    loc_parts = [str(r.get(f, "")) for f in ("building", "floor", "room")]
                    location  = " / ".join(p for p in loc_parts if p) or "—"
                    st.markdown(f"**🎫 Ticket:**      `{r['id']}`")
                    st.markdown(f"**🏫 Campus:**       {r['campus']}")
                    st.markdown(f"**🎓 Department:**   {r.get('department','—')}")
                    st.markdown(f"**🗂 Category:**    {r['category']}")
                    st.markdown(f"**📍 Location:**    {location}")
                    st.markdown(f"**📅 Submitted:**   {r['created_at']}")
                    res = str(r.get("resolution_note","")) if "resolution_note" in r.index else ""
                    st.markdown("---")
                    if res.strip():
                        st.markdown("#### 💬 Union Response")
                        st.success(res)
                    else:
                        st.caption("The Union has not posted a response yet. Check back later.")

                with ce:
                    st.markdown("**📸 Your Evidence**")
                    _render_evidence(
                        str(r.get("evidence_path","")),
                        str(r.get("evidence_file_id","")),
                    )

    st.markdown("---")
    st.markdown(
        f"To submit a new complaint: Telegram → [@{BOT_USERNAME}]({BOT_URL}) → /start"
    )
