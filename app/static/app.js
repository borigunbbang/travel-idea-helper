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
    h += `<div class="error"><b>${esc(label)}</b> — ${esc(state.error.message)}<br>
      <span class="muted">다음 행동: ${esc(next)}</span><br>
      <button class="secondary" id="retry">재시도</button></div>`;
  }
  if (state.active_run) h += `<p><span class="spinner"></span>작업 중… <button class="secondary" id="cancel">취소</button></p>`;
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

const CHECK = { file: "파일", overflow: "글자 잘림", contrast: "대비", phone: "휴대폰 가독성", sources: "사실/출처" };
let picked = new Set();

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
    if (cur) {
      h += `<ul class="checks">${Object.entries(cur.checks).map(([k, v]) => `<li class="${v.ok ? "ok" : "bad"}">${v.ok ? "✓" : "✗"} ${CHECK[k] || k}: ${esc(v.detail)}</li>`).join("")}</ul>`;
      h += `<p class="muted">이미지: ${esc(cur.image.tool)} · ${esc(cur.image.note || "")}</p>`;
      if (cur.image.fallback_reason) h += `<p class="error" style="padding:6px 8px">대체 생성 — ${esc(cur.image.fallback_reason)}</p>`;
    }
    h += `</div>`;
  }
  h += `</div>`;
  if (!state.active_run) {
    h += `<div class="box"><h2>부분 수정</h2><p class="muted">고칠 카드를 체크하고 요청을 적으세요. 체크하지 않은 카드는 바뀌지 않습니다. 요청을 비우면 그림만 다시 만듭니다.</p>
      <textarea id="instr" rows="2" placeholder="예: 제목을 더 짧게, 본문에 패치 버전 강조"></textarea>
      <div class="row" style="margin-top:8px"><button class="secondary" id="revise" ${picked.size ? "" : "disabled"}>선택한 ${picked.size}장 수정</button>
      ${s === "final_review" ? `<button id="approveFinal">최종 승인</button>` : ""}</div></div>`;
  }
  return h;
}

async function loadExports() {
  const el = document.getElementById("dl");
  if (!el) return;
  try {
    const r = await api("GET", `/api/projects/${pid()}/exports`);
    el.innerHTML = `<p>승인 버전 v${r.approvedVersion}</p><ul>${r.files.map((f) => `<li><a href="${f.url}" download>${esc(f.name)}</a></li>`).join("")}</ul>`;
  } catch (e) { el.textContent = e.message; }
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
  const revise = async (cardIds, instruction) => {
    try { await api("POST", `/api/projects/${pid()}/revisions`, { cardIds, instruction, version: state.version, requestId: uid() }); }
    catch (err) { alert(err.message); }
    picked = new Set(); refresh();
  };
  document.querySelectorAll(".pick").forEach((el) => (el.onchange = () => {
    el.checked ? picked.add(el.dataset.id) : picked.delete(el.dataset.id);
    const b = document.getElementById("revise");
    if (b) { b.disabled = !picked.size; b.textContent = `선택한 ${picked.size}장 수정`; }
  }));
  document.querySelectorAll(".retryCard").forEach((el) => (el.onclick = () => { el.disabled = true; revise([el.dataset.id], ""); }));
  on("revise", (e) => { e.target.disabled = true; revise([...picked], document.getElementById("instr").value); });
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
