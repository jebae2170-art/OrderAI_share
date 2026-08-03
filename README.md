# order-ai-share

> AI 기반 **초도 발주 오더 제안 시뮬레이터** 

전 시즌 마감 실적 + 자기 브랜드 PLC 표준 곡선을 활용해 차시즌 발주 수량/예산/사이즈 아소트를 5단계 워크플로우로 제안합니다.

**📖 무엇을 읽어야 하나** — 역할별 필독은 1개뿐입니다:
- **운영담당자(주간갱신)**: [`HANDOVER.md`](./HANDOVER.md) 하나로 시작~매주 루틴까지. 문제 시 [`SETUP.md`](./SETUP.md) §Troubleshooting.
- **개발/코드 인수**: 본 README → [`CLAUDE.md`](./CLAUDE.md)(변경 가드레일) → [`SETUP.md`](./SETUP.md).
- 나머지(`docs/`·`queries/README.md`)는 참조용 — 안 읽어도 운영 가능합니다.

---

## 누가 / 언제 / 왜

| 누가 | 언제 | 왜 |
|---|---|---|
| **사업부 단일 운영자** | 차시즌 발주 직전 (시즌 마감 ~ 다음 시즌 발주 확정 사이) | AI 추천을 출발점으로 자체 판단을 더하기 위해 |
| **AX팀** | 새 fork 인계 시 1회 + cold-start 브랜드 seed PLC 제공 시 | 본 fork 는 인계 후 **사업부 자율 운영** (severance) |

---

## Claude Code 운영 흐름 (권장)

본 fork 는 Claude Code 안에서 **스킬 순차 실행** 으로 운영합니다. README 를 연 직후 다음 슬래시 명령을 차례로 호출하세요:

| 순서 | 스킬 | 역할 |
|---|---|---|
| 1 | `/onboard` | 첫 셋업 — OS 점검, `.env` 모드 결정 (SSO / Service Account / skip), `setup.sh` 실행, 실패 시 패턴 매칭 진단 |
| 2 | `/prepare-pipeline` | brand+season 셋업 + PLC csv 확보 (없으면 자동 빌드) |
| 3 | `/run-pipeline` | 분석 임계값 4종 (목표판매율 / 대물량 목표판매율 / 대물량 기준 / 등급 기준) 검토 + `run_all.py` 6 step (5 분석 + baseline DuckDB 적재) + 결과 진단 |
| 4 | `/server-start` | 백엔드 + 프론트엔드 기동 + 브라우저 자동 띄우기 + 5 step UI 체크리스트 + 종료 안내 |
| (반복) | `/weekly-refresh` | 인시즌 주간 갱신 — Snowflake 재조회 → run_all 재실행 → KG 교차검증 게이트 → 통과 시에만 baseline 반영(실패 시 직전 baseline 롤백) → **운영배포(S3→EC2 재시작→서빙검증, 자격 구비 시)** |

각 스킬은 실패 시 진단 메시지와 함께 다음 행동을 안내합니다. 수동 트러블슈팅은 [`SETUP.md`](./SETUP.md) / [`CLAUDE.md`](./CLAUDE.md) 가 폴백.

### 주간 운영 루틴 (인시즌)

**매주 월요일 09시 이후** (상류 GT 주간 적재가 월 ~08시 완료 — 그 전 실행 시 예측 기준이 지난주로 밀림):

- **단일 브랜드 운영**: `/weekly-refresh` 1회.
- **두 브랜드 운영 (예: MLB + Discovery)**: 브랜드마다 반복 —
  1. `/weekly-refresh` (현재 brand_config 브랜드) — **운영배포(Stage 6)는 보류**
  2. `/prepare-pipeline` 으로 다른 브랜드 전환 (같은 baseSeason)
  3. `/weekly-refresh` 재실행 — **여기서 운영배포 1회** (baseline 통째 업로드라 두 브랜드 갱신 완료 후 1회가 원칙)
  > baseline DuckDB 는 멀티브랜드 적재라 앞 브랜드 데이터는 유지됩니다. `.env` 의 `BRAND` 도 brand_config 와 일치시켜야 합니다 (불일치 시 RuntimeError 가드).

스킬 없이 직접 명령으로 운영하려면 → 아래 **Quick Start** / **처음 셋업** 섹션.

---

## Quick Start (수동 — 스킬 미사용 시)

이미 셋업 끝났다는 가정:

```bash
# 1. 파이프라인 실행 (6 step: 5 분석 + baseline DuckDB 적재)
.venv/bin/python scripts/run_all.py

# 2. 백엔드 기동
.venv/bin/uvicorn server.api:app --port 8000

# 3. 프론트엔드 (별도 터미널)
cd apps/lite && npm run dev
```

브라우저: http://localhost:5173 (또는 vite 출력 포트)

