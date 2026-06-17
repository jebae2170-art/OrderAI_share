"""PLC 엔진 — in-season 잠재수요 보정 (L2).

진행중(미마감) 시즌의 to-date 수요를 진행률로 나눠 full-season 잠재수요로 외삽한다.
season_type=="ss" 이고 in-season일 때만 engine.py 소비점에서 호출 (FW/마감시즌 미경유 → 0-diff).

  potential_full = to_date_demand / progress
    · as_of_fwo  = CUM_SALE이 strictly 증가한 마지막 주차 (버퍼 max 아님; spec C7)
    · to_date_demand = sum(pred_total[:as_of_idx+1])  (결품복원 포함된 to-date 합)
    · progress = **전년(seasons_for_plc) 누적판매분율** = Σtodate / Σfull (item별 풀링)
                 = "역사적으로 이 아이템이 이 주차까지 보통 몇 % 팔렸나" (현업 로직)

설계 근거(2026-05-27 25SS 백테스트):
  - progress는 empirical(전년 판매분율) 채택 — PLC sell-through곡선/위상정렬(anchor)보다
    총량 회복(agg_ratio) 우수: 16주 0.86 vs 곡선 0.81. 현업 사고방식과도 일치.
  - floor 블렌딩/clamp는 효과 없음이 입증됨 → 극초기 0-division 방지용 최소 clamp만.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd


def build_prioryear_progress(restored_df, season_spec, sale_col='ADJ_SC_SALE_QTY_TAX', min_sc_sub=5):
    """seasons_for_plc(마감 과거시즌) 실판매 기반 누적판매분율 progress 클로저.

    progress(item, as_of, sub) = Σ_season(누적판매 ≤ as_of) / Σ_season(full)  — 풀링.
    서브시즌(ADDITIVE, SS only): sub(=PROD_CD 마지막자리) 학습SC ≥ min_sc_sub면 (item,sub)
      곡선, 아니면 item 곡선 폴백. sub=None(기존 호출)이면 항상 item — 기존 동작 보존.
    """
    df = restored_df[restored_df['SSN_CD'].isin(season_spec.seasons_for_plc)].copy()
    if 'WEEK_OF_YEAR' not in df.columns:
        df['WEEK_OF_YEAR'] = pd.to_datetime(df['WEEK_START']).dt.isocalendar().week.astype(int)
    df['SESN_SUB'] = df['PROD_CD'].astype(str).str[-1]
    sub_ok = {(it, sb) for (it, sb), n in
              df.groupby(['ITEM', 'SESN_SUB'])['PROD_CD'].nunique().items() if n >= min_sc_sub}
    seasons = season_spec.seasons_for_plc

    def _build_cum(keys):
        g = df.groupby(['SSN_CD'] + keys + ['WEEK_OF_YEAR'])[sale_col].sum().reset_index()
        cum = {}
        for gk, grp in g.groupby(['SSN_CD'] + keys):
            grp = grp.sort_values('WEEK_OF_YEAR')
            c = grp[sale_col].cumsum().values
            cum[gk] = (grp['WEEK_OF_YEAR'].values, c, float(c[-1]) if len(c) else 0.0)
        return cum
    cum_item = _build_cum(['ITEM'])              # (ssn, item)
    cum_sub = _build_cum(['ITEM', 'SESN_SUB'])   # (ssn, item, sub)

    def _sum_progress(cum, keybuild, as_of):
        td = fl = 0.0
        for s in seasons:
            v = cum.get(keybuild(s))
            if not v or v[2] <= 0:
                continue
            wks, c, full = v
            mask = wks <= as_of
            td += float(c[mask][-1]) if mask.any() else 0.0
            fl += full
        return (td / fl) if fl > 0 else None

    def progress(item, as_of, sub=None):
        if sub is not None and (item, sub) in sub_ok:
            r = _sum_progress(cum_sub, lambda s: (s, item, sub), as_of)
            if r is not None:
                return r
        return _sum_progress(cum_item, lambda s: (s, item), as_of)

    return progress


def build_prioryear_incr(restored_df, season_spec, sale_col='ADJ_SC_SALE_QTY_TAX', min_sc_sub=5):
    """주차 증분판매분율 incr(item, week, sub) = Σ_season(week판매) / Σ_season(full).

    Σ_w incr = 1. forward 투영 형상 분배용. 서브시즌(ADDITIVE, SS only): sub 학습SC ≥
      min_sc_sub면 (item,sub) 형상, 아니면 item 폴백. sub=None이면 item — 기존 동작.
      ⇒ 봄(긴팔)은 5월 이후 0에 가까운 incr → 여름 혹 제거.
    """
    df = restored_df[restored_df['SSN_CD'].isin(season_spec.seasons_for_plc)].copy()
    if 'WEEK_OF_YEAR' not in df.columns:
        df['WEEK_OF_YEAR'] = pd.to_datetime(df['WEEK_START']).dt.isocalendar().week.astype(int)
    df['SESN_SUB'] = df['PROD_CD'].astype(str).str[-1]
    sub_ok = {(it, sb) for (it, sb), n in
              df.groupby(['ITEM', 'SESN_SUB'])['PROD_CD'].nunique().items() if n >= min_sc_sub}
    full_item = df.groupby('ITEM')[sale_col].sum()
    wk_item = df.groupby(['ITEM', 'WEEK_OF_YEAR'])[sale_col].sum()
    full_sub = df.groupby(['ITEM', 'SESN_SUB'])[sale_col].sum()
    wk_sub = df.groupby(['ITEM', 'SESN_SUB', 'WEEK_OF_YEAR'])[sale_col].sum()

    def incr(item, week, sub=None):
        if sub is not None and (item, sub) in sub_ok:
            f = full_sub.get((item, sub), 0)
            if f > 0:
                return float(wk_sub.get((item, sub, int(week)), 0.0)) / float(f)
        f = full_item.get(item, 0)
        if f <= 0:
            return 0.0
        return float(wk_item.get((item, int(week)), 0.0)) / float(f)

    return incr


VANCHOR_K = 4              # 최근속도 윈도우(주). 25SS 백테스트 robust(3/4/6 안정), K=4 채택.
VANCHOR_MATURITY = 0.45    # as_of ≥ 이 비율×season_end 일 때만 vanchor (초기 노이즈 폴백)


def inseason_forecast(pred_total, week_numbers, actuals, item, progress_fn, incr_fn,
                      season_spec, floor=0.05, sub=None, is_broken=False):
    """진행중 SS: 잠재수요 + 마감까지 주차별 forward 투영 곡선.

    as_of는 **실판매(actuals) 누적**의 마지막 증가주차로 잡는다 — pred_total은 프레임
    버퍼/CUM_INTAKE ffill로 미래주차가 누수될 수 있어 as_of 신호로 쓰지 않는다(spec C7).
    to_date는 그 as_of까지의 pred_total 합(결품복원 포함; spec C1 슬라이스).

    forward 산출 2방식 (25SS 백테스트 검증):
      · vanchor (비결품 + 성숙시점): forward_w = 최근K주속도 × incr[w]/평년최근K avg.
        당해 속도 anchor → 시즌 강·약 자동 스케일(전년 총량 외삽 과대투영 보완).
      · 전년형상 (결품 SC / 초기): potential = to_date/progress, forward = potential × incr.
        결품은 to_date에 복원분 포함되어 progress 외삽이 적정. 초기는 속도 노이즈로 폴백.

    반환 dict 또는 None:
      potential / as_of_week / forecast_weeks[as_of+1..end] / forecast_values
    """
    if progress_fn is None or not week_numbers:
        return None
    as_of_week, as_of_idx = find_as_of(week_numbers, list(np.cumsum(actuals)))
    progress = progress_fn(item, as_of_week, sub)
    if progress is None or progress <= 0:
        return None
    to_date = sum(pred_total[:as_of_idx + 1])
    end_fwo = int(season_spec.season_end_fwo)
    fwks = list(range(int(as_of_week) + 1, end_fwo + 1))

    # vanchor 적용 조건: 비결품 + incr 보유 + 성숙(초기 폴백) + 속도윈도우 데이터 충분
    # ⚑ ORDERAI_BROKEN_VANCHOR=1 이면 결품 SC도 vanchor 허용 (25S 백테스트 근거, 기본 OFF=현행 외삽).
    #    롤백: env 해제 후 재실행, 또는 백업 DuckDB 복원.
    allow_broken = os.environ.get('ORDERAI_BROKEN_VANCHOR', '').lower() in ('1', 'true', 'yes')
    use_vanchor = ((not is_broken or allow_broken) and incr_fn is not None
                   and as_of_week >= VANCHOR_MATURITY * end_fwo
                   and as_of_idx + 1 >= VANCHOR_K)
    if use_vanchor:
        v = max(0.0, (to_date - sum(pred_total[:as_of_idx + 1 - VANCHOR_K])) / VANCHOR_K)
        recent = [incr_fn(item, w, sub)
                  for w in range(int(as_of_week) - VANCHOR_K + 1, int(as_of_week) + 1)]
        denom = float(np.mean(recent)) if recent else 0.0
        if v > 0 and denom > 0:
            fvals = [v * incr_fn(item, w, sub) / denom for w in fwks]
            return {'potential': to_date + sum(fvals), 'as_of_week': int(as_of_week),
                    'forecast_weeks': fwks, 'forecast_values': fvals}

    # 폴백/결품: 전년형상 외삽
    potential = to_date / max(progress, floor)
    fvals = [potential * incr_fn(item, w, sub) for w in fwks] if incr_fn is not None else []
    if incr_fn is None:
        fwks = []
    return {'potential': potential, 'as_of_week': int(as_of_week),
            'forecast_weeks': fwks, 'forecast_values': fvals}


def find_as_of(week_numbers, cum_sale):
    """CUM_SALE이 strictly 증가한 마지막 인덱스 → (as_of_week, as_of_idx)."""
    last = 0
    for i in range(1, len(cum_sale)):
        if cum_sale[i] > cum_sale[i - 1]:
            last = i
    return week_numbers[last], last


def is_in_season(week_numbers, actuals, season_spec):
    """as-of(=실판매 누적 마지막 증가주차)가 시즌 마감 fwo 이전이면 진행중."""
    if not week_numbers:
        return False
    as_of_week, _ = find_as_of(week_numbers, list(np.cumsum(actuals)))
    return season_spec.fw_order(as_of_week) < season_spec.season_end_fwo


def inseason_potential(pred_total, week_numbers, item, progress_fn, floor=0.05, sub=None):
    """진행중 시즌 full-season 잠재수요 외삽. 계산 불가 시 None(→ 호출부가 기존합 사용).

    as_of는 pred_total 누적이 strictly 증가한 마지막 주차에서 자체 도출 (결품/비결품 공통).
    progress_fn: progress(item, as_of_week) — build_prioryear_progress 결과.
    floor: progress 하한 (0-division/극초기 폭주 방지용 최소 clamp).
    """
    if progress_fn is None or not week_numbers:
        return None
    as_of_week, as_of_idx = find_as_of(week_numbers, list(np.cumsum(pred_total)))
    progress = progress_fn(item, as_of_week, sub)
    if progress is None or progress <= 0:
        return None
    to_date = sum(pred_total[:as_of_idx + 1])
    return to_date / max(progress, floor)
