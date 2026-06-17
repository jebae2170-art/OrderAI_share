-- d3_r2_similarity_gtm_image.sql
-- 유사스타일매핑 (2차): GTM 이미지 기반 ML 유사도 매핑 결과
-- R1(d3_similarity_mapping.sql)에서 매칭되지 않은 스타일 보완용
--
-- 파라미터: {brand}, {target_season}
--   brand: 브랜드코드 (M=MLB 등)
--   target_season: 차시즌 (예: 27S) ← config_loader.get_target_season()
--
-- 테이블:
--   FNF.DEV.DB_PRDT_SIMILAR_GTM_IMAGE_ML  — GTM 이미지 기반 ML 유사도 (PART_CD=9자리)
--   FNF.PRCS.DB_PRDT                      — 상품 마스터 (PART_CD=9자리, SESN=시즌, ITEM/CAT JOIN)
--
-- 사용처: step4_integration.py (Step 4) — R1 미매칭 스타일 폴백
-- 출력 컬럼: R1(d3_similarity_mapping.sql)과 동일 + IMAGE_YN, SIMILAR_IMAGE_YN
--
-- 시즌 처리: 신규 스타일 마스터(n)의 SESN 컬럼으로 직접 필터 (= {target_season}).
--   ※ 구버전은 PART_CD 끝자리 SUBSTR로 시즌 파생 + PRDT_CD 재구성 JOIN(FW 전용)이라
--     시즌 필터가 없어 적재 시즌과 무관하게 끌어왔음 — SESN 기준으로 교정.
-- 조인: GTM.PART_CD(9자리) = DB_PRDT.PART_CD(9자리), 브랜드 동시 매칭으로 fanout 방지.

SELECT
    a.BRD_CD,
    n.SESN                AS NEW_SEASON,
    a.PART_CD             AS NEW_STYLE,
    b.PRDT_NM,
    a.SIMILAR_PART_CD     AS SIMILAR_STYLE,
    b.SESN                AS SIMILAR_STYLE_SEASON,
    b.ITEM,
    b.CAT_NM,
    a.RANKING,
    a.IMAGE_YN,
    a.SIMILAR_IMAGE_YN
FROM FNF.DEV.DB_PRDT_SIMILAR_GTM_IMAGE_ML a
    JOIN FNF.PRCS.DB_PRDT n ON a.PART_CD = n.PART_CD AND a.BRD_CD = n.BRD_CD
    JOIN FNF.PRCS.DB_PRDT b ON a.SIMILAR_PART_CD = b.PART_CD AND a.BRD_CD = b.BRD_CD
WHERE a.BRD_CD = '{brand}'
  AND n.SESN = '{target_season}'
  AND a.RANKING <= 3
ORDER BY
    n.SESN,
    a.PART_CD,
    a.RANKING ASC