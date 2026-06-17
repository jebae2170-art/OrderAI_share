"""
공유 설정 로더: public/brand_config.json에서 설정값 읽기
없으면 현재 하드코딩된 기본값과 동일한 값 사용

모든 Python 스크립트는 이 모듈을 통해 브랜드/시즌/파일경로를 참조해야 합니다.
UI(Step 0 BrandIndexSetup)에서 설정 → 서버 API → public/brand_config.json 저장 → 여기서 읽기
"""

import json
import os
from datetime import date

import pandas as pd

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_BASE_DIR, 'public', 'brand_config.json')
_config_cache = None


def _load_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
                print(f"[Config] brand_config.json 로드 완료")
                return _config_cache
        except Exception as e:
            print(f"[Config] brand_config.json 파싱 실패, 기본값 사용: {e}")

    _config_cache = {}
    return _config_cache


# ── 브랜드 / 시즌 ──────────────────────────────────────────

def get_brand():
    """브랜드명. order-ai-share 단일 브랜드 모드:
    - BRAND 환경변수와 brand_config.json::brand 가 모두 있고 불일치하면 RuntimeError.
    - 둘 다 없으면 RuntimeError (기본값 없음 — 의도된 fail-fast).
    - case 는 brand_config.json 의 원본 표기 유지 (Discovery, Duvetica 등 mixed-case lookup 호환).

    [v3-#1] [I8] order-ai-share 의 단일 브랜드 자치 모델.
    """
    import os
    env_brand = os.environ.get("BRAND", "").strip()
    config = _load_config()
    config_brand = (config.get('brand') or '').strip()

    if env_brand and config_brand and env_brand.upper() != config_brand.upper():
        raise RuntimeError(
            f"[Config] BRAND 환경변수({env_brand!r})와 brand_config.json({config_brand!r})가 "
            f"불일치합니다. 둘 중 하나를 수정하세요.\n"
            f"  - .env: BRAND={env_brand}\n"
            f"  - public/brand_config.json: \"brand\": \"{config_brand}\""
        )

    brand = config_brand or env_brand
    if not brand:
        raise RuntimeError(
            "[Config] BRAND 환경변수 또는 brand_config.json 에 brand 를 설정하세요.\n"
            "  예: .env 의 BRAND=DUVETICA, 또는 brand_config.json 의 'brand' 필드"
        )
    return brand


def get_base_season():
    """기준(분석) 시즌 코드 (기본: '25S')"""
    return _load_config().get('baseSeason', '25S')


def get_target_season():
    """기획(발주) 시즌 코드 (기본: '26S')"""
    return _load_config().get('targetSeason', '26S')


def get_prev_season():
    """전년 시즌 코드: baseSeason의 연도 -1, 시즌 유형 유지. '25F' → '24F', '25S' → '24S'"""
    base = get_base_season()
    try:
        year = int(base[:2])
        season_type = base[2:]  # 'F', 'S', 'FW', 'SS' 등
        return f"{year - 1:02d}{season_type}"
    except (ValueError, IndexError):
        return base


def get_prev2_season():
    """재작년 시즌 코드: baseSeason의 연도 -2, 시즌 유형 유지. '25F' → '23F', '25S' → '23S'"""
    base = get_base_season()
    try:
        year = int(base[:2])
        season_type = base[2:]
        return f"{year - 2:02d}{season_type}"
    except (ValueError, IndexError):
        return base


def _season_year(season_code=None):
    """시즌 코드에서 연도 추출: '25S' → 2025, '25FW' → 2025"""
    code = season_code or get_base_season()
    try:
        return 2000 + int(code[:2])
    except (ValueError, IndexError):
        return 2025


# ── 등급 / 임계값 ──────────────────────────────────────────

def get_grade_thresholds():
    """등급 임계값 반환: {'S': 75, 'A': 65, 'B': 55, 'C': 40}"""
    cfg = _load_config()
    thresholds = cfg.get('gradeThresholds', {})
    return {
        'S': thresholds.get('S', 75),
        'A': thresholds.get('A', 65),
        'B': thresholds.get('B', 55),
        'C': thresholds.get('C', 40),
    }


