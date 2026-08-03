# SETUP — order-ai-share

> 처음 받은 사람을 위한 단계별 가이드.
>
> 막히는 곳 있으면 에러 메시지 + 본 문서 + 본 디렉토리 구조를 Claude Code 에 붙여넣고 도움 요청.


---

## 0. Prerequisites

다음을 미리 설치 (운영자 본인 PC):

| 도구 | 버전 | 설치 명령 (macOS Homebrew) |
|---|---|---|
| Python | 3.11+ | `brew install python@3.11` |
| Node.js | 18+ | `brew install node@18` |
| Git | 최신 | `brew install git` |

설치 확인:

```bash
python3 --version   # 3.11.x 이상
node --version      # v18.x 이상
git --version
```

Windows 사용자는 WSL2 (Ubuntu) 사용 권장 — bash 기반 setup.sh 호환.

---

## 1. 코드 받기

AX팀이 제공한 GitHub 초대 수락 후:

```bash
git clone https://github.com/Judyjieunb/OrderAI_share.git
cd OrderAI_share
```

**zip 으로 받은 경우**: 반드시 **Finder 에서 더블클릭**으로 압축을 푸세요.
터미널 `unzip` 은 한글 파일명(`docs/STEP5_사이즈배분_운영가이드.md` 등) 인코딩이 깨져
일부 파일이 손상됩니다 (macOS 터미널 대안: `ditto -x -k order-ai-share.zip <대상폴더>`).

---

## 2. Pre-meeting 셋업 (.env 없이 가능)

온보딩 미팅 **전에** 환경을 미리 만들어둘 수 있습니다. Snowflake credentials 없이도:

```bash
./setup.sh --skip-snowflake
```

이 명령이 하는 일:

| 단계 | 내용 |
|---|---|
| 1/5 | Python 3.11+ / Node 18+ 존재 확인 |
| 2/5 | `.venv` Python 가상환경 생성 + `pip install -r requirements.txt` |
| 3/5 | `apps/lite/` 에서 `npm install` |
| 4/5 | `.env` 검증 (skip 모드라 없어도 OK) |
| 5/5 | Smoke test (skip 모드라 건너뜀) |

성공 시 `=== SETUP COMPLETE: PRE-MEETING ===` 메시지가 마지막 줄에 표시됩니다.

---

## 3. 온보딩 미팅에서 받을 것

**옵션 A: SSO 사용 (권장)**

| 항목 | 형식 | 비고 |
|---|---|---|
| 본인 Snowflake account 의 브랜드 권한 확인 | IT 가 본인 role 에 브랜드 grant 부여 | password 받을 필요 X |
| Snowflake account / warehouse / database / schema | 1Password share 또는 회의 중 화면 공유 | password 외 공통 변수만 |
| GitHub repo 권한 | collaborator invite | private repo |
| (cold-start 브랜드 한정) seed PLC | `data/plc/{brand}_{type}_plc_forecast_standard.csv` (예: `mlb_fw_*`) | AX팀이 수동 생성 → 1Password / GPG |

**옵션 B: Service Account (헤드리스 환경용)**

| 항목 | 형식 | 비고 |
|---|---|---|
| Snowflake credentials (service account) | **1Password share** | account / user / **password** / warehouse / role / database / schema |
| GitHub repo 권한 | collaborator invite | private repo |
| (cold-start 브랜드 한정) seed PLC | 동상 | 동상 |

> ⚠️ 옵션 B 의 password 는 plain email / Slack DM / USB unencrypted 로 절대 받지 마세요. 보안 정책 위반.

**공통 — 모드 무관 필수 전달물**: 전체 목록·형식·없을 때 증상은 **`HANDOVER.md` §1 이 정본**입니다
(restored.csv · dcs-ai-cli+키 · S3_API_KEY · EC2 ssh 키 — zip/git 에 없어 별도 전달 필수).

---

## 4. .env 설정

```bash
cp .env.example .env
```

`.env` 편집. **두 모드 중 본인 선택**:

> **모드 선택**: 매주 반복하는 `/weekly-refresh` 루틴이 주 업무라면 **옵션 B (password) 권장** — SSO 는 매 실행 브라우저 팝업이 떠서 반복 루틴에 번거롭습니다. SSO 는 credential 을 .env 에 보관하지 않는 장점이 있어 일회성 조회·검증에 적합합니다.

### 옵션 A: SSO (externalbrowser) — 일회성 조회용