PLC csv 4개 (`data/plc/{brand}_{type}_plc_forecast_standard.csv`) 는 동봉됨. 본인 brand+type 가 매칭되면 별도 빌드 불필요. 신규 brand/시즌은 [`docs/PLC_GUIDE.md`](./docs/PLC_GUIDE.md) 참고.

처음이라면 → [`SETUP.md`](./SETUP.md) 부터.

---

## 문제 생기면

1. **에러 메시지 + README.md / SETUP.md / 본 디렉토리 구조** 를 Claude Code 에 붙여넣고 도움 요청
2. PLC 관련 의문 → [`docs/PLC_GUIDE.md`](./docs/PLC_GUIDE.md)
3. Claude Code 의 가드레일 / 변경 가능 영역 → [`CLAUDE.md`](./CLAUDE.md)

---

## 아키텍처

```
   Snowflake (raw)  또는  data/{brand}/{season}/*.csv (CSV 캐시)
        │
        ▼  (scripts/run_all.py — 6 step)
            [1/6] main.py            — 시즌 마감 분석
            [2/6] weekly_analysis.py — 주차별 시계열
            [3/6] ai_sales_loss_v3   — PLC 기반 기회비용
            [4/6] step4_integration  — 유사 스타일 매핑
            [5/6] generate_size_data — 사이즈 데이터
            [6/6] dump_to_duckdb     — baseline DuckDB 적재
        │
        ▼
   public/*.json (baseline JSON)  +  data/production/order_ai.duckdb (baseline DuckDB)
        │
        ▼  (server/api.py — FastAPI)
   REST API  (/api/lite/* + /api/s3/file/*)
        │
        ▼  (apps/lite — React + Vite)
   브라우저 UI (Step 1 ~ 5)

   사용자 Step 3+ UI 동작 시 → data/user-storage/ (본인 사물함, confirmed_*, go_list 등)
```

5 단계 워크플로우 (브라우저 UI):

| Step | 화면 | 목적 |
|---|---|---|
| 1 | Sales Performance | 전 시즌 마감 진단 (S/A/B/C/D 등급, BCG) |
| 2 | Case Study | 주차별 실적 + 기회비용 진단 |
| 3 | Style Match | GO list 업로드 + ML 유사 스타일 매핑 확정 |
| 4 | Order Suggest | 잠재 수요 기반 발주 추천 검토 |
| 5 | Size Assortment | 사이즈 아소트 최적화 + Excel export |

각 step 산출물은 본인 사물함 (`data/user-storage/`) 에 저장돼 다음 step 의 입력이 됨.

---

## 디렉토리 구조 (요약)

```
order-ai-share/
├── README.md, SETUP.md, CLAUDE.md
├── setup.sh, smoke_test.py, pytest.ini    ← 자가검증 하네스
├── Dockerfile/deploy 없음                  ← share 는 로컬 단일 운영
│
├── apps/lite/                              ← React + Vite 프론트
│   ├── src/{App, components, contexts, ...}
│   └── package.json, vite.config.js, ...
├── server/                                 ← FastAPI 백엔드
│   ├── api.py                              ← 진입점 (uvicorn)
│   ├── routers/{lite, shared}.py
│   ├── services/{user_storage, order_calc, ...}
│   └── db.py, permissions.py (stub)
├── scripts/                                ← 파이프라인 + PLC 도구
│   ├── run_all.py, main.py, ...
│   ├── dump_to_duckdb.py                   ← baseline DuckDB 적재 (run_all 의 6 step)
│   └── plc_engine/{build_plc_standard, ...}
├── queries/                                ← Snowflake SQL
├── public/                                 ← 정적 설정 (git tracked)
│   ├── brand_config.json, plc_engine_config.json
│   ├── color_mapping.json, item_nm_map.json (전 브랜드 공통)
├── data/                                   ← 런타임 데이터
│   ├── plc/{brand}_{type}_plc_forecast_standard.csv  ← 4개 동봉 (git tracked)
│   ├── production/order_ai.duckdb          ← baseline (run_all 산출, .gitignore)
│   └── user-storage/                       ← 본인 사물함 (.gitignore)
```

자세한 디렉토리별 dos/donts → [`CLAUDE.md`](./CLAUDE.md).

---

## 처음 셋업 (수동 — 스킬 미사용 시)

처음이면 [`SETUP.md`](./SETUP.md) 를 따라가세요. 요약하면:

```bash
# 1. Pre-meeting (혼자 가능, .env 없이)
./setup.sh --skip-snowflake

# 2. 온보딩 미팅 후 IT 가 전달한 .env 적용
cp .env.example .env
# .env 편집 — IT 제공 1Password share 의 값 채움

# 3. 풀 검증
./setup.sh
```

`./setup.sh` PASS 보이면 위 Quick Start 로.


---

## 라이선스 / 보안

- 본 코드는 F&F 내부 자산. 외부 공유 금지.
- `.env`, `data/`, `output/` 은 절대 git 커밋 X (`.gitignore` 확인).