def get_target_sell_through():
    """기본 목표 판매율 (default 65% = 0.65, 현실 수준) — 일반 스타일 발주량 역산용"""
    cfg = _load_config()
    value = cfg.get('targetSellThrough', 65)
    return value / 100  # % → 비율


def get_high_volume_target_sell_through():
    """대물량(Top 5%) 스타일 목표 판매율 (default 70% = 0.70) — 매출 견인 라인은 빡빡하게"""
    cfg = _load_config()
    value = cfg.get('highVolumeTargetSellThrough', 70)
    return value / 100  # % → 비율


def get_high_volume_top_percent():
    """대물량 분류 비율 (default 5 = 상위 5%) — 시즌 의류 당해 PART_CD 잠재수요 정렬 기준"""
    cfg = _load_config()
    return cfg.get('highVolumeTopPercent', 5)


def get_sell_through_threshold():
    """상업적 결품 감지 판매율 기준 (기본 70% = 0.7) — 사이즈 깨짐으로 판매력 하락 시점"""
    cfg = _load_config()
    value = cfg.get('commercialStockoutThreshold', 70)
    return value / 100  # % → 비율


def get_shortage_loss_thresholds():
    """Shortage 재분류 임계값 (Hit/Normal → Shortage 이동 기준).

    장수 ≥ minQty AND (비율 ≥ minRatio OR 금액 ≥ minAmt) 이면 Shortage로 재분류.
    """
    cfg = _load_config()
    t = cfg.get('shortageThresholds', {})
    return {
        'min_qty': t.get('minQty', 200),
        'min_ratio': t.get('minRatio', 0.20),
        'min_amt': t.get('minAmt', 100_000_000),
    }


def get_diagnosis_thresholds():
    """AI 진단 분류 임계값 — 등급 기준(S/B)에서 자동 파생
    S등급 기준 이상: Hit or Shortage, B등급 기준 이상: Normal, 미만: Risk
    """
    grades = get_grade_thresholds()
    return {
        'high': grades['S'] / 100,
        'low': grades['B'] / 100,
    }


# ── 날짜 ────────────────────────────────────────────────────

def _crosses_year_boundary(cfg):
    """FW 시즌처럼 endDate가 startDate보다 이른 월이면 연도 넘김"""
    start_month = int(cfg.get('startDate', {}).get('month', '01'))
    end_month = int(cfg.get('endDate', {}).get('month', '12'))
    return end_month < start_month


def get_season_end_date(sub_season=None):
    """시즌 종료일(감쇠 종착점) 반환 (pd.Timestamp).
    sub_season: 스타일코드 끝 1자리 ('1'=Spring, '3'=Summer, '4'=Fall, '6'=Winter)
    서브시즌별: Spring→06/30, Summer→08/31, Fall→12/31, Winter→02/28
    """
    cfg = _load_config()
    year = _season_year()

    # 서브시즌별 디폴트 (month, day, 연도넘김 여부)
    _sub_defaults = {
        '1': ('06', '30', False),  # Spring → 같은해 6/30
        '3': ('08', '31', False),  # Summer → 같은해 8/31
        '4': ('12', '31', False),  # Fall → 같은해 12/31
        '6': ('02', '28', True),   # Winter → 이듬해 2/28
    }

    if sub_season and sub_season in _sub_defaults:
        month, day, cross_year = _sub_defaults[sub_season]
        if cross_year:
            year += 1
        return pd.Timestamp(f'{year}-{month}-{day}')

    # 기존 로직 (sub_season 미지정 시 — config endDate 참조)
    end = cfg.get('endDate', {})
    month = end.get('month', '09')
    day = end.get('day', '30')
    if _crosses_year_boundary(cfg):
        year += 1
    return pd.Timestamp(f'{year}-{month}-{day}')


def get_season_end_cutoff(sub_season=None):
    """시즌 마감 기준일 = 시즌종료일 + 1개월 (AI 예측 차단 시점)"""
    return get_season_end_date(sub_season) + pd.DateOffset(months=1)


# 시즌타입별 마감일 내부 디폴트 (seasonEndByType 미설정 시 폴백)
_SEASON_END_DEFAULTS_BY_TYPE = {
    'FW': ('02', '28'),
    'SS': ('08', '31'),
}


