"""실행 엔진 인터페이스. 결과는 progress / need_input / result / error 로 정규화한다.

이 규약은 이 앱이 정한 것이며 CLI 공통 규격이 아니다.
- ClaudeCliEngine: 실제 조사. `claude -p` 를 인자 배열로 실행하고 `--resume`으로 이어간다.
- MockEngine: 화면 확인용 모의 데이터(TRAVEL_ENGINE=mock).
"""
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone

from . import prompts, store

KST = timezone(timedelta(hours=9))


class Engine:
    name = "base"

    def start(self, task, context, cancel):
        """새 세션으로 작업 시작. 반환: (정규화 결과 dict, session_id)"""
        raise NotImplementedError

    def answer(self, session_id, question, answer, context, cancel):
        """같은 세션에 사용자 답변을 전달해 이어간다."""
        raise NotImplementedError

    def revise(self, session_id, issues, instruction, context, cancel):
        """고른 쟁점만 고친다."""
        raise NotImplementedError

    def cancel(self):
        pass


def _sleep(sec, cancel):
    for _ in range(int(sec * 10)):
        if cancel.is_set():
            raise InterruptedError
        time.sleep(0.1)


def _ev(claim, n, kind="여행 정보", days_ago=20, published=True):
    day = (datetime.now(KST) - timedelta(days=days_ago)).date().isoformat()
    return {"claim": f"[모의] {claim}", "url": f"https://example.com/mock/{n}", "source_title": f"[모의] 출처 {n}",
            "published": day if published else None, "source_type": kind}


class MockEngine(Engine):
    """모의 데이터. 결과에 mock=True를 붙여 화면에 반드시 표시한다."""
    name = "mock"

    def start(self, task, context, cancel):
        _sleep(1.5, cancel)
        sid = f"mock-session-{context['project_id']}"
        if task == "research":
            return {"type": "result", "mock": True, "data": self._concepts(context)}, sid
        if task == "explain":
            if not context["answers"]:
                return {"type": "need_input", "mock": True, "question": {
                    "question": "언제 떠날 생각인가요?",
                    "options": [
                        {"value": "11월 중순", "desc": "비수기라 값이 싸지만 일부 해변 시설이 문을 닫을 수 있음"},
                        {"value": "12월 말 연휴", "desc": "날씨는 비슷하지만 항공·숙소 값과 혼잡이 크게 달라짐"},
                        {"value": "아직 정하지 않음", "desc": "두 시기를 나눠 해설하고 차이를 표시함"},
                    ],
                    "allow_free_text": True, "recommended": "아직 정하지 않음",
                    "reason": "시기에 따라 비용·혼잡 쟁점의 답이 달라짐(모의 추천)",
                }}, sid
            return {"type": "result", "mock": True, "data": self._guide(context["selected"], context["answers"][-1]["answer"])}, sid
        return {"type": "error", "kind": "engine_failed", "message": f"알 수 없는 작업: {task}"}, sid

    def answer(self, session_id, question, answer, context, cancel):
        _sleep(1.5, cancel)
        return {"type": "result", "mock": True, "data": self._guide(context["selected"], answer)}

    def revise(self, session_id, issues, instruction, ctx, cancel):
        _sleep(1, cancel)
        out = []
        for i in issues:
            n = dict(i, explanation=f"[모의 수정: {instruction}] {i['explanation']}")
            if "찾" in instruction:  # 자료 더 찾기: 출처 하나를 더 붙이고 부족한 점 하나를 지운다
                n["evidence"] = i["evidence"] + [_ev("추가로 찾은 공식 안내", 90 + len(out), "공식", 3)]
                n["gaps"] = i["gaps"][1:]
                n["confidence"] = "충분" if not n["gaps"] else "일부"
            out.append(n)
        return {"type": "result", "mock": True, "data": out}

    def _concepts(self, ctx):
        now = datetime.now(KST)
        items = [
            ("겨울 햇살 아래 섬 해변 걷기", ["오키나와 본섬", "이시가키"], "따뜻한 바다를 원한 구상과 맞음"),
            ("온천 마을에 이틀 머물기", ["벳푸", "유후인"], "조용히 쉬고 싶은 구상과 맞음"),
            ("야시장 골목 미식 산책", ["타이베이", "타이난"], "걷기 좋아하는 구상과 맞음"),
            ("바닷가 작은 서점·카페 순례", ["가마쿠라"], "혼자 여행에 맞는 조용한 동선"),
            ("섬과 섬을 잇는 페리 여행", ["세토 내해"], "이동 자체가 풍경인 여행"),
            ("고성 마을 새벽 산책", ["호이안"], "날씨가 따뜻하고 걷기 좋음"),
        ]
        return {
            "searched_at": now.isoformat(),
            "conditions": {"known": [{"name": "동행", "value": "혼자"}, {"name": "기간", "value": "3박 4일"},
                                     {"name": "좋아하는 것", "value": "걷기, 조용한 곳"}],
                           "missing": ["여행 시기", "예산", "출발 공항"]},
            "concepts": [{
                "id": f"k{i+1}", "title": f"[모의] {t}", "pitch": f"[모의] {t}을 중심으로 하는 여행입니다. 실제 검색 결과가 아닙니다.",
                "places": places, "fits": fits,
                "sources": [] if i == 5 else [{"url": f"https://example.com/mock/c{i+1}", "title": f"[모의] {places[0]} 안내",
                                               "published": None if i == 4 else (now - timedelta(days=10 * i)).date().isoformat()}],
                "uncertainty": "시기에 따라 운영 여부가 다름", "gaps": ["이 개념을 뒷받침한 출처를 찾지 못함"] if i == 5 else [],
            } for i, (t, places, fits) in enumerate(items)],
        }

    def _guide(self, selected, when):
        issues = []
        for k, c in enumerate(selected):
            place = c["places"][0]
            issues += [
                {"concept_id": c["id"], "question": f"{when}의 {place} 날씨는 이 여행에 맞는가?",
                 "short_answer": "[모의] 낮 기온이 걷기에 알맞고 비 오는 날이 적은 편이다.",
                 "explanation": "[모의] 기상 기관의 평년값으로 보면 낮 기온은 걷기에 무리가 없다. 다만 바닷바람이 강한 날이 있어 겉옷이 필요하다.",
                 "evidence": [_ev("월평균 기온과 강수일", 10 * k + 1, "공식", 30), _ev("현지 체감 날씨 안내", 10 * k + 2, "여행 정보", 40)],
                 "confidence": "충분", "gaps": []},
                {"concept_id": c["id"], "question": f"{when}에 항공·숙소 비용이 크게 오르는가?",
                 "short_answer": "[모의] 판단 보류 — 올해 가격 자료를 찾지 못했다.",
                 "explanation": "[모의] 지난해 기사에서 연휴 기간 숙소 값이 오른다고 했지만 올해 가격은 확인하지 못했다.",
                 "evidence": [_ev("지난해 연휴 숙소 값 상승 기사", 10 * k + 3, "언론", 400)],
                 "confidence": "일부", "gaps": [{"what": "올해 같은 시기 항공·숙소 가격", "why": "예산이 맞는지 판단하려면 필요",
                                                "where_to_check": "항공사·숙소 예약 사이트에서 날짜를 넣어 직접 검색"}]},
                {"concept_id": c["id"], "question": f"{place}의 핵심 경험을 그 시기에 실제로 할 수 있는가?",
                 "short_answer": "[모의] 판단 보류 — 운영 기간 자료가 없다.",
                 "explanation": "[모의] 이 경험을 운영하는 곳의 공식 안내를 찾지 못했다.",
                 "evidence": [], "confidence": "부족", "gaps": [{"what": "운영 기간과 휴무일", "why": "가서 못 할 수 있음",
                                                                "where_to_check": "운영 기관 공식 사이트"}]},
            ]
        return {"guide": {"summary": f"[모의] 고른 {len(selected)}개 개념을 {when} 기준으로 비교했다. 날씨는 모두 무난하지만 비용과 운영 여부는 아직 확인되지 않았다.",
                          "overall_gaps": [{"what": "예산", "why": "비용 쟁점의 답이 예산에 따라 달라짐", "where_to_check": "직접 정하기"}],
                          "issues": issues},
                "notes": {"facts": ["[모의] 평년 기온 자료 확인"], "limits": ["[모의] 올해 가격 미확인"], "conflicts": []}}


