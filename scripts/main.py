"""
시즌 판매 효율 분석 및 발주 최적화 프로젝트
기획서 기반 자동화 분석 시스템

브랜드/시즌 설정: public/brand_config.json (config_loader)
"""

import math
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple
import matplotlib.pyplot as plt
from config_loader import (
    get_base_season, get_target_season, get_grade_thresholds,
    get_target_sell_through,
    get_input_data_path, get_weekly_data_path, get_analysis_output_path,
)
import matplotlib.font_manager as fm
import seaborn as sns
from openpyxl.drawing.image import Image as XLImage
from openpyxl import load_workbook
import os
import io
import json
from config_loader import get_grade_thresholds


# ============================================
# 1. 데이터 로딩 및 전처리
# ============================================

def load_and_preprocess_data(file_path: str, year_type: str = 'current', df_override=None) -> pd.DataFrame:
    """
    엑셀 파일을 로드하고 전처리하는 함수

    Args:
        file_path: 입력 엑셀 파일 경로
        year_type: 'current'(당해) 또는 'prior'(전년) 선택
        df_override: 지정 시 d1 대신 이 raw DataFrame을 사용 (전년 동주차 스냅샷 등)

    Returns:
        전처리된 데이터프레임
    """
    label = '당해' if year_type == 'current' else '전년'
    print(f"[1단계] 데이터 로딩 중 ({label})")

    # 데이터 로딩 (override 우선 → Snowflake/CSV 3-tier → 엑셀 폴백)
    if df_override is not None:
        df = df_override.copy()
    else:
        try:
            from config_loader import load_data as _load_data
            df = _load_data("d1")
        except Exception:
            df = pd.read_excel(file_path)
    
    # 컬럼명 매핑 (기획서 기준)
    column_mapping = {
        '당해': 'SEASON_GB',
        'PARENT_PRDT_KIND_NM': 'CLASS1',
        'PRDT_KIND_NM': 'CLASS2',
        'ITEM_NM': 'ITEM_NM',
        'PART_CD': 'STYLE_CD',
        'STOR_QTY_KOR': 'IN_QTY',
        'SALE_NML_QTY_CNS': 'SALE_QTY',
        'stock_qty': 'STOCK_QTY'
    }
    
    # 실제 컬럼명 확인 및 매핑
    actual_columns = df.columns.tolist()
    print(f"실제 컬럼명: {actual_columns}")
    
    # 컬럼명 매핑 적용 (존재하는 컬럼만)
    rename_dict = {}
    
    # 다양한 가능한 컬럼명 패턴 매핑
    column_patterns = {
        'SEASON_GB': ['당해', 'SEASON_GB', '시즌구분', '시즌'],
        'CLASS1': ['PARENT_PRDT_KIND_NM', 'CLASS1', '대분류', 'PARENT_PRDT'],
        'CLASS2': ['PRDT_KIND_NM', 'CLASS2', '중분류', 'PRDT_KIND'],
        'ITEM_NM': ['ITEM_NM', '아이템', 'ITEM'],
        'STYLE_CD': ['PART_CD', 'STYLE_CD', '품번', 'PART', 'STYLE'],
        'IN_QTY': ['STOR_QTY_KR', 'STOR_QTY_KOR', 'IN_QTY', '입고수량', '입고'],
        'ORDER_QTY': ['ORDER_QTY_KR', 'ORDER_QTY_KOR', '발주수량', '발주'],
        'SALE_QTY': ['SALE_QTY_CNS', 'SALE_NML_QTY_CNS', 'SALE_QTY', '판매수량', '판매'],
        'TAG_PRICE': ['TAG_PRICE', 'TAG_AMT', '정가', '태그가격'],
        'STOCK_QTY': ['stock_qty', 'STOCK_QTY', '재고수량', '재고', 'STOCK']
    }
    
    # 국내(KR) 컬럼 우선 매핑: 글로벌 컬럼이 있더라도 KR 컬럼으로 덮어쓰기
    # (SALE_QTY가 국내 판매 기준이므로 발주/입고도 국내 기준이어야 함)
    kr_priority = {
        'ORDER_QTY': 'ORDER_QTY_KR',
        'IN_QTY': 'STOR_QTY_KR',
    }
    for standard_name, kr_col in kr_priority.items():
        if kr_col in actual_columns and standard_name in actual_columns:
            df[standard_name] = df[kr_col]
            print(f"[정보] {standard_name}을 {kr_col}(국내 기준)으로 대체합니다.")

    # 각 표준 컬럼명에 대해 가능한 패턴들을 찾아 매핑
    already_mapped_cols = set()  # 이미 rename 대상인 원본 컬럼
    for standard_name, patterns in column_patterns.items():
        # 이미 표준 이름 그대로 존재하면 매핑 불필요
        if standard_name in actual_columns:
            continue
        found = False
        for pattern in patterns:
            for col in actual_columns:
                if col in already_mapped_cols:
                    continue
                if pattern in str(col) or str(col) == pattern:
                    rename_dict[col] = standard_name
                    already_mapped_cols.add(col)
                    found = True
                    break
            if found:
                break

    df = df.rename(columns=rename_dict)
    
    # SEASON_GB가 없는 경우, 첫 번째 컬럼이 시즌 구분일 수도 있으므로 확인
    if 'SEASON_GB' not in df.columns:
        # '당해' 또는 '전년' 값을 가진 컬럼 찾기
        for col in actual_columns:
            if col in df.columns:
                unique_vals = df[col].astype(str).unique()[:5]
                if any('당해' in str(v) or '전년' in str(v) for v in unique_vals):
                    df['SEASON_GB'] = df[col]
                    print(f"[정보] '{col}' 컬럼을 SEASON_GB로 사용합니다.")
                    break
    
    # 시즌 데이터 필터링
    # CSV(Snowflake)는 라벨 정상, Excel은 라벨 반전되어 있음
    is_csv_source = 'SEASON_GB' in df.columns and df.columns[0] != "'당해'"
    if is_csv_source:
        keyword = '당해' if year_type == 'current' else '전년'
    else:
        keyword = '전년' if year_type == 'current' else '당해'
    if 'SEASON_GB' in df.columns:
        before_count = len(df)
        df = df[df['SEASON_GB'].astype(str).str.contains(keyword, na=False)].copy()
        after_count = len(df)
        print(f"[정보] {keyword} 시즌 필터링: {before_count}행 -> {after_count}행")
    else:
        print("[경고] SEASON_GB 컬럼을 찾을 수 없습니다. 전체 데이터를 사용합니다.")
    
    # 결측치 처리
    # SALE_AMT_REAL/SALE_TAG_AMT/STOR_TAG_AMT: KG 정합용 금액 컬럼 (d1 SQL에서 추가, 구캐시엔 없을 수 있어 guard)
    numeric_cols = ['IN_QTY', 'ORDER_QTY', 'SALE_QTY', 'STOCK_QTY',
                    'SALE_AMT_REAL', 'SALE_TAG_AMT', 'STOR_TAG_AMT']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # ORDER_QTY가 없는 경우 IN_QTY를 사용 (하위 호환성)
    if 'ORDER_QTY' not in df.columns and 'IN_QTY' in df.columns:
        df['ORDER_QTY'] = df['IN_QTY']
        print("[정보] ORDER_QTY가 없어 IN_QTY를 ORDER_QTY로 사용합니다.")
    
    # ORDER_QTY가 0인 데이터 제외
    if 'ORDER_QTY' in df.columns:
        df = df[df['ORDER_QTY'] > 0].copy()
    elif 'IN_QTY' in df.columns:
        df = df[df['IN_QTY'] > 0].copy()
    
    # STOCK_QTY = 매장재고 + 창고재고(WH_QTY) 합산
    if 'STOCK_QTY' in df.columns:
        df['STOCK_QTY'] = pd.to_numeric(df['STOCK_QTY'], errors='coerce').fillna(0)
        if 'WH_QTY' in df.columns:
            df['STOCK_QTY'] = df['STOCK_QTY'] + pd.to_numeric(df['WH_QTY'], errors='coerce').fillna(0)
            print(f"[정보] STOCK_QTY = 매장재고 + 창고재고(WH_QTY) 합산")
        df['STOCK_QTY'] = df['STOCK_QTY'].clip(lower=0).astype(int)
    elif 'IN_QTY' in df.columns and 'SALE_QTY' in df.columns:
        df['STOCK_QTY'] = (df['IN_QTY'] - df['SALE_QTY']).clip(lower=0)
        print("[정보] STOCK_QTY를 IN_QTY - SALE_QTY로 계산했습니다.")
    else:
        df['STOCK_QTY'] = 0
        print("[경고] 재고수량 컬럼을 찾을 수 없어 0으로 설정했습니다.")
    
    # CLASS1 = '의류'만 필터링 (ACC 제외)
    if 'CLASS1' in df.columns:
        before = len(df)
        df = df[df['CLASS1'].astype(str).str.contains('의류', na=False)].copy()
        print(f"[정보] 의류(CLASS1) 필터링: {before}행 -> {len(df)}행")

    print(f"전처리 완료: {len(df)}행, {len(df.columns)}컬럼")

    # TAG_PRICE 확인 (season_raw에서 직접 로드)
    if 'TAG_PRICE' in df.columns:
        df['TAG_PRICE'] = pd.to_numeric(df['TAG_PRICE'], errors='coerce').fillna(0).astype(int)
        print(f"[정보] TAG_PRICE: {(df['TAG_PRICE'] > 0).sum()}/{len(df)} 스타일")
    else:
        print("[경고] TAG_PRICE 컬럼이 없습니다. 매출금액 0으로 처리됩니다.")
        df['TAG_PRICE'] = 0

    return df


# ============================================
# 2. Level 1: 전체 시즌 건강도 진단
# ============================================

