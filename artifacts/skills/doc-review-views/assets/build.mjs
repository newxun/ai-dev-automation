#!/usr/bin/env node
/**
 * doc-review-views — 把人工核对类 markdown 文档批量渲染为自包含 HTML 审阅视图，
 * 并生成目录页 docs/review-views/index.html。
 * md 是唯一源：本脚本只读 md、只写派生物（目录页 + 各视图，均在 docs/review-views/），绝不回写 md。
 *
 * 用法: node build.mjs <project-root>
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ASSETS = path.dirname(fileURLToPath(import.meta.url));
const TEMPLATE = fs.readFileSync(path.join(ASSETS, 'template.html'), 'utf8');

/* ---------- 文档族（扫描白名单；只取各目录直接子文件） ---------- */
const FAMILIES = [
  { dir: 'docs/requirement-consistency-check', pattern: /\.md$/i, type: '一致性核对', brand: '一致性核对报告', level: 'project', chip: 'info' },
  { dir: 'docs/development-readiness', pattern: /-baseline\.md$/i, type: '就绪基线', brand: '就绪基线', level: 'project', chip: 'ok' },
  { dir: 'docs/development-readiness/tasks', pattern: /\.md$/i, type: '任务就绪报告', brand: '任务就绪报告', level: 'task', chip: 'user' },
  { dir: 'docs/scenario-acceptance', pattern: /\.md$/i, type: '场景验收', brand: '场景验收', level: 'project', chip: 'warn' },
  { dir: 'docs/scenario-acceptance/tasks', pattern: /\.md$/i, type: '场景验收报告', brand: '场景验收报告', level: 'task', chip: 'warn' },
];

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ---------- 扫描 ---------- */
function scan(root) {
  const out = [];
  for (const f of FAMILIES) {
    const dir = path.join(root, f.dir);
    if (!fs.existsSync(dir)) continue;
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!e.isFile() || !e.name.endsWith('.md')) continue;
      if (e.name.toLowerCase() === 'readme.md') continue;
      if (!f.pattern.test(e.name)) continue;
      out.push({
        abs: path.join(dir, e.name),
        rel: f.dir + '/' + e.name,          // 相对 docs/ 的路径（用作门户 href）
        base: e.name,
        type: f.type, brand: f.brand, level: f.level, chip: f.chip,
      });
    }
  }
  return out;
}

