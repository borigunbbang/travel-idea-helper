"""내보낸 파일 점검. 최종 승인 뒤 export 폴더와 ZIP을 다시 열어 누락·글자 잘림·출처·날짜를 확인한다.

결과는 통과(pass) / 실패(fail) / 미확인(unverified) 세 가지다. 앱이 확인할 수 없는 것은 미확인으로 남기고
사람이 볼 곳(todo)을 함께 적는다. 미확인을 통과로 세지 않는다.
"""
import hashlib
import io
import json
import re
import ssl
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from PIL import Image

from . import store
from .production import known_urls, source_dates
from .render import BOLD, MEDIUM, REGULAR, H, W, _font

STATUS = {"pass": "통과", "fail": "실패", "unverified": "미확인"}
AREAS = ["누락", "글자 잘림", "출처", "날짜"]
LATEST_WORDS = ("최신", "이번 주", "이번주", "최근")
# python.org판 Python은 인증서 묶음이 없어 macOS 기본 묶음을 쓴다
_SSL = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if Path("/etc/ssl/cert.pem").is_file() else None
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def missing_glyphs(text):
    """글꼴에 없는 글자(빈 네모로 그려지는 글자)를 찾는다. 없는 글자는 글꼴의 '없음' 모양과 같게 그려진다."""
    bad = set()
    for weight in (BOLD, MEDIUM, REGULAR):
        f = _font(40, weight)
        m = f.getmask("")
        notdef = (m.size, bytes(m))
        for ch in set(text):
            if ch.isspace():
                continue
            g = f.getmask(ch)
            if (g.size, bytes(g)) == notdef:
                bad.add(ch)
    return sorted(bad)


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


