#!/usr/bin/env python3
"""
Viral Posts Dashboard Generator
--------------------------------
Pulls both Airtable tables (Viral Posts + AI Post Chalette) and bakes a
static dashboard into docs/index.html for GitHub Pages.

- Videos play inline via Instagram's official /embed/ iframe (never expires).
- Status tabs (未处理/拍摄中/已处理/跳过) write back to Airtable directly
  from the browser, using a team key the viewer enters once (stored in
  localStorage — never embedded in this page or repo).

Token: AIRTABLE_PAT (environment variable, build-time read only).
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
STATUSES = ["未处理", "拍摄中", "已处理", "跳过"]

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


def normalize(rec, table):
    f = rec.get("fields", {})
    post_id = str(f.get("Post ID") or "").strip()
    if not post_id:
        return None
    er = f.get("Engagement Rate") or 0
    return {
        "id": post_id,
        "rec": rec.get("id"),
        "tbl": table["id"],
        "tracker": table["label"],
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
        "status": f.get("Status") or "未处理",
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
    --green: #3fb950; --orange: #d29922; --pink: #f778ba; --red: #f85149;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
  header { position: sticky; top: 0; z-index: 50; background: rgba(13,17,23,.95);
    backdrop-filter: blur(8px); border-bottom: 1px solid var(--border); padding: 12px 20px; }
  .hrow { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; max-width: 1400px; margin: 0 auto; }
  h1 { font-size: 17px; margin-right: auto; white-space: nowrap; }
  h1 small { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 8px; }
  select, .btn { background: var(--card); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 7px 10px; font-size: 13px; cursor: pointer; }
  .btn.on { border-color: var(--accent); color: var(--accent); }
  .tabs { display: flex; gap: 6px; flex-wrap: wrap; max-width: 1400px; margin: 10px auto 0; }
  .tab { background: var(--card); border: 1px solid var(--border); border-radius: 999px;
    padding: 6px 14px; font-size: 13px; color: var(--muted); cursor: pointer; user-select: none; }
  .tab.on { border-color: var(--accent); color: var(--accent); background: rgba(88,166,255,.08); }
  .tab b { font-weight: 600; }
  body.dragging .tab.droppable { border-style: dashed; border-color: var(--accent); }
  .tab.over { background: rgba(88,166,255,.25); color: var(--text); transform: scale(1.08); }
  .card[draggable] { cursor: grab; }
  .card.lifting { opacity: .45; }
  .handle { color: var(--muted); font-size: 14px; cursor: grab; user-select: none; }
  main { max-width: 1400px; margin: 20px auto; padding: 0 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 18px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    overflow: hidden; display: flex; flex-direction: column; transition: border-color .3s; }
  .card.saved { border-color: var(--green); }
  .embed { position: relative; width: 100%; aspect-ratio: 4/5; background: #010409; }
  .embed iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
  .embed .ph { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 8px;
    align-items: center; justify-content: center; color: var(--muted); font-size: 13px; }
  .meta { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
  .row1 { display: flex; align-items: center; gap: 8px; font-size: 13px; flex-wrap: wrap; }
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
  .srow { display: flex; align-items: center; gap: 8px; }
  .srow label { font-size: 12px; color: var(--muted); }
  .status { flex: 1; }
  .status.s未处理 { color: var(--muted); }
  .status.s拍摄中 { color: var(--orange); border-color: var(--orange); }
  .status.s已处理 { color: var(--green); border-color: var(--green); }
  .status.s跳过 { color: var(--red); border-color: var(--red); }
  .more { text-align: center; margin: 26px 0 40px; }
  .more button { background: var(--card); color: var(--accent); border: 1px solid var(--border);
    border-radius: 10px; padding: 10px 26px; font-size: 14px; cursor: pointer; }
  .empty { color: var(--muted); text-align: center; padding: 60px 0; }
  footer { color: var(--muted); font-size: 11px; text-align: center; padding: 20px; }
  #toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 18px; font-size: 13px; display: none; z-index: 99; }
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
    <button class="btn" id="fVideo">🎬 只看视频</button>
    <button class="btn" id="keyBtn" title="输入团队密钥后才能更新状态">🔑</button>
  </div>
  <div class="tabs" id="tabs"></div>
</header>
<main>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">没有符合条件的帖子</div>
  <div class="more" id="moreWrap" style="display:none"><button id="moreBtn">载入更多</button></div>
</main>
<footer>数据来源 Airtable · 每周一自动更新 · 状态更改即时同步全团队 · 视频由 Instagram 官方 embed 播放</footer>
<div id="toast"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const BASE = '__BASE__';
const TABLE_IDS = __TABLE_IDS__;
const STATUSES = __STATUSES__;
document.getElementById('updated').textContent = '更新于 __UPDATED__';
const PAGE = 48;
let shown = PAGE;
let videoOnly = false;
let curTab = '全部';

const $ = id => document.getElementById(id);
const fmt = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n);
const getKey = () => localStorage.getItem('team_key') || '';
const byRec = {}; DATA.forEach(d => byRec[d.rec] = d);

function toast(msg, ok) {
  const t = $('toast'); t.textContent = msg; t.style.display = 'block';
  t.style.borderColor = ok ? 'var(--green)' : 'var(--red)';
  clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', 2600);
}

// filters
const comps = [...new Set(DATA.map(d => d.competitor))].sort();
comps.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = '@'+c; $('fComp').appendChild(o); });
[...new Set(DATA.map(d => d.tracker))].forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; $('fTracker').appendChild(o); });

function filtered() {
  const t = $('fTracker').value, c = $('fComp').value, s = $('fSort').value;
  let arr = DATA.filter(d => (!t || d.tracker === t) && (!c || d.competitor === c) && (!videoOnly || d.video)
    && (curTab === '全部' || d.status === curTab));
  const key = { score:'score', er:'er', comments:'comments', likes:'likes' }[s];
  if (s === 'date') arr.sort((a,b) => (b.date||'').localeCompare(a.date||''));
  else arr.sort((a,b) => (b[key]||0) - (a[key]||0));
  return arr;
}

function renderTabs() {
  const t = $('fTracker').value, c = $('fComp').value;
  const pool = DATA.filter(d => (!t || d.tracker === t) && (!c || d.competitor === c) && (!videoOnly || d.video));
  const counts = { '全部': pool.length };
  STATUSES.forEach(s => counts[s] = pool.filter(d => d.status === s).length);
  $('tabs').innerHTML = '';
  ['全部', ...STATUSES].forEach(name => {
    const el = document.createElement('div');
    el.className = 'tab' + (curTab === name ? ' on' : '') + (name !== '全部' ? ' droppable' : '');
    el.innerHTML = `${name} <b>${counts[name]}</b>`;
    el.onclick = () => { curTab = name; shown = PAGE; render(); };
    if (name !== '全部') {
      el.ondragover = e => { if (dragRec) { e.preventDefault(); el.classList.add('over'); } };
      el.ondragleave = () => el.classList.remove('over');
      el.ondrop = e => {
        e.preventDefault(); el.classList.remove('over');
        const d = byRec[dragRec]; dragRec = null;
        if (d && d.status !== name) setStatus(d, name);
      };
    }
    $('tabs').appendChild(el);
  });
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

let dragRec = null;

async function setStatus(d, val) {
  let key = getKey();
  if (!key) { promptKey(); key = getKey(); if (!key) { render(); return; } }
  try {
    const r = await fetch(`https://api.airtable.com/v0/${BASE}/${d.tbl}/${d.rec}`, {
      method: 'PATCH',
      headers: { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields: { 'Status': val }, typecast: true })
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d.status = val;
    toast('✓ @' + d.competitor + ' 已移到「' + val + '」', true);
  } catch (e) {
    toast('更新失败: ' + e.message + (String(e.message).includes('401') || String(e.message).includes('403') ? ' — 密钥无效?' : ''), false);
  }
  render();
}

function card(d) {
  const el = document.createElement('div'); el.className = 'card';
  el.draggable = true;
  el.ondragstart = e => {
    dragRec = d.rec;
    e.dataTransfer.effectAllowed = 'move';
    el.classList.add('lifting');
    document.body.classList.add('dragging');
  };
  el.ondragend = () => {
    el.classList.remove('lifting');
    document.body.classList.remove('dragging');
    dragRec = null;
  };
  const emoji = d.video ? '🎬' : (d.type === 'Carousel' ? '🖼️' : '📷');
  el.innerHTML = `
    <div class="embed" data-pid="${d.id}"><div class="ph">${emoji} 载入中…</div></div>
    <div class="meta">
      <div class="row1">
        <span class="handle" title="拖到上方 tab 即可归类">⠿</span>
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
      <div class="srow"><label>状态</label><select class="status btn s${d.status}"></select></div>
    </div>`;
  el.querySelector('.cap').textContent = d.caption || '';
  const sel = el.querySelector('.status');
  STATUSES.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });
  sel.value = d.status;
  sel.onchange = () => setStatus(d, sel.value);
  io.observe(el.querySelector('.embed'));
  return el;
}

function render() {
  renderTabs();
  const arr = filtered();
  const grid = $('grid'); grid.innerHTML = '';
  arr.slice(0, shown).forEach(d => grid.appendChild(card(d)));
  $('empty').style.display = arr.length ? 'none' : '';
  $('moreWrap').style.display = arr.length > shown ? '' : 'none';
}

function promptKey() {
  const cur = getKey();
  const v = prompt('输入团队密钥(用于更新状态,只需输入一次):', cur);
  if (v !== null) {
    if (v.trim()) { localStorage.setItem('team_key', v.trim()); toast('✓ 密钥已保存', true); refreshStatuses(); }
    else { localStorage.removeItem('team_key'); toast('密钥已清除', true); }
    $('keyBtn').classList.toggle('on', !!getKey());
  }
}
$('keyBtn').onclick = promptKey;
$('keyBtn').classList.toggle('on', !!getKey());

// live status refresh (so weekly-baked page shows current statuses)
async function refreshStatuses() {
  const key = getKey(); if (!key) return;
  try {
    for (const tbl of TABLE_IDS) {
      let offset = '';
      do {
        const u = `https://api.airtable.com/v0/${BASE}/${tbl}?pageSize=100&fields%5B%5D=Status` + (offset ? `&offset=${encodeURIComponent(offset)}` : '');
        const r = await fetch(u, { headers: { 'Authorization': 'Bearer ' + key } });
        if (!r.ok) return;
        const j = await r.json();
        (j.records || []).forEach(rec => {
          const d = byRec[rec.id];
          if (d) d.status = (rec.fields && rec.fields['Status']) || '未处理';
        });
        offset = j.offset || '';
      } while (offset);
    }
    render();
  } catch (e) { /* offline etc — keep baked statuses */ }
}

['fTracker','fComp','fSort'].forEach(id => $(id).onchange = () => { shown = PAGE; render(); });
$('fVideo').onclick = () => { videoOnly = !videoOnly; $('fVideo').classList.toggle('on', videoOnly); shown = PAGE; render(); };
$('moreBtn').onclick = () => { shown += PAGE; render(); };
render();
refreshStatuses();
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
            row = normalize(rec, t)
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
    html = (
        HTML_TEMPLATE
        .replace("__DATA__", payload)
        .replace("__UPDATED__", updated)
        .replace("__BASE__", AIRTABLE_BASE)
        .replace("__TABLE_IDS__", json.dumps([t["id"] for t in TABLES]))
        .replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))
    )

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nWrote {OUT_PATH} with {len(unique)} posts.")


if __name__ == "__main__":
    main()