```
SNOWFLAKE_ACCOUNT=your_account.ap-northeast-2.aws
SNOWFLAKE_USER=your.email@fnf.co.kr            # 본인 SSO 이메일
SNOWFLAKE_AUTH=externalbrowser                  # ← 이 줄 추가
# SNOWFLAKE_PASSWORD — SSO 모드에선 무시됨, 비워두거나 줄 삭제 OK
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=FNF
SNOWFLAKE_SCHEMA=PRCS
SNOWFLAKE_ROLE=                                 # 비우면 본인 default role 사용

BRAND=DUVETICA
SINGLE_BRAND_MODE=true
DUCKDB_PATH=data/production/order_ai.duckdb
USER_STORAGE_PATH=data/user-storage
```

setup.sh 또는 파이프라인 실행 시 브라우저 팝업이 떠서 본인 SSO login. 한 번 로그인하면 token 캐시되어 일정 시간 재로그인 불필요.

### 옵션 B: Service Account + Password — 주간 갱신 루틴 권장

```
SNOWFLAKE_ACCOUNT=your_account.ap-northeast-2.aws   # 1Password 값
SNOWFLAKE_USER=your_service_account               # 1Password 값
SNOWFLAKE_PASSWORD=...                          # 1Password 값 (절대 git 커밋 X)
# SNOWFLAKE_AUTH 줄은 추가 X (또는 비활성 주석)
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_ROLE=ORDERAI_READER
SNOWFLAKE_DATABASE=FNF
SNOWFLAKE_SCHEMA=PRCS

BRAND=DUVETICA
SINGLE_BRAND_MODE=true
DUCKDB_PATH=data/production/order_ai.duckdb
USER_STORAGE_PATH=data/user-storage
```

`.env` 는 `.gitignore` 에 등록돼있어 절대 커밋되지 않습니다.

---

## 4.5. dcs-ai-cli 설치 (KG 교차검증 게이트 — /weekly-refresh 필수)

`/weekly-refresh` 의 Stage 4 (지식그래프 교차검증 게이트)는 `dcs-ai-cli` 로 DCS AI KG API 를 호출합니다.
**F&F 내부 배포 바이너리**라 공개 저장소에 없습니다 — 온보딩 미팅에서 설치본과 API key 를 받으세요 (프로세스팀/AX팀).

1. **바이너리 배치** (macOS arm64):
   ```bash
   mkdir -p ~/.local/bin
   cp <전달받은 dcs-ai-cli> ~/.local/bin/dcs-ai-cli
   chmod +x ~/.local/bin/dcs-ai-cli
   # PATH 에 없으면 (zsh):
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
   ```
2. **API key 설정** — 설정 파일은 `~/Library/Application Support/dcs-ai-cli/config.toml` (host / port / api_key / user_email):
   ```bash
   dcs-ai-cli config set   # 대화형 설정 (또는 config.toml 직접 편집)
   dcs-ai-cli config show  # 확인
   ```
3. **동작 검증**:
   ```bash
   dcs-ai-cli --help   # 사용법 출력되면 설치 OK
   ```
   실제 API 왕복은 `/weekly-refresh` 첫 실행의 Stage 4 통과로 최종 확인됩니다.

> 게이트 스크립트(`scripts/_verify_kg_crosscheck.py`)는 `~/.local/bin` 등 표준 경로를 자동 탐색하므로,
> 위 위치에 설치하면 PATH 미설정 상태로도 동작합니다.

---

## 5. brand_config.json 확인

`public/brand_config.json` 의 `brand` 필드가 `.env` 의 `BRAND` 와 **일치**해야 합니다 (대소문자는 무관, setup.sh 가 자동 검증).

```json
{
  "brand": "Duvetica",        ← .env 의 BRAND=DUVETICA 와 매칭 OK
  "baseSeason": "25F",         ← 분석 기준 시즌
  "targetSeason": "26F",       ← 발주 계획 시즌
  ...
}
```

불일치면 `./setup.sh` 가 `RuntimeError` + 명확한 메시지로 차단합니다.

---

## 6. 풀 셋업 검증

```bash
./setup.sh
```

성공 시:

```
=== SETUP COMPLETE: PASS ===

Next steps:
  1. PLC 준비:        Claude Code 에서 /prepare-pipeline 스킬 실행 (자동 점검 + 필요 시 빌드)
                      또는 수동:  .venv/bin/python scripts/plc_engine/build_plc_standard.py
                      ※ 동봉된 4 PLC csv 가 본인 brand+type 에 매칭되면 스킬이 빠르게 종료.
  2. 파이프라인 실행: .venv/bin/python scripts/run_all.py
  3. 백엔드 기동:     .venv/bin/uvicorn server.api:app --port 8000
  4. 프론트 기동:     cd apps/lite && npm run dev
```

