import os
import socket
from contextlib import asynccontextmanager
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import production, store, worker

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="카드뉴스 에이전트")


def err(status, code, message):
    raise HTTPException(status, {"code": code, "message": message})


def load(pid):
    d = store.get_project(pid)
    if not d:
        err(404, "not_found", "프로젝트가 없습니다.")
    return d


def check_version(d, version):
    if d["version"] != version:
        err(409, "stale_version", "화면이 오래되었습니다. 새로고침 후 다시 시도하세요.")


def idempotent(pid, request_id, payload, compute):
    resp, conflict = store.remember_request(pid, request_id, payload, compute)
    if conflict:
        err(409, "request_conflict", "같은 requestId로 다른 요청이 들어왔습니다.")
    return resp


@asynccontextmanager
async def lifespan(_):
    store.init()
    store.mark_interrupted()
    yield


app.router.lifespan_context = lifespan


class NewProject(BaseModel):
    topic: str
    period_days: int = 7


@app.post("/api/projects")
def create_project(body: NewProject):
    if not body.topic.strip():
        err(400, "empty_topic", "주제를 입력하세요.")
    return {"projectId": store.create_project(body.topic.strip(), body.period_days)["id"]}


@app.get("/api/projects")
def list_projects():
    return {"projects": store.list_projects()}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    d = load(pid)
    d["runs"] = store.project_runs(pid)
    d["active_run"] = store.active_run(pid)
    # 화면 표시용: 이 서버가 쓰는 조사·원고 엔진과 이미지 작업자
    d["workers"] = {"text": d["engine"] or worker.ENGINE_NAME,
                    "image": os.environ.get("CARDNEWS_IMAGE", "agy")}
    return d


class Research(BaseModel):
    requestId: str
    period_days: int | None = None


@app.post("/api/projects/{pid}/research")
def research(pid: str, body: Research):
    load(pid)

    def go():
        if store.active_run(pid):
            err(409, "already_running", "이미 진행 중인 작업이 있습니다.")

        def f(d):
            if body.period_days:
                d["period_days"] = body.period_days
            d.update(stage="researching", error=None, candidates=[], selection=[])
        store.update_project(pid, f)
        return {"runId": worker.research(pid), "status": "running"}
    return idempotent(pid, body.requestId, body.model_dump(), go)


class Selection(BaseModel):
    candidateIds: list[str] = Field(min_length=1, max_length=3)
    version: int


@app.post("/api/projects/{pid}/selection")
def selection(pid: str, body: Selection):
    d = load(pid)
    check_version(d, body.version)
    retry_deep = d["stage"] == "deep_research" and d["error"] and not store.active_run(pid)
    if d["stage"] != "selecting" and not retry_deep:
        err(409, "wrong_stage", "지금은 후보를 선택하는 단계가 아닙니다.")
    valid = {c["id"] for c in d["candidates"]}
    if not set(body.candidateIds) <= valid:
        err(400, "bad_candidate", "없는 후보가 포함되어 있습니다.")
    store.update_project(pid, lambda x: x.update(selection=body.candidateIds, stage="deep_research", error=None))
    return {"selection": body.candidateIds, "runId": worker.deep_research(pid)}


class Answer(BaseModel):
    questionId: str
    version: int
    answer: str
    requestId: str


@app.post("/api/projects/{pid}/answers")
def answers(pid: str, body: Answer):
    load(pid)

    def go():
        d = store.get_project(pid)
        q = d["question"]
        if d["stage"] != "waiting_for_user" or not q or q["id"] != body.questionId or q["version"] != body.version:
            err(409, "stale_question", "이미 답했거나 지난 질문입니다. 새로고침하세요.")
        if not body.answer.strip():
            err(400, "empty_answer", "답변을 고르거나 입력하세요.")

        def f(x):
            x["answers"].append({"questionId": q["id"], "question": q["question"], "answer": body.answer})
            x.update(question=None, stage="deep_research", error=None)
        store.update_project(pid, f)
        worker.continue_with_answer(pid, q, body.answer)
        return {"accepted": True, "stage": "deep_research"}
    return idempotent(pid, body.requestId, body.model_dump(), go)


@app.post("/api/runs/{rid}/cancel")
def cancel(rid: str):
    run = store.get_run(rid)
    if not run:
        err(404, "not_found", "작업이 없습니다.")
    if run["status"] != "running":
        return {"status": run["status"]}
    worker.cancel(rid)
    return {"status": "cancelling"}


class Approve(BaseModel):
    stage: str
    version: int
    requestId: str


