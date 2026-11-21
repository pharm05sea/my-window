# -*- coding: utf-8 -*-
"""BigQuery 쿼리 2 결과(CSV)와 앱(DuckDB) 결과를 같은 기준으로 비교한다.

    python tools/compare_bigquery.py <BigQuery 결과 CSV>
    python tools/compare_bigquery.py --expected      앱 기준 값만 만들어 tools/bq_expected.csv 로 저장

비교 열: cnt_may_jul · cnt_aug_oct · cnt_diff · cnt_growth_pct · mfr_may_jul · mfr_aug_oct · mfr_diff · top1_share
앱 쪽 값은 data/*.parquet 을 DuckDB 로 읽어 tools/bigquery.sql 쿼리 2 와 같은 기준으로 센다.
다르면 조합 이름과 두 값을 보여 준다 — 원인은 기간 조건 · 조인 방식 · 중복 제거 기준 · 제형 분류값에서 찾는다.
"""
import os
import sys

import duckdb
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(HERE, "data").replace("\\", "/")
KEY = ["func", "form"]
VAL = ["cnt_may_jul", "cnt_aug_oct", "cnt_diff", "cnt_growth_pct", "mfr_may_jul", "mfr_aug_oct", "mfr_diff", "top1_share"]

SQL = f"""
WITH filings_p AS (
  SELECT filing_id, manufacturer_id,
         CASE WHEN filing_date BETWEEN DATE '2026-05-01' AND DATE '2026-07-31' THEN 1
              WHEN filing_date BETWEEN DATE '2026-08-01' AND DATE '2026-10-31' THEN 2 END AS period
  FROM read_parquet('{D}/filings.parquet') WHERE filing_date BETWEEN DATE '2026-05-01' AND DATE '2026-10-31'),
func AS (SELECT DISTINCT filing_id, function_group FROM read_parquet('{D}/filing_functions.parquet')),
linked AS (
  SELECT s.filing_id, s.manufacturer_id, s.period, COALESCE(k.function_group, '미분류') AS func, COALESCE(p.dosage_form, '제형 미확인') AS form
  FROM filings_p s LEFT JOIN read_parquet('{D}/product_details.parquet') p USING (filing_id) LEFT JOIN func k USING (filing_id)),
by_mfr AS (SELECT func, form, manufacturer_id, COUNT(DISTINCT filing_id) AS cnt FROM linked WHERE period = 2 AND manufacturer_id IS NOT NULL GROUP BY 1, 2, 3),
top1 AS (SELECT func, form, MAX(cnt) AS top1_cnt FROM by_mfr GROUP BY 1, 2),
c AS (SELECT func, form,
        COUNT(DISTINCT filing_id) FILTER (WHERE period = 1) AS cnt_may_jul, COUNT(DISTINCT filing_id) FILTER (WHERE period = 2) AS cnt_aug_oct,
        COUNT(DISTINCT manufacturer_id) FILTER (WHERE period = 1) AS mfr_may_jul, COUNT(DISTINCT manufacturer_id) FILTER (WHERE period = 2) AS mfr_aug_oct
      FROM linked GROUP BY 1, 2)
SELECT c.func, c.form, c.cnt_may_jul, c.cnt_aug_oct, c.cnt_aug_oct - c.cnt_may_jul AS cnt_diff,
       ROUND(100.0 * (c.cnt_aug_oct - c.cnt_may_jul) / NULLIF(c.cnt_may_jul, 0), 1) AS cnt_growth_pct,
       c.mfr_may_jul, c.mfr_aug_oct, c.mfr_aug_oct - c.mfr_may_jul AS mfr_diff,
       ROUND(100.0 * t.top1_cnt / NULLIF(c.cnt_aug_oct, 0), 1) AS top1_share
FROM c LEFT JOIN top1 t ON t.func = c.func AND t.form = c.form
"""


def main():
    exp = duckdb.connect().execute(SQL).df()
    if "--expected" in sys.argv:
        out = os.path.join(HERE, "tools", "bq_expected.csv")
        exp.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"앱 기준 {len(exp)}개 조합 → {out}")
        return
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bq = pd.read_csv(sys.argv[1], encoding="utf-8-sig")
    miss = [c for c in KEY + VAL if c not in bq.columns]
    if miss:
        sys.exit(f"BigQuery 결과에 없는 열: {miss}  (쿼리 2 결과인지 확인)")
    print(f"행 수: 앱 {len(exp)} · BigQuery {len(bq)} · {'같음' if len(exp) == len(bq) else '다름'}")
    m = exp.merge(bq, on=KEY, how="outer", suffixes=("_app", "_bq"), indicator=True)
    only_app, only_bq = m[m["_merge"] == "left_only"], m[m["_merge"] == "right_only"]
    print(f"조합: 앱에만 {len(only_app)} · BigQuery에만 {len(only_bq)}")
    for _, r in only_app.iterrows():
        print(f"   앱에만: {r.func} × {r.form}")
    for _, r in only_bq.iterrows():
        print(f"   BigQuery에만: {r.func} × {r.form}")
    both = m[m["_merge"] == "both"]
    bad = 0
    for v in VAL:
        a, b = both[v + "_app"], both[v + "_bq"]
        diff = ~((a == b) | (a.isna() & b.isna()) | ((a - b).abs() < 1e-9))
        n = int(diff.sum())
        bad += n
        print(f"  {v:<15} 다른 조합 {n}")
        for _, r in both[diff].head(5).iterrows():
            print(f"      {r.func} × {r.form}: 앱 {r[v + '_app']} · BigQuery {r[v + '_bq']}")
    print(f"\n비교한 조합 {len(both)}개 · 값이 다른 칸 {bad}개 · " + ("모두 일치" if not bad and not len(only_app) and not len(only_bq) else "차이 있음"))
    sys.exit(0 if not bad and not len(only_app) and not len(only_bq) else 1)


if __name__ == "__main__":
    main()
