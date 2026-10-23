# API 명세

서버: `http://127.0.0.1:8765` (127.0.0.1에만 바인딩). 모든 본문은 JSON.

## 상태 모델

프로젝트 `stage` 흐름:

```
created → researching → selecting → deep_research ⇄ waiting_for_user
        → storyboard_review → producing → final_review → approved
```

- `waiting_for_user`는 실패가 아니다. `question`에 현재 질문이 들어 있다.
- 실패/중단: `error` 필드가 채워지고 `stage`는 실패한 단계 그대로 남는다. `error.kind`로 원인 구분.
- `version`: 상태가 바뀔 때마다 1 증가. 변경 요청은 화면이 본 `version`을 보내고, 다르면 `409 stale_version`.

### error.kind
| kind | 의미 | 다음 행동 |
|---|---|---|
| `auth_required` | 엔진/agy 로그인 필요 | 터미널에서 로그인 후 재시도 |
| `search_failed` | 검색 도구 없음/호출 실패 | 재시도 |
| `engine_failed` | 엔진 출력이 규약에 맞지 않음 등 | 재시도 |
| `image_failed` | 이미지 파일 생성·검증 실패 | 해당 카드 재시도 |
| `interrupted` | 서버 재시작으로 작업 중단 | 재시도 |
| `cancelled` | 사용자가 취소 | 재시도 |
| `quota_exceeded` | agy 이미지 쿼터 초과(카드 단위) | Claude CLI로 자동 대체. 쿼터가 채워진 뒤 카드 재시도 |

## 질문 객체
```json
{ "id": "q_ab12", "version": 1, "question": "어떤 독자에게 전달할까요?",
  "options": [{"value": "일반 사용자", "desc": "..."}], "allow_free_text": true,
  "recommended": "일반 사용자", "reason": "추천 이유" }
```
추천값은 표시만 하며 사용자 응답으로 간주하지 않는다.

## 엔드포인트

| 요청 | 입력 | 성공 응답 | 오류 |
|---|---|---|---|
| `POST /api/projects` | `{topic, period_days?=7}` | `{projectId}` | 400 빈 주제 |
| `GET /api/projects/{id}` | — | 프로젝트 전체(후보·선택·stage·question·cards·error·version·runs) | 404 |
| `POST /api/projects/{id}/research` | `{requestId, period_days?}` | `{runId, status}` | 409 이미 진행 중 |
| `POST /api/projects/{id}/selection` | `{candidateIds[1..3], version}` | 저장된 선택 + 심층 조사 `runId` | 400 개수, 409 stale |
| `POST /api/projects/{id}/answers` | `{questionId, version, answer, requestId}` | `{accepted, stage}` | 409 오래된 질문 |
| `POST /api/projects/{id}/approve` | `{stage: storyboard\|final, version, requestId}` | storyboard: 카드 생성 `{runId}` / final: `{approved, files}` | 409 `wrong_stage`, `checks_failed`(점검 실패 카드 목록) |
| `POST /api/projects/{id}/revisions` | `{cardIds, instruction?, version, requestId}` | `{runId}`. 지정 카드만 새 버전, 최종 승인 해제. instruction이 비면 그림만 재생성(실패 카드 재시도) | 409 |
| `POST /api/projects/{id}/cards/{cardId}/edit` | `{title, line, body?, layout?, version, requestId}` | `{runId}`. AI 없이 글자·배치만 바꿔 그 카드만 새 버전(그림 재사용), 최종 승인 해제 | 400 빈 제목·잘못된 배치 값, 409 |
| `POST /api/runs/{id}/cancel` | — | `{status: cancelled}` | 404 |
| `GET /api/projects/{id}/exports` | — | 최종 승인 버전의 다운로드 목록 `{approvedVersion, files:[{name,url}]}` | 409 미승인 |
| `GET /api/projects/{id}/files/{path}` | — | 프로젝트 폴더 안 파일(카드 PNG, 내보내기). 폴더 밖 경로는 404 | 404 |

## requestId 중복 처리
프로젝트 범위에서 `requestId`를 저장한다.
- 같은 ID + 같은 입력 → 저장된 응답 그대로 반환(작업 재실행 없음).
- 같은 ID + 다른 입력 → `409 request_conflict`.

## 오류 응답 형식
```json
{ "detail": { "code": "stale_version", "message": "화면이 오래되었습니다. 새로고침하세요." } }
```

## 연결 방식

```
브라우저(app/static)  ──HTTP JSON──▶  웹 서버(app/main.py, FastAPI)  ──스레드──▶  작업(app/worker.py)  ──▶  엔진(app/engines.py)
        ▲  2초마다 GET /api/projects/{id}           │ 상태 저장                                              │
        └──────────────────────────────────────── SQLite(data/app.db) ◀──────── 결과 저장 ──────────────────┘
```

1. **브라우저 → 서버**: 변경 요청(POST)은 즉시 `runId`를 받고 끝난다. 긴 HTTP 요청을 열어 두지 않는다.
2. **진행 상황**: 작업 중이면 브라우저가 2초마다 `GET /api/projects/{id}`를 다시 불러 화면을 그린다(폴링). 프로젝트 ID는 주소의 `#p_...` 부분에 있어 새로고침해도 같은 프로젝트를 다시 불러온다.
3. **서버 → 작업**: `worker.launch()`가 백그라운드 스레드에서 엔진을 호출한다. 취소는 `threading.Event`로 알리고 엔진의 `cancel()`로 하위 프로세스를 정리한다.
4. **엔진 결과 정규화**: 엔진은 `{type: progress | need_input | result | error}`를 돌려준다(이 앱의 규약).
   - `need_input` → 질문 저장, `stage = waiting_for_user`
   - `result` → 후보·편집 판단 저장, 다음 단계로
   - `error` → `error` 저장, 화면에 재시도 버튼
