#!/usr/bin/env python3
"""fetch_prices.py Rev 4

Changelog:
- Rev 4: fixes after the second review.
  1. Numbers are rounded first and checked after, so a tiny price can no
     longer become zero. Every division is guarded.
  2. Healthy sources can no longer be starved: Coinbase goes first, gold-api
     and Frankfurter are fetched as a reserve right after the Yahoo batch,
     a request timeout stops that host for the rest of the run, and no host
     may use more than 30 percent of the run time.
  3. Hard wall-clock limit on every request (also while headers trickle in),
     plus an alarm in main() at DEADLINE + 15 seconds.
  4. Price time must be after 1e9 and at most 10 minutes ahead of now, for
     fresh values and for the previous file.
  5. At most 10 Yahoo requests per run in total (batch, retries, singles).
  6. A new, changed or cleared jump marker "h" counts as a change, so the
     file is written and the marker is not lost.
  7. Missing or unreadable symbols file gives a clear SymbolsError (exit 2).
     Private URL opener, nothing installed process-wide. EUR/USD time never
     steps back on the Frankfurter fallback. Output file mode 0644.
- Rev 3: hardened after review. Every reply field is type checked and every
  source and item is wrapped, so one odd reply only loses that source. Reply
  bodies capped at 2 MB, deep JSON and non-JSON rejected. Previous file is
  validated and rebuilt from known fields only (bad file = no file). A price
  jump over 40 percent is held until the next run confirms it (field "h").
  Whole run limited to 90 seconds, 10 s per request, at most 3 failures per
  host, at most 10 Yahoo single-symbol requests. Redirects only to https and
  to the hosts below, at most 3 hops. Temp files always cleaned up. "kept"
  carries its reason. One summary line per source.
- Rev 2: Nasdaq fallback for the Nasdaq 100 index, real EUR/USD previous close
  from Frankfurter, polite HTTP 429 handling (Retry-After, one retry per host).
- Rev 1: first version. Reads ticker-symbols.json, writes ticker.json.

Usage:
  python3 fetch_prices.py <ticker-symbols.json> <out.json> [<previous.json>] [--block a,b]

--block lists sources to treat as unreachable (for testing):
  yahoo, yahoo2, nasdaq, goldapi, frankfurter, coinbase

Fallback order (plan 5.3), which value is used:
  Yahoo batch (v7 spark) -> Yahoo per symbol (v8 chart, query1)
  -> Yahoo per symbol on query2 -> Nasdaq API (stocks and funds, Nasdaq 100)
  -> gold-api.com (gold, silver) / Frankfurter (EUR/USD)
  -> keep previous value from previous.json (marked "kept: <reason>").
Crypto comes from Coinbase REST.
Request order (so a hanging host cannot starve the others): Coinbase, Yahoo
batch, then gold-api and Frankfurter as a reserve for items the batch did not
deliver, then Yahoo per symbol, second Yahoo host, Nasdaq. The reserve is
used only for items those slower fallbacks could not deliver.

Item fields: p price, pc previous close, t epoch of price, open, s points,
and optional h: a price jump candidate waiting for confirmation.

Python standard library only. No keys, no secrets.
Exit code 0 if the file was written (at least one item freshly updated, or a
jump marker "h" set, changed or cleared), 1 otherwise (then the output file is
not touched), 2 for bad arguments or an unreadable symbols file.
"""
import glob
import http.client
import json
import math
import os
import re
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
# Yahoo answered a bare "Mozilla/5.0" far more often than a full Chrome string
# from a shared IP (tested), so Yahoo hosts get the short one.
YAHOO_UA = "Mozilla/5.0"
TIMEOUT = 10            # seconds per request
DEADLINE = 90           # seconds for the whole run
MAX_POINTS = 80
MAX_JUMP = 0.40         # bigger moves are held until confirmed
CONFIRM = 0.05          # next run within 5 percent of the held candidate
MAX_PRICE = 1e9
MAX_BYTES = 60 * 1024   # output cap
MAX_BODY = 2 * 1024 * 1024  # reply cap
MAX_PREV = 2 * 1024 * 1024  # previous file cap
MAX_RETRY_WAIT = 20
MAX_HOST_FAILS = 3
YAHOO_CAP = 10          # all Yahoo requests of one run
HOST_SHARE = 0.30       # share of the run time one host may use
MAX_AHEAD = 600         # a price time may be this far ahead of now
MIN_TIME = 1e9
MAX_REDIRECTS = 3
GROUPS = ("Indexes", "Stocks", "FX and commodities", "Crypto")
BLOCKABLE = ("yahoo", "yahoo2", "nasdaq", "goldapi", "frankfurter", "coinbase")
GOLDAPI = {"GC=F": "XAU", "SI=F": "XAG"}
# host_key -> (real host, --block name)
HOSTS = {
    "query1": ("query1.finance.yahoo.com", "yahoo"),
    "query2": ("query2.finance.yahoo.com", "yahoo2"),
    "nasdaq": ("api.nasdaq.com", "nasdaq"),
    "goldapi": ("api.gold-api.com", "goldapi"),
    "frankfurter": ("api.frankfurter.dev", "frankfurter"),
    "coinbase": ("api.exchange.coinbase.com", "coinbase"),
}
LABEL_HOST = {"yahoo": "query1", "yahoo-single": "query1", "yahoo2": "query2",
              "nasdaq": "nasdaq", "goldapi": "goldapi",
              "frankfurter": "frankfurter", "coinbase": "coinbase"}
