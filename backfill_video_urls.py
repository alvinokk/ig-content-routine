#!/usr/bin/env python3
"""
One-off backfill: re-scrape historical viral posts by Post URL via Apify
to get fresh (non-expired) video URLs, so Whisper can transcribe them.

- Finds video posts with no transcript (or previously failed)
- Re-scrapes them by direct post URL (apify~instagram-post-scraper)
- Updates Video URL, clears Transcript, bumps Last Synced to today
  (so transcribe_videos.py's freshness filter picks them up)
- Posts Apify can't return (deleted/private) get marked (视频不可用)

Env: AIRTABLE_PAT, APIFY_TOKEN
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

AIRTABLE_BASE = "appaGrhUHc8aHmGIT"
TABLES = [
    ("tblfMRnvStZziWkPq", "IG Competitors"),
    ("tbl9jmrGp0DK1vJtt", "AI Competitors"),
]
ACTOR = "apify~instagram-post-scraper"
POLL_INTERVAL = 20
POLL_TIMEOUT = 1200


def _request(url, method="GET", data=None, headers=None, timeout=60):
    hdrs = headers or {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_targets(pat, table_id):
    """Video posts lacking a transcript."""
    formula = urllib.parse.quote(
        "AND(OR({Post Type}='Video', {Post Type}='Reel', {Video URL}!=''), "
        "OR({Transcript}='', {Transcript}='(转录失败)'))"
    )
    base_url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table_id}"
        f"?filterByFormula={formula}&pageSize=100"
        f"&fields%5B%5D=Post%20ID&fields%5B%5D=Post%20URL"
    )
    headers = {"Authorization": f"Bearer {pat}"}
    out, offset = [], None
    while True:
        url = base_url + (f"&offset={urllib.parse.quote(offset)}" if offset else "")
        resp = _request(url, headers=headers, timeout=30)
        for rec in resp.get("records", []):
            f = rec.get("fields") or {}
            pid = str(f.get("Post ID") or "").strip()
            purl = f.get("Post URL") or (f"https://www.instagram.com/p/{pid}/" if pid else "")
            if pid and purl:
                out.append({"rec": rec["id"], "pid": pid, "url": purl})
        offset = resp.get("offset")
        if not offset:
            break
    return out


def run_apify(token, post_urls):
    print(f"[Apify] Re-scraping {len(post_urls)} posts by URL...")
    run_url = f"https://api.apify.com/v2/acts/{ACTOR}/runs?token={token}"
    result = _request(run_url, method="POST",
                      data={"username": post_urls, "resultsLimit": 1}, timeout=120)
    run_id = result["data"]["id"]
    print(f"  Run started: {run_id}")
    poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}"
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        status = _request(poll_url, timeout=30)["data"]
        print(f"  Polling... {status['status']}")
        if status["status"] == "SUCCEEDED":
            ds = status["defaultDatasetId"]
            items = _request(
                f"https://api.apify.com/v2/datasets/{ds}/items?token={token}&clean=true",
                timeout=120)
            print(f"  Fetched {len(items)} items")
            return items if isinstance(items, list) else []
        if status["status"] in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run ended: {status['status']}")
    raise TimeoutError("Apify run did not finish in time")


def batch_update(pat, table_id, updates):
    """PATCH up to 10 records per request."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table_id}"
    headers = {"Authorization": f"Bearer {pat}"}
    n = 0
    for i in range(0, len(updates), 10):
        batch = updates[i:i + 10]
        _request(url, method="PATCH",
                 data={"records": batch, "typecast": True},
                 headers=headers, timeout=30)
        n += len(batch)
        time.sleep(0.25)
    return n


def main():
    pat = (os.environ.get("AIRTABLE_PAT") or os.environ.get("AIRTABLE_API_KEY") or "").strip()
    token = (os.environ.get("APIFY_TOKEN") or os.environ.get("APIFY_API_TOKEN") or "").strip()
    if not pat or not token:
        print("ERROR: need AIRTABLE_PAT and APIFY_TOKEN")
        sys.exit(1)

    today = datetime.now(timezone.utc).date().isoformat()

    # 1. collect targets from both tables
    all_targets = {}
    for table_id, label in TABLES:
        targets = fetch_targets(pat, table_id)
        print(f"[{label}] {len(targets)} video posts need fresh URLs")
        for t in targets:
            t["tbl"] = table_id
            all_targets[t["pid"]] = t

    if not all_targets:
        print("Nothing to backfill.")
        return

    # 2. one Apify run with all post URLs
    items = run_apify(token, [t["url"] for t in all_targets.values()])

    # 3. map shortcode -> fresh video url
    fresh = {}
    for it in items:
        code = it.get("shortCode") or it.get("code") or ""
        vurl = it.get("videoUrl") or ""
        if code and vurl:
            fresh[code] = vurl

    # 4. build per-table updates
    matched = missed = 0
    per_table = {tid: [] for tid, _ in TABLES}
    for pid, t in all_targets.items():
        if pid in fresh:
            per_table[t["tbl"]].append({"id": t["rec"], "fields": {
                "Video URL": fresh[pid], "Transcript": "", "Last Synced": today}})
            matched += 1
        else:
            per_table[t["tbl"]].append({"id": t["rec"], "fields": {
                "Transcript": "(视频不可用)"}})
            missed += 1

    for table_id, label in TABLES:
        if per_table[table_id]:
            n = batch_update(pat, table_id, per_table[table_id])
            print(f"[{label}] updated {n} records")

    print(f"\nDone: {matched} fresh video URLs, {missed} unavailable (marked).")


if __name__ == "__main__":
    main()
