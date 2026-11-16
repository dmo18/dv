#!/usr/bin/env python3
"""fetch_prices.py Rev 3

Changelog:
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

Fallback order (plan 5.3):
  Yahoo batch (v7 spark) -> Yahoo per symbol (v8 chart, query1)
  -> Yahoo per symbol on query2 -> Nasdaq API (stocks and funds, Nasdaq 100)
  -> gold-api.com (gold, silver) / Frankfurter (EUR/USD)
  -> keep previous value from previous.json (marked "kept: <reason>").
Crypto comes from Coinbase REST.

Item fields: p price, pc previous close, t epoch of price, open, s points,
and optional h: a price jump candidate waiting for confirmation.

Python standard library only. No keys, no secrets.
Exit code 0 if at least one item was freshly updated, 1 otherwise
(then the output file is not touched), 2 for bad arguments.
"""
import glob
import json
import math
import os
import re
import socket
import sys
import tempfile
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
YAHOO_SINGLE_CAP = 10
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
LAST_STATS = []       # summary lines of the last run


class FetchError(Exception):
    pass


class Blocked(FetchError):
    pass


class OutOfTime(FetchError):
    pass


# ---------------------------------------------------------------- helpers

def good(x):
    """True for a finite number above zero and at most 1e9 (bool excluded)."""
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and 0 < x <= MAX_PRICE)


def good_time(t):
    return (isinstance(t, (int, float)) and not isinstance(t, bool)
            and math.isfinite(t) and 0 < t < 4e9)


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


def install_opener():
    urllib.request.install_opener(urllib.request.build_opener(SafeRedirect()))


def _read_capped(r):
    reader = getattr(r, "read1", None) or r.read
    chunks, size = [], 0
    while True:
        if remaining() <= 0:
            raise OutOfTime("deadline")
        c = reader(65536)
        if not c:
            break
        size += len(c)
        if size > MAX_BODY:
            raise FetchError("reply too big")
        chunks.append(c)
        if reader is r.read and len(c) < 65536:
            # plain read() returns everything it has; one more call ends it
            continue
    return b"".join(chunks)


def _fail(host_key, msg):
    _fails[host_key] = _fails.get(host_key, 0) + 1
    raise FetchError(msg)


