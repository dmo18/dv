#!/usr/bin/env python3
"""fetch_prices.py Rev 2

Changelog:
- Rev 2: Nasdaq fallback for the Nasdaq 100 index, real EUR/USD previous close
  from Frankfurter, polite HTTP 429 handling (Retry-After, one retry per host).
- Rev 1: first version. Reads ticker-symbols.json, writes ticker.json.

Usage:
  python3 fetch_prices.py <ticker-symbols.json> <out.json> [<previous.json>] [--block a,b]

--block lists sources to treat as unreachable (for testing):
  yahoo, yahoo2, nasdaq, goldapi, frankfurter, coinbase

Fallback order (plan 5.3):
  Yahoo batch (v7 spark) -> Yahoo per symbol (v8 chart, query1)
  -> Yahoo per symbol on query2 -> Nasdaq API (stocks and funds)
  -> gold-api.com (gold, silver) / Frankfurter (EUR/USD)
  -> keep previous value from previous.json (marked "kept").
Crypto comes from Coinbase REST.

Python standard library only. No keys, no secrets.
Exit code 0 if at least one item was freshly updated, 1 otherwise
(then the output file is not touched).
"""
import json
import math
import os
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
TIMEOUT = 20
MAX_POINTS = 80
MAX_JUMP = 0.40
MAX_BYTES = 60 * 1024
GROUPS = ("Indexes", "Stocks", "FX and commodities", "Crypto")
BLOCKABLE = ("yahoo", "yahoo2", "nasdaq", "goldapi", "frankfurter", "coinbase")
GOLDAPI = {"GC=F": "XAU", "SI=F": "XAG"}

_last_call = {}
_retried = set()  # hosts already retried after a 429 in this run
MAX_RETRY_WAIT = 20


class FetchError(Exception):
    pass


class Blocked(FetchError):
    pass


# ---------------------------------------------------------------- helpers

def good(x):
    """True for a finite number above zero (bool excluded)."""
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and x > 0)


