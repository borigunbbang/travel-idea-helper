// 프로젝트 ID는 URL 해시에 둔다. 새로고침해도 서버 상태를 다시 읽어 화면을 복원한다.
const $app = document.getElementById("app");
let state = null;
let pollTimer = null;
let lastSig = "";
let checked = new Set();   // 고른 핵심 개념
let picked = new Set();    // 고칠 쟁점
let draftInstr = "";
const reqIds = {}; // 같은 동작의 중복 클릭은 같은 requestId를 재사용

const uid = () => crypto.randomUUID();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ERR = {
  search_failed: ["검색 실패", "검색 도구 호출이 실패했거나 결과가 없습니다. 잠시 후 재시도하세요."],
  auth_required: ["로그인 필요", "터미널에서 claude를 실행해 로그인한 뒤 재시도하세요."],
  engine_failed: ["엔진 오류", "엔진 응답이 올바르지 않았습니다. 재시도하세요."],
  interrupted: ["작업 중단", "서버가 재시작되어 작업이 멈췄습니다. 재시도하세요."],
  cancelled: ["취소됨", "다시 진행하려면 재시도하세요."],
};
const LEVEL = { 충분: ["근거 충분", "lv-ok"], 일부: ["근거 일부", "lv-part"], 부족: ["자료 부족", "lv-none"] };
const fmt = (iso) => (iso ? new Date(iso).toLocaleString("ko-KR") : "미확인");
const link = (url, label) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label || url)}</a>`;

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail?.message || JSON.stringify(data.detail) || r.statusText);
  return data;
}

function pid() { return location.hash.slice(1).split("/")[0]; }
// 주소로 바로 열기: #<프로젝트>/photos(후보 사진 모음), #<프로젝트>/photos/<개념 id>(그 개념 자세히 보기)
function applyHashView() {
  const [, view, cid] = location.hash.slice(1).split("/");
  if (view === "photos") { guideView = "photos"; conceptView = cid || null; }
}

const STAGE = {
  created: "생성됨", researching: "개념 찾는 중", selecting: "개념 고르기", explaining: "해설 만드는 중",
  waiting_for_user: "질문 대기", review: "해설 검토", approved: "승인 완료",
};
const $side = document.getElementById("side");
let sideTimer = null;

async function renderSide() {
  let list;
  try { list = (await api("GET", "/api/projects")).projects; }
  catch { $side.innerHTML = ""; return; }
  const cur = pid();
  const when = (t) => new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  $side.innerHTML = `<a class="newp ${cur ? "" : "on"}" href="#">＋ 새 여행 구상</a><h3>내 구상 ${list.length}개</h3>` +
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
  const sig = JSON.stringify(state);  // 바뀐 게 없으면 다시 그리지 않는다(열어 둔 '자세히'가 접히지 않게)
  if (sig !== lastSig) { lastSig = sig; render(); }
  const busy = state.active_run || ["researching", "explaining"].includes(state.stage) && !state.error || state.photos?.state === "loading";
  clearTimeout(pollTimer);
  if (busy) pollTimer = setTimeout(refresh, 2000);
}

function renderStart() {
  $app.innerHTML = `
    <div class="box">
      <h2>어떤 여행을 구상하나요?</h2>
      <p class="muted">떠오르는 대로 적으면 실제 자료를 찾아 여행의 핵심 개념을 제안합니다. 고른 개념마다 따져볼 쟁점과 근거가 있는 해설을 만들고, 자료가 모자란 곳은 따로 표시합니다.</p>
      <form id="f">
        <textarea id="topic" rows="3" placeholder="예: 12월에 혼자 3박 4일. 따뜻하고 조용한 바닷가에서 많이 걷고 싶다" required></textarea>
        <div class="row" style="margin-top:8px"><button>핵심 개념 제안 받기</button>
        <span class="muted">시기·기간·동행·예산을 적으면 해설이 더 정확해집니다.</span></div>
      </form>
    </div>`;
  document.getElementById("f").onsubmit = async (e) => {
    e.preventDefault();
    e.submitter.disabled = true;
    try {
      const { projectId } = await api("POST", "/api/projects", { topic: document.getElementById("topic").value });
      await api("POST", `/api/projects/${projectId}/research`, { requestId: uid() });
      location.hash = projectId;
    } catch (err) { alert(err.message); e.submitter.disabled = false; }
  };
  document.getElementById("topic").focus();
}

function header() {
  const r = state.research;
  const TEXT = { claude_cli: "Claude CLI", mock: "모의 엔진" };
  const w = state.workers || {};
  let h = `<h2>${esc(state.topic)}</h2><p class="muted">단계: <b>${esc(STAGE[state.stage] || state.stage)}</b> · 조사·해설: ${esc(TEXT[w.text] || w.text || "-")}</p>`;
  if (r?.mock || state.mock || state.question?.mock) h += `<div class="mock">⚠ 모의 데이터입니다. 실제 검색 결과가 아닙니다.</div>`;
  if (r) {
    h += `<p class="muted">조사 기준 시각: ${fmt(r.searched_at)}</p>`;
    const c = r.conditions || { known: [], missing: [] };
    h += `<p class="conds">${c.known.map((k) => `<span class="tag">${esc(k.name)}: ${esc(k.value)}</span>`).join("")}`
      + c.missing.map((m) => `<span class="tag tag-gap" title="구상에 없어 정해지지 않은 조건">${esc(m)}: 정하지 않음</span>`).join("") + `</p>`;
  }
  if (state.error) {
    const [label, next] = ERR[state.error.kind] || [state.error.kind, "재시도하세요."];
    const s = state.stage;
    const retry = s === "review" ? `<span class="muted">아래 '해설 고치기'에서 요청을 다시 보내세요.</span>`
      : s === "approved" ? `<button class="secondary" id="recheck">내보낸 파일 다시 점검</button>`
      : `<button class="secondary" id="retry">재시도</button>`;
    h += `<div class="error"><b>${esc(label)}</b> — ${esc(state.error.message)}<br>
      <span class="muted">다음 행동: ${esc(next)}</span><br>${state.active_run ? "" : retry}</div>`;
  }
  if (state.notice) h += `<div class="mock">${esc(state.notice)}</div>`;
  if (state.active_run) {
    const what = { research: "자료를 찾아 핵심 개념을 고르는 중… (1~2분)", explain: "고른 개념의 쟁점과 근거를 확인하는 중… (2~5분)",
      answer: "답변을 반영해 해설을 만드는 중…", revise: "고른 쟁점을 고치는 중…", verify: "내보낸 파일 점검 중…" }[state.active_run.kind] || "작업 중…";
    h += `<p><span class="spinner"></span>${what} <button class="secondary" id="cancel">취소</button></p>`;
  }
  return h;
}

function render() {
  let body = "";
  const s = state.stage;
  if (s === "selecting") body = renderConcepts();
  else if (s === "waiting_for_user") body = renderQuestion();
  else if (["review", "approved"].includes(s)) body = renderGuide();
  else if (!state.active_run && !state.error) body = `<p class="muted">상태를 불러오는 중…</p>`;
  $app.innerHTML = header() + body;
  $app.classList.toggle("wide", ["review", "approved"].includes(s));
  bind();
}

function sourcesHtml(srcs) {
  if (!srcs.length) return `<span class="bad">확인한 출처 없음</span>`;
  return srcs.map((s) => `${link(s.url, s.title)} <span class="muted">· 게시일 ${esc(s.published || "미확인")}</span>`).join("<br>");
}

function photoHtml(place) {
  const r = state.photos?.places?.[place];
  const label = `<span class="pname">📍 ${esc(place)}</span>`;
  if (!r || r.stale) return `<figure class="ph empty">${label}<span class="pmsg"><span class="spinner"></span>사진 찾는 중</span></figure>`;
  if (r.status !== "ok") return `<figure class="ph empty">${label}<span class="pmsg">${r.status === "none" ? "쓸 수 있는 사진을 찾지 못함" : "사진을 받지 못함"}</span></figure>`;
  return `<figure class="ph"><img src="/api/projects/${pid()}/files/${r.file}" alt="${esc(place)} 사진" loading="lazy">${label}</figure>`;
}

function creditHtml(places) {
  const rs = places.map((p) => [p, state.photos?.places?.[p]]).filter(([, r]) => r?.status === "ok" && !r.stale);
  if (!rs.length) return "";
  return `<p class="credit">${rs.map(([p, r]) => `${esc(p)} → <a href="${esc(r.file_page)}" target="_blank" rel="noopener">공용 사진 '${esc(r.title || r.wiki_title)}'</a>${r.year ? ` (${r.year}년 촬영)` : ""} · ${esc(r.author)} · ${esc(r.license)}`).join("<br>")}</p>`;
}

const conceptGaps = (c) => [...c.gaps, ...(c.sources.length ? [] : ["이 개념을 뒷받침한 출처가 없다"])];
let conceptView = null;  // 사진 모음에서 연 개념 id

function galleryHtml(selectable) {
  if (conceptView) return conceptDetailHtml(selectable);
  const st = state.photos?.state;
  let h = `<p class="muted">사진은 위키미디어 공용에서 '장소 + 풍경'으로 찾은 최근 컬러 사진(자유 이용 라이선스)입니다. 장소와 정확히 같지 않을 수 있어 파일 이름과 촬영 연도를 적었습니다. 항목을 누르면 자세히 봅니다.${st === "loading" ? ' <span class="spinner"></span>사진을 차례로 찾는 중…' : ""}
    ${st !== "loading" && Object.values(state.photos?.places || {}).some((r) => r.status === "failed") ? '<button class="secondary small" id="photoRetry">못 받은 사진 다시 찾기</button>' : ""}</p><div class="gallery">`;
  for (const c of state.concepts) {
    const on = selectable ? checked.has(c.id) : state.selection.includes(c.id);
    const gaps = conceptGaps(c);
    const ps = c.places.slice(0, 3);
    h += `<${selectable ? "label" : "div"} class="ptile ${on ? "checked" : ""} ${selectable ? "" : "clickable"}" data-cid="${c.id}">
      <div class="pgrid n${ps.length}">${ps.map(photoHtml).join("")}</div>
      <div class="pbody"><div class="row">${selectable ? `<input type="checkbox" data-id="${c.id}" ${on ? "checked" : ""}>` : on ? '<span class="tag">고른 개념</span>' : ""}
        <b class="ctitle">${esc(c.title)}</b></div>
      <p>${esc(c.pitch)}</p>
      <div class="row between"><span class="muted small">출처 ${c.sources.length}개${gaps.length ? ` · <span class="warnc">부족한 자료 ${gaps.length}</span>` : ""}</span>
        <button class="secondary small openDetail" data-cid="${c.id}">자세히 보기 ›</button></div>
      </div></${selectable ? "label" : "div"}>`;
  }
  return h + `</div>`;
}

function conceptDetailHtml(selectable) {
  const cs = state.concepts;
  const k = cs.findIndex((x) => x.id === conceptView);
  const c = cs[k];
  if (!c) { conceptView = null; return galleryHtml(selectable); }
  const gaps = conceptGaps(c);
  const on = selectable ? checked.has(c.id) : state.selection.includes(c.id);
  let h = `<div class="cdetail"><div class="row between">
      <button class="secondary small" id="backGallery">← 사진 모음으로</button>
      <span class="row"><button class="secondary small" id="prevConcept" ${k ? "" : "disabled"}>‹ 이전 개념</button>
      <span class="muted small">${k + 1} / ${cs.length}</span>
      <button class="secondary small" id="nextConcept" ${k < cs.length - 1 ? "" : "disabled"}>다음 개념 ›</button></span></div>
    <div class="dphotos">${c.places.map((p) => `<div>${photoHtml(p)}${creditHtml([p])}</div>`).join("")}</div>
    <h2>${esc(c.title)}</h2>
    <div>${c.places.map((p) => `<span class="tag">📍 ${esc(p)}</span>`).join("")}</div>
    <p>${esc(c.pitch)}</p>`;
  h += selectable
    ? `<label class="opt ${on ? "rec" : ""}"><input type="checkbox" id="detailPick" ${on ? "checked" : ""}> <b>이 개념 고르기</b> <span class="muted">(${checked.size}개 선택 · 1~3개)</span></label>`
    : `<p>${on ? '<span class="tag">고른 개념</span> 이 개념으로 해설을 만들었습니다.' : '<span class="muted">고르지 않은 개념입니다.</span>'}</p>`;
  h += `<dl class="sbd"><dt>맞는 이유</dt><dd>${esc(c.fits)}</dd><dt>불확실한 점</dt><dd>${esc(c.uncertainty)}</dd><dt>출처</dt><dd>${sourcesHtml(c.sources)}</dd></dl>`;
  if (gaps.length) h += `<div class="gap"><b>⚠ 부족한 자료</b><ul>${gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul></div>`;
  const its = (state.issues || []).filter((i) => i.concept_id === c.id);
  if (!selectable && its.length) {
    h += `<h3>이 개념의 쟁점 ${its.length}개</h3><ul class="ilist">${its.map((i) => `<li><span class="lv ${LEVEL[i.check.level][1]}">${LEVEL[i.check.level][0]}</span>
      <a href="#" class="issueJump" data-iid="${i.id}">${i.n}. ${esc(i.question)}</a></li>`).join("")}</ul><p class="muted">쟁점을 누르면 그 카드로 갑니다.</p>`;
  }
  return h + `</div>`;
}

function renderConcepts() {
  const cs = state.concepts;
  if (!cs.length) return `<div class="box">제안된 개념이 없습니다. <button id="again">다시 제안 받기</button></div>`;
  let h = `<p>마음에 드는 핵심 개념을 1~3개 고르세요. 고른 개념마다 따져볼 쟁점과 해설을 만듭니다. (${checked.size}개 선택)</p>`;
  h += galleryHtml(true);
  h += `<div class="row stickybar"><button id="next" ${checked.size < 1 || checked.size > 3 ? "disabled" : ""}>고른 ${checked.size}개로 해설 만들기</button>
        <button class="secondary" id="again">다시 제안 받기</button></div>`;
  return h;
}

function renderQuestion() {
  const q = state.question;
  let h = `<div class="box"><h2>${esc(q.question)}</h2>`;
  if (q.recommended) h += `<p class="muted">추천: <b>${esc(q.recommended)}</b> — ${esc(q.reason)}<br>(추천은 자동으로 선택되지 않습니다)</p>`;
  for (const o of q.options) {
    h += `<label class="opt ${o.value === q.recommended ? "rec" : ""}"><input type="radio" name="ans" value="${esc(o.value)}"> <b>${esc(o.value)}</b> — <span class="muted">${esc(o.desc)}</span></label>`;
  }
  if (q.allow_free_text) h += `<label class="opt"><input type="radio" name="ans" value="__free"> 직접 입력 <input type="text" id="free" placeholder="선택지에 없으면 직접 적어 주세요"></label>`;
  h += `<button id="answer">답변 제출</button></div>`;
  return h;
}

function gapItems(i) {
  return [...i.gaps.map((g) => `<b>${esc(g.what)}</b> — ${esc(g.why)} <span class="muted">(확인할 곳: ${esc(g.where_to_check)})</span>`),
    ...i.check.auto_gaps.map((g) => `${esc(g)} <span class="muted">(앱 점검)</span>`)];
}

function issueHtml(i) {
  const [label, cls] = LEVEL[i.check.level];
  const busy = state.revising?.includes(i.id);
  const gaps = gapItems(i);
  let h = `<div class="box issue ${cls}" id="issue-${i.n}">
    <label class="row ihead"><input type="checkbox" class="pick" data-id="${i.id}" ${picked.has(i.id) ? "checked" : ""} ${state.active_run ? "disabled" : ""}>
      <span class="num">쟁점 ${i.n}</span><span class="lv ${cls}">${label}</span>${i.v > 1 ? `<span class="muted">v${i.v}</span>` : ""}
      ${busy ? '<span class="spinner"></span><span class="muted">고치는 중</span>' : ""}</label>
    <h3 class="q">${esc(i.question)}</h3>
    <p class="ans">${esc(i.short_answer)}</p>
    <p>${esc(i.explanation)}</p>`;
  h += i.evidence.length ? `<p class="muted"><b>근거</b></p><ul class="ev">${i.evidence.map((e) => `<li>${esc(e.claim)}<br>
      ${link(e.url, e.source_title)} <span class="muted">· ${esc(e.source_type)} · 게시일 ${esc(e.published || "미확인")}</span></li>`).join("")}</ul>`
    : `<p class="bad">근거: 확인한 출처가 없습니다.</p>`;
  if (gaps.length) h += `<div class="gap"><b>⚠ 부족한 자료</b><ul>${gaps.map((g) => `<li>${g}</li>`).join("")}</ul>
    ${state.active_run ? "" : `<button class="secondary small more" data-id="${i.id}">이 쟁점 자료 더 찾기</button>`}</div>`;
  if (i.check.ai_level !== i.check.level) h += `<p class="muted">AI 판단은 '${esc(i.check.ai_level)}'이지만, 붙은 출처로 보면 '${esc(i.check.level)}'까지만 인정합니다.</p>`;
  if (i.history.length) {
    const p = i.history[i.history.length - 1];
    h += `<details class="cardtext"><summary>이전 버전 v${p.v}과 비교${i.instruction ? ` — 요청: ${esc(i.instruction)}` : ""}</summary>
      <dl class="sbd"><dt>판단</dt><dd><del>${esc(p.short_answer)}</del></dd><dt>근거 수준</dt><dd>${esc(LEVEL[p.check.level][0])} → ${label}</dd>
      <dt>출처 수</dt><dd>${p.evidence.length} → ${i.evidence.length}</dd></dl></details>`;
  }
  return h + `</div>`;
}

let curCard = 0;
let curKey = "";
const cardKey = (c) => c.kind + (c.issue_id || c.concept_id || "");

function levelsHtml() {
  const n = (lv) => state.issues.filter((i) => i.check.level === lv);
  const weak = [...n("부족"), ...n("일부")];
  return `<p class="levels"><span class="lv lv-ok">근거 충분 ${n("충분").length}</span> <span class="lv lv-part">근거 일부 ${n("일부").length}</span> <span class="lv lv-none">자료 부족 ${n("부족").length}</span>
    <span class="muted">쟁점 ${state.issues.length}개</span></p>`
    + (weak.length ? `<p class="muted">근거가 모자란 쟁점: ${weak.map((i) => `<a href="#" class="jump" data-n="${i.n}">${i.n}번</a>`).join(" · ")}</p>` : "")
    + `<p class="muted">근거 수준은 AI의 판단과 실제로 붙은 출처(개수, 공식 자료 여부) 중 낮은 쪽입니다.</p>`;
}

function cardDetail(c) {
  let h = c.overflow?.length ? `<div class="error">이 카드는 글자가 넘쳐 줄였습니다: ${esc(c.overflow.join(" / "))}. 전체 내용은 아래 글로 보세요.</div>` : "";
  const g = state.guide;
  if (c.kind === "cover") return h + `<div class="box"><h2>이 구상의 카드</h2>${levelsHtml()}</div>`;
  if (c.kind === "overview") return h + `<div class="box"><h2>한눈에 보기</h2><p>${esc(g.summary)}</p>${state.answers.map((a) => `<p class="muted">받은 답변: ${esc(a.question)} → ${esc(a.answer)}</p>`).join("")}</div>` + renderNotes();
  if (c.kind === "concept") {
    const k = state.concepts.find((x) => x.id === c.concept_id);
    return h + `<div class="box"><h2>${esc(k.title)}</h2><div>${k.places.map((p) => `<span class="tag">📍 ${esc(p)}</span>`).join("")}</div><p>${esc(k.pitch)}</p>
      <dl class="sbd"><dt>맞는 이유</dt><dd>${esc(k.fits)}</dd><dt>불확실한 점</dt><dd>${esc(k.uncertainty)}</dd><dt>출처</dt><dd>${sourcesHtml(k.sources)}</dd></dl></div>`;
  }
  if (c.kind === "issue") return h + issueHtml(state.issues.find((i) => i.id === c.issue_id));
  const weak = state.issues.filter((i) => i.check.level !== "충분");
  return h + (g.overall_gaps?.length ? `<div class="gap"><b>⚠ 전체적으로 부족한 자료</b><ul>${g.overall_gaps.map((x) => `<li><b>${esc(x.what)}</b> — ${esc(x.why)} <span class="muted">(확인할 곳: ${esc(x.where_to_check)})</span></li>`).join("")}</ul></div>` : "")
    + `<div class="box"><h2>근거가 모자란 쟁점 ${weak.length}개</h2><ul>${weak.map((i) => `<li><a href="#" class="jump" data-n="${i.n}">${i.n}번</a> ${esc(i.question)} <span class="muted">(${esc(LEVEL[i.check.level][0])})</span></li>`).join("")}</ul></div>`;
}

function goCard(k) {
  const deck = state.deck || [];
  if (k < 0 || k >= deck.length) return;
  curKey = cardKey(deck[k]);
  render();
}

let guideView = "cards";

function renderGuide() {
  const g = state.guide;
  const tabs = `<div class="tabs"><button class="${guideView === "cards" ? "" : "secondary"}" id="viewCards">카드로 보기</button>
    <button class="${guideView === "photos" ? "" : "secondary"}" id="viewPhotos">후보 사진 한눈에 보기</button></div>`;
  if (guideView === "photos") return tabs + galleryHtml(false);
  const issues = state.issues;
  const n = (lv) => issues.filter((i) => i.check.level === lv);
  let h = "";
  const deck = state.deck || [];
  h += tabs;
  if (deck.length) {
    curCard = Math.min(Math.max(0, deck.findIndex((c) => cardKey(c) === curKey)), deck.length - 1);
    const c = deck[curCard];
    const src = (x) => `/api/projects/${pid()}/files/${x.png}`;
    const LVC = { 충분: "lv-ok", 일부: "lv-part", 부족: "lv-none" };
    h += `<div class="deckwrap"><div class="viewer-col">
      <div class="viewer"><button class="nav" id="prevCard" ${curCard ? "" : "disabled"} aria-label="이전 카드">‹</button>
        <a href="${src(c)}" target="_blank"><img id="cardImg" src="${src(c)}" alt="${curCard + 1}번째 카드"></a>
        <button class="nav" id="nextCard" ${curCard < deck.length - 1 ? "" : "disabled"} aria-label="다음 카드">›</button></div>
      <p class="muted center">${curCard + 1} / ${deck.length} · ← → 키나 밀어서 넘기기</p>
      <div class="thumbs">${deck.map((x, k) => `<img class="thumb ${k === curCard ? "on" : ""} ${x.level ? LVC[x.level] : ""}" data-k="${k}" src="${src(x)}" alt="${k + 1}" loading="lazy">`).join("")}</div>
    </div><div class="detail">${cardDetail(c)}</div></div>`;
  } else {
    h += `<p class="muted"><span class="spinner"></span>카드를 그리는 중…</p>`;
  }
  h += `<details class="box"><summary>전체 글로 보기 (쟁점 ${issues.length}개 한 번에)</summary>`;
  for (const c of state.concepts.filter((c) => state.selection.includes(c.id))) {
    h += `<section class="concept"><h2>${esc(c.title)}</h2>`;
    h += issues.filter((i) => i.concept_id === c.id).map(issueHtml).join("") + `</section>`;
  }
  h += `</details>`;
  if (state.stage === "approved") h += `<div class="box"><h2>최종 승인됨</h2><div id="dl" class="muted">내려받기 목록을 불러오는 중…</div></div>`;
  h += renderNotes();
  if (!state.active_run) {
    h += `<div class="box" id="revbox"><h2>해설 고치기</h2>
      <p class="muted">쟁점을 체크하거나 요청에 '3번'처럼 번호를 적으세요. 고르지 않은 쟁점은 그대로 둡니다.${state.stage === "approved" ? " <b>고치면 최종 승인이 풀려 다시 승인해야 합니다.</b>" : ""}</p>
      <textarea id="instr" rows="2" placeholder="예: 3번 쟁점의 공식 자료를 더 찾아 줘 / 5번 해설을 두 문장으로 줄여 줘">${esc(draftInstr)}</textarea>
      <p class="muted" id="rtarget">${targetText()}</p>
      <div class="row"><button class="secondary" id="revise" ${reviseTargets().length ? "" : "disabled"}>고치기</button>
      ${state.stage === "review" ? `<button id="approveFinal">최종 승인하고 내보내기</button>` : ""}</div></div>`;
  }
  return h;
}

function numbersInText(t) {
  return [...t.matchAll(/(\d+)\s*번/g)].map((m) => +m[1]).filter((n) => n >= 1 && n <= state.issues.length);
}

function reviseTargets() {  // 체크한 쟁점이 없으면 요청 속 'N번'을 대상으로
  if (picked.size) return state.issues.filter((i) => picked.has(i.id));
  const ns = numbersInText(draftInstr);
  return state.issues.filter((i) => ns.includes(i.n));
}

function targetText() {
  const ts = reviseTargets();
  if (!ts.length) return "대상: 아직 없음 (쟁점을 체크하거나 요청에 번호를 적으세요)";
  const other = picked.size ? numbersInText(draftInstr).filter((n) => !ts.some((i) => i.n === n)) : [];
  return `대상: 쟁점 ${ts.map((i) => i.n + "번").join(", ")} · 이 쟁점만 새 버전`
    + (other.length ? ` <span class="bad">(요청에 적힌 ${other.join(", ")}번은 체크되지 않아 고치지 않습니다)</span>` : "");
}

function renderNotes() {
  const n = state.notes;
  if (!n) return "";
  const sec = (t, xs) => (xs?.length ? `<p><b>${t}</b></p><ul>${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");
  return `<details class="box"><summary>조사 기록 (확인한 사실·한계·출처끼리 다른 내용)</summary>${sec("확인한 사실", n.facts)}${sec("한계", n.limits)}${sec("출처끼리 다른 내용", n.conflicts)}</details>`;
}

async function loadExports() {
  const el = document.getElementById("dl");
  if (!el) return;
  try {
    const r = await api("GET", `/api/projects/${pid()}/exports`);
    const NAME = { "travel-cards.zip": "카드 전체 ZIP (PNG + 해설·근거)", "guide.html": "해설 문서 (HTML)", "guide.md": "해설 문서 (Markdown)", "sources.json": "근거·부족한 자료 기록 (JSON)", "check_report.md": "점검 보고서" };
    const pngs = r.files.filter((f) => /^card-.*\.png$/.test(f.name));
    const docs = r.files.filter((f) => NAME[f.name]);
    el.innerHTML = `<p>승인 버전 v${r.approvedVersion}</p><ul>${docs.map((f) => `<li><a href="${f.url}" download>⬇ ${esc(NAME[f.name])}</a> <span class="muted">${esc(f.name)}</span></li>`).join("")}</ul>`
      + (pngs.length ? `<p><b>카드 PNG ${pngs.length}장</b> ${pngs.map((f, k) => `<a href="${f.url}" download>${k + 1}</a>`).join(" · ")}</p>` : "") + checkHtml(r.check);
    const b = document.getElementById("recheck2");
    if (b) b.onclick = async () => { try { await api("POST", `/api/projects/${pid()}/exports/check`); } catch (err) { alert(err.message); } refresh(); };
  } catch (e) { el.textContent = e.message; }
}

const ST = { pass: ["통과", "ok"], fail: ["실패", "bad"], unverified: ["미확인", "warn"] };

function checkHtml(rep) {
  if (!rep) return state.active_run ? `<p class="muted"><span class="spinner"></span>내보낸 파일을 다시 열어 점검하는 중…</p>` : "";
  const c = rep.counts;
  let h = `<h3>내보낸 파일 점검</h3><p>통과 <b class="okc">${c.pass}</b> · 실패 <b class="bad">${c.fail}</b> · 미확인 <b class="warnc">${c.unverified}</b>
    <span class="muted">(${new Date(rep.checked_at * 1000).toLocaleString("ko-KR")}) 미확인은 통과가 아닙니다.</span> ${state.active_run ? "" : `<button class="secondary" id="recheck2">다시 점검</button>`}</p>`;
  for (const area of ["누락", "카드", "자료 부족 표시", "링크", "날짜"]) {
    const its = rep.items.filter((i) => i.area === area);
    const n = (k) => its.filter((i) => i.status === k).length;
    h += `<details class="cardtext" ${n("fail") ? "open" : ""}><summary>${area} — 통과 ${n("pass")} · 실패 ${n("fail")} · 미확인 ${n("unverified")}</summary><ul class="checks">`
      + its.map((i) => `<li class="${ST[i.status][1]}">[${ST[i.status][0]}] ${i.issue ? "쟁점 " + i.issue + " " : ""}${esc(i.name)}: ${esc(i.detail)}</li>`).join("") + `</ul></details>`;
  }
  const todos = rep.items.filter((i) => i.todo);
  if (todos.length) h += `<h3>직접 확인할 것 (${todos.length})</h3><ol class="todo">${todos.map((i) => `<li>${i.issue ? "쟁점 " + i.issue + " " : ""}${esc(i.todo)}</li>`).join("")}</ol>`;
  return h;
}

function bind() {
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  document.querySelectorAll(".ptile input").forEach((el) => (el.onchange = () => {
    el.checked ? checked.add(el.dataset.id) : checked.delete(el.dataset.id);
    render();
  }));
  on("next", async (e) => {
    e.target.disabled = true;
    try { await api("POST", `/api/projects/${pid()}/selection`, { conceptIds: [...checked], version: state.version }); }
    catch (err) { alert(err.message); }
    refresh();
  });
  const rerun = async () => {
    try { await api("POST", `/api/projects/${pid()}/research`, { requestId: uid() }); }
    catch (err) { alert(err.message); }
    checked = new Set(); refresh();
  };
  on("again", rerun);
  on("retry", async () => {
    if (state.stage !== "explaining") return rerun();
    try { await api("POST", `/api/projects/${pid()}/selection`, { conceptIds: state.selection, version: state.version }); }
    catch (err) { alert(err.message); }
    refresh();
  });
  on("approveFinal", async (e) => {
    const weak = state.issues.filter((i) => i.check.level !== "충분").length;
    if (weak && !confirm(`근거가 모자란 쟁점이 ${weak}개 있습니다. 문서에 '자료 부족' 표시를 그대로 남기고 승인할까요?`)) return;
    e.target.disabled = true;
    try { await api("POST", `/api/projects/${pid()}/approve`, { version: state.version, requestId: reqIds.final ??= uid() }); }
    catch (err) { alert(err.message); }
    reqIds.final = undefined;
    refresh();
  });
  const revise = async (issueIds, instruction) => {
    if (state.stage === "approved" && !confirm("고치면 최종 승인이 풀립니다. 계속할까요?")) return;
    try { await api("POST", `/api/projects/${pid()}/revisions`, { issueIds, instruction, version: state.version, requestId: uid() }); }
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
  on("revise", (e) => {
    if (!draftInstr.trim()) return alert("어떻게 고칠지 적어 주세요.");
    e.target.disabled = true;
    revise(reviseTargets().map((i) => i.id), draftInstr);
  });
  document.querySelectorAll(".more").forEach((el) => (el.onclick = () => {
    el.disabled = true;
    revise([el.dataset.id], "부족한 자료를 공식 자료 위주로 더 찾아 근거를 채워 줘. 찾지 못한 것은 지어내지 말고 부족한 자료로 남겨 줘.");
  }));
  document.querySelectorAll(".jump").forEach((el) => (el.onclick = (e) => {
    e.preventDefault();
    const iss = state.issues.find((i) => i.n === +el.dataset.n);
    goCard((state.deck || []).findIndex((c) => c.issue_id === iss?.id));
  }));
  const openConcept = (id) => { conceptView = id; render(); window.scrollTo(0, $app.offsetTop); };
  document.querySelectorAll(".openDetail").forEach((el) => (el.onclick = (e) => { e.preventDefault(); e.stopPropagation(); openConcept(el.dataset.cid); }));
  document.querySelectorAll(".ptile.clickable").forEach((el) => (el.onclick = (e) => { if (!e.target.closest("a")) openConcept(el.dataset.cid); }));
  on("backGallery", () => { const id = conceptView; conceptView = null; render(); document.querySelector(`.ptile[data-cid="${id}"]`)?.scrollIntoView({ block: "center" }); });
  const stepConcept = (d) => { const k = state.concepts.findIndex((x) => x.id === conceptView) + d; if (state.concepts[k]) openConcept(state.concepts[k].id); };
  on("prevConcept", () => stepConcept(-1));
  on("nextConcept", () => stepConcept(1));
  const dp = document.getElementById("detailPick");
  if (dp) dp.onchange = () => { dp.checked ? checked.add(conceptView) : checked.delete(conceptView); render(); };
  document.querySelectorAll(".issueJump").forEach((el) => (el.onclick = (e) => {
    e.preventDefault();
    guideView = "cards"; conceptView = null;
    goCard((state.deck || []).findIndex((c) => c.issue_id === el.dataset.iid));
  }));
  on("viewCards", () => { guideView = "cards"; render(); });
  on("viewPhotos", () => { guideView = "photos"; conceptView = null; render(); });
  on("photoRetry", async () => { try { await api("POST", `/api/projects/${pid()}/photos/retry`); } catch (err) { alert(err.message); } refresh(); });
  on("prevCard", () => goCard(curCard - 1));
  on("nextCard", () => goCard(curCard + 1));
  document.querySelectorAll(".thumb").forEach((el) => (el.onclick = () => goCard(+el.dataset.k)));
  const img = document.getElementById("cardImg");
  if (img) {  // 휴대폰처럼 밀어서 넘기기
    let x0 = null;
    img.ontouchstart = (e) => { x0 = e.touches[0].clientX; };
    img.ontouchend = (e) => { if (x0 == null) return; const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 40) goCard(curCard + (dx < 0 ? 1 : -1)); x0 = null; };
  }
  document.querySelector(".thumb.on")?.scrollIntoView({ block: "nearest", inline: "center" });
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

document.addEventListener("keydown", (e) => {
  if (!state?.deck?.length || !["review", "approved"].includes(state.stage) || /INPUT|TEXTAREA/.test(document.activeElement?.tagName)) return;
  if (e.key === "ArrowLeft") goCard(curCard - 1);
  if (e.key === "ArrowRight") goCard(curCard + 1);
});

window.onhashchange = () => { lastSig = ""; guideView = "cards"; conceptView = null; applyHashView(); checked = new Set(); picked = new Set(); draftInstr = ""; curKey = ""; refresh(); };
applyHashView();
refresh();