실패 시 위 pytest 출력을 Claude Code 에 붙여넣어 트러블슈팅.

---

## 7. 첫 회 PLC 빌드

> **먼저 확인**: 본 fork 와 함께 4개 표준 PLC csv (`data/plc/{brand}_{type}_plc_forecast_standard.csv`) 가 동봉됩니다. 본인 brand+type 가 매칭되면 빌드 단계 **불필요** — 바로 §8 (파이프라인 실행) 로.
>
> Claude Code 운영자는 `/prepare-pipeline` 스킬을 호출하면 자동 점검 + (필요 시) 빌드까지 처리합니다.

수동 빌드 (신규 brand/시즌 운영, 또는 재생성):

```bash
.venv/bin/python scripts/plc_engine/build_plc_standard.py
```

동작:

- CSV 우선 — `data/{BRAND}_GT_*.csv` 자동 탐색
- CSV 부재 시 Snowflake fallback (`queries/plc_standard.sql`)
- **>=2 시즌 데이터 검증** (sufficiency gate) — 부족 시 AX팀 seed PLC 요청 안내
- 출력: `data/plc/{brand}_{type}_plc_forecast_standard.csv` (brand/type 자동 도출)
- 첫 빌드는 drift report 없음. 재생성 시 평균 변화율 (<5/5-30/>30%) 분기 메시지.

자세히 → [`docs/PLC_GUIDE.md`](./docs/PLC_GUIDE.md).

---

## 8. 파이프라인 실행

```bash
.venv/bin/python scripts/run_all.py
```

6 step 이 순차 실행됩니다 (대략 3-10분):

```
[1/6] main.py             — 시즌 마감 분석 (season_closing_data.json)
[2/6] weekly_analysis.py  — 주차별 시계열 (dashboard_data.json, past_styles_data.json)
[3/6] ai_sales_loss_v3.py — PLC 기반 기회비용
[4/6] step4_integration.py — 유사 스타일 매핑 (style_mapping_data.json)
[5/6] generate_size_data.py — 사이즈 데이터 (size_assortment_data.json)
[6/6] dump_to_duckdb.py   — baseline DuckDB 적재 (order_ai.duckdb)
```

산출물:
- `public/*.json` (각 step baseline JSON — 백엔드 + 프론트가 직접 read)
- `data/production/order_ai.duckdb` (baseline DuckDB — `/api/lite/dashboard` 등 API 의 데이터 소스)
- `output/` (분석 Excel/리포트)

> **주의**: 운영자 fresh 환경 첫 실행 시 `order_recommendation_data.json` (사용자 Step 3 UI 동작 후 생성되는 사물함 데이터) 미존재 → dump_to_duckdb 가 빈 baseline 으로 OK 적재 (INFO 메시지). 정상 동작.

---

## 9. 백엔드 + 프론트엔드 기동

별도 터미널 2개에서:

```bash
# 터미널 1 — 백엔드
.venv/bin/uvicorn server.api:app --port 8000 --reload

# 터미널 2 — 프론트엔드
cd apps/lite && npm run dev
```

브라우저 → http://localhost:5173 (vite 출력 포트 확인)

UI 가 정상이면 5 step 모두 데이터를 표시합니다.

---

## 10. 다음 시즌 데이터 도착 시

새 시즌 raw 데이터가 Snowflake 에 들어왔다면:

```bash
# 1) PLC 재생성 (선택 — 누적 데이터로 더 정확)
.venv/bin/python scripts/plc_engine/build_plc_standard.py
# → drift report 확인 (PLC_GUIDE.md 참고)

# 2) 파이프라인 재실행 (6 step, baseline DuckDB 까지 자동 갱신)
.venv/bin/python scripts/run_all.py

# 3) 서버 재기동 (필요 시)
```

브라우저 새로고침하면 새 데이터 반영.

---

## Troubleshooting FAQ

### setup.sh 가 step 1 에서 fail

```
FAIL: python3 not found. Install Python 3.11+
```
→ Python 3.11+ 미설치. `brew install python@3.11` (macOS) 또는 Windows installer.

### setup.sh 가 step 4 에서 fail (SSO 모드인데도)

→ 본 fork 는 SSO 허용. setup.sh 가 SSO 모드 발견 시 INFO 메시지만 출력하고 통과해야 함. fail 한다면 step 5 의 smoke_test 단계에서 fail 한 것일 가능성 — 아래 항목 참고.

