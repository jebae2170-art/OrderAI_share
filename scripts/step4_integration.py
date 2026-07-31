"""
STEP 5: 유사스타일 맵핑 데이터 생성 (프론트엔드용)

ML 유사스타일 맵핑 결과를 STEP2/3 분석 결과와 결합하여
프론트엔드 Step 3 (StyleMapping)에서 사용할 JSON을 생성합니다.

매핑 파일 포맷:
  - Long format xlsx: BRD_CD, NEW_SEASON, NEW_STYLE, SIMILAR_STYLE,
    SIMILAR_STYLE_SEASON, ITEM, CAT_NM_ENG, RANKING
  - Wide format csv (레거시): NEW_PART_CD, REF_PART_CD_1~3, REF_SCORE_1~3

AI발주량 조회 우선순위:
  1) STEP2/3 timeseries 결과 (AI제안 발주량) — 당해 시즌 스타일
  2) season_raw 폴백 (총판매 기반 추정) — 전년 시즌 스타일
  3) 참조 불가 — 2시즌 이전 스타일
"""

import os
import sys
import json
import math
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

# ── 경로 설정 ───────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

from config_loader import (
    get_base_season, get_target_season, get_timeseries_output_path,
    get_similarity_mapping_path, get_input_data_path,
)

ANALYSIS_RESULT_FILE = get_timeseries_output_path()
DEFAULT_MAPPING_FILE = get_similarity_mapping_path()
SAMPLE_MAPPING_FILE = DEFAULT_MAPPING_FILE.replace(
    "similarity_mapping.csv", "similarity_mapping_sample.csv"
)

OUTPUT_JSON = os.path.join(PUBLIC_DIR, "style_mapping_data.json")

# ── 상수 ────────────────────────────────────────────────────
MIN_SCORE = 0.50
NEW_SEASON = get_target_season()
REF_SEASON = get_base_season()

# STEP2/3 결과 컬럼명
COL_PART_CD = "PART_CD"
COL_ITEM_NM = "ITEM_NM"
COL_PRICE = "판매가"
COL_COLOR_CD = "COLOR_CD"
COL_TOTAL_ORDER = "총발주"
COL_TOTAL_INBOUND = "총입고"
COL_TOTAL_SALE = "총판매"
COL_SELL_RATE = "최종판매율"
COL_AI_DIAG = "AI_진단"
COL_AI_OPP_COST = "AI 계산 기회비용"
COL_AI_ORDER = "AI제안 발주량"

# CAT_NM_ENG → CLASS2 (복종) 매핑
# budget_config.json의 class2(Inner/Bottom/Outer)와 매칭하기 위한 변환
CAT_TO_CLASS2 = {
    # English
    "T-shirts": "Inner", "Sweater": "Inner", "Sweatshirts/Hoodie": "Inner",
    "Shirt": "Inner", "Sleeveless": "Inner", "Sweatsuit": "Inner",
    "Denim": "Bottom", "Pants": "Bottom", "Shorts": "Bottom",
    "Skirt": "Bottom", "Leggings": "Bottom",
    "Outerwear": "Outer", "Padded Jacket": "Outer", "Fleece": "Outer",
    # Korean (CAT_NM)
    "티셔츠": "Inner", "스웨터": "Inner", "맨투맨/후드": "Inner",
    "셔츠": "Inner", "트레이닝셋업": "Inner",
    "데님": "Bottom", "팬츠": "Bottom", "스커트": "Bottom", "레깅스": "Bottom",
    "아우터": "Outer", "패딩": "Outer", "후리스": "Outer",
    "모자": "Outer", "기타용품": "Outer",
}

# RANKING → 더미 유사도 점수
RANK_TO_SCORE = {1: 0.95, 2: 0.85, 3: 0.75}


# ═══════════════════════════════════════════════════════════════
# 매핑 파일 로드
# ═══════════════════════════════════════════════════════════════