def get_season_end_for_code(season_code):
    """시즌코드(예: '25F', '24S') → 마감실적 기준 종료일 (pd.Timestamp).

    - FW (code 끝 'F'): 종료는 다음해 (예: 25F → 2026-02-28)
    - SS (code 끝 'S'): 종료는 같은해 (예: 24S → 2024-08-31)

    우선순위: brand_config.seasonEndByType → 내부 디폴트
    """
    cfg = _load_config()
    map_by_type = cfg.get('seasonEndByType', {})

    try:
        year = 2000 + int(season_code[:2])
    except (ValueError, IndexError):
        year = 2025

    suffix = season_code[-1].upper() if season_code else 'F'
    type_key = 'FW' if suffix == 'F' else 'SS'

    default_month, default_day = _SEASON_END_DEFAULTS_BY_TYPE.get(type_key, ('02', '28'))
    entry = map_by_type.get(type_key, {})
    month = entry.get('month', default_month)
    day = entry.get('day', default_day)

    if type_key == 'FW':
        year += 1

    return pd.Timestamp(f'{year}-{month}-{day}')


def get_shortage_cutoff_date(sub_season=None):
    """Hit/Shortage 구분 기준일 (이 날짜 이전 결품 = Shortage, 이후 = Hit)
    sub_season: 스타일코드 끝 1자리 ('1'=Spring, '3'=Summer, '4'=Fall, '6'=Winter)

    우선순위:
    1. brand_config.json의 subSeasonCutoff (UI에서 오버라이드한 값)
    2. 내부 디폴트 (Spring→05/15, Summer→06/30, Fall→11/15, Winter→12/31)
    3. 폴백: seasonType 기반
    """
    cfg = _load_config()
    year = _season_year()

    _sub_code_to_name = {
        '1': 'Spring',
        '3': 'Summer',
        '4': 'Fall',
        '6': 'Winter',
    }

    _sub_defaults = {
        'Spring': ('05', '15'),
        'Summer': ('06', '30'),
        'Fall':   ('11', '15'),
        'Winter': ('12', '31'),
    }

    if sub_season and sub_season in _sub_code_to_name:
        name = _sub_code_to_name[sub_season]

        # 1순위: UI에서 설정한 subSeasonCutoff
        ui_cutoff = cfg.get('subSeasonCutoff', {}).get(name, {})
        if ui_cutoff and ui_cutoff.get('month'):
            month = ui_cutoff['month']
            day = ui_cutoff.get('day', '15')
            return pd.Timestamp(f'{year}-{month}-{day}')

        # 2순위: 내부 디폴트
        month, day = _sub_defaults[name]
        return pd.Timestamp(f'{year}-{month}-{day}')

    # 폴백: seasonType 기반
    season_type = cfg.get('seasonType', 'SS')
    if season_type == 'FW':
        month, day = '12', '31'
    else:
        month, day = '06', '30'

    return pd.Timestamp(f'{year}-{month}-{day}')


# ── 파일 경로 ────────────────────────────────────────────────
# 구조: data/{brand}/{season}/season_raw.xlsx 등

def _data_dir():
    """브랜드/시즌별 data 디렉토리: data/{brand_lower}/{baseSeason_lower}/"""
    brand = get_brand().lower()
    season = get_base_season().lower()
    return os.path.join(_BASE_DIR, 'data', brand, season)


def get_input_data_path():
    """시즌 마감 원시 데이터 경로 (season_raw.xlsx)"""
    return os.path.join(_data_dir(), 'season_raw.xlsx')


def get_weekly_data_path():
    """주간 시계열 데이터 경로 (weekly_raw.xlsx)"""
    return os.path.join(_data_dir(), 'weekly_raw.xlsx')


