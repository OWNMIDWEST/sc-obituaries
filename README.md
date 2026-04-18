# SC Obituary Scraper

Automatically scrapes daily obituaries for **Greenville, Spartanburg, and Anderson** counties in South Carolina. Runs via GitHub Actions every morning, emails results, and publishes a live dashboard via GitHub Pages.

---

## Live Dashboard

Once set up, your dashboard will be available at:
```
https://YOUR-GITHUB-USERNAME.github.io/sc-obituaries/
```

---

## Setup Guide (one-time, ~10 minutes)

### Step 1 — Create the GitHub repo

1. Go to [github.com](https://github.com) and sign in (or create a free account)
2. Click **New repository**
3. Name it `sc-obituaries`
4. Set it to **Private** (recommended — keeps your email credentials safe)
5. Click **Create repository**

---

### Step 2 — Upload the files

Upload these files to your new repo (drag & drop in the GitHub web interface):

```
sc-obituaries/
├── .github/
│   └── workflows/
│       └── daily_scrape.yml
├── docs/
│   └── (empty for now — scraper will create files here)
├── scraper.py
├── requirements.txt
└── README.md
```

To upload via the web:
1. In your repo, click **Add file → Upload files**
2. Drag all the files in — GitHub will preserve the folder structure
3. Click **Commit changes**

---

### Step 3 — Add your email credentials as GitHub Secrets

Your email and password are stored as **encrypted secrets** — never visible in the code.

1. In your repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** and add each of these:

| Secret name      | Value                                      |
|------------------|--------------------------------------------|
| `EMAIL_FROM`     | Your Gmail address (e.g. `you@gmail.com`)  |
| `EMAIL_PASSWORD` | Your Gmail **App Password** (see below)    |
| `EMAIL_TO`       | Recipient email(s), comma-separated        |

#### Getting a Gmail App Password
Gmail requires an App Password (not your regular password) for scripts:
1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Select **Mail** and **Windows Computer** (or any device)
3. Click **Generate** — copy the 16-character password
4. Paste it as the `EMAIL_PASSWORD` secret

> You must have 2-Step Verification enabled on your Google account to use App Passwords.

---

### Step 4 — Enable GitHub Pages

This publishes your dashboard as a live website:

1. Go to **Settings → Pages**
2. Under **Source**, select **Deploy from a branch**
3. Set branch to `main` and folder to `/docs`
4. Click **Save**

Your dashboard URL will be:
```
https://YOUR-USERNAME.github.io/sc-obituaries/
```

---

### Step 5 — Run it manually the first time

1. Go to the **Actions** tab in your repo
2. Click **Daily SC Obituary Scraper** in the left sidebar
3. Click **Run workflow → Run workflow**
4. Watch it run — it takes about 1–2 minutes
5. Check your email and your dashboard URL!

After this, it runs **automatically every day at 7:00 AM Eastern**.

---

## How it works

```
GitHub Actions (7am ET daily)
        │
        ▼
  scraper.py runs
        │
        ├──► Scrapes Legacy.com + funeral home sites
        │         (Greenville, Spartanburg, Anderson)
        │
        ├──► Updates docs/sc_obituaries_history.json
        │         (rolling 7-day window)
        │
        ├──► Rebuilds docs/index.html dashboard
        │         (embedded history, no server needed)
        │
        ├──► Commits changes back to GitHub
        │
        └──► Sends HTML email to you
```

---

## Customization

### Change the run time
Edit `.github/workflows/daily_scrape.yml`:
```yaml
- cron: '0 11 * * *'   # 11:00 UTC = 7:00 AM ET
```
Use [crontab.guru](https://crontab.guru) to find the right UTC time for your timezone.

### Add more funeral homes
Edit `scraper.py` and add entries to the `SOURCES` dict:
```python
{"name": "New Funeral Home", "url": "https://...", "parser": "generic"},
```

### Change history window
Edit `HISTORY_DAYS = 7` in `scraper.py`.

### Send to multiple emails
Set `EMAIL_TO` secret to comma-separated addresses:
```
person1@email.com, person2@email.com
```

---

## Files

| File | Purpose |
|------|---------|
| `scraper.py` | Main scraper — runs daily via GitHub Actions |
| `.github/workflows/daily_scrape.yml` | Schedules and runs the scraper |
| `requirements.txt` | Python dependencies |
| `docs/index.html` | Dashboard (auto-generated, served by GitHub Pages) |
| `docs/sc_obituaries_history.json` | 7-day history data (auto-generated) |
| `README.md` | This file |
