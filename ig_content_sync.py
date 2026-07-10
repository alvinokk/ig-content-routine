#!/usr/bin/env python3
"""
IG Content Sync Pipeline
------------------------
Scrapes Instagram profiles and posts via Apify, calculates engagement rate,
filters viral content (ER > 3%, comments > 50), deduplicates against Airtable,
and inserts new records.

Requirements: Python 3.11+ stdlib only (no pip packages).
Tokens: APIFY_TOKEN, AIRTABLE_PAT (environment variables).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IG_USERNAMES = [
    "leo_sfm",
    "headhomeuni",
    "dayoff_joanna",
    "growithfyn",
    "iamchrischung",
    "jacky5908",
    "_orangelaii",
    "tang_digital.nomad",
    "bored_raccoon_teacher",
    "oleaandfig",
]

AIRTABLE_BASE = "appaGrhUHc8aHmGIT"
AIRTABLE_TABLE = "tblfMRnvStZziWkPq"
COMPETITORS_TABLE = "tblPmOMhS3FW3KvnG"  # self-service competitor list
TRACKER_LABEL = "IG Competitors"

FIELD_MAP = {
    "post_id": "fldlNaLuLUaSzsdyb",
    "competitor": "fldOlYf1PNZJ3PPz8",
    "caption": "fldP1TX2wkqibY4uN",
    "post_type": "fldH6eeA40IOwGL4D",
    "likes": "fldBOkbspLs8xl7Pe",
    "comments": "fld9UscYAC5jX87dP",
    "engagement_rate": "flddOBGfc5TcLL1rp",
    "followers": "fldGJYb5S30hjqLHq",
    "post_date": "fldXURZXJCTC7AbEy",
    "post_url": "fldFI4OIA0hceaMYB",
    "preview": "fld5S0Zwpf1pZC7la",
    "video_url": "fldvEQGpgIHE0akIA",
    "hashtags": "fldgtSZKmF8J6tfuF",
    "is_video": "fldYMwgvQboJR4Ae0",
    "synced_date": "fldCRNqXbVSNG34BB",
}

POLL_INTERVAL = 30      # seconds between status polls
POLL_TIMEOUT = 900      # 15 minutes max wait per actor run
BATCH_SIZE = 10         # Airtable max records per request
BATCH_DELAY = 0.25      # seconds between Airtable batches (rate limit)
MIN_ER = 0.03           # 3% engagement rate threshold
MIN_COMMENTS = 50       # minimum comments threshold
MAX_RECORDS = 900       # cleanup threshold (Free plan = 1000 max)
STALE_DAYS = 30         # delete records not synced in this many days

# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------


def _request(url, method="GET", data=None, headers=None, timeout=60):
    """Fire an HTTP request and return parsed JSON (or raw bytes on non-JSON)."""
    hdrs = headers or {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return raw
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"  HTTP {exc.code} for {method} {url[:120]}")
        print(f"  Response: {err_body[:500]}")
        raise
    except urllib.error.URLError as exc:
        print(f"  URLError for {method} {url[:120]}: {exc.reason}")
        raise


# ---------------------------------------------------------------------------
# Competitor list (Airtable-managed, marketer self-service)
# ---------------------------------------------------------------------------


def fetch_competitors(pat):
    """Load active usernames for this tracker from the Competitors table.
    Returns [] on empty; caller falls back to the built-in list."""
    formula = urllib.parse.quote(
        f"AND({{Tracker}}='{TRACKER_LABEL}', {{Active}}=1)"
    )
    base_url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{COMPETITORS_TABLE}"
        f"?filterByFormula={formula}&pageSize=100"
    )
    headers = {"Authorization": f"Bearer {pat}"}
    names = []
    offset = None
    while True:
        url = base_url + (f"&offset={urllib.parse.quote(offset)}" if offset else "")
        resp = _request(url, headers=headers, timeout=30)
        for rec in resp.get("records", []):
            n = ((rec.get("fields") or {}).get("Username") or "").strip().lstrip("@")
            if n:
                names.append(n)
        offset = resp.get("offset")
        if not offset:
            break
    return names


# ---------------------------------------------------------------------------
# Apify helpers
# ---------------------------------------------------------------------------


def _run_apify_actor(token, actor_id, actor_input, step_label):
    """Start an Apify actor, poll until SUCCEEDED, return dataset items."""
    print(f"[{step_label}] Triggering Apify actor: {actor_id}...")
    run_url = (
        f"https://api.apify.com/v2/acts/{actor_id}/runs"
        f"?token={token}"
    )
    result = _request(run_url, method="POST", data=actor_input, timeout=120)
    run_id = result["data"]["id"]
    print(f"  Run started: {run_id}")

    # Poll until finished
    poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}"
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        status_resp = _request(poll_url, timeout=30)
        status = status_resp["data"]["status"]
        print(f"  Polling... status={status}")
        if status == "SUCCEEDED":
            dataset_id = status_resp["data"]["defaultDatasetId"]
            print(f"  Completed. Dataset: {dataset_id}")
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run ended with status: {status}")
    else:
        raise TimeoutError(f"Apify run {run_id} did not finish within {POLL_TIMEOUT}s")

    # Fetch dataset
    print(f"  Fetching dataset items...")
    ds_url = (
        f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        f"?token={token}&clean=true"
    )
    items = _request(ds_url, timeout=120)
    if not isinstance(items, list):
        items = []
    print(f"  Fetched {len(items)} items")
    return items


# ---------------------------------------------------------------------------
# Step 1 -- Scrape Instagram profiles (follower counts)
# ---------------------------------------------------------------------------


def scrape_profiles(token):
    """Scrape IG profiles and return {username: followersCount} mapping."""
    actor_input = {"usernames": IG_USERNAMES}
    items = _run_apify_actor(
        token,
        "apify~instagram-profile-scraper",
        actor_input,
        "Step 1",
    )

    followers_map = {}
    for profile in items:
        username = (profile.get("username") or "").lower().strip()
        count = profile.get("followersCount") or profile.get("followers") or 0
        if username and count > 0:
            followers_map[username] = int(count)

    print(f"  Follower counts for {len(followers_map)} profiles:")
    for u, c in sorted(followers_map.items()):
        print(f"    @{u}: {c:,}")

    # Warn about missing profiles
    for u in IG_USERNAMES:
        if u.lower() not in followers_map:
            print(f"  WARNING: No follower data for @{u}")

    return followers_map


# ---------------------------------------------------------------------------
# Step 2 -- Scrape Instagram posts
# ---------------------------------------------------------------------------


def scrape_posts(token):
    """Scrape recent IG posts and return raw items."""
    actor_input = {
        "username": IG_USERNAMES,
        "resultsLimit": 50,
        "onlyPostsNewerThan": "30 days",
    }
    items = _run_apify_actor(
        token,
        "apify~instagram-post-scraper",
        actor_input,
        "Step 2",
    )
    return items


# ---------------------------------------------------------------------------
# Step 3 -- Calculate engagement rate & filter
# ---------------------------------------------------------------------------


def parse_timestamp(value):
    """Parse IG post timestamp to a date object.

    Handles ISO strings and Unix timestamps.
    """
    if value is None:
        return None

    # Unix timestamp (seconds)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).date()

    if isinstance(value, str):
        # Try as numeric
        try:
            ts = float(value)
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except ValueError:
            pass
        # ISO date formats
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue

    return None


def detect_post_type(post):
    """Determine post type: Image, Video, Carousel, or Reel."""
    raw_type = (post.get("type") or "").strip()

    type_map = {
        "Image": "Image",
        "Video": "Video",
        "Sidecar": "Carousel",
        "Reel": "Reel",
        "GraphImage": "Image",
        "GraphVideo": "Video",
        "GraphSidecar": "Carousel",
    }

    if raw_type in type_map:
        return type_map[raw_type]

    # Fallback: check isVideo flag
    if post.get("isVideo"):
        return "Video"

    return "Image"


def calculate_and_filter(posts, followers_map):
    """Calculate ER for each post, filter by ER > 3% AND comments > 50.

    Returns (filtered_posts, per_competitor_stats).
    """
    print(f"\n[Step 3] Calculating engagement rate & filtering...")
    print(f"  Thresholds: ER > {MIN_ER:.0%}, comments > {MIN_COMMENTS}")

    stats = {}
    filtered = []

    for post in posts:
        try:
            username = (
                post.get("ownerUsername")
                or post.get("owner", {}).get("username")
                or ""
            ).lower().strip()

            if not username:
                continue

            followers = followers_map.get(username, 0)
            if followers == 0:
                # Skip posts where we have no follower data
                stats.setdefault(username, _new_stat())
                stats[username]["scraped"] += 1
                stats[username]["no_followers"] += 1
                continue

            likes = int(post.get("likesCount") or post.get("likes") or 0)
            comments = int(post.get("commentsCount") or post.get("comments") or 0)
            er = (likes + comments) / followers if followers > 0 else 0

            stats.setdefault(username, _new_stat())
            stats[username]["scraped"] += 1

            if er > MIN_ER and comments > MIN_COMMENTS:
                short_code = post.get("shortCode") or post.get("code") or ""
                caption = post.get("caption") or ""
                post_date = parse_timestamp(post.get("timestamp"))
                display_url = post.get("displayUrl") or post.get("imageUrl") or ""
                video_url = post.get("videoUrl") or ""
                hashtags = post.get("hashtags") or []
                is_video = bool(post.get("isVideo"))
                post_type = detect_post_type(post)

                filtered.append({
                    "post_id": short_code,
                    "competitor": username,
                    "caption": caption[:5000] if caption else "",
                    "post_type": post_type,
                    "likes": likes,
                    "comments": comments,
                    "engagement_rate": round(er, 6),
                    "followers": followers,
                    "post_date": post_date.isoformat() if post_date else "",
                    "post_url": f"https://www.instagram.com/p/{short_code}/" if short_code else "",
                    "preview_url": display_url,
                    "video_url": video_url,
                    "hashtags": ", ".join(hashtags) if isinstance(hashtags, list) else str(hashtags),
                    "is_video": is_video,
                })
                stats[username]["filtered"] += 1
            else:
                stats[username]["below_threshold"] += 1

        except Exception as exc:
            post_code = post.get("shortCode") or "unknown"
            print(f"  WARNING: Error processing post {post_code}: {exc}")

    print(f"  {len(filtered)} posts pass filters (from {len(posts)} total)")
    return filtered, stats


def _new_stat():
    return {
        "scraped": 0,
        "filtered": 0,
        "below_threshold": 0,
        "no_followers": 0,
        "dupes": 0,
        "new": 0,
    }


# ---------------------------------------------------------------------------
# Step 4 -- Dedup against Airtable
# ---------------------------------------------------------------------------


def fetch_existing_post_ids(pat):
    """Paginate through Airtable and collect all existing Post IDs."""
    print("\n[Step 4] Fetching existing Post IDs from Airtable...")
    existing = set()
    offset = None

    # Use field ID for Post ID
    field_param = urllib.parse.quote(FIELD_MAP["post_id"])
    base_url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}"
        f"?fields%5B%5D={field_param}"
    )
    headers = {"Authorization": f"Bearer {pat}"}

    while True:
        url = base_url
        if offset:
            url += f"&offset={urllib.parse.quote(offset)}"
        resp = _request(url, headers=headers, timeout=30)
        for rec in resp.get("records", []):
            fields = rec.get("fields") or {}
            # Try field-ID key
            pid = fields.get(FIELD_MAP["post_id"])
            # Fallback to field name
            if not pid:
                pid = fields.get("Post ID")
            if pid:
                existing.add(str(pid))
        offset = resp.get("offset")
        if not offset:
            break

    print(f"  Found {len(existing)} existing Post IDs in Airtable")
    return existing


def dedup(posts, existing_ids, stats):
    """Remove posts whose post_id already exists in Airtable.
    Updates stats in-place with dupe/new counts."""
    new = []
    for p in posts:
        name = p["competitor"]
        if p["post_id"] and p["post_id"] not in existing_ids:
            new.append(p)
            stats.setdefault(name, _new_stat())
            stats[name]["new"] += 1
        else:
            stats.setdefault(name, _new_stat())
            stats[name]["dupes"] += 1

    dupes = len(posts) - len(new)
    print(f"  {dupes} duplicates removed, {len(new)} new posts to insert")
    return new, dupes


# ---------------------------------------------------------------------------
# Step 5 -- Insert into Airtable
# ---------------------------------------------------------------------------


def build_airtable_record(post):
    """Map our canonical post dict to Airtable field-ID based record."""
    today_str = datetime.now(timezone.utc).date().isoformat()
    fields = {}

    # Simple text/number fields
    simple_map = {
        "post_id": "post_id",
        "competitor": "competitor",
        "caption": "caption",
        "post_type": "post_type",
        "likes": "likes",
        "comments": "comments",
        "engagement_rate": "engagement_rate",
        "followers": "followers",
        "post_date": "post_date",
        "post_url": "post_url",
        "video_url": "video_url",
        "hashtags": "hashtags",
        "is_video": "is_video",
    }

    for at_key, src_key in simple_map.items():
        val = post.get(src_key)
        if val is None or val == "" or val == []:
            continue
        fields[FIELD_MAP[at_key]] = val

    # Preview attachment
    preview_url = post.get("preview_url")
    if preview_url:
        fields[FIELD_MAP["preview"]] = [{"url": preview_url}]

    # Synced date
    fields[FIELD_MAP["synced_date"]] = today_str

    return {"fields": fields}


def insert_to_airtable(pat, posts):
    """Batch-insert posts into Airtable (max 10 per request)."""
    print(f"\n[Step 5] Inserting {len(posts)} new posts into Airtable...")
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}"
    headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
    }

    inserted = 0
    errors = []

    for i in range(0, len(posts), BATCH_SIZE):
        batch = posts[i : i + BATCH_SIZE]
        records = [build_airtable_record(p) for p in batch]
        payload = {"records": records, "typecast": True}

        try:
            resp = _request(url, method="POST", data=payload, headers=headers, timeout=30)
            created = len(resp.get("records", []))
            inserted += created
            print(f"  Batch {i // BATCH_SIZE + 1}: inserted {created} records")
        except Exception as exc:
            err_msg = f"Batch {i // BATCH_SIZE + 1} failed: {exc}"
            print(f"  ERROR: {err_msg}")
            errors.append(err_msg)

        if i + BATCH_SIZE < len(posts):
            time.sleep(BATCH_DELAY)

    return inserted, errors


# ---------------------------------------------------------------------------
# Step 5b -- Auto-cleanup (Free plan: keep under MAX_RECORDS)
# ---------------------------------------------------------------------------


def cleanup_stale_records(pat):
    """Delete records with Last Synced older than STALE_DAYS, or oldest records
    if total count exceeds MAX_RECORDS. Returns number of records deleted."""
    print(f"\n[Step 5b] Checking cleanup (max {MAX_RECORDS}, stale > {STALE_DAYS}d)...")
    today = datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=STALE_DAYS)).isoformat()

    # Fetch all records with just the synced_date field
    all_records = []
    offset = None
    field_param = urllib.parse.quote(FIELD_MAP["synced_date"])
    base_url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}"
        f"?fields%5B%5D={field_param}"
    )
    headers = {"Authorization": f"Bearer {pat}"}

    while True:
        url = base_url
        if offset:
            url += f"&offset={urllib.parse.quote(offset)}"
        resp = _request(url, headers=headers, timeout=30)
        for rec in resp.get("records", []):
            synced = (rec.get("fields") or {}).get(FIELD_MAP["synced_date"], "")
            all_records.append({"id": rec["id"], "synced": synced})
        offset = resp.get("offset")
        if not offset:
            break

    total = len(all_records)
    print(f"  Total records in table: {total}")

    # Identify records to delete
    to_delete = set()

    # 1) Stale records (not synced in STALE_DAYS)
    for rec in all_records:
        if rec["synced"] and rec["synced"] < cutoff:
            to_delete.add(rec["id"])

    stale_count = len(to_delete)
    if stale_count:
        print(f"  Found {stale_count} stale records (Last Synced < {cutoff})")

    # 2) If still over MAX_RECORDS after removing stale, remove oldest
    remaining = total - len(to_delete)
    if remaining > MAX_RECORDS:
        non_deleted = [r for r in all_records if r["id"] not in to_delete]
        non_deleted.sort(key=lambda r: r["synced"] or "0000-00-00")
        overflow = remaining - MAX_RECORDS
        for rec in non_deleted[:overflow]:
            to_delete.add(rec["id"])
        print(f"  Removing {overflow} additional oldest records to stay under {MAX_RECORDS}")

    if not to_delete:
        print("  No cleanup needed.")
        return 0

    # Batch delete (max 10 per request)
    delete_list = list(to_delete)
    deleted = 0
    del_headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
    }

    for i in range(0, len(delete_list), BATCH_SIZE):
        batch = delete_list[i : i + BATCH_SIZE]
        params = "&".join(f"records[]={rid}" for rid in batch)
        del_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}?{params}"
        try:
            _request(del_url, method="DELETE", headers=del_headers, timeout=30)
            deleted += len(batch)
        except Exception as exc:
            print(f"  WARNING: Delete batch failed: {exc}")
        time.sleep(BATCH_DELAY)

    print(f"  Cleaned up {deleted} records. Remaining: ~{total - deleted}")
    return deleted


# ---------------------------------------------------------------------------
# Step 6 -- Summary
# ---------------------------------------------------------------------------


def print_summary(stats, total_scraped, errors, cleaned=0):
    """Print a per-competitor and totals breakdown."""
    print("\n" + "=" * 60)
    print("IG CONTENT SYNC SUMMARY")
    print("=" * 60)

    totals = {
        "scraped": 0,
        "filtered": 0,
        "dupes": 0,
        "new": 0,
        "below_threshold": 0,
        "no_followers": 0,
    }

    for name, s in sorted(stats.items()):
        print(f"\n  @{name}:")
        print(f"    Scraped:          {s['scraped']}")
        print(f"    Viral (filtered): {s['filtered']}")
        print(f"    Below threshold:  {s['below_threshold']}")
        print(f"    Dupes:            {s['dupes']}")
        print(f"    New inserted:     {s['new']}")
        if s["no_followers"]:
            print(f"    No follower data: {s['no_followers']}")
        for k in totals:
            totals[k] += s.get(k, 0)

    print(f"\n  TOTALS:")
    print(f"    Posts scraped:    {total_scraped}")
    print(f"    Viral (filtered): {totals['filtered']}")
    print(f"    Below threshold:  {totals['below_threshold']}")
    print(f"    Dupes skipped:    {totals['dupes']}")
    print(f"    New inserted:     {totals['new']}")
    print(f"    Cleaned up:       {cleaned}")
    print(f"    Filters:          ER > {MIN_ER:.0%}, Comments > {MIN_COMMENTS}")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    - {e}")
    else:
        print("\n  No errors.")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    # Check environment
    apify_token = os.environ.get("APIFY_TOKEN", "").strip()
    airtable_pat = os.environ.get("AIRTABLE_PAT", "").strip()

    if not apify_token:
        print("ERROR: APIFY_TOKEN environment variable is not set.")
        sys.exit(1)
    if not airtable_pat:
        print("ERROR: AIRTABLE_PAT environment variable is not set.")
        sys.exit(1)

    # Competitor list from Airtable (marketer-managed); fallback to built-in
    global IG_USERNAMES
    try:
        names = fetch_competitors(airtable_pat)
        if names:
            IG_USERNAMES = names
            print(f"[Config] {len(names)} active competitors loaded from Airtable")
        else:
            print("[Config] Competitors table returned none — using built-in list")
    except Exception as exc:
        print(f"[Config] Competitors fetch failed ({exc}) — using built-in list")

    all_errors = []

    # --- Step 1: Scrape profiles (follower counts) ---
    try:
        followers_map = scrape_profiles(apify_token)
    except Exception as exc:
        print(f"FATAL: Profile scrape failed: {exc}")
        sys.exit(1)

    if not followers_map:
        print("WARNING: No follower data retrieved. Cannot calculate ER.")
        print("Exiting to avoid inserting posts with 0% engagement rate.")
        sys.exit(1)

    # --- Step 2: Scrape posts ---
    try:
        raw_posts = scrape_posts(apify_token)
    except Exception as exc:
        print(f"FATAL: Post scrape failed: {exc}")
        sys.exit(1)

    if not raw_posts:
        print("No posts returned from Apify. Exiting.")
        sys.exit(0)

    total_scraped = len(raw_posts)

    # --- Step 3: Calculate ER & filter ---
    filtered_posts, stats = calculate_and_filter(raw_posts, followers_map)

    if not filtered_posts:
        print("  No posts passed the engagement filters.")
        print_summary(stats, total_scraped, all_errors)
        sys.exit(0)

    # --- Step 4: Dedup ---
    try:
        existing_ids = fetch_existing_post_ids(airtable_pat)
    except Exception as exc:
        print(f"FATAL: Could not fetch Airtable records: {exc}")
        sys.exit(1)

    new_posts, total_dupes = dedup(filtered_posts, existing_ids, stats)

    # --- Step 5: Insert ---
    if new_posts:
        inserted, insert_errors = insert_to_airtable(airtable_pat, new_posts)
        all_errors.extend(insert_errors)
        print(f"  Successfully inserted {inserted} records")
    else:
        print("  No new posts to insert.")

    # --- Step 5b: Cleanup ---
    try:
        cleaned = cleanup_stale_records(airtable_pat)
    except Exception as exc:
        cleaned = 0
        err = f"Cleanup failed: {exc}"
        print(f"  WARNING: {err}")
        all_errors.append(err)

    # --- Step 6: Summary ---
    print_summary(stats, total_scraped, all_errors, cleaned)

    if all_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
