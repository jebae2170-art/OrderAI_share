"""배포 후 운영 서빙 검증 — EC2 lite 컨테이너 health + 서빙 baseline 버전 대사.

로컬 baseline(seasons.pipeline_version, 당해시즌)과 EC2 lite-app-1 컨테이너가
실제 서빙 중인 DuckDB의 pipeline_version이 일치하는지 확인한다.
S3 업로드만 되고 컨테이너 재시작이 누락된 "미반영 배포"를 잡는다.

실행: PYTHONPATH=. .venv/bin/python3 scripts/_verify_prd_serving.py
종료코드: 0=일치, 1=불일치/health 실패

원본(order_ai)의 scripts/_verify_prd_serving.py 정본 미러링.
"""
import ast
import subprocess
import sys
from pathlib import Path

import duckdb

_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DB = _ROOT / "data" / "production" / "order_ai.duckdb"
EC2_HOST = "ec2-business-user@10.81.3.230"
REMOTE_DB = "/data/duckdb/order_ai.duckdb"

_SEASONS_SQL = ("SELECT brand_code, season_code, pipeline_version FROM seasons "
                "WHERE base_season IS NOT NULL ORDER BY 1, 2")


def _local_versions():
    con = duckdb.connect(str(LOCAL_DB), read_only=True)
    try:
        return {(b, s): v for b, s, v in con.execute(_SEASONS_SQL).fetchall()}
    finally:
        con.close()


def _remote_health_and_versions():
    remote_py = (f"import duckdb; con = duckdb.connect('{REMOTE_DB}', read_only=True); "
                 f"print(con.execute(\\\"{_SEASONS_SQL}\\\").fetchall())")
    cmd = ("curl -s -o /dev/null -w '%{http_code}' http://localhost:8520/health; echo; "
           f"docker exec lite-app-1 python -c \"{remote_py}\"")
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", EC2_HOST, cmd],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"EC2 원격 조회 실패: {proc.stderr.strip()[:300]}")
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    health = lines[0].strip()
    rows = ast.literal_eval(lines[1].strip())
    return health, {(b, s): v for b, s, v in rows}


def main():
    if not LOCAL_DB.exists():
        sys.exit(f"❌ 로컬 baseline 없음: {LOCAL_DB}")
    local = _local_versions()
    health, remote = _remote_health_and_versions()
    ok = True
    if health != "200":
        ok = False
        print(f"❌ health {health} (200 아님)")
    else:
        print("✅ health 200")
    for key in sorted(local):
        lv, rv = local[key], remote.get(key)
        mark = "✅" if lv == rv else "❌"
        if lv != rv:
            ok = False
        print(f"  {mark} {key[0]} {key[1]}: 로컬 {lv} vs 서빙 {rv}")
    print("[서빙 검증] " + ("통과 ✅" if ok else "실패 ❌ — 운영 미반영"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
