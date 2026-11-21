/* 데이터에 묻는 창 — 화면.
 *
 * DuckDB-WASM 이 브라우저 안에서 parquet 을 읽는다. 표는 SQL에 나오는 것만 그때그때 받는다.
 * AI(api/sql)에는 질문 문장과 표 설명만 보낸다. 데이터는 보내지 않는다.
 * 실행하는 SQL은 전부 «읽기 전용» 검사를 거친다 — AI가 쓴 것이든 사람이 쓴 것이든.
 */
import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.32.0/+esm";

const $ = s => document.querySelector(s);
const esc = v => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const S = { db: null, conn: null, loaded: new Set(), schema: null, questions: [], files: [], mine: {} };

// ── 엔진 ─────────────────────────────────────────────
async function startEngine() {
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }));
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(workerUrl));
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  S.db = db;
  S.conn = await db.connect();
}

async function ensureTables(sql) {
  const names = Object.keys(S.schema.tables).filter(t => new RegExp(`\\b${t}\\b`, "i").test(sql));
  for (const t of names) {
    if (S.loaded.has(t)) continue;
    $("#engine").textContent = `표 «${t}» 받는 중…`;
    const buf = new Uint8Array(await (await fetch(`data/${t}.parquet`)).arrayBuffer());
    await S.db.registerFileBuffer(`${t}.parquet`, buf);
    await S.conn.query(`CREATE VIEW ${t} AS SELECT * FROM '${t}.parquet'`);
    S.loaded.add(t);
  }
  engineLine();
}

function engineLine() {
  const mine = Object.keys(S.mine);
  $("#engine").textContent = mine.length
    ? `내 파일로 묻는 중 · ${mine.map(t => `${t} ← ${S.mine[t].file}`).join(" · ")}`
    : `준비됨 · 불러온 표 ${S.loaded.size}개 / ${Object.keys(S.schema.tables).length}개`;
  $("#engine").classList.add("ready");
  updateLoaded();
}

// ── 내 파일로 — 엑셀 · CSV 를 이 브라우저 안에서 표로 올린다 ─────────────
// 파일 이름에 questions.json 의 files[].match 가 들어 있으면 그 표를 대신한다.
function tableFor(name) {
  const hit = S.files.find(f => name.includes(f.match));
  return hit ? hit.table : null;
}

async function loadMine(fileList) {
  for (const f of fileList) {
    const table = tableFor(f.name);
    const item = document.createElement("div");
    item.className = "mine";
    $("#mine-list").appendChild(item);
    if (!table) {
      item.innerHTML = `<span class="no">✕</span> ${esc(f.name)} — 이 파일에 맞는 표가 없습니다. 파일 이름에 ${S.files.map(x => `«${esc(x.match)}»`).join(" · ")} 가운데 하나가 들어 있어야 합니다`;
      continue;
    }
    item.innerHTML = `${esc(f.name)} 읽는 중…`;
    const buf = new Uint8Array(await f.arrayBuffer());
    const isCsv = /\.csv$/i.test(f.name);
    const wb = isCsv
      ? XLSX.read(new TextDecoder("utf-8").decode(buf).replace(/^﻿/, ""), { type: "string", raw: true })
      : XLSX.read(buf, { type: "array" });
    const ws = wb.Sheets[wb.SheetNames[0]];
    const read = XLSX.utils.sheet_to_json(ws, { defval: "" }).length;
    const csv = XLSX.utils.sheet_to_csv(ws);
    const tmp = `mine_${table}.csv`;
    await S.db.registerFileText(tmp, csv);
    await S.conn.query(`DROP VIEW IF EXISTS ${table}`);
    await S.conn.query(`CREATE OR REPLACE TABLE ${table} AS SELECT * FROM read_csv('${tmp}', header = true, auto_detect = true)`);
    const n = Number((await S.conn.query(`SELECT count(*) AS n FROM ${table}`)).toArray()[0].n);
    S.loaded.add(table);
    S.mine[table] = { file: f.name, rows: n };
    const ok = n === read;
    item.innerHTML = `<span class="${ok ? "ok" : "no"}">${ok ? "✓" : "✕"}</span> <b>${esc(f.name)}</b> → 표 <code>${table}</code> · `
      + `파일에서 읽은 줄 ${read.toLocaleString("ko-KR")} · 표에 올라간 줄 ${n.toLocaleString("ko-KR")}`
      + (wb.SheetNames.length > 1 ? ` · ⚠ 시트 ${wb.SheetNames.length}개 중 첫 시트만 읽었습니다` : "");
  }
  $("#btn-mine-reset").hidden = !Object.keys(S.mine).length;
  engineLine();
  // 내 파일로 바꾼 뒤에는 범위와 준비한 질문을 그 파일 기준으로 다시 돌린다
  await showRange();
  for (const q of S.questions) await runBrief(q);
  renderTables(); renderActive(); dashDone = false;
  if (!$("#tab-dash").hidden) renderDash();
  if (TV.t) selectTable(TV.t, 0);
}