/* ---------- 计数：与阅读视图模板 JS 的 pill 计数同一套规则 ---------- */
function sevOf(t) { if (/高优先/.test(t)) return 'high'; if (/中低/.test(t)) return 'mid'; if (/轻微/.test(t)) return 'low'; return null; }
function tagsOf(raw) { const out = []; const re = /`(\[[^\]]+\])`/g; let m; while ((m = re.exec(raw))) out.push(m[1].replace(/^\[|\]$/g, '')); return out; }
function hasConflict(tags) { return tags.some(t => /冲突/.test(t) && !/消解/.test(t)); }
/* 待决：标签或正文措辞命中即算（[留开发] 处置已定，不算）；规则须与模板 JS 完全一致，见 SKILL.md 计数口径 */
const PENDING_RE = /待确认|未确认|未验证|需要澄清|需澄清|待澄清/;
function plainText(s) { // 与模板 JS 的 plainText 同一套剥离规则，保证待决计数两侧一致
  return s.replace(/`([^`]+)`/g, '$1').replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\[|\]$/g, '');
}
function isPending(tags, raw) { return PENDING_RE.test(tags.join(',') + ' ' + plainText(raw).toLowerCase()); }

const CARD_RE = /^\*\*([A-Z]{1,3}\d+)[.、]?\s*(.+?)\*\*\s*(.*)$/;

function parseBlocks(md) {
  const lines = md.split(/\r?\n/); const blocks = []; let i = 0;
  const isListLine = l => /^(\s*)([-*]|\d+\.)\s+/.test(l);
  const isTableLine = l => /^\s*\|/.test(l);
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*$/.test(l)) { i++; continue; }
    const h = /^(#{1,6})\s+(.*)$/.exec(l);
    if (h) { blocks.push({ type: 'h', text: h[2] }); i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { i++; continue; }
    if (/^\s*>/.test(l)) { while (i < lines.length && /^\s*>/.test(lines[i])) i++; blocks.push({ type: 'quote' }); continue; }
    if (isTableLine(l)) {
      const rows = [];
      while (i < lines.length && isTableLine(lines[i])) {
        rows.push(lines[i].trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim()));
        i++;
      }
      blocks.push({ type: 'table', rows }); continue;
    }
    if (isListLine(l)) {
      const items = []; // 仅顶层条目（嵌套子项不算独立 unit，与模板一致）
      while (i < lines.length && (isListLine(lines[i]) || /^\s+\S/.test(lines[i]))) {
        const m = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(lines[i]);
        if (m) {
          if (Math.floor(m[1].length / 2) === 0) items.push(m[3]);
        } else if (items.length) {
          items[items.length - 1] += ' ' + lines[i].trim();
        }
        i++;
      }
      blocks.push({ type: 'list', items }); continue;
    }
    const p = [l.trim()]; i++;
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,6})\s/.test(lines[i])
      && !isListLine(lines[i]) && !isTableLine(lines[i]) && !/^\s*>/.test(lines[i])
      && !/^\s*(-{3,}|\*{3,})\s*$/.test(lines[i])) { p.push(lines[i].trim()); i++; }
    blocks.push({ type: 'p', text: p.join(' ') });
  }
  return blocks;
}

function analyze(md) {
  const blocks = parseBlocks(md);
  let curSev = null;
  const counts = { high: 0, mid: 0, low: 0, conflict: 0, pending: 0, total: 0 };
  const bump = (raw) => {
    if (curSev === 'high') counts.high++; else if (curSev === 'mid') counts.mid++; else counts.low++;
    const tags = tagsOf(raw);
    if (hasConflict(tags)) counts.conflict++;
    if (isPending(tags, raw)) counts.pending++;
    counts.total++;
  };
  blocks.forEach((b, bi) => {
    if (b.type === 'h') { curSev = sevOf(b.text); return; }
    if (!curSev) return;
    if (b.type === 'p') {
      // 问题卡片段落（后跟列表）整卡算 1 个 unit；列表体由卡片消费，不再逐条计
      const m = CARD_RE.exec(b.text);
      if (m && blocks[bi + 1] && blocks[bi + 1].type === 'list') bump(b.text);
      return;
    }
    if (b.type === 'list') {
      const prev = blocks[bi - 1];
      if (prev && prev.type === 'p' && CARD_RE.test(prev.text)) return; // 卡片体
      b.items.forEach(it => bump(it));
      return;
    }
    if (b.type === 'table') {
      if (b.rows[0] && b.rows[0].length === 2) return; // 2 列元信息表不算 unit（模板渲染为 meta 卡）
      const sep = /^[-: ]+$/.test(b.rows[1] ? b.rows[1].map(c => c || '').join('') : 'x');
      const body = sep ? b.rows.slice(2) : b.rows.slice(1);
      body.forEach(r => bump(r.join('|')));
      return;
    }
  });
  return counts;
}

/* ---------- 元数据 ---------- */
function metaOf(doc, md) {
  const titleM = /^#\s+(.+)$/m.exec(md);
  const dateM = /^(\d{4}-\d{2}-\d{2})T/.exec(doc.base);
  const stripped = doc.base.replace(/^\d{4}-\d{2}-\d{2}T[\d-]*Z-/, '');
  const workM = /([A-Za-z]{2,6}-\d{3,})/.exec(stripped);
  return {
    title: titleM ? titleM[1].trim() : doc.base.replace(/\.md$/i, ''),
    date: dateM ? dateM[1] : fs.statSync(doc.abs).mtime.toISOString().slice(0, 10),
    work: workM ? (workM[1].toUpperCase()) : null,
  };
}

/* ---------- 渲染单篇（所有视图集中输出到 docs/review-views/，与目录页同目录） ---------- */
function prepareDoc(doc, outDir, stem) {
  const md = fs.readFileSync(doc.abs, 'utf8');
  const meta = metaOf(doc, md);
  return {
    ...doc, ...meta, md,
    counts: analyze(md),
    htmlName: stem + '.html',                                   // 与门户同目录，href 直接用文件名
    mdBack: path.relative(outDir, doc.abs).replace(/\\/g, '/'), // 从视图指回源 md
  };
}

function renderDoc(d, doclist) {
  const json = JSON.stringify(d.md).replace(/<\//g, '<\\/').replace(/<!--/g, '<\\!--');
  const html = TEMPLATE
    .split('{{TITLE}}').join(escHtml(d.title))
    .split('{{BRAND}}').join(d.brand)
    .split('{{DOCLIST_HTML}}').join(doclist)
    .split('{{MARKDOWN_JSON}}').join(json);
  fs.writeFileSync(d.outAbs, html);
}

/* ---------- 侧栏「切换文档」列表：与门户同一套分组，当前篇高亮 ---------- */
function doclistHTML(docs, currentName) {
  const item = d => d.htmlName === currentName
    ? '<span class="doc cur" title="' + escHtml(d.title) + '">' + escHtml(d.title) + '</span>'
    : '<a class="doc" href="' + encodeURI(d.htmlName) + '" title="' + escHtml(d.title) + '">' + escHtml(d.title) + '</a>';
  const group = (t, list) => '<div class="dg">' + escHtml(t) + '</div>' + list.map(item).join('');
  const desc = (a, b) => (b.date || '').localeCompare(a.date || '');
  const parts = [];
  const project = docs.filter(d => d.level === 'project').sort(desc);
  if (project.length) parts.push(group('项目级 · 迭代级', project));
  const byWork = new Map();
  docs.filter(d => d.level === 'task').forEach(d => {
    const k = d.work || '其他';
    if (!byWork.has(k)) byWork.set(k, []);
    byWork.get(k).push(d);
  });
  [...byWork.keys()].sort().forEach(k => parts.push(group(k, byWork.get(k).sort(desc))));
  return parts.join('');
}

/* ---------- 目录页 ---------- */
function countChips(c) {
  const parts = [];
  if (c.high) parts.push('<span class="c c-bad">高优先 ' + c.high + '</span>');
  if (c.mid) parts.push('<span class="c c-warn">中低 ' + c.mid + '</span>');
  if (c.low) parts.push('<span class="c c-dim">轻微 ' + c.low + '</span>');
  if (c.conflict) parts.push('<span class="c c-bad-o">证据冲突 ' + c.conflict + '</span>');
  if (c.pending) parts.push('<span class="c c-warn-o">待决 ' + c.pending + '</span>');
  return parts.join('');
}

function indexHTML(root, docs) {
  const project = docs.filter(d => d.level === 'project');
  const byWork = new Map();
  docs.filter(d => d.level === 'task').forEach(d => {
    const k = d.work || '其他';
    if (!byWork.has(k)) byWork.set(k, []);
    byWork.get(k).push(d);
  });
  const desc = (a, b) => (b.date || '').localeCompare(a.date || '');
  project.sort(desc);
  [...byWork.keys()].sort().forEach(k => byWork.get(k).sort(desc));

  const card = d => {
    const href = encodeURI(d.htmlName);
    const mdHref = encodeURI(d.mdBack);
    return '<div class="card">' +
      '<div class="card-top"><span class="chip chip-' + d.chip + '">' + d.type + '</span>' +
      '<span class="date">' + escHtml(d.date || '') + '</span></div>' +
      '<a class="card-title" href="' + href + '">' + escHtml(d.title) + '</a>' +
      '<div class="card-bot"><span class="cnts">' + countChips(d.counts) + '</span>' +
      '<a class="mdlink" href="' + mdHref + '">md 源文件</a></div>' +
      '</div>';
  };
  const group = (title, list) => {
    if (!list.length) return '';
    return '<section><h2>' + escHtml(title) + '<b>' + list.length + '</b></h2>' +
      '<div class="grid">' + list.map(card).join('') + '</div></section>';
  };
  let body = group('项目级 · 迭代级文档', project);
  [...byWork.keys()].sort().forEach(k => body += group(k, byWork.get(k)));

  const genTime = new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
  const projName = path.basename(path.resolve(root));
  return '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n' +
'<meta name="viewport" content="width=device-width, initial-scale=1">\n' +
'<title>人工核对文档 · 审阅目录 · ' + escHtml(projName) + '</title>\n' +
'<style>\n' +
':root{--bg:#f6f6f4;--panel:#fff;--panel2:#fbfbfa;--text:#2b2b28;--muted:#8b8b85;--faint:#b0b0aa;' +
'--line:#e7e7e2;--line2:#dedeD8;--accent:#41618c;--accent-soft:rgba(65,97,140,.08);' +
'--ok:#3d7a4f;--ok-bg:rgba(61,122,79,.07);--ok-line:rgba(61,122,79,.22);' +
'--warn:#96682c;--warn-bg:rgba(150,104,44,.07);--warn-line:rgba(150,104,44,.22);' +
'--bad:#a8493d;--bad-bg:rgba(168,73,61,.07);--bad-line:rgba(168,73,61,.22);' +
'--info:#4a6f96;--info-bg:rgba(74,111,150,.07);--info-line:rgba(74,111,150,.22);' +
'--user:#7a5f96;--user-bg:rgba(122,95,150,.07);--user-line:rgba(122,95,150,.22)}\n' +
'@media (prefers-color-scheme: dark){:root{--bg:#161719;--panel:#1e2023;--panel2:#1a1c1f;' +
'--text:#d8d7d2;--muted:#93938d;--faint:#6d6d68;--line:#2c2f33;--line2:#383b3f;' +
'--accent:#7fa3cc;--accent-soft:rgba(127,163,204,.12);' +
'--ok:#7db98d;--ok-bg:rgba(125,185,141,.09);--ok-line:rgba(125,185,141,.25);' +
'--warn:#d0a05e;--warn-bg:rgba(208,160,94,.09);--warn-line:rgba(208,160,94,.25);' +
'--bad:#d98276;--bad-bg:rgba(217,130,118,.09);--bad-line:rgba(217,130,118,.25);' +
'--info:#82a8d0;--info-bg:rgba(130,168,208,.09);--info-line:rgba(130,168,208,.25);' +
'--user:#ab92c8;--user-bg:rgba(171,146,200,.09);--user-line:rgba(171,146,200,.25)}}\n' +
'*{box-sizing:border-box}html,body{margin:0;padding:0}\n' +
'body{background:var(--bg);color:var(--text);font:14px/1.75 -apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;-webkit-font-smoothing:antialiased}\n' +
'main{max-width:920px;margin:0 auto;padding:36px 26px 80px}\n' +
'h1{font-size:21px;margin:0 0 6px}\n' +
'.sub{color:var(--muted);font-size:12.5px;margin-bottom:4px}\n' +
'.sub code{font-family:Consolas,monospace;font-size:11px;background:var(--accent-soft);border:1px solid var(--line);border-radius:4px;padding:0 5px}\n' +
'.regen{color:var(--faint);font-size:11.5px;margin-bottom:30px}\n' +
'section h2{font-size:15px;margin:30px 0 4px;padding-bottom:8px;border-bottom:1px solid var(--line)}\n' +
'section h2 b{font-weight:500;color:var(--faint);font-size:12px;margin-left:8px}\n' +
'.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-top:14px}\n' +
'.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 16px;display:flex;flex-direction:column;gap:7px}\n' +
'.card:hover{border-color:var(--accent)}\n' +
'.card-top{display:flex;align-items:center;justify-content:space-between;gap:8px}\n' +
'.date{color:var(--faint);font-size:11.5px;font-family:Consolas,monospace}\n' +
'.card-title{color:var(--text);text-decoration:none;font-weight:600;font-size:13.5px;line-height:1.5}\n' +
'.card-title:hover{color:var(--accent)}\n' +
'.card-bot{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:22px}\n' +
'.cnts{display:flex;gap:5px;flex-wrap:wrap}\n' +
'.c{font-size:10.5px;line-height:1.7;padding:0 7px;border-radius:99px;white-space:nowrap}\n' +
'.c-bad{color:var(--bad);background:var(--bad-bg);border:1px solid var(--bad-line)}\n' +
'.c-bad-o{color:var(--bad);border:1px dashed var(--bad-line)}\n' +
'.c-warn-o{color:var(--warn);border:1px dashed var(--warn-line)}\n' +
'.c-warn{color:var(--warn);background:var(--warn-bg);border:1px solid var(--warn-line)}\n' +
'.c-dim{color:var(--muted);border:1px solid var(--line2)}\n' +
'.mdlink{color:var(--faint);font-size:11px;text-decoration:none;flex:none}\n' +
'.mdlink:hover{color:var(--accent)}\n' +
'.chip{display:inline-block;font-size:11px;line-height:1.6;padding:0 7px;border-radius:99px;font-weight:500;white-space:nowrap}\n' +
'.chip-ok{color:var(--ok);background:var(--ok-bg);border:1px solid var(--ok-line)}\n' +
'.chip-warn{color:var(--warn);background:var(--warn-bg);border:1px solid var(--warn-line)}\n' +
'.chip-info{color:var(--info);background:var(--info-bg);border:1px solid var(--info-line)}\n' +
'.chip-user{color:var(--user);background:var(--user-bg);border:1px solid var(--user-line)}\n' +
'@media print{body{background:#fff}.grid{grid-template-columns:1fr 1fr}}\n' +
'</style>\n</head>\n<body>\n<main>\n' +
'<h1>人工核对文档 · 审阅目录</h1>\n' +
'<div class="sub">' + escHtml(projName) + ' · 生成于 ' + genTime + ' · 本目录收录全部审阅视图，入口内可用待决/筛选/搜索/折叠</div>\n' +
'<div class="regen">md 为唯一源；重跑 <code>doc-review-views</code> skill（node …/doc-review-views/assets/build.mjs ' + escHtml(projName) + ' 所在项目根）刷新本页与各视图</div>\n' +
body + '\n</main>\n</body>\n</html>\n';
}

/* ---------- main ---------- */
const rootArg = process.argv[2];
if (!rootArg) { console.error('用法: node build.mjs <project-root>'); process.exit(1); }
const root = path.resolve(rootArg);
if (!fs.existsSync(root)) { console.error('项目根不存在: ' + root); process.exit(1); }

const found = scan(root);
if (!found.length) {
  console.error('未在 ' + root + ' 下找到任何已知文档族（requirement-consistency-check / development-readiness / scenario-acceptance，规则见 SKILL.md）。');
  console.error('若有其他人工核对文档，先与用户确认路径，不要擅自放宽扫描。');
  process.exit(1);
}

// 所有派生物集中在一个目录：docs/review-views/（目录页 index.html + 各视图）
const outDir = path.join(root, 'docs', 'review-views');
fs.mkdirSync(outDir, { recursive: true });
// 该目录是本 skill 派生物专区：先清掉旧 *.html，避免源 md 删除/改名后残留过期视图
for (const e of fs.readdirSync(outDir)) {
  if (e.toLowerCase().endsWith('.html')) fs.unlinkSync(path.join(outDir, e));
}
const used = new Set();
const built = found.map(d => {
  let stem = d.base.replace(/\.md$/i, '');
  if (used.has(stem.toLowerCase())) stem = d.rel.slice(0, d.rel.lastIndexOf('/')).replace(/\//g, '-') + '-' + stem; // 同名消歧：冠以文档族目录
  used.add(stem.toLowerCase());
  const p = prepareDoc(d, outDir, stem);
  p.outAbs = path.join(outDir, stem + '.html');
  return p;
});
built.forEach(d => renderDoc(d, doclistHTML(built, d.htmlName)));
built.forEach(d => {
  const c = d.counts;
  const cnt = c.total ? ' · 高' + c.high + '/中低' + c.mid + '/轻微' + c.low + (c.conflict ? '/冲突' + c.conflict : '') + (c.pending ? '/待决' + c.pending : '') : '';
  console.log('✓ docs/review-views/' + d.htmlName + cnt);
});
fs.writeFileSync(path.join(outDir, 'index.html'), indexHTML(root, built));
console.log('✓ docs/review-views/index.html（目录页 · ' + built.length + ' 篇）');
