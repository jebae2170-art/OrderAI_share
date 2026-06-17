-- d1_season_raw.sql
-- 시즌마감실적: 스타일×컬러별 최종 발주/입고/판매/재고 집계
--
-- 파라미터: {brand}, {base_season}, {prev_season}, {prev2_season}
--   brand: 브랜드코드 (M=MLB, X=Discovery, V=Duvetica, I=MLB KIDS)
--   base_season: 당해 시즌 (예: 25F) ← config_loader.get_base_season()
--   prev_season: 전년 시즌 (예: 24F) ← config_loader.get_prev_season()
--   prev2_season: 재작년 시즌 (예: 23F) ← config_loader.get_prev2_season()
--
-- 테이블:
--   FNF.PRCS.DB_SCS_W  — 주차별 판매/재고 실적 (Weekly)
--   FNF.PRCS.DB_PRDT   — 상품 마스터
--
-- 사용처: main.py (Step 1), step4_integration.py (Step 4 룩업), generate_color_mapping.py
-- 예상 행수: 500~2,000
-- 캐시: data/{brand}/{season}/season_raw.parquet
--
-- 코드 적용 필터 (쿼리 후 Python에서 처리):
--   SEASON_GB == '당해' → 당해 시즌만 (전년은 비교용)
--   CLASS1 contains '의류' → 용품/신발 제외
--   ORDER_QTY > 0 → 기획만 있고 생산 안 된 스타일 제외

SELECT
    CASE w.SESN
        WHEN '{base_season}' THEN '당해'
        WHEN '{prev_season}' THEN '전년'
        WHEN '{prev2_season}' THEN '재작년'
    END                                                    AS SEASON_GB,
    p.PARENT_PRDT_KIND_NM                                  AS CLASS1,
    p.PRDT_KIND_NM                                         AS CLASS2,
    p.ITEM,
    p.ITEM_NM,
    w.PART_CD,
    w.COLOR_CD,
    p.TAG_PRICE,
    SUM(w.ORD_QTY)                                        AS ORDER_QTY,
    SUM(w.ORD_QTY_KOR)                                    AS ORDER_QTY_KR,
    SUM(w.STOR_QTY_KOR)                                   AS STOR_QTY_KR,
    SUM(w.SALE_NML_QTY_CNS + w.SALE_RET_QTY_CNS)          AS SALE_QTY_CNS,
    SUM(w.SALE_NML_QTY_CHN + w.SALE_NML_QTY_GVL)          AS SALE_QTY_GLB,
    SUM(w.DELV_NML_QTY_WSL + w.DELV_RET_QTY_WSL)          AS WH_QTY,
    -- KG(지식그래프) 발입출판재 쿼리 정합용 금액 컬럼 (택가/실판매액 기준)
    SUM(w.SALE_NML_SALE_AMT_CNS + w.SALE_RET_SALE_AMT_CNS) AS SALE_AMT_REAL, -- 실판매액(소비자 정상+반품, 할인반영)
    SUM(w.SALE_NML_TAG_AMT_CNS + w.SALE_RET_TAG_AMT_CNS)   AS SALE_TAG_AMT,   -- 판매택가(소비자 정상+반품, 정가환산)
    SUM(w.STOR_TAG_AMT_KOR)                                AS STOR_TAG_AMT,   -- 입고택가(국내, 정가환산)
    SUM(w.STOR_QTY_KOR)
        - SUM(w.SALE_NML_QTY_CNS + w.SALE_RET_QTY_CNS)
        - SUM(w.DELV_NML_QTY_WSL + w.DELV_RET_QTY_WSL)   AS STOCK_QTY
FROM FNF.PRCS.DB_SCS_W w
    LEFT JOIN FNF.PRCS.DB_PRDT p ON w.PART_CD = p.PART_CD
WHERE w.BRD_CD = '{brand}'
  AND (
    (w.SESN = '{base_season}'  AND w.END_DT <= DATE '{base_end_date}') OR
    (w.SESN = '{prev_season}'  AND w.END_DT <= DATE '{prev_end_date}') OR
    (w.SESN = '{prev2_season}' AND w.END_DT <= DATE '{prev2_end_date}')
  )
GROUP BY
    w.SESN,
    p.PARENT_PRDT_KIND_NM,
    p.PRDT_KIND_NM,
    p.ITEM,
    p.ITEM_NM,
    w.PART_CD,
    w.COLOR_CD,
    p.TAG_PRICE
ORDER BY SEASON_GB, CLASS2, ITEM_NM, PART_CD
