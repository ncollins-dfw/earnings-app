#!/usr/bin/env python3
"""Earnings price-reaction web app (stdlib server + yfinance).
Window logic is copied from /workspace/nok.py and /workspace/femy.py:
  day 0  = first trading session after the release (release-day session if before the open)
  base   = last close before the reaction
  window = release_date-15 calendar days .. day0+30 calendar days, day_offset = calendar days from day 0
"""
import json, math, os, re, sys, threading, time, traceback
from datetime import datetime, timedelta, time as dtime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
N_RELEASES = 5
CACHE_TTL = 30 * 60          # seconds for good results
ERR_TTL = 2 * 60             # seconds for "not found" type results
_cache, _lock = {}, threading.Lock()
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")

class AppError(Exception):
    def __init__(self, status, code, msg):
        super().__init__(msg); self.status, self.code, self.msg = status, code, msg

def _f(x, nd):
    try:
        x = float(x)
        return None if math.isnan(x) else round(x, nd)
    except Exception:
        return None

def quarter_label(rd):
    """Calendar quarter that ended most recently before the release (approx. reported period)."""
    y, m = rd.year, rd.month
    ends = [(y - 1, 12, 31, 4), (y, 3, 31, 1), (y, 6, 30, 2), (y, 9, 30, 3), (y, 12, 31, 4)]
    best = None
    for yy, mm, dd, q in ends:
        if pd.Timestamp(yy, mm, dd) < rd: best = (q, yy)
    return f"Q{best[0]} {best[1]}"

def classify_time(ts):
    """Yahoo earnings timestamp (America/New_York) -> 'bmo' | 'amc' | 'intraday' | None (unknown)."""
    t = ts.time()
    if t == dtime(0, 0): return None
    if t < dtime(9, 30): return "bmo"
    if t >= dtime(16, 0): return "amc"
    return "intraday"

def get_earnings(tk):
    try:
        ed = yf.Ticker(tk).get_earnings_dates(limit=24)
    except Exception as e:
        msg = repr(e)
        if "RateLimit" in type(e).__name__ or "Too Many" in msg:
            raise AppError(503, "yahoo_rate_limited", "Yahoo is rate-limiting requests. Try again in a minute.")
        if "No earnings dates" in msg or "may be delisted" in msg or "404" in msg:
            raise AppError(404, "no_earnings", f"No earnings dates found for {tk}. Check the ticker (ETFs and funds have none).")
        raise AppError(502, "yahoo_error", f"Yahoo earnings lookup failed: {type(e).__name__}")
    if ed is None or len(ed) == 0:
        raise AppError(404, "no_earnings", f"No earnings dates found for {tk}. Check the ticker (ETFs and funds have none).")
    out = []
    for ts, row in ed.iterrows():
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None: ts = ts.tz_localize("America/New_York")
        else: ts = ts.tz_convert("America/New_York")
        out.append(dict(ts=ts, date=pd.Timestamp(ts.date()), timing=classify_time(ts),
                        eps_est=_f(row.get("EPS Estimate"), 4), eps_act=_f(row.get("Reported EPS"), 4),
                        surprise=_f(row.get("Surprise(%)"), 2)))
    # de-dupe by date, oldest first
    seen, res = set(), []
    for e in sorted(out, key=lambda e: e["ts"]):
        if e["date"] in seen: continue
        seen.add(e["date"]); res.append(e)
    return res

def get_prices(tk, start, end):
    try:
        h = yf.download(tk, start=start, end=end, auto_adjust=False, progress=False, actions=True, threads=False)
    except Exception as e:
        raise AppError(502, "yahoo_error", f"Yahoo price download failed: {type(e).__name__}")
    if h is None or len(h) == 0:
        raise AppError(404, "no_prices", f"No price data from Yahoo for {tk}. The ticker may be wrong or delisted.")
    h.columns = [c[0] if isinstance(c, tuple) else c for c in h.columns]
    h.index = pd.to_datetime(h.index).tz_localize(None)
    h = h.dropna(subset=["Close"])
    if len(h) == 0:
        raise AppError(404, "no_prices", f"No price data from Yahoo for {tk}.")
    return h

def sessions(h, rd, timing):
    """Return (day0, base_date) for a release on rd with given timing."""
    if timing == "amc":   # femy.py
        return h[h.index > rd].index.min(), h[h.index <= rd].index.max()
    d0 = h[h.index >= rd].index.min()   # nok.py (before open / during session)
    return d0, h[h.index < d0].index.max()

def infer_timing(h, rd):
    """Release time unknown: pick the session with the bigger absolute move.
    bmo case -> move of release-day session vs prior close; amc case -> move of next session vs release-day close."""
    on = h[h.index >= rd]; before = h[h.index < rd]
    if len(on) < 1 or len(before) < 1: return "bmo", None
    s_rd = on.index[0]
    if s_rd != rd:   # release on a non-trading day -> both cases give the same day 0
        return "bmo", None
    m_bmo = abs(on.Close.iloc[0] / before.Close.iloc[-1] - 1)
    if len(on) < 2: return "bmo", dict(release_day_move_pct=round(float(m_bmo) * 100, 2), next_day_move_pct=None)
    m_amc = abs(on.Close.iloc[1] / on.Close.iloc[0] - 1)
    return ("amc" if m_amc > m_bmo else "bmo"), dict(release_day_move_pct=round(float(m_bmo) * 100, 2), next_day_move_pct=round(float(m_amc) * 100, 2))