def analyze_total_season_health(df: pd.DataFrame) -> Dict:
    """
    전체 시즌 건강도를 진단하는 함수
    
    Args:
        df: 전처리된 데이터프레임
        
    Returns:
        전체 시즌 진단 결과 딕셔너리
    """
    print("[2단계] Level 1: 전체 시즌 건강도 진단 중...")
    
    total_in_qty = df['IN_QTY'].sum() if 'IN_QTY' in df.columns else 0
    total_sale_qty = df['SALE_QTY'].sum() if 'SALE_QTY' in df.columns else 0
    total_stock_qty = df['STOCK_QTY'].sum() if 'STOCK_QTY' in df.columns else 0

    # --- KG(지식그래프) 발입출판재 쿼리 정합 지표 (택가/실판매액 기준) ---
    # 실판매액(할인반영), 판매택가/입고택가(정가환산). d1 SQL의 신규 금액 컬럼 합산.
    total_sale_amt_real = df['SALE_AMT_REAL'].sum() if 'SALE_AMT_REAL' in df.columns else 0
    total_sale_tag = df['SALE_TAG_AMT'].sum() if 'SALE_TAG_AMT' in df.columns else 0
    total_stor_tag = df['STOR_TAG_AMT'].sum() if 'STOR_TAG_AMT' in df.columns else 0
    # KG 판매율 = 판매택가 / 입고택가 * 100 (KG 쿼리와 동일하게 정수 반올림)
    sell_through_rate_amt = round(total_sale_tag / total_stor_tag * 100, 0) if total_stor_tag > 0 else 0

    # 판매율 계산
    sell_through_rate = (total_sale_qty / total_in_qty * 100) if total_in_qty > 0 else 0
    
    # 재고 리스크 계산 (재고율)
    stock_risk_rate = (total_stock_qty / total_in_qty * 100) if total_in_qty > 0 else 0
    
    # 목표 판매율 설정 (brand_config.json → targetSellThrough)
    target_rate = get_target_sell_through() * 100
    achievement_status = "달성" if sell_through_rate >= target_rate else "미달성"
    # 택가(KG) 판매율 기준 목표달성 — 판매율 카드가 택가 기준으로 표시되므로 동일 기준 적용
    achievement_status_amt = "달성" if sell_through_rate_amt >= target_rate else "미달성"
    
    # AI 코멘트 생성
    if sell_through_rate >= 75:
        comment = f"🔥 [우수] 전체 판매율 {sell_through_rate:.1f}%, 재고 리스크 {stock_risk_rate:.1f}%로 매우 건전합니다."
    elif sell_through_rate >= 60:
        comment = f"✅ [양호] 전체 판매율 {sell_through_rate:.1f}%, 재고 리스크 {stock_risk_rate:.1f}% 수준입니다."
    elif sell_through_rate >= 40:
        comment = f"⚠️ [주의] 전체 판매율 {sell_through_rate:.1f}%, 재고 리스크 {stock_risk_rate:.1f}%로 재고 관리가 필요합니다."
    else:
        comment = f"🔴 [위험] 전체 판매율 {sell_through_rate:.1f}%, 재고 리스크 {stock_risk_rate:.1f}%로 즉시 조치가 필요합니다."
    
    result = {
        '총입고수량': total_in_qty,
        '총판매수량': total_sale_qty,
        '총재고수량': total_stock_qty,
        '판매율': round(sell_through_rate, 2),
        '재고리스크': round(stock_risk_rate, 2),
        '목표달성여부': achievement_status,
        'AI코멘트': comment,
        # KG 정합 지표 (택가 기준)
        '목표달성여부_택가': achievement_status_amt,
        '실판매액': int(total_sale_amt_real),
        '판매택가': int(total_sale_tag),
        '입고택가': int(total_stor_tag),
        '판매율_택가': float(sell_through_rate_amt),
    }
    
    return result


# ============================================
# 3. Level 2: 복종별 밸런스 분석
# ============================================

def analyze_class_balance(df: pd.DataFrame) -> pd.DataFrame:
    """
    복종(CLASS2)별 밸런스 분석 함수
    
    Args:
        df: 전처리된 데이터프레임
        
    Returns:
        복종별 분석 결과 데이터프레임
    """
    print("[3단계] Level 2: 복종별 밸런스 분석 중...")
    
    if 'CLASS2' not in df.columns:
        print("[경고] CLASS2 컬럼이 없습니다.")
        return pd.DataFrame()
    
    # CLASS2별 집계
    class_summary = df.groupby('CLASS2').agg({
        'IN_QTY': 'sum',
        'SALE_QTY': 'sum',
        'STOCK_QTY': 'sum'
    }).reset_index()

    # 매출금액 집계
    class_summary['SALE_AMT'] = df.groupby('CLASS2').apply(
        lambda g: (g['SALE_QTY'] * g['TAG_PRICE']).sum()
    ).values
    class_summary['IN_AMT'] = df.groupby('CLASS2').apply(
        lambda g: (g['IN_QTY'] * g['TAG_PRICE']).sum()
    ).values
    # 발주금액: ORDER_QTY_KR × TAG_PRICE (한국 발주 기준)
    ord_qty_col = next((c for c in ['ORDER_QTY_KR', 'ORDER_QTY', 'IN_QTY'] if c in df.columns), None)
    if ord_qty_col:
        class_summary['ORD_AMT'] = df.groupby('CLASS2').apply(
            lambda g: int((g[ord_qty_col] * g['TAG_PRICE']).sum())
        ).values
    else:
        class_summary['ORD_AMT'] = 0
    class_summary['AVG_PRICE'] = df.groupby('CLASS2').apply(
        lambda g: int((g['SALE_QTY'] * g['TAG_PRICE']).sum() / g['SALE_QTY'].sum()) if g['SALE_QTY'].sum() > 0 else 0
    ).values

    # 전체 대비 비중 계산
    total_in = class_summary['IN_QTY'].sum()
    total_sale = class_summary['SALE_QTY'].sum()
    
    class_summary['물량비중'] = (class_summary['IN_QTY'] / total_in * 100).round(2)
    class_summary['판매비중'] = (class_summary['SALE_QTY'] / total_sale * 100).round(2)
    class_summary['판매율'] = (class_summary['SALE_QTY'] / class_summary['IN_QTY'] * 100).round(2)
    
    # 밸런스 차이 계산
    class_summary['비중차이'] = class_summary['판매비중'] - class_summary['물량비중']
    
    # 판정 로직 (오차범위 ±5%p)
    def determine_balance(diff):
        if diff > 5.0:
            return "확대필요"
        elif diff < -5.0:
            return "축소필요"
        else:
            return "적정"
    
    class_summary['밸런스판정'] = class_summary['비중차이'].apply(determine_balance)
    
    # AI 코멘트 생성
    def generate_class_comment(row):
        diff = row['비중차이']
        str_rate = row['판매율']
        class_name = row['CLASS2']
        
        comments = []
        
        if diff > 5.0:
            comments.append(f"⭐ {class_name}은(는) 판매 비중({row['판매비중']:.1f}%)이 물량 비중({row['물량비중']:.1f}%)보다 {diff:.1f}%p 높아 효율이 우수합니다.")
            comments.append(f"{get_target_season()} 시즌 물량 비중 확대 검토가 필요합니다.")
        elif diff < -5.0:
            comments.append(f"⚠️ {class_name}은(는) 물량 비중({row['물량비중']:.1f}%)이 판매 비중({row['판매비중']:.1f}%)보다 {-diff:.1f}%p 높아 과도하게 발주되었습니다.")
            comments.append(f"{get_target_season()} 시즌 물량 비중 축소 검토가 필요합니다.")
        else:
            comments.append(f"✅ {class_name}은(는) 물량과 판매 비중이 균형을 이루고 있습니다(차이: {diff:.1f}%p).")
        
        if str_rate >= 75:
            comments.append(f"판매율이 {str_rate:.1f}%로 매우 우수하여 추가 확대 가능성 높습니다.")
        elif str_rate < 40:
            comments.append(f"판매율이 {str_rate:.1f}%로 저조하여 재고 관리에 주의가 필요합니다.")
        
        return " ".join(comments)
    
    class_summary['AI코멘트'] = class_summary.apply(generate_class_comment, axis=1)
    
    # 컬럼 순서 정리
    result_df = class_summary[[
        'CLASS2', '물량비중', '판매비중', '비중차이', '밸런스판정',
        'IN_QTY', 'SALE_QTY', 'STOCK_QTY', 'SALE_AMT', 'IN_AMT', 'ORD_AMT', 'AVG_PRICE',
        '판매율', 'AI코멘트'
    ]].copy()
    
    result_df = result_df.sort_values('비중차이', ascending=False)
    
    return result_df


# ============================================
# 4. Level 3: 아이템별 효율 분석
# ============================================

