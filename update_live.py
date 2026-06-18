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

import io, json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

import pandas as pd

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover
    _ET = None


def et_now() -> datetime:
    """Current time in US/Eastern (falls back to UTC if zoneinfo unavailable)."""
    now = datetime.now(tz=timezone.utc)
    return now.astimezone(_ET) if _ET else now


def load_prev(path: str = "live_status.json") -> dict:
    """Load the previous live_status.json so we can carry forward EOD data and
    per-signal change timestamps. Returns {} if missing/unreadable."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as e:
        print(f"  prev live_status.json read FAILED: {e}")
    return {}


# ── Per-signal cadence metadata (non-proprietary; describes data availability) ─
# cadence: "live" = refreshes intraday (~5 min); "eod" = only changes after the
# CBOE end-of-day print (~5:30pm ET). `expected` explains when the print that
# feeds the next-open trade decision actually lands.
SIG_INFO = {
    "vix9d": ("live",
              "Live ^VIX9D (Yahoo), ~5-min refresh; falls back to CBOE EOD if "
              "unavailable. The 4pm close value feeds the ~7pm evaluation."),
    "vix1m": ("live",
              "Live VIX spot, refreshes ~every 5 min. The 4pm close value is the "
              "one that feeds the ~7pm evaluation."),
    "contango": ("live",
                 "Live VIX3M ÷ VIX1M (both Yahoo intraday); finalises for the "
                 "~7pm evaluation."),
    "vdelta": ("live",
               "Live VIX1M − VIX9D (both Yahoo intraday)."),
    "ratio_1m_3m": ("live",
                    "Live VIX1M ÷ VIX3M (both Yahoo intraday)."),
    "move_vix_ratio": ("live",
                       "ICE MOVE ÷ VIX; refreshes intraday when MOVE is "
                       "available, combined with the 4pm VIX close at ~7pm."),
}

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
        # Find the entry anchor of the currently-open position: walk back over the
        # trailing run of non-CASH bars and take the price at its first bar. Lets
        # us compute a LIVE % return (entry → live quote) instead of the stale
        # backtest open_pnl, which is ~0 on the entry bar.
        entry_price, entry_date = None, None
        pos = b.get("position")
        if pos and pos != "CASH":
            i = len(bars) - 1
            while i >= 0 and bars[i].get("position") == pos:
                start = bars[i]
                i -= 1
            entry_date = start.get("date")
            entry_price = (start.get("svxy_price") if pos == "LONG_VOL_SELLER"
                           else start.get("uvxy_price"))
        return {
            "date":        b.get("date"),
            "position":    pos,
            "signal":      b.get("signal"),
            "open_pnl":    b.get("open_pnl_pct"),
            "equity":      b.get("equity"),
            "entry_date":  entry_date,
            "entry_price": entry_price,
        }
    except Exception as e:
        print(f"  backtest_results.json read FAILED: {e}")
        return {}


def _live_official(quotes: dict) -> dict:
    """last_official() + a LIVE open P&L (entry price → current quote), so the
    header shows the open trade's return to date rather than a stale ~0%."""
    o = last_official()
    pos, ep = o.get("position"), o.get("entry_price")
    if pos and pos != "CASH" and ep:
        sym = "svxy" if pos == "LONG_VOL_SELLER" else "uvxy"
        px = (quotes.get(sym) or {}).get("price")
        if px:
            o["open_pnl"] = round(px / ep - 1.0, 6)
            o["open_pnl_live"] = True
    return o


