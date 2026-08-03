---
name: onboard
description: order-ai-share 의 첫 셋업 자율 가이드. OS 확인 → Prereqs 점검 → .env 모드 결정 → setup.sh 실행 → 결과 진단 → 다음 skill 안내. 운영자가 코드 받은 직후 가장 먼저 호출. setup.sh 실패 진단도 본 스킬로 흡수.
---

# onboard

운영자가 프로젝트를 받고 README 를 연 직후, 자율 셋업을 위해 가장 먼저 호출하는 스킬. `setup.sh` 의 자가검증 흐름을 인터랙티브하게 감싸고, 실패 시 진단 메시지를 패턴 매칭으로 직접 제공한다.

## 적용 시점 (trigger)

- "처음 받았어, 뭐부터 해야 하지?"
- `README.md` 안내에 따라 첫 호출
- `./setup.sh` 결과가 fail 났을 때 진단이 필요한 경우
- `.env` 모드 (SSO / Service Account / skip) 결정이 필요할 때

## 절차 (순서대로 엄격히 진행)

### Stage 1 — OS 감지 + 확인

**Bash**: `uname -s` 로 감지.

| 반환값 | 해석 |
|---|---|
| `Darwin` | macOS |
| `Linux` | Ubuntu / WSL2 / 그 외 Linux |
| `MINGW*` / `CYGWIN*` / `MSYS*` | Windows native (Git Bash) |

**AskUserQuestion**: "감지된 OS: `{os}`. 맞나요?"

- 옵션:
  - "맞음 (감지값 그대로)" — 권장 (자동감지 신뢰)
  - "macOS"
  - "Linux/WSL"
  - "Windows native"

**Windows native 선택 시 즉시 종료**:
- 안내 출력: "본 fork 는 bash 기반 `setup.sh` 사용. **WSL2 (Ubuntu) 설치 권장**. WSL2 환경에서 다시 `/onboard` 호출하세요. SETUP.md §0 참조."
- 스킬 종료.

이후 Stage 에서 OS 별 분기 안내에 활용 (최종 OS 라벨: macOS / Linux/WSL).

### Stage 2 — Prereqs 점검

**Bash**: 다음을 묶어 한 번에 확인:
```
python3 --version 2>&1; node --version 2>&1; npm --version 2>&1
```

만족 조건:
- Python **3.11+**
- Node **18+**
- npm 존재

**미충족 시 OS 별 안내 출력 후 종료**:

| OS | 설치 명령 안내 |
|---|---|
| macOS | `brew install python@3.11 node` |
| Linux/WSL | `sudo apt update && sudo apt install python3.11 python3.11-venv nodejs npm` |

"설치 후 다시 `/onboard` 호출하세요." → 종료.

만족 시 Stage 3 로.

### Stage 3 — .env 모드 결정 (AskUserQuestion 1회)

질문: "Snowflake 인증 모드를 선택하세요."

- 옵션 A: "**SSO (externalbrowser) — 권장 (기본)**"
  - 본인 SSO 이메일만 있으면 됨. 임직원 기본 경로.
- 옵션 B: "Service Account"
  - 1Password 의 service account password 보유자 한정. 헤드리스 환경 (CI/Docker) 용.
- 옵션 C: "skip — pre-meeting prep"
  - Snowflake 없이 `.venv`/npm 만 설치. 나중에 다시 `/onboard`.

옵션 C 선택 시 → Stage 4 skip 하고 Stage 5 로.

### Stage 4 — .env 작성 (옵션 A/B 한정)

**Bash**: `.env` 존재 여부 확인:
```
test -f .env && echo EXISTS || echo MISSING
```

**EXISTS 인 경우 사용자 확인**:
- AskUserQuestion: "기존 `.env` 있음. 어떻게 할까요?"
  - 옵션:
    - "기존 그대로 사용" → Stage 5 로 (cp + 키 안내 skip).
    - "백업하고 새로 작성" → `.env` 를 `.env.backup-<timestamp>` 로 백업 후 cp 진행.

**MISSING 또는 새로 작성**:
- **Bash**: `cp .env.example .env`

**모드별 필요 키 안내 (사용자에게 출력만, 값 입력은 사용자가 직접 편집)**:

옵션 A (SSO):
- `SNOWFLAKE_ACCOUNT` — 1Password share 또는 회의 화면 공유로 받은 account 값
- `SNOWFLAKE_USER` — 본인 SSO 이메일 (예: `your.email@fnf.co.kr`)
- `SNOWFLAKE_AUTH=externalbrowser` — **이 줄 반드시 추가**
- `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`
- `SNOWFLAKE_PASSWORD` — SSO 모드에선 무시됨, 비워두거나 줄 삭제 OK
- `BRAND` — `public/brand_config.json` 의 `brand` 값과 **일치 필수** (대소문자 무관)

옵션 B (Service Account):
- `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` (예: `your_service_account`)
- `SNOWFLAKE_PASSWORD` — 1Password 값 (절대 git 커밋 X)
- `SNOWFLAKE_AUTH=externalbrowser` 줄 추가 **X**
- 나머지 동일

**사용자에게 `.env` 직접 편집 안내**:
- 스킬은 `.env` 값을 만지지 않음 (보안)
- "에디터로 `.env` 열어 위 키 채우신 뒤 '완료' 라고 알려주세요." 안내.

