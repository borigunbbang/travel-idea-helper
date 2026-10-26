"""내보낸 파일 점검. 최종 승인 뒤 export 폴더를 다시 열어 누락·자료 부족 표시·링크·날짜를 확인한다.

결과는 통과(pass) / 실패(fail) / 미확인(unverified) 세 가지다. 앱이 확인할 수 없는 것은 미확인으로 남기고
사람이 볼 곳(todo)을 함께 적는다. 미확인을 통과로 세지 않는다.
"""
import json
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from . import checks, store
from .guide import LEVEL_TEXT

STATUS = {"pass": "통과", "fail": "실패", "unverified": "미확인"}
AREAS = ["누락", "카드", "자료 부족 표시", "링크", "날짜"]
# python.org판 Python은 인증서 묶음이 없어 macOS 기본 묶음을 쓴다
_SSL = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if Path("/etc/ssl/cert.pem").is_file() else None
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _open_url(url):
    """원문 링크가 열리는지만 본다. 막히거나 시간이 넘으면 '미확인'(사이트가 자동 접속을 막는 경우가 많다)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10, context=_SSL) as r:
            return r.status < 400, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return False, f"HTTP {e.code} 페이지 없음"
        return None, f"HTTP {e.code}(자동 접속 차단일 수 있음)"
    except Exception as e:
        return None, f"열지 못함({str(e)[:80]})"


def run(pid, cancel):
    d = store.get_project(pid)
    out = store.project_dir(pid) / "export" / f"approved-v{d['approved']['version']}"
    items = []

    def add(area, name, status, detail, todo=None, issue=None):
        items.append({"area": area, "issue": issue, "name": name, "status": status, "detail": detail, "todo": todo})

    # 누락: 세 파일이 열리고, 승인한 쟁점이 모두 들어 있는지
    texts = {}
    for f in ("guide.md", "guide.html", "sources.json"):
        p = out / f
        try:
            texts[f] = p.read_text()
            add("누락", f, "pass", f"{p.stat().st_size:,}바이트")
        except OSError as e:
            add("누락", f, "fail", f"파일을 열지 못함: {e}")
    try:
        src = json.loads(texts.get("sources.json", ""))
    except json.JSONDecodeError:
        src = None
        add("누락", "sources.json 형식", "fail", "JSON으로 읽히지 않음")
    approved = d["approved"]["issues"]
    for i in d["issues"]:
        miss = [f for f in ("guide.md", "guide.html") if f in texts and f"쟁점 {i['n']}." not in texts[f]]
        if src is not None and not any(x["id"] == i["id"] and x["v"] == approved.get(i["id"]) for x in src["issues"]):
            miss.append("sources.json")
        add("누락", "쟁점", "fail" if miss else "pass",
            ("빠진 파일: " + ", ".join(miss)) if miss else f"v{i['v']} 포함", issue=i["n"])

    # 카드: PNG가 열리고 1080×1350인지, 글자가 넘쳐 줄어든 곳이 없는지, ZIP에 모두 들었는지
    try:
        deck = json.loads((out / "deck.json").read_text())
    except (OSError, json.JSONDecodeError):
        deck = []
        add("카드", "deck.json", "fail", "카드 목록을 읽지 못함")
    if not deck:
        add("카드", "카드 묶음", "fail", "내보낸 카드가 없음")
    try:
        in_zip = set(zipfile.ZipFile(out / "travel-cards.zip").namelist())
    except (OSError, zipfile.BadZipFile) as e:
        in_zip = set()
        add("카드", "travel-cards.zip", "fail", f"ZIP을 열지 못함: {e}")
    for c in deck:
        try:
            with Image.open(out / c["file"]) as im:
                im.load()
                size = im.size
            bad = []
            if size != (1080, 1350):
                bad.append(f"크기 {size[0]}×{size[1]}")
            if c["overflow"]:
                bad.append("글자 넘침: " + " / ".join(c["overflow"]))
            if c["file"] not in in_zip:
                bad.append("ZIP에 없음")
            add("카드", c["file"], "fail" if bad else "pass", " · ".join(bad) or "PNG 1080×1350, 글자 넘침 없음, ZIP 포함",
                todo=f"{c['file']}을 열어 잘린 글자를 보세요" if c["overflow"] else None)
        except OSError as e:
            add("카드", c["file"], "fail", f"PNG를 열지 못함: {e}")
    if deck:
        add("카드", "휴대폰 화면 가독성", "unverified", "제목 54px·판단·부족한 자료 36px 이상, 출처·쪽수 줄은 26~30px. 실제 휴대폰에서는 보지 않음",
            todo="카드 한두 장을 휴대폰으로 옮겨 넘겨 보세요")

    # 자료 부족 표시: 근거가 모자란 쟁점이 문서에서 '자료 부족'/'근거 일부'로 보이는지 (판단을 다시 계산해 비교)
    md = texts.get("guide.md", "")
    for i in d["issues"]:
        lv = checks.assess(i)["level"]
        sec = md.split(f"### 쟁점 {i['n']}.", 1)[-1].split("### 쟁점", 1)[0] if md else ""
        shown = f"[{LEVEL_TEXT[lv]}]" in sec
        flagged = lv == "충분" or "[자료 부족]" in sec
        add("자료 부족 표시", "근거 수준", "pass" if shown and flagged else "fail",
            f"{LEVEL_TEXT[lv]}" + ("" if shown else " 표시가 문서에 없음") + ("" if flagged else " · 부족한 자료 목록이 없음"), issue=i["n"])

    # 링크: 근거 원문이 열리는지
    urls = {}
    for i in d["issues"]:
        for e in i["evidence"]:
            urls.setdefault(e["url"], []).append(i["n"])
    if cancel.is_set():
        raise InterruptedError
    if any("example.com/mock" in u for u in urls):
        for u, ns in urls.items():
            add("링크", u, "unverified", "모의 데이터 링크", todo=None, issue=ns[0])
    else:
        with ThreadPoolExecutor(8) as ex:
            res = dict(zip(urls, ex.map(_open_url, urls)))
        for u, (ok, why) in res.items():
            st = "pass" if ok else "fail" if ok is False else "unverified"
            add("링크", u, st, why, todo=None if ok else f"브라우저에서 직접 열어 보세요: {u}", issue=urls[u][0])

    # 날짜: 게시일 미확인·1년 넘은 자료는 사람이 볼 곳으로
    for i in d["issues"]:
        c = checks.assess(i)["counts"]
        if not c["evidence"]:
            add("날짜", "근거 날짜", "unverified", "근거가 없어 날짜를 볼 수 없음", todo="이 쟁점은 자료를 더 찾거나 직접 확인하세요", issue=i["n"])
        elif c["undated"] or c["old"]:
            add("날짜", "근거 날짜", "unverified", f"게시일 미확인 {c['undated']}개 · 1년 넘은 자료 {c['old']}개",
                todo=" / ".join(filter(None, [
                    "원문을 열어 게시일·수정일을 확인하세요" if c["undated"] else "",
                    "1년 넘은 자료의 가격·운영 시간이 지금도 같은지 보세요" if c["old"] else ""])), issue=i["n"])
        else:
            add("날짜", "근거 날짜", "pass", f"출처 {c['evidence']}개 모두 1년 안", issue=i["n"])

    counts = {k: sum(1 for x in items if x["status"] == k) for k in STATUS}
    rep = {"checked_at": time.time(), "version": d["approved"]["version"], "counts": counts, "items": items,
           "areas": {a: _area(items, a) for a in AREAS}}
    (out / "check_report.md").write_text(_markdown(d, rep))
    store.update_project(pid, lambda x: x.update(export_check=rep))


def _area(items, a):
    st = [x["status"] for x in items if x["area"] == a]
    return "fail" if "fail" in st else "unverified" if "unverified" in st else "pass"


def _markdown(d, rep):
    L = [f"# 내보낸 파일 점검: {d['topic']} (v{rep['version']})", "",
         f"통과 {rep['counts']['pass']} · 실패 {rep['counts']['fail']} · 미확인 {rep['counts']['unverified']}",
         "미확인은 통과가 아니다. 아래 '직접 확인할 것'을 사람이 본다.", ""]
    for a in AREAS:
        L += [f"## {a}: {STATUS[rep['areas'][a]]}", ""]
        for i in rep["items"]:
            if i["area"] == a:
                where = f"쟁점 {i['issue']} " if i["issue"] else ""
                L.append(f"- [{STATUS[i['status']]}] {where}{i['name']}: {i['detail']}")
        L.append("")
    todos = [i for i in rep["items"] if i["todo"]]
    if todos:
        L += ["## 직접 확인할 것", ""]
        L += [f"- [ ] {'쟁점 ' + str(i['issue']) + ' ' if i['issue'] else ''}{i['todo']}" for i in todos]
    return "\n".join(L)
