"""SQLite 영속 상태. 프로젝트는 JSON 문서 하나로 저장하고 version으로 동시 변경을 막는다."""
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

# 시험할 때는 TRAVEL_DATA로 별도 폴더를 써서 실제 프로젝트를 건드리지 않는다.
# 기본은 data/travel. 예전 카드뉴스 기록(data/app.db)은 건드리지 않고 그대로 둔다.
DATA_DIR = Path(os.environ.get("TRAVEL_DATA") or Path(__file__).resolve().parent.parent / "data" / "travel").resolve()
DB_PATH = DATA_DIR / "app.db"
_lock = threading.RLock()


def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, project_id TEXT, kind TEXT,
            status TEXT, error TEXT, created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS requests (project_id TEXT, request_id TEXT, input_hash TEXT,
            response TEXT, PRIMARY KEY (project_id, request_id));
        """)


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def project_dir(pid):
    d = DATA_DIR / "projects" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_project(topic):
    pid = new_id("p")
    doc = {
        "id": pid, "topic": topic, "stage": "created",
        "version": 1, "engine": None, "session_id": None, "research": None,
        "concepts": [], "selection": [], "question": None, "answers": [],
        "guide": None, "issues": [], "revising": [], "approved": None, "export_check": None,
        "error": None, "notice": None, "created": time.time(),
    }
    with _lock, _conn() as c:
        c.execute("INSERT INTO projects VALUES (?, ?)", (pid, json.dumps(doc, ensure_ascii=False)))
    project_dir(pid)
    return doc


def get_project(pid):
    with _lock, _conn() as c:
        row = c.execute("SELECT doc FROM projects WHERE id=?", (pid,)).fetchone()
    return json.loads(row["doc"]) if row else None


def update_project(pid, fn):
    """fn(doc)로 문서를 바꾸고 version을 올린다. fn이 예외를 내면 저장하지 않는다."""
    with _lock, _conn() as c:
        doc = json.loads(c.execute("SELECT doc FROM projects WHERE id=?", (pid,)).fetchone()["doc"])
        fn(doc)
        doc["version"] += 1
        c.execute("UPDATE projects SET doc=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), pid))
    return doc


def create_run(pid, kind):
    rid = new_id("r")
    now = time.time()
    with _lock, _conn() as c:
        c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (rid, pid, kind, "running", None, now, now))
    return rid


def set_run(rid, status, error=None):
    with _lock, _conn() as c:
        c.execute("UPDATE runs SET status=?, error=?, updated=? WHERE id=?", (status, error, time.time(), rid))


def get_run(rid):
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    return dict(row) if row else None


def project_runs(pid):
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM runs WHERE project_id=? ORDER BY created DESC LIMIT 10", (pid,)).fetchall()
    return [dict(r) for r in rows]


def active_run(pid):
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE project_id=? AND status='running'", (pid,)).fetchone()
    return dict(row) if row else None


def mark_interrupted():
    """서버 시작 시 running으로 남은 작업을 중단 상태로 표시한다."""
    with _lock, _conn() as c:
        rows = c.execute("SELECT id, project_id FROM runs WHERE status='running'").fetchall()
    for r in rows:
        set_run(r["id"], "interrupted", "서버 재시작으로 중단됨")
        def f(d):
            d["error"] = {"kind": "interrupted", "message": "서버가 재시작되어 작업이 중단되었습니다.", "action": "재시도"}
            d["revising"] = []  # 고치던 쟁점은 이전 버전 그대로 남는다
        update_project(r["project_id"], f)


def remember_request(pid, request_id, payload, compute):
    """requestId 중복 처리: 같은 입력이면 저장된 응답, 다른 입력이면 conflict."""
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with _lock:
        with _conn() as c:
            row = c.execute("SELECT input_hash, response FROM requests WHERE project_id=? AND request_id=?",
                            (pid, request_id)).fetchone()
        if row:
            if row["input_hash"] != h:
                return None, "request_conflict"
            return json.loads(row["response"]), None
        resp = compute()
        with _conn() as c:
            c.execute("INSERT INTO requests VALUES (?,?,?,?)", (pid, request_id, h, json.dumps(resp, ensure_ascii=False)))
        return resp, None


def list_projects():
    """목록 화면용 요약. 최근에 만든 것부터."""
    with _lock, _conn() as c:
        rows = c.execute("SELECT doc FROM projects").fetchall()
        running = {r["project_id"] for r in c.execute("SELECT project_id FROM runs WHERE status='running'")}
    out = []
    for row in rows:
        d = json.loads(row["doc"])
        out.append({"id": d["id"], "topic": d["topic"], "stage": d["stage"], "created": d["created"],
                    "error": (d.get("error") or {}).get("kind"), "running": d["id"] in running,
                    "mock": bool((d.get("research") or {}).get("mock"))})
    return sorted(out, key=lambda x: x["created"], reverse=True)
