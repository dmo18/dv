#!/usr/bin/env python3
"""relay_loop.py Rev 2

Changelog:
- Rev 2: fixes after the review.
  1. The successor start is retried: 3 attempts, one minute apart.
  2. Timing test inputs without chain_remaining mean chain_remaining 0
     (no successor), so a test chain can never run on forever.
  3. Any error while loading the previous files (for example a deeply nested
     JSON file) means "no previous file"; it never stops the run.
  4. Previous and published data is shape checked ("items" must be an
     object) wherever it is used.
  5. The token is no longer in any command line: git gets a plain https
     address and the auth header through GIT_CONFIG_* env vars, set only for
     git. Fetch children get no token at all; only the successor command
     gets GH_TOKEN.
  6. A second stop signal can no longer hang the process (reentrant lock,
     no lock in the signal handler), and the watchdog exits at most 10 s
     after a second signal.
  7. (relay.yml runs this script with exec, so stop signals reach it.)
  8. --remote may not start with "-", and "--" goes before every positional
     git argument.
  Also: slow mode is decided only after a successful cycle, and only from
  items that are present, so a stale or odd file cannot switch it on.
- Rev 1: first version (plan task 2). One process is one "Data relay" run.
  Test inputs for the successor chain (--inputs-from-env, see below).
  Publish decision taken by fetch_prices.should_write() (fetch_prices Rev 4).

What it does, in a loop, for about 5.5 hours:
- every 2 minutes: fetch prices (fetch_prices.run) and publish ticker.json
  to the branch "ticker-data" (one commit, replaced each time);
- every 15 minutes: run fetch_headlines.py and publish headlines.json to the
  branch "headlines-data" the same way;
- when no non-crypto item is open (all markets closed), the price cycle
  slows to once every 15 minutes;
- 10 minutes before the end it starts its successor (up to 3 attempts, one
  minute apart), then it stops.

Safety:
- Only the branches ticker-data and headlines-data are ever pushed, with an
  explicit refspec, never main.
- Every child process has a timeout, and a watchdog ends the process at the
  run length plus a fixed grace, even if a child hangs.
- The price fetch runs in a child process, so a hang cannot freeze the loop.
- The token is never printed (every logged child output is scrubbed) and is
  never on a command line. Only git (auth header in env) and the successor
  command (GH_TOKEN) receive it.
- SIGTERM or SIGINT: no new step starts, a running fetch is stopped, a
  running push may finish (a push replaces the branch in one step, so the
  branch is always either the old or the new commit). A second signal stops
  everything at once.
- 20 price cycles failing in a row: exit 1.

Decisions taken during task 2 (accepted by the coordinator):
- At start the previous ticker.json and headlines.json are loaded from the
  data branches, so kept values and held jumps carry over between runs.
- Headline failures are logged but do not count toward the 20 failure limit
  (only price cycles count).
- No successor is started after a failure exit or a stop signal; the
  workflow's safety-net schedule restarts the chain.

Test inputs (--inputs-from-env, used by relay.yml): the env vars IN_CYCLE,
IN_RUN, IN_HEADLINES and IN_CHAIN hold the workflow_dispatch inputs
cycle_seconds, run_minutes, headlines_every_seconds and chain_remaining.
Each must be empty or a whole number within bounds (5-3600, 1-340, 10-7200,
0-10), otherwise exit 2. Without any of them the successor is started with
no inputs (plan defaults). With some, the successor gets the same inputs as
separate -f arguments (no shell), chain_remaining minus 1; when
chain_remaining is 0 no successor is started. When any timing input
(cycle_seconds, run_minutes, headlines_every_seconds) is set and
chain_remaining is empty, chain_remaining counts as 0: no successor.
Inputs only change timings, never which branches are pushed or which files
are read.

Python standard library only.
Exit codes: 0 normal end or stopped by signal, 1 too many failures,
2 bad arguments or unusable work folder, 3 watchdog.
"""
import argparse
import base64
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# branch -> the only file it may hold. Nothing else is ever pushed.
BRANCHES = {"ticker-data": "ticker.json", "headlines-data": "headlines.json"}
MAX_FAILS = 20
FETCH_DEADLINE = 90        # fetch_prices' own limit for one run
PUSH_TIMEOUT = 60
GIT_FETCH_TIMEOUT = 60
HEADLINES_TIMEOUT = 600
SUCCESSOR_TIMEOUT = 60
SUCCESSOR_ATTEMPTS = 3
SECOND_SIGNAL_EXIT = 10    # seconds after a second signal, exit for sure
TICK = 0.5
# env vars that carry credentials; removed for every child except git
# (gets its own header) and the successor command (gets GH_TOKEN).
SECRET_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GIT_CONFIG_COUNT")

