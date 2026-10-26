"""쟁점별 근거 점검. AI가 매긴 확신도를 그대로 믿지 않고, 실제로 붙은 출처로 상한을 정한다.

- 출처가 없으면 '부족'
- 출처가 하나뿐이거나 공식 자료가 없으면, 또는 AI가 부족한 점(gaps)을 적었으면 최대 '일부'
- 그 밖에는 AI의 확신도
앱이 찾아낸 부족한 점은 auto_gaps로 따로 적어 화면에서 AI의 gaps와 구분한다.
"""
from datetime import date

LEVELS = ["부족", "일부", "충분"]
OLD_DAYS = 365  # 가격·운영 시간은 1년이 지나면 바뀌었을 수 있다


def _day(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def assess(issue, today=None):
    today = today or date.today()
    ev = [e for e in issue.get("evidence", []) if str(e.get("url", "")).startswith(("http://", "https://"))]
    official = [e for e in ev if e.get("source_type") == "공식"]
    undated = [e for e in ev if not _day(e.get("published"))]
    old = [e for e in ev if _day(e.get("published")) and (today - _day(e["published"])).days > OLD_DAYS]

    ai = issue.get("confidence") if issue.get("confidence") in LEVELS else "부족"
    cap, auto = "충분", []
    if not ev:
        cap = "부족"
        auto.append("근거 출처가 없다. 이 쟁점의 판단은 확인되지 않았다.")
    else:
        if len(ev) == 1:
            cap = "일부"
            auto.append("출처가 하나뿐이다. 다른 자료로 교차 확인하지 못했다.")
        if not official:
            cap = "일부"
            auto.append("공식 자료(관광청·운영 기관·교통사·정부 등)를 확인하지 못했다.")
    if issue.get("gaps") and cap == "충분":
        cap = "일부"
    if undated:
        auto.append(f"게시일을 확인 못 한 출처 {len(undated)}개.")
    if old:
        auto.append(f"1년 넘은 자료 {len(old)}개. 가격·운영 시간은 바뀌었을 수 있다.")
    level = LEVELS[min(LEVELS.index(ai), LEVELS.index(cap))]
    if level != "충분" and not auto and not issue.get("gaps"):
        auto.append(f"AI가 근거를 '{ai}'(으)로 봤지만 무엇이 부족한지 적지 않았다.")
    return {"level": level, "ai_level": ai, "auto_gaps": auto,
            "counts": {"evidence": len(ev), "official": len(official), "undated": len(undated), "old": len(old)}}


def summary(issues):
    out = {k: 0 for k in LEVELS}
    for i in issues:
        out[i["check"]["level"]] += 1
    return out
