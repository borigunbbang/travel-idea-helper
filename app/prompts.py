"""Claude CLI에 보내는 지시문과 결과 JSON 스키마. 해설 규칙은 PRD 3~4절, DECISIONS 44~."""
import json

_SOURCE = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "title": {"type": "string"},
        "published": {"type": ["string", "null"], "description": "원문 게시일 또는 마지막 수정일 YYYY-MM-DD, 확인 못하면 null"},
    },
    "required": ["url", "title", "published"],
}

_GAP = {
    "type": "object",
    "properties": {
        "what": {"type": "string", "description": "무엇이 부족한가"},
        "why": {"type": "string", "description": "판단에 왜 필요한가"},
        "where_to_check": {"type": "string", "description": "어디서 확인하면 되나(공식 사이트, 운영 기관, 예약 사이트 등)"},
    },
    "required": ["what", "why", "where_to_check"],
}

_CONCEPT = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "여행의 방향을 한 줄로. 예: 겨울 햇살 아래 섬 해변 걷기"},
        "pitch": {"type": "string", "description": "어떤 경험을 하는 여행인지 1~2문장"},
        "places": {"type": "array", "items": {"type": "string"}, "description": "이 개념에 맞는 후보 지역 1~3곳"},
        "fits": {"type": "string", "description": "사용자의 구상에 맞는 이유"},
        "sources": {"type": "array", "items": _SOURCE},
        "uncertainty": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}, "description": "이 개념을 판단하는 데 아직 부족한 자료"},
    },
    "required": ["title", "pitch", "places", "fits", "sources", "uncertainty", "gaps"],
}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "search_failed"]},
        "failure_reason": {"type": ["string", "null"]},
        "conditions": {"type": "object", "properties": {
            "known": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "value": {"type": "string"}}, "required": ["name", "value"]}},
            "missing": {"type": "array", "items": {"type": "string"}},
        }, "required": ["known", "missing"]},
        "concepts": {"type": "array", "items": _CONCEPT},
    },
    "required": ["status", "conditions", "concepts"],
}

_EVIDENCE = {
    "type": "object",
    "properties": {
        "claim": {"type": "string", "description": "이 출처가 실제로 뒷받침하는 구체적 내용"},
        "url": {"type": "string"},
        "source_title": {"type": "string"},
        "published": {"type": ["string", "null"], "description": "YYYY-MM-DD, 확인 못하면 null"},
        "source_type": {"type": "string", "enum": ["공식", "언론", "여행 정보", "개인 글"]},
    },
    "required": ["claim", "url", "source_title", "published", "source_type"],
}

ISSUE = {
    "type": "object",
    "properties": {
        "concept_id": {"type": "string", "description": "이 쟁점이 속한 개념 id(받은 그대로)"},
        "question": {"type": "string", "description": "따져볼 문제를 질문 한 문장으로"},
        "short_answer": {"type": "string", "description": "지금 자료로 내린 판단 1~2문장. 근거가 부족하면 '판단 보류'로 시작"},
        "explanation": {"type": "string", "description": "근거를 이어 설명하는 해설 3~5문장"},
        "evidence": {"type": "array", "items": _EVIDENCE},
        "confidence": {"type": "string", "enum": ["충분", "일부", "부족"]},
        "gaps": {"type": "array", "items": _GAP},
    },
    "required": ["concept_id", "question", "short_answer", "explanation", "evidence", "confidence", "gaps"],
}

_NOTES = {"type": "object", "properties": {
    "facts": {"type": "array", "items": {"type": "string"}},
    "limits": {"type": "array", "items": {"type": "string"}},
    "conflicts": {"type": "array", "items": {"type": "string"}},
}}

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["need_input", "result", "search_failed"]},
        "failure_reason": {"type": ["string", "null"]},
        "notes": _NOTES,
        "question": {"type": ["object", "null"], "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "object", "properties": {
                "value": {"type": "string"}, "desc": {"type": "string"}}, "required": ["value", "desc"]}},
            "recommended": {"type": "string"}, "reason": {"type": "string"},
        }},
        "guide": {"type": ["object", "null"], "properties": {
            "summary": {"type": "string", "description": "고른 개념들을 비교해 어떤 사람에게 어느 쪽이 맞는지 정리한 한 문단"},
            "overall_gaps": {"type": "array", "items": _GAP},
            "issues": {"type": "array", "items": ISSUE},
        }, "required": ["summary", "overall_gaps", "issues"]},
    },
    "required": ["status"],
}

GUIDE_RULES = """해설 규칙:
- 쟁점(문제)은 사용자의 결정을 바꿀 수 있는 질문이다. 개념마다 3~5개. 예: 그 시기 날씨·계절, 이동 시간과 방법,
  비용(항공·숙소·현지), 혼잡·예약, 입국 조건·안전, 그 개념의 핵심 경험이 실제로 가능한지(운영 기간, 휴무).
- 해설은 반드시 원문을 WebSearch/WebFetch로 확인해 쓴다. 관광청·운영 기관·교통사·정부 같은 공식 자료를 우선한다.
- evidence의 claim은 그 출처가 실제로 말하는 내용만 쓴다. 출처에 없는 수치·순위·가격을 만들지 않는다.
- 개인 블로그나 후기 하나만으로 사실을 확정하지 않는다. 추정은 '추정'이라고 쓴다.
- 가격·운영 시간·일정은 바뀌므로 게시일(또는 확인한 날)을 함께 적는다. 확인 못하면 published=null.
- 근거가 없거나 모자라면 숨기지 말고 confidence를 '일부' 또는 '부족'으로 하고 gaps에 무엇이 없는지(what),
  왜 필요한지(why), 어디서 확인하면 되는지(where_to_check)를 적는다. 이때 short_answer는 '판단 보류'로 시작한다.
- 출처끼리 말이 다르면 explanation에 둘 다 적고 notes.conflicts에도 남긴다.
- 쉬운 한국어 평어체로 쓴다."""


