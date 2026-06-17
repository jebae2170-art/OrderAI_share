"""PLC 엔진 — SC 단위 S5 예측 루프."""

from __future__ import annotations

import pandas as pd

from .specs import BrandSpec, SeasonSpec, EngineParams, BrokenPoint
from .builders import asymmetric_smooth
from .preprocess import SFLookups


def predict_s5_for_sc(
    cdf,
    brand_spec: BrandSpec,
    season_spec: SeasonSpec,
    params: EngineParams,
    plc_curve,
    plc_ratio_fn,
    broken: BrokenPoint,
    dtw_result: dict,
    sf_lookups: SFLookups,
    prod_cd: str,
    color: str,
) -> dict:
    """SC 단위 S5 Accurate 예측.

    Args:
        cdf: reindexed SC DataFrame
        brand_spec: 브랜드 설정
        season_spec: 시즌 설정
        params: 엔진 파라미터
        plc_curve: 해당 아이템 PLC 곡선 (or None)
        plc_ratio_fn: get_plc_ratio_decayed 클로저
        broken: BrokenPoint (브로큰 정보)
        dtw_result: {'matched': int|None, 'shift': int, 'dist': float, 'conf': str}
        sf_lookups: SF lookup 딕셔너리
        prod_cd: 스타일 코드
        color: 컬러 코드

    Returns:
        dict with keys: predicted, weeks, actuals_all, actuals_tax, actuals_sf,
                        intakes, stocks, str_list
    """
    sale_col = season_spec.sale_col
    dtw_matched = dtw_result['matched']
    dtw_conf = dtw_result['conf']
    broken_week = broken.week
    broken_pos = broken.pos

    # 서브시즌(ADDITIVE): SC의 PROD_CD 마지막자리 1회 도출.
    # plc_ratio에 SESN_SUB가 없으면 클로저가 항상 'ALL'로 폴백 → 기존과 동일.
    sub = None
    if 'PROD_CD' in cdf.columns:
        _pc = cdf['PROD_CD'].dropna()
        if len(_pc) > 0:
            sub = str(_pc.iloc[0])[-1]
    elif prod_cd:
        sub = str(prod_cd)[-1]

    weeks, week_numbers, actuals_all, actuals_tax, actuals_sf = [], [], [], [], []
    intakes, stocks, str_list = [], [], []
    predicted = []
    recent_actuals, anchor, zero_stock = [], None, 0
    last_sf_ci, last_sf_cs = 0, 0

    for idx, (_, r) in enumerate(cdf.iterrows()):
        wk = int(r['WEEK_OF_YEAR'])
        sale_all = int(r['SC_SALE_QTY_ALL'])
        sale_tax = int(r[sale_col])
        stock = int(r['BOW_STOCK'])
        ws = r.get('WEEK_START', '')
        date_label = str(ws)[5:10].replace('-', '/') if pd.notna(ws) and len(str(ws)) >= 10 else f'W{wk}'

        weeks.append(date_label)
        week_numbers.append(wk)
        actuals_all.append(sale_all)
        actuals_tax.append(sale_tax)
        actuals_sf.append(max(0, sf_lookups.sales.get((prod_cd, color, wk), 0)))
        wk_intake = sf_lookups.weekly_intake.get((prod_cd, color, wk), 0)
        intakes.append(wk_intake if wk_intake > 0 else None)
        stocks.append(stock)

        key_sf = (prod_cd, color, wk)
        if key_sf in sf_lookups.cum_intake:
            last_sf_ci = sf_lookups.cum_intake[key_sf]
            last_sf_cs = sf_lookups.cum_sale[key_sf]
        str_list.append(round(last_sf_cs / last_sf_ci * 100, 1) if last_sf_ci > 0 else 0)

        if r['CUM_INTAKE'] <= 0:
            predicted.append(0)
            continue

        is_before_broken = idx < (broken_pos - cdf.index[0])

        if is_before_broken:
            predicted.append(sale_tax)
            if sale_tax > 0:
                recent_actuals.append(sale_tax)
            continue

        # 앵커 초기화
        if anchor is None:
            if len(recent_actuals) >= 2:
                anchor = (recent_actuals[-1] + recent_actuals[-2]) / 2
            elif len(recent_actuals) == 1:
                anchor = recent_actuals[-1]
            else:
                anchor = max(sale_tax, 1)

        # Dead stock 체크
        if stock == 0 and sale_all == 0:
            zero_stock += 1
        else:
            zero_stock = 0
        if zero_stock >= params.dead_stock_weeks:
            predicted.append(0)
            continue

        # S1 (캘린더) 기본 폴백
        cur_r = plc_ratio_fn(cdf['ITEM'].iloc[0] if 'ITEM' in cdf.columns else None, wk, sub)
        br_r = plc_ratio_fn(cdf['ITEM'].iloc[0] if 'ITEM' in cdf.columns else None, broken_week, sub)
        pred = anchor * (cur_r / br_r) if (cur_r and br_r and br_r > 0) else anchor

        # S5 Accurate: High+Medium은 DTW 시프트 적용 (S3)
        if dtw_matched is not None and dtw_conf in ('High', 'Medium'):
            offset = idx - (broken_pos - cdf.index[0])
            shifted_fwo = dtw_matched + offset
            broken_fwo = dtw_matched
            shifted_wn = season_spec.fwo_to_week_int(shifted_fwo)
            broken_wn = season_spec.fwo_to_week_int(broken_fwo)
            item = cdf['ITEM'].iloc[0] if 'ITEM' in cdf.columns else None
            s_r = plc_ratio_fn(item, shifted_wn, sub)
            b_r = plc_ratio_fn(item, broken_wn, sub)
            if s_r and b_r and b_r > 0:
                pred = anchor * (s_r / b_r)

        predicted.append(round(max(pred, 0), 1))

    # 스무딩
    predicted = asymmetric_smooth(predicted, broken_pos - cdf.index[0])

    return {
        'predicted': predicted,
        'weeks': weeks,
        'week_numbers': week_numbers,
        'actuals_all': actuals_all,
        'actuals_tax': actuals_tax,
        'actuals_sf': actuals_sf,
        'intakes': intakes,
        'stocks': stocks,
        'str_list': str_list,
    }


