#!/usr/bin/env python3
"""
Video Transcription Pipeline
-----------------------------
Downloads new viral reels and transcribes the spoken audio with local
Whisper, writing the transcript back to Airtable's Transcript field.

Runs on GitHub Actions after each weekly sync. IG CDN video URLs expire
within days, so only recently-synced posts are attempted; anything that
fails is marked so it is never retried.

Env: AIRTABLE_PAT (required)
     WHISPER_MODEL (default "small"), MAX_AGE_DAYS (default 14),
     VIDEO_LIMIT (default 25 per run)
"""

import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request

AIRTABLE_BASE = "appaGrhUHc8aHmGIT"
TABLES = [
    ("tblfMRnvStZziWkPq", "IG Competitors"),
    ("tbl9jmrGp0DK1vJtt", "AI Competitors"),
]
MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "14"))
VIDEO_LIMIT = int(os.environ.get("VIDEO_LIMIT", "25"))
TIME_BUDGET_MIN = int(os.environ.get("TIME_BUDGET_MIN", "300"))  # stop gracefully before GH 6h cap
FAIL_MARK = "(转录失败)"
MAX_CHARS = 5000


def _request(url, method="GET", data=None, headers=None, timeout=60):
    hdrs = headers or {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_pending(pat, table_id):
    """Recently-synced video posts that have no transcript yet."""
    formula = urllib.parse.quote(
        f"AND({{Video URL}}!='', {{Transcript}}='', "
        f"DATETIME_DIFF(NOW(), {{Last Synced}}, 'days') <= {MAX_AGE_DAYS})"
    )
    base_url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table_id}"
        f"?filterByFormula={formula}&pageSize=100"
        f"&fields%5B%5D=Video%20URL&fields%5B%5D=Competitor&fields%5B%5D=Post%20ID"
    )
    headers = {"Authorization": f"Bearer {pat}"}
    out, offset = [], None
    while True:
        url = base_url + (f"&offset={urllib.parse.quote(offset)}" if offset else "")
        resp = _request(url, headers=headers, timeout=30)
        for rec in resp.get("records", []):
            f = rec.get("fields") or {}
            if f.get("Video URL"):
                out.append({
                    "rec": rec["id"],
                    "url": f["Video URL"],
                    "who": f.get("Competitor", "?"),
                    "post": f.get("Post ID", "?"),
                })
        offset = resp.get("offset")
        if not offset:
            break
    return out


def save_transcript(pat, table_id, rec_id, text):
    _request(
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table_id}/{rec_id}",
        method="PATCH",
        data={"fields": {"Transcript": text[:MAX_CHARS]}, "typecast": True},
        headers={"Authorization": f"Bearer {pat}"},
        timeout=30,
    )


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def main():
    pat = (os.environ.get("AIRTABLE_PAT") or "").strip()
    if not pat:
        print("ERROR: AIRTABLE_PAT is not set.")
        sys.exit(1)

    # collect pending videos across both tables
    queue = []
    for table_id, label in TABLES:
        items = fetch_pending(pat, table_id)
        print(f"[{label}] {len(items)} videos pending transcription")
        for it in items:
            it["tbl"] = table_id
            queue.append(it)

    if not queue:
        print("Nothing to transcribe. Done.")
        return

    if len(queue) > VIDEO_LIMIT:
        print(f"Capping at {VIDEO_LIMIT} videos this run ({len(queue) - VIDEO_LIMIT} left for next run)")
        queue = queue[:VIDEO_LIMIT]

    import whisper
    print(f"[Init] Loading Whisper '{MODEL_NAME}' model...")
    model = whisper.load_model(MODEL_NAME)
    print("[Init] Model ready.\n")

    import time as _time
    started = _time.time()
    ok = failed = 0
    for i, it in enumerate(queue, 1):
        if (_time.time() - started) / 60 > TIME_BUDGET_MIN:
            print(f"\nTime budget ({TIME_BUDGET_MIN}min) reached — {len(queue) - i + 1} left for next run.")
            break
        print(f"[{i}/{len(queue)}] @{it['who']} {it['post']}")
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                tmp_path = f.name
            download(it["url"], tmp_path)
            result = model.transcribe(tmp_path, fp16=False)
            text = (result.get("text") or "").strip()
            if not text:
                text = "(无口播内容)"
            save_transcript(pat, it["tbl"], it["rec"], text)
            ok += 1
            print(f"  OK: {text[:70]}{'...' if len(text) > 70 else ''}")
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {exc}")
            try:
                save_transcript(pat, it["tbl"], it["rec"], FAIL_MARK)
            except Exception:
                pass
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    print(f"\nDone: {ok} transcribed, {failed} failed (marked, will not retry).")


if __name__ == "__main__":
    main()