ALLOWED_HOSTS = frozenset(h for h, _ in HOSTS.values())
NY = None
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:
    pass

# per-run state, reset by run()
_last_call = {}
_retried = set()      # hosts already retried after a 429 in this run
_fails = {}           # host_key -> failures this run
_sent = {}            # host_key -> requests sent this run
_budget = {}          # name -> requests left
_blocked = set()
_deadline = [float("inf")]
_dead = set()         # hosts stopped by a timeout in this run
_spent = {}           # host_key -> seconds used this run
_host_cap = [float("inf")]   # seconds one host may use (set during run())
_opener = [None]      # private URL opener, never installed process-wide
_live = {}            # thread id -> open connection (for the hard limit)
_STD_URLOPEN = urllib.request.urlopen
LAST_STATS = []       # summary lines of the last run
LAST_HELD = [0]       # jump markers set, changed or cleared by the last run


class FetchError(Exception):
    pass


class Skip(FetchError):
    """Not tried at all (blocked, deadline, cap, host stopped)."""
    pass


class Blocked(Skip):
    pass


class OutOfTime(Skip):
    pass


class Timeout(FetchError):
    pass


class SymbolsError(ValueError):
    """The symbols file is missing or unreadable."""
    pass


class _Alarm(BaseException):
    pass


# ---------------------------------------------------------------- helpers

def good(x):
    """True for a finite number above zero and at most 1e9 (bool excluded)."""
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and 0 < x <= MAX_PRICE)


def r6(x):
    """Round to 6 places first, then check. None if not a good number."""
    if not good(x):
        return None
    v = round(float(x), 6)
    return v if good(v) else None


def epoch_like(t):
    """Loose check for helper times (session start and end, chart points)."""
    return (isinstance(t, (int, float)) and not isinstance(t, bool)
            and math.isfinite(t) and 0 < t < 4e9)


def good_time(t):
    """A price time: after 1e9 and at most 10 minutes ahead of now."""
    return epoch_like(t) and MIN_TIME < t <= time.time() + MAX_AHEAD


def num(x):
    """Number or numeric string -> float, else None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    elif isinstance(x, str) and len(x) <= 40:
        try:
            v = float(x.replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    return v if math.isfinite(v) else None


def dig(d, *path):
    """Walk dicts (str keys) and lists (int keys). None on any mismatch."""
    for k in path:
        if isinstance(k, int):
            if not isinstance(d, list) or not -len(d) <= k < len(d):
                return None
        elif not isinstance(d, dict):
            return None
        else:
            d = d.get(k)
            continue
        d = d[k]
    return d


def clean_text(s, limit=80):
    s = re.sub(r"[^A-Za-z0-9 .,:;_=/%()+-]", " ", str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def reason_of(e):
    if isinstance(e, FetchError):
        return str(e) or "error"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timeout"
    return "bad reply " + type(e).__name__


def remaining():
    return _deadline[0] - time.time()


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to https and only to our own hosts."""
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" or (u.hostname or "") not in ALLOWED_HOSTS:
            raise FetchError("redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Tracked:
    """Remembers the connection of the current thread so the hard limit can
    close its socket."""
    def connect(self):
        _live[threading.get_ident()] = self
        super().connect()


class _HTTPSConn(_Tracked, http.client.HTTPSConnection):
    pass


class _HTTPConn(_Tracked, http.client.HTTPConnection):
    pass


class _HTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConn, req,
                            context=getattr(self, "_context", None))


class _HTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConn, req)


def install_opener():
    """Build the private opener. Nothing is installed process-wide."""
    _opener[0] = urllib.request.build_opener(SafeRedirect(), _HTTPS(), _HTTP())


def _open(req, timeout):
    """Open with the private opener. A urlopen replaced by the caller (tests)
    is respected."""
    fn = urllib.request.urlopen
    if fn is not _STD_URLOPEN:
        return fn(req, timeout=timeout)
    if _opener[0] is None:
        install_opener()
    return _opener[0].open(req, timeout=timeout)


