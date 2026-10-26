"""해설 문서 만들기와 내보내기. 쟁점마다 버전을 남기고, 최종 승인한 버전만 파일로 내보낸다."""
import html
import json
import shutil
import time
import zipfile

from . import checks, store

LEVEL_TEXT = {"충분": "근거 충분", "일부": "근거 일부", "부족": "자료 부족"}
ISSUE_FIELDS = ("question", "short_answer", "explanation", "evidence", "confidence", "gaps")


def issues_from_guide(guide):
    out = []
    for n, i in enumerate(guide["issues"], 1):
        issue = {"id": f"i{n}", "n": n, "concept_id": i["concept_id"], "v": 1, "history": [],
                 "created": time.time(), **{k: i[k] for k in ISSUE_FIELDS}}
        issue["check"] = checks.assess(issue)
        out.append(issue)
    return out


def apply_revision(issue, new, instruction):
    """이전 내용을 history에 남기고 새 버전으로 바꾼다."""
    issue["history"].append({k: issue[k] for k in ("v", "created", "check", *ISSUE_FIELDS)} | {"instruction": issue.get("instruction")})
    issue.update({k: new[k] for k in ISSUE_FIELDS if k in new})
    issue.update(v=issue["v"] + 1, created=time.time(), instruction=instruction)
    issue["check"] = checks.assess(issue)


def _when(iso):
    return str(iso)[:16].replace("T", " ")


def _concepts(d):
    return [c for c in d["concepts"] if c["id"] in d["selection"]]


def markdown(d):
    g = d["guide"]
    s = checks.summary(d["issues"])
    L = [f"# 여행 구상 해설: {d['topic']}", "",
         f"- 조사 기준 시각: {_when(d['research']['searched_at'])}",
         f"- 최종 승인 버전: v{d['approved']['version']}",
         f"- 쟁점 {len(d['issues'])}개: 근거 충분 {s['충분']} · 근거 일부 {s['일부']} · 자료 부족 {s['부족']}"]
    if d["research"].get("mock"):
        L.append("- ⚠ 모의 데이터로 만든 문서다. 실제 검색 결과가 아니다.")
    for a in d["answers"]:
        L.append(f"- 받은 답변: {a['question']} → {a['answer']}")
    L += ["", "## 요약", "", g["summary"], ""]
    if g.get("overall_gaps"):
        L += ["## 전체적으로 부족한 자료", ""]
        L += [f"- [자료 부족] {x['what']} — {x['why']} (확인할 곳: {x['where_to_check']})" for x in g["overall_gaps"]]
        L.append("")
    for c in _concepts(d):
        L += [f"## {c['title']}", "", f"후보 지역: {', '.join(c['places'])}", "", c["pitch"], ""]
        for i in [i for i in d["issues"] if i["concept_id"] == c["id"]]:
            L += [f"### 쟁점 {i['n']}. {i['question']}", "",
                  f"**[{LEVEL_TEXT[i['check']['level']]}]** {i['short_answer']}", "", i["explanation"], ""]
            if i["evidence"]:
                L.append("근거:")
                L += [f"- {e['claim']} — [{e['source_title']}]({e['url']}) · {e['source_type']} · 게시일 {e['published'] or '미확인'}"
                      for e in i["evidence"]]
            else:
                L.append("근거: 없음")
            gaps = [f"{x['what']} — {x['why']} (확인할 곳: {x['where_to_check']})" for x in i["gaps"]] + i["check"]["auto_gaps"]
            if gaps:
                L += ["", "부족한 자료:"] + [f"- [자료 부족] {x}" for x in gaps]
            L.append("")
    return "\n".join(L)


def _h(s):
    return html.escape(str(s or ""))


