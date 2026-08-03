import subprocess
import sys
import time
import os
from config_loader import get_base_season, get_target_season, get_brand

def run_script(script_name, description, args=None):
    print("=" * 60)
    print(f"* 실행 중: {script_name}")
    print(f"   ({description})")
    print("=" * 60)

    start_time = time.time()
    args = args or []
    try:
        # python 인터프리터로 스크립트 실행
        # unbuffered 모드(-u)로 실행하여 출력을 즉시 확인 + utf-8 인코딩 설정
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            [sys.executable, "-u", script_name] + args,
            check=True,
            text=True,
            env=env,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        elapsed = time.time() - start_time
        print(f"\n* {script_name} 완료 (소요시간: {elapsed:.2f}초)\n")
        return True

    except subprocess.CalledProcessError as e:
        print(f"\n* {script_name} 실행 실패 (Exit Code: {e.returncode})\n")
        return False
    except Exception as e:
        print(f"\n* 오류 발생: {str(e)}\n")
        return False

def check_config():
    """brand_config.json 존재 여부 확인"""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'public', 'brand_config.json')
    if os.path.exists(config_path):
        print(f"[Config] brand_config.json 발견 → 사용자 설정 적용")
    else:
        print(f"[Config] brand_config.json 없음 → 기본값 사용")

def main():
    pipeline_start = time.time()
    base = get_base_season()
    target = get_target_season()
    print("\n" + "=" * 60)
    print(f"  {base} 시즌 분석 → {target} 발주 최적화 (6-Step Pipeline)")
    print("=" * 60 + "\n")

    check_config()
    print()

    scripts = [
        ("main.py",              "STEP 1: 시즌 마감 분석 & 기본 데이터 처리", []),
        ("weekly_analysis.py",   "STEP 2: 시계열 패턴 분석 & 대시보드 데이터 생성", []),
        ("ai_sales_loss_v3.py",  "STEP 3: AI 수요 예측 & 기회비용 분석", []),
        ("step4_integration.py", "STEP 4: 유사스타일 맵핑 데이터 생성 (프론트엔드용)", []),
        ("generate_size_data.py","STEP 5: 사이즈 배분 데이터 생성", []),
        ("dump_to_duckdb.py",    "STEP 6: baseline DuckDB 적재", [
            "--brand", get_brand().lower(),
            "--season", base.lower(),
            "--json-dir", "../public",
            "--db", "../data/production/order_ai.duckdb",
        ]),
    ]

    success_count = 0

    for script, desc, args in scripts:
        if run_script(script, desc, args):
            success_count += 1
        else:
            print("* 스크립트 실행 실패로 인해 전체 프로세스를 중단합니다.")
            break

    total_elapsed = time.time() - pipeline_start

    print("=" * 60)
    if success_count == len(scripts):
        print(f"* 모든 분석이 성공적으로 완료되었습니다! ({success_count}/{len(scripts)})")
        print(f"   - 결과 파일: {base}_Analysis_Result.xlsx")
        print("   - 예산 설정: budget_config.json")
        print(f"   - 결과 파일: {base}_TimeSeries_Analysis_Result.xlsx")
        print("   - 대시보드: dashboard_data.json")
        print(f"   - 발주 제안: {target}_Order_Recommendation.xlsx")
        print("   - 사이즈 데이터: size_assortment_data.json")
        print("   - baseline DuckDB: data/production/order_ai.duckdb")
    else:
        print(f"* 일부 과정이 완료되지 않았습니다. ({success_count}/{len(scripts)})")
    print(f"\n* 전체 파이프라인 소요시간: {total_elapsed:.1f}초")
    print("=" * 60)

    # 일시 정지 (콘솔 창이 바로 닫히지 않도록)
    if os.name == 'nt': # Windows인 경우
        os.system('pause')

    # 종료코드 명시: /weekly-refresh 등 자동화가 실패를 감지하도록.
    #   기존엔 스텝 실패에도 항상 exit 0 → 부분실패 산출물이 조용히 baseline 적재로
    #   흘러갈 수 있었다. 6단계 미완주 시 exit 1. (원본 order_ai 정본 미러링)
    if success_count != len(scripts):
        sys.exit(1)

if __name__ == "__main__":
    main()