def align_weekly_for_inseason(weekly_df):
    """in-season SS: 당해 weekly를 GT(ground_truth) 최신주차까지로 정렬.

    예측 as-of는 GT 기준이므로 화면 실적(weekly)도 그 시점에 맞춰야 예측과 매끄럽게
    이어진다. FW(코드 끝 'F')나 GT==weekly 시점이면 사실상 no-op. 전년/재작년 행은 유지.
    (보너스: 엔진 build_week_frame이 미래주차를 안 넣어 predicted_total ffill 누수도 차단.)
    """
    try:
        if get_base_season()[-1].upper() != 'S':   # SS만 적용 (FW 무영향)
            return weekly_df
        gt = load_gt()
        # as-of = 최신 실판매 주차. WEEK_OF_YEAR.max()는 교차연도 버퍼(Dec=woy49~52)에
        #   오염되므로 최신 END_DT 날짜의 ISO주차로 산출 (SS as-of는 1~35주, wrap 없음).
        gt_max = int(pd.to_datetime(gt['END_DT']).max().isocalendar()[1])
    except Exception:
        return weekly_df
    if 'PERIOD' not in weekly_df.columns or 'END_DT' not in weekly_df.columns:
        return weekly_df
    df = weekly_df.copy()
    woy = pd.to_datetime(df['END_DT']).dt.isocalendar().week.astype(int)
    cur = df['PERIOD'] == '당해'
    return df[(~cur) | (woy <= gt_max)].reset_index(drop=True)


def get_similarity_mapping_path():
    """유사스타일 매핑 경로 (similarity_mapping.csv)"""
    return os.path.join(_data_dir(), 'similarity_mapping.csv')


def get_size_data_path():
    """사이즈 배분 원시 데이터 경로 (fw_size_data.xlsx)"""
    return os.path.join(_data_dir(), 'fw_size_data.xlsx')


def get_color_mapping_path():
    """컬러레인지 그룹 매핑 파일 경로 (FNF_GROUP_COLOR_*.xlsx) — 엑셀 원본 (재생성용)"""
    data_root = os.path.join(_BASE_DIR, 'data')
    for f in os.listdir(data_root):
        if f.startswith('FNF_GROUP_COLOR_') and f.endswith('.xlsx'):
            return os.path.join(data_root, f)
    return os.path.join(data_root, 'FNF_GROUP_COLOR.xlsx')


