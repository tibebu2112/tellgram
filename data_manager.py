"""Store student complaints in Google Sheets, SQL, or CSV.

The Telegram bot writes through this module.
The Streamlit dashboard reads the same store with st.connection.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from constants import default_action, infer_priority

ROOT         = Path(__file__).resolve().parent

# On Streamlit Cloud /mount/src/... is read-only after deploy.
# We write to /tmp (always writable) and seed it from the bundled CSV on first run.
_TMP_CSV = Path("/tmp/bdu_complaints.csv")
_TMP_EV  = Path("/tmp/bdu_evidence")

def _resolve_csv() -> Path:
    """Return a writable CSV path, copying the bundled seed file if needed."""
    bundled = ROOT / "complaints.csv"
    if _TMP_CSV.exists():
        return _TMP_CSV
    # Seed from bundled CSV so existing demo data is visible on first boot
    try:
        if bundled.exists():
            import shutil
            shutil.copy2(bundled, _TMP_CSV)
        else:
            _TMP_CSV.parent.mkdir(parents=True, exist_ok=True)
            _TMP_CSV.write_text("")
    except Exception:
        pass
    return _TMP_CSV

def _resolve_evidence_dir() -> Path:
    """Return a writable evidence directory."""
    try:
        _TMP_EV.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return _TMP_EV

CSV_PATH     = _resolve_csv()
EVIDENCE_DIR = _resolve_evidence_dir()
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"

# All columns stored per complaint
COLUMNS = [
    "id",
    "created_at",
    "campus",
    "department",
    "category",
    "description",
    "building",
    "floor",
    "room",
    "evidence_path",
    "evidence_file_id",
    "is_anonymous",
    "full_name",
    "student_id",
    "phone",
    "telegram_id",
    "telegram_username",
    "priority",
    "status",
    "action",
    "resolution_note",
]

SQL_CREATE = """
CREATE TABLE IF NOT EXISTS complaints (
    id               TEXT PRIMARY KEY,
    created_at       TEXT,
    campus           TEXT,
    department       TEXT,
    category         TEXT,
    description      TEXT,
    building         TEXT,
    floor            TEXT,
    room             TEXT,
    evidence_path    TEXT,
    evidence_file_id TEXT,
    is_anonymous     TEXT,
    full_name        TEXT,
    student_id       TEXT,
    phone            TEXT,
    telegram_id      TEXT,
    telegram_username TEXT,
    priority         TEXT,
    status           TEXT,
    action           TEXT,
    resolution_note  TEXT
)
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_secrets() -> dict[str, Any]:
    if not SECRETS_PATH.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    with SECRETS_PATH.open("rb") as fh:
        return tomllib.load(fh)


def backend_name() -> str:
    secrets = load_secrets()
    configured = (
        os.getenv("DATA_BACKEND")
        or secrets.get("data_backend")
        or ""
    ).strip().lower()
    if configured in {"gsheets", "sql", "csv"}:
        return configured
    if _gsheets_config():
        return "gsheets"
    if _sql_url():
        return "sql"
    return "csv"


def _gsheets_config() -> dict[str, Any] | None:
    secrets = load_secrets()
    block = secrets.get("connections", {}).get("gsheets", {})
    spreadsheet = os.getenv("GSHEETS_SPREADSHEET") or block.get("spreadsheet")
    if not spreadsheet:
        return None
    account = {
        "type":                        os.getenv("GSPREAD_TYPE")           or block.get("type")           or "service_account",
        "project_id":                  os.getenv("GSPREAD_PROJECT_ID")     or block.get("project_id"),
        "private_key_id":              os.getenv("GSPREAD_PRIVATE_KEY_ID") or block.get("private_key_id"),
        "private_key":                 (os.getenv("GSPREAD_PRIVATE_KEY")   or block.get("private_key") or "").replace("\\n", "\n"),
        "client_email":                os.getenv("GSPREAD_CLIENT_EMAIL")   or block.get("client_email"),
        "client_id":                   os.getenv("GSPREAD_CLIENT_ID")      or block.get("client_id"),
        "auth_uri":                    block.get("auth_uri")                or "https://accounts.google.com/o/oauth2/auth",
        "token_uri":                   block.get("token_uri")               or "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": block.get("auth_provider_x509_cert_url") or "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url":        os.getenv("GSPREAD_CLIENT_X509")   or block.get("client_x509_cert_url"),
        "universe_domain":             block.get("universe_domain")         or "googleapis.com",
    }
    if not account["client_email"] or not account["private_key"]:
        return None
    worksheet = os.getenv("GSHEETS_WORKSHEET") or block.get("worksheet") or "complaints"
    return {"spreadsheet": spreadsheet, "worksheet": worksheet, "account": account}


