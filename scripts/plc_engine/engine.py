"""PLC 엔진 — 메인 진입점."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .specs import (
    BrandSpec, SeasonSpec, EngineParams, EngineInputs, EngineResult,
)
from .builders import (
    build_sell_through_plc, build_sc_sell_through,
    find_plc_position, dtw_confidence,
)
from .broken import detect_commercial_stockout, compute_broken_series
from .predictor import predict_s5_for_sc, apply_scale_up
from .inseason_potential import (
    is_in_season, build_prioryear_progress, build_prioryear_incr, inseason_forecast,
)
from .preprocess import (
    build_sf_lookups, build_sc_scale, build_rst_num_sizes, build_week_frame,
)
from .utils import get_plc_ratio_decayed_factory


def run_engine(
    brand_spec: BrandSpec,
    season_spec: SeasonSpec,
    engine_params: EngineParams,
    inputs: EngineInputs,
) -> EngineResult:
    """PLC 수요예측 엔진 실행.

    Args:
        brand_spec: 브랜드 설정
        season_spec: 시즌 설정
        engine_params: 엔진 파라미터
        inputs: 입력 데이터 (gt, restored, weekly_raw, plc_ratio)

    Returns:
        EngineResult
    """
    # 0. GT 전처리: WEEK_OF_YEAR 파생 + SC_SALE_QTY_ALL 보정
    gt = inputs.gt.copy()
    if 'WEEK_OF_YEAR' not in gt.columns:
        gt['WEEK_START'] = pd.to_datetime(gt['WEEK_START'])
        gt['WEEK_OF_YEAR'] = gt['WEEK_START'].dt.isocalendar().week.astype(int)
    if 'SC_SALE_QTY_ALL' not in gt.columns:
        gt['SC_SALE_QTY_ALL'] = gt[season_spec.sale_col]

    # 1. SF pre-aggregate
    sf = build_sf_lookups(inputs.weekly_raw)

    # 2. 복원 PLC (GT에 ADJ_SC_SALE_QTY_TAX 통합된 경우 GT 직접 사용)
    restored = inputs.restored if inputs.restored is not None else gt
    if 'WEEK_OF_YEAR' not in restored.columns:
        restored = restored.copy()
        restored['WEEK_START'] = pd.to_datetime(restored['WEEK_START'])
        restored['WEEK_OF_YEAR'] = restored['WEEK_START'].dt.isocalendar().week.astype(int)

    plc_curves = build_sell_through_plc(
        restored, 'ADJ_SC_SALE_QTY_TAX',
        seasons=season_spec.seasons_for_plc,
        exclude_prods=brand_spec.plc_exclude_prods,
        fwo_range=season_spec.fwo_range,
        fw_order_fn=season_spec.fw_order,
        min_cum_intake=engine_params.min_cum_intake,
        min_sc_for_plc=engine_params.min_sc_for_plc,
    )

    # 3. PLC ratio decay 클로저 (서브시즌은 SS에서만 — FW 하드 보호)
    plc_ratio_fn = get_plc_ratio_decayed_factory(
        inputs.plc_ratio, engine_params.plc_tail_decay,
        fw_order_fn=season_spec.fw_order,
        use_subseason=(season_spec.season_type == "ss"),
    )

    # 4. SC별 스케일업
    gt_season = gt[gt['SSN_CD'] == season_spec.season_code].copy()
    sc_scale = build_sc_scale(gt_season, sf.kpi_dict, season_spec.sale_col)

    # 5. 복원 NUM_SIZES (GT에 통합된 경우 GT에서 직접)
    rst_num_sizes = build_rst_num_sizes(restored)

    # 6. 주차 프레임 (현재 시즌만 사용 — 전 시즌 포함 시 프레임 폭발)
    all_week_list, all_week_start = build_week_frame(gt_season, inputs.weekly_raw)

    # 7. 진행중 SS 잠재수요 외삽용 전년 누적판매분율 progress (SS만 사전계산)
    py_progress = (
        build_prioryear_progress(restored, season_spec)
        if season_spec.season_type == "ss" else None
    )
    py_incr = (
        build_prioryear_incr(restored, season_spec)
        if season_spec.season_type == "ss" else None
    )

    # 8. SC 루프
    sc_predictions = {}

    for (prod_cd, color), cdf in gt_season.groupby(['PROD_CD', 'COLOR_CD']):
        cdf = _reindex_weekly(cdf, all_week_list, all_week_start, season_spec.sale_col)
        item = (
            cdf['ITEM'].iloc[0]
            if 'ITEM' in cdf.columns and cdf['ITEM'].notna().any()
            else None
        )
        if item is None or int(cdf['CUM_INTAKE'].max()) <= 0:
            continue

        broken = detect_commercial_stockout(
            cdf, brand_spec.avg_size_stock_threshold,
            engine_params.min_sale_weeks, rst_num_sizes, prod_cd, color,
        )

        # PLC 커브 없는 아이템(1시즌만 있는 신규/단종)은 예측 생성 안 함 — 잘못된 평탄 예측 방지
        plc_curve = plc_curves.get(item)
        if broken is None or plc_curve is None:
            sc_predictions[(prod_cd, color)] = _no_broken_result(
                cdf, sf, prod_cd, color, season_spec.sale_col,
            )
            _maybe_extrapolate(sc_predictions[(prod_cd, color)], item, season_spec, py_progress, py_incr,
                           sub=str(prod_cd)[-1])
            continue

        dtw_result = _dtw_match(
            cdf, plc_curves.get(item), broken, engine_params, season_spec,
        )

        pred_data = predict_s5_for_sc(
            cdf, brand_spec, season_spec, engine_params,
            plc_curves.get(item), plc_ratio_fn, broken, dtw_result,
            sf, prod_cd, color,
        )

        scale_raw = sc_scale.get((prod_cd, color), 1.0)
        scale_adj = 1.0 + (scale_raw - 1.0) * brand_spec.scale_blend

        # 주차별 결품 상태 배열 (loss 계산 + SS 전체수요선 per-week 판정에 사용)
        is_broken_series = compute_broken_series(
            cdf, brand_spec.avg_size_stock_threshold,
            rst_num_sizes, prod_cd, color,
        )

        # 전체수요선 생성. SS는 per-week 결품 판정(비결품 주차=실판매 관측, 결품 주차=잠재수요 예측)
        #   → 재입고로 결품 해소된 비결품 주차에 감쇠예측이 덮이는 문제 해소.
        #   FW는 is_broken_series=None → 단일 결품시점 컷오프(기존 동작, 0-diff 보존).
        pred_total = apply_scale_up(
            pred_data['predicted'], sf.sales, broken.pos, scale_adj,
            cdf, prod_cd, color,
            is_broken_series=(is_broken_series if season_spec.season_type == "ss" else None),
        )

        # floor=실판매(방어): 수요선이 실판매보다 낮게 깔리지 않도록 전 주차 바닥 보정.
        #   스무딩·스케일업이 끝난 최종값에 적용. 비결품 주차도 스무딩으로 실판매 아래로
        #   내려갈 수 있으므로 결품/비결품 구분 없이 매주 보정(전체수요≥전체판매 불변식).
        #   국내수요(predicted_sc)≥순수국내(actuals_tax=GT). 전체수요(predicted_total)≥국내전체판매(actuals_sf=weekly SALE_QTY_CNS).
        #   ※ actuals_all(GT)은 순수국내라 화면 전체판매 바와 불일치 → 전체수요 바닥은 actuals_sf 사용.
        #   total_demand(발주) 계산 전에 적용하여 발주에도 일관 반영.
        #   ⚠️ SS 전용 게이트: FW(25F) 0-diff(오라클①) 보존을 위해 FW에는 미적용.
        if season_spec.season_type == "ss":
            _pred_sc = pred_data['predicted']
            _at, _asf = pred_data['actuals_tax'], pred_data['actuals_sf']
            for _i in range(len(pred_total)):
                if _i < len(_pred_sc) and _i < len(_at):
                    _pred_sc[_i] = max(_pred_sc[_i], _at[_i])
                if _i < len(_asf):
                    pred_total[_i] = max(pred_total[_i], _asf[_i])

        # L2: 진행중 SS는 to-date 수요를 PLC 진행률로 full-season 외삽 (FW/마감SS는 기존 합).
        total_demand = round(sum(pred_total))
        sf_sale_total = sum(pred_data['actuals_sf'])

        # 결품 주차 한정 lost_qty 재계산 (potential - actual_sf, 음수 제외)
        actuals_sf = pred_data['actuals_sf']
        lost_qty_weekly = sum(
            max(0, pred_total[i] - actuals_sf[i])
            for i in range(min(len(pred_total), len(is_broken_series), len(actuals_sf)))
            if is_broken_series[i]
        )

        sc_predictions[(prod_cd, color)] = {
            'weeks': pred_data['weeks'],
            'week_numbers': pred_data['week_numbers'],
            'predicted_sc': pred_data['predicted'],
            'predicted_total': pred_total,
            'actuals_all': pred_data['actuals_all'],
            'actuals_tax': pred_data['actuals_tax'],
            'actuals_sf': actuals_sf,
            'intakes': pred_data['intakes'],
            'stocks': pred_data['stocks'],
            'str_list': pred_data['str_list'],
            'total_demand': total_demand,
            'sf_sale_total': sf_sale_total,
            'lost_qty': round(lost_qty_weekly),
            'broken': broken,
            'is_broken_series': is_broken_series,
            'dtw_shift': dtw_result['shift'],
            'dtw_conf': dtw_result['conf'],
            'dtw_dist': dtw_result['dist'],
        }
        _maybe_extrapolate(sc_predictions[(prod_cd, color)], item, season_spec, py_progress, py_incr,
                           sub=str(prod_cd)[-1])

    style_aggregates = _aggregate_by_style(sc_predictions, gt_season)
    metrics = _compute_metrics(sc_predictions)
    return EngineResult(sc_predictions, style_aggregates, metrics)


# ── 내부 헬퍼 ──


def _maybe_extrapolate(d, item, season_spec, py_progress, py_incr, sub=None):
    """진행중 SS: total_demand 외삽 + forward 투영 곡선 (결품/비결품 SC 공통).

    sub: SC의 서브시즌(PROD_CD 마지막자리) — 전년 진행률/증분을 서브시즌별로 적용
         (봄 긴팔이 여름 혹 안 받도록). None이면 item 레벨(기존 동작).
    """
    if season_spec.season_type != "ss" or py_progress is None:
        return
    actuals = d['actuals_tax']
    if not is_in_season(d['week_numbers'], actuals, season_spec):
        return
    fc = inseason_forecast(d['predicted_total'], d['week_numbers'], actuals, item,
                           py_progress, py_incr, season_spec, sub=sub,
                           is_broken=(d.get('broken') is not None))
    if fc is None:
        return
    d['total_demand'] = round(fc['potential'])
    d['forecast_weeks'] = fc['forecast_weeks']
    d['forecast_values'] = fc['forecast_values']


def _reindex_weekly(cdf, all_week_list, all_week_start, sale_col):
    """cdf를 전체 주차 프레임으로 reindex."""
    cdf = cdf.sort_values('WEEK_START').copy()
    cdf = cdf.set_index('WEEK_OF_YEAR').reindex(all_week_list).reset_index()
    cdf['WEEK_START'] = all_week_start
    cdf['SC_SALE_QTY_ALL'] = cdf['SC_SALE_QTY_ALL'].fillna(0).astype(int)
    cdf[sale_col] = cdf[sale_col].fillna(0).astype(int)
    cdf['BOW_STOCK'] = cdf['BOW_STOCK'].fillna(0).astype(int)
    cdf['NUM_SIZES'] = cdf['NUM_SIZES'].ffill().fillna(0).astype(int)
    cdf['CUM_INTAKE'] = cdf['CUM_INTAKE'].ffill().fillna(0).astype(int)
    cdf['CUM_SALE'] = cdf[sale_col].cumsum()
    # ffill ITEM (reindex로 NaN될 수 있음)
    if 'ITEM' in cdf.columns:
        cdf['ITEM'] = cdf['ITEM'].ffill().bfill()
    return cdf


def _no_broken_result(cdf, sf, prod_cd, color, sale_col):
    """브로큰 없는 SC — 예측=실판매, loss=0."""
    weeks = []
    week_numbers = []
    actuals_sf = []
    for _, r in cdf.iterrows():
        wk = int(r['WEEK_OF_YEAR'])
        ws = r.get('WEEK_START', '')
        date_label = str(ws)[5:10].replace('-', '/') if pd.notna(ws) and len(str(ws)) >= 10 else f'W{wk}'
        weeks.append(date_label)
        week_numbers.append(wk)
        actuals_sf.append(max(0, sf.sales.get((prod_cd, color, wk), 0)))

    sf_sale_total = sum(actuals_sf)
    return {
        'weeks': weeks,
        'week_numbers': week_numbers,
        'predicted_sc': [int(r[sale_col]) for _, r in cdf.iterrows()],
        'predicted_total': actuals_sf[:],
        'actuals_all': [int(r['SC_SALE_QTY_ALL']) for _, r in cdf.iterrows()],
        'actuals_tax': [int(r[sale_col]) for _, r in cdf.iterrows()],
        'actuals_sf': actuals_sf,
        'intakes': [
            sf.weekly_intake.get((prod_cd, color, int(r['WEEK_OF_YEAR'])), 0) or None
            for _, r in cdf.iterrows()
        ],
        'stocks': [int(r['BOW_STOCK']) for _, r in cdf.iterrows()],
        'str_list': [],
        'total_demand': sf_sale_total,
        'sf_sale_total': sf_sale_total,
        'lost_qty': 0,
        'broken': None,
        'is_broken_series': [False] * len(weeks),
        'dtw_shift': 0,
        'dtw_conf': 'N/A',
        'dtw_dist': float('inf'),
    }


def _dtw_match(cdf, plc_curve, broken, params, season_spec):
    """DTW 슬라이딩 매칭 수행."""
    if plc_curve is None:
        return {'matched': None, 'shift': 0, 'dist': float('inf'), 'conf': 'N/A'}

    actual_broken_fwo = season_spec.fw_order(broken.week)
    sc_curve = build_sc_sell_through(cdf, broken.pos, params.min_cum_intake)

    if len(sc_curve) < params.dtw_min_window:
        return {'matched': None, 'shift': 0, 'dist': float('inf'), 'conf': 'N/A'}

    matched, shift, dist = find_plc_position(
        sc_curve, plc_curve, actual_broken_fwo, params,
    )
    if matched is not None:
        conf, shift = dtw_confidence(dist, shift, params)
        matched = actual_broken_fwo + shift
    else:
        conf = 'N/A'

    return {'matched': matched, 'shift': shift, 'dist': dist, 'conf': conf}


def _aggregate_by_style(sc_predictions, gt_df):
    """prod_cd 단위로 color별 합산."""
    style_agg = {}
    # Group by prod_cd
    prod_colors = {}
    for (prod_cd, color), data in sc_predictions.items():
        prod_colors.setdefault(prod_cd, {})[color] = data

    for prod_cd, colors in prod_colors.items():
        if not colors:
            continue
        sample = next(iter(colors.values()))
        n = len(sample['weeks'])

        tot = {
            k: [0] * n
            for k in ['actuals_all', 'actuals_tax', 'actuals_sf',
                       'predicted_sc', 'predicted_total', 'stocks']
        }
        tot_intakes = [None] * n

        for cdata in colors.values():
            for i in range(min(n, len(cdata.get('weeks', [])))):
                for k in tot:
                    if k in cdata and i < len(cdata[k]):
                        tot[k][i] += cdata[k][i] if cdata[k][i] is not None else 0
                ci = cdata['intakes'][i] if 'intakes' in cdata and i < len(cdata['intakes']) else None
                if ci is not None:
                    tot_intakes[i] = (tot_intakes[i] or 0) + ci

        total_demand = round(sum(tot['predicted_total']))
        total_sf_sale = sum(tot['actuals_sf'])

        style_agg[prod_cd] = {
            'weeks': sample['weeks'],
            'actuals_all': tot['actuals_all'],
            'actuals_tax': tot['actuals_tax'],
            'actuals_sf': tot['actuals_sf'],
            'predicted_sc': tot['predicted_sc'],
            'predicted_total': tot['predicted_total'],
            'stocks': tot['stocks'],
            'intakes': tot_intakes,
            'total_demand': total_demand,
            'sf_sale_total': total_sf_sale,
            'lost_qty': max(0, total_demand - total_sf_sale),
            'color_count': len(colors),
        }
    return style_agg


def _compute_metrics(sc_predictions):
    """DTW distance/shift 분포 + 전체 기회비용."""
    all_dists = []
    all_shifts = []
    total_lost = 0

    for data in sc_predictions.values():
        dist = data.get('dtw_dist')
        if dist is not None and dist != float('inf'):
            all_dists.append(dist)
            all_shifts.append(abs(data.get('dtw_shift', 0)))
        total_lost += data.get('lost_qty', 0)

    metrics = {'total_lost_qty': total_lost}

    if all_dists:
        p25, p75 = np.percentile(all_dists, [25, 75])
        metrics['dtw_dist_p25'] = round(float(p25), 3)
        metrics['dtw_dist_median'] = round(float(np.median(all_dists)), 3)
        metrics['dtw_dist_p75'] = round(float(p75), 3)
        within2 = sum(1 for s in all_shifts if s <= 2)
        big = sum(1 for s in all_shifts if s > 4)
        metrics['shift_within_2w'] = within2
        metrics['shift_gt_4w'] = big
        metrics['shift_total'] = len(all_shifts)

    return metrics
