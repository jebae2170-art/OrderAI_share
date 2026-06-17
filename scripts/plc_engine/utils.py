"""PLC 엔진 유틸리티 함수."""

from __future__ import annotations

import numpy as np
import pandas as pd


def remove_outliers(vals, ratio=5.0):
    """동일 주차 시즌간 이상치 제거 — ratio배 이상 편차 시 해당 값 제외"""
    if len(vals) <= 1:
        return vals
    filtered = []
    for v in vals:
        others = [x for x in vals if x != v]
        if len(others) == 0:
            filtered.append(v)
            continue
        om = np.median(others)
        if om > 0 and (v / om > ratio or (v > 0 and om / v > ratio)):
            continue
        filtered.append(v)
    return filtered if filtered else list(vals)


def get_plc_ratio_decayed_factory(plc_ratio_df, tail_decay=0.7, *, fw_order_fn, use_subseason=True):
    """PLC_RATIO lookup 클로저. 범위 밖이면 지수감쇠.

    fw_order_fn: WEEK_OF_YEAR→FW_ORDER 변환 (필수, engine에서 season_spec.fw_order 주입).
    FW 주차 수식의 단일 소스는 SeasonSpec.fw_order — 여기서 재정의하지 않는다(일원화).

    서브시즌(ADDITIVE):
      - plc_ratio_df에 SESN_SUB 컬럼이 있으면 (item, sub, wk) 키로 적재.
      - 없으면(기존 파일) 전 행을 (item, 'ALL', wk)로 적재 → 기존과 동일 경로.
      - 반환 클로저 get_plc_ratio_decayed(item, wk, sub=None):
          (item, sub, wk) 시도 → 없으면 (item, 'ALL', wk) 폴백.
          sub=None이면 항상 'ALL' → 기존 동작과 byte-identical.

    use_subseason: SS-only 하드 게이트. False면(=season_type!="ss", engine에서 주입)
      SESN_SUB 컬럼이 있어도 무시하고 ITEM('ALL') 곡선만 적재 → FW는 데이터 실수에도
      서브시즌 절대 미적용(기존 동작 보존). True(기본)면 컬럼 유무로 자동 판별.
    """
    has_sub = use_subseason and 'SESN_SUB' in plc_ratio_df.columns
    df = plc_ratio_df
    if ('SESN_SUB' in df.columns) and not has_sub:
        # 서브 비활성(FW): ITEM 곡선(SESN_SUB=='ALL') 행만 — sub 전용 행 배제로 'ALL' 키 오염 방지
        df = df[df['SESN_SUB'] == 'ALL']
    plc_ratio = {}              # (item, sub, wk) -> ratio
    plc_max_fwo = {}            # (item, sub) -> max FW_ORDER
    plc_last_ratio = {}         # (item, sub) -> ratio at max FW_ORDER
    for _, r in df.iterrows():
        if pd.notna(r['PLC_RATIO']):
            sub = r['SESN_SUB'] if has_sub else 'ALL'
            plc_ratio[(r['ITEM'], sub, int(r['WEEK_NUM']))] = r['PLC_RATIO']
            fwo = int(r['FW_ORDER'])
            if fwo > plc_max_fwo.get((r['ITEM'], sub), -1):
                plc_max_fwo[(r['ITEM'], sub)] = fwo
                plc_last_ratio[(r['ITEM'], sub)] = r['PLC_RATIO']

    def _lookup(item, wk, sub):
        r = plc_ratio.get((item, sub, wk))
        if r is not None:
            return r
        max_fwo = plc_max_fwo.get((item, sub))
        last_r = plc_last_ratio.get((item, sub))
        if max_fwo is None or last_r is None:
            return None
        cur_fwo = fw_order_fn(wk)
        if cur_fwo <= max_fwo:
            return None
        return last_r * (tail_decay ** (cur_fwo - max_fwo))

    def get_plc_ratio_decayed(item, wk, sub=None):
        if sub is not None:
            r = _lookup(item, wk, sub)
            if r is not None:
                return r
        return _lookup(item, wk, 'ALL')

    return get_plc_ratio_decayed
