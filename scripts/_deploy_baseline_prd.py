"""baseline DuckDB를 운영(prd) S3에 안전 배포한다.

절차(가드):
  1. 라이브 prd baseline 다운로드 → rollback/prd_baseline_<TS>.duckdb.bak (롤백용 백업)
  2. 커버리지 검증: 로컬이 prd의 모든 (brand, season)을 포함하는가 (누락 시 중단)
  3. 25f(과거시즌) 시그니처 불변 검증: season_code='25f' 행수가 로컬==prd 인가 (다르면 중단)
  4. KG 교차검증 게이트: 로컬 원천 KPI(판매량·판매택가)를 지식그래프와 대사 (허용오차 초과 시 중단)
     — _verify_kg_crosscheck.run_crosscheck(). KG API 장애 등 비상시 --skip-kg-check로 우회.
  5. --confirm 있을 때만 로컬 baseline을 prd 키로 업로드

검증만:   python _deploy_baseline_prd.py
실제 배포: python _deploy_baseline_prd.py --confirm

기본 prd 대상. 로컬 baseline = data/production/order_ai.duckdb.

원본(order_ai)의 scripts/_deploy_baseline_prd.py 정본 미러링 — 수정 필요 시 오너에게 원천 반영 요청.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

import duckdb

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from server.s3_client import get_duckdb_s3_key, download_binary, upload_binary  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_kg_crosscheck import run_crosscheck  # noqa: E402

LOCAL_DB = _ROOT / "data" / "production" / "order_ai.duckdb"
PAST_SEASON = "25f"  # 불변이어야 하는 과거시즌


def _tables_with_season(con):
    rows = con.execute(
        "SELECT table_name FROM information_schema.columns "
        "WHERE column_name='season_code' GROUP BY table_name ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]


def _coverage(con):
    return set(
        (b, s) for b, s in con.execute(
            "SELECT DISTINCT brand_code, season_code FROM seasons"
        ).fetchall()
    )


def _past_signature(con):
    """season_code=25f 의 테이블별·브랜드별 행수 = 시그니처."""
    sig = {}
    for t in _tables_with_season(con):
        for b, n in con.execute(
            f"SELECT brand_code, COUNT(*) FROM {t} "
            f"WHERE lower(season_code)=? GROUP BY brand_code", [PAST_SEASON]
        ).fetchall():
            sig[(t, b)] = n
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="prd")
    ap.add_argument("--confirm", action="store_true", help="실제 업로드 수행")
    ap.add_argument("--skip-kg-check", action="store_true",
                    help="KG 교차검증 건너뛰기 (KG API 장애 등 비상시에만)")
    args = ap.parse_args()

    if not os.getenv("S3_API_KEY"):
        sys.exit("❌ S3_API_KEY 미설정")
    if not LOCAL_DB.exists():
        sys.exit(f"❌ 로컬 baseline 없음: {LOCAL_DB}")

    key = get_duckdb_s3_key(args.env)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = _ROOT / "rollback" / f"prd_baseline_{ts}.duckdb.bak"

    print(f"[1/4] 라이브 {args.env} baseline 다운로드 → {bak.name}")
    ok = asyncio.run(download_binary(key, str(bak)))
    if not ok:
        sys.exit(f"❌ 라이브 {args.env} 다운로드 실패 (key={key}). 최초 배포면 별도 처리 필요.")

    lcon = duckdb.connect(str(LOCAL_DB), read_only=True)
    pcon = duckdb.connect(str(bak), read_only=True)
    try:
        lcov, pcov = _coverage(lcon), _coverage(pcon)
        print(f"[2/4] 커버리지  로컬={sorted(lcov)}  prd={sorted(pcov)}")
        missing = pcov - lcov
        if missing:
            sys.exit(f"❌ 로컬이 prd의 일부 (brand,season)을 누락 → 통째 업로드 시 손실: {sorted(missing)}")

        lsig, psig = _past_signature(lcon), _past_signature(pcon)
        diff = {k: (lsig.get(k), psig.get(k)) for k in set(lsig) | set(psig) if lsig.get(k) != psig.get(k)}
        print(f"[3/4] {PAST_SEASON} 시그니처 비교: {'동일 ✅' if not diff else f'불일치 ⚠️ {diff}'}")
        if diff:
            sys.exit(f"❌ {PAST_SEASON} 데이터가 로컬과 prd에서 다름 → 통째 업로드 위험. 중단(additive 배포 필요).")

        # 무엇이 갱신되는지 표시 (26s pipeline_version 비교)
        print("[3/4] 26s 갱신 내역:")
        for b, s, lv in lcon.execute(
            "SELECT brand_code, season_code, pipeline_version FROM seasons WHERE lower(season_code) LIKE '26%' ORDER BY 1"
        ).fetchall():
            pv = pcon.execute(
                "SELECT pipeline_version FROM seasons WHERE brand_code=? AND season_code=?", [b, s]
            ).fetchone()
            print(f"        {b} {s}: prd {pv[0] if pv else '없음'} → 로컬 {lv}")
    finally:
        lcon.close(); pcon.close()

    # [KG 게이트] 업로드 직전, 로컬 원천 KPI를 지식그래프와 교차검증 (매 배포 필수)
    if args.skip_kg_check:
        print("[KG] ⚠️ 교차검증 건너뜀 (--skip-kg-check)")
    else:
        print("[KG] 지식그래프 교차검증 중...")
        kg_ok, _ = run_crosscheck(verbose=True)
        if not kg_ok:
            sys.exit("❌ KG 교차검증 실패 → 배포 중단. (원인 확인 후 재시도, 비상시 --skip-kg-check)")

    if not args.confirm:
        print(f"\n[4/4] 검증 통과. 실제 배포하려면 --confirm 추가 실행.\n      백업: {bak}")
        return

    print(f"[4/4] 업로드: {LOCAL_DB} → s3://{key}")
    ok = asyncio.run(upload_binary(key, str(LOCAL_DB)))
    if not ok:
        sys.exit("❌ 업로드 실패")
    print(f"✅ prd 배포 완료. 롤백용 백업: {bak}")


if __name__ == "__main__":
    main()
