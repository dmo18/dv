#!/usr/bin/env python3
"""Build epg.json ("what is on now") for tv.html from Pluto TV's public channel guide.

fetch_epg.py Rev 1 (2026-11-19)

Usage: fetch_epg.py <channels.json> <epg-map.json> <out/epg.json>

epg-map.json maps our channel names (as in channels.json) to Pluto TV channel names. Only mapped channels get
a schedule; a channel missing from Pluto's answer is skipped. Output:
  { "generated": <unix seconds>, "source": "Pluto TV guide",
    "channels": { "<our channel name>": [ { "start": <unix s>, "stop": <unix s>, "title": "..." }, ... ] } }
Only the standard library is used. Exit status 1 when no channel at all could be filled (nothing is published then).
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HOURS = 8


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:00:00.000Z")


def unix(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def main():
    channels_path, map_path, out_path = sys.argv[1:4]
    ours = {c["name"] for c in json.load(open(channels_path, encoding="utf-8"))}
    mapping = json.load(open(map_path, encoding="utf-8"))
    now = datetime.now(timezone.utc)
    url = "https://api.pluto.tv/v2/channels?start=%s&stop=%s" % (iso(now - timedelta(hours=1)), iso(now + timedelta(hours=HOURS)))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        guide = json.load(r)
    by_name = {}
    for c in guide:
        by_name.setdefault(c.get("name", ""), c)

    out = {}
    for name, pluto_name in mapping.items():
        if name.startswith("_"):
            continue
        if name not in ours:
            print("not in channels.json, skipped:", name)
            continue
        c = by_name.get(pluto_name)
        if not c:
            print("not in the guide, skipped:", name, "->", pluto_name)
            continue
        shows = []
        for t in c.get("timelines") or []:
            title = str(t.get("title") or "").strip()[:120]
            try:
                start, stop = unix(t["start"]), unix(t["stop"])
            except Exception:
                continue
            if title and stop > start:
                shows.append({"start": start, "stop": stop, "title": title})
        shows.sort(key=lambda s: s["start"])
        if shows:
            out[name] = shows
    print("channels with a schedule:", len(out), "of", len([k for k in mapping if not k.startswith("_")]))
    if not out:
        return 1
    json.dump({"generated": int(time.time()), "source": "Pluto TV guide", "channels": out},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