def analyze_item_efficiency(df: pd.DataFrame) -> pd.DataFrame:
    """
    아이템(ITEM_NM)별 효율 분석 함수 (BCG Matrix 응용)
    
    Args:
        df: 전처리된 데이터프레임
        
    Returns:
        아이템별 분석 결과 데이터프레임
    """
    print("[4단계] Level 3: 아이템별 효율 분석 중...")
    
    if 'ITEM_NM' not in df.columns:
        print("[경고] ITEM_NM 컬럼이 없습니다.")
        return pd.DataFrame()
    
    # ITEM_NM별 집계
    agg_cols = {'IN_QTY': 'sum', 'SALE_QTY': 'sum', 'STOCK_QTY': 'sum'}
    if 'ORDER_QTY' in df.columns:
        agg_cols['ORDER_QTY'] = 'sum'
    item_summary = df.groupby(['CLASS2', 'ITEM_NM']).agg(agg_cols).reset_index()

    # 금액 집계 (TAG_PRICE 기반): 발주금액 = ORDER_QTY_KR × TAG_PRICE (한국 발주 기준)
    if 'TAG_PRICE' in df.columns:
        # 한국 발주수량 우선 (ORDER_QTY_KR > ORDER_QTY > IN_QTY)
        ord_qty_col = next((c for c in ['ORDER_QTY_KR', 'ORDER_QTY', 'IN_QTY'] if c in df.columns), None)
        ord_amt = df.groupby(['CLASS2', 'ITEM_NM']).apply(
            lambda g: int((g[ord_qty_col] * g['TAG_PRICE']).sum()) if ord_qty_col else 0
        ).reset_index(name='ORD_AMT')
        sale_amt = df.groupby(['CLASS2', 'ITEM_NM']).apply(
            lambda g: int((g['SALE_QTY'] * g['TAG_PRICE']).sum())
        ).reset_index(name='SALE_AMT')
        item_summary = item_summary.merge(ord_amt, on=['CLASS2', 'ITEM_NM'], how='left')
        item_summary = item_summary.merge(sale_amt, on=['CLASS2', 'ITEM_NM'], how='left')
        item_summary['ORD_AMT'] = item_summary['ORD_AMT'].fillna(0).astype(int)
        item_summary['SALE_AMT'] = item_summary['SALE_AMT'].fillna(0).astype(int)
    else:
        item_summary['ORD_AMT'] = 0
        item_summary['SALE_AMT'] = 0
    
    # 전체 대비 비중 계산
    total_in = item_summary['IN_QTY'].sum()
    total_sale = item_summary['SALE_QTY'].sum()
    
    item_summary['물량비중'] = (item_summary['IN_QTY'] / total_in * 100).round(2)
    item_summary['판매비중'] = (item_summary['SALE_QTY'] / total_sale * 100).round(2)
    item_summary['판매율'] = (item_summary['SALE_QTY'] / item_summary['IN_QTY'] * 100).round(2)
    
    # BCG Matrix 분류
    # 중앙값 기준으로 분류
    median_str = item_summary['판매율'].median()
    median_volume_share = item_summary['물량비중'].median()
    
    def classify_bcg(row):
        str_rate = row['판매율']
        volume_share = row['물량비중']
        
        if str_rate >= median_str and volume_share >= median_volume_share:
            return "Cash Cow"
        elif str_rate >= median_str and volume_share < median_volume_share:
            return "Star"
        elif str_rate < median_str and volume_share >= median_volume_share:
            return "Problem Child"
        else:
            return "Question Mark"
    
    item_summary['BCG분류'] = item_summary.apply(classify_bcg, axis=1)
    
    # 등급 부여 (의류 기준)
    _thresholds = get_grade_thresholds()
    def assign_grade(str_rate):
        if str_rate >= _thresholds['S']:
            return "S"
        elif str_rate >= _thresholds['A']:
            return "A"
        elif str_rate >= _thresholds['B']:
            return "B"
        elif str_rate >= _thresholds['C']:
            return "C"
        else:
            return "D"

    item_summary['등급'] = item_summary['판매율'].apply(assign_grade)
    
    # AI 코멘트 생성
    def generate_item_comment(row):
        bcg = row['BCG분류']
        str_rate = row['판매율']
        item_name = row['ITEM_NM']
        grade = row['등급']
        
        comments = []
        
        if bcg == "Star":
            comments.append(f"⭐ [Star] {item_name}은(는) 판매율이 높고 물량 비중이 낮아 성장 주도 아이템입니다.")
            comments.append(f"{get_target_season()} 시즌 물량 확대 권장.")
        elif bcg == "Cash Cow":
            comments.append(f"💰 [Cash Cow] {item_name}은(는) 판매율과 물량 비중이 모두 높아 매출 지지 아이템입니다.")
            comments.append(f"현행 유지 또는 소폭 확대 검토.")
        elif bcg == "Problem Child":
            comments.append(f"⚠️ [Problem Child] {item_name}은(는) 판매율이 낮은데 물량 비중이 높아 효율 저하 요인입니다.")
            comments.append(f"{get_target_season()} 시즌 물량 축소 또는 스타일 재검토 필요.")
        else:
            comments.append(f"❓ [Question Mark] {item_name}은(는) 관찰이 필요한 아이템입니다.")
        
        # 등급별 코멘트
        if grade == "S":
            comments.append(f"판매율 {str_rate:.1f}%로 부족 현상이 발생했습니다. 공급 확대 검토 필요.")
        elif grade == "A":
            comments.append(f"판매율 {str_rate:.1f}%로 우수한 성과를 보이고 있습니다.")
        elif grade == "C":
            comments.append(f"판매율 {str_rate:.1f}%로 둔화 추세입니다. 보수적 운영 권장.")
        elif grade == "D":
            comments.append(f"판매율 {str_rate:.1f}%로 위험 수준입니다. 스타일 축소 또는 Drop 검토.")
        
        return " ".join(comments)
    
    item_summary['AI코멘트'] = item_summary.apply(generate_item_comment, axis=1)
    
    # 컬럼 순서 정리
    keep_cols = [
        'CLASS2', 'ITEM_NM', '등급', 'BCG분류', '판매율',
        '물량비중', '판매비중', 'IN_QTY', 'SALE_QTY', 'STOCK_QTY',
        'ORD_AMT', 'SALE_AMT', 'AI코멘트'
    ]
    result_df = item_summary[[c for c in keep_cols if c in item_summary.columns]].copy()
    
    result_df = result_df.sort_values('판매율', ascending=False)
    
    return result_df


# ============================================
# 5. Level 4: 스타일 상세 분석
# ============================================

def analyze_style_detail(df: pd.DataFrame) -> pd.DataFrame:
    """
    스타일(STYLE_CD)별 상세 분석 함수
    
    Args:
        df: 전처리된 데이터프레임
        
    Returns:
        스타일별 분석 결과 데이터프레임
    """
    print("[5단계] Level 4: 스타일 상세 분석 중...")
    
    if 'STYLE_CD' not in df.columns:
        print("[경고] STYLE_CD 컬럼이 없습니다.")
        return pd.DataFrame()
    
    # 스타일별 집계 (CLASS1, CLASS2, ITEM_NM 포함)
    # 발주수량 기준으로 집계
    agg_dict = {
        'SALE_QTY': 'sum',
        'STOCK_QTY': 'sum'
    }
    
    # ORDER_QTY 또는 IN_QTY 사용
    if 'ORDER_QTY' in df.columns:
        agg_dict['ORDER_QTY'] = 'sum'
    else:
        agg_dict['IN_QTY'] = 'sum'
    
    style_df = df.groupby(['CLASS1', 'CLASS2', 'ITEM_NM', 'STYLE_CD']).agg(agg_dict).reset_index()
    
    # 판매율 계산 (발주수량 대비 판매)
    if 'ORDER_QTY' in style_df.columns:
        style_df['판매율'] = (style_df['SALE_QTY'] / style_df['ORDER_QTY'] * 100).round(2)
        style_df['발주수량'] = style_df['ORDER_QTY']  # 컬럼명 통일
        style_df = style_df.drop(columns=['ORDER_QTY'], errors='ignore')  # 원본 컬럼 삭제
    else:
        style_df['판매율'] = (style_df['SALE_QTY'] / style_df['IN_QTY'] * 100).round(2)
        style_df['발주수량'] = style_df['IN_QTY']  # 하위 호환성
        style_df = style_df.drop(columns=['IN_QTY'], errors='ignore')  # 원본 컬럼 삭제
    
    # 등급 부여
    _thresholds_s = get_grade_thresholds()
    def assign_grade(str_rate):
        if str_rate >= _thresholds_s['S']:
            return "S"
        elif str_rate >= _thresholds_s['A']:
            return "A"
        elif str_rate >= _thresholds_s['B']:
            return "B"
        elif str_rate >= _thresholds_s['C']:
            return "C"
        else:
            return "D"

    style_df['등급'] = style_df['판매율'].apply(assign_grade)
    
    # 액션 가이드 생성
    def determine_action(grade):
        if grade == "S":
            return "Aggressive"
        elif grade == "A":
            return "Expand"
        elif grade == "B":
            return "Maintain"
        elif grade == "C":
            return "Observation"
        else:
            return "Cut/Drop"
    
    style_df['액션'] = style_df['등급'].apply(determine_action)
    
    # AI 코멘트 생성
    style_df['AI코멘트'] = style_df.apply(generate_style_ai_comment, axis=1)
    
    # 발주수량 컬럼명 통일 (ORDER_QTY, IN_QTY 중 하나가 '발주수량'으로 이미 설정됨)
    # 컬럼명을 한글로 변경 (엑셀 시트 표시용)
    column_rename = {
        'CLASS1': '대분류',
        'CLASS2': '중분류',
        'ITEM_NM': '아이템명',
        'STYLE_CD': '스타일코드'
    }
    
    # 숫자 컬럼명 변경 (존재하는 것만)
    if 'SALE_QTY' in style_df.columns:
        column_rename['SALE_QTY'] = '판매수량'
    if 'STOCK_QTY' in style_df.columns:
        column_rename['STOCK_QTY'] = '재고수량'
    if 'ORDER_QTY' in style_df.columns and '발주수량' not in style_df.columns:
        column_rename['ORDER_QTY'] = '발주수량'
    elif 'IN_QTY' in style_df.columns and '발주수량' not in style_df.columns:
        column_rename['IN_QTY'] = '발주수량'
    
    # 컬럼명 변경
    result_df = style_df.rename(columns=column_rename)
    
    # 컬럼 순서 정리 (발주수량 기준)
    result_columns = ['대분류', '중분류', '아이템명', '스타일코드', '등급', '액션', '발주수량', '판매수량', '재고수량', '판매율', 'AI코멘트']
    # 존재하는 컬럼만 선택
    available_columns = [col for col in result_columns if col in result_df.columns]
    result_df = result_df[available_columns].copy()
    
    result_df = result_df.sort_values('판매율', ascending=False)
    
    return result_df


# ============================================
# 6. AI 코멘트 생성 함수 (스타일별)
# ============================================

