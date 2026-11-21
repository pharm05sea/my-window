# -*- coding: utf-8 -*-
"""data/*.parquet 에서 표 설명(schema.json)을 만든다.

    python tools/make_schema.py

- 열 이름·자료형은 parquet 에서 읽는다
- 종류가 12가지 이하인 글자 열은 값 목록을 함께 적는다 (AI가 SQL을 쓸 때 쓴다)
- **한 줄이 무엇인지(그레인)와 표·열 설명은 사람이 정한 것**이다
- 키가 정말 한 줄을 가리키는지는 실행할 때 parquet 으로 다시 확인한다 (아니면 멈춘다)
"""
import io
import json
import os

import duckdb

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")

GRAIN = {
    "filings": "품목신고 1건",
    "filing_functions": "품목신고 1건에 분류된 기능성 1개 (품목신고 1건 × 기능성 1개)",
    "product_details": "품목신고 1건의 제품 상세정보 1건",
}
KEY = {  # 한 줄을 가리키는 열 — 실행 때 고유한지 확인
    "filings": ["filing_id"],
    "filing_functions": ["filing_id", "function_group"],
    "product_details": ["filing_id"],
}
DESC = {
    "filings": (
        "식품안전나라 I0030 건강기능식품 품목제조 신고사항. 허가일자 2026-05-01 ~ 2026-10-31 품목. "
        "filing_id(품목제조번호)가 신고 1건을 식별하는 기준이다 — 행 수 = filing_id 고유값. "
        "품목 수는 이 표의 filing_id 로 센다."
    ),
    "filing_functions": (
        "I0030 주된 기능성 문구를 규칙으로 분류한 결과. 신고 1건에 기능성이 0개 ~ 여러 개다. "
        "기능성이 하나도 분류되지 않은 신고는 이 표에 행이 없다 — 집계 때 «미분류»로 남기려면 filings 기준 LEFT JOIN. "
        "filings 와 바로 조인하면 기능성 수만큼 신고가 여러 줄로 늘어나므로(팬아웃) "
        "품목 수는 COUNT(*) 가 아니라 COUNT(DISTINCT filing_id) 로 센다."
    ),
    "product_details": (
        "공공데이터포털 식품의약품안전처 건강기능식품정보 상세조회(HtfsInfoService03/getHtfsItem01) 결과 중 "
        "응답 품목번호(STTEMNT_NO)가 filing_id 와 같은 것. filing_id 당 최대 1행(0~1행). "
        "상세가 없는 신고는 이 표에 행이 없다 — filings 기준 LEFT JOIN 으로 «제형 미확인»을 남긴다."
    ),
}
COL_DESC = {
    "filings": {
        "filing_id": "품목제조번호 PRDLST_REPORT_NO (문자열) — 신고 1건의 키",
        "filing_date": "허가일자 PRMS_DT (기간 집계 기준일)",
        "product_name": "품목명 PRDLST_NM",
        "manufacturer_id": "인허가번호 LCNS_NO (문자열) — 제조업소 수는 이 값의 고유값",
        "manufacturer_name": "업소명 BSSH_NM",
    },
    "filing_functions": {
        "filing_id": "filings.filing_id 와 연결",
        "function_group": "주된 기능성 문구를 규칙으로 분류한 기능성 그룹 (API 원문 값 아님)",
    },
    "product_details": {
        "filing_id": "filings.filing_id 와 연결 (상세 응답 STTEMNT_NO 와 일치한 것만)",
        "dosage_form": (
            "제형 — API 원문 값이 아니라 규칙으로 분류한 값. 근거: 상세조회 원본 응답(원본/ 보관)의 필드는 "
            "BASE_STANDARD · DISTB_PD · ENTRPS · INTAKE_HINT1 · MAIN_FNCTN · PRDUCT · PRSRV_PD · REGIST_DT · "
            "SRV_USE · STTEMNT_NO · SUNGSANG 뿐이고 제형 필드가 없다. 짧은 제형 이름 몇 가지로만 되어 있어 "
            "성상(SUNGSANG) 문구에서 분류한 값으로 본다. 비어 있으면 판정 못 한 것"
        ),
        "intake_method": "섭취방법 SRV_USE 원문",
    },
}
SKIP_VALUES = {"product_name", "manufacturer_name", "intake_method"}


def main():
    con = duckdb.connect()
    out = {"note": "한 줄이 무엇인지(grain)는 사람이 정한 것입니다. 조인하기 전에 반드시 봅니다. "
                   "품목 수는 filings.filing_id 고유값으로 셉니다 — filing_functions 를 붙이면 신고가 기능성 수만큼 늘어납니다.",
           "tables": {}}
    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".parquet"):
            continue
        t = f[:-8]
        path = os.path.join(DATA, f).replace("\\", "/")
        src = f"read_parquet('{path}')"
        n = con.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0]
        key = ", ".join(KEY[t])
        uniq = con.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT {key} FROM {src})").fetchone()[0]
        assert uniq == n, f"{t}: 키({key})가 한 줄을 가리키지 않습니다 ({uniq} ≠ {n})"
        cols = []
        for name, typ, *_ in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall():
            c = {"name": name, "type": typ}
            if name in COL_DESC.get(t, {}):
                c["desc"] = COL_DESC[t][name]
            if typ == "VARCHAR" and name not in SKIP_VALUES:
                vals = con.execute(f"SELECT DISTINCT {name} FROM {src} "
                                   f"WHERE {name} IS NOT NULL ORDER BY 1 LIMIT 13").fetchall()
                if len(vals) <= 12:
                    c["values"] = [v[0] for v in vals]
            cols.append(c)
        out["tables"][t] = {"grain": GRAIN[t], "key": KEY[t], "desc": DESC[t], "rows": n, "columns": cols}
    io.open(os.path.join(HERE, "schema.json"), "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"표 {len(out['tables'])}개 · 전체 {sum(v['rows'] for v in out['tables'].values()):,}행")


if __name__ == "__main__":
    main()
