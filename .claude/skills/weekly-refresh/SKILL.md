---
name: weekly-refresh
description: 주간판매데이터 갱신 E2E — Snowflake 강제 재조회 → run_all 6 step 재실행 → 지식그래프(KG) 교차검증 게이트 → 통과 시에만 baseline 반영(실패 시 직전 baseline 자동 롤백). 인시즌 주차 갱신 루틴. /run-pipeline 이 최초 1회 완료된 이후에 사용.
---

# weekly-refresh

원본(order_ai)의 주간갱신 E2E 체인(`weekly_refresh_and_deploy.sh`)을 order-ai-share 모델로 이식한 스킬.
원본과의 차이: 멀티브랜드 루프 없음(단일 브랜드), S3/EC2 배포 없음(로컬 baseline 교체가 곧 배포), Slack 알림 없음.

**핵심 안전장치**: run_all 은 baseline DuckDB 를 직접 덮어쓰므로, 실행 전 직전 baseline 을 백업하고
KG 게이트 **실패 시 백업으로 롤백**한다 → "게이트 차단 시 운영은 직전 baseline 유지" 라는 원본 시맨틱 보존.

## 적용 시점 (trigger)

- "주간 갱신", "주간 데이터 갱신", "weekly refresh", "최신 주차 반영"
- 인시즌 운영 중 매주 1회 (권장: 월요일 — 직전 일요일 완결 주차 반영)

## 절차 (순서대로 엄격히 진행)

### Stage 0 — 의존성 점검

**Bash** 로 다음을 묶어 확인 (프로젝트 루트에서):

```bash
test -f .env && echo "env:OK" || echo "env:MISSING"
test -f public/brand_config.json && echo "config:OK" || echo "config:MISSING"
test -f data/production/order_ai.duckdb && echo "baseline:OK" || echo "baseline:MISSING"
(command -v dcs-ai-cli || ls ~/.local/bin/dcs-ai-cli /usr/local/bin/dcs-ai-cli /opt/homebrew/bin/dcs-ai-cli 2>/dev/null) >/dev/null 2>&1 && echo "kg-cli:OK" || echo "kg-cli:MISSING"
pgrep -f "uvicorn server.api" >/dev/null && echo "server:RUNNING" || echo "server:STOPPED"
```

**미충족 시 즉시 종료** (어떤 파일도 건드리기 전):
- `env:MISSING` → "`.env` 없음. **`/onboard`** 먼저." (무인에 가까운 실행을 위해 `SNOWFLAKE_AUTH=password` 권장 — SSO 는 브라우저 팝업 발생)
- `config:MISSING` 또는 `baseline:MISSING` → "최초 파이프라인 미실행 상태. **`/prepare-pipeline` → `/run-pipeline`** 먼저. weekly-refresh 는 갱신 루틴입니다."
- `kg-cli:MISSING` → "`dcs-ai-cli` 없음 — KG 교차검증 게이트 실행 불가. 설치(F&F 내부 배포본) 후 재시도하세요. 게이트 없이 갱신하는 우회는 이 스킬이 제공하지 않습니다(사용자가 명시적으로 요청한 경우에만 Stage 4 를 건너뛰되, '검증 없이 반영됨'을 결과에 굵게 명시)."
- `server:RUNNING` → 사용자에게 안내: "run_all 의 DuckDB 적재(STEP 6)가 서버의 read-only 커넥션과 쓰기 락 충돌할 수 있습니다. 서버를 잠시 종료합니다." → uvicorn/vite 종료 후 진행 (완료 후 Stage 5 에서 재기동 안내).

### Stage 1 — 직전 baseline 백업

```bash
mkdir -p data/.weekly_backup
cp data/production/order_ai.duckdb data/.weekly_backup/
cp public/*.json data/.weekly_backup/
echo "백업 완료: $(ls data/.weekly_backup | wc -l) 파일"
```

`data/` 는 gitignore 영역이라 백업이 커밋될 일 없음. 백업 실패 시 진행 중단.

### Stage 2 — Snowflake 데이터 강제 재조회

```bash
.venv/bin/python scripts/_refresh_data.py
```

- 출력의 `max END_DT` 가 **직전 일요일(최신 완결 주차)** 인지 확인 — 아니면 Snowflake 원천 지연이므로 사용자에게 보고 후 중단(백업 불필요한 상태 그대로).
- d1/d2/gt 중 `error` 가 있으면 중단. (d3_r1/r2·d1_prev_asof 의 skip 은 비인시즌/모델 부재로 정상일 수 있음 — 메시지로 판단)

### Stage 3 — 분석 파이프라인 재실행 (run_all 6 step)

```bash
ORDERAI_BROKEN_VANCHOR=1 .venv/bin/python scripts/run_all.py
```

- `ORDERAI_BROKEN_VANCHOR=1` 은 **원본 운영 확정값** — 인시즌 결품 SC 의 forward 투영을 vanchor(당해 최근속도)로 고정. 미설정 시 전년형상 외삽으로 회귀하므로 반드시 포함.
- 어느 step 이라도 실패하면 → **Stage 5-FAIL 롤백** 실행 후 실패 로그 보고.

### Stage 4 — KG 교차검증 게이트

```bash
.venv/bin/python scripts/_verify_kg_crosscheck.py
```

- 로컬 weekly_raw(DB_SCS_W 원천) ↔ 지식그래프(DW_SALE) 의 판매량·판매택가를 **시즌누계 + 최신 완결주차** 각각 대사. 허용오차 기본 1%.
- exit 0 → Stage 5-PASS. exit ≠ 0 → Stage 5-FAIL.

### Stage 5-PASS — 반영 확정

```bash
rm -rf data/.weekly_backup
```

- 서버는 요청마다 read-only 커넥션을 새로 열므로 별도 배포 절차 없음 — **갱신된 baseline 이 곧 서빙 데이터**.
- Stage 0 에서 서버를 종료했다면 **`/server-start`** 로 재기동 안내.
- 결과 요약 출력: 갱신 주차(END_DT), KG 대사 결과 라인, "✅ 주간 갱신 완료 — KG 교차검증 통과, baseline 반영됨".

### Stage 5-FAIL — 롤백 (직전 baseline 유지)

```bash
cp data/.weekly_backup/order_ai.duckdb data/production/
cp data/.weekly_backup/*.json public/
rm -rf data/.weekly_backup
```

- 보고: "❌ KG 교차검증 실패(또는 파이프라인 실패) → 직전 baseline 으로 롤백 완료. 운영 데이터는 갱신 전 상태 유지."
- KG 불일치 라인(❌ 표시)을 그대로 보여주고 원인 후보 안내: Snowflake 원천 지연 / 시즌·브랜드 스코프 불일치 / KG API 장애.
- 재시도 안내: 원인 해소 후 `/weekly-refresh` 재실행. (data/{brand}/{season}/*.csv 캐시는 갱신된 상태로 남지만 baseline 이 롤백되었으므로 서빙에 영향 없음)

## 주의

- 본 스킬은 **인시즌 주간 루틴**이다. 브랜드/시즌 전환은 `/prepare-pipeline`, 최초 실행은 `/run-pipeline` 책임.
- `scripts/_refresh_data.py` · `scripts/_verify_kg_crosscheck.py` 는 원본(order_ai) 정본 미러링 파일 — 수정 필요 시 오너에게 원천 반영 요청 (CLAUDE.md §2 동기화 예외 참조).
