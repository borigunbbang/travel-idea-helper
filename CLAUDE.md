# 카드뉴스 에이전트 — 새 세션용 안내

이 파일은 새 세션이 지금까지의 흐름을 빠르게 파악하도록 정리한 것이다. 자세한 근거는 아래 문서에 있다.
- `PRD.md` 요구사항 · `ACCEPTANCE.md` 확인 목록 · `DECISIONS.md` 결정 기록(1~43) · `API_SPEC.md` API·상태·객체 · `README.md` 실행법과 확인 결과
- `KIT_README.md`, `ENGINE_GUIDE.md`, `EXAMPLE_RUN.md`, `REFERENCES.md`는 키트 원본 자료(고치지 않음)

## 무엇을 하는 앱인가
주제를 넣으면 최근 자료를 조사 → 사용자가 후보 1~3개 선택 → 심층 조사와 독자 질문 → 스토리보드 승인 → 카드 그림·글자 제작 → 카드별 수정 → 최종 승인 → 내려받기와 내보낸 파일 점검.
Python + FastAPI, 화면은 바닐라 JS, 상태는 SQLite, 127.0.0.1:8765.

## 단계(stage) 흐름
```
created → researching → selecting → deep_research ⇄ waiting_for_user
        → storyboard_review → producing → final_review → approved
```
- 변경 요청은 화면이 본 `version`을 보내고 다르면 409 `stale_version`. 변경 요청은 `requestId`로 중복을 거른다.
- 작업은 즉시 `runId`를 돌려주고 백그라운드 스레드에서 돈다. 화면은 2초마다 `GET /api/projects/{id}`로 다시 그린다.
- 오류는 `error.kind`(auth_required / search_failed / engine_failed / image_failed / quota_exceeded / interrupted / cancelled)로 구분.

## 파일 지도 (`app/`)
| 파일 | 역할 |
|---|---|
| `main.py` | API 엔드포인트, 단계·version·requestId 검사 |
| `store.py` | SQLite 저장. 프로젝트 = JSON 문서 하나. `CARDNEWS_DATA`로 데이터 폴더 변경 |
| `worker.py` | 백그라운드 작업: 조사, 심층 조사, 답변 이어가기, 제작, 수정(`revise`), 내보낸 파일 점검 |
| `engines.py` | Claude CLI 엔진(`claude -p … --json-schema`, `--resume`)과 모의 엔진 |
| `prompts.py` | 엔진 지시문·결과 스키마·편집 규칙(`EDITORIAL_RULES`), 수정 모드 지시(`REVISE_MODE`) |
| `images.py` | agy 그림 생성, 실패 시 Claude CLI 도형 설계 + Pillow 대체, 모의 그림 |
| `production.py` | 카드 제작(3장 병렬)·버전 관리·출처 점검·`finish`·`export` |
| `render.py` | Pillow 글자 배치, 줄 단위 잘림·대비·휴대폰 가독성·그림 보임 점검, 수치(metrics) |
| `verify.py` | 최종 승인 후 ZIP을 다시 열어 누락·글자 잘림·출처·날짜 점검(통과/실패/미확인) |
| `static/app.js` | 화면 전체(후보 선택, 질문, 스토리보드, 카드, 휴대폰 미리보기, 카드 고치기, 내려받기·점검 결과) |

## 핵심 규칙 (어기면 안 되는 것)
- 미확인을 통과로 적지 않는다. 확인 못 한 것은 '미확인'과 사람이 볼 곳을 남긴다.
- agy의 `SUCCESS` 응답을 믿지 않는다. 실제 파일을 찾아 Pillow로 열어 본다(DECISIONS 19, 31).
- 그림 안에 글자를 넣지 않는다. 글자는 Pillow가 따로 그린다.
- 카드 수정은 고른 카드만 새 버전(`cards/<id>/v<n>/`). 고치면 최종 승인 해제.
  - `mode=text`: AI가 제목·핵심 문장·본문·출처만, 그림 재사용
  - `mode=image`: 글자는 그대로, 그 카드 그림만 새로
  - 글자·배치만 직접 고치기는 `POST /cards/{id}/edit`(AI 없이 2~3초)