_stop = threading.Event()
_signals = [0]
_children = set()
_children_lock = threading.RLock()
_secrets = []
_token = [""]
_second_signal = [None]


# ---------------------------------------------------------------- logging

def scrub(text):
    text = str(text)
    for s in _secrets:
        if s:
            text = text.replace(s, "***")
    return text


def log(msg):
    stamp = time.strftime("%H:%M:%S", time.gmtime())
    print("%s %s" % (stamp, scrub(msg)), flush=True)


def short(out, limit=200):
    """Last meaningful lines of a child's output, one line, scrubbed."""
    lines = [l.strip() for l in scrub(out or "").splitlines() if l.strip()]
    return " | ".join(lines[-2:])[:limit]


# ---------------------------------------------------------------- children

def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def run_cmd(args, timeout, hard_end, kill_on_stop=True, env=None, cwd=None):
    """Run a child with a timeout (capped at hard_end). Returns (rc, output).
    rc is None when the child was killed (timeout or stop)."""
    limit = min(time.monotonic() + timeout, hard_end)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             env=clean_env() if env is None else env, cwd=cwd,
                             start_new_session=True)
    except OSError as e:
        return None, "could not start: %s" % e.strerror
    with _children_lock:
        _children.add(p)
    out, why = b"", None
    try:
        while True:
            try:
                out, _ = p.communicate(timeout=TICK)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= limit:
                    why = "timed out"
                elif _signals[0] >= 2 or (kill_on_stop and _stop.is_set()):
                    why = "stopped"
                if why:
                    _kill_group(p)
                    out, _ = p.communicate()
                    break
    finally:
        with _children_lock:
            _children.discard(p)
    text = out.decode("utf-8", "replace")
    if why:
        return None, (text + "\n" + why)
    return p.returncode, text


def kill_all_children():
    # No lock here: this also runs from the signal handler, and copying the
    # set is safe without one.
    for p in list(_children):
        _kill_group(p)


def clean_env():
    """os.environ without any credential."""
    env = dict(os.environ)
    for k in list(env):
        if k in SECRET_ENV or k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            del env[k]
    return env


# ---------------------------------------------------------------- git

def git_env():
    env = clean_env()
    if _token[0]:
        # auth header for github.com, given to git through env, never argv
        basic = base64.b64encode(("x-access-token:" + _token[0]).encode()).decode()
        env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + basic,
        })
    env.update({
        "GIT_TERMINAL_PROMPT": "0",          # never wait for a password
        "GIT_ASKPASS": "true",
        "GIT_HTTP_LOW_SPEED_LIMIT": "1000",  # abort a stalled transfer
        "GIT_HTTP_LOW_SPEED_TIME": "20",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    })
    return env


