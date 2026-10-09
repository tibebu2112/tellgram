# Union Student — Telegram complaints + Streamlit dashboard

Students send complaints to a Telegram bot. The Student Union sees them in near real time on a Streamlit dashboard.

The dashboard does **not** use a sample CSV as the primary source. It reads **Google Sheets** or a **database** with Streamlit `st.connection`. `complaints.csv` is only a local fallback.

```
unionstudent/
├── app.py                 # Streamlit dashboard (deploy this)
├── bot.py                 # Telegram bot
├── data_manager.py        # Google Sheets / SQL / CSV writes
├── complaints.csv         # local fallback store
├── requirements.txt
└── README.md
```

## 1. Create the Telegram bot

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a name and a username.
3. Copy the token. Put it in `.streamlit/secrets.toml` as `TELEGRAM_BOT_TOKEN`.

## 2. Google Sheets (recommended for Streamlit Cloud)

1. Create a Google Sheet. Name the first worksheet `complaints`.
2. Put this header row in row 1:

   `id,created_at,telegram_id,telegram_username,full_name,student_id,department,category,urgency,complaint,status`

3. In [Google Cloud Console](https://console.cloud.google.com/) create a project, enable **Google Sheets API** and **Google Drive API**.
4. Create a **service account**, download the JSON key.
5. Share the spreadsheet with the service account `client_email` as **Editor**.
6. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in the JSON fields plus the spreadsheet URL.

The dashboard reads with:

```python
conn = st.connection("gsheets", type=GSheetsConnection)
df = conn.read(ttl=8)
```

The bot writes new rows through `data_manager.py` using the same service account.

## 3. Optional: SQL instead of Sheets

Set `data_backend = "sql"` and add:

```toml
[connections.complaints_db]
url = "postgresql://USER:PASSWORD@HOST:5432/unionstudent"
```

Then Streamlit uses `st.connection("complaints_db", type="sql")`.

## 4. Run locally

```bash
cd unionstudent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Edit `secrets.toml`, then in two terminals:

```bash
python bot.py
streamlit run app.py
```

Open `http://localhost:8501`. Send `/start` to the bot; the dashboard refreshes every 8 seconds.

## 5. Deploy the dashboard on Streamlit Cloud (ደረጃ 2)

Push this folder to GitHub as `YOUR_USERNAME/unionstudent`.

1. Go to [https://share.streamlit.io](https://share.streamlit.io) (Streamlit Community Cloud).
2. Sign in with GitHub.
3. Click **New App**.
4. Repository: `YOUR_USERNAME/unionstudent`.
5. Branch: `main`.
6. **Main file path:** `app.py`.
7. Click **Deploy**.
8. Open **App settings → Secrets** and paste the contents of your local `.streamlit/secrets.toml` (Google Sheet URL + service account + bot token if you want it stored there).

The public app URL will look like `https://unionstudent.streamlit.app`.

## 6. Keep the bot online

Streamlit Cloud only hosts `app.py`. The bot must keep running somewhere else:

- your PC (`python bot.py`), or
- a small VPS, or
- a free process host (Railway, Render, Fly.io).

On that host set the same `TELEGRAM_BOT_TOKEN` and Google / database credentials so both the bot and the dashboard talk to one sheet or one database.

## Privacy

Complaints include student names and IDs. Share the dashboard only with union officers. Do not commit `.streamlit/secrets.toml`.