// ── 읽기 전용 검사 (api/sql.py 와 같은 규칙) ───────────────
const FORBIDDEN = /\b(insert|update|delete|drop|create|alter|attach|detach|copy|pragma|install|load|export|import|call|set)\b/i;
function readOnly(sql) {
  const s = String(sql || "").replace(/--[^\n]*/g, "").trim().replace(/;\s*$/, "").trim();
  if (!s || s.includes(";")) return null;
  if (!/^(select|with)\b/i.test(s)) return null;
  if (FORBIDDEN.test(s.replace(/'[^']*'/g, "''"))) return null;
  return s;
}

// ── 실행 ─────────────────────────────────────────────
async function run(sql) {
  const safe = readOnly(sql);
  if (!safe) throw new Error("읽기(SELECT · WITH)만 실행합니다. 한 문장만, 바꾸는 명령 없이.");
  await ensureTables(safe);
  let t = await S.conn.query(safe);
  // 소수 자리가 정해진 숫자(DECIMAL)는 화면에서 읽기 어려운 모양으로 온다 — 보통 숫자로 바꿔 다시 받는다
  const dec = t.schema.fields.filter(f => String(f.type).startsWith("Decimal"));
  if (dec.length) {
    const cols = t.schema.fields.map(f => dec.includes(f) ? `"${f.name}"::DOUBLE AS "${f.name}"` : `"${f.name}"`);
    t = await S.conn.query(`SELECT ${cols.join(", ")} FROM (${safe})`);
  }
  const fields = t.schema.fields.map(f => f.name);
  const rows = t.toArray().map(r => {
    const o = {};
    fields.forEach(f => { const v = r[f]; o[f] = typeof v === "bigint" ? Number(v) : v; });
    return o;
  });
  return { fields, rows };
}

function fmt(v) {
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("ko-KR") : String(v);
  if (v instanceof Date) return v.toISOString().slice(0, 10);
  return esc(v);
}

// opt(questions.json 의 table): groups = 열 묶음 머리(품목 · 업소), labels = 보이는 이름, signed = + 부호, percent = % 붙임
// SQL 열 이름은 바꾸지 않는다 — check 가 그 이름을 쓴다
function render({ fields, rows }, opt = {}) {
  const lab = f => (opt.labels && opt.labels[f]) || f;
  const groupOf = f => (opt.groups || []).find(g => g.cols.includes(f));
  const starts = new Set(fields.filter((f, i) => groupOf(f) && groupOf(f) !== groupOf(fields[i - 1])));
  const cls = (f, n) => [n ? "n" : "", starts.has(f) ? "gstart" : "", groupOf(f) ? "tone-" + (groupOf(f).tone || "a") : "",
    (opt.signed || []).includes(f) ? "sg" : ""].filter(Boolean).join(" ");
  let head;
  if (opt.groups) {
    let r1 = "", r2 = "";
    fields.forEach((f, i) => {
      const g = groupOf(f);
      if (!g) { r1 += `<th rowspan="2">${esc(lab(f))}</th>`; return; }
      if (starts.has(f)) {
        let span = 0; while (fields[i + span] && groupOf(fields[i + span]) === g) span++;
        r1 += `<th colspan="${span}" class="grp gstart tone-${g.tone || "a"}">${esc(g.label)}</th>`;
      }
      r2 += `<th class="${cls(f, true)}">${esc(lab(f))}</th>`;
    });
    head = `<tr>${r1}</tr><tr>${r2}</tr>`;
  } else {
    head = `<tr>${fields.map(f => `<th>${esc(lab(f))}</th>`).join("")}</tr>`;
  }
  const cell = (f, v) => {
    if (typeof v !== "number") return fmt(v);
    return ((opt.signed || []).includes(f) && v > 0 ? "+" : "") + fmt(v) + ((opt.percent || []).includes(f) ? "%" : "");
  };
  const body = rows.slice(0, 50).map(r => `<tr>${fields.map(f =>
    `<td class="${cls(f, typeof r[f] === "number")}">${cell(f, r[f])}</td>`).join("")}</tr>`).join("");
  let html = `<div class="res"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  if (rows.length > 50) html += `<p class="small muted">… ${rows.length}줄 중 50줄만 보여줍니다</p>`;
  // 막대 — 첫 열이 글자이고 마지막 열이 숫자일 때
  const last = fields[fields.length - 1];
  if (!opt.noBars && rows.length > 1 && rows.length <= 20 && typeof rows[0][fields[0]] === "string" && typeof rows[0][last] === "number") {
    const max = Math.max(...rows.map(r => Math.abs(r[last]))) || 1;
    html += `<div class="bars">` + rows.map(r => `<div class="bar"><span class="l">${esc(r[fields[0]])}</span>`
      + `<span class="t"><i style="width:${(Math.abs(r[last]) / max) * 100}%"></i></span><span class="v">${fmt(r[last])}</span></div>`).join("") + `</div>`;
  }
  return html;
}

function checks(q, res) {
  return `<div class="checks"><span class="h">CHECK</span>` + q.check.map(c => {
    const hit = res.rows.find(r => Object.entries(c.where || {}).every(([k, v]) => r[k] === v));
    const got = hit ? hit[c.col] : undefined;
    const ok = typeof got === "number" && Math.abs(got - c.want) < 1e-9;
    const where = Object.values(c.where || {}).join(" ");
    return `<span class="${ok ? "ok" : "no"}">${ok ? "✓" : "✕"} ${esc(where)} ${esc(c.col)} ${fmt(c.want)}</span>`;
  }).join("") + (Object.keys(S.mine).some(t => new RegExp(`\\b${t}\\b`, "i").test(q.sql))
    ? `<span class="muted">← 확인할 값은 목업 데이터의 값입니다. 내 파일에서는 달라도 됩니다</span>`
    : `<span class="muted">← 등록한 기댓값과 대조 (edata9-data 도구로 확인한 값)</span>`) + `</div>`;
}

// ── 준비한 질문 ───────────────────────────────────────
// 카드: questions.json 의 cards 설정이 가리키는 열을 결과에서 읽어 그린다. 숫자는 전부 SQL 결과에서 온다.
const sign = v => (typeof v === "number" && v > 0 ? "+" : "") + fmt(v);
const ko = n => Number(n || 0).toLocaleString("ko-KR");
function cardsHtml(q, res) {
  const c = q.cards;
  const need = [...c.title, c.before, c.after];
  if (!need.every(k => res.fields.includes(k))) return render(res);
  if (!res.rows.length) return `<div class="grid ranks"><div class="card empty">조건에 맞는 조합이 없습니다</div></div>`;
  const has = k => k && res.fields.includes(k);
  // 두 기간 비교 막대 — 카드 안에서 큰 값을 100%로
  const pair = (label, unit, b, a, d) => {
    const mx = Math.max(b || 0, a || 0) || 1;
    return `<div class="metric">
        <div class="m-head"><span>${label}</span><b>${fmt(b)} → ${fmt(a)}${unit}${d == null ? "" : ` <em>${sign(d)}</em>`}</b></div>
        <div class="m-row before"><span>${esc(c.before_label || "이전")}</span><span class="m-track"><i style="width:${(b / mx) * 100}%"></i></span><span class="m-v">${fmt(b)}</span></div>
        <div class="m-row after"><span>${esc(c.after_label || "이후")}</span><span class="m-track"><i style="width:${(a / mx) * 100}%"></i></span><span class="m-v">${fmt(a)}</span></div>
      </div>`;
  };
  return `<div class="grid ranks">` + res.rows.slice(0, 3).map((r, i) => {
    const pct = has(c.delta_pct) ? (r[c.delta_pct] == null ? "신규" : `${sign(r[c.delta_pct])}%`) : "";
    const share = has(c.share) ? r[c.share] : null, lim = c.share_limit;
    return `<div class="card rank-${i + 1}" style="--i:${i}">
      <div class="top">
        <div class="rank-no"><b>${i + 1}</b><span>위</span></div>
        <div class="rank-id"><span class="tag">CANDIDATE ${String.fromCharCode(65 + i)}</span>
          <h3>${c.title.map(k => esc(r[k])).join('<span class="x">×</span>')}</h3></div>
        <div class="pct">${esc(pct)}<small>품목 증감률</small></div>
      </div>
      ${pair("품목 (고유 신고)", "건", r[c.before], r[c.after], has(c.delta) ? r[c.delta] : null)}
      ${has(c.mfr_before) && has(c.mfr_after) ? pair("제조업소 (고유 인허가번호)", "곳", r[c.mfr_before], r[c.mfr_after], r[c.mfr_after] - r[c.mfr_before]) : ""}
      ${share == null ? "" : `<div class="metric share">
        <div class="m-head"><span>${esc(c.after_label || "")} 1위 업소 비중</span><b>${fmt(share)}%</b></div>
        <div class="meter" role="img" aria-label="1위 업소 비중 ${fmt(share)}%${lim ? `, 기준 ${lim}% 미만` : ""}">
          <i style="width:${Math.min(100, share)}%"></i>${lim ? `<s style="left:${lim}%"><em>기준 ${lim}%</em></s>` : ""}</div>
        <div class="m-note">${lim ? `기준 ${lim}% 미만 · ` : ""}낮을수록 여러 업소에 고르게 퍼진 증가</div>
      </div>`}
    </div>`;
  }).join("") + `</div>`;
}

async function runBrief(q) {
  const box = document.querySelector(`#q-${q.id}`);
  const out = box.querySelector(".out"), raw = box.querySelector(".raw-out"), btn = box.querySelector("[data-run]");
  const ms = box.querySelector("[data-ms]");
  btn.disabled = true; btn.textContent = "실행 중…";
  try {
    const t0 = performance.now();
    const res = await run(q.sql);
    const took = Math.round(performance.now() - t0);
    out.innerHTML = (q.cards ? cardsHtml(q, res) : render(res))
      + `<div class="check-row">${checks(q, res)}</div>`;
    raw.innerHTML = q.cards ? render(res, { ...(q.table || {}), noBars: true }) : "";
    raw.hidden = !q.cards;
    ms.textContent = `DuckDB · ${ko(took)}ms · ${res.rows.length}행`;
  } catch (e) { out.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  btn.disabled = false; btn.textContent = "▶ SQL 다시 실행";
}

function renderQuestions() {
  if (!S.questions.length) {
    $("#qlist").innerHTML = `<p class="note">questions.json 에 등록된 질문이 없습니다. «직접 묻기»에서 SQL을 실행할 수 있습니다.</p>`;
    return;
  }
  $("#qlist").innerHTML = S.questions.map(q => `
    <article class="brief" id="q-${q.id}">
      <div class="box rq">
        <div class="rq-row">
          <div class="rq-text">
            <div class="kicker"><i class="ico">?</i>RESEARCH QUESTION</div>
            <h2>${esc(q.q)}</h2>
            <div class="rq-meta">${[q.kicker ? `비교 ${q.kicker}` : "", q.cards?.basis || ""].filter(Boolean).map(t => `<span>${esc(t)}</span>`).join("")}</div>
          </div>
          <button class="run" data-run="${q.id}">▶ SQL 실행</button>
        </div>
      </div>
      <section class="cands">
        <div class="sec-head">
          <span class="sec-title big">${esc(q.cards ? q.cards.label : "결과")}${q.cards ? ' <span class="pill">상위 3개 · 순위순</span>' : ""}</span>
          <span class="sec-meta">${esc(q.cards?.basis || "")}</span>
        </div>
        <div class="out"><p class="small dim">데이터 엔진이 준비되면 SQL을 실행합니다…</p></div>
      </section>
      <section class="box sqlbox">
        <div class="box-head">
          <span class="res-title">실행된 SQL 쿼리 및 전체 결과표</span>
          <span class="box-meta" data-ms>DuckDB</span>
        </div>
        <div class="raw-out"></div>
        <pre class="sql">${esc(q.sql)}</pre>
        <div class="box-foot">
          <span class="box-meta">읽기(SELECT · WITH)만 실행합니다</span>
          <span class="btns"><button class="copy" data-copy="${q.id}">SQL 복사</button><button class="copy" data-edit="${q.id}">고쳐서 묻기</button></span>
        </div>
      </section>
      <section class="box method-box">
        <div class="box-head"><span class="box-title">METHODOLOGY &amp; LIMITATIONS</span></div>
        <div class="method"><b>선정 기준</b> ${esc(q.judge)}</div>
        ${q.limits ? `<div class="limits">${q.limits.map(l => `<div><b>${esc(l.label)}</b>${esc(l.text)}</div>`).join("")}</div>`
                   : (q.note ? `<p class="small">${esc(q.note)}</p>` : "")}
      </section>
    </article>`).join("");
  document.querySelectorAll("[data-run]").forEach(b => b.addEventListener("click", () =>
    runBrief(S.questions.find(x => x.id === b.dataset.run))));
  document.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(S.questions.find(x => x.id === b.dataset.copy).sql); b.textContent = "복사됨 ✓"; }
    catch { b.textContent = "복사 실패"; }
    setTimeout(() => { b.textContent = "SQL 복사"; }, 1500);
  }));
  document.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => {
    $("#sql").value = S.questions.find(x => x.id === b.dataset.edit).sql;
    document.querySelector("[data-tab=ask]").click();
  }));
}

