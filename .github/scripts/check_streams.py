#!/usr/bin/env python3
"""Check that every stream used by wallboard.html and encoder.html is actually broadcasting.

For each stream address this:
  1. downloads the playlist (following a master playlist to its first quality level),
  2. confirms the playlist lists video segments and that the newest segment downloads,
  3. confirms the server allows playback from other websites (a browser requirement),
  4. looks again after a pause to confirm the stream is moving, not frozen.

It writes a Markdown report and prints the number of dead streams on the last line
("DEAD=<n>"). Event feeds that are normally off air between events are reported but
not counted as dead.

It also checks how old the data files published by the "Data relay" are (prices on branch ticker-data,
headlines on branch headlines-data). A file older than 60 minutes, unreadable, or dated in the future
is reported in the same report and counted in DEAD, so the same issue opens.

Also enforces the standing rule: every stream address used on the wall (main and backup, exact
string) must also be saved in encoder.html. Missing ones are listed and counted in DEAD, and the
script exits with status 1.

Changelog:
  2026-11-19  The encoder list is read from channels.json (next to encoder.html), which encoder.html and tv.html share.
  2026-11-17  Review fixes: only problems that affect the wall are counted in DEAD (wall streams, wall addresses
              missing from encoder.html, stale data, unreadable tiles); encoder-only spares that fail (for
              example IRINN, Kan 11) are listed for information and no longer keep the issue open. A NaN time
              stamp or age limit is rejected. A backup address written with single quotes is reported.
  2026-11-16  Freshness alarm: ticker.json and headlines.json older than 60 minutes are reported and counted.
              Also reports wall tiles it could not read (written differently from the expected pattern),
              and the summary now separates not playing, missing from encoder.html and stale data.
              New options: --ticker-url, --headlines-url, --max-age-min, --data-only (data check only).
  2026-11-16  Reads the new wallboard tile list (player(...) calls with an options object in any key
              order: alt, altLabel, cc). Fails when a wall address is missing from encoder.html.

Usage: check_streams.py <wallboard.html> <encoder.html> <report.md>
         [--ticker-url URL] [--headlines-url URL] [--max-age-min 60] [--data-only]
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urljoin

TIMEOUT_S = 20
PAUSE_S = 25
ORIGIN = "https://dmo18.github.io"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
# Feeds that only broadcast during events. Off air is normal for these.
EVENT_FEED_MARKERS = ("abcn-live-",)


UNREAD_TILES = 0   # wall tiles written in a form the pattern below could not read (set by collect)


def collect(wallboard_path, encoder_path):
    """Return [(where, name, url)] with one row per distinct address."""
    global UNREAD_TILES
    rows, seen = [], set()

    def add(where, name, url):
        if url not in seen:
            seen.add(url)
            rows.append((where, name, url))

    wall = open(wallboard_path, encoding="utf-8").read()
    # Ignore comments, so examples in them are not read as tiles.
    wall = re.sub(r"<!--.*?-->|/\*.*?\*/", "", wall, flags=re.S)
    wall = re.sub(r"(?m)^\s*//.*$", "", wall)
    call = re.compile(r'player\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"(https?://[^"]+)"\s*(?:,\s*\{([^}]*)\})?\s*\)')
    UNREAD_TILES = max(0, len(re.findall(r"\bplayer\(", wall)) - len(call.findall(wall)))
    for m in call.finditer(wall):
        opts = m.group(4) or ""
        alt = re.search(r'\balt\s*:\s*"([^"]+)"', opts)
        if re.search(r"\balt\s*:", opts) and not alt:
            UNREAD_TILES += 1      # a backup address written in a form this check cannot read
        alt_label = re.search(r'\baltLabel\s*:\s*"([^"]+)"', opts)
        add("Wallboard", m.group(2), m.group(3))
        if alt:
            add("Wallboard backup", alt_label.group(1) if alt_label else m.group(2), alt.group(1))

    for c in channels(encoder_path):
        add("Encoder", c["name"], c["url"])
    return rows


def channels(encoder_path):
    """The encoder's channel list: channels.json next to encoder.html."""
    path = os.path.join(os.path.dirname(os.path.abspath(encoder_path)), "channels.json")
    return json.load(open(path, encoding="utf-8"))


