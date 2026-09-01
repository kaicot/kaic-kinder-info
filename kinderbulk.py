#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""과거 공시 차수 일괄 데이터 — 추이(trend)와 차수 간 비교(diff)의 데이터원.

유치원알리미의 '공시자료 다운로드'(/download/getOpenData.do)를 스크립트로 호출한다.
Open API 와 달리 **과거 차수(2013년~)**를 시도 단위로 통째로 준다. 인증키가 필요 없다.
사이트 자체의 시계열 화면(kinderSeriesInfo)은 서비스 중단 상태라, 추이는 이 경로가 유일하다.

설계 원칙 (kinderweb 과 동일한 4겹 방어):
  * 열을 위치가 아니라 **헤더 이름**으로 찾는다. 필요한 열이 없으면 BulkError 로
    명시적으로 실패한다 — 조용히 틀린 값을 주지 않는다.
  * **지난 차수는 다시 바뀌지 않으므로 영구 캐시**(cache/bulk/), 올해 차수만 7일.
    kinderinfo 의 refresh 는 최상위 캐시만 지우므로 영구 캐시는 살아남는다.
  * 차수 간 유치원 매칭은 코드가 없어 **정규화한 이름 + 주소(시군구)**로 한다.
    폐원·신설로 비는 차수는 None 으로 정직하게 남긴다.
  * `python kinderbulk.py selftest` 로 다운로드·헤더 구조를 상시 점검한다.

파이썬 표준 라이브러리만 사용한다.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BULK_CACHE = ROOT / "cache" / "bulk"
URL = "https://e-childschoolinfo.moe.go.kr/download/getOpenData.do"
TIMEOUT = 120

ITEMS = {"05": "일반 현황", "07": "교사 근속연수"}

SIDO_FULL = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시",
    "29": "광주광역시", "30": "대전광역시", "31": "울산광역시", "36": "세종특별자치시",
    "41": "경기도", "51": "강원특별자치도", "43": "충청북도", "44": "충청남도",
    "52": "전북특별자치도", "46": "전라남도", "47": "경상북도", "48": "경상남도",
    "50": "제주특별자치도",
}

# 필요한 열 — 이름이 바뀌면 BulkError 로 알게 된다
NEED_05 = ["유치원명", "주소", "인가총정원수",
           "만3세원아수", "만4세원아수", "만5세원아수", "혼합원아수", "특수원아수"]
NEED_07 = ["유치원명", "주소", "1년미만교사수", "1년이상2년미만교사수",
           "2년이상4년미만교사수", "4년이상6년미만교사수", "6년이상교사수"]


class BulkError(Exception):
    pass


def _num(v):
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", str(v or "").replace(",", ""))
    return int(m.group()) if m else None


def _norm(s):
    return re.sub(r"\s+", "", str(s or ""))


# ------------------------------------------------------------------- 차수
def latest_timing(today=None):
    """현재 받을 수 있는 최신 차수. 1차는 4월말, 2차는 10월말경 게시된다."""
    t = today or date.today()
    if t.month >= 11:
        return f"{t.year}2"
    if t.month >= 5:
        return f"{t.year}1"
    return f"{t.year - 1}2"


def recent_timings(n=5, today=None):
    """최신 차수부터 거꾸로 n개. 예: ['20241','20242','20251','20252','20261']"""
    cur = latest_timing(today)
    year, half = int(cur[:4]), int(cur[4])
    out = []
    for _ in range(n):
        out.append(f"{year}{half}")
        half -= 1
        if half == 0:
            year, half = year - 1, 2
    return list(reversed(out))


def timing_label(t):
    return f"{t[:4]}-{t[4]}차"