def _pivot_long_to_wide(df: pd.DataFrame, item_name_map: dict, item_class2_map: dict = None) -> list:
    """Long format DataFrame을 Wide format dict list로 피벗.

    item_class2_map: D1 season_raw의 ITEM 코드 → CLASS2 (Inner/Outer/Bottom 영문) 매핑.
    제공되면 NEW_CLASS2 결정 시 CAT_NM_ENG/CAT_NM(한글 카테고리) 대신 D1 CLASS2를 우선 사용해
    한글 매핑 누락 문제를 해소한다. 또한 row에 "CLASS2_BUDGET"로 영문값을 보존해
    하류 build_style_mapping이 다시 lookup하지 않고 그대로 활용한다.
    """
    item_class2_map = item_class2_map or {}
    styles = []
    for new_style, group in df.groupby("NEW_STYLE"):
        group_sorted = group.sort_values("RANKING")
        item_cd = str(group_sorted.iloc[0].get("ITEM", "")).strip()
        class2_budget = item_class2_map.get(item_cd, "")
        row = {
            "NEW_PART_CD": new_style,
            "NEW_ITEM_NM": item_name_map.get(str(item_cd), str(item_cd)),
            "NEW_PRDT_NM": str(group_sorted.iloc[0].get("NEW_PRDT_NM", group_sorted.iloc[0].get("PRDT_NM", ""))),
            "NEW_PO_IMG": str(group_sorted.iloc[0].get("NEW_PO_IMG", "")),
            "NEW_CLASS2": group_sorted.iloc[0].get("CAT_NM_ENG", "") or group_sorted.iloc[0].get("CAT_NM", ""),
            "CLASS2_BUDGET": class2_budget,
        }
        for _, r in group_sorted.iterrows():
            rank = int(r["RANKING"])
            if 1 <= rank <= 3:
                row[f"REF_PART_CD_{rank}"] = r["SIMILAR_STYLE"]
                row[f"REF_SCORE_{rank}"] = RANK_TO_SCORE.get(rank, 0.70)
                row[f"REF_PRDT_NM_{rank}"] = str(r.get("PRDT_NM", ""))
                row[f"REF_PRDT_IMG_{rank}"] = str(r.get("SIMILAR_PRDT_IMG", ""))
                row[f"REF_PO_IMG_{rank}"] = str(r.get("SIMILAR_PO_IMG", ""))
        styles.append(row)
    return styles


