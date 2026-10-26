"""후보 여행지 사진. 장소의 위키미디어 공용 분류(없으면 '장소 이름 + 풍경' 검색)에서 최근 컬러 풍경 사진을 고른다.

- 사진은 AI 생성이 아니라 실제 사진이다. 다만 검색으로 고른 사진이라 그 장소와 정확히 같지 않을 수 있어
  공용 파일 이름(title)과 촬영 연도를 함께 보여 준다.
- 자유 이용 라이선스만, 지도·로고·SVG, 2000년 이전 촬영, 흑백·세피아(채도가 낮은) 사진은 쓰지 않는다.
- 작가·라이선스·원본 주소를 기록하고 화면에 표시한다.
- 결과는 프로젝트 문서가 아니라 photos.json에 따로 둔다. 사진을 받는 동안 문서 version이 올라가
  화면의 선택 요청이 '오래된 화면'으로 거절되지 않게 하기 위해서다.
"""
import hashlib
import html
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from PIL import Image

from . import store
from .verify import _SSL

UA = "travel-idea-helper/0.1 (local study app; Wikipedia API)"
GENERIC = {"일대", "일원", "주변", "등", "전국", "지역", "남부", "북부", "동부", "서부", "해안", "해변", "도심", "시내", "섬"}
PROVINCES = {"경북", "경남", "전북", "전남", "충북", "충남", "강원", "경기", "제주", "필리핀", "일본", "베트남", "대만"}
NOT_PHOTO = re.compile(r"\.svg$|map|locator|location|지도|위치|logo|로고|flag|emblem", re.I)
_running = set()
_lock = threading.Lock()


def _get(url, timeout=12):
    """위키미디어는 한꺼번에 많이 부르면 429로 막는다. 한 번에 하나씩, 막히면 기다렸다가 다시 부른다."""
    for wait in (2, 5, 10, None):
        time.sleep(0.3)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None:
                raise
            ra = e.headers.get("Retry-After")
            time.sleep(min(int(ra) if ra and ra.isdigit() else wait, wait))  # 서버가 긴 시간을 말해도 오래 기다리지 않는다


def _api(host, **params):
    q = urllib.parse.urlencode({"format": "json", **params})
    return json.loads(_get(f"https://{host}/w/api.php?{q}"))


def queries(place):
    """'오키나와 나하·남부 해안', '전국 템플스테이 사찰(지역은 …)' 같은 이름에서 검색어 몇 개를 만든다."""
    base = re.sub(r"\(.*?\)", " ", place)
    parts = [p.strip() for p in re.split(r"[·/,]", base) if p.strip()]
    out = [" ".join(base.replace("·", " ").split())]
    if parts:
        out.append(parts[0])
        words = [w for w in parts[0].split() if w not in GENERIC]
        out.append(" ".join(words))
        out += [w for w in sorted(words, key=len, reverse=True) if w not in PROVINCES and len(w) >= 2][:2]
    seen, res = set(), []
    for q in out:
        if q and q not in seen:
            seen.add(q)
            res.append(q)
    return res[:5]


PHOTO_METHOD = 4  # 1: 위키백과 대표 사진, 2~3: 공용 검색, 4: 장소의 공용 분류에서 최근 컬러 사진(없으면 공용 검색). 바뀌면 다시 찾는다
SCENIC = re.compile(r"풍경|전경|경치|view|landscape|panorama|scenery|beach|해변|해수욕장|바다|coast|해안|mountain|숲|forest|"
                    r"일출|sunrise|sunset|일몰|온천|hot.?spring|trail|island|lake|호수|valley|계곡|temple|사찰|템플스테이|village|마을|숲길", re.I)
UNSCENIC = re.compile(r"\bbus\b|버스|school|학교|station|sign|표지|marker|interior|내부|museum|박물관|city hall|시청|청사|office|statue|동상|"
                      r"좌상|입상|불상|비석|현판|탁본|유물|document|plan|도면|ticket|portrait|logo|menu|receipt|인물|선수|의원|politician|"
                      r"player|singer|가수|배우|actor|actress|member|기자회견|press|conference|award|시상|wedding|결혼|\bcar\b|vehicle|"
                      r"train|열차|무궁화호|KTX|aircraft|항공기|비행장|airfield|military|군사|food|음식", re.I)
IIPROPS = {"prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 900,
           "iiextmetadatafilter": "DateTimeOriginal|LicenseShortName|LicenseUrl|Artist|ImageDescription"}