5. **질문 답변**: `POST /answers`가 질문 ID·version을 확인한 뒤 `engine.answer(session_id, …)`로 같은 엔진 세션에 이어 보낸다.
6. **서버 재시작**: 시작할 때 `running`으로 남은 작업을 `interrupted`로 바꾸고 화면에 재시도를 안내한다.

### 화면 흐름과 호출 순서
| 화면 | 사용자 동작 | 호출 |
|---|---|---|
| 주제 입력 | 주제 입력 → "조사 시작" | `POST /api/projects` → `POST /research` → 주소에 `#projectId` |
| 조사 중 | 기다림 / 취소 | 폴링, `POST /api/runs/{id}/cancel` |
| 후보 선택 | 1~3개 체크 → "다음 단계" / "30일로 넓혀 다시 조사" | `POST /selection` / `POST /research {period_days:30}` |
| 질문 | 선택지 또는 직접 입력 → "답변 제출" | `POST /answers` (같은 질문은 같은 requestId 재사용) |
| 스토리보드 | 결과 확인 | 폴링 |

### 현재 엔진
- `claude_cli`(기본): `claude -p <지시문> --output-format json --model sonnet --effort medium --tools WebSearch WebFetch --allowedTools WebSearch WebFetch --json-schema <스키마>`, 이어가기는 `--resume <session_id>`. 인자 배열로 실행하며 주제를 셸 문자열에 붙이지 않는다. 프로젝트별 `data/projects/<id>/engine/`에서 실행.
  - 결과는 응답 JSON의 `structured_output`만 사용(스키마: `app/prompts.py`). `status`가 `search_failed`면 `error.kind=search_failed`, 로그인 관련 문구면 `auth_required`, 형식이 맞지 않으면 `engine_failed`.
  - 취소 시 프로세스 그룹 전체에 SIGTERM.
- `mock`: `CARDNEWS_ENGINE=mock`으로 켤 때만. 실제 검색을 하지 않고, 화면 상단에 **"⚠ 모의 데이터입니다. 실제 검색 결과가 아닙니다."**를 표시하며 후보 제목에 `[모의]`가 붙는다.

## 카드 객체
```json
{ "id": "k1", "index": 1, "role": "표지", "title": "...", "line": "핵심 문장", "body": "본문 1~2문장", "sources": ["https://..."],
  "image_role": "이미지가 맡는 역할", "image_plan": "그릴 내용",
  "status": "pending|running|done|failed", "error": {"kind": "image_failed|quota_exceeded|auth_required|interrupted|cancelled", "message": "..."},
  "current": 2,
  "versions": [{ "v": 1, "title": "...", "line": "...", "sources": [], "bg": "cards/k1/v1/source.jpg",
                 "png": "cards/k1/v1/card.png", "created": "...",
                 "image": {"kind": "generated|mock", "tool": "Antigravity CLI (agy) 1.4.6 | Claude CLI(도형 설계) + Pillow(그리기)", "conversation_id": "...", "prompt": "...", "created": "...", "fallback_reason": "agy 실패(quota_exceeded): ... (대체했을 때만)", "note": "AI 생성 이미지..."},
                 "checks": {"file": {"ok": true, "detail": "PNG 1080×1350"}, "overflow": {}, "contrast": {}, "phone": {}, "sources": {}} }] }
```
최종 승인 시 `approved = {version, cards: {cardId: 버전번호}}`를 저장하고 `export/approved-v<version>/`에 `card_01.png…`, `sources.md`, `image_credits.json`, `cardnews.zip`을 만든다.

## 이미지 작업자 (agy)
`agy -p <자연어 요청> --output-format json --mode accept-edits --print-timeout 300s`를 카드 버전 폴더에서 인자 배열로 실행한다.
응답 JSON은 대화 결과일 뿐 이미지가 아니다. `conversation_id`로 `~/.gemini/antigravity-cli/brain/<id>/`의 이미지 파일을 찾아 Pillow로 검사한 뒤 사용한다. 파일이 없으면 `SUCCESS`여도 실패로 처리한다.

프로젝트에는 심층 조사 결과로 `visual_style = {palette, style, mood}`가 저장되고, 모든 카드의 이미지 요청에 같은 값이 들어간다.

### agy 실패 이유 확인과 대체
1. 첫 호출 뒤 60초 안에 이미지 파일이 없으면 `agy --conversation <id> -p "하위 작업이 끝날 때까지 기다려…"`로 한 번 이어 묻는다.
2. 응답에 `429`·`RESOURCE_EXHAUSTED`·`quota`가 있으면 `quota_exceeded`, 그 밖에 파일이 없으면 `image_failed`.
3. agy가 실패하면 `claude -p … --tools "" --json-schema <도형 스키마>`로 그림을 도형 목록으로 받고 Pillow가 1080×1350 PNG로 그린다(`cards/<id>/v<n>/fallback/`). 이것도 실패하면 카드는 `failed`이고 두 실패 이유를 함께 보여 준다.

### 카드 버전의 배치와 문제 위치
버전마다 `layout`(position·size·shade)과 `problems = [{kind: clip|overlap|contrast, label, box:[x0,y0,x1,y1]}]`를 저장한다. box는 1080×1350 카드 기준 픽셀이며, 화면의 휴대폰 미리보기가 이 위치에 상자를 그린다.

프로젝트에는 스토리보드 승인 때 `card_bg`(예: `#F5EFE0`)가 저장된다. 모든 카드의 글자 영역과 그림 요청 배경에 같은 색을 쓰고, 카드 점검에 `background`(그림 배경이 `card_bg`와 같은지)가 추가된다.
