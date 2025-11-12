#!/usr/bin/env python3
import os, json, re, hashlib, time, email.utils, difflib
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
STATE_FILE = DATA / "state.json"
CHANGES_FILE = DATA / "changes.json"
FEED_FILE = ROOT / "feed.xml"   # served by GitHub Pages at /feed.xml
URLS_FILE = ROOT / "urls.json"

DATA.mkdir(exist_ok=True)
SNAPSHOTS.mkdir(exist_ok=True)

def load_json(p, default):
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default

def save_json(p, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def rfc2822_now(ts=None):
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return email.utils.format_datetime(dt)

def clean_html(html, selector=None):
    soup = BeautifulSoup(html, "html.parser")
    if selector:
        target = soup.select_one(selector)
        if target is None:
            return ""
        soup = BeautifulSoup(str(target), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text

def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def fetch_with_conditionals(url, prev_meta):
    headers = {
        "User-Agent": "GitHubActions-PageWatcher/1.0 (+https://github.com/)",
        "Accept": "text/html,application/xhtml+xml"
    }
    if prev_meta.get("etag"):
        headers["If-None-Match"] = prev_meta["etag"]
    if prev_meta.get("last_modified"):
        headers["If-Modified-Since"] = prev_meta["last_modified"]
    return requests.get(url, headers=headers, timeout=30)

def ensure_index_html():
    idx = ROOT / "index.html"
    if not idx.exists():
        idx.write_text(
            "<!doctype html><meta charset='utf-8'>"
            "<title>Page Update Feed</title>"
            "<h1>Page Update Feed</h1>"
            "<p>Subscribe to the <a href='feed.xml'>RSS feed</a>.</p>",
            encoding="utf-8"
        )

def generate_feed(changes):
    changes = sorted(changes, key=lambda c: c["timestamp"], reverse=True)[:100]
    channel_title = "Page Update Checker"
    channel_link = ""  # optional cosmetics
    channel_desc = "RSS feed of diffs detected by the GitHub Actions watcher."

    items_xml = []
    for c in changes:
        pub_date = rfc2822_now(c["timestamp"])
        title = f"{c['title']} changed"
        link = c["url"]
        guid = f"{c['id']}:{int(c['timestamp'])}"
        desc = c.get("summary", "Content changed.")
        def esc(s):
            return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
        items_xml.append(
f"""  <item>
    <title>{esc(title)}</title>
    <link>{esc(link)}</link>
    <guid isPermaLink="false">{esc(guid)}</guid>
    <pubDate>{pub_date}</pubDate>
    <description>{esc(desc)}</description>
  </item>"""
        )

    feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{channel_title}</title>
  <link>{channel_link}</link>
  <description>{channel_desc}</description>
  <lastBuildDate>{rfc2822_now()}</lastBuildDate>
{os.linesep.join(items_xml)}
</channel>
</rss>
"""
    FEED_FILE.write_text(feed_xml, encoding="utf-8")

def main():
    urls = load_json(URLS_FILE, [])
    state = load_json(STATE_FILE, {})
    changes = load_json(CHANGES_FILE, [])
    something_changed = False

    for entry in urls:
        uid = entry["id"]
        url = entry["url"]
        title = entry.get("title", url)
        selector = entry.get("selector")
        prev = state.get(uid, {})
        resp = fetch_with_conditionals(url, prev)

        if resp.status_code == 304:
            continue
        if not (200 <= resp.status_code < 300):
            continue

        etag = resp.headers.get("ETag")
        last_mod = resp.headers.get("Last-Modified")

        text = clean_html(resp.text, selector=selector)
        h = sha256(text)

        if prev.get("hash") != h:
            ts = time.time()
            something_changed = True

            snap_dir = SNAPSHOTS / uid
            snap_dir.mkdir(parents=True, exist_ok=True)
            (snap_dir / f"{int(ts)}.txt").write_text(text, encoding="utf-8")
            (snap_dir / f"{int(ts)}.html").write_text(resp.text, encoding="utf-8")

            summary = "Content changed."
            old_text = None
            if prev.get("last_text_file") and (snap_dir / prev["last_text_file"]).exists():
                old_text = (snap_dir / prev["last_text_file"]).read_text(encoding="utf-8")
            if old_text:
                diff = difflib.unified_diff(
                    old_text.splitlines(), text.splitlines(),
                    fromfile="before", tofile="after", lineterm=""
                )
                preview = []
                for i, line in enumerate(diff):
                    if i > 20:
                        preview.append("... (diff truncated)")
                        break
                    preview.append(line)
                summary = "\n".join(preview) or "Content changed."

            changes.append({
                "id": uid, "url": url, "title": title,
                "timestamp": ts, "hash": h, "summary": summary
            })

            state[uid] = {
                "hash": h, "etag": etag, "last_modified": last_mod,
                "last_text_file": f"{int(ts)}.txt"
            }
        else:
            state[uid] = {
                "hash": h,
                "etag": etag or prev.get("etag"),
                "last_modified": last_mod or prev.get("last_modified"),
                "last_text_file": prev.get("last_text_file")
            }

    if something_changed:
        changes = sorted(changes, key=lambda c: c["timestamp"], reverse=True)[:1000]
        save_json(CHANGES_FILE, changes)
        save_json(STATE_FILE, state)
        ensure_index_html()
        generate_feed(changes)
    else:
        ensure_index_html()
        generate_feed(changes)

if __name__ == "__main__":
    main()
