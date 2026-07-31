"""Snowflake 데이터 일괄 강제 재조회 (force_refresh) — order-ai-share.

현재 brand_config.json(단일 브랜드) 기준으로 Snowflake에서 최신값을 끌어와
data/{brand}/{season}/*.csv 캐시를 덮어쓴다. restored.csv·PLC 표준 csv는 대상 아님.
재조회 후 각 데이터의 max END_DT를 출력해 최신 주차 반영 여부를 검증한다.

원본(order_ai)의 scripts/_refresh_data.py 정본 미러링 — /weekly-refresh 스킬이 호출.
"""
import config_loader as cl
import pandas as pd


def _max_end_dt(df):
    for c in ('END_DT', 'end_dt'):
        if c in df.columns:
            return str(pd.to_datetime(df[c], errors='coerce').max().date())
    return 'no END_DT col'


def main():
    print(f"[Refresh] brand={cl.get_brand()} base={cl.get_base_season()} target={cl.get_target_season()}")

    # 1) d1~d4 (season_raw, weekly_raw, similarity r1/r2, size_data)
    results = cl.prefetch_all(force_refresh=True)

    # 2) GT(ground_truth) 진행중 시즌
    try:
        gt = cl.load_gt(force_refresh=True)
        results['gt'] = 'ok'
    except Exception as e:
        gt = None
        results['gt'] = f'error: {e}'

    # 3) 전년 동주차 스냅샷 (인시즌 to-date YoY 정합용). 비인시즌이면 None(skip).
    try:
        prev_asof = cl.load_season_raw_prev_asof(force_refresh=True)
        results['d1_prev_asof'] = 'ok' if prev_asof is not None else 'skip(비인시즌)'
    except Exception as e:
        prev_asof = None
        results['d1_prev_asof'] = f'error: {e}'

    print("\n=== 재조회 결과 + 최신 주차 검증 ===")
    for key in ('d1', 'd2', 'd3_r1', 'd3_r2', 'd4'):
        df = cl._data_cache.get(key)
        if df is not None:
            print(f"  {key:6s}: {len(df):>8,}행  max END_DT={_max_end_dt(df)}  [{results.get(key)}]")
        else:
            print(f"  {key:6s}: 실패 [{results.get(key)}]")
    if gt is not None:
        print(f"  gt    : {len(gt):>8,}행  max END_DT={_max_end_dt(gt)}  [{results.get('gt')}]")
    else:
        print(f"  gt    : 실패 [{results.get('gt')}]")
    if prev_asof is not None:
        print(f"  d1_prev_asof: {len(prev_asof):>8,}행  [{results.get('d1_prev_asof')}]")
    else:
        print(f"  d1_prev_asof: [{results.get('d1_prev_asof')}]")


if __name__ == '__main__':
    main()