def load_mapping_long_xlsx(filepath: str, target_season: str) -> pd.DataFrame:
    """ML Long format xlsx를 Wide format DataFrame으로 변환.

    Input:  BRD_CD | NEW_SEASON | NEW_STYLE | SIMILAR_STYLE | SIMILAR_STYLE_SEASON | ITEM | CAT_NM_ENG | RANKING
    Output: NEW_PART_CD | NEW_ITEM_NM | NEW_CLASS2 | REF_PART_CD_1 | REF_SCORE_1 | ... | REF_PART_CD_3 | REF_SCORE_3

    Result 1 시트에서 1차 매핑 후, 매칭 불가 스타일은 Result 2 시트에서 재탐색.
    """
    # ITEM 약어 → 풀네임/CLASS2 매핑 (season_raw 기준)
    # D1.CLASS2는 이미 Inner/Outer/Bottom/Headwear/Acc_etc 등 영문 표준값이라 추가 변환 불필요.
    item_name_map = {}
    item_class2_map = {}
    try:
        from config_loader import load_data as _load_data
        raw_df = _load_data("d1")[["ITEM", "ITEM_NM", "CLASS2"]]
        item_name_map = dict(zip(raw_df["ITEM"].astype(str), raw_df["ITEM_NM"].astype(str)))
        item_class2_map = dict(zip(raw_df["ITEM"].astype(str), raw_df["CLASS2"].astype(str)))
    except Exception:
        try:
            from config_loader import get_input_data_path
            raw_path = get_input_data_path()
            if os.path.exists(raw_path):
                raw_df = pd.read_excel(raw_path, usecols=["ITEM", "ITEM_NM", "CLASS2"])
                item_name_map = dict(zip(raw_df["ITEM"].astype(str), raw_df["ITEM_NM"].astype(str)))
                item_class2_map = dict(zip(raw_df["ITEM"].astype(str), raw_df["CLASS2"].astype(str)))
        except Exception:
            pass

    # 1차: Result 1 로드 (Snowflake/CSV 3-tier → 엑셀 폴백)
    try:
        df1 = _load_data("d3_r1")
    except Exception:
        xls = pd.ExcelFile(filepath)
        sheet1 = "Result 1" if "Result 1" in xls.sheet_names else xls.sheet_names[0]
        df1 = pd.read_excel(xls, sheet_name=sheet1)

    if "NEW_SEASON" in df1.columns:
        before = len(df1)
        df1 = df1[df1["NEW_SEASON"] == target_season].copy()
        print(f"    - {target_season} 필터 (Result 1): {before}행 → {len(df1)}행")

    if df1.empty:
        print(f"    ⚠ {target_season} Result 1 없음 → Result 2(GTM)로 보완 시도")
        styles = []
    else:
        styles = _pivot_long_to_wide(df1, item_name_map, item_class2_map)
        print(f"    - Result 1 피벗: {len(styles)} 스타일")

    # 2차: Result 2에서 미매칭 스타일 보완 (Snowflake/CSV 3-tier → 엑셀 폴백)
    r1_style_set = {s["NEW_PART_CD"] for s in styles}
    df2 = pd.DataFrame()
    try:
        df2 = _load_data("d3_r2")
    except Exception:
        try:
            xls_fb = pd.ExcelFile(filepath)
            if "Result 2" in xls_fb.sheet_names:
                df2 = pd.read_excel(xls_fb, sheet_name="Result 2")
        except Exception:
            pass

    if "NEW_SEASON" in df2.columns:
        df2 = df2[df2["NEW_SEASON"] == target_season].copy()

    # Result 1에 없는 스타일만 필터
    if not df2.empty:
        df2 = df2[~df2["NEW_STYLE"].isin(r1_style_set)].copy()

    if not df2.empty:
        r2_styles = _pivot_long_to_wide(df2, item_name_map, item_class2_map)
        styles.extend(r2_styles)
        print(f"    - Result 2 보완: +{len(r2_styles)} 스타일 (R1 미포함)")

    if not styles:
        print(f"    ⚠ {target_season} Result 1·2 모두 비어있음.")
        return pd.DataFrame()

    result = pd.DataFrame(styles)
    print(f"    - 피벗 완료: {len(result)} 스타일 (Wide format)")
    return result


def load_mapping_wide_csv(filepath: str) -> pd.DataFrame:
    """레거시 Wide format CSV 로드."""
    return pd.read_csv(filepath, encoding="utf-8-sig")