def http_json(url, host_key, gap=0.0):
    """GET a URL and parse JSON. gap = minimum seconds between calls per host.
    On HTTP 429: wait for Retry-After (capped at 20 s, default 5 s) and try
    once more. At most one such retry per host per run."""
    for attempt in (0, 1):
        if gap:
            wait = _last_call.get(host_key, 0) + gap - time.time()
            if wait > 0:
                time.sleep(wait)
        _last_call[host_key] = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": YAHOO_UA if host_key in ("query1", "query2") else UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read(5_000_000)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0 and host_key not in _retried:
                _retried.add(host_key)
                try:
                    delay = float(e.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    delay = 5.0
                time.sleep(max(0.0, min(delay, MAX_RETRY_WAIT)))
                continue
            raise FetchError("HTTP %s" % e.code)
        except Exception as e:
            raise FetchError(type(e).__name__)
    try:
        return json.loads(raw)
    except ValueError:
        raise FetchError("bad JSON")


def clean_series(vals):
    out = []
    for v in vals or []:
        if good(v):
            out.append(round(float(v), 6))
    return out[-MAX_POINTS:]


def load_symbols(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    items = []
    seen = set()
    for it in d.get("items", []):
        if (not isinstance(it, dict) or it.get("id") in seen
                or it.get("group") not in GROUPS
                or it.get("source") not in ("yahoo", "coinbase")
                or not isinstance(it.get("symbol"), str)):
            continue
        seen.add(it["id"])
        items.append(it)
    return items


def load_previous(path, ids):
    """Previous items, only for known ids and only if fully valid."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        raw = d["items"]
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        if k in ids and valid_item(v):
            out[k] = v
    return out


def valid_item(v):
    return (isinstance(v, dict) and good(v.get("p")) and good(v.get("pc"))
            and good(v.get("t")) and isinstance(v.get("open"), bool)
            and isinstance(v.get("s"), list) and len(v["s"]) <= MAX_POINTS
            and all(good(x) for x in v["s"]))


def make_item(p, pc, t, is_open, s):
    """Build a candidate item or return None if any number is bad."""
    if not (good(p) and good(t)):
        return None
    if not good(pc):
        return None
    s = clean_series(s)
    if not s:
        s = [p]
    return {"p": round(float(p), 6), "pc": round(float(pc), 6),
            "t": int(t), "open": bool(is_open), "s": s}


# ------------------------------------------------------------------ Yahoo

def yahoo_parse(res, now):
    """res = {meta, timestamp, indicators} -> item or None."""
    meta = res.get("meta") or {}
    p = meta.get("regularMarketPrice")
    pc = meta.get("chartPreviousClose")
    if not good(pc):
        pc = meta.get("previousClose")
    t = meta.get("regularMarketTime")
    try:
        closes = res["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        closes = []
    cur = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = cur.get("start"), cur.get("end")
    if good(start) and good(end):
        is_open = start <= now <= end
    else:
        is_open = good(t) and now - t < 900
    if good(t) and now - t > 3600:
        is_open = False
    return make_item(p, pc, t, is_open, closes)


def yahoo_batch(symbols, host):
    """One spark request for many symbols. Returns {symbol: item}."""
    q = urllib.parse.quote(",".join(symbols), safe=",")
    url = ("https://%s.finance.yahoo.com/v7/finance/spark?symbols=%s"
           "&range=1d&interval=5m&includePrePost=false" % (host, q))
    d = http_json(url, host)
    now = time.time()
    out = {}
    try:
        results = d["spark"]["result"]
    except (KeyError, TypeError):
        raise FetchError("spark shape")
    for r in results or []:
        sym = r.get("symbol")
        if sym not in symbols:
            continue
        try:
            item = yahoo_parse(r["response"][0], now)
        except (KeyError, IndexError, TypeError):
            item = None
        if item:
            out[sym] = item
    return out


def yahoo_chart(symbol, host):
    url = ("https://%s.finance.yahoo.com/v8/finance/chart/%s"
           "?range=1d&interval=5m&includePrePost=false"
           % (host, urllib.parse.quote(symbol, safe="")))
    d = http_json(url, host, gap=0.8)
    try:
        res = d["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        raise FetchError("chart shape")
    item = yahoo_parse(res, time.time())
    if not item:
        raise FetchError("chart fields")
    return item


# ----------------------------------------------------------------- Nasdaq

def _money(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def nasdaq_item(symbol):
    last_err = "no data"
    for ac in ("stocks", "etf"):
        url = ("https://api.nasdaq.com/api/quote/%s/chart?assetclass=%s"
               % (urllib.parse.quote(symbol, safe=""), ac))
        try:
            d = http_json(url, "nasdaq", gap=0.4)
        except FetchError as e:
            last_err = str(e)
            continue
        data = d.get("data") if isinstance(d, dict) else None
        if not data or not data.get("chart"):
            continue
        pts = []
        for c in data["chart"]:
            try:
                x, y = ny_wall_to_epoch(c["x"] / 1000.0), float(c["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if good(y) and good(x):
                pts.append((x, y))
        if not pts:
            continue
        pts.sort()
        t = pts[-1][0]
        p = _money(data.get("lastSalePrice"))
        if not good(p):
            p = pts[-1][1]
        pc = _money(data.get("previousClose"))
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
    try:
        from zoneinfo import ZoneInfo
        naive = datetime.fromtimestamp(x, timezone.utc).replace(tzinfo=None)
        return naive.replace(tzinfo=ZoneInfo("America/New_York")).timestamp()
    except Exception:
        return x + 5 * 3600


def in_regular_session(epoch):
    """US regular session 9:30-16:00 New York time, Monday to Friday."""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(epoch, ZoneInfo("America/New_York"))
    except Exception:
        return True
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return 570 <= m < 960


def nasdaq_index_item(symbol, prev):
    """Nasdaq 100 only (other indexes return 400 there). Price and previous
    close only, so the little chart reuses the previous series."""
    url = ("https://api.nasdaq.com/api/quote/%s/info?assetclass=index"
           % urllib.parse.quote(symbol, safe=""))
    d = http_json(url, "nasdaq", gap=0.4)
    data = d.get("data") if isinstance(d, dict) else None
    if not data or not data.get("primaryData"):
        raise FetchError("no data")
    pd = data["primaryData"]
    p = _money(pd.get("lastSalePrice"))
    pc = _money(((data.get("keyStats") or {}).get("previousclose") or {})
                .get("value"))
    t = time.time()
    try:
        from zoneinfo import ZoneInfo
        stamp = str(pd.get("lastTradeTimestamp", "")).replace(" ET", "")
        t = datetime.strptime(stamp, "%b %d, %Y %I:%M %p").replace(
            tzinfo=ZoneInfo("America/New_York")).timestamp()
    except Exception:
        pass
    is_open = data.get("marketStatus") == "Open" and time.time() - t < 1800
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
    p = d.get("price")
    try:
        t = datetime.strptime(d["updatedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        t = time.time()
    return fallback_item(p, t, True, prev)


def frankfurter_item(prev):
    d = http_json("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD",
                  "frankfurter")
    p = d["rates"]["USD"]
    day = datetime.strptime(d["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t = day.timestamp()
    # previous business day rate (one extra request) gives a real pc
    back = day - timedelta(days=1)
    while back.weekday() >= 5:
        back -= timedelta(days=1)
    pc = None
    try:
        d2 = http_json("https://api.frankfurter.dev/v1/%s?base=EUR&symbols=USD"
                       % back.strftime("%Y-%m-%d"), "frankfurter")
        pc = d2["rates"]["USD"]
    except (FetchError, KeyError, TypeError):
        pass
    if not good(pc):
        pc = prev["pc"] if prev else p
    if not good(p):
        raise FetchError("bad price")
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
    base = "https://api.exchange.coinbase.com/products/%s" % product
    st = http_json(base + "/stats", "coinbase")
    p = float(st["last"])
    pc = float(st["open"])
    series = []
    try:
        cs = http_json(base + "/candles?granularity=900", "coinbase", gap=0.3)
        rows = sorted((r for r in cs if isinstance(r, list) and len(r) >= 5),
                      key=lambda r: r[0])
        series = [r[4] for r in rows]
    except (FetchError, TypeError):
        pass
    item = make_item(p, pc, time.time(), True, series)
    if not item:
        raise FetchError("bad fields")
    return item


# ------------------------------------------------------------------- main

def check_jump(item, prev):
    """True if the new price is acceptable against the previous one."""
    if not prev:
        return True
    return abs(item["p"] / prev["p"] - 1.0) <= MAX_JUMP


def run(symbols_path, previous_path=None, blocked=()):
    """Fetch everything. Returns the ticker.json dict (always, even if empty)."""
    blocked = set(blocked)
    _retried.clear()
    syms = load_symbols(symbols_path)
    by_id = {it["id"]: it for it in syms}
    prev_all = load_previous(previous_path, set(by_id))
    items, sources = {}, {}
    problems = {}

    def accept(iid, item, src):
        """Validate and store. Returns True when the symbol is settled."""
        prev = prev_all.get(iid)
        if not check_jump(item, prev):
            items[iid] = dict(prev)
            sources[iid] = "kept"
            problems[iid] = "jump over 40 percent held back"
            return True
        items[iid] = item
        sources[iid] = src
        return True

    def pending(group_filter=None):
        return [i for i in syms if i["id"] not in sources
                and (group_filter is None or group_filter(i))]

    # ---- Yahoo
    ysyms = [i for i in syms if i["source"] == "yahoo"]
    if "yahoo" not in blocked and ysyms:
        try:
            got = yahoo_batch([i["symbol"] for i in ysyms], "query1")
            for i in ysyms:
                if i["symbol"] in got:
                    accept(i["id"], got[i["symbol"]], "yahoo")
        except FetchError as e:
            problems["_batch"] = "yahoo batch: %s" % e
    for host, key, label in (("query1", "yahoo", "yahoo-single"),
                             ("query2", "yahoo2", "yahoo2")):
        if key in blocked:
            continue
        fails = 0
        for i in pending(lambda i: i["source"] == "yahoo"):
            if fails >= 3:  # host is refusing us, stop being noisy
                break
            try:
                accept(i["id"], yahoo_chart(i["symbol"], host), label)
                fails = 0
            except FetchError as e:
                fails += 1
                problems[i["id"]] = "%s: %s" % (label, e)

    # ---- Nasdaq (stocks and funds only, no indexes, futures or FX)
    if "nasdaq" not in blocked:
        fails = 0
        for i in pending(lambda i: i["source"] == "yahoo"
                         and i["group"] == "Stocks"):
            if fails >= 3:
                break
            try:
                accept(i["id"], nasdaq_item(i["symbol"]), "nasdaq")
                fails = 0
            except FetchError as e:
                fails += 1
                problems[i["id"]] = "nasdaq: %s" % e

    if "nasdaq" not in blocked:
        for i in pending(lambda i: i["symbol"] == "^NDX"):
            try:
                accept(i["id"], nasdaq_index_item("NDX", prev_all.get(i["id"])),
                       "nasdaq")
            except FetchError as e:
                problems[i["id"]] = "nasdaq: %s" % e

    # ---- gold-api and Frankfurter
    for i in pending(lambda i: i["source"] == "yahoo"):
        prev = prev_all.get(i["id"])
        try:
            if i["symbol"] in GOLDAPI and "goldapi" not in blocked:
                accept(i["id"], goldapi_item(i["symbol"], prev), "goldapi")
            elif i["symbol"] == "EURUSD=X" and "frankfurter" not in blocked:
                accept(i["id"], frankfurter_item(prev), "frankfurter")
        except (FetchError, KeyError, TypeError) as e:
            problems[i["id"]] = "fallback: %s" % e

    # ---- Coinbase
    for i in syms:
        if i["source"] != "coinbase":
            continue
        if "coinbase" in blocked:
            problems[i["id"]] = "coinbase blocked"
            continue
        try:
            accept(i["id"], coinbase_item(i["symbol"]), "coinbase")
        except (FetchError, KeyError, TypeError, ValueError) as e:
            problems[i["id"]] = "coinbase: %s" % e

    # ---- keep previous for anything still missing
    for i in syms:
        iid = i["id"]
        if iid in sources:
            continue
        if iid in prev_all:
            items[iid] = dict(prev_all[iid])
            sources[iid] = "kept"
        else:
            sources[iid] = "error: %s" % problems.get(iid, "no source answered")

    doc = {"generated": int(time.time()), "items": items, "sources": sources}
    shrink_to_fit(doc)
    return doc


def encoded(doc):
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def shrink_to_fit(doc):
    n = MAX_POINTS
    while len(encoded(doc)) >= MAX_BYTES and n > 10:
        n //= 2
        for it in doc["items"].values():
            it["s"] = it["s"][-n:]
    if len(encoded(doc)) >= MAX_BYTES:
        raise RuntimeError("output too large")


def fresh_count(doc):
    return sum(1 for v in doc["sources"].values()
               if not v.startswith("error") and v != "kept")


def write_atomic(path, doc):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".ticker-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded(doc))
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def summary(syms, doc):
    for i in syms:
        iid = i["id"]
        it = doc["items"].get(iid)
        src = doc["sources"].get(iid)
        if it:
            chg = (it["p"] / it["pc"] - 1) * 100
            print("%-7s %-12s %12.*f  %+6.2f%%  t=%d  %s  pts=%d  [%s]" % (
                iid, i["name"], i["decimals"], it["p"], chg, it["t"],
                "open" if it["open"] else "closed", len(it["s"]), src))
        else:
            print("%-7s %-12s %s" % (iid, i["name"], src))


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
    doc = run(symbols_path, prev_path, blocked)
    summary(load_symbols(symbols_path), doc)
    n = fresh_count(doc)
    if n == 0:
        print("nothing could be updated, %s left untouched" % out_path)
        return 1
    write_atomic(out_path, doc)
    print("wrote %s: %d fresh, %d total, %d bytes" % (
        out_path, n, len(doc["items"]), len(encoded(doc))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
