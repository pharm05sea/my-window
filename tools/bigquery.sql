-- BigQuery 용 SQL — questions.json 의 npd_candidates 와 같은 집계 기준 (고유 filing_id · 고유 manufacturer_id · filings 기준 LEFT JOIN)
-- 테이블: edata9.filings · edata9.filing_functions · edata9.product_details (data/*.parquet 을 Parquet 으로 올린 것)
-- 앱(DuckDB)에서 바꾼 부분: ① COUNT(DISTINCT x) FILTER (WHERE …) → COUNT(DISTINCT IF(조건, x, NULL))
--                          ② ::DOUBLE → 쓰지 않음 (ROUND 결과가 이미 FLOAT64)  ③ 한글 별칭 → 영문 별칭 (BigQuery 별칭 안전)
-- 결과 열: func 기능성 · form 제형 · cnt_may_jul / cnt_aug_oct 품목 5~7월 / 8~10월 · mfr_may_jul / mfr_aug_oct 업소 · top1_share 8~10월 1위 업소 비중
-- 쿼리 편집기에서 «쿼리 하나씩» 선택해 Run 한다 (세 개를 한꺼번에 붙이면 마지막만 실행되거나 오류가 난다).

-- ═══ 쿼리 0 — 올린 표가 맞는지 (행 수 · 키 고유값) ═══════════════════════════════
-- 기대값: filings 3289 / 3289 · filing_functions 7482 / 7482 · product_details 3289 / 3289 · 세 표 LEFT JOIN 7498 행
SELECT 'filings' AS t, COUNT(*) AS n, COUNT(DISTINCT filing_id) AS k FROM edata9.filings
UNION ALL SELECT 'filing_functions', COUNT(*), COUNT(DISTINCT CONCAT(filing_id, '|', function_group)) FROM edata9.filing_functions
UNION ALL SELECT 'product_details', COUNT(*), COUNT(DISTINCT filing_id) FROM edata9.product_details
UNION ALL SELECT 'filings LEFT JOIN 상세 LEFT JOIN 기능성', COUNT(*), COUNT(DISTINCT f.filing_id)
  FROM edata9.filings f
  LEFT JOIN edata9.product_details p USING (filing_id)
  LEFT JOIN edata9.filing_functions ff USING (filing_id);

-- ═══ 쿼리 1 — 등록한 질문(신규 제품 검토 후보 상위 3개) ═══════════════════════════
-- 기대값(앱): 1 영양소 기능×액상 109→163 · 2 혈행×분말 5→14 · 3 면역 기능×분말 7→15
WITH filings_p AS (
  SELECT filing_id, manufacturer_id,
         CASE WHEN filing_date BETWEEN DATE '2026-05-01' AND DATE '2026-07-31' THEN 1
              WHEN filing_date BETWEEN DATE '2026-08-01' AND DATE '2026-10-31' THEN 2 END AS period
  FROM edata9.filings
  WHERE filing_date BETWEEN DATE '2026-05-01' AND DATE '2026-10-31'
),
func AS (SELECT DISTINCT filing_id, function_group FROM edata9.filing_functions),
linked AS (
  SELECT s.filing_id, s.manufacturer_id, s.period,
         COALESCE(k.function_group, '미분류') AS func,
         COALESCE(p.dosage_form, '제형 미확인') AS form
  FROM filings_p s
  LEFT JOIN edata9.product_details p USING (filing_id)
  LEFT JOIN func k USING (filing_id)
),
combo AS (
  SELECT func, form,
         COUNT(DISTINCT IF(period = 1, filing_id, NULL)) AS cnt_may_jul,
         COUNT(DISTINCT IF(period = 2, filing_id, NULL)) AS cnt_aug_oct,
         COUNT(DISTINCT IF(period = 1, manufacturer_id, NULL)) AS mfr_may_jul,
         COUNT(DISTINCT IF(period = 2, manufacturer_id, NULL)) AS mfr_aug_oct
  FROM linked GROUP BY func, form
),
by_mfr AS (
  SELECT func, form, manufacturer_id, COUNT(DISTINCT filing_id) AS cnt
  FROM linked WHERE period = 2 AND manufacturer_id IS NOT NULL GROUP BY func, form, manufacturer_id
),
top1 AS (SELECT func, form, MAX(cnt) AS top1_cnt FROM by_mfr GROUP BY func, form)
SELECT ROW_NUMBER() OVER (ORDER BY c.cnt_aug_oct - c.cnt_may_jul DESC, c.cnt_aug_oct DESC, c.func, c.form) AS rank_no,
       c.func, c.form, c.cnt_may_jul, c.cnt_aug_oct,
       c.cnt_aug_oct - c.cnt_may_jul AS cnt_diff,
       ROUND(100.0 * (c.cnt_aug_oct - c.cnt_may_jul) / NULLIF(c.cnt_may_jul, 0), 1) AS cnt_growth_pct,
       c.mfr_may_jul, c.mfr_aug_oct, c.mfr_aug_oct - c.mfr_may_jul AS mfr_diff,
       ROUND(100.0 * t.top1_cnt / NULLIF(c.cnt_aug_oct, 0), 1) AS top1_share
FROM combo c
LEFT JOIN top1 t ON t.func = c.func AND t.form = c.form
WHERE c.func <> '미분류' AND c.form <> '제형 미확인'
  AND c.cnt_aug_oct > c.cnt_may_jul
  AND c.mfr_aug_oct >= 5
  AND c.mfr_aug_oct >= c.mfr_may_jul
  AND 100.0 * t.top1_cnt / NULLIF(c.cnt_aug_oct, 0) < 30
ORDER BY rank_no
LIMIT 3;

-- ═══ 쿼리 2 — 기능성 × 제형 전체 조합 (비교용: 조합 167개 기대) ═══════════════════════
-- 3단계 비교에 쓴다. 결과를 CSV 로 내려받아 tools/compare_bigquery.py 로 비교한다 (BigQuery 결과창 → 결과 저장 → CSV).
WITH filings_p AS (
  SELECT filing_id, manufacturer_id,
         CASE WHEN filing_date BETWEEN DATE '2026-05-01' AND DATE '2026-07-31' THEN 1
              WHEN filing_date BETWEEN DATE '2026-08-01' AND DATE '2026-10-31' THEN 2 END AS period
  FROM edata9.filings
  WHERE filing_date BETWEEN DATE '2026-05-01' AND DATE '2026-10-31'
),
func AS (SELECT DISTINCT filing_id, function_group FROM edata9.filing_functions),
linked AS (
  SELECT s.filing_id, s.manufacturer_id, s.period,
         COALESCE(k.function_group, '미분류') AS func,
         COALESCE(p.dosage_form, '제형 미확인') AS form
  FROM filings_p s
  LEFT JOIN edata9.product_details p USING (filing_id)
  LEFT JOIN func k USING (filing_id)
),
by_mfr AS (
  SELECT func, form, manufacturer_id, COUNT(DISTINCT filing_id) AS cnt
  FROM linked WHERE period = 2 AND manufacturer_id IS NOT NULL GROUP BY func, form, manufacturer_id
),
top1 AS (SELECT func, form, MAX(cnt) AS top1_cnt FROM by_mfr GROUP BY func, form)
SELECT c.func, c.form, c.cnt_may_jul, c.cnt_aug_oct,
       c.cnt_aug_oct - c.cnt_may_jul AS cnt_diff,
       ROUND(100.0 * (c.cnt_aug_oct - c.cnt_may_jul) / NULLIF(c.cnt_may_jul, 0), 1) AS cnt_growth_pct,
       c.mfr_may_jul, c.mfr_aug_oct, c.mfr_aug_oct - c.mfr_may_jul AS mfr_diff,
       ROUND(100.0 * t.top1_cnt / NULLIF(c.cnt_aug_oct, 0), 1) AS top1_share
FROM (
  SELECT func, form,
         COUNT(DISTINCT IF(period = 1, filing_id, NULL)) AS cnt_may_jul,
         COUNT(DISTINCT IF(period = 2, filing_id, NULL)) AS cnt_aug_oct,
         COUNT(DISTINCT IF(period = 1, manufacturer_id, NULL)) AS mfr_may_jul,
         COUNT(DISTINCT IF(period = 2, manufacturer_id, NULL)) AS mfr_aug_oct
  FROM linked GROUP BY func, form
) c
LEFT JOIN top1 t ON t.func = c.func AND t.form = c.form
ORDER BY cnt_diff DESC, cnt_aug_oct DESC, func, form;
