"""작업 프로세스 역할. HTTP 요청과 분리된 스레드에서 엔진을 호출하고 결과를 저장한다."""
import os
import threading
import uuid

from . import production, store, verify
from .engines import get_engine

_cancels = {}  # run_id -> (Event, Engine)
ENGINE_NAME = os.environ.get("CARDNEWS_ENGINE", "claude_cli")  # mock: 모의 데이터


def _apply(pid, res, on_result):
    """정규화된 엔진 결과를 프로젝트 상태에 반영한다."""
    t = res["type"]
    if t == "need_input":
        q = dict(res["question"], id=f"q_{uuid.uuid4().hex[:6]}", mock=res.get("mock", False))

        def f(d):
            q["version"] = d["version"] + 1  # 저장 직후 문서 version과 같음
            d.update(question=q, stage="waiting_for_user", error=None)
            if res.get("notes"):
                d["notes"] = res["notes"]
        store.update_project(pid, f)
    elif t == "result":
        store.update_project(pid, lambda d: on_result(d, res))
    else:
        store.update_project(pid, lambda d: d.update(error={
            "kind": res.get("kind", "engine_failed"), "message": res.get("message", ""), "action": "재시도"}))
    return t


def launch(pid, kind, fn):
    """작업 ID를 즉시 반환하고 백그라운드에서 fn(engine, cancel)을 실행한다."""
    rid = store.create_run(pid, kind)
    cancel = threading.Event()
    engine = get_engine(ENGINE_NAME)
    cancel.cancellables = [engine]  # 취소 시 정리할 엔진·이미지 작업자
    _cancels[rid] = cancel

    def run():
        try:
            fn(engine, cancel)
            store.set_run(rid, "done")
        except InterruptedError:
            store.set_run(rid, "cancelled")
            store.update_project(pid, lambda d: d.update(error={
                "kind": "cancelled", "message": "작업을 취소했습니다.", "action": "재시도"}))
        except Exception as e:  # 실패를 성공으로 표시하지 않는다
            store.set_run(rid, "failed", str(e))
            store.update_project(pid, lambda d: d.update(error={
                "kind": "engine_failed", "message": str(e), "action": "재시도"}))
        finally:
            _cancels.pop(rid, None)

    threading.Thread(target=run, daemon=True).start()
    return rid


def cancel(rid):
    cancel = _cancels.get(rid)
    if not cancel:
        return False
    cancel.set()
    for c in list(cancel.cancellables):
        c.cancel()  # 하위 프로세스 정리
    return True


def research(pid):
    def fn(engine, cancel):
        d = store.get_project(pid)
        res, sid = engine.start("research", {"project_id": pid, "topic": d["topic"],
                                             "period_days": d["period_days"]}, cancel)

        def ok(doc, r):
            doc.update(research=r["data"] | {"mock": r.get("mock", False)},
                       candidates=r["data"]["candidates"], stage="selecting", error=None,
                       engine=engine.name, session_id=sid)
        _apply(pid, res, ok)
    return launch(pid, "research", fn)


def deep_research(pid):
    def fn(engine, cancel):
        d = store.get_project(pid)
        chosen = [c for c in d["candidates"] if c["id"] in d["selection"]]
        res, sid = engine.start("deep_research", {"project_id": pid, "topic": d["topic"],
                                                  "period_days": d["period_days"], "selected": chosen}, cancel)
        store.update_project(pid, lambda doc: doc.update(session_id=sid, engine=engine.name))
        _apply(pid, res, _editorial)
    return launch(pid, "deep_research", fn)


def continue_with_answer(pid, question, answer):
    def fn(engine, cancel):
        d = store.get_project(pid)
        if d["engine"] != engine.name or not d["session_id"]:
            # 다른 엔진의 세션 ID는 재사용하지 않는다
            raise RuntimeError(f"엔진이 {d['engine']}에서 {engine.name}(으)로 바뀌어 세션을 이어갈 수 없습니다. 재시도하면 새 세션으로 심층 조사를 다시 합니다.")
        res = engine.answer(d["session_id"], question, answer, {"project_id": pid}, cancel)
        _apply(pid, res, _editorial)
    return launch(pid, "answer", fn)


def _editorial(doc, r):
    if r["data"].get("notes"):
        doc["notes"] = r["data"]["notes"]
    doc["visual_style"] = r["data"].get("visual_style")
    doc.update(editorial=r["data"]["editorial"], storyboard=r["data"]["storyboard"],
               stage="storyboard_review", question=None, error=None, mock=r.get("mock", False))


def produce(pid, targets):
    """targets: {card_id: 그림 재생성 여부}"""
    def fn(engine, cancel):
        production.produce(pid, targets, cancel, cancel.cancellables)
    return launch(pid, "produce", fn)


TEXT_FIELDS = ("title", "line", "body", "sources")
IMAGE_FIELDS = ("image_role", "image_plan")


def revise(pid, card_ids, instruction, mode):
    """선택한 카드만 수정한다.
    mode=text: AI가 글자만 고치고 그림은 이전 것을 그대로 쓴다.
    mode=image: 글자는 두고 그 카드의 그림만 다시 만든다. 요청이 있으면 AI가 그릴 내용을 먼저 고친다."""
    def fn(engine, cancel):
        d = store.get_project(pid)
        regenerate = mode == "image"
        targets = {c["id"]: regenerate for c in d["cards"] if c["id"] in card_ids}
        if instruction.strip():
            cards = [{k: c.get(k, "") for k in ("id", "role", *TEXT_FIELDS, *IMAGE_FIELDS)}
                     for c in d["cards"] if c["id"] in card_ids]
            res = engine.revise(d["session_id"], cards, instruction, {"project_id": pid}, cancel, mode=mode)
            if res["type"] == "error":
                _apply(pid, res, None)
                _restore(pid, card_ids)
                return
            changed = {c["id"]: c for c in res["data"] if c["id"] in card_ids}  # 대상 밖 카드는 무시
            fields = TEXT_FIELDS if mode == "text" else IMAGE_FIELDS  # 모드 밖의 필드는 AI가 바꿔도 반영하지 않는다
            missing = [cid for cid in card_ids if cid not in changed]

            def f(doc):
                for c in doc["cards"]:
                    if c["id"] in changed:
                        n = changed[c["id"]]
                        c.update({k: n[k] for k in fields if k in n})
                if missing:
                    doc["notice"] = "AI가 수정 결과를 돌려주지 않은 카드는 바꾸지 않았습니다: " + ", ".join(
                        str(c["index"]) + "번" for c in doc["cards"] if c["id"] in missing)
            store.update_project(pid, f)
            _restore(pid, missing)
            targets = {cid: regenerate for cid in changed}
        production.produce(pid, targets, cancel, cancel.cancellables)
    return launch(pid, "revise", fn)


def _restore(pid, card_ids):
    """수정하지 못한 카드는 이전 버전 그대로 완성 상태로 되돌린다."""
    def f(doc):
        for c in doc["cards"]:
            if c["id"] in card_ids and c["status"] == "pending":
                c.update(status="done" if c["current"] else "failed", error=None if c["current"] else
                         {"kind": "engine_failed", "message": "수정하지 못했습니다."})
    store.update_project(pid, f)
    production.finish(pid, keep_error=True)


def verify_export(pid):
    """최종 승인 후 내보낸 파일을 다시 열어 점검한다."""
    def fn(engine, cancel):
        verify.run(pid, cancel)
    return launch(pid, "verify", fn)
