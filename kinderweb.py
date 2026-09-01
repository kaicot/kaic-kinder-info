#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""유치원알리미 '웹 화면' 조회 — Open API 에 없는 항목을 공시 페이지에서 읽는다.

Open API(14개 항목)에는 **혼합반 세부 연령**, **원비**, **시정명령 이력**이 없다. 같은 내용이
유치원알리미 웹 상세 페이지에는 공시된다(공공누리 제1유형, robots.txt 전면 허용).
이 모듈은 그 페이지를 읽는다. **인증키가 필요 없다.**

공식 API 가 아니므로 화면 개편 시 깨질 수 있다. 그래서 이렇게 방어한다.

  1. 위치(몇 번째 표·열)가 아니라 **이름**(제목·머리글·행 라벨)으로 찾는다.
     이름은 법정 공시 항목이라 잘 바뀌지 않는다.
  2. 읽은 뒤 형태를 검산하고, 어긋나면 값 대신 ParseChanged 를 던진다.
     **조용히 틀린 값을 주지 않는 것이 이 모듈의 첫 번째 규칙이다.**
  3. 결과에 항상 원본 페이지 주소를 담는다. 의심되면 사람이 원본을 본다.
  4. `python kinderweb.py selftest` 로 구조가 그대로인지 언제든 확인한다.

파이썬 표준 라이브러리만 사용한다.
"""
import json
import hashlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from datetime import datetime, timedelta
from pathlib import Path

BASE = "https://e-childschoolinfo.moe.go.kr"
ROOT = Path(__file__).resolve().parent
WEB_CACHE = ROOT / "cache" / "web"
PAGES = {
    "cost": ("kinderEducateAndCost", "교육과정 교육비용"),
    "violation": ("kinderViolation", "위반내용"),
    "operate": ("kinderOperate", "유치원 평가"),
    "classes": ("kinderChildAndStaff", "연령별 학급 현황"),
    "hours": ("kinderEducateAndCare", "교육과정 운영시간"),
}
TIMEOUT = 25
CACHE_DAYS = 7
REQUEST_INTERVAL = 0.3
AGE_HEADS = {3: "만 3세", 4: "만 4세", 5: "만 5세"}
_last_request = 0.0


class WebError(Exception):
    """페이지를 가져오지 못함(네트워크·차단 등)."""


class ParseChanged(WebError):
    """페이지는 열렸지만 기대한 표 구조가 아님 — 화면 개편 가능성."""


# ------------------------------------------------------------- HTML → 격자
class _Tables(HTMLParser):
    """h4 제목과 표를 문서 순서대로 수집한다.

    rowspan/colspan 을 펼쳐 2차원 텍스트 격자로 만든다. 덕분에 이후 파싱은
    '머리글에서 열 이름 찾기'와 '행 라벨 찾기'만으로 끝난다.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []      # [(직전 h4 제목, 격자), ...]
        self._heading = ""
        self._cap = None      # 텍스트 수집 모드: "h4" | "cell"
        self._buf = []
        self._grid = None
        self._carry = None    # rowspan 이월: {열: [남은 행수, 텍스트]}
        self._row = None
        self._span = (1, 1)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h4":
            self._cap, self._buf = "h4", []
        elif tag == "table":
            self._grid, self._carry = [], {}
        elif tag == "tr" and self._grid is not None:
            self._row = {}
            for col in list(self._carry):
                left, text = self._carry[col]
                self._row[col] = text
                if left <= 1:
                    del self._carry[col]
                else:
                    self._carry[col][0] = left - 1
        elif tag in ("td", "th") and self._row is not None:
            def _int(v):
                try:
                    return max(1, int(v))
                except (TypeError, ValueError):
                    return 1
            self._span = (_int(a.get("rowspan")), _int(a.get("colspan")))
            self._cap, self._buf = "cell", []

    def handle_endtag(self, tag):
        if tag == "h4" and self._cap == "h4":
            self._heading = " ".join("".join(self._buf).split())
            self._cap = None
        elif tag in ("td", "th") and self._cap == "cell":
            text = " ".join("".join(self._buf).split())
            rowspan, colspan = self._span
            col = 0
            while col in self._row:
                col += 1
            for c in range(col, col + colspan):
                self._row[c] = text
                if rowspan > 1:
                    self._carry[c] = [rowspan - 1, text]
            self._cap = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                width = max(self._row) + 1
                self._grid.append([self._row.get(i, "") for i in range(width)])
            self._row = None
        elif tag == "table" and self._grid is not None:
            self.blocks.append((self._heading, self._grid))
            self._grid = None

    def handle_data(self, data):
        if self._cap:
            self._buf.append(data)


