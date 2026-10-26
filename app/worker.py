"""작업 프로세스 역할. HTTP 요청과 분리된 스레드에서 엔진을 호출하고 결과를 저장한다."""
import os
import threading
import time
import uuid

from . import cards, guide, store, verify
from .engines import get_engine

_cancels = {}  # run_id -> (Event, Engine)
ENGINE_NAME = os.environ.get("TRAVEL_ENGINE", "claude_cli")  # mock: 모의 데이터


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
    cancel.engine = engine  # 취소 시 하위 프로세스 정리
    _cancels[rid] = cancel

    def run():
        try:
            fn(engine, cancel)
            store.set_run(rid, "done")
        except InterruptedError:
            store.set_run(rid, "cancelled")
            store.update_project(pid, lambda d: d.update(revising=[], error={
                "kind": "cancelled", "message": "작업을 취소했습니다.", "action": "재시도"}))
        except Exception as e:  # 실패를 성공으로 표시하지 않는다
            store.set_run(rid, "failed", str(e))
            store.update_project(pid, lambda d: d.update(revising=[], error={
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
    cancel.engine.cancel()
    return True


def research(pid):
    def fn(engine, cancel):
        d = store.get_project(pid)
        res, sid = engine.start("research", {"project_id": pid, "topic": d["topic"]}, cancel)

        def ok(doc, r):
            doc.update(research={"searched_at": r["data"]["searched_at"], "conditions": r["data"]["conditions"],
                                 "mock": r.get("mock", False)},
                       concepts=r["data"]["concepts"], stage="selecting", error=None,
                       engine=engine.name, session_id=sid)
        _apply(pid, res, ok)
    return launch(pid, "research", fn)


def build_cards(pid):
    """해설이 바뀐 뒤 카드 묶음을 다시 만든다. 실패해도 해설은 남기고 알림만 띄운다."""
    try:
        cards.build(pid)
    except Exception as e:
        store.update_project(pid, lambda d: d.update(deck=[], notice=f"카드를 그리지 못했습니다(해설은 그대로): {e}"))


def _ctx(d):
    return {"project_id": d["id"], "topic": d["topic"], "conditions": d["research"]["conditions"],
            "answers": d["answers"], "selected": [c for c in d["concepts"] if c["id"] in d["selection"]]}


def explain(pid):
    def fn(engine, cancel):
        d = store.get_project(pid)
        res, sid = engine.start("explain", _ctx(d), cancel)
        store.update_project(pid, lambda doc: doc.update(session_id=sid, engine=engine.name))
        if _apply(pid, res, _guide) == "result":
            build_cards(pid)
    return launch(pid, "explain", fn)


def continue_with_answer(pid, question, answer):
    def fn(engine, cancel):
        d = store.get_project(pid)
        if d["engine"] != engine.name or not d["session_id"]:
            # 다른 엔진의 세션 ID는 재사용하지 않는다
            raise RuntimeError(f"엔진이 {d['engine']}에서 {engine.name}(으)로 바뀌어 세션을 이어갈 수 없습니다. 재시도하면 새 세션으로 해설을 다시 만듭니다.")
        res = engine.answer(d["session_id"], question, answer, _ctx(d), cancel)
        if _apply(pid, res, _guide) == "result":
            build_cards(pid)
    return launch(pid, "answer", fn)


def _guide(doc, r):
    if r["data"].get("notes"):
        doc["notes"] = r["data"]["notes"]
    g = r["data"]["guide"]
    doc.update(guide={"summary": g["summary"], "overall_gaps": g.get("overall_gaps", []), "made_at": time.time()},
               issues=guide.issues_from_guide(g), stage="review", question=None, error=None,
               mock=r.get("mock", False), approved=None, export_check=None, deck=None)


def revise(pid, issue_ids, instruction):
    """고른 쟁점만 새 버전으로 고친다. 나머지 쟁점은 그대로 둔다."""
    def fn(engine, cancel):
        d = store.get_project(pid)
        targets = [{"id": i["id"], **{k: i[k] for k in ("concept_id", *guide.ISSUE_FIELDS)}}
                   for i in d["issues"] if i["id"] in issue_ids]
        res = engine.revise(d["session_id"], targets, instruction, _ctx(d), cancel)
        if res["type"] == "error":
            store.update_project(pid, lambda x: x.update(revising=[]))
            _apply(pid, res, None)
            return
        changed = {i["id"]: i for i in res["data"] if i.get("id") in issue_ids}  # 대상 밖 쟁점은 무시
        missing = [n for n in issue_ids if n not in changed]

        def f(doc):
            for i in doc["issues"]:
                if i["id"] in changed:
                    guide.apply_revision(i, changed[i["id"]], instruction)
            doc.update(revising=[], error=None, notice=None if not missing else
                       "AI가 결과를 돌려주지 않은 쟁점은 바꾸지 않았습니다: " + ", ".join(
                           f"{i['n']}번" for i in doc["issues"] if i["id"] in missing))
        store.update_project(pid, f)
        build_cards(pid)
    return launch(pid, "revise", fn)


def verify_export(pid):
    """최종 승인 후 내보낸 파일을 다시 열어 점검한다."""
    def fn(engine, cancel):
        verify.run(pid, cancel)
    return launch(pid, "verify", fn)
