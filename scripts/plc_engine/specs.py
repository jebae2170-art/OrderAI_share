"""PLC 엔진 v2 데이터 클래스 정의."""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Literal, Optional

import pandas as pd


@dataclass(frozen=True)
class BrandSpec:
    brand_code: str
    brand_name: str
    avg_size_stock_threshold: int = 100
    has_tax_free: bool = True
    scale_blend: float = 0.5
    plc_exclude_prods: list[str] = field(default_factory=list)
    item_nm_map_path: str = ""
    store_count: Optional[int] = None
    broken_coverage_ratio: float = 0.667  # 1/3 매장 배분 불가 기준

    def __post_init__(self):
        if not self.has_tax_free and self.scale_blend != 0.0:
            object.__setattr__(self, 'scale_blend', 0.0)

    @classmethod
    def from_json(cls, path: str, brand_code: str) -> BrandSpec:
        data = json.load(open(path))
        brand = dict(data["brands"][brand_code])
        # store_count × broken_coverage_ratio 자동 계산 (수동 avg_size_stock_threshold 있으면 우선)
        if 'avg_size_stock_threshold' not in brand and brand.get('store_count'):
            brand['avg_size_stock_threshold'] = round(
                brand['store_count'] * brand.get('broken_coverage_ratio', 0.667)
            )
        return cls(**brand)

    def load_item_nm_map(self) -> dict[str, str]:
        if not self.item_nm_map_path:
            return {}
        return json.load(open(self.item_nm_map_path))


@dataclass(frozen=True)
class SeasonSpec:
    season_code: str
    season_type: Literal["fw", "ss"]
    seasons_for_plc: list[str] = field(default_factory=list)
    fwo_range: tuple[int, int] = (1, 39)
    sale_col: str = "SC_SALE_QTY_TAX"
    season_end_fwo: int = 0  # from_json에서 마감일 ISO주차로 연도-인식 유도 (하드코딩 금지)

    @classmethod
    def from_json(cls, path: str, season_code: str) -> SeasonSpec:
        data = json.load(open(path))
        s = data["seasons"][season_code]
        s["fwo_range"] = tuple(s["fwo_range"])
        spec = cls(**s)
        # season_end_fwo: 시즌 마감일 ISO주차 → fw_order 적용 (SS 항등=ISO주차, FW=39).
        # config 하드코딩 금지 — 매 시즌 마감일에서 연도-인식 유도 (26S→36, 25S→35).
        from config_loader import get_season_end_for_code
        end_iso_week = int(get_season_end_for_code(season_code).isocalendar().week)
        try:
            end_fwo = spec.fw_order(end_iso_week)
        except NotImplementedError:
            end_fwo = end_iso_week  # SS는 항등 (P2 전 임시; P2 후 fw_order가 동일값 반환)
        object.__setattr__(spec, 'season_end_fwo', end_fwo)
        return spec

    def fw_order(self, week: int) -> int:
        if self.season_type == "fw":
            return week - 22 if week >= 23 else week + 30
        return week  # SS: 1월초(ISO 1주) 시작, 연도경계 없음 → FW_ORDER = WEEK_OF_YEAR (항등)

    def fwo_to_label(self, fwo: int) -> str:
        if self.season_type == "fw":
            wk = fwo + 22 if fwo <= 30 else fwo - 30
            return f"W{wk:02d}"
        return f"W{fwo:02d}"  # SS: 항등 (FW_ORDER = WEEK_OF_YEAR)

    def fwo_to_week_int(self, fwo: int) -> int:
        """FW_ORDER → ISO WEEK_OF_YEAR (int). predictor 인라인 역변환 일원화."""
        if self.season_type == "fw":
            return fwo + 22 if fwo <= 30 else fwo - 30
        return fwo  # SS: 항등 (FW_ORDER = WEEK_OF_YEAR)


@dataclass(frozen=True)
class EngineParams:
    dtw_min_window: int = 4
    dtw_warp_band: int = 2
    dtw_shift_high: int = 6
    dtw_shift_medium: int = 3
    dtw_dist_high: float = 0.029
    dtw_dist_medium: float = 0.085
    min_sale_weeks: int = 4
    min_cum_intake: int = 50
    min_sc_for_plc: int = 5
    dead_stock_weeks: int = 2
    plc_tail_decay: float = 0.7

    @classmethod
    def from_json(cls, path: str, brand_code: str) -> EngineParams:
        data = json.load(open(path))
        ep = data["engine_params"]
        merged = {**ep["default"], **ep.get(brand_code, {})}
        return cls(**merged)


@dataclass(frozen=True)
class EngineInputs:
    gt: pd.DataFrame
    restored: Optional[pd.DataFrame]  # None이면 GT의 ADJ 컬럼 사용
    weekly_raw: pd.DataFrame
    plc_ratio: pd.DataFrame


@dataclass(frozen=True)
class BrokenPoint:
    week: int
    pos: int
    avg_size_stock: float = 0.0


@dataclass
class EngineResult:
    sc_predictions: dict  # (prod_cd, color_cd) -> dict
    style_aggregates: dict  # prod_cd -> dict
    metrics: dict
