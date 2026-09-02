#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""학교안전지원시스템 어린이통학버스 등록현황.

공개 조회 화면을 우선 사용하며 인증키가 필요 없다. 7일 캐시한다. 사이트 조회가
막힐 때만 사용자가 내려받은 XLSX를 가져와 보조자료로 쓴다. 차량번호와 운영자
실명은 화면에 표시하거나 가져오기 캐시에 보관하지 않는다.
"""
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache" / "schoolbus"
BASE = "https://www.schoolsafe24.or.kr"
LIST_URL = BASE + "/front/scbus/scbusCmptncSchoolAjaxList.do"
DETAIL_URL = BASE + "/front/scbus/scbusCmptncSchoolDetail.do"
TTL_DAYS = 7
TIMEOUT = 30
SIDO_10 = {"11": "1100000000", "26": "2600000000", "27": "2700000000",
           "28": "2800000000", "29": "2900000000", "30": "3000000000",
           "31": "3100000000", "36": "3611000000", "41": "4100000000",
           "42": "5100000000", "51": "5100000000", "43": "4300000000",
           "44": "4400000000", "45": "5200000000", "52": "5200000000",
           "46": "4600000000", "47": "4700000000", "48": "4800000000",
           "50": "5000000000"}


class BusError(Exception):
    pass


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.table, self.row, self.cell, self.buf = [], None, None, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "table": self.table = []
        elif tag == "tr" and self.table is not None: self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell, self.buf = True, []

    def handle_data(self, data):
        if self.cell: self.buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell:
            self.row.append(" ".join("".join(self.buf).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row: self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def _tables(text):
    p = _Tables(); p.feed(text); return p.tables


def _norm(text):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(text or "")).lower()


def _road_key(addr):
    text = re.sub(r"\([^)]*\)", "", str(addr or ""))
    return _norm(text.replace("광역시", "").replace("특별시", "").replace("특별자치시", ""))


def _post(url, values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    req = urllib.request.Request(url, data=data,
        headers={"User-Agent": "Mozilla/5.0 (kaic-kinder-info/1.10)",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": BASE + "/front/scbus/scbusCmptncSchoolList.do?menuSn=170&upperMenuSn=146"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise BusError(f"학교안전지원시스템 조회 실패: {e}")


def _base_params(name, sido):
    return {"menuSn": "170", "upperMenuSn": "146", "pageIndex": "1",
            "perPage": "100", "listType": "LIST", "schlCd": "", "excelDown": "",
            "searchRgnSeCd": SIDO_10.get(str(sido), ""), "searchCmptncSeCd": "",
            "searchFcltNm": name, "searchFcltSeCd": "SCH00001"}


def _list(name, sido):
    text = _post(LIST_URL, _base_params(name, sido))
    tables = _tables(text)
    grid = next((g for g in tables if g and "시설명" in g[0] and "등록차량수" in g[0]), None)
    if grid is None:
        if "총 <strong" in text and ">0<" in text:
            return []
        raise BusError("통학버스 목록 화면 구조가 바뀐 것 같습니다")
    codes = { _norm(html.unescape(label)): code for code, label in
             re.findall(r"fnView\('([^']+)'\);[^>]*>([^<]+)</a>", text) }
    head = grid[0]
    out = []
    for row in grid[1:]:
        if len(row) < len(head): continue
        d = dict(zip(head, row))
        d["code"] = codes.get(_norm(d.get("시설명")))
        out.append(d)
    return out


def _summary_from_detail(text):
    tables = _tables(text)
    facility = next((g for g in tables if g and "시설명" in " ".join(g[0]) and "시설구분" in " ".join(g[0])), [])
    vehicles = next((g for g in tables if g and g[0][:2] == ["차량번호", "제작사/차명"]), [])
    operator = next((g for g in tables if g and "운영자 교육" in g[0]), [])
    drivers = next((g for g in tables if g and "운전자 교육" in g[0]), [])
    companions = next((g for g in tables if g and "동승자 교육" in g[0]), [])
    if not facility or not vehicles:
        raise BusError("통학버스 상세 화면 구조가 바뀐 것 같습니다")

    fflat = [c for r in facility for c in r]
    fname = fflat[fflat.index("시설명") + 1] if "시설명" in fflat else ""
    addr = fflat[fflat.index("주소") + 1] if "주소" in fflat else ""
    vrows = vehicles[1:]
    sizes, ownership = {}, {}
    for r in vrows:
        if len(r) < 4: continue
        sizes[r[2]] = sizes.get(r[2], 0) + 1
        ownership[r[3]] = ownership.get(r[3], 0) + 1

    def stat(grid, col):
        vals = [r[col] for r in grid[1:] if len(r) > col]
        return {v: vals.count(v) for v in sorted(set(vals))}

    return {"facility_name": fname, "address": addr, "vehicle_count": len(vrows),
            "vehicle_sizes": sizes, "ownership": ownership,
            "operator_training": stat(operator, 1),
            "driver_training": stat(drivers, 1),
            "companion_training": stat(companions, 1),
            "companion_employment": stat(companions, 2),
            "source": "학교안전지원시스템 공개 통학버스 등록현황",
            "source_url": BASE + "/front/scbus/scbusCmptncSchoolList.do?menuSn=170&upperMenuSn=146",
            "retrieved_at": datetime.now().isoformat(timespec="seconds"), "mode": "live"}


def _cache_file(name, address):
    h = hashlib.sha256((_norm(name) + "|" + _road_key(address)).encode()).hexdigest()[:20]
    return CACHE / f"live_{h}.json"


def query(name, address, sido, fresh=False):
    cache = _cache_file(name, address)
    if not fresh and cache.exists():
        if datetime.now().timestamp() - cache.stat().st_mtime < timedelta(days=TTL_DAYS).total_seconds():
            try: return json.loads(cache.read_text(encoding="utf-8"))
            except ValueError: pass
    try:
        rows = _list(name, sido)
    except BusError as e:
        fallback = query_import(name, address)
        if fallback:
            fallback["live_error"] = str(e)
            return fallback
        raise
    same = [r for r in rows if _norm(r.get("시설명")) == _norm(name)]
    if len(same) > 1 and address:
        exact_addr = [r for r in same if _road_key(address) in _road_key(r.get("소재지(주소)")) or
                      _road_key(r.get("소재지(주소)")) in _road_key(address)]
        if exact_addr: same = exact_addr
    if len(same) != 1 or not same[0].get("code"):
        fallback = query_import(name, address)
        if fallback: return fallback
        return {"status": "not_found" if not same else "ambiguous", "matches": len(same),
                "source": "학교안전지원시스템 공개 통학버스 등록현황", "mode": "live"}
    params = _base_params(name, sido); params["schlCd"] = same[0]["code"]
    try:
        result = _summary_from_detail(_post(DETAIL_URL, params))
    except BusError as e:
        fallback = query_import(name, address)
        if fallback:
            fallback["live_error"] = str(e)
            return fallback
        raise
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache)
    return result


def _xlsx_rows(path):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(ns + "si"):
                shared.append("".join((t.text or "") for t in si.iter(ns + "t")))
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        for row in root.findall(".//" + ns + "sheetData/" + ns + "row"):
            vals = [""] * 8
            for c in row.findall(ns + "c"):
                ref = c.get("r", "A1")
                letters = re.match(r"[A-Z]+", ref).group()
                col = 0
                for ch in letters: col = col * 26 + ord(ch) - 64
                col -= 1
                if col >= len(vals): continue
                v = c.find(ns + "v")
                val = "" if v is None else (v.text or "")
                if c.get("t") == "s" and val: val = shared[int(val)]
                vals[col] = val
            yield vals


def _vehicle_cell_summary(cell):
    if not cell or cell.strip() == "-": return {"count": 0, "sizes": {}}
    # 현재 공개 엑셀은 차량들을 쉼표 또는 줄바꿈으로 구분한다.
    parts = [p for p in re.split(r"[,\r\n]+", cell) if p.strip()]
    sizes = {}
    for p in parts:
        m = re.search(r"\(([^()]*(?:인승(?:이상)?|승))\)\s*$", p)
        size = m.group(1) if m else "규모 미상"
        sizes[size] = sizes.get(size, 0) + 1
    return {"count": len(parts), "sizes": sizes}


def import_xlsx(path):
    path = Path(path)
    if not path.exists(): raise BusError(f"파일을 찾을 수 없습니다: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rows = list(_xlsx_rows(path))
    header_idx = next((i for i, r in enumerate(rows) if "시설명" in r and "소재지(주소)" in r), None)
    if header_idx is None: raise BusError("통학버스 엑셀 머리글을 찾지 못했습니다")
    head = rows[header_idx]
    data = []
    for r in rows[header_idx + 1:]:
        d = dict(zip(head, r))
        if d.get("시설 종류") != "유치원" or not d.get("시설명"): continue
        vs = _vehicle_cell_summary(d.get("차명(차량번호)(승차정원)"))
        data.append({"facility_name": d["시설명"], "address": d.get("소재지(주소)", ""),
                     "vehicle_count": vs["count"], "vehicle_sizes": vs["sizes"]})
    payload = {"imported_at": datetime.now().isoformat(timespec="seconds"),
               "source_file": path.name, "sha256": digest, "row_count": len(data), "rows": data}
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / "manual_import.json"; tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(out)
    return {k: v for k, v in payload.items() if k != "rows"}


def query_import(name, address=""):
    path = CACHE / "manual_import.json"
    if not path.exists(): return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    same = [r for r in payload.get("rows", []) if _norm(r.get("facility_name")) == _norm(name)]
    if len(same) > 1 and address:
        exact = [r for r in same if _road_key(address) in _road_key(r.get("address")) or
                 _road_key(r.get("address")) in _road_key(address)]
        if exact: same = exact
    if len(same) != 1: return None
    out = dict(same[0]); out.update({"source": "사용자 수동 통학버스 엑셀",
        "retrieved_at": payload.get("imported_at"), "source_file": payload.get("source_file"),
        "mode": "manual_import"})
    return out


def status():
    imp = CACHE / "manual_import.json"
    live = list(CACHE.glob("live_*.json")) if CACHE.exists() else []
    out = {"live_cache_count": len(live), "live_ttl_days": TTL_DAYS, "manual_import": None}
    if imp.exists():
        d = json.loads(imp.read_text(encoding="utf-8"))
        out["manual_import"] = {k: d.get(k) for k in ("imported_at", "source_file", "sha256", "row_count")}
    return out


def clear_cache(live_only=True):
    n = 0
    if CACHE.exists():
        for p in CACHE.glob("live_*.json"):
            p.unlink(); n += 1
        if not live_only:
            p = CACHE / "manual_import.json"
            if p.exists(): p.unlink(); n += 1
    return n


def selftest(verbose=True):
    try:
        rows = _list("유치원", "11")
        ok = isinstance(rows, list)
    except BusError as e:
        ok = False
        if verbose: print(f"  ❌ {e}")
    if verbose and ok:
        print(f"  ✅ 학교안전지원시스템 공개 목록 파싱 ({len(rows)}건 표본)")
        print("→ 정상: 통학버스 자동 조회가 동작합니다.")
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(0 if selftest() else 1)
    print(__doc__)