def main() -> None:
    print("Fetching live market state ...")

    prev = load_prev()
    prev_curve = ((prev.get("market") or {}).get("curve")) or {}
    now_iso = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

    # ── Decide whether to re-fetch the CBOE end-of-day term structure ─────────
    # The CBOE history CSVs only gain a new row after the close (posted ~5:30pm
    # ET). Through the trading day they still show *yesterday's* close, so
    # polling them every 5 min is wasted work. Fetch only when (a) we have no
    # cached curve, or (b) it's past the EOD-post window and a fresh print may
    # be available. The rest of the day we carry the cached values forward.
    et = et_now()
    after_eod_post = (et.hour > 17) or (et.hour == 17 and et.minute >= 30)
    have_cached = any(prev_curve.get(k) is not None for k in ("vix9d", "vix3m"))
    refresh_cboe = (not have_cached) or after_eod_post

    curve = {}
    if refresh_cboe:
        # Term structure (CBOE EOD). vix1m proxy = VIX index, matching backtest.
        for key, sym in [("vix9d", "VIX9D"), ("vix1m", "VIX"), ("vix3m", "VIX3M"),
                         ("vix6m", "VIX6M"), ("vix1y", "VIX1Y"), ("vvix", "VVIX")]:
            val, pv, dt = cboe_last(sym)
            curve[key] = val
            curve[key + "_date"] = dt
            curve[key + "_chg"] = round(val / pv - 1.0, 6) if (val and pv) else None
            print(f"  cboe  {sym:6s} {val}")
    else:
        curve = dict(prev_curve)
        print(f"  cboe  reused cached EOD curve "
              f"(date={curve.get('vix9d_date')}, ET={et.strftime('%H:%M')}) "
              f"— not yet past the ~5:30pm post window")

    # Live spot / ETF quotes (Yahoo intraday) — always refreshed. Yahoo also
    # carries the term-structure indices intraday (^VIX9D, ^VIX3M), so we pull
    # those live and only fall back to the CBOE end-of-day value if Yahoo fails.
    quotes = {sym.lower().lstrip("^"): yahoo_quote(sym)
              for sym in ["^VIX", "^VIX9D", "^VIX3M", "SVXY", "UVXY", "SVIX", "UVIX",
                          "TQQQ", "SQQQ", "SPY", "^GSPC", "^MOVE"]}

    # Live values override the EOD term structure for the freshest reading.
    vix_spot = quotes["vix"]["price"]
    vix1m = vix_spot if vix_spot is not None else curve.get("vix1m")
    vix9d = quotes.get("vix9d", {}).get("price"); vix9d = vix9d if vix9d is not None else curve.get("vix9d")
    vix3m = quotes.get("vix3m", {}).get("price"); vix3m = vix3m if vix3m is not None else curve.get("vix3m")
    move  = quotes.get("move", {}).get("price")

    # Generic, non-proprietary derived readings (use the live-or-EOD values).
    def ratio(a, b):
        return (a / b - 1.0) if (a and b) else None
    contango   = ratio(vix3m, vix1m)
    backend    = ratio(curve.get("vix6m"), vix3m)
    vdelta     = (vix1m - vix9d) if (vix1m and vix9d) else None
    # Matches the backtest definitions: ratio_1m_3m = VIX1M/VIX3M, MOVE/VIX1M.
    ratio_1m_3m = (vix1m / vix3m) if (vix1m and vix3m) else None
    move_vix    = (move / vix1m) if (move and vix1m) else None
    regime     = None
    if contango is not None:
        regime = "contango" if contango > 0 else "backwardation"

    # ── Per-signal change-time tracking (persisted across runs) ───────────────
    # Stamp each signal with the time its value last *changed*. We carry the
    # prior timestamp forward when the value is unchanged, so the "last changed"
    # clock survives across runs/sessions (the dashboard reset on every reload).
    prev_timing = prev.get("signal_timing") or {}

    def _r(v):
        return None if v is None else round(float(v), 4)

    tracked = {
        "vix9d":          vix9d,
        "vix1m":          vix1m,
        "contango":       contango,
        "vdelta":         vdelta,
        "ratio_1m_3m":    ratio_1m_3m,
        "move_vix_ratio": move_vix,
    }
    signal_timing = {}
    for key, val in tracked.items():
        cadence, expected = SIG_INFO[key]
        new_v = _r(val)
        pv = prev_timing.get(key) or {}
        old_v = pv.get("value")
        if new_v != old_v:
            changed_utc = now_iso                       # value moved this run
        else:
            changed_utc = pv.get("changed_utc") or now_iso
        signal_timing[key] = {
            "value":       new_v,
            "changed_utc": changed_utc,
            "cadence":     cadence,
            "expected":    expected,
        }

    status = {
        "generated_utc": now_iso,
        "market": {
            "vix_spot": vix_spot,
            "vix1m_used": round(vix1m, 4) if vix1m else None,
            "vix9d_used": round(vix9d, 4) if vix9d else None,
            "vix3m_used": round(vix3m, 4) if vix3m else None,
            "curve": curve,
            "quotes": quotes,
        },
        "derived": {
            "contango": round(contango, 6) if contango is not None else None,
            "backend_slope": round(backend, 6) if backend is not None else None,
            "vdelta": round(vdelta, 4) if vdelta is not None else None,
            "ratio_1m_3m": round(ratio_1m_3m, 4) if ratio_1m_3m is not None else None,
            "move_vix_ratio": round(move_vix, 2) if move_vix is not None else None,
            "regime": regime,
        },
        # When each signal's decision-relevant print lands, and when each value
        # last changed (carried forward across runs).
        "signal_timing": signal_timing,
        "cboe_refreshed": refresh_cboe,
        # Strategy's last OFFICIAL state (signals finalize ~7pm ET each day;
        # trades execute the next market open — intraday readings are indicative).
        "official": _live_official(quotes),
        "note": ("Term structure is CBOE end-of-day (re-fetched only after the "
                 "~5:30pm ET post; cached intraday). VIX spot & ETF quotes are "
                 "Yahoo intraday (~15-min delayed). Live signals refresh ~every "
                 "5 min so you can see where the EOD reading is heading; signals "
                 "finalize at the ~7pm ET evaluation and trade the next open."),
    }

    with open("live_status.json", "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2)
    print(f"Wrote live_status.json  (regime={regime}, "
          f"contango={status['derived']['contango']}, cboe_refreshed={refresh_cboe})")


if __name__ == "__main__":
    main()
