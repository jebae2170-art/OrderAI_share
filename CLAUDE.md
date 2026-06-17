# CLAUDE.md — order-ai-share

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 0. 작업 시작 전 반드시 확인

1. [`README.md`](./README.md) — 전체 그림
2. [`SETUP.md`](./SETUP.md) — 셋업 / 트러블슈팅 FAQ
3. 코드 변경 시 §1 코딩 준수 사항을 반드시 따름
4. 본 CLAUDE.md §2 "디렉토리별 변경 가이드 (3단계)" 표 — 작업 위치가 🟢/🟡/🔴 어느 영역인지 먼저 파악

---

## 1. Karpathy-Inspired 코딩 가이드라인

### Think Before Coding
- 가정은 명시. 불확실하면 질문.
- 여러 해석 가능 시 옵션 제시. 임의 선택 X.
- 더 단순한 방법이 있다면 그렇게 말하기.

### Simplicity First
- 요청 문제를 푸는 **최소 코드**. 추측성 기능 X.
- 일회용 코드의 추상화 X. "유연성" 도 요청 시에만.
- 200줄 짜놓고 50줄로 가능했으면 다시 쓰기.

### Surgical Changes
- 변경한 코드의 **직접 이웃**만 손대기. 인접 코드 / 주석 / 포맷팅 "개선" 금지.
- 기존 스타일 따르기. 본인이 다르게 할 거라도.
- 변경 후 생긴 dead import / 변수만 정리. 사전 dead code 는 의뢰 없이 건드리지 X.

**테스트**: 변경된 모든 줄이 의뢰의 직접 결과인가? 그렇지 않으면 surgical 위반.

### Goal-Driven Execution
"검증" 단계가 명시되지 않은 작업은 위험:
- "검증 로직 추가" → "잘못된 입력 테스트 작성 → 통과시키기"
- "버그 수정" → "버그 재현 테스트 작성 → 통과시키기"

§4 "검증 명령" 을 success criterion 으로 사용.

### 변경 전 영향도 분석 필수
: 코드 수정 전 upstream(입력 제공처) + downstream(출력 소비처) 의 전체 연결고리를 파악하고, 영향받는 파일 목록을 의뢰자에게 먼저 공유.

### UI/대시보드 변경 시 사용자 승인 필수
: 프론트엔드 컴포넌트(JSX) 의 화면 구성 / 레이아웃 / 차트 종류 / 색상 등 시각적 변경은 **반드시 사용자에게 변경 내용을 설명하고 승인 받은 후** 진행. 임의로 UI 수정 X.

### 기존 로직 고도화/수정 시 현행 분석 선행 필수
: 기존 코드 고도화 / 수정 전 **현재 로직의 흐름 / 조건 / 수식 / 입출력을 코드 위치와 함께 상세 정리**하여 사용자와 공유 → 변경 방향 합의 후 작업 시작.

### 구조/방식 변경 시 비교분석 필수
: 새로운 방식 도입이나 기존 방식 변경 요청 시 무조건 수용하지 말고, **현재 방식 vs 새 방식의 장단점을 비교 테이블로** 제시한 뒤 합의된 방향으로 진행.

---

## 2. 디렉토리별 변경 가이드 (3단계)

### 🟢 자유롭게 변경 — Config / Param

| 위치 | 변경 가능 항목 | 반영 방법 |
|---|---|---|
| `.env` | Snowflake / BRAND / DUCKDB_PATH. `SNOWFLAKE_AUTH=externalbrowser` 로 SSO 토글 | 서버 재기동 |
| `public/brand_config.json` | targetSellThrough, gradeThresholds, sizeOrder, subSeasonCutoff | 다음 파이프라인 실행 |
| `public/plc_engine_config.json` | 신규 시즌 등록 (`seasons`) / brand 별 `plc_exclude_prods` | 다음 build / run |
| `public/item_nm_map.json` | 신규 ITEM 추가 (전 브랜드 공통) | PLC 빌드 시 자동 적용 |
| `public/color_mapping.json` | 새 컬러코드 추가 | 다음 파이프라인 실행 |
| `requirements.txt` / `apps/lite/package.json` | 새 패키지 추가 | `pip install` / `npm install` |

### 🟡 신중하게 — 새 파일 / 새 함수 추가 권장

기존 코드 수정 대신 **새 파일 / 새 함수 추가** 가 더 안전:

| 위치 | 권장 패턴 |
|---|---|
| `server/routers/lite.py` | 새 endpoint 끝부분에 `@router.get/post` 추가. 기존 endpoint **경로 / 응답 키** 변경 X |
| `server/services/` | 새 모듈 추가 (e.g., `my_team_rules.py`). 기존 함수 시그니처 변경 X |
| `server/db.py` | 새 query 함수 끝부분에 추가. DuckDB **write** 쿼리는 추가 X (read-only baseline) |
| `apps/lite/src/components/` | 새 컴포넌트 평면 배치 (공통은 `common/`). 기존 Step 1~5 는 surgical 수정만 |
| `scripts/` | 새 분석 step → 새 `.py` + `run_all.py` 끝에 호출 추가 |
| `scripts/plc_engine/` | 새 strategy 파일 추가. `engine.py` / `predictor.py` 직접 수정 X |
| `queries/` | 새 SQL 파일 추가 |
| `.claude/skills/` | 새 스킬 → `{name}/SKILL.md` (frontmatter 형식 따르기) |