# ------------------------------------------------------------------ fetch
def fetch_bulk(sido_code, timing, item="05"):
    """시도 × 차수 × 항목 한 덩어리. {'header': [...], 'body': [[...], ...]}"""
    sido_code = str(sido_code)
    sido_name = SIDO_FULL.get(sido_code)
    if not sido_name:
        raise BulkError(f"모르는 시도 코드: {sido_code}")
    BULK_CACHE.mkdir(parents=True, exist_ok=True)
    cache = BULK_CACHE / f"bulk_{sido_code}_{timing}_{item}.json"
    if cache.exists():
        permanent = int(timing[:4]) < datetime.now().year
        fresh_enough = (datetime.now().timestamp() - cache.stat().st_mtime
                        < timedelta(days=7).total_seconds())
        if permanent or fresh_enough:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                pass  # 파손 시 재요청

    q = urllib.parse.urlencode({
        "combineSidoCode": sido_code, "combineSidoName": sido_name,
        "timingListCode": timing, "gongsiListCode": item, "ExcelCsv": "3"})
    req = urllib.request.Request(
        f"{URL}?{q}",
        headers={"User-Agent": "Mozilla/5.0 (kaic-kinder-info)",
                 "Referer": "https://e-childschoolinfo.moe.go.kr/openData.do"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8-sig", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise BulkError(f"다운로드 실패({timing_label(timing)}): {e}")
    try:
        data = json.loads(raw)
        header, body = data["header"], data["body"]
    except (ValueError, KeyError, TypeError):
        raise BulkError(f"다운로드 응답이 기대한 JSON 이 아닙니다"
                        f"({timing_label(timing)}) — 화면이 바뀌었을 수 있습니다")
    need = NEED_05 if item == "05" else NEED_07
    missing = [c for c in need if c not in header]
    if missing:
        raise BulkError(f"{ITEMS[item]} 헤더에서 열을 찾지 못했습니다: {missing}")
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _find_row(data, name, addr_hint):
    """이름(+주소 힌트)으로 행 찾기. 없으면 None, 모호하면 첫 일치."""
    header, body = data["header"], data["body"]
    i_name, i_addr = header.index("유치원명"), header.index("주소")
    want, hint = _norm(name), _norm(addr_hint)
    hits = [r for r in body if _norm(r[i_name]) == want]
    if len(hits) > 1 and hint:
        narrowed = [r for r in hits if hint in _norm(r[i_addr])]
        hits = narrowed or hits
    return hits[0] if hits else None


# ------------------------------------------------------------------ series
def series(sido_code, name, addr, timings=None, delay=0.3):
    """유치원 1곳의 차수별 지표 시리즈.

    반환: [{"timing","label","정원","원아","충원율","원아3","원아4","원아5",
            "교사","근속1년미만"} | 값 없으면 None 필드] — 차수 오름차순.
    addr 의 앞 두 토큰(시도+시군구)을 동명이원 구분 힌트로 쓴다.
    """
    timings = timings or recent_timings()
    hint = " ".join(str(addr or "").split()[:2])
    out = []
    for t in timings:
        row = {"timing": t, "label": timing_label(t)}
        try:
            g = fetch_bulk(sido_code, t, "05")
            r = _find_row(g, name, hint)
            if r:
                h = g["header"]
                pupils = [_num(r[h.index(c)]) or 0 for c in
                          ("만3세원아수", "만4세원아수", "만5세원아수",
                           "혼합원아수", "특수원아수")]
                cap = _num(r[h.index("인가총정원수")])
                row["정원"] = cap
                row["원아"] = sum(pupils)
                row["충원율"] = round(100 * sum(pupils) / cap) if cap else None
                for age, col in ((3, "만3세원아수"), (4, "만4세원아수"),
                                 (5, "만5세원아수")):
                    row[f"원아{age}"] = _num(r[h.index(col)])
        except BulkError as e:
            row["오류"] = str(e)
        try:
            g = fetch_bulk(sido_code, t, "07")
            r = _find_row(g, name, hint)
            if r:
                h = g["header"]
                counts = [_num(r[h.index(c)]) or 0 for c in NEED_07[2:]]
                total = sum(counts)
                row["교사"] = total or None
                row["근속1년미만"] = (round(100 * counts[0] / total)
                                  if total else None)
        except BulkError:
            pass  # 근속만 실패하면 근속 열만 빈다
        out.append(row)
        time.sleep(delay)
    return out


def direction(first, last, unit="%p", threshold=2):
    """추세 화살표. 판단은 절제하고 방향만 표시한다."""
    if first is None or last is None:
        return ""
    d = last - first
    if d >= threshold:
        return f"↗ ({first}→{last}, +{d}{unit})"
    if d <= -threshold:
        return f"↘ ({first}→{last}, {d}{unit})"
    return f"→ ({first}→{last})"


# ---------------------------------------------------------------- selftest
def selftest(sido_code="11", verbose=True):
    """다운로드 경로와 헤더 구조가 그대로인지 확인."""
    ok = True
    t = recent_timings(1)[0]
    for item in ("05", "07"):
        try:
            d = fetch_bulk(sido_code, t, item)
            if verbose:
                print(f"  ✅ {ITEMS[item]}({timing_label(t)}): "
                      f"{len(d['body'])}행, 열 {len(d['header'])}개")
        except BulkError as e:
            ok = False
            if verbose:
                print(f"  ❌ {ITEMS[item]}: {e}")
    if verbose:
        print("→ 정상: 일괄 다운로드 구조가 그대로입니다." if ok else
              "→ 실패: 다운로드 화면이 바뀐 것 같습니다. trend/diff 대신 "
              "유치원알리미의 공시자료 다운로드에서 직접 받아 보세요.")
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(0 if selftest() else 1)
    print(__doc__)
    print("사용법: python kinderbulk.py selftest")
