-- d3_similarity_mapping.sql
-- 유사스타일매핑: ML 모델 산출 신규↔유사 스타일 매핑 결과
--
-- 파라미터: {brand}, {target_season}
--   brand: 브랜드코드 (M=MLB 등) ← config_loader.get_query_params()["brand"]
--   target_season: 차시즌 (예: 27S) ← config_loader.get_target_season()
--
-- 테이블:
--   FNF.PRCS.DB_PRDT_SIMILAR_ML  — ML 유사도 매핑 결과 (canonical, 최신)
--                                   컬럼: BRD_CD, STYLE_CD(9자리), SIMILAR_STYLE_CD(9자리), RANKING ...
--   FNF.PRCS.DB_PRDT             — 상품 마스터 (PART_CD=9자리 스타일코드, SESN=시즌, 이미지/ITEM/CAT JOIN)
--
-- 사용처: step4_integration.py (Step 4)
-- 예상 행수: 100~500 (Top 3 × 신규 스타일 수)
-- 캐시: data/{brand}/{season}/similarity_mapping_r1.csv
--
-- 시즌 처리: 신규 스타일 마스터(n)의 SESN 컬럼으로 직접 필터 (= {target_season}).
--   ※ 구버전은 PRDT_CD 끝자리 SUBSTR로 시즌을 파생하며 시즌 필터가 없어 적재 시즌과 무관하게
--     ML 테이블 전량(당시 26F)을 끌어왔음 — 다른 파이프라인과 동일하게 SESN 기준으로 교정.
-- 조인: ML.STYLE_CD(9자리) = DB_PRDT.PART_CD(9자리), 브랜드 동시 매칭으로 fanout 방지.
--
-- 코드 적용 필터 (쿼리 후 Python에서 처리):
--   RANKING <= 3 (Top 3), Long → Wide 피벗 변환 (step4_integration.py에 구현됨)

SELECT
    n.BRD_CD,
    n.SESN                       AS NEW_SEASON,
    a.STYLE_CD                   AS NEW_STYLE,
    n.PRDT_NM                    AS NEW_PRDT_NM,
    n.PO_IMG                     AS NEW_PO_IMG,
    b.PRDT_NM,
    b.PRDT_IMG                   AS SIMILAR_PRDT_IMG,
    b.PO_IMG                     AS SIMILAR_PO_IMG,
    a.SIMILAR_STYLE_CD           AS SIMILAR_STYLE,
    b.SESN                       AS SIMILAR_STYLE_SEASON,
    b.ITEM,
    b.CAT_NM,
    a.RANKING
FROM FNF.PRCS.DB_PRDT_SIMILAR_ML a
    JOIN FNF.PRCS.DB_PRDT n ON a.STYLE_CD = n.PART_CD AND a.BRD_CD = n.BRD_CD
    JOIN FNF.PRCS.DB_PRDT b ON a.SIMILAR_STYLE_CD = b.PART_CD AND a.BRD_CD = b.BRD_CD
WHERE n.BRD_CD = '{brand}'
  AND n.SESN = '{target_season}'
  AND a.RANKING <= 3
ORDER BY
    n.SESN,
    a.STYLE_CD,
    a.RANKING ASC