def publish(cfg, branch, src_path, message, hard_end):
    """Push src_path as the only file of a fresh one-commit branch (force).
    Returns (ok, seconds, note)."""
    if branch not in BRANCHES:
        raise ValueError("refusing to push branch %r" % branch)
    name = BRANCHES[branch]
    started = time.monotonic()
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix="pub-", dir=cfg.workdir)
        shutil.copyfile(src_path, os.path.join(tmp, name))
        env = git_env()
        steps = [
            ("init", ["git", "init", "-q", "-b", branch]),
            ("add", ["git", "add", "--", name]),
            ("commit", ["git", "-c", "commit.gpgsign=false", "commit", "-q",
                        "-m", message]),
            ("push", ["git", "-c", "credential.helper=", "push", "-q", "--force",
                      "--", cfg.remote, "HEAD:refs/heads/" + branch]),
        ]
        for step, args in steps:
            rc, out = run_cmd(args, PUSH_TIMEOUT, hard_end,
                              kill_on_stop=False, env=env, cwd=tmp)
            if rc != 0:
                return False, time.monotonic() - started, "git %s: %s" % (
                    step, short(out))
        return True, time.monotonic() - started, ""
    except OSError as e:
        return False, time.monotonic() - started, "local file error: %s" % e.strerror
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def previous_ok(name, text):
    """A previous file is used only if it parses and has the expected shape."""
    doc = json.loads(text)
    if not isinstance(doc, dict):
        return False
    items = doc.get("items")
    return isinstance(items, dict) if name == "ticker.json" else isinstance(items, list)


def bootstrap_previous(cfg, hard_end):
    """Copy the published ticker.json and headlines.json into the work folder,
    so held jumps and kept values carry over from the previous run.
    Any problem means "no previous file", never a stopped run."""
    try:
        _bootstrap(cfg, hard_end)
    except Exception as e:
        log("previous files not loaded: %s" % type(e).__name__)


def _bootstrap(cfg, hard_end):
    tmp = tempfile.mkdtemp(prefix="boot-", dir=cfg.workdir)
    try:
        env = git_env()
        run_cmd(["git", "init", "-q"], 20, hard_end, env=env, cwd=tmp)
        for branch, name in BRANCHES.items():
            if os.path.exists(os.path.join(cfg.workdir, name)):
                continue
            rc, out = run_cmd(["git", "-c", "credential.helper=", "fetch", "-q",
                               "--depth", "1", "--", cfg.remote, "refs/heads/" + branch],
                              GIT_FETCH_TIMEOUT, hard_end, env=env, cwd=tmp)
            if rc != 0:
                log("previous %s not loaded: %s" % (name, short(out, 120)))
                continue
            rc, out = run_cmd(["git", "show", "FETCH_HEAD:" + name],
                              20, hard_end, env=env, cwd=tmp)
            if rc != 0:
                log("previous %s not in branch" % name)
                continue
            try:
                good = previous_ok(name, out)
            except Exception as e:
                good = False
            if not good:
                log("previous %s is not usable, ignored" % name)
                continue
            try:
                with open(os.path.join(cfg.workdir, name), "w", encoding="utf-8") as f:
                    f.write(out)
            except OSError as e:
                log("previous %s not saved: %s" % (name, e.strerror))
                continue
            log("previous %s loaded from %s" % (name, branch))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- prices

def fetch_child(module_path, symbols, out_path, prev_path, deadline):
    """Runs in a child process: fetch_prices.run() and an atomic write.
    Exit 0 if fetch_prices.should_write(doc), 1 otherwise or on a bad
    symbols file (file untouched)."""
    spec = importlib.util.spec_from_file_location("fetch_prices", module_path)
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    prev = prev_path if prev_path and os.path.exists(prev_path) else None
    try:
        doc = fp.run(symbols, prev, (), deadline=float(deadline))
    except fp.SymbolsError as e:
        print("symbols file unusable: %s" % str(e)[:100])
        return 1
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
    fresh = sum(1 for v in sources.values()
                if not str(v).startswith(("error", "kept")))
    total = len(doc["items"]) if isinstance(doc.get("items"), dict) else 0
    # fetch_prices decides: a fresh item, or a jump marker "h" set, changed
    # or cleared, is worth saving.
    if not fp.should_write(doc):
        print("nothing to save, %d items" % total)
        return 1
    data = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".ticker-", suffix=".tmp",
                               dir=os.path.dirname(os.path.abspath(out_path)))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print("fresh %d/%d" % (fresh, total))
    return 0


