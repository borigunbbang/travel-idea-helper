"""Claude CLI에 보내는 지시문과 결과 JSON 스키마. 편집 규칙은 PRD 3~4절, DECISIONS 11~13."""
import json

_CANDIDATE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string", "description": "모델/제품/연구/개발 도구/정책 등"},
        "summary": {"type": "string"},
        "published": {"type": ["string", "null"], "description": "원문 게시일 YYYY-MM-DD, 확인 못하면 null"},
        "event_date": {"type": ["string", "null"]},
        "url": {"type": "string"},
        "related_urls": {"type": "array", "items": {"type": "string"}},
        "value": {"type": "string"},
        "uncertainty": {"type": "string"},
    },
    "required": ["title", "category", "summary", "published", "url", "value", "uncertainty"],
}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "search_failed"]},
        "failure_reason": {"type": ["string", "null"]},
        "candidates": {"type": "array", "items": _CANDIDATE},
    },
    "required": ["status", "candidates"],
}

_CARD = {
    "type": "object",
    "properties": {
        "role": {"type": "string", "description": "표지/본문/요약"},
        "title": {"type": "string", "description": "짧은 제목"},
        "line": {"type": "string", "description": "핵심 문장 1개. 이 카드가 전하는 단 하나의 메시지"},
        "body": {"type": "string", "description": "핵심 문장을 뒷받침하는 본문 1~2문장(80자 안팎)"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "image_role": {"type": "string", "description": "이미지가 이 카드에서 맡는 역할. 예: 개념을 비유로 설명, 과정을 도식으로 보여 줌"},
        "image_plan": {"type": "string", "description": "무엇을 어떻게 그릴지(구도·소재). 색·그림체는 visual_style을 따름"},
    },
    "required": ["role", "title", "line", "body", "sources", "image_role", "image_plan"],
}

_STYLE = {
    "type": ["object", "null"],
    "description": "모든 카드 이미지에 공통으로 쓸 그림체",
    "properties": {
        "palette": {"type": "string", "description": "주조색·보조색·강조색 3~4개(색 이름이나 HEX)"},
        "style": {"type": "string", "description": "그림체. 예: 플랫 벡터 일러스트, 단순한 선과 면, 그림자 없음"},
        "mood": {"type": "string"},
    },
    "required": ["palette", "style", "mood"],
}

DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["need_input", "result", "search_failed"]},
        "failure_reason": {"type": ["string", "null"]},
        "notes": {"type": "object", "properties": {
            "facts": {"type": "array", "items": {"type": "string"}},
            "sources": {"type": "array", "items": {"type": "string"}},
            "limits": {"type": "array", "items": {"type": "string"}},
            "conflicts": {"type": "array", "items": {"type": "string"}},
        }},
        "question": {"type": ["object", "null"], "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "object", "properties": {
                "value": {"type": "string"}, "desc": {"type": "string"}}, "required": ["value", "desc"]}},
            "recommended": {"type": "string"}, "reason": {"type": "string"},
        }},
        "editorial": {"type": ["object", "null"], "properties": {
            "audience": {"type": "string"}, "why": {"type": "string"},
            "format_comparison": {"type": "array", "items": {"type": "object", "properties": {
                "format": {"type": "string"}, "fits": {"type": "string"}, "tradeoff": {"type": "string"}}}},
            "format": {"type": "string"}, "format_reason": {"type": "string"},
            "hooks": {"type": "array", "items": {"type": "string"}},
        }},
        "storyboard": {"type": ["array", "null"], "items": _CARD},
        "visual_style": _STYLE,
    },
    "required": ["status"],
}

EDITORIAL_RULES = """편집 규칙:
- 원문을 직접 확인하고 발표 기관·논문·공식 문서를 우선한다. 기사 제목만으로 사실을 확정하지 않는다.
- 사실, 출처, 한계, 상충 내용을 notes에 구분해 적는다.
- 공개 예정인 것을 출시된 것으로, 업체 주장을 독립 검증된 사실로 바꾸지 않는다.
- 카드 형식은 두 가지를 format_comparison에 비교하고 하나를 추천한다. 기본 가설은 '목록형'이다
  (이번 주 뉴스를 한눈에 빠르게 볼 수 있어 시간이 적거나 최신 트렌드를 빠르게 파악하려는 독자에게 맞음).
  조사 결과나 독자가 맞지 않으면 다른 형식을 추천하고 이유를 적는다.
- 제목·표지의 '최신', '이번 주' 같은 표현은 조사 기준일과 원문 게시일로 뒷받침될 때만 쓴다.
- 후킹 문구 2~3개는 확인된 사실 범위 안에서만 쓴다. 확인하지 않은 기능이나 효과는 넣지 않는다.
- 스토리보드는 editorial.format에서 추천한 형식을 그대로 따르고, 반드시 5~8장으로 만든다.
  표지는 관심을 끌고, 본문 카드는 하나의 핵심, 마지막은 요약 또는 다음 행동.
- 한 장에는 메시지 하나만 담는다. line(핵심 문장)은 한 문장, body(본문)는 그 문장을 뒷받침하는 1~2문장이다.
  두 가지 소식이나 주장을 한 장에 섞지 않는다.
- 각 카드에 근거 출처 URL을 넣는다. 근거 없는 순위·수치·과장은 금지.
- 이미지는 분위기용 배경이 아니라 그 카드의 메시지를 돕는 설명용 일러스트다. image_role에 이미지가 맡는 역할,
  image_plan에 무엇을 어떻게 그릴지 적는다. 그림 안에 글자·숫자·로고를 넣지 않는다.
- visual_style에 모든 카드가 함께 쓸 색감(palette)과 그림체(style), 분위기(mood)를 하나만 정한다.
  카드마다 그림체를 바꾸지 않는다. 위쪽 35%는 제목을 올릴 수 있게 비워 두는 구도를 전제로 한다."""


