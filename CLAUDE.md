# 여행 구상 도우미 — 새 세션용 안내

이 파일은 새 세션이 지금까지의 흐름을 빠르게 파악하도록 정리한 것이다. 자세한 근거는 아래 문서에 있다.
- `PRD.md` 요구사항 · `ACCEPTANCE.md` 확인 목록 · `DECISIONS.md` 결정 기록(1~64, 44부터 여행 구상, 52부터 카드 보기, 57부터 후보 사진) · `API_SPEC.md` API·상태·객체 · `README.md` 실행법과 확인 결과
- `KIT_README.md`, `ENGINE_GUIDE.md`, `EXAMPLE_RUN.md`, `REFERENCES.md`는 키트 원본 자료(고치지 않음)
- 2026-10-23에 카드뉴스 에이전트를 이 앱으로 교체했다. 카드뉴스 코드는 커밋 `725cf53`까지의 git 기록에 있다.

## 무엇을 하는 앱인가
여행 구상 문장 입력 → 실제 검색으로 핵심 개념 5~8개 제안(조건·정하지 않은 조건 표시) → 사용자가 1~3개 선택 → (필요하면 시기 등 질문) → 개념마다 쟁점(문제)·판단·해설·근거·근거 수준·부족한 자료 → 그림 카드로 넘겨 보기 → 쟁점별 수정·자료 더 찾기 → 최종 승인 → 카드 PNG·ZIP·HTML/Markdown/JSON 내보내기와 점검.
Python + FastAPI, 화면은 바닐라 JS, 상태는 SQLite, 127.0.0.1:8765.

## 단계(stage) 흐름
```
created → researching → selecting → explaining ⇄ waiting_for_user → review → approved
```
- 변경 요청은 화면이 본 `version`을 보내고 다르면 409 `stale_version`. 변경 요청은 `requestId`로 중복을 거른다.
- 작업은 즉시 `runId`를 돌려주고 백그라운드 스레드에서 돈다. 화면은 2초마다 `GET /api/projects/{id}`로 다시 그린다.
- 오류는 `error.kind`(auth_required / search_failed / engine_failed / interrupted / cancelled)로 구분.

## 파일 지도 (`app/`)
| 파일 | 역할 |
|---|---|
| `main.py` | API 엔드포인트, 단계·version·requestId 검사 |
| `store.py` | SQLite 저장. 프로젝트 = JSON 문서 하나. 기본 `data/travel/`, `TRAVEL_DATA`로 변경 |
| `worker.py` | 백그라운드 작업: 개념 조사, 해설, 답변 이어가기, 쟁점 수정(`revise`), 내보낸 파일 점검 |
| `engines.py` | Claude CLI 엔진(`claude -p … --json-schema`, `--resume`)과 모의 엔진(`TRAVEL_ENGINE=mock`) |
| `prompts.py` | 지시문·결과 스키마·해설 규칙(`GUIDE_RULES`) |
| `checks.py` | 쟁점별 근거 수준 재계산(출처 수·공식 자료·게시일·1년 넘은 자료)과 앱 점검 부족 자료 |
| `guide.py` | 쟁점 버전 관리, HTML/Markdown/JSON·카드 PNG·ZIP 내보내기 |
| `cards.py` | 해설을 그림 카드(PNG 1080×1350)로 그림. 쟁점 종류별 도형 아이콘, 내용 해시 파일 이름으로 바뀐 카드만 다시 그림 |
| `photos.py` | 후보 지역 사진. 장소의 공용 분류(없으면 '장소 + 풍경' 검색)에서 최근 컬러 사진을 점수로 골라 차례로 받아 `photos.json`에 저장 |
| `verify.py` | 최종 승인 후 내보낸 파일 점검(누락·자료 부족 표시·링크·날짜, 통과/실패/미확인) |
| `static/app.js` | 화면 전체(구상 입력, 개념 선택, 질문, 카드 넘겨 보기·해설, 고치기, 내려받기·점검 결과) |

