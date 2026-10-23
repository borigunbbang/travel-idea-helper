// 프로젝트 ID는 URL 해시에 둔다. 새로고침해도 서버 상태를 다시 읽어 화면을 복원한다.
const $app = document.getElementById("app");
let state = null;
let pollTimer = null;
let checked = new Set();
const reqIds = {}; // 같은 동작의 중복 클릭은 같은 requestId를 재사용

const uid = () => crypto.randomUUID();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ERR = {
  search_failed: ["검색 실패", "검색 도구 호출이 실패했거나 결과가 없습니다. 잠시 후 재시도하세요."],
  auth_required: ["로그인 필요", "터미널에서 claude 또는 agy를 실행해 로그인한 뒤 재시도하세요."],
  quota_exceeded: ["사용량 초과", "agy 이미지 쿼터가 다시 채워진 뒤 재시도하세요."],
  image_failed: ["이미지 생성 실패", "해당 카드를 재시도하세요."],
  engine_failed: ["엔진 오류", "엔진 응답이 올바르지 않았습니다. 재시도하세요."],
  interrupted: ["작업 중단", "서버가 재시작되어 작업이 멈췄습니다. 재시도하세요."],
  cancelled: ["취소됨", "다시 진행하려면 재시도하세요."],
};
const fmt = (iso) => (iso ? new Date(iso).toLocaleString("ko-KR") : "미확인");

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail?.message || JSON.stringify(data.detail) || r.statusText);
  return data;
}

function pid() { return location.hash.slice(1); }

const STAGE = {
  created: "생성됨", researching: "조사 중", selecting: "후보 선택", deep_research: "심층 조사",
  waiting_for_user: "질문 대기", storyboard_review: "스토리보드 검토", producing: "카드 제작",
  final_review: "최종 검토", approved: "승인 완료",
};
const $side = document.getElementById("side");
let sideTimer = null;

async function renderSide() {
  let list;
  try { list = (await api("GET", "/api/projects")).projects; }
  catch { $side.innerHTML = ""; return; }  // 목록 기능이 없는 옛 서버면 목록을 숨긴다
  const cur = pid();
  const when = (t) => new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  $side.innerHTML = `<a class="newp ${cur ? "" : "on"}" href="#">＋ 새 프로젝트</a><h3>내 프로젝트 ${list.length}개</h3>` +
    list.map((p) => `<a class="pitem ${p.id === cur ? "on" : ""}" href="#${p.id}">
      <b>${esc(p.topic)}</b>${p.mock ? ' <span class="tag">모의</span>' : ""}
      <span class="muted">${when(p.created)} · ${p.running ? '<span class="spinner"></span>' : ""}${esc(STAGE[p.stage] || p.stage)}${p.error ? ' · <span class="bad">' + esc((ERR[p.error] || [p.error])[0]) + "</span>" : ""}</span></a>`).join("");
  clearTimeout(sideTimer);
  if (list.some((p) => p.running)) sideTimer = setTimeout(renderSide, 4000);
}

async function refresh() {
  renderSide();
  if (!pid()) return renderStart();
  try {
    state = await api("GET", `/api/projects/${pid()}`);
  } catch (e) {
    $app.innerHTML = `<div class="error">${esc(e.message)}</div><a href="/">처음으로</a>`;
    return;
  }
  if (state.stage === "selecting" && checked.size === 0) checked = new Set(state.selection);
  render();
  const busy = state.active_run || ["researching", "deep_research"].includes(state.stage) && !state.error;
  clearTimeout(pollTimer);
  if (busy) pollTimer = setTimeout(refresh, 2000);
}

function renderStart() {
  $app.innerHTML = `
    <div class="box">
      <h2>주제 입력</h2>
      <p class="muted">주제를 입력하면 최근 자료를 조사해 후보를 보여 줍니다.</p>
      <form id="f" class="row">
        <input type="text" id="topic" placeholder="예: AI 최신소식" style="flex:1" required>
        <button>조사 시작</button>
      </form>
      <p class="muted">기본 조사 기간: 최근 7일</p>
    </div>`;
  document.getElementById("f").onsubmit = async (e) => {
    e.preventDefault();
    e.submitter.disabled = true;
    const { projectId } = await api("POST", "/api/projects", { topic: document.getElementById("topic").value });
    await api("POST", `/api/projects/${projectId}/research`, { requestId: uid() });
    location.hash = projectId;
  };
  document.getElementById("topic").focus();
}

