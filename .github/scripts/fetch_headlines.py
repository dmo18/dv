#!/usr/bin/env python3
"""Fetch the news feeds listed in headlines.html and write them to one JSON file.

A browser cannot read most news feeds directly (the feeds do not allow cross-site
requests), so the "Update headlines" workflow runs this script on a schedule and
publishes the result, which headlines.html then loads.

The feed list is headlines-feeds.json. If a feed fails, its headlines from the previously
published file are kept, so a temporary outage does not remove a source from the ticker.

Usage: fetch_headlines.py <headlines-feeds.json> <output json path> [url of previous output]
"""
import gzip
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from email.utils import parsedate_to_datetime

PER_FEED_MAX = 8
TIMEOUT_S = 25
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def read_feeds(path):
    with open(path, encoding="utf-8") as f:
        return [(feed["name"], feed["url"]) for feed in json.load(f)["feeds"]]


def previous_items(url):
    """Headlines from the last published file, grouped by source ({} if there is none)."""
    by_source = {}
    try:
        for item in json.loads(download(url)).get("items", []):
            by_source.setdefault(item["source"], []).append(item)
    except Exception as exc:
        print("No previous file to fall back on:", str(exc)[:80])
    return by_source


def download(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        data = res.read()
        encoding = (res.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif encoding == "deflate":
        data = zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node, *names):
    for child in node:
        if local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def to_epoch(value):
    if not value:
        return 0
    try:
        return int(parsedate_to_datetime(value).timestamp())
    except Exception:
        pass
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def parse(data, source, strip_suffix):
    root = ET.fromstring(data)
    items = []
    for node in root.iter():
        kind = local(node.tag)
        if kind not in ("item", "entry"):
            continue
        title = " ".join(child_text(node, "title").split())
        link = child_text(node, "link")
        if not link:
            for child in node:
                if local(child.tag) == "link" and child.get("href"):
                    link = child.get("href").strip()
                    if child.get("rel", "alternate") == "alternate":
                        break
        date = child_text(node, "pubdate", "date", "updated", "published")
        if strip_suffix:
            # Google News search results end every title with " - Publisher".
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
        if title and link.startswith("http"):
            items.append({"title": title, "link": link, "pubDate": date,
                          "ts": to_epoch(date), "source": source})
    items.sort(key=lambda i: i["ts"], reverse=True)
    return items[:PER_FEED_MAX]


def main():
    feeds_path, out_path = sys.argv[1], sys.argv[2]
    previous = previous_items(sys.argv[3]) if len(sys.argv) > 3 else {}
    all_items, status = [], {}
    for name, url in read_feeds(feeds_path):
        try:
            items = parse(download(url), name, "news.google.com/rss/search" in url)
            if not items:
                raise ValueError("feed returned no headlines")
            status[name] = len(items)
        except Exception as exc:  # one bad feed must not stop the others
            # Keep that source's last known headlines so it does not vanish from the ticker.
            items = [dict(i, ts=to_epoch(i.get("pubDate"))) for i in previous.get(name, [])]
            status[name] = f"error: {str(exc)[:100]} (kept {len(items)} previous)"
        all_items.extend(items)
        print(f"{name:20} {status[name]}")

    if not all_items:
        print("No feed returned any headlines; leaving the previous file in place.")
        return 1

    all_items.sort(key=lambda i: i["ts"], reverse=True)
    for item in all_items:
        del item["ts"]
    with open(out_path, "w", encoding="utf-8") as out:
        json.dump({"generated": int(time.time()), "feeds": status, "items": all_items},
                  out, ensure_ascii=False, indent=1)
    print(f"Wrote {len(all_items)} headlines to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