def get_color_mapping():
    """컬러코드 → COLOR_RANGE 매핑 dict 반환 (public/color_mapping.json 기반)

    Returns:
        dict: {"BKS": "BLACK", "WHM": "WHITE", ...} (최종 대문자 그룹)
    """
    json_path = os.path.join(_BASE_DIR, 'public', 'color_mapping.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # color_to_final: 최종 컬럼 기반 (BLACK, WHITE 등)
        if 'color_to_final' in data:
            return data['color_to_final']
        # 폴백: color_to_group (1차고민 기반)
        return data.get('color_to_group', {})
    # JSON 없으면 엑셀 폴백
    return _load_color_mapping_from_excel()


def _load_color_mapping_from_excel():
    """엑셀에서 직접 컬러 매핑 로드 (JSON 없을 때 폴백)"""
    path = get_color_mapping_path()
    if not os.path.exists(path):
        return {}
    try:
        cm = pd.read_excel(path, skiprows=1)
        cm = cm[['상세코드', '최종']].dropna(subset=['최종'])
        return dict(zip(cm['상세코드'], cm['최종']))
    except Exception:
        return {}


def get_analysis_output_path():
    """시즌 마감 분석 Excel 출력 경로: {baseSeason}_Analysis_Result.xlsx"""
    season = get_base_season()
    return os.path.join(_BASE_DIR, 'output', f'{season}_Analysis_Result.xlsx')


def get_timeseries_output_path():
    """시계열 분석 Excel 출력 경로: {baseSeason}_TimeSeries_Analysis_Result.xlsx"""
    season = get_base_season()
    return os.path.join(_BASE_DIR, 'output', f'{season}_TimeSeries_Analysis_Result.xlsx')


def get_order_output_path(ext='xlsx'):
    """발주 추천 출력 경로: {targetSeason}_Order_Recommendation.{ext}"""
    season = get_target_season()
    return os.path.join(_BASE_DIR, 'output', f'{season}_Order_Recommendation.{ext}')


def get_budget_config_path():
    """예산 설정 경로: output/budget_config.json"""
    return os.path.join(_BASE_DIR, 'output', 'budget_config.json')


# ── PLC 엔진 v2 경로 + Spec 로더 ──
PLC_ENGINE_CONFIG_PATH = os.path.join(_BASE_DIR, 'public', 'plc_engine_config.json')

def get_gt_path():
    return os.path.join(_data_dir(), 'ground_truth.csv')

def get_restored_path():
    return os.path.join(_data_dir(), 'restored.csv')

def get_plc_forecast_path():
    # 표준 PLC: brand × season_type 별 분리 파일.
    # data/plc/{brand_lower}_{type}_plc_forecast_standard.csv
    # 예: data/plc/mlb_fw_plc_forecast_standard.csv
    # season_type 은 targetSeason 끝 글자로 도출 (F→fw, S→ss).
    brand = get_brand().lower()
    target = get_target_season()
    if not target:
        raise RuntimeError("brand_config.json::targetSeason 누락 — PLC 경로 도출 불가")
    last = target[-1].upper()
    if last == 'F':
        season_type = 'fw'
    elif last == 'S':
        season_type = 'ss'
    else:
        raise RuntimeError(
            f"targetSeason={target!r} 의 type 도출 불가. 끝 글자가 F/S 여야 함."
        )
    return os.path.join(
        _BASE_DIR, 'data', 'plc',
        f'{brand}_{season_type}_plc_forecast_standard.csv',
    )

def get_dashboard_json_path():
    return os.path.join(_BASE_DIR, 'public', 'dashboard_data.json')

def load_plc_engine_specs():
    from plc_engine.specs import BrandSpec, SeasonSpec, EngineParams
    brand_code = get_brand().lower()
    season_code = get_base_season()
    brand = BrandSpec.from_json(PLC_ENGINE_CONFIG_PATH, brand_code)
    season = SeasonSpec.from_json(PLC_ENGINE_CONFIG_PATH, season_code)
    params = EngineParams.from_json(PLC_ENGINE_CONFIG_PATH, brand_code)
    return brand, season, params


# ── 데이터 로딩 레이어 ──────────────────────────────────────────
# 3-tier: 1차 메모리 → 2차 parquet → 3차 Snowflake(미구현) → 4차 엑셀 폴백

_data_cache = {}  # {"d1": DataFrame, "d2": DataFrame, ...}

# SQL 파일 → 엑셀 폴백 경로 매핑
_DATA_SOURCES = {
    "d1":    {"sql": "d1_season_raw.sql",              "csv": "season_raw.csv"},
    "d2":    {"sql": "d2_weekly_raw.sql",              "csv": "weekly_raw.csv"},
    "d3_r1": {"sql": "d3_similarity_mapping.sql",      "csv": "similarity_mapping_r1.csv"},
    "d3_r2": {"sql": "d3_r2_similarity_gtm_image.sql", "csv": "similarity_mapping_r2.csv"},
    "d4":    {"sql": "d4_size_data.sql",               "csv": "size_data.csv"},
}


def load_query(filename, **params):
    """queries/ 폴더에서 SQL 파일 읽기 + 파라미터 치환 (주석 내 중괄호 안전 처리)"""
    sql_path = os.path.join(_BASE_DIR, "queries", filename)
    if not os.path.exists(sql_path):
        return None
    with open(sql_path, "r", encoding="utf-8") as f:
        template = f.read()
    # 주석 행(-- ...)은 치환 대상에서 제외
    lines = []
    for line in template.split("\n"):
        if line.strip().startswith("--"):
            lines.append(line)
        else:
            lines.append(line.format(**params))
    return "\n".join(lines)


def _csv_path(key):
    """CSV 저장 파일 경로"""
    source = _DATA_SOURCES.get(key)
    if not source:
        return None
    return os.path.join(_data_dir(), source["csv"])


def _load_from_csv(key):
    """2차: CSV 파일에서 로드"""
    path = _csv_path(key)
    if path and os.path.exists(path):
        print(f"[DataLoader] {key}: CSV 로드 ({path})")
        return pd.read_csv(path, encoding="utf-8-sig")
    return None


def _save_to_csv(key, df):
    """CSV 파일 저장"""
    path = _csv_path(key)
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"[DataLoader] {key}: CSV 저장 ({len(df):,}행 → {path})")


