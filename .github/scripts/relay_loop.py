#!/usr/bin/env python3
"""relay_loop.py Rev 1

Changelog:
- Rev 1: first version (plan task 2). One process is one "Data relay" run.

What it does, in a loop, for about 5.5 hours:
- every 2 minutes: fetch prices (fetch_prices.run) and publish ticker.json
  to the branch "ticker-data" (one commit, replaced each time);
- every 15 minutes: run fetch_headlines.py and publish headlines.json to the
  branch "headlines-data" the same way;
- when no non-crypto item is open (all markets closed), the price cycle
  slows to once every 15 minutes;
- 10 minutes before the end it starts its successor (once), then it stops.

Safety:
- Only the branches ticker-data and headlines-data are ever pushed, with an
  explicit refspec, never main.
- Every child process has a timeout, and a watchdog ends the process at the
  run length plus a fixed grace, even if a child hangs.
- The price fetch runs in a child process, so a hang cannot freeze the loop.
- The token is never printed: every logged child output is scrubbed.
- SIGTERM or SIGINT: no new step starts, a running fetch is stopped, a
  running push may finish (a push replaces the branch in one step, so the
  branch is always either the old or the new commit). A second signal stops
  everything at once.
- 20 price cycles failing in a row: exit 1.

Python standard library only.
Exit codes: 0 normal end or stopped by signal, 1 too many failures,
2 bad arguments or unusable work folder, 3 watchdog.
"""
import argparse
import importlib.util
import json
import math
import os
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
TICK = 0.5

_stop = threading.Event()
_signals = [0]
_children = set()
_children_lock = threading.Lock()
_secrets = []


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
                             stdin=subprocess.DEVNULL, env=env, cwd=cwd,
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
    with _children_lock:
        for p in list(_children):
            _kill_group(p)


# ---------------------------------------------------------------- git

