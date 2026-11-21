# -*- coding: utf-8 -*-
"""I0030 주된 기능성 문구를 규칙으로 분류해 data/filing_functions.parquet 을 만든다 (규칙 v2).

    python tools/classify_functions.py            v2 로 만들고 v1 과의 차이를 보여준다
    python tools/classify_functions.py --check    v1 규칙이 내려받기/기능성분류_2026.xlsx 와 똑같이 나오는지만 확인

규칙 (v1 — 내려받기/기능성분류_2026.xlsx 를 만든 규칙, 잠가 둔 정답의 기준)
  1. 문구에서 [ ] 안의 원료명을 지운다
  2. 줄바꿈 · ①② · 1. · 1) · (1) · (가) · 가) · 쉼표 · 세미콜론 · / 로 조각을 나눈다
  3. 조각에 «필요»가 있으면 «영양소 기능»
  4. 아니면 product-planning/analysis/ingredients.py 의 FUNCTION_GROUPS 정규식에 맞는 분류를 모두 붙인다
  5. 아무 분류도 없으면 행을 만들지 않는다 (집계 때 «미분류»)
v2 에서 바꾼 것 (2026-11-21 사용자 결정 — 분류 정확도 점검에서 빠지던 표현)
  - 면역 기능: «면역력» 추가 (예: 면역력 증진에 도움)
  - 뼈 → «뼈 건강» 으로 이름을 바꾸고 «골다공증» 추가 (예: 골다공증발생 위험 감소에 도움)
"""
import json
import os
import re
import sqlite3
import sys
import zlib

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data").replace("\\", "/")
DB = r"C:\Users\pharm\product-planning\data\market.db"
SRC_RULES = r"C:\Users\pharm\product-planning\analysis\ingredients.py"

SPLIT = re.compile(
    r"\r?\n|[\u2460-\u2473\u3260-\u327b]|(?<![\d.])\d{1,2}\s?[.)](?!\d)|\(\s?\d{1,2}\s?\)"
    r"|\(\s?[가-하]\s?\)|(?<![가-힣])[가-하]\)|[,，;；/]")
BRACKET = re.compile(r"\[[^\]]*\]")


def load_v1():
    src = open(SRC_RULES, encoding="utf-8").read()
    ns = {}
    exec(compile(re.search(r"FUNCTION_GROUPS = \{.*?\n\}", src, re.S).group(0), "fg", "exec"), ns)
    return dict(ns["FUNCTION_GROUPS"])


def make_v2(v1):
    v2 = {}
    for g, pat in v1.items():
        if g == "면역 기능":
            v2[g] = pat + r"|면역력"
        elif g == "뼈":
            v2["뼈 건강"] = pat + r"|골다공증"
        else:
            v2[g] = pat
    return v2


def classify(text, groups):
    found = []
    for piece in SPLIT.split(BRACKET.sub(" ", text or "")):
        piece = piece.strip()
        if not piece:
            continue
        hit = ["영양소 기능"] if "필요" in piece else [g for g, p in groups.items() if re.search(p, piece)]
        for g in hit:
            if g not in found:
                found.append(g)
    return found


def frame(texts, groups):
    rows = [(fid, g) for fid, t in texts for g in classify(t, groups)]
    return pd.DataFrame(rows, columns=["filing_id", "function_group"]).drop_duplicates()


def main():
    con = sqlite3.connect(DB)
    raw = {}
    for (body,) in con.execute("SELECT body FROM raw_responses WHERE source='mfds' AND request LIKE 'I0030/%' "
                               "AND id BETWEEN 28 AND 74 ORDER BY id"):
        for r in json.loads(zlib.decompress(body))["I0030"]["row"]:
            raw[str(r["PRDLST_REPORT_NO"]).strip()] = r.get("PRIMARY_FNCLTY")
    ids = [r[0] for r in duckdb.connect().execute(f"SELECT filing_id FROM read_parquet('{DATA}/filings.parquet')").fetchall()]
    texts = [(i, raw[i]) for i in ids]
    v1 = load_v1()

    # v1 이 기존 엑셀과 같은지 먼저 확인 — 같지 않으면 멈춘다
    old = pd.read_excel(os.path.join(HERE, "내려받기", "기능성분류_2026.xlsx"), dtype=str)
    a = frame(texts, v1)
    same = set(map(tuple, a.values)) == set(map(tuple, old[["filing_id", "function_group"]].values))
    print(f"v1 재현: {len(a):,}행 · 엑셀 {len(old):,}행 · 같음 {same}")
    if not same:
        sys.exit("v1 규칙이 기존 엑셀과 다르게 나옵니다 — 멈춥니다")
    if "--check" in sys.argv:
        return

    b = frame(texts, make_v2(v1)).sort_values(["filing_id", "function_group"]).reset_index(drop=True)
    tbl = pa.Table.from_pandas(b, schema=pa.schema([("filing_id", pa.string()), ("function_group", pa.string())]),
                               preserve_index=False)
    pq.write_table(tbl, os.path.join(DATA, "filing_functions.parquet"))

    # v1 → v2 차이
    n = len(ids)
    un1, un2 = n - a["filing_id"].nunique(), n - b["filing_id"].nunique()
    multi = lambda d: int((d.groupby("filing_id").size() > 1).sum())
    print(f"v2: {len(b):,}행 → data/filing_functions.parquet")
    print(f"  행 수 {len(a):,} → {len(b):,} · 미분류 신고 {un1} → {un2} · 기능성 2개 이상 신고 {multi(a):,} → {multi(b):,}"
          f" · 신고당 최대 {a.groupby('filing_id').size().max()} → {b.groupby('filing_id').size().max()}")
    c1, c2 = a["function_group"].value_counts(), b["function_group"].value_counts()
    for g in sorted(set(c1.index) | set(c2.index)):
        if c1.get(g, 0) != c2.get(g, 0):
            print(f"  {g}: {c1.get(g, 0)} → {c2.get(g, 0)}")


if __name__ == "__main__":
    main()
