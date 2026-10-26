import socket
from contextlib import asynccontextmanager
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cards, guide, photos, store, worker

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="여행 구상 도우미")


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


@app.post("/api/projects")
def create_project(body: NewProject):
    if not body.topic.strip():
        err(400, "empty_topic", "여행 구상을 입력하세요.")
    return {"projectId": store.create_project(body.topic.strip())["id"]}


@app.get("/api/projects")
def list_projects():
    return {"projects": store.list_projects()}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    d = load(pid)
    stale = d.get("deck") is None or (d["deck"] and d.get("deck_version") != cards.RENDER_VERSION)
    if d.get("guide") and stale and d["stage"] != "approved" and not store.active_run(pid):
        worker.build_cards(pid)  # 카드 기능 전에 만든 해설이나 그리는 방식이 바뀐 카드도 새로 그려 보이게(승인본은 그대로)
        d = load(pid)
    if d["concepts"]:
        photos.ensure(pid, d["concepts"])  # 후보 장소 사진을 아직 안 찾았으면 백그라운드에서 찾는다
    d["photos"] = photos.view(pid, d["concepts"])
    d["runs"] = store.project_runs(pid)
    d["active_run"] = store.active_run(pid)
    d["workers"] = {"text": d["engine"] or worker.ENGINE_NAME}  # 화면 표시용: 이 서버가 쓰는 조사·해설 엔진
    return d


class Research(BaseModel):
    requestId: str


@app.post("/api/projects/{pid}/research")
def research(pid: str, body: Research):
    load(pid)

    def go():
        if store.active_run(pid):
            err(409, "already_running", "이미 진행 중인 작업이 있습니다.")
        if store.get_project(pid)["stage"] not in ("created", "researching", "selecting"):
            err(409, "wrong_stage", "개념을 고른 뒤에는 처음 조사를 다시 하지 않습니다. 새 프로젝트를 만드세요.")
        store.update_project(pid, lambda d: d.update(stage="researching", error=None, concepts=[], selection=[]))
        return {"runId": worker.research(pid), "status": "running"}
    return idempotent(pid, body.requestId, body.model_dump(), go)


class Selection(BaseModel):
    conceptIds: list[str] = Field(min_length=1, max_length=3)
    version: int


@app.post("/api/projects/{pid}/selection")
def selection(pid: str, body: Selection):
    d = load(pid)
    check_version(d, body.version)
    if store.active_run(pid):
        err(409, "already_running", "이미 진행 중인 작업이 있습니다.")
    retry = d["stage"] == "explaining" and d["error"]
    if d["stage"] != "selecting" and not retry:
        err(409, "wrong_stage", "지금은 개념을 고르는 단계가 아닙니다.")
    valid = {c["id"] for c in d["concepts"]}
    if not set(body.conceptIds) <= valid:
        err(400, "bad_concept", "없는 개념이 포함되어 있습니다.")
    store.update_project(pid, lambda x: x.update(selection=body.conceptIds, stage="explaining", error=None))
    return {"selection": body.conceptIds, "runId": worker.explain(pid)}


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
            x.update(question=None, stage="explaining", error=None)
        store.update_project(pid, f)
        worker.continue_with_answer(pid, q, body.answer)
        return {"accepted": True, "stage": "explaining"}
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


class Revision(BaseModel):
    issueIds: list[str] = Field(min_length=1)
    instruction: str
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
        if d["stage"] not in ("review", "approved"):
            err(409, "wrong_stage", "해설을 고칠 단계가 아닙니다.")
        if not set(body.issueIds) <= {i["id"] for i in d["issues"]}:
            err(400, "bad_issue", "없는 쟁점이 포함되어 있습니다.")
        if not body.instruction.strip():
            err(400, "empty_instruction", "어떻게 고칠지 적어 주세요.")
        # 고치면 최종 승인 해제
        store.update_project(pid, lambda x: x.update(stage="review", approved=None, export_check=None, error=None,
                                                     notice=None, revising=body.issueIds))
        return {"runId": worker.revise(pid, body.issueIds, body.instruction.strip())}
    return idempotent(pid, body.requestId, body.model_dump(), go)


class Approve(BaseModel):
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
        if d["stage"] != "review":
            err(409, "wrong_stage", "최종 승인할 단계가 아닙니다.")
        store.update_project(pid, lambda x: x.update(stage="approved", export_check=None, approved={
            "version": x["version"] + 1, "issues": {i["id"]: i["v"] for i in x["issues"]}}))
        cards.build(pid)  # 승인하는 내용 그대로 카드를 맞춰 둔다(바뀌지 않은 카드는 다시 그리지 않음)
        files = guide.export(pid)
        return {"approved": True, "files": files, "checkRunId": worker.verify_export(pid)}
    return idempotent(pid, body.requestId, body.model_dump(), go)


@app.post("/api/projects/{pid}/photos/retry")
def photos_retry(pid: str):
    """받지 못한 후보 사진을 다시 찾는다."""
    d = load(pid)
    photos.ensure(pid, d["concepts"], retry_failed=True)
    return {"state": "loading"}


@app.get("/api/projects/{pid}/exports")
def exports(pid: str):
    d = load(pid)
    if not d.get("approved"):
        err(409, "not_approved", "최종 승인 후 내려받을 수 있습니다.")
    root = store.project_dir(pid)
    out = root / "export" / f"approved-v{d['approved']['version']}"
    files = [str(p.relative_to(root)) for p in sorted(out.iterdir())] if out.is_dir() else []
    return {"approvedVersion": d["approved"]["version"], "check": d.get("export_check"),
            "files": [{"name": Path(f).name, "url": f"/api/projects/{pid}/files/{f}"} for f in files]}


@app.post("/api/projects/{pid}/exports/check")
def recheck(pid: str):
    """내보낸 파일 점검을 다시 한다(링크가 잠깐 안 열렸을 때 등)."""
    d = load(pid)
    if not d.get("approved"):
        err(409, "not_approved", "최종 승인 후 점검할 수 있습니다.")
    if store.active_run(pid):
        err(409, "already_running", "이미 진행 중인 작업이 있습니다.")
    store.update_project(pid, lambda x: x.update(error=None))
    return {"runId": worker.verify_export(pid)}


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
