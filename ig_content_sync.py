#!/usr/bin/env python3
"""
IG Content Sync Pipeline — Free Version
----------------------------------------
Instaloader (scrape) → Whisper (transcribe) → Supabase (store)
No Apify, no Airtable, no paid services.
"""

import os
import sys
import time
import tempfile
import urllib.request
from datetime import datetime, timezone, timedelta

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

MIN_ER = 0.03          # 3% engagement rate
MIN_COMMENTS = 50      # minimum comments to qualify as viral
DAYS_LOOKBACK = 30     # only scrape posts from last 30 days
MAX_POSTS_PER_PROFILE = 50
WHISPER_MODEL = "tiny" # tiny=39MB fast; use "base" for better accuracy
PROFILE_DELAY = 8      # seconds between profiles (avoid IG rate limit)
POST_DELAY = 1         # seconds between posts

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_video(video_url, model):
    """Download video to temp file and transcribe with local Whisper."""
    if not video_url:
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            tmp_path = f.name

        print("    Downloading video for transcription...")
        urllib.request.urlretrieve(video_url, tmp_path)

        print("    Transcribing with Whisper...")
        result = model.transcribe(tmp_path, fp16=False)
        transcript = result.get("text", "").strip()
        print(f"    Done: {transcript[:80]}{'...' if len(transcript) > 80 else ''}")
        return transcript or None

    except Exception as exc:
        print(f"    Transcription failed: {exc}")
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not supabase_url:
        print("ERROR: SUPABASE_URL is not set.")
        sys.exit(1)
    if not supabase_key:
        print("ERROR: SUPABASE_SERVICE_ROLE_KEY is not set.")
        sys.exit(1)

    import instaloader
    import whisper
    from supabase import create_client

    # Connect to Supabase
    supabase = create_client(supabase_url, supabase_key)

    # Load Whisper model once
    print(f"[Init] Loading Whisper '{WHISPER_MODEL}' model...")
    model = whisper.load_model(WHISPER_MODEL)
    print("[Init] Model ready.")

    # Setup Instaloader (no login — public profiles only)
    L = instaloader.Instaloader(
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        request_timeout=30,
    )

    # Fetch existing post IDs to avoid duplicates
    print("\n[Step 1] Fetching existing post IDs from Supabase...")
    existing_ids = set()
    try:
        resp = supabase.table("ig_viral_posts").select("post_id").execute()
        for row in resp.data:
            existing_ids.add(row["post_id"])
        print(f"  Found {len(existing_ids)} existing posts")
    except Exception as exc:
        print(f"FATAL: Could not fetch Supabase records: {exc}")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_LOOKBACK)
    today = datetime.now(timezone.utc).date().isoformat()

    total_new = 0
    stats = {}

    # Scrape each competitor
    for username in IG_USERNAMES:
        print(f"\n[Scraping] @{username}")
        stats[username] = {"checked": 0, "viral": 0, "added": 0, "errors": 0}

        try:
            profile = instaloader.Profile.from_username(L.context, username)
            followers = profile.followers
            print(f"  Followers: {followers:,}")

            if followers == 0:
                print("  Skipping (followers = 0)")
                continue

        except Exception as exc:
            print(f"  ERROR loading profile: {exc}")
            stats[username]["errors"] += 1
            time.sleep(PROFILE_DELAY)
            continue

        try:
            for post in profile.get_posts():
                if stats[username]["checked"] >= MAX_POSTS_PER_PROFILE:
                    break

                post_date = post.date_utc.replace(tzinfo=timezone.utc)
                if post_date < cutoff:
                    break

                stats[username]["checked"] += 1
                post_id = post.shortcode

                if post_id in existing_ids:
                    time.sleep(POST_DELAY)
                    continue

                # Engagement filter
                likes = post.likes if post.likes and post.likes > 0 else 0
                comments = post.comments or 0
                er = (likes + comments) / followers

                if er < MIN_ER or comments < MIN_COMMENTS:
                    time.sleep(POST_DELAY)
                    continue

                stats[username]["viral"] += 1

                # Post type
                if post.is_video:
                    post_type = "Video"
                elif post.typename == "GraphSidecar":
                    post_type = "Carousel"
                else:
                    post_type = "Image"

                # Thumbnail (post.url = image or video thumbnail)
                thumbnail_url = post.url

                # Video URL and transcript
                video_url = post.video_url if post.is_video else None
                transcript = None
                if post.is_video and video_url:
                    transcript = transcribe_video(video_url, model)

                # Hashtags
                hashtags = " ".join(post.caption_hashtags) if post.caption_hashtags else None

                record = {
                    "post_id": post_id,
                    "competitor": username,
                    "caption": post.caption or "",
                    "transcript": transcript,
                    "post_type": post_type,
                    "likes": likes,
                    "comments": comments,
                    "engagement_rate": round(er * 100, 2),
                    "followers": followers,
                    "post_date": post.date_utc.date().isoformat(),
                    "post_url": f"https://www.instagram.com/p/{post_id}/",
                    "thumbnail_url": thumbnail_url,
                    "video_url": video_url,
                    "hashtags": hashtags,
                    "is_video": post.is_video,
                    "last_synced": today,
                }

                try:
                    supabase.table("ig_viral_posts").insert(record).execute()
                    existing_ids.add(post_id)
                    stats[username]["added"] += 1
                    total_new += 1
                    print(f"  + {post_id} | ER={er*100:.1f}% | {comments}💬 | {post_type}")
                except Exception as exc:
                    print(f"  ERROR inserting {post_id}: {exc}")
                    stats[username]["errors"] += 1

                time.sleep(POST_DELAY)

        except Exception as exc:
            print(f"  ERROR scraping posts: {exc}")
            stats[username]["errors"] += 1

        time.sleep(PROFILE_DELAY)

    # Summary
    print("\n" + "=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)
    for uname, s in stats.items():
        print(f"  @{uname}: checked={s['checked']} viral={s['viral']} added={s['added']} errors={s['errors']}")
    print(f"\n  TOTAL NEW: {total_new}")
    print("=" * 60)


if __name__ == "__main__":
    main()
