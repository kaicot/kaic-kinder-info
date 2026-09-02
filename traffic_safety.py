#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""도로교통공단 어린이 사고다발지와 실제 자차 경로의 교차 분석.

공식 CSV(인증키 불필요)의 폴리곤과 OSRM 전체 자차 경로를 비교한다. 직선거리나
임의 반경으로 안전성을 판단하지 않는다. 결과가 0건이어도 '안전'이 아니라
'공식 사고다발지 선정 기준에 해당하는 구간이 확인되지 않음'으로만 해석한다.
"""
import csv
import json
import math
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache" / "traffic"
TTL_DAYS = 30
TIMEOUT = 60
SOURCES = {
    "child": {
        "label": "보행어린이 교통사고 다발지역",
        "url": "https://opendata.koroad.or.kr/api/down/childdown.jsp",
    },
    "schoolzone": {
        "label": "어린이보호구역 내 어린이 교통사고 다발지역",
        "url": "https://opendata.koroad.or.kr/api/down/schoolzonedown.jsp",
    },
}


class TrafficError(Exception):
    pass


def _download(kind, fresh=False):
    if kind not in SOURCES:
        raise TrafficError(f"지원하지 않는 교통 자료: {kind}")
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{kind}.csv"
    if not fresh and path.exists():
        age = datetime.now().timestamp() - path.stat().st_mtime
        if age < timedelta(days=TTL_DAYS).total_seconds():
            return path
    req = urllib.request.Request(SOURCES[kind]["url"],
                                 headers={"User-Agent": "kaic-kinder-info/1.10"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        if path.exists():
            return path                 # 갱신 실패 시 마지막 정상본
        raise TrafficError(f"도로교통공단 CSV 다운로드 실패: {e}")
    if len(data) < 1000 or b"," not in data[:500]:
        if path.exists():
            return path
        raise TrafficError("도로교통공단 CSV 응답 형식이 예상과 다릅니다")
    tmp = path.with_suffix(".csv.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    meta = {"downloaded_at": datetime.now().isoformat(timespec="seconds"),
            "source_url": SOURCES[kind]["url"], "bytes": len(data)}
    mpath = CACHE / f"{kind}.meta.json"
    mt = mpath.with_suffix(".json.tmp")
    mt.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    mt.replace(mpath)
    return path


def _polygons(geometry):
    if not geometry:
        return []
    typ, coords = geometry.get("type"), geometry.get("coordinates") or []
    if typ == "Polygon":
        return [coords[0]] if coords else []
    if typ == "MultiPolygon":
        return [poly[0] for poly in coords if poly]
    return []


def load(kind, fresh=False, recent_years=5):
    path = _download(kind, fresh=fresh)
    rows = []
    try:
        fh = path.open(encoding="cp949", newline="")
    except UnicodeDecodeError:
        fh = path.open(encoding="utf-8-sig", newline="")
    with fh:
        reader = csv.DictReader(fh)
        required = {"사고다발지id", "지점명", "사고건수", "경도", "위도", "다발지역폴리곤"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise TrafficError("도로교통공단 CSV 열 구성이 바뀐 것 같습니다")
        for r in reader:
            try:
                year = int(str(r["사고다발지id"])[:4])
                geom = json.loads(r["다발지역폴리곤"])
                polygons = _polygons(geom)
                if not polygons:
                    continue
                rows.append({
                    "kind": kind, "year": year,
                    "name": r["지점명"].strip(),
                    "accidents": int(r["사고건수"] or 0),
                    "casualties": int(r.get("사상자수") or 0),
                    "deaths": int(r.get("사망자수") or 0),
                    "center": [float(r["경도"]), float(r["위도"])],
                    "polygons": polygons,
                })
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
    if not rows:
        raise TrafficError("도로교통공단 CSV에서 사고다발지를 읽지 못했습니다")
    latest = max(r["year"] for r in rows)
    floor = latest - max(1, recent_years) + 1
    return [r for r in rows if r["year"] >= floor]


def _inside(point, poly):
    x, y = point
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)):
            cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi
            if x < cross:
                inside = not inside
        j = i
    return inside


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a, b, c, d):
    eps = 1e-12
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    proper = (((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and
              ((o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)))
    if proper:
        return True

    def on_segment(p, q, r):
        return (abs(_orient(p, q, r)) <= eps and
                min(p[0], q[0]) - eps <= r[0] <= max(p[0], q[0]) + eps and
                min(p[1], q[1]) - eps <= r[1] <= max(p[1], q[1]) + eps)
    return any((on_segment(a, b, c), on_segment(a, b, d),
                on_segment(c, d, a), on_segment(c, d, b)))


def _bbox(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _route_crosses(route_coords, poly):
    if not route_coords or len(route_coords) < 2:
        return False
    minx, miny, maxx, maxy = _bbox(poly)
    for p in route_coords:
        if minx <= p[0] <= maxx and miny <= p[1] <= maxy and _inside(p, poly):
            return True
    edges = list(zip(poly, poly[1:] + poly[:1]))
    for a, b in zip(route_coords, route_coords[1:]):
        if max(a[0], b[0]) < minx or min(a[0], b[0]) > maxx or \
           max(a[1], b[1]) < miny or min(a[1], b[1]) > maxy:
            continue
        if any(_segments_intersect(a, b, c, d) for c, d in edges):
            return True
    return False


def analyze(home, destination, fresh=False, recent_years=5):
    """(위도,경도) 두 점의 자차 경로와 공식 폴리곤을 분석한다."""
    if not home or not destination:
        raise TrafficError("집 또는 유치원 좌표가 없습니다")
    try:
        import route
    except ImportError as e:
        raise TrafficError(f"route.py를 불러오지 못했습니다: {e}")
    rr = route.driving_route(home, destination, geometry=True)
    if not rr or not rr.get("geometry"):
        raise TrafficError("OSRM에서 전체 자차 경로를 받지 못했습니다")
    end = [destination[1], destination[0]]
    all_rows = []
    for kind in SOURCES:
        all_rows.extend(load(kind, fresh=fresh, recent_years=recent_years))
    entrance, route_hits = [], []
    for item in all_rows:
        inside = any(_inside(end, poly) for poly in item["polygons"])
        crossed = any(_route_crosses(rr["geometry"], poly) for poly in item["polygons"])
        if inside:
            entrance.append(item)
        if crossed:
            route_hits.append(item)
    years = sorted({r["year"] for r in all_rows})
    return {"road_km": rr["km"], "minutes": rr["min"],
            "entrance_hits": entrance, "route_hits": route_hits,
            "years": years, "datasets": [SOURCES[k]["label"] for k in SOURCES],
            "interpretation": "0건은 안전 판정이 아니라 공식 사고다발지 선정 기준 비해당입니다."}


def refresh():
    out = {}
    for kind in SOURCES:
        path = CACHE / f"{kind}.csv"
        before = path.stat().st_mtime if path.exists() else None
        got = _download(kind, fresh=True)
        after = got.stat().st_mtime
        out[kind] = {"path": str(got), "updated": before is None or after > before}
    return out


def status():
    out = {}
    for kind, src in SOURCES.items():
        path = CACHE / f"{kind}.csv"
        meta = CACHE / f"{kind}.meta.json"
        out[kind] = {"label": src["label"], "cached": path.exists(),
                     "bytes": path.stat().st_size if path.exists() else 0,
                     "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") if path.exists() else None,
                     "meta": json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None}
    return out


def selftest(verbose=True):
    try:
        counts = {k: len(load(k)) for k in SOURCES}
        ok = all(counts.values())
    except TrafficError as e:
        ok = False
        if verbose:
            print(f"  ❌ {e}")
    if verbose and ok:
        print("  ✅ " + " · ".join(f"{SOURCES[k]['label']} {v}곳" for k, v in counts.items()))
        print("→ 정상: 공식 CSV와 폴리곤을 읽었습니다.")
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(0 if selftest() else 1)
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        print(json.dumps(refresh(), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