def _year(meta):
    m = re.search(r"(19|20)\d\d", meta.get("DateTimeOriginal", {}).get("value", ""))
    return int(m.group(0)) if m else None


def _rank(pages, need_scenic):
    """사진 파일 목록을 거르고 점수 순으로 돌려준다.
    거름: JPEG·폭 800px 이상·자유 라이선스·지도/로고 아님·2000년 이후 촬영·사람/탈것/건물/유물 사진 아님
    (공용 검색 결과는 풍경 낱말도 있어야 함). 점수: 목록 순서 + 풍경 낱말 + 최근 촬영 + 가로 사진."""
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        m = ii.get("extmetadata", {})
        val = lambda k: html.unescape(re.sub(r"<[^>]+>", "", m.get(k, {}).get("value", ""))).strip()
        lic, title = val("LicenseShortName"), p["title"].removeprefix("File:")
        text = f"{title} {val('ImageDescription')[:300]}"
        if ii.get("mime") != "image/jpeg" or not ii.get("thumburl") or ii.get("width", 0) < 800:
            continue
        if not lic or re.search(r"non-free|fair use|copyrighted", lic, re.I) or NOT_PHOTO.search(title) or UNSCENIC.search(text):
            continue
        year = _year(m)
        if year and year < 2000:  # 옛날 사진(스캔한 흑백 사진 등)은 쓰지 않는다
            continue
        if re.search(r"(?<!\d)19\d\d(?!\d)", text):  # 촬영일이 스캔한 날로 적혀 있어도 이름·설명에 옛 연도가 있으면 뺀다
            continue
        scenic = bool(SCENIC.search(text))
        if need_scenic and not scenic:
            continue
        score = -0.3 * p.get("index", p.get("_order", 30)) + (6 if scenic else 0)
        score += 6 if year and year >= 2015 else 3 if year and year >= 2010 else -2 if year else 0
        score += 4 if ii["width"] > ii.get("height", 0) * 1.1 else -3
        out.append((score, {"title": title, "thumb": ii["thumburl"], "file_page": ii.get("descriptionurl"), "year": year,
                            "author": val("Artist")[:120] or "작가 미상", "license": lic, "license_url": val("LicenseUrl")}))
    return [c for _, c in sorted(out, key=lambda x: -x[0])]


def _category(q):
    """위키백과 문서 → 위키데이터 → 공용 분류 이름. 없으면 None."""
    d = _api("ko.wikipedia.org", action="query", titles=q, redirects=1, prop="pageprops", ppprop="wikibase_item|disambiguation")
    page = next(iter((d.get("query") or {}).get("pages", {}).values()), {})
    qid = page.get("pageprops", {}).get("wikibase_item")
    if "missing" in page or not qid or "disambiguation" in page.get("pageprops", {}):
        return None
    e = _api("www.wikidata.org", action="wbgetentities", ids=qid, props="claims|sitelinks", sitefilter="commonswiki")
    ent = e.get("entities", {}).get(qid, {})
    p373 = ent.get("claims", {}).get("P373", [])
    if p373:
        return "Category:" + p373[0]["mainsnak"]["datavalue"]["value"]
    link = ent.get("sitelinks", {}).get("commonswiki", {}).get("title", "")
    return link if link.startswith("Category:") else None


def _from_category(cat):
    d = _api("commons.wikimedia.org", action="query", generator="categorymembers", gcmtitle=cat, gcmtype="file",
             gcmlimit=40, gcmsort="timestamp", gcmdir="desc", **IIPROPS)  # 최근에 올린 사진부터
    pages = list((d.get("query") or {}).get("pages", {}).values())
    for i, p in enumerate(sorted(pages, key=lambda p: p.get("pageid", 0), reverse=True)):
        p["_order"] = i
    return _rank(pages, need_scenic=False)


def _from_search(q):
    d = _api("commons.wikimedia.org", action="query", generator="search", gsrnamespace=6, gsrlimit=15,
             gsrsearch=f"{q} filetype:bitmap", **IIPROPS)
    return _rank((d.get("query") or {}).get("pages", {}).values(), need_scenic=True)


def _colorful(im):
    """흑백·세피아 사진을 거른다(채도 평균이 낮으면 컬러 사진이 아니다)."""
    sat = im.convert("HSV").getchannel("S").resize((64, 64))
    return sum(sat.getdata()) / (64 * 64) >= 35