// 머리의 데이터 범위 — filings 에서 직접 센다 (내 파일을 올렸으면 그 파일 기준)
async function showRange() {
  if (!S.schema.tables.filings) { $("#range").textContent = ""; return; }
  try {
    const { rows } = await run("SELECT strftime(MIN(filing_date), '%Y-%m-%d') AS a, strftime(MAX(filing_date), '%Y-%m-%d') AS b, "
      + "COUNT(*) FILTER (WHERE filing_date IS NULL) AS n FROM filings");
    $("#range").textContent = `DATA · ${rows[0].a} ~ ${rows[0].b} (날짜 누락 ${fmt(rows[0].n)}건)`;
  } catch { $("#range").textContent = "데이터 범위를 확인하지 못했습니다"; }
}

// ── 핵심 지표 — 모든 숫자는 이 자리에서 SQL로 센다 ─────────────
const P1 = ["2026-05-01", "2026-07-31"], P2 = ["2026-08-01", "2026-10-31"];
const inP = (p, col = "filing_date") => `${col} BETWEEN DATE '${p[0]}' AND DATE '${p[1]}'`;
const DASH_SQL = {
  kpi: `SELECT COUNT(DISTINCT filing_id) AS 전체_품목,
       COUNT(DISTINCT filing_id) FILTER (WHERE ${inP(P1)}) AS 품목_5_7월,
       COUNT(DISTINCT filing_id) FILTER (WHERE ${inP(P2)}) AS 품목_8_10월,
       COUNT(DISTINCT manufacturer_id) AS 전체_업소,
       COUNT(DISTINCT manufacturer_id) FILTER (WHERE ${inP(P1)}) AS 업소_5_7월,
       COUNT(DISTINCT manufacturer_id) FILTER (WHERE ${inP(P2)}) AS 업소_8_10월,
       (SELECT COUNT(DISTINCT filing_id) FROM filings WHERE filing_id NOT IN (SELECT filing_id FROM filing_functions)) AS 미분류,
       (SELECT COUNT(DISTINCT filing_id) FROM filings WHERE filing_id IN (SELECT filing_id FROM product_details)) AS 상세_연결,
       strftime(MIN(filing_date), '%Y-%m-%d') AS 시작, strftime(MAX(filing_date), '%Y-%m-%d') AS 끝
FROM filings`,
  month: `SELECT strftime(filing_date, '%Y-%m') AS 월,
       COUNT(DISTINCT filing_id) AS 신고_품목,
       COUNT(DISTINCT manufacturer_id) AS 제조업소
FROM filings
GROUP BY 1
ORDER BY 1`,
  form: `SELECT COALESCE(p.dosage_form, '제형 미확인') AS 제형,
       COUNT(DISTINCT f.filing_id) AS 신고_품목
FROM filings f
LEFT JOIN product_details p USING (filing_id)
GROUP BY 1
ORDER BY 2 DESC, 1`,
  func: `SELECT function_group AS 기능성,
       COUNT(DISTINCT filing_id) AS 신고_품목
FROM filing_functions
WHERE filing_id IN (SELECT filing_id FROM filings)
GROUP BY 1
ORDER BY 2 DESC, 1
LIMIT 10`,
};
let dashDone = false;
const more = (res, sql) => `<details class="more"><summary>표로 보기</summary>${render(res, { noBars: true })}</details>`
  + `<details class="more"><summary>SQL 보기</summary><pre class="sql">${esc(sql)}</pre></details>`;
