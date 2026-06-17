import pandas as pd
import json
import os
from config_loader import (
    get_sell_through_threshold, get_shortage_cutoff_date,
    get_diagnosis_thresholds,
    get_weekly_data_path, get_timeseries_output_path,
    align_weekly_for_inseason, get_season_end_for_code, get_base_season,
)

_ST_THRESHOLD = get_sell_through_threshold()
_ST_LABEL = f"결품시점({int(_ST_THRESHOLD * 100)}%)"  # 동적 컬럼명 (현재 설정에 맞춰 생성)
_DIAG = get_diagnosis_thresholds()
# 차트 가로축 cap: 시즌종료(seasonEndByType, SS=08/31) +1개월 여유 = 09/30.
# 엔진과 동일 소스(get_season_end_for_code) 사용 → 시즌 넘는 주차(zero 꼬리)를 차트에서 제외.
_CHART_CAP = get_season_end_for_code(get_base_season()) + pd.DateOffset(months=1)

# 1. 데이터 로드 (Snowflake/CSV 3-tier → 엑셀 폴백)
try:
    from config_loader import load_data as _load_data
    df = _load_data("d2")
except Exception:
    _weekly_path = get_weekly_data_path()
    _csv_path = _weekly_path.replace('.xlsx', '.xlsx - Data.csv')
    try:
        df = pd.read_csv(_csv_path)
    except Exception:
        df = pd.read_excel(_weekly_path, sheet_name=0)

# B(2026-06-11): 인시즌 주차필터(align)가 SS 전년12월(ISO 49~52주) 입고/발주를 누락 → KPI 오류
#   (총발주=0·총입고 음수·ST% 0). 총발주/총입고/총판매는 **필터 전 당해 weekly 전체합**에서 산출.
#   (차트·엔진은 필터 그대로 사용 → 무변동. 룩업: (PART_CD, COLOR_CD).)
_KPI_COLS = ['ORDER_QTY_KR', 'STOR_QTY_KR', 'SALE_QTY_CNS']
_cur_for_kpi = df[df['PERIOD'] == '당해'] if 'PERIOD' in df.columns else df
_has_kpi = set(_KPI_COLS + ['PART_CD', 'COLOR_CD']).issubset(_cur_for_kpi.columns)
_KPI_TOTALS = _cur_for_kpi.groupby(['PART_CD', 'COLOR_CD'])[_KPI_COLS].sum() if _has_kpi else None
_KPI_TOTALS_PART = _cur_for_kpi.groupby('PART_CD')[_KPI_COLS].sum() if _has_kpi else None  # ALL(total) 룩업용

# in-season SS: 당해 weekly를 GT 최신주차까지 정렬 (예측 as-of와 일치 → 곡선 연결). FW는 no-op.
df = align_weekly_for_inseason(df)

# 2. 전처리: 시즌('당해') 데이터 필터링 및 날짜 변환
df_process = df[df['PERIOD'] == '당해'].copy()
# CLASS1 = '의류'만 필터링 (ACC 제외)
if 'CLASS1' in df_process.columns:
    _before = len(df_process)
    df_process = df_process[df_process['CLASS1'].astype(str).str.contains('의류', na=False)].copy()
    print(f"[정보] 의류(CLASS1) 필터링: {_before}행 -> {len(df_process)}행")
df_process['END_DT'] = pd.to_datetime(df_process['END_DT'])

# 입고 실적 없는 품번 제외 (기획만 등록되고 생산/입고가 안 된 스타일)
_parts_with_inbound = df_process.groupby('PART_CD')['STOR_QTY_KR'].sum()
_zero_inbound = _parts_with_inbound[_parts_with_inbound == 0].index.tolist()
if _zero_inbound:
    _before = len(df_process)
    df_process = df_process[~df_process['PART_CD'].isin(_zero_inbound)].copy()
    print(f"[정보] 입고 실적 없는 품번 제외: {len(_zero_inbound)}개 품번 ({_before}행 -> {len(df_process)}행)")