def _sql_url() -> str | None:
    secrets = load_secrets()
    url = os.getenv("DATABASE_URL") or secrets.get("connections", {}).get("complaints_db", {}).get("url")
    return url or None


def _ensure_csv() -> None:
    if CSV_PATH.exists():
        return
    with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=COLUMNS).writeheader()


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    # Legacy column renames
    if "complaint" in df.columns and "description" not in df.columns:
        df["description"] = df["complaint"]
    if "urgency" in df.columns and "priority" not in df.columns:
        df["priority"] = df["urgency"]
    if "location" in df.columns:
        # old single-field location → building
        if "building" not in df.columns:
            df["building"] = df["location"]
        df.drop(columns=["location"], inplace=True, errors="ignore")
    # Status legacy: "Open" → "Pending"
    if "status" in df.columns:
        df["status"] = df["status"].replace("Open", "Pending")
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUMNS].fillna("")
    df = df[df["id"].astype(str).str.strip() != ""]
    return df.reset_index(drop=True)


def _next_id(df: pd.DataFrame) -> str:
    year   = datetime.now(timezone.utc).year
    marker = f"BDU-{year}-"
    nums: list[int] = []
    for v in (df["id"].astype(str) if not df.empty else []):
        if marker not in str(v).replace("#", ""):
            continue
        digits = "".join(c for c in v.split("-")[-1] if c.isdigit())
        if digits:
            nums.append(int(digits))
    return f"#BDU-{year}-{max(nums, default=100) + 1:03d}"


# ── Google Sheets helpers ──────────────────────────────────────────────────────

def _open_worksheet():
    import gspread
    from google.oauth2.service_account import Credentials

    config = _gsheets_config()
    if not config:
        raise RuntimeError("Google Sheets credentials are missing.")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(config["account"], scopes=scopes)
    client = gspread.authorize(creds)
    ss = (
        client.open_by_url(config["spreadsheet"])
        if str(config["spreadsheet"]).startswith("http")
        else client.open(config["spreadsheet"])
    )
    title = str(config["worksheet"])
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=2000, cols=len(COLUMNS))
        ws.append_row(COLUMNS)
        return ws
    if not ws.row_values(1):
        ws.append_row(COLUMNS)
    return ws


def _read_gsheets() -> pd.DataFrame:
    return _normalize(pd.DataFrame(_open_worksheet().get_all_records()))


def _append_gsheets(row: dict) -> None:
    _open_worksheet().append_row(
        [row[c] for c in COLUMNS], value_input_option="USER_ENTERED"
    )


def _update_gsheets(ticket_id: str, fields: dict) -> None:
    ws = _open_worksheet()
    ids = ws.col_values(1)
    for i, v in enumerate(ids, start=1):
        if str(v).strip() == str(ticket_id).strip():
            header  = ws.row_values(1)
            current = ws.row_values(i)
            current += [""] * (len(header) - len(current))
            row = {header[j]: current[j] for j in range(len(header))}
            row.update(fields)
            ws.update(
                f"A{i}",
                [[row.get(c, "") for c in (header or COLUMNS)]],
                value_input_option="USER_ENTERED",
            )
            return
    raise KeyError(ticket_id)


# ── SQL helpers ────────────────────────────────────────────────────────────────

def _read_sql() -> pd.DataFrame:
    from sqlalchemy import create_engine, text
    engine = create_engine(_sql_url())
    with engine.begin() as conn:
        conn.execute(text(SQL_CREATE))
        rows = conn.execute(text("SELECT * FROM complaints ORDER BY created_at DESC")).mappings().all()
    return _normalize(pd.DataFrame(rows))


