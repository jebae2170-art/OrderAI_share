"""운영(prd) baseline에서 로컬에 없는 (brand, season) 데이터를 시드한다 — 최초 1회.

share 는 과거시즌(예: 25f) 원천 데이터를 갖지 않아 run_all 로 재생성할 수 없다.
운영배포는 baseline 통째 업로드라, 과거시즌이 로컬에 없으면 _deploy_baseline_prd.py 의
커버리지 가드가 (정당하게) 차단한다. 본 스크립트는 라이브 prd baseline 을 내려받아
로컬에 없는 (brand_code, season_code) 조합의 행 전체를 season_code 컬럼이 있는
모든 테이블에 복사한다. 이미 커버리지가 같으면 no-op.

사용: PYTHONPATH=. .venv/bin/python scripts/_seed_missing_seasons_from_prd.py
전제: .env 의 S3_API_KEY, 로컬 baseline 존재(/run-pipeline 완료).

order-ai-share 전용 (원본 order_ai 에는 불필요 — 원본 baseline 은 과거시즌을 항상 보유).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import duckdb

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from server.s3_client import get_duckdb_s3_key, download_binary  # noqa: E402

LOCAL_DB = _ROOT / "data" / "production" / "order_ai.duckdb"


def main():
    if not os.getenv("S3_API_KEY"):
        sys.exit("❌ S3_API_KEY 미설정 (.env)")
    if not LOCAL_DB.exists():
        sys.exit("❌ 로컬 baseline 없음 — /run-pipeline 먼저 실행하세요")

    tmp = Path(tempfile.gettempdir()) / "prd_baseline_seed.duckdb"
    key = get_duckdb_s3_key("prd")
    print(f"[1/3] 라이브 prd baseline 다운로드 → {tmp}")
    if not asyncio.run(download_binary(key, str(tmp))):
        sys.exit(f"❌ prd 다운로드 실패 (key={key})")

    con = duckdb.connect(str(LOCAL_DB))
    try:
        con.execute(f"ATTACH '{tmp}' AS prd (READ_ONLY)")
        cat = con.execute("SELECT current_database()").fetchone()[0]
        lcov = set(con.execute("SELECT DISTINCT brand_code, season_code FROM seasons").fetchall())
        pcov = set(con.execute("SELECT DISTINCT brand_code, season_code FROM prd.seasons").fetchall())
        missing = sorted(pcov - lcov)
        print(f"[2/3] 커버리지  로컬={sorted(lcov)}  prd={sorted(pcov)}")
        if not missing:
            print("✅ 시드 불필요 — 로컬이 prd 커버리지를 모두 포함")
            return
        print(f"      시드 대상: {missing}")

        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE column_name='season_code' AND table_catalog=? GROUP BY 1 ORDER BY 1", [cat]
        ).fetchall()]
        print(f"[3/3] 복사 (테이블 {len(tables)}개):")
        for b, s in missing:
            for t in tables:
                try:
                    n = con.execute(
                        f"SELECT COUNT(*) FROM prd.{t} WHERE brand_code=? AND season_code=?", [b, s]
                    ).fetchone()[0]
                    if n == 0:
                        continue
                    con.execute(
                        f"INSERT INTO {t} SELECT * FROM prd.{t} WHERE brand_code=? AND season_code=?",
                        [b, s],
                    )
                    print(f"      {b} {s} ← {t}: {n:,}행")
                except Exception as e:  # 스키마 불일치 등 — 어떤 테이블에서 막혔는지 명시
                    sys.exit(f"❌ {t} ({b},{s}) 복사 실패: {e}")

        lcov2 = set(con.execute("SELECT DISTINCT brand_code, season_code FROM seasons").fetchall())
        if pcov - lcov2:
            sys.exit(f"❌ 시드 후에도 누락 잔존: {sorted(pcov - lcov2)}")
        print(f"✅ 시드 완료 — 로컬 커버리지: {sorted(lcov2)}")
    finally:
        con.close()
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
