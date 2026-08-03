#!/usr/bin/env bash
#
# package_handover.sh — 운영담당자(주간판매갱신 전담)용 "폴더째" 전달 패키지 생성.
#
# 2트랙 전달 체계 (HANDOVER.md §1.5):
#   Track A (본 스크립트) : 운영 스냅샷 — 코드+데이터(baseline·시즌캐시·restored) 포함.
#                           수령자는 setup.sh + 본인 .env + dcs-ai-cli + ssh키만 하면
#                           초기 파이프라인 없이 첫 월요일부터 /weekly-refresh 가능.
#   Track B (git archive) : 코드배포본 order-ai-share.zip — 데이터 없음, 별도 유지.
#
# 자동 제외(보안·용량): .env(시크릿) .venv .git rollback백업 logs 임시로그 캐시류
# 산출: ../order-ai-share-handover_<YYYYMMDD>.zip + 자가검증(시크릿 미포함·핵심 데이터 포함)

set -euo pipefail
cd "$(dirname "$0")"

OUT="../order-ai-share-handover_$(date +%Y%m%d).zip"

echo "[1/3] 사전 점검 — 패키지 성립 요건"
test -f data/production/order_ai.duckdb || { echo "❌ baseline 없음 (data/production/order_ai.duckdb) — 이 패키지의 핵심. /run-pipeline 먼저."; exit 1; }
ls data/*/*/restored.csv >/dev/null 2>&1 || { echo "❌ restored.csv 없음 (data/{brand}/{season}/) — 없으면 수령자 STEP 3 실패."; exit 1; }
echo "  baseline:OK  restored:OK"

echo "[2/3] 압축 (제외: .env·.venv·.git·rollback백업·logs·임시로그)"
rm -f "$OUT"
zip -rq "$OUT" . \
  -x ".env" -x ".env.local" -x ".env.production" \
  -x ".venv/*" -x ".git/*" -x ".omc/*" -x ".pytest_cache/*" \
  -x "rollback/*.bak" -x "logs/*" -x "*.log" \
  -x "output/*.xlsx" -x "output/*.json" \
  -x "data/.weekly_backup/*" \
  -x "*__pycache__*" -x "*.pyc" -x "*.DS_Store" \
  -x "*node_modules*" -x "apps/lite/dist/*"

echo "[3/3] 자가검증"
if unzip -l "$OUT" | awk '{print $4}' | grep -qxE "\.env(\.local|\.production)?"; then
  echo "❌ .env 가 패키지에 포함됨 — 삭제 후 중단"; rm -f "$OUT"; exit 1
fi
unzip -l "$OUT" | grep -q "data/production/order_ai.duckdb" || { echo "❌ baseline 누락"; rm -f "$OUT"; exit 1; }
unzip -l "$OUT" | grep -q "restored.csv" || { echo "❌ restored.csv 누락"; rm -f "$OUT"; exit 1; }
unzip -l "$OUT" | grep -q ".claude/skills/weekly-refresh/SKILL.md" || { echo "❌ 스킬 누락"; rm -f "$OUT"; exit 1; }

echo ""
echo "✅ 생성: $OUT ($(du -h "$OUT" | cut -f1))"
echo "   수령자 안내: Finder 더블클릭으로 해제 → HANDOVER.md §1.5 (setup.sh·본인 .env·dcs-ai-cli·ssh키)"
echo "   ⚠️ 전달 후: 이 머신에서의 배포 중단 확인 (활성 배포 주체는 전체에서 한 곳만)"
