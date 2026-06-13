"""
update_live.py — live market-state generator for the VIX Swing Strategy dashboard
=================================================================================
Writes `live_status.json`, consumed by index.html and refreshed on a schedule
by the GitHub Action (and polled client-side every 60s while the page is open).

PRIVACY NOTE
------------
This script is intentionally self-contained and contains NO strategy parameters
and NO entry/exit logic. It publishes only:
  • public CBOE VIX term-structure values (end-of-day CSVs),
  • the live VIX spot + SVXY/UVXY/SPY quotes (Yahoo, intraday),
  • generic, non-proprietary derived readings (contango %, VDelta, curve slope),
  • a plain regime label (contango / backwardation), and
  • the strategy's LAST OFFICIAL position, read straight from
    backtest_results.json (produced privately by the backtest).

The optimized thresholds and the signal-combination logic stay in the private
project and never reach this public repository.

Data sources (all public):
  CBOE CDN  — VIX9D, VIX, VIX3M, VIX6M, VIX1Y daily history CSVs
  Yahoo v8  — ^VIX, SVXY, UVXY, SPY  (live / intraday quote)

Run:
    python update_live.py            # writes live_status.json
"""

from __future__ import annotations

import io, json, urllib.request, urllib.parse
from datetime import datetime, timezone

import pandas as pd

HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, timeout: int = 20, extra: dict | None = None) -> bytes:
    h = {**HDRS, **(extra or {})}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        import gzip as gz
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gz.decompress(data)
        return data


# ── CBOE end-of-day term structure ───────────────────────────────────────────
def cboe_last(symbol: str) -> tuple[float | None, float | None, str | None]:
    """Return (last_close, prev_close, iso_date) for a CBOE index history CSV."""
    url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
    try:
        text = _get(url).decode("utf-8", errors="replace")
        lines = text.strip().splitlines()
        hdr = next(i for i, l in enumerate(lines) if "DATE" in l.upper())
        df = pd.read_csv(io.StringIO("\n".join(lines[hdr:])))
        df.columns = [c.strip().upper() for c in df.columns]
        dc = next(c for c in df.columns if "DATE" in c)
        cc_candidates = [c for c in df.columns if "CLOSE" in c] or \
                        [c for c in df.columns if c != dc]
        cc = cc_candidates[0]
        df[dc] = pd.to_datetime(df[dc], errors="coerce")
        df = df.dropna(subset=[dc]).set_index(dc).sort_index()
        s = pd.to_numeric(df[cc], errors="coerce").dropna()
        if s.empty:
            return None, None, None
        prev = float(s.iloc[-2]) if len(s) >= 2 else None
        return float(s.iloc[-1]), prev, s.index[-1].date().isoformat()
    except Exception as e:
        print(f"  cboe  {symbol:6s} FAILED: {e}")
        return None, None, None


# ── Yahoo v8 live quote ───────────────────────────────────────────────────────
def yahoo_quote(symbol: str) -> dict:
    """Return {price, prev_close, change_pct, time} from the freshest Yahoo quote."""
    try:
        # Grab a cookie first (Yahoo gates the v8 endpoint behind one).
        try:
            with urllib.request.urlopen(
                urllib.request.Request("https://finance.yahoo.com/", headers=HDRS),
                timeout=10
            ) as r:
                cookie = r.headers.get("Set-Cookie", "").split(";")[0]
        except Exception:
            cookie = ""
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(symbol)}?range=1d&interval=5m")
        data = json.loads(_get(url, extra={"Cookie": cookie} if cookie else None))
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev  = meta.get("chartPreviousClose") or meta.get("previousClose")
        chg   = ((price / prev - 1.0) if (price and prev) else None)
        t     = meta.get("regularMarketTime")
        return {
            "price": round(price, 4) if price is not None else None,
            "prev_close": round(prev, 4) if prev is not None else None,
            "change_pct": round(chg, 6) if chg is not None else None,
            "time": (datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
                     if t else None),
        }
    except Exception as e:
        print(f"  yahoo {symbol:6s} FAILED: {e}")
        return {"price": None, "prev_close": None, "change_pct": None, "time": None}


# ── Last official position from the (privately generated) backtest ───────────
def last_official(path: str = "backtest_results.json") -> dict:
    """
    Read only the final bar of the backtest's bar_history to surface the
    strategy's last official position + signal. No logic is re-derived here.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            res = json.load(fh)
        bars = res.get("bar_history") or []
        if not bars:
            return {}
        b = bars[-1]
        return {
            "date":       b.get("date"),
            "position":   b.get("position"),
            "signal":     b.get("signal"),
            "open_pnl":   b.get("open_pnl_pct"),
            "equity":     b.get("equity"),
        }
    except Exception as e:
        print(f"  backtest_results.json read FAILED: {e}")
        return {}


def main() -> None:
    print("Fetching live market state ...")

    # Term structure (CBOE EOD). vix1m proxy = VIX index, matching the backtest.
    curve = {}
    for key, sym in [("vix9d", "VIX9D"), ("vix1m", "VIX"), ("vix3m", "VIX3M"),
                     ("vix6m", "VIX6M"), ("vix1y", "VIX1Y"), ("vvix", "VVIX")]:
        val, prev, dt = cboe_last(sym)
        curve[key] = val
        curve[key + "_date"] = dt
        curve[key + "_chg"] = round(val / prev - 1.0, 6) if (val and prev) else None
        print(f"  cboe  {sym:6s} {val}")

    # Live spot / ETF quotes (Yahoo intraday).
    quotes = {sym.lower().lstrip("^"): yahoo_quote(sym)
              for sym in ["^VIX", "SVXY", "UVXY", "SVIX", "UVIX",
                          "TQQQ", "SQQQ", "SPY", "^GSPC", "^MOVE"]}

    # Live VIX spot overrides the EOD VIX1M for the freshest front-end reading.
    vix_spot = quotes["vix"]["price"]
    vix1m = vix_spot if vix_spot is not None else curve.get("vix1m")

    # Generic, non-proprietary derived readings.
    def ratio(a, b):
        return (a / b - 1.0) if (a and b) else None
    contango   = ratio(curve.get("vix3m"), vix1m)
    backend    = ratio(curve.get("vix6m"), curve.get("vix3m"))
    vdelta     = (vix1m - curve["vix9d"]) if (vix1m and curve.get("vix9d")) else None
    regime     = None
    if contango is not None:
        regime = "contango" if contango > 0 else "backwardation"

    status = {
        "generated_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "market": {
            "vix_spot": vix_spot,
            "vix1m_used": round(vix1m, 4) if vix1m else None,
            "curve": curve,
            "quotes": quotes,
        },
        "derived": {
            "contango": round(contango, 6) if contango is not None else None,
            "backend_slope": round(backend, 6) if backend is not None else None,
            "vdelta": round(vdelta, 4) if vdelta is not None else None,
            "regime": regime,
        },
        # Strategy's last OFFICIAL state (daily, evaluated on confirmed closes
        # at 15:45 ET — intraday readings above are indicative only).
        "official": last_official(),
        "note": ("Term structure is CBOE end-of-day; VIX spot & ETF quotes are "
                 "Yahoo intraday (~15-min delayed). The strategy evaluates once "
                 "daily on confirmed closes at 15:45 ET — no intraday repaint."),
    }

    with open("live_status.json", "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2)
    print(f"Wrote live_status.json  (regime={regime}, contango={status['derived']['contango']})")


if __name__ == "__main__":
    main()
