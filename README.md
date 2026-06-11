# VIX Swing Strategy — Live Dashboard

A public-but-unlisted dashboard for a VIX term-structure swing strategy
(SVXY / UVXY). Historical backtest results plus a live market-state strip that
refreshes during market hours.

**Live URL (after deploy):** https://markandrewjenkins.github.io/vix-swing-strategy/

> Unlisted, not private: GitHub Pages on the free/Pro plan has no login gate.
> The page carries `<meta name="robots" content="noindex,nofollow">` so search
> engines skip it, and it isn't linked anywhere until you choose to link it.
> Anyone with the URL can view it.

---

## What's in this repo (and what isn't)

This repo is intentionally limited to **display + public market data**. It does
**not** contain the strategy's optimized parameters or its entry/exit logic —
those stay in the private project.

| File | Purpose |
|------|---------|
| `index.html` | The dashboard (loads the two JSON files below) |
| `backtest_results.json` | Historical backtest output — regenerated **privately** and pushed manually |
| `live_status.json` | Live market state — regenerated on a schedule by the Action |
| `update_live.py` | Self-contained fetcher: public CBOE term structure + Yahoo quotes → `live_status.json`. No params, no signal logic. |
| `.github/workflows/update-live.yml` | Cron Action that runs `update_live.py` every ~5 min during market hours |

### How "live" it is — honest limits
- **VIX spot + SVXY/UVXY/SPY quotes:** Yahoo intraday (~15 min delayed).
- **VIX9D / VIX3M / VIX6M / VIX1Y term structure:** CBOE **end-of-day** CSVs
  (no free intraday feed), so the curve updates after the close.
- **The strategy's official position:** evaluated once daily on confirmed closes
  at **15:45 ET** (no intraday repaint). The live strip's derived readings are
  labelled indicative.
- **GitHub cron is best-effort** — runs are routinely delayed 5–15 min. The page
  also self-polls `live_status.json` every 60 s while open, so an open tab stays
  as fresh as the latest committed update.

---

## One-time deploy

### 1. Create the public repo and push

```bash
cd "D:/Google Drive/Claude/vix-swing-strategy"
git add -A
git commit -m "Initial dashboard"

# With GitHub CLI (install: https://cli.github.com/ ):
gh repo create vix-swing-strategy --public --source=. --remote=origin --push

# …or without gh: create an empty public repo named "vix-swing-strategy" on
# github.com first (no README), then:
git remote add origin https://github.com/markandrewjenkins/vix-swing-strategy.git
git push -u origin main
```

### 2. Enable GitHub Pages
Repo → **Settings → Pages** → *Build and deployment* → **Source: Deploy from a
branch** → Branch **main**, folder **/(root)** → **Save**.
Live within ~1 min at the URL above.

### 3. Enable the scheduled Action
Repo → **Settings → Actions → General** → Workflow permissions →
**Read and write permissions** → Save. Then open the **Actions** tab, pick
*Update live market status*, and **Run workflow** once to confirm it commits a
fresh `live_status.json`. The cron takes over afterward.

> Note: GitHub disables scheduled Actions on repos with **no activity for 60
> days**. A single push or manual run re-enables them.

### 4. Link it from your portfolio (when you're ready to surface it)
In `markandrewjenkins/portfolio` → `index.html`, add a link/card pointing to
`https://markandrewjenkins.github.io/vix-swing-strategy/`. Until you add that
link the dashboard stays unlisted.

---

## Updating the backtest results

`backtest_results.json` here is a copy of the private backtest's output. To
refresh the displayed history after a strategy change, regenerate it in the
private project and copy it over:

```bash
# in the private project
python backtest.py --start 2011-11-30 --params best_params.json
cp backtest_results.json "../vix-swing-strategy/backtest_results.json"

# in this repo
git add backtest_results.json && git commit -m "Refresh backtest results" && git push
```

The Action will fold the new official position into the next `live_status.json`.

---

## Local preview

```bash
python -m http.server 8000
# open http://localhost:8000/
```
`live_status.json` must be served over HTTP (not opened as a `file://` path) for
the live strip to fetch it.
