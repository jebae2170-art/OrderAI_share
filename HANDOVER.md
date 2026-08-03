# HANDOVER — order-ai-share 인수인계 체크리스트

> 신규 운영자가 zip(또는 git clone)만 받으면 되는 게 아닙니다.
> 아래 **별도 전달물 4종**이 모두 있어야 파이프라인·주간갱신이 완성됩니다.

---

## 1. 전달물 체크리스트

### 인수자가 받을 것

| # | 전달물 | 전달 방법 | 없을 때 증상 |
|---|---|---|---|
| 1 | **코드** — zip 또는 GitHub 초대 | zip 은 **Finder 더블클릭으로 해제** (터미널 `unzip` 은 한글 파일명 깨짐) | — |
| 2 | **`.env` 값** — Snowflake 계정 (password 모드 권장) | **1Password share / GPG** (plain email·Slack DM 금지) | setup.sh step 4 FAIL |
| 3 | **`restored.csv`** — 브랜드×시즌별 복원수요 (엔지니어 제공, Snowflake 재생성 불가) | 파일 전달 → `data/{brand}/{season}/restored.csv` 배치 (예: `data/mlb/26s/`) | run_all STEP 3 `KeyError: ADJ_SC_SALE_QTY_TAX` |
| 4 | **`dcs-ai-cli`** 바이너리 + API key | 설치는 `SETUP.md` §4.5 | `/weekly-refresh` KG 게이트 실행 불가 |

### zip 에 이미 들어있는 것 (별도 전달 불필요)

- PLC 표준 csv 4종 (`data/plc/{mlb,discovery}_{ss,fw}_plc_forecast_standard.csv`)
- 스킬 5종 (`.claude/skills/` — onboard / prepare-pipeline / run-pipeline / server-start / weekly-refresh)
- `output/`·`state/`·`data/user-storage/` 등 산출물 디렉토리 골격

### 시즌 데이터(csv)와 baseline DuckDB 는 왜 없나

의도적으로 제외 — 전부 **첫 파이프라인 실행 시 Snowflake 에서 재생성**됩니다 (약 5분).
첫 실행이 곧 환경 검증(계정 권한·의존성)이자 매주 할 일의 리허설입니다.

---

## 2. 첫날 순서 (Claude Code 에서)

```
1. zip 해제 (Finder 더블클릭) → 폴더에서 Claude Code 실행
2. /onboard          — .env 작성 + setup.sh (venv·npm·Snowflake 연결 검증)
3. restored.csv 배치 — data/{brand}/{season}/ 에 복사
4. /prepare-pipeline — 운영 브랜드+시즌 선택
5. /run-pipeline     — 첫 파이프라인 (분석 5 + baseline DuckDB)
6. /server-start     — 화면 확인 (http://localhost:5173)
```

첫 실행은 **이관 미팅에서 전임자와 함께** 돌려보는 것을 권장합니다.

## 3. 이후 매주 루틴

**매주 월요일 09시 이후** — `README.md` §주간 운영 루틴 참조.
09시 이전 실행 시 상류 GT(예측 입력) 적재 전이라 갱신 스크립트/게이트가 지연을 감지하고 중단합니다 → 09시 이후 재실행.

## 4. 문제 발생 시

1. 스킬이 출력한 진단 메시지 → `SETUP.md` §Troubleshooting FAQ
2. 해결 안 되면 에러 로그 전문을 Claude Code 에 붙여넣고 진단 요청
3. 코드 로직 문제(발주 수식·PLC 엔진)로 판단되면 **직접 수정하지 말고** 오너에게 원천(order_ai) 반영 요청 — `CLAUDE.md` §2 변경 가드레일
