"""이미지 작업자. Antigravity CLI(agy)의 내장 이미지 도구에 자연어로 요청한다.

확인한 agy 1.4.5 동작(2026-10-23):
- `agy -p ... --output-format json` 의 응답은 대화 결과 JSON이지 이미지가 아니다.
  status가 SUCCESS여도 요청한 경로에 파일이 없을 수 있다.
- 생성된 이미지는 ~/.gemini/antigravity-cli/brain/<conversation_id>/ 에 JPEG 등으로 저장된다.
- 로그인되어 있지 않으면 "Authentication required"를 출력하고 로그인을 기다린다.
그래서 앱이 직접 그 폴더에서 이미지 파일을 찾고, Pillow로 열어 형식·크기를 검사한다.
"""
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

AGY = shutil.which("agy") or str(Path.home() / ".local/bin/agy")
BRAIN = Path.home() / ".gemini/antigravity-cli/brain"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MIN_SIDE = 512


class ImageError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def style_text(style):
    if not style:
        return "플랫 벡터 일러스트, 차분한 남색 계열, 단순한 선과 면"
    return f"색감: {style['palette']} / 그림체: {style['style']} / 분위기: {style['mood']}"


def build_prompt(card, style=None, ratio="4:5"):
    return (
        "내장 이미지 생성 도구로 카드뉴스용 설명 일러스트 1장을 만들어 줘. 이미지 생성이 완전히 끝난 뒤에 답해.\n"
        f"비율: 세로 {ratio}.\n"
        f"이 그림의 역할: {card.get('image_role', '')}\n"
        f"그릴 내용: {card['image_plan']}\n"
        f"카드가 전하는 메시지(참고용, 그림에 쓰지 말 것): {card.get('line', card['title'])}\n"
        f"공통 그림체(모든 카드가 같아야 함): {style_text(style)}\n"
        "조건: 그림 안에 글자·숫자·로고·워터마크를 절대 넣지 마. 위쪽 35%는 제목을 올릴 수 있게 배경색만 있는 여백으로 비워 둬. "
        "주요 소재는 가운데와 아래쪽에 배치해. 실제 제품 화면이나 실제 사건 사진처럼 보이지 않게 일러스트로 그려 줘. 셸 명령은 실행하지 마."
    )


def _verify(path):
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            fmt, size = im.format, im.size
    except Exception as e:
        raise ImageError("image_failed", f"이미지 파일을 열 수 없습니다: {e}")
    if min(size) < MIN_SIDE:
        raise ImageError("image_failed", f"이미지가 너무 작습니다: {size}")
    return fmt, size