def research(topic, now):
    return f"""너는 여행 구상 도우미의 조사 담당이다. 반드시 WebSearch로 실제 검색하고, 필요하면 WebFetch로 원문을 확인한다.
사용자의 여행 구상: {json.dumps(topic, ensure_ascii=False)}
조사 기준 시각: {now}

할 일:
- 구상 문장에서 이미 정해진 조건(시기, 기간, 동행, 예산, 출발지, 좋아하는 것 등)을 conditions.known에 name/value로 뽑는다.
  해설에 필요한데 문장에 없는 조건은 conditions.missing에 이름만 적는다. 추측해서 채우지 않는다.
- 이 구상에 맞는 '여행 핵심 개념'을 5~8개 제안한다. 핵심 개념은 장소 이름 하나가 아니라 어떤 경험을 하는 여행인지를
  한 줄로 정리한 것이고, 그 경험에 맞는 후보 지역(places)을 함께 단다. 서로 성격이 겹치지 않게 고른다.
- sources에는 이 개념을 제안하는 데 실제로 확인한 출처만 넣는다. 확인한 출처가 없으면 빈 배열로 두고 gaps에 적는다.
- uncertainty에는 불확실한 점(시기에 따라 다름, 운영 여부 미확인 등), gaps에는 판단에 아직 부족한 자료를 적는다.
- 기억이나 과거 지식만으로 개념을 만들지 않는다. 검색 도구를 쓸 수 없으면 status="search_failed"와 failure_reason을 쓴다.
- 쉬운 한국어로 쓴다."""


def explain(topic, conditions, selected, answers, now):
    prev = "\n".join(f"- {a['question']} → {a['answer']}" for a in answers) or "없음"
    return f"""너는 여행 구상 해설가다. 조사 기준 시각 {now}.
사용자의 여행 구상: {json.dumps(topic, ensure_ascii=False)}
구상에서 뽑은 조건: {json.dumps(conditions, ensure_ascii=False)}
앞서 받은 답변:
{prev}
사용자가 고른 핵심 개념(id 포함):
{json.dumps(selected, ensure_ascii=False, indent=1)}

할 일:
- 고른 개념마다 따져볼 쟁점을 세우고, 원문을 확인해 근거가 있는 해설을 쓴다. 각 쟁점의 concept_id는 받은 id 그대로 쓴다.
- 시기·예산·동행처럼 쟁점의 답을 크게 바꾸는 조건이 정해지지 않았고(conditions.missing과 앞선 답변을 본다),
  그 조건에 따라 해설이 달라진다면 먼저 status="need_input"으로 question을 채운다. 질문, 선택지 3개 안팎
  (value는 '12월 말 연휴'처럼 짧은 한국어, desc는 고르면 무엇이 달라지는지 한 줄), recommended, 추천 reason. guide는 null.
- 조건이 충분하거나 어느 쪽이든 해설이 같으면 status="result"로 guide를 채운다. 정하지 않은 조건은 해설에 가정으로 밝힌다.
- 원문 확인 도구를 쓸 수 없으면 status="search_failed".
- notes에 확인한 사실(facts), 한계(limits), 출처끼리 다른 내용(conflicts)을 구분해 적는다.

{GUIDE_RULES}"""


def answer(question, answer_text):
    return f"""사용자가 웹 화면에서 답했다.
질문: {question}
답변: {json.dumps(answer_text, ensure_ascii=False)}

이 답을 반영해 status="result"로 guide를 완성한다. 앞에서 확인한 자료를 쓰고, 답 때문에 새로 확인할 것은 검색한다.
추가 확인이 꼭 필요할 때만 다시 status="need_input"을 쓴다.

{GUIDE_RULES}"""


REVISE_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, **ISSUE["properties"]},
        "required": ["id", *ISSUE["required"]],
    }}},
    "required": ["issues"],
}


def revise(issues, instruction):
    return f"""사용자가 아래 쟁점만 고쳐 달라고 했다. 다른 쟁점은 바꾸지 않는다.
수정 요청: {json.dumps(instruction, ensure_ascii=False)}
대상 쟁점:
{json.dumps(issues, ensure_ascii=False, indent=1)}

요청이 자료를 더 찾아 달라는 것이면 WebSearch/WebFetch로 실제로 찾는다. 찾지 못하면 지어내지 말고 gaps와 confidence를 그대로 둔다.
대상 쟁점마다 id와 concept_id를 그대로 두고 모든 필드를 채워 돌려준다. 요청에 '짧게' 같은 조건이 있으면 지킨다.

{GUIDE_RULES}"""