def git_env():
    env = dict(os.environ)
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
    tmp = tempfile.mkdtemp(prefix="pub-", dir=cfg.workdir)
    try:
        shutil.copyfile(src_path, os.path.join(tmp, name))
        env = git_env()
        steps = [
            ("init", ["git", "init", "-q", "-b", branch]),
            ("add", ["git", "add", "--", name]),
            ("commit", ["git", "-c", "commit.gpgsign=false", "commit", "-q",
                        "-m", message]),
            ("push", ["git", "-c", "credential.helper=", "push", "-q", "--force",
                      cfg.remote, "HEAD:refs/heads/" + branch]),
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
        shutil.rmtree(tmp, ignore_errors=True)


def bootstrap_previous(cfg, hard_end):
    """Copy the published ticker.json and headlines.json into the work folder,
    so held jumps and kept values carry over from the previous run."""
    tmp = tempfile.mkdtemp(prefix="boot-", dir=cfg.workdir)
    try:
        env = git_env()
        rc, out = run_cmd(["git", "init", "-q"], 20, hard_end, env=env, cwd=tmp)
        for branch, name in BRANCHES.items():
            if os.path.exists(os.path.join(cfg.workdir, name)):
                continue
            rc, out = run_cmd(["git", "-c", "credential.helper=", "fetch", "-q",
                               "--depth", "1", cfg.remote, "refs/heads/" + branch],
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
                json.loads(out)
            except ValueError:
                log("previous %s is not valid JSON, ignored" % name)
                continue
            with open(os.path.join(cfg.workdir, name), "w", encoding="utf-8") as f:
                f.write(out)
            log("previous %s loaded from %s" % (name, branch))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- prices

def fetch_child(module_path, symbols, out_path, prev_path, deadline):
    """Runs in a child process: fetch_prices.run() and an atomic write.
    Exit 0 if at least one item is fresh, 1 otherwise (file untouched)."""
    spec = importlib.util.spec_from_file_location("fetch_prices", module_path)
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    prev = prev_path if prev_path and os.path.exists(prev_path) else None
    doc = fp.run(symbols, prev, (), deadline=float(deadline))
    fresh = sum(1 for v in doc.get("sources", {}).values()
                if not str(v).startswith(("error", "kept")))
    total = len(doc.get("items", {}))
    if fresh == 0:
        print("nothing fresh, %d items" % total)
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
    with open(symbols_path, encoding="utf-8") as f:
        d = json.load(f)
    return {i["id"] for i in d.get("items", [])
            if isinstance(i, dict) and i.get("group") != "Crypto" and "id" in i}


def any_market_open(ticker_path, ids):
    """True if a non-crypto item is open, None if unknown."""
    try:
        with open(ticker_path, encoding="utf-8") as f:
            items = json.load(f).get("items", {})
    except (OSError, ValueError, AttributeError):
        return None
    return any(isinstance(items.get(i), dict) and items[i].get("open") is True
               for i in ids)


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
                                         stdin=subprocess.DEVNULL, start_new_session=True)
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
                n = len(json.load(f).get("items", []))
            os.replace(self.new, self.final)
        except (OSError, ValueError, AttributeError) as e:
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
    while True:
        left = hard_end - time.monotonic()
        if left <= 0:
            break
        time.sleep(min(left, 5))
    log("watchdog: run length plus grace reached, stopping hard")
    kill_all_children()
    sys.stdout.flush()
    os._exit(3)


def on_signal(signum, frame):
    _signals[0] += 1
    _stop.set()
    if _signals[0] >= 2:
        kill_all_children()


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
                          if cfg.successor_command else "off"))
    bootstrap_previous(cfg, hard_end)
    ids = non_crypto_ids(cfg.symbols)
    ticker_path = os.path.join(cfg.workdir, "ticker.json")
    heads = Headlines(cfg)
    next_price = next_head = t0
    successor_done = not cfg.successor_command
    slow = False
    fails = 0
    code = 0
    try:
        while not _stop.is_set():
            now = time.monotonic()
            if not successor_done and now >= succ_at:
                successor_done = True
                rc, out = run_cmd(shlex.split(cfg.successor_command),
                                  SUCCESSOR_TIMEOUT, hard_end)
                log("successor started" if rc == 0 else
                    "successor failed: %s" % short(out))
            heads.poll(hard_end)
            if now >= end:
                break
            if now >= next_head and not heads.proc:
                heads.start()
                next_head = next_slot(next_head, cfg.headlines_every_seconds, now)
            if now >= next_price:
                ok, msg = price_cycle(cfg, hard_end, cfg.cycle_seconds)
                if _stop.is_set() and not ok:
                    break
                fails = 0 if ok else fails + 1
                log(msg if ok else "%s (failed %d in a row)" % (msg, fails))
                if fails >= MAX_FAILS:
                    log("%d price cycles failed in a row, giving up" % fails)
                    code = 1
                    break
                is_open = any_market_open(ticker_path, ids)
                if is_open is not None and slow == is_open:
                    slow = not is_open
                    log("slow mode %s (%s)" % ("on" if slow else "off",
                        "all markets closed" if slow else "a market is open"))
                length = cfg.slow_cycle_seconds if slow else cfg.cycle_seconds
                next_price = next_slot(next_price, length, time.monotonic())
            wake = min(next_price, end,
                       next_head if not heads.proc else end,
                       succ_at if not successor_done else end)
            if heads.proc:
                wake = min(wake, time.monotonic() + 1.0)
            _stop.wait(max(0.0, wake - time.monotonic()))
    finally:
        heads.stop()
    if _stop.is_set():
        log("stopped by signal, branches left as last published")
    elif code == 0:
        log("relay end after %.1f min" % ((time.monotonic() - t0) / 60))
    return code


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
    a.add_argument("--grace-seconds", type=float, default=300)
    a.add_argument("--workdir", default=None)
    a.add_argument("--symbols", default=os.path.join(ROOT, "ticker-symbols.json"))
    a.add_argument("--feeds", default=os.path.join(ROOT, "headlines-feeds.json"))
    a.add_argument("--prices-module", default=os.path.join(HERE, "fetch_prices.py"))
    a.add_argument("--headlines-script", default=os.path.join(HERE, "fetch_headlines.py"))
    cfg = a.parse_args(argv)
    for name in ("cycle_seconds", "headlines_every_seconds", "run_minutes",
                 "slow_cycle_seconds"):
        v = getattr(cfg, name)
        if not (math.isfinite(v) and v > 0):
            a.error("--%s must be above zero" % name.replace("_", "-"))
    for name in ("successor_lead_minutes", "grace_seconds"):
        v = getattr(cfg, name)
        if not (math.isfinite(v) and v >= 0):
            a.error("--%s must be zero or more" % name.replace("_", "-"))
    token = os.environ.get("GH_TOKEN", "")
    _secrets.append(token)
    if cfg.remote is None:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not token or "/" not in repo:
            a.error("no --remote and GH_TOKEN or GITHUB_REPOSITORY missing")
        cfg.remote = "https://x-access-token:%s@github.com/%s.git" % (token, repo)
    _secrets.insert(0, cfg.remote)
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