## 핵심 규칙 (어기면 안 되는 것)
- 미확인을 통과로 적지 않는다. 확인 못 한 것은 '미확인'·'자료 부족'과 사람이 볼 곳을 남긴다.
- AI의 근거 수준을 그대로 믿지 않는다. `checks.assess`가 실제 출처로 상한을 정한다(DECISIONS 47).
- 카드 그림 안에 글자를 넣지 않는다. 카드 글자는 제목 54px·본문 36px 아래로 줄이지 않고, 넘치면 overflow로 표시한다.
- 출처에 없는 가격·수치를 만들지 않는다. 찾지 못한 자료는 지어내지 않고 부족한 자료로 남긴다.
- 쟁점 수정은 고른 쟁점만 새 버전(`history`에 이전 버전). 고치면 최종 승인 해제.
- 개념을 고른 뒤에는 처음 조사를 다시 돌리지 않는다(`/research`는 409).
- 사용자가 입력한 구상은 셸 문자열에 붙이지 않고 인자 배열로만 넘긴다.

## 실행과 시험
```bash
.venv/bin/python -m app.main                                   # 실제 엔진(claude CLI)
TRAVEL_ENGINE=mock TRAVEL_DATA=data/mock-test .venv/bin/python -m app.main 8790   # 모의
TRAVEL_DATA=data/real-test .venv/bin/python -m app.main 8791                       # 실제 기록을 건드리지 않고 시험
```
- 문법 확인: `.venv/bin/python -c "import app.main, app.verify"` 와 `node --check app/static/app.js`
- 앱 안 미리보기(preview_start)로 서버를 켜면 Documents 폴더 접근에서 멈출 수 있다. 그때는 터미널 탭에서 켜고 브라우저로 연다.
- 사용자가 터미널에서 켜 둔 8765 서버가 있으면 끄지 말고 다른 포트로 시험한다. 코드를 바꾸면 사용자에게 앱을 다시 켜라고 알린다.

## 현재 상태 (2026-10-26 기준)
- 과제 제출물 모음: `submission/`(README, 화면 캡처 `screens/`, 정상 입력 `run-normal/`, 자료 부족 입력 `run-poor/`, 카드뉴스 완료 `cardnews-done/`). 화면 캡처는 `submission/shoot.sh <이름> <해시>`(크롬 헤드리스, 8765 앱이 켜져 있어야 함).
- 실제 프로젝트: `p_663fc14a2e` 내 구상(최종 승인 v11), `p_f0df7ec06a` 자료 부족 입력 시험(해설 검토 단계, 승인 안 함).
- 교체 구현 완료. 모의 엔진으로 전체 흐름, 실제 엔진으로 개념 제안·해설 확인(README 확인 결과 표).
- 미확인: 로그인 필요 표시, 실제 엔진 실행 중 취소, 서버 재시작 복구(여행 버전).
- 예전 카드뉴스 기록은 `data/app.db`, `data/projects/`에 그대로 있다(새 앱은 읽지 않음). 저장소 루트 `cardnews/`는 사용자가 푼 카드뉴스 결과물(커밋 안 함).

## 작업 방식 (사용자 선호)
- 사용자는 한국어로 쓰고, 문서와 화면 문구는 쉬운 한국어 평어체로 쓴다.
- 기능을 바꾸면 `DECISIONS.md`에 번호를 이어 기록하고, API가 바뀌면 `API_SPEC.md`, 확인 결과는 `README.md`의 확인 결과 표에 남긴다(통과/실패/미확인과 근거).
- 화면 변경은 브라우저로 직접 눌러 확인하고, 실제 엔진 확인은 별도 데이터 폴더로 한다.
- 커밋은 사용자가 요청할 때만 한다. 커밋 메시지는 한국어 한 줄 요약 + 항목.