function header() {
  const r = state.research;
  const TEXT = { claude_cli: "Claude CLI", mock: "모의 엔진" };
  const IMAGE = { agy: "agy (Antigravity CLI)", mock: "모의 이미지" };
  const w = state.workers || {};
  let h = `<h2>${esc(state.topic)}</h2><p class="muted">단계: <b>${esc(STAGE[state.stage] || state.stage)}</b> · 조사·원고: ${esc(TEXT[w.text] || w.text || state.engine || "-")} · 이미지: ${esc(IMAGE[w.image] || w.image || "-")}</p>`;
  if (state.research?.mock || state.mock || state.question?.mock) h += `<div class="mock">⚠ 모의 데이터입니다. 실제 검색 결과가 아닙니다.</div>`;
  if (r) h += `<p class="muted">조사 기준 시각: ${fmt(r.searched_at)} · 기간: 최근 ${r.period_days}일 (${fmt(r.period_start)} ~)</p>`;
  if (state.error) {
    const [label, next] = ERR[state.error.kind] || [state.error.kind, "재시도하세요."];
    const cardStage = ["producing", "final_review", "approved"].includes(state.stage);
    const failedCards = cardStage ? state.cards.filter((c) => c.status === "failed") : [];
    const retry = !cardStage ? `<button class="secondary" id="retry">재시도</button>`
      : failedCards.length ? `<button class="secondary" id="retry">실패한 ${failedCards.length}장 재시도</button>`
      : state.stage === "approved" ? `<button class="secondary" id="recheck">내보낸 파일 다시 점검</button>`
      : `<span class="muted">아래 '카드 고치기'에서 요청을 다시 보내세요.</span>`;
    h += `<div class="error"><b>${esc(label)}</b> — ${esc(state.error.message)}<br>
      <span class="muted">다음 행동: ${esc(next)}</span><br>${state.active_run ? "" : retry}</div>`;
  }
  if (state.notice) h += `<div class="mock">${esc(state.notice)}</div>`;
  if (state.active_run) h += `<p><span class="spinner"></span>${state.active_run.kind === "verify" ? "내보낸 파일 점검 중…" : "작업 중…"} <button class="secondary" id="cancel">취소</button></p>`;
  return h;
}

function render() {
  let body = "";
  const s = state.stage;
  if (s === "selecting") body = renderCandidates();
  else if (s === "waiting_for_user") body = renderQuestion();
  else if (s === "storyboard_review") body = renderStoryboard();
  else if (["producing", "final_review", "approved"].includes(s)) body = renderCards();
  else if (!state.active_run && !state.error) body = `<p class="muted">상태를 불러오는 중…</p>`;
  $app.innerHTML = header() + body;
  bind();
}

function renderCandidates() {
  const cs = state.candidates;
  if (!cs.length) return `<div class="box">후보가 없습니다. <button id="widen">30일로 넓혀 다시 조사</button></div>`;
  let h = "";
  if (cs.length < 5 && state.period_days < 30) h += `<div class="mock">최근 ${state.period_days}일 결과가 ${cs.length}개뿐입니다. 30일로 넓혀 다시 조사할까요? <button class="secondary" id="widen2">30일로 넓히기</button></div>`;
  h += `<p>후보를 1~3개 고르세요. (${checked.size}개 선택)</p>`;
  for (const c of cs) {
    h += `<label class="box cand ${checked.has(c.id) ? "checked" : ""}">
      <input type="checkbox" data-id="${c.id}" ${checked.has(c.id) ? "checked" : ""}>
      <div><div><span class="tag">${esc(c.category)}</span>${c.grouped > 1 ? `<span class="tag">관련 기사 ${c.grouped}건 묶음</span>` : ""}</div>
      <b>${esc(c.title)}</b>
      <p>${esc(c.summary)}</p>
      <p class="muted">게시일: ${esc(c.published || "미확인")} · 사건일: ${esc(c.event_date || "미확인")}<br>
      가치: ${esc(c.value)}<br>불확실성: ${esc(c.uncertainty)}<br>
      <a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.url)}</a></p></div></label>`;
  }
  h += `<div class="row"><button id="next" ${checked.size < 1 || checked.size > 3 ? "disabled" : ""}>선택한 ${checked.size}개로 다음 단계</button>
        <button class="secondary" id="widen">30일로 넓혀 다시 조사</button></div>`;
  return h;
}