def tables_of(html):
    p = _Tables()
    p.feed(html)
    return p.blocks


def _find_grid(blocks, title_kw):
    for heading, grid in blocks:
        if title_kw in heading:
            return grid
    return None


def _num(text):
    m = re.search(r"-?[\d,]+", str(text))
    return int(m.group().replace(",", "")) if m else None


def _basis(html):
    m = re.search(r"자료기준일\s*:?\s*([^<\n]{2,30})", html)
    return m.group(1).strip() if m else None


# ------------------------------------------------------------------- fetch
def page_url(page, itt_id):
    """사람이 브라우저로 열 수 있는 원본 주소."""
    return f"{BASE}/kinderMt/{PAGES[page][0]}.do?ittId={urllib.parse.quote(str(itt_id))}"


def _cache_file(page, itt_id):
    digest = hashlib.sha256(str(itt_id).encode("utf-8")).hexdigest()[:20]
    return WEB_CACHE / f"{page}_{digest}.html"


def clear_cache():
    """웹 화면 캐시만 비운다. 과거 벌크·자차 경로 캐시는 건드리지 않는다."""
    n = 0
    if WEB_CACHE.exists():
        for path in WEB_CACHE.glob("*.html"):
            path.unlink()
            n += 1
    return n


def fetch(page, itt_id, fresh=False):
    path, marker = PAGES[page]
    cache = _cache_file(page, itt_id)
    if not fresh and cache.exists():
        young = (datetime.now().timestamp() - cache.stat().st_mtime
                 < timedelta(days=CACHE_DAYS).total_seconds())
        if young:
            html = cache.read_text(encoding="utf-8")
            if marker in html:
                return html

    global _last_request
    wait = REQUEST_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        page_url(page, itt_id),
        headers={"User-Agent": "Mozilla/5.0 (kaic-kinder-info)",
                 "Referer": BASE + "/kinderMt/combineFind.do"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            html = r.read().decode("utf-8", "replace")
        _last_request = time.monotonic()
    except (urllib.error.URLError, TimeoutError) as e:
        raise WebError(f"유치원알리미 접속 실패: {e}")
    if marker not in html:
        raise WebError("페이지를 열었지만 기대한 내용이 없습니다 "
                       "(유치원 코드가 다르거나 화면이 바뀐 듯합니다)")
    WEB_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding="utf-8")
    return html


# -------------------------------------------------------------------- 원비
def _cost_table(grid, title):
    """원비 표 한 개 → {행 라벨: {"금액": {3:…,4:…,5:…}, "결제주기": str}}"""
    if not grid or len(grid) < 2:
        raise ParseChanged(f"'{title}' 표가 비어 있습니다")
    header = grid[0]
    cols = {}
    for age, head in AGE_HEADS.items():
        if head not in header:
            raise ParseChanged(f"'{title}' 머리글에서 '{head}' 열을 찾지 못했습니다")
        cols[age] = header.index(head)
    label_col = min(cols.values()) - 1
    cycle_col = header.index("결제주기") if "결제주기" in header else None
    if label_col < 0:
        raise ParseChanged(f"'{title}' 표의 항목명 열을 찾지 못했습니다")

    rows = {}
    for r in grid[1:]:
        if len(r) <= max(cols.values()):
            continue
        label = r[label_col].strip()
        if not label:
            continue
        rows[label] = {
            "금액": {age: _num(r[c]) for age, c in cols.items()},
            "결제주기": r[cycle_col].strip()
            if cycle_col is not None and cycle_col < len(r) else "",
        }
    total = next((v for k, v in rows.items() if k.startswith("합계")), None)
    if total is None:
        raise ParseChanged(f"'{title}' 표에서 합계 행을 찾지 못했습니다")
    if all(v is None for v in total["금액"].values()):
        raise ParseChanged(f"'{title}' 합계가 숫자로 읽히지 않습니다")
    return rows