### 🔴 절대 금지 — 변경 시 모든 결과의 신뢰성 무너짐

| 위치 | 이유 |
|---|---|
| `server/db.py` 의 DuckDB 스키마 가정 (테이블 / 컬럼명) | `query_meta` / `query_dashboard` 등 모든 query 함수 fail. `dump_to_duckdb.py` 적재도 실패 |
| `server/routers/lite.py` 의 **endpoint 경로** | 프론트 호출과 contract — URL 변경 시 양쪽 동시 수정 필수 |
| `server/api.py` 의 `app` 에 새 import / 미들웨어 함부로 추가 | CORS / 라우팅 설정 깨짐 가능 |
| `server/permissions.py` 함수 시그니처 | `routers/lite.py` 의 `Depends()` 호출 깨짐 |
| baseline DuckDB 에 영구 SQL write | baseline 은 read-only. 영구 저장은 `data/user-storage/` JSON 으로 |
| `scripts/plc_engine/` core (`engine.py`, `predictor.py`, `specs.py`) | PLC 학습 로직 자체 — 발주 예측 신뢰성 와해 |
| `apps/lite/src/service/apiClient.js` 의 `FILE_TO_ENDPOINT` 매핑 | baseline / 사물함 라우팅 anchor |
| `apps/lite/src/services/` (복수형) 폴더 재생성 | 단수 `service/` 로 통합됨 — 재생성 시 중복 |
| 본인 사물함 JSON 파일명 (`confirmed_*`, `go_list.json`) | 백 / 프론트 양쪽 contract |
| `brand_config.json::brand` ≠ `.env::BRAND` | `RuntimeError` (consistency 가드) |
| `data/` 안의 어떤 파일도 git commit | `.gitignore` 처리됨 |

> **※ 동기화 예외 (오너 전용)**: 위 🔴 표의 `scripts/plc_engine/ core (engine.py / predictor.py / specs.py)` 변경 금지는 **운영자(사업부)의 수기 변경** 대상입니다. 오너(Judy)가 원천 저장소(`order_ai`)의 검증된 로직을 **정본 미러링으로 동기화**하는 작업은 예외이며, 이 경우 core 파일은 원천을 기준으로 갱신됩니다(3-way 머지, base=fork 시점). 운영자는 이 파일들을 직접 수정하지 말고, 로직 변경이 필요하면 오너에게 원천 반영을 요청하세요.

---

## 3. 자주 헷갈리는 패턴 — 디테일

**API 호출** (`apps/lite/src/`)
- 항상 `createApiClient(user.email, brand, season, user.role)` + `useMemo` 로 감싸기
- baseline data fetch → `api.fetchFile('xxx.json')` (filename 화이트리스트 확인)
- POST 액션 → `api.post('/api/lite/xxx', body)` 또는 `/api/lite/...`
- ❌ `fetch('/api/lite/...')` 직접 호출 — `X-User-Email` / brand·season 자동 부착 잃음

**파일 신규 추가** 패턴
- 새 endpoint → `server/routers/lite.py` 끝부분 + Pydantic 모델 + `Depends(require_brand_access)`
- 새 비즈니스 로직 → `server/services/{name}.py`
- 새 분석 step → `scripts/{name}.py` + `run_all.py` 호출 추가
- 새 PLC strategy → `scripts/plc_engine/{name}.py` (`engine.py::build_sell_through_plc` 호출 패턴 follow)
- 새 스킬 → `.claude/skills/{name}/SKILL.md` (frontmatter 형식)

**임시 리셋** 패턴
- 본인 사물함 reset → UI 우상단 Reset 버튼 또는 `rm -rf data/user-storage/{brand}/{season}/`
- baseline DuckDB reset → `rm data/production/order_ai.duckdb` 후 `run_all.py` 재실행
- baseline DuckDB 직접 SQL write 는 **임시 분석/탐색에만** — 다음 `run_all.py` 실행 시 사라짐

---

## 4. 변경 후 반드시 돌릴 검증

작업 종료 / PR 시 다음 통과 필수:

```bash
# (1) 백엔드 정적 import + 부팅 검증
PYTHONPATH=. .venv/bin/python -c "import server.api; print('routes:', len(server.api.app.routes))"

# (2) 프론트 빌드
cd apps/lite && npm run build

# (3) 백엔드 헬스체크
.venv/bin/uvicorn server.api:app --port 8000 &
sleep 3
curl -fs http://localhost:8000/api/health | grep -q '"ok"' && echo "✅ health"
curl -fs http://localhost:8000/openapi.json | python3 -c "import sys, json; print(len(json.load(sys.stdin)['paths']), 'endpoints')"

# (4) Smoke test (Snowflake 연결 변경 시)
.venv/bin/python -m pytest smoke_test.py -v
```

3개 모두 통과 = 최소 안전성 확보.

---

## 5. 자가 점검 체크리스트

작업 종료 전 한 번 더:

- [ ] §4 검증 명령 3개 모두 통과
- [ ] 변경 파일 목록이 의뢰의 직접 결과인가 (surgical)
- [ ] 새 환경변수 추가 시 `.env.example` 반영
- [ ] 새 npm/pip 의존성 추가 시 `package.json` / `requirements.txt` 반영
- [ ] git commit 시 `.env` / `data/` / `output/` 안 들어갔는가 (`git status` 확인)

---