function renderQuestion() {
  const q = state.question;
  let h = `<div class="box"><h2>${esc(q.question)}</h2>`;
  if (q.recommended) h += `<p class="muted">추천: <b>${esc(q.recommended)}</b> — ${esc(q.reason)}<br>(추천은 자동으로 선택되지 않습니다)</p>`;
  for (const o of q.options) {
    h += `<label class="opt ${o.value === q.recommended ? "rec" : ""}"><input type="radio" name="ans" value="${esc(o.value)}"> <b>${esc(o.value)}</b> — <span class="muted">${esc(o.desc)}</span></label>`;
  }
  if (q.allow_free_text) h += `<label class="opt"><input type="radio" name="ans" value="__free"> 직접 입력 <input type="text" id="free" placeholder="원하는 독자/방향"></label>`;
  h += `<button id="answer">답변 제출</button></div>`;
  return h;
}

function renderStoryboard() {
  const e = state.editorial;
  let h = `<div class="box"><h2>편집 판단</h2><p>독자: <b>${esc(e.audience)}</b><br>이유: ${esc(e.why)}<br>추천 형식: <b>${esc(e.format)}</b>${e.format_reason ? ` — ${esc(e.format_reason)}` : ""}</p>`;
  if (e.format_comparison?.length) h += `<p>형식 비교:</p><ul>${e.format_comparison.map((f) => `<li><b>${esc(f.format)}</b>: ${esc(f.fits)} / ${esc(f.tradeoff)}</li>`).join("")}</ul>`;
  h += `<p>후킹 문구:</p><ul>${e.hooks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
  h += renderNotes();
  h += `<div class="box"><h2>스토리보드 (${state.storyboard.length}장 · ${esc(e.format)})</h2>`;
  const vs = state.visual_style;
  if (vs) h += `<p class="muted">공통 그림체 — 색감: ${esc(vs.palette)} · 그림체: ${esc(vs.style)} · 분위기: ${esc(vs.mood)}</p>`;
  h += `<ol class="sb">`;
  for (const c of state.storyboard) h += `<li><b>[${esc(c.role)}] ${esc(c.title)}</b>
    <dl class="sbd"><dt>핵심 문장</dt><dd>${esc(c.line)}</dd>
    <dt>본문</dt><dd>${esc(c.body || "-")}</dd>
    <dt>이미지 역할</dt><dd>${esc(c.image_role || "-")}<br><span class="muted">그릴 내용: ${esc(c.image_plan)}</span></dd>
    <dt>출처</dt><dd>${c.sources.length ? c.sources.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join("<br>") : "없음"}</dd></dl></li>`;
  h += `</ol><button id="approveSb">스토리보드 승인하고 카드 제작</button>
    <p class="muted">승인하면 카드마다 Antigravity로 배경 그림을 만들고 글자를 따로 올립니다. 장당 1~2분 걸립니다.</p></div>`;
  for (const a of state.answers) h += `<p class="muted">받은 답변: ${esc(a.question)} → ${esc(a.answer)}</p>`;
  return h;
}

const CHECK = { background: "배경색 통일", picture: "그림 보임", file: "파일", overflow: "글자 잘림", contrast: "대비", phone: "휴대폰 가독성", sources: "사실/출처" };
let picked = new Set();
let editing = null;  // 휴대폰 미리보기·글자 고치기 창을 연 카드 id

function renderCards() {
  const s = state.stage;
  const fileUrl = (path) => `/api/projects/${pid()}/files/${path}`;
  let h = "";
  if (s === "approved") h += `<div class="box"><h2>최종 승인됨</h2><div id="dl" class="muted">내려받기 목록을 불러오는 중…</div></div>`;
  h += `<div class="grid">`;
  for (const c of state.cards) {
    const cur = c.current ? c.versions[c.current - 1] : null;
    h += `<div class="box cardbox">`;
    h += `<label class="row"><input type="checkbox" class="pick" data-id="${c.id}" ${picked.has(c.id) ? "checked" : ""} ${state.active_run ? "disabled" : ""}> <b>${c.index}. [${esc(c.role)}]</b> <span class="tag">${esc(c.status)}</span>${cur ? `<span class="muted">v${cur.v}</span>` : ""}</label>`;
    if (cur) h += `<a href="${fileUrl(cur.png)}" target="_blank"><img src="${fileUrl(cur.png)}?t=${cur.created}" alt="${esc(cur.title)}"></a>`;
    else if (c.status === "running") h += `<p><span class="spinner"></span>그림 생성 중…</p>`;
    else if (c.status === "pending") h += `<p class="muted">대기 중</p>`;
    if (c.status === "failed") h += `<div class="error"><b>${esc((ERR[c.error?.kind] || ["실패"])[0])}</b> — ${esc(c.error?.message)}<br><button class="secondary retryCard" data-id="${c.id}" ${state.active_run ? "disabled" : ""}>이 카드 재시도</button></div>`;
    const t = cur || c;  // 완성된 카드는 그 버전의 글, 아직이면 기획 글
    h += `<details class="cardtext"${cur ? "" : " open"}><summary>${esc(t.title)}</summary><dl class="sbd">
      <dt>역할</dt><dd>${esc(c.role)}</dd>
      <dt>핵심 문장</dt><dd>${esc(t.line)}</dd>
      <dt>본문</dt><dd>${esc(t.body ?? c.body ?? "-")}</dd>
      <dt>그림 계획</dt><dd>${esc(t.image_role ?? c.image_role ?? "")}${(t.image_role ?? c.image_role) ? "<br>" : ""}<span class="muted">${esc(t.image_plan)}</span></dd>
      <dt>출처</dt><dd>${t.sources.length ? t.sources.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join("<br>") : "없음"}</dd></dl></details>`;
    if (cur && cur.v > 1) h += changesHtml(c.versions[cur.v - 2], cur);
    if (cur) h += `<button class="secondary editCard" data-id="${c.id}" style="width:100%;margin:4px 0">📱 휴대폰 미리보기·글자 고치기${cur.problems?.length ? ` <span class="bad">(문제 ${cur.problems.length})</span>` : ""}</button>`;
    if (cur) {
      h += `<ul class="checks">${Object.entries(cur.checks).map(([k, v]) => `<li class="${v.ok ? "ok" : "bad"}">${v.ok ? "✓" : "✗"} ${CHECK[k] || k}: ${esc(v.detail)}</li>`).join("")}</ul>`;
      h += `<p class="muted">이미지: ${esc(cur.image.tool)} · ${esc(cur.image.note || "")}</p>`;
      if (cur.image.fallback_reason) h += `<p class="error" style="padding:6px 8px">대체 생성 — ${esc(cur.image.fallback_reason)}</p>`;
    }
    h += `</div>`;
  }
  h += `</div>`;
  if (editing) h += renderEditor(state.cards.find((c) => c.id === editing));
  if (!state.active_run) {
    h += `<div class="box" id="revbox"><h2>카드 고치기</h2>
      <p class="muted">카드를 체크하거나 요청에 '3번 카드'처럼 번호를 적으세요. 고르지 않은 카드는 그대로 둡니다.${s === "approved" ? " <b>고치면 최종 승인이 풀려 다시 승인해야 합니다.</b>" : ""}</p>
      <div class="row">
        <label class="opt modeopt"><input type="radio" name="rmode" value="text" ${reviseMode === "text" ? "checked" : ""}> <b>글자만 고치기</b> <span class="muted">— 그림은 그대로 씁니다</span></label>
        <label class="opt modeopt"><input type="radio" name="rmode" value="image" ${reviseMode === "image" ? "checked" : ""}> <b>그림 다시 만들기</b> <span class="muted">— 글자는 그대로, 그 카드 그림만 새로</span></label>
      </div>
      <textarea id="instr" rows="2" placeholder="${reviseMode === "text" ? "예: 3번 카드의 본문이 길어. 핵심 조건을 유지하며 두 문장으로 줄여 줘" : "예: 사람 모양이 이상해. 노트북과 그래프만 그려 줘 (비우면 같은 계획으로 다시 그림)"}">${esc(draftInstr)}</textarea>
      <p class="muted" id="rtarget">${targetText()}</p>
      <div class="row"><button class="secondary" id="revise" ${reviseTargets().length ? "" : "disabled"}>고치기</button>
      ${s === "final_review" ? `<button id="approveFinal">최종 승인</button>` : ""}</div></div>`;
  }
  return h;
}

let reviseMode = "text";
let draftInstr = "";

function numbersInText(t) {
  return [...t.matchAll(/(\d+)\s*번/g)].map((m) => +m[1]).filter((n) => n >= 1 && n <= state.cards.length);
}

function reviseTargets() {  // 체크한 카드가 없으면 요청 속 'N번'을 대상으로
  if (picked.size) return state.cards.filter((c) => picked.has(c.id));
  const ns = numbersInText(draftInstr);
  return state.cards.filter((c) => ns.includes(c.index));
}

function targetText() {
  const ts = reviseTargets();
  if (!ts.length) return "대상: 아직 없음 (카드를 체크하거나 요청에 번호를 적으세요)";
  const other = picked.size ? numbersInText(draftInstr).filter((n) => !ts.some((c) => c.index === n)) : [];
  return `대상: ${ts.map((c) => c.index + "번").join(", ")} · ${reviseMode === "text" ? "글자만 새 버전, 그림 재사용" : "그림을 새로 만들어 새 버전"}`
    + (other.length ? ` <span class="bad">(요청에 적힌 ${other.join(", ")}번은 체크되지 않아 고치지 않습니다)</span>` : "");
}

function changesHtml(prev, cur) {
  const rows = [["제목", "title"], ["핵심 문장", "line"], ["본문", "body"]]
    .filter(([, k]) => (prev[k] || "") !== (cur[k] || ""))
    .map(([t, k]) => `<dt>${t}</dt><dd><del>${esc(prev[k])}</del><br>${esc(cur[k])}</dd>`);
  const pic = prev.bg === cur.bg ? "그림 그대로" : "그림 새로 만듦";
  return `<details class="cardtext"><summary>v${prev.v} → v${cur.v} 바뀐 점 · ${pic}</summary>${rows.length ? `<dl class="sbd">${rows.join("")}</dl>` : `<p class="muted">글자 변화 없음</p>`}</details>`;
}

const PALETTE = { match: "그림 배경색", dark: "남색" };
const POS = { split: "글자 위 · 그림 아래 (겹치지 않게)", picture_top: "그림 위 · 글자 아래 (겹치지 않게)", top: "그림 위에 겹쳐서: 위(제목)·아래(본문)", bottom: "그림 위에 겹쳐서: 모두 아래 띠에" };
const SIZE = { small: "작게", normal: "보통", large: "크게" };
const SHADE = { light: "약하게", normal: "보통", strong: "강하게" };

function renderEditor(c) {
  if (!c) return "";
  const cur = c.current ? c.versions[c.current - 1] : null;
  const lay = { position: "split", size: "normal", shade: "normal", palette: "match", ...(cur?.layout || {}), ...(c.layout || {}) };
  const sel = (id, map, val) => `<select id="${id}">${Object.entries(map).map(([v, t]) => `<option value="${v}" ${v === val ? "selected" : ""}>${t}</option>`).join("")}</select>`;
  const probs = cur?.problems || [];
  return `<div class="modal" id="modal"><div class="sheet">
    <div class="row" style="justify-content:space-between">
      <div class="row"><button class="secondary" id="edPrev" ${c.index <= 1 ? "disabled" : ""}>◀ 이전</button>
        <h2 style="margin:0">${c.index} / ${state.cards.length} · 휴대폰 미리보기</h2>
        <button class="secondary" id="edNext" ${c.index >= state.cards.length ? "disabled" : ""}>다음 ▶</button></div>
      <button class="secondary" id="closeEd">닫기</button></div>
    <div class="edwrap">
      <div>
        <div class="phone"><div class="screen" id="phoneScreen">
          ${cur ? `<img src="/api/projects/${pid()}/files/${cur.png}?t=${cur.created}" alt="카드 미리보기">` : "<p>아직 그림이 없습니다</p>"}
          ${probs.map((p, i) => `<div class="pbox ${p.kind}" title="${esc(p.label)}" style="left:${p.box[0] / 10.8}%;top:${p.box[1] / 13.5}%;width:${Math.max(0.5, (p.box[2] - p.box[0]) / 10.8)}%;height:${Math.max(0.5, (p.box[3] - p.box[1]) / 13.5)}%"><span>${i + 1}</span></div>`).join("")}
        </div></div>
        <p class="muted" style="text-align:center">실제 휴대폰 폭(360pt) 크기 · ←/→ 키나 밀어서 넘기기</p>
        ${metricsHtml(cur)}
      </div>
      <div class="edform">
        ${probs.length ? `<div class="error"><b>고칠 곳 ${probs.length}개</b><ol>${probs.map((p) => `<li>${esc(p.label)}</li>`).join("")}</ol>
          <span class="muted">잘림 → 글자를 줄이거나 크기 '작게' · 배경 겹침 → '글자 위 · 그림 아래' 배치 또는 배경 가림 '강하게'</span></div>`
          : `<p class="okmsg">✓ 잘리거나 배경과 겹친 줄이 없습니다</p>`}
        <label>제목<input type="text" id="edTitle" value="${esc(c.title)}"></label>
        <label>핵심 문장<textarea id="edLine" rows="2">${esc(c.line)}</textarea></label>
        <label>본문<textarea id="edBody" rows="3">${esc(c.body || "")}</textarea></label>
        <div class="row">
          <label>글자 위치 ${sel("edPos", POS, lay.position)}</label>
          <label>글자 크기 ${sel("edSize", SIZE, lay.size)}</label>
          <label>글자 영역 색 ${sel("edPalette", PALETTE, lay.palette)}</label>
          <label>배경 가림(겹쳐 배치만) ${sel("edShade", SHADE, lay.shade)}</label>
        </div>
        <button id="edApply" ${state.active_run ? "disabled" : ""}>${state.active_run ? '<span class="spinner"></span>다시 그리는 중…' : "글자 다시 배치 (그림은 그대로)"}</button>
        <p class="muted">AI를 다시 부르지 않고 몇 초 안에 새 버전을 만듭니다. 고치면 최종 승인은 풀립니다.</p>
      </div>
    </div></div></div>`;
}

function metricsHtml(cur) {
  const m = cur?.metrics;
  if (!m) return `<p class="muted">글자 수치는 이 카드를 한 번 다시 배치하면 표시됩니다.</p>`;
  const px = (v, min) => (v == null ? "-" : `<span class="${v >= min ? "" : "bad"}">${v}px</span>`);
  return `<table class="metrics">
    <tr><th>글자 크기(휴대폰)</th><td>제목 ${px(m.phone_px.title, 18)} · 핵심 ${px(m.phone_px.line, 12)} · 본문 ${px(m.phone_px.body, 12)}</td></tr>
    <tr><th>줄바꿈</th><td>제목 ${m.lines.title}줄 · 핵심 ${m.lines.line}줄 · 본문 ${m.lines.body}줄</td></tr>
    <tr><th>여백</th><td>좌우 ${m.margin_pt}pt · 그림 ${m.picture_pct}%</td></tr>
    <tr><th>명암(최저 대비)</th><td><span class="${m.min_contrast == null || m.min_contrast >= 4.5 ? "" : "bad"}">${m.min_contrast ?? "-"} : 1</span> (기준 4.5:1)</td></tr></table>`;
}

function moveEditor(step) {
  const i = state.cards.findIndex((c) => c.id === editing) + step;
  if (i >= 0 && i < state.cards.length) { editing = state.cards[i].id; render(); }
}

document.addEventListener("keydown", (e) => {
  if (!editing || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  if (e.key === "ArrowLeft") moveEditor(-1);
  if (e.key === "ArrowRight") moveEditor(1);
  if (e.key === "Escape") { editing = null; render(); }
});

async function loadExports() {
  const el = document.getElementById("dl");
  if (!el) return;
  try {
    const r = await api("GET", `/api/projects/${pid()}/exports`);
    const link = (f, label) => `<a href="${f.url}" download>${esc(label || f.name)}</a>`;
    const by = (re) => r.files.filter((f) => re.test(f.name));
    const zip = by(/\.zip$/), pngs = by(/\.png$/), recs = by(/^(sources\.md|image_credits\.json)$/), reps = by(/^check_report/);
    el.innerHTML = `<p>승인 버전 v${r.approvedVersion}</p>
      ${zip.map((f) => `<p>${link(f, "⬇ 전체 ZIP 내려받기 (cardnews.zip)")}</p>`).join("")}
      <p><b>카드별 PNG</b> ${pngs.map((f) => link(f)).join(" · ")}</p>
      <p><b>출처·이미지 사용 기록</b> ${recs.map((f) => link(f)).join(" · ")}</p>
      ${reps.length ? `<p><b>점검 보고서</b> ${reps.map((f) => link(f)).join(" · ")}</p>` : ""}` + checkHtml(r.check);
    const b = document.getElementById("recheck2");
    if (b) b.onclick = async () => { try { await api("POST", `/api/projects/${pid()}/exports/check`); } catch (err) { alert(err.message); } refresh(); };
  } catch (e) { el.textContent = e.message; }
}

const ST = { pass: ["통과", "ok"], fail: ["실패", "bad"], unverified: ["미확인", "warn"] };

function checkHtml(rep) {
  if (!rep) return state.active_run ? `<p class="muted"><span class="spinner"></span>내보낸 파일을 다시 열어 점검하는 중…</p>` : "";
  const c = rep.counts;
  let h = `<h3>내보낸 파일 점검</h3><p>통과 <b class="okc">${c.pass}</b> · 실패 <b class="bad">${c.fail}</b> · 미확인 <b class="warnc">${c.unverified}</b>
    <span class="muted">(${fmt(rep.checked_at)}) 미확인은 통과가 아닙니다.</span> ${state.active_run ? "" : `<button class="secondary" id="recheck2">다시 점검</button>`}</p>`;
  for (const area of ["누락", "글자 잘림", "출처", "날짜"]) {
    const its = rep.items.filter((i) => i.area === area);
    const n = (k) => its.filter((i) => i.status === k).length;
    h += `<details class="cardtext" ${n("fail") ? "open" : ""}><summary>${area} — 통과 ${n("pass")} · 실패 ${n("fail")} · 미확인 ${n("unverified")}</summary><ul class="checks">`
      + its.map((i) => `<li class="${ST[i.status][1]}">[${ST[i.status][0]}] ${i.card ? i.card + "번 " : ""}${esc(i.name)}: ${esc(i.detail)}</li>`).join("") + `</ul></details>`;
  }
  const todos = rep.items.filter((i) => i.todo);
  if (todos.length) h += `<h3>직접 확인할 것 (${todos.length})</h3><ol class="todo">${todos.map((i) => `<li>${i.card ? i.card + "번 " : ""}${esc(i.todo)}</li>`).join("")}</ol>`;
  return h;
}

function renderNotes() {
  const n = state.notes;
  if (!n) return "";
  const sec = (t, xs) => (xs?.length ? `<p><b>${t}</b></p><ul>${xs.map((x) => `<li>${/^https?:/.test(x) ? `<a href="${esc(x)}" target="_blank" rel="noopener">${esc(x)}</a>` : esc(x)}</li>`).join("")}</ul>` : "");
  return `<details class="box"><summary>조사 기록 (사실·출처·한계·상충)</summary>${sec("사실", n.facts)}${sec("출처", n.sources)}${sec("한계", n.limits)}${sec("상충하는 내용", n.conflicts)}</details>`;
}

function bind() {
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  document.querySelectorAll(".cand input").forEach((el) => (el.onchange = () => {
    el.checked ? checked.add(el.dataset.id) : checked.delete(el.dataset.id);
    render();
  }));
  on("next", async (e) => {
    e.target.disabled = true;
    try { await api("POST", `/api/projects/${pid()}/selection`, { candidateIds: [...checked], version: state.version }); }
    catch (err) { alert(err.message); }
    refresh();
  });
  const rerun = async (days) => {
    try { await api("POST", `/api/projects/${pid()}/research`, { requestId: uid(), period_days: days }); }
    catch (err) { alert(err.message); }
    checked = new Set(); refresh();
  };
  on("widen", () => rerun(30));
  on("widen2", () => rerun(30));
  on("retry", async () => {
    if (["producing", "final_review", "approved"].includes(state.stage)) {
      return revise(state.cards.filter((c) => c.status === "failed").map((c) => c.id), "", "image");
    }
    if (state.stage !== "deep_research") return rerun();
    try { await api("POST", `/api/projects/${pid()}/selection`, { candidateIds: state.selection, version: state.version }); }
    catch (err) { alert(err.message); }
    refresh();
  });
  on("approveSb", async (e) => {
    e.target.disabled = true;
    try { await api("POST", `/api/projects/${pid()}/approve`, { stage: "storyboard", version: state.version, requestId: reqIds.sb ??= uid() }); }
    catch (err) { alert(err.message); }
    refresh();
  });
  on("approveFinal", async (e) => {
    e.target.disabled = true;
    try { await api("POST", `/api/projects/${pid()}/approve`, { stage: "final", version: state.version, requestId: uid() }); }
    catch (err) { alert(err.message); }
    refresh();
  });
  const revise = async (cardIds, instruction, mode) => {
    try { await api("POST", `/api/projects/${pid()}/revisions`, { cardIds, instruction, mode, version: state.version, requestId: uid() }); }
    catch (err) { alert(err.message); return refresh(); }
    picked = new Set(); draftInstr = ""; refresh();
  };
  document.querySelectorAll(".pick").forEach((el) => (el.onchange = () => {
    el.checked ? picked.add(el.dataset.id) : picked.delete(el.dataset.id);
    updateTarget();
  }));
  const updateTarget = () => {
    const t = document.getElementById("rtarget"), b = document.getElementById("revise");
    if (t) t.innerHTML = targetText();
    if (b) b.disabled = !reviseTargets().length;
  };
  const instr = document.getElementById("instr");
  if (instr) instr.oninput = () => { draftInstr = instr.value; updateTarget(); };
  document.querySelectorAll("input[name=rmode]").forEach((el) => (el.onchange = () => { reviseMode = el.value; render(); }));
  document.querySelectorAll(".editCard").forEach((el) => (el.onclick = () => { editing = el.dataset.id; render(); }));
  on("closeEd", () => { editing = null; render(); });
  on("edPrev", () => moveEditor(-1));
  on("edNext", () => moveEditor(1));
  const scr = document.getElementById("phoneScreen");
  if (scr) {  // 휴대폰처럼 밀어서 넘기기
    let x0 = null;
    scr.ontouchstart = (e) => { x0 = e.touches[0].clientX; };
    scr.ontouchend = (e) => { if (x0 == null) return; const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 40) moveEditor(dx < 0 ? 1 : -1); x0 = null; };
  }
  on("edApply", async (e) => {
    e.target.disabled = true;
    const v = (id) => document.getElementById(id).value;
    try {
      await api("POST", `/api/projects/${pid()}/cards/${editing}/edit`, {
        title: v("edTitle"), line: v("edLine"), body: v("edBody"),
        layout: { position: v("edPos"), size: v("edSize"), shade: v("edShade"), palette: v("edPalette") }, version: state.version, requestId: uid(),
      });
    } catch (err) { alert(err.message); }
    refresh();
  });
  document.querySelectorAll(".retryCard").forEach((el) => (el.onclick = () => { el.disabled = true; revise([el.dataset.id], "", "image"); }));
  on("revise", (e) => {
    const ts = reviseTargets();
    if (reviseMode === "text" && !draftInstr.trim()) return alert("글자를 어떻게 고칠지 적어 주세요.");
    if (state.stage === "approved" && !confirm("고치면 최종 승인이 풀립니다. 계속할까요?")) return;
    e.target.disabled = true;
    revise(ts.map((c) => c.id), draftInstr, reviseMode);
  });
  on("recheck", async () => {
    try { await api("POST", `/api/projects/${pid()}/exports/check`); } catch (err) { alert(err.message); }
    refresh();
  });
  loadExports();
  on("cancel", async () => { await api("POST", `/api/runs/${state.active_run.id}/cancel`); refresh(); });
  on("answer", async (e) => {
    const sel = document.querySelector("input[name=ans]:checked");
    if (!sel) return alert("선택지를 고르세요.");
    const answer = sel.value === "__free" ? document.getElementById("free").value : sel.value;
    const q = state.question;
    reqIds[q.id] ??= uid();
    e.target.disabled = true;
    try { await api("POST", `/api/projects/${pid()}/answers`, { questionId: q.id, version: q.version, answer, requestId: reqIds[q.id] }); }
    catch (err) { alert(err.message); }
    refresh();
  });
}

window.onhashchange = () => { checked = new Set(); refresh(); };
refresh();
