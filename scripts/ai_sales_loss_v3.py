"""AI Sales Loss V3 — S5 PLC 엔진 기반 기회비용 계산기 (얇은 러너)

GT/복원/PLC 미존재 시 FileNotFoundError (fail fast).

V3 부터 S5 PLC 엔진 사용 (이전 V2 감쇠모델 코드는 order-ai-share 에서 제외됨).
"""
import json
import math
import os
import pandas as pd
from pathlib import Path
from config_loader import (
    get_gt_path, get_restored_path, get_plc_forecast_path, load_gt,
    get_weekly_data_path, get_timeseries_output_path, get_dashboard_json_path,
    get_target_sell_through, load_plc_engine_specs,
    get_shortage_loss_thresholds, align_weekly_for_inseason,
    get_diagnosis_thresholds, _season_year,
)
from plc_engine import run_engine
from plc_engine.specs import EngineInputs

print("=" * 60)
print("Step 3: AI 수요 예측 (S5 PLC 엔진 v2)")
print("=" * 60)

TARGET_SELL_THROUGH = get_target_sell_through()


def main():
    brand_spec, season_spec, engine_params = load_plc_engine_specs()
    print(f"  [{brand_spec.brand_name} {season_spec.season_code}] 엔진 실행")

    weekly_path = get_weekly_data_path()
    weekly_csv_fallback = weekly_path.replace('.xlsx', '.csv')
    # GT는 load_gt()가 Snowflake/로컬 폴백 처리(미존재 시 RuntimeError). PLC는 로컬 전용 → 여기서 fail-fast.
    if not Path(get_plc_forecast_path()).exists():
        raise FileNotFoundError(f"엔진 입력 파일 없음: PLC ({get_plc_forecast_path()})")
    # 주간데이터는 csv(Snowflake 캐시) 또는 xlsx 둘 중 하나만 있어도 OK
    if not Path(weekly_path).exists() and not Path(weekly_csv_fallback).exists():
        raise FileNotFoundError(
            f"엔진 입력 파일 없음: 주간데이터 ({weekly_path} 또는 {weekly_csv_fallback})"
        )

    # CSV 우선 사용 (Snowflake 최신 데이터, xlsx보다 범위 넓음)
    if os.path.exists(weekly_csv_fallback):
        weekly_df = pd.read_csv(weekly_csv_fallback)
    elif weekly_path.endswith('.xlsx'):
        weekly_df = pd.read_excel(weekly_path)
    else:
        weekly_df = pd.read_csv(weekly_path)

    # in-season SS: 당해 weekly를 GT 최신주차까지 정렬 (예측 as-of 일치 + 프레임 누수 차단). FW no-op.
    weekly_df = align_weekly_for_inseason(weekly_df)

    # GT에 ADJ_SC_SALE_QTY_TAX 통합 → restored 별도 로드 불필요
    # 인시즌은 Snowflake 최신값, 마감시즌은 로컬(ADJ 포함) — load_gt가 판별/폴백
    gt_df = load_gt()
    restored_df = None
    restored_path = Path(get_restored_path())
    if restored_path.exists() and 'ADJ_SC_SALE_QTY_TAX' not in gt_df.columns:
        restored_df = pd.read_csv(restored_path)
        print("  [복원 데이터] 별도 파일 로드")
    else:
        print("  [복원 데이터] GT 통합 (ADJ_SC_SALE_QTY_TAX)")

    inputs = EngineInputs(
        gt=gt_df,
        restored=restored_df,
        weekly_raw=weekly_df,
        plc_ratio=pd.read_csv(get_plc_forecast_path()),
    )

    result = run_engine(brand_spec, season_spec, engine_params, inputs)
    m = result.metrics
    print(f"  SC: {len(result.sc_predictions)}, "
          f"DTW P25: {m.get('dtw_dist_p25', 0):.3f}, "
          f"기회비용: {m.get('total_lost_qty', 0):,}장")

    update_excel(result)
    update_dashboard_json(result)
    apply_inseason_fcst_and_rediagnose(result, season_spec)
    apply_eofs_estimate(result, season_spec, gt_df)  # Track2: 카드 '예상 ST%'를 현실 예상마감판매율로 교체
    regate_low_diag(result, season_spec)             # risk/normal: 잠재수요선·기회비용 제거 + 발주를 eofs/목표판매율로 교정
    enrich_season_closing_fcst(result, season_spec)
    update_past_styles_json()


def enrich_season_closing_fcst(result, season_spec):
    """진행중 SS: season_closing_data.json summary에 전체 예측마감 집계 주입 (fcst_ 키만 추가).

    Sales Performance(UI Step1) 화면용. FW/마감시즌은 no-op.
      예측마감판매량 = Σ SC 잠재수요, 예측마감판매율 = min(100, Σ판매/Σ입고),
      추가수요 = Σ_style max(0, 스타일판매 − 스타일입고)  (공급 초과 = 재발주 신호)
    """
    if season_spec.season_type != 'ss':
        return
    path = Path(get_dashboard_json_path()).parent / 'season_closing_data.json'
    if not path.exists():
        return
    d = json.loads(path.read_text(encoding='utf-8'))

    by_style_sales, by_style_intake = {}, {}
    for (prod, _color), sc in result.sc_predictions.items():
        by_style_sales[prod] = by_style_sales.get(prod, 0) + sc.get('total_demand', 0)
        by_style_intake[prod] = by_style_intake.get(prod, 0) + sum(
            (x or 0) for x in (sc.get('intakes') or [])
        )
    total_sales = sum(by_style_sales.values())
    total_intake = sum(by_style_intake.values())
    extra = sum(max(0, by_style_sales[p] - by_style_intake.get(p, 0)) for p in by_style_sales)

    summ = d.setdefault('summary', {})
    summ['fcst_예측마감판매량'] = round(total_sales)
    summ['fcst_예측마감판매율'] = round(min(100.0, total_sales / total_intake * 100), 1) if total_intake > 0 else 0.0
    summ['fcst_추가수요'] = round(extra)

    # 마감예측판매율 ↔ 전년 '동시즌 마감' 벤치마크 (to-end 지표는 전년 마감과 비교).
    #   prior_year_season_end 는 main.py(인시즌)가 주입한 전년 마감 스냅샷.
    pse = (d.get('prior_year_season_end') or {}).get('summary') or {}
    prev_end_st = pse.get('sell_through_rate')
    if prev_end_st is not None:
        prev_end_st = float(prev_end_st)
        summ['fcst_전년마감판매율'] = round(prev_end_st, 1)
        summ['fcst_예측마감판매율_전년마감_delta'] = round(summ['fcst_예측마감판매율'] - prev_end_st, 1)
        eofs = summ.get('fcst_eofs_예측마감판매율')
        if eofs is not None:
            summ['fcst_eofs_예측마감판매율_전년마감_delta'] = round(float(eofs) - prev_end_st, 1)

    path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  * season_closing 예측마감 집계: 판매량 {round(total_sales):,} / "
          f"판매율 {summ['fcst_예측마감판매율']}% / 추가수요 {round(extra):,}")


