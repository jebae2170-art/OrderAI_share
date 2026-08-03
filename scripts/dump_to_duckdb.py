"""
DuckDB 적재 스크립트 — public/*.json 5종 → data/production/order_ai.duckdb

배경:
  Order AI Lite Phase 1 Step 3. 운영팀이 시즌당 1회 사전 계산한 결과물(JSON 5종)을
  단일 DuckDB 파일로 적재해 Lite 모드(`/api/lite/*`)의 read-only 데이터 소스로 사용.

설계 원칙:
  - 시즌×브랜드 단위 upsert (DELETE + INSERT) — 기존 시즌 영향 없음
  - JSON 통째 저장 + 자주 쓰는 컬럼만 정규화 (스키마 단순)
  - run_all.py 출력 형식에 변화가 있으면 verify_dump.py가 잡아냄

사용:
  python scripts/dump_to_duckdb.py \
      --brand mlb --season 25f \
      --json-dir public --db data/production/order_ai.duckdb

  # brand_config.json 자동 감지 (생략 시)
  python scripts/dump_to_duckdb.py
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# .env 자동 로드 (server/api.py와 동일 패턴) — S3_API_KEY 등을 export 없이 사용 가능
try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dump_to_duckdb")


# 적재 대상 (운영팀 파이프라인 결과)
INPUT_FILES = {
    "season_closing": "season_closing_data.json",
    "dashboard": "dashboard_data.json",
    "past_styles": "past_styles_data.json",
    "style_mapping": "style_mapping_data.json",
    "order_recommendation": "order_recommendation_data.json",
    "size_assortment": "size_assortment_data.json",
}

# past_styles_data.json: 이전 파이프라인엔 없던 산출 → 누락 시 경고.
# order_recommendation_data.json: 사용자 Step 3 동작 후 data/user-storage/ 에 생성되는 사물함 데이터.
#   baseline 영역에선 의미 없음 (lite.py 의 /order-recommendation 은 사물함만 사용).
#   첫 셋업 시점 (운영자 fresh /run-pipeline) 에는 부재가 정상 → 누락 OK.
OPTIONAL_INPUTS = {"past_styles", "order_recommendation"}


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS brands (
    brand_code VARCHAR PRIMARY KEY,
    brand_name VARCHAR
);

CREATE TABLE IF NOT EXISTS seasons (
    brand_code VARCHAR,
    season_code VARCHAR,
    base_season VARCHAR,
    pipeline_run_at TIMESTAMP,
    pipeline_version VARCHAR,
    source_meta JSON,
    PRIMARY KEY (brand_code, season_code)
);

CREATE TABLE IF NOT EXISTS season_summary (
    brand_code VARCHAR,
    season_code VARCHAR,
    summary_json JSON,
    PRIMARY KEY (brand_code, season_code)
);

CREATE TABLE IF NOT EXISTS class_item_analysis (
    brand_code VARCHAR,
    season_code VARCHAR,
    level VARCHAR,         -- class | item | style_summary | prior_year | yoy
    key VARCHAR,           -- class2 / item_nm / 'main'
    data_json JSON,
    PRIMARY KEY (brand_code, season_code, level, key)
);

CREATE TABLE IF NOT EXISTS style_timeseries (
    brand_code VARCHAR,
    season_code VARCHAR,
    period VARCHAR,        -- current | prev | prev2
    part_cd VARCHAR,
    color_cd VARCHAR,
    ai_diagnosis VARCHAR,  -- hit | normal | shortage | risk (당해만 의미, 과거는 'past')
    chart_data JSON,
    PRIMARY KEY (brand_code, season_code, period, part_cd, color_cd)
);

CREATE TABLE IF NOT EXISTS style_mapping (
    brand_code VARCHAR,
    season_code VARCHAR,
    new_part_cd VARCHAR,
    top1_ref_part VARCHAR,
    top2_ref_part VARCHAR,
    top3_ref_part VARCHAR,
    data_json JSON,
    PRIMARY KEY (brand_code, season_code, new_part_cd)
);

CREATE TABLE IF NOT EXISTS style_mapping_meta (
    brand_code VARCHAR,
    season_code VARCHAR,
    metadata_json JSON,
    PRIMARY KEY (brand_code, season_code)
);

CREATE TABLE IF NOT EXISTS order_recommendation (
    brand_code VARCHAR,
    season_code VARCHAR,
    new_part_cd VARCHAR,
    recommended_qty INTEGER,
    data_json JSON,
    PRIMARY KEY (brand_code, season_code, new_part_cd)
);

CREATE TABLE IF NOT EXISTS order_recommendation_meta (
    brand_code VARCHAR,
    season_code VARCHAR,
    metadata_json JSON,
    PRIMARY KEY (brand_code, season_code)
);

-- Phase 3-1: SESN_SUB_NM/FIT_INFO1 컬럼 추가
-- 1회성 마이그레이션은 이미 완료됨 (옛 13컬럼 → 새 15컬럼)
-- 매 dump 시에는 _dump_size에서 brand_code 단위 DELETE 후 재 INSERT (다른 브랜드 데이터 보존)
CREATE TABLE IF NOT EXISTS size_assortment (
    brand_code VARCHAR,
    season_code VARCHAR,
    period VARCHAR,        -- current | prev
    sex_nm VARCHAR,
    class2 VARCHAR,
    cat_nm VARCHAR,
    sub_cat_nm VARCHAR,
    item VARCHAR,
    item_nm VARCHAR,
    sesn_sub_nm VARCHAR,   -- Phase 3-1: 서브시즌
    fit_info1 VARCHAR,     -- Phase 3-1: 핏정보
    color_range VARCHAR,
    size_cd VARCHAR,
    order_qty INTEGER,
    sale_qty INTEGER
);

-- Phase 3-1: 카테고리 분포 / SC 카운트 / ref 메타 (정책 3 빈자리 채우기용)
CREATE TABLE IF NOT EXISTS size_assortment_meta (
    brand_code VARCHAR,
    season_code VARCHAR,
    category_size_dist JSON,      -- {by_l1: {group: {size: ratio}}, ...}
    category_sample_count JSON,   -- {by_l1: {group: SC_count}, ...}
    ref_meta JSON,                -- {PART_CD: {SESN_SUB_NM, FIT_INFO1}}
    size_order JSON,              -- ['XS','S','M','L',...]
    PRIMARY KEY (brand_code, season_code)
);

CREATE TABLE IF NOT EXISTS size_color_mapping (
    brand_code VARCHAR,
    season_code VARCHAR,
    color_cd VARCHAR,
    color_nm VARCHAR,
    color_range VARCHAR
);

-- 권한 (Step 4에서 채움)
CREATE TABLE IF NOT EXISTS users (
    email VARCHAR PRIMARY KEY,
    brand_access VARCHAR[]
);

CREATE INDEX IF NOT EXISTS idx_timeseries_part
    ON style_timeseries(brand_code, season_code, part_cd);
CREATE INDEX IF NOT EXISTS idx_mapping_part
    ON style_mapping(brand_code, season_code, new_part_cd);
CREATE INDEX IF NOT EXISTS idx_order_part
    ON order_recommendation(brand_code, season_code, new_part_cd);
CREATE INDEX IF NOT EXISTS idx_size_filters
    ON size_assortment(brand_code, season_code, period, class2, item);
"""


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _ensure_size_assortment_schema(con) -> None:
    """Phase 3-1 마이그레이션: size_assortment 가 구 스키마(<15컬럼)면 DROP + 재생성.

    SCHEMA_DDL 의 CREATE TABLE IF NOT EXISTS 는 기존 테이블이 있으면 건너뛰므로,
    구 스키마 (sesn_sub_nm/fit_info1 컬럼 없음) 가 살아남아 INSERT 시 컬럼 mismatch
    에러가 발생하는 문제를 방지. 데이터는 dump 마다 brand 단위 DELETE+INSERT 패턴
    이라 손실 없음 — 다음 dump 가 즉시 재생성. Idempotent: 신 스키마/fresh DB 면 noop.
    """
    # 테이블 존재 확인 (PRAGMA 는 테이블 없을 때 CatalogException 던짐 → fresh DB 보호)
    exists = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'size_assortment'"
    ).fetchone()
    if not exists:
        return  # fresh DB — SCHEMA_DDL 이 신 스키마로 생성

    cols = con.execute("PRAGMA table_info('size_assortment')").fetchall()
    if len(cols) < 15:  # sesn_sub_nm, fit_info1 미포함 = 구 스키마
        logger.warning(
            "size_assortment 구 스키마 감지 (%d컬럼 < 15) → DROP + 재생성. "
            "데이터는 다음 dump 가 재생성합니다.",
            len(cols),
        )
        con.execute("DROP TABLE IF EXISTS size_assortment")


