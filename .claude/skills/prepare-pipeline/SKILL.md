---
name: prepare-pipeline
description: order-ai-share 의 분석 파이프라인을 특정 brand+season 조합으로 준비. brand_config.json 갱신 → PLC 표준 csv 확보 (없으면 자동 빌드). 실제 파이프라인 실행은 후속 스킬 `/run-pipeline` 의 책임. 운영자가 새 브랜드/시즌으로 전환하거나 첫 셋업, 또는 PLC csv missing 에러를 만난 경우 사용.
---

# prepare-pipeline

운영자가 brand+season 을 선택하고 분석 파이프라인을 돌릴 수 있는 상태까지 끌어주는 인터랙티브 스킬.

## 적용 시점 (trigger)

- "MLB FW 로 운영 시작하고 싶어", "Discovery SS 로 전환할게" 같은 요청
- `run_all.py` 가 PLC csv 부재로 실패한 직후
- 첫 셋업 후 어떤 명령부터 돌릴지 모를 때

## 절차 (순서대로 엄격히 진행)

### Stage 1 — 입력 수집 (AskUserQuestion 1회)

다음 두 질문을 한 번에 묶어 묻는다:

**Q1**: 운영할 브랜드?
- MLB
- Discovery
- Other (직접 입력)

**Q2**: 기준 시즌 (`baseSeason`)?
- 25F / 25S / 26F / 26S 중 선택
- 또는 "Other" 로 직접 입력 (예: 27F)

`targetSeason` 은 자동 도출: `baseSeason` 의 연도 +1, 같은 type. 예: `25F` → `26F`, `26S` → `27S`. 사용자에게 도출값을 보여주고 확인.

브랜드가 "Other" 면 BRAND_CODE_MAP (`scripts/plc_engine/build_plc_standard.py`) 에 단일자 코드 추가 필요 — 경고하고 진행.

### Stage 2 — `public/brand_config.json` 갱신

1. **Read** `public/brand_config.json` 먼저 (다른 필드 보존).
2. **Edit** 다음 필드만:
   - `brand`: 입력값 (case-preserve, 예: "MLB" 또는 "Discovery")
   - `baseSeason`: 입력값 (예: "25F")
   - `targetSeason`: 도출값 (예: "26F")
   - `seasonType`: 마지막 글자 기반, 대문자 — "FW" (F) 또는 "SS" (S)
   - `startDate`: FW → `{"month": "09", "day": "01"}`, SS → `{"month": "03", "day": "01"}`
   - `endDate`: FW → `{"month": "02", "day": "28"}`, SS → `{"month": "08", "day": "31"}`
3. **Read** 다시해서 4-5개 필드 모두 의도대로 변경됐는지 확인.

### Stage 3 — PLC 표준 csv 확보

예상 경로 계산:
```
data/plc/{brand_lower}_{type_lower}_plc_forecast_standard.csv
```
(`type` = `targetSeason[-1].lower()` → fw/ss)

**Bash 로 존재 확인**: `test -f <path> && echo EXISTS || echo MISSING`

**Case A — 존재**:
- "[OK] PLC csv 준비됨: {path}" 출력 → Stage 4 로.

**Case B — 부재**:
1. 사용자에게 안내: "PLC csv 없음 → build_plc_standard.py 실행. data/{BRAND}_GT_*.csv 자동 탐색 또는 Snowflake fallback."
2. **Bash 로 빌드 실행** (반드시 `.venv/bin/python` — 시스템 python3 은 의존성 없음):
   ```
   cd <project_root> && BRAND=<brand_upper> PYTHONPATH=. .venv/bin/python scripts/plc_engine/build_plc_standard.py
   ```
3. 종료 코드 확인:
   - **성공 (exit 0)**: 출력 끝부분의 `행 수`, `아이템 수` 발췌해서 사용자에게 보고. Drift report 가 있으면 그 결과도 한 줄 요약.
   - **실패 (exit != 0)**:
     - stderr 에 "GT CSV 없음" 포함 → "data/{BRAND}_GT_*.csv 가 필요합니다. AX팀에서 GT csv 를 받거나 .env 의 Snowflake 설정을 완료한 뒤 다시 시도해 주세요."
     - "Snowflake 조회 실패" 포함 → "SETUP.md §4 의 Snowflake 환경변수 설정을 확인하세요."
     - 그 외 → 마지막 5-10줄 stderr 그대로 출력 + 사용자에게 진단 요청.
     - 어느 경우든 Stage 4 진입 금지.

### Stage 4 — 다음 단계 안내

PLC csv 까지 확보된 상태. **분석 임계값 검토 + 파이프라인 실행은 후속 스킬 `/run-pipeline` 의 책임** (책임 분리).

사용자에게 안내 출력:

```
[OK] brand+season+PLC 셋업 완료.

다음: /run-pipeline 호출
   - 분석 임계값 4종 (목표판매율 / 대물량 목표판매율 / 대물량 기준 / 등급 기준) 검토
   - run_all.py 6 step 실행 (5 분석 + baseline DuckDB 적재)
   - 결과 진단
```

## 안전 제약

- `public/brand_config.json` 외 파일 수정 금지 (단 build_plc_standard.py 가 생성하는 `data/plc/*.csv` 는 자동 산출이라 OK).
- 기존 PLC csv 삭제 금지.
- 사용자가 중간에 취소하면 brand_config.json 은 Edit 시점까지의 상태로 유지 (Edit 는 atomic). 사용자에게 현 상태 명시.
- Bash 실행 시 `cd <project_root>` 명시 — 작업 디렉토리 일관성.

## 참고 문서

- 환경 셋업: `SETUP.md` §4 (Snowflake 환경변수)
- PLC 정책: `docs/PLC_GUIDE.md`
- 변경 가드레일: `CLAUDE.md` §1 (변경 가능 vs 금지 영역)
- 검증 명령: `CLAUDE.md` §3

## 사용 예시 (대화)

```
사용자: /prepare-pipeline

스킬: [Q1] 운영할 브랜드? (MLB / Discovery / Other)
      [Q2] 기준 시즌 (baseSeason)? (25F / 25S / 26F / 26S / Other)
사용자: MLB, 26F

스킬: targetSeason = 27F 로 도출됩니다. 진행할까요?
사용자: 네

스킬: [Stage 2] brand_config.json 갱신 중...
      brand=MLB, baseSeason=26F, targetSeason=27F, seasonType=FW
      [OK] 갱신 완료

      [Stage 3] PLC 표준 csv 확보 중...
      예상 경로: data/plc/mlb_fw_plc_forecast_standard.csv
      [INFO] 파일 부재 → build_plc_standard.py 자동 실행
      ... (build 진행) ...
      [OK] PLC csv 생성 완료: 669행, 23 아이템

      [Stage 4] 분석 파이프라인을 지금 실행할까요? (예/아니오)
```
