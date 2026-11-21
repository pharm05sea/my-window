# -*- coding: utf-8 -*-
"""주원료(filing_materials)를 한국식품연구원 자료의 이름과 대조해 data/material_links.parquet 을 만든다.

    python tools/match_materials.py

입력
  data/filing_materials.parquet   신고 1건 × 주원료 1개 (tools/build_materials.py)
  tools/ref/kfri_names.json       한국식품연구원 이름 목록 (tools/extract_kfri_names.py)
  tools/material_synonyms.tsv     사람이 확인한 동의어 (관계 same / related)

단계
  A  정규화 후 이름이 같다 — 괄호 안 · 공백 · 기호를 지우고, 끝의 형태 낱말(추출물 · 분말 · 농축액 …)을 뗀 뒤 비교
  B  동의어 표의 관계 same 으로 같다 (사람이 확인한 것만)
  R  동의어 표의 관계 related — 관련 소재로만 기록하고 «연결»로 세지 않는다
부분 일치 · 비슷한 글자는 자동으로 연결하지 않는다 (후보로만 화면에 보여준다).
"""
import collections
import csv
import io
import json
import os
import re
import sys

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data").replace("\\", "/")
SUF = re.compile(r"(추출물|추출분말|추출액|추출농축액|농축액|농축분말|농축물|건조분말|건조물|분말|엑스|엑기스|가루|발효물|추출)$")
CANDIDATES = [("영양소 기능", "액상"), ("혈행", "분말"), ("피로 개선", "분말")]   # npd_candidates 결과


def norm(s):
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", s or "")
    return re.sub(r"[\s\-·ㆍ.,/]", "", s).lower()


def core(s):
    n, prev = norm(s), None
    while n != prev:
        prev, n = n, SUF.sub("", n)
    return n


def main():
    ref = json.load(io.open(os.path.join(HERE, "tools", "ref", "kfri_names.json"), encoding="utf-8"))
    tgt = collections.defaultdict(lambda: collections.defaultdict(dict))      # core → 출처 → {이름: 행 수}
    for src, names in ref["names"].items():
        for name, n in names.items():
            c = core(name)
            if c:
                tgt[c][src][name] = n
    syn = list(csv.DictReader(io.open(os.path.join(HERE, "tools", "material_synonyms.tsv"), encoding="utf-8"), delimiter="\t"))
    con = duckdb.connect()
    fm = con.execute(f"SELECT filing_id, material FROM read_parquet('{DATA}/filing_materials.parquet')").fetchall()
    mats = sorted({m for _, m in fm})

    links = []   # (material, std_name, tier, relation, source, target_name, target_rows)
    tier_of = {}
    for m in mats:
        c = core(m)
        if c in tgt:
            tier_of[m] = "A"
            for src, d in tgt[c].items():
                for name, n in d.items():
                    links.append((m, m, "A", "same", src, name, n))
        for s in syn:
            if core(s["원료이름"]) != c:
                continue
            tc = core(s["대상이름"])
            tier = "B" if s["관계"] == "same" else "R"
            for src, d in tgt.get(tc, {}).items():
                for name, n in d.items():
                    links.append((m, s["표준소재명"], tier, s["관계"], src, name, n))
            if tier == "B" and tc in tgt:
                tier_of.setdefault(m, "B")
            elif tier == "R" and tc in tgt:
                tier_of.setdefault(m, "R")
    cols = ["material", "std_name", "tier", "relation", "source", "target_name", "target_rows"]
    tbl = pa.table({k: [r[i] for r in links] for i, k in enumerate(cols)},
                   schema=pa.schema([(k, pa.int64() if k == "target_rows" else pa.string()) for k in cols]))
    pq.write_table(tbl, os.path.join(DATA, "material_links.parquet"))

    cand = {r[0] for r in con.execute(f"""
        SELECT DISTINCT ff.filing_id FROM read_parquet('{DATA}/filing_functions.parquet') ff
        JOIN read_parquet('{DATA}/product_details.parquet') p USING (filing_id)
        WHERE (ff.function_group, p.dosage_form) IN ({", ".join(f"('{a}', '{b}')" for a, b in CANDIDATES)})""").fetchall()}
    linked = lambda m: tier_of.get(m) in ("A", "B")

    def report(label, fids):
        ms = sorted({m for f, m in fm if f in fids})
        a = sum(tier_of.get(m) == "A" for m in ms); b = sum(tier_of.get(m) == "B" for m in ms)
        r = sum(tier_of.get(m) == "R" for m in ms)
        lf = {f for f, m in fm if f in fids and linked(m)}
        rf = {f for f, m in fm if f in fids and (linked(m) or tier_of.get(m) == "R")}
        print(f"{label}: 고유 원료 {len(ms)} · A {a} · B {b} · 연결 {a + b} ({100 * (a + b) / len(ms):.1f}%) · 관련(R) {r}"
              f" | 연결 원료가 있는 신고 {len(lf):,}/{len(fids):,} ({100 * len(lf) / len(fids):.1f}%)"
              f" · 관련까지 포함 {len(rf):,} ({100 * len(rf) / len(fids):.1f}%)")

    report("전체", {f for f, _ in fm})
    report("후보 3조합", cand)
    print(f"material_links {tbl.num_rows}행 → data/material_links.parquet")


if __name__ == "__main__":
    main()
