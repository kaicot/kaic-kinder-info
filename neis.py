#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NEIS 교육정보 개방 포털 연동 — 병설유치원의 방학 일정과 급식 식단.

유치원알리미에는 학사일정이 없다. 그런데 **초등학교 병설유치원**은 모(母)초등학교에
붙어 있고, 초등학교 학사일정은 NEIS(open.neis.go.kr)가 공개한다.
유치원명에서 '병설유치원'을 떼면 초등학교명이 되므로 이걸로 연결한다.

한계 — 반드시 함께 안내할 것:
  * 초등학교 학사일정 ≠ 유치원 학사일정. 병설 정규 수업일수(180일)와
    초등 법정 수업일수(190일)가 달라 **강력한 근사치**일 뿐이다.
  * 사립·단설 유치원은 NEIS에 없어 이 방법이 통하지 않는다.

인증키(무료): https://open.neis.go.kr → .env 의 NEIS_API_KEY
파이썬 표준 라이브러리만 사용한다.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

HUB = "https://open.neis.go.kr/hub/{svc}"
TIMEOUT = 25

SIDO_FULL = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시",
    "29": "광주광역시", "30": "대전광역시", "31": "울산광역시", "36": "세종특별자치시",
    "41": "경기도", "51": "강원특별자치도", "43": "충청북도", "44": "충청남도",
    "52": "전북특별자치도", "46": "전라남도", "47": "경상북도", "48": "경상남도",
    "50": "제주특별자치도",
}


class NeisError(Exception):
    pass


class NeisKeyMissing(NeisError):
    pass


def _norm(s):
    """주소·학교명 비교용 정규화. '동명로176번길' 과 '동명로 176번길' 을 같게 본다."""
    return re.sub(r"\s+", "", str(s or ""))


def _call(svc, key, **params):
    """NEIS 허브 호출. 결과 행 리스트를 반환하고, 데이터가 없으면 빈 리스트."""
    if not key:
        raise NeisKeyMissing(
            "NEIS 인증키가 없습니다. https://open.neis.go.kr 에서 무료로 발급받아 "
            ".env 에 NEIS_API_KEY=발급받은키 로 넣어주세요.")
    q = {"KEY": key, "Type": "json", "pIndex": 1, "pSize": 1000}
    q.update({k: v for k, v in params.items() if v not in (None, "")})
    url = HUB.format(svc=svc) + "?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise NeisError(f"NEIS 호출 실패({svc}): {e}")

    if "RESULT" in data:                      # 데이터 없음 또는 오류
        code = data["RESULT"].get("CODE", "")
        msg = data["RESULT"].get("MESSAGE", "")
        if code == "INFO-200":                # 해당하는 데이터가 없습니다
            return []
        raise NeisError(f"NEIS 오류({svc}): {msg}")
    body = data.get(svc)
    if not body or len(body) < 2:
        return []
    return body[1].get("row", [])


# ------------------------------------------------------------------ 학교 매칭
def school_name_of(kindername):
    """'연포초등학교병설유치원' → '연포초등학교'. 병설이 아니면 None."""
    name = str(kindername or "")
    if "병설" not in name:
        return None
    return re.sub(r"병설유치원.*$", "", name).strip() or None


def find_school(key, kindername, sido_code, addr=None):
    """병설유치원에 대응하는 초등학교를 찾는다. 못 찾으면 None."""
    school = school_name_of(kindername)
    if not school:
        return None
    rows = _call("schoolInfo", key, SCHUL_NM=school,
                 LCTN_SC_NM=SIDO_FULL.get(str(sido_code)))
    if not rows:
        return None
    if addr:   # 주소 앞부분(시도+시군구+도로명)으로 동명이교를 가려낸다
        want = _norm(" ".join(str(addr).split()[:3]))
        for r in rows:
            if want and want in _norm(r.get("ORG_RDNMA")):
                return r
    return rows[0]


# ------------------------------------------------------------------ 학사일정
def school_year_range(year):
    """학년도 기간. 2026학년도 = 2026-03-01 ~ 2027-02-28."""
    return f"{year}0301", f"{year + 1}0228"


def current_school_year(today=None):
    t = today or date.today()
    return t.year if t.month >= 3 else t.year - 1


def fetch_schedule(key, office_code, school_code, from_ymd, to_ymd):
    return _call("SchoolSchedule", key,
                 ATPT_OFCDC_SC_CODE=office_code, SD_SCHUL_CODE=school_code,
                 AA_FROM_YMD=from_ymd, AA_TO_YMD=to_ymd)


def _to_date(ymd):
    s = str(ymd)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def vacation_periods(rows):
    """학사일정 행 → [(방학명, 시작 date, 종료 date, 일수), ...]

    '여름방학식' 같은 행사일(…식)은 제외하고, 같은 이름의 연속된 날짜를 한 구간으로 묶는다.
    """
    days = {}
    for r in rows:
        name = str(r.get("EVENT_NM") or "").strip()
        if "방학" not in name or name.endswith("식"):
            continue
        try:
            days.setdefault(name, set()).add(_to_date(r.get("AA_YMD")))
        except (ValueError, TypeError):
            continue

    periods = []
    for name, dates in days.items():
        run = []
        for d in sorted(dates):
            if run and d - run[-1] > timedelta(days=1):
                periods.append((name, run[0], run[-1], len(run)))
                run = []
            run.append(d)
        if run:
            periods.append((name, run[0], run[-1], len(run)))
    return sorted(periods, key=lambda p: p[1])


def key_events(rows):
    """입학식·개학식·방학식·종업식 등 눈에 띄는 하루짜리 행사."""
    out = []
    for r in rows:
        name = str(r.get("EVENT_NM") or "").strip()
        if name.endswith("식") and any(w in name for w in
                                       ("입학", "개학", "방학", "종업", "졸업")):
            try:
                out.append((_to_date(r.get("AA_YMD")), name))
            except (ValueError, TypeError):
                continue
    return sorted(set(out))


# ------------------------------------------------------------------ 급식 식단
def fetch_meals(key, office_code, school_code, from_ymd, to_ymd):
    rows = _call("mealServiceDietInfo", key,
                 ATPT_OFCDC_SC_CODE=office_code, SD_SCHUL_CODE=school_code,
                 MLSV_FROM_YMD=from_ymd, MLSV_TO_YMD=to_ymd)
    meals = []
    for r in rows:
        dish = re.sub(r"<br\s*/?>", " / ", str(r.get("DDISH_NM") or ""))
        dish = re.sub(r"\([0-9.*]+\)", "", dish)        # 알레르기 번호 제거
        meals.append({
            "date": r.get("MLSV_YMD"),
            "type": r.get("MMEAL_SC_NM"),
            "menu": re.sub(r"\s{2,}", " ", dish).strip(),
            "kcal": r.get("CAL_INFO"),
        })
    return meals