def non_crypto_ids(symbols_path):
    """Ids of the non-crypto items; empty set if the file is unusable."""
    try:
        with open(symbols_path, encoding="utf-8") as f:
            d = json.load(f)
        items = d.get("items") if isinstance(d, dict) else None
        return {i["id"] for i in (items if isinstance(items, list) else [])
                if isinstance(i, dict) and i.get("group") != "Crypto"
                and isinstance(i.get("id"), str)}
    except Exception:
        return set()


def any_market_open(ticker_path, ids):
    """True if a non-crypto item is open, None if unknown."""
    if not ids:
        return None
    try:
        with open(ticker_path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return None
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, dict):
        return None
    known = [items[i] for i in ids if isinstance(items.get(i), dict)]
    if not known:
        return None
    return any(it.get("open") is True for it in known)


def price_cycle(cfg, hard_end, cycle_len):
    """Fetch and publish once. Returns (ok, message)."""
    out = os.path.join(cfg.workdir, "ticker.json")
    deadline = min(FETCH_DEADLINE, max(5.0, 0.75 * cycle_len))
    t = time.monotonic()
    rc, text = run_cmd([sys.executable, os.path.abspath(__file__), "--fetch-child",
                        cfg.prices_module, cfg.symbols, out, out, str(deadline)],
                       deadline + 15, hard_end)
    took = time.monotonic() - t
    if rc != 0:
        return False, "ticker fetch failed after %.1fs: %s" % (took, short(text))
    ok, secs, note = publish(cfg, "ticker-data", out, "Update ticker", hard_end)
    if not ok:
        return False, "ticker fetched (%s) in %.1fs, publish failed: %s" % (
            short(text, 40), took, note)
    return True, "ticker %s fetch %.1fs push %.1fs" % (short(text, 40), took, secs)


# ---------------------------------------------------------------- headlines

