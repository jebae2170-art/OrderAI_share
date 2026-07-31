-- d3_r2_similarity_gtm_image.sql
-- 유사스타일매핑 (2차): GTM 이미지 기반 ML 유사도 매핑 결과
-- R1(d3_similarity_mapping.sql)에서 매칭되지 않은 스타일 보완용
--
-- 파라미터: {brand}, {target_season}
--   brand: 브랜드코드 (M=MLB 등)
--   target_season: 차시즌 (예: 27S) ← config_loader.get_target_season()
--
-- 테이블 (2026-07-13 경로/스키마 이관):
--   FNF.PRCS.DB_PRDT_SIMILAR_GTM_INFO_ML  — GTM 이미지 기반 ML 유사도 (신 테이블)
--                                           컬럼: PRDT_CD, SIMILAR_PRDT_CD, RANKING (BRD_CD·IMAGE_YN 없음)
--   FNF.GTM.DW_27S_GTM_EXCEL              — ★27S GTM 기획(엑셀) 마스터. 신규 27S 스타일의 BRD_CD·PART_CD 원천.
--                                           (신규 27S 스타일은 아직 FNF.PRCS.DB_PRDT 상품마스터에 미적재라
--                                            DB_PRDT로 조인하면 전량 탈락 → GTM 기획테이블로 조인)
--   FNF.PRCS.DB_PRDT                      — 상품 마스터. 유사(SIMILAR)스타일(과거)은 여기 존재 → b 조인 유지.
--   ※ 구 테이블 FNF.DEV.DB_PRDT_SIMILAR_GTM_IMAGE_ML(BRD_CD/PART_CD/IMAGE_YN)에서 이관.
--
-- 조인키:
--   신규(NEW) = a.PRDT_CD(13=브랜드1+시즌3+PART_CD9). BRD_CD=SUBSTR(,1,1)·SESN=SUBSTR(,2,3)·PART_CD=SUBSTR(,5,9).
--     → GTM 엑셀(e)에 BRD_CD+PART_CD로 조인해 실제 27S 기획스타일만 통과(색상 다중행이라 DISTINCT).
--   유사(SIMILAR) = a.SIMILAR_PRDT_CD = b.PRDT_CD (DB_PRDT, 유일).
--   ※ 소스 a에 완전중복 행이 있어 DISTINCT로 정리(step4 top3 피벗 정확성).
--
-- ⚠️ 이 GTM 엑셀은 MLB·27S 전용 덤프 → r2는 현재 MLB 27S 보완 전용.
--    (Discovery 27S는 r1이 담당) 차시즌 전환 시 테이블명(DW_{시즌}_GTM_EXCEL) 갱신 필요.
--
-- 사용처: step4_integration.py (Step 4) — R1 미매칭 스타일 폴백
-- 출력 컬럼: R1과 동일 축 + IMAGE_YN, SIMILAR_IMAGE_YN(신 테이블에 없어 NULL·step4 미사용)
-- 시즌 처리: a.PRDT_CD의 시즌코드(SUBSTR ,2,3) = {target_season} 필터.

SELECT
    e.BRD_CD,
    SUBSTR(a.PRDT_CD, 2, 3)  AS NEW_SEASON,
    e.PART_CD                AS NEW_STYLE,
    b.PRDT_NM,
    b.PART_CD                AS SIMILAR_STYLE,
    b.SESN                   AS SIMILAR_STYLE_SEASON,
    b.ITEM,
    b.CAT_NM,
    a.RANKING,
    NULL                     AS IMAGE_YN,
    NULL                     AS SIMILAR_IMAGE_YN
FROM (SELECT DISTINCT PRDT_CD, SIMILAR_PRDT_CD, RANKING
      FROM FNF.PRCS.DB_PRDT_SIMILAR_GTM_INFO_ML) a
    JOIN (SELECT DISTINCT BRD_CD, PART_CD FROM FNF.GTM.DW_27S_GTM_EXCEL) e
        ON e.BRD_CD = SUBSTR(a.PRDT_CD, 1, 1)
       AND e.PART_CD = SUBSTR(a.PRDT_CD, 5, 9)
    JOIN FNF.PRCS.DB_PRDT b ON a.SIMILAR_PRDT_CD = b.PRDT_CD
WHERE SUBSTR(a.PRDT_CD, 2, 3) = '{target_season}'
  AND e.BRD_CD = '{brand}'
  AND a.RANKING <= 3
ORDER BY
    e.PART_CD,
    a.RANKING ASC