def _resolve_brand_season(args) -> tuple[str, str, str | None]:
    """CLI 인자 우선, 없으면 brand_config.json에서 추론."""
    brand = args.brand
    season = args.season
    base_season = None

    if brand and season:
        # CLI 인자로 받아도 base_season은 brand_config에서 추론 — run_all이 --brand/--season을
        #   명시해 호출하면 base_season=None으로 적재되어, KG 게이트 _targets()(base_season IS
        #   NOT NULL 행만 검증)가 빈 목록 → 게이트가 아무것도 검증 않고 통과하던 결함(2026-08-03).
        bc_path = Path(args.json_dir) / "brand_config.json"
        if bc_path.exists():
            bc = _load_json(bc_path)
            if (bc.get("baseSeason") or "").lower() == season.lower():
                base_season = bc.get("baseSeason")
        return brand.lower(), season.lower(), base_season

    bc_path = Path(args.json_dir) / "brand_config.json"
    if not bc_path.exists():
        raise SystemExit(
            f"--brand/--season 미지정 + {bc_path} 없음 — brand/season을 명시해 주세요."
        )
    bc = _load_json(bc_path)
    brand = brand or bc.get("brand", "").lower()
    # season_code는 분석 baseline 기준(baseSeason). server.db의 query 함수들이 25f를 사용.
    # targetSeason(26f)은 별도 컬럼(target_season)으로 메타 보존.
    base_season = bc.get("baseSeason")
    season = season or (base_season or "").lower()
    if not brand or not season:
        raise SystemExit(f"brand_config.json에서 brand/season 추출 실패: {bc}")
    return brand, season, base_season