def build(tk, force_infer=False):
    events = get_earnings(tk)
    today = pd.Timestamp(datetime.now().date())
    past = [e for e in events if e["date"] <= today and (e["eps_act"] is not None or e["date"] < today - pd.Timedelta(days=3))]
    if not past:
        raise AppError(404, "no_past_earnings", f"Yahoo has no past earnings releases for {tk}.")
    cand = past[-(N_RELEASES + 1):]
    start = (cand[0]["date"] - pd.Timedelta(days=25)).date().isoformat()
    end = (today + pd.Timedelta(days=1)).date().isoformat()
    h = get_prices(tk, start, end)
    last = h.index.max()
    releases, rows = [], []
    for e in cand:
        rd = e["date"]; timing = None if force_infer else e["timing"]; inferred = False; evidence = None
        if timing is None:
            timing, evidence = infer_timing(h, rd); inferred = True
        d0, base_date = sessions(h, rd, timing)
        if pd.isna(d0) or pd.isna(base_date): continue   # no reaction session yet
        releases.append((e, rd, timing, inferred, evidence, d0, base_date))
    releases = releases[-N_RELEASES:]
    if not releases:
        raise AppError(404, "no_windows", f"Not enough price history around {tk}'s earnings dates.")
    summary = []
    for e, rd, timing, inferred, evidence, d0, base_date in releases:
        d = rd.date().isoformat(); q = quarter_label(rd)
        base = float(h.loc[base_date, "Close"])
        w = h[(h.index >= rd - pd.Timedelta(days=15)) & (h.index <= d0 + pd.Timedelta(days=30))]
        for t, r in w.iterrows():
            rows.append(dict(earnings_date=d, quarter=q, date=t.date().isoformat(), day_offset=int((t - d0).days),
                             open=_f(r.Open, 4), high=_f(r.High, 4), low=_f(r.Low, 4), close=_f(r.Close, 4),
                             adj_close=_f(r.get("Adj Close"), 4), volume=int(r.Volume) if not pd.isna(r.Volume) else None,
                             pct_change_from_close_before_release=_f((r.Close / base - 1) * 100, 3)))
        i0 = h.index.get_loc(d0)
        def at(k):   # k TRADING sessions after day 0 (changed from calendar days in nok.py/femy.py)
            if i0 + k >= len(h): return None, None, None
            t = h.index[i0 + k]; c = h.Close.iloc[i0 + k]
            return t.date().isoformat(), _f((c / base - 1) * 100, 2), _f(c, 4)
        s0, s1, s5, s21 = at(0), at(1), at(5), at(21)
        label = {"bmo": "before open", "amc": "after close", "intraday": "during market hours"}[timing]
        summary.append(dict(quarter=q, release_date=d, release_time_et=None if e["timing"] is None else e["ts"].strftime("%H:%M"),
                            timing=timing, timing_label=label, timing_inferred=inferred, inference_evidence=evidence,
                            day0_date=d0.date().isoformat(), base_close_date=base_date.date().isoformat(), base_close=_f(base, 4),
                            day0_close=s0[2], day0_pct=s0[1], d1_date=s1[0], d1_pct=s1[1], d5_date=s5[0], d5_pct=s5[1], horizon_unit="trading_days",
                            d21_date=s21[0], d21_pct=s21[1], day0_volume=int(h.loc[d0, "Volume"]),
                            eps_estimate=e["eps_est"], eps_reported=e["eps_act"], surprise_pct=e["surprise"],
                            last_date_in_window=w.index.max().date().isoformat()))
    return dict(ticker=tk, generated_at=datetime.now().isoformat(timespec="seconds"), last_price_date=last.date().isoformat(),
                source="Yahoo Finance via yfinance (unofficial)", releases=summary, windows=rows)

def get_cached(tk, force_infer=False):
    key = (tk, force_infer); now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now: return hit[1], hit[2], True
    try:
        data, status = build(tk, force_infer), 200; ttl = CACHE_TTL
    except AppError as e:
        data, status = dict(error=e.code, message=e.msg, ticker=tk), e.status
        ttl = ERR_TTL if e.status == 404 else 0
    except Exception as e:
        traceback.print_exc()
        data, status, ttl = dict(error="server_error", message=f"Unexpected error: {type(e).__name__}", ticker=tk), 500, 0
    if ttl:
        with _lock: _cache[key] = (now + ttl, data, status)
    return data, status, False

class H(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype, extra=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(b)
    do_HEAD = lambda self: self.do_GET()
    def do_GET(self):
        u = urlparse(self.path); qs = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8", {"Cache-Control": "no-cache"})
        if u.path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if u.path == "/api/earnings":
            tk = (qs.get("ticker", [""])[0] or "").strip().upper()
            if not TICKER_RE.match(tk):
                return self._send(400, json.dumps(dict(error="bad_ticker", message="Enter a ticker like AAPL, NOK or BRK-B.")), "application/json")
            force = qs.get("infer", ["0"])[0] == "1"
            data, status, cached = get_cached(tk, force)
            if status == 200: data = dict(data, cached=cached)
            return self._send(status, json.dumps(data, allow_nan=False), "application/json")
        self._send(404, json.dumps(dict(error="not_found")), "application/json")
    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fmt % a))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))  # Render/Heroku-style hosts set PORT
    print(f"serving on http://0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