# 발주0·판매0 색상 제외 (미생산 SKU): 전 구간 0값 달력이 total 합산을 오염시키는 것 방지
_color_agg = df_process.groupby(['PART_CD', 'COLOR_CD']).agg(
    _in=('STOR_QTY_KR', 'sum'), _sale=('SALE_QTY_CNS', 'sum')
)
_dead_colors = _color_agg[(_color_agg['_in'] == 0) & (_color_agg['_sale'] == 0)].index
if len(_dead_colors):
    _before = len(df_process)
    _keys = df_process.set_index(['PART_CD', 'COLOR_CD']).index
    df_process = df_process[~_keys.isin(_dead_colors)].copy()
    print(f"[정보] 발주0·판매0 색상 제외: {len(_dead_colors)}개 색상 ({_before}행 -> {len(df_process)}행)")

# -------------------------------------------------------
# 3. 핵심 로직: 스타일별 시계열 패턴 분석 함수
# -------------------------------------------------------
def generate_chart_data(group, init_date):
    """
    그룹(스타일 or 컬러)의 Chart JSON 생성을 위한 데이터 리스트 반환
    판매 시작(최초입고일) 4주 전부터 데이터 포함
    """
    chart_data = []
    reorder_count = 0
    
    # 최초입고일 4주 전 계산
    cutoff_date = None
    if pd.notnull(init_date):
        cutoff_date = init_date - pd.Timedelta(days=28)  # 4주 = 28일
    
    # 그룹별 재고, 판매, 입고 계산 (이미 날짜별로 정렬된 상태라고 가정)
    for _, row in group.iterrows():
        # 최초입고일 4주 전 이후 데이터만 포함
        if cutoff_date is not None and row['END_DT'] < cutoff_date:
            continue
        # 시즌 가로축 cap(09/30) 초과분 제외 → 차트가 시즌 기간 밖으로 안 넘어감
        if _CHART_CAP is not None and row['END_DT'] > _CHART_CAP:
            continue

        label = ''
        if row['STOR_QTY_KR'] > 0 and pd.notnull(init_date) and row['END_DT'] > init_date:
            reorder_count += 1
            label = f'{reorder_count}차 리오더' if reorder_count > 0 else '리오더'
        elif row.get('Sell_Through', 0) >= _ST_THRESHOLD and label == '':
            label = '재고부족'
            
        chart_data.append({
            'date': row['END_DT'].strftime('%m/%d'),
            'sale': int(row['SALE_QTY_CNS']),
            'stock': int(row['STOCK_QTY_KR']) if 'STOCK_QTY_KR' in row else int(row.get('STOCK_QTY', 0)),
            'in': int(row['STOR_QTY_KR']),
            'label': label
        })
    return chart_data