def load_mapping_file(filepath: str) -> pd.DataFrame:
    """파일 확장자에 따라 적절한 로더 선택."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".xlsx":
        return load_mapping_long_xlsx(filepath, NEW_SEASON)
    else:
        return load_mapping_wide_csv(filepath)


# ═══════════════════════════════════════════════════════════════
# 분석 결과 로드 (timeseries + season_raw 폴백)
# ═══════════════════════════════════════════════════════════════

def ceil_10(x):
    """10단위 올림"""
    if pd.isna(x) or x <= 0:
        return 0
    return int(math.ceil(x / 10) * 10)


def load_analysis_result() -> pd.DataFrame:
    """STEP2/3 timeseries 분석 결과 → 스타일 레벨 집계"""
    print(f"  ▸ STEP2/3 결과 로드: {os.path.basename(ANALYSIS_RESULT_FILE)}")
    df = pd.read_excel(ANALYSIS_RESULT_FILE)
    print(f"    - 원본 행 수 (컬러별): {len(df)}")

    for col in [COL_TOTAL_ORDER, COL_TOTAL_INBOUND, COL_TOTAL_SALE,
                COL_AI_OPP_COST, COL_AI_ORDER, COL_SELL_RATE]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    diag_priority = {
        "🟢Hit (적기 소진)": 1,
        "🚨Early Shortage (5월전 품절)": 2,
        "⚠️Shortage (시즌중 품절)": 3,
        "⚪Normal": 4,
        "🔴Risk (부진)": 5,
    }

    def representative_diag(series):
        vals = series.dropna().unique()
        if len(vals) == 0:
            return "-"
        return min(vals, key=lambda x: diag_priority.get(x, 99))

    agg_dict = {
        COL_TOTAL_ORDER: "sum",
        COL_TOTAL_INBOUND: "sum",
        COL_TOTAL_SALE: "sum",
        COL_AI_OPP_COST: "sum",
        COL_AI_ORDER: "sum",
        COL_PRICE: "first",
        COL_ITEM_NM: "first",
    }
    agg_dict = {k: v for k, v in agg_dict.items() if k in df.columns}
    style_summary = df.groupby(COL_PART_CD).agg(agg_dict).reset_index()

    if COL_TOTAL_SALE in style_summary.columns and COL_TOTAL_INBOUND in style_summary.columns:
        style_summary[COL_SELL_RATE] = (
            style_summary[COL_TOTAL_SALE] / style_summary[COL_TOTAL_INBOUND].replace(0, np.nan) * 100
        ).fillna(0).round(1)

    if COL_AI_DIAG in df.columns:
        diag_series = df.groupby(COL_PART_CD)[COL_AI_DIAG].apply(representative_diag)
        style_summary = style_summary.merge(diag_series.reset_index(), on=COL_PART_CD, how="left")

    print(f"    - 스타일 수 (timeseries): {len(style_summary)}")
    return style_summary


def load_season_raw_fallback() -> pd.DataFrame:
    """season_raw.xlsx → 스타일 레벨 집계 (timeseries에 없는 전년 스타일용 폴백)"""
    print(f"  ▸ season_raw 폴백 로드")
    try:
        from config_loader import load_data as _load_data
        df = _load_data("d1")
    except Exception:
        season_raw_path = get_input_data_path()
        if not os.path.exists(season_raw_path):
            return pd.DataFrame()
        df = pd.read_excel(season_raw_path)

    # 당해+전년 모두 로드 (유사스타일이 과거 시즌일 수 있으므로 필터하지 않음)
    # PART_CD에 시즌코드가 포함되어 있어 당해/전년 스타일코드가 겹치지 않음

    # CLASS1 = '의류'만 필터링 (ACC 제외)
    if 'CLASS1' in df.columns:
        df = df[df['CLASS1'].astype(str).str.contains('의류', na=False)].copy()

    # 컬럼 매핑 — 필요한 컬럼만 안전하게 추출
    cols = df.columns.tolist()

    # PART_CD
    part_col = next((c for c in cols if "PART_CD" in str(c)), None)
    # ITEM_NM
    item_col = next((c for c in cols if "ITEM_NM" in str(c)), None)
    # 입고: STOR_QTY_KR 우선, 없으면 STOR_QTY
    in_col = next((c for c in cols if "STOR_QTY_KR" in str(c)),
                  next((c for c in cols if "STOR_QTY" in str(c)), None))
    # 판매: SALE_QTY_CNS 우선
    sale_col = next((c for c in cols if "SALE_QTY_CNS" in str(c)),
                    next((c for c in cols if "SALE_QTY" in str(c)), None))
    # 발주: ORDER_QTY_KR 우선 (국내), 없으면 ORDER_QTY
    order_col = next((c for c in cols if "ORDER_QTY_KR" in str(c)),
                     next((c for c in cols if c == "ORDER_QTY"), None))

    rename = {}
    if part_col: rename[part_col] = "PART_CD"
    if item_col: rename[item_col] = "ITEM_NM"
    if in_col: rename[in_col] = "IN_QTY"
    if sale_col: rename[sale_col] = "SALE_QTY"
    if order_col: rename[order_col] = "ORDER_QTY"

    # 충돌 방지: rename 대상이 아닌 동명 컬럼은 미리 드롭
    keep_cols = set(rename.keys()) | {"PART_CD"}
    drop_cols = [c for c in cols if c not in keep_cols and rename.get(c) is None]
    df = df[list(rename.keys())].copy()
    df = df.rename(columns=rename)

    if "PART_CD" not in df.columns:
        return pd.DataFrame()

    # 수치 변환
    for c in ["ORDER_QTY", "IN_QTY", "SALE_QTY", "STOCK_QTY"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # 스타일 레벨 집계
    agg = {}
    if "IN_QTY" in df.columns:
        agg["IN_QTY"] = "sum"
    if "SALE_QTY" in df.columns:
        agg["SALE_QTY"] = "sum"
    if "ORDER_QTY" in df.columns:
        agg["ORDER_QTY"] = "sum"
    if "ITEM_NM" in df.columns:
        agg["ITEM_NM"] = "first"

    if not agg:
        return pd.DataFrame()

    summary = df.groupby("PART_CD").agg(agg).reset_index()

    # 판매율 계산
    if "SALE_QTY" in summary.columns and "IN_QTY" in summary.columns:
        summary["SELL_RATE"] = (
            summary["SALE_QTY"] / summary["IN_QTY"].replace(0, np.nan) * 100
        ).fillna(0).round(1)

    print(f"    - 스타일 수 (season_raw 폴백): {len(summary)}")
    return summary


# ═══════════════════════════════════════════════════════════════
# 참조 스타일 정보 조회
# ═══════════════════════════════════════════════════════════════

def get_reference_info(
    ref_part_cd: str,
    ref_score: float,
    style_summary: pd.DataFrame,
    season_fallback: pd.DataFrame,
) -> Optional[dict]:
    """참조 스타일의 실적 정보 조회. timeseries → season_raw 폴백."""
    if pd.isna(ref_part_cd) or pd.isna(ref_score) or ref_score < MIN_SCORE:
        return None

    ref_part_cd = str(ref_part_cd).strip()

    # 1차: timeseries 결과 (AI발주량 포함)
    match = style_summary[style_summary[COL_PART_CD] == ref_part_cd]
    if not match.empty:
        row = match.iloc[0]
        return {
            "part_cd": ref_part_cd,
            "score": float(ref_score),
            "총판매": int(row.get(COL_TOTAL_SALE, 0)),
            "총발주": int(row.get(COL_TOTAL_ORDER, 0)),
            "총입고": int(row.get(COL_TOTAL_INBOUND, 0)),
            "판매율": float(row.get(COL_SELL_RATE, 0)),
            "기회비용": int(row.get(COL_AI_OPP_COST, 0)),
            "AI발주량": int(row.get(COL_AI_ORDER, 0)),
            "진단": str(row.get(COL_AI_DIAG, "-")),
            "판매가": int(row.get(COL_PRICE, 0)),
            "아이템명": str(row.get(COL_ITEM_NM, "-")),
            "source": "timeseries",
        }

    # 2차: season_raw 폴백 (총판매를 AI발주량 추정치로 사용)
    if not season_fallback.empty and "PART_CD" in season_fallback.columns:
        fb = season_fallback[season_fallback["PART_CD"] == ref_part_cd]
        if not fb.empty:
            row = fb.iloc[0]
            sale_qty = int(row.get("SALE_QTY", 0))
            in_qty = int(row.get("IN_QTY", 0))
            order_qty = int(row.get("ORDER_QTY", 0))
            return {
                "part_cd": ref_part_cd,
                "score": float(ref_score),
                "총판매": sale_qty,
                "총발주": order_qty,
                "총입고": in_qty,
                "판매율": float(row.get("SELL_RATE", 0)),
                "기회비용": 0,
                "AI발주량": ceil_10(sale_qty),  # 폴백: 총판매 = 추정 수요
                "진단": "📊 전년 데이터 (폴백)",
                "판매가": 0,
                "아이템명": str(row.get("ITEM_NM", "-")),
                "source": "season_raw",
            }

    return None


def get_top3_references(
    mapping_row: pd.Series,
    style_summary: pd.DataFrame,
    season_fallback: pd.DataFrame,
) -> List[dict]:
    """맵핑 행에서 Top 3 유사스타일 실적 조회"""
    refs = []
    for i in range(1, 4):
        part_col = f"REF_PART_CD_{i}"
        score_col = f"REF_SCORE_{i}"
        prdt_col = f"REF_PRDT_NM_{i}"
        ref_part_cd = mapping_row.get(part_col)
        ref_score = mapping_row.get(score_col, 0)
        img_col = f"REF_PRDT_IMG_{i}"
        po_img_col = f"REF_PO_IMG_{i}"
        info = get_reference_info(ref_part_cd, ref_score, style_summary, season_fallback)
        if info is not None:
            info["rank"] = i
            info["prdt_nm"] = str(mapping_row.get(prdt_col, "")).strip()
            _img = str(mapping_row.get(img_col, "")).strip()
            info["prdt_img"] = _img if _img != 'None' else ''
            _po_img = str(mapping_row.get(po_img_col, "")).strip()
            info["po_img"] = _po_img if _po_img != 'None' else ''
            refs.append(info)
    return refs


# ═══════════════════════════════════════════════════════════════
# JSON 생성
# ═══════════════════════════════════════════════════════════════

def generate_style_mapping_json(
    mapping_df: pd.DataFrame,
    style_summary: pd.DataFrame,
    season_fallback: pd.DataFrame,
) -> dict:
    """전체 신규 스타일에 대한 맵핑 JSON 생성"""
    styles = []
    matched = 0
    unmatched = 0
    source_counts = {"timeseries": 0, "season_raw": 0}

    for _, row in mapping_df.iterrows():
        new_part_cd = str(row.get("NEW_PART_CD", "")).strip()
        new_item_nm = str(row.get("NEW_ITEM_NM", "")).strip()
        new_prdt_nm = str(row.get("NEW_PRDT_NM", "")).strip()
        new_po_img = str(row.get("NEW_PO_IMG", "")).strip()
        new_class2 = str(row.get("NEW_CLASS2", "")).strip()

        refs = get_top3_references(row, style_summary, season_fallback)

        if refs:
            matched += 1
            # Top-1의 source 기록
            source_counts[refs[0].get("source", "timeseries")] += 1
        else:
            unmatched += 1

        # D1.CLASS2(영문 표준) 우선 — _pivot_long_to_wide가 CLASS2_BUDGET으로 보존.
        # 비어 있을 때만 CAT_NM 한글 매핑 fallback (방어용).
        class2_budget = str(row.get("CLASS2_BUDGET", "")).strip()
        class2 = class2_budget or CAT_TO_CLASS2.get(new_class2, "")

        styles.append({
            "new_part_cd": new_part_cd,
            "new_item_nm": new_item_nm,
            "new_prdt_nm": new_prdt_nm,
            "new_po_img": new_po_img if new_po_img != 'None' else '',
            "new_class2": new_class2,
            "class2": class2,
            "references": [
                {
                    "rank": r["rank"],
                    "part_cd": r["part_cd"],
                    "item_nm": r["아이템명"],
                    "score": r["score"],
                    "총판매": r["총판매"],
                    "총입고": r["총입고"],
                    "판매율": r["판매율"],
                    "진단": r["진단"],
                    "AI발주량": r["AI발주량"],
                    "기회비용": r["기회비용"],
                    "판매가": r["판매가"],
                    "prdt_nm": r.get("prdt_nm", ""),
                    "prdt_img": r.get("prdt_img", ""),
                    "po_img": r.get("po_img", ""),
                    "source": r.get("source", "timeseries"),
                }
                for r in refs
            ],
        })

    print(f"    - 매칭 성공: {matched} (timeseries: {source_counts['timeseries']}, season_raw 폴백: {source_counts['season_raw']})")
    print(f"    - 매칭 불가: {unmatched}")

    return {
        "metadata": {
            "new_season": NEW_SEASON,
            "ref_season": REF_SEASON,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_styles": len(styles),
            "matched_styles": matched,
            "unmatched_styles": unmatched,
            "source_breakdown": source_counts,
        },
        "styles": styles,
    }


# ═══════════════════════════════════════════════════════════════
# 메인 실행
# ═══════════════════════════════════════════════════════════════

def main():
    print(f"\n◆ STEP 5: 유사스타일 맵핑 데이터 생성 ({NEW_SEASON} 프론트엔드용)\n")

    # 1. ML 맵핑 데이터 로드 (Snowflake 3-tier → 엑셀 폴백)
    mapping_file = DEFAULT_MAPPING_FILE
    xlsx_path = DEFAULT_MAPPING_FILE.replace(".csv", ".xlsx")

    # load_mapping_long_xlsx 내부에서 Snowflake → CSV → xlsx 순으로 시도
    print(f"  ▸ 맵핑 데이터 로드 중...")
    try:
        mapping_df = load_mapping_long_xlsx(xlsx_path, NEW_SEASON)
    except Exception as e:
        print(f"  ⚠ 맵핑 데이터 로드 실패: {e}")
        print("    → STEP 5를 건너뜁니다.")
        return

    if mapping_df.empty:
        print("  ⚠ 맵핑 데이터가 비어있습니다. STEP 5를 건너뜁니다.")
        return

    # 2. STEP2/3 분석 결과 확인
    if not os.path.exists(ANALYSIS_RESULT_FILE):
        print(f"  ✗ STEP2/3 분석 결과 파일이 없습니다: {os.path.basename(ANALYSIS_RESULT_FILE)}")
        print("    → STEP 1~4를 먼저 실행해주세요.")
        sys.exit(1)

    # ACC 카테고리 제외 (의류만)
    if "NEW_CLASS2" in mapping_df.columns:
        before = len(mapping_df)
        acc_pattern = r"(?i)Acc|Headwear|Bag|Shoes"
        mapping_df = mapping_df[~mapping_df["NEW_CLASS2"].astype(str).str.contains(acc_pattern, na=False)].copy()
        if before != len(mapping_df):
            print(f"    - 의류 필터링: {before} → {len(mapping_df)} (ACC 제외)")

    print(f"    - {NEW_SEASON} 신규 스타일 수: {len(mapping_df)}")

    # 4. 분석 결과 로드
    style_summary = load_analysis_result()
    season_fallback = load_season_raw_fallback()

    # 5. 맵핑 JSON 생성
    print("  ▸ 맵핑 데이터 생성 중...")
    output = generate_style_mapping_json(mapping_df, style_summary, season_fallback)

    # 6. JSON 저장
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  ▸ JSON 저장 완료: {os.path.basename(OUTPUT_JSON)}")

    # 7. 요약 출력
    meta = output["metadata"]
    print(f"\n  ◆ 결과 요약:")
    print(f"    - 전체 스타일: {meta['total_styles']}")
    print(f"    - 매칭 성공: {meta['matched_styles']}")
    print(f"    - 매칭 불가: {meta['unmatched_styles']}")
    print(f"    - 소스: timeseries {meta['source_breakdown']['timeseries']}, season_raw 폴백 {meta['source_breakdown']['season_raw']}")


if __name__ == "__main__":
    main()
