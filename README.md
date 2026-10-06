# Lagonika monitor

Checks https://www.lagonika.gr/?dtype=all every 5 minutes on GitHub Actions and emails one message per new deal (title, price, store, image, discount code, links).

Python standard library only. No `pip install`.

| File | Role |
| --- | --- |
| `lagonika_monitor.py` | Fetch deals, compare to `seen.json`, email new ones |
| `seen.json` | IDs already handled (committed back by the workflow) |
| `.github/workflows/monitor.yml` | Cron every 5 min + manual "Run workflow" button |
| `run-local.bat` | Run one check on your PC using `.env` |

## How it works

- Reads the deal list embedded in the page (`__NEXT_DATA__`). If that ever breaks, it falls back to the RSS feed `https://www.lagonika.gr/feed/`.
- First run with an empty `seen.json` only remembers the current deals. No emails.
- Each later run emails every deal not in `seen.json`, oldest first. Expired deals are skipped.
- A deal is only marked seen after its email sends, so a failed send is retried next run.
- GitHub's cron isn't exact. Runs usually land every 5–15 minutes.

## Setup on GitHub

1. On github.com create a new **public** repository (e.g. `lagonika-monitor`), empty, with no README.
2. Push this folder:
   ```bat
   git remote add origin https://github.com/<you>/lagonika-monitor.git
   git push -u origin main
   ```
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add:
   - `SMTP_USER`: the Gmail address that sends
   - `SMTP_PASSWORD`: the 16-char Gmail App Password
   - `NOTIFY_EMAIL`: where alerts go
4. Repo → **Actions** → enable workflows if asked → **Lagonika monitor** → **Run workflow**.

Optional keyword filter: **Settings → Secrets and variables → Actions → Variables** → `KEYWORDS` = `iphone,playstation,lego` (empty or missing = all deals).

To pause: Actions → Lagonika monitor → **⋯ → Disable workflow**.

## Local commands

```bat
python lagonika_monitor.py --dry-run      :: list new deals, send nothing
python lagonika_monitor.py --test-email   :: email the newest deal as [TEST]
python lagonika_monitor.py                :: one real check
python lagonika_monitor.py --reset        :: forget seen deals
python lagonika_monitor.py --env C:\path\to\.env
```
