---
name: run-pipeline
description: order-ai-share 분석 파이프라인 실행. brand_config.json 의 분석 임계값 4개 (목표판매율 / 대물량 목표판매율 / 대물량 기준 / 스타일 등급 기준) 검토 → run_all.py 6 step 실행 (5 분석 + baseline DuckDB 적재) → 결과 진단. /prepare-pipeline 후속 단계.
---

# run-pipeline

분석 파이프라인을 자율 실행하는 스킬. `/prepare-pipeline` 이 identity (brand+season+PLC csv) 까지 준비했다는 전제. 본 스킬은 **분석 임계값을 검토/수정** 하고 `run_all.py` 를 실행한 뒤 결과를 진단한다.

## 적용 시점 (trigger)

- "분석 돌릴게", "run_all 실행", "파이프라인 실행"
- `/prepare-pipeline` 완료 후 다음 단계
- 임계값 (목표판매율, 등급 기준 등) 조정 후 재실행

## 절차 (순서대로 엄격히 진행)

### Stage 0 — 의존성 점검

**Bash** 로 다음을 묶어 확인:

```bash
test -f public/brand_config.json && echo "config:OK" || echo "config:MISSING"
PYTHONPATH=. .venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); from config_loader import get_brand, get_base_season, get_target_season, get_plc_forecast_path; print(f'brand={get_brand()}'); print(f'baseSeason={get_base_season()}'); print(f'targetSeason={get_target_season()}'); import os; p=get_plc_forecast_path(); print(f'plc:{os.path.basename(p)}:{ \"OK\" if os.path.exists(p) else \"MISSING\"}'); b,s=get_brand().lower(),get_base_season().lower(); print(f'restored:{ \"OK\" if os.path.exists(f\"data/{b}/{s}/restored.csv\") else \"MISSING\"}')" 2>&1
test -f .env && echo "env:OK" || echo "env:MISSING"
mkdir -p output state data/user-storage data/production   # 산출물 디렉토리 보장 (없으면 STEP 1 즉사)
```

> ⚠️ 반드시 `.venv/bin/python` 사용 — 시스템 `python3` 은 duckdb 등 의존성이 없어 STEP 6 에서 실패한다.

**미충족 시 즉시 종료**:
- `config:MISSING` 또는 identity 필드 누락 → "**`/prepare-pipeline`** 먼저 호출하여 brand+season 셋팅하세요."
- `plc:.*MISSING` → "PLC csv 없음. **`/prepare-pipeline`** 가 Stage 3 에서 자동 빌드. 다시 실행 후 본 스킬 호출."
- `env:MISSING` → "**`.env`** 없음. **`/onboard`** 먼저 호출하여 Snowflake 인증 셋팅."
- `restored:MISSING` → "`data/{brand}/{season}/restored.csv` 없음 — 엔지니어 제공 복원수요 파일로 **Snowflake 재생성 불가, 인수 패키지로만 전달됨** (`HANDOVER.md` 별도 전달물 참조). 없으면 STEP 3 (PLC 엔진) 이 `KeyError: ADJ_SC_SALE_QTY_TAX` 로 실패한다."

모두 충족 시 Stage 1 로.

### Stage 1 — 분석 임계값 검토 (AskUserQuestion)

**Read** `public/brand_config.json` 후 다음 4 임계값을 표로 출력:

| 한글 라벨 | config 키 | 현 값 |
|---|---|---|
| 목표판매율 (%) | `targetSellThrough` | (현 값) |
| 대물량 목표판매율 (%) | `highVolumeTargetSellThrough` | (현 값) |
| 대물량 기준 (상위 %) | `highVolumeTopPercent` | (현 값) |
| 스타일 등급 기준 (S/A/B/C) | `gradeThresholds.S/A/B/C` | (현 값) |

**AskUserQuestion**:
- 옵션 A: **"기본값 그대로 진행 (권장)"** — 현 값 유지하고 Stage 4 직행
- 옵션 B: "수정할 항목 있음"