def analyze_style_pattern(group, is_total=False, _part_cd=None, _color=None):
    # 날짜순 정렬
    group = group.sort_values('END_DT')
    
    # [A] 기초 재고 및 누적 흐름 계산
    # 누적 입고 (STOR_QTY_KR)
    group['Cum_In'] = group['STOR_QTY_KR'].cumsum()
    # 누적 판매 (SALE_QTY_CNS)
    group['Cum_Sale'] = group['SALE_QTY_CNS'].cumsum()
    
    # 판매율 (Sell-Through)
    group['Sell_Through'] = group.apply(
        lambda x: x['Cum_Sale'] / x['Cum_In'] if x['Cum_In'] > 0 else 0, axis=1
    )
    
    # [B] 중요 시점 추출
    # 1. 최초 입고일
    in_stock = group[group['STOR_QTY_KR'] > 0]
    init_date = in_stock['END_DT'].min() if not in_stock.empty else pd.NaT
    
    # 2. 리오더 발생일 (최초 입고일 + 14일 이후 입고가 있는 경우)
    reorders = []
    if pd.notnull(init_date):
        reorder_rows = group[
            (group['END_DT'] > init_date + pd.Timedelta(days=14)) & 
            (group['STOR_QTY_KR'] > 0)
        ]
        reorders = reorder_rows['END_DT'].dt.strftime('%m/%d').tolist()
    
    # 3. 결품 임박 시점 (누적 판매율 _ST_THRESHOLD 최초 돌파 주차)
    # 단, 입고가 10장 이상인 유의미한 경우만 체크
    stock_out_row = group[(group['Sell_Through'] >= _ST_THRESHOLD) & (group['Cum_In'] > 10)]
    stock_out_date = stock_out_row['END_DT'].min() if not stock_out_row.empty else pd.NaT
    
    # [C] AI 진단 (Diagnosis)
    total_sale = group['SALE_QTY_CNS'].sum()
    final_str = group['Sell_Through'].iloc[-1] if not group.empty else 0
    total_in = group['STOR_QTY_KR'].sum()
    total_order = group['ORDER_QTY_KR'].sum() if 'ORDER_QTY_KR' in group.columns else (group['ORDER_QTY'].sum() if 'ORDER_QTY' in group.columns else total_in)

    # B: 인시즌 주차필터로 누락된 전년12월 입고/발주 보정 — 필터 전 당해 전체합으로 교체(당해만 매칭).
    #   per-color 호출=(PART,COLOR) 룩업 / ALL(total) 호출=COLOR_CD 없으니 PART 합계 룩업.
    _t = None
    if not is_total and _color is not None and _KPI_TOTALS is not None and _part_cd is not None:
        _k = (_part_cd, _color)                      # per-color: (PART, COLOR) 룩업
        _t = _KPI_TOTALS.loc[_k] if _k in _KPI_TOTALS.index else None
    elif _KPI_TOTALS_PART is not None and _part_cd is not None and _part_cd in _KPI_TOTALS_PART.index:
        _t = _KPI_TOTALS_PART.loc[_part_cd]          # ALL(total): PART 합계 룩업
    if _t is not None:
        total_order = int(_t['ORDER_QTY_KR'])
        total_in = int(_t['STOR_QTY_KR'])
        total_sale = int(_t['SALE_QTY_CNS'])
        final_str = (total_sale / total_in) if total_in > 0 else 0.0  # ST% 재산출(카드+진단)

    # 4분류: Hit / Normal / Shortage / Risk
    # 1차 판단: 최종 판매율 → 2차 판단: 결품 시점 vs CUTOFF (서브시즌별)
    part_cd = _part_cd or (group['PART_CD'].iloc[0] if 'PART_CD' in group.columns else '')
    sub_season = str(part_cd)[-1]  # 스타일코드 끝 1자리
    cutoff_date = get_shortage_cutoff_date(sub_season)

    if final_str >= _DIAG['high']:
        if pd.notnull(stock_out_date) and stock_out_date <= cutoff_date:
            status = "⚠️Shortage"
        else:
            status = "🟢Hit"
    elif final_str >= _DIAG['low']:
        status = "⚪Normal"
    else:
        status = "🔴Risk"

    # [D] 차트 데이터 생성
    chart_data = generate_chart_data(group, init_date)

    # 판매가 (TAG_PRICE) 추출 - 그룹 내 첫 번째 값 사용
    tag_price = group['TAG_PRICE'].iloc[0] if 'TAG_PRICE' in group.columns else 0
    prdt_nm = group['PRDT_NM'].iloc[0] if 'PRDT_NM' in group.columns else ''

    return pd.Series({
        '최초입고': init_date.strftime('%Y-%m-%d') if pd.notnull(init_date) else '-',
        _ST_LABEL: stock_out_date.strftime('%Y-%m-%d') if pd.notnull(stock_out_date) else '-',
        '리오더입고일': ', '.join(reorders),
        '총발주': total_order,
        '총입고': total_in,
        '총판매': total_sale,
        '최종판매율': round(final_str * 100, 1),
        'AI_진단': status,
        '판매가': int(tag_price),
        'PRDT_NM': str(prdt_nm),
        'Chart_JSON': json.dumps(chart_data, ensure_ascii=False) # JSON 문자열로 저장
    })

# 3-1. 이상치 품번 제외 (데이터 이상값 확인된 스타일)
EXCLUDE_STYLES = []
_before = len(df_process)
df_process = df_process[~df_process['PART_CD'].isin(EXCLUDE_STYLES)].copy()
if len(df_process) < _before:
    print(f"[정보] 이상치 품번 제외: {EXCLUDE_STYLES} ({_before}행 -> {len(df_process)}행)")

# 4. 전체 스타일 분석 실행
print("데이터 분석 중...")
result_df = df_process.groupby(['ITEM_NM', 'PART_CD', 'COLOR_CD']).apply(
    lambda g: analyze_style_pattern(g, _part_cd=g.name[1], _color=g.name[2])
).reset_index()

