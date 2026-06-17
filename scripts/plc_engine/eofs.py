"""예상마감판매 (Track 2, 표시·진단용) — 국내전체 PLC 타이밍 진척 + 재고 캡.

Track 1(잠재수요/발주/DTW/predict_s5)과 **완전 격리**. 이 모듈은 "이 입고로 이번 시즌
마감 때 현실적으로 얼마 팔릴까"(재고 제약) 표시 지표만 산출한다. 발주에 쓰이지 않는다.

방식 C (2026-05-31 백테스트 검증, 25SS as-of 21 MAPE 15.9%, naive 46.1% 대비 우수):
  진척 p = PLC국내전체[현재주차] / PLC국내전체[서브시즌 마감주차]
  예상마감판매량 = min( 누계판매(국내전체) / max(p, floor) , 누계입고 )
  예상마감판매율 = 예상마감판매량 / 누계입고 (≤100%)

한계(문서화): 체계적 ~−15% 과소 bias(특히 Summer 중반). 표시 지표라 보수적·안전.
백테스트 PLC는 단일시즌이었고 운영은 2시즌(전년+재작년)이라 개선 기대.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SS_SUBS = ('1', '3')  # SS 서브시즌: 1=Spring, 3=Summer (FW 4/6 제외)

# NOTE: 잔여주차 기반 bias 보정은 25SS 단일시즌 적합(과적합 리스크) + 미래 베팅이라
#   2026-05-31 사용자 결정으로 제거. 중립(순수 PLC 형상)값을 표시. 두 번째 완료시즌 확보 후
#   재검토 가능 — 적합 하네스 scripts/_fit_bias_correction.py, 재검증 scripts/_backtest_eofs.py.


def build_total_plc(weekly_df, periods, min_cum_intake=10):
    """국내전체 PLC: (ITEM, SUB) 주차별 누적판매율 곡선(단조증가), fwo 1..39.

    SS만(PART_CD[-1] in {1,3}). 누적판매율 = cumsum(SALE_QTY_CNS)/cumsum(STOR_QTY_KR).
    periods: 사용할 PERIOD 리스트 (예: ['전년','재작년']).
    """
    df = weekly_df.copy()
    df['PART_CD'] = df['PART_CD'].astype(str)
    df = df[df['PART_CD'].str.len() == 9]
    df['SUB'] = df['PART_CD'].str[-1]
    df = df[df['SUB'].isin(SS_SUBS) & df['PERIOD'].isin(periods)]
    if df.empty:
        return {}
    df['FWO'] = pd.to_datetime(df['END_DT']).dt.isocalendar().week.astype(int)
    df = df[(df['FWO'] >= 1) & (df['FWO'] <= 39)]

    recs = []
    for (part, color), grp in df.groupby(['PART_CD', 'COLOR_CD']):
        grp = grp.sort_values('FWO')
        item, sub = grp['ITEM'].iloc[0], grp['SUB'].iloc[0]
        if pd.isna(item):
            continue
        cs = grp['SALE_QTY_CNS'].cumsum().values
        ci = grp['STOR_QTY_KR'].cumsum().values
        prev = 0.0
        for i, (_, r) in enumerate(grp.iterrows()):
            if ci[i] < min_cum_intake:
                continue
            st = max(cs[i] / ci[i], prev)
            prev = st
            recs.append({'IS': (item, sub), 'FWO': int(r['FWO']), 'ST': st})
    if not recs:
        return {}
    avg = pd.DataFrame(recs).groupby(['IS', 'FWO'])['ST'].mean().reset_index()
    out = {}
    for key, idf in avg.groupby('IS'):
        idf = idf.set_index('FWO')
        vals = [float(idf.loc[f, 'ST']) if f in idf.index else np.nan for f in range(1, 40)]
        arr = pd.Series(vals).interpolate(limit_direction='both').fillna(0).values.copy()
        for i in range(1, len(arr)):
            arr[i] = max(arr[i], arr[i - 1])
        out[key] = arr
    return out


def eofs_week_weights(plc_curve, fwos):
    """미래 주차(fwos)에 마감예측 잔여분을 분배할 비중(합=1) — PLC 곡선 증분 비례.

    곡선 없거나 증분 합이 0이면 균등분배. forecast 막대가 시즌말로 갈수록 자연 감소(taper).
    """
    n = len(fwos)
    if n == 0:
        return []
    if plc_curve is None or len(plc_curve) < 39:
        return [1.0 / n] * n
    ws = []
    for f in fwos:
        i = min(max(int(f), 1), 39) - 1
        ws.append(max(0.0, plc_curve[i] - plc_curve[i - 1]) if i >= 1 else 0.0)
    tot = sum(ws)
    return [w / tot for w in ws] if tot > 0 else [1.0 / n] * n


def estimate_eofs(sales_asof, intake, plc_curve, cutoff_fwo, end_fwo, floor=0.05):
    """예상마감판매 (량, 율). 계산 불가 시 None → 호출부가 폴백.

    sales_asof: 누계판매(국내전체), intake: 누계입고, plc_curve: (item,sub) 곡선 or None.
    """
    if intake <= 0 or plc_curve is None or len(plc_curve) < 39:
        return None
    i_cur = min(max(int(cutoff_fwo), 1), 39) - 1
    i_end = min(max(int(end_fwo), 1), 39) - 1
    pe = plc_curve[i_end]
    pc = plc_curve[i_cur]
    if pe <= 0:
        return None
    p = pc / pe
    qty = min(sales_asof / max(p, floor), intake)        # 재고 캡
    return qty, min(100.0, qty / intake * 100)
