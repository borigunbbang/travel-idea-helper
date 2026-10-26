# API 명세 (여행 구상 도우미)

> 2026-10-23 카드뉴스 API를 여행 구상 API로 바꿨다(DECISIONS 44). 카드뉴스 API는 git 기록에 있다.

모든 요청·응답은 JSON. 오류는 `{"detail": {"code": "...", "message": "..."}}`.
변경 요청은 화면이 본 `version`을 보내며, 다르면 409 `stale_version`. `requestId`가 있는 요청은 같은 ID·같은 입력이면 저장한 응답을, 같은 ID·다른 입력이면 409 `request_conflict`.

## 단계(stage)
```
created → researching → selecting → explaining ⇄ waiting_for_user → review → approved
```
- 고치기(`revisions`)는 review/approved에서 가능하고, 하면 `approved=null`, stage=review.
- 실행 중 작업은 `active_run`으로 보인다. 2초마다 `GET /api/projects/{id}`로 다시 그린다.
- 오류는 `error.kind`: auth_required / search_failed / engine_failed / interrupted / cancelled.

## 엔드포인트
| 요청 | 입력 | 결과 | 주요 오류 |
|---|---|---|---|
| `POST /api/projects` | `topic` | `projectId` | 400 empty_topic |
| `GET /api/projects` | | 목록 요약 | |
| `GET /api/projects/{id}` | | 프로젝트 문서 + `runs`, `active_run`, `workers` | 404 |
| `POST /api/projects/{id}/research` | `requestId` | `runId` | 409 already_running, wrong_stage(개념 선택 뒤) |
| `POST /api/projects/{id}/selection` | `conceptIds`(1~3), `version` | `runId` | 409 wrong_stage, 400 bad_concept |
| `POST /api/projects/{id}/answers` | `questionId`, `version`(질문 버전), `answer`, `requestId` | `accepted` | 409 stale_question |
| `POST /api/projects/{id}/revisions` | `issueIds`, `instruction`, `version`, `requestId` | `runId` | 400 bad_issue, empty_instruction |
| `POST /api/projects/{id}/approve` | `version`, `requestId` | `files`, `checkRunId` | 409 wrong_stage |
| `POST /api/runs/{id}/cancel` | | `status` | |
| `POST /api/projects/{id}/photos/retry` | | `state` | |
| `GET /api/projects/{id}/exports` | | `approvedVersion`, `files`, `check` | 409 not_approved |
| `POST /api/projects/{id}/exports/check` | | `runId` | 409 not_approved |
| `GET /api/projects/{id}/files/{path}` | | 파일 | 404 |

`selection`은 explaining 단계에서 오류가 났을 때도 받아 해설을 새 세션으로 다시 만든다(재시도).

## 프로젝트 문서
```
{
  id, topic, stage, version, engine, session_id, error, notice, created,
  research: { searched_at, mock, conditions: { known: [{name, value}], missing: [string] } },
  concepts: [{ id: "k1", title, pitch, places: [], fits, uncertainty, gaps: [string],
               sources: [{ url, title, published|null }] }],
  selection: ["k1", ...],
  question: { id, version, question, options: [{value, desc}], allow_free_text, recommended, reason } | null,
  answers: [{ questionId, question, answer }],
  notes: { facts, limits, conflicts },
  guide: { summary, overall_gaps: [{what, why, where_to_check}], made_at },
  issues: [{
    id: "i1", n: 1, concept_id, v, created, instruction,
    question, short_answer, explanation, confidence(AI: 충분/일부/부족),
    evidence: [{ claim, url, source_title, published|null, source_type: 공식|언론|여행 정보|개인 글 }],
    gaps: [{ what, why, where_to_check }],
    check: { level, ai_level, auto_gaps: [string], counts: { evidence, official, undated, old } },
    history: [이전 버전들]
  }],
  revising: [고치는 중인 쟁점 id],
  deck: [{ page, kind: cover|overview|concept|issue|gaps, png: "cards/<내용 해시>.png", overflow: [string],
           issue_id?, concept_id?, level? }] | null,   // null이면 GET 때 그려 둔다
  photos: { state: loading|done, places: { "<후보 지역 이름>": { status: ok|none|failed, method: 2, file: "photos/<해시>.jpg",
            title(공용 파일 이름), year(촬영 연도|null), author, license, license_url, file_page, query } } },  // GET 때 photos.json에서 붙임
  approved: { version, issues: { i1: v } } | null,
  export_check: { checked_at, version, counts: {pass, fail, unverified}, areas, items: [{area, issue, name, status, detail, todo}] } | null
}
```

`check.level` 계산(`app/checks.py`): 출처 없음 → 부족. 출처 1개·공식 자료 없음·AI가 gaps를 적음 → 최대 일부. 그 밖에는 AI 판단. 게시일 미확인·1년 넘은 자료는 `auto_gaps`에 적는다.

## 엔진 결과 규약
엔진 출력은 `--json-schema`의 `structured_output`만 쓴다(`app/prompts.py`).
- 조사: `RESEARCH_SCHEMA` → `{status: ok|search_failed, conditions, concepts}`
- 해설: `EXPLAIN_SCHEMA` → `{status: need_input|result|search_failed, notes, question, guide: {summary, overall_gaps, issues}}`. 쟁점의 `concept_id`가 고른 개념과 맞지 않거나 쟁점 없는 개념이 있으면 `engine_failed`.
- 수정: `REVISE_SCHEMA` → `{issues: [{id, ...쟁점 필드}]}`. 대상 밖 id는 무시하고, 돌려주지 않은 쟁점은 바꾸지 않는다(notice로 알림).

## 내보낸 파일 (`data/travel/projects/<id>/export/approved-v<n>/`)
- `guide.html`, `guide.md`: 해설 문서. 쟁점마다 `[근거 충분|근거 일부|자료 부족]`과 부족한 자료 목록
- `sources.json`: 쟁점별 근거·부족한 자료·근거 수준
- `card-NN-<종류>.png`: 카드 PNG(1080×1350), `deck.json`: 카드 순서와 글자 넘침 기록
- `travel-cards.zip`: 카드 PNG 전체 + guide.md + sources.json
- `check_report.md`: 내보낸 파일 점검(누락·카드·자료 부족 표시·링크·날짜)
