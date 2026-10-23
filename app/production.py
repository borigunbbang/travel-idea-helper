"""카드 제작·부분 수정·내보내기. 카드마다 버전을 쌓고, 승인된 카드는 수정 대상이 아니면 건드리지 않는다."""
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import store
from .images import ClaudeDrawImage, ImageError, get_image_backend
from .render import render

PARALLEL = 3
_lock = threading.Lock()


def cards_from_storyboard(storyboard):
    return [dict(c, id=f"k{i+1}", index=i + 1, status="pending", error=None, versions=[], current=None)
            for i, c in enumerate(storyboard)]


def pick_card_bg(style):
    """공통 그림체 색감에서 카드 배경색 하나를 고른다. '바탕·배경'이라고 적힌 색, 없으면 가장 밝은 색."""
    import re
    from .render import _hex, _luminance
    text = (style or {}).get("palette", "")
    marked = re.findall(r"(?:바탕|배경)[^#,/]*?(#[0-9A-Fa-f]{6})|(#[0-9A-Fa-f]{6})[^,/]*?(?:바탕|배경)", text)
    for a, b in marked:
        if a or b:
            return (a or b).upper()
    hexes = re.findall(r"#[0-9A-Fa-f]{6}", text)
    return max(hexes, key=lambda h: _luminance(_hex(h))).upper() if hexes else "#F5EFE0"


def known_urls(doc):
    urls = set()
    for c in doc["candidates"]:
        urls.add(c["url"])
        urls.update(c.get("related_urls") or [])
    urls.update((doc.get("notes") or {}).get("sources") or [])
    return urls


def source_check(card, urls):
    """사실/출처 점검: 본문 카드는 출처가 있어야 하고, 출처는 조사에서 확인한 URL이어야 한다."""
    if card["role"] == "본문" and not card["sources"]:
        return {"ok": False, "detail": "본문 카드에 근거 출처가 없음"}
    unknown = [u for u in card["sources"] if u not in urls and not any(u.split("#")[0] in k for k in urls)]
    if unknown:
        return {"ok": False, "detail": f"조사 기록에 없는 출처: {', '.join(unknown)}"}
    return {"ok": True, "detail": f"출처 {len(card['sources'])}개, 모두 조사 기록에 있음"}


def _set_card(pid, cid, **fields):
    def f(d):
        for c in d["cards"]:
            if c["id"] == cid:
                c.update(fields)
    with _lock:
        store.update_project(pid, f)


def _generate_image(pid, card, vdir, style, cancel, backends):
    """agy로 그린다. agy가 실패하면 Claude CLI 도형 그림으로 대신하고, 대신한 이유를 기록한다."""
    backend = get_image_backend()
    backends.append(backend)
    try:
        if backend.name == "agy" and getattr(cancel, "agy_quota", None):
            raise cancel.agy_quota  # 이번 제작에서 이미 쿼터 초과를 확인했으면 agy를 다시 기다리지 않는다
        return backend.generate(card, vdir, cancel, style=style, pid=pid)
    except ImageError as first:
        if backend.name != "agy":
            raise
        if first.kind == "quota_exceeded":
            cancel.agy_quota = first
        fallback = ClaudeDrawImage()
        backends.append(fallback)
        try:
            bg, meta = fallback.generate(card, vdir / "fallback", cancel, style=style, pid=pid)
        except ImageError as second:
            raise ImageError(first.kind, f"agy 실패: {first} / Claude CLI 대체도 실패: {second}")
        meta["fallback_reason"] = f"agy 실패({first.kind}): {first}"
        return bg, meta