def _delete_existing(con, brand: str, season: str) -> None:
    """브랜드×시즌 단위 기존 데이터 삭제 (idempotent upsert)."""
    tables_with_pk = [
        "seasons", "season_summary", "style_timeseries", "style_mapping",
        "style_mapping_meta", "order_recommendation", "order_recommendation_meta",
        "class_item_analysis", "size_assortment", "size_color_mapping",
    ]
    for t in tables_with_pk:
        con.execute(
            f"DELETE FROM {t} WHERE brand_code = ? AND season_code = ?",
            [brand, season],
        )


def _upsert_brand(con, brand: str) -> None:
    con.execute(
        "INSERT INTO brands VALUES (?, ?) ON CONFLICT (brand_code) DO NOTHING",
        [brand, brand.upper()],
    )


def _insert_season(con, brand: str, season: str, base_season: str | None,
                   pipeline_version: str, source_meta: dict) -> None:
    con.execute(
        """INSERT INTO seasons
           VALUES (?, ?, ?, ?, ?, ?)""",
        [brand, season, base_season,
         datetime.now(timezone.utc), pipeline_version, json.dumps(source_meta, ensure_ascii=False)],
    )


def _compact_db(db_path: Path) -> None:
    """누적된 deleted page 회수 — 새 파일로 데이터 복사 후 atomic swap.

    DuckDB 1.x는 in-place DELETE의 deleted page를 회수하지 않아 매 dump마다
    파일이 누적 증가한다. 동일 스키마의 새 파일에 INSERT 후 atomic rename으로
    압축. 양 brand 모든 테이블 row count 검증 통과 시에만 swap.
    """
    new_path = db_path.with_suffix(".compact.duckdb")
    new_path.unlink(missing_ok=True)
    src_size = db_path.stat().st_size / (1024 * 1024)

    try:
        new_con = duckdb.connect(str(new_path))
        new_con.execute(SCHEMA_DDL)
        new_con.execute(f"ATTACH '{db_path}' AS src")
        tables = [r[0] for r in new_con.execute("SHOW TABLES FROM src").fetchall()]
        for t in tables:
            src_n = new_con.execute(f"SELECT COUNT(*) FROM src.{t}").fetchone()[0]
            new_con.execute(f"INSERT INTO {t} SELECT * FROM src.{t}")
            dst_n = new_con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if src_n != dst_n:
                raise RuntimeError(
                    f"Compact 검증 실패: {t} src={src_n} dst={dst_n}"
                )
        new_con.execute("DETACH src")
        new_con.close()
        new_path.replace(db_path)  # atomic rename on same filesystem
        dst_size = db_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"Compact 완료: {src_size:.2f} → {dst_size:.2f} MB "
            f"({(1 - dst_size / src_size) * 100:.1f}% 절감)"
        )
    except Exception as e:
        logger.error(f"Compact 실패 — 원본 유지: {e}")
        new_path.unlink(missing_ok=True)
        raise


