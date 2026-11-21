# -*- coding: utf-8 -*-
"""한국식품연구원 자료에서 원료 대조에 쓸 «이름»만 뽑아 tools/ref/kfri_names.json 에 저장한다.

    python tools/extract_kfri_names.py      (xls 를 읽으려면 xlrd 가 필요하다)

읽는 파일 (이 폴더 맨 위, 2019년 공개분)
  - 한국식품연구원_대표식품별 건강기능 (한의문헌) 정보_20190305.xlsx   → 대표식품명
  - 한국식품연구원_대표식품별 건강기능(학술) 정보_20190305.xlsx       → 대표식품명
  - 대표식품별_건강기능_병증처방_정보_1~3_20190305.xls (시트 3개씩)    → 대표식품명 · 처방(현대어) · 구성약제의 독음·현대어
이름과 그 이름이 나온 행 수만 저장한다. 문헌 내용·병증 내용은 저장하지 않는다.
"""
import collections
import datetime
import json
import os
import re
import sys

import openpyxl
import xlrd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "tools", "ref", "kfri_names.json")
XLSX = {
    "한의문헌": "한국식품연구원_대표식품별 건강기능 (한의문헌) 정보_20190305.xlsx",
    "학술": "한국식품연구원_대표식품별 건강기능(학술) 정보_20190305.xlsx",
}
RX = [f"대표식품별_건강기능_병증처방_정보_{k}_20190305.xls" for k in (1, 2, 3)]
N = lambda s: re.sub(r"\s+", "", str(s))


def main():
    names = collections.defaultdict(collections.Counter)   # 출처 → 이름 → 행 수
    rows = {}
    for src, f in XLSX.items():
        ws = openpyxl.load_workbook(os.path.join(HERE, f), read_only=True).active
        it = ws.iter_rows(values_only=True)
        j = [N(h) for h in next(it)].index("대표식품명")
        n = 0
        for r in it:
            n += 1
            if r[j]:
                names[src][str(r[j]).strip()] += 1
        rows[f] = n
    for f in RX:
        bk = xlrd.open_workbook(os.path.join(HERE, f))
        n = 0
        for sh in bk.sheets():
            hdr = [N(v) for v in sh.row_values(2)]          # 머리글은 3번째 줄 (시트에 따라 줄바꿈이 섞여 있어 공백을 지운다)
            ci = {h: j for j, h in enumerate(hdr)}
            jr = next(j for j, h in enumerate(hdr) if h.startswith("구성약제"))
            for i in range(3, sh.nrows):
                v = [str(x).strip() for x in sh.row_values(i)]
                n += 1
                names["병증처방·대표식품"][v[ci["대표식품명"]]] += 1
                if v[ci["처방(현대어)"]]:
                    names["병증처방·처방"][v[ci["처방(현대어)"]]] += 1
                # 구성약제: [원문, 독음, 이명…, 현대어, 수량, 단위, 기타, 약재용법, 주재료/부재료] 이 여러 개
                #   독음과 현대어가 같으면 한 번만 센다
                for g in re.findall(r"\[([^\[\]]*)\]", v[jr]):
                    p = [x.strip() for x in g.split(",")]
                    herb = set()
                    if len(p) >= 2 and p[1]:
                        herb.add(p[1])
                    if len(p) >= 7 and p[-1] in ("주재료", "부재료") and p[-6]:
                        herb.add(p[-6])
                    for h in herb:
                        names["병증처방·약재"][h] += 1
        rows[f] = n
    out = {"made": datetime.date.today().isoformat(), "files": rows,
           "names": {s: dict(sorted(c.items())) for s, c in names.items()}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("읽은 행:", {k: f"{v:,}" for k, v in rows.items()})
    print("출처별 고유 이름:", {s: len(c) for s, c in names.items()}, "→ tools/ref/kfri_names.json")


if __name__ == "__main__":
    main()
