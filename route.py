#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""자차 도로 경로 — 직선거리가 못 잡는 실제 이동 거리를 구한다.

부산처럼 지형이 험한 곳에서는 직선거리가 무의미하다. 실측하면 우회율(도로÷직선)이
1.0배에서 3.1배까지 벌어진다 — 직선거리가 똑같이 541m인 두 유치원이 실제로는
720m와 1.20km로 갈렸다. 그래서 도로 경로를 따로 구한다.

경로원: OSRM 공개 서버(router.project-osrm.org). **인증키가 필요 없다.**
  * 실시간 교통은 반영되지 않는다 — 아침 등원 시간대에는 더 걸린다.
  * 한국 도로망은 OpenStreetMap 기반이라 일방통행·좁은 골목의 최신성이
    네이버·카카오보다 떨어질 수 있다.
  두 한계는 출력에 항상 함께 표시한다. 실패하면 값을 지어내지 않고 None 을 준다.

공개 서버를 쓰므로 요청 간 간격을 강제하고, 좌표쌍이 같으면 영구 캐시를 쓴다
(집·유치원 위치는 안 바뀌므로 한 번 조회하면 끝이다).

파이썬 표준 라이브러리만 사용한다.
"""
import hashlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache" / "route"
OSRM = "https://router.project-osrm.org/route/v1/driving/{a};{b}"
MIN_INTERVAL = 1.0          # 공개 서버 예의: 초당 1회 이하
TIMEOUT = 25
SOURCE_NOTE = ("OSRM 오픈 데이터 기준 · **실시간 교통 미반영**(아침 등원 시간대에는 "
               "더 걸립니다) · 한국 도로망은 OpenStreetMap 기반이라 일방통행·골목의 "
               "최신성이 상용 지도보다 떨어질 수 있습니다.")

_last_call = 0.0


class RouteError(Exception):
    pass


def _throttle():
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _key(a, b):
    return hashlib.sha1(
        f"{a[0]:.6f},{a[1]:.6f}|{b[0]:.6f},{b[1]:.6f}".encode()).hexdigest()[:16]


def haversine_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def driving_route(a, b, use_cache=True, geometry=False):
    """자차 경로 원자료. geometry=True면 전체 GeoJSON 좌표도 받는다.

    좌표는 (위도, 경도). 결과는 영구 캐시된다 — 위치가 바뀌지 않기 때문이다.
    """
    if not a or not b:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    suffix = "_full" if geometry else ""
    f = CACHE / f"{_key(a, b)}{suffix}.json"
    if use_cache and f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass

    overview = "full&geometries=geojson" if geometry else "false"
    url = (OSRM.format(a=f"{a[1]},{a[0]}", b=f"{b[1]},{b[0]}")
           + f"?overview={overview}")
    req = urllib.request.Request(url, headers={"User-Agent": "kaic-kinder-info/1.7"})
    try:
        _throttle()
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None      # 지어내지 않는다
    if d.get("code") != "Ok" or not d.get("routes"):
        return None
    route = d["routes"][0]
    out = {"km": route["distance"] / 1000,
           "min": route["duration"] / 60}
    if geometry:
        out["geometry"] = (route.get("geometry") or {}).get("coordinates") or []
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    return out


def driving(a, b, use_cache=True):
    """자차 경로 (도로거리 km, 소요 분). 실패하면 (None, None)."""
    out = driving_route(a, b, use_cache=use_cache, geometry=False)
    return ((out.get("km"), out.get("min")) if out else (None, None))


def detour_ratio(km, straight_km):
    """우회율(도로÷직선). 2.0 이상이면 동선을 눈으로 확인할 값어치가 있다."""
    if km is None or not straight_km:
        return None
    return km / straight_km


def describe(km, minutes, straight_km=None):
    """'1.20km / 2.9분 (직선의 2.2배)' 형태 문구. 값이 없으면 None."""
    if km is None:
        return None
    minute_text = f"{minutes:.1f}분" if minutes < 1 else f"{minutes:.0f}분"
    out = f"{km:.2f}km / {minute_text}"
    r = detour_ratio(km, straight_km)
    if r:
        out += f" (직선의 {r:.1f}배{'  ⚠' if r >= 2.0 else ''})"
    return out


# ------------------------------------------------------- 지도 링크 → 좌표
def coords_from_map_link(link):
    """네이버지도 링크에서 (위도, 경도) 추출. 실패 시 RouteError.

    카카오 링크는 위경도가 아니라 자체 좌표계(urlX/urlY)를 주므로 변환이
    부정확하다 — 받지 않고 네이버 링크를 안내한다.
    """
    link = str(link or "").strip()
    if not link.startswith("http"):
        raise RouteError("링크가 아닙니다. 네이버지도 공유 링크를 붙여넣어 주세요.")
    final = link
    if "naver.me" in link or "://" in link:
        try:
            req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                final = r.geturl()
        except (urllib.error.URLError, TimeoutError) as e:
            raise RouteError(f"링크를 여는 데 실패했습니다: {e}")

    q = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
    lat = q.get("lat", [None])[0]
    lng = q.get("lng", [None])[0] or q.get("lon", [None])[0]
    if lat and lng:
        try:
            return float(lat), float(lng)
        except ValueError:
            pass
    m = re.search(r"[?&/](?:c=)?(1[2-3]\d\.\d+),(3[3-9]\.\d+)", final)
    if m:
        return float(m.group(2)), float(m.group(1))
    if "kakao" in final or "kko.to" in final:
        raise RouteError(
            "카카오맵 링크는 위경도가 아니라 자체 좌표계(urlX/urlY)를 담고 있어 "
            "정확한 변환이 어렵습니다.\n"
            "  네이버지도에서 같은 위치를 열고 [공유] 링크를 주세요 — "
            "그 링크에는 위경도가 그대로 들어 있습니다.")
    raise RouteError("링크에서 위경도를 찾지 못했습니다. 네이버지도 공유 링크인지 "
                     "확인해 주세요.")


# ---------------------------------------------------------------- selftest
def selftest(verbose=True):
    """경로 조회와 캐시가 동작하는지 확인(서울 시내 두 지점)."""
    a, b = (37.5665, 126.9780), (37.5512, 126.9882)   # 서울시청 ↔ 남산
    ok = True
    km, mn = driving(a, b, use_cache=False)
    if km is None:
        ok = False
        if verbose:
            print("  ❌ 경로 조회 실패 — OSRM 공개 서버에 닿지 못했습니다")
    elif verbose:
        print(f"  ✅ 경로 조회: {km:.2f}km / {mn:.0f}분")
    if ok:
        t0 = time.monotonic()
        driving(a, b)
        if verbose:
            print(f"  ✅ 캐시 재사용: {(time.monotonic() - t0) * 1000:.0f}ms")
    if verbose:
        print("→ 정상: 자차 경로 조회가 동작합니다." if ok else
              "→ 실패: 경로 조회를 쓸 수 없습니다. 직선거리만 표시됩니다.")
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(0 if selftest() else 1)
    if len(sys.argv) > 2 and sys.argv[1] == "link":
        try:
            print("{},{}".format(*coords_from_map_link(sys.argv[2])))
        except RouteError as e:
            sys.exit(f"[오류] {e}")
    else:
        print(__doc__)
        print("사용법: python route.py selftest | python route.py link <네이버지도 링크>")