def _dump_season_summary(con, brand: str, season: str, sc: dict) -> int:
    con.execute(
        "INSERT INTO season_summary VALUES (?, ?, ?)",
        [brand, season, json.dumps(sc.get("summary", {}), ensure_ascii=False)],
    )
    meta = sc.get("metadata", {})
    if meta:
        con.execute(
            "INSERT INTO class_item_analysis VALUES (?, ?, ?, ?, ?)",
            [brand, season, "metadata", "main", json.dumps(meta, ensure_ascii=False)],
        )
    return 1


def _dump_class_item(con, brand: str, season: str, sc: dict) -> int:
    rows = 0
    for c in sc.get("class_analysis", []):
        con.execute(
            "INSERT INTO class_item_analysis VALUES (?, ?, ?, ?, ?)",
            [brand, season, "class", c.get("class2", ""),
             json.dumps(c, ensure_ascii=False)],
        )
        rows += 1
    for it in sc.get("item_analysis", []):
        key = f"{it.get('class2','')}/{it.get('item_nm','')}"
        con.execute(
            "INSERT INTO class_item_analysis VALUES (?, ?, ?, ?, ?)",
            [brand, season, "item", key, json.dumps(it, ensure_ascii=False)],
        )
        rows += 1
    for level_key in ("style_summary", "prior_year", "yoy"):
        if level_key in sc:
            con.execute(
                "INSERT INTO class_item_analysis VALUES (?, ?, ?, ?, ?)",
                [brand, season, level_key, "main",
                 json.dumps(sc[level_key], ensure_ascii=False)],
            )
            rows += 1
    return rows


def _dump_dashboard(con, brand: str, season: str, dash: dict) -> int:
    """당해 시즌 dashboard_data.json → style_timeseries (period='current')."""
    rows = 0
    for diagnosis in ("hit", "normal", "shortage", "risk"):
        for entry in dash.get(diagnosis, []):
            total = entry.get("total", {})
            info = total.get("itemInfo", {})
            part_cd = info.get("code", "")
            con.execute(
                "INSERT OR REPLACE INTO style_timeseries VALUES (?, ?, ?, ?, ?, ?, ?)",
                [brand, season, "current", part_cd, "ALL", diagnosis,
                 json.dumps(total, ensure_ascii=False)],
            )
            rows += 1
            colors = entry.get("colors") or {}
            if isinstance(colors, dict):
                for color_cd, color_data in colors.items():
                    con.execute(
                        "INSERT OR REPLACE INTO style_timeseries VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [brand, season, "current", part_cd, color_cd, diagnosis,
                         json.dumps(color_data, ensure_ascii=False)],
                    )
                    rows += 1
    return rows


def _dump_past_styles(con, brand: str, season: str, past: dict) -> int:
    """과거 시즌 past_styles_data.json → style_timeseries (period='prev'|'prev2').

    Step 3 직접입력 PART_CD lookup에서 과거 3시즌 ref 데이터를 조회할 때 사용.
    AI 진단은 당해 분류 전용이므로 과거는 'past'로 통일.
    """
    rows = 0
    for period_key in ("prev", "prev2"):
        section = past.get(period_key) or {}
        if not isinstance(section, dict):
            continue
        for part_cd, entry in section.items():
            total = entry.get("total", {})
            con.execute(
                "INSERT OR REPLACE INTO style_timeseries VALUES (?, ?, ?, ?, ?, ?, ?)",
                [brand, season, period_key, part_cd, "ALL", "past",
                 json.dumps(total, ensure_ascii=False)],
            )
            rows += 1
            colors = entry.get("colors") or {}
            if isinstance(colors, dict):
                for color_cd, color_data in colors.items():
                    con.execute(
                        "INSERT OR REPLACE INTO style_timeseries VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [brand, season, period_key, part_cd, color_cd, "past",
                         json.dumps(color_data, ensure_ascii=False)],
                    )
                    rows += 1
    return rows


def _ref_part(refs: list, idx: int) -> str | None:
    if not isinstance(refs, list) or idx >= len(refs):
        return None
    r = refs[idx]
    if not isinstance(r, dict):
        return None
    return r.get("ref_part_cd") or r.get("ref_part") or r.get("part_cd")


