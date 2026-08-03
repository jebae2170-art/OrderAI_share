# HANDOVER — order-ai-share 인수인계 체크리스트

> 신규 운영자가 zip(또는 git clone)만 받으면 되는 게 아닙니다.
> 아래 **별도 전달물**이 모두 있어야 파이프라인·주간갱신이 완성됩니다.

## 0. 전달본 2트랙 — 목적에 맞는 것을 고르세요

| 트랙 | 파일 | 대상·목적 | 만드는 법 |
|---|---|---|---|
| **A. 운영 이관 패키지** | `order-ai-share-handover_<날짜>.zip` (~24M) | **주간판매갱신 전담 운영담당자** — 데이터(baseline·시즌캐시·restored) 포함, 초기 파이프라인 생략 → §1.5 | `./package_handover.sh` (시크릿 자동 제외+자가검증) |
| **B. 코드배포본** | `order-ai-share.zip` (~0.4M) | 코드 공유·개발 인수 — git 추적 파일만, 데이터 없음 → §1~2 전체 절차 필요 | `git archive --format=zip -o ../order-ai-share.zip HEAD` |

두 트랙은 **별도로 유지**합니다 — A는 운영 스냅샷(전달 시점 데이터 포함), B는 항상 깨끗한 코드 재현본.

---

## 1. 전달물 체크리스트

### 인수자가 받을 것

| # | 전달물 | 전달 방법 | 없을 때 증상 |
|---|---|---|---|
| 1 | **코드** — zip 또는 GitHub 초대 | zip 은 **Finder 더블클릭으로 해제** (터미널 `unzip` 은 한글 파일명 깨짐) | — |
| 2 | **`.env` 값** — Snowflake 계정 (password 모드 권장) | **1Password share / GPG** (plain email·Slack DM 금지) | setup.sh step 4 FAIL |
| 3 | **`restored.csv`** — 브랜드×시즌별 복원수요 (엔지니어 제공, Snowflake 재생성 불가) | 파일 전달 → `data/{brand}/{season}/restored.csv` 배치 (예: `data/mlb/26s/`) | run_all STEP 3 `KeyError: ADJ_SC_SALE_QTY_TAX` |
| 4 | **`dcs-ai-cli`** 바이너리 + API key | 설치는 `SETUP.md` §4.5 | `/weekly-refresh` KG 게이트 실행 불가 |
| 5 | **`S3_API_KEY`** (S3 Presigned URL API, AX팀 발급) | 1Password → `.env` 에 기입 | 운영배포(S3 업로드) 생략 — 로컬 반영까지만 |
| 6 | **EC2 ssh 키 + 접속 대상** (`user@host` — 1Password) | 키 파일 전달 → `~/.ssh` 배치, 접속 대상은 `.env` 에 `EC2_SSH_TARGET=user@host` 로 기입 (BatchMode 접속 확인) | EC2 재시작·서빙검증 생략 — 로컬 반영까지만 |

> #5·#6 이 없어도 갱신·KG검증·로컬 반영은 정상 동작합니다. **운영(EC2 8520) 배포 권한을 넘길 때만** 전달하고,
> 전달 시점에 **원본 머신의 n8n 주간 자동배포는 반드시 비활성화** (이중 배포 경합 — 컷오버 절차는 원본 운영이관 가이드).

### zip 에 이미 들어있는 것 (별도 전달 불필요)

- PLC 표준 csv 4종 (`data/plc/{mlb,discovery}_{ss,fw}_plc_forecast_standard.csv`)
- 스킬 5종 (`.claude/skills/` — onboard / prepare-pipeline / run-pipeline / server-start / weekly-refresh)
- `output/`·`state/`·`data/user-storage/` 등 산출물 디렉토리 골격

### 시즌 데이터(csv)와 baseline DuckDB 는 왜 없나

의도적으로 제외 — 전부 **첫 파이프라인 실행 시 Snowflake 에서 재생성**됩니다 (약 5분).
첫 실행이 곧 환경 검증(계정 권한·의존성)이자 매주 할 일의 리허설입니다.

---

## 1.5. Track A — 운영 이관 패키지 (주간갱신 전담 담당자용)

`./package_handover.sh` 로 생성 — 운영하던 폴더가 **데이터째 넘어가므로 절차가 줄어듭니다**:

**생략되는 것** ✅
- `/run-pipeline` 초기 실행 — baseline DuckDB(과거시즌 25f 시드 포함)가 폴더에 있음
- `restored.csv` 별도 전달(#3) — `data/{brand}/{season}/` 에 이미 배치됨
- `_seed_missing_seasons_from_prd.py` 최초 시드 — baseline 에 이미 반영됨
- → 첫 월요일부터 바로 `/weekly-refresh` 루틴 가능

**그래도 새 머신에서 반드시 필요한 것** ⚠️ (머신 레벨 — 폴더로 못 넘어감)
| # | 작업 | 이유 |
|---|---|---|
| 1 | `setup.sh` 재실행 (`.venv` 재생성) | `.venv` 는 절대경로·전임자 머신의 Python 에 묶여 있어 **다른 머신/경로에서 깨짐**. 폴더 안에 .venv 가 보여도 그대로 쓸 수 없음 |
| 2 | 본인 `.env` 작성 (§4) | 전달물 #2·#5 는 여전히 1Password 로 |
| 3 | dcs-ai-cli 설치 + API key (`SETUP.md` §4.5) | `~/.local/bin`·설정파일이 머신 레벨 |
| 4 | EC2 ssh 키 `~/.ssh` 배치 (#6) | 머신 레벨 |

**전임자(넘기는 쪽)** 🔒
- 패키징은 반드시 **`./package_handover.sh`** 로 — `.env`(시크릿)·`.venv`·`.git`·`rollback` 백업·`logs` 를 자동 제외하고, 시크릿 미포함·핵심 데이터 포함을 자가검증합니다. 수동 압축 금지.
- [ ] 전달 후 **본인 머신의 배포 경로 비활성화 확인** — n8n(원본) 및 이 폴더에서의 수동 배포 중단 (활성 배포 주체는 전체에서 한 곳만)

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
미팅 아젠다(~50분): 환경 확인 → 인증모드 결정·credential 전달(1Password) → 셋업 검증(setup.sh) → 첫 파이프라인+화면 확인 → 주간 루틴 시연·Q&A.

## 3. 이후 매주 루틴

**매주 월요일 09시 이후** — `README.md` §주간 운영 루틴 참조.
09시 이전 실행 시 상류 GT(예측 입력) 적재 전이라 갱신 스크립트/게이트가 지연을 감지하고 중단합니다 → 09시 이후 재실행.

## 4. 문제 발생 시

1. 스킬이 출력한 진단 메시지 → `SETUP.md` §Troubleshooting FAQ
2. 해결 안 되면 에러 로그 전문을 Claude Code 에 붙여넣고 진단 요청
3. 코드 로직 문제(발주 수식·PLC 엔진)로 판단되면 **직접 수정하지 말고** 오너에게 원천(order_ai) 반영 요청 — `CLAUDE.md` §2 변경 가드레일