def encoder_urls(encoder_path):
    return {c["url"] for c in channels(encoder_path)}


def get(url, byte_range=None):
    headers = {"User-Agent": USER_AGENT, "Origin": ORIGIN, "Referer": ORIGIN + "/"}
    if byte_range:
        headers["Range"] = byte_range
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        # Playlists are read whole (a DVR playlist can be long); segments only need their first bytes.
        limit = 4096 if byte_range else 8_000_000
        return res.read(limit), res.headers.get("Access-Control-Allow-Origin")


def uris(playlist):
    return [l.strip() for l in playlist.splitlines() if l.strip() and not l.startswith("#")]


def media_playlist(url):
    """Return (media playlist url, text, cors header), following a master playlist once."""
    data, cors = get(url)
    text = data.decode("utf-8", "replace")
    if "#EXTM3U" not in text:
        raise ValueError("not a playlist")
    if "#EXTINF" not in text:
        variants = uris(text)
        if not variants:
            raise ValueError("playlist is empty")
        url = urljoin(url, variants[0])
        data, _ = get(url)
        text = data.decode("utf-8", "replace")
    return url, text, cors


def check(url):
    """Return (state, detail). state is 'ok', 'frozen' or 'dead'."""
    try:
        media_url, text, cors = media_playlist(url)
        segments = uris(text)
        if "#EXTINF" not in text or not segments:
            return "dead", "playlist lists no video", None
        get(urljoin(media_url, segments[-1]), "bytes=0-2047")
        if cors not in ("*", ORIGIN):
            return "dead", "server does not allow playback from other websites", None
        return "ok", "", (media_url, segments[-1])
    except Exception as exc:
        return "dead", str(exc)[:90], None


