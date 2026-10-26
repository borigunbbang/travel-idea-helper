"""해설을 그림 카드(PNG 1080×1350)로 만든다. 그림은 AI 생성이 아니라 앱이 도형으로 그린 아이콘이다.

카드 순서: 표지 → 한눈에 보기 → (개념 표지 → 쟁점 카드들) × 고른 개념 → 아직 확인 못 한 것.
파일 이름은 카드 내용의 해시라서, 바뀌지 않은 카드는 다시 그리지 않고 같은 파일을 쓴다.
글자가 최소 크기에서도 넘치면 '…'로 줄이고 overflow에 남긴다(화면과 내보낸 파일 점검에 표시).
"""
import hashlib
import json
import math
from datetime import datetime
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from . import checks, store

FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
BOLD, MEDIUM, REGULAR = 6, 2, 0  # ttc 안의 글꼴 번호
W, H = 1080, 1350
M = 80  # 좌우 여백
TITLE_MIN, BODY_MIN, META = 54, 36, 30  # 휴대폰(360pt) 환산 제목 18px·본문 12px 하한(DECISIONS 43)
RENDER_VERSION = 4  # 그리는 방식을 바꾸면 올려서 모든 카드를 다시 그린다

BG, INK, MUTED, LINE = (247, 243, 235), (31, 42, 68), (107, 114, 128), (225, 219, 207)
LEVEL_COLOR = {"충분": (6, 118, 71), "일부": (181, 71, 8), "부족": (180, 35, 24)}
LEVEL_TEXT = {"충분": "근거 충분", "일부": "근거 일부", "부족": "자료 부족"}
WARN_BG, WARN = (255, 240, 219), (150, 64, 6)

# 쟁점 종류: (이름, 찾을 낱말, 바탕색, 그림색). 앞에서부터 먼저 맞는 것을 쓴다
TOPICS = [
    ("입국·안전", ("입국", "비자", "여권", "안전", "치안", "위험"), (237, 227, 250), (122, 85, 199)),
    ("날씨", ("날씨", "기온", "따뜻", "추위", "바람", "비 ", "강수", "계절", "일조", "파도"), (253, 236, 196), (230, 150, 30)),
    ("비용", ("비용", "가격", "예산", "값", "요금", "만원", "저렴", "비싸"), (217, 240, 227), (46, 158, 107)),
    ("혼잡", ("혼잡", "붐비", "성수기", "조용", "인파", "사람"), (251, 225, 220), (217, 96, 76)),
    ("이동", ("이동", "항공", "교통", "비행", "버스", "기차", "직항", "공항", "배편", "페리", "운전")
     , (220, 232, 251), (59, 111, 216)),
    ("일정·운영", ("운영", "휴무", "개장", "예약", "기간", "시간표", "일정"), (214, 240, 238), (33, 146, 135)),
    ("경험·코스", ("경험", "코스", "걷", "산책", "체험", "할 수 있", "해변", "온천", "숲", "길"), (230, 244, 217), (90, 154, 46)),
]
OTHER = ("기타", (), (236, 238, 241), (107, 114, 128))


def topic_of(question):
    for t in TOPICS:
        if any(k in question for k in t[1]):
            return t
    return OTHER


def _font(size, weight=REGULAR):
    return ImageFont.truetype(FONT, size, index=weight)