def _fetch(req, timeout, started):
    """One request with a hard wall-clock limit that also covers the status
    line and the headers. The request runs in a helper thread. If it is not
    done in time its socket is closed and it counts as a timeout."""
    box = {}

    def work():
        try:
            with _open(req, timeout) as r:
                box["raw"] = _read_capped(r, started)
        except BaseException as e:
            box["err"] = e
        finally:
            _live.pop(threading.get_ident(), None)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout + 0.5)
    if th.is_alive():
        sock = getattr(_live.pop(th.ident, None), "sock", None)
        for close in (lambda: sock.shutdown(socket.SHUT_RDWR),
                      lambda: sock.close()):
            try:
                close()
            except Exception:
                pass
        raise Timeout("timeout")
    if "err" in box:
        raise box["err"]
    return box["raw"]


def _read_capped(r, started):
    """Read at most MAX_BODY bytes. A real response is read in pieces so a
    slow trickle is cut off after TIMEOUT seconds or at the deadline."""
    reader = getattr(r, "read1", None)
    if reader is None:
        raw = r.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise FetchError("reply too big")
        return raw
    chunks, size = [], 0
    while True:
        c = reader(65536)
        if not c:
            break
        size += len(c)
        if size > MAX_BODY:
            raise FetchError("reply too big")
        chunks.append(c)
        if time.time() - started > TIMEOUT:
            raise Timeout("timeout")
        if remaining() <= 0:
            raise OutOfTime("deadline")
    return b"".join(chunks)


def _fail(host_key, msg):
    _fails[host_key] = _fails.get(host_key, 0) + 1
    e = FetchError(msg)
    e.counted = True
    raise e


def _timed_out(host_key):
    """A timeout stops the host for the rest of the run."""
    _dead.add(host_key)
    _fails[host_key] = max(_fails.get(host_key, 0) + 1, MAX_HOST_FAILS)
    e = Timeout("timeout")
    e.counted = True
    raise e


def http_json(url, host_key, gap=0.0, budget=None):
    """GET a URL and parse JSON. gap = minimum seconds between calls per host.
    On HTTP 429: wait for Retry-After (capped at 20 s, default 5 s) and try
    once more. At most one such retry per host per run. After 3 failures, or
    after one timeout, a host gets no more requests this run. A host may use
    at most 30 percent of the run time. No request after the deadline."""
    if HOSTS.get(host_key, (None, host_key))[1] in _blocked:
        raise Blocked("blocked")
    t_in = time.time()
    used = _spent.get(host_key, 0.0)

    def host_left():
        return _host_cap[0] - used - (time.time() - t_in)

    try:
        raw = _request(url, host_key, gap, budget, host_left)
    finally:
        _spent[host_key] = used + max(0.0, time.time() - t_in)
    try:
        if raw.lstrip()[:1] not in (b"{", b"["):
            raise ValueError("not JSON")
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        _fail(host_key, "not JSON")