**완료 후 검증 (값 출력 X)**:
- **Bash**: 키 존재만 확인, 값은 마스킹:
  ```
  awk -F= '/^SNOWFLAKE_ACCOUNT=|^SNOWFLAKE_USER=|^BRAND=/ {print $1"=<set>"}' .env
  ```
- 누락된 필수 키 있으면: "누락: {key}. `.env` 다시 편집 후 '완료' 라고 알려주세요." → Stage 4 의 검증 단계로 복귀.

### Stage 5 — setup.sh 실행 (background)

**Bash (run_in_background=true)**:

- 옵션 C: `./setup.sh --skip-snowflake > /tmp/onboard_setup.log 2>&1`
- 옵션 A/B: `./setup.sh > /tmp/onboard_setup.log 2>&1`

Background ID 받아서 사용자에게 안내: "setup.sh background 실행 중 (예상 2-5분 — venv + npm install). 완료되면 진단합니다."

옵션 A 의 경우: "**SSO 모드** 는 smoke_test 단계에서 브라우저 팝업이 뜹니다. 미리 SSO 로그인 준비해 두세요."

### Stage 6 — 결과 진단

완료 알림 받으면:

**Read** `/tmp/onboard_setup.log` 의 전체 내용 (또는 마지막 100줄).

다음 패턴 매칭으로 진단:

| 패턴 (로그 내 등장 시) | 진단 + 다음 행동 |
|---|---|
| `=== SETUP COMPLETE: PASS ===` | "[OK] 셋업 완료." → Stage 7 |
| `=== SETUP COMPLETE: PRE-MEETING ===` | "[OK] pre-meeting 셋업 완료." → Stage 7 (skip 경로) |
| `FAIL: python3 not found` 또는 `FAIL: node not found` | "Prereqs 미설치. Stage 2 안내 참조 후 설치하고 다시 `/onboard`." |
| `FAIL: .env file not found` | "`.env` 누락. Stage 3 다시 진행하거나 옵션 C 선택." → Stage 3 복귀 권유 |
| `HttpError 290404` 또는 `404 Not Found.*snowflakecomputing.com` | "`SNOWFLAKE_ACCOUNT` 값 오타 의심. `.env` 의 account 값 재확인. 1Password 값과 한 글자도 다르지 않아야 합니다." |
| `Incorrect username or password` 또는 `390100` | "Credentials 오류. 옵션 A 면 본인 SSO 이메일 / 옵션 B 면 1Password password 재확인." |
| `Insufficient privileges` 또는 `No data found for brand` | "Snowflake 권한 부족. AX팀에 본인 role 의 브랜드 grant 확인 요청." |
| `RuntimeError.*brand` 또는 `BRAND.*mismatch` | "브랜드는 `public/brand_config.json` 의 `brand` 가 정본 — `.env` 에 BRAND 가 있다면 삭제(권장)하거나 일치시킬 것." |
| `Could not connect to.*externalbrowser` 또는 SSO timeout | "SSO 브라우저 팝업 미완료. 헤드리스 환경이면 옵션 B (Service Account) 로 전환." |
| 그 외 | 로그의 마지막 20줄을 출력 + "에러를 Claude Code 에 붙여넣어 추가 진단 요청." |

**PASS 가 아니면 Stage 7 진입 X**. 사용자가 수정 후 다시 `/onboard` 호출하거나, 부분 단계 (Stage 3-6) 만 다시 진행.

### Stage 7 — 다음 단계 안내

PASS 결과에 따라:

| Stage 5 결과 | 다음 안내 |
|---|---|
| 옵션 A/B PASS | "다음으로 **`/prepare-pipeline`** 호출하여 brand+season 셋업 + PLC csv 확보." |
| 옵션 C PASS | "Pre-meeting 셋업 완료. 온보딩 미팅 후 Snowflake credentials 받으면 `.env` 채운 뒤 다시 **`/onboard`** 호출." |

(향후 추가 예정 스킬: `/run-pipeline`, `/server-start` — 본 단계에선 언급만 가능, 미존재 시 안내 X)

## 안전 제약

- `.env` 의 **`SNOWFLAKE_PASSWORD` / `SNOWFLAKE_USER` 값을 출력 X**. grep/awk 시 키 이름만 + `<set>` 마스킹.
- `.env` 가 이미 있으면 덮어쓰기 전 반드시 사용자 확인 + 백업.
- `setup.sh` 는 **background 실행** — venv/npm install 가 길어질 수 있음. 완료 알림 받으면 결과 진단.
- Windows native 선택 시 setup.sh 실행 시도 금지 — WSL2 안내 후 즉시 종료.
- 본 fork 의 변경 가드레일은 `CLAUDE.md` §1 참조. 스킬은 `.env` 외 파일 수정 **X** (단 `.env.example` → `.env` 복사 + `.env.backup-*` 생성은 OK).
- 스킬이 `.env` 의 **값을 직접 입력하지 않는다** — 운영자가 에디터로 직접 채움 (보안).

## 참고 문서

- 첫 셋업 단계: `SETUP.md` (전체 흐름)
- 환경변수 + SSO/Service Account 분기: `SETUP.md` §4
- `.env` 키 목록 + 기본값: `.env.example`
- Snowflake 트러블슈팅: `SETUP.md` §Troubleshooting FAQ
- 변경 가드레일: `CLAUDE.md` §1
- 검증 명령: `CLAUDE.md` §3
- 다음 스킬: `/prepare-pipeline` (brand+season + PLC csv)