def _special_programs(grid):
    """특성화 활동비 표 → 프로그램 목록. 구조가 어긋나면 None(부가 정보라 생략)."""
    if not grid or len(grid) < 2:
        return None
    header = grid[0]

    def col(kw):
        return next((i for i, h in enumerate(header) if kw in h), None)

    c_age, c_area = col("구분"), col("활동영역")
    c_name, c_comp, c_fee = col("프로그램명"), col("업체명"), col("월 학부모")
    if None in (c_age, c_name, c_fee):
        return None
    out = []
    for r in grid[1:]:
        if len(r) <= max(c_name, c_fee) or not r[c_name].strip():
            continue
        out.append({
            "연령": r[c_age].strip() if c_age < len(r) else "",
            "영역": r[c_area].strip() if c_area is not None and c_area < len(r) else "",
            "프로그램": r[c_name].strip(),
            "업체": r[c_comp].strip() if c_comp is not None and c_comp < len(r) else "",
            "월부담금": _num(r[c_fee]),
        })
    return out


def parse_costs(html):
    blocks = tables_of(html)
    regular = _find_grid(blocks, "교육과정 교육비용")
    after = _find_grid(blocks, "방과후 과정 교육비용")
    if regular is None or after is None:
        raise ParseChanged("원비 표(교육과정/방과후)를 찾지 못했습니다")
    return {
        "교육과정": _cost_table(regular, "교육과정 교육비용"),
        "방과후": _cost_table(after, "방과후 과정 교육비용"),
        "특성화": _special_programs(_find_grid(blocks, "특성화 활동비")),
        "기준": _basis(html),
    }


def get_costs(itt_id, fresh=False):
    out = parse_costs(fetch("cost", itt_id, fresh=fresh))
    out["url"] = page_url("cost", itt_id)
    return out


# -------------------------------------------------------------- 시정명령
def parse_violations(html):
    blocks = tables_of(html)
    grid = _find_grid(blocks, "위반내용")
    if grid is None:
        raise ParseChanged("'위반내용' 표를 찾지 못했습니다")
    head = " ".join(" ".join(r) for r in grid[:2])
    for kw in ("위반내용", "조치결과"):
        if kw not in head:
            raise ParseChanged(f"위반내용 표 머리글에 '{kw}'가 없습니다")
    if any("해당사항이 없습니다" in " ".join(r) for r in grid):
        return {"clean": True, "items": []}
    items = []
    for r in grid[2:]:
        if len(r) >= 5 and r[0].strip().rstrip(".").isdigit():
            items.append({"제목": r[1], "위반내용": r[2],
                          "조치결과": r[3], "기관": r[4]})
    if not items:
        # '없음' 표시도, 해석 가능한 행도 없으면 판단 불가 → 값을 지어내지 않는다
        raise ParseChanged("위반내용 표를 해석하지 못했습니다")
    return {"clean": False, "items": items}


def get_violations(itt_id, fresh=False):
    html = fetch("violation", itt_id, fresh=fresh)
    out = parse_violations(html)
    out["기준"] = _basis(html)
    out["url"] = page_url("violation", itt_id)
    return out


# ------------------------------------------------------------ 유치원 평가
def parse_evaluation(html):
    """유치원 평가 실시 이력과 평가결과 PDF 목록.

    평가 '내용'은 PDF 첨부라 구조화되지 않는다 — 실시 여부와 문서 존재만 알려주고,
    내용은 원본 링크에서 사람이 본다.
    """
    blocks = tables_of(html)
    ev = _find_grid(blocks, "유치원 평가")
    if ev is None or not ev or "평가 실시 여부" not in " ".join(ev[0]):
        raise ParseChanged("유치원 평가 표를 찾지 못했습니다")
    years = []
    for r in ev[1:]:
        if len(r) >= 3 and r[0].strip():
            years.append({"학년도": r[0].strip(), "실시": r[1].strip(),
                          "연월": r[2].strip()})
    pdfs = []
    for heading, grid in blocks:
        if "평가소견" in heading and grid and "파일명" in " ".join(grid[0]):
            for r in grid[1:]:
                if len(r) >= 2 and ".pdf" in r[1].lower():
                    pdfs.append(re.sub(r"\s*미리보기\s*$", "", r[1]).strip())
    return {"실시": years, "보고서": pdfs}