function hbars(res, lk, vk, unit) {
  const max = Math.max(...res.rows.map(r => r[vk])) || 1;
  return `<div class="hbars">` + res.rows.map(r => `<div class="hbar"><span class="l" title="${esc(r[lk])}">${esc(r[lk])}</span>`
    + `<span class="t"><i class="mark" tabindex="0" data-tip="${esc(r[lk])} · ${ko(r[vk])}${unit}" style="width:${(r[vk] / max) * 100}%"></i></span>`
    + `<span class="v">${ko(r[vk])}</span></div>`).join("") + `</div>`;
}
async function renderDash() {
  if (!S.conn) return;
  const el = $("#dash");
  el.innerHTML = `<p class="empty-msg">지표를 계산하는 중…</p>`;
  try {
    const k = (await run(DASH_SQL.kpi)).rows[0];
    const m = await run(DASH_SQL.month), f = await run(DASH_SQL.form), g = await run(DASH_SQL.func);
    const d = k.품목_8_10월 - k.품목_5_7월, dp = k.품목_5_7월 ? (d / k.품목_5_7월) * 100 : null;
    const minus = v => (v > 0 ? "+" : v < 0 ? "−" : "") + ko(Math.abs(v));
    const maxM = Math.max(...m.rows.map(r => r.신고_품목)) || 1;
    const labIdx = new Set([m.rows.findIndex(r => r.신고_품목 === maxM), m.rows.length - 1]);
    el.innerHTML = `
      <div class="dash-hero"><span class="kk">✦ 전체 고유 신고 품목</span>
        <div class="big">${ko(k.전체_품목)}<small>건</small></div>
        <p>${esc(k.시작)} ~ ${esc(k.끝)} · 허가일자 기준 · 고유 filing_id</p></div>
      <div class="kpi-grid">
        <div class="kpi"><div class="k-label">8~10월 신고 품목</div><div class="k-value">${ko(k.품목_8_10월)}</div>
          <div class="k-delta">5~7월 ${ko(k.품목_5_7월)}건 대비 ${minus(d)}${dp == null ? "" : ` (${dp > 0 ? "+" : dp < 0 ? "−" : ""}${Math.abs(dp).toFixed(1)}%)`}</div></div>
        <div class="kpi"><div class="k-label">제조업소 (고유 인허가번호)</div><div class="k-value">${ko(k.전체_업소)}</div>
          <div class="k-sub">5~7월 ${ko(k.업소_5_7월)}곳 → 8~10월 ${ko(k.업소_8_10월)}곳</div></div>
        <div class="kpi"><div class="k-label">기능성 미분류</div><div class="k-value">${ko(k.미분류)}</div>
          <div class="k-sub">분류된 신고 ${k.전체_품목 ? ((1 - k.미분류 / k.전체_품목) * 100).toFixed(1) : "—"}% (규칙 v2)</div></div>
        <div class="kpi"><div class="k-label">제품상세 연결</div><div class="k-value">${k.전체_품목 ? ((k.상세_연결 / k.전체_품목) * 100).toFixed(1) : "—"}%</div>
          <div class="k-sub">${ko(k.상세_연결)} / ${ko(k.전체_품목)}건</div></div>
      </div>
      <div class="dash-grid">
        <div class="box">
          <div class="box-head"><div><div class="res-title">월별 고유 신고 품목</div>
            <div class="box-meta">허가일자 기준 · 막대에 마우스를 올리면 품목·업소 수가 보입니다</div></div></div>
          <div class="cols">${m.rows.map((r, i) => `<div class="col${labIdx.has(i) ? " lab" : ""}"><span class="v">${ko(r.신고_품목)}</span>`
            + `<i class="mark" tabindex="0" data-tip="${esc(r.월)} · 품목 ${ko(r.신고_품목)}건 · 업소 ${ko(r.제조업소)}곳" style="height:${(r.신고_품목 / maxM) * 100}%"></i></div>`).join("")}</div>
          <div class="cols-x">${m.rows.map(r => `<span>${esc(String(r.월).slice(2).replace("-", "."))}</span>`).join("")}</div>
          ${more(m, DASH_SQL.month)}
        </div>
        <div class="box">
          <div class="box-head"><div><div class="res-title">제형별 고유 신고 품목</div>
            <div class="box-meta">전체 기간 · 제형은 상세조회 성상 문구에서 분류한 값</div></div></div>
          ${hbars(f, "제형", "신고_품목", "건")}
          ${more(f, DASH_SQL.form)}
        </div>
        <div class="box wide">
          <div class="box-head"><div><div class="res-title">기능성별 고유 신고 품목 TOP 10</div>
            <div class="box-meta">전체 기간 · 규칙 v2 · 기능성이 여러 개인 신고는 기능성마다 한 번씩 셉니다</div></div></div>
          ${hbars(g, "기능성", "신고_품목", "건")}
          ${more(g, DASH_SQL.func)}
        </div>
      </div>`;
    dashDone = true;
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// ── 직접 묻기 ─────────────────────────────────────────
async function runAsk(label) {
  const b = $("#btn-run"); b.disabled = true;
  try {
    const t0 = performance.now();
    const res = await run($("#sql").value);
    const took = Math.round(performance.now() - t0);
    $("#ask-out").innerHTML = res.rows.length ? render(res, { noBars: true }) : `<p class="empty-msg">결과가 0행입니다</p>`;
    $("#ask-stat").hidden = false;
    $("#ask-stat").textContent = `${ko(took)}ms · ${res.rows.length}행`;
    $("#ask-rows").textContent = `${ko(res.rows.length)} rows`;
    $("#ask-sub").textContent = label ? `질문: ${label}` : "직접 쓴 SQL";
    $("#ask-foot").textContent = `읽기 전용 검사 통과 · ${res.rows.length > 50 ? "앞 50행만 보여줍니다" : "전체 행을 보여줍니다"}`;
  } catch (e) {
    $("#ask-out").innerHTML = `<p class="err">${esc(e.message)}</p>`;
    $("#ask-stat").hidden = true; $("#ask-rows").textContent = ""; $("#ask-foot").textContent = "실행하지 못했습니다";
  }
  b.disabled = false;
}
$("#btn-run").addEventListener("click", () => runAsk(null));
$("#btn-copy-sql").addEventListener("click", async e => {
  const b = e.currentTarget;
  try { await navigator.clipboard.writeText($("#sql").value); b.textContent = "복사됨 ✓"; } catch { b.textContent = "복사 실패"; }
  setTimeout(() => { b.textContent = "SQL 복사"; }, 1500);
});
document.querySelectorAll("[data-nl]").forEach(b => b.addEventListener("click", () => { $("#nl").value = b.dataset.nl; $("#nl").focus(); }));

$("#btn-nl").addEventListener("click", async () => {
  const q = $("#nl").value.trim();
  if (!q) { $("#nl").focus(); return; }
  const b = $("#btn-nl"); b.disabled = true; b.textContent = "AI가 SQL을 쓰는 중…";
  let r;
  try {
    const res = await fetch("api/sql", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, schema: S.schema }) });
    r = res.ok ? await res.json() : { sql: null, reason: `서버 함수가 없습니다 (${res.status}) — 배포하면 생깁니다` };
  } catch (e) { r = { sql: null, reason: "서버 함수에 닿지 못했습니다" }; }
  $("#nl-msg").hidden = false;
  if (r.sql) {
    $("#sql").value = r.sql;
    $("#nl-msg").textContent = `AI가 쓴 SQL입니다 — ${r.why || ""} · 결과를 읽어 보고, 하나는 손으로 검산하십시오.`;
    b.textContent = "실행 중…";
    await runAsk(q);
  } else {
    $("#nl-msg").textContent = `SQL을 만들지 못했습니다 — ${r.reason}. 아래 SQL을 직접 고쳐서 실행할 수 있습니다.`;
  }
  b.disabled = false; b.textContent = "▶ SQL 생성 및 실행";
});

