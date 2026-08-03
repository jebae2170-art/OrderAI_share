#!/usr/bin/env bash
#
# _auto_deploy_prd.sh — 주간갱신 후 무인 운영배포 체인.
#
#   1) _deploy_baseline_prd.py --confirm  (내부 가드: 롤백백업·커버리지·25f불변·KG 교차검증[누계+최신주차])
#   2) EC2 lite 컨테이너 재시작 (S3 새 baseline 재pull)
#   3) _verify_prd_serving.py  (health 200 + 서빙 pipeline_version == 로컬)
#
# 어느 단계든 실패 시 즉시 exit≠0 (호출측이 알림 판단). 로그: logs/deploy/<TS>.log
# ⚠️ EC2 compose 경로는 ~/20_OrderAI/apps/lite (문서의 apps/lite는 이 하위 경로).
# 원본(order_ai)의 scripts/_auto_deploy_prd.sh 정본 미러링 — /weekly-refresh Stage 6이 호출.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="$ROOT/.venv/bin/python3"
# EC2 접속 대상은 .env::EC2_SSH_TARGET 에서 주입 (공개 저장소 — 내부 식별자 하드코딩 금지)
EC2="$(grep -E '^EC2_SSH_TARGET=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')"
[ -n "$EC2" ] || { echo "❌ EC2_SSH_TARGET 미설정 (.env) — 운영배포 불가 (HANDOVER.md 전달물 참조)"; exit 1; }
EC2_COMPOSE_DIR="20_OrderAI/apps/lite"

LOG_DIR="$ROOT/logs/deploy"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d_%H%M%S).log"
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "===== 자동 운영배포 시작 (root=$ROOT) ====="

log "STEP 1/3: 배포 게이트 + S3 업로드 (_deploy_baseline_prd.py --confirm)"
if ! (cd "$ROOT" && PYTHONPATH="$ROOT" PYTHONIOENCODING=utf-8 "$PY" -u scripts/_deploy_baseline_prd.py --confirm) >>"$LOG" 2>&1; then
  log "❌ 배포 게이트/업로드 실패 — 중단 (운영은 기존 baseline 유지)"
  exit 1
fi

log "STEP 2/3: EC2 컨테이너 재시작 (docker compose restart @ $EC2_COMPOSE_DIR)"
if ! ssh -o BatchMode=yes -o ConnectTimeout=15 "$EC2" \
    "cd $EC2_COMPOSE_DIR && docker compose restart" >>"$LOG" 2>&1; then
  log "❌ EC2 재시작 실패 — S3는 갱신됐으나 운영 미반영. 수동 재시작 필요"
  exit 1
fi

log "STEP 3/3: 서빙 검증 (health + pipeline_version 대사, 기동 대기 30s)"
sleep 30
if ! (cd "$ROOT" && PYTHONPATH="$ROOT" PYTHONIOENCODING=utf-8 "$PY" -u scripts/_verify_prd_serving.py) >>"$LOG" 2>&1; then
  log "❌ 서빙 검증 실패 (health 또는 버전 불일치) — 로그 확인: $LOG"
  exit 1
fi

log "===== ✅ 자동 운영배포 완료 ====="
log "로그: $LOG"