def http_json(url, host_key, gap=0.0, budget=None):
    """GET a URL and parse JSON. gap = minimum seconds between calls per host.
    On HTTP 429: wait for Retry-After (capped at 20 s, default 5 s) and try
    once more. At most one such retry per host per run. After 3 failures a
    host gets no more requests this run. No request after the deadline."""
    if HOSTS.get(host_key, (None, host_key))[1] in _blocked:
        raise Blocked("blocked")
    for attempt in (0, 1):
        if _fails.get(host_key, 0) >= MAX_HOST_FAILS:
            raise FetchError("host stopped after %d failures" % MAX_HOST_FAILS)
        if budget is not None:
            if _budget.get(budget, 0) <= 0:
                raise FetchError("request cap reached")
            _budget[budget] -= 1
        if gap:
            wait = min(_last_call.get(host_key, 0) + gap - time.time(), gap)
            if wait > 0:
                if wait >= remaining():
                    raise OutOfTime("deadline")
                time.sleep(wait)
        left = remaining()
        if left <= 0.5:
            raise OutOfTime("deadline")
        _last_call[host_key] = time.time()
        _sent[host_key] = _sent.get(host_key, 0) + 1
        req = urllib.request.Request(url, headers={
            "User-Agent": YAHOO_UA if host_key in ("query1", "query2") else UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=min(TIMEOUT, left)) as r:
                raw = _read_capped(r)
            break
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
                if delay + 1 >= remaining():
                    _fail(host_key, "HTTP 429")
                time.sleep(delay)
                continue
            _fail(host_key, "HTTP %s" % e.code)
        except OutOfTime:
            raise
        except FetchError as e:
            _fail(host_key, str(e))
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                _fail(host_key, "timeout")
            if isinstance(e.reason, FetchError):
                _fail(host_key, str(e.reason))
            _fail(host_key, "network error")
        except (socket.timeout, TimeoutError):
            _fail(host_key, "timeout")
        except Exception as e:
            _fail(host_key, "network " + type(e).__name__)
    try:
        if raw[:1].lstrip() == b"<" or raw.lstrip()[:1] == b"<":
            raise ValueError("html")
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        _fail(host_key, "not JSON")


def clean_series(vals):
    out = []
    if isinstance(vals, list):
        for v in vals[-MAX_POINTS * 4:]:
            if good(v):
                out.append(round(float(v), 6))
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
    p, pc, t, op, s = (v.get("p"), v.get("pc"), v.get("t"), v.get("open"),
                       v.get("s"))
    if not (good(p) and good(pc) and good_time(t) and isinstance(op, bool)
            and isinstance(s, list) and 1 <= len(s) <= MAX_POINTS
            and all(good(x) for x in s)):
        return None
    out = {"p": float(p), "pc": float(pc), "t": int(t), "open": op,
           "s": [float(x) for x in s]}
    if good(v.get("h")):
        out["h"] = float(v["h"])
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
    if not (good(p) and good(pc) and good_time(t)):
        return None
    s = clean_series(s)
    if not s:
        s = [round(float(p), 6)]
    return {"p": round(float(p), 6), "pc": round(float(pc), 6),
            "t": int(t), "open": bool(is_open), "s": s}


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
    if good_time(start) and good_time(end):
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
    d = http_json(url, host)
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
    d = http_json(url, host, gap=0.8, budget="yahoo_single")
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
    for ac in ("stocks", "etf"):
        url = ("https://api.nasdaq.com/api/quote/%s/chart?assetclass=%s"
               % (urllib.parse.quote(symbol, safe=""), ac))
        try:
            d = http_json(url, "nasdaq", gap=0.4)
        except (OutOfTime, Blocked):
            raise
        except FetchError as e:
            last_err = str(e)
            if str(e).startswith("host stopped"):
                break
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
            if good_time(x):
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
    except (OutOfTime, Blocked):
        pass
    except FetchError:
        pass
    item = make_item(p, pc, time.time(), True, series)
    if not item:
        raise FetchError("bad fields")
    return item


# ------------------------------------------------------------------- main

def run(symbols_path, previous_path=None, blocked=(), deadline=None):
    """Fetch everything. Returns the ticker.json dict (always, even if empty).
    deadline: seconds for the whole run (default DEADLINE)."""
    start = time.time()
    _blocked.clear()
    _blocked.update(blocked)
    _retried.clear()
    _fails.clear()
    _sent.clear()
    _budget.clear()
    _budget["yahoo_single"] = YAHOO_SINGLE_CAP
    _deadline[0] = start + (DEADLINE if deadline is None else deadline)
    syms = load_symbols(symbols_path)
    by_id = {it["id"]: it for it in syms}
    prev_all = load_previous(previous_path, set(by_id))
    items, sources, problems = {}, {}, {}

    def note(iid, label, e):
        problems[iid] = "%s %s" % (label, reason_of(e))

    def accept(iid, item, src):
        """Store a fresh item, or hold back a big jump until confirmed."""
        prev = prev_all.get(iid)
        if prev and abs(item["p"] / prev["p"] - 1.0) > MAX_JUMP:
            h = prev.get("h")
            if not (h and abs(item["p"] / h - 1.0) <= CONFIRM):
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

    yahoo_like = lambda i: i["source"] == "yahoo"

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

    # ---- Yahoo per symbol, query1 then query2 (shared cap of 10 requests)
    for host, label in (("query1", "yahoo-single"), ("query2", "yahoo2")):
        for i in pending(yahoo_like):
            attempt(i, label, lambda i=i, host=host: yahoo_chart(i["symbol"], host))

    # ---- Nasdaq (stocks and funds, plus the Nasdaq 100 index)
    for i in pending(lambda i: yahoo_like(i) and i["group"] == "Stocks"):
        attempt(i, "nasdaq", lambda i=i: nasdaq_item(i["symbol"]))
    for i in pending(lambda i: i["symbol"] == "^NDX"):
        attempt(i, "nasdaq", lambda i=i: nasdaq_index_item(
            "NDX", prev_all.get(i["id"])))

    # ---- gold-api and Frankfurter
    for i in pending(lambda i: i["symbol"] in GOLDAPI):
        attempt(i, "goldapi", lambda i=i: goldapi_item(
            i["symbol"], prev_all.get(i["id"])))
    for i in pending(lambda i: i["symbol"] == "EURUSD=X"):
        attempt(i, "frankfurter", lambda i=i: frankfurter_item(
            prev_all.get(i["id"])))

    # ---- Coinbase
    for i in pending(lambda i: i["source"] == "coinbase"):
        attempt(i, "coinbase", lambda i=i: coinbase_item(i["symbol"]))

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
        LAST_STATS.append("source %-11s %-25s requests %2d  failures %d" % (
            name, host, _sent.get(key, 0), _fails.get(key, 0)))
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
            chg = (it["p"] / it["pc"] - 1) * 100
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
    try:
        clean_stale_temps(out_path)
        install_opener()
        doc = run(symbols_path, prev_path, blocked)
        summary(load_symbols(symbols_path), doc)
        for line in LAST_STATS:
            print(line)
        n = fresh_count(doc)
        if n == 0:
            print("nothing could be updated, %s left untouched" % out_path)
            return 1
        write_atomic(out_path, doc)
    except Exception as e:
        print("run failed (%s), %s left untouched" % (
            clean_text(type(e).__name__ + " " + str(e)), out_path))
        return 1
    print("wrote %s: %d fresh, %d total, %d bytes" % (
        out_path, n, len(doc["items"]), len(encoded(doc))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