def _wrap(text, font, width):
    """띄어쓰기 단위로 줄바꿈하고, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines, cur = [], ""
    for word in str(text).split():
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if font.getlength(cur + ch) > width and cur:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur:
        lines.append(cur)
    return lines


class Card:
    def __init__(self):
        self.img = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.img)
        self.overflow = []

    def text(self, text, x, y, width, start, minimum, max_lines, weight=REGULAR, color=INK, label="", gap=1.35):
        """줄 수 안에 들어갈 때까지 글자를 줄인다. 최소 크기에서도 넘치면 마지막 줄을 '…'로 끝내고 기록한다.
        반환: 다음 글자를 놓을 y"""
        size = start
        while True:
            f = _font(size, weight)
            lines = _wrap(text, f, width)
            if len(lines) <= max_lines or size <= minimum:
                break
            size -= 2
        if len(lines) > max_lines:
            self.overflow.append(f"{label}: {len(lines)}줄 중 {max_lines}줄만 들어감")
            lines = lines[:max_lines]
            last = lines[-1]
            while last and f.getlength(last + "…") > width:
                last = last[:-1]
            lines[-1] = last + "…"
        step = round(size * gap)
        for i, line in enumerate(lines):
            self.d.text((x, y + i * step), line, font=f, fill=color)
        return y + len(lines) * step

    def pill(self, text, x, y, fill, color=(255, 255, 255), size=META, right=False):
        f = _font(size, BOLD)
        w = f.getlength(text) + 36
        if right:
            x -= w
        self.d.rounded_rectangle((x, y, x + w, y + size + 22), radius=(size + 22) // 2, fill=fill)
        self.d.text((x + 18, y + 9), text, font=f, fill=color)
        return x + w

    def footer(self, left, page, total):
        self.d.line((M, H - 96, W - M, H - 96), fill=LINE, width=2)
        self.d.text((M, H - 74), left, font=_font(26), fill=MUTED)
        f = _font(26, BOLD)
        s = f"{page} / {total}"
        self.d.text((W - M - f.getlength(s), H - 74), s, font=f, fill=MUTED)


# ---------- 아이콘 (도형으로 그림. 그림 안에 글자 없음) ----------

def _sun_cloud(d, cx, cy, s, c):
    for i in range(8):
        a = math.pi * 2 * i / 8
        d.line((cx - s * .1 + math.cos(a) * s * .42, cy - s * .12 + math.sin(a) * s * .42,
                cx - s * .1 + math.cos(a) * s * .58, cy - s * .12 + math.sin(a) * s * .58), fill=c, width=int(s * .06))
    d.ellipse((cx - s * .42, cy - s * .44, cx + s * .22, cy + s * .2), fill=c)
    w = (255, 255, 255)
    d.ellipse((cx - s * .2, cy + s * .02, cx + s * .2, cy + s * .4), fill=w)
    d.ellipse((cx + s * .05, cy - s * .1, cx + s * .5, cy + s * .35), fill=w)
    d.rounded_rectangle((cx - s * .32, cy + s * .18, cx + s * .62, cy + s * .42), radius=int(s * .12), fill=w)


def _coins(d, cx, cy, s, c):
    dark = tuple(max(0, v - 40) for v in c)
    for i in range(4):
        y = cy + s * .3 - i * s * .17
        d.ellipse((cx - s * .45, y - s * .1, cx + s * .15, y + s * .1), fill=dark)
        d.ellipse((cx - s * .45, y - s * .14, cx + s * .15, y + s * .06), fill=c)
    d.ellipse((cx - s * .05, cy - s * .2, cx + s * .5, cy + s * .35), fill=c, outline=dark, width=int(s * .04))
    d.ellipse((cx + s * .1, cy - s * .05, cx + s * .35, cy + s * .2), outline=(255, 255, 255), width=int(s * .035))


def _plane(d, cx, cy, s, c):
    for i in range(5):  # 지나온 점선 길
        x = cx - s * .55 + i * s * .14
        d.ellipse((x, cy + s * .38 - i * s * .05, x + s * .05, cy + s * .43 - i * s * .05), fill=c)
    p = [(-.5, .05), (.35, -.05), (.5, -.12), (.55, -.05), (.45, .05), (-.5, .12)]
    d.polygon([(cx + x * s, cy + y * s) for x, y in p], fill=c)
    d.polygon([(cx + x * s, cy + y * s) for x, y in [(-.05, 0), (.15, 0), (-.1, .45), (-.22, .45)]], fill=c)
    d.polygon([(cx + x * s, cy + y * s) for x, y in [(.0, -.02), (.15, -.02), (-.05, -.35), (-.15, -.35)]], fill=c)
    d.polygon([(cx + x * s, cy + y * s) for x, y in [(-.45, .06), (-.35, .06), (-.42, -.2), (-.5, -.2)]], fill=c)


def _shield(d, cx, cy, s, c):
    p = [(0, -.48), (.42, -.32), (.38, .12), (0, .5), (-.38, .12), (-.42, -.32)]
    d.polygon([(cx + x * s, cy + y * s) for x, y in p], fill=c)
    d.line([(cx - s * .17, cy), (cx - s * .03, cy + s * .15), (cx + s * .2, cy - s * .15)], fill=(255, 255, 255), width=int(s * .08), joint="curve")


def _people(d, cx, cy, s, c):
    light = tuple(min(255, v + 60) for v in c)
    for dx, col, k in ((-.32, light, .8), (.32, light, .8), (0, c, 1)):
        x, r = cx + dx * s, s * .13 * k
        d.ellipse((x - r, cy - s * .25 - r, x + r, cy - s * .25 + r), fill=col)
        d.rounded_rectangle((x - r * 1.6, cy - s * .08, x + r * 1.6, cy + s * .4), radius=int(r * 1.2), fill=col)


def _calendar(d, cx, cy, s, c):
    d.rounded_rectangle((cx - s * .45, cy - s * .38, cx + s * .45, cy + s * .45), radius=int(s * .08), fill=(255, 255, 255), outline=c, width=int(s * .05))
    d.rectangle((cx - s * .43, cy - s * .36, cx + s * .43, cy - s * .16), fill=c)
    for x in (-.25, .25):
        d.rounded_rectangle((cx + x * s - s * .04, cy - s * .5, cx + x * s + s * .04, cy - s * .28), radius=int(s * .03), fill=c)
    for r in range(2):
        for k in range(3):
            x, y = cx - s * .28 + k * s * .28, cy - s * .02 + r * s * .22
            d.ellipse((x - s * .05, y - s * .05, x + s * .05, y + s * .05), fill=c if (r, k) == (1, 1) else tuple(min(255, v + 90) for v in c))


def _pin_path(d, cx, cy, s, c):
    light = tuple(min(255, v + 80) for v in c)
    d.line([(cx - s * .5, cy + s * .42), (cx - s * .15, cy + s * .2), (cx + s * .1, cy + s * .38), (cx + s * .45, cy + s * .15)], fill=light, width=int(s * .06), joint="curve")
    d.ellipse((cx - s * .22, cy - s * .48, cx + s * .22, cy - s * .04), fill=c)
    d.polygon([(cx - s * .19, cy - s * .16), (cx + s * .19, cy - s * .16), (cx, cy + s * .2)], fill=c)
    d.ellipse((cx - s * .08, cy - s * .34, cx + s * .08, cy - s * .18), fill=(255, 255, 255))


def _compass(d, cx, cy, s, c):
    d.ellipse((cx - s * .45, cy - s * .45, cx + s * .45, cy + s * .45), outline=c, width=int(s * .06), fill=(255, 255, 255))
    d.polygon([(cx, cy - s * .35), (cx + s * .1, cy), (cx - s * .1, cy)], fill=c)
    d.polygon([(cx, cy + s * .35), (cx + s * .1, cy), (cx - s * .1, cy)], fill=tuple(min(255, v + 90) for v in c))


def _suitcase(d, cx, cy, s, c):
    d.rounded_rectangle((cx - s * .15, cy - s * .45, cx + s * .15, cy - s * .25), radius=int(s * .05), outline=c, width=int(s * .05))
    d.rounded_rectangle((cx - s * .48, cy - s * .3, cx + s * .48, cy + s * .42), radius=int(s * .1), fill=c)
    for x in (-.25, .25):
        d.rectangle((cx + x * s - s * .03, cy - s * .3, cx + x * s + s * .03, cy + s * .42), fill=tuple(max(0, v - 40) for v in c))


ICONS = {"날씨": _sun_cloud, "비용": _coins, "이동": _plane, "입국·안전": _shield, "혼잡": _people,
         "일정·운영": _calendar, "경험·코스": _pin_path, "기타": _compass}


def _illustration(card, box, topic_bg, topic_fg, draw):
    x0, y0, x1, y1 = (round(v) for v in box)
    w, h = x1 - x0, y1 - y0
    # 장식 원이 그림 판 밖으로 나가지 않게 판을 따로 그려 둥근 모양으로 붙인다
    panel = Image.new("RGB", (w, h), topic_bg)
    pd = ImageDraw.Draw(panel)
    light = tuple(min(255, v + 12) for v in topic_bg)
    pd.ellipse((w - 260, -60, w + 40, 240), fill=light)
    pd.ellipse((-40, h - 160, 160, h + 40), fill=light)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=36, fill=255)
    card.img.paste(panel, (x0, y0), mask)
    s = min(x1 - x0, y1 - y0) * .62
    draw(card.d, (x0 + x1) / 2, (y0 + y1) / 2, s, topic_fg)


def _triangle(card, x, y, size, color):
    card.d.polygon([(x + size / 2, y), (x + size, y + size), (x, y + size)], fill=color)
    card.d.rectangle((x + size * .45, y + size * .35, x + size * .55, y + size * .7), fill=(255, 255, 255))
    card.d.rectangle((x + size * .45, y + size * .78, x + size * .55, y + size * .88), fill=(255, 255, 255))


def _domain(url):
    return urlparse(url).netloc.removeprefix("www.") or url


def _day(iso):
    return str(iso)[:10]


# ---------- 카드 종류별 ----------

def _cover(d, levels, foot, page, total):
    c = Card()
    c.d.text((M, 96), "여행 구상 카드", font=_font(34, BOLD), fill=MUTED)
    y = c.text(d["topic"], M, 150, W - 2 * M, 66, TITLE_MIN, 4, BOLD, label="구상")
    _illustration(c, (M, y + 30, W - M, y + 370), (253, 236, 196), (217, 120, 40), _suitcase)
    y += 410
    cond = d["research"]["conditions"]
    x = M
    for k in cond["known"][:6]:
        t = f"{k['name']} {k['value']}"
        t = t if len(t) <= 16 else t[:15] + "…"
        w = _font(META, BOLD).getlength(t) + 36
        if x + w > W - M:
            x, y = M, y + 66
        x = c.pill(t, x, y, (230, 225, 214), INK) + 12
    y += 82
    if cond["missing"]:
        y = c.text("정하지 않음: " + ", ".join(cond["missing"]), M, y, W - 2 * M, BODY_MIN, BODY_MIN, 4, MEDIUM, WARN, "정하지 않은 조건") + 20
    x = M
    for lv in ("충분", "일부", "부족"):
        x = c.pill(f"{LEVEL_TEXT[lv]} {levels[lv]}", x, y, LEVEL_COLOR[lv], size=34) + 14
    c.footer(foot, page, total)
    return c


def _overview(d, foot, page, total):
    c = Card()
    c.d.text((M, 96), "한눈에 보기", font=_font(34, BOLD), fill=MUTED)
    _illustration(c, (M, 160, W - M, 420), (220, 232, 251), (59, 111, 216), _compass)
    y = c.text(d["guide"]["summary"], M, 470, W - 2 * M, 40, BODY_MIN, 15, REGULAR, label="요약", gap=1.45)
    for a in d["answers"][:2]:
        y = c.text(f"받은 답변: {a['answer']}", M, y + 16, W - 2 * M, META, META, 1, MEDIUM, MUTED, "받은 답변")
    c.footer(foot, page, total)
    return c


def _concept(d, concept, k, issues, foot, page, total):
    c = Card()
    c.d.text((M, 96), f"핵심 개념 {k}", font=_font(34, BOLD), fill=MUTED)
    y = c.text(concept["title"], M, 150, W - 2 * M, 68, TITLE_MIN, 3, BOLD, label="개념 이름")
    _illustration(c, (M, y + 30, W - M, y + 400), (230, 244, 217), (90, 154, 46), _pin_path)
    y += 440
    y = c.text("후보 지역  " + " · ".join(concept["places"]), M, y, W - 2 * M, 38, BODY_MIN, 2, BOLD, (59, 111, 216), "후보 지역") + 14
    y = c.text(concept["pitch"], M, y, W - 2 * M, 38, BODY_MIN, 4, REGULAR, label="소개") + 24
    x = M
    lv = checks.summary(issues)
    c.d.text((M, y), f"쟁점 {len(issues)}개", font=_font(META, BOLD), fill=MUTED)
    x = M + 150
    for key in ("충분", "일부", "부족"):
        if lv[key]:
            x = c.pill(f"{LEVEL_TEXT[key]} {lv[key]}", x, y - 8, LEVEL_COLOR[key]) + 12
    c.footer(foot, page, total)
    return c


def _issue(d, issue, concept_no, foot, page, total):
    c = Card()
    name, _, tbg, tfg = topic_of(issue["question"])
    lv = issue["check"]["level"]
    c.d.rectangle((0, 0, W, 18), fill=LEVEL_COLOR[lv])
    c.d.text((M, 70), f"쟁점 {issue['n']}  ·  개념 {concept_no}  ·  {name}", font=_font(32, BOLD), fill=MUTED)
    c.pill(LEVEL_TEXT[lv], W - M, 60, LEVEL_COLOR[lv], size=34, right=True)
    _illustration(c, (M, 140, W - M, 460), tbg, tfg, ICONS[name])
    y = c.text(issue["question"], M, 500, W - 2 * M, 60, TITLE_MIN, 3, BOLD, label="문제") + 18
    # 판단 상자
    f = _font(BODY_MIN + 2, REGULAR)
    ans_lines = min(len(_wrap(issue["short_answer"], f, W - 2 * M - 60)), 5)
    box_h = 70 + ans_lines * round((BODY_MIN + 2) * 1.4) + 20
    c.d.rounded_rectangle((M, y, W - M, y + box_h), radius=24, fill=(255, 255, 255), outline=LINE, width=2)
    c.d.text((M + 30, y + 22), "판단", font=_font(30, BOLD), fill=LEVEL_COLOR[lv])
    c.text(issue["short_answer"], M + 30, y + 66, W - 2 * M - 60, BODY_MIN + 2, BODY_MIN, 5, REGULAR, label="판단", gap=1.4)
    y += box_h + 24
    cnt = issue["check"]["counts"]
    doms = list(dict.fromkeys(_domain(e["url"]) for e in issue["evidence"]))[:2]
    src = f"근거 {cnt['evidence']}개 (공식 {cnt['official']})" + (f"  ·  {', '.join(doms)}" if doms else "  ·  확인한 출처 없음")
    y = c.text(src, M, y, W - 2 * M, META, META, 1, MEDIUM, MUTED, "근거") + 12
    gaps = [g["what"] for g in issue["gaps"]] + issue["check"]["auto_gaps"]
    if gaps:
        room = H - 120 - y
        if room > 110:
            lines = max(1, min(3, (room - 50) // 46))
            c.d.rounded_rectangle((M, y, W - M, y + 40 + lines * 46), radius=20, fill=WARN_BG)
            _triangle(c, M + 24, y + 22, 30, WARN)
            more = f" 외 {len(gaps) - 1}개" if len(gaps) > 1 else ""
            c.text(f"부족: {gaps[0]}{more}", M + 70, y + 18, W - 2 * M - 100, BODY_MIN, BODY_MIN, lines, MEDIUM, WARN, "부족한 자료", gap=1.28)
        else:
            c.overflow.append("부족한 자료: 카드에 자리가 없어 빠짐")
    c.footer(foot, page, total)
    return c


def _gaps(d, foot, page, total):
    c = Card()
    c.d.text((M, 96), "아직 확인 못 한 것", font=_font(34, BOLD), fill=MUTED)
    _illustration(c, (M, 160, W - M, 400), WARN_BG, (217, 120, 40), _compass)
    items = [g["what"] for g in d["guide"].get("overall_gaps", [])]
    for i in d["issues"]:
        if i["check"]["level"] != "충분":
            items += [f"쟁점 {i['n']}: {g['what']}" for g in i["gaps"][:1]]
    items = list(dict.fromkeys(items))
    y = 440
    shown = 0
    for t in items:
        if y > H - 260:
            break
        _triangle(c, M, y + 6, 30, WARN)
        y = c.text(t, M + 50, y, W - 2 * M - 50, BODY_MIN, BODY_MIN, 2, REGULAR, label="부족한 자료") + 18
        shown += 1
    if not items:
        c.text("앱 점검으로 찾은 부족한 자료가 없다. 그래도 원문은 직접 열어 확인한다.", M, y, W - 2 * M, BODY_MIN, BODY_MIN, 3)
    elif shown < len(items):
        c.text(f"나머지 {len(items) - shown}개는 해설 문서에서 볼 수 있다.", M, y + 6, W - 2 * M, META, META, 1, MEDIUM, MUTED)
    c.footer(foot, page, total)
    return c


def build(pid):
    """현재 해설로 카드 묶음을 만들고 doc['deck']에 저장한다. 바뀌지 않은 카드는 다시 그리지 않는다."""
    d = store.get_project(pid)
    if not d.get("guide"):
        return []
    concepts = [c for c in d["concepts"] if c["id"] in d["selection"]]
    plan = [("cover", None), ("overview", None)]
    for k, c in enumerate(concepts, 1):
        plan.append(("concept", (k, c)))
        plan += [("issue", (k, i)) for i in d["issues"] if i["concept_id"] == c["id"]]
    plan.append(("gaps", None))
    total = len(plan)
    when = datetime.fromisoformat(d["research"]["searched_at"]).strftime("%Y-%m-%d") if d.get("research") else ""
    foot = f"조사 {when}  ·  AI 조사·작성, 근거 수준은 앱 점검" + ("  ·  모의 데이터" if d["research"].get("mock") else "")
    levels = checks.summary(d["issues"])
    out_dir = store.project_dir(pid) / "cards"
    out_dir.mkdir(exist_ok=True)
    deck = []
    for page, (kind, arg) in enumerate(plan, 1):
        if kind == "cover":
            key_src, make, extra = [d["topic"], d["research"]["conditions"], levels], lambda: _cover(d, levels, foot, page, total), {}
        elif kind == "overview":
            key_src, make, extra = [d["guide"]["summary"], d["answers"]], lambda: _overview(d, foot, page, total), {}
        elif kind == "concept":
            k, con = arg
            its = [i for i in d["issues"] if i["concept_id"] == con["id"]]
            key_src = [con, [i["check"]["level"] for i in its]]
            make, extra = (lambda k=k, con=con, its=its: _concept(d, con, k, its, foot, page, total)), {"concept_id": con["id"]}
        elif kind == "issue":
            k, i = arg
            key_src = [{f: i[f] for f in ("n", "question", "short_answer", "evidence", "gaps")}, i["check"], k]
            make, extra = (lambda k=k, i=i: _issue(d, i, k, foot, page, total)), {"issue_id": i["id"], "level": i["check"]["level"]}
        else:
            key_src, make, extra = [d["guide"].get("overall_gaps"), [(i["n"], i["check"]["level"], i["gaps"][:1]) for i in d["issues"]]], \
                lambda: _gaps(d, foot, page, total), {}
        key = hashlib.sha1(json.dumps([RENDER_VERSION, kind, key_src, foot, page, total], ensure_ascii=False, sort_keys=True,
                                      default=str).encode()).hexdigest()[:16]
        path = out_dir / f"{key}.png"
        meta = out_dir / f"{key}.json"
        if path.is_file() and meta.is_file():
            overflow = json.loads(meta.read_text())
        else:
            c = make()
            c.img.save(path)
            overflow = c.overflow
            meta.write_text(json.dumps(overflow, ensure_ascii=False))
        deck.append({"page": page, "kind": kind, "png": f"cards/{key}.png", "overflow": overflow, **extra})
    store.update_project(pid, lambda x: x.update(deck=deck, deck_version=RENDER_VERSION))
    return deck