### SSO 모드에서 smoke_test 가 브라우저 팝업 후 timeout

→ Snowflake SSO IdP (회사 SSO) 로그인 화면이 떴는데 시간 내 미완료. 다시 실행해서 빠르게 login.

→ 헤드리스 환경(SSH 원격 서버 등)에서 실행한 경우 SSO 모드 불가. 옵션 B (service account) 로 전환.

### setup.sh 가 step 5 (smoke test) 에서 fail

```
FAILED smoke_test.py::TestSnowflakeConnectivity::test_select_one
```
→ Snowflake credentials 오류. 1Password 값과 `.env` 다시 비교.

```
FAILED smoke_test.py::TestSnowflakeConnectivity::test_brand_data_access
No data found for brand 'XXX'
```
→ Snowflake 권한 문제. AX팀에 service account 의 `FNF.PRCS.DB_SCS_W` SELECT 권한 확인 요청.

### `build_plc_standard.py` 가 데이터 부족 에러

```
RuntimeError: 최소 2 시즌의 weekly_raw 데이터 필요. AX팀에 seed PLC 요청하세요.
```
→ Cold-start 브랜드. AX팀에 manual seed PLC 1회 요청. 받은 csv 를 `data/plc/{brand}_{type}_plc_forecast_standard.csv` (본인 환경의 brand/type) 에 배치. 정확한 경로는 `python -c "import sys; sys.path.insert(0,'scripts'); from config_loader import get_plc_forecast_path; print(get_plc_forecast_path())"` 로 확인.

### 프론트엔드 화면이 비어있음

- 브라우저 콘솔 (F12) 확인
- 백엔드 (`uvicorn`) 가 8000 포트에서 정상 기동 중인지 확인 (`curl http://localhost:8000/api/health` 응답 `{"status":"ok"}` 여야 함)
- `data/production/order_ai.duckdb` 파일 존재 여부 (`run_all.py` 가 만듦)

### Step 3 화면이 "GO list 업로드" 안내만 표시

→ **정상**. GO list 를 아직 업로드 안 한 상태. Template 다운로드 → 채워서 Upload.

### 모든 step 이 "데이터가 없습니다"

→ `run_all.py` 미실행. 먼저 파이프라인 돌리세요.

### Step 5 dump 에서 컬럼 mismatch 에러

→ `size_assortment` 테이블이 구 스키마(13컬럼)로 잔존. 보통 `dump_to_duckdb.py` 의 자동 마이그레이션 (`_ensure_size_assortment_schema`) 이 처리하지만 외부 도구로 만든 DB 등에서는 수동 reset 필요:

```bash
rm data/production/order_ai.duckdb
.venv/bin/python scripts/run_all.py
```

데이터는 다음 dump 가 재생성하므로 손실 없음. 자세한 배경은 [`docs/STEP5_사이즈배분_운영가이드.md`](./docs/STEP5_사이즈배분_운영가이드.md) §11.1 참고.

### 동작이 이상한데 에러는 없음 (조용한 깨짐)

가장 위험한 케이스. 의심할 것:

- 본인 사물함 (`data/user-storage/`) 에 옛 시즌 결과 잔존 → UI 우상단 Reset 버튼
- 브라우저 캐시 → 시크릿 창에서 다시 시도

---

## 사고 대응

### Credential 유출 의심 (옵션 B 사용 시)

1. 즉시 `.env` 의 `SNOWFLAKE_PASSWORD` 무효화 (Snowflake 콘솔)
2. 새 service account 발급 요청 (1Password share 로 받음)

### 코드 손상 (git reset 으로 되돌리기)

```bash
git status                # 현재 변경사항 확인
git stash                 # 작업 중인 변경 안전 보관
git reset --hard HEAD~1   # 직전 커밋으로 돌리기 (또는 특정 커밋)
git stash pop             # 안전 보관한 변경 복원
```

### 데이터 손상 (DuckDB 재생성)

```bash
rm -f data/production/order_ai.duckdb
.venv/bin/python scripts/run_all.py   # 6 step 자동 실행 → public/*.json + DuckDB 재생성
```

본인 사물함 reset (`data/user-storage/` 통째 또는 특정 시즌):

```bash
rm -rf data/user-storage/{brand}/{season}/   # 본인 책임
```

또는 UI 우상단 Reset 버튼 사용.

---

## 다음 읽을 것

- 코드 수정 / 커스터마이즈 → [`CLAUDE.md`](./CLAUDE.md)
- PLC 관련 → [`docs/PLC_GUIDE.md`](./docs/PLC_GUIDE.md)