def apply_inseason_fcst_and_rediagnose(result, season_spec):
    """진행중 SS: 예측마감(fcst) 값 주입 + 예측마감판매율 기준 진단 재분배.

    기존 키 불변 — `fcst_` 키만 추가. 진단은 리스트 재배치(AI_진단 원본 유지, fcst_진단 신규).
    FW/마감시즌(season_type!='ss')은 no-op → 기존 진단/화면 그대로.
      예측마감판매량 = Σ(컬러 total_demand)  (엔진 잠재수요)
      예측마감판매율 = min(100, 판매량/총입고×100)  (100% 캡)
      추가수요 = max(0, 판매량 − 총입고)  (공급 초과 = 재발주 신호)
      진단 = 캡 전 판매율(판매량/입고) 기준 hit/normal/risk
    """
    if season_spec.season_type != 'ss':
        return
    json_path = get_dashboard_json_path()
    data = json.loads(Path(json_path).read_text(encoding='utf-8'))
    diag = get_diagnosis_thresholds()
    high, low = diag['high'] * 100, diag['low'] * 100   # _DIAG는 fraction → percent

    buckets = {'hit': [], 'normal': [], 'shortage': [], 'risk': []}
    for d_key in ['hit', 'normal', 'shortage', 'risk']:
        for entry in data.get(d_key, []):
            total = entry.setdefault('total', {})
            ana = total.setdefault('analysis', {})
            part = total.get('itemInfo', {}).get('code')
            cols = entry.get('colors', {})
            # 예측마감판매량 = Σ 컬러 잠재수요(total_demand), 총입고 = Σ 컬러 입고
            fcst_sales = sum(
                result.sc_predictions.get((part, ck), {}).get('total_demand', 0) for ck in cols
            )
            intake = sum(
                (x or 0)
                for ck in cols
                for x in (result.sc_predictions.get((part, ck), {}).get('intakes') or [])
            )
            st_uncapped = (fcst_sales / intake * 100) if intake > 0 else float(ana.get('최종판매율', 0) or 0)
            extra = max(0, fcst_sales - intake)
            # 시즌 중 실제 재고부족 발생 여부(실증): 컬러 중 결품주차 존재 or lost_qty>0.
            #   외삽(예측수요)만으로 extra>0인 거짓양성(결품 0인데 Shortage)을 방지 —
            #   UI 정의 "시즌 중 재고부족 발생"과 정합. (하락품목 외삽 과대투영 거름)
            has_shortage_evidence = any(
                sum(result.sc_predictions.get((part, ck), {}).get('is_broken_series') or []) > 0
                or (result.sc_predictions.get((part, ck), {}).get('lost_qty', 0) or 0) > 0
                for ck in cols
            )
            ana['fcst_예측마감판매량'] = round(fcst_sales)
            ana['fcst_예측마감판매율'] = round(min(100.0, st_uncapped), 1)   # 100% 캡
            ana['fcst_추가수요'] = round(extra)                              # 공급 초과 = 재발주 신호
            # 컬러(SC)별 예상마감판매율 — SC 탭에서도 '예상 ST%' 표기되도록 각 컬러 analysis에 주입
            for ck in cols:
                sc = result.sc_predictions.get((part, ck), {})
                c_intake = sum((x or 0) for x in (sc.get('intakes') or []))
                c_ana = cols[ck].setdefault('analysis', {})
                if c_intake > 0:
                    c_ana['fcst_예측마감판매율'] = round(min(100.0, sc.get('total_demand', 0) / c_intake * 100), 1)
                else:
                    c_ana['fcst_예측마감판매율'] = float(c_ana.get('최종판매율', 0) or 0)
            # 진단: 언더바이(extra>0) + 실제 결품 발생 시에만 Shortage / 65%↑=Hit / 45%↑=Normal / else Risk
            if extra > 0 and has_shortage_evidence:
                newd = 'shortage'
            elif st_uncapped >= high:
                newd = 'hit'
            elif st_uncapped >= low:
                newd = 'normal'
            else:
                newd = 'risk'
            ana['fcst_진단'] = newd
            buckets[newd].append(entry)
    for k in buckets:
        data[k] = buckets[k]
    Path(json_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  * in-season 재진단(예측마감ST): hit {len(buckets['hit'])}, "
          f"normal {len(buckets['normal'])}, shortage {len(buckets['shortage'])}, "
          f"risk {len(buckets['risk'])}")


