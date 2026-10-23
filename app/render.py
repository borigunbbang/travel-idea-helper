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


def _luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(fg, bg):
    a, b = sorted([_luminance(fg), _luminance(bg)], reverse=True)
    return (a + 0.05) / (b + 0.05)


def _region_contrast(canvas, box):
    if box[3] <= box[1]:
        return 99.0
    med = tuple(int(v) for v in ImageStat.Stat(canvas.crop(box)).median)
    return _contrast((255, 255, 255), med)


def render(card, bg_path, out_path, index, total, searched_at, image_kind):
    """위: 역할·제목·핵심 문장 / 가운데: 설명 그림 / 아래: 본문 띠와 출처 띠."""
    canvas = _cover(Image.open(bg_path))
    width = W - 2 * MARGIN

    # 글자 크기와 줄을 먼저 정해 영역 높이를 계산한다
    tf, tsize, tlines, t_over = _fit(card["title"], True, 72, 56, 3, width)
    lf, lsize, llines, l_over = _fit(card["line"], MEDIUM, 44, 38, 3, width)
    body = card.get("body", "").strip()
    bf, bsize, blines, b_over = _fit(body, False, 38, 36, 4, width) if body else (None, 36, [], False)

    top_bottom = MARGIN + 60 + len(tlines[:3]) * int(tsize * 1.25) + 24 + len(llines[:3]) * int(lsize * 1.45)
    foot_top = H - 150
    body_h = len(blines[:4]) * int(bsize * 1.5)
    body_top = foot_top - 40 - body_h if blines else foot_top

    # 대비용 그늘: 위쪽 그라데이션, 본문 띠, 출처 띠
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    fade = top_bottom + 120
    for y in range(min(fade, H)):
        sd.line([(0, y), (W, y)], fill=int(215 * (1 - max(0, y - top_bottom) / 120)) if y > top_bottom else 215)
    if blines:
        sd.rectangle([0, body_top - 36, W, foot_top], fill=200)
    sd.rectangle([0, foot_top, W, H], fill=225)
    canvas = Image.composite(Image.new("RGB", (W, H), (8, 10, 20)), canvas, shade)
    d = ImageDraw.Draw(canvas)

    d.text((MARGIN, MARGIN), f"{card['role']}  {index}/{total}", font=_font(30, True), fill=(120, 220, 210))
    y = MARGIN + 60
    for ln in tlines[:3]:
        d.text((MARGIN, y), ln, font=tf, fill=(255, 255, 255))
        y += int(tsize * 1.25)
    y += 24
    for ln in llines[:3]:
        d.text((MARGIN, y), ln, font=lf, fill=(235, 238, 245))
        y += int(lsize * 1.45)
    y = body_top
    for ln in blines[:4]:
        d.text((MARGIN, y), ln, font=bf, fill=(225, 230, 240))
        y += int(bsize * 1.5)

    ff = _font(30)
    domains = ", ".join(dict.fromkeys(urlparse(u).netloc.removeprefix("www.") for u in card["sources"] if u.startswith("http")))
    foot1 = f"출처: {domains}" if domains else "출처: 없음"
    foot2 = f"조사 기준 {searched_at[:10]} · " + ("AI 생성 이미지" if image_kind == "generated" else "모의 이미지")
    f1 = _wrap(foot1, ff, width)
    d.text((MARGIN, H - 128), f1[0] + (" …" if len(f1) > 1 else ""), font=ff, fill=(200, 205, 215))
    d.text((MARGIN, H - 80), foot2, font=ff, fill=(200, 205, 215))
    canvas.save(out_path, "PNG")

    # 결과 파일 검사
    with Image.open(out_path) as im:
        size, fmt = im.size, im.format
    ratio = round(min(_region_contrast(canvas, (MARGIN, MARGIN, W - MARGIN, top_bottom)),
                      _region_contrast(canvas, (MARGIN, body_top, W - MARGIN, body_top + body_h))), 2)
    overflow = t_over or l_over or b_over or top_bottom > (body_top - 36 if blines else foot_top) - 20
    gap = (body_top - 36 if blines else foot_top) - top_bottom
    phone = PHONE_WIDTH / W
    small = min(lsize, bsize if blines else lsize)
    return {
        "file": {"ok": fmt == "PNG" and size == (W, H), "detail": f"{fmt} {size[0]}×{size[1]}"},
        "overflow": {"ok": not overflow,
                     "detail": f"넘침 없음, 그림이 보이는 높이 {gap}px" if not overflow else "글자가 영역을 넘치거나 위아래 글자가 겹침"},
        "contrast": {"ok": ratio >= 4.5, "detail": f"글자 영역 최저 대비 {ratio}:1 (기준 4.5:1)"},
        "phone": {"ok": tsize * phone >= MIN_PHONE_PX["title"] and small * phone >= MIN_PHONE_PX["body"],
                  "detail": f"휴대폰 폭 {PHONE_WIDTH}pt 환산 제목 {tsize * phone:.0f}px·핵심/본문 {small * phone:.0f}px"},
    }
