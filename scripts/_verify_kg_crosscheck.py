"""지식그래프(KG) 교차검증 게이트 — order-ai-share.

로컬 baseline 원천(weekly_raw.csv, DB_SCS_W)과 DCS 지식그래프(get_product_sales_day,
DW_SALE)의 KPI가 허용오차 내에서 일치하는지 확인한다. 소스가 다른 두 경로가 동일 값을
내는지 대사(對査)하여 데이터 적재 오류·시즌/브랜드 스코프 실수를 반영 전에 잡는다.

검증 KPI (원단위 대사 가능한 판매 지표) — 시즌누계 + 최신 완결주차 각각 대사:
  - 판매량   : 로컬 SALE_QTY_CNS(소비자 정상+반품)   ↔ KG SALE_QTY
  - 판매택가 : 로컬 SUM(SALE_QTY_CNS × TAG_PRICE)     ↔ KG SALE_TAG_AMT
  ※ 발주/입고는 KG에 원단위 대사 가능한 전용 소스가 없어(재고=스냅샷) 제외.
    발주/입고 정합은 파이프라인 내부(부분합=총합)에서 별도 검증.

대상: 로컬 baseline DuckDB의 seasons 중 base_season IS NOT NULL 행(= 당해 시즌).
KG 조회: dcs-ai-cli (config.toml의 api_key 사용, 프로젝트 .env 불필요).

단독 실행:  .venv/bin/python scripts/_verify_kg_crosscheck.py
            (옵션: --tolerance 0.01  기본 1.0%)
게이트 연동: /weekly-refresh 스킬이 run_all 완료 후 호출.
            실패(exit 1) 시 스킬이 직전 baseline으로 롤백한다.

원본(order_ai)의 scripts/_verify_kg_crosscheck.py 정본 미러링.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DB = _ROOT / "data" / "production" / "order_ai.duckdb"

# brand_code(소문자, DuckDB) → KG BRD_CD 문자코드. 새 브랜드 추가 시 여기만 수정.
BRAND_TO_BRD_CD = {"mlb": "M", "discovery": "X", "duvetica": "V", "mlb_kids": "I"}

_SALES_ENDPOINT = "/api/v1/hq/sales_analysis/product/product_sales_day"
DEFAULT_TOLERANCE = 0.01  # 1.0% (관측 노이즈 ~0.13%보다 충분히 크고, 실오류는 잡음)


def _resolve_cli():
    """dcs-ai-cli 절대경로 탐색. 무인 실행은 로그인 셸 PATH를
    안 물려받아 ~/.local/bin이 PATH에 없을 수 있으므로 후보 경로를 직접 확인."""
    found = shutil.which("dcs-ai-cli")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "dcs-ai-cli",
                 Path("/usr/local/bin/dcs-ai-cli"),
                 Path("/opt/homebrew/bin/dcs-ai-cli")):
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    raise RuntimeError(
        "dcs-ai-cli 실행파일을 찾을 수 없음. PATH 또는 ~/.local/bin 확인 "
        "(무인 실행 시 PATH에 ~/.local/bin 노출 필요)."
    )


def _targets():
    """검증 대상 (brand_code, season_code, sesn) 목록 — 당해 시즌(base_season 존재)."""
    con = duckdb.connect(str(LOCAL_DB), read_only=True)
    try:
        rows = con.execute(
            "SELECT brand_code, season_code, base_season FROM seasons "
            "WHERE base_season IS NOT NULL ORDER BY 1, 2"
        ).fetchall()
    finally:
        con.close()
    return [(b, s, base) for b, s, base in rows]


def _local_totals(brand_code, season_code):
    """weekly_raw.csv 당해 판매량·판매택가 합계 + END_DT 범위."""
    path = _ROOT / "data" / brand_code / season_code / "weekly_raw.csv"
    if not path.exists():
        raise FileNotFoundError(f"로컬 weekly_raw 없음: {path}")
    qty = tag_amt = 0.0
    dmin, dmax = "9999-99-99", ""
    with open(path, encoding="utf-8-sig") as f:  # BOM 제거 필수
        for r in csv.DictReader(f):
            if (r.get("PERIOD") or "").strip() != "당해":
                continue
            q = float(r.get("SALE_QTY_CNS") or 0)
            price = float(r.get("TAG_PRICE") or 0)
            qty += q
            tag_amt += q * price
            d = (r.get("END_DT") or "").strip()
            if d:
                dmin, dmax = min(dmin, d), max(dmax, d)
    return {"qty": qty, "tag_amt": tag_amt, "dmin": dmin, "dmax": dmax}


def _local_week_totals(brand_code, season_code):
    """당해 최신 완결 주차(END_DT < 실행일)의 판매량·판매택가 합계.

    진행 중 부분주(예: 월요일 실행 시 그 주)는 표본이 작아 제외하고,
    직전 일요일로 끝난 완결 주만 대사한다. 완결 주가 없으면 None.
    """
    path = _ROOT / "data" / brand_code / season_code / "weekly_raw.csv"
    if not path.exists():
        raise FileNotFoundError(f"로컬 weekly_raw 없음: {path}")
    today = datetime.now().strftime("%Y-%m-%d")
    end_dt, qty, tag_amt = "", 0.0, 0.0
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("PERIOD") or "").strip() != "당해":
                continue
            d = (r.get("END_DT") or "").strip()
            if d and d < today:
                rows.append((d, r))
                end_dt = max(end_dt, d)
    if not end_dt:
        return None
    for d, r in rows:
        if d == end_dt:
            q = float(r.get("SALE_QTY_CNS") or 0)
            qty += q
            tag_amt += q * float(r.get("TAG_PRICE") or 0)
    start_dt = (datetime.strptime(end_dt, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    return {"qty": qty, "tag_amt": tag_amt, "start_dt": start_dt, "end_dt": end_dt}


def _kg_totals(brd_cd, sesn, start_dt, end_dt):
    """dcs-ai-cli로 KG 판매량·판매택가 브랜드 총계 조회."""
    body = {
        "selectors_product": [{"system_field_name": "BRD_CD"}],
        "metrics": [{"system_field_name": "SALE_QTY"}, {"system_field_name": "SALE_TAG_AMT"}],
        "filters_product": [
            {"system_code": brd_cd, "system_field_name": "BRD_CD"},
            {"system_code": sesn, "system_field_name": "SESN"},
        ],
        "order_by_clauses": [{"system_field_name": "SALE_QTY", "direction": "DESC"}],
        "periods": {"start_dt": start_dt, "end_dt": end_dt, "is_time_series": False},
        "meta_info": {"requested_record_rows": 20000, "data_type": "list"},
    }
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out_path = tf.name
    proc = subprocess.run(
        [_resolve_cli(), "fetch", "--endpoint", _SALES_ENDPOINT, "--method", "POST",
         "--body", json.dumps(body), "--output", out_path],
        capture_output=True, text=True, timeout=120,
    )
    # --output 미지원 CLI 버전 대비: stdout의 "Saved to <path>" 파싱 폴백
    raw = None
    if Path(out_path).exists() and Path(out_path).stat().st_size > 0:
        raw = Path(out_path).read_text()
    else:
        m = re.search(r"Saved to (\S+\.json)", proc.stdout)
        if m:
            raw = Path(m.group(1)).read_text()
    if not raw:
        raise RuntimeError(f"KG 조회 실패 (brd={brd_cd} sesn={sesn}): {proc.stdout}{proc.stderr}")
    payload = json.loads(raw)
    data = payload.get("data") or []
    if not data:
        raise RuntimeError(f"KG 응답 비어있음 (brd={brd_cd} sesn={sesn}): {raw[:300]}")
    row = data[0]
    return {"qty": float(row["SALE_QTY"]), "tag_amt": float(row["SALE_TAG_AMT"])}


def run_crosscheck(tolerance=DEFAULT_TOLERANCE, verbose=True):
    """전 대상 브랜드×당해시즌 KG 교차검증. (ok: bool, lines: list[str]) 반환."""
    lines, ok = [], True
    lines.append(f"[KG 교차검증] 허용오차 {tolerance * 100:.2f}%  (시즌누계 + 최신주차 / 판매량·판매택가)")
    for brand_code, season_code, sesn in _targets():
        brd_cd = BRAND_TO_BRD_CD.get(brand_code)
        if not brd_cd:
            ok = False
            lines.append(f"  ❌ {brand_code}: BRD_CD 매핑 없음 → BRAND_TO_BRD_CD에 추가 필요")
            continue
        loc = _local_totals(brand_code, season_code)
        # KG 기간: 시즌연도 전년초 ~ 로컬 최신 주차말 (양측 동일 구간 커버)
        start_dt = f"{2000 + int(sesn[:2]) - 1}-01-01"
        kg = _kg_totals(brd_cd, sesn, start_dt, loc["dmax"])
        for label, lv, kv in (("판매량", loc["qty"], kg["qty"]),
                              ("판매택가", loc["tag_amt"], kg["tag_amt"])):
            rel = abs(lv - kv) / kv if kv else (0.0 if lv == 0 else 1.0)
            mark = "✅" if rel <= tolerance else "❌"
            if rel > tolerance:
                ok = False
            lines.append(
                f"  {mark} {brand_code} {sesn} {label}: "
                f"로컬 {lv:,.0f} vs KG {kv:,.0f}  (오차 {rel * 100:.3f}%)"
            )
        # 최신 완결 주차 단일주 대사 — 누계는 맞는데 최근 주가 깨진 경우를 잡는다
        wk = _local_week_totals(brand_code, season_code)
        if wk is None:
            lines.append(f"  ⚠️ {brand_code} {sesn} 최신주차: 완결 주 없음 → 주간 대사 생략")
            continue
        kg_wk = _kg_totals(brd_cd, sesn, wk["start_dt"], wk["end_dt"])
        for label, lv, kv in (("판매량", wk["qty"], kg_wk["qty"]),
                              ("판매택가", wk["tag_amt"], kg_wk["tag_amt"])):
            rel = abs(lv - kv) / kv if kv else (0.0 if lv == 0 else 1.0)
            mark = "✅" if rel <= tolerance else "❌"
            if rel > tolerance:
                ok = False
            lines.append(
                f"  {mark} {brand_code} {sesn} 주간({wk['start_dt']}~{wk['end_dt']}) {label}: "
                f"로컬 {lv:,.0f} vs KG {kv:,.0f}  (오차 {rel * 100:.3f}%)"
            )
    lines.append("[KG 교차검증] " + ("통과 ✅" if ok else "실패 ❌ — 반영 중단"))
    if verbose:
        print("\n".join(lines))
    return ok, lines


def main():
    ap = argparse.ArgumentParser(description="지식그래프 KG 교차검증 게이트")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                    help=f"허용 상대오차 (기본 {DEFAULT_TOLERANCE} = {DEFAULT_TOLERANCE * 100:.1f}%%)")
    args = ap.parse_args()
    if not LOCAL_DB.exists():
        sys.exit(f"❌ 로컬 baseline 없음: {LOCAL_DB}")
    ok, _ = run_crosscheck(tolerance=args.tolerance)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