class Headlines:
    """fetch_headlines.py runs in the background so price cycles stay on time."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.started = 0.0
        self.new = os.path.join(cfg.workdir, "headlines.new.json")
        self.final = os.path.join(cfg.workdir, "headlines.json")
        self.logf = None

    def start(self):
        if self.proc:
            return
        try:
            if os.path.exists(self.new):
                os.unlink(self.new)
            args = [sys.executable, self.cfg.headlines_script, self.cfg.feeds, self.new]
            if os.path.exists(self.final):
                args.append("file://" + os.path.abspath(self.final))
            self.logf = open(os.path.join(self.cfg.workdir, "headlines.log"), "wb")
            self.proc = subprocess.Popen(args, stdout=self.logf, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, env=clean_env(),
                                         start_new_session=True)
        except OSError as e:
            log("headlines could not start: %s" % e.strerror)
            self.close()
            return
        with _children_lock:
            _children.add(self.proc)
        self.started = time.monotonic()

    def close(self):
        if self.proc:
            with _children_lock:
                _children.discard(self.proc)
        self.proc = None
        if self.logf:
            self.logf.close()
            self.logf = None

    def tail(self):
        try:
            with open(os.path.join(self.cfg.workdir, "headlines.log"),
                      encoding="utf-8", errors="replace") as f:
                return short(f.read())
        except OSError:
            return ""

    def poll(self, hard_end):
        """Publish when the background fetch has finished."""
        if not self.proc:
            return
        rc = self.proc.poll()
        if rc is None:
            if time.monotonic() - self.started > HEADLINES_TIMEOUT:
                self.stop()
                log("headlines fetch timed out, killed")
            return
        took = time.monotonic() - self.started
        self.close()
        if rc != 0:
            log("headlines fetch failed after %.1fs: %s" % (took, self.tail()))
            return
        try:
            with open(self.new, encoding="utf-8") as f:
                doc = json.load(f)
            if not (isinstance(doc, dict) and isinstance(doc.get("items"), list)):
                raise ValueError("bad shape")
            n = len(doc["items"])
            os.replace(self.new, self.final)
        except Exception as e:
            log("headlines output unusable: %s" % type(e).__name__)
            return
        ok, secs, note = publish(self.cfg, "headlines-data", self.final,
                                 "Update headlines", hard_end)
        if ok:
            log("headlines %d items fetch %.1fs push %.1fs" % (n, took, secs))
        else:
            log("headlines publish failed: %s" % note)

    def stop(self):
        if self.proc:
            _kill_group(self.proc)
            try:
                self.proc.wait(5)
            except Exception:
                pass
        self.close()


# ---------------------------------------------------------------- main loop

def next_slot(prev_due, length, now):
    """Next start on the fixed grid prev_due + k*length that is after now,
    so a slow cycle skips a slot instead of drifting or bursting."""
    due = prev_due + length
    if due <= now:
        due += math.ceil((now - due) / length + 1e-9) * length
        if due <= now:
            due += length
    return due


def watchdog(hard_end):
    why = "run length plus grace reached"
    while True:
        now = time.monotonic()
        if now >= hard_end:
            break
        if _second_signal[0] is not None and now - _second_signal[0] >= SECOND_SIGNAL_EXIT:
            why = "second stop signal"
            break
        time.sleep(min(hard_end - now, 0.5))
    kill_all_children()
    try:
        log("watchdog: %s, stopping hard" % why)
    except Exception:
        pass
    os._exit(3)


def on_signal(signum, frame):
    # Keep this tiny: no locks, no printing.
    _signals[0] += 1
    _stop.set()
    if _signals[0] >= 2:
        if _second_signal[0] is None:
            _second_signal[0] = time.monotonic()
        kill_all_children()


def start_successor(cfg, attempts, hard_end):
    """One attempt to start the successor. Returns (attempts, next attempt).
    Safe to repeat: the concurrency group holds at most one pending run."""
    attempts += 1
    env = clean_env()
    if _token[0]:
        env["GH_TOKEN"] = _token[0]
    rc, out = run_cmd(cfg.successor, SUCCESSOR_TIMEOUT, hard_end, env=env)
    if rc == 0:
        log("successor started (attempt %d)" % attempts)
        return SUCCESSOR_ATTEMPTS, float("inf")
    log("successor attempt %d of %d failed: %s" % (attempts, SUCCESSOR_ATTEMPTS,
                                                    short(out, 120)))
    return attempts, time.monotonic() + cfg.successor_retry_seconds


def main_loop(cfg):
    t0 = time.monotonic()
    end = t0 + cfg.run_minutes * 60
    hard_end = end + cfg.grace_seconds
    succ_at = end - cfg.successor_lead_minutes * 60
    threading.Thread(target=watchdog, args=(hard_end,), daemon=True).start()
    log("relay start: cycle %gs, slow %gs, headlines every %gs, run %g min, "
        "successor %s" % (cfg.cycle_seconds, cfg.slow_cycle_seconds,
                          cfg.headlines_every_seconds, cfg.run_minutes,
                          "at -%g min" % cfg.successor_lead_minutes
                          if cfg.successor else "off"))
    bootstrap_previous(cfg, hard_end)
    ids = non_crypto_ids(cfg.symbols)
    ticker_path = os.path.join(cfg.workdir, "ticker.json")
    heads = Headlines(cfg)
    next_price = next_head = t0
    attempts = 0 if cfg.successor else SUCCESSOR_ATTEMPTS
    next_succ = succ_at
    slow = False
    fails = 0
    code = 0
    try:
        while not _stop.is_set():
            now = time.monotonic()
            if attempts < SUCCESSOR_ATTEMPTS and now >= next_succ:
                attempts, next_succ = start_successor(cfg, attempts, hard_end)
            try:
                heads.poll(hard_end)
            except Exception as e:
                heads.close()
                log("headlines step error: %s" % type(e).__name__)
            if now >= end:
                break
            if now >= next_head and not heads.proc:
                heads.start()
                next_head = next_slot(next_head, cfg.headlines_every_seconds, now)
            if now >= next_price:
                try:
                    ok, msg = price_cycle(cfg, hard_end, cfg.cycle_seconds)
                except Exception as e:
                    ok, msg = False, "ticker step error: %s" % type(e).__name__
                if _stop.is_set() and not ok:
                    break
                fails = 0 if ok else fails + 1
                log(msg if ok else "%s (failed %d in a row)" % (msg, fails))
                if fails >= MAX_FAILS:
                    log("%d price cycles failed in a row, giving up" % fails)
                    code = 1
                    break
                # decide only on a file this cycle just wrote
                is_open = any_market_open(ticker_path, ids) if ok else None
                if is_open is not None and slow == is_open:
                    slow = not is_open
                    log("slow mode %s (%s)" % ("on" if slow else "off",
                        "all markets closed" if slow else "a market is open"))
                length = cfg.slow_cycle_seconds if slow else cfg.cycle_seconds
                next_price = next_slot(next_price, length, time.monotonic())
            wake = min(next_price, end,
                       next_head if not heads.proc else end,
                       next_succ if attempts < SUCCESSOR_ATTEMPTS else end)
            if heads.proc:
                wake = min(wake, time.monotonic() + 1.0)
            _stop.wait(max(0.0, wake - time.monotonic()))
        # Retries still due at the end of the run (short test runs): finish
        # them, within the grace time. Not after a failure exit or a signal.
        while code == 0 and attempts < SUCCESSOR_ATTEMPTS and not _stop.is_set():
            if time.monotonic() >= next_succ:
                attempts, next_succ = start_successor(cfg, attempts, hard_end)
            else:
                _stop.wait(max(0.0, min(next_succ, hard_end - 5) - time.monotonic()))
                if time.monotonic() >= hard_end - 5:
                    break
    finally:
        heads.stop()
    if _stop.is_set():
        log("stopped by signal, branches left as last published")
    elif code == 0:
        log("relay end after %.1f min" % ((time.monotonic() - t0) / 60))
    return code


# name -> (env var, min, max). Values are whole numbers only.
TEST_INPUTS = {
    "cycle_seconds": ("IN_CYCLE", 5, 3600),
    "run_minutes": ("IN_RUN", 1, 340),
    "headlines_every_seconds": ("IN_HEADLINES", 10, 7200),
    "chain_remaining": ("IN_CHAIN", 0, 10),
}


def read_test_inputs(env):
    """Validated test inputs from env, {name: int}. ValueError(name) if bad."""
    got = {}
    for name, (var, lo, hi) in TEST_INPUTS.items():
        raw = env.get(var, "")
        if raw == "":
            continue
        if not re.fullmatch(r"[0-9]{1,5}", raw) or not lo <= int(raw) <= hi:
            raise ValueError(name)
        got[name] = int(raw)
    return got


def successor_args(cfg):
    """The successor command as an argument list, or None for no successor."""
    if not cfg.successor_command:
        return None
    args = shlex.split(cfg.successor_command)
    inputs = dict(cfg.test_inputs)
    timing = ("cycle_seconds", "run_minutes", "headlines_every_seconds")
    if any(k in inputs for k in timing) and "chain_remaining" not in inputs:
        return None     # a test run without a chain length starts no successor
    if "chain_remaining" in inputs:
        if inputs["chain_remaining"] <= 0:
            return None
        inputs["chain_remaining"] -= 1
    for name in TEST_INPUTS:
        if name in inputs:
            args += ["-f", "%s=%d" % (name, inputs[name])]
    return args


def parse(argv):
    a = argparse.ArgumentParser(description="Data relay loop (one run).")
    a.add_argument("--cycle-seconds", type=float, default=120)
    a.add_argument("--headlines-every-seconds", type=float, default=900)
    a.add_argument("--run-minutes", type=float, default=330)
    a.add_argument("--slow-cycle-seconds", type=float, default=900)
    a.add_argument("--remote", default=None,
                   help="git remote url (default: GitHub from GITHUB_REPOSITORY and GH_TOKEN)")
    a.add_argument("--successor-command", default="")
    a.add_argument("--successor-lead-minutes", type=float, default=10)
    a.add_argument("--successor-retry-seconds", type=float, default=60)
    a.add_argument("--grace-seconds", type=float, default=300)
    a.add_argument("--workdir", default=None)
    a.add_argument("--symbols", default=os.path.join(ROOT, "ticker-symbols.json"))
    a.add_argument("--feeds", default=os.path.join(ROOT, "headlines-feeds.json"))
    a.add_argument("--prices-module", default=os.path.join(HERE, "fetch_prices.py"))
    a.add_argument("--headlines-script", default=os.path.join(HERE, "fetch_headlines.py"))
    a.add_argument("--inputs-from-env", action="store_true",
                   help="read validated test inputs from IN_CYCLE, IN_RUN, "
                        "IN_HEADLINES, IN_CHAIN")
    cfg = a.parse_args(argv)
    cfg.test_inputs = {}
    if cfg.inputs_from_env:
        try:
            cfg.test_inputs = read_test_inputs(os.environ)
        except ValueError as e:
            a.error("bad test input %s (whole number within bounds expected)" % e)
        for name in ("cycle_seconds", "run_minutes", "headlines_every_seconds"):
            if name in cfg.test_inputs:
                setattr(cfg, name, float(cfg.test_inputs[name]))
    cfg.successor = successor_args(cfg) if cfg.successor_command else None
    for name in ("cycle_seconds", "headlines_every_seconds", "run_minutes",
                 "slow_cycle_seconds"):
        v = getattr(cfg, name)
        if not (math.isfinite(v) and v > 0):
            a.error("--%s must be above zero" % name.replace("_", "-"))
    for name in ("successor_lead_minutes", "grace_seconds", "successor_retry_seconds"):
        v = getattr(cfg, name)
        if not (math.isfinite(v) and v >= 0):
            a.error("--%s must be zero or more" % name.replace("_", "-"))
    token = os.environ.get("GH_TOKEN", "")
    _token[0] = token
    if token:
        basic = base64.b64encode(("x-access-token:" + token).encode()).decode()
        _secrets.extend([basic, token])
    if cfg.remote is None:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            a.error("no --remote and GH_TOKEN or GITHUB_REPOSITORY missing")
        cfg.remote = "https://github.com/%s.git" % repo
    if cfg.remote.startswith("-"):
        a.error("--remote may not start with '-'")
    # a remote given with credentials in it must not be printed either
    m = re.match(r"[a-z]+://([^/@]+)@", cfg.remote)
    if m:
        _secrets.insert(0, m.group(1))
    return cfg


def main(argv):
    if argv[:1] == ["--fetch-child"]:
        return fetch_child(*argv[1:6])
    cfg = parse(argv)
    try:
        cfg.workdir = os.path.abspath(cfg.workdir or tempfile.mkdtemp(prefix="relay-"))
        os.makedirs(cfg.workdir, exist_ok=True)
        probe = os.path.join(cfg.workdir, ".probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.unlink(probe)
    except OSError as e:
        log("work folder not usable: %s" % e.strerror)
        return 2
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, on_signal)
    return main_loop(cfg)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
