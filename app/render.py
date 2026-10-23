"""Pillow 글자 렌더러. 배경 이미지 위에 한국어 글자를 따로 배치하고 결과를 검사한다."""
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, ImageStat

FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
BOLD, MEDIUM, REGULAR = 6, 2, 0  # ttc 안의 글꼴 번호
W, H = 1080, 1350
MARGIN = 80
PHONE_WIDTH = 360  # 휴대폰 화면 폭(pt)에서의 가독성 기준
MIN_PHONE_PX = {"title": 18, "body": 12}


def _font(size, weight=REGULAR):
    if weight is True:
        weight = BOLD
    elif weight is False:
        weight = REGULAR
    return ImageFont.truetype(FONT, size, index=weight)


def _wrap(text, font, width):
    """띄어쓰기 단위로 줄바꿈하고, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines, cur = [], ""
    for word in text.split():
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


def _fit(text, bold, start, minimum, max_lines, width):
    """줄 수 안에 들어갈 때까지 글자 크기를 줄인다. 최소 크기에서도 넘치면 overflow=True."""
    size = start
    while True:
        f = _font(size, bold)
        lines = _wrap(text, f, width)
        if len(lines) <= max_lines or size <= minimum:
            return f, size, lines, len(lines) > max_lines
        size -= 2


def _cover(bg):
    bg = bg.convert("RGB")
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((round(bg.width * scale), round(bg.height * scale)), Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    return bg.crop((left, top, left + W, top + H))


def _cover_to(img, w, h):
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * scale)), max(h, round(img.height * scale))), Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 3  # 세로로 남으면 위를 조금 더 잘라 아래 소재를 살린다
    return img.crop((left, top, left + w, top + h))


def _luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(fg, bg):
    a, b = sorted([_luminance(fg), _luminance(bg)], reverse=True)
    return (a + 0.05) / (b + 0.05)


DEFAULT_LAYOUT = {"position": "split", "size": "normal", "shade": "normal", "palette": "match"}
PANEL = (16, 22, 40)  # 나눠 배치의 글자 영역 색
EMPTY_TOP = 0.30  # 그림 요청에서 비워 두게 한 윗부분 비율(35%) 중 잘라 낼 만큼
SIZE_FACTOR = {"small": 0.88, "normal": 1.0, "large": 1.12}
SHADE_ALPHA = {"light": 150, "normal": 215, "strong": 245}


def _worst_contrast(canvas, box, fg=(255, 255, 255)):
    """글자 줄이 놓일 자리 배경의 밝은 쪽·어두운 쪽(상·하위 10%)과 글자색의 대비 중 낮은 값."""
    x0, y0, x1, y1 = (max(0, int(v)) for v in box)
    if x1 <= x0 or y1 <= y0:
        return 99.0
    hist = canvas.crop((x0, y0, min(x1, W), min(y1, H))).convert("L").histogram()
    total = sum(hist)

    def pct(q):
        acc = 0
        for v in range(256):
            acc += hist[v]
            if acc >= total * q:
                return v
        return 255
    lo, hi = pct(0.10), pct(0.90)
    return min(_contrast(fg, (lo, lo, lo)), _contrast(fg, (hi, hi, hi)))


DARK_TEXT = {"tag": (18, 110, 100), "title": (20, 26, 46), "line": (38, 46, 68), "body": (58, 66, 88), "foot": (80, 86, 104)}
LIGHT_TEXT = {"tag": (120, 220, 210), "title": (255, 255, 255), "line": (235, 238, 245), "body": (225, 230, 240), "foot": (200, 205, 215)}


def _panel_color(src):
    """그림의 비워 둔 윗부분(배경색)을 뽑아 글자 영역 색으로 쓴다."""
    top = src.crop((0, 0, src.width, int(src.height * 0.2)))
    return tuple(int(v) for v in ImageStat.Stat(top).median)


def render(card, bg_path, out_path, index, total, searched_at, image_kind, layout=None):
    """배경 그림 위에 글자를 따로 그린다.
    position=split(기본): 위 단색 영역에 글자를 모두, 아래에 그림을 덮개 없이(그림의 빈 윗부분은 잘라 냄)
    position=top: 위(역할·제목·핵심 문장) / 가운데 그림 / 아래 본문 띠
    position=bottom: 위는 그림만, 아래 띠에 제목·핵심 문장·본문을 모아서
    잘림·겹침·대비가 문제인 줄은 problems에 위치(box)와 함께 남긴다."""
    lay = {**DEFAULT_LAYOUT, **(layout or {})}
    k = SIZE_FACTOR.get(lay["size"], 1.0)
    alpha = SHADE_ALPHA.get(lay["shade"], 215)
    src = Image.open(bg_path).convert("RGB")
    canvas = _cover(src)
    width = W - 2 * MARGIN
    sz = lambda v: int(round(v * k))

    tf, tsize, tlines, t_over = _fit(card["title"], True, sz(72), sz(56), 3, width)
    lf, lsize, llines, l_over = _fit(card["line"], MEDIUM, sz(44), sz(38), 3, width)
    body = card.get("body", "").strip()
    bf, bsize, blines, b_over = _fit(body, False, sz(38), sz(36), 4, width) if body else (None, sz(36), [], False)
    th, lh, bh = int(tsize * 1.25), int(lsize * 1.45), int(bsize * 1.5)
    tag_h = 60
    head_h = tag_h + len(tlines[:3]) * th + 24 + len(llines[:3]) * lh
    body_h = len(blines[:4]) * bh
    foot_top = H - 150

    split = lay["position"] in ("split", "picture_top")
    panel = _panel_color(src) if lay.get("palette") == "match" else PANEL
    colors = DARK_TEXT if split and _luminance(panel) > 0.35 else LIGHT_TEXT
    text_h = head_h + (24 + body_h if blines else 0)

    # 블록 위치 정하기
    if split:
        foot_top = H - 120
        cut = src.crop((0, int(src.height * EMPTY_TOP), src.width, src.height))  # 비워 둔 윗부분은 잘라 냄
        if lay["position"] == "split":  # 글자 위, 그림 아래
            head_y = MARGIN
            pic_top = head_y + text_h + 50
            pic_bottom = foot_top
        else:  # 그림 위, 글자 아래
            pic_top = 0
            head_y = foot_top - 40 - text_h
            pic_bottom = head_y - 50
        body_y = head_y + head_h + 24
        clear_top, clear_bottom = pic_top, pic_bottom
        stacked_over = pic_bottom - pic_top < 300  # 그림 자리가 너무 작으면 문제로 본다
        canvas = Image.new("RGB", (W, H), panel)
        canvas.paste(_cover_to(cut, W, max(1, pic_bottom - pic_top)), (0, pic_top))
    elif lay["position"] == "bottom":
        panel_top = foot_top - 40 - head_h - (24 + body_h if blines else 0)
        head_y, body_y = panel_top, panel_top + head_h + 24
        clear_top, clear_bottom = MARGIN, panel_top - 36  # 그림이 보이는 영역
        stacked_over = panel_top - 36 < MARGIN
    else:
        head_y = MARGIN
        body_y = foot_top - 40 - body_h if blines else foot_top
        clear_top, clear_bottom = head_y + head_h, (body_y - 36 if blines else foot_top)
        stacked_over = clear_bottom - clear_top < 20

    # 대비용 그늘(나눠 배치는 글자가 단색 위에 있어 그림을 덮지 않고, 출처 띠만 둔다)
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    if split:
        pass
    elif lay["position"] == "bottom":
        for y in range(max(0, head_y - 156), head_y - 36):
            sd.line([(0, y), (W, y)], fill=int(alpha * (y - (head_y - 156)) / 120))
        sd.rectangle([0, head_y - 36, W, foot_top], fill=alpha)
    else:
        fade_end = head_y + head_h
        for y in range(min(fade_end + 120, H)):
            sd.line([(0, y), (W, y)], fill=alpha if y <= fade_end else int(alpha * (1 - (y - fade_end) / 120)))
        if blines:
            sd.rectangle([0, body_y - 36, W, foot_top], fill=max(0, alpha - 15))
    if not split:
        sd.rectangle([0, foot_top, W, H], fill=225)
    canvas = Image.composite(Image.new("RGB", (W, H), (8, 10, 20)), canvas, shade)
    if split:  # 출처 띠는 글자 영역과 같은 색
        ImageDraw.Draw(canvas).rectangle([0, foot_top, W, H], fill=panel)
    d = ImageDraw.Draw(canvas)

    problems = []

    def draw_lines(lines, font, x, y, step, color, over, max_lines, label):
        shown = lines[:max_lines]
        for i, ln in enumerate(shown):
            if over and i == len(shown) - 1:
                ln = ln.rstrip() + "…"
            box = (x, y, x + font.getlength(ln), y + step)
            c = _worst_contrast(canvas, box, color)
            if c < 4.5:
                problems.append({"kind": "contrast", "label": f"{label} {i+1}번째 줄이 배경과 겹쳐 잘 안 보임(대비 {c:.1f}:1)", "box": [int(v) for v in box]})
            d.text((x, y), ln, font=font, fill=color)
            y += step
        if over:
            problems.append({"kind": "clip", "label": f"{label}이(가) {len(lines)}줄이라 {max_lines}줄 뒤가 잘림", "box": [x, int(y - step), W - MARGIN, int(y)]})
        return y

    d.text((MARGIN, head_y), f"{card['role']}  {index}/{total}", font=_font(30, True), fill=colors["tag"])
    y = draw_lines(tlines, tf, MARGIN, head_y + tag_h, th, colors["title"], t_over, 3, "제목")
    draw_lines(llines, lf, MARGIN, y + 24, lh, colors["line"], l_over, 3, "핵심 문장")
    if blines:
        draw_lines(blines, bf, MARGIN, body_y, bh, colors["body"], b_over, 4, "본문")
    if stacked_over:
        problems.append({"kind": "overlap", "label": "글자 블록끼리 겹치거나 화면 위로 넘침",
                         "box": [MARGIN, max(MARGIN, clear_bottom), W - MARGIN, clear_top]})

    ff = _font(30)
    domains = ", ".join(dict.fromkeys(urlparse(u).netloc.removeprefix("www.") for u in card["sources"] if u.startswith("http")))
    foot1 = f"출처: {domains}" if domains else "출처: 없음"
    foot2 = f"조사 기준 {searched_at[:10]} · " + ("AI 생성 이미지" if image_kind == "generated" else "모의 이미지")
    f1 = _wrap(foot1, ff, width)
    fy = H - 100 if split else H - 128
    d.text((MARGIN, fy), f1[0] + (" …" if len(f1) > 1 else ""), font=ff, fill=colors["foot"])
    d.text((MARGIN, fy + 48), foot2, font=ff, fill=colors["foot"])
    canvas.save(out_path, "PNG")

    with Image.open(out_path) as im:
        size, fmt = im.size, im.format
    phone = PHONE_WIDTH / W
    small = min(lsize, bsize if blines else lsize)
    clips = [p for p in problems if p["kind"] in ("clip", "overlap")]
    lows = [p for p in problems if p["kind"] == "contrast"]
    gap = clear_bottom - clear_top
    if split:
        shown = gap / H
        pic_detail = f"그림이 덮개 없이 보이는 높이 {gap}px(카드의 {shown:.0%})"
    else:
        shown = gap / H
        pic_detail = f"그림이 가리지 않고 보이는 높이 {gap}px(카드의 {shown:.0%}). 나머지는 글자 띠가 덮음"
    return {
        "picture": {"ok": shown >= 0.4, "detail": pic_detail + ("" if shown >= 0.4 else " — 40% 미만, 글자를 줄이거나 '나눠 배치'를 쓰세요")},
        "file": {"ok": fmt == "PNG" and size == (W, H), "detail": f"{fmt} {size[0]}×{size[1]}"},
        "overflow": {"ok": not clips, "detail": f"넘침 없음, 그림이 보이는 높이 {gap}px" if not clips else " / ".join(p["label"] for p in clips)},
        "contrast": {"ok": not lows, "detail": "모든 줄 대비 4.5:1 이상" if not lows else " / ".join(p["label"] for p in lows)},
        "phone": {"ok": tsize * phone >= MIN_PHONE_PX["title"] and small * phone >= MIN_PHONE_PX["body"],
                  "detail": f"휴대폰 폭 {PHONE_WIDTH}pt 환산 제목 {tsize * phone:.0f}px·핵심/본문 {small * phone:.0f}px"},
    }, problems, lay