def _load_from_snowflake(key):
    """3차: Snowflake 조회 → DataFrame"""
    try:
        from scripts import snowflake_client
    except ImportError:
        try:
            import snowflake_client
        except ImportError:
            return None

    source = _DATA_SOURCES.get(key)
    if not source:
        return None

    params = get_query_params()
    sql = load_query(source["sql"], **params)
    if not sql:
        print(f"[DataLoader] {key}: SQL 파일 없음 ({source['sql']})")
        return None

    print(f"[DataLoader] {key}: Snowflake 조회 중...")
    df = snowflake_client.execute_query(sql)
    return df


def load_data(key, force_refresh=False):
    """데이터 로딩 통합 함수

    로드 순서:
      1차: 메모리 캐시 → 즉시 반환
      2차: CSV 파일 → 로드
      3차: Snowflake 조회 → CSV 저장 + 메모리
      없으면: 에러

    Args:
        key: 데이터 키 ("d1", "d2", "d3", "d4")
        force_refresh: True면 캐시/CSV 무시하고 Snowflake 재조회

    Returns:
        pd.DataFrame

    Raises:
        RuntimeError: 모든 소스에서 데이터를 가져올 수 없을 때
    """
    if key not in _DATA_SOURCES:
        raise RuntimeError(f"[DataLoader] 알 수 없는 데이터 키: {key}")

    # 1차: 메모리 캐시
    if not force_refresh and key in _data_cache:
        print(f"[DataLoader] {key}: 메모리 캐시 반환")
        return _data_cache[key]

    # 2차: CSV 파일
    if not force_refresh:
        df = _load_from_csv(key)
        if df is not None:
            _data_cache[key] = df
            return df

    # 3차: Snowflake
    df = _load_from_snowflake(key)
    if df is not None:
        _data_cache[key] = df
        _save_to_csv(key, df)
        return df

    raise RuntimeError(
        f"[DataLoader] {key}: 데이터 조회 실패. "
        f"Snowflake 연결을 확인하거나 CSV 파일({_csv_path(key)})을 배치해주세요."
    )


def prefetch_all(force_refresh=False):
    """Step 0 저장 시 호출 — 4개 데이터 모두 로드

    Returns:
        dict: {"d1": "ok", "d2": "ok", "d3": "error: ...", ...}
    """
    results = {}
    for key in _DATA_SOURCES:
        try:
            load_data(key, force_refresh=force_refresh)
            results[key] = "ok"
        except RuntimeError as e:
            results[key] = f"error: {e}"
            print(f"[DataLoader] {key}: 로드 실패 — {e}")
    print(f"[DataLoader] 전체 로드 결과: {results}")
    return results


def clear_data_cache():
    """데이터 캐시 전체 초기화 (브랜드/시즌 변경 시)"""
    _data_cache.clear()
    _gt_mem.clear()
    for key in _DATA_SOURCES:
        path = _csv_path(key)
        if path and os.path.exists(path):
            os.remove(path)
            print(f"[DataLoader] {key}: CSV 삭제")
    # 특수 캐시 (전년 동주차 스냅샷) 도 정리
    extra = os.path.join(_data_dir(), "season_raw_prev_asof.csv")
    if os.path.exists(extra):
        os.remove(extra)
        print("[DataLoader] d1_prev_asof: CSV 삭제")
    print("[DataLoader] 캐시 초기화 완료")


# ── GT(ground_truth) 전용 로더 ──────────────────────────────────
# _DATA_SOURCES와 분리: GT는 시즌 진행상태별로 포맷이 다르다 (FW/SS 무관, 인시즌 vs 마감).
#   · 인시즌(진행중) : 단일시즌 raw (복원 전이라 ADJ 없음, 25/26컬럼) — Snowflake gt_ongoing.sql 최신값
#   · 마감시즌       : 멀티시즌+ADJ 통합 (복원 완료, 18~20컬럼) — 엔지니어 제공 로컬 CSV 그대로 (복원수요 Snowflake 미적재)
#   판별: ADJ 컬럼 유무 (복원은 시즌 종료 후 산출 → 인시즌엔 없음).
# _DATA_SOURCES에 넣지 않는 이유: clear_data_cache가 마감시즌 멀티시즌 GT를 삭제하는 사고 방지.