class AgyImage:
    name = "agy"

    def __init__(self):
        self.proc = None

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)

    def _call(self, args, workdir, cancel, timeout, tag):
        """agy를 한 번 실행하고 마지막 줄의 JSON을 돌려준다. 로그인 대기는 바로 끊는다."""
        out_path, err_path = workdir / f"{tag}.json", workdir / f"{tag}.stderr.txt"
        started = time.time()
        with open(out_path, "w") as out, open(err_path, "w") as err:
            try:
                self.proc = subprocess.Popen(args, cwd=workdir, stdout=out, stderr=err,
                                             stdin=subprocess.DEVNULL, start_new_session=True)
            except FileNotFoundError:
                raise ImageError("image_failed", "agy 명령을 찾을 수 없습니다. Antigravity CLI를 설치하세요.")
            while self.proc.poll() is None:
                if cancel.is_set():
                    self.cancel()
                    raise InterruptedError
                text = out_path.read_text(errors="ignore") + err_path.read_text(errors="ignore")
                if "Authentication required" in text or "Please sign in" in text:
                    self.cancel()  # 로그인 대기로 멈춰 있지 않게 바로 끊는다
                    raise ImageError("auth_required", "agy 로그인이 필요합니다. 터미널에서 agy를 실행해 로그인한 뒤 재시도하세요.")
                if time.time() - started > timeout:
                    self.cancel()
                    raise ImageError("image_failed", "agy 응답 시간이 초과되었습니다.")
                time.sleep(0.5)
        raw = out_path.read_text(errors="ignore")
        try:
            data = json.loads(raw.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            raise ImageError("image_failed", f"agy 출력이 JSON이 아닙니다: {raw[:200]}")
        if data.get("status") != "SUCCESS":
            raise ImageError("image_failed", f"agy 실패: {data.get('error') or data.get('status')}")
        return data

    @staticmethod
    def _wait_file(folder, cancel, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if cancel.is_set():
                raise InterruptedError
            if folder and folder.is_dir():
                files = sorted((p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT),
                               key=lambda p: p.stat().st_mtime)
                if files and time.time() - files[-1].stat().st_mtime > 2:  # 쓰기가 끝난 파일
                    return files[-1]
            time.sleep(2)
        return None

    def generate(self, card, workdir, cancel, style=None, pid=None, timeout=420):
        """배경 이미지를 만들어 원본 경로와 기록을 돌려준다."""
        workdir.mkdir(parents=True, exist_ok=True)
        prompt = build_prompt(card, style)
        opts = ["--output-format", "json", "--mode", "accept-edits", "--print-timeout", "300s"]
        data = self._call([AGY, "-p", prompt, *opts], workdir, cancel, timeout, "agy")
        cid = data.get("conversation_id")
        folder = BRAIN / cid if cid else None
        # agy는 이미지 생성을 하위 작업에 맡기고 먼저 끝날 수 있다(1.4.6 실측).
        # 파일이 없으면 같은 대화를 이어 하위 작업이 끝날 때까지 기다리게 하고, 실패 이유를 받아 온다.
        found = self._wait_file(folder, cancel, 60)
        if not found and cid:
            follow = self._call([AGY, "--conversation", cid, "-p",
                                 "앞에서 시작한 이미지 생성 하위 작업이 끝날 때까지 기다려. 다른 파일은 읽지 마. "
                                 "이미지가 저장되면 그 파일 경로만, 실패했으면 실패 이유를 그대로 답해.", *opts],
                                workdir, cancel, timeout, "agy-wait")
            reply = follow.get("response", "")
            if any(k in reply for k in ("429", "RESOURCE_EXHAUSTED", "quota", "쿼터")):
                raise ImageError("quota_exceeded", f"agy 이미지 사용량(쿼터)이 초과되었습니다. agy 응답: {reply.strip()[:300]}")
            found = self._wait_file(folder, cancel, 60)
            if not found:
                raise ImageError("image_failed", f"agy가 이미지 파일을 만들지 못했습니다. agy 응답: {reply.strip()[:300]}")
        if not found:
            raise ImageError("image_failed", "agy가 SUCCESS를 반환했지만 이미지 파일이 만들어지지 않았습니다.")
        fmt, size = _verify(found)
        local = workdir / f"source{found.suffix.lower()}"
        shutil.copy2(found, local)
        return local, {
            "kind": "generated", "tool": f"Antigravity CLI (agy) {_agy_version()}",
            "conversation_id": cid, "created": datetime.now().isoformat(timespec="seconds"),
            "prompt": prompt, "original_format": fmt, "original_size": list(size),
            "note": "AI 생성 이미지. 실제 제품 화면·사건 사진이 아님.",
        }


class MockImage:
    """테스트용. Pillow로 그라데이션을 그린다. 결과에 mock 표시."""
    name = "mock"

    def cancel(self):
        pass

    def generate(self, card, workdir, cancel, style=None, pid=None, timeout=0):
        workdir.mkdir(parents=True, exist_ok=True)
        time.sleep(0.5)
        if cancel.is_set():
            raise InterruptedError
        im = Image.new("RGB", (1080, 1350))
        d = ImageDraw.Draw(im)
        for y in range(1350):
            c = int(20 + 60 * y / 1350)
            d.line([(0, y), (1080, y)], fill=(c // 2, c // 2, c + 30))
        local = workdir / "source.png"
        im.save(local)
        return local, {"kind": "mock", "tool": "모의 이미지(Pillow 그라데이션)",
                       "created": datetime.now().isoformat(timespec="seconds"),
                       "prompt": build_prompt(card, style), "note": "모의 이미지"}


DRAW_SCHEMA = {
    "type": "object",
    "properties": {
        "background": {"type": "string", "description": "#RRGGBB"},
        "shapes": {"type": "array", "maxItems": 120, "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["rect", "ellipse", "polygon", "line"]},
            "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                       "description": "rect·ellipse는 [[x0,y0],[x1,y1]], polygon·line은 꼭짓점 목록"},
            "fill": {"type": ["string", "null"]}, "outline": {"type": ["string", "null"]},
            "width": {"type": "number"},
        }, "required": ["type", "points"]}},
    },
    "required": ["background", "shapes"],
}


def _color(c):
    if isinstance(c, str) and len(c) == 7 and c.startswith("#"):
        try:
            int(c[1:], 16)
            return c
        except ValueError:
            pass
    return None


class ClaudeDrawImage:
    """agy 대체용. Claude CLI가 그림을 도형 목록(JSON)으로 설계하고, 앱이 Pillow로 그린다.
    사진 같은 이미지는 못 만들고 단순한 플랫 일러스트가 된다."""
    name = "claude_draw"

    def __init__(self):
        from .engines import ClaudeCliEngine
        self.engine = ClaudeCliEngine()

    def cancel(self):
        self.engine.cancel()

    def generate(self, card, workdir, cancel, style=None, pid=None, timeout=0):
        workdir.mkdir(parents=True, exist_ok=True)
        prompt = (
            "카드뉴스용 설명 일러스트를 도형으로 설계해 JSON으로만 답해. 캔버스는 가로 1080, 세로 1350 픽셀, 원점은 왼쪽 위.\n"
            f"이 그림의 역할: {card.get('image_role', '')}\n그릴 내용: {card['image_plan']}\n"
            f"카드 메시지(참고용, 글자로 쓰지 말 것): {card.get('line', card['title'])}\n"
            f"공통 그림체: {style_text(style)}. 색은 이 팔레트 안에서 #RRGGBB로 고른다.\n"
            "규칙: 글자·숫자 모양을 만들지 않는다. y < 470(위쪽 35%)에는 도형을 두지 않고 배경만 둔다. "
            "rect·ellipse·polygon·line을 30~120개 써서 무엇을 그렸는지 알아볼 수 있게 단순한 플랫 일러스트로 만든다."
        )
        out, _ = self.engine._run(pid, prompt, DRAW_SCHEMA, [""], cancel, label="draw")
        if out.get("type") == "error":
            raise ImageError(out["kind"], f"Claude CLI 그림 설계 실패: {out['message']}")
        scale = 2  # 두 배로 그린 뒤 줄여 가장자리를 부드럽게
        bg = _color(out.get("background")) or "#1E2A4A"
        im = Image.new("RGB", (1080 * scale, 1350 * scale), bg)
        d = ImageDraw.Draw(im)
        drawn = 0
        for sh in out.get("shapes", []):
            try:
                pts = [(float(x) * scale, float(y) * scale) for x, y in sh["points"]]
                fill, outline = _color(sh.get("fill")), _color(sh.get("outline"))
                w = max(1, int(float(sh.get("width") or 2) * scale))
                if sh["type"] in ("rect", "ellipse"):
                    (x0, y0), (x1, y1) = pts[0], pts[1]
                    box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
                    (d.rectangle if sh["type"] == "rect" else d.ellipse)(box, fill=fill, outline=outline, width=w)
                elif sh["type"] == "polygon" and len(pts) >= 3:
                    d.polygon(pts, fill=fill, outline=outline, width=w)
                elif sh["type"] == "line" and len(pts) >= 2:
                    d.line(pts, fill=outline or fill or "#FFFFFF", width=w, joint="curve")
                else:
                    continue
                drawn += 1
            except (KeyError, ValueError, TypeError):
                continue
        if drawn < 5:
            raise ImageError("image_failed", f"Claude CLI가 그릴 수 있는 도형을 {drawn}개만 돌려줬습니다.")
        local = workdir / "source.png"
        im.resize((1080, 1350), Image.LANCZOS).save(local, "PNG")
        fmt, size = _verify(local)
        (workdir / "shapes.json").write_text(json.dumps(out, ensure_ascii=False))
        return local, {
            "kind": "generated", "tool": "Claude CLI(도형 설계) + Pillow(그리기)",
            "created": datetime.now().isoformat(timespec="seconds"), "prompt": prompt,
            "original_format": fmt, "original_size": list(size), "shapes": drawn,
            "note": "AI가 설계한 도형으로 그린 일러스트. 실제 제품 화면·사건 사진이 아님.",
        }


_version = None


def _agy_version():
    global _version
    if _version is None:
        try:
            _version = subprocess.run([AGY, "--version"], capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[-1]
        except Exception:
            _version = "버전 미확인"
    return _version


def get_image_backend():
    return {"agy": AgyImage, "mock": MockImage}[os.environ.get("CARDNEWS_IMAGE", "agy")]()
