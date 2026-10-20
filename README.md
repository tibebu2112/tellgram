# የባህር ዳር ዩኒቨርሲቲ የተማሪዎች ህብረት — ቅሬታ ስርዓት

```
[ተማሪዎች] → (Telegram Bot) → [Google Sheets / Database] → (Streamlit Dashboard) → [የሕብረቱ አመራሮች]
```

- ተማሪዎች ቅሬታ ሲኖራቸው **በማንኛውም ሰዓት** በስልክ ቦቱ ላይ ያስገባሉ (`/start` ወይም `/new`)።
- ዋና ሥራ አስፈጻሚው ዳሽቦርዱን **በማንኛውም ጊዜ** ይከፍታል፣ አዲስ ቅሬታ በቅጽበት ይታያል።

**ቦት:** [https://t.me/unionstudent_complaints_bot](https://t.me/unionstudent_complaints_bot)

## የቦቱ ሂደት

1. **ካምፓስ:** ፔዳ፣ ፖሊ/BiT፣ ዘንዘልማ፣ ሰባታሚት፣ ጥቁር ዓባይ/ጥበበ ጊዮን፣ **ይባቤ (Yibabe)**፣ **ሰላም (Selam)**
2. **ዘርፍ:** ካፌ፣ ዶርም፣ አካዳሚክና ቴክኖሎጂ፣ ሰላምና ደህንነት፣ ሌሎች
3. **ዝርዝር:** ጽሑፍ፣ አማራጭ ፎቶ/ቪዲዮ፣ አማራጭ ህንጻ/ዶርም ቁጥር
4. **ማንነት:** ስም ይገለጽ ወይም በምስጢር ይያዝ

መልስ: `ቅሬታዎ በቁጥር #BDU-2026-104 ተመዝግቧል!`

## ዳሽቦርድ (ለአመራር)

- ሜትሪክስ: ጠቅላላ፣ የተፈቱ፣ በሂደት ላይ፣ አጣዳፊ
- ባር ቻርት በካምፓስ፣ ዶናት በዘርፍ፣ መስመር በቀን፣ ካርታ
- ሠንጠረዥ: Ticket ID፣ ካምፓስ፣ ዘርፍ፣ ሁኔታ፣ ቅድሚያ፣ እርምጃ

## GitHub ላይ የሚገፋው (push this)

ከ `unionstudent/` ፎልደር ውስጥ **እነዚህን ብቻ** commit/push ያድርጉ:

| ፋይል | ሚና |
|------|------|
| `app.py` | Streamlit ዳሽቦርድ |
| `bot.py` | Telegram ቦት (24/7 ቅሬታ) |
| `constants.py` | ካምፓስ (ይባቤ፣ ሰላም ጨምሮ) እና ዘርፍ |
| `data_manager.py` | Google Sheets / SQL / CSV |
| `complaints.csv` | የሙከራ መረጃ (demo tickets) |
| `requirements.txt` | Python packages |
| `runtime.txt` | Streamlit Cloud Python version |
| `README.md` | መመሪያ |
| `.gitignore` | ሚስጥር እንዳይገባ |
| `.streamlit/secrets.toml.example` | Secrets ምሳሌ |
| `evidence/.gitkeep` | የፎቶ ፎልደር |

**አይገፋም (do not push):**

- `.streamlit/secrets.toml` — የቦት ቶከን አለበት
- `.venv/`
- `evidence/*.jpg` / ቪዲዮ

```bash
cd unionstudent
git add app.py bot.py constants.py data_manager.py complaints.csv requirements.txt runtime.txt README.md .gitignore Procfile
git add .streamlit/secrets.toml.example evidence/.gitkeep
git status
git commit -m "Keep complaint bot always answering and remove Play Store Mini App menu"
git push origin main
```

Streamlit Cloud: **Main file path** = `app.py`. In **Secrets** paste:

```toml
TELEGRAM_BOT_TOKEN = "your-token-from-BotFather"
STREAMLIT_APP_URL = "https://YOUR-APP.streamlit.app"
```

Do **not** push `.streamlit/secrets.toml`.

Then add `https://YOUR-APP.streamlit.app` to [UptimeRobot](https://uptimerobot.com/) or [cron-job.org](https://cron-job.org/) as an HTTP ping every 5 minutes. Without that ping, Streamlit Cloud sleeps and the bot stops answering.

## Google Play Telegram ("Not available")

That Play Store popup is **not** a Python bug. Google Play Telegram hides bots that have a Mini App / Web App menu. After you deploy this code:

1. Open [@BotFather](https://t.me/BotFather) → `/mybots` → your bot → **Bot Settings**
2. **Menu Button** → **Commands** (or Default). Do **not** set a website URL.
3. If you see **Configure Mini App** / **Domain**, delete the URL.
4. `/setmenubutton` → choose to remove any custom web button.

Students on Play Store Telegram can still use:

- the direct link: https://t.me/unionstudent_complaints_bot
- Telegram Web: https://web.telegram.org
- Telegram from https://telegram.org/android (official APK)
- iPhone Telegram

Test with `/ping` — the bot should reply immediately. Then send `/start` (with the slash) or the word `start`.

## ማስኬጃ (ኮምፒውተር ላይ)

```bash
cd unionstudent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
```

`TELEGRAM_BOT_TOKEN` በ `secrets.toml` ያስገቡ። ከዚያ `streamlit run app.py` — ቦቱ ከዳሽቦርዱ ጋር በጀርባ ይሰራል።