_gt_mem = {}  # {(brand, season): DataFrame} — 프로세스 내 1회 조회 캐시


def _load_gt_from_snowflake():
    """인시즌 GT를 Snowflake에서 조회 (gt_ongoing.sql). 실패 시 None."""
    try:
        from scripts import snowflake_client
    except ImportError:
        try:
            import snowflake_client
        except ImportError:
            return None
    sql = load_query("gt_ongoing.sql", **get_query_params())
    if not sql:
        return None
    print("[GT] Snowflake 조회 중 (gt_ongoing.sql)...")
    return snowflake_client.execute_query(sql)


def load_gt(force_refresh=False):
    """GT(ground_truth) 로더.

    - 마감시즌(로컬 GT에 ADJ 복원 컬럼 존재) → 기존 로컬 CSV 그대로 (Snowflake 미적용).
    - 인시즌(단일시즌 raw, ADJ 없음) → Snowflake 최신값 우선, 실패 시 로컬 CSV 폴백.
      (사용자 합의 2026-05-28: 진행중 시즌만 최신값 업데이트, 과거 복원수요는 로컬 restored.csv 유지)
      ※ FW/SS 구분이 아니라 시즌 진행상태 기준 — 26FW 진행 중 27FW 발주 시점도 인시즌으로 동일 처리.
    """
    path = get_gt_path()

    # 마감시즌(복원 완료, 멀티시즌+ADJ): 엔지니어 로컬 CSV 그대로 — Snowflake 단일시즌 쿼리로 덮어쓰지 않음
    if os.path.exists(path) and 'ADJ_SC_SALE_QTY_TAX' in pd.read_csv(path, nrows=0).columns:
        return pd.read_csv(path)

    # 인시즌: 메모리 → Snowflake → 로컬 raw CSV 폴백
    ck = (get_brand(), get_base_season())
    if not force_refresh and ck in _gt_mem:
        return _gt_mem[ck]

    df = _load_gt_from_snowflake()
    if df is not None:
        _gt_mem[ck] = df
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_csv(path, index=False, encoding='utf-8-sig')
        print(f"[GT] Snowflake {len(df):,}행 → 캐시 ({path})")
        return df

    if os.path.exists(path):
        print(f"[GT] Snowflake 실패 → 로컬 CSV 폴백 ({path})")
        return pd.read_csv(path)
    raise RuntimeError(f"[GT] 로드 실패: Snowflake 연결 또는 로컬 CSV({path}) 필요")


def get_query_params():
    """SQL 파라미터 반환 — load_query()에 전달용.

    _end_date          = 시즌마감일 (엄격, D1 season_raw용)
    _end_date_buffered = 시즌마감일 + 8주 (여유, D2 weekly_raw 그래프 X축용)
    """
    # UI ID(공백 없음)와 풀네임 모두 수용 — BrandIndexSetup.jsx의 brandOptions ID는 'MLBKids'/'Sergio'로 저장됨
    brand_map = {
        'MLB': 'M',
        'Discovery': 'X',
        'Duvetica': 'V',
        'MLB KIDS': 'I', 'MLBKids': 'I',
        'Sergio Tacchini': 'ST', 'Sergio': 'ST',
    }
    brand_name = get_brand()
    base = get_base_season()
    prev = get_prev_season()
    prev2 = get_prev2_season()

    base_end = get_season_end_for_code(base)
    prev_end = get_season_end_for_code(prev)
    prev2_end = get_season_end_for_code(prev2)
    buf = pd.Timedelta(weeks=8)

    return {
        "brand": brand_map.get(brand_name, brand_name),
        "base_season": base,
        "prev_season": prev,
        "prev2_season": prev2,
        "target_season": get_target_season(),
        "base_end_date": base_end.strftime('%Y-%m-%d'),
        "prev_end_date": prev_end.strftime('%Y-%m-%d'),
        "prev2_end_date": prev2_end.strftime('%Y-%m-%d'),
        "base_end_date_buffered": (base_end + buf).strftime('%Y-%m-%d'),
        "prev_end_date_buffered": (prev_end + buf).strftime('%Y-%m-%d'),
        "prev2_end_date_buffered": (prev2_end + buf).strftime('%Y-%m-%d'),
    }


