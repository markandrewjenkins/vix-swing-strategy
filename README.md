# VIX Swing Strategy — Live Dashboard

**Live dashboard:** https://markandrewjenkins.github.io/vix-swing-strategy/

A personal research project: a systematic swing strategy that trades the VIX futures
term structure through volatility ETFs — short volatility (SVXY / SVIX) when the curve
is in healthy contango, long volatility (UVXY / UVIX) in acute stress — with a live
signal monitor, the full backtest history and risk analytics.

> Not investment advice. Results shown are a **hypothetical backtest** (before trading
> costs, slippage and taxes, and partly in-sample), not live trading results.

## What the dashboard shows

- **Live Chart & Signals** — price with entry/exit markers and a live signal monitor that
  re-evaluates through the session and finalises after the CBOE end-of-day print.
- **P&L Summary / Trades Detail** — equity curve, performance statistics and every trade.
- **Risk Analysis** — Monte Carlo resampling, rolling returns, MAE/MFE.
- **Markets & Regime** — risk/return versus buy-and-hold and performance by regime.
- **Forward-Looking Analysis** — what followed the most similar past setups, with the
  method's own out-of-sample track record.

## How it works

The strategy engine (signal logic, parameter research and backtest) lives in a separate
private project. A scheduled cloud job runs it after each US close and publishes the
results here as `backtest_results.json`; a second job refreshes live quotes
(`live_status.json`) during market hours. The page itself is static HTML/JS on GitHub Pages.

| File | Purpose |
|------|---------|
| `index.html` | The dashboard |
| `backtest_results.json` | Backtest output published by the private engine |
| `live_status.json`, `*_ohlc.json` | Live quotes and daily candles |
| `update_live.py`, `build_ohlc.py` | Fetch public market data (CBOE end-of-day files, Yahoo Finance) |

## Data & limitations

- Quotes are delayed; the VIX term structure comes from CBOE end-of-day files.
- SVIX/UVIX launched in 2022; earlier SVIX/UVIX figures are simulated from SVXY/UVXY,
  scaled by each fund's leverage at the time.
- Backtest fills use the 4:00 PM close; live orders would be placed after hours.

## Local preview

```bash
python -m http.server 8000
# then open http://localhost:8000/
```
