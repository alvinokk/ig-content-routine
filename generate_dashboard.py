#!/usr/bin/env python3
"""
Viral Posts Dashboard Generator
--------------------------------
Pulls both Airtable tables (Viral Posts + AI Post Chalette) and bakes a
static dashboard into docs/index.html for GitHub Pages.

Videos play inline via Instagram's official /embed/ iframe, so nothing
expires and no tokens are exposed in the page.

Token: AIRTABLE_PAT (environment variable).
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

AIRTABLE_BASE = "appaGrhUHc8aHmGIT"
TABLES = [
    {"id": "tblfMRnvStZziWkPq", "label": "IG Competitors"},
    {"id": "tbl9jmrGp0DK1vJtt", "label": "AI Competitors"},
]

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")


def fetch_table(pat, table_id):
    """Fetch all records from one Airtable table."""
    records = []
    offset = None
    headers = {"Authorization": f"Bearer {pat}"}
    while True:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table_id}?pageSize=100"
        if offset:
            url += f"&offset={urllib.parse.quote(offset)}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return records


def normalize(rec, tracker):
    f = rec.get("fields", {})
    post_id = str(f.get("Post ID") or "").strip()
    if not post_id:
        return None
    er = f.get("Engagement Rate") or 0
    return {
        "id": post_id,
        "tracker": tracker,
        "competitor": f.get("Competitor") or "",
        "caption": (f.get("Caption") or "")[:600],
        "type": f.get("Post Type") or "",
        "likes": f.get("Likes") if isinstance(f.get("Likes"), (int, float)) else 0,
        "comments": f.get("Comments") or 0,
        "er": round(er * 100, 2),
        "followers": f.get("Followers") or 0,
        "date": f.get("Post Date") or "",
        "url": f.get("Post URL") or f"https://www.instagram.com/p/{post_id}/",
        "video": bool(f.get("Is Video")),
        "ratio": f.get("Comments-to-Likes Ratio") or 0,
        "score": f.get("Viral Score") or 0,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Viral Posts Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --card: #161b22; --border: #21262d;
    --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --orange: #d29922; --pink: #f778ba;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
  header { position: sticky; top: 0; z-index: 50; background: rgba(13,17,23,.95);
    backdrop-filter: blur(8px); border-bottom: 1px solid var(--border); padding: 14px 20px; }
  .hrow { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; max-width: 1400px; margin: 0 auto; }
  h1 { font-size: 17px; margin-right: auto; white-space: nowrap; }
  h1 small { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 8px; }
  select, .toggle { background: var(--card); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 7px 10px; font-size: 13px; cursor: pointer; }
  .toggle.on { border-color: var(--accent); color: var(--accent); }
  .stats { display: flex; gap: 8px; flex-wrap: wrap; max-width: 1400px; margin: 10px auto 0; }
  .chip { background: var(--card); border: 1px solid var(--border); border-radius: 999px;
    padding: 4px 12px; font-size: 12px; color: var(--muted); }
  .chip b { color: var(--text); }
  main { max-width: 1400px; margin: 20px auto; padding: 0 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 18px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    overflow: hidden; display: flex; flex-direction: column; }
  .embed { position: relative; width: 100%; aspect-ratio: 4/5; background: #010409; }
  .embed iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
  .embed .ph { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 8px;
    align-items: center; justify-content: center; color: var(--muted); font-size: 13px; }
  .meta { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
  .row1 { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .who { font-weight: 600; color: var(--accent); text-decoration: none; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
  .date { margin-left: auto; color: var(--muted); font-size: 11px; }
  .nums { display: flex; gap: 6px; flex-wrap: wrap; }
  .num { background: #0d1117; border: 1px solid var(--border); border-radius: 8px;
    padding: 5px 9px; font-size: 11.5px; color: var(--muted); }
  .num b { color: var(--text); font-size: 12.5px; }
  .num.hot b { color: var(--pink); }
  .num.er b { color: var(--green); }
  .cap { color: var(--muted); font-size: 12.5px; line-height: 1.5; display: -webkit-box;
    -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; cursor: pointer; }
  .cap.open { -webkit-line-clamp: unset; }
  .more { text-align: center; margin: 26px 0 40px; }
  .more button { background: var(--card); color: var(--accent); border: 1px solid var(--border);
    border-radius: 10px; padding: 10px 26px; font-size: 14px; cursor: pointer; }
  .empty { color: var(--muted); text-align: center; padding: 60px 0; }
  footer { color: var(--muted); font-size: 11px; text-align: center; padding: 20px; }
</style>
</head>
<body>
<header>
  <div class="hrow">
    <h1>🔥 Viral Posts Dashboard<small id="updated"></small></h1>
    <select id="fTracker"><option value="">全部 Tracker</option></select>
    <select id="fComp"><option value="">全部 Competitor</option></select>
    <select id="fSort">
      <option value="score">按 Viral Score</option>
      <option value="er">按 Engagement Rate</option>
      <option value="comments">按 Comments</option>
      <option value="likes">按 Likes</option>
      <option value="date">按 最新日期</option>
    </select>
    <button class="toggle" id="fVideo">🎬 只看视频</button>
  </div>
  <div class="stats" id="stats"></div>
</header>
<main>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">没有符合条件的帖子</div>
  <div class="more" id="moreWrap" style="display:none"><button id="moreBtn">载入更多</button></div>
</main>
<footer>数据来源 Airtable · 每周一自动更新 · 视频由 Instagram 官方 embed 播放</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
document.getElementById('updated').textContent = '更新于 __UPDATED__';
const PAGE = 48;
let shown = PAGE;
let videoOnly = false;

const $ = id => document.getElementById(id);
const fmt = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n);

// build competitor options
const comps = [...new Set(DATA.map(d => d.competitor))].sort();
comps.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = '@'+c; $('fComp').appendChild(o); });
const trackers = [...new Set(DATA.map(d => d.tracker))];
trackers.forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; $('fTracker').appendChild(o); });

function filtered() {
  const t = $('fTracker').value, c = $('fComp').value, s = $('fSort').value;
  let arr = DATA.filter(d => (!t || d.tracker === t) && (!c || d.competitor === c) && (!videoOnly || d.video));
  const key = { score:'score', er:'er', comments:'comments', likes:'likes' }[s];
  if (s === 'date') arr.sort((a,b) => (b.date||'').localeCompare(a.date||''));
  else arr.sort((a,b) => (b[key]||0) - (a[key]||0));
  return arr;
}

const io = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      const el = e.target, pid = el.dataset.pid;
      if (!el.querySelector('iframe')) {
        const f = document.createElement('iframe');
        f.src = 'https://www.instagram.com/p/' + pid + '/embed/';
        f.loading = 'lazy'; f.allow = 'autoplay; encrypted-media';
        el.appendChild(f);
      }
      io.unobserve(el);
    }
  });
}, { rootMargin: '400px' });

function card(d) {
  const el = document.createElement('div'); el.className = 'card';
  const emoji = d.video ? '🎬' : (d.type === 'Carousel' ? '🖼️' : '📷');
  el.innerHTML = `
    <div class="embed" data-pid="${d.id}"><div class="ph">${emoji} 载入中…</div></div>
    <div class="meta">
      <div class="row1">
        <a class="who" href="https://www.instagram.com/${d.competitor}/" target="_blank">@${d.competitor}</a>
        <span class="badge">${d.type || '-'}</span>
        <span class="badge">${d.tracker}</span>
        <span class="date">${d.date || ''}</span>
      </div>
      <div class="nums">
        <span class="num hot">🔥 <b>${d.score}</b></span>
        <span class="num er">ER <b>${d.er}%</b></span>
        <span class="num">❤️ <b>${d.likes > 0 ? fmt(d.likes) : '隐藏'}</b></span>
        <span class="num">💬 <b>${fmt(d.comments)}</b></span>
        <span class="num">👥 <b>${fmt(d.followers)}</b></span>
      </div>
      <div class="cap" onclick="this.classList.toggle('open')"></div>
    </div>`;
  el.querySelector('.cap').textContent = d.caption || '';
  io.observe(el.querySelector('.embed'));
  return el;
}

function render() {
  const arr = filtered();
  const grid = $('grid'); grid.innerHTML = '';
  arr.slice(0, shown).forEach(d => grid.appendChild(card(d)));
  $('empty').style.display = arr.length ? 'none' : '';
  $('moreWrap').style.display = arr.length > shown ? '' : 'none';
  const vids = arr.filter(d => d.video).length;
  $('stats').innerHTML =
    `<span class="chip">帖子 <b>${arr.length}</b></span>` +
    `<span class="chip">视频 <b>${vids}</b></span>` +
    `<span class="chip">账号 <b>${new Set(arr.map(d=>d.competitor)).size}</b></span>`;
}

['fTracker','fComp','fSort'].forEach(id => $(id).onchange = () => { shown = PAGE; render(); });
$('fVideo').onclick = () => { videoOnly = !videoOnly; $('fVideo').classList.toggle('on', videoOnly); shown = PAGE; render(); };
$('moreBtn').onclick = () => { shown += PAGE; render(); };
render();
</script>
</body>
</html>
"""


def main():
    pat = (os.environ.get("AIRTABLE_PAT") or os.environ.get("AIRTABLE_API_KEY") or "").strip()
    if not pat:
        print("ERROR: AIRTABLE_PAT is not set.")
        sys.exit(1)

    posts = []
    for t in TABLES:
        print(f"Fetching {t['label']} ({t['id']})...")
        recs = fetch_table(pat, t["id"])
        n = 0
        for rec in recs:
            row = normalize(rec, t["label"])
            if row:
                posts.append(row)
                n += 1
        print(f"  {n} posts")

    # de-dup by post id (keep first)
    seen, unique = set(), []
    for p in posts:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = json.dumps(unique, ensure_ascii=False).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__DATA__", payload).replace("__UPDATED__", updated)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nWrote {OUT_PATH} with {len(unique)} posts.")


if __name__ == "__main__":
    main()
