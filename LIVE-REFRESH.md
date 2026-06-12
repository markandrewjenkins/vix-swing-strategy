# Live refresh — how the dashboard updates

The dashboard reads two files that a GitHub Action regenerates:
- `svxy_ohlc.json` — daily candles (incl. the current forming day)
- `live_status.json` — live quotes, current position, signal levels

An open dashboard tab re-fetches both every 60 seconds, so it shows whatever
the **latest Action run** produced. The question is just *how often the Action
runs*. There are two independent triggers:

## 1. Reliable end-of-day backbone (already set up)

The workflow's `schedule:` fires after the US close, once CBOE has posted the
day's end-of-day term-structure files:

```
cron: "35 21,22,23 * * 1-5"   # 21:35 / 22:35 / 23:35 UTC, weekdays
```

That's ~4:35–7:35pm ET (the spread covers EST/EDT and gives redundancy if
GitHub delays or skips one). Once-daily-ish scheduled runs are reliable, so this
guarantees a **confirmed EOD signal each evening** with no extra services.

> Why not poll every 5 min here? GitHub heavily throttles high-frequency
> *scheduled* workflows (ours fired hours apart). So intraday refresh is done
> via an **external trigger** instead — see below.

## 2. Reliable intraday refresh (external trigger — needs ~10 min one-time setup)

Manually-**dispatched** workflow runs are *not* throttled like scheduled ones,
so we trigger the workflow from a free external scheduler that fires on time.

### Step A — create a GitHub token (fine-grained)
1. GitHub → your avatar → **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**.
2. **Repository access:** *Only select repositories* → `vix-swing-strategy`.
3. **Permissions → Repository permissions → Actions: Read and write.**
4. Generate, and copy the token (starts with `github_pat_…`). Treat it like a
   password — anyone with it can trigger your Action.

### Step B — set up the trigger on cron-job.org (free)
Create a free account at https://cron-job.org, then **Create cronjob**:

- **URL:**
  `https://api.github.com/repos/markandrewjenkins/vix-swing-strategy/actions/workflows/update-live.yml/dispatches`
- **Request method:** `POST`
- **Request body (raw):** `{"ref":"main"}`
- **Headers:**
  - `Authorization: Bearer github_pat_YOUR_TOKEN_HERE`
  - `Accept: application/vnd.github+json`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `Content-Type: application/json`
- **Schedule:** every **5 minutes**, **Mon–Fri**, **13:30–20:05 UTC**
  (= 9:30am–4:05pm ET; cron-job.org lets you set a time window + weekdays).
  This window includes the strategy's ~15:45 ET decision point.

A correct dispatch returns **HTTP 204** (no content) — cron-job.org will show
the job as successful. You'll then see `chore: refresh live data [skip ci]`
commits appear every few minutes during market hours.

### Equivalent alternatives (any one works)
- **Google Apps Script** time-driven trigger doing the same POST (also free).
- A home machine / Raspberry Pi cron running
  `python build_ohlc.py && python update_live.py && git commit -am ... && git push`.

## Reality check on freshness
Even fully wired up, the underlying data has built-in lag:
- SVXY/UVXY + VIX spot quotes: Yahoo, **~15 min delayed**.
- VIX9D/3M/6M/1Y term structure: CBOE **end-of-day only**.
So intraday the chart/quotes trail real-time by ~15–20 min, and the
term-structure signals only finalize after the close. For true tick data you'd
need a paid market-data feed and an always-on server.

## Keeping it alive
GitHub disables scheduled workflows after **60 days of no repo activity**. The
Action's own commits count as activity, so as long as it's running it stays
enabled. If it ever goes dormant, push any commit or run it once manually.
