"""
order-ai-share — 스모크 테스트.
환경 셋업이 올바른지 검증 (pytest).

Level 2: venv + deps + .env format + Snowflake SELECT 1 + brand-specific COUNT.

Run: python -m pytest smoke_test.py -v
"""
import json
import os

import pytest
from dotenv import load_dotenv

load_dotenv()


# [I2] Brand name -> BRD_CD (Snowflake 컬럼 값) 매핑
def _resolve_brand() -> str:
    """운영 브랜드 (대문자). BRAND env 우선, 미설정 시 brand_config.json 폴백 —
    server/permissions.py·config_loader.get_brand 와 동일 규칙."""
    brand = os.environ.get("BRAND", "").strip().upper()
    if brand:
        return brand
    p = os.path.join("public", "brand_config.json")
    if os.path.exists(p):
        with open(p) as f:
            return (json.load(f).get("brand", "") or "").strip().upper()
    return ""


BRAND_TO_BRD_CD = {
    "MLB": "M",
    "DISCOVERY": "X",
    "DUVETICA": "V",
    "MLB KIDS": "I",
    "MLBKIDS": "I",
    "SERGIO": "ST",
    "SERGIO TACCHINI": "ST",
}


class TestEnvironment:
    def test_python_version(self):
        import sys
        assert sys.version_info >= (3, 11), "Python 3.11+ required"

    def test_critical_imports(self):
        import pandas  # noqa: F401
        import duckdb  # noqa: F401
        import snowflake.connector  # noqa: F401
        import fastapi  # noqa: F401

    def test_server_api_importable(self):
        """[I1] server.api 가 부팅 가능해야 함."""
        import server.api  # noqa: F401

    def test_env_file_exists(self):
        assert os.path.exists(".env"), ".env file missing — .env.example 을 복사하세요"

    def test_env_has_snowflake_vars(self):
        # 공통 필수 변수 (인증 모드 무관)
        required = [
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USER",
            "SNOWFLAKE_WAREHOUSE",
            "SNOWFLAKE_DATABASE",
            "SNOWFLAKE_SCHEMA",
        ]
        missing = [v for v in required if not os.environ.get(v)]
        assert not missing, f"Missing env vars: {missing}"

        # 인증 모드별 추가 검증
        auth_mode = os.environ.get("SNOWFLAKE_AUTH", "").strip().lower()
        if auth_mode != "externalbrowser":
            # password auth — SNOWFLAKE_PASSWORD 필수
            assert os.environ.get("SNOWFLAKE_PASSWORD"), (
                "SNOWFLAKE_PASSWORD 누락. SSO 사용 시 SNOWFLAKE_AUTH=externalbrowser 추가."
            )

    def test_brand_configured(self):
        # BRAND env 는 선택 — 미설정 시 brand_config.json 이 SoT (2026-08-03 이중관리 제거)
        assert _resolve_brand(), (
            "브랜드 미설정: brand_config.json 의 'brand' 필드(권장) 또는 BRAND env 필요"
        )

    def test_brand_config_consistency(self):
        """[I8] BRAND env var 와 brand_config.json 이 둘 다 있으면 일치해야 함."""
        env_brand = os.environ.get("BRAND", "").strip().upper()
        config_path = os.path.join("public", "brand_config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
            config_brand = (config.get("brand", "") or "").strip().upper()
            if env_brand and config_brand:
                assert env_brand == config_brand, (
                    f"BRAND env ({env_brand}) != brand_config.json ({config_brand}). "
                    f"Fix one to match the other."
                )

    def test_build_plc_standard_importable(self):
        """[P4] 1번 작업 회귀 방어: build_plc_standard.py import + 핵심 함수 노출."""
        import sys
        scripts_path = os.path.join(os.path.dirname(__file__), "scripts")
        if scripts_path not in sys.path:
            sys.path.insert(0, scripts_path)
        from plc_engine import build_plc_standard
        for fn in ("main", "compute_drift", "prepare_weekly", "calc_plc", "query_snowflake_for_plc"):
            assert callable(getattr(build_plc_standard, fn, None)), f"{fn} 누락"

    def test_seasonspec_fallback(self):
        """[P4] 이슈#1 회귀 방어: SeasonSpec.from_json 이 미등록 시즌에서도 fallback 동작."""
        import sys
        scripts_path = os.path.join(os.path.dirname(__file__), "scripts")
        if scripts_path not in sys.path:
            sys.path.insert(0, scripts_path)
        from plc_engine.specs import SeasonSpec

        cfg_path = os.path.join("public", "plc_engine_config.json")

        # 등록 시즌 — 기존 entry 사용 (회귀 안 깨짐)
        s = SeasonSpec.from_json(cfg_path, "25F")
        assert s.season_type == "fw"

        # 미등록 FW — fallback
        s = SeasonSpec.from_json(cfg_path, "99F")
        assert s.season_type == "fw"
        assert "99F" in s.seasons_for_plc

        # 미등록 SS — fallback
        s = SeasonSpec.from_json(cfg_path, "99S")
        assert s.season_type == "ss"
        assert "99S" in s.seasons_for_plc

        # Invalid suffix — RuntimeError
        with pytest.raises(RuntimeError):
            SeasonSpec.from_json(cfg_path, "99X")


class TestSnowflakeConnectivity:
    def _connect(self):
        import snowflake.connector
        auth_mode = os.environ.get("SNOWFLAKE_AUTH", "").strip().lower()
        kwargs = dict(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
            role=os.environ.get("SNOWFLAKE_ROLE", ""),
            database=os.environ.get("SNOWFLAKE_DATABASE", "FNF"),
            schema=os.environ.get("SNOWFLAKE_SCHEMA", "PRCS"),
        )
        if auth_mode == "externalbrowser":
            kwargs["authenticator"] = "externalbrowser"
        else:
            kwargs["password"] = os.environ.get("SNOWFLAKE_PASSWORD", "")
        return snowflake.connector.connect(**kwargs)

    def test_select_one(self):
        """Snowflake basic connectivity."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        conn.close()

    def test_brand_data_access(self):
        """[I2][I7] 브랜드별 데이터 접근 — parameterized query."""
        brand = _resolve_brand()
        brd_cd = BRAND_TO_BRD_CD.get(brand)
        assert brd_cd is not None, (
            f"Unknown brand '{brand}'. "
            f"Valid brands: {list(BRAND_TO_BRD_CD.keys())}"
        )

        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM FNF.PRCS.DB_SCS_W "
            "WHERE BRD_CD = %s AND SESN = '25F'",
            (brd_cd,),
        )
        count = cur.fetchone()[0]
        assert count > 0, (
            f"No data found for brand '{brand}' (BRD_CD='{brd_cd}') "
            f"-- Snowflake 권한 점검 필요"
        )
        conn.close()
