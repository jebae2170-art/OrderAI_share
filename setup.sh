#!/usr/bin/env bash
# order-ai-share — 자가검증형 셋업 스크립트
#
# 사용법:
#   ./setup.sh                  # 풀 검증 (.env 필수, Snowflake 연결 테스트 포함)
#   ./setup.sh --skip-snowflake # pre-meeting 모드 (venv/npm 설치까지만, .env 없어도 OK)
#   ./setup.sh --help           # 도움말
#
# 단계: prereqs → venv+pip → npm → .env 검증 → smoke_test
set -euo pipefail

SKIP_SNOWFLAKE=false
for arg in "$@"; do
    case $arg in
        --skip-snowflake) SKIP_SNOWFLAKE=true ;;
        --help|-h)
            echo "Usage: $0 [--skip-snowflake]"
            echo "  --skip-snowflake : Snowflake 연결 검증 skip (pre-meeting 모드)."
            echo "                     venv + 의존성 + npm 설치까지만 수행."
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Try: $0 --help"
            exit 1
            ;;
    esac
done

echo "+==============================================+"
echo "|  order-ai-share — Setup                      |"
if [ "$SKIP_SNOWFLAKE" = true ]; then
    echo "|  Mode: PRE-MEETING (Snowflake check skipped) |"
fi
echo "+==============================================+"
echo ""

# --- Step 1/5: Prereqs ---
echo "[1/5] Checking prerequisites..."
command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 not found. Install Python 3.11+"; exit 1; }
command -v node    >/dev/null 2>&1 || { echo "FAIL: node not found. Install Node.js 18+"; exit 1; }
command -v npm     >/dev/null 2>&1 || { echo "FAIL: npm not found. Install Node.js 18+"; exit 1; }

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Python: $PY_VERSION"
echo "  Node:   $(node --version)"
PY_MAJ=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MIN=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJ" -lt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 11 ]; }; then
    echo "FAIL: Python 3.11+ required (got $PY_VERSION)"
    exit 1
fi

# --- Step 2/5: venv + Python deps ---
echo "[2/5] Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  .venv 생성됨."
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "  Done. $(pip list 2>/dev/null | wc -l | tr -d ' ') packages installed."

# --- Step 3/5: Node deps ---
echo "[3/5] Installing frontend dependencies..."
cd apps/lite
npm install --silent --no-audit --no-fund
cd ../..
echo "  Done."

# --- Step 4/5: .env validation ---
echo "[4/5] Checking .env configuration..."
if [ ! -f ".env" ]; then
    if [ "$SKIP_SNOWFLAKE" = true ]; then
        echo "  .env 없음 (pre-meeting 모드에서 OK)."
    else
        echo "FAIL: .env file not found."
        echo "  -> .env.example 을 .env 로 복사 + Snowflake credentials 채우세요."
        echo "  -> 또는: ./setup.sh --skip-snowflake (Snowflake 검증 건너뛰기)"
        exit 1
    fi
else
    # 인증 모드 안내 [v3.1-SSO]
    if grep -q "^SNOWFLAKE_AUTH=externalbrowser" .env 2>/dev/null; then
        echo "  .env 발견. 인증 모드: SSO (externalbrowser)"
        echo "    - 사용자 본인 Snowflake login 사용 — 브랜드 권한 자동."
        echo "    - SNOWFLAKE_PASSWORD 값은 무시됨."
        echo "    - 헤드리스 환경(Docker/CI)에선 동작 안 함 (브라우저 팝업 필요)."
    else
        echo "  .env 발견. 인증 모드: Password (service account)"
    fi
fi

# --- Step 5/5: Smoke test ---
if [ "$SKIP_SNOWFLAKE" = true ]; then
    echo "[5/5] Skipping smoke test (pre-meeting 모드)."
    echo ""
    echo "=== SETUP COMPLETE: PRE-MEETING ==="
    echo ""
    echo "Pre-meeting prep 완료. 온보딩 미팅 후:"
    echo "  1. .env.example → .env 복사 + Snowflake credentials 채움"
    echo "  2. ./setup.sh   (풀 검증 다시 실행)"
    echo "  3. .venv/bin/python scripts/plc_engine/build_plc_standard.py  (첫 회 PLC 빌드)"
    echo "  4. .venv/bin/python scripts/run_all.py                         (파이프라인)"
    exit 0
fi

echo "[5/5] Running smoke test (Snowflake connectivity)..."
# set -e 하에서 pytest 실패 시 스크립트가 즉사해 아래 FAILED 진단이 출력되지 않던 문제 방지
set +e
python -m pytest smoke_test.py -v --tb=short
RESULT=$?
set -e

echo ""
if [ $RESULT -eq 0 ]; then
    echo "=== SETUP COMPLETE: PASS ==="
    echo ""
    echo "Next steps:"
    echo "  1. 첫 회 PLC 빌드:  .venv/bin/python scripts/plc_engine/build_plc_standard.py"
    echo "  2. 파이프라인 실행:  .venv/bin/python scripts/run_all.py"
    echo "  3. 백엔드 기동:      .venv/bin/uvicorn server.api:app --port 8000"
    echo "  4. 프론트 기동:      cd apps/lite && npm run dev"
    echo ""
    echo "  venv 활성화 후 짧게 실행:  source .venv/bin/activate"
else
    echo "=== SETUP FAILED at step 5: Smoke test ==="
    echo "  -> .env 의 Snowflake credentials 점검"
    echo "  -> 위 pytest 에러 메시지를 Claude Code 에 붙여넣으면 트러블슈팅 가능"
    exit 1
fi