AUTH_HINTS = ("not logged in", "/login", "please log in", "authentication", "invalid api key", "401", "oauth")


class ClaudeCliEngine(Engine):
    name = "claude_cli"
    MODEL, EFFORT = "sonnet", "medium"  # DECISIONS 10
    TIMEOUT = 15 * 60

    def __init__(self):
        self.proc = None

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)  # claude와 그 하위 프로세스까지 정리

    def _run(self, pid, prompt, schema, tools, cancel, session_id=None, label="call"):
        args = ["claude", "-p", prompt, "--output-format", "json",
                "--model", self.MODEL, "--effort", self.EFFORT,
                "--tools", *tools, "--allowedTools", *tools,
                "--json-schema", json.dumps(schema, ensure_ascii=False)]
        if session_id:
            args += ["--resume", session_id]
        cwd = store.project_dir(pid) / "engine"  # 프로젝트별 작업 디렉터리. resume도 같은 곳에서
        cwd.mkdir(exist_ok=True)
        log = store.project_dir(pid) / "logs"
        log.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            self.proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         stdin=subprocess.DEVNULL, text=True, start_new_session=True)
        except FileNotFoundError:
            return {"type": "error", "kind": "engine_failed",
                    "message": "claude 명령을 찾을 수 없습니다. Claude Code CLI를 설치하세요."}, None
        started = time.time()
        while self.proc.poll() is None:
            if cancel.is_set():
                self.cancel()
                self.proc.wait()
                raise InterruptedError
            if time.time() - started > self.TIMEOUT:
                self.cancel()
                return {"type": "error", "kind": "engine_failed", "message": "엔진 응답 시간이 15분을 넘었습니다."}, session_id
            time.sleep(0.5)
        out, err = self.proc.communicate()
        (log / f"{stamp}-{label}.json").write_text(out or "")
        if err:
            (log / f"{stamp}-{label}.stderr.txt").write_text(err)
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
        text = ((data or {}).get("result") or "") + (err or "") + ("" if data else out or "")
        if data is None or data.get("is_error") or self.proc.returncode != 0:
            if any(h in text.lower() for h in AUTH_HINTS):
                return {"type": "error", "kind": "auth_required",
                        "message": "Claude CLI 로그인이 필요합니다. 터미널에서 `claude`를 실행해 로그인한 뒤 재시도하세요."}, session_id
            return {"type": "error", "kind": "engine_failed",
                    "message": f"엔진 실행 실패(종료 코드 {self.proc.returncode}): {text.strip()[:300]}"}, session_id
        out_obj = data.get("structured_output")
        if not isinstance(out_obj, dict):
            return {"type": "error", "kind": "engine_failed",
                    "message": "엔진 출력이 약속한 JSON 형식이 아닙니다. 완료로 처리하지 않았습니다."}, data.get("session_id")
        return out_obj, data.get("session_id")

    def start(self, task, ctx, cancel):
        now = datetime.now(KST)
        if task == "research":
            out, sid = self._run(ctx["project_id"], prompts.research(ctx["topic"], now.isoformat(timespec="minutes")),
                                 prompts.RESEARCH_SCHEMA, ["WebSearch", "WebFetch"], cancel, label="research")
            if out.get("type") == "error":
                return out, sid
            if out["status"] == "search_failed":
                return _search_failed(out), sid
            concepts = [dict(c, id=f"k{i+1}") for i, c in enumerate(out["concepts"])]
            if not concepts:
                return {"type": "error", "kind": "search_failed", "message": "핵심 개념을 하나도 찾지 못했습니다. 구상을 조금 더 자세히 적어 다시 시도하세요."}, sid
            return {"type": "result", "data": {"searched_at": now.isoformat(), "conditions": out["conditions"],
                                               "concepts": concepts}}, sid
        if task == "explain":
            out, sid = self._run(ctx["project_id"], prompts.explain(
                ctx["topic"], ctx["conditions"], ctx["selected"], ctx["answers"], now.isoformat(timespec="minutes")),
                prompts.EXPLAIN_SCHEMA, ["WebSearch", "WebFetch"], cancel, label="explain")
            return _normalize_explain(out, ctx["selected"]), sid
        return {"type": "error", "kind": "engine_failed", "message": f"알 수 없는 작업: {task}"}, None

    def answer(self, session_id, question, answer, ctx, cancel):
        out, _ = self._run(ctx["project_id"], prompts.answer(question["question"], answer),
                           prompts.EXPLAIN_SCHEMA, ["WebSearch", "WebFetch"], cancel,
                           session_id=session_id, label="answer")
        return _normalize_explain(out, ctx["selected"])

    def revise(self, session_id, issues, instruction, ctx, cancel):
        out, _ = self._run(ctx["project_id"], prompts.revise(issues, instruction), prompts.REVISE_SCHEMA,
                           ["WebSearch", "WebFetch"], cancel, session_id=session_id, label="revise")
        if out.get("type") == "error":
            return out
        return {"type": "result", "data": out["issues"]}