def research(topic, now, start, days):
    return f"""너는 카드뉴스 조사 담당이다. 반드시 WebSearch로 실제 검색하고, 필요하면 WebFetch로 원문을 확인한다.
주제: {json.dumps(topic, ensure_ascii=False)}
조사 기준 시각: {now}
조사 기간: {start} ~ {now} (최근 {days}일)

할 일:
- 기간 안에 게시된 서로 다른 후보를 7~12개 찾는다. 같은 발표를 다룬 기사는 하나로 묶고 나머지 URL은 related_urls에 넣는다.
- 주제에 맞게 모델·제품·연구·개발 도구 등 범주를 다양하게 한다.
- published는 원문에서 확인한 게시일만 쓰고, 확인 못하면 null. 기간 밖 기사는 넣지 않는다.
- value에는 독자에게 선택할 가치, uncertainty에는 불확실한 점(발표 예정, 업체 주장, 날짜 미확인 등)을 쓴다.
- 기억이나 과거 지식으로 후보를 만들지 않는다. 검색 도구를 쓸 수 없거나 결과가 없으면 status="search_failed"와 failure_reason을 쓴다.
- 결과가 적으면 찾은 만큼만 넣는다."""


def deep_research(topic, selected, now):
    return f"""너는 카드뉴스 편집자다. 주제 {json.dumps(topic, ensure_ascii=False)}, 조사 기준 시각 {now}.
사용자가 고른 후보:
{json.dumps(selected, ensure_ascii=False, indent=1)}

선택한 후보의 원문과 1차 출처를 WebSearch/WebFetch로 확인해 notes를 만든다.
그다음 독자, 독자에게 유용한 이유, 카드 형식, 후킹 문구를 정한다.
- 독자는 네가 추측해 정하지 않는다. 주제 문구에 독자가 드러나지 않았고(예: '개발자를 위한', '초등 교사용' 같은 표현이 없음),
  선택한 후보를 독자에 따라 다르게 써야 한다면(예: 일반 사용자에게는 생활 영향, 개발자에게는 API·버전·패치) status="need_input"으로 question을 채운다.
  question에는 질문, 선택지 3개 안팎(value와 차이를 설명하는 한 줄 desc), recommended, 추천 reason을 넣는다. editorial/storyboard는 null.
  value와 recommended는 화면에 그대로 보이므로 '일반 사용자', '개발자'처럼 짧은 한국어로 쓴다(영문 코드 금지).
- 주제 문구에 독자가 드러나거나, 어느 독자에게나 같은 내용이 되는 경우에만 status="result"로 editorial과 storyboard를 채우고 why에 그렇게 판단한 근거를 쓴다.
- 원문 확인 도구를 쓸 수 없으면 status="search_failed".

{EDITORIAL_RULES}"""


REVISE_SCHEMA = {
    "type": "object",
    "properties": {"cards": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "title": {"type": "string"}, "line": {"type": "string"},
        "body": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}},
        "image_role": {"type": "string"}, "image_plan": {"type": "string"},
        "regenerate_image": {"type": "boolean", "description": "그림을 다시 만들어야 하면 true"},
    }, "required": ["id", "title", "line", "body", "sources", "image_role", "image_plan", "regenerate_image"]}}},
    "required": ["cards"],
}


def revise(cards, instruction):
    return f"""사용자가 아래 카드만 수정해 달라고 했다. 다른 카드는 바꾸지 않는다.
수정 요청: {json.dumps(instruction, ensure_ascii=False)}
대상 카드:
{json.dumps(cards, ensure_ascii=False, indent=1)}

대상 카드마다 id를 그대로 두고 title, line, body, sources, image_role, image_plan을 고쳐서 돌려준다.
그림체(visual_style)는 다른 카드와 같게 유지한다.
그림을 바꿔야 하는 요청이면 regenerate_image=true, 글자만 바뀌면 false.
앞에서 확인한 조사 기록 범위 안에서만 고치고, 확인하지 않은 사실을 새로 넣지 않는다.

{EDITORIAL_RULES}"""


def answer(question, answer_text):
    return f"""사용자가 웹 화면에서 답했다.
질문: {question}
답변: {json.dumps(answer_text, ensure_ascii=False)}

이 답을 반영해 앞에서 확인한 notes를 바탕으로 status="result"로 editorial과 storyboard를 완성한다.
추가 확인이 꼭 필요할 때만 다시 status="need_input"을 쓴다.

{EDITORIAL_RULES}"""