def data_freshness(sources, max_age_s):
    """sources: [(label, url)]. Return [(label, problem)] for every file that is stale or unreadable."""
    problems = []
    for label, url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
                doc = json.loads(res.read(4_000_000).decode("utf-8", "replace"))
            gen = doc.get("generated") if isinstance(doc, dict) else None
            if isinstance(gen, bool) or not isinstance(gen, (int, float)) or not math.isfinite(gen) or gen <= 0:
                problems.append((label, "file has no valid time stamp"))
                continue
            age = time.time() - gen
            if age < -600:
                problems.append((label, "time stamp is in the future"))
            elif age > max_age_s:
                problems.append((label, f"last updated {age / 3600:.1f} hours ago" if age >= 7200 else f"last updated {int(age / 60)} minutes ago"))
        except Exception as exc:
            problems.append((label, f"could not be read: {str(exc)[:80]}"))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallboard")
    ap.add_argument("encoder")
    ap.add_argument("report")
    repo = os.environ.get("GITHUB_REPOSITORY") or "dmo18/dv"
    ap.add_argument("--ticker-url", default=f"https://raw.githubusercontent.com/{repo}/ticker-data/ticker.json")
    ap.add_argument("--headlines-url", default=f"https://raw.githubusercontent.com/{repo}/headlines-data/headlines.json")
    ap.add_argument("--max-age-min", type=float, default=60)
    ap.add_argument("--data-only", action="store_true", help="only check the data files (for tests)")
    args = ap.parse_args()
    if not (math.isfinite(args.max_age_min) and args.max_age_min > 0):
        ap.error("--max-age-min must be a finite number above 0")
    wallboard_path, encoder_path, report_path = args.wallboard, args.encoder, args.report

    stale = data_freshness([("Prices file (ticker-data)", args.ticker_url),
                            ("Headlines file (headlines-data)", args.headlines_url)], args.max_age_min * 60)
    data_lines = []
    if stale:
        data_lines = ["### Data not fresh", "", "The Data relay should refresh these every few minutes. "
                      "If this stays, check the Actions tab: run the \"Data relay\" workflow by hand.", "",
                      "| File | Problem |", "|---|---|"] + [f"| {l} | {p} |" for l, p in stale] + [""]
    if args.data_only:
        text = "\n".join(data_lines) if data_lines else "Data files are fresh.\n"
        open(report_path, "w", encoding="utf-8").write(text + "\n")
        print(text)
        print(f"DATA_PROBLEMS={len(stale)}")
        sys.exit(1 if stale else 0)

    rows = collect(wallboard_path, encoder_path)
    saved = encoder_urls(encoder_path)
    unsaved = [r for r in rows if r[0].startswith("Wallboard") and r[2] not in saved]
    first = [check(url) for _, _, url in rows]

    time.sleep(PAUSE_S)
    results = []
    for (where, name, url), (state, detail, marker) in zip(rows, first):
        if state == "ok":
            try:
                media_url, text, _ = media_playlist(url)
                if "#EXT-X-ENDLIST" not in text and uris(text)[-1] == marker[1]:
                    time.sleep(10)   # slow playlists: give it one more look
                    _, text, _ = media_playlist(url)
                    if uris(text)[-1] == marker[1]:
                        state, detail = "frozen", "playlist is not advancing"
            except Exception as exc:
                state, detail = "dead", str(exc)[:90]
        event = any(k in url for k in EVENT_FEED_MARKERS)
        results.append((where, name, url, state, detail, event))

    bad = [r for r in results if r[3] != "ok" and not r[5]]
    # Only streams the wall actually plays count as problems. Encoder-only spares (for example IRINN, Kan 11) are
    # listed for information: they must not keep the issue open, or a later data outage would raise no new email.
    not_playing = [r for r in bad if r[0].startswith("Wallboard")]
    spares = [r for r in bad if not r[0].startswith("Wallboard")]
    off_air = [r for r in results if r[3] != "ok" and r[5]]
    playing = len(results) - len(bad) - len(off_air)
    # Standing rule: a wall address missing from the encoder counts as a failure.
    missing = [(w, n, u, "dead", "address is not saved in encoder.html", False) for w, n, u in unsaved]
    dead = not_playing + missing
    problems = len(dead) + len(stale) + (1 if UNREAD_TILES else 0)

    lines = [f"Checked {len(results)} streams: {playing} playing, {len(not_playing)} wall streams not playing, "
             f"{len(missing)} wall address(es) missing from encoder.html, {len(spares)} spare stream(s) not playing "
             f"(encoder only, not counted), {len(off_air)} event feeds off air. Data files stale: {len(stale)}.", ""]
    if UNREAD_TILES:
        lines += ["### Wall tiles not recognised", "",
                  f"{UNREAD_TILES} tile(s) in wallboard.html are written in a form this check cannot read "
                  "(for example a variable or single quotes instead of a plain \"address\"). They are not being checked.", ""]
    lines += data_lines
    if dead:
        lines += ["### Not playing or not saved", "", "| Used in | Channel | Problem |", "|---|---|---|"]
        lines += [f"| {w} | {n} | {d or s} |" for w, n, _, s, d, _ in dead]
        lines.append("")
    if spares:
        lines += ["### Spare streams not playing (encoder only, for information)", "", "| Channel | Problem |", "|---|---|"]
        lines += [f"| {n} | {d or s} |" for w, n, _, s, d, _ in spares] + [""]
    if off_air:
        lines += ["### Event feeds off air (normal between events)", ""]
        lines += [f"- {n} ({w})" for w, n, *_ in off_air] + [""]
    lines += ["<details><summary>All streams</summary>", "", "| Used in | Channel | State |", "|---|---|---|"]
    lines += [f"| {w} | {n} | {'playing' if s == 'ok' else (d or s)} |" for w, n, _, s, d, _ in results]
    lines += ["", "</details>", "",
              "This check runs from GitHub's servers in the United States. A stream limited to "
              "another region can be reported as not playing here and still work on the wallboard PC."]
    report = "\n".join(lines) + "\n"
    open(report_path, "w", encoding="utf-8").write(report)
    print(report)
    print(f"WALL_STREAMS={sum(1 for r in rows if r[0].startswith('Wallboard'))} NOT_IN_ENCODER={len(unsaved)} "
          f"UNREAD_TILES={UNREAD_TILES} STALE_DATA={len(stale)}")
    print(f"DEAD={problems}")
    if unsaved or stale or UNREAD_TILES:
        sys.exit(1)


if __name__ == "__main__":
    main()
