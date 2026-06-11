"""
build_ohlc.py — generate svxy_ohlc.json (daily candlesticks) for the dashboard.

Self-contained: fetches SVXY daily Open/High/Low/Close from Yahoo (public data),
writes a compact array consumed by the Summary page candlestick chart.
No strategy logic or parameters here.

Output format (compact, date-keyed):
  [ ["YYYY-MM-DD", open, high, low, close], ... ]

Run:
    python build_ohlc.py
"""
from __future__ import annotations
import json, urllib.request, urllib.parse
from datetime import datetime, timezone

HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

def _get(url, timeout=30, extra=None):
    h = {**HDRS, **(extra or {})}
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        import gzip
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data

def fetch_ohlc(symbol="SVXY", start="2011-10-01"):
    try:
        with urllib.request.urlopen(
            urllib.request.Request("https://finance.yahoo.com/", headers=HDRS), timeout=10) as r:
            cookie = r.headers.get("Set-Cookie", "").split(";")[0]
    except Exception:
        cookie = ""
    t1 = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    t2 = int(datetime.now(tz=timezone.utc).timestamp()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?period1={t1}&period2={t2}&interval=1d")
    data = json.loads(_get(url, extra={"Cookie": cookie} if cookie else None))
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    o, h, l, c = q["open"], q["high"], q["low"], q["close"]
    rows = []
    for i, t in enumerate(ts):
        if None in (o[i], h[i], l[i], c[i]):
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        rows.append([d, round(o[i], 4), round(h[i], 4), round(l[i], 4), round(c[i], 4)])
    return rows

def main():
    rows = fetch_ohlc()
    with open("svxy_ohlc.json", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, separators=(",", ":"))
    print(f"Wrote svxy_ohlc.json: {len(rows)} daily bars "
          f"({rows[0][0]} .. {rows[-1][0]}), last close={rows[-1][4]}")

if __name__ == "__main__":
    main()