# 5. 결과 저장
# 5-1. 새로운 컬럼 추가 (기회비용 분석용)
result_df['AI 계산 기회비용'] = 0  # 초기값 0, ai_sales_loss_v3.py에서 업데이트
result_df['AI제안 발주량'] = 0      # 초기값 0, ai_sales_loss_v3.py에서 업데이트
result_df['PERIOD'] = '당해'

# 5-2. 전년/재작년 시계열 분석 (과거 ref 실적용)
_prior_results = []
for _period in ['전년', '재작년']:
    _df_prior = df[df['PERIOD'] == _period].copy()
    if _df_prior.empty:
        print(f"[정보] {_period} 데이터 없음 → 스킵")
        continue
    # 당해와 동일한 필터 적용
    if 'CLASS1' in _df_prior.columns:
        _df_prior = _df_prior[_df_prior['CLASS1'].astype(str).str.contains('의류', na=False)].copy()
    _df_prior['END_DT'] = pd.to_datetime(_df_prior['END_DT'])
    _parts_inbound = _df_prior.groupby('PART_CD')['STOR_QTY_KR'].sum()
    _zero = _parts_inbound[_parts_inbound == 0].index.tolist()
    if _zero:
        _df_prior = _df_prior[~_df_prior['PART_CD'].isin(_zero)].copy()
    _df_prior = _df_prior[~_df_prior['PART_CD'].isin(EXCLUDE_STYLES)].copy()
    if _df_prior.empty:
        print(f"[정보] {_period} 필터 후 데이터 없음 → 스킵")
        continue
    print(f"[정보] {_period} 분석 중... ({_df_prior['PART_CD'].nunique()}개 스타일)")
    _result_prior = _df_prior.groupby(['ITEM_NM', 'PART_CD', 'COLOR_CD']).apply(
        lambda g: analyze_style_pattern(g, _part_cd=g.name[1], _color=g.name[2])
    ).reset_index()
    _result_prior['AI 계산 기회비용'] = 0
    _result_prior['AI제안 발주량'] = 0
    _result_prior['PERIOD'] = _period
    _prior_results.append(_result_prior)
    print(f"  → {_period}: {len(_result_prior)}행 ({_result_prior['PART_CD'].nunique()}개 스타일)")

# 5-3. 컬럼 순서 재정렬
column_order = [
    'PERIOD', 'ITEM_NM', 'PRDT_NM', 'PART_CD', '판매가', 'COLOR_CD',
    '최초입고', _ST_LABEL, '리오더입고일',
    '총발주', '총입고', '총판매', '최종판매율',
    'AI_진단', 'AI 계산 기회비용', 'AI제안 발주량',
    'Chart_JSON'
]
result_df = result_df[column_order]

# 전년/재작년 결과 합치기
result_all = result_df.copy()
for _rp in _prior_results:
    _rp = _rp[column_order]
    result_all = pd.concat([result_all, _rp], ignore_index=True)

# 5-4. 엑셀 저장 (Chart_JSON 제외, 3시즌 통합)
_ts_output_path = get_timeseries_output_path()
os.makedirs(os.path.dirname(_ts_output_path), exist_ok=True)
result_all.drop(columns=['Chart_JSON']).to_excel(_ts_output_path, index=False)
print(f"* 분석 결과 저장 완료: {_ts_output_path} ({len(result_all)}행, 당해 {len(result_df)}행 + 과거 {len(result_all)-len(result_df)}행)")

# 6. 대시보드용 JSON 출력 및 저장 (대표 성공/실패 사례 1건씩)
# 6. 대시보드용 JSON 출력 및 저장 (대표 성공/실패 사례 -> Total + Colors 구조로 변환)
print("\n--- [대시보드 데이터 생성 중 (Total + Colors)] ---")