def _dump_style_mapping(con, brand: str, season: str, sm: dict) -> int:
    con.execute(
        "INSERT INTO style_mapping_meta VALUES (?, ?, ?)",
        [brand, season, json.dumps(sm.get("metadata", {}), ensure_ascii=False)],
    )
    rows = 0
    for s in sm.get("styles", []):
        new_part = s.get("new_part_cd", "")
        refs = s.get("references", [])
        con.execute(
            "INSERT OR REPLACE INTO style_mapping VALUES (?, ?, ?, ?, ?, ?, ?)",
            [brand, season, new_part,
             _ref_part(refs, 0), _ref_part(refs, 1), _ref_part(refs, 2),
             json.dumps(s, ensure_ascii=False)],
        )
        rows += 1
    return rows


def _dump_order_recommendation(con, brand: str, season: str, ors: dict) -> int:
    con.execute(
        "INSERT INTO order_recommendation_meta VALUES (?, ?, ?)",
        [brand, season, json.dumps(ors.get("metadata", {}), ensure_ascii=False)],
    )
    rows = 0
    for r in ors.get("recommendations", []):
        new_part = r.get("new_part_cd", "")
        qty = r.get("추천발주량") or r.get("recommended_qty") or 0
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            qty_int = 0
        con.execute(
            "INSERT OR REPLACE INTO order_recommendation VALUES (?, ?, ?, ?, ?)",
            [brand, season, new_part, qty_int,
             json.dumps(r, ensure_ascii=False)],
        )
        rows += 1
    return rows


def _dump_size(con, brand: str, season: str, sa: dict) -> tuple[int, int]:
    # 기존 데이터 정리 (스키마 변경 시 충돌 방지 + 중복 행 방지)
    con.execute("DELETE FROM size_assortment WHERE brand_code=? AND season_code=?", [brand, season])
    con.execute("DELETE FROM size_assortment_meta WHERE brand_code=? AND season_code=?", [brand, season])
    con.execute("DELETE FROM size_color_mapping WHERE brand_code=? AND season_code=?", [brand, season])

    rows_size = 0
    for period_key, src_key in (("current", "salesData"), ("prev", "prevData")):
        for r in sa.get(src_key, []):
            con.execute(
                "INSERT INTO size_assortment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    brand, season, period_key,
                    r.get("SEX_NM"), r.get("CLASS2"), r.get("CAT_NM"),
                    r.get("SUB_CAT_NM"), r.get("ITEM"), r.get("ITEM_NM"),
                    r.get("SESN_SUB_NM"), r.get("FIT_INFO1"),
                    r.get("COLOR_RANGE"), r.get("SIZE_CD"),
                    int(r.get("ORDER_QTY") or 0), int(r.get("SALE_QTY") or 0),
                ],
            )
            rows_size += 1

    rows_color = 0
    for cm in sa.get("colorMapping", []):
        con.execute(
            "INSERT INTO size_color_mapping VALUES (?, ?, ?, ?, ?)",
            [brand, season, cm.get("컬러코드"), cm.get("컬러명"), cm.get("COLOR_RANGE")],
        )
        rows_color += 1

    # Phase 3-1: 카테고리 분포 + ref_meta + sample_count + size_order 저장
    con.execute(
        "INSERT INTO size_assortment_meta VALUES (?, ?, ?, ?, ?, ?)",
        [
            brand, season,
            json.dumps(sa.get("category_size_dist", {}), ensure_ascii=False),
            json.dumps(sa.get("category_sample_count", {}), ensure_ascii=False),
            json.dumps(sa.get("ref_meta", {}), ensure_ascii=False),
            json.dumps((sa.get("meta") or {}).get("sizeOrder", []), ensure_ascii=False),
        ],
    )
    return rows_size, rows_color


