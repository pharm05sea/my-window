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
    "filing_materials": "품목신고 1건의 주원료 1개 (품목신고 1건 × 주원료 1개)",
    "material_links": "주원료 이름 1개 × 한국식품연구원 자료의 대상 이름 1개 (연결 1건)",
}
KEY = {  # 한 줄을 가리키는 열 — 실행 때 고유한지 확인
    "filings": ["filing_id"],
    "filing_functions": ["filing_id", "function_group"],
    "product_details": ["filing_id"],
    "filing_materials": ["filing_id", "material"],
    "material_links": ["material", "tier", "source", "target_name"],
}
DESC = {
    "filings": (
        "식품안전나라 I0030 건강기능식품 품목제조 신고사항. 허가일자 2026-05-01 ~ 2026-10-31 품목. "
        "filing_id(품목제조번호)가 신고 1건을 식별하는 기준이다 — 행 수 = filing_id 고유값. "
        "품목 수는 이 표의 filing_id 로 센다."
    ),
    "filing_functions": (
        "I0030 주된 기능성 문구를 규칙으로 분류한 결과 (규칙 v2, tools/classify_functions.py — v1 에 «면역력»→면역 기능, "
        "«골다공증»→뼈 건강(기존 «뼈»의 새 이름)을 더함. 내려받기/기능성분류_2026.xlsx 와 잠가 둔 정답은 v1 기준). "
        "신고 1건에 기능성이 0개 ~ 여러 개다. "
        "기능성이 하나도 분류되지 않은 신고는 이 표에 행이 없다 — 집계 때 «미분류»로 남기려면 filings 기준 LEFT JOIN. "
        "filings 와 바로 조인하면 기능성 수만큼 신고가 여러 줄로 늘어나므로(팬아웃) "
        "품목 수는 COUNT(*) 가 아니라 COUNT(DISTINCT filing_id) 로 센다."
    ),
    "product_details": (
        "공공데이터포털 식품의약품안전처 건강기능식품정보 상세조회(HtfsInfoService03/getHtfsItem01) 결과 중 "
        "응답 품목번호(STTEMNT_NO)가 filing_id 와 같은 것. filing_id 당 최대 1행(0~1행). "
        "상세가 없는 신고는 이 표에 행이 없다 — filings 기준 LEFT JOIN 으로 «제형 미확인»을 남긴다."
    ),
    "filing_materials": (
        "I0030 주원료(RAWMTRL_NM)를 괄호 밖 쉼표로 나눠 신고마다 한 줄씩 펼친 표 (tools/build_materials.py). "
        "신고 1건에 주원료가 여러 개다 — filings 와 바로 조인하면 원료 수만큼 신고가 늘어나므로 "
        "품목 수는 COUNT(DISTINCT filing_id) 로 센다. 원료 이름은 원문 그대로이며 표준화하지 않았다."
    ),
    "material_links": (
        "filing_materials 의 주원료 이름을 한국식품연구원 자료(한의문헌 · 학술 · 병증처방 1~3, 2019년 공개)의 이름과 대조한 결과 "
        "(tools/match_materials.py). 신고번호가 아니라 «원료 이름» 단위 연결이다 — filing_materials 와 material 로 조인한다. "
        "tier A = 정규화 후 같은 이름, B = 사람이 확인한 동의어(tools/material_synonyms.tsv, relation same), "
        "R = 관련 소재(relation related, 예: 홍삼 → 인삼)로 «연결»로 세지 않는다. 연결 안 된 원료는 이 표에 행이 없다. "
        "한 원료가 여러 대상 이름에 연결되므로 조인하면 행이 늘어난다. 문헌 기록은 효능 근거가 아니다."
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
    "filing_materials": {
        "filing_id": "filings.filing_id 와 연결",
        "material": "주원료 이름 원문 (I0030 RAWMTRL_NM 의 한 조각, 예: 은행잎 추출물 · 비타민 C)",
    },
    "material_links": {
        "material": "filing_materials.material 과 같은 원문 이름",
        "std_name": "표준 소재명 (A 는 원문 그대로, B · R 은 동의어 표의 표준소재명)",
        "tier": "A 정규화 일치 · B 확인된 동의어 · R 관련 소재(연결로 세지 않음)",
        "relation": "same 같은 소재 · related 관련 소재",
        "source": "대상 자료 — 한의문헌 · 학술 · 병증처방·대표식품 · 병증처방·처방 · 병증처방·약재",
        "target_name": "대상 자료에 적힌 이름 원문",
        "target_rows": "대상 자료에서 그 이름이 나온 행 수 (문헌 · 처방 기록 수, 효능 근거 아님)",
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