// ── 표 보기 ───────────────────────────────────────────
const PAGE = 8;
const TV = { t: null, page: 0, n: 0 };
function fileOf(t) {
  if (S.mine[t]) return S.mine[t].file;
  const f = S.files.find(x => x.table === t);
  return f ? (f.file || f.match) : `data/${t}.parquet`;
}
function updateLoaded() { const el = $("#t-loaded"); if (el && S.schema) el.textContent = `불러온 표 ${S.loaded.size}개`; }
function renderTables() {
  $("#schema-note").textContent = S.schema.note;
  const ts = Object.entries(S.schema.tables);
  $("#t-count").textContent = `ACTIVE TABLES (${ts.length})`;
  $("#tables").innerHTML = ts.map(([t, i]) => `<button data-t="${esc(t)}" aria-pressed="${TV.t === t}">`
    + `<b>${esc(t)}</b><span class="n">${ko(S.mine[t] ? S.mine[t].rows : i.rows)}행</span><span class="f">${esc(fileOf(t))}</span></button>`).join("");
  document.querySelectorAll("#tables button").forEach(b => b.addEventListener("click", () => selectTable(b.dataset.t, 0)));
  updateLoaded();
}
async function selectTable(t, page) {
  const i = S.schema.tables[t];
  TV.t = t; TV.page = page;
  document.querySelectorAll("#tables button").forEach(b => b.setAttribute("aria-pressed", b.dataset.t === t));
  $("#tv-name").textContent = t;
  $("#tv-file").textContent = fileOf(t);
  $("#tv-grain").innerHTML = `<b>한 줄 = ${esc(i.grain)}</b>${i.desc ? ` · ${esc(i.desc)}` : ""}`;
  $("#tv-cols").innerHTML = i.columns.map(c => `<span><b>${esc(c.name)}</b>${esc(c.type)}</span>`).join("");
  if (!S.conn) { $("#tv-rows").innerHTML = `<p class="empty-msg">데이터 엔진이 준비되면 미리보기를 보여줍니다</p>`; return; }
  try {
    const n = (await run(`SELECT COUNT(*) AS n FROM ${t}`)).rows[0].n;
    TV.n = n;
    const dated = i.columns.map(c => (c.type === "DATE" ? `strftime(${c.name}, '%Y-%m-%d') AS ${c.name}` : c.name)).join(", ");
    let res;
    try { res = await run(`SELECT ${dated} FROM ${t} LIMIT ${PAGE} OFFSET ${page * PAGE}`); }
    catch { res = await run(`SELECT * FROM ${t} LIMIT ${PAGE} OFFSET ${page * PAGE}`); }   // 내 파일은 열 형식이 다를 수 있다
    $("#tv-meta").textContent = `${ko(n)} rows · ${res.fields.length} columns`;
    $("#tv-rows").innerHTML = render(res, { noBars: true });
    const a = n ? page * PAGE + 1 : 0, z = Math.min(n, (page + 1) * PAGE);
    $("#tv-range").textContent = `${ko(a)}–${ko(z)} / ${ko(n)}행 표시 중`;
    $("#tv-prev").disabled = page === 0;
    $("#tv-next").disabled = z >= n;
  } catch (e) { $("#tv-rows").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  updateLoaded();
}
$("#tv-prev").addEventListener("click", () => { if (TV.t && TV.page > 0) selectTable(TV.t, TV.page - 1); });
$("#tv-next").addEventListener("click", () => { if (TV.t) selectTable(TV.t, TV.page + 1); });

// ── 탭 ───────────────────────────────────────────────
const TABS = ["preset", "dash", "ask", "tables", "mine"];
document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach(x => x.setAttribute("aria-selected", x === b));
  TABS.forEach(k => { $("#tab-" + k).hidden = k !== b.dataset.tab; });
  if (b.dataset.tab === "dash" && !dashDone) renderDash();
  if (b.dataset.tab === "tables" && !TV.t && S.schema) selectTable(S.schema.tables.filings ? "filings" : Object.keys(S.schema.tables)[0], 0);
}));