def create_dashboard_entry(part_cd, color_cd, raw_df, anal_df):
    """
    특정 스타일(part_cd)에 대한 대시보드 데이터 생성
    - total: 해당 스타일의 모든 컬러 합산 데이터
    - colors: 각 컬러별 데이터 맵
    """
    # 1. Total Data 생성 (Raw Data에서 다시 집계)
    style_raw_mask = (raw_df['PART_CD'] == part_cd)
    style_raw = raw_df[style_raw_mask].copy()
    
    # 날짜별로 모든 컬러 합산
    agg_dict = {
        'STOR_QTY_KR': 'sum',
        'SALE_QTY_CNS': 'sum',
        'STOCK_QTY_KR': 'sum',
        'TAG_PRICE': 'first'
    }
    # ORDER_QTY_KR 컬럼이 있으면 추가 (fallback: ORDER_QTY)
    if 'ORDER_QTY_KR' in style_raw.columns:
        agg_dict['ORDER_QTY_KR'] = 'sum'
    elif 'ORDER_QTY' in style_raw.columns:
        agg_dict['ORDER_QTY'] = 'sum'
    
    style_total_raw = style_raw.groupby('END_DT').agg(agg_dict).reset_index()
    
    # Total 분석 실행
    total_analysis = analyze_style_pattern(style_total_raw, is_total=True, _part_cd=part_cd)
    
    # ITEM_NM, PRDT_NM 추출
    item_nm = style_raw['ITEM_NM'].iloc[0]
    prdt_nm = style_raw['PRDT_NM'].iloc[0] if 'PRDT_NM' in style_raw.columns else ''
    
    total_entry = {
        'chartData': json.loads(total_analysis['Chart_JSON']),
        'itemInfo': {
            'name': str(item_nm),
            'code': str(part_cd),
            'color': '전체',
            'price': int(total_analysis['판매가']),
            'prdt_nm': str(prdt_nm)
        },
        'analysis': {
            '최초입고': str(total_analysis['최초입고']),
            '결품시점': str(total_analysis[_ST_LABEL]),
            '리오더입고일': str(total_analysis['리오더입고일']),
            '총발주': int(total_analysis['총발주']),
            '총입고': int(total_analysis['총입고']),
            '총판매': int(total_analysis['총판매']),
            '최종판매율': float(total_analysis['최종판매율']),
            'AI_진단': str(total_analysis['AI_진단'])
        }
    }
    
    # 2. Colors Data 수집 (이미 분석된 anal_df 활용)
    # 해당 스타일의 모든 컬러 찾기
    colors_anal = anal_df[anal_df['PART_CD'] == part_cd]
    colors_entry = {}
    
    for _, row in colors_anal.iterrows():
        c_code = str(row['COLOR_CD'])
        colors_entry[c_code] = {
            'chartData': json.loads(row['Chart_JSON']),
            'itemInfo': {
                'name': str(row['ITEM_NM']),
                'code': str(row['PART_CD']),
                'color': c_code,
                'price': int(row['판매가']),
                'prdt_nm': str(prdt_nm)
            },
            'analysis': {
                '최초입고': str(row['최초입고']),
                '결품시점': str(row[_ST_LABEL]),
                '리오더입고일': str(row['리오더입고일']),
                '총발주': int(row['총발주']),
                '총입고': int(row['총입고']),
                '총판매': int(row['총판매']),
                '최종판매율': float(row['최종판매율']),
                'AI_진단': str(row['AI_진단'])
            }
        }
        
    return {
        'total': total_entry,
        'colors': colors_entry
    }

# JSON 구조: 4분류
dashboard_data = {
    'hit': [],
    'normal': [],
    'shortage': [],
    'risk': []
}

# Total 기준으로 스타일 분류 (컬러별 중복 방지)
print("\n[진단별 스타일 수집 (Total 기준)]")
part_codes_all = result_df['PART_CD'].unique()
classified = set()

for part_cd in part_codes_all:
    if part_cd in classified:
        continue
    classified.add(part_cd)

    entry = create_dashboard_entry(part_cd, None, df_process, result_df)
    total_diagnosis = entry['total']['analysis'].get('AI_진단', '')

    if '🟢Hit' in total_diagnosis:
        dashboard_data['hit'].append(entry)
    elif '⚠️Shortage' in total_diagnosis:
        dashboard_data['shortage'].append(entry)
    elif '🔴Risk' in total_diagnosis:
        dashboard_data['risk'].append(entry)
    else:
        dashboard_data['normal'].append(entry)