@app.post("/api/projects/{pid}/approve")
def approve(pid: str, body: Approve):
    load(pid)

    def go():
        d = store.get_project(pid)
        check_version(d, body.version)
        if store.active_run(pid):
            err(409, "already_running", "이미 진행 중인 작업이 있습니다.")
        if body.stage == "storyboard":
            if d["stage"] != "storyboard_review":
                err(409, "wrong_stage", "스토리보드를 승인할 단계가 아닙니다.")
            cards = production.cards_from_storyboard(d["storyboard"])
            store.update_project(pid, lambda x: x.update(cards=cards, stage="producing", error=None, approved=None))
            return {"runId": worker.produce(pid, {c["id"]: True for c in cards})}
        if body.stage == "final":
            if d["stage"] != "final_review":
                err(409, "wrong_stage", "최종 승인할 단계가 아닙니다.")
            issues = production.blocking_issues(d)
            if issues:
                err(409, "checks_failed", "점검을 통과하지 못한 카드가 있습니다: " + " / ".join(issues))
            store.update_project(pid, lambda x: x.update(stage="approved", approved={
                "version": x["version"] + 1, "cards": {c["id"]: c["current"] for c in x["cards"]}}))
            return {"approved": True, "files": production.export(pid)}
        err(400, "bad_stage", "stage는 storyboard 또는 final 입니다.")
    return idempotent(pid, body.requestId, body.model_dump(), go)


class Revision(BaseModel):
    cardIds: list[str] = Field(min_length=1)
    instruction: str = ""
    version: int
    requestId: str


@app.post("/api/projects/{pid}/revisions")
def revisions(pid: str, body: Revision):
    load(pid)

    def go():
        d = store.get_project(pid)
        check_version(d, body.version)
        if store.active_run(pid):
            err(409, "already_running", "이미 진행 중인 작업이 있습니다.")
        if d["stage"] not in ("producing", "final_review", "approved"):
            err(409, "wrong_stage", "카드를 수정할 단계가 아닙니다.")
        ids = {c["id"] for c in d["cards"]}
        if not set(body.cardIds) <= ids:
            err(400, "bad_card", "없는 카드가 포함되어 있습니다.")

        def f(x):
            x.update(stage="producing", approved=None, error=None)  # 수정하면 최종 승인 해제
            for c in x["cards"]:
                if c["id"] in body.cardIds:
                    c.update(status="pending", error=None)
        store.update_project(pid, f)
        return {"runId": worker.revise(pid, body.cardIds, body.instruction)}
    return idempotent(pid, body.requestId, body.model_dump(), go)


class CardEdit(BaseModel):
    title: str
    line: str
    body: str = ""
    layout: dict = {}
    version: int
    requestId: str


@app.post("/api/projects/{pid}/cards/{cid}/edit")
def edit_card(pid: str, cid: str, body: CardEdit):
    """AI를 부르지 않고 글자와 배치만 바꿔 다시 그린다. 그림은 그대로 쓴다."""
    load(pid)

    def go():
        d = store.get_project(pid)
        check_version(d, body.version)
        if store.active_run(pid):
            err(409, "already_running", "이미 진행 중인 작업이 있습니다.")
        if d["stage"] not in ("producing", "final_review", "approved"):
            err(409, "wrong_stage", "카드를 고칠 단계가 아닙니다.")
        if not body.title.strip() or not body.line.strip():
            err(400, "empty_text", "제목과 핵심 문장은 비울 수 없습니다.")
        bad = {k: v for k, v in body.layout.items()
               if (k, v) not in {("position", "split"), ("position", "top"), ("position", "bottom"), ("size", "small"), ("size", "normal"),
                                 ("size", "large"), ("shade", "light"), ("shade", "normal"), ("shade", "strong"),
                                 ("position", "picture_top"), ("palette", "match"), ("palette", "dark")}}
        if bad:
            err(400, "bad_layout", f"알 수 없는 배치 값: {bad}")
        if cid not in {c["id"] for c in d["cards"]}:
            err(404, "not_found", "카드가 없습니다.")

        def f(x):
            x.update(stage="producing", approved=None, error=None)  # 고치면 최종 승인 해제
            for c in x["cards"]:
                if c["id"] == cid:
                    c.update(title=body.title.strip(), line=body.line.strip(), body=body.body.strip(),
                             layout={**c.get("layout", {}), **body.layout}, status="pending", error=None)
        store.update_project(pid, f)
        return {"runId": worker.produce(pid, {cid: False})}
    return idempotent(pid, body.requestId, body.model_dump(), go)


@app.get("/api/projects/{pid}/exports")
def exports(pid: str):
    d = load(pid)
    if not d.get("approved"):
        err(409, "not_approved", "최종 승인 후 내려받을 수 있습니다.")
    root = store.project_dir(pid)
    out = root / "export" / f"approved-v{d['approved']['version']}"
    files = [str(p.relative_to(root)) for p in sorted(out.iterdir())] if out.is_dir() else []
    return {"approvedVersion": d["approved"]["version"],
            "files": [{"name": Path(f).name, "url": f"/api/projects/{pid}/files/{f}"} for f in files]}


@app.get("/api/projects/{pid}/files/{path:path}")
def files(pid: str, path: str):
    load(pid)
    root = store.project_dir(pid).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        err(404, "not_found", "파일이 없습니다.")
    return FileResponse(target)


@app.middleware("http")
async def no_stale_static(request, call_next):
    # 앱을 고친 뒤 브라우저가 옛 화면 코드를 쓰지 않게 매번 확인하도록 한다
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def main():
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"포트 {port}가 사용 중입니다. 다른 포트로 실행하세요: .venv/bin/python -m app.main {port + 1}")
            sys.exit(1)
    print(f"열기: http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