def _download(c, q, how, pid):
    raw = _get(c["thumb"], timeout=20)
    im = Image.open(io.BytesIO(raw))
    im.load()  # 실제 그림 파일인지 연다
    im = im.convert("RGB")
    if not _colorful(im):
        return None
    im.thumbnail((900, 900))
    name = f"photos/{hashlib.sha1(c['thumb'].encode()).hexdigest()[:16]}.jpg"
    path = store.project_dir(pid) / name
    path.parent.mkdir(exist_ok=True)
    im.save(path, "JPEG", quality=85)
    return {"status": "ok", "method": PHOTO_METHOD, "file": name, "width": im.width, "height": im.height, "query": q, "how": how,
            **{k: c[k] for k in ("title", "file_page", "year", "author", "license", "license_url")}, "fetched": time.time()}


def find(place, pid):
    """장소 하나의 사진을 찾아 내려받는다. 반환: 기록 dict(status ok/none/failed).
    1) 장소 이름이 맞는 위키백과 문서의 공용 분류(예: 월정사 → Category:Woljeongsa)에서 최근 컬러 풍경 사진
    2) 없으면 공용에서 '장소 + 풍경'으로 검색(풍경 낱말이 있는 사진만)"""
    tried = []
    deadline = time.time() + 45  # 한 장소에 45초 넘게 쓰지 않는다
    fail = lambda msg: {"status": "failed", "method": PHOTO_METHOD, "error": msg, "tried": tried, "fetched": time.time()}
    try:
        qs = queries(place)
        for q in qs:
            if time.time() > deadline:
                return fail("45초 안에 찾지 못함")
            cat = _category(q)
            tried.append(f"분류 {q} → {cat or '없음'}")
            if cat:
                for c in _from_category(cat)[:2]:
                    if rec := _download(c, q, f"공용 분류 {cat.removeprefix('Category:')}", pid):
                        return rec
        for q in qs[:2]:
            if time.time() > deadline:
                return fail("45초 안에 찾지 못함")
            tried.append(f"검색 {q} 풍경")
            for c in _from_search(f"{q} 풍경")[:2]:
                if rec := _download(c, q, "공용 검색", pid):
                    return rec
        return {"status": "none", "method": PHOTO_METHOD, "tried": tried, "fetched": time.time()}
    except Exception as e:  # 사진을 못 받아도 앱 흐름은 멈추지 않는다
        return fail(str(e)[:200])


def _path(pid):
    return store.project_dir(pid) / "photos.json"


def load(pid):
    try:
        return json.loads(_path(pid).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _save(pid, data):
    with _lock:
        tmp = _path(pid).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        tmp.replace(_path(pid))


def ensure(pid, concepts, retry_failed=False):
    """아직 사진을 찾지 않은 장소가 있으면 백그라운드에서 찾는다. 이미 찾는 중이면 아무것도 하지 않는다."""
    places = list(dict.fromkeys(p for c in concepts for p in c.get("places", [])))
    data = load(pid) or {"places": {}}
    todo = [p for p in places if p not in data["places"] or data["places"][p].get("method") != PHOTO_METHOD
            or (retry_failed and data["places"][p]["status"] == "failed")]
    with _lock:
        if not todo or pid in _running:
            return
        _running.add(pid)

    def run():
        try:
            data["state"] = "loading"
            _save(pid, data)
            for place in todo:  # 차례로(병렬로 부르면 위키미디어가 막는다)
                ex = ThreadPoolExecutor(1)
                try:  # 네트워크가 멈춰도(주소 찾기 등) 한 장소에 60초 넘게 묶이지 않게 한다
                    data["places"][place] = ex.submit(find, place, pid).result(timeout=60)
                except FutureTimeout:
                    data["places"][place] = {"status": "failed", "error": "60초 안에 응답이 없음", "fetched": time.time()}
                finally:
                    ex.shutdown(wait=False)
                _save(pid, data)
        finally:
            data["state"] = "done"
            _save(pid, data)
            with _lock:
                _running.discard(pid)

    threading.Thread(target=run, daemon=True).start()


def view(pid, concepts):
    """GET 응답에 붙일 사진 정보. 찾는 중이면 state=loading."""
    data = load(pid) or {"places": {}, "state": "loading"}
    if pid in _running:
        data["state"] = "loading"
    elif data.get("state") == "loading":  # 서버가 재시작되어 멈춘 작업
        data["state"] = "done"
    for rec in data["places"].values():
        rec["stale"] = rec.get("method") != PHOTO_METHOD  # 예전 방식으로 받은 사진(곧 다시 찾음)
    if any(p not in data["places"] or data["places"][p]["stale"] for c in concepts for p in c.get("places", [])):
        data["state"] = "loading"
    return data