def _make_card(pid, card, regenerate, cancel, backends):
    """카드 하나: 필요하면 배경 생성 → 글자 렌더링 → 새 버전 추가."""
    _set_card(pid, card["id"], status="running", error=None)
    d = store.get_project(pid)
    v = len(card["versions"]) + 1
    vdir = store.project_dir(pid) / "cards" / card["id"] / f"v{v}"
    prev = card["versions"][-1] if card["versions"] else None
    try:
        style = dict(d.get("visual_style") or {}, background=d.get("card_bg"))
        if regenerate or not prev:
            bg, meta = _generate_image(pid, card, vdir, style, cancel, backends)
        else:  # 글자만 바뀐 수정은 이전 배경을 재사용
            vdir.mkdir(parents=True, exist_ok=True)
            bg, meta = store.project_dir(pid) / prev["bg"], prev["image"]
        png = vdir / "card.png"
        lay = dict(card.get("layout") or {})
        if d.get("card_bg"):
            lay["bg"] = d["card_bg"]
        checks, problems, layout = render(card, bg, png, card["index"], len(d["cards"]), d["research"]["searched_at"],
                                          meta["kind"], lay)
        checks["sources"] = source_check(card, known_urls(d))
    except ImageError as e:
        _set_card(pid, card["id"], status="failed", error={"kind": e.kind, "message": str(e)})
        return False
    except InterruptedError:
        _set_card(pid, card["id"], status="failed", error={"kind": "cancelled", "message": "취소됨"})
        raise
    except Exception as e:
        _set_card(pid, card["id"], status="failed", error={"kind": "image_failed", "message": f"렌더링 실패: {e}"})
        return False
    root = store.project_dir(pid).resolve()
    version = {"v": v, "title": card["title"], "line": card["line"], "body": card.get("body", ""),
               "sources": card["sources"], "image_role": card.get("image_role", ""), "image_plan": card["image_plan"], "bg": str(Path(bg).resolve().relative_to(root)),
               "png": str(png.resolve().relative_to(root)), "image": meta, "checks": checks,
               "problems": problems, "layout": layout,
               "created": datetime.now().isoformat(timespec="seconds")}

    def f(doc):
        for c in doc["cards"]:
            if c["id"] == card["id"]:
                c["versions"].append(version)
                c.update(current=v, status="done", error=None)
    with _lock:
        store.update_project(pid, f)
    return True


def produce(pid, targets, cancel, backends):
    """targets: {card_id: regenerate_image(bool)}. 대상 밖의 카드는 읽기만 한다."""
    d = store.get_project(pid)
    cards = [c for c in d["cards"] if c["id"] in targets]
    with ThreadPoolExecutor(PARALLEL) as ex:
        futures = [ex.submit(_make_card, pid, c, targets[c["id"]], cancel, backends) for c in cards]
        for fu in futures:
            fu.result()
    finish(pid)


def finish(pid):
    def f(d):
        failed = [c for c in d["cards"] if c["status"] != "done"]
        if failed:
            kinds = {c["error"]["kind"] for c in failed if c.get("error")}
            kind = "auth_required" if "auth_required" in kinds else "image_failed"
            d["error"] = {"kind": kind, "message": f"{len(failed)}장 제작 실패: " + ", ".join(
                f"{c['index']}번({(c.get('error') or {}).get('message', '대기')})" for c in failed), "action": "실패한 카드 재시도"}
            d["stage"] = "producing"
        else:
            d["error"] = None
            d["stage"] = "final_review"
    store.update_project(pid, f)


def blocking_issues(d):
    issues = []
    for c in d["cards"]:
        if c["status"] != "done":
            issues.append(f"{c['index']}번 카드가 완성되지 않음")
            continue
        cur = c["versions"][c["current"] - 1]
        for name, chk in cur["checks"].items():
            if not chk["ok"]:
                issues.append(f"{c['index']}번 {name}: {chk['detail']}")
    return issues


def export(pid):
    """최종 승인된 버전만 내보낸다. PNG, 출처·이미지 사용 기록, 전체 ZIP."""
    d = store.get_project(pid)
    ap = d["approved"]
    root = store.project_dir(pid)
    out = root / "export" / f"approved-v{ap['version']}"
    out.mkdir(parents=True, exist_ok=True)
    files, credits, lines = [], [], [f"# {d['topic']} 카드뉴스 출처 기록", "",
                                     f"- 조사 기준 시각: {d['research']['searched_at']}",
                                     f"- 조사 기간: 최근 {d['research']['period_days']}일", ""]
    for c in d["cards"]:
        ver = c["versions"][ap["cards"][c["id"]] - 1]
        name = f"card_{c['index']:02d}.png"
        (out / name).write_bytes((root / ver["png"]).read_bytes())
        files.append(name)
        srcs = [f"- {u}" for u in ver["sources"]] or ["- 출처 없음"]
        lines += [f"## {c['index']}. [{c['role']}] {ver['title']}", *srcs, ""]
        credits.append({"card": name, **{k: ver["image"].get(k) for k in ("kind", "tool", "created", "prompt", "conversation_id", "fallback_reason", "note")}})
    (out / "sources.md").write_text("\n".join(lines))
    (out / "image_credits.json").write_text(json.dumps(credits, ensure_ascii=False, indent=1))
    with zipfile.ZipFile(out / "cardnews.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for n in files + ["sources.md", "image_credits.json"]:
            z.write(out / n, n)
    return [str((out / n).relative_to(root)) for n in files + ["sources.md", "image_credits.json", "cardnews.zip"]]
