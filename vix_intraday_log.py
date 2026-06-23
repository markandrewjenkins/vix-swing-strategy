"""
vix_intraday_log.py — append a 5-minute snapshot of VIX9D and VIX1M (the VIX spot)
to vix_intraday_log.csv, so we accumulate the intraday-velocity dataset needed to
research speed-of-change signals (e.g. a fast spike that falters on a 5-min basis).

Self-contained (urllib + Yahoo v8). Run it on the same ~5-min cron as update_live.py
(pre-market through after-hours). It only appends one row per run and de-dups by the
Yahoo quote timestamp, so re-runs in the same 5-min bucket won't double-log.

CSV columns: ts_utc, vix9d, vix9d_time, vix1m, vix1m_time
"""
import csv, json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG  = os.path.join(ROOT, "vix_intraday_log.csv")
HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}


def _quote(symbol: str):
    """(price, regularMarketTime-iso) from Yahoo's 5-min chart, or (None, None)."""
    try:
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    "https://finance.yahoo.com/", headers=HDRS), timeout=10) as r:
                cookie = r.headers.get("Set-Cookie", "").split(";")[0]
        except Exception:
            cookie = ""
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               + urllib.parse.quote(symbol) + "?range=1d&interval=5m")
        h = dict(HDRS)
        if cookie:
            h["Cookie"] = cookie
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
            meta = json.loads(r.read())["chart"]["result"][0]["meta"]
        px = meta.get("regularMarketPrice")
        t  = meta.get("regularMarketTime")
        return (round(px, 4) if px is not None else None,
                datetime.fromtimestamp(t, tz=timezone.utc).isoformat() if t else None)
    except Exception as e:
        print(f"  {symbol}: {e}")
        return None, None


def main():
    v9, v9t = _quote("^VIX9D")
    v1, v1t = _quote("^VIX")
    if v9 is None and v1 is None:
        print("no quotes — skip"); return
    # De-dup: skip if the last logged row has the same VIX quote timestamps.
    if os.path.exists(LOG):
        try:
            with open(LOG) as f:
                last = list(csv.reader(f))[-1]
            if len(last) >= 5 and last[2] == (v9t or "") and last[4] == (v1t or ""):
                print(f"{v9t}/{v1t}: unchanged — skip"); return
        except Exception:
            pass
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts_utc", "vix9d", "vix9d_time", "vix1m", "vix1m_time"])
        w.writerow([datetime.now(timezone.utc).isoformat(), v9, v9t or "", v1, v1t or ""])
    print(f"logged vix9d={v9} vix1m={v1}")


if __name__ == "__main__":
    main()