// ── 내 파일로 — 넣기 ─────────────────────────────────────
$("#mine-file").addEventListener("change", e => { if (e.target.files.length) loadMine([...e.target.files]); });
const drop = $("#drop");
["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => { if (e.dataTransfer.files.length) loadMine([...e.dataTransfer.files]); });
$("#btn-mine-reset").addEventListener("click", () => location.reload());

function renderActive() {
  $("#active-tables").innerHTML = `<span class="box-title"><i class="ico">◎</i>ACTIVE TABLES</span><div class="at-items">`
    + Object.keys(S.schema.tables).map(t => `<span><b>${esc(t)}</b>— ${esc(fileOf(t))}</span>`).join("") + `</div>`;
}

// ── 시작 ─────────────────────────────────────────────
(async () => {
  const [sch, qs] = await Promise.all([fetch("schema.json").then(r => r.json()), fetch("questions.json").then(r => r.json())]);
  S.schema = sch; S.questions = qs.questions; S.files = qs.files || [];
  renderQuestions(); renderTables(); renderActive();
  $("#ask-ref").textContent = "참조: " + Object.keys(S.schema.tables).join(", ");
  $("#mine-rule").textContent = S.files.length
    ? "파일 이름으로 표를 고릅니다 — " + S.files.map(f => `«${f.match}»가 든 파일 → ${f.table}`).join(" · ")
    : "questions.json 에 files 가 없어 내 파일을 받을 수 없습니다";
  try {
    await startEngine();
    $("#engine").textContent = "준비됨 · 표는 질문에 나올 때 받습니다";
    $("#engine").classList.add("ready");
    document.body.dataset.engine = "ready";
  } catch (e) {
    $("#engine").textContent = "데이터 엔진을 불러오지 못했습니다 — 인터넷 연결을 확인하십시오";
    document.body.dataset.engine = "fail";
    document.querySelectorAll(".brief .out").forEach(o => { o.innerHTML = `<p class="err">데이터 엔진이 없어 SQL을 실행하지 못했습니다</p>`; });
    return;
  }
  await showRange();
  for (const q of S.questions) await runBrief(q);   // 첫 화면에서 바로 결과가 보이게
  document.body.dataset.briefs = "done";
  if (!$("#tab-dash").hidden && !dashDone) renderDash();
  if (!$("#tab-tables").hidden && TV.t) selectTable(TV.t, TV.page);
})();
