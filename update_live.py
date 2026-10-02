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
def _et_bar(ts):
    """(ET date string, minutes-since-midnight ET) for an epoch timestamp."""
    dt = (datetime.fromtimestamp(ts, tz=_ET) if _ET
          else datetime.fromtimestamp(ts, tz=timezone.utc))
    return dt.strftime("%Y-%m-%d"), dt.hour * 60 + dt.minute


_RTH_OPEN, _RTH_CLOSE = 9 * 60 + 30, 16 * 60      # 9:30 / 16:00 ET


def yahoo_quote(symbol: str, prepost: bool = False) -> dict:
    """Session-aware quote following the standard market convention (ported from the
    QQQ Swing Strategy dashboard so both share the same pre/regular/after semantics).

    Built from a 5-day 5-minute series so we know each session's real close:
      • Regular hours → price = live regular print, % = vs the PRIOR regular close.
      • After-hours   → price/% stay frozen at today's regular close vs the prior
                        close (the day's official change); the AH move is a
                        SEPARATE number measured from *today's* close.
      • Pre-market    → today hasn't traded regular yet, so price/% show the LAST
                        COMPLETED session; the pre-market move is the separate
                        number, measured from yesterday's close. For ^VIX this is
                        where CBOE's Global Trading Hours prints (from ~3:15am ET)
                        surface — as a PRE chip, not as the headline.

    Emitted fields:
      price / change_pct   the headline pair (always internally consistent)
      prev_close           the regular close the headline % is measured against
      session              'pre' | 'regular' | 'post' | 'closed'
      ext_price/ext_change extended-hours move vs its correct baseline
      asof_date            ET date of the reading shown as `price`
      time                 ISO timestamp of the freshest print
    """
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
               f"{urllib.parse.quote(symbol)}?range=5d&interval=5m"
               + ("&includePrePost=true" if prepost else ""))
        data = json.loads(_get(url, extra={"Cookie": cookie} if cookie else None))
        res  = data["chart"]["result"][0]
        ts   = res.get("timestamp") or []
        cl   = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []

        # Indices get a 15-minute settle grace: VIX3M/VIX9D publish 09:30-16:15 and
        # VIX 03:15-16:15, so their post-4pm prints are the closing settle, not
        # after-hours trading. ETFs genuinely trade from 16:00, so no grace.
        grace = 15 if symbol.startswith("^") else 0
        reg_close, pre_last, post_last, reg_time = {}, {}, {}, {}
        last_t = last_date = last_tod = None
        for i, c in enumerate(cl):
            if c is None or i >= len(ts):
                continue
            t = ts[i]
            dstr, tod = _et_bar(t)
            last_t, last_date, last_tod = t, dstr, tod
            if _RTH_OPEN <= tod <= _RTH_CLOSE + grace:
                reg_close[dstr] = c; reg_time[dstr] = t
            elif tod < _RTH_OPEN:
                pre_last[dstr] = c
            else:
                post_last[dstr] = c

        rdates = sorted(reg_close)
        if not rdates:
            raise ValueError("no regular-session bars")

        # Which session are we in, per the freshest print?
        today = last_date
        has_reg_today = today in reg_close
        if last_t is None:
            session = "closed"
        elif has_reg_today and _RTH_OPEN <= last_tod <= _RTH_CLOSE + grace:
            session = "regular"
        elif last_tod is not None and last_tod < _RTH_OPEN and not has_reg_today:
            session = "pre"
        elif last_tod is not None and last_tod > _RTH_CLOSE + grace and has_reg_today:
            session = "post"
        else:
            session = "regular" if has_reg_today else "closed"

        def _chg(a, b):
            return (a / b - 1.0) if (a and b) else None

        cur_d  = rdates[-1]
        prev_d = rdates[-2] if len(rdates) >= 2 else None
        price  = reg_close[cur_d]
        prev   = reg_close[prev_d] if prev_d else None
        chg    = _chg(price, prev)
        asof   = cur_d
        if session == "post":
            ext_px = post_last.get(today)
        elif session == "pre":
            ext_px = pre_last.get(today)
        else:
            ext_px = None
        ext_chg = _chg(ext_px, price) if ext_px else None

        r6 = lambda v: round(v, 6) if v is not None else None
        r4 = lambda v: round(v, 4) if v is not None else None
        return {
            "price": r4(price), "prev_close": r4(prev), "change_pct": r6(chg),
            "session": session, "asof_date": asof,
            "regular_price": r4(price), "regular_change": r6(chg),
            "ext_price": r4(ext_px), "ext_change": r6(ext_chg),
            "reg_time": (datetime.fromtimestamp(reg_time.get(asof), tz=timezone.utc).isoformat()
                         if reg_time.get(asof) else None),
            "time": (datetime.fromtimestamp(last_t, tz=timezone.utc).isoformat()
                     if last_t else None),
        }
    except Exception as e:
        print(f"  yahoo {symbol:6s} FAILED: {e}")
        return {"price": None, "prev_close": None, "change_pct": None,
                "session": None, "asof_date": None,
                "regular_price": None, "regular_change": None,
                "ext_price": None, "ext_change": None, "reg_time": None, "time": None}


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
            # Dynamic sizing (ADD/TRIM) re-blends the engine's entry basis mid-trade,
            # so the first-bar price is no longer the position's cost basis. Back the
            # EFFECTIVE entry out of the engine's own state instead:
            #   eff_entry = last_close / (1 + open_pnl_pct)
            # which is exact whatever adjustment history the trade has.
            _lp = b.get("svxy_price") if pos == "LONG_VOL_SELLER" else b.get("uvxy_price")
            _op = b.get("open_pnl_pct")
            if _lp and _op is not None and (1.0 + _op) > 0:
                entry_price = round(_lp / (1.0 + _op), 4)
        return {
            "date":        b.get("date"),
            "position":    pos,
            "signal":      b.get("signal"),
            "open_pnl":    b.get("open_pnl_pct"),
            "equity":      b.get("equity"),
            "entry_date":  entry_date,
            "entry_price": entry_price,
            "pos_size":    b.get("pos_size"),
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

    today_iso = et.date().isoformat()
    curve = {}
    if refresh_cboe:
        # Term structure (CBOE EOD). vix1m proxy = VIX index, matching backtest.
        for key, sym in [("vix9d", "VIX9D"), ("vix1m", "VIX"), ("vix3m", "VIX3M"),
                         ("vix6m", "VIX6M"), ("vix1y", "VIX1Y"), ("vvix", "VVIX")]:
            val, pv, dt = cboe_last(sym)
            curve[key] = val
            curve[key + "_date"] = dt
            curve[key + "_chg"] = round(val / pv - 1.0, 6) if (val and pv) else None
            # Prior-day official close = the authoritative reference for TODAY's
            # intraday %change. If the CSV already shows today's close, that's `pv`;
            # otherwise the latest close (yesterday's) is itself the reference.
            curve[key + "_ref"] = pv if (dt == today_iso) else val
            print(f"  cboe  {sym:6s} {val}")
    else:
        curve = dict(prev_curve)
        print(f"  cboe  reused cached EOD curve "
              f"(date={curve.get('vix9d_date')}, ET={et.strftime('%H:%M')}) "
              f"— not yet past the ~5:30pm post window")

    # Set each index's reference close for TODAY's intraday %change.
    #   • cached / prior-day curve (dt != today): the cached close IS yesterday's close, so
    #     it is the reference. This must OVERWRITE any _ref carried over from a prior EOD run
    #     (which pointed at the close BEFORE this cached one — the bug that made intraday
    #      %change read ~yesterday's move all day).
    #   • curve already shows today's close (dt == today, just posted at EOD): keep the
    #     prior close set in the fetch loop, or derive it from the stored %change.
    for key in ("vix9d", "vix1m", "vix3m", "vix6m", "vix1y", "vvix"):
        if curve.get(key) is None:
            continue
        dt, chg = curve.get(key + "_date"), curve.get(key + "_chg")
        if dt != today_iso:
            curve[key + "_ref"] = curve[key]
        elif curve.get(key + "_ref") is None and chg not in (None, -1):
            curve[key + "_ref"] = round(curve[key] / (1.0 + chg), 4)

    # Live quotes (Yahoo intraday). Indices (VIX*) are regular-session only, so no
    # pre/post. ETFs use includePrePost so they update from ~4am through after-hours.
    quotes = {}
    # prepost=True makes yahoo_quote scan the intraday bars for the LATEST print instead of
    # using regularMarketPrice (which freezes at the 16:15 close). ^VIX carries CBOE's Global
    # Trading Hours session (bars from ~03:15 ET), so this lets VIX update pre-market.
    # VIX9D/VIX3M/VVIX/VIX1Y have no extended session (09:30 only) — harmless there.
    for sym in ["^VIX", "^VIX9D", "^VIX3M", "^VVIX", "^VIX1Y", "^MOVE", "^GSPC"]:
        quotes[sym.lower().lstrip("^")] = yahoo_quote(sym, prepost=True)
    for sym in ["SVXY", "UVXY", "SVIX", "UVIX", "TQQQ", "SQQQ", "SPY"]:
        quotes[sym.lower().lstrip("^")] = yahoo_quote(sym, prepost=True)
    # Re-base VIX-index %change on the authoritative CBOE prior close (Yahoo's
    # chartPreviousClose is unreliable for VIX), keeping value & %ch self-consistent.
    for qkey, ckey in [("vix", "vix1m"), ("vix9d", "vix9d"), ("vix3m", "vix3m"),
                       ("vvix", "vvix"), ("vix1y", "vix1y")]:
        q = quotes.get(qkey); ref = curve.get(ckey + "_ref")
        if q and q.get("price") and ref:
            q["change_pct"] = round(q["price"] / ref - 1.0, 6)
            q["prev_close"] = round(ref, 4)

    # SPX (^GSPC) is a regular-session-only index. Under the session-aware convention the
    # headline stays the official regular pair; SPY (trades ~4am-8pm) supplies the
    # extended-hours move, surfaced as SPX's PRE/AH chip (SPX level scaled by SPY's move).
    g, spy = quotes.get("gspc"), quotes.get("spy")
    if g and spy and spy.get("ext_change") is not None and g.get("price") \
       and spy.get("session") in ("pre", "post"):
        g["session"]    = spy["session"]
        g["ext_change"] = spy["ext_change"]
        g["ext_price"]  = round(g["price"] * (1.0 + spy["ext_change"]), 2)
        g["time"]       = spy.get("time") or g.get("time")

    # Live values override the EOD term structure for the freshest reading.
    vix_spot = quotes["vix"]["price"]
    vix1m = vix_spot if vix_spot is not None else curve.get("vix1m")
    vix9d = quotes.get("vix9d", {}).get("price"); vix9d = vix9d if vix9d is not None else curve.get("vix9d")
    vix3m = quotes.get("vix3m", {}).get("price"); vix3m = vix3m if vix3m is not None else curve.get("vix3m")
    vvix  = quotes.get("vvix", {}).get("price"); vvix = vvix if vvix is not None else curve.get("vvix")
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
            "vvix_used": round(vvix, 4) if vvix else None,
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