def html_doc(d):
    g = d["guide"]
    s = checks.summary(d["issues"])
    color = {"충분": "#067647", "일부": "#b54708", "부족": "#b42318"}
    b = [f"<h1>여행 구상 해설: {_h(d['topic'])}</h1>",
         f"<p class=m>조사 기준 시각 {_h(_when(d['research']['searched_at']))} · 최종 승인 v{d['approved']['version']} · "
         f"근거 충분 {s['충분']} · 근거 일부 {s['일부']} · 자료 부족 {s['부족']}</p>"]
    if d["research"].get("mock"):
        b.append("<p class=gap>⚠ 모의 데이터로 만든 문서다. 실제 검색 결과가 아니다.</p>")
    b.append(f"<h2>요약</h2><p>{_h(g['summary'])}</p>")
    if g.get("overall_gaps"):
        b.append("<div class=gap><b>전체적으로 부족한 자료</b><ul>" + "".join(
            f"<li>{_h(x['what'])} — {_h(x['why'])} <span class=m>(확인할 곳: {_h(x['where_to_check'])})</span></li>"
            for x in g["overall_gaps"]) + "</ul></div>")
    for c in _concepts(d):
        b.append(f"<h2>{_h(c['title'])}</h2><p class=m>후보 지역: {_h(', '.join(c['places']))}</p><p>{_h(c['pitch'])}</p>")
        for i in [i for i in d["issues"] if i["concept_id"] == c["id"]]:
            lv = i["check"]["level"]
            b.append(f"<section><h3>쟁점 {i['n']}. {_h(i['question'])}</h3>"
                     f"<p><span class=lv style='background:{color[lv]}'>{LEVEL_TEXT[lv]}</span> <b>{_h(i['short_answer'])}</b></p>"
                     f"<p>{_h(i['explanation'])}</p>")
            b.append("<ul>" + "".join(
                f"<li>{_h(e['claim'])} — <a href='{_h(e['url'])}'>{_h(e['source_title'])}</a> "
                f"<span class=m>· {_h(e['source_type'])} · 게시일 {_h(e['published'] or '미확인')}</span></li>"
                for e in i["evidence"]) + "</ul>" if i["evidence"] else "<p class=m>근거: 없음</p>")
            gaps = [f"{_h(x['what'])} — {_h(x['why'])} <span class=m>(확인할 곳: {_h(x['where_to_check'])})</span>" for x in i["gaps"]]
            gaps += [_h(x) for x in i["check"]["auto_gaps"]]
            if gaps:
                b.append("<div class=gap><b>부족한 자료</b><ul>" + "".join(f"<li>{x}</li>" for x in gaps) + "</ul></div>")
            b.append("</section>")
    css = ("body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:760px;margin:24px auto;padding:0 16px;line-height:1.6;color:#1d1d1f}"
           ".m{color:#6b6b70;font-size:.9em}.gap{background:#fff4e5;border:1px solid #f5c48a;border-radius:8px;padding:8px 12px;margin:8px 0}"
           ".lv{color:#fff;border-radius:99px;padding:1px 8px;font-size:.8em}section{border-top:1px solid #ddd;padding-top:4px}")
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_h(d['topic'])}</title><style>{css}</style></head><body>{''.join(b)}</body></html>")


def sources_json(d):
    return {"topic": d["topic"], "searched_at": d["research"]["searched_at"], "approved_version": d["approved"]["version"],
            "mock": bool(d["research"].get("mock")),
            "issues": [{"n": i["n"], "id": i["id"], "v": i["v"], "question": i["question"], "level": i["check"]["level"],
                        "evidence": i["evidence"], "gaps": i["gaps"], "auto_gaps": i["check"]["auto_gaps"]} for i in d["issues"]]}


def export(pid):
    d = store.get_project(pid)
    out = store.project_dir(pid) / "export" / f"approved-v{d['approved']['version']}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "guide.md").write_text(markdown(d))
    (out / "guide.html").write_text(html_doc(d))
    (out / "sources.json").write_text(json.dumps(sources_json(d), ensure_ascii=False, indent=1))
    # 카드 PNG: 승인한 순간의 카드 묶음을 순서대로 복사하고 ZIP으로도 묶는다
    root = store.project_dir(pid)
    names = []
    for c in d.get("deck") or []:
        name = f"card-{c['page']:02d}-{c['kind']}.png"
        shutil.copyfile(root / c["png"], out / name)
        names.append(name)
    (out / "deck.json").write_text(json.dumps([dict(c, file=n) for c, n in zip(d.get("deck") or [], names)], ensure_ascii=False, indent=1))
    with zipfile.ZipFile(out / "travel-cards.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for n in names + ["guide.md", "sources.json"]:
            z.write(out / n, n)
    return [p.name for p in sorted(out.iterdir())]