def get_evaluation(itt_id, fresh=False):
    out = parse_evaluation(fetch("operate", itt_id, fresh=fresh))
    out["url"] = page_url("operate", itt_id)
    return out


# --------------------------------------------------------- 연령별 학급 구성
CLASS_COLUMNS = {
    "만3세": ("만3세반",),
    "만4세": ("만4세반",),
    "만5세": ("만5세반",),
    "3-4세": ("만3~4세", "만3-4세"),
    "4-5세": ("만4~5세", "만4-5세"),
    "3-5세": ("만3~5세", "만3-5세"),
    "특수": ("특수학급",),
}


def _compact(text):
    return re.sub(r"\s+", "", str(text or "")).replace("∼", "~")


def parse_age_classes(html):
    """웹 공시의 전용·혼합 연령별 학급/정원/현원을 구조화한다.

    같은 제목 아래 과거 시계열 표도 있으므로, '학급 수·정원·현원' 행과
    '만 3~4세' 열을 함께 가진 현재 학급표만 선택한다.
    """
    candidates = []
    for heading, grid in tables_of(html):
        if "연령별 학급 현황" not in heading or not grid:
            continue
        flat = " ".join(" ".join(r) for r in grid)
        if all(label in flat for label in ("학급 수", "정원", "현원")) and "만 3~4세" in flat:
            candidates.append(grid)
    if len(candidates) != 1:
        raise ParseChanged("현재 연령별 학급 현황 표를 하나로 식별하지 못했습니다")
    grid = candidates[0]

    data_start = next((i for i, r in enumerate(grid)
                       if any(_compact(c) == "학급수" for c in r)), None)
    if data_start is None or data_start < 1:
        raise ParseChanged("연령별 학급 표에서 '학급 수' 행을 찾지 못했습니다")
    header = [_compact(c) for c in grid[data_start - 1]]
    cols = {}
    for key, aliases in CLASS_COLUMNS.items():
        aliases = tuple(_compact(a) for a in aliases)
        col = next((i for i, h in enumerate(header) if h in aliases), None)
        if col is None:
            raise ParseChanged(f"연령별 학급 표에서 '{key}' 열을 찾지 못했습니다")
        cols[key] = col

    rows = {}
    for label in ("학급 수", "정원", "현원"):
        want = _compact(label)
        row = next((r for r in grid[data_start:]
                    if any(_compact(c) == want for c in r)), None)
        if row is None:
            raise ParseChanged(f"연령별 학급 표에서 '{label}' 행을 찾지 못했습니다")
        rows[label] = row

    classes = {}
    for key, col in cols.items():
        classes[key] = {
            "학급": _num(rows["학급 수"][col]) if col < len(rows["학급 수"]) else None,
            "정원": _num(rows["정원"][col]) if col < len(rows["정원"]) else None,
            "현원": _num(rows["현원"][col]) if col < len(rows["현원"]) else None,
        }
    if not any((v["학급"] or 0) > 0 for v in classes.values()):
        raise ParseChanged("연령별 학급 수가 하나도 숫자로 읽히지 않습니다")

    total_row = rows["정원"]
    current_row = rows["현원"]
    return {
        "학급": classes,
        "인가총정원": _num(total_row[0]) if total_row else None,
        "총현원": _num(current_row[1]) if len(current_row) > 1 else None,
        "기준": _basis(html),
    }


def get_age_classes(itt_id, fresh=False):
    out = parse_age_classes(fetch("classes", itt_id, fresh=fresh))
    out["url"] = page_url("classes", itt_id)
    return out


# ------------------------------------------------------------- 실제 운영시간
def _clock(text):
    """'09시 00분' 또는 '09:00'을 HH:MM으로 정규화한다."""
    m = re.search(r"(?<!\d)([0-2]?\d)\s*(?:시|:)\s*([0-5]?\d)?\s*분?", str(text))
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def _clock_range(cells, title):
    text = " ".join(str(c) for c in cells)
    clocks = []
    for m in re.finditer(r"(?<!\d)([0-2]?\d)\s*(?:시|:)\s*([0-5]?\d)?\s*분?", text):
        clock = _clock(m.group(0))
        if clock and clock not in clocks:
            clocks.append(clock)
    if len(clocks) < 2:
        raise ParseChanged(f"'{title}'에서 시작·종료시간을 읽지 못했습니다")
    if clocks[0] >= clocks[1]:
        raise ParseChanged(f"'{title}'의 시작·종료시간 순서가 올바르지 않습니다")
    return {"시작": clocks[0], "종료": clocks[1]}