def generate_style_ai_comment(row: pd.Series) -> str:
    """
    스타일별 AI 코멘트 생성 함수 (기획서의 generate_ai_comment 로직 구현)
    
    Args:
        row: 스타일 데이터 행
        
    Returns:
        AI 코멘트 문자열
    """
    category = row.get('CLASS1', '의류')
    item_name = row.get('ITEM_NM', '')
    str_rate = row.get('판매율', 0)
    sale_qty = row.get('SALE_QTY', 0)
    order_qty = row.get('발주수량', row.get('ORDER_QTY', row.get('IN_QTY', 0)))  # 발주수량 우선, 없으면 IN_QTY
    stock_qty = row.get('STOCK_QTY', 0)
    
    # 전체 대비 비중 계산 (필요시)
    # 실제로는 전체 데이터 필요하지만, 여기서는 대략적인 판단만 수행
    
    comments = []
    
    # [Logic 1] 복종별 효율 진단 분기
    if category == '용품' or '용품' in str(category):
        # 용품의 경우 재고주수(WOS) 기준
        # 주평균 판매량 계산 (시즌 16주 가정)
        weekly_avg_sale = sale_qty / 16 if sale_qty > 0 else 0.01
        wos = stock_qty / weekly_avg_sale if weekly_avg_sale > 0 else 0
        
        # 적정 WOS 결정
        if '모자' in item_name:
            target_wos = 6
        elif '가방' in item_name:
            target_wos = 10
        elif '신발' in item_name:
            target_wos = 12
        else:
            target_wos = 8  # 기본값
        
        if wos > 0 and wos < target_wos * 0.8:
            comments.append(f"🚨 [재고부족] 현재 재고가 {wos:.1f}주 분량뿐입니다 (적정 {target_wos}주). 긴급 리오더가 필요합니다.")
        elif wos > target_wos * 1.3:
            comments.append(f"📦 [재고과다] 재고 소진까지 {wos:.1f}주가 소요될 예상입니다. 프로모션이 시급합니다.")
        else:
            comments.append(f"✅ 재고주수 {wos:.1f}주로 적정 수준입니다.")
    
    else:  # 의류
        if str_rate > 75:
            comments.append(f"🔥 [물량부족] 판매율({str_rate:.1f}%)이 폭발적입니다. 조기 품절로 인한 기회비용 발생 중입니다.")
        elif str_rate >= 65:
            comments.append(f"⭐ [베스트] 판매율({str_rate:.1f}%)이 우수합니다. 핵심 상품군으로 육성 필요합니다.")
        elif str_rate >= 55:
            comments.append(f"✅ [정상] 판매율({str_rate:.1f}%)이 적정 수준입니다. 현행 유지가 가능합니다.")
        elif str_rate >= 40:
            comments.append(f"🟡 [둔화] 판매율({str_rate:.1f}%)이 둔화 추세입니다. 반응 생산 전환 및 보수적 운영 권장.")
        else:
            comments.append(f"📉 [재고위험] 판매율({str_rate:.1f}%)이 매우 저조합니다. 과감한 스타일 Drop이 필요합니다.")
    
    # [Logic 2] 스타일 유형별 판단
    # 볼륨 드라이버: 판매율 60% 전후이지만 판매수량이 매우 많은 경우
    total_sale_for_comparison = sale_qty  # 실제로는 전체 평균과 비교해야 함
    
    if 55 <= str_rate <= 65 and sale_qty >= 500:  # 임계값은 데이터에 맞게 조정 필요
        comments.append("📊 [볼륨 드라이버] 판매율은 보통이나 판매수량이 많아 매출 방어용 기본물로 유지 필요합니다.")
    
    # 히트 아이템: 판매율 80% 이상
    if str_rate >= 80:
        comments.append("🔥 [히트 아이템] 조기 소진된 스타일입니다. 스타일 수평 전개(Color/Graphic 추가) 권장합니다.")
    
    # 룩킹 제안용: 판매량은 적지만 고단가 (임계값 조정 필요)
    if sale_qty < 50 and order_qty < 100 and str_rate >= 40:
        comments.append("👔 [VMD] 판매량은 적지만 구색상 필요한 아이템입니다. 최소 진열 수량(Min-Display)만 운영 권장합니다.")
    
    # [Logic 3] 등급별 액션 가이드
    grade = row.get('등급', 'B')
    action = row.get('액션', 'Maintain')
    
    if action == "Aggressive":
        comments.append(f"💪 [{get_target_season()} 가이드] 물량 30% 이상 확대 검토가 필요합니다.")
    elif action == "Expand":
        comments.append(f"📈 [{get_target_season()} 가이드] 핵심 상품군으로 육성하여 물량 확대 검토.")
    elif action == "Maintain":
        comments.append(f"🔄 [{get_target_season()} 가이드] 현행 유지.")
    elif action == "Observation":
        comments.append(f"👀 [{get_target_season()} 가이드] 반응 생산 전환, 보수적 운영.")
    elif action == "Cut/Drop":
        comments.append(f"✂️ [{get_target_season()} 가이드] 스타일 축소 및 디자인 재검토 필요.")
    
    return " ".join(comments) if comments else "현행 유지 (특이사항 없음)"


# ============================================
# 7. 시각화 함수들
# ============================================