def apply_eofs_estimate(result, season_spec, gt_df):
    """진행중 SS(Track 2): 예상마감판매율(현실) 산출 + 진단 티어 재분류.

    - fcst_예측마감판매율(total+컬러) = 국내전체 PLC 타이밍 진척 + 재고 캡 (plc_engine/eofs.py).
    - 진단 재분류(apply_inseason 1차 버킷 대체): **Shortage=언더바이(잠재수요>입고)+실제결품**(잠재수요 기준),
      **Hit/Normal/Risk=예상ST(Track2) 기준** — 표시 예상ST와 진단을 같은 기준으로 통일.
    - 발주(잠재수요/total_demand)는 불변. FW/마감시즌 no-op. PLC 미산출 SC는 누계판매율로 폴백.
    """
    if season_spec.season_type != 'ss':
        return
    from plc_engine.eofs import build_total_plc, estimate_eofs, eofs_week_weights
    from config_loader import get_season_end_date

    try:
        base_year = 2000 + int(season_spec.season_code[:2])
    except (ValueError, TypeError):
        base_year = pd.Timestamp.now().year

    def _date_to_fwo(dstr):
        try:
            m, d = int(str(dstr)[:2]), int(str(dstr)[3:5])
            return int(pd.Timestamp(year=base_year, month=m, day=d).isocalendar().week)
        except Exception:
            return None

    # 원본 weekly(전년/재작년 포함) 재로딩 — align된 당해 데이터로는 PLC 산출 불가
    wp = get_weekly_data_path()
    wc = wp.replace('.xlsx', '.csv')
    wk_raw = pd.read_csv(wc) if os.path.exists(wc) else (pd.read_excel(wp) if wp.endswith('.xlsx') else pd.read_csv(wp))
    plc = build_total_plc(wk_raw, ['전년', '재작년'])
    if not plc:
        print("  * 예상마감판매: 국내전체 PLC 산출 실패 — 스킵(기존값 유지)")
        return

    part_item = dict(zip(gt_df['PROD_CD'].astype(str), gt_df['ITEM']))
    end_fwo = {}
    for s in ('1', '3'):
        try:
            end_fwo[s] = int(pd.Timestamp(get_season_end_date(s)).isocalendar().week)
        except Exception:
            end_fwo[s] = season_spec.season_end_fwo
    wks = [int(w) for w in gt_df['WEEK_OF_YEAR'].unique() if 1 <= int(w) <= season_spec.season_end_fwo]
    cutoff = max(wks) if wks else season_spec.season_end_fwo

    json_path = get_dashboard_json_path()
    data = json.loads(Path(json_path).read_text(encoding='utf-8'))
    diag = get_diagnosis_thresholds()
    high, low = diag['high'] * 100, diag['low'] * 100
    n_sc = n_fb = 0
    grand_qty = grand_intake = 0.0   # 시즌 전체 Track2 집계(Step1 KPI용)
    buckets = {'hit': [], 'normal': [], 'shortage': [], 'risk': []}
    for dkey in ['hit', 'normal', 'shortage', 'risk']:
        for entry in data.get(dkey, []):
            total = entry.get('total', {})
            part = str(total.get('itemInfo', {}).get('code', ''))
            sub = part[-1] if part else ''
            item = part_item.get(part)
            tot_qty = tot_intake = 0.0
            sum_td = sum_intake = 0.0          # 잠재수요·입고 합(언더바이 판정용)
            has_evidence = False               # 시즌 중 실제 결품 발생 여부
            date_eofs = {}                     # 미래주차별 마감예측 분배합(total 막대용)
            for ck, cdata in (entry.get('colors') or {}).items():
                sc = result.sc_predictions.get((part, ck), {})
                intake = sum(x for x in (sc.get('intakes') or []) if x)
                sales = sum(sc.get('actuals_sf') or [])
                sum_td += sc.get('total_demand', 0)
                sum_intake += intake
                if sum(sc.get('is_broken_series') or []) > 0 or (sc.get('lost_qty', 0) or 0) > 0:
                    has_evidence = True
                r = estimate_eofs(sales, intake, plc.get((item, sub)), cutoff,
                                  end_fwo.get(sub, season_spec.season_end_fwo))
                cana = cdata.setdefault('analysis', {})
                if r is not None:
                    cana['fcst_예측마감판매율'] = round(r[1], 1)
                    cana['fcst_eofs_qty'] = round(r[0], 1)   # 풀시즌 현실 마감판매량(실판매+회색막대) — risk/normal 발주 base
                    tot_qty += r[0]
                    tot_intake += intake
                    grand_qty += r[0]
                    grand_intake += intake
                    n_sc += 1
                    # 주차별 마감예측 분배 → forecast 막대(eofs_sale). 잔여분(예상마감−누계판매)을
                    #   PLC 곡선 증분으로 미래주차에 taper 분배. 발주/잠재수요 불변(표시 전용).
                    fpts = [p for p in cdata.get('chartData', []) if p.get('is_forecast')]
                    if fpts:
                        fwos = [(_date_to_fwo(p.get('date')) or 0) for p in fpts]
                        wts = eofs_week_weights(plc.get((item, sub)), fwos)
                        remaining = max(0.0, r[0] - sales)
                        for p, w in zip(fpts, wts):
                            ev = remaining * w
                            p['eofs_sale'] = round(ev, 1)
                            d = p.get('date')
                            date_eofs[d] = date_eofs.get(d, 0.0) + ev
                else:
                    cana['fcst_예측마감판매율'] = float(cana.get('최종판매율', 0) or 0)
                    cana['fcst_eofs_qty'] = round(min(sales, intake), 1)   # 폴백: 누계판매(투영 불가)
                    grand_qty += min(sales, intake)   # 폴백: 현재 누계판매(투영 없음)
                    grand_intake += intake
                    n_fb += 1
            # total.chartData 미래주차 막대(eofs_sale) = 컬러 분배합
            tb = total.get('chartData') if isinstance(total, dict) else None
            if tb:
                for p in tb:
                    if p.get('is_forecast'):
                        p['eofs_sale'] = round(date_eofs.get(p.get('date'), 0.0), 1)
            # 스타일 total 예상마감판매율(Track2 현실) — 표시 + 진단 티어 기준
            track2_st = (round(min(100.0, tot_qty / tot_intake * 100), 1) if tot_intake > 0
                         else float(total.get('analysis', {}).get('최종판매율', 0) or 0))
            tana = total.setdefault('analysis', {})
            tana['fcst_예측마감판매율'] = track2_st
            # 재분류: Shortage=언더바이(잠재수요>입고)+실제결품(잠재수요 기준) /
            #   Hit·Normal·Risk는 표시와 일관되게 예상ST(Track2) 기준.
            if (sum_td - sum_intake) > 0 and has_evidence:
                nb = 'shortage'
            elif track2_st >= high:
                nb = 'hit'
            elif track2_st >= low:
                nb = 'normal'
            else:
                nb = 'risk'
            tana['fcst_진단'] = nb
            buckets[nb].append(entry)
    for k in buckets:
        data[k] = buckets[k]
    Path(json_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    # Step1 KPI용: season_closing_data.json summary에 Track2 시즌 전체 마감예상판매율 주입
    if grand_intake > 0:
        scp = Path(json_path).parent / 'season_closing_data.json'
        if scp.exists():
            sc_data = json.loads(scp.read_text(encoding='utf-8'))
            summ = sc_data.get('summary', sc_data)
            summ['fcst_eofs_예측마감판매량'] = round(grand_qty)
            summ['fcst_eofs_예측마감판매율'] = round(min(100.0, grand_qty / grand_intake * 100), 1)
            scp.write_text(json.dumps(sc_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  * 예상마감판매(현실): PLC적용 {n_sc} SC, 폴백 {n_fb} SC (현재주차 fwo{cutoff}, "
          f"마감 Spring={end_fwo.get('1')}/Summer={end_fwo.get('3')}, "
          f"시즌전체 마감예상ST={round(min(100.0, grand_qty / grand_intake * 100), 1) if grand_intake else '-'}%)")


def regate_low_diag(result, season_spec):
    """최종진단 risk/normal SC: 잠재수요(빨강)·기회비용 제거 + 발주를 현실(eofs)/목표판매율로 교정.

    배경(이전 legacy 복원): legacy(ai_sales_loss_v2)는 기회비용·잠재기반 발주를 Shortage/Hit
      한정으로 적용하고 Normal/Risk는 제외했다. v3는 in-season에서 '결품(broken) 감지'만으로
      잠재수요선/loss를 주입(update_dashboard_json:607) → 과잉재고로 판매율이 낮은(Risk) SC에도
      품절 기회비용이 붙는 모순이 발생. 본 패스는 **최종진단 버킷**(apply_eofs_estimate 결과)
      기준으로 risk/normal을 환원한다.

    - 발주 base = eofs 풀시즌 현실량(실판매+회색막대, fcst_eofs_qty) / 목표판매율(update_excel과 동일 target).
    - 표시 게이팅: chartData potential_sale=현실(과거 실판매·미래 eofs), loss=0 → 빨강선 과투영·기회비용 제거.
    - Excel('AI제안 발주량','AI 계산 기회비용') + JSON(analysis) 동기화. FW/마감시즌 no-op(SS에서만 eofs 존재).
    """
    if season_spec.season_type != 'ss':
        return
    base_target = TARGET_SELL_THROUGH
    targets = getattr(result, '_order_targets', {}) or {}
    json_path = get_dashboard_json_path()
    data = json.loads(Path(json_path).read_text(encoding='utf-8'))

    def _gate_points(points):
        for p in points:
            p['loss'] = 0
            p['is_broken'] = False
            if p.get('is_forecast'):
                p['potential_sale'] = p.get('eofs_sale', 0) or 0          # 미래: 빨강=회색(현실)
            else:
                p['potential_sale'] = p.get('sale', p.get('actual_tax', 0)) or 0  # 과거: 빨강=실판매

    new_orders = {}   # (PART_CD, COLOR_CD) -> 교정 발주량 (Excel 동기화용)
    n_sc = 0
    for dkey in ('normal', 'risk'):
        for entry in data.get(dkey, []):
            total = entry.get('total', {}) if isinstance(entry.get('total'), dict) else {}
            part_cd = str(total.get('itemInfo', {}).get('code', ''))
            tgt = targets.get(part_cd, base_target)
            for ck, cdata in (entry.get('colors') or {}).items():
                cana = cdata.setdefault('analysis', {})
                eofs_qty = cana.get('fcst_eofs_qty')
                if eofs_qty is None:   # 폴백: 누계 실판매
                    eofs_qty = sum((p.get('sale', 0) or 0)
                                   for p in cdata.get('chartData', []) if not p.get('is_forecast'))
                order = math.ceil(eofs_qty / tgt / 10) * 10 if eofs_qty and eofs_qty > 0 else 0
                cana['AI제안 발주량'] = int(order)
                cana['예상손실수량'] = 0
                new_orders[(part_cd, str(ck))] = int(order)
                _gate_points(cdata.get('chartData', []))
                n_sc += 1
            if total.get('chartData'):
                _gate_points(total['chartData'])
            total.setdefault('analysis', {})['예상손실수량'] = 0
    Path(json_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    # Excel 동기화 ('AI제안 발주량'=교정값, 'AI 계산 기회비용'=0) — 당해 row만
    excel_upd = 0
    try:
        excel_path = get_timeseries_output_path()
        df = pd.read_excel(excel_path)
        is_cur = (df['PERIOD'].astype(str) == '당해') if 'PERIOD' in df.columns else pd.Series([True] * len(df))
        for idx, row in df.iterrows():
            if not bool(is_cur.iloc[idx]):
                continue
            key = (str(row.get('PART_CD', '')).strip(), str(row.get('COLOR_CD', '')).strip())
            if key in new_orders:
                df.at[idx, 'AI제안 발주량'] = new_orders[key]
                df.at[idx, 'AI 계산 기회비용'] = 0
                excel_upd += 1
        df.to_excel(excel_path, index=False)
    except Exception as e:
        print(f"  ⚠ risk/normal Excel 동기화 실패: {e}")
    print(f"  * risk/normal 발주 교정(eofs/목표판매율): JSON {n_sc} SC, Excel {excel_upd} row "
          f"(잠재수요선·기회비용 제거)")


def update_excel(result):
    """Excel 'AI 계산 기회비용', 'AI제안 발주량' 컬럼 갱신.

    Top 5% 대물량 스타일은 70% 목표판매율, 나머지는 65% (기본) — brand_config 기반.
    의류 PART_CD만 분류 대상 (timeseries Excel은 weekly_analysis에서 이미 의류 필터링됨).
    """
    from config_loader import get_high_volume_target_sell_through, get_high_volume_top_percent

    base_target = TARGET_SELL_THROUGH  # 0.65
    high_target = get_high_volume_target_sell_through()  # 0.70
    top_pct = get_high_volume_top_percent()  # 5

    excel_path = get_timeseries_output_path()
    df = pd.read_excel(excel_path)

    if 'AI 계산 기회비용' not in df.columns:
        df['AI 계산 기회비용'] = 0
    if 'AI제안 발주량' not in df.columns:
        df['AI제안 발주량'] = 0

    # === Pass 1: AI 기회비용 산출 + 당해 시즌 PART_CD별 잠재수요 집계 ===
    # (Top 5% 분류는 당해 시즌만 대상. 전년/재작년 PART_CD는 default base target 적용)
    matched, total = 0, 0
    style_potential = {}  # 당해 PART_CD -> 잠재수요 합

    for idx, row in df.iterrows():
        pc = row.get('PART_CD')
        cc = row.get('COLOR_CD')
        if pd.isna(pc) or pd.isna(cc):
            continue
        total += 1
        sc = result.sc_predictions.get((pc, cc))
        loss = sc['lost_qty'] if sc is not None else 0
        if sc is not None:
            matched += 1
        df.at[idx, 'AI 계산 기회비용'] = loss

        # 잠재수요 집계는 당해 시즌만 (PERIOD == '당해')
        period = str(row.get('PERIOD', '당해'))
        if period != '당해':
            continue
        total_sale = float(row.get('총판매', 0) or 0)
        # B(in-season SS): 발주 잠재수요 = 풀시즌 total_demand(forward 포함). 그 외는 총판매+loss.
        #   forecast_weeks 보유 = in-season SS only → FW/마감시즌 불변.
        if sc is not None and sc.get('forecast_weeks'):
            potential = float(sc.get('total_demand', total_sale + loss))
        else:
            potential = total_sale + loss
        if potential > 0:
            pc_str = str(pc)
            style_potential[pc_str] = style_potential.get(pc_str, 0) + potential

    # === Top N% 대물량 PART_CD 식별 (당해 의류 잠재수요 기준) ===
    sorted_styles = sorted(style_potential.items(), key=lambda x: -x[1])
    n_styles = len(sorted_styles)
    n_top = max(1, int(n_styles * top_pct / 100)) if n_styles > 0 else 0
    top_set = set(pc for pc, _ in sorted_styles[:n_top])

    print(f"  * 대물량 분류 (당해 의류): Top {top_pct}% = {n_top}/{n_styles} PART_CD (목표판매율 {high_target:.0%})")
    print(f"    일반 스타일 {n_styles - n_top}개 목표판매율 {base_target:.0%} 적용")

    # === Pass 2: 차등 target으로 AI제안 발주량 산출 (모든 row 처리) ===
    order_targets = {}   # PART_CD -> 적용 target (risk/normal 발주 교정 패스 regate_low_diag에서 재사용)
    for idx, row in df.iterrows():
        pc = row.get('PART_CD')
        cc = row.get('COLOR_CD')
        if pd.isna(pc):
            df.at[idx, 'AI제안 발주량'] = 0
            continue
        loss = df.at[idx, 'AI 계산 기회비용']
        total_sale = float(row.get('총판매', 0) or 0)
        # B(in-season SS): 발주 잠재수요 = 풀시즌 total_demand(forward 포함). 그 외는 총판매+loss.
        sc = result.sc_predictions.get((pc, cc)) if not pd.isna(cc) else None
        if sc is not None and sc.get('forecast_weeks'):
            potential = float(sc.get('total_demand', total_sale + loss))
        else:
            potential = total_sale + loss
        if potential > 0:
            target = high_target if str(pc) in top_set else base_target
            order = math.ceil(potential / target / 10) * 10
        else:
            target = high_target if str(pc) in top_set else base_target
            order = 0
        order_targets[str(pc)] = target
        df.at[idx, 'AI제안 발주량'] = int(order)
    # risk/normal 발주 교정 패스(regate_low_diag)가 동일 target으로 재계산하도록 보관
    result._order_targets = order_targets

    # in-season SS: 예측마감 fcst 컬럼 추가 (per PART_CD+COLOR). FW/마감시즌은 미추가 → Excel 불변.
    from config_loader import get_base_season
    if get_base_season()[-1:].upper() == 'S':
        sales_map = {(p, c): sc.get('total_demand', 0) for (p, c), sc in result.sc_predictions.items()}
        intake_map = {
            (p, c): sum((x or 0) for x in (sc.get('intakes') or []))
            for (p, c), sc in result.sc_predictions.items()
        }

        def _fc(row, kind):
            key = (str(row.get('PART_CD', '')).strip(), str(row.get('COLOR_CD', '')).strip())
            s = sales_map.get(key)
            if s is None:
                return None
            ina = intake_map.get(key, 0)
            if kind == 'sales':
                return round(s)
            if kind == 'extra':
                return round(max(0, s - ina))
            return round(min(100.0, s / ina * 100), 1) if ina > 0 else None  # st (100% 캡)

        df['fcst_예측마감판매량'] = df.apply(lambda r: _fc(r, 'sales'), axis=1)
        df['fcst_예측마감판매율'] = df.apply(lambda r: _fc(r, 'st'), axis=1)
        df['fcst_추가수요'] = df.apply(lambda r: _fc(r, 'extra'), axis=1)

    df.to_excel(excel_path, index=False)
    coverage = matched / max(total, 1)
    print(f"  * Excel 업데이트: {matched}/{total} ({coverage:.1%})")


def _build_json_date_to_woy(weekly_path):
    """weekly_raw END_DT로부터 JSON date(MM/DD) → WEEK_OF_YEAR 매핑 구축.
    .csv 우선 사용 (Snowflake 최신 데이터 포함), .xlsx fallback."""
    csv_path = weekly_path.replace('.xlsx', '.csv')
    if os.path.exists(csv_path):
        wr = pd.read_csv(csv_path)
    elif weekly_path.endswith('.xlsx'):
        wr = pd.read_excel(weekly_path)
    else:
        wr = pd.read_csv(weekly_path)
    wr = wr[wr['PERIOD'] == '당해'].copy()
    wr['END_DT'] = pd.to_datetime(wr['END_DT'])
    wr['WOY'] = wr['END_DT'].dt.isocalendar().week.astype(int)
    wr['DATE_LABEL'] = wr['END_DT'].dt.strftime('%m/%d')
    return dict(zip(wr['DATE_LABEL'], wr['WOY']))


def _forecast_points(sc, base_year, existing_dates):
    """엔진 forecast_weeks/values → 미래주차 chartData 포인트 (in-season SS만; FW는 빈 리스트).

    forecast_values는 전체수요(predicted_total) 기준 단일 곡선이라, 국내수요(predicted_sc)는
    관측구간 Σ국내/Σ전체 비율로 스케일해 예측구간에서도 면세 split을 유지한다.
    (전체수요 실선·total_demand·발주 로직은 그대로, 국내수요 점선만 조정.)
    """
    fw = sc.get('forecast_weeks')
    fv = sc.get('forecast_values')
    if not fw:
        return []
    tot_obs = sum(sc.get('predicted_total') or [])
    sc_obs = sum(sc.get('predicted_sc') or [])
    dom_ratio = max(0.0, min(1.0, sc_obs / tot_obs)) if tot_obs > 0 else 1.0
    pts = []
    for w, val in zip(fw, fv):
        try:
            dt = pd.Timestamp.fromisocalendar(int(base_year), int(w), 7)  # ISO주 일요일
        except Exception:
            continue
        dk = dt.strftime('%m/%d')
        if dk in existing_dates:
            continue
        pts.append({
            'date': dk, 'actual_tax': 0, 'predicted_sc': float(val) * dom_ratio,
            'potential_sale': float(val), 'loss': 0,
            'is_broken': False, 'is_forecast': True,
        })
    return pts


def _chrono_sort_chartdata(data, cur_year):
    """모든 chartData를 시즌 인식 시간순 정렬.

    date가 연도 없는 '%m/%d'라 cross-year(작년 Dec 런칭 vs 올해) 모호하나,
    STEP2 cap(09/30) 이후엔 10·11월 포인트가 없어 월≥10=전년, 1~9월=당해로 명확.
    forecast(시즌말 6~8월)가 append되어 실데이터(~5월) 뒤 제자리에 정렬되게 함.
    """
    def _key(p):
        s = str(p.get('date', ''))
        try:
            m, d = int(s[:2]), int(s[3:5])
        except (ValueError, IndexError):
            return (cur_year, 13, 32)
        return (cur_year - 1 if m >= 10 else cur_year, m, d)

    def _walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == 'chartData' and isinstance(v, list):
                    v.sort(key=_key)
                else:
                    _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(data)


def update_dashboard_json(result):
    """JSON chartData에 potential_sale/loss 주입 + total 재집계.

    analysis 블록에 'AI제안 발주량' 추가 (timeseries Excel에서 lookup) — ref-style endpoint에서 활용.
    """
    json_path = get_dashboard_json_path()
    data = json.loads(Path(json_path).read_text(encoding='utf-8'))

    json_date_to_woy = _build_json_date_to_woy(get_weekly_data_path())

    # in-season forward 투영 포인트 날짜 연도 (base 시즌, 예: 26S→2026)
    from config_loader import get_base_season
    try:
        base_year = 2000 + int(get_base_season()[:2])
    except (ValueError, TypeError):
        base_year = pd.Timestamp.now().year

    # 멱등성: 이전 실행이 추가한 forecast 포인트 제거 후 재생성
    # (재실행 시 메인 루프 else 분기가 forecast 값을 0으로 덮어쓰는 것 방지)
    for _dk in ['hit', 'normal', 'shortage', 'risk']:
        for _e in data.get(_dk, []):
            for _cd in _e.get('colors', {}).values():
                _cd['chartData'] = [p for p in _cd.get('chartData', []) if not p.get('is_forecast')]
            _tb = _e.get('total', {})
            if isinstance(_tb, dict) and 'chartData' in _tb:
                _tb['chartData'] = [p for p in _tb['chartData'] if not p.get('is_forecast')]

    # timeseries Excel에서 (PART_CD, COLOR_CD) → AI제안 발주량 매핑 구축 (analysis 블록 보강용)
    ai_order_map: dict = {}
    try:
        excel_path = get_timeseries_output_path()
        df_excel = pd.read_excel(excel_path)
        for _, row in df_excel.iterrows():
            pc = str(row.get('PART_CD', '')).strip()
            cc = str(row.get('COLOR_CD', '')).strip()
            if pc and cc:
                ai_order_map[(pc, cc)] = int(pd.to_numeric(row.get('AI제안 발주량', 0), errors='coerce') or 0)
    except Exception as e:
        print(f"  ⚠ AI제안 발주량 매핑 로드 실패: {e}")

    def _sale_or_zero(p):
        try:
            return int(p.get('sale', 0))
        except (TypeError, ValueError):
            return 0

    matched_sc, total_sc = 0, 0
    week_matched, week_total = 0, 0

    for diagnosis in ['hit', 'normal', 'shortage', 'risk']:
        for entry in data.get(diagnosis, []):
            colors_data = entry.get('colors', {})

            for color_key, color_data in colors_data.items():
                part_cd = color_data.get('itemInfo', {}).get('code')
                if not part_cd:
                    continue
                total_sc += 1
                sc = result.sc_predictions.get((part_cd, color_key))

                if sc is None:
                    for point in color_data.get('chartData', []):
                        point['potential_sale'] = 0
                        point['loss'] = 0
                        point['actual_tax'] = 0
                        point['predicted_sc'] = 0
                    continue

                matched_sc += 1
                woy_map = {woy: i for i, woy in enumerate(sc['week_numbers'])}

                # 결품(브로큰) 감지 안 된 SC는 PLC 예측선/loss 표시 안 함
                # (설계: 결품 시점부터 PLC 예측 그래프를 그림)
                if sc.get('broken') is None:
                    # in-season SS 비결품: 과거 전체수요=실판매로 채워 예측선과 자연 연결
                    #   (잠재수요=실판매, 결품 없음). FW/마감시즌은 0 유지 → 화면 동결.
                    inseason_ss = bool(sc.get('forecast_weeks'))
                    for point in color_data.get('chartData', []):
                        week_total += 1
                        date_key = point.get('date')
                        woy = json_date_to_woy.get(date_key)
                        if woy is not None and woy in woy_map:
                            week_matched += 1
                            point['actual_tax'] = int(sc['actuals_tax'][woy_map[woy]])
                        else:
                            point['actual_tax'] = 0
                        point['potential_sale'] = (int(point.get('sale', 0) or 0)
                                                   if inseason_ss else 0)
                        point['predicted_sc'] = 0
                        point['loss'] = 0
                    analysis = color_data.setdefault('analysis', {})
                    analysis['예상손실수량'] = 0
                    analysis['AI제안 발주량'] = ai_order_map.get((part_cd, color_key), 0)
                    _existing = {p.get('date') for p in color_data.get('chartData', [])}
                    color_data['chartData'].extend(_forecast_points(sc, base_year, _existing))
                    continue

                is_broken_series = sc.get('is_broken_series', [])
                for point in color_data.get('chartData', []):
                    week_total += 1
                    date_key = point.get('date')
                    woy = json_date_to_woy.get(date_key)
                    if woy is not None and woy in woy_map:
                        week_matched += 1
                        i = woy_map[woy]
                        point['potential_sale'] = float(sc['predicted_total'][i])
                        point['predicted_sc'] = float(sc['predicted_sc'][i])
                        point['actual_tax'] = int(sc['actuals_tax'][i])
                        sale = _sale_or_zero(point)
                        # 주차별 결품 상태 기반 loss 계산 (결품 주차만, 재입고 후 해소 주차는 0)
                        point['is_broken'] = bool(is_broken_series[i]) if i < len(is_broken_series) else False
                        if point['is_broken']:
                            point['loss'] = max(0, point['potential_sale'] - sale)
                        else:
                            point['loss'] = 0
                    else:
                        point['potential_sale'] = _sale_or_zero(point)
                        point['predicted_sc'] = 0
                        point['actual_tax'] = 0
                        point['loss'] = 0
                        point['is_broken'] = False

                analysis = color_data.setdefault('analysis', {})
                analysis['예상손실수량'] = sc['lost_qty']
                analysis['AI제안 발주량'] = ai_order_map.get((part_cd, color_key), 0)
                _existing = {p.get('date') for p in color_data.get('chartData', [])}
                color_data['chartData'].extend(_forecast_points(sc, base_year, _existing))

            # total.chartData 재집계
            total_block = entry.get('total')
            if total_block and 'chartData' in total_block:
                date_prediction_sum = {}
                date_loss_sum = {}
                date_actual_tax_sum = {}
                date_predicted_sc_sum = {}
                for color_key, color_data in colors_data.items():
                    for point in color_data.get('chartData', []):
                        d = point.get('date')
                        if not d:
                            continue
                        # 결품 미감지 컬러: 예측값=0이지만 실판매 자체가 잠재 수요.
                        # 전체수요 fallback ← sale, 국내수요 fallback ← actual_tax.
                        ps = point.get('potential_sale', 0) or _sale_or_zero(point)
                        psc = point.get('predicted_sc', 0) or int(point.get('actual_tax', 0) or 0)
                        date_prediction_sum[d] = date_prediction_sum.get(d, 0) + ps
                        date_predicted_sc_sum[d] = date_predicted_sc_sum.get(d, 0) + psc
                        date_loss_sum[d] = date_loss_sum.get(d, 0) + point.get('loss', 0)
                        date_actual_tax_sum[d] = date_actual_tax_sum.get(d, 0) + point.get('actual_tax', 0)

                for point in total_block['chartData']:
                    d = point.get('date')
                    if d in date_prediction_sum:
                        point['potential_sale'] = date_prediction_sum[d]
                        point['loss'] = date_loss_sum.get(d, 0)
                        point['actual_tax'] = date_actual_tax_sum.get(d, 0)
                        point['predicted_sc'] = date_predicted_sc_sum.get(d, 0)
                    else:
                        point['potential_sale'] = _sale_or_zero(point)
                        point['loss'] = 0
                        point['actual_tax'] = 0
                        point['predicted_sc'] = 0

                # in-season forward: 컬러 집계에 있던 미래주차를 total에도 추가
                _existing_total = {p.get('date') for p in total_block['chartData']}
                for d in date_prediction_sum:
                    if d not in _existing_total:
                        total_block['chartData'].append({
                            'date': d,
                            'potential_sale': date_prediction_sum[d],
                            'loss': date_loss_sum.get(d, 0),
                            'actual_tax': date_actual_tax_sum.get(d, 0),
                            'predicted_sc': date_predicted_sc_sum.get(d, 0),
                            'is_forecast': True,
                        })

    # 차트 시간순 정렬: forecast(시즌말 6~8월)를 실데이터 뒤 제자리에 배치 (두 번째 봉우리 방지)
    _chrono_sort_chartdata(data, _season_year())

    # Shortage 재분류: loss 주입 완료 후 Hit/Normal 중 엄격 기준 통과분을 Shortage로 이동
    # (Risk는 유지 — 공급 부족으로 판매율 낮은 케이스는 Risk 특성 보존)
    moved = _reclassify_shortage(data)

    Path(json_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    sc_cov = matched_sc / max(total_sc, 1)
    wk_cov = week_matched / max(week_total, 1)
    print(f"  * JSON 업데이트: SC {matched_sc}/{total_sc} ({sc_cov:.1%}), weeks {week_matched}/{week_total} ({wk_cov:.1%})")
    print(f"  * Shortage 재분류 (엄격 기준): {moved['hit_to_shortage']}건(Hit→) + {moved['normal_to_shortage']}건(Normal→) 이동")
    # Engine→JSON은 100% (엔진 출력 전부 매핑). JSON→Engine은 GT 미포함 SC 때문에 <100% 정상.
    if matched_sc == 0:
        print("  [경고] JSON 매핑 SC 0건 — 식별자 불일치 의심")


def update_past_styles_json():
    """past_styles_data.json entries에 AI제안 발주량 사후 주입.

    weekly_analysis.py가 past_styles_data.json을 생성하는 시점은 ai_sales_loss_v3 실행 전이라
    analysis 블록에 'AI제안 발주량' 키가 없거나 0. 본 함수가 result.xlsx의 prev/prev2 row를
    읽어 (PART_CD, COLOR_CD) 단위로 사후 주입한다. dump_to_duckdb가 이 JSON을 그대로
    style_timeseries(period='prev'|'prev2')에 적재하므로, 결과적으로 과거 시즌 ref도
    DuckDB에서 AI발주량 lookup이 가능해진다.
    """
    project_root = Path(__file__).resolve().parent.parent
    paths = [
        project_root / 'output/past_styles_data.json',
        project_root / 'public/past_styles_data.json',
    ]
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("  * past_styles_data.json 없음 → 스킵")
        return

    df = pd.read_excel(get_timeseries_output_path())
    color_ai_map: dict = {}
    style_ai_map: dict = {}
    for _, row in df.iterrows():
        pc = str(row.get('PART_CD', '')).strip()
        cc = str(row.get('COLOR_CD', '')).strip()
        period = str(row.get('PERIOD', ''))
        if not (pc and cc and period in ('전년', '재작년')):
            continue
        ai_order = int(pd.to_numeric(row.get('AI제안 발주량', 0), errors='coerce') or 0)
        color_ai_map[(pc, cc)] = ai_order
        style_ai_map[pc] = style_ai_map.get(pc, 0) + ai_order

    updated_styles = 0
    for path in paths:
        data = json.loads(path.read_text(encoding='utf-8'))
        for period_key in ('prev', 'prev2'):
            section = data.get(period_key) or {}
            for part_cd, entry in section.items():
                total = entry.get('total') or {}
                total_analysis = total.setdefault('analysis', {})
                total_analysis['AI제안 발주량'] = style_ai_map.get(part_cd, 0)
                colors = entry.get('colors') or {}
                for color_cd, color_data in colors.items():
                    cd_analysis = color_data.setdefault('analysis', {})
                    cd_analysis['AI제안 발주량'] = color_ai_map.get((part_cd, color_cd), 0)
                updated_styles += 1
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    print(f"  * past_styles 업데이트: {updated_styles}개 PART_CD × {len(paths)} 파일")


def _reclassify_shortage(data):
    """Hit/Normal 중 기회비용 엄격 기준 통과 스타일을 Shortage 탭으로 이동.

    기준: 장수 ≥ min_qty AND (비율 ≥ min_ratio OR 금액 ≥ min_amt)
    - Risk는 유지 (공급 부족이 판매율 하락 원인일 수 있으나 Risk 의미 보존)
    """
    t = get_shortage_loss_thresholds()
    moved_count = {'hit_to_shortage': 0, 'normal_to_shortage': 0}
    moved_items = []

    for source in ['hit', 'normal']:
        remaining = []
        for item in data.get(source, []):
            total = item.get('total', {})
            cd = total.get('chartData', [])
            info = total.get('itemInfo', {})
            lost_qty = sum(r.get('loss', 0) or 0 for r in cd)
            total_sale = sum(r.get('sale', 0) or 0 for r in cd)
            price = info.get('price', 0) or 0
            loss_amt = lost_qty * price
            loss_ratio = lost_qty / max(total_sale + lost_qty, 1)

            is_shortage = (
                lost_qty >= t['min_qty'] and
                (loss_ratio >= t['min_ratio'] or loss_amt >= t['min_amt'])
            )
            if is_shortage:
                moved_items.append(item)
                moved_count[f'{source}_to_shortage'] += 1
            else:
                remaining.append(item)
        data[source] = remaining

    data.setdefault('shortage', []).extend(moved_items)
    return moved_count


if __name__ == "__main__":
    main()
