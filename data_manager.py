"""Store student complaints in Google Sheets, SQL, or CSV.

The Telegram bot writes through this module. The Streamlit dashboard
reads the same store with st.connection (Google Sheets or SQL).
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "complaints.csv"
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"

COLUMNS = [
    "id",
    "created_at",
    "telegram_id",
    "telegram_username",
    "full_name",
    "student_id",
    "department",
    "category",
    "urgency",
    "complaint",
    "status",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_secrets() -> dict[str, Any]:
    if not SECRETS_PATH.exists():
        return {}
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        import tomli as tomllib  # type: ignore

    with SECRETS_PATH.open("rb") as handle:
        return tomllib.load(handle)


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
        "type": os.getenv("GSPREAD_TYPE") or block.get("type") or "service_account",
        "project_id": os.getenv("GSPREAD_PROJECT_ID") or block.get("project_id"),
        "private_key_id": os.getenv("GSPREAD_PRIVATE_KEY_ID") or block.get("private_key_id"),
        "private_key": (os.getenv("GSPREAD_PRIVATE_KEY") or block.get("private_key") or "").replace("\\n", "\n"),
        "client_email": os.getenv("GSPREAD_CLIENT_EMAIL") or block.get("client_email"),
        "client_id": os.getenv("GSPREAD_CLIENT_ID") or block.get("client_id"),
        "auth_uri": block.get("auth_uri") or "https://accounts.google.com/o/oauth2/auth",
        "token_uri": block.get("token_uri") or "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": block.get("auth_provider_x509_cert_url")
        or "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("GSPREAD_CLIENT_X509") or block.get("client_x509_cert_url"),
        "universe_domain": block.get("universe_domain") or "googleapis.com",
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
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = ""
    df = df[COLUMNS]
    df = df.dropna(how="all")
    df = df[df["id"].astype(str).str.strip() != ""]
    return df.reset_index(drop=True)


def _next_id(df: pd.DataFrame) -> str:
    if df.empty:
        return "C-1001"
    numbers = []
    for value in df["id"].astype(str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            numbers.append(int(digits))
    return f"C-{max(numbers, default=1000) + 1}"


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
    credentials = Credentials.from_service_account_info(config["account"], scopes=scopes)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_url(config["spreadsheet"]) if str(config["spreadsheet"]).startswith("http") else client.open(config["spreadsheet"])
    title = str(config["worksheet"])
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=2000, cols=len(COLUMNS))
        worksheet.append_row(COLUMNS)
        return worksheet

    existing = worksheet.row_values(1)
    if not existing:
        worksheet.append_row(COLUMNS)
    return worksheet


def _read_gsheets() -> pd.DataFrame:
    worksheet = _open_worksheet()
    records = worksheet.get_all_records()
    return _normalize(pd.DataFrame(records))


def _append_gsheets(row: dict[str, str]) -> None:
    worksheet = _open_worksheet()
    worksheet.append_row([row[column] for column in COLUMNS], value_input_option="USER_ENTERED")


def _read_sql() -> pd.DataFrame:
    from sqlalchemy import create_engine, text

    engine = create_engine(_sql_url())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS complaints (
                    id TEXT PRIMARY KEY,
                    created_at TEXT,
                    telegram_id TEXT,
                    telegram_username TEXT,
                    full_name TEXT,
                    student_id TEXT,
                    department TEXT,
                    category TEXT,
                    urgency TEXT,
                    complaint TEXT,
                    status TEXT
                )
                """
            )
        )
        result = connection.execute(text("SELECT * FROM complaints ORDER BY created_at DESC"))
        rows = result.mappings().all()
    return _normalize(pd.DataFrame(rows))


def _append_sql(row: dict[str, str]) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(_sql_url())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS complaints (
                    id TEXT PRIMARY KEY,
                    created_at TEXT,
                    telegram_id TEXT,
                    telegram_username TEXT,
                    full_name TEXT,
                    student_id TEXT,
                    department TEXT,
                    category TEXT,
                    urgency TEXT,
                    complaint TEXT,
                    status TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO complaints (
                    id, created_at, telegram_id, telegram_username, full_name,
                    student_id, department, category, urgency, complaint, status
                ) VALUES (
                    :id, :created_at, :telegram_id, :telegram_username, :full_name,
                    :student_id, :department, :category, :urgency, :complaint, :status
                )
                """
            ),
            row,
        )


def _read_csv() -> pd.DataFrame:
    _ensure_csv()
    try:
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    except pd.errors.EmptyDataError:
        df = pd.DataFrame(columns=COLUMNS)
    return _normalize(df)


def _append_csv(row: dict[str, str]) -> None:
    _ensure_csv()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writerow(row)


def read_complaints() -> pd.DataFrame:
    name = backend_name()
    if name == "gsheets":
        return _read_gsheets()
    if name == "sql":
        return _read_sql()
    return _read_csv()


def save_complaint(
    *,
    telegram_id: str,
    telegram_username: str,
    full_name: str,
    student_id: str,
    department: str,
    category: str,
    urgency: str,
    complaint: str,
) -> dict[str, str]:
    current = read_complaints()
    row = {
        "id": _next_id(current),
        "created_at": utc_now(),
        "telegram_id": str(telegram_id or ""),
        "telegram_username": telegram_username or "",
        "full_name": full_name.strip(),
        "student_id": student_id.strip(),
        "department": department.strip(),
        "category": category.strip(),
        "urgency": urgency.strip(),
        "complaint": complaint.strip(),
        "status": "Open",
    }
    name = backend_name()
    if name == "gsheets":
        _append_gsheets(row)
    elif name == "sql":
        _append_sql(row)
    else:
        _append_csv(row)
        return row

    # Keep a local CSV copy so the repo still has a file-based fallback.
    try:
        _append_csv(row)
    except OSError:
        pass
    return row