def create_bcg_matrix(item_analysis: pd.DataFrame, output_dir: str = 'temp_charts') -> str:
    """
    아이템별 BCG 매트릭스 포지셔닝 맵 생성
    
    Args:
        item_analysis: 아이템별 분석 결과 데이터프레임
        output_dir: 이미지 저장 디렉토리
        
    Returns:
        생성된 이미지 파일 경로
    """
    if item_analysis.empty:
        return None
    
    # 한글 폰트 설정
    try:
        plt.rcParams['font.family'] = 'Malgun Gothic'  # Windows
    except:
        try:
            plt.rcParams['font.family'] = 'NanumGothic'  # 대체 폰트
        except:
            plt.rcParams['font.family'] = 'DejaVu Sans'  # 영문 폰트
    plt.rcParams['axes.unicode_minus'] = False
    
    os.makedirs(output_dir, exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(16, 12))
    
    # 데이터 범위 계산
    min_volume = item_analysis['물량비중'].min()
    max_volume = item_analysis['물량비중'].max()
    min_str = item_analysis['판매율'].min()
    max_str = item_analysis['판매율'].max()
    
    # 여유 공간 추가 (10%)
    volume_range = max_volume - min_volume
    str_range = max_str - min_str
    x_margin = volume_range * 0.1
    y_margin = str_range * 0.1
    
    # X축과 Y축 범위를 동일하게 맞추기 위해 더 넓은 범위 사용
    x_min = max(0, min_volume - x_margin)
    x_max = max_volume + x_margin
    y_min = max(0, min_str - y_margin)
    y_max = max_str + y_margin
    
    # 4분면을 동일한 크기로 만들기 위해 중앙값을 실제 데이터 중앙값으로 계산하되,
    # 축 범위의 절반 지점을 기준선으로 사용
    x_center = (x_min + x_max) / 2
    y_center = (y_min + y_max) / 2
    
    # 중앙값 기준선 (고정된 위치)
    ax.axvline(x=x_center, color='gray', linestyle='--', linewidth=2.5, alpha=0.6, label='중앙값 기준선')
    ax.axhline(y=y_center, color='gray', linestyle='--', linewidth=2.5, alpha=0.6)
    
    # 4분면 배경색 (연하게)
    quadrant_colors = {
        'top_left': '#E8F4FD',      # Question Mark - 하늘색
        'top_right': '#FFF9E6',     # Star - 노란색
        'bottom_left': '#FFE6E6',   # Dog - 연한 빨강
        'bottom_right': '#E6FFE6'   # Cash Cow - 연한 초록
    }
    
    # 각 분면에 배경색 칠하기 (Rectangle 사용)
    from matplotlib.patches import Rectangle
    
    # Bottom Left (Problem Child)
    rect1 = Rectangle((x_min, y_min), x_center - x_min, y_center - y_min, 
                     facecolor=quadrant_colors['bottom_left'], alpha=0.15, zorder=0)
    ax.add_patch(rect1)
    
    # Bottom Right (Cash Cow)
    rect2 = Rectangle((x_center, y_min), x_max - x_center, y_center - y_min, 
                     facecolor=quadrant_colors['bottom_right'], alpha=0.15, zorder=0)
    ax.add_patch(rect2)
    
    # Top Left (Question Mark)
    rect3 = Rectangle((x_min, y_center), x_center - x_min, y_max - y_center, 
                     facecolor=quadrant_colors['top_left'], alpha=0.15, zorder=0)
    ax.add_patch(rect3)
    
    # Top Right (Star)
    rect4 = Rectangle((x_center, y_center), x_max - x_center, y_max - y_center, 
                     facecolor=quadrant_colors['top_right'], alpha=0.15, zorder=0)
    ax.add_patch(rect4)
    
    # BCG 분류별 색상 매핑
    color_map = {
        'Star': '#FFD700',  # 금색
        'Cash Cow': '#32CD32',  # 연두색
        'Problem Child': '#FF6B6B',  # 연한 빨강
        'Question Mark': '#87CEEB'  # 하늘색
    }
    
    # 분류별로 그룹화하여 플롯
    for bcg_type in ['Star', 'Cash Cow', 'Problem Child', 'Question Mark']:
        mask = item_analysis['BCG분류'] == bcg_type
        data = item_analysis[mask]
        
        if not data.empty:
            scatter = ax.scatter(
                data['물량비중'],
                data['판매율'],
                s=data['판매비중'] * 50,  # 크기는 판매비중에 비례
                alpha=0.7,
                c=color_map.get(bcg_type, '#808080'),
                edgecolors='black',
                linewidths=1.5,
                label=bcg_type,
                zorder=5
            )
            
            # 아이템명 라벨 추가 (상위 8개만)
            top_items = data.nlargest(8, '판매비중')
            for idx, row in top_items.iterrows():
                ax.annotate(
                    row['ITEM_NM'][:10],  # 이름이 너무 길면 잘라냄
                    (row['물량비중'], row['판매율']),
                    fontsize=9,
                    alpha=0.9,
                    ha='center',
                    va='bottom',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.7, edgecolor='gray', linewidth=0.5),
                    zorder=6
                )
    
    # 각 분면의 왼쪽 위에 라벨과 코멘트를 함께 배치
    # 여유 공간 계산 (범위의 5%)
    x_margin_comment = (x_max - x_min) * 0.05
    y_margin_comment = (y_max - y_min) * 0.05
    
    quadrant_info = {
        'Question Mark': {
            'x': x_min + x_margin_comment,  # 1사분면 왼쪽 위
            'y': y_max - y_margin_comment,
            'label': 'Question Mark (잠재 성장주)',
            'comment': '높은 판매율을 보이지만 아직 매출 비중이 낮아 전략적 판단 필요',
            'color': '#0066CC',
            'bgcolor': '#E8F4FD'
        },
        'Star': {
            'x': x_center + x_margin_comment,  # 2사분면 왼쪽 위 (오른쪽 분면 내에서 왼쪽)
            'y': y_max - y_margin_comment,
            'label': 'Star (핵심 성장동력)',
            'comment': '높은 매출비중과 높은 판매율을 기록한 효자상품',
            'color': '#CC9900',
            'bgcolor': '#FFF9E6'
        },
        'Problem Child': {
            'x': x_min + x_margin_comment,  # 3사분면 왼쪽 아래
            'y': y_min + y_margin_comment,  # 하단 분면 내에서 아래쪽
            'label': 'Problem Child (저효율군)',
            'comment': '낮은 매출 비중과 낮은 판매율율로 개선 또는 정리 검토 필요',
            'color': '#CC0000',
            'bgcolor': '#FFE6E6'
        },
        'Cash Cow': {
            'x': x_center + x_margin_comment,  # 4사분면 왼쪽 아래 (오른쪽 분면 내에서 왼쪽)
            'y': y_min + y_margin_comment,  # 하단 분면 내에서 아래쪽
            'label': 'Cash Cow (안정 수익원)',
            'comment': '높은 비중으로 안정적인 수익을 창출하지만 성장은 둔화',
            'color': '#006600',
            'bgcolor': '#E6FFE6'
        }
    }
    
    # 분면 라벨과 코멘트 추가
    for quadrant_name, quadrant_data in quadrant_info.items():
        # 상단 분면(1, 2사분면)은 왼쪽 위, 하단 분면(3, 4사분면)은 왼쪽 아래
        is_upper = quadrant_name in ['Question Mark', 'Star']
        
        # 라벨 (큰 폰트, bold, 색상과 테두리 없이)
        ax.text(
            quadrant_data['x'],
            quadrant_data['y'],
            quadrant_data['label'],
            ha='left',  # 왼쪽 정렬
            va='top' if is_upper else 'bottom',  # 상단은 위, 하단은 아래
            fontsize=20,
            fontweight='bold',
            color='black',
            zorder=10
        )
        
        # 코멘트 (라벨 바로 아래/위, 작은 폰트, 한 줄, 박스 없이 텍스트만)
        # 라벨과 코멘트 사이 간격 계산 (데이터 좌표계 기준)
        # 폰트 크기와 패딩을 고려한 간격: 대략 (y_max - y_min) * 0.04 정도
        spacing = (y_max - y_min) * 0.04
        if is_upper:
            comment_y = quadrant_data['y'] - spacing  # 상단: 라벨 아래쪽
        else:
            comment_y = quadrant_data['y'] + spacing  # 하단: 라벨 위쪽
        
        ax.text(
            quadrant_data['x'],
            comment_y,
            quadrant_data['comment'],
            ha='left',  # 왼쪽 정렬
            va='top' if is_upper else 'bottom',  # 상단은 위, 하단은 아래
            fontsize=11,
            fontweight='normal',
            color=quadrant_data['color'],
            zorder=10
        )
    
    # 축 설정
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_xlabel('물량 비중 (%)', fontsize=14, fontweight='bold')
    ax.set_ylabel('판매율 (%)', fontsize=14, fontweight='bold')
    ax.set_title('아이템별 BCG 매트릭스 포지셔닝 맵\n(버블 크기 = 판매 비중)', fontsize=16, fontweight='bold', pad=25)
    ax.legend(loc='upper right', fontsize=20, framealpha=0.9)
    ax.grid(True, alpha=0.3, linestyle='-', linewidth=0.5)
    
    plt.tight_layout()
    
    file_path = os.path.join(output_dir, 'bcg_matrix.png')
    plt.savefig(file_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    return file_path


def create_class_balance_chart(class_analysis: pd.DataFrame, output_dir: str = 'temp_charts') -> str:
    """
    복종별 밸런스 차이 바 차트 생성
    
    Args:
        class_analysis: 복종별 분석 결과 데이터프레임
        output_dir: 이미지 저장 디렉토리
        
    Returns:
        생성된 이미지 파일 경로
    """
    if class_analysis.empty:
        return None
    
    plt.rcParams['font.family'] = 'Malgun Gothic'
    plt.rcParams['axes.unicode_minus'] = False
    
    os.makedirs(output_dir, exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(12, 8))
    
    # 정렬 (비중차이 기준)
    data = class_analysis.sort_values('비중차이', ascending=True).copy()
    
    # 색상 매핑
    colors = []
    for val in data['비중차이']:
        if val > 5.0:
            colors.append('#FF6B6B')  # 빨강 (확대 필요)
        elif val < -5.0:
            colors.append('#4ECDC4')  # 청록 (축소 필요)
        else:
            colors.append('#95E1D3')  # 연두 (적정)
    
    bars = ax.barh(data['CLASS2'], data['비중차이'], color=colors, edgecolor='black', linewidth=1.5)
    
    # 0선 표시
    ax.axvline(x=0, color='black', linewidth=2)
    
    # ±5%p 기준선 표시
    ax.axvline(x=5, color='orange', linestyle='--', linewidth=1.5, alpha=0.7, label='확대 기준선 (+5%p)')
    ax.axvline(x=-5, color='orange', linestyle='--', linewidth=1.5, alpha=0.7, label='축소 기준선 (-5%p)')
    
    # 값 라벨 추가
    for i, (idx, row) in enumerate(data.iterrows()):
        value = row['비중차이']
        ax.text(value + (0.5 if value >= 0 else -0.5), i, 
                f'{value:+.1f}%p', 
                va='center', ha='left' if value >= 0 else 'right',
                fontweight='bold', fontsize=10)
    
    ax.set_xlabel('비중 차이 (판매 비중 - 물량 비중, %p)', fontsize=12, fontweight='bold')
    ax.set_title('복종별 포트폴리오 밸런스 분석', fontsize=14, fontweight='bold', pad=20)
    ax.legend(loc='lower right', fontsize=10)
    ax.grid(True, alpha=0.3, axis='x')
    
    plt.tight_layout()
    
    file_path = os.path.join(output_dir, 'class_balance.png')
    plt.savefig(file_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    return file_path


def create_sell_through_distribution(style_analysis: pd.DataFrame, output_dir: str = 'temp_charts') -> str:
    """
    판매율 등급별 분포 차트 생성
    
    Args:
        style_analysis: 스타일별 분석 결과 데이터프레임
        output_dir: 이미지 저장 디렉토리
        
    Returns:
        생성된 이미지 파일 경로
    """
    if style_analysis.empty:
        return None
    
    plt.rcParams['font.family'] = 'Malgun Gothic'
    plt.rcParams['axes.unicode_minus'] = False
    
    os.makedirs(output_dir, exist_ok=True)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    
    # 1. 등급별 개수 바 차트
    grade_counts = style_analysis['등급'].value_counts().sort_index()
    grade_order = ['S', 'A', 'B', 'C', 'D']
    grade_counts = grade_counts.reindex([g for g in grade_order if g in grade_counts.index])
    
    grade_colors = {'S': '#FF0000', 'A': '#FFA500', 'B': '#32CD32', 'C': '#FFD700', 'D': '#808080'}
    colors = [grade_colors.get(g, '#808080') for g in grade_counts.index]
    
    bars1 = ax1.bar(grade_counts.index, grade_counts.values, color=colors, edgecolor='black', linewidth=1.5)
    ax1.set_xlabel('등급', fontsize=12, fontweight='bold')
    ax1.set_ylabel('스타일 개수', fontsize=12, fontweight='bold')
    ax1.set_title('등급별 스타일 분포', fontsize=13, fontweight='bold')
    ax1.grid(True, alpha=0.3, axis='y')
    
    # 값 라벨 추가
    for bar in bars1:
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height,
                f'{int(height)}',
                ha='center', va='bottom', fontweight='bold')
    
    # 2. 판매율 히스토그램
    ax2.hist(style_analysis['판매율'], bins=30, color='skyblue', edgecolor='black', alpha=0.7)
    ax2.axvline(style_analysis['판매율'].mean(), color='red', linestyle='--', linewidth=2, label=f'평균: {style_analysis["판매율"].mean():.1f}%')
    ax2.set_xlabel('판매율 (%)', fontsize=12, fontweight='bold')
    ax2.set_ylabel('빈도', fontsize=12, fontweight='bold')
    ax2.set_title('판매율 분포', fontsize=13, fontweight='bold')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    file_path = os.path.join(output_dir, 'sell_through_distribution.png')
    plt.savefig(file_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    return file_path


def create_style_scatter(style_analysis: pd.DataFrame, output_dir: str = 'temp_charts') -> str:
    """
    스타일별 판매율 vs 발주수량 산점도 생성
    
    Args:
        style_analysis: 스타일별 분석 결과 데이터프레임
        output_dir: 이미지 저장 디렉토리
        
    Returns:
        생성된 이미지 파일 경로
    """
    if style_analysis.empty:
        return None
    
    plt.rcParams['font.family'] = 'Malgun Gothic'
    plt.rcParams['axes.unicode_minus'] = False
    
    os.makedirs(output_dir, exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(14, 10))
    
    # 발주수량 컬럼 확인 (발주수량, ORDER_QTY, IN_QTY 순으로 확인)
    qty_col = None
    for col in ['발주수량', 'ORDER_QTY', 'IN_QTY']:
        if col in style_analysis.columns:
            qty_col = col
            break
    
    if qty_col is None:
        print("[경고] 발주수량 컬럼을 찾을 수 없습니다.")
        return None
    
    # 등급별 색상 매핑
    grade_colors = {
        'S': '#FF0000',  # 빨강
        'A': '#FFA500',  # 주황
        'B': '#32CD32',  # 초록
        'C': '#FFD700',  # 금색
        'D': '#808080'   # 회색
    }
    
    # 등급별로 그룹화하여 플롯
    for grade in ['S', 'A', 'B', 'C', 'D']:
        mask = style_analysis['등급'] == grade
        data = style_analysis[mask]
        
        if not data.empty:
            ax.scatter(
                data[qty_col],
                data['판매율'],
                s=100,
                alpha=0.6,
                c=grade_colors.get(grade, '#808080'),
                edgecolors='black',
                linewidths=1,
                label=f'등급 {grade}'
            )
    
    # 판매율 기준선 표시
    _t = get_grade_thresholds()
    ax.axhline(y=_t['S'], color='red', linestyle='--', linewidth=1.5, alpha=0.5, label=f'S등급 기준 ({_t["S"]}%)')
    ax.axhline(y=_t['A'], color='orange', linestyle='--', linewidth=1.5, alpha=0.5, label=f'A등급 기준 ({_t["A"]}%)')
    ax.axhline(y=_t['B'], color='green', linestyle='--', linewidth=1.5, alpha=0.5, label=f'B등급 기준 ({_t["B"]}%)')
    ax.axhline(y=_t['C'], color='yellow', linestyle='--', linewidth=1.5, alpha=0.5, label=f'C등급 기준 ({_t["C"]}%)')
    
    ax.set_xlabel('발주수량', fontsize=12, fontweight='bold')
    ax.set_ylabel('판매율 (%)', fontsize=12, fontweight='bold')
    ax.set_title('스타일별 판매율 vs 발주수량 산점도', fontsize=14, fontweight='bold', pad=20)
    ax.legend(loc='upper right', fontsize=10, ncol=2)
    ax.grid(True, alpha=0.3)
    ax.set_xscale('log')  # 발주수량이 크게 차이나므로 로그 스케일 적용
    
    plt.tight_layout()
    
    file_path = os.path.join(output_dir, 'style_scatter.png')
    plt.savefig(file_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    return file_path


def create_class_portfolio_pie(class_analysis: pd.DataFrame, output_dir: str = 'temp_charts') -> str:
    """
    복종별 포트폴리오 비중 파이 차트 생성
    
    Args:
        class_analysis: 복종별 분석 결과 데이터프레임
        output_dir: 이미지 저장 디렉토리
        
    Returns:
        생성된 이미지 파일 경로
    """
    if class_analysis.empty:
        return None
    
    plt.rcParams['font.family'] = 'Malgun Gothic'
    plt.rcParams['axes.unicode_minus'] = False
    
    os.makedirs(output_dir, exist_ok=True)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
    
    # 파이 차트 색상 설정
    colors = plt.cm.Set3(range(len(class_analysis)))
    
    # 1. 물량 비중 파이 차트
    ax1.pie(class_analysis['물량비중'], labels=class_analysis['CLASS2'], autopct='%1.1f%%',
            startangle=90, colors=colors, textprops={'fontsize': 10})
    ax1.set_title('복종별 물량 비중', fontsize=13, fontweight='bold', pad=20)
    
    # 2. 판매 비중 파이 차트
    ax2.pie(class_analysis['판매비중'], labels=class_analysis['CLASS2'], autopct='%1.1f%%',
            startangle=90, colors=colors, textprops={'fontsize': 10})
    ax2.set_title('복종별 판매 비중', fontsize=13, fontweight='bold', pad=20)
    
    plt.tight_layout()
    
    file_path = os.path.join(output_dir, 'class_portfolio.png')
    plt.savefig(file_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    return file_path


def insert_image_to_excel(excel_path: str, image_path: str, sheet_name: str, cell_address: str):
    """
    엑셀 시트에 이미지 삽입
    
    Args:
        excel_path: 엑셀 파일 경로
        image_path: 삽입할 이미지 파일 경로
        sheet_name: 시트 이름
        cell_address: 삽입할 셀 주소 (예: 'A10')
    """
    if not os.path.exists(image_path):
        return
    
    try:
        wb = load_workbook(excel_path)
        ws = wb[sheet_name]
        
        img = XLImage(image_path)
        img.width = min(img.width, 1200)  # 최대 너비 제한
        img.height = min(img.height, 800)  # 최대 높이 제한
        
        ws.add_image(img, cell_address)
        wb.save(excel_path)
        wb.close()
    except Exception as e:
        print(f"[경고] 이미지 삽입 실패 ({image_path}): {str(e)}")


# ============================================
# 8. 결과 엑셀 파일 생성 (시각화 포함)
# ============================================

def create_result_excel(
    total_health: Dict,
    class_analysis: pd.DataFrame,
    item_analysis: pd.DataFrame,
    style_analysis: pd.DataFrame,
    output_path: str
) -> None:
    """
    분석 결과를 엑셀 파일로 생성하는 함수
    
    Args:
        total_health: 전체 시즌 건강도 진단 결과
        class_analysis: 복종별 분석 결과
        item_analysis: 아이템별 분석 결과
        style_analysis: 스타일별 분석 결과
        output_path: 출력 파일 경로
    """
    print(f"[6단계] 결과 엑셀 파일 생성 중: {output_path}")
    
    # 엑셀 파일 작성 (with 블록 내에서 자동으로 저장되고 닫힘)
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # 1. Summary 시트
        summary_data = {
            '지표': ['총입고수량', '총판매수량', '총재고수량', '판매율(%)', '재고리스크(%)', '목표달성여부'],
            '값': [
                total_health['총입고수량'],
                total_health['총판매수량'],
                total_health['총재고수량'],
                total_health['판매율'],
                total_health['재고리스크'],
                total_health['목표달성여부']
            ]
        }
        summary_df = pd.DataFrame(summary_data)
        
        # AI 코멘트 추가
        comment_df = pd.DataFrame({
            '지표': ['AI 종합 코멘트'],
            '값': [total_health['AI코멘트']]
        })
        
        # Summary 시트 구성 (지표 + 코멘트)
        summary_sheet = pd.concat([summary_df, comment_df], ignore_index=True)
        summary_sheet.to_excel(writer, sheet_name='Summary', index=False)
        
        # 2. Class_Analysis 시트
        if not class_analysis.empty:
            class_analysis.to_excel(writer, sheet_name='Class_Analysis', index=False)
        else:
            pd.DataFrame({'메시지': ['데이터가 없습니다.']}).to_excel(
                writer, sheet_name='Class_Analysis', index=False
            )
        
        # 3. Item_Analysis 시트
        if not item_analysis.empty:
            item_analysis.to_excel(writer, sheet_name='Item_Analysis', index=False)
        else:
            pd.DataFrame({'메시지': ['데이터가 없습니다.']}).to_excel(
                writer, sheet_name='Item_Analysis', index=False
            )
        
        # 4. Style_Action_Plan 시트
        if not style_analysis.empty:
            style_analysis.to_excel(writer, sheet_name='Style_Action_Plan', index=False)
        else:
            pd.DataFrame({'메시지': ['데이터가 없습니다.']}).to_excel(
                writer, sheet_name='Style_Action_Plan', index=False
            )
    
    # with 블록이 끝나면 파일이 자동으로 저장되고 닫힘
    # 이제 파일을 다시 열어서 이미지를 삽입할 수 있음
    
    # 시각화 생성 및 삽입
    print("[7단계] 시각화 생성 중...")
    temp_dir = 'temp_charts'
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # 1. BCG 매트릭스 (Item_Analysis 시트에 삽입)
        if not item_analysis.empty:
            bcg_image = create_bcg_matrix(item_analysis, temp_dir)
            if bcg_image:
                insert_image_to_excel(output_path, bcg_image, 'Item_Analysis', 'M2')
                print("  * BCG 매트릭스 생성 완료")
        
        # 2. 복종별 밸런스 차트 (Class_Analysis 시트에 삽입)
        if not class_analysis.empty:
            balance_image = create_class_balance_chart(class_analysis, temp_dir)
            if balance_image:
                insert_image_to_excel(output_path, balance_image, 'Class_Analysis', 'K2')
                print("  * 복종별 밸런스 차트 생성 완료")
            
            # 3. 복종별 포트폴리오 파이 차트 (Class_Analysis 시트에 삽입)
            pie_image = create_class_portfolio_pie(class_analysis, temp_dir)
            if pie_image:
                insert_image_to_excel(output_path, pie_image, 'Class_Analysis', 'K20')
                print("  * 복종별 포트폴리오 파이 차트 생성 완료")
        
        # 4. 판매율 분포 차트 (Summary 시트에 삽입)
        if not style_analysis.empty:
            dist_image = create_sell_through_distribution(style_analysis, temp_dir)
            if dist_image:
                insert_image_to_excel(output_path, dist_image, 'Summary', 'D10')
                print("  * 판매율 분포 차트 생성 완료")
            
            # 5. 스타일 산점도 (Style_Action_Plan 시트에 삽입)
            scatter_image = create_style_scatter(style_analysis, temp_dir)
            if scatter_image:
                insert_image_to_excel(output_path, scatter_image, 'Style_Action_Plan', 'M2')
                print("  * 스타일 산점도 생성 완료")
        
        # 임시 이미지 파일 정리
        import shutil
        try:
            shutil.rmtree(temp_dir)
            print("  * 임시 파일 정리 완료")
        except:
            pass
    
    except Exception as e:
        print(f"[경고] 시각화 생성 중 오류: {str(e)}")
        import traceback
        traceback.print_exc()
    
    print(f"* 분석 완료! 결과 파일: {output_path}")


# ============================================
# 9. JSON 출력 (프론트엔드용)
# ============================================

def calculate_yoy_delta(current_val, prior_val):
    """퍼센트포인트 증감 계산 (판매율 등 비율 지표용)"""
    if prior_val is None or current_val is None:
        return None
    return round(current_val - prior_val, 2)


def calculate_yoy_growth(current_val, prior_val):
    """증감률(%) 계산 (수량/금액 지표용)"""
    if prior_val is None or prior_val == 0:
        return None
    return round((current_val - prior_val) / prior_val * 100, 1)


def build_prior_year_data(health_prior, class_prior, item_prior):
    """전년 분석 결과를 JSON용 dict로 변환"""
    prior_class_list = []
    if not class_prior.empty:
        for _, row in class_prior.iterrows():
            prior_class_list.append({
                "class2": str(row.get("CLASS2", "")),
                "in_qty": int(row.get("IN_QTY", 0)),
                "sale_qty": int(row.get("SALE_QTY", 0)),
                "stock_qty": int(row.get("STOCK_QTY", 0)),
                "sale_amt": int(row.get("SALE_AMT", 0)),
                "in_amt": int(row.get("IN_AMT", 0)),
                "ord_amt": int(row.get("ORD_AMT", 0)),
                "sell_through_rate": float(row.get("판매율", 0)),
            })

    prior_item_list = []
    if not item_prior.empty:
        for _, row in item_prior.iterrows():
            if int(row.get("IN_QTY", 0)) == 0:
                continue
            prior_item_list.append({
                "class2": str(row.get("CLASS2", "")),
                "item_nm": str(row.get("ITEM_NM", "")),
                "sell_through_rate": float(row.get("판매율", 0)),
                "grade": str(row.get("등급", "")),
                "in_qty": int(row.get("IN_QTY", 0)),
                "sale_qty": int(row.get("SALE_QTY", 0)),
                "ord_amt": int(row.get("ORD_AMT", 0)),
                "sale_amt": int(row.get("SALE_AMT", 0)),
            })

    # 전년 매출/입고 금액 합산
    total_sale_amt_prior = sum(c.get("sale_amt", 0) for c in prior_class_list) if prior_class_list else 0
    total_in_amt_prior = sum(c.get("in_amt", 0) for c in prior_class_list) if prior_class_list else 0

    return {
        "summary": {
            "total_inbound": int(health_prior.get("총입고수량", 0)),
            "total_sales": int(health_prior.get("총판매수량", 0)),
            "total_stock": int(health_prior.get("총재고수량", 0)),
            "total_sale_amt": total_sale_amt_prior,
            "total_in_amt": total_in_amt_prior,
            "sell_through_rate": float(health_prior.get("판매율", 0)),
            "stock_risk": float(health_prior.get("재고리스크", 0)),
            # KG 정합 지표 (전년)
            "total_sale_amt_actual": int(health_prior.get("실판매액", 0)),
            "total_sale_tag": int(health_prior.get("판매택가", 0)),
            "total_stor_tag": int(health_prior.get("입고택가", 0)),
            "sell_through_rate_amt": float(health_prior.get("판매율_택가", 0)),
        },
        "class_analysis": prior_class_list,
        "item_analysis": prior_item_list,
    }


def build_yoy_data(summary_current, prior_year_data, class_current_list, item_current_list):
    """YoY 증감 데이터 빌드"""
    ps = prior_year_data["summary"]

    yoy_summary = {
        "sell_through_rate_delta": calculate_yoy_delta(
            summary_current["sell_through_rate"], ps["sell_through_rate"]
        ),
        "stock_risk_delta": calculate_yoy_delta(
            summary_current["stock_risk"], ps["stock_risk"]
        ),
        "total_sales_growth_pct": calculate_yoy_growth(
            summary_current["total_sales"], ps["total_sales"]
        ),
        "total_inbound_growth_pct": calculate_yoy_growth(
            summary_current["total_inbound"], ps["total_inbound"]
        ),
        "total_revenue_growth_pct": calculate_yoy_growth(
            summary_current["total_sale_amt"], ps["total_sale_amt"]
        ),
        # KG 정합 지표 YoY (총매출액=실판매액, 판매율=택가 기준)
        "total_revenue_actual_growth_pct": calculate_yoy_growth(
            summary_current.get("total_sale_amt_actual", 0), ps.get("total_sale_amt_actual", 0)
        ),
        "sell_through_rate_amt_delta": calculate_yoy_delta(
            summary_current.get("sell_through_rate_amt", 0), ps.get("sell_through_rate_amt", 0)
        ),
    }

    # 복종별 YoY
    prior_class_map = {c["class2"]: c for c in prior_year_data["class_analysis"]}
    yoy_class = []
    for c in class_current_list:
        pc = prior_class_map.get(c["class2"])
        if pc:
            yoy_class.append({
                "class2": c["class2"],
                "sell_through_rate_delta": calculate_yoy_delta(
                    c["sell_through_rate"], pc["sell_through_rate"]
                ),
                "in_qty_growth_pct": calculate_yoy_growth(c["in_qty"], pc["in_qty"]),
                "sale_qty_growth_pct": calculate_yoy_growth(c["sale_qty"], pc["sale_qty"]),
            })

    # 아이템별 YoY
    prior_item_map = {(i["class2"], i["item_nm"]): i for i in prior_year_data["item_analysis"]}
    yoy_item = []
    for item in item_current_list:
        pi = prior_item_map.get((item["class2"], item["item_nm"]))
        if pi:
            grade_change = None
            if pi["grade"] != item["grade"]:
                grade_change = f"{pi['grade']} → {item['grade']}"
            yoy_item.append({
                "class2": item["class2"],
                "item_nm": item["item_nm"],
                "sell_through_rate_delta": calculate_yoy_delta(
                    item["sell_through_rate"], pi["sell_through_rate"]
                ),
                "prior_sell_through_rate": pi["sell_through_rate"],
                "prior_grade": pi["grade"],
                "grade_change": grade_change,
            })

    return {
        "summary": yoy_summary,
        "class_analysis": yoy_class,
        "item_analysis": yoy_item,
    }


def export_season_closing_json(
    total_health: Dict,
    class_analysis: pd.DataFrame,
    item_analysis: pd.DataFrame,
    style_analysis: pd.DataFrame,
    output_path: str,
    health_prior: Dict = None,
    class_prior: pd.DataFrame = None,
    item_prior: pd.DataFrame = None,
    health_prior_season_end: Dict = None,
    class_prior_season_end: pd.DataFrame = None,
    item_prior_season_end: pd.DataFrame = None,
) -> None:
    """
    시즌 마감 분석 결과를 프론트엔드 대시보드용 JSON으로 출력

    Args:
        total_health: 전체 시즌 건강도 진단 결과
        class_analysis: 복종별 분석 결과
        item_analysis: 아이템별 분석 결과
        style_analysis: 스타일별 분석 결과
        output_path: JSON 출력 경로
    """
    print(f"[8단계] 프론트엔드 JSON 생성 중: {output_path}")

    # 스타일 등급 분포 계산
    grade_dist = {}
    action_dist = {}
    if not style_analysis.empty:
        grade_col = '등급' if '등급' in style_analysis.columns else None
        action_col = '액션' if '액션' in style_analysis.columns else None
        if grade_col:
            grade_dist = style_analysis[grade_col].value_counts().to_dict()
        if action_col:
            action_dist = style_analysis[action_col].value_counts().to_dict()

    # class_analysis → JSON 직렬화
    class_list = []
    if not class_analysis.empty:
        for _, row in class_analysis.iterrows():
            class_list.append({
                "class2": str(row.get("CLASS2", "")),
                "in_qty": int(row.get("IN_QTY", 0)),
                "sale_qty": int(row.get("SALE_QTY", 0)),
                "stock_qty": int(row.get("STOCK_QTY", 0)),
                "sale_amt": int(row.get("SALE_AMT", 0)),
                "in_amt": int(row.get("IN_AMT", 0)),
                "ord_amt": int(row.get("ORD_AMT", 0)),
                "avg_price": int(row.get("AVG_PRICE", 0)),
                "volume_share": float(row.get("물량비중", 0)),
                "sales_share": float(row.get("판매비중", 0)),
                "sell_through_rate": float(row.get("판매율", 0)),
                "balance_delta": float(row.get("비중차이", 0)),
                "balance_judgment": str(row.get("밸런스판정", "")),
                "ai_comment": str(row.get("AI코멘트", ""))
            })

    # item_analysis → JSON 직렬화 (입고 0인 아이템 제외)
    item_list = []
    if not item_analysis.empty:
        for _, row in item_analysis.iterrows():
            if int(row.get("IN_QTY", 0)) == 0:
                continue
            item_list.append({
                "class2": str(row.get("CLASS2", "")),
                "item_nm": str(row.get("ITEM_NM", "")),
                "grade": str(row.get("등급", "")),
                "bcg_class": str(row.get("BCG분류", "")),
                "sell_through_rate": float(row.get("판매율", 0)),
                "volume_share": float(row.get("물량비중", 0)),
                "sales_share": float(row.get("판매비중", 0)),
                "in_qty": int(row.get("IN_QTY", 0)),
                "sale_qty": int(row.get("SALE_QTY", 0)),
                "stock_qty": int(row.get("STOCK_QTY", 0)),
                "ord_amt": int(row.get("ORD_AMT", 0)),
                "sale_amt": int(row.get("SALE_AMT", 0)),
                "ai_comment": str(row.get("AI코멘트", ""))
            })

    total_styles = len(style_analysis) if not style_analysis.empty else 0

    # Top/Bottom 10 스타일 추출
    top_performers = []
    bottom_performers = []
    if not style_analysis.empty and '판매율' in style_analysis.columns:
        style_cols = ['스타일코드', '중분류', '아이템명', '등급', '액션', '판매율', 'AI코멘트']
        available_cols = [c for c in style_cols if c in style_analysis.columns]

        top_df = style_analysis.nlargest(10, '판매율')[available_cols]
        bottom_df = style_analysis.nsmallest(10, '판매율')[available_cols]

        for _, row in top_df.iterrows():
            top_performers.append({
                "style_cd": str(row.get("스타일코드", "")),
                "class2": str(row.get("중분류", "")),
                "item_nm": str(row.get("아이템명", "")),
                "grade": str(row.get("등급", "")),
                "action": str(row.get("액션", "")),
                "sell_through_rate": float(row.get("판매율", 0)),
                "ai_comment": str(row.get("AI코멘트", ""))
            })

        for _, row in bottom_df.iterrows():
            bottom_performers.append({
                "style_cd": str(row.get("스타일코드", "")),
                "class2": str(row.get("중분류", "")),
                "item_nm": str(row.get("아이템명", "")),
                "grade": str(row.get("등급", "")),
                "action": str(row.get("액션", "")),
                "sell_through_rate": float(row.get("판매율", 0)),
                "ai_comment": str(row.get("AI코멘트", ""))
            })

    # 액션별 스타일 목록 추출
    action_styles = {}
    if not style_analysis.empty and '액션' in style_analysis.columns:
        for action in ['Aggressive', 'Expand', 'Maintain', 'Observation', 'Cut/Drop']:
            action_df = style_analysis[style_analysis['액션'] == action].sort_values('판매율', ascending=(action in ['Observation', 'Cut/Drop']))
            styles_list = []
            for _, row in action_df.iterrows():
                styles_list.append({
                    "style_cd": str(row.get("스타일코드", "")),
                    "class2": str(row.get("중분류", "")),
                    "item_nm": str(row.get("아이템명", "")),
                    "grade": str(row.get("등급", "")),
                    "sell_through_rate": float(row.get("판매율", 0)),
                    "in_qty": int(row.get("발주수량", 0)),
                    "sale_qty": int(row.get("판매수량", 0)),
                    "ai_comment": str(row.get("AI코멘트", ""))
                })
            action_styles[action] = styles_list

    # 총 매출금액/입고금액 집계
    total_sale_amt = sum(c.get("sale_amt", 0) for c in class_list) if class_list else 0
    total_in_amt = sum(c.get("in_amt", 0) for c in class_list) if class_list else 0

    output = {
        "metadata": {
            "season": get_base_season(),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_styles": total_styles
        },
        "summary": {
            "total_inbound": int(total_health.get("총입고수량", 0)),
            "total_sales": int(total_health.get("총판매수량", 0)),
            "total_stock": int(total_health.get("총재고수량", 0)),
            "total_sale_amt": total_sale_amt,
            "total_in_amt": total_in_amt,
            "sell_through_rate": float(total_health.get("판매율", 0)),
            "stock_risk": float(total_health.get("재고리스크", 0)),
            "target_achievement": str(total_health.get("목표달성여부", "")),
            "ai_comment": str(total_health.get("AI코멘트", "")),
            # KG(지식그래프) 정합 지표 — 총매출액=실판매액, 판매율=판매택가/입고택가
            "total_sale_amt_actual": int(total_health.get("실판매액", 0)),
            "total_sale_tag": int(total_health.get("판매택가", 0)),
            "total_stor_tag": int(total_health.get("입고택가", 0)),
            "sell_through_rate_amt": float(total_health.get("판매율_택가", 0)),
            "target_achievement_amt": str(total_health.get("목표달성여부_택가", ""))
        },
        "class_analysis": class_list,
        "item_analysis": item_list,
        "style_summary": {
            "grade_distribution": {k: int(v) for k, v in grade_dist.items()},
            "action_distribution": {k: int(v) for k, v in action_dist.items()},
            "top_performers": top_performers,
            "bottom_performers": bottom_performers,
            "action_styles": action_styles
        }
    }

    # 전년 데이터가 있으면 prior_year / yoy 섹션 추가
    if health_prior and class_prior is not None and item_prior is not None:
        if not class_prior.empty or not item_prior.empty:
            prior_year_data = build_prior_year_data(health_prior, class_prior, item_prior)
            output["prior_year"] = prior_year_data
            output["yoy"] = build_yoy_data(
                output["summary"], prior_year_data, class_list, item_list
            )
            print("  * 전년 대비(YoY) 데이터 포함")

    # 마감예측판매율 벤치마크용: 전년 '동시즌 마감(season-end)' 스냅샷 (인시즌일 때만 별도 보유)
    #   to-date KPI는 prior_year(동주차)와, 마감예측은 prior_year_season_end(마감)와 비교.
    if (health_prior_season_end and class_prior_season_end is not None
            and item_prior_season_end is not None):
        if not class_prior_season_end.empty or not item_prior_season_end.empty:
            output["prior_year_season_end"] = build_prior_year_data(
                health_prior_season_end, class_prior_season_end, item_prior_season_end
            )
            print("  * 전년 동시즌 마감(prior_year_season_end) 데이터 포함 (마감예측 벤치마크용)")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # NaN/Infinity → null 변환 (브라우저 JSON.parse 호환)
    def sanitize(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(v) for v in obj]
        return obj

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sanitize(output), f, ensure_ascii=False, indent=2)

    print(f"  * JSON 저장 완료: {output_path}")


# ============================================
# 10. 메인 실행 함수
# ============================================

def main():
    """메인 실행 함수"""
    base = get_base_season()
    target = get_target_season()
    print("=" * 60)
    print(f"{base} 시즌 판매 효율 분석 및 {target} 발주 최적화 프로젝트")
    print("=" * 60)
    print()

    # 입력/출력 파일 경로 (config_loader에서 읽기)
    input_file = get_input_data_path()
    output_file = get_analysis_output_path()
    
    try:
        # 1. 데이터 로딩 및 전처리 (당해)
        df = load_and_preprocess_data(input_file, year_type='current')

        if df.empty:
            print("[오류] 데이터가 없습니다. 입력 파일을 확인해주세요.")
            return

        # 1-b. 전년 데이터 로딩 (있으면)
        health_prior = None
        class_prior = None
        item_prior = None
        try:
            df_prior = load_and_preprocess_data(input_file, year_type='prior')
            if not df_prior.empty:
                health_prior = analyze_total_season_health(df_prior)
                class_prior = analyze_class_balance(df_prior)
                item_prior = analyze_item_efficiency(df_prior)
                print(f"전년 데이터 분석 완료: {len(df_prior)}행")
            else:
                print("[정보] 전년 데이터가 없습니다. YoY 비교 생략.")
        except Exception as e:
            print(f"[정보] 전년 데이터 로딩 실패 (YoY 비교 생략): {e}")

        # 1-c. 인시즌: 전년/재작년 '동주차' 스냅샷 (to-date 실적 KPI의 사과-대-사과 YoY용)
        #   당해(부분시즌) vs 전년(마감) 비교는 성숙도 불일치로 하락 과장 → 전년 동주차로 비교.
        #   비인시즌(FW/마감)이면 None → 기존 전년 마감 기준 그대로 사용.
        health_prior_asof = None
        class_prior_asof = None
        item_prior_asof = None
        try:
            import config_loader as _cl
            df_asof_raw = _cl.load_season_raw_prev_asof()
            if df_asof_raw is not None:
                df_prior_asof = load_and_preprocess_data(
                    input_file, year_type='prior', df_override=df_asof_raw)
                if not df_prior_asof.empty:
                    health_prior_asof = analyze_total_season_health(df_prior_asof)
                    class_prior_asof = analyze_class_balance(df_prior_asof)
                    item_prior_asof = analyze_item_efficiency(df_prior_asof)
                    print(f"  * 인시즌 전년 동주차 스냅샷 분석 완료: {len(df_prior_asof)}행 (to-date YoY 기준)")
        except Exception as e:
            print(f"[정보] 전년 동주차 스냅샷 생략: {e}")

        # to-date YoY 비교 기준: 인시즌이면 전년 동주차, 아니면 전년 마감(현행)
        health_cmp = health_prior_asof if health_prior_asof is not None else health_prior
        class_cmp = class_prior_asof if class_prior_asof is not None else class_prior
        item_cmp = item_prior_asof if item_prior_asof is not None else item_prior
        yoy_basis_label = "전년 동주차" if health_prior_asof is not None else "전년"
        print()

        # 2. Level 1: 전체 시즌 건강도 진단
        total_health = analyze_total_season_health(df)

        # AI 코멘트에 전년 대비 맥락 추가 (인시즌이면 전년 동주차 기준)
        if health_cmp:
            str_delta = calculate_yoy_delta(
                total_health['판매율'], health_cmp['판매율']
            )
            risk_delta = calculate_yoy_delta(
                total_health['재고리스크'], health_cmp['재고리스크']
            )
            if str_delta is not None:
                direction = "개선" if str_delta > 0 else "하락"
                total_health['AI코멘트'] += (
                    f" {yoy_basis_label} 판매율 {health_cmp['판매율']:.1f}% 대비 "
                    f"{'+' if str_delta > 0 else ''}{str_delta:.1f}%p {direction}, "
                    f"재고 리스크 {'+' if risk_delta > 0 else ''}{risk_delta:.1f}%p."
                )

        print(f"전체 판매율: {total_health['판매율']:.2f}%")
        print(f"AI 코멘트: {total_health['AI코멘트']}")
        print()

        # 3. Level 2: 복종별 밸런스 분석
        class_analysis = analyze_class_balance(df)
        print(f"복종 분석 완료: {len(class_analysis)}개 복종")
        print()

        # 4. Level 3: 아이템별 효율 분석
        item_analysis = analyze_item_efficiency(df)
        print(f"아이템 분석 완료: {len(item_analysis)}개 아이템")
        print()

        # 5. Level 4: 스타일 상세 분석
        style_analysis = analyze_style_detail(df)
        print(f"스타일 분석 완료: {len(style_analysis)}개 스타일")
        print()

        # 6. 결과 엑셀 파일 생성
        create_result_excel(
            total_health,
            class_analysis,
            item_analysis,
            style_analysis,
            output_file
        )

        # 7. 프론트엔드용 JSON 출력 (전년 데이터 포함)
        json_output_file = "../public/season_closing_data.json"
        export_season_closing_json(
            total_health,
            class_analysis,
            item_analysis,
            style_analysis,
            json_output_file,
            health_prior=health_cmp,
            class_prior=class_cmp if class_cmp is not None else pd.DataFrame(),
            item_prior=item_cmp if item_cmp is not None else pd.DataFrame(),
            # 인시즌이면 전년 마감 스냅샷도 함께 전달 → prior_year_season_end (마감예측 벤치마크)
            health_prior_season_end=(health_prior if health_prior_asof is not None else None),
            class_prior_season_end=(class_prior if health_prior_asof is not None else None),
            item_prior_season_end=(item_prior if health_prior_asof is not None else None),
        )

        print()
        print("=" * 60)
        print("모든 분석이 완료되었습니다!")
        print("=" * 60)
        
    except FileNotFoundError:
        print(f"[오류] 파일을 찾을 수 없습니다: {input_file}")
        print("입력 파일이 현재 디렉토리에 있는지 확인해주세요.")
    except Exception as e:
        print(f"[오류] 분석 중 오류가 발생했습니다: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