# 각 분류를 총판매 내림차순 정렬
for key in dashboard_data:
    dashboard_data[key].sort(key=lambda x: x['total']['analysis'].get('총판매', 0), reverse=True)

hit_count = len(dashboard_data['hit'])
normal_count = len(dashboard_data['normal'])
shortage_count = len(dashboard_data['shortage'])
risk_count = len(dashboard_data['risk'])

print(f"  - 🟢Hit: {hit_count}개 스타일")
print(f"  - ⚪Normal: {normal_count}개 스타일")
print(f"  - ⚠️Shortage: {shortage_count}개 스타일")
print(f"  - 🔴Risk: {risk_count}개 스타일")

total_count = hit_count + normal_count + shortage_count + risk_count
print(f"\n* 총 {total_count}개 스타일 대시보드 데이터 생성 완료 (중복 없음)")

# JSON 파일로 저장 (루트 및 public 폴더)
import os

# output 폴더에 저장
with open('../output/dashboard_data.json', 'w', encoding='utf-8') as f:
    json.dump(dashboard_data, f, ensure_ascii=False, indent=2)

# public 폴더에도 저장 (React 앱용)
os.makedirs('../public', exist_ok=True)
with open('../public/dashboard_data.json', 'w', encoding='utf-8') as f:
    json.dump(dashboard_data, f, ensure_ascii=False, indent=2)

print("* 대시보드 데이터 저장 완료: dashboard_data.json (구조: Total + Colors)")


# 7. 과거 시즌 PART_CD 데이터 (Step 3 직접입력 ref-style lookup용)
#    dashboard_data.json은 당해 진단(hit/normal/shortage/risk) 분류라 당해만 담음.
#    전년/재작년 PART_CD는 별도 past_styles_data.json으로 분리 적재.
print("\n--- [과거 시즌 데이터 생성 중 (prev/prev2)] ---")
past_styles = {"prev": {}, "prev2": {}}
for _period_kr, _key in (("전년", "prev"), ("재작년", "prev2")):
    _df_period_raw = df[df['PERIOD'] == _period_kr].copy()
    if _df_period_raw.empty:
        print(f"  - {_period_kr}: raw 없음 → 스킵")
        continue
    if 'CLASS1' in _df_period_raw.columns:
        _df_period_raw = _df_period_raw[
            _df_period_raw['CLASS1'].astype(str).str.contains('의류', na=False)
        ].copy()
    _df_period_raw['END_DT'] = pd.to_datetime(_df_period_raw['END_DT'])
    _df_period_anal = result_all[result_all['PERIOD'] == _period_kr]
    if _df_period_anal.empty:
        print(f"  - {_period_kr}: 분석 결과 없음 → 스킵")
        continue
    for _part_cd in _df_period_anal['PART_CD'].unique():
        _entry = create_dashboard_entry(_part_cd, None, _df_period_raw, _df_period_anal)
        past_styles[_key][_part_cd] = _entry
    print(f"  - {_period_kr}: {len(past_styles[_key])}개 PART_CD")

with open('../output/past_styles_data.json', 'w', encoding='utf-8') as f:
    json.dump(past_styles, f, ensure_ascii=False, indent=2)
with open('../public/past_styles_data.json', 'w', encoding='utf-8') as f:
    json.dump(past_styles, f, ensure_ascii=False, indent=2)
print(f"* 과거 시즌 데이터 저장 완료: past_styles_data.json "
      f"(prev {len(past_styles['prev'])} + prev2 {len(past_styles['prev2'])})")


# 8. season_closing_data.json metadata.data_through 주입 (당해 주차 raw 마감일)
print("\n--- [season_closing metadata.data_through 주입] ---")
_data_through = df_process['END_DT'].max().strftime('%Y-%m-%d')
for _sc_path in ('../output/season_closing_data.json', '../public/season_closing_data.json'):
    if not os.path.exists(_sc_path):
        continue
    with open(_sc_path, 'r', encoding='utf-8') as f:
        _sc = json.load(f)
    _sc.setdefault('metadata', {})['data_through'] = _data_through
    with open(_sc_path, 'w', encoding='utf-8') as f:
        json.dump(_sc, f, ensure_ascii=False, indent=2)
print(f"* season_closing metadata.data_through = {_data_through}")