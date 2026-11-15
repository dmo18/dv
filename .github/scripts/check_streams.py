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

Usage: check_streams.py <wallboard.html> <encoder.html> <report.md>
"""
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


def collect(wallboard_path, encoder_path):
    """Return [(where, name, url)] with one row per distinct address."""
    rows, seen = [], set()

    def add(where, name, url):
        if url not in seen:
            seen.add(url)
            rows.append((where, name, url))

    wall = open(wallboard_path, encoding="utf-8").read()
    for m in re.finditer(r'player\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)"(?:,\s*\{\s*alt:\s*"([^"]+)",\s*altLabel:\s*"([^"]+)")?', wall):
        add("Wallboard", m.group(2), m.group(3))
        if m.group(4):
            add("Wallboard backup", m.group(5), m.group(4))

    enc = open(encoder_path, encoding="utf-8").read()
    block = enc[enc.index("const STREAMS = ["):]
    block = block[:block.index("];")]
    for name, url in re.findall(r'\{\s*name:\s*"([^"]+)",\s*url:\s*"([^"]+)"\s*\}', block):
        add("Encoder", name, url)
    return rows


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


def main():
    wallboard_path, encoder_path, report_path = sys.argv[1:4]
    rows = collect(wallboard_path, encoder_path)
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

    dead = [r for r in results if r[3] != "ok" and not r[5]]
    off_air = [r for r in results if r[3] != "ok" and r[5]]

    lines = [f"Checked {len(results)} streams: {len(results) - len(dead) - len(off_air)} playing, "
             f"{len(dead)} not playing, {len(off_air)} event feeds off air.", ""]
    if dead:
        lines += ["### Not playing", "", "| Used in | Channel | Problem |", "|---|---|---|"]
        lines += [f"| {w} | {n} | {d or s} |" for w, n, _, s, d, _ in dead]
        lines.append("")
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
    print(f"DEAD={len(dead)}")


if __name__ == "__main__":
    main()