def main():
    ap = argparse.ArgumentParser(description="public/*.json 5종 → DuckDB 적재")
    ap.add_argument("--brand", help="브랜드 코드 (예: mlb). 미지정 시 brand_config.json 사용")
    ap.add_argument("--season", help="시즌 코드 (예: 25f). 미지정 시 brand_config.json::targetSeason")
    ap.add_argument("--json-dir", default="public", help="입력 JSON 디렉토리 (기본 public)")
    ap.add_argument("--db", default="data/production/order_ai.duckdb",
                    help="출력 DuckDB 경로")
    ap.add_argument("--pipeline-version", default=None,
                    help="파이프라인 버전 태그 (미지정 시 timestamp)")
    ap.add_argument("--s3-upload", action="store_true",
                    help="적재 완료 후 baseline DuckDB를 S3로 업로드 (S3_API_KEY 환경변수 필요)")
    ap.add_argument("--s3-env", default=None,
                    help="S3 environment (dev/stg/prd). 미지정 시 S3_ENV 환경변수 사용")
    args = ap.parse_args()

    brand, season, base_season = _resolve_brand_season(args)
    pipeline_version = args.pipeline_version or datetime.now().strftime("%Y%m%d_%H%M%S")

    json_dir = Path(args.json_dir)
    inputs = {k: json_dir / fn for k, fn in INPUT_FILES.items()}
    missing = [str(p) for k, p in inputs.items()
               if not p.exists() and k not in OPTIONAL_INPUTS]
    if missing:
        raise SystemExit(f"입력 JSON 누락: {missing}")

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"브랜드={brand} 시즌={season} base={base_season} db={db_path}")

    con = duckdb.connect(str(db_path))
    try:
        con.execute("BEGIN")
        # Phase 3-1: 기존 baseline DB 가 구 스키마 size_assortment 를 갖고 있으면
        # CREATE TABLE IF NOT EXISTS 가 건너뛰어 INSERT 시 컬럼 mismatch 발생.
        # 자동 마이그레이션 (idempotent — 신 스키마면 noop).
        _ensure_size_assortment_schema(con)
        con.execute(SCHEMA_DDL)
        _delete_existing(con, brand, season)
        _upsert_brand(con, brand)

        # 입력 메타 (적재 검증/추적용)
        source_meta = {
            "input_files": {k: str(v) for k, v in inputs.items()},
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        _insert_season(con, brand, season, base_season,
                       pipeline_version, source_meta)

        sc = _load_json(inputs["season_closing"])
        n_summary = _dump_season_summary(con, brand, season, sc)
        n_classitem = _dump_class_item(con, brand, season, sc)

        dash = _load_json(inputs["dashboard"])
        n_ts = _dump_dashboard(con, brand, season, dash)

        # 과거 시즌 ref 데이터 (Step 3 직접입력 lookup용) — 신구 파이프라인 모두 호환
        n_past = 0
        past_path = inputs["past_styles"]
        if past_path.exists():
            past = _load_json(past_path)
            n_past = _dump_past_styles(con, brand, season, past)
        else:
            logger.warning(
                f"past_styles_data.json 미존재 → 과거 시즌 ref lookup 미지원 "
                f"(weekly_analysis.py 최신 버전 재실행 필요)"
            )

        sm = _load_json(inputs["style_mapping"])
        n_map = _dump_style_mapping(con, brand, season, sm)

        n_ord = 0
        order_path = inputs["order_recommendation"]
        if order_path.exists():
            ors = _load_json(order_path)
            n_ord = _dump_order_recommendation(con, brand, season, ors)
        else:
            logger.info(
                "order_recommendation_data.json 미존재 → baseline 비어있음 "
                "(사용자 Step 3 동작 후 data/user-storage/에 자동 생성)"
            )

        sa = _load_json(inputs["size_assortment"])
        n_size, n_color = _dump_size(con, brand, season, sa)

        con.execute("COMMIT")
        # WAL을 메인 파일로 flush (안정성). DuckDB 1.5는 deleted page를 회수하지 않으므로
        # 동일 시즌 재dump 시 파일 크기는 누적된다. 50MB 한도 초과 시 DB 파일 재생성 권장.
        con.execute("CHECKPOINT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    logger.info(
        f"완료: summary={n_summary} class_item={n_classitem} "
        f"timeseries={n_ts}(current) +{n_past}(prev/prev2) "
        f"mapping={n_map} order={n_ord} size={n_size} color_map={n_color}"
    )

    file_size = db_path.stat().st_size / (1024 * 1024)
    logger.info(f"DB 파일 크기: {file_size:.2f} MB")

    _compact_db(db_path)

    if args.s3_upload:
        if args.s3_env:
            os.environ["S3_ENV"] = args.s3_env
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from server.s3_client import get_duckdb_s3_key, upload_binary

        if not os.getenv("S3_API_KEY"):
            logger.error("S3_API_KEY 환경변수가 없어 S3 업로드를 건너뜁니다.")
            sys.exit(3)

        s3_key = get_duckdb_s3_key()
        logger.info(f"S3 업로드 시작: {db_path} → s3://{s3_key}")
        ok = asyncio.run(upload_binary(s3_key, str(db_path)))
        if not ok:
            logger.error("S3 업로드 실패")
            sys.exit(4)
        logger.info(f"S3 업로드 완료: {s3_key}")


if __name__ == "__main__":
    main()