def _append_sql(row: dict) -> None:
    from sqlalchemy import create_engine, text
    engine = create_engine(_sql_url())
    cols   = ", ".join(COLUMNS)
    ph     = ", ".join(f":{c}" for c in COLUMNS)
    with engine.begin() as conn:
        conn.execute(text(SQL_CREATE))
        conn.execute(text(f"INSERT INTO complaints ({cols}) VALUES ({ph})"), row)


def _update_sql(ticket_id: str, fields: dict) -> None:
    from sqlalchemy import create_engine, text
    allowed = {k: v for k, v in fields.items() if k in COLUMNS and k != "id"}
    if not allowed:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in allowed)
    engine = create_engine(_sql_url())
    with engine.begin() as conn:
        conn.execute(text(SQL_CREATE))
        conn.execute(
            text(f"UPDATE complaints SET {assignments} WHERE id = :id"),
            {**allowed, "id": ticket_id},
        )


# ── CSV helpers ────────────────────────────────────────────────────────────────

def _read_csv() -> pd.DataFrame:
    _ensure_csv()
    try:
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    except pd.errors.EmptyDataError:
        df = pd.DataFrame(columns=COLUMNS)
    return _normalize(df)


def _append_csv(row: dict) -> None:
    _ensure_csv()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=COLUMNS).writerow(row)


def _update_csv(ticket_id: str, fields: dict) -> None:
    df   = _read_csv()
    mask = df["id"].astype(str).str.strip() == str(ticket_id).strip()
    if not mask.any():
        raise KeyError(ticket_id)
    for k, v in fields.items():
        if k in df.columns:
            df.loc[mask, k] = v
    df.to_csv(CSV_PATH, index=False)


# ── Public API ─────────────────────────────────────────────────────────────────

def read_complaints() -> pd.DataFrame:
    name = backend_name()
    if name == "gsheets":
        return _read_gsheets()
    if name == "sql":
        return _read_sql()
    return _read_csv()


def save_complaint(
    *,
    telegram_id:       str,
    telegram_username: str,
    campus:            str,
    department:        str  = "",
    category:          str,
    description:       str,
    building:          str  = "",
    floor:             str  = "",
    room:              str  = "",
    evidence_path:     str  = "",
    evidence_file_id:  str  = "",
    is_anonymous:      bool = False,
    full_name:         str  = "",
    student_id:        str  = "",
    phone:             str  = "",
    priority:          str | None = None,
    status:            str  = "Pending",
    action:            str | None = None,
) -> dict:
    current = read_complaints()
    anon    = "yes" if is_anonymous else "no"
    row = {
        "id":               _next_id(current),
        "created_at":       utc_now(),
        "campus":           campus.strip(),
        "department":       department.strip(),
        "category":         category.strip(),
        "description":      description.strip(),
        "building":         building.strip(),
        "floor":            floor.strip(),
        "room":             room.strip(),
        "evidence_path":    evidence_path,
        "evidence_file_id": evidence_file_id,
        "is_anonymous":     anon,
        "full_name":        "Anonymous" if is_anonymous else full_name.strip(),
        "student_id":       "" if is_anonymous else student_id.strip(),
        "phone":            "" if is_anonymous else phone.strip(),
        "telegram_id":      "" if is_anonymous else str(telegram_id or ""),
        "telegram_username":"" if is_anonymous else (telegram_username or ""),
        "priority":         (priority or infer_priority(category, description)).strip(),
        "status":           status.strip() or "Pending",
        "action":           (action or default_action(category)).strip(),
        "resolution_note":  "",
    }
    name = backend_name()
    if name == "gsheets":
        _append_gsheets(row)
    elif name == "sql":
        _append_sql(row)
    else:
        _append_csv(row)
        return row
    try:
        _append_csv(row)
    except OSError:
        pass
    return row


def update_complaint(ticket_id: str, **fields: str) -> None:
    allowed = {k: str(v) for k, v in fields.items() if k in COLUMNS and k != "id"}
    if not allowed:
        return
    name = backend_name()
    if name == "gsheets":
        _update_gsheets(ticket_id, allowed)
    elif name == "sql":
        _update_sql(ticket_id, allowed)
    else:
        _update_csv(ticket_id, allowed)
        return
    try:
        _update_csv(ticket_id, allowed)
    except (OSError, KeyError):
        pass
