#!/usr/bin/env python3
"""
AI Viral-Post Breakdown
------------------------
Feeds each viral post (stats + caption + transcript) to an LLM and writes
a structured breakdown (Hook type / structure / why it went viral / how to
adapt it for the AI course-selling niche) into Airtable's "AI 拆解" field.

Runs weekly after transcription. Provider: GitHub Models (free, built-in
GITHUB_TOKEN with models:read) by default; uses Anthropic API instead when
ANTHROPIC_API_KEY is set.

Env: AIRTABLE_PAT, GITHUB_TOKEN (or ANTHROPIC_API_KEY)
     GH_MODEL (default openai/gpt-4o-mini), POST_LIMIT (default 60)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AIRTABLE_BASE = "appaGrhUHc8aHmGIT"
TABLES = [
    ("tblfMRnvStZziWkPq", "IG Competitors"),
    ("tbl9jmrGp0DK1vJtt", "AI Competitors"),
]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
GH_MODEL = os.environ.get("GH_MODEL", "openai/gpt-4o-mini")
POST_LIMIT = int(os.environ.get("POST_LIMIT", "60"))
FAIL_MARK = "(拆解失败)"

SYSTEM = (
    "你是爆款短视频拆解专家,服务一个做「AI 做课卖课」教育业务的团队(华人市场)。"
    "给你一条竞对爆款的资料,输出精炼拆解,严格按以下四行格式,每行不超过60字,不要任何其他文字:\n"
    "Hook类型: <如 反常识/痛点直击/悬念/数字承诺/身份共鸣/展示成果>\n"
    "结构: <A → B → C 形式概括>\n"
    "为什么火: <一句话>\n"
    "改编角度: <给AI做课卖课领域的具体改编建议,一句话>"
)


def _request(url, method="GET", data=None, headers=None, timeout=120):
    hdrs = headers or {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_pending(pat, table_id):
    """Posts with no breakdown yet that have enough material to analyze."""
    formula = urllib.parse.quote(
        "AND({AI 拆解}='', OR(LEN({Transcript})>30, LEN({Caption})>50))"
    )
    fields = "&".join("fields%5B%5D=" + urllib.parse.quote(f) for f in
                      ["Post ID", "Competitor", "Caption", "Transcript",
                       "Post Type", "Likes", "Comments", "Followers"])
    # Newest first: a post that can never be analysed (e.g. the model's content
    # filter rejects it) must not sit at the head of the queue forever and starve
    # every new post out of the per-run budget.
    base_url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table_id}"
        f"?filterByFormula={formula}&pageSize=100&{fields}"
        f"&sort%5B0%5D%5Bfield%5D=Post%20Date&sort%5B0%5D%5Bdirection%5D=desc"
    )
    headers = {"Authorization": f"Bearer {pat}"}
    out, offset = [], None
    while True:
        url = base_url + (f"&offset={urllib.parse.quote(offset)}" if offset else "")
        resp = _request(url, headers=headers, timeout=30)
        for rec in resp.get("records", []):
            f = rec.get("fields") or {}
            out.append({"rec": rec["id"], "f": f})
        offset = resp.get("offset")
        if not offset:
            break
    return out


def _build_prompt(f):
    transcript = f.get("Transcript") or ""
    if transcript in ("(转录失败)", "(视频不可用)", "(无口播内容)"):
        transcript = ""
    return (
        f"账号: @{f.get('Competitor', '?')}(粉丝 {f.get('Followers', '?')})\n"
        f"类型: {f.get('Post Type', '?')} | 赞 {f.get('Likes', '?')} | 评论 {f.get('Comments', '?')}\n"
        f"文案(caption):\n{(f.get('Caption') or '')[:1200]}\n"
        + (f"\n口播稿:\n{transcript[:2500]}" if transcript else "")
    )


def analyze_anthropic(api_key, f):
    resp = _request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        data={
            "model": MODEL,
            "max_tokens": 400,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": _build_prompt(f)}],
        },
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=90,
    )
    parts = resp.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def analyze_github(token, f):
    """Free path: GitHub Models chat/completions with the Actions token."""
    resp = _request(
        "https://models.github.ai/inference/chat/completions",
        method="POST",
        data={
            "model": GH_MODEL,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": _build_prompt(f)},
            ],
        },
        headers={"Authorization": "Bearer " + token},
        timeout=90,
    )
    choices = resp.get("choices") or []
    return (choices[0].get("message", {}).get("content") or "").strip() if choices else ""


def save(pat, table_id, rec_id, text):
    _request(
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table_id}/{rec_id}",
        method="PATCH",
        data={"fields": {"AI 拆解": text[:1500]}, "typecast": True},
        headers={"Authorization": f"Bearer {pat}"},
        timeout=30,
    )


def main():
    pat = (os.environ.get("AIRTABLE_PAT") or os.environ.get("AIRTABLE_API_KEY") or "").strip()
    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    gh_token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not pat:
        print("ERROR: AIRTABLE_PAT is not set.")
        sys.exit(1)
    if anthropic_key:
        provider, delay = "anthropic", 0.5
        call = lambda f: analyze_anthropic(anthropic_key, f)
    elif gh_token:
        provider, delay = "github-models (" + GH_MODEL + ")", 4.5  # free tier: 15 req/min
        call = lambda f: analyze_github(gh_token, f)
    else:
        print("No ANTHROPIC_API_KEY or GITHUB_TOKEN — skipping AI breakdown (non-fatal).")
        return
    print(f"[Provider] {provider}")

    queue = []
    for table_id, label in TABLES:
        items = fetch_pending(pat, table_id)
        print(f"[{label}] {len(items)} posts pending breakdown")
        for it in items:
            it["tbl"] = table_id
            queue.append(it)

    if not queue:
        print("Nothing to analyze. Done.")
        return
    if len(queue) > POST_LIMIT:
        print(f"Capping at {POST_LIMIT} this run ({len(queue) - POST_LIMIT} left for next)")
        queue = queue[:POST_LIMIT]

    ok = failed = 0
    for i, it in enumerate(queue, 1):
        who = it["f"].get("Competitor", "?")
        try:
            text = call(it["f"])
            if not text:
                raise ValueError("empty response")
            save(pat, it["tbl"], it["rec"], text)
            ok += 1
            print(f"[{i}/{len(queue)}] @{who} OK: {text[:50]}...")
        except urllib.error.HTTPError as exc:
            failed += 1
            if exc.code == 400:
                # The model refused this content — it will refuse it every week.
                # Mark it so the queue moves on instead of re-serving it forever.
                try:
                    save(pat, it["tbl"], it["rec"], FAIL_MARK)
                except Exception:
                    pass
                print(f"[{i}/{len(queue)}] @{who} REJECTED (marked): HTTP 400")
            else:
                print(f"[{i}/{len(queue)}] @{who} FAILED: HTTP {exc.code} (retry next run)")
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(queue)}] @{who} FAILED: {exc} (retry next run)")
        time.sleep(delay)

    print(f"\nDone: {ok} analyzed, {failed} failed (will retry next run).")


if __name__ == "__main__":
    main()