def _search_failed(out):
    return {"type": "error", "kind": "search_failed",
            "message": f"검색에 실패했습니다: {out.get('failure_reason') or '이유 미상'}"}


def _normalize_explain(out, selected):
    if out.get("type") == "error":
        return out
    st = out.get("status")
    if st == "search_failed":
        return _search_failed(out)
    if st == "need_input" and out.get("question") and out["question"].get("options"):
        q = out["question"]
        return {"type": "need_input", "notes": out.get("notes"), "question": {
            "question": q["question"], "options": q["options"], "allow_free_text": True,
            "recommended": q.get("recommended"), "reason": q.get("reason")}}
    g = out.get("guide")
    if st == "result" and g and g.get("issues"):
        ids = {c["id"] for c in selected}
        bad = {i["concept_id"] for i in g["issues"]} - ids
        empty = [c["title"] for c in selected if not any(i["concept_id"] == c["id"] for i in g["issues"])]
        if bad or empty:
            return {"type": "error", "kind": "engine_failed", "message":
                    "해설이 고른 개념과 맞지 않습니다" + (f"(모르는 개념 {sorted(bad)})" if bad else "")
                    + (f"(쟁점이 없는 개념: {', '.join(empty)})" if empty else "") + ". 완료로 처리하지 않았습니다. 재시도하세요."}
        return {"type": "result", "data": {"guide": g, "notes": out.get("notes")}}
    return {"type": "error", "kind": "engine_failed",
            "message": "엔진 결과에 질문이나 해설이 빠져 있습니다. 완료로 처리하지 않았습니다."}


def get_engine(name):
    return {"mock": MockEngine, "claude_cli": ClaudeCliEngine}[name]()
