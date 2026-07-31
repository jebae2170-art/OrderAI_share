-- d3_similarity_mapping.sql
-- 유사스타일매핑: ML 모델 산출 신규↔유사 스타일 매핑 결과
--
-- 파라미터: {brand}, {target_season}
--   brand: 브랜드코드 (M=MLB 등) ← config_loader.get_query_params()["brand"]
--   target_season: 차시즌 (예: 27S) ← config_loader.get_target_season()
--
-- 테이블 (2026-07-13 경로/스키마 이관):
--   FNF.PRCS.DB_PRDT_SIMILAR_INFO_ML  — ML 유사도 매핑 결과 (canonical, 신 테이블)
--                                       컬럼: PRDT_CD, SIMILAR_PRDT_CD, RANKING  (BRD_CD·STYLE_CD 없음)
--   FNF.PRCS.DB_PRDT                   — 상품 마스터. PRDT_CD(13=브랜드1+시즌3+PART_CD9) = 조인키,
--                                       PART_CD(9자리 스타일코드), SESN, BRD_CD, 이미지/ITEM/CAT
--   ※ 구 테이블 FNF.PRCS.DB_PRDT_SIMILAR_ML(BRD_CD/STYLE_CD/SIMILAR_STYLE_CD)에서 이관.
--     신 테이블은 BRD_CD가 없고 키가 PRDT_CD → DB_PRDT.PRDT_CD로 조인해 브랜드/시즌/PART_CD를 얻는다.
--     출력 컬럼은 기존과 동일(NEW_STYLE=PART_CD, SIMILAR_STYLE=PART_CD 유지) → step4 다운스트림 무변경.
--
-- 사용처: step4_integration.py (Step 4)
-- 예상 행수: 100~500 (Top 3 × 신규 스타일 수)
-- 캐시: data/{brand}/{season}/similarity_mapping_r1.csv
--
-- 시즌 처리: 신규 스타일 마스터(n)의 SESN 컬럼으로 직접 필터 (= {target_season}).
-- 조인: PRDT_CD는 DB_PRDT에서 유일(fanout 없음). 브랜드 매칭은 n.BRD_CD 필터로 처리.
--
-- 코드 적용 필터 (쿼리 후 Python에서 처리):
--   RANKING <= 3 (Top 3), Long → Wide 피벗 변환 (step4_integration.py에 구현됨)

SELECT
    n.BRD_CD,
    n.SESN                       AS NEW_SEASON,
    n.PART_CD                    AS NEW_STYLE,
    n.PRDT_NM                    AS NEW_PRDT_NM,
    n.PO_IMG                     AS NEW_PO_IMG,
    b.PRDT_NM,
    b.PRDT_IMG                   AS SIMILAR_PRDT_IMG,
    b.PO_IMG                     AS SIMILAR_PO_IMG,
    b.PART_CD                    AS SIMILAR_STYLE,
    b.SESN                       AS SIMILAR_STYLE_SEASON,
    b.ITEM,
    b.CAT_NM,
    a.RANKING
FROM FNF.PRCS.DB_PRDT_SIMILAR_INFO_ML a
    JOIN FNF.PRCS.DB_PRDT n ON a.PRDT_CD = n.PRDT_CD
    JOIN FNF.PRCS.DB_PRDT b ON a.SIMILAR_PRDT_CD = b.PRDT_CD
WHERE n.BRD_CD = '{brand}'
  AND n.SESN = '{target_season}'
  AND a.RANKING <= 3
ORDER BY
    n.SESN,
    n.PART_CD,
    a.RANKING ASC
