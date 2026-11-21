# -*- coding: utf-8 -*-
"""filings 의 신고마다 주원료(I0030 RAWMTRL_NM)를 한 줄씩 펼쳐 data/filing_materials.parquet 을 만든다.

    python tools/build_materials.py

- 원본: product-planning/data/market.db 의 raw_responses (2026-11-20 I0030 전체 수집본, id 28~74). API 는 다시 부르지 않는다
- 대상: data/filings.parquet 에 있는 filing_id 만
- 주원료 문자열을 괄호 밖 쉼표로 나눈다. 원료 이름은 원문 그대로(앞뒤 공백만 제거) 둔다
- 같은 신고 안에서 같은 원료 이름은 한 번만
"""
import json
import os
import sqlite3
import sys
import zlib

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
DB = r"C:\Users\pharm\product-planning\data\market.db"


def split_outside_parens(text):
    out, buf, depth = [], [], 0
    for ch in text or "":
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def main():
    con = sqlite3.connect(DB)
    recs = []
    for (body,) in con.execute("SELECT body FROM raw_responses WHERE source='mfds' AND request LIKE 'I0030/%' "
                               "AND id BETWEEN 28 AND 74 ORDER BY id"):
        recs += json.loads(zlib.decompress(body))["I0030"]["row"]
    fp = os.path.join(DATA, "filings.parquet").replace("\\", "/")
    ids = {r[0] for r in duckdb.connect().execute(f"SELECT filing_id FROM read_parquet('{fp}')").fetchall()}
    rows, seen, got = [], set(), set()
    for r in recs:
        fid = str(r["PRDLST_REPORT_NO"]).strip()
        if fid not in ids:
            continue
        got.add(fid)
        for m in split_outside_parens(r.get("RAWMTRL_NM")):
            if (fid, m) not in seen:
                seen.add((fid, m)); rows.append((fid, m))
    tbl = pa.table({"filing_id": [a for a, _ in rows], "material": [b for _, b in rows]},
                   schema=pa.schema([("filing_id", pa.string()), ("material", pa.string())]))
    out = os.path.join(DATA, "filing_materials.parquet")
    pq.write_table(tbl, out)
    print(f"filings {len(ids):,}건 중 원본에서 찾은 신고 {len(got):,}건 · filing_materials {tbl.num_rows:,}행 · "
          f"고유 원료명 {len({b for _, b in rows}):,}개 → data/filing_materials.parquet")


if __name__ == "__main__":
    main()