def _request(url, host_key, gap, budget, host_left):
    for attempt in (0, 1):
        if _fails.get(host_key, 0) >= MAX_HOST_FAILS:
            if host_key in _dead:
                raise Skip("host stopped after timeout")
            raise Skip("host stopped after %d failures" % MAX_HOST_FAILS)
        if host_left() <= 0.5:
            raise Skip("host time share used")
        if budget is not None:
            if _budget.get(budget, 0) <= 0:
                raise Skip("request cap reached")
            _budget[budget] -= 1
        if gap:
            wait = min(_last_call.get(host_key, 0) + gap - time.time(), gap)
            if wait > 0:
                if wait >= remaining():
                    raise OutOfTime("deadline")
                if wait >= host_left():
                    raise Skip("host time share used")
                time.sleep(wait)
        left = remaining()
        if left <= 0.5:
            raise OutOfTime("deadline")
        limit = min(TIMEOUT, left, max(host_left(), 0.5))
        started = _last_call[host_key] = time.time()
        _sent[host_key] = _sent.get(host_key, 0) + 1
        req = urllib.request.Request(url, headers={
            "User-Agent": YAHOO_UA if host_key in ("query1", "query2") else UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9"})
        try:
            return _fetch(req, limit, started)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0 and host_key not in _retried:
                _retried.add(host_key)
                try:
                    delay = float(e.headers.get("Retry-After", ""))
                    if not math.isfinite(delay):
                        delay = 5.0
                except (TypeError, ValueError, AttributeError):
                    delay = 5.0
                delay = max(0.0, min(delay, MAX_RETRY_WAIT))
                if delay + 1 >= min(remaining(), host_left()):
                    _fail(host_key, "HTTP 429")
                time.sleep(delay)
                continue
            _fail(host_key, "HTTP %s" % e.code)
        except Skip:
            raise
        except Timeout:
            _timed_out(host_key)
        except FetchError as e:
            _fail(host_key, str(e))
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                _timed_out(host_key)
            if isinstance(e.reason, FetchError):
                _fail(host_key, str(e.reason))
            _fail(host_key, "network error")
        except (socket.timeout, TimeoutError):
            _timed_out(host_key)
        except Exception as e:
            _fail(host_key, "network " + type(e).__name__)
    _fail(host_key, "HTTP 429")


def clean_series(vals):
    out = []
    if isinstance(vals, list):
        for v in vals[-MAX_POINTS * 4:]:
            v = r6(v)
            if v is not None:
                out.append(v)
    return out[-MAX_POINTS:]


def load_symbols(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    items = []
    seen = set()
    for it in (d.get("items") if isinstance(d, dict) else None) or []:
        if (not isinstance(it, dict) or not isinstance(it.get("id"), str)
                or it["id"] in seen
                or it.get("group") not in GROUPS
                or it.get("source") not in ("yahoo", "coinbase")
                or not isinstance(it.get("symbol"), str)):
            continue
        if not isinstance(it.get("decimals"), int) or isinstance(
                it.get("decimals"), bool) or not 0 <= it["decimals"] <= 8:
            it = dict(it, decimals=2)
        if not isinstance(it.get("name"), str):
            it = dict(it, name=it["id"])
        seen.add(it["id"])
        items.append(it)
    return items


def rebuild_prev(v):
    """A clean copy of a previous item from known fields only, or None."""
    if not isinstance(v, dict):
        return None
    p, pc, t, op, s = (r6(v.get("p")), r6(v.get("pc")), v.get("t"),
                       v.get("open"), v.get("s"))
    if not (p is not None and pc is not None and good_time(t)
            and isinstance(op, bool)
            and isinstance(s, list) and 1 <= len(s) <= MAX_POINTS):
        return None
    s = [r6(x) for x in s]
    if None in s:
        return None
    out = {"p": p, "pc": pc, "t": int(t), "open": op, "s": s}
    h = r6(v.get("h"))
    if h is not None:
        out["h"] = h
    return out


def load_previous(path, ids):
    """Previous items, only for known ids and only if valid. A file of the
    wrong shape counts as no file."""
    try:
        if not path or not os.path.isfile(path):
            return {}
        if os.path.getsize(path) > MAX_PREV:
            return {}
        with open(path, encoding="utf-8") as f:
            d = json.loads(f.read())
        raw = d.get("items") if isinstance(d, dict) else None
        if not isinstance(raw, dict):
            return {}
        out = {}
        for k, v in raw.items():
            if k in ids:
                item = rebuild_prev(v)
                if item:
                    out[k] = item
        return out
    except (Exception, RecursionError):
        return {}


def make_item(p, pc, t, is_open, s):
    """Build a candidate item or return None if any number is bad."""
    p, pc = r6(p), r6(pc)
    if p is None or pc is None or not good_time(t):
        return None
    s = clean_series(s) or [p]
    return {"p": p, "pc": pc, "t": int(t), "open": bool(is_open), "s": s}


# ------------------------------------------------------------------ Yahoo

def yahoo_parse(res, now):
    """res = {meta, timestamp, indicators} -> item or None."""
    meta = dig(res, "meta")
    if not isinstance(meta, dict):
        return None
    p = meta.get("regularMarketPrice")
    pc = meta.get("chartPreviousClose")
    if not good(pc):
        pc = meta.get("previousClose")
    t = meta.get("regularMarketTime")
    closes = dig(res, "indicators", "quote", 0, "close")
    start = dig(meta, "currentTradingPeriod", "regular", "start")
    end = dig(meta, "currentTradingPeriod", "regular", "end")
    if epoch_like(start) and epoch_like(end):
        is_open = start <= now <= end
    else:
        is_open = good_time(t) and now - t < 900
    if good_time(t) and now - t > 3600:
        is_open = False
    return make_item(p, pc, t, is_open, closes)


def yahoo_batch(symbols, host):
    """One spark request for many symbols. Returns {symbol: item}."""
    q = urllib.parse.quote(",".join(symbols), safe=",")
    url = ("https://%s.finance.yahoo.com/v7/finance/spark?symbols=%s"
           "&range=1d&interval=5m&includePrePost=false" % (host, q))
    d = http_json(url, host, budget="yahoo")
    now = time.time()
    results = dig(d, "spark", "result")
    if not isinstance(results, list):
        raise FetchError("spark shape")
    out = {}
    for r in results:
        sym = dig(r, "symbol")
        if not isinstance(sym, str) or sym not in symbols:
            continue
        try:
            item = yahoo_parse(dig(r, "response", 0), now)
        except Exception:
            item = None
        if item:
            out[sym] = item
    return out


def yahoo_chart(symbol, host):
    url = ("https://%s.finance.yahoo.com/v8/finance/chart/%s"
           "?range=1d&interval=5m&includePrePost=false"
           % (host, urllib.parse.quote(symbol, safe="")))
    d = http_json(url, host, gap=0.8, budget="yahoo")
    res = dig(d, "chart", "result", 0)
    if not isinstance(res, dict):
        raise FetchError("chart shape")
    item = yahoo_parse(res, time.time())
    if not item:
        raise FetchError("chart fields")
    return item


# ----------------------------------------------------------------- Nasdaq

def nasdaq_item(symbol):
    last_err = "no data"
    counted = None
    for ac in ("stocks", "etf"):
        url = ("https://api.nasdaq.com/api/quote/%s/chart?assetclass=%s"
               % (urllib.parse.quote(symbol, safe=""), ac))
        try:
            d = http_json(url, "nasdaq", gap=0.4)
        except Skip:
            raise
        except FetchError as e:
            last_err = str(e)
            counted = e
            continue
        data = dig(d, "data")
        chart = dig(data, "chart")
        if not isinstance(chart, list) or not chart:
            continue
        pts = []
        for c in chart[-2000:]:
            try:
                x, y = num(dig(c, "x")), num(dig(c, "y"))
                if x is None or not good(y):
                    continue
                x = ny_wall_to_epoch(x / 1000.0)
            except Exception:
                continue
            if epoch_like(x):
                pts.append((x, y))
        if not pts:
            continue
        pts.sort()
        t = pts[-1][0]
        p = num(dig(data, "lastSalePrice"))
        if not good(p):
            p = pts[-1][1]
        pc = num(dig(data, "previousClose"))
        # regular session only for the little chart, 5-minute buckets
        sess = [(x, y) for x, y in pts if in_regular_session(x)] or pts
        buckets = {}
        for x, y in sess:
            buckets[int(x // 300)] = y
        series = [buckets[k] for k in sorted(buckets)]
        is_open = in_regular_session(time.time()) and time.time() - t < 1200
        item = make_item(p, pc, t, is_open, series)
        if item:
            return item
        last_err = "bad fields"
    if counted is not None and last_err == str(counted):
        raise counted
    raise FetchError(last_err)


def ny_wall_to_epoch(x):
    """Nasdaq's chart 'x' is New York wall-clock time written as if it were
    UTC (4:00 AM ET comes as 04:00 UTC). Convert to a real epoch."""
    if NY is None:
        return x + 5 * 3600
    naive = datetime.fromtimestamp(x, timezone.utc).replace(tzinfo=None)
    return naive.replace(tzinfo=NY).timestamp()


def in_regular_session(epoch):
    """US regular session 9:30-16:00 New York time, Monday to Friday."""
    if NY is None:
        return True
    try:
        dt = datetime.fromtimestamp(epoch, NY)
    except Exception:
        return False
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return 570 <= m < 960


def nasdaq_index_item(symbol, prev):
    """Nasdaq 100 only (other indexes return 400 there). Price and previous
    close only, so the little chart reuses the previous series. If the trade
    time has no clock time, the previous t is used (no previous: failure)."""
    url = ("https://api.nasdaq.com/api/quote/%s/info?assetclass=index"
           % urllib.parse.quote(symbol, safe=""))
    d = http_json(url, "nasdaq", gap=0.4)
    data = dig(d, "data")
    pd = dig(data, "primaryData")
    if not isinstance(pd, dict):
        raise FetchError("no data")
    p = num(pd.get("lastSalePrice"))
    pc = num(dig(data, "keyStats", "previousclose", "value"))
    t = None
    stamp = pd.get("lastTradeTimestamp")
    if isinstance(stamp, str) and len(stamp) <= 40 and NY is not None:
        stamp = re.sub(r"\s+ET$", "", stamp.strip())
        try:
            t = datetime.strptime(stamp, "%b %d, %Y %I:%M %p").replace(
                tzinfo=NY).timestamp()
        except ValueError:
            t = None
    if t is not None and t > time.time() + 600:
        t = None
    if t is None:
        if not prev:
            raise FetchError("no trade time")
        t = prev["t"]
    is_open = dig(data, "marketStatus") == "Open" and time.time() - t < 1800
    item = make_item(p, pc, t, is_open, prev["s"] if prev else [])
    if not item:
        raise FetchError("bad fields")
    return item


# -------------------------------------------------- gold-api and Frankfurter

def goldapi_item(symbol, prev):
    # Limitation: gold-api.com gives a price only. pc and s are reused from the
    # previous file, so pc equals p only on a first run (no previous file).
    code = GOLDAPI[symbol]
    d = http_json("https://api.gold-api.com/price/%s" % code, "goldapi")
    if not isinstance(d, dict):
        raise FetchError("gold shape")
    p = d.get("price")
    t = time.time()
    if isinstance(d.get("updatedAt"), str) and len(d["updatedAt"]) <= 40:
        try:
            t = datetime.strptime(d["updatedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return fallback_item(p, t, True, prev)


def frankfurter_item(prev):
    d = http_json("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD",
                  "frankfurter")
    p = dig(d, "rates", "USD")
    date = dig(d, "date")
    if not good(p):
        raise FetchError("bad price")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise FetchError("bad date")
    try:
        day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise FetchError("bad date")
    t = day.timestamp()
    if not good_time(t) or t > time.time() + 86400:
        raise FetchError("bad date")
    # previous business day rate (one extra request) gives a real pc
    back = day - timedelta(days=1)
    while back.weekday() >= 5:
        back -= timedelta(days=1)
    pc = None
    try:
        d2 = http_json("https://api.frankfurter.dev/v1/%s?base=EUR&symbols=USD"
                       % back.strftime("%Y-%m-%d"), "frankfurter")
        pc = dig(d2, "rates", "USD")
    except FetchError:
        pass
    if not good(pc):
        pc = prev["pc"] if prev else p
    # the date has no clock time: never let t step back behind the previous t
    if prev and prev["t"] > t:
        t = prev["t"]
    item = make_item(p, pc, t, False, prev["s"] if prev else [])
    if not item:
        raise FetchError("bad fields")
    return item


def fallback_item(p, t, is_open, prev):
    """These sources give a price only. Reuse previous close and series."""
    if not good(p):
        raise FetchError("bad price")
    pc = prev["pc"] if prev else p
    s = list(prev["s"]) if prev else []
    item = make_item(p, pc, t, is_open, s)
    if not item:
        raise FetchError("bad fields")
    return item


# --------------------------------------------------------------- Coinbase

def coinbase_item(product):
    base = ("https://api.exchange.coinbase.com/products/%s"
            % urllib.parse.quote(product, safe="-"))
    st = http_json(base + "/stats", "coinbase")
    p = num(dig(st, "last"))
    pc = num(dig(st, "open"))
    if not (good(p) and good(pc)):
        raise FetchError("bad fields")
    series = []
    try:
        cs = http_json(base + "/candles?granularity=900", "coinbase", gap=0.3)
        if isinstance(cs, list):
            rows = []
            for r in cs[:1000]:
                if isinstance(r, list) and len(r) >= 5:
                    ts, c = num(r[0]), num(r[4])
                    if ts is not None and good(c):
                        rows.append((ts, c))
            rows.sort()
            series = [c for _, c in rows]
    except FetchError:
        pass
    item = make_item(p, pc, time.time(), True, series)
    if not item:
        raise FetchError("bad fields")
    return item


# ------------------------------------------------------------------- main

def run(symbols_path, previous_path=None, blocked=(), deadline=None):
    """Fetch everything. Returns the ticker.json dict (always, even if empty).
    deadline: seconds for the whole run (default DEADLINE).
    Raises SymbolsError (a ValueError) if the symbols file is missing or
    unreadable. Nothing else is raised for bad data or network trouble.
    should_write(doc) tells whether the result is worth writing."""
    try:
        syms = load_symbols(symbols_path)
    except RecursionError:
        raise SymbolsError("symbols file unreadable: too deep")
    except (OSError, ValueError, TypeError) as e:
        raise SymbolsError("symbols file unreadable: %s"
                           % clean_text(type(e).__name__))
    start = time.time()
    total = DEADLINE if deadline is None else deadline
    _blocked.clear()
    _blocked.update(blocked)
    _retried.clear()
    _fails.clear()
    _sent.clear()
    _dead.clear()
    _spent.clear()
    _budget.clear()
    _budget["yahoo"] = YAHOO_CAP
    _deadline[0] = start + total
    _host_cap[0] = HOST_SHARE * total
    LAST_HELD[0] = 0
    install_opener()
    try:
        return _run(syms, previous_path, start)
    finally:
        _host_cap[0] = float("inf")


def _run(syms, previous_path, start):
    by_id = {it["id"]: it for it in syms}
    prev_all = load_previous(previous_path, set(by_id))
    items, sources, problems = {}, {}, {}

    def note(iid, label, e):
        problems[iid] = "%s %s" % (label, reason_of(e))

    def accept(iid, item, src):
        """Store a fresh item, or hold back a big jump until confirmed."""
        prev = prev_all.get(iid)
        if (prev and prev["p"] > 0
                and abs(item["p"] / prev["p"] - 1.0) > MAX_JUMP):
            h = prev.get("h")
            if not (h and h > 0 and abs(item["p"] / h - 1.0) <= CONFIRM):
                kept = dict(prev, s=list(prev["s"]), h=item["p"])
                items[iid] = kept
                sources[iid] = "kept: jump held, %s" % src
                return
        items[iid] = item
        sources[iid] = src

    def pending(test):
        return [i for i in syms if i["id"] not in sources and test(i)]

    def attempt(i, label, fn):
        try:
            accept(i["id"], fn(), label)
        except Exception as e:
            note(i["id"], label, e)
            # an unusable reply also counts toward the 3 failures of a host
            if not isinstance(e, Skip) and not getattr(e, "counted", False):
                key = LABEL_HOST[label]
                _fails[key] = _fails.get(key, 0) + 1

    reserve, reserve_err = {}, {}

    def stash(i, label, fn):
        """Like attempt, but the value is only used if nothing better comes."""
        try:
            reserve[i["id"]] = (fn(), label)
        except Exception as e:
            reserve_err[i["id"]] = "%s %s" % (label, reason_of(e))
            if not isinstance(e, Skip) and not getattr(e, "counted", False):
                key = LABEL_HOST[label]
                _fails[key] = _fails.get(key, 0) + 1

    yahoo_like = lambda i: i["source"] == "yahoo"

    # ---- Coinbase first: independent of everything else
    for i in pending(lambda i: i["source"] == "coinbase"):
        attempt(i, "coinbase", lambda i=i: coinbase_item(i["symbol"]))

    # ---- Yahoo batch
    ysyms = [i for i in syms if yahoo_like(i)]
    if ysyms:
        try:
            got = yahoo_batch([i["symbol"] for i in ysyms], "query1")
            for i in ysyms:
                if i["symbol"] in got:
                    accept(i["id"], got[i["symbol"]], "yahoo")
                else:
                    problems[i["id"]] = "yahoo missing in batch"
        except Exception as e:
            for i in ysyms:
                note(i["id"], "yahoo", e)

    # ---- gold-api and Frankfurter now, as a reserve (cheap, and a hanging
    # Yahoo or Nasdaq cannot starve them). Used further down.
    for i in pending(lambda i: i["symbol"] in GOLDAPI):
        stash(i, "goldapi", lambda i=i: goldapi_item(
            i["symbol"], prev_all.get(i["id"])))
    for i in pending(lambda i: i["symbol"] == "EURUSD=X"):
        stash(i, "frankfurter", lambda i=i: frankfurter_item(
            prev_all.get(i["id"])))

    # ---- Yahoo per symbol, query1 then query2. All Yahoo requests of a run
    # (batch, retries, singles) share one cap of 10.
    # Items no other source can supply go first, so the cap does not use
    # up the requests on stocks that Nasdaq can still deliver.
    def no_other_source(i):
        return not (i["group"] == "Stocks" or i["symbol"] == "^NDX"
                    or i["symbol"] in GOLDAPI or i["symbol"] == "EURUSD=X")
    for host, label in (("query1", "yahoo-single"), ("query2", "yahoo2")):
        todo = sorted(pending(yahoo_like), key=lambda i: not no_other_source(i))
        for i in todo:
            attempt(i, label, lambda i=i, host=host: yahoo_chart(i["symbol"], host))

    # ---- Nasdaq (Nasdaq 100 index first, then stocks and funds)
    for i in pending(lambda i: i["symbol"] == "^NDX"):
        attempt(i, "nasdaq", lambda i=i: nasdaq_index_item(
            "NDX", prev_all.get(i["id"])))
    for i in pending(lambda i: yahoo_like(i) and i["group"] == "Stocks"):
        attempt(i, "nasdaq", lambda i=i: nasdaq_item(i["symbol"]))

    # ---- gold-api and Frankfurter: use the reserve for what is still missing
    for i in pending(lambda i: i["id"] in reserve):
        accept(i["id"], *reserve[i["id"]])
    for i in pending(lambda i: i["id"] in reserve_err):
        problems[i["id"]] = reserve_err[i["id"]]

    # ---- keep previous for anything still missing
    for i in syms:
        iid = i["id"]
        if iid in sources:
            continue
        why = problems.get(iid, "no source answered")
        if iid in prev_all:
            items[iid] = dict(prev_all[iid], s=list(prev_all[iid]["s"]))
            sources[iid] = clean_text("kept: " + why)
        else:
            sources[iid] = clean_text("error: " + why)
    for iid in sources:
        sources[iid] = clean_text(sources[iid])

    LAST_HELD[0] = sum(
        1 for iid in by_id
        if (items.get(iid) or {}).get("h") != (prev_all.get(iid) or {}).get("h"))
    doc = {"generated": int(time.time()), "items": items, "sources": sources}
    shrink_to_fit(doc)
    stats(doc, time.time() - start)
    return doc


def stats(doc, secs):
    """One summary line per source, kept in LAST_STATS."""
    counts = {}
    for v in doc["sources"].values():
        k = v.split(":")[0]
        counts[k] = counts.get(k, 0) + 1
    LAST_STATS[:] = []
    for key, (host, name) in HOSTS.items():
        LAST_STATS.append("source %-11s %-25s requests %2d  failures %d%s" % (
            name, host, _sent.get(key, 0), _fails.get(key, 0),
            "  timed out" if key in _dead else ""))
    for k in ("yahoo", "yahoo-single", "yahoo2", "nasdaq", "goldapi",
              "frankfurter", "coinbase", "kept", "error"):
        LAST_STATS.append("items %-12s %d" % (k, counts.get(k, 0)))
    LAST_STATS.append("run took %.1f s" % secs)


def encoded(doc):
    return json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf-8")


def shrink_to_fit(doc):
    n = MAX_POINTS
    while len(encoded(doc)) >= MAX_BYTES and n > 1:
        n //= 2
        for it in doc["items"].values():
            it["s"] = it["s"][-n:]
    if len(encoded(doc)) >= MAX_BYTES:
        raise RuntimeError("output too large")


def fresh_count(doc):
    return sum(1 for v in doc["sources"].values()
               if not v.startswith(("error", "kept")))


def should_write(doc):
    """True if the last run() changed something worth saving: a fresh item,
    or a jump marker "h" that was set, changed or cleared."""
    return fresh_count(doc) > 0 or LAST_HELD[0] > 0


def clean_stale_temps(path):
    d = os.path.dirname(os.path.abspath(path))
    for f in glob.glob(os.path.join(glob.escape(d), ".ticker-*.tmp")):
        try:
            os.unlink(f)
        except OSError:
            pass


def write_atomic(path, doc):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".ticker-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded(doc))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def summary(syms, doc):
    for i in syms:
        iid = i["id"]
        it = doc["items"].get(iid)
        src = doc["sources"].get(iid)
        if it:
            chg = (it["p"] / it["pc"] - 1) * 100 if it["pc"] > 0 else 0.0
            print("%-7s %-12s %12.*f  %+6.2f%%  t=%d  %s  pts=%d  [%s]" % (
                iid, i["name"][:12], i["decimals"], it["p"], chg, it["t"],
                "open" if it["open"] else "closed", len(it["s"]), src))
        else:
            print("%-7s %-12s %s" % (iid, i["name"][:12], src))


def main(argv):
    args, blocked = [], []
    k = 0
    while k < len(argv):
        if argv[k] == "--block" and k + 1 < len(argv):
            blocked = [b for b in argv[k + 1].split(",") if b]
            k += 2
        elif argv[k].startswith("--block="):
            blocked = [b for b in argv[k][8:].split(",") if b]
            k += 1
        else:
            args.append(argv[k])
            k += 1
    bad = [b for b in blocked if b not in BLOCKABLE]
    if len(args) < 2 or len(args) > 3 or bad:
        print(__doc__)
        if bad:
            print("unknown --block names: %s" % ",".join(bad))
        return 2
    symbols_path, out_path = args[0], args[1]
    prev_path = args[2] if len(args) == 3 else (
        out_path if os.path.exists(out_path) else None)
    # last line of defence: a hard stop a little after the run deadline
    armed, old = False, None
    if hasattr(signal, "SIGALRM"):
        try:
            old = signal.signal(signal.SIGALRM, _on_alarm)
            signal.alarm(int(DEADLINE + 15))
            armed = True
        except ValueError:      # not the main thread
            pass
    try:
        return _main(symbols_path, out_path, prev_path, blocked)
    except _Alarm:
        print("run stopped by the alarm, %s left untouched" % out_path)
        return 1
    finally:
        if armed:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)


def _on_alarm(signum, frame):
    raise _Alarm()


def _main(symbols_path, out_path, prev_path, blocked):
    try:
        clean_stale_temps(out_path)
        doc = run(symbols_path, prev_path, blocked)
        summary(load_symbols(symbols_path), doc)
        for line in LAST_STATS:
            print(line)
        n = fresh_count(doc)
        if not should_write(doc):
            print("nothing could be updated, %s left untouched" % out_path)
            return 1
        write_atomic(out_path, doc)
    except SymbolsError as e:
        print("%s, %s left untouched" % (clean_text(str(e)), out_path))
        return 2
    except Exception as e:
        print("run failed (%s), %s left untouched" % (
            clean_text(type(e).__name__ + " " + str(e)), out_path))
        return 1
    print("wrote %s: %d fresh, %d total, %d bytes" % (
        out_path, n, len(doc["items"]), len(encoded(doc))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