def _date_mentions(text, year):
    """카드 문장 속 날짜(10월 18일, 2026-10-18)를 찾는다. 일까지 있는 것만."""
    out = []
    for m in re.finditer(r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})(?:~(\d{1,2}))?일", text):
        y = int(m.group(1) or year)
        for day in filter(None, (m.group(3), m.group(4))):
            try:
                out.append((m.group(0), date(y, int(m.group(2)), int(day))))
            except ValueError:
                pass
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", text):
        try:
            out.append((m.group(0), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            pass
    return out


def _date_in_notes(dt, facts):
    """조사 기록(사실)에 같은 날짜가 적혀 있는지. 여러 표기를 허용한다."""
    en = dt.strftime("%B") + f" {dt.day}"
    forms = [f"{dt.month}월 {dt.day}일", f"{dt.month}월{dt.day}일", dt.isoformat(), f"{dt.month}/{dt.day}",
             f"{dt.month:02d}-{dt.day:02d}", en, dt.strftime("%b") + f" {dt.day}"]
    return any(any(f in fact for f in forms) for fact in facts)


def _claims(ver):
    """원문과 대조해야 할 문장: 숫자·비율·금액·날짜가 들어간 문장(제목·핵심 문장·본문 문장 단위)."""
    sents = [ver["title"], ver["line"]] + re.split(r"(?<=[.!?])\s+", ver.get("body", ""))
    return [s.strip() for s in sents if s.strip() and re.search(r"\d", s)]


def run(pid, cancel):
    d = store.get_project(pid)
    ap = d["approved"]
    root = store.project_dir(pid)
    out = root / "export" / f"approved-v{ap['version']}"
    r = d["research"]
    searched = datetime.fromisoformat(r["searched_at"]).date()
    period_start = datetime.fromisoformat(r["period_start"]).date()
    items = []

    def add(area, name, status, detail, todo=None, card=None):
        items.append({"area": area, "card": card, "name": name, "status": status, "detail": detail, "todo": todo})

    # ── 누락: ZIP과 폴더에 카드·기록이 모두 있는지, 파일이 승인한 버전과 같은지
    expected = [f"card_{c['index']:02d}.png" for c in d["cards"]] + ["sources.md", "image_credits.json"]
    zpath = out / "cardnews.zip"
    zfiles = {}
    if not zpath.is_file():
        add("누락", "ZIP 파일", "fail", "cardnews.zip이 없습니다")
    else:
        try:
            with zipfile.ZipFile(zpath) as z:
                broken = z.testzip()
                zfiles = {n: z.read(n) for n in z.namelist()}
            add("누락", "ZIP 무결성", "fail" if broken else "pass",
                f"손상된 항목: {broken}" if broken else f"ZIP 안 파일 {len(zfiles)}개, 압축 검사 통과")
        except zipfile.BadZipFile as e:
            add("누락", "ZIP 무결성", "fail", f"ZIP을 열 수 없음: {e}")
    lost = [n for n in expected if n not in zfiles]
    extra = [n for n in zfiles if n not in expected]
    add("누락", "ZIP 구성", "fail" if lost else "pass",
        (f"빠진 파일: {', '.join(lost)}" if lost else f"카드 {len(d['cards'])}장 + sources.md + image_credits.json 모두 있음")
        + (f" / 예상 밖 파일: {', '.join(extra)}" if extra else ""))
    lost_dir = [n for n in expected if not (out / n).is_file()]
    add("누락", "개별 내려받기 파일", "fail" if lost_dir else "pass",
        f"빠진 파일: {', '.join(lost_dir)}" if lost_dir else "카드별 PNG와 기록 파일이 폴더에도 모두 있음")

    sources_md = zfiles.get("sources.md", b"").decode("utf-8", "ignore")
    try:
        credits = {e["card"]: e for e in json.loads(zfiles.get("image_credits.json", b"[]"))}
    except json.JSONDecodeError:
        credits = {}
        add("누락", "이미지 사용 기록", "fail", "image_credits.json을 읽을 수 없음")
    same_file = {}  # 카드 id -> 내보낸 PNG가 렌더링 기록을 남긴 그 파일과 같은지
    for c in d["cards"]:
        name = f"card_{c['index']:02d}.png"
        ver = c["versions"][ap["cards"][c["id"]] - 1]
        data = zfiles.get(name)
        if data is None:
            same_file[c["id"]] = False
            continue
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                ok_size = im.format == "PNG" and im.size == (W, H)
                info = f"{im.format} {im.size[0]}×{im.size[1]}"
        except Exception as e:
            add("누락", "PNG 열기", "fail", f"열 수 없음: {e}", card=c["index"])
            same_file[c["id"]] = False
            continue
        rendered = (root / ver["png"]).read_bytes()
        same_file[c["id"]] = _sha(data) == _sha(rendered)
        add("누락", "PNG 파일", "pass" if ok_size and same_file[c["id"]] else "fail",
            info + (", 승인한 v%d와 같은 파일" % ver["v"] if same_file[c["id"]] else ", 승인한 버전과 다른 파일"),
            card=c["index"])
        head = f"## {c['index']}. [{c['role']}]"
        no_src = [u for u in ver["sources"] if u not in sources_md]
        cr = credits.get(name) or {}
        lacks = [k for k in ("tool", "created", "prompt") if not cr.get(k)]
        if head not in sources_md or no_src:
            add("누락", "출처 기록", "fail", f"sources.md에 {'카드 항목' if head not in sources_md else '출처 ' + ', '.join(no_src)}이 없음", card=c["index"])
        if lacks:
            add("누락", "이미지 사용 기록", "fail", f"image_credits.json에 {', '.join(lacks)} 없음", card=c["index"])
        elif ver["image"].get("fallback_reason") and not cr.get("fallback_reason"):
            add("누락", "이미지 사용 기록", "fail", "대체 생성인데 대체 이유가 기록에 없음", card=c["index"])
    if not [i for i in items if i["area"] == "누락" and i["name"] in ("출처 기록", "이미지 사용 기록")]:
        add("누락", "출처·이미지 사용 기록", "pass", "모든 카드의 출처와 이미지 도구·만든 시각·요청문이 기록됨")

    # ── 글자 잘림: 렌더링 때 줄마다 잰 결과는 내보낸 파일이 그 파일과 같을 때만 인정한다
    for c in d["cards"]:
        ver = c["versions"][ap["cards"][c["id"]] - 1]
        if not same_file.get(c["id"]):
            add("글자 잘림", "잘림·겹침·대비", "unverified", "내보낸 PNG가 측정한 파일과 달라 측정 결과를 쓸 수 없음",
                todo="이 카드 PNG를 열어 글자가 잘렸는지 직접 보세요", card=c["index"])
            continue
        probs = ver.get("problems") or []
        m = ver.get("metrics") or {}
        if probs:
            add("글자 잘림", "잘림·겹침·대비", "fail", " / ".join(p["label"] for p in probs), card=c["index"])
        else:
            lines = m.get("lines") or {}
            add("글자 잘림", "잘림·겹침·대비", "pass",
                f"제목 {lines.get('title', '?')}줄·핵심 {lines.get('line', '?')}줄·본문 {lines.get('body', '?')}줄 모두 칸 안, "
                f"최저 대비 {m.get('min_contrast', '?')}:1" if m else "렌더링 기록에 잘림·겹침 없음", card=c["index"])
        text = " ".join([ver["title"], ver["line"], ver.get("body", ""), c["role"]])
        bad = missing_glyphs(text)
        if bad:
            add("글자 잘림", "글꼴에 없는 글자", "fail", f"빈 네모로 나올 글자: {' '.join(bad)}", card=c["index"])
    if not any(i["name"] == "글꼴에 없는 글자" for i in items):
        add("글자 잘림", "글꼴에 없는 글자", "pass", "모든 카드 글자가 글꼴에 있음")
    add("글자 잘림", "실제 휴대폰에서 보기", "unverified", "앱은 글자 칸과 대비를 계산할 뿐 사람 눈의 읽기 편함은 판단하지 못함",
        todo="ZIP 안 PNG를 휴대폰으로 옮겨 한 장씩 넘겨 보며 줄바꿈이 어색한 곳, 단어가 끊긴 곳이 없는지 보세요")

    # ── 출처: 조사 기록에 있는 URL인지, 링크가 열리는지, 문장이 원문과 맞는지
    urls = known_urls(d)
    all_urls = []
    for c in d["cards"]:
        ver = c["versions"][ap["cards"][c["id"]] - 1]
        unknown = [u for u in ver["sources"] if u not in urls]
        if c["role"] == "본문" and not ver["sources"]:
            add("출처", "근거 출처", "fail", "본문 카드에 출처가 없음", card=c["index"])
        elif unknown:
            add("출처", "근거 출처", "fail", f"조사 기록에 없는 출처: {', '.join(unknown)}", card=c["index"])
        else:
            add("출처", "근거 출처", "pass", f"출처 {len(ver['sources'])}개 모두 조사 기록에 있음" if ver["sources"]
                else f"{c['role']} 카드라 출처 없음(본문 카드만 출처 필수)", card=c["index"])
        all_urls += [u for u in ver["sources"] if u not in all_urls]
    with ThreadPoolExecutor(6) as ex:
        opened = dict(zip(all_urls, ex.map(_open_url, all_urls)))
    if cancel.is_set():
        raise InterruptedError
    for u, (ok, info) in opened.items():
        add("출처", "링크 열림", "pass" if ok else ("fail" if ok is False else "unverified"), f"{u} — {info}",
            todo=None if ok else f"브라우저에서 직접 열어 보세요: {u}")
    for c in d["cards"]:
        ver = c["versions"][ap["cards"][c["id"]] - 1]
        claims = _claims(ver)
        if claims:
            add("출처", "문장과 원문 대조", "unverified", "숫자·날짜가 든 문장: " + " / ".join(f"「{x}」" for x in claims),
                todo=f"{' / '.join(f'「{x}」' for x in claims)} — 원문과 같은지 확인: "
                     + (", ".join(ver["sources"][:2]) or "이 카드에는 출처가 없어 다른 카드의 출처로 확인"), card=c["index"])

    # ── 날짜: 카드의 조사 기준일 표시, 출처 게시일, 카드 속 날짜, '최신' 표현의 근거
    for c in d["cards"]:
        ok = same_file.get(c["id"])
        add("날짜", "조사 기준일 표시", "pass" if ok else "unverified",
            f"카드 아래에 '조사 기준 {r['searched_at'][:10]}'로 그림" if ok else "내보낸 파일이 렌더링한 파일과 달라 확인 불가",
            todo=None if ok else "카드 아래 조사 기준일을 직접 보세요", card=c["index"])
    dates = source_dates(d)
    for u in all_urls:
        pub = dates.get(u)
        if not pub:
            add("날짜", "출처 게시일", "unverified", f"{u} — 조사에서 게시일을 확인하지 못한 출처(관련 기사·심층 조사 출처)",
                todo=f"원문을 열어 게시일이 조사 기간({period_start}~{searched}) 안인지 보세요: {u}")
            continue
        p = date.fromisoformat(pub[:10])
        inside = period_start <= p <= searched
        add("날짜", "출처 게시일", "pass" if inside else "fail",
            f"{u} — 게시일 {pub}, 조사 기간 {period_start}~{searched} {'안' if inside else '밖'}")
    facts = (d.get("notes") or {}).get("facts") or []
    for c in d["cards"]:
        ver = c["versions"][ap["cards"][c["id"]] - 1]
        for raw, dt in _date_mentions(" ".join([ver["title"], ver["line"], ver.get("body", "")]), searched.year):
            if dt > searched:
                add("날짜", "카드 속 날짜", "fail", f"'{raw}'가 조사 기준일({searched}) 뒤 — 예정이라면 '예정'으로 적어야 함", card=c["index"])
            elif _date_in_notes(dt, facts):
                add("날짜", "카드 속 날짜", "pass", f"'{raw}'({dt}) 조사 기록 사실에 같은 날짜가 있음", card=c["index"])
            else:
                add("날짜", "카드 속 날짜", "unverified", f"'{raw}'({dt}) 조사 기록에서 같은 날짜를 찾지 못함",
                    todo=f"원문에서 '{raw}'가 맞는지 보세요", card=c["index"])
    latest = [c for c in d["cards"] if any(w in c["versions"][ap["cards"][c["id"]] - 1]["title"] + c["versions"][ap["cards"][c["id"]] - 1]["line"] for w in LATEST_WORDS)]
    if latest:
        backed = [u for u in all_urls if dates.get(u) and period_start <= date.fromisoformat(dates[u][:10]) <= searched]
        add("날짜", "'최신' 표현 근거", "pass" if backed else "fail",
            f"{', '.join(str(c['index']) + '번' for c in latest)} 카드에 시점 표현. "
            + (f"조사 기간 안 게시일이 확인된 출처 {len(backed)}개" if backed else "조사 기간 안 게시일이 확인된 출처가 없음"))

    counts = {k: sum(1 for i in items if i["status"] == k) for k in STATUS}
    report = {"checked_at": datetime.now().isoformat(timespec="seconds"), "approved_version": ap["version"],
              "folder": str(out.relative_to(root)), "counts": counts, "items": items}
    (out / "check_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (out / "check_report.md").write_text(_markdown(d, report))
    store.update_project(pid, lambda x: x.update(export_check=report))
    return report


def _markdown(d, rep):
    c = rep["counts"]
    lines = [f"# 내보낸 파일 점검 — {d['topic']}", "",
             f"- 점검 시각: {rep['checked_at']} · 승인 버전 v{rep['approved_version']}",
             f"- 결과: 통과 {c['pass']} · 실패 {c['fail']} · 미확인 {c['unverified']}",
             "- 미확인은 통과가 아닙니다. 아래 '직접 확인할 것'을 사람이 봐야 합니다.", ""]
    for area in AREAS:
        lines += [f"## {area}", ""]
        for i in rep["items"]:
            if i["area"] == area:
                where = f"{i['card']}번 " if i["card"] else ""
                lines.append(f"- [{STATUS[i['status']]}] {where}{i['name']}: {i['detail']}")
        lines.append("")
    todos = [i for i in rep["items"] if i["todo"]]
    if todos:
        lines += ["## 직접 확인할 것", ""]
        lines += [f"- [ ] {str(i['card']) + '번 ' if i['card'] else ''}{i['todo']}" for i in todos]
    return "\n".join(lines) + "\n"