- 기본 배치는 `split`(위 글자, 아래 그림), 배경색은 프로젝트 공통 `card_bg`.
- 글자 크기는 휴대폰 기준(제목 18px·본문 12px) 아래로 줄이지 않는다(DECISIONS 43).
- 후보를 고른 뒤에는 처음 조사를 다시 돌리지 않는다(`/research`는 409).
- 사용자가 입력한 주제는 셸 문자열에 붙이지 않고 인자 배열로만 넘긴다.

## 실행과 시험
```bash
.venv/bin/python -m app.main                 # 실제 엔진(claude CLI, agy)
CARDNEWS_ENGINE=mock CARDNEWS_IMAGE=mock .venv/bin/python -m app.main 8790   # 모의
CARDNEWS_DATA=<임시 폴더> .venv/bin/python -m app.main 8791                   # 실제 프로젝트를 건드리지 않고 시험
```
- 문법 확인: `.venv/bin/python -c "import app.main, app.verify"` 와 `node --check app/static/app.js`
- 실제 프로젝트를 복사해 시험할 때: `sqlite3 data/app.db ".backup <폴더>/app.db"` 후 `data/projects/<id>`를 복사.
  Claude CLI `--resume`은 작업 폴더 기준으로 세션을 찾으므로 `~/.claude/projects/<원래 engine 경로를 -로 바꾼 이름>/<session_id>.jsonl`을 새 경로 이름 폴더에도 복사해야 이어진다.
- 사용자가 터미널에서 켜 둔 8765 서버가 있으면 끄지 말고 다른 포트로 시험한다. 코드를 바꾸면 사용자에게 앱을 다시 켜라고 알린다.

## 현재 상태 (2026-10-23 기준)
- 구현 순서 1~5단계 완료. 6단계(ACCEPTANCE 전체 확인)는 일부만 확인. 미확인: Claude 로그인 필요 표시, 실제 엔진 실행 중 취소, 이미지 실패·로그인 필요 재시도(실제).
- agy 이미지 쿼터 초과(약 166시간 뒤 재설정) → 지금 그림은 모두 Claude CLI 도형 대체 그림.
- 주요 프로젝트(`data/`):
  - `p_e494002368` "ai 최신 뉴스"(12:48) — 사용자가 최종 승인함
  - `p_a677bb05a1` "유럽" — 최종 검토 중. 3·4번 카드가 '작게'로 되어 있어 휴대폰 가독성 실패였음 → 렌더러 수정 후 '보통'으로 다시 배치하면 통과
- 사용자가 직접 확인해야 할 것으로 남은 것: "ai 최신 뉴스" 5번 본문 "세부 구조는 몇 달 안에 공개하겠다고 했다"가 카드 출처(Al Jazeera, ABC)에 없음(Anthropic 블로그 미확인).
- 저장소 루트의 `cardnews/` 폴더는 사용자가 내려받아 푼 결과물(커밋 안 함).

## 작업 방식 (사용자 선호)
- 사용자는 한국어로 쓰고, 문서와 화면 문구는 쉬운 한국어 평어체로 쓴다.
- 기능을 바꾸면 `DECISIONS.md`에 번호를 이어 기록하고, API가 바뀌면 `API_SPEC.md`, 확인 결과는 `README.md`의 확인 결과 표에 남긴다(통과/실패/미확인과 근거).
- 화면 변경은 브라우저로 직접 눌러 확인하고, 실제 엔진 확인은 복사본 데이터로 한다.
- 커밋은 사용자가 요청할 때만 한다. 커밋 메시지는 한국어 한 줄 요약 + 항목.
