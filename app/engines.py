"""실행 엔진 인터페이스. 결과는 progress / need_input / result / error 로 정규화한다.

이 규약은 이 앱이 정한 것이며 CLI 공통 규격이 아니다.
- ClaudeCliEngine: 실제 조사. `claude -p` 를 인자 배열로 실행하고 `--resume`으로 이어간다.
- MockEngine: 화면 확인용 모의 데이터(CARDNEWS_ENGINE=mock).
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

    def cancel(self):
        pass


def _sleep(sec, cancel):
    for _ in range(int(sec * 10)):
        if cancel.is_set():
            raise InterruptedError
        time.sleep(0.1)


class MockEngine(Engine):
    """모의 데이터. 결과에 mock=True를 붙여 화면에 반드시 표시한다."""
    name = "mock"

    def start(self, task, context, cancel):
        _sleep(1.5, cancel)
        sid = f"mock-session-{context['project_id']}"
        if task == "research":
            return {"type": "result", "mock": True, "data": self._candidates(context)}, sid
        if task == "deep_research":
            return {"type": "need_input", "mock": True, "question": {
                "question": "어떤 독자에게 전달할까요?",
                "options": [
                    {"value": "일반 사용자", "desc": "용어를 풀어 쓰고 생활 속 변화 중심"},
                    {"value": "개발자", "desc": "API·도구·성능 차이 중심"},
                    {"value": "교육자", "desc": "수업 활용과 주의점 중심"},
                ],
                "allow_free_text": True, "recommended": "일반 사용자",
                "reason": "주제가 넓어 입문 독자에게 가장 많은 정보 가치가 있음(모의 추천)",
            }}, sid
        return {"type": "error", "kind": "engine_failed", "message": f"알 수 없는 작업: {task}"}, sid

    def answer(self, session_id, question, answer, context, cancel):
        _sleep(1.5, cancel)
        return {"type": "result", "mock": True, "data": {
            "editorial": {
                "audience": answer,
                "why": f"[모의] {answer}에게 이번 주 변화를 한눈에 정리해 줌",
                "format": "요약형 5장",
                "hooks": ["[모의] 이번 주 AI, 이것만 보면 된다", "[모의] 놓치기 쉬운 AI 변화 3가지"],
            },
            "storyboard": [
                {"role": "표지", "title": "[모의] 이번 주 AI 소식", "line": "핵심 3가지만 골랐습니다", "body": "모의 데이터로 만든 카드입니다.", "sources": [], "image_role": "주제를 비유로 소개", "image_plan": "책상 위 노트북과 신문"},
                {"role": "본문", "title": "[모의] 소식 1", "line": "한 문장 핵심", "body": "핵심을 뒷받침하는 짧은 본문입니다.", "sources": ["https://example.com/mock/1"], "image_role": "개념 설명", "image_plan": "톱니바퀴 두 개가 맞물림"},
                {"role": "본문", "title": "[모의] 소식 2", "line": "두 번째 핵심", "body": "한 장에 메시지 하나만 담습니다.", "sources": ["https://example.com/mock/2"], "image_role": "과정 도식", "image_plan": "화살표로 이어진 세 단계"},
                {"role": "본문", "title": "[모의] 소식 3", "line": "세 번째 핵심", "body": "출처가 없는 모의 카드입니다.", "sources": ["https://example.com/mock/3"], "image_role": "비교", "image_plan": "저울 양쪽의 상자"},
                {"role": "요약", "title": "[모의] 정리", "line": "다음에 확인할 것", "body": "원문을 직접 확인하세요.", "sources": [], "image_role": "행동 유도", "image_plan": "체크리스트와 연필"},
            ],
            "visual_style": {"palette": "남색 #1E2A4A, 청록 #2EC4B6, 크림 #F4EBD9", "style": "플랫 벡터 일러스트", "mood": "차분함"},
        }}


    def revise(self, session_id, cards, instruction, ctx, cancel, mode="text"):
        _sleep(1, cancel)
        if mode == "image":
            return {"type": "result", "mock": True, "data": [
                dict(c, image_plan=f"{c['image_plan']} [모의 수정: {instruction}]", regenerate_image=True) for c in cards]}
        return {"type": "result", "mock": True, "data": [
            dict(c, body=f"[모의 수정: {instruction}] {c.get('body', '')}", regenerate_image=False) for c in cards]}

    def _candidates(self, ctx):
        now = datetime.now(timezone.utc)
        items = [
            ("새 언어 모델 발표", "모델", "기업 발표 자료 기준, 독립 벤치마크 없음"),
            ("코딩 에이전트 업데이트", "개발 도구", "베타 기능, 정식 출시일 미정"),
            ("이미지 생성 연구 논문", "연구", "프리프린트, 동료 심사 전"),
            ("AI 기능이 들어간 앱 출시", "제품", "일부 국가만 제공"),
            ("AI 규제 가이드라인 초안", "정책", "초안 단계, 확정 아님"),
            ("오픈소스 모델 공개", "모델", "라이선스 조건 확인 필요"),
            ("음성 비서 기능 개선", "제품", "날짜 미확인"),
        ]
        return {
            "searched_at": now.isoformat(),
            "period_start": (now - timedelta(days=ctx["period_days"])).isoformat(),
            "period_days": ctx["period_days"],
            "candidates": [{
                "id": f"c{i+1}", "title": f"[모의] {t}", "category": cat,
                "summary": f"[모의] {t}에 대한 짧은 요약입니다. 실제 검색 결과가 아닙니다.",
                "published": None if i == 6 else (now - timedelta(days=i)).date().isoformat(),
                "event_date": None, "url": f"https://example.com/mock/{i+1}",
                "value": "독자에게 변화의 방향을 보여 줌", "uncertainty": unc, "grouped": 1 + (i % 3),
            } for i, (t, cat, unc) in enumerate(items)],
        }


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
            start = now - timedelta(days=ctx["period_days"])
            out, sid = self._run(ctx["project_id"], prompts.research(
                ctx["topic"], now.isoformat(timespec="minutes"), start.isoformat(timespec="minutes"), ctx["period_days"]),
                prompts.RESEARCH_SCHEMA, ["WebSearch", "WebFetch"], cancel, label="research")
            if out.get("type") == "error":
                return out, sid
            if out["status"] == "search_failed":
                return _search_failed(out), sid
            cands = [dict(c, id=f"c{i+1}", grouped=1 + len(c.get("related_urls") or []),
                          event_date=c.get("event_date")) for i, c in enumerate(out["candidates"])]
            return {"type": "result", "data": {
                "searched_at": now.isoformat(), "period_start": start.isoformat(),
                "period_days": ctx["period_days"], "candidates": cands}}, sid
        if task == "deep_research":
            out, sid = self._run(ctx["project_id"], prompts.deep_research(
                ctx["topic"], ctx["selected"], now.isoformat(timespec="minutes")),
                prompts.DEEP_SCHEMA, ["WebSearch", "WebFetch"], cancel, label="deep")
            return _normalize_deep(out), sid
        return {"type": "error", "kind": "engine_failed", "message": f"알 수 없는 작업: {task}"}, None

    def answer(self, session_id, question, answer, ctx, cancel):
        out, _ = self._run(ctx["project_id"], prompts.answer(question["question"], answer),
                           prompts.DEEP_SCHEMA, ["WebSearch", "WebFetch"], cancel,
                           session_id=session_id, label="answer")
        return _normalize_deep(out)


    def revise(self, session_id, cards, instruction, ctx, cancel, mode="text"):
        out, _ = self._run(ctx["project_id"], prompts.revise(cards, instruction, mode), prompts.REVISE_SCHEMA,
                           ["WebFetch"], cancel, session_id=session_id, label="revise")
        if out.get("type") == "error":
            return out
        return {"type": "result", "data": out["cards"]}


def _search_failed(out):
    return {"type": "error", "kind": "search_failed",
            "message": f"검색에 실패했습니다: {out.get('failure_reason') or '이유 미상'}"}


def _normalize_deep(out):
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
    if st == "result" and out.get("editorial") and out.get("storyboard"):
        n = len(out["storyboard"])
        if not 5 <= n <= 8:
            return {"type": "error", "kind": "engine_failed",
                    "message": f"스토리보드가 {n}장입니다(규칙: 5~8장). 완료로 처리하지 않았습니다. 재시도하세요."}
        return {"type": "result", "data": {"editorial": out["editorial"], "storyboard": out["storyboard"],
                                           "visual_style": out.get("visual_style"), "notes": out.get("notes")}}
    return {"type": "error", "kind": "engine_failed",
            "message": "엔진 결과에 질문이나 스토리보드가 빠져 있습니다. 완료로 처리하지 않았습니다."}


def get_engine(name):
    return {"mock": MockEngine, "claude_cli": ClaudeCliEngine}[name]()
