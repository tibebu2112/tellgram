"""Run this once to write app.py: python _write_app.py"""
import pathlib

CODE = r'''"""BDU Student Union complaints dashboard + Telegram bot."""

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
    page_title="BDU Student Union Dashboard",
    page_icon="\U0001f393",
    layout="wide",
)

REFRESH_SECONDS = 10

# ─── CSS: force light theme on ALL widgets including dropdown popups ───────────
st.markdown("""
<style>
/* PAGE */
html,body,[data-testid="stAppViewContainer"],[data-testid="stHeader"],
[data-testid="stMain"],.main{
    background:#f0f4fb !important;color:#0a0e1a !important;}
.block-container{padding-top:1.2rem;}

/* ALL TEXT */
h1,h2,h3,h4,h5,h6,p,label,span,li,div,
.stMarkdown,.stCaption,
[data-testid="stWidgetLabel"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li{color:#0a0e1a !important;}
h1{font-size:2rem !important;font-weight:800 !important;}
h2{font-size:1.5rem !important;font-weight:700 !important;}
h3{font-size:1.2rem !important;font-weight:700 !important;}

/* SIDEBAR */
[data-testid="stSidebar"]{background:#ffffff !important;border-right:2px solid #c5d0de;}
[data-testid="stSidebar"] *{color:#0a0e1a !important;}

/* METRICS */
div[data-testid="stMetric"]{background:#ffffff !important;border:2px solid #2563eb !important;
    border-radius:14px !important;padding:16px 18px !important;}
div[data-testid="stMetric"] label{color:#1e3a5f !important;font-size:.9rem !important;font-weight:600 !important;}
div[data-testid="stMetric"] [data-testid="stMetricValue"]{color:#1e3a5f !important;font-weight:800 !important;font-size:1.75rem !important;}

/* SELECT CLOSED STATE */
div[data-testid="stSelectbox"]>div>div,
div[data-testid="stSelectbox"]>div>div>div{
    background:#ffffff !important;color:#0a0e1a !important;
    border:1.5px solid #94a3b8 !important;border-radius:8px !important;}
div[data-testid="stSelectbox"] span,
div[data-testid="stSelectbox"] input{color:#0a0e1a !important;background:transparent !important;}

/* DROPDOWN POPUP — rendered in a portal at the root of the DOM */
ul[role="listbox"],
div[role="listbox"],
[data-baseweb="popover"] ul,
[data-baseweb="menu"]{background:#ffffff !important;}

ul[role="listbox"] li,
div[role="listbox"] li,
[data-baseweb="menu"] [role="option"],
[data-baseweb="select"] [role="option"],
[data-baseweb="popover"] [role="option"]{
    background:#ffffff !important;color:#0a0e1a !important;}

ul[role="listbox"] li:hover,
[data-baseweb="menu"] [role="option"]:hover{
    background:#dbeafe !important;color:#1e3a5f !important;}

ul[role="listbox"] li[aria-selected="true"],
[data-baseweb="menu"] [role="option"][aria-selected="true"]{
    background:#bfdbfe !important;color:#1e3a5f !important;font-weight:700 !important;}

/* TEXT INPUT */
div[data-testid="stTextInput"] input{
    background:#ffffff !important;color:#0a0e1a !important;
    border:1.5px solid #94a3b8 !important;border-radius:8px !important;}
div[data-testid="stTextInput"] input::placeholder{color:#64748b !important;}

/* TEXT AREA */
div[data-testid="stTextArea"] textarea{
    background:#ffffff !important;color:#0a0e1a !important;
    border:1.5px solid #94a3b8 !important;border-radius:8px !important;}
div[data-testid="stTextArea"] textarea::placeholder{color:#64748b !important;}

/* TABS */
[data-testid="stTabs"] button{color:#1e3a5f !important;font-size:1rem !important;font-weight:600 !important;}
[data-testid="stTabs"] button[aria-selected="true"]{border-bottom:3px solid #2563eb !important;color:#2563eb !important;}

/* BUTTONS */
.stButton>button{background:#2563eb !important;color:#ffffff !important;border:none !important;
    border-radius:8px !important;font-size:1rem !important;font-weight:700 !important;
    padding:.55rem 1.4rem !important;}
.stButton>button:hover{background:#1d4ed8 !important;}

/* ALERTS / TABLE */
[data-testid="stAlert"] p{color:#0a0e1a !important;}
[data-testid="stAlert"]{color:#0a0e1a !important;}
.stExpander{color:#0a0e1a !important;}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{
    background:#ffffff !important;color:#0a0e1a !important;
    border:1px solid #c5d0de;border-radius:10px;}
</style>
""", unsafe_allow_html=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

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
            df = conn.query("SELECT * FROM complaints ORDER BY created_at DESC", ttl=REFRESH_SECONDS)
            return _clean(df), "Database"
        except Exception as exc:
            st.sidebar.warning(f"Database read failed, using CSV. ({exc})")
    return _from_csv(), "CSV / SQLite fallback"


def _chart_layout(fig, title: str = ""):
    """Light readable layout for all Plotly charts."""
    fig.update_layout(
        title=dict(text=title, font=dict(size=17, color="#0a0e1a"), x=0),
        font=dict(size=14, color="#0a0e1a"),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f7f9fc",
        xaxis=dict(tickfont=dict(size=13, color="#0a0e1a"), title_font=dict(size=14, color="#0a0e1a")),
        yaxis=dict(tickfont=dict(size=13, color="#0a0e1a"), title_font=dict(size=14, color="#0a0e1a")),
        legend=dict(font=dict(size=13, color="#0a0e1a")),
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


def _render_evidence(evidence_path: str, evidence_file_id: str) -> None:
    """Show photo/video evidence inline."""
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
                f'<a href="data:application/octet-stream;base64,{b64}" download="{path.name}">Download {path.name}</a>',
                unsafe_allow_html=True,
            )
    elif evidence_file_id:
        st.caption(f"Telegram file_id: {evidence_file_id[:40]}")
    else:
        st.caption(f"File path: {evidence_path}")


# ─── Boot bot ─────────────────────────────────────────────────────────────────
bot_state = start_bot_background()
status, bot_error = bot_status()

st.title("\U0001f393 BDU Student Union - Complaint Dashboard")
st.caption("For union leadership. Students submit 24/7 via Telegram Bot.")

with st.expander("How to submit a complaint", expanded=False):
    st.markdown(
        f"Open Telegram \u2192 [@{BOT_USERNAME}]({BOT_URL}) \u2192 `/start`\n\n"
        "1. Campus  2. Category  3. Description + Evidence (photo/video)  4. Name or Anonymous"
    )
    st.link_button("Open Telegram bot", BOT_URL)

col_a, col_b, col_c = st.columns(3)
col_a.metric("Telegram bot", f"@{BOT_USERNAME}")
col_b.metric("Bot status", status)
col_c.metric("Dashboard", "Always on")

if bot_state == "missing_token" or status == "missing_token":
    st.error("Token missing. Add TELEGRAM_BOT_TOKEN to Streamlit Cloud Secrets.")
elif bot_error:
    st.warning(bot_error)

# ─── Sidebar filters ──────────────────────────────────────────────────────────
st.sidebar.markdown("## Union Leadership")
st.sidebar.caption("Open anytime. Refresh for new complaints.")

df, source = load_live_complaints()
df = df.copy()
df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
st.sidebar.success(f"Data source: {source}")

campuses        = ["All"] + sorted([x for x in df["campus"].dropna().unique()   if x])
categories      = ["All"] + sorted([x for x in df["category"].dropna().unique() if x])
statuses_opts   = ["All"] + sorted([x for x in df["status"].dropna().unique()   if x])
priorities_opts = ["All"] + sorted([x for x in df["priority"].dropna().unique() if x])

campus        = st.sidebar.selectbox("Campus",   campuses)
category      = st.sidebar.selectbox("Category", categories)
status_filter = st.sidebar.selectbox("Status",   statuses_opts)
priority      = st.sidebar.selectbox("Priority", priorities_opts)
search        = st.sidebar.text_input("Search ticket, location, text")

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

sn = df["status"].astype(str)
resolved_count = int(sn.str.lower().isin(["resolved"]).sum())    if not df.empty else 0
progress_count = int(sn.str.lower().isin(["in progress"]).sum()) if not df.empty else 0
high_count     = int(df["priority"].astype(str).str.lower().eq("high").sum()) if not df.empty else 0

st.markdown("---")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Complaints", len(df))
m2.metric("Resolved",         resolved_count)
m3.metric("In Progress",      progress_count)
m4.metric("High Priority",    high_count)
st.markdown("---")

tab_dash, tab_chief, tab_student = st.tabs([
    "\U0001f4ca Dashboard",
    "\U0001f451 Chief Panel",
    "\U0001f393 Student Progress",
])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — CHARTS + TABLE
# ════════════════════════════════════════════════════════════════════════════════
with tab_dash:
    if filtered.empty:
        st.warning("No complaints yet. Open Telegram and send /start to the bot.")
    else:
        st.subheader("\U0001f4c8 Visual Analytics")
        c1, c2 = st.columns(2)

        by_campus = filtered.groupby("campus", dropna=False).size().reset_index(name="count")
        fig_b = px.bar(by_campus, x="campus", y="count", color="campus",
                       color_discrete_sequence=px.colors.qualitative.Bold, text="count")
        fig_b.update_traces(textposition="outside", textfont_size=14)
        _chart_layout(fig_b, "Complaints by Campus")
        fig_b.update_layout(showlegend=False, xaxis_title="", yaxis_title="Complaints")
        c1.plotly_chart(fig_b, use_container_width=True)

        by_cat = filtered.groupby("category", dropna=False).size().reset_index(name="count")
        fig_p = px.pie(by_cat, names="category", values="count", hole=0.42,
                       color_discrete_sequence=px.colors.qualitative.Bold)
        fig_p.update_traces(textinfo="label+percent+value", textfont_size=13)
        _chart_layout(fig_p, "Complaints by Category")
        c2.plotly_chart(fig_p, use_container_width=True)

        ll, rl = st.columns(2)
        dated = filtered.dropna(subset=["created_at_dt"]).copy()
        if not dated.empty:
            dated["day"] = dated["created_at_dt"].dt.date
            daily = dated.groupby("day").size().reset_index(name="count")
            fig_l = px.line(daily, x="day", y="count", markers=True,
                            color_discrete_sequence=["#2563eb"])
            fig_l.update_traces(marker=dict(size=10), line=dict(width=3),
                                 text=daily["count"], textposition="top center", textfont_size=13)
            _chart_layout(fig_l, "Daily Trend")
            fig_l.update_layout(xaxis_title="Date", yaxis_title="Complaints")
            ll.plotly_chart(fig_l, use_container_width=True)
        else:
            ll.info("No date data yet.")

        by_st = filtered.groupby("status", dropna=False).size().reset_index(name="count")
        fig_s = px.bar(by_st, x="status", y="count", color="status",
                       color_discrete_map={"Open": "#ef4444", "In Progress": "#f59e0b", "Resolved": "#22c55e"},
                       text="count")
        fig_s.update_traces(textposition="outside", textfont_size=14)
        _chart_layout(fig_s, "Status Breakdown")
        fig_s.update_layout(showlegend=False, xaxis_title="", yaxis_title="Count")
        rl.plotly_chart(fig_s, use_container_width=True)

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
                    title=dict(text="Campus Map", font=dict(size=17, color="#0a0e1a")),
                    legend=dict(font=dict(size=13, color="#0a0e1a")),
                )
                st.plotly_chart(fig_m, use_container_width=True)
            except Exception:
                st.plotly_chart(
                    _chart_layout(px.bar(mdf, x="campus", y="count", color="campus", text="count"),
                                  "Campus Map (fallback)"),
                    use_container_width=True,
                )

        st.subheader("\U0001f4cb Complaints Table")
        tbl = filtered.sort_values("created_at", ascending=False).copy()
        tbl["Status Label"]   = tbl["status"].map(lambda x: STATUS_AM.get(str(x), str(x)))
        tbl["Priority Label"] = tbl["priority"].map(lambda x: PRIORITY_AM.get(str(x), str(x)))
        tbl["Identity"]       = tbl["is_anonymous"].map(
            lambda x: "Anonymous" if str(x).lower() in {"yes", "true", "1"} else "Named")
        disp = ["id", "campus", "category", "Status Label", "Priority Label",
                "status", "priority", "action", "description",
                "location", "Identity", "full_name", "phone", "department", "created_at"]
        editor = st.data_editor(
            tbl[disp], use_container_width=True, hide_index=True,
            disabled=["id", "campus", "category", "Status Label", "Priority Label",
                      "description", "location", "Identity", "full_name", "phone", "department", "created_at"],
            column_config={
                "id": "Ticket ID", "campus": "Campus", "category": "Category",
                "status": st.column_config.SelectboxColumn("Status", options=["Open", "In Progress", "Resolved"]),
                "priority": st.column_config.SelectboxColumn("Priority", options=["High", "Medium", "Low"]),
                "action": "Action taken", "description": "Description", "location": "Location",
            },
            key="complaints_editor",
        )
        orig   = tbl.set_index("id")[["status", "priority", "action"]]
        edited = editor.set_index("id")[["status", "priority", "action"]]
        changed = [tid for tid in edited.index
                   if any(str(orig.loc[tid, c]) != str(edited.loc[tid, c])
                          for c in ("status", "priority", "action"))]
        if changed:
            for tid in changed:
                update_complaint(str(tid),
                                 status=str(edited.loc[tid, "status"]),
                                 priority=str(edited.loc[tid, "priority"]),
                                 action=str(edited.loc[tid, "action"]))
            st.success("Changes saved!")
            st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — CHIEF PANEL
# ════════════════════════════════════════════════════════════════════════════════
with tab_chief:
    st.subheader("\U0001f451 Chief / Chairperson Panel")
    st.info(
        "Select a ticket, review the evidence, update the status, "
        "and write a resolution note visible to the student.",
        icon="\u2139\ufe0f",
    )

    if df.empty:
        st.warning("No complaints registered yet.")
    else:
        ticket_ids  = sorted(df["id"].astype(str).tolist(), reverse=True)
        selected_id = st.selectbox("Select Ticket", ticket_ids, key="chief_select")
        row = df[df["id"].astype(str) == selected_id]

        if not row.empty:
            r = row.iloc[0]
            st.markdown("---")
            d_col, m_col = st.columns([3, 2])
            with d_col:
                st.markdown(f"**Ticket:** `{r['id']}`")
                st.markdown(f"**Campus:** {r['campus']}")
                st.markdown(f"**Category:** {r['category']}")
                st.markdown(f"**Description:** {r['description']}")
                st.markdown(f"**Location:** {r['location'] or '\u2014'}")
                st.markdown(f"**Student name:** {r['full_name'] or '(anonymous)'}")
                st.markdown(f"**Phone:** {r['phone'] or '\u2014'}")
                st.markdown(f"**Department:** {r['department'] or '\u2014'}")
                st.markdown(f"**Submitted:** {r['created_at']}")
                current_status = str(r["status"])
                st.markdown(f"**Current status:** {STATUS_AM.get(current_status, current_status)}")
            with m_col:
                st.markdown("**Evidence (photo/video)**")
                _render_evidence(
                    str(r.get("evidence_path", "")),
                    str(r.get("evidence_file_id", "")),
                )

            st.markdown("---")
            st.markdown("### Update Status")
            f1, f2 = st.columns(2)
            with f1:
                new_status = st.selectbox(
                    "New Status", STATUSES,
                    index=STATUSES.index(current_status) if current_status in STATUSES else 0,
                    key="chief_status",
                )
            with f2:
                pri_opts = ["High", "Medium", "Low"]
                new_priority = st.selectbox(
                    "Priority", pri_opts,
                    index=pri_opts.index(str(r["priority"])) if str(r["priority"]) in pri_opts else 1,
                    key="chief_priority",
                )
            new_action = st.text_area(
                "Action taken (internal note)",
                value=str(r["action"]) if r["action"] else "",
                height=85, key="chief_action",
            )
            resolution_note = st.text_area(
                "Resolution Note (visible to student)",
                value=str(r.get("resolution_note", "")) if "resolution_note" in r.index else "",
                height=110,
                placeholder="e.g. We spoke to the cafeteria manager. Issue will be resolved by Friday.",
                key="chief_note",
            )

            if st.button("\U0001f4be Save Update", key="chief_save"):
                fields: dict = {
                    "status":   new_status,
                    "priority": new_priority,
                    "action":   new_action,
                }
                if resolution_note.strip():
                    fields["resolution_note"] = resolution_note.strip()
                update_complaint(str(selected_id), **fields)
                st.success(f"Ticket {selected_id} updated \u2192 {new_status}")
                st.balloons()
                st.rerun()

            st.markdown("---")
            with st.expander("Bulk Status Update (multiple tickets at once)"):
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
                        st.success(f"{ok} tickets updated!")
                        st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — STUDENT PROGRESS
# ════════════════════════════════════════════════════════════════════════════════
with tab_student:
    st.subheader("\U0001f393 Track My Complaint")
    st.info("Enter your ticket number to see the current status.", icon="\U0001f50d")

    lookup_id = st.text_input(
        "Enter Ticket ID", placeholder="#BDU-2026-112", key="student_lookup"
    )

    if st.button("Check Status", key="student_check"):
        if not lookup_id.strip():
            st.warning("Please enter a ticket ID.")
        else:
            match = df[df["id"].astype(str).str.strip() == lookup_id.strip()]
            if match.empty:
                st.error(f"Ticket '{lookup_id}' not found. Check the number (e.g. #BDU-2026-112).")
            else:
                r   = match.iloc[0]
                sv  = str(r["status"])
                sl  = STATUS_AM.get(sv, sv)
                pct = {"Open": 5, "In Progress": 50, "Resolved": 100}.get(sv, 5)

                if sv == "Resolved":
                    st.success(f"Your complaint has been RESOLVED! ({sl})")
                elif sv == "In Progress":
                    st.warning(f"Your complaint is IN PROGRESS. ({sl})")
                else:
                    st.info(f"Your complaint was received. Awaiting review. ({sl})")

                st.markdown(f"**Progress: {pct}%**")
                st.progress(pct)
                st.markdown("---")

                ci, ce = st.columns([3, 2])
                with ci:
                    st.markdown(f"**Ticket:** `{r['id']}`")
                    st.markdown(f"**Campus:** {r['campus']}")
                    st.markdown(f"**Category:** {r['category']}")
                    st.markdown(f"**Description:** {r['description']}")
                    st.markdown(f"**Submitted:** {r['created_at']}")
                    res = str(r.get("resolution_note", "")) if "resolution_note" in r.index else ""
                    st.markdown("---")
                    if res.strip():
                        st.markdown("#### Union Response")
                        st.success(res)
                    else:
                        st.caption("The union has not posted a response yet. Please check back later.")
                with ce:
                    st.markdown("**Your submitted evidence**")
                    _render_evidence(
                        str(r.get("evidence_path", "")),
                        str(r.get("evidence_file_id", "")),
                    )

    st.markdown("---")
    st.markdown(f"To submit a complaint: Telegram \u2192 [@{BOT_USERNAME}]({BOT_URL}) \u2192 /start")
'''

out = pathlib.Path(r"c:/Users/hp/Desktop/peda/unionstudent/app.py")
out.write_text(CODE, encoding="utf-8")
print("Written", out.stat().st_size, "bytes to", out)
