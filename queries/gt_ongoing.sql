-- gt_ongoing.sql
-- GT(Ground Truth) 진행중 시즌 실적: 스타일×컬러별 주차 단위 raw 판매/재고/기온 피처
--
-- 파라미터: {brand}, {base_season}
--   brand: 브랜드코드 (M=MLB, X=Discovery) ← config_loader.get_query_params()["brand"]
--   base_season: 당해(진행중) 시즌 (예: 26S) ← config_loader.get_base_season()
--
-- 테이블: FNF.ML_DIST.GT_SC_W (26컬럼, raw GT — 복원수요(ADJ) 컬럼 없음)
--
-- 사용처: ai_sales_loss_v3.py (Step 3, PLC 엔진 gt 입력), config_loader.align_weekly_for_inseason
-- 캐시: data/{brand}/{season}/ground_truth.csv (key="gt")
--
-- 범위 정책 (사용자 합의 2026-05-28):
--   - 인시즌(진행중) 시즌만 Snowflake에서 최신값 조회 → ground_truth.csv (FW/SS 무관, 26FW 진행 중 27FW 발주 시점도 동일)
--   - 마감시즌 복원수요(restored.csv·ADJ 포함, 멀티시즌)는 엔지니어 제공 로컬 CSV 유지 (복원수요 Snowflake 미적재)
--   - 판별은 load_gt()가 로컬 GT의 ADJ 컬럼 유무로 수행 (인시즌=ADJ 없음 → Snowflake)
--
-- 컬럼 별칭(alias) 정책: GT_SC_W의 DB 컬럼명을 기존 파이프라인/엔진이 기대하는 이름으로 복원
--   (SESN→SSN_CD, BRD_CD→BRAND_CD, SALE_QTY_*→SC_SALE_QTY_*, START_DT→WEEK_START)
--   → plc_engine·ai_sales_loss_v3 코드 무수정 (기존 25컬럼은 이름·순서 동일)
--
-- 주차 매칭: 실제 조인 키는 WEEK_OF_YEAR(ISO 주차). weekly_raw·차트는 END_DT(일요일 주마감)
--   기준이며 GT_SC_W END_DT(=START_DT+6)도 동일 일요일 → 검증 18/19주 END_DT 일치.
--   END_DT를 함께 내려보내 교차연도 ISO주차 aliasing 회피 + END_DT 직접 조인 가능 (ground_truth 25→26컬럼, 순수 추가)
--
-- apparel만 적재된 테이블이라 카테고리 필터 불필요 (사용자 확인)

SELECT
    BRD_CD                AS BRAND_CD,
    SESN                  AS SSN_CD,
    PROD_CD,
    COLOR_CD,
    PRDT_CD,
    PARENT_PRDT_KIND_CD,
    PRDT_KIND_CD,
    ITEM,
    TAG_PRICE,
    START_DT              AS WEEK_START,   -- 주 시작(월요일)
    END_DT,                                 -- 주 마감(일요일) = weekly_raw 주차축과 동일 기준
    YEAR_OF_WEEK,
    WEEK_OF_YEAR,                           -- ISO 주차번호 = 엔진/차트 실제 조인 키
    SALE_QTY_ALL          AS SC_SALE_QTY_ALL,
    SALE_QTY_NOTAX        AS SC_SALE_QTY_NOTAX,
    SALE_QTY_TAX          AS SC_SALE_QTY_TAX,
    BOW_STOCK,
    STOCK_RATIO,
    CUM_INTAKE,
    NUM_SIZES,
    DISC_RATE,
    AVG_MIN_TEMP,
    AVG_MAX_TEMP,
    TOTAL_PCP,
    LAG1_SALE_QTY,
    STD4W_SALE_QTY
FROM FNF.ML_DIST.GT_SC_W
WHERE BRD_CD = '{brand}'
  AND SESN   = '{base_season}'
ORDER BY PROD_CD, COLOR_CD, WEEK_OF_YEAR
