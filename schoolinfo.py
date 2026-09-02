#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""학교알리미 Open API — 병설유치원의 모초등학교 보조정보.

이 모듈의 자료는 유치원 자체 공시가 아니라 모(母)초등학교 공시다. 병설유치원의
시설·급식 환경을 이해하는 보조자료로만 사용하고 사립·단설에는 연결하지 않는다.
2026년 이후 신규 키는 시도·시군구 코드가 필수이며, 공시 항목은 pbanYr도 전달한다.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache" / "schoolinfo"
BASE = "https://www.schoolinfo.go.kr/openApi.do"
TTL_DAYS = 30
TIMEOUT = 25
TYPES = {
    "basic": "0",
    "building": "17",
    "support": "18",
    "meal": "34",
    "health": "38",
    "safety": "44",
}


class SchoolInfoError(Exception):
    pass


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _cache_file(kind, sido, sgg, year):
    return CACHE / f"{kind}_{sido}_{sgg}_{year or 'all'}.json"


def fetch(key, kind, sido, sgg, year=None, fresh=False):
    if not key:
        raise SchoolInfoError("SCHOOLINFO_API_KEY가 없습니다")
    if kind not in TYPES:
        raise SchoolInfoError(f"지원하지 않는 학교알리미 항목: {kind}")
    cache = _cache_file(kind, sido, sgg, year)
    if not fresh and cache.exists():
        young = datetime.now().timestamp() - cache.stat().st_mtime < timedelta(days=TTL_DAYS).total_seconds()
        if young:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
    params = {
        "apiKey": key,
        "apiType": TYPES[kind],
        "sidoCode": str(sido),
        "sggCode": str(sgg),
        "schulKndCode": "02",
    }
    if kind != "basic":
        params["pbanYr"] = str(year or datetime.now().year)
    req = urllib.request.Request(
        BASE + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "kaic-kinder-info/1.10"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise SchoolInfoError(f"학교알리미 조회 실패: {e}")
    if payload.get("resultCode") != "success":
        raise SchoolInfoError(payload.get("resultMsg") or "학교알리미가 실패를 반환했습니다")
    rows = payload.get("list") or []
    _atomic_json(cache, rows)
    return rows


def mother_name(kindergarten_name):
    name = re.sub(r"\s+", "", str(kindergarten_name or ""))
    if "병설" not in name:
        return None
    return re.sub(r"병설유치원$", "", name)


def find_mother(kindergarten, key, year=None, fresh=False):
    """유치원 행의 _sido/_sgg와 이름으로 모초등학교를 찾는다."""
    target = mother_name(kindergarten.get("kindername"))
    if not target:
        return None
    sido, sgg = kindergarten.get("_sido"), kindergarten.get("_sgg")
    rows = fetch(key, "basic", sido, sgg, fresh=fresh)
    exact = [r for r in rows if re.sub(r"\s+", "", r.get("SCHUL_NM", "")) == target]
    if len(exact) != 1:
        return None
    return exact[0]


def context(kindergarten, key, year=None, fresh=False):
    year = year or datetime.now().year
    school = find_mother(kindergarten, key, year=year, fresh=fresh)
    if not school:
        return None
    code = school.get("SCHUL_CODE")
    sido, sgg = kindergarten.get("_sido"), kindergarten.get("_sgg")
    out = {"school": school, "year": year, "source": "학교알리미(모초등학교 공시)"}
    for kind in ("building", "support", "meal", "health", "safety"):
        rows = fetch(key, kind, sido, sgg, year=year, fresh=fresh)
        out[kind] = [r for r in rows if r.get("SCHUL_CODE") == code]
    return out


def clear_cache():
    n = 0
    if CACHE.exists():
        for p in CACHE.glob("*.json"):
            p.unlink()
            n += 1
    return n


def selftest(key, verbose=True):
    try:
        rows = fetch(key, "basic", "26", "26290", fresh=True)
        ok = bool(rows) and all("SCHUL_CODE" in r for r in rows)
    except SchoolInfoError as e:
        ok = False
        if verbose:
            print(f"  ❌ {e}")
    if verbose and ok:
        print(f"  ✅ 부산 남구 초등학교 기본정보 {len(rows)}곳")
        print("→ 정상: 학교알리미 신규 키의 시도·시군구 필수 호출이 동작합니다.")
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        # 독립 실행 때만 .env를 최소 파싱한다.
        vals = {}
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
        sys.exit(0 if selftest(vals.get("SCHOOLINFO_API_KEY")) else 1)
    print(__doc__)