def parse_hours(html):
    """정규 교육과정과 공시상 방과후·돌봄 전체 범위를 구조화한다.

    방과후 표의 07~21시 같은 값은 일반 방과후만의 시간이 아니라 아침·저녁돌봄까지
    합친 외곽 범위일 수 있다. 따라서 이름을 '공시전체범위'로 고정해 과장하지 않는다.
    """
    blocks = tables_of(html)
    education = _find_grid(blocks, "교육과정 운영시간")
    if education is None:
        raise ParseChanged("'교육과정 운영시간' 표를 찾지 못했습니다")
    edu_row = next((r for r in education
                    if "교육과정 운영시간" in " ".join(r)), None)
    if edu_row is None:
        raise ParseChanged("교육과정 운영시간 행을 찾지 못했습니다")

    outer = None
    for heading, grid in blocks:
        flat = " ".join(" ".join(r) for r in grid)
        if ("방과후 과정 편성" in heading
                and "시작시간" in flat and "종료시간" in flat):
            outer = grid
            break
    if outer is None or len(outer) < 2:
        raise ParseChanged("방과후 과정 시작·종료시간 표를 찾지 못했습니다")

    plans = []
    plan_grid = _find_grid(blocks, "연간 교육과정 편성 계획안")
    if plan_grid and plan_grid[0]:
        header = plan_grid[0]
        file_col = next((i for i, h in enumerate(header) if "파일명" in h), None)
        date_col = next((i for i, h in enumerate(header) if "등록일" in h), None)
        if file_col is not None:
            for row in plan_grid[1:]:
                if file_col >= len(row) or not row[file_col].strip():
                    continue
                item = {"파일명": re.sub(r"\s*미리보기\s*$", "", row[file_col]).strip()}
                if date_col is not None and date_col < len(row):
                    item["등록일"] = row[date_col].strip()
                plans.append(item)

    return {
        "교육과정": _clock_range(edu_row, "교육과정 운영시간"),
        "공시전체범위": _clock_range(outer[1], "방과후 과정 운영시간"),
        "계획서": plans,
        "기준": _basis(html),
    }


def get_hours(itt_id, fresh=False):
    out = parse_hours(fetch("hours", itt_id, fresh=fresh))
    out["url"] = page_url("hours", itt_id)
    return out


# ---------------------------------------------------------------- selftest
# 자가진단 표본: 서울 강남구의 실제 유치원 1곳(공개 공시 데이터).
SELFTEST_ID = "34140010-58e8-44b4-9e91-49d5eb6669e1"   # 강남유정유치원


def selftest(itt_id=SELFTEST_ID, verbose=True):
    """공시 화면 구조가 그대로인지 확인. 전부 통과하면 True."""
    checks = [
        ("원비 표 파싱", lambda: parse_costs(fetch("cost", itt_id, fresh=True))),
        ("시정명령 표 파싱", lambda: parse_violations(fetch("violation", itt_id, fresh=True))),
        ("유치원 평가 표 파싱", lambda: parse_evaluation(fetch("operate", itt_id, fresh=True))),
        ("연령별 학급 표 파싱", lambda: parse_age_classes(fetch("classes", itt_id, fresh=True))),
        ("운영시간 표 파싱", lambda: parse_hours(fetch("hours", itt_id, fresh=True))),
    ]
    ok = True
    for name, fn in checks:
        try:
            fn()
            if verbose:
                print(f"  ✅ {name}")
        except WebError as e:
            ok = False
            if verbose:
                print(f"  ❌ {name}: {e}")
        time.sleep(0.3)
    if verbose:
        print("→ 정상: 공시 화면 구조가 그대로입니다." if ok else
              "→ 실패: 유치원알리미 화면이 바뀐 것 같습니다. 학급 구성·원비·시정명령은 "
              "원본 링크로 직접 확인하세요.")
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(0 if selftest() else 1)
    print(__doc__)
    print("사용법: python kinderweb.py selftest")