def apply_scale_up(predicted, sf_sales, broken_pos, scale_adj, cdf, prod_cd, color,
                   is_broken_series=None):
    """전체수요예측 (결품 주차=국내예측 x scale_adj, 비결품 주차=SF 실판매).

    결품 판정 방식:
      - is_broken_series 제공(SS): 주차별 결품 상태로 판정 → 재입고로 결품 해소된
        비결품 주차는 실판매(관측수요)로 되돌림(감쇠예측이 덮어쓰는 문제 해소).
      - None(FW): broken_pos 단일 컷오프(첫 결품 이후 전부 예측) — 기존 동작·0-diff 보존.

    Args:
        predicted: 국내 예측 리스트
        sf_sales: {(prod_cd, color, week) -> sale_qty}
        broken_pos: 브로큰 인덱스 위치 (or None)
        scale_adj: 스케일업 계수
        cdf: SC DataFrame
        prod_cd: 스타일 코드
        color: 컬러 코드
        is_broken_series: 주차별 결품 bool 리스트 (SS). None이면 broken_pos 컷오프(FW).

    Returns:
        list: 전체수요예측 (3주 rolling MA 스무딩)
    """
    bp_rel = (broken_pos - cdf.index[0]) if broken_pos is not None else len(cdf)
    predicted_total_raw = []
    for i, (_, r) in enumerate(cdf.iterrows()):
        wk_r = int(r['WEEK_OF_YEAR'])
        broken_i = (
            is_broken_series[i]
            if (is_broken_series is not None and i < len(is_broken_series))
            else (i >= bp_rel)
        )
        if not broken_i:
            predicted_total_raw.append(max(0, sf_sales.get((prod_cd, color, wk_r), 0)))
        else:
            predicted_total_raw.append(predicted[i] * scale_adj)

    return (
        pd.Series(predicted_total_raw)
        .rolling(3, min_periods=1, center=True)
        .mean()
        .round(1)
        .tolist()
    )