옵션 A 선택 → Stage 4 로 직행. 옵션 B → Stage 2.

### Stage 2 — 수정값 자유 텍스트 입력

사용자에게 안내 출력:

```
수정할 항목과 새 값을 알려주세요. 형식: <항목>=<값> 콤마 또는 줄바꿈 구분.

예시:
  목표판매율=70
  대물량 목표판매율=75, 대물량 기준=3
  등급S=60, 등급A=50, 등급B=40, 등급C=30
```

**한글 라벨 → config 키 매핑** (fuzzy 매칭):

| 입력 라벨 (허용 표기) | 영문 키 |
|---|---|
| `목표판매율`, `목표 판매율` | `targetSellThrough` |
| `대물량 목표판매율`, `대물량 목표 판매율` | `highVolumeTargetSellThrough` |
| `대물량 기준`, `대물량(상위%)`, `대물량 상위` | `highVolumeTopPercent` |
| `등급S`, `S등급`, `등급 S`, `gradeS` | `gradeThresholds.S` |
| `등급A`, `A등급`, `gradeA` | `gradeThresholds.A` |
| `등급B`, `등급 B`, `gradeB` | `gradeThresholds.B` |
| `등급C`, `gradeC` | `gradeThresholds.C` |

알 수 없는 라벨 → 사용자에게 경고 출력 + 무시 (스킬은 진행).

입력 파싱 후 변경 요약을 사용자에게 보여주기:
```
변경 요약:
  목표판매율: 65 → 70
  등급S: 65 → 60
```

### Stage 3 — `brand_config.json` 갱신

**Read** `public/brand_config.json` (다른 필드 보존용).

**Edit** 으로 변경된 필드만 수정:
- `targetSellThrough`, `highVolumeTargetSellThrough`, `highVolumeTopPercent` 는 top-level 필드.
- `gradeThresholds.{S,A,B,C}` 는 nested object.

**Read 재검증**: 변경 후 다시 Read 해서 의도대로 갱신됐는지 확인. identity 필드 (brand / baseSeason / targetSeason 등) 가 그대로 보존됐는지도 확인.

### Stage 4 — `run_all.py` 실행 (background)

**Bash (run_in_background=true)**:

```bash
cd <project_root> && PYTHONPATH=. ORDERAI_BROKEN_VANCHOR=1 .venv/bin/python scripts/run_all.py > /tmp/run_pipeline.log 2>&1
```

> `ORDERAI_BROKEN_VANCHOR=1` 은 원본(order_ai) 운영 확정값 — 인시즌 결품 SC 의 forward 투영 고정.
> 마감시즌 실행에는 영향 없으므로 항상 포함해 `/weekly-refresh` 와 결과 일관성을 유지한다.
> 인터프리터는 반드시 `.venv/bin/python` (시스템 python3 금지 — 의존성 없음).

Background ID 사용자에게 안내: "파이프라인 실행 중 (예상 3-10분). 완료되면 결과 진단합니다."

6 step 순서 (참고):
```
[1/6] main.py             — 시즌 마감 분석 (season_closing_data.json)
[2/6] weekly_analysis.py  — 주차별 시계열 (dashboard_data.json, past_styles_data.json)
[3/6] ai_sales_loss_v3.py — PLC 기반 기회비용
[4/6] step4_integration.py — 유사 스타일 매핑 (style_mapping_data.json)
[5/6] generate_size_data.py — 사이즈 데이터 (size_assortment_data.json)
[6/6] dump_to_duckdb.py   — baseline DuckDB 적재 (order_ai.duckdb)
```

> 첫 실행 시 `order_recommendation_data.json` (사물함 영역) 부재가 정상 — Stage 6 의 dump_to_duckdb 가 INFO 로그 출력 후 빈 baseline 으로 OK 적재.

### Stage 5 — 결과 진단

완료 알림 받으면:

**Read** `/tmp/run_pipeline.log` (마지막 ~100줄).

**완료 step 카운트**: 로그에서 `[N/5]` 패턴 등장 횟수.

**진단 패턴**:

| 패턴 | 진단 + 다음 행동 |
|---|---|
| `[6/6]` 완료 + 에러 없음 | "[OK] 전체 6 step 완료 (분석 5 + baseline DuckDB)." → 산출물 점검 후 Stage 6 |
| `snowflake.connector.errors` 또는 `HttpError` | "Snowflake 연결 실패. `/onboard` Stage 6 진단표 참조하여 `.env` 점검." |
| `Incorrect username or password` 또는 `390100` | "Snowflake credentials 오류. `.env` 의 SNOWFLAKE_USER / PASSWORD 재확인." |
| `FileNotFoundError.*plc_forecast_standard` 또는 `RuntimeError.*PLC` | "PLC csv 부재 또는 손상. `/prepare-pipeline` 재호출하여 빌드." |
| `KeyError.*BRAND` 또는 `RuntimeError.*brand` | "`.env` 의 BRAND ↔ `brand_config.json` 의 brand 불일치. 일치시키고 재실행." |
| `[N/5]` 가 `[5/5]` 미만 (중간 fail) | "Step {N} 에서 fail. 로그 마지막 30줄 출력 후 사용자에게 진단 요청." |
| 그 외 | 로그 마지막 30줄 출력 + "에러 메시지를 Claude Code 에 붙여 추가 진단 요청." |

**산출물 점검** (Stage 5 PASS 시):

```bash
test -d data/production && ls data/production/*.duckdb 2>&1
ls -la public/*.json 2>&1 | grep -v brand_config | grep -v color_mapping | grep -v plc_engine
ls -la output/ 2>&1 | head -10
```

산출물 키 (정상 시 존재):
- `data/production/order_ai.duckdb` — DuckDB 적재
- `public/dashboard_data.json` — Step 1 의 dashboard 출력
- `public/*.json` (각 step JSON)
- `output/*.xlsx` — 분석 Excel

산출물 부재 시 → "Step 별 fail 가능성. 로그 다시 확인."

### Stage 6 — 다음 안내

Stage 5 PASS 시:
- "다음으로 **`/server-start`** 호출하여 백엔드 + 프론트엔드 기동 + 화면 띄우기. (`/server-start` 미구현 시 임시 안내):"
  ```
  # 터미널 1
  .venv/bin/uvicorn server.api:app --port 8000
  # 터미널 2
  cd apps/lite && npm run dev
  # 브라우저: http://localhost:5173
  ```

Stage 5 fail 시:
- "임계값 조정 후 재실행하려면 본 스킬 (`/run-pipeline`) 다시 호출."

## 안전 제약

- `brand_config.json` 의 **4 임계값 영역 외 필드 수정 금지** — identity 영역 (brand / baseSeason / targetSeason / seasonType / startDate / endDate) 은 `/prepare-pipeline` 책임.
- `gradeThresholds` 의 부분 키 수정 시 (예: S 만) 나머지 (A, B, C) 보존.
- `run_all.py` 는 **background 실행** — 5-10분 걸릴 수 있음. 완료 알림 받으면 진단.
- 기존 산출물 (`public/dashboard_data.json`, `data/production/order_ai.duckdb` 등) 덮어쓰기는 정상 동작 (재실행 의도).
- Stage 0 의존성 점검 미충족 시 절대 Stage 1 진입 X.
- 사용자가 Stage 2 입력에서 100 보다 큰 값 (예: `목표판매율=120`) → 경고 출력 후 진행 (스킬은 강제 거부 안 함, 운영자 판단 존중).

## 참고 문서

- 분석 임계값 의미: `scripts/config_loader.py` 의 getter 함수 docstring
- run_all.py 의 6 step (5 분석 + dump_to_duckdb): `scripts/run_all.py`
- Snowflake 트러블슈팅: `SETUP.md` §Troubleshooting FAQ
- 변경 가드레일: `CLAUDE.md` §1
- 이전 스킬: `/prepare-pipeline` (identity 영역)
- 다음 스킬: `/server-start` (예정 — 백엔드 + 프론트 기동)