# ── 인시즌 전년 동주차 (YoY 정합) ─────────────────────────────
# to-date 실적 KPI(총매출/입고/판매율/누계판매율)의 전년비는 "전년 동주차" 기준이어야
# 사과-대-사과다. 전년 마감(8/31)과 비교하면 당해 부분시즌(as-of)과 성숙도가 어긋나
# 판매율 하락이 과장된다. (마감예상판매율 등 to-end 지표는 별도로 전년 마감과 비교)

def get_current_asof_date():
    """당해 in-season as-of 기준일 = GT(ground_truth) 최신 주마감(END_DT). 실패 시 None."""
    try:
        gt = load_gt()
    except Exception:
        return None
    if gt is None or 'END_DT' not in gt.columns or len(gt) == 0:
        return None
    return pd.to_datetime(gt['END_DT'], errors='coerce').max()


def is_inseason():
    """진행중(in-season) 여부: base가 SS(코드 끝 'S') + as-of가 시즌마감 이전.

    FW·마감 시즌은 False → 전년 동주차 보정 미적용(현행 마감 기준 그대로).
    """
    base = get_base_season()
    if not base or base[-1].upper() != 'S':
        return False
    asof = get_current_asof_date()
    if asof is None:
        return False
    return asof < get_season_end_for_code(base)


def get_prev_asof_dates():
    """전년/재작년 '동주차'(전년 동일 ISO주차 일요일) 누적 종료일. 인시즌 아니면 None.

    예: 당해 as-of 2026-06-07(ISO 2026-W23-일) → 전년 2025-06-08, 재작년 2024-06-09.
    """
    if not is_inseason():
        return None
    asof = get_current_asof_date()
    iso = asof.isocalendar()
    iso_year, iso_week = int(iso[0]), int(iso[1])

    def _same_week_sunday(year):
        try:
            return date.fromisocalendar(year, iso_week, 7)
        except ValueError:
            # 해당 연도에 그 ISO주차가 없으면 364일(52주) 단위로 역산 근사
            return (asof - pd.Timedelta(days=364 * (iso_year - year))).date()

    return {
        "prev_asof_date": _same_week_sunday(iso_year - 1).strftime('%Y-%m-%d'),
        "prev2_asof_date": _same_week_sunday(iso_year - 2).strftime('%Y-%m-%d'),
    }


def load_season_raw_prev_asof(force_refresh=False):
    """전년/재작년을 '동주차' 누적 컷오프로 가져온 season_raw (d1 SQL 재사용).

    당해 행은 d1과 동일. 전년/재작년만 마감(8/31)이 아닌 동주차로 컷한다.
    인시즌이 아니면 None. 캐시: data/{brand}/{season}/season_raw_prev_asof.csv
    (전년은 마감시즌이라 안정적이나 as-of 주차가 매주 이동 → _refresh_data가 force_refresh로 갱신)
    """
    asof_dates = get_prev_asof_dates()
    if asof_dates is None:
        return None

    csv_path = os.path.join(_data_dir(), "season_raw_prev_asof.csv")
    if not force_refresh and os.path.exists(csv_path):
        return pd.read_csv(csv_path, encoding="utf-8-sig")

    try:
        from scripts import snowflake_client
    except ImportError:
        try:
            import snowflake_client
        except ImportError:
            return None

    params = dict(get_query_params())
    params["prev_end_date"] = asof_dates["prev_asof_date"]
    params["prev2_end_date"] = asof_dates["prev2_asof_date"]
    sql = load_query("d1_season_raw.sql", **params)
    if not sql:
        return None

    print(f"[DataLoader] d1_prev_asof: Snowflake 조회 "
          f"(전년 동주차 {asof_dates['prev_asof_date']}, 재작년 {asof_dates['prev2_asof_date']})...")
    df = snowflake_client.execute_query(sql)
    if df is not None:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"[DataLoader] d1_prev_asof: CSV 저장 ({len(df):,}행 → {csv_path})")
    return df


# ── 유틸리티 ─────────────────────────────────────────────────

def reset_cache():
    """캐시 리셋 (테스트용 또는 설정 변경 후 재로드)"""
    global _config_cache
    _config_cache = None
    clear_data_cache()
