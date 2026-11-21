# -*- coding: utf-8 -*-
"""내려받기/ 의 엑셀 세 개를 data/*.parquet 으로 바꾼다. 엑셀 원본은 읽기만 한다.

    python tools/excel_to_parquet.py

- 컬럼 이름은 엑셀 그대로 둔다
- filing_id · manufacturer_id 같은 번호는 문자열 (앞자리 0, 긴 숫자 보존)
- filing_date 는 날짜(DATE)
- 변환 뒤 엑셀 행 수와 parquet 행 수가 같은지 확인한다
"""
import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "내려받기")
DATA = os.path.join(HERE, "data")

FILES = {  # 엑셀 → (표 이름, 날짜 열)
    "품목신고_2026.xlsx": ("filings", ["filing_date"]),
    "제품상세_2026.xlsx": ("product_details", []),
}
# 기능성분류_2026.xlsx(규칙 v1, 잠가 둔 정답의 기준)는 여기서 변환하지 않는다.
# data/filing_functions.parquet 은 tools/classify_functions.py 가 규칙 v2 로 만든다.


def main():
    os.makedirs(DATA, exist_ok=True)
    bad = 0
    for fname, (table, date_cols) in FILES.items():
        path = os.path.join(SRC, fname)
        df = pd.read_excel(path, dtype=str)           # 먼저 전부 글자로 읽는다 — 번호가 숫자로 바뀌지 않게
        for c in df.columns:
            df[c] = df[c].where(df[c].notna(), None)
            df[c] = df[c].map(lambda v: v.strip() if isinstance(v, str) else v)
            df[c] = df[c].map(lambda v: None if v == "" else v)
        fields = []
        for c in df.columns:
            if c in date_cols:
                df[c] = pd.to_datetime(df[c], errors="raise").dt.date
                fields.append(pa.field(c, pa.date32()))
            else:
                fields.append(pa.field(c, pa.string()))
        tbl = pa.Table.from_pandas(df, schema=pa.schema(fields), preserve_index=False)
        out = os.path.join(DATA, f"{table}.parquet")
        pq.write_table(tbl, out)
        n_pq = pq.read_metadata(out).num_rows
        ok = n_pq == len(df)
        bad += not ok
        print(f"{'✓' if ok else '✕'} {fname} → data/{table}.parquet · 엑셀 {len(df):,}행 · parquet {n_pq:,}행 · "
              + ", ".join(f"{f.name}:{f.type}" for f in fields))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
