#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""유치원알리미(e-childschoolinfo.moe.go.kr) Open API CLI.

학부모가 유치원을 검색·비교·심층조회할 수 있게 14개 공시 항목 API를 감싼 도구.
표준 라이브러리만 사용. 사용법은 README.md / AGENTS.md 참고.

주요 명령:
  regions   저장된 시도/시군구 코드 목록
  discover  시군구 코드 자동 탐색(새 지역 최초 1회)
  search    지역별 유치원 검색 (--age 3|4|5, --target 등)
  profile   유치원 1곳의 전체 공시 항목 종합 리포트
  compare   여러 유치원 핵심 지표 비교표
  hours     실제 운영시간(정규·방과후·돌봄) 출처별 비교
  raw       엔드포인트 원본 JSON 덤프
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

__version__ = "1.10.0"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
CONFIG_FILE = ROOT / "config.json"   # 구버전 호환용(현재는 .env 권장)
SGG_FILE = ROOT / "sgg_codes.json"
CACHE_DIR = ROOT / "cache"

BASE_URL = "https://e-childschoolinfo.moe.go.kr/api/notice/{ep}.do"
CACHE_TTL_DAYS = 7

# ---------------------------------------------------------------- endpoints
ENDPOINTS = {
    "basicInfo": "기본현황(구버전)",
    "basicInfo2": "기본현황",
    "building": "건물현황",
    "classArea": "교실면적현황",
    "teachersInfo": "직위·자격별 교직원현황",
    "lessonDay": "수업일수현황",
    "schoolMeal": "급식운영현황",
    "schoolBus": "통학차량현황",
    "yearOfWork": "근속연수현황",
    "environmentHygiene": "환경위생 관리현황",
    "safetyEdu": "안전점검·교육 실시현황",
    "deductionSociety": "공제회 가입현황",
    "insurance": "보험별 가입현황",
    "afterSchoolPresent": "방과후 과정 편성·운영 현황",
}

# ------------------------------------------------------------- region codes
SIDO = {
    "서울": "11", "부산": "26", "대구": "27", "인천": "28", "광주": "29",
    "대전": "30", "울산": "31", "세종": "36", "경기": "41", "강원": "51",
    "충북": "43", "충남": "44", "전북": "52", "전남": "46", "경북": "47",
    "경남": "48", "제주": "50",
}
# 개편 이전 코드(강원 42, 전북 45)도 discover 시 자동 시도
SIDO_LEGACY = {"51": "42", "52": "45"}
SIDO_ALIAS = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
    "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
    "울산광역시": "울산", "세종특별자치시": "세종", "세종시": "세종",
    "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원",
    "충청북도": "충북", "충청남도": "충남",
    "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남",
    "제주도": "제주", "제주특별자치도": "제주",
}

# ------------------------------------------------------------- field labels
FIELD_LABELS = {
    # 공통
    "kindercode": "유치원코드", "officeedu": "교육청", "subofficeedu": "교육지원청",
    "kindername": "유치원명", "establish": "설립유형", "estb_pt": "설립유형",
    "pbnttmng": "공시차수",
    # 기본현황(basicInfo2)
    "edate": "설립인가일", "odate": "개원일", "addr": "주소", "telno": "전화",
    "faxno": "팩스", "hpaddr": "홈페이지", "opertime": "운영시간",
    "rppnname": "대표자", "ldgrname": "원장",
    "clcnt3": "만3세 학급수", "clcnt4": "만4세 학급수", "clcnt5": "만5세 학급수",
    "mixclcnt": "혼합연령 학급수", "shclcnt": "특수 학급수",
    "ppcnt3": "만3세 원아수", "ppcnt4": "만4세 원아수", "ppcnt5": "만5세 원아수",
    "mixppcnt": "혼합연령 원아수", "shppcnt": "특수 원아수",
    "prmstfcnt": "인가 총정원",
    "ag3fpcnt": "만3세 정원", "ag4fpcnt": "만4세 정원", "ag5fpcnt": "만5세 정원",
    "mixfpcnt": "혼합연령 정원", "spcnfpcnt": "특수 정원",
    "rpst_yn": "대표시설 여부", "lttdcdnt": "위도", "lngtcdnt": "경도",
    # 건물현황
    "archyy": "건축연도", "floorcnt": "층수",
    "bldgprusarea": "건물 전용면적", "grottar": "연면적",
    # 교실면적현황
    "crcnt": "교실 수", "clsrarea": "교실 총면적",
    "phgrindrarea": "실내 체육장 면적", "hlsparea": "보건·위생공간 면적",
    "ktchmssparea": "조리·급식공간 면적", "otsparea": "옥외 체육장(놀이터) 면적",
    # 수업일수현황
    "ag3_lsn_dcnt": "만3세 수업일수", "ag4_lsn_dcnt": "만4세 수업일수",
    "ag5_lsn_dcnt": "만5세 수업일수", "mix_age_lsn_dcnt": "혼합연령 수업일수",
    "spcl_lsn_dcnt": "특수학급 수업일수", "afsc_pros_lsn_dcnt": "방과후과정 운영일수",
    "ldnum_blw_yn": "법정 수업일수 미달 여부",
    # 근속연수현황
    "yy1_undr_thcnt": "근속 1년 미만", "yy1_abv_yy2_undr_thcnt": "근속 1~2년",
    "yy2_abv_yy4_undr_thcnt": "근속 2~4년", "yy4_abv_yy6_undr_thcnt": "근속 4~6년",
    "yy6_abv_thcnt": "근속 6년 이상",
    # 통학차량현황
    "vhcl_oprn_yn": "통학차량 운행 여부", "opra_vhcnt": "운행 차량 수",
    "dclr_vhcnt": "신고 차량 수", "psg9_dclr_vhcnt": "9인승 신고 대수",
    "psg12_dclr_vhcnt": "12인승 신고 대수", "psg15_dclr_vhcnt": "15인승 신고 대수",
    # 방과후 과정
    "inor_clcnt": "방과후과정 편성 학급수", "pm_rrgn_clcnt": "오후 재편성 학급수",
    "oper_time": "방과후 운영시간", "inor_ptcn_kpcnt": "방과후과정 참여 원아수",
    "pm_rrgn_ptcn_kpcnt": "오후 재편성 참여 원아수",
    "fxrl_thcnt": "방과후 전담교사(정규) 수", "shcnt_thcnt": "방과후 전담교사(단시간) 수",
    "incnt": "방과후 기간제교사 수", "cce_tcr_cnt": "방과후 강사 수",
    "etc_thts_cnt": "방과후 기타인력 수",
    # 안전점검·교육
    "plyg_ck_yn": "놀이시설 안전점검 실시", "plyg_ck_dt": "놀이시설 점검일",
    "plyg_ck_rs_cd": "놀이시설 점검 결과",
    "cctv_ist_yn": "CCTV 설치 여부", "cctv_ist_total": "CCTV 총 대수",
    "cctv_ist_in": "CCTV 실내", "cctv_ist_out": "CCTV 실외",
    "fire_avd_yn": "소방대피훈련 실시", "fire_avd_dt": "소방대피훈련일",
    "fire_safe_yn": "소방안전점검 실시", "fire_safe_dt": "소방안전점검일",
    "gas_ck_yn": "가스안전점검 실시", "gas_ck_dt": "가스안전점검일",
    "elect_ck_yn": "전기안전점검 실시", "elect_ck_dt": "전기안전점검일",
    # 환경위생
    "mdst_chk_dt": "미세먼지 점검일", "mdst_chk_rslt_cd": "미세먼지 점검 결과",
    "ilmn_chk_dt": "조도 점검일", "ilmn_chk_rslt_cd": "조도 점검 결과",
    "unwt_qlwt_insc_yn": "지하수 사용(수질검사 대상) 여부",
    "qlwt_insc_dt": "수질검사일", "qlwt_insc_stby_yn": "수질검사 적합 여부",
    "fxtm_dsnf_trgt_yn": "정기소독 대상 여부", "fxtm_dsnf_chk_dt": "정기소독일",
    "fxtm_dsnf_chk_rslt_tp_cd": "정기소독 실시 여부",
    "arql_chk_dt": "공기질 측정일", "arql_chk_rslt_tp_cd": "공기질 측정 결과",
    # 공제회/보험
    "school_ds_yn": "학교안전공제회 가입", "school_ds_en": "학교안전공제회 가입대상",
    "educate_ds_yn": "교육시설재난공제회 가입", "educate_ds_en": "교육시설재난공제회 가입대상",
    "insurance_nm": "보험명", "insurance_en": "가입대상 여부",
    "insurance_yn": "가입 여부", "company1": "보험사1", "company2": "보험사2",
    "company3": "보험사3",
}
SKIP_FIELDS = {"key", "page"}


# ------------------------------------------------------------------- errors
class ApiError(Exception):
    pass


class ApiDenied(ApiError):
    """공시측 데이터 점검 중 등으로 항목이 잠긴 상태."""


class ApiKeyError(ApiError):
    """인증키가 없거나 유효하지 않음."""


# -------------------------------------------------------------------- utils
_env_cache = None


def env_file_values():
    """.env 파일을 읽어 dict 로 반환(외부 패키지 없이 최소 파싱)."""
    global _env_cache
    if _env_cache is not None:
        return _env_cache
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, val = line.partition("=")
            if not sep:
                continue
            values[key.strip()] = val.strip().strip('"').strip("'")
    _env_cache = values
    return values


def get_setting(name, default=None):
    """설정값 조회. 우선순위: 환경변수 > .env 파일 > 기본값."""
    return os.environ.get(name) or env_file_values().get(name) or default


NO_KEY_MSG = """[오류] API 인증키가 없습니다.

  1) 유치원알리미(https://e-childschoolinfo.moe.go.kr)에서 Open API 인증키를 신청하세요.
  2) 이 폴더의 .env.example 을 .env 로 복사한 뒤,
     KINDER_API_KEY=발급받은키   형태로 키를 채워 넣으세요.

  (환경변수 KINDER_API_KEY 로 지정해도 됩니다.)"""


def load_api_key():
    key = get_setting("KINDER_API_KEY")
    if key:
        return key
    if CONFIG_FILE.exists():  # 구버전 호환
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if cfg.get("api_key"):
            return cfg["api_key"]
    sys.exit(NO_KEY_MSG)


# ------------------------------------------------------- 입학 연령 계산
AGE_CLASSES = (3, 4, 5)


def next_admission_year(today=None):
    """다음에 지원 가능한 학년도. 접수가 전해 11월이라 '현재연도+1'이 기준."""
    return (today or datetime.now()).year + 1


def current_school_year(today=None):
    """현재 날짜에 진행 중인 학년도(한국 학년도는 3월 시작)."""
    now = today or datetime.now()
    return now.year if now.month >= 3 else now.year - 1


def age_class_for(birth_year, school_year):
    """해당 학년도에 배정되는 유치원 연령(만N세). 만3세반 = 출생연도+4."""
    return school_year - birth_year - 1


def parse_birth_ym(text):
    """'2020-01' / '2020.1' / '202001' → (연, 월). 실패 시 None."""
    m = re.match(r"^\s*(\d{4})\D?(\d{1,2})?\s*$", str(text or ""))
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2) or 1)
    if not 1 <= month <= 12:
        return None
    return year, month


def resolve_target_age():
    """CHILD_BIRTH_YM 설정으로 대상 연령반을 계산. (연령, 안내문) 반환."""
    raw = get_setting("CHILD_BIRTH_YM")
    if not raw:
        sys.exit("[오류] --target 을 쓰려면 .env 에 CHILD_BIRTH_YM=YYYY-MM 을 "
                 "설정하세요. (예: CHILD_BIRTH_YM=2020-01)")
    parsed = parse_birth_ym(raw)
    if not parsed:
        sys.exit(f"[오류] CHILD_BIRTH_YM 형식이 올바르지 않습니다: '{raw}' "
                 f"(YYYY-MM 형태로 적어주세요)")
    birth_year, birth_month = parsed
    year = next_admission_year()
    age = age_class_for(birth_year, year)
    if age < min(AGE_CLASSES):
        first = birth_year + 4
        sys.exit(f"[안내] {birth_year}년 {birth_month}월생은 아직 유치원 입학 "
                 f"대상이 아닙니다. 만3세반 입학은 {first}학년도이고 "
                 f"처음학교로 접수는 {first - 1}년 11월경입니다.")
    if age > max(AGE_CLASSES):
        sys.exit(f"[안내] {birth_year}년 {birth_month}월생은 {year}학년도 기준 "
                 f"유치원 연령(만3~5세)을 넘었습니다.")
    note = (f"{birth_year}년 {birth_month}월생 → {year}학년도 만{age}세반 대상 "
            f"(처음학교로 접수: {year - 1}년 11월경)")
    return age, note


def requested_school_year(args):
    """명령의 비교 학년도. --year > --target > 현재 학년도 순서."""
    explicit = getattr(args, "year", None)
    if explicit:
        return int(explicit)
    if getattr(args, "target", False):
        parsed = parse_birth_ym(get_setting("CHILD_BIRTH_YM"))
        if not parsed:
            sys.exit("[오류] --target 을 쓰려면 .env 에 "
                     "CHILD_BIRTH_YM=YYYY-MM 을 설정하세요.")
        return parsed[0] + 4
    return current_school_year()


def to_int(v):
    """'781㎡', '12개', '3' → int. 숫자가 없으면 None."""
    if v is None:
        return None
    m = re.search(r"-?\d+", str(v).replace(",", ""))
    return int(m.group()) if m else None


def fmt_date(v):
    s = str(v or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def fmt_pbnttmng(v):
    s = str(v or "")
    if re.fullmatch(r"\d{5}", s):
        return f"{s[:4]}년 {s[4]}차 공시"
    return s


def is_empty(v):
    return v is None or str(v).strip() in ("", "-", "null", "None")


# ---------------------------------------------------------------- API fetch
def fetch(ep, sido, sgg, fresh=False, quiet=False):
    """엔드포인트 1개 × 시군구 1개 호출(디스크 캐시). 반환: 레코드 list."""
    if ep not in ENDPOINTS:
        raise ApiError(f"알 수 없는 엔드포인트: {ep} (가능: {', '.join(ENDPOINTS)})")
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{ep}_{sido}_{sgg}.json"
    if not fresh and cache_file.exists():
        try:
            blob = json.loads(cache_file.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(blob["fetched"])
            if datetime.now() - fetched < timedelta(days=CACHE_TTL_DAYS):
                return blob["rows"]
        except Exception:
            pass  # 캐시 파손 시 재요청

    params = urllib.parse.urlencode(
        {"key": load_api_key(), "sidoCode": sido, "sggCode": sgg})
    url = BASE_URL.format(ep=ep) + "?" + params
    req = urllib.request.Request(url, headers={"User-Agent": "kinder-info-cli/1.0"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(1 + attempt)
    else:
        raise ApiError(f"API 호출 실패({ep}): {last_err}")

    status = data.get("status")
    if status == "DENIED":
        msg = data.get("message", "데이터 점검 중")
        # 키 문제와 '항목 점검 중'이 같은 DENIED 로 오므로 구분한다.
        if "키" in msg or "key" in msg.lower():
            raise ApiKeyError(msg)
        raise ApiDenied(msg)
    if status != "SUCCESS":
        raise ApiError(f"API 응답 이상({ep}): {json.dumps(data, ensure_ascii=False)[:300]}")
    rows = data.get("kinderInfo", []) or []
    cache_file.write_text(
        json.dumps({"fetched": datetime.now().isoformat(), "rows": rows},
                   ensure_ascii=False), encoding="utf-8")
    if not quiet:
        print(f"  [API] {ENDPOINTS[ep]} {sido}/{sgg}: {len(rows)}건", file=sys.stderr)
    return rows


# ------------------------------------------------------ 새 공시 차수 감지
PULSE_FILE = CACHE_DIR / "_pulse.json"


def check_new_disclosure():
    """하루 1회, 캐시가 가장 작은 지역 하나만 새로 받아 공시 차수를 비교한다.

    새 차수(예: 2026년 2차)가 게시됐는데 캐시가 옛 차수면 안내 문구를 반환.
    실패는 조용히 무시한다 — 알림이 본 기능을 방해하면 안 된다.
    """
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        state = {}
        if PULSE_FILE.exists():
            state = json.loads(PULSE_FILE.read_text(encoding="utf-8"))
        if state.get("checked") == today:
            return None
        caches = sorted((f for f in CACHE_DIR.glob("basicInfo2_*.json")),
                        key=lambda f: f.stat().st_size)
        tmngs, smallest = set(), None
        for f in caches:
            try:
                rows = json.loads(f.read_text(encoding="utf-8"))["rows"]
            except (OSError, ValueError, KeyError):
                continue
            if rows:
                tmngs.add(str(rows[0].get("pbnttmng") or ""))
                smallest = smallest or f
        state["checked"] = today
        PULSE_FILE.write_text(json.dumps(state), encoding="utf-8")
        if not smallest or not tmngs:
            return None
        _, sido, sgg = smallest.stem.split("_")
        fresh_rows = fetch("basicInfo2", sido, sgg, fresh=True, quiet=True)
        fresh = str(fresh_rows[0].get("pbnttmng") or "") if fresh_rows else ""
        if fresh and fresh > min(t for t in tmngs if t):
            return (f"새 공시({fmt_pbnttmng(fresh)})가 게시되었습니다. "
                    f"`python kinderinfo.py refresh` 후 다시 조회하면 최신 자료로 보고, "
                    f"후보를 저장해 뒀다면 `diff` 로 무엇이 바뀌었는지 바로 볼 수 있습니다.")
    except Exception:  # noqa: BLE001 — 감지 실패는 본 기능에 영향 없음
        return None
    return None


def notify_new_disclosure():
    note = check_new_disclosure()
    if note:
        print(f"\n📢 {note}")


def cmd_refresh(args):
    """캐시를 비워 다음 조회부터 최신 공시를 새로 받게 한다."""
    source = getattr(args, "source", None)
    if source == "traffic":
        try:
            import traffic_safety
            result = traffic_safety.refresh()
            updated = sum(1 for v in result.values() if v.get("updated"))
            print(f"도로교통공단 공식 CSV {updated}/2종을 갱신했습니다. "
                  "갱신되지 않은 항목은 마지막 정상 캐시를 유지합니다.")
        except Exception as e:  # noqa: BLE001
            sys.exit(f"[오류] 교통안전 자료 갱신 실패: {e}")
        return
    if source == "bus":
        try:
            import schoolbus
            n = schoolbus.clear_cache(live_only=True)
            print(f"통학버스 자동 조회 캐시 {n}개를 비웠습니다. 다음 조회 때 최신 화면을 읽습니다.")
        except Exception as e:  # noqa: BLE001
            sys.exit(f"[오류] 통학버스 캐시 갱신 실패: {e}")
        return
    if source == "schoolinfo":
        try:
            import schoolinfo
            n = schoolinfo.clear_cache()
            print(f"학교알리미 캐시 {n}개를 비웠습니다. 다음 조회 때 최신 공시를 읽습니다.")
        except Exception as e:  # noqa: BLE001
            sys.exit(f"[오류] 학교알리미 캐시 갱신 실패: {e}")
        return
    n = 0
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()
            n += 1
    try:
        import kinderweb
        n += kinderweb.clear_cache()
    except (ImportError, OSError):
        pass
    if getattr(args, "all", False):
        try:
            import schoolinfo
            n += schoolinfo.clear_cache()
        except (ImportError, OSError):
            pass
        try:
            import schoolbus
            n += schoolbus.clear_cache(live_only=True)
        except (ImportError, OSError):
            pass
        try:
            import traffic_safety
            traffic_safety.refresh()
        except Exception as e:  # noqa: BLE001
            print(f"[안내] 교통안전 CSV 갱신만 실패했습니다: {e}", file=sys.stderr)
    print(f"캐시 {n}개 파일을 비웠습니다. 다음 조회부터 최신 공시를 새로 받습니다."
          " (과거 차수 벌크 자료는 불변이라 보존됩니다)")


# ------------------------------------------------------------ region tables
def load_sgg():
    if SGG_FILE.exists():
        return json.loads(SGG_FILE.read_text(encoding="utf-8"))
    return {}


def save_sgg(table):
    SGG_FILE.write_text(json.dumps(table, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def sgg_name_from_addr(addr, sido_code):
    """주소 문자열에서 시군구명 추출. '서울특별시 강남구 테헤란로 1' → '강남구'."""
    if sido_code == "36":
        return "세종시"
    toks = str(addr or "").split()
    if len(toks) < 2:
        return None
    name = toks[1]
    # '수원시 장안구' 처럼 시+구 이중 구조
    if name.endswith("시") and len(toks) >= 3 and toks[2].endswith("구"):
        name = f"{name} {toks[2]}"
    if name.endswith(("구", "군", "시")):
        return name
    return None


def resolve_sido(text):
    """'서울'/'서울특별시'/'11' → ('11', '서울'). 실패 시 None."""
    t = str(text).strip()
    if t in SIDO_ALIAS:
        t = SIDO_ALIAS[t]
    if t in SIDO:
        return SIDO[t], t
    if re.fullmatch(r"\d{2}", t):
        for name, code in SIDO.items():
            if code == t:
                return code, name
        return t, t
    return None


def discover(sido_text, full=False, fresh=False):
    """해당 시도의 유효 시군구 코드를 스캔해 sgg_codes.json 에 저장."""
    r = resolve_sido(sido_text)
    if not r:
        sys.exit(f"[오류] 시도를 해석할 수 없습니다: {sido_text} "
                 f"(가능: {', '.join(SIDO)})")
    sido_code, sido_name = r
    table = load_sgg()
    if not fresh and table.get(sido_code):
        print(f"[정보] {sido_name}({sido_code})는 이미 탐색됨 "
              f"({len(table[sido_code])}개 시군구). --fresh 로 재탐색 가능.",
              file=sys.stderr)
        return table[sido_code]

    step = 1 if full else 5
    codes_to_try = [sido_code] + ([SIDO_LEGACY[sido_code]]
                                  if sido_code in SIDO_LEGACY else [])
    found = {}
    for sc in codes_to_try:
        print(f"[탐색] {sido_name}: sidoCode={sc}, sggCode {sc}100~{sc}999 "
              f"(step {step}) 스캔 중...", file=sys.stderr)
        for n in range(100, 1000, step):
            sgg = f"{sc}{n:03d}"
            try:
                rows = fetch("basicInfo2", sc, sgg, quiet=True)
            except ApiError:
                continue
            if not rows:
                continue
            name = sgg_name_from_addr(rows[0].get("addr"), sc) or sgg
            found[sgg] = {"name": name, "sido": sc, "count": len(rows)}
            print(f"  {sgg} = {name} ({len(rows)}개 유치원)", file=sys.stderr)
            time.sleep(0.05)
        if found:
            break  # 신코드에서 찾았으면 구코드 스캔 불필요
    if not found:
        hint = "" if full else " 경기 등 '일반시+구' 지역은 --full 로 재시도하세요."
        sys.exit(f"[오류] {sido_name}에서 시군구를 찾지 못했습니다.{hint}")
    table[sido_code] = found
    save_sgg(table)
    print(f"[완료] {sido_name}: {len(found)}개 시군구 저장 → {SGG_FILE.name}",
          file=sys.stderr)
    return found


def resolve_region(text, auto_discover=True):
    """지역 문자열 → [(sido_code, sgg_code, sgg_name), ...]

    허용: '11680' | '서울' | '서울 강남구' | '서울강남구' | '강남구'(유일할 때)
    """
    t = str(text).strip()
    table = load_sgg()

    if re.fullmatch(r"\d{5}", t):  # 시군구 코드 직접 지정
        sido_code = t[:2]
        name = table.get(sido_code, {}).get(t, {}).get("name", t)
        return [(sido_code, t, name)]

    # 시도 접두 분리
    sido_part, rest = None, t
    for alias, base in list(SIDO_ALIAS.items()) + [(k, k) for k in SIDO]:
        if t.startswith(alias):
            sido_part, rest = base, t[len(alias):].strip()
            break

    if sido_part:
        sido_code, sido_name = resolve_sido(sido_part)
        if sido_code not in table and auto_discover:
            print(f"[정보] {sido_name} 시군구 코드 최초 탐색을 시작합니다 "
                  f"(1회, 이후 캐시 사용)...", file=sys.stderr)
            discover(sido_part)
            table = load_sgg()
        sggs = table.get(sido_code, {})
        if not sggs:
            sys.exit(f"[오류] {sido_name} 시군구 정보가 없습니다. "
                     f"discover {sido_part} 를 먼저 실행하세요.")
        if not rest:  # 시도 전체
            return [(v["sido"], code, v["name"]) for code, v in sorted(sggs.items())]
        hits = [(v["sido"], code, v["name"]) for code, v in sorted(sggs.items())
                if rest.replace(" ", "") in v["name"].replace(" ", "")]
        if not hits:
            sys.exit(f"[오류] {sido_name}에서 '{rest}' 시군구를 찾지 못했습니다. "
                     f"가능: {', '.join(v['name'] for v in sggs.values())}")
        return hits

    # 시도 없이 시군구명만: 전 캐시에서 검색
    hits = []
    for sido_code, sggs in table.items():
        for code, v in sggs.items():
            if t.replace(" ", "") in v["name"].replace(" ", ""):
                hits.append((v["sido"], code, v["name"]))
    if len(hits) == 1:
        return hits
    if len(hits) > 1:
        opts = ", ".join(f"{s}{'' if s else ''}{c}({n})" for s, c, n in hits)
        sys.exit(f"[오류] '{t}'가 여러 지역과 일치합니다: {opts} — "
                 f"'서울 강남구'처럼 시도를 붙여주세요.")
    sys.exit(f"[오류] 지역을 해석할 수 없습니다: '{t}'. "
             f"'서울', '서울 강남구', 시군구코드(예: 11680) 형식을 지원합니다.")


# --------------------------------------------------------- derived metrics
def total_pupils(b):
    vals = [to_int(b.get(k)) for k in
            ("ppcnt3", "ppcnt4", "ppcnt5", "mixppcnt", "shppcnt")]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def total_classes(b):
    vals = [to_int(b.get(k)) for k in
            ("clcnt3", "clcnt4", "clcnt5", "mixclcnt", "shclcnt")]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def fill_rate(b):
    tp, cap = total_pupils(b), to_int(b.get("prmstfcnt"))
    if tp is None or not cap:
        return None
    return round(100 * tp / cap)


def per_class(b, age):
    pp, cl = to_int(b.get(f"ppcnt{age}")), to_int(b.get(f"clcnt{age}"))
    if pp is None or not cl:
        return None
    return round(pp / cl, 1)


TENURE_BINS = [
    ("yy1_undr_thcnt", 0.5), ("yy1_abv_yy2_undr_thcnt", 1.5),
    ("yy2_abv_yy4_undr_thcnt", 3.0), ("yy4_abv_yy6_undr_thcnt", 5.0),
    ("yy6_abv_thcnt", 8.0),
]


def tenure_stats(row):
    """근속연수 행 → (교사수, 추정 평균 근속, 1년미만 비율%, 6년이상 비율%)"""
    if not row:
        return None
    counts = [(to_int(row.get(k)) or 0, mid) for k, mid in TENURE_BINS]
    total = sum(c for c, _ in counts)
    if total == 0:
        return None
    avg = sum(c * m for c, m in counts) / total
    under1 = round(100 * counts[0][0] / total)
    over6 = round(100 * counts[-1][0] / total)
    return total, round(avg, 1), under1, over6


def afterschool_participants(a):
    """방과후과정 참여 원아수.

    공시 양식이 두 가지다 — 인가 학급 그대로 편성(inor_)하거나 오후에 재편성(pm_rrgn_).
    한쪽만 세면 다른 양식으로 공시한 유치원이 참여 0명으로 잘못 나온다.
    """
    if not a:
        return None
    vals = [to_int(a.get(k))
            for k in ("inor_ptcn_kpcnt", "pm_rrgn_ptcn_kpcnt")]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def afterschool_rate(a, b):
    part, tp = afterschool_participants(a), total_pupils(b)
    if part is None or not tp:
        return None
    return round(100 * part / tp)


def attendance_days(lesson_row, age=None):
    """(정규 수업일수, 방과후 포함 운영일수) 반환.

    병설유치원은 초등학교 학사일정을 따라 정규 수업일수가 법정 최소(180일)에 가깝고,
    방학은 방과후과정으로 메운다. 두 값의 차이가 '방과후에 기대야 하는 날'이다.
    """
    if not lesson_row:
        return None, None
    keys = [f"ag{age}_lsn_dcnt"] if age else []
    keys += [f"ag{a}_lsn_dcnt" for a in AGE_CLASSES] + ["mix_age_lsn_dcnt"]
    regular = next((v for v in (to_int(lesson_row.get(k)) for k in keys) if v), None)
    total = to_int(lesson_row.get("afsc_pros_lsn_dcnt"))
    return regular, total


# ------------------------------------------------------------ 위치·접근성
def coords_of(b):
    """유치원 좌표 (위도, 경도). 없으면 None."""
    try:
        lat, lng = float(b.get("lttdcdnt")), float(b.get("lngtcdnt"))
    except (TypeError, ValueError):
        return None
    return (lat, lng) if lat and lng else None


def home_coords():
    """.env 의 HOME_LATLNG='37.5665,126.9780' → (위도, 경도). 없으면 None.

    지도 앱에서 집을 우클릭하면 좌표가 나온다. 도로명주소를 좌표로 바꾸는 무료 방법은
    한국에서 신뢰할 수 없어(건물번호를 무시하고 도로만 잡는다) 좌표를 직접 받는다.
    """
    raw = get_setting("HOME_LATLNG")
    if not raw:
        return None
    m = re.findall(r"-?\d+\.?\d*", str(raw))
    if len(m) < 2:
        return None
    return float(m[0]), float(m[1])


def haversine_km(a, b):
    """두 좌표 사이 직선거리(km). 언덕·도로를 고려하지 않는 하한값이다."""
    import math
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def distance_from_home(b):
    """집에서 유치원까지 직선거리(km). 설정·좌표가 없으면 None."""
    home, there = home_coords(), coords_of(b)
    return haversine_km(home, there) if home and there else None


def fmt_km(km):
    if km is None:
        return None
    return f"{int(round(km * 1000))}m" if km < 1 else f"{km:.1f}km"


def roadview_url(b):
    """카카오 로드뷰 링크. 정차 여건은 데이터에 없으니 눈으로 봐야 한다."""
    c = coords_of(b)
    return f"https://map.kakao.com/link/roadview/{c[0]},{c[1]}" if c else None


def map_url(b):
    c = coords_of(b)
    if not c:
        return None
    name = urllib.parse.quote(str(b.get("kindername", "유치원")))
    return f"https://map.kakao.com/link/map/{name},{c[0]},{c[1]}"


def load_route():
    """경로 모듈 지연 로드. 없으면 None — 직선거리만 쓴다."""
    try:
        import route
    except ImportError:
        return None
    return route


def road_from_home(b, quiet=True):
    """집→유치원 자차 경로 (km, 분, 우회율). 못 구하면 (None, None, None)."""
    rt = load_route()
    home, there = home_coords(), coords_of(b)
    if not rt or not home or not there:
        return None, None, None
    km, mins = rt.driving(home, there)
    return km, mins, rt.detour_ratio(km, distance_from_home(b))


def road_note(b):
    """'1.20km / 3분 (직선의 2.2배 ⚠)' 형태."""
    rt = load_route()
    if not rt:
        return None
    km, mins, _ = road_from_home(b)
    return rt.describe(km, mins, distance_from_home(b))


def cmd_home(args):
    """집 좌표 설정 — 네이버지도 공유 링크에서 바로 뽑는다."""
    rt = load_route()
    if not rt:
        sys.exit("[오류] route.py 를 찾을 수 없습니다. 저장소에서 함께 받아주세요.")
    if args.show or not args.link:
        h = home_coords()
        print(f"집 좌표: {h[0]}, {h[1]}" if h else
              "집 좌표가 없습니다.\n"
              "  네이버지도에서 집을 찾아 [공유] 링크를 복사한 뒤:\n"
              '    python kinderinfo.py home "https://naver.me/XXXXXX"')
        return
    try:
        lat, lng = rt.coords_from_map_link(args.link)
    except rt.RouteError as e:
        sys.exit(f"[오류] {e}")

    lines, done = [], False
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("HOME_LATLNG="):
                lines.append(f"HOME_LATLNG={lat},{lng}")
                done = True
            else:
                lines.append(line)
    if not done:
        lines += ["", "# 집 좌표 (지도 공유 링크에서 추출)",
                  f"HOME_LATLNG={lat},{lng}"]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    global _env_cache
    _env_cache = None
    print(f"집 좌표를 저장했습니다: {lat},{lng}")
    print("이제 search --near / --road 로 집 기준 거리를 쓸 수 있습니다.")


def bus_note(bus_row):
    """통학차량 운행 여부. 운행하면 거리보다 노선이 중요해진다."""
    if not bus_row:
        return None
    if bus_row.get("vhcl_oprn_yn") == "Y":
        n = to_int(bus_row.get("opra_vhcnt")) or 0
        return f"운행 {n}대 (노선 정보는 미공시)"
    return "미운행 — 자차·도보 등하원"


def is_annex(b):
    """초등학교 병설 여부. 병설은 초등 학사일정을 따라 방학이 길다."""
    return "병설" in str(b.get("establish") or b.get("estb_pt") or "")


def attendance_note(lesson_row, b=None):
    """'정규 180일 + 방과후로 52일 = 232일' 형태의 안내 문구."""
    regular, total = attendance_days(lesson_row)
    if not regular and not total:
        return None
    if not total or not regular:
        return f"정규 수업 {regular or total}일"
    gap = total - regular
    note = f"정규 수업 {regular}일 / 방과후 포함 {total}일"
    if gap > 0:
        note += f" (차이 {gap}일은 방학 등, 방과후과정으로 운영)"
    if b is not None and is_annex(b) and regular <= 190:
        note += " ※병설: 정규 일수가 법정 최소(180일)에 가까움"
    return note


def operating_note(b, asp_row=None):
    """공시 운영시간은 '문 여는 시간'이지 정규 교육과정 시간이 아니다."""
    base = b.get("opertime")
    if not base:
        return None
    after = (asp_row or {}).get("oper_time")
    note = str(base)
    if after and str(after) != str(base):
        note += f" / 방과후 {after}"
    return note + " (문 여는 시간 기준. 정규 교육과정은 1일 4~5시간이고 나머지는 방과후·돌봄)"


def fmt_time_range(value):
    """{'시작':'09:00','종료':'14:00'}를 한 줄 시간 범위로 표시."""
    if not isinstance(value, dict):
        return None
    start, end = value.get("시작"), value.get("종료")
    return f"{start}~{end}" if start and end else None


def gather_hours(kinder, school_year=None, fresh=False):
    """웹 공시와 로컬 공식문서 검증값을 합친 운영시간 정보."""
    school_year = school_year or current_school_year()
    result = {"requested_year": school_year, "web": None,
              "verified": None, "warnings": []}
    code = kinder.get("kindercode")
    try:
        import kinderweb
        result["web"] = kinderweb.get_hours(code, fresh=fresh)
    except Exception as e:  # noqa: BLE001 — 부가 웹 공시
        result["warnings"].append(f"웹 운영시간 조회 실패: {e}")
    try:
        import verified_hours
        result["verified"] = verified_hours.get(code, school_year)
    except Exception as e:  # noqa: BLE001 — 로컬 검증정보 오류도 본체와 분리
        result["warnings"].append(f"공식문서 검증정보 읽기 실패: {e}")

    web_range = (result.get("web") or {}).get("교육과정")
    verified_range = (result.get("verified") or {}).get("education")
    if (web_range and verified_range
            and fmt_time_range(web_range) != fmt_time_range(verified_range)):
        result["warnings"].append(
            "웹 공시 교육과정 시간과 공식 계획서 검증값이 서로 다름")
    return result


def hours_value(info, key):
    """운영시간 항목 하나를 출처·학년도와 함께 표시."""
    if not isinstance(info, dict):
        return None
    verified = info.get("verified") or {}
    value = verified.get(key)
    if value:
        year = verified.get("school_year")
        requested = info.get("requested_year")
        suffix = f" ({year} 공식 계획서"
        if requested and year != requested:
            suffix += f", {requested}학년도 참고용"
        return fmt_time_range(value) + suffix + ")"
    if key == "education":
        web = info.get("web") or {}
        value = web.get("교육과정")
        if value:
            return f"{fmt_time_range(value)} ({web.get('기준') or '웹 공시'})"
    return None


def hours_summary_items(b, asp_row=None, info=None):
    """profile/report에 넣는 운영시간의 출처 구분 행."""
    if not info:
        return [("공시 전체 운영범위", operating_note(b, asp_row))]
    web = info.get("web") or {}
    verified = info.get("verified") or {}
    outer = fmt_time_range(web.get("공시전체범위")) or b.get("opertime")
    rows = [
        ("공시 전체 운영범위",
         (f"{outer} (조기·정규·방과후·저녁돌봄을 합친 외곽 범위)"
          if outer else None)),
        ("정규 교육과정", hours_value(info, "education")),
        ("일반 방과후", hours_value(info, "afterschool")),
        ("조기돌봄", hours_value(info, "early_care")),
        ("저녁돌봄", hours_value(info, "late_care")),
        ("방학 중 운영", hours_value(info, "vacation_hours")),
    ]
    closures = verified.get("vacation_closures") or []
    conditions = verified.get("conditions") or []
    if closures:
        rows.append(("방학 미운영", " · ".join(closures)))
    if conditions:
        rows.append(("운영 조건", " · ".join(conditions)))
    for warning in info.get("warnings") or []:
        rows.append(("운영시간 주의", f"⚠ {warning}"))
    return rows


def area_per_pupil(c, b):
    area, tp = to_int((c or {}).get("clsrarea")), total_pupils(b)
    if area is None or not tp:
        return None
    return round(area / tp, 1)


# ----------------------------------------------------------- data assembly
def region_basic(regions, fresh=False):
    """지역 목록의 basicInfo2 레코드 전부 (시군구명 부착)."""
    out = []
    for sido, sgg, name in regions:
        for row in fetch("basicInfo2", sido, sgg, fresh=fresh):
            row["_sgg_name"] = name
            row["_sido"] = sido
            row["_sgg"] = sgg
            out.append(row)
    return out


def find_kinder(regions, query, fresh=False):
    """이름 부분일치 or kindercode 로 유치원 1곳 특정."""
    rows = region_basic(regions, fresh=fresh)
    q = query.replace(" ", "")
    hits = [r for r in rows if r.get("kindercode") == query]
    if not hits:
        hits = [r for r in rows
                if q in str(r.get("kindername", "")).replace(" ", "")]
    if not hits:
        sys.exit(f"[오류] '{query}' 유치원을 찾지 못했습니다. "
                 f"search 명령으로 정확한 이름을 확인하세요.")
    if len(hits) > 1:
        names = ", ".join(f"{r['kindername']}({r['_sgg_name']})" for r in hits[:10])
        sys.exit(f"[오류] '{query}'와 일치하는 유치원이 {len(hits)}곳입니다: "
                 f"{names} — 더 구체적으로 지정하세요.")
    return hits[0]


def rows_for_kinder(ep, kinder, fresh=False):
    """해당 유치원(시군구 고정)의 특정 엔드포인트 레코드들. DENIED 는 예외 전파."""
    rows = fetch(ep, kinder["_sido"], kinder["_sgg"], fresh=fresh)
    code = kinder.get("kindercode")
    name = str(kinder.get("kindername", "")).replace(" ", "")
    hits = [r for r in rows if r.get("kindercode") == code]
    if not hits and name:  # 일부 항목은 kindercode 가 달라 이름으로 보조 매칭
        hits = [r for r in rows
                if str(r.get("kindername", "")).replace(" ", "") == name]
    return hits


# ------------------------------------------------------------------ output
def md_escape(v):
    return str(v).replace("|", "\\|")


def render_kv(row, skip_common=True):
    lines = []
    skip = set(SKIP_FIELDS)
    if skip_common:
        skip |= {"kindercode", "officeedu", "subofficeedu", "kindername",
                 "establish", "estb_pt", "pbnttmng",
                 "_sgg_name", "_sido", "_sgg"}
    for k, v in row.items():
        if k in skip or is_empty(v):
            continue
        label = FIELD_LABELS.get(k, k)
        if k.endswith(("_dt", "edate", "odate")) or k in ("edate", "odate"):
            v = fmt_date(v)
        lines.append(f"- {label}: {v}")
    return lines


def one_line_summary(b):
    est = b.get("establish") or b.get("estb_pt") or "?"
    tp, cap = total_pupils(b), to_int(b.get("prmstfcnt"))
    fr = fill_rate(b)
    seg = f"원아 {tp}명" + (f"/정원 {cap}명({fr}%)" if cap else "")
    return f"{est} · {seg}"


# ------------------------------------------------------------- cmd: search
def resolve_search_age(args):
    """검색에 쓸 연령반과 안내문 결정. (연령|None, 안내문|None)"""
    if getattr(args, "target", False):
        return resolve_target_age()
    if getattr(args, "age3", False) and not args.age:
        return 3, None
    return args.age, None


def cmd_search(args):
    age, target_note = resolve_search_age(args)
    regions = resolve_region(args.region)
    rows = region_basic(regions, fresh=args.fresh)
    if args.name:
        q = args.name.replace(" ", "")
        rows = [r for r in rows
                if q in str(r.get("kindername", "")).replace(" ", "")]
    if args.estab:
        rows = [r for r in rows
                if str(r.get("establish", "")).startswith(args.estab)]
    age_details = {}   # kindercode -> 웹 학급표 | "unverified"
    if age:
        kept = []
        kinderweb = None
        if not args.no_web:
            try:
                import kinderweb as _kinderweb
                kinderweb = _kinderweb
            except ImportError:
                pass
        for r in rows:
            code = r.get("kindercode")
            direct = ((to_int(r.get(f"clcnt{age}")) or 0) > 0 or
                      (to_int(r.get(f"ag{age}fpcnt")) or 0) > 0)
            if direct:
                kept.append(r)
                continue
            if (to_int(r.get("mixclcnt")) or 0) <= 0:
                continue
            if kinderweb is None:
                age_details[code] = "unverified"
                kept.append(r)  # 불확실할 때는 후보를 조용히 버리지 않는다
                continue
            try:
                detail = kinderweb.get_age_classes(code, fresh=args.fresh)
                if mixed_classes_for_age(detail, age):
                    age_details[code] = detail
                    kept.append(r)
            except kinderweb.WebError:
                age_details[code] = "unverified"
                kept.append(r)  # 화면 개편/네트워크 실패도 누락보다 경고가 안전하다
        rows = kept

    home = home_coords()
    road = {}   # kindercode -> (km, 분, 우회율)
    if args.near or args.road:
        if not home:
            sys.exit("[오류] 집 좌표가 필요합니다. 네이버지도에서 집을 찾아 "
                     "[공유] 링크를 복사한 뒤 아래를 실행하세요.\n"
                     '  python kinderinfo.py home "https://naver.me/XXXXXX"')
        limit = args.near or 1e9
        # 도로 조회는 비싸므로 직선거리로 넉넉히 거른 뒤(2배) 남은 곳만 부른다.
        # 직선으로는 멀어 보여도 도로로 가까운 곳을 놓치지 않기 위함.
        prelim = limit * 2 if args.road else limit
        rows = [r for r in rows if (distance_from_home(r) or 1e9) <= prelim]
        if args.road:
            rt = load_route()
            if not rt:
                sys.exit("[오류] route.py 를 찾을 수 없습니다.")
            print(f"  [경로] {len(rows)}곳 자차 경로 조회 중...", file=sys.stderr)
            for r in rows:
                road[r["kindercode"]] = road_from_home(r)
            if args.near:
                rows = [r for r in rows
                        if (road[r["kindercode"]][0] or
                            distance_from_home(r) or 1e9) <= limit]

    size_key = f"ag{age or 3}fpcnt"

    def age_capacity(r):
        cap = to_int(r.get(size_key)) or 0
        detail = age_details.get(r.get("kindercode"))
        if isinstance(detail, dict) and age:
            cap += sum(v.get("정원") or 0 for _, v in mixed_classes_for_age(detail, age))
        return cap

    keyf = {
        "name": lambda r: str(r.get("kindername", "")),
        "size": lambda r: -age_capacity(r),
        "size3": lambda r: -age_capacity(r),   # 구 옵션 별칭
        "fill": lambda r: -(fill_rate(r) or -1),
        "dist": (lambda r: (road.get(r["kindercode"], (None,))[0]
                            or distance_from_home(r) or 1e9)) if road else
                (lambda r: distance_from_home(r)
                 if distance_from_home(r) is not None else 1e9),
    }[args.sort]
    rows.sort(key=keyf)
    if args.limit:
        rows = rows[:args.limit]

    if args.json:
        out = []
        for r in rows:
            item = dict(r)
            if r.get("kindercode") in age_details:
                item["_age_class_detail"] = age_details[r["kindercode"]]
            out.append(item)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    reg_label = ", ".join(dict.fromkeys(n for _, _, n in regions))
    pb = fmt_pbnttmng(rows[0].get("pbnttmng")) if rows else ""
    print(f"# 유치원 검색: {reg_label} — {len(rows)}곳"
          + (f" ({pb} 기준)" if pb else ""))
    if target_note:
        print(f"대상: {target_note}")
    flt = []
    if age:
        flt.append(f"만{age}세 포함(전용반+혼합반)")
    if args.estab:
        flt.append(f"설립유형={args.estab}")
    if args.name:
        flt.append(f"이름 '{args.name}'")
    if flt:
        print(f"필터: {', '.join(flt)}")
    print()

    age_col = f"만{age}세 학급/원아/정원" if age else "학급수(3/4/5세)"
    dist_col = "| 직선거리 " if home else ""
    dist_sep = "|---" if home else ""
    road_col = "| 자차(도로) " if road else ""
    road_sep = "|---" if road else ""
    print(f"| 유치원명 {dist_col}{road_col}| 시군구 | 설립 | {age_col} | 혼합반 | "
          "전체 원아/정원(충원율) | 운영시간 | 전화 |")
    print(f"|---{dist_sep}{road_sep}|---|---|---|---|---|---|---|")
    for r in rows:
        dist_cell = f"| {fmt_km(distance_from_home(r)) or '-'} " if home else ""
        road_cell = ""
        if road:
            km, mins, ratio = road.get(r["kindercode"], (None, None, None))
            road_cell = ("| {} ".format(
                f"{km:.2f}km/{mins:.0f}분" + (f" ({ratio:.1f}배{'⚠' if ratio >= 2 else ''})"
                                             if ratio else "")
                if km is not None else "-"))
        if age:
            detail = age_details.get(r.get("kindercode"))
            if detail == "unverified":
                cell = "혼합반 구성 미확인 ⚠"
            elif isinstance(detail, dict):
                cell = mixed_age_summary(detail, age, compact=True)
            else:
                cell = (f"{to_int(r.get(f'clcnt{age}')) or 0}학급/"
                        f"{to_int(r.get(f'ppcnt{age}')) or 0}명/"
                        f"{to_int(r.get(f'ag{age}fpcnt')) or 0}명")
        else:
            cell = "/".join(str(to_int(r.get(f"clcnt{a}")) or 0)
                            for a in AGE_CLASSES)
        mix = to_int(r.get("mixclcnt")) or 0
        tp, cap, fr = total_pupils(r), to_int(r.get("prmstfcnt")), fill_rate(r)
        whole = f"{tp}/{cap}" + (f" ({fr}%)" if fr is not None else "")
        print(f"| {md_escape(r.get('kindername'))} {dist_cell}{road_cell}| {r.get('_sgg_name')} "
              f"| {r.get('establish')} | {cell} "
              f"| {mix or '-'} | {whole} | {r.get('opertime') or '-'} "
              f"| {r.get('telno') or '-'} |")
    print()
    if age:
        print("* 혼합반 정원·현원은 포함 연령 전체의 합계입니다. 실제 만"
              f"{age}세 모집 인원과는 다를 수 있습니다.")
        if any(v == "unverified" for v in age_details.values()):
            print("⚠ 웹 학급표를 확인하지 못한 곳은 후보에서 빼지 않고 '구성 미확인'으로 "
                  "남겼습니다. profile --web으로 원본 공시를 다시 확인할 수 있습니다.")
    if home and not road:
        print("\n직선거리는 **언덕과 도로를 무시한 하한값**입니다. 실측하면 우회율이 "
              "1.0~3.1배까지 벌어집니다 — `--road` 를 붙이면 실제 자차 거리를 봅니다.")
    if road:
        rt = load_route()
        print(f"\n자차 거리·시간: {rt.SOURCE_NOTE}")
        print("⚠는 직선거리의 2배 넘게 돌아간다는 뜻입니다. 등하원 동선을 로드뷰로 "
              "확인해 보세요.")
    if home:
        print("자차 등하원이라면 **유치원 앞에 잠시 정차할 수 있는지**가 관건인데 이는 어떤 "
              "데이터에도 없습니다. profile 의 로드뷰 링크로 직접 확인하세요.")
    notify_new_disclosure()


# ------------------------------------------------------ 웹 공시(원비·시정명령)
def fmt_won(v):
    return "-" if v is None else f"{v:,}원"


def _cost_total_line(table):
    total = next((v for k, v in table.items() if k.startswith("합계")), None)
    if not total:
        return None
    a = total["금액"]
    return " / ".join(f"만{age}세 {fmt_won(a.get(age))}" for age in AGE_CLASSES)


MIXED_KEYS_BY_AGE = {
    3: ("3-4세", "3-5세"),
    4: ("3-4세", "4-5세", "3-5세"),
    5: ("4-5세", "3-5세"),
}


def mixed_classes_for_age(age_classes, age):
    """현재 웹 학급표에서 age 를 포함하는 혼합반 목록."""
    classes = (age_classes or {}).get("학급") or {}
    return [(key, classes.get(key) or {}) for key in MIXED_KEYS_BY_AGE[age]
            if ((classes.get(key) or {}).get("학급") or 0) > 0]


def mixed_age_summary(age_classes, age, compact=False):
    """'3~4세 2학급 + 3~5세 1학급 (합산 정원 68명*)' 형태."""
    found = mixed_classes_for_age(age_classes, age)
    if not found:
        return None
    parts = [f"{key.replace('-', '~')} {v['학급']}학급" for key, v in found]
    cap = sum(v.get("정원") or 0 for _, v in found)
    current = sum(v.get("현원") or 0 for _, v in found)
    if compact:
        return (f"혼합 {sum(v['학급'] for _, v in found)}학급/"
                f"{current}명/{cap}명*")
    return (" + ".join(parts) +
            (f" (합산 현원 {current}명·정원 {cap}명*)" if cap else ""))


def age_classes_table_lines(age_classes):
    """profile/report 용 현재 연령별 학급표."""
    classes = (age_classes or {}).get("학급") or {}
    labels = (("만3세", "만3세"), ("만4세", "만4세"), ("만5세", "만5세"),
              ("3-4세", "만3~4세"), ("4-5세", "만4~5세"),
              ("3-5세", "만3~5세"), ("특수", "특수"))
    lines = ["| 구분 | 학급 | 현원 | 정원 |", "|---|---:|---:|---:|"]
    for key, label in labels:
        v = classes.get(key) or {}
        lines.append(f"| {label} | {v.get('학급') or '-'} | "
                     f"{v.get('현원') or '-'} | {v.get('정원') or '-'} |")
    return lines


def render_web_extras(b, out=None, fresh=False):
    """유치원알리미 웹 공시(원비·시정명령)를 profile 에 붙인다.

    공식 API 가 아니라서 실패할 수 있다 — 실패해도 리포트의 나머지는 그대로 나오고,
    사람이 확인할 수 있게 원본 링크를 남긴다.
    """
    try:
        import kinderweb
    except ImportError:
        print("## 웹 공시 — 연령별 학급·시정명령·원비\n- ⚠ kinderweb.py 를 찾을 수 없습니다. "
              "저장소에서 함께 받아주세요.\n")
        return
    code = b.get("kindercode")
    print("## 웹 공시 — 연령별 학급·시정명령·원비")

    try:
        ac = kinderweb.get_age_classes(code, fresh=fresh)
        if out is not None:
            out["age_classes"] = ac
        print("**현재 연령별 학급 구성**")
        print()
        for line in age_classes_table_lines(ac):
            print(line)
        print()
        print("- ※ 혼합반 정원·현원은 포함 연령 전체의 합계입니다.")
        print(f"- 기준: {ac.get('기준') or '?'} · 원본: {ac['url']}")
    except kinderweb.WebError as e:
        print(f"- ⚠ 연령별 학급 조회 실패: {e}")
        print(f"  - 직접 확인: {kinderweb.page_url('classes', code)}")

    try:
        v = kinderweb.get_violations(code, fresh=fresh)
        if out is not None:
            out["violations"] = v
        if v["clean"]:
            print("- **시정명령·행정처분 이력**: 공시된 이력 없음"
                  + (f" (기준 {v['기준']})" if v.get("기준") else ""))
        else:
            print(f"- **시정명령·행정처분 이력**: ⚠ **{len(v['items'])}건** — 내용 확인 필수")
            for it in v["items"]:
                print(f"  - {it['제목']} | 위반: {it['위반내용']} | "
                      f"조치: {it['조치결과']} ({it['기관']})")
        print(f"  - 원본: {v['url']}")
    except kinderweb.WebError as e:
        print(f"- ⚠ 시정명령 조회 실패: {e}")
        print(f"  - 직접 확인: {kinderweb.page_url('violation', code)}")

    try:
        c = kinderweb.get_costs(code, fresh=fresh)
        if out is not None:
            out["costs"] = c
        for name, key in (("교육과정 원비 합계(월)", "교육과정"),
                          ("방과후 원비 합계(월)", "방과후")):
            line = _cost_total_line(c[key])
            if line:
                print(f"- **{name}**: {line}")
        paid = []
        for tname, table in (("교육과정", c["교육과정"]), ("방과후", c["방과후"])):
            for label, row in table.items():
                if label.startswith(("합계", "소계")):
                    continue
                vals = sorted({v for v in row["금액"].values() if v})
                if not vals:
                    continue
                amt = (f"{vals[0]:,}원" if len(vals) == 1
                       else f"{vals[0]:,}~{vals[-1]:,}원")
                cyc = (f"/{row['결제주기']}"
                       if row.get("결제주기") and row["결제주기"] != "-" else "")
                paid.append(f"{tname} {label} {amt}{cyc}")
        print(f"- **부담금 있는 항목**: {' · '.join(paid) if paid else '없음(전액 학부모 부담 0원)'}")
        sp = c.get("특성화")
        if sp is None:
            print("- 특성화 활동비: 표를 읽지 못했습니다(화면 변경 가능성) — 원본에서 확인")
        elif sp:
            fee = sum(p["월부담금"] or 0 for p in sp)
            note = "전액 무료" if fee == 0 else f"월 부담금 합계 {fee:,}원"
            print(f"- **특성화 활동**: {len(sp)}개 프로그램, {note}")
        print("- ※ 0원 = 과정은 운영하되 학부모 부담 없음 / '-' = 해당 연령 과정 없음")
        print(f"  - 기준: {c.get('기준') or '?'} · 원본: {c['url']}")
    except kinderweb.WebError as e:
        print(f"- ⚠ 원비 조회 실패: {e}")
        print(f"  - 직접 확인: {kinderweb.page_url('cost', code)}")

    try:
        ev = kinderweb.get_evaluation(code, fresh=fresh)
        done = [y["학년도"].replace("학년도", "") for y in ev.get("실시", [])
                if y.get("실시") == "실시"]
        pdfs = len(ev.get("보고서", []))
        line = (f"{len(done)}회 실시 ({', '.join(done[-3:])})"
                if done else "실시 이력 없음")
        print(f"- **유치원 평가**: {line}"
              + (f", 평가결과 PDF {pdfs}건 공시" if pdfs else ""))
        print(f"  - 원본(PDF 열람): {ev['url']}")
    except kinderweb.WebError as e:
        print(f"- ⚠ 유치원 평가 조회 실패: {e}")
        print(f"  - 직접 확인: {kinderweb.page_url('operate', code)}")
    print()


# ------------------------------------------------------- 확장 데이터(선택)
def gather_extended(b, fresh=False):
    """웹 공시·학교알리미·교통·통학버스를 독립적으로 수집한다.

    한 출처가 실패해도 나머지는 유지한다. 각 실패는 error 문자열로 남긴다.
    """
    out = {}
    try:
        import kinderweb
        out["sanitation"] = kinderweb.get_sanitation(b["kindercode"], fresh=fresh)
    except Exception as e:  # noqa: BLE001
        out["sanitation"] = {"error": str(e)}
    try:
        import kinderweb
        out["finance"] = kinderweb.get_finance(b["kindercode"], fresh=fresh)
    except Exception as e:  # noqa: BLE001
        out["finance"] = {"error": str(e)}
    try:
        import schoolbus
        out["bus"] = schoolbus.query(b.get("kindername"), b.get("addr"),
                                      b.get("_sido"), fresh=fresh)
    except Exception as e:  # noqa: BLE001
        out["bus"] = {"error": str(e)}
    if home_coords() and coords_of(b):
        try:
            import traffic_safety
            out["traffic"] = traffic_safety.analyze(
                home_coords(), coords_of(b), fresh=fresh)
        except Exception as e:  # noqa: BLE001
            out["traffic"] = {"error": str(e)}
    if is_annex(b):
        try:
            import schoolinfo
            out["mother_school"] = schoolinfo.context(
                b, get_setting("SCHOOLINFO_API_KEY"),
                year=current_school_year(), fresh=fresh)
        except Exception as e:  # noqa: BLE001
            out["mother_school"] = {"error": str(e)}
    return out


def bus_crosscheck(live, api_row):
    api_operates = bool(api_row and api_row.get("vhcl_oprn_yn") == "Y")
    api_count = to_int(api_row.get("opra_vhcnt")) if api_row else None
    if not isinstance(live, dict) or live.get("error"):
        return "한쪽 출처 조회 불가"
    if live.get("status") in ("not_found", "ambiguous"):
        return "출처 간 불일치" if api_operates else "양쪽 모두 미운영"
    live_count = live.get("vehicle_count")
    if not api_operates and (live_count or 0) > 0:
        return "추가 등록 확인(유치원알리미와 불일치)"
    if api_operates and live_count == api_count:
        return "두 출처 일치"
    if api_operates:
        return "출처 간 차량 수 불일치"
    return "양쪽 모두 미운영"


def extended_lines(b, ext, sections=None):
    """확장 데이터를 짧은 마크다운 문장으로 만든다."""
    lines = []
    san = ext.get("sanitation") or {}
    if san.get("error"):
        lines.append(f"- ⚠ 급식·보건·환경 조회 실패: {san['error']}")
    elif san:
        lines.append(f"- **유치원 보건·환경** ({san.get('기준') or '웹 공시'}):")
        for key in ("식중독", "실내공기질", "소독", "음용수", "미세먼지", "조도"):
            d = san.get(key) or {}
            if d:
                lines.append(f"  - {key}: " + " · ".join(f"{k} {v}" for k, v in d.items()))
        files = san.get("식단표") or []
        lines.append(f"  - 식단표({san.get('식단연월')}): " +
                     (", ".join(f.get("파일명") or "식단표" for f in files) if files
                      else "등록 파일 확인되지 않음"))
        lines.append(f"  - 원본: {san.get('url')}")
    fin = ext.get("finance") or {}
    if fin.get("error"):
        lines.append(f"- ⚠ 재정 조회 실패: {fin['error']}")
    elif fin:
        budgets = fin.get("예산추이") or []
        actuals = fin.get("결산추이") or []
        if budgets:
            lines.append("- **예산 추이(천원)**: " + " → ".join(
                f"{r['연도차수']} {r['예산액천원']:,}" for r in budgets))
        if actuals:
            lines.append("- **결산 추이(천원, 수납/지출)**: " + " → ".join(
                f"{r['연도']} {r['수납액천원']:,}/{r['지출액천원']:,}" for r in actuals))
        lines.append(f"  - 원본: {fin.get('url')}")
    bus = ext.get("bus") or {}
    api_bus = first_row(sections or {}, "schoolBus") if sections else None
    if bus.get("error"):
        lines.append(f"- ⚠ 통학버스 등록현황 조회 실패: {bus['error']}")
    elif bus.get("status"):
        lines.append(f"- **통학버스 교차확인**: {bus_crosscheck(bus, api_bus)} "
                     f"(학교안전지원시스템 {bus.get('status')})")
    elif bus:
        sizes = " · ".join(f"{k} {v}대" for k, v in (bus.get("vehicle_sizes") or {}).items()) or "규모 미상"
        lines.append(f"- **통학버스 등록**: {bus.get('vehicle_count', 0)}대 ({sizes}) · "
                     f"{bus_crosscheck(bus, api_bus)}")
        if bus.get("driver_training"):
            lines.append("  - 운전자 교육: " + " · ".join(
                f"{k} {v}명" for k, v in bus["driver_training"].items()))
        if bus.get("companion_training"):
            lines.append("  - 동승자 교육: " + " · ".join(
                f"{k} {v}명" for k, v in bus["companion_training"].items()))
    tr = ext.get("traffic") or {}
    if tr.get("error"):
        lines.append(f"- ⚠ 자차 경로 교통안전 분석 실패: {tr['error']}")
    elif tr:
        minute_text = f"{tr['minutes']:.1f}분" if tr["minutes"] < 1 else f"{tr['minutes']:.0f}분"
        lines.append(f"- **자차 경로 교통안전**: 도로 {tr['road_km']:.2f}km / {minute_text} · "
                     f"경로상 공식 사고다발지 {len(tr['route_hits'])}곳 · "
                     f"유치원 출입구 포함 {len(tr['entrance_hits'])}곳")
        if tr["route_hits"]:
            for hit in tr["route_hits"][:5]:
                lines.append(f"  - {hit['year']} {hit['name']} ({hit['accidents']}건, {hit['kind']})")
        lines.append(f"  - 자료연도: {min(tr['years'])}~{max(tr['years'])} · "
                     "0건은 안전 판정이 아니라 공식 사고다발지 선정 기준 비해당")
    ms = ext.get("mother_school")
    if isinstance(ms, dict) and ms.get("error"):
        lines.append(f"- ⚠ 모초등학교 정보 조회 실패: {ms['error']}")
    elif ms:
        school = ms["school"]
        lines.append(f"- **모초등학교 보조정보**: {school.get('SCHUL_NM')} ({ms.get('year')}년) "
                     "— 아래 값은 유치원 자체가 아닌 모초등학교 공시")
        meal = (ms.get("meal") or [None])[0]
        if meal:
            lines.append(f"  - 급식: {meal.get('OPER_MET_CODE') or '-'} · 급식 학생 {meal.get('MLSV_STDNT_FGR') or '-'}명 · "
                         f"급식률 {meal.get('KS_RATE') if meal.get('KS_RATE') is not None else '-'}%")
        safety = sorted(ms.get("safety") or [], key=lambda r: str(r.get("CK_YMD") or ""))
        if safety:
            lines.append("  - 시설안전 점검: " + " · ".join(
                f"{fmt_date(r.get('CK_YMD'))} {r.get('CK_RSLT_CODE') or '-'}" for r in safety[-4:]))
    return lines


# ------------------------------------------------------------ cmd: profile
PROFILE_SECTIONS = [
    ("building", None), ("classArea", None), ("lessonDay", None),
    ("teachersInfo", None), ("yearOfWork", None), ("schoolMeal", None),
    ("schoolBus", None), ("safetyEdu", None), ("environmentHygiene", None),
    ("insurance", None), ("deductionSociety", None), ("afterSchoolPresent", None),
]


def gather_sections(b, fresh=False):
    """유치원 1곳의 전 공시 항목 수집. 점검 중/오류는 'DENIED:…'/'ERROR:…' 문자열."""
    sections = {}
    for ep, _ in PROFILE_SECTIONS:
        try:
            sections[ep] = rows_for_kinder(ep, b, fresh=fresh)
        except ApiDenied as e:
            sections[ep] = f"DENIED:{e}"
        except ApiError as e:
            sections[ep] = f"ERROR:{e}"
    return sections


def first_row(sections, ep):
    v = sections.get(ep)
    return v[0] if isinstance(v, list) and v else None


def summary_items(b, sections, hours=None):
    """핵심 요약(파생 지표) 목록. profile 과 report 가 공유한다."""
    yow_row = first_row(sections, "yearOfWork")
    asp_row = first_row(sections, "afterSchoolPresent")
    ca_row = first_row(sections, "classArea")
    safe_row = first_row(sections, "safetyEdu")
    bus_row = first_row(sections, "schoolBus")
    lsn_row = first_row(sections, "lessonDay")
    ts = tenure_stats(yow_row)
    items = [
        ("만3세", f"{to_int(b.get('clcnt3')) or 0}학급 / 원아 "
                  f"{to_int(b.get('ppcnt3')) or 0}명 / 정원 "
                  f"{to_int(b.get('ag3fpcnt')) or 0}명"
                  + (f" (학급당 {per_class(b, 3)}명)" if per_class(b, 3) else "")),
        ("정원 충원율", f"{fill_rate(b)}%" if fill_rate(b) is not None else None),
        ("교사 근속", (f"교사 {ts[0]}명, 추정 평균 {ts[1]}년 "
                      f"(1년 미만 {ts[2]}%, 6년 이상 {ts[3]}%)") if ts else None),
        ("원아 1인당 교실면적",
         f"{area_per_pupil(ca_row, b)}㎡" if area_per_pupil(ca_row, b) else None),
        ("CCTV", (f"총 {safe_row.get('cctv_ist_total')}대 (실내 "
                  f"{safe_row.get('cctv_ist_in')} / 실외 {safe_row.get('cctv_ist_out')})")
         if safe_row and not is_empty(safe_row.get("cctv_ist_total")) else None),
        ("통학차량", (f"운행 (운행 {to_int(bus_row.get('opra_vhcnt'))}대, "
                     f"신고 {to_int(bus_row.get('dclr_vhcnt'))}대)")
         if bus_row and bus_row.get("vhcl_oprn_yn") == "Y"
         else ("미운행" if bus_row else None)),
        ("방과후 참여율",
         f"{afterschool_rate(asp_row, b)}% (참여 "
         f"{afterschool_participants(asp_row)}명)"
         if asp_row and afterschool_rate(asp_row, b) is not None else None),
        ("등원 가능일수", attendance_note(lsn_row, b)),
    ]
    items.extend(hours_summary_items(b, asp_row, hours))
    items.extend([
        ("집에서 직선거리",
         (f"{fmt_km(distance_from_home(b))} (언덕·도로 무시한 하한값)"
          if distance_from_home(b) is not None else None)),
        ("집에서 자차", road_note(b)),
        ("통학차량 관점", bus_note(bus_row)),
    ])
    return items


def cmd_profile(args):
    regions = resolve_region(args.region)
    b = find_kinder(regions, args.name, fresh=args.fresh)
    sections = gather_sections(b, fresh=args.fresh)
    use_web = getattr(args, "web", False) or getattr(args, "extended", False)
    hours = (gather_hours(b, current_school_year(), fresh=args.fresh)
             if use_web else None)
    extended = gather_extended(b, fresh=args.fresh) if getattr(args, "extended", False) else None

    if args.json:
        out = {"basicInfo2": b}
        for ep, v in sections.items():
            out[ep] = v if isinstance(v, list) else {"status": v}
        if use_web:
            try:
                import kinderweb
                out["web"] = {
                    "age_classes": kinderweb.get_age_classes(
                        b["kindercode"], fresh=args.fresh),
                    "violations": kinderweb.get_violations(
                        b["kindercode"], fresh=args.fresh),
                    "costs": kinderweb.get_costs(
                        b["kindercode"], fresh=args.fresh),
                    "evaluation": kinderweb.get_evaluation(
                        b["kindercode"], fresh=args.fresh),
                    "hours": hours,
                }
            except Exception as e:  # noqa: BLE001 — 부가 정보라 본체를 막지 않는다
                out["web"] = {"error": str(e)}
        if extended is not None:
            out["extended"] = extended
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    print(f"# {b['kindername']} 종합 리포트")
    print(f"{b.get('_sgg_name')} · {one_line_summary(b)} · "
          f"{fmt_pbnttmng(b.get('pbnttmng'))} 기준")
    print()
    print("## 핵심 요약(파생 지표)")
    for label, val in summary_items(b, sections, hours=hours):
        if val:
            print(f"- **{label}**: {val}")
    print()

    if roadview_url(b):
        print("## 직접 확인 (정차 여건)")
        print(f"- 로드뷰: {roadview_url(b)}")
        print(f"- 지도: {map_url(b)}")
        print("- 자차 등하원이라면 **유치원 앞에 잠시 세울 수 있는지**가 관건입니다. "
              "이 정보는 어떤 공시·API에도 없으니 로드뷰로 길 폭과 갓길을 직접 보세요.")
        print()

    if use_web:
        render_web_extras(b, fresh=args.fresh)

    if extended is not None:
        print("## 확장 정보 — 보건·재정·교통안전·통학버스")
        for line in extended_lines(b, extended, sections):
            print(line)
        print()

    print("## 기본현황")
    for line in render_kv(b):
        print(line)
    print()

    for ep, _ in PROFILE_SECTIONS:
        v = sections[ep]
        title = ENDPOINTS[ep]
        if isinstance(v, str):
            kind, _, msg = v.partition(":")
            note = "공시 데이터 점검 중" if kind == "DENIED" else "조회 오류"
            print(f"## {title}\n- ⚠ {note}: {msg}\n")
            continue
        if not v:
            print(f"## {title}\n- (데이터 없음)\n")
            continue
        print(f"## {title}")
        if ep == "insurance":
            for r in v:
                nm = r.get("insurance_nm")
                yn = "가입" if r.get("insurance_yn") == "Y" else (
                    "미가입" if r.get("insurance_yn") == "N" else r.get("insurance_yn"))
                comps = ", ".join(str(r[k]) for k in ("company1", "company2", "company3")
                                  if not is_empty(r.get(k)))
                tgt = "" if r.get("insurance_en") == "Y" else " (가입대상 아님)"
                print(f"- {nm}: {yn}{tgt}" + (f" — {comps}" if comps else ""))
        else:
            for r in v:
                for line in render_kv(r):
                    print(line)
        print()
    notify_new_disclosure()


# ------------------------------------------------------------ cmd: compare
def gather_compare_aux(kinders, fresh=False):
    """비교표에 필요한 부가 항목(근속·차량·안전·면적·방과후·수업일수) 수집."""
    aux = {}
    for k in kinders:
        code = k["kindercode"]
        aux[code] = {}
        for ep in ("yearOfWork", "schoolBus", "safetyEdu", "classArea",
                   "afterSchoolPresent", "lessonDay"):
            try:
                rows = rows_for_kinder(ep, k, fresh=fresh)
                aux[code][ep] = rows[0] if rows else None
            except ApiError:
                aux[code][ep] = None
    return aux


def compare_table_lines(kinders, aux, age, extra_rows=(), hours_map=None):
    """비교표 마크다운 줄 목록. compare 와 report 가 공유한다.

    extra_rows: (라벨, fn(kinder)→str|None) 목록 — 주소 행 앞에 끼워 넣는다.
    """
    lines = []

    def metric_row(label, fn):
        cells = " | ".join(md_escape(fn(k) or "-") for k in kinders)
        lines.append(f"| {label} | {cells} |")

    def ten(k):
        ts = tenure_stats(aux[k["kindercode"]].get("yearOfWork"))
        if not ts:
            return None
        return f"평균≈{ts[1]}년 (1년↓ {ts[2]}%, 6년↑ {ts[3]}%)"

    def bus(k):
        r = aux[k["kindercode"]].get("schoolBus")
        if not r:
            return None
        return (f"운행 {to_int(r.get('opra_vhcnt'))}대"
                if r.get("vhcl_oprn_yn") == "Y" else "미운행")

    def cctv(k):
        r = aux[k["kindercode"]].get("safetyEdu")
        if not r or is_empty(r.get("cctv_ist_total")):
            return None
        return (f"{r.get('cctv_ist_total')}대 "
                f"(내{r.get('cctv_ist_in')}/외{r.get('cctv_ist_out')})")

    def afsc(k):
        r = aux[k["kindercode"]].get("afterSchoolPresent")
        rate = afterschool_rate(r, k)
        if rate is None:
            return None
        return f"{rate}% ({afterschool_participants(r)}명)"

    def total_days(k):
        _, total = attendance_days(aux[k["kindercode"]].get("lessonDay"), age)
        return f"{total}일" if total else None

    def vacation_gap(k):
        reg, total = attendance_days(aux[k["kindercode"]].get("lessonDay"), age)
        if not reg or not total:
            return None
        return f"{total - reg}일" + (" (병설)" if is_annex(k) else "")

    def app(k):
        return (f"{area_per_pupil(aux[k['kindercode']].get('classArea'), k)}㎡"
                if area_per_pupil(aux[k["kindercode"]].get("classArea"), k) else None)

    def lesson_days(k):
        r = aux[k["kindercode"]].get("lessonDay")
        if not r:
            return None
        v = to_int(r.get(f"ag{age}_lsn_dcnt"))
        if not v:   # 해당 연령반이 없으면(예: 혼합반만 운영) 0 이 온다
            return None
        blw = r.get("ldnum_blw_yn")
        return f"{v}일" + (" ⚠법정미달" if blw == "Y" else "")

    header = " | ".join(md_escape(k["kindername"]) for k in kinders)
    lines.append(f"| 항목 | {header} |")
    lines.append("|---" * (len(kinders) + 1) + "|")
    metric_row("시군구", lambda k: k.get("_sgg_name"))
    metric_row("설립유형", lambda k: k.get("establish"))
    metric_row("개원일", lambda k: fmt_date(k.get("odate")))
    if hours_map:
        metric_row("공시 전체 운영범위", lambda k: (
            fmt_time_range(((hours_map.get(k["kindercode"]) or {}).get("web") or {})
                           .get("공시전체범위")) or k.get("opertime")))
        metric_row("정규 교육과정", lambda k: hours_value(
            hours_map.get(k["kindercode"]), "education"))
        metric_row("일반 방과후", lambda k: hours_value(
            hours_map.get(k["kindercode"]), "afterschool"))
        metric_row("조기돌봄", lambda k: hours_value(
            hours_map.get(k["kindercode"]), "early_care"))
        metric_row("저녁돌봄", lambda k: hours_value(
            hours_map.get(k["kindercode"]), "late_care"))
    else:
        metric_row("공시 전체 운영범위", lambda k: k.get("opertime"))
    metric_row(f"만{age}세 학급수", lambda k: to_int(k.get(f"clcnt{age}")))
    metric_row(f"만{age}세 원아/정원",
               lambda k: (f"{to_int(k.get(f'ppcnt{age}')) or 0}/"
                          f"{to_int(k.get(f'ag{age}fpcnt')) or 0}명"))
    metric_row(f"만{age}세 학급당 원아", lambda k: per_class(k, age))
    metric_row("혼합연령 학급수", lambda k: to_int(k.get("mixclcnt")))
    metric_row("전체 원아/정원",
               lambda k: f"{total_pupils(k)}/{to_int(k.get('prmstfcnt'))}명")
    metric_row("충원율", lambda k: f"{fill_rate(k)}%" if fill_rate(k) is not None else None)
    metric_row("교사 근속", ten)
    metric_row(f"만{age}세 정규 수업일수", lesson_days)
    metric_row("방과후 포함 운영일수", total_days)
    metric_row("방과후 의존일수(방학 등)", vacation_gap)
    metric_row("원아 1인당 교실면적", app)
    metric_row("CCTV", cctv)
    metric_row("통학차량", bus)
    metric_row("방과후 참여율", afsc)
    if home_coords():
        metric_row("집에서 직선거리", lambda k: fmt_km(distance_from_home(k)))
        metric_row("집에서 자차(도로)", road_note)
    metric_row("통학차량 관점",
               lambda k: (bus_note(aux[k["kindercode"]].get("schoolBus")) or "")
               .replace("**", ""))
    for label, fn in extra_rows:
        metric_row(label, fn)
    metric_row("주소", lambda k: k.get("addr"))
    metric_row("전화", lambda k: k.get("telno"))
    metric_row("로드뷰(정차 여건 확인)", roadview_url)
    return lines


def cmd_compare(args):
    age, target_note = resolve_search_age(args)
    age = age or 3   # 비교표는 기준 연령이 하나 필요
    regions = resolve_region(args.region)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if len(names) < 2:
        sys.exit("[오류] compare 는 쉼표로 구분한 2곳 이상이 필요합니다.")
    kinders = [find_kinder(regions, n, fresh=args.fresh) for n in names]
    aux = gather_compare_aux(kinders, fresh=args.fresh)
    school_year = requested_school_year(args)
    hours_map = {k["kindercode"]: gather_hours(
        k, school_year, fresh=args.fresh) for k in kinders}

    print(f"# 유치원 비교 ({fmt_pbnttmng(kinders[0].get('pbnttmng'))} 기준)")
    if target_note:
        print(f"대상: {target_note}")
    print()
    for line in compare_table_lines(kinders, aux, age, hours_map=hours_map):
        print(line)
    print()
    print("근속 평균은 공시 구간(1년 미만~6년 이상)의 중간값 가중 추정치입니다. "
          "교직원 수·급식 항목은 현재 공시 점검 중이면 표시되지 않을 수 있습니다.")
    notify_new_disclosure()


# ---------------------------------------------------------- 후보(shortlist)
SHORTLIST_FILE = ROOT / "shortlist.json"


def load_shortlist():
    if SHORTLIST_FILE.exists():
        try:
            sl = json.loads(SHORTLIST_FILE.read_text(encoding="utf-8"))
            if sl.get("region") and sl.get("names"):
                return sl
        except ValueError:
            pass
    return None


def region_names_or_shortlist(args):
    """(지역, 이름들) 반환. 인자를 안 주면 pick 으로 저장한 후보를 쓴다."""
    if getattr(args, "region", None) and getattr(args, "names", None):
        return args.region, args.names
    sl = load_shortlist()
    if sl:
        print(f"[후보 사용] {sl['region']} — {', '.join(sl['names'])}",
              file=sys.stderr)
        return sl["region"], ",".join(sl["names"])
    sys.exit("[오류] 지역과 유치원명을 함께 주거나, 먼저 후보를 저장하세요.\n"
             '  예: python kinderinfo.py pick "서울 강남구" "가나,다라,마바"')


def cmd_pick(args):
    if args.clear:
        if SHORTLIST_FILE.exists():
            SHORTLIST_FILE.unlink()
        print("후보를 비웠습니다.")
        return
    if args.show or not args.region:
        sl = load_shortlist()
        print(f"후보: {sl['region']} — {', '.join(sl['names'])} "
              f"(저장 {sl.get('saved', '?')})" if sl else
              "저장된 후보가 없습니다. 예: python kinderinfo.py pick "
              '"서울 강남구" "가나,다라"')
        return
    if not args.names:
        sys.exit('[오류] 유치원명들을 쉼표로 주세요. 예: pick "서울 강남구" "가나,다라"')
    regions = resolve_region(args.region)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    kinders = [find_kinder(regions, n) for n in names]   # 검증 겸 정식 명칭 확보
    sl = {"region": args.region,
          "names": [k["kindername"] for k in kinders],
          "saved": datetime.now().strftime("%Y-%m-%d")}
    SHORTLIST_FILE.write_text(json.dumps(sl, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"후보 {len(kinders)}곳 저장: {', '.join(sl['names'])}")
    print("이제 report / trend / diff 를 인자 없이 쓸 수 있습니다.")


# ------------------------------------------------------- cmd: trend / diff
def load_bulk():
    try:
        import kinderbulk
    except ImportError:
        sys.exit("[오류] kinderbulk.py 를 찾을 수 없습니다. 저장소에서 함께 받아주세요.")
    return kinderbulk


def gather_series(kinders, periods=5):
    """유치원별 차수 시리즈. 실패한 유치원은 {'error': …} 로 표시."""
    kb = load_bulk()
    timings = kb.recent_timings(periods)
    out = {}
    for k in kinders:
        try:
            out[k["kindercode"]] = kb.series(
                k["_sido"], k["kindername"], k.get("addr"), timings)
        except Exception as e:  # noqa: BLE001 — 한 곳 실패가 전체를 막지 않게
            out[k["kindercode"]] = {"error": str(e)}
    return out, timings


def trend_lines(ser):
    """유치원 1곳의 추이 마크다운 줄들. trend 명령과 report 카드가 공유."""
    kb = load_bulk()
    if not isinstance(ser, list):
        return [f"- ⚠ 추이 조회 실패: {(ser or {}).get('error', '?')}"]
    lines = ["| 차수 | 충원율 | 만3세 전용반 원아 | 혼합반 원아 | 전체 원아/정원 | 근속 1년 미만 |",
             "|---|---|---|---|---|---|"]
    for r in ser:
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            r["label"],
            f"{r['충원율']}%" if r.get("충원율") is not None else "-",
            f"{r['원아3']}명" if r.get("원아3") is not None else "-",
            f"{r['혼합원아']}명" if r.get("혼합원아") is not None else "-",
            f"{r['원아']}/{r['정원']}" if r.get("원아") is not None else "-",
            f"{r['근속1년미만']}%" if r.get("근속1년미만") is not None else "-"))
    fills = [r.get("충원율") for r in ser if r.get("충원율") is not None]
    tens = [r.get("근속1년미만") for r in ser if r.get("근속1년미만") is not None]
    marks = []
    if len(fills) >= 2:
        marks.append("충원율 " + kb.direction(fills[0], fills[-1]))
    if len(tens) >= 2:
        marks.append("근속 1년 미만 " + kb.direction(tens[0], tens[-1]))
    if marks:
        lines += ["", "추세: " + " · ".join(marks)]
    return lines


def cmd_trend(args):
    region, names_str = region_names_or_shortlist(args)
    regions = resolve_region(region)
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    kinders = [find_kinder(regions, n, fresh=args.fresh) for n in names]
    ser_map, timings = gather_series(kinders, args.periods)
    if args.json:
        print(json.dumps({k["kindername"]: ser_map[k["kindercode"]]
                          for k in kinders}, ensure_ascii=False, indent=1))
        return
    kb = load_bulk()
    print(f"# 후보 추이 — {kb.timing_label(timings[0])} ~ "
          f"{kb.timing_label(timings[-1])}")
    for k in kinders:
        print(f"\n## {k['kindername']} ({k.get('establish')})")
        for line in trend_lines(ser_map[k["kindercode"]]):
            print(line)
    print("\n화살표는 첫 차수와 끝 차수의 단순 비교이며 ±2 이상일 때만 방향을 "
          "표시합니다. '만3세 전용반 원아'가 0명이어도 혼합반에 만3세가 포함될 수 "
          "있습니다. 혼합반 원아는 모든 포함 연령의 합계이며, 현재 구성은 profile --web로 "
          "확인하세요. "
          "과거 차수 자료는 최초 1회만 내려받아 영구 보관합니다.")


def cmd_diff(args):
    region, names_str = region_names_or_shortlist(args)
    regions = resolve_region(region)
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    kinders = [find_kinder(regions, n, fresh=args.fresh) for n in names]
    ser_map, timings = gather_series(kinders, 2)
    kb = load_bulk()
    print(f"# 후보 변화 — {kb.timing_label(timings[0])} → "
          f"{kb.timing_label(timings[1])}")
    for k in kinders:
        ser = ser_map[k["kindercode"]]
        print(f"\n## {k['kindername']}")
        if not isinstance(ser, list):
            print(f"- ⚠ 조회 실패: {(ser or {}).get('error', '?')}")
            continue
        prev, cur = ser[0], ser[1]
        changes, same = [], []
        for label, key, unit in (("충원율", "충원율", "%p"),
                                 ("만3세 전용반 원아", "원아3", "명"),
                                 ("혼합반 원아", "혼합원아", "명"),
                                 ("전체 원아", "원아", "명"),
                                 ("정원", "정원", "명"),
                                 ("근속 1년 미만", "근속1년미만", "%p")):
            p, c = prev.get(key), cur.get(key)
            if p is None and c is None:
                continue
            if p is None or c is None:
                changes.append(f"{label}: {'-' if p is None else p} → "
                               f"{'-' if c is None else c}")
            elif p != c:
                d = c - p
                changes.append(f"{label}: {p} → {c} ({'+' if d > 0 else ''}{d}{unit})")
            else:
                same.append(label)
        for ch in changes:
            print(f"- {ch}")
        if not changes:
            print("- 변화 없음")
        elif same:
            print(f"- (변화 없음: {', '.join(same)})")
    print("\n새 공시 직후에 쓰면 후보들의 변동을 한눈에 봅니다. 큰 변동이 있으면 "
          "trend 로 흐름을, profile 로 상세를 확인하세요.")


# ------------------------------------------------------------- cmd: report
VISIT_COMMON_QUESTIONS = [
    "급식은 직영인가요? 조리 인력과 식단 구성은 어떻게 되나요? "
    "(급식 공시가 현재 점검 중이라 직접 확인 필요)",
    "모집 인원과 추첨·대기 순번 방식은 어떻게 되나요?",
    "학부모 참관·상담은 어떤 방식으로 하나요?",
]


def visit_questions(b, sections, web, sched, age):
    """공시 데이터의 이상 신호를 방문·전화 질문으로 바꾼다. 규칙 기반."""
    qs = []
    asp = first_row(sections, "afterSchoolPresent")
    bus = first_row(sections, "schoolBus")
    safe = first_row(sections, "safetyEdu")
    lsn = first_row(sections, "lessonDay")

    if is_annex(b):
        vac = (sum(d for *_, d in sched["vacs"])
               if sched and sched.get("vacs") else None)
        days = f"약 {vac}일" if vac else "긴"
        staff = ""
        if asp:
            staff = (f" (공시상 방과후 전담: 정규 {to_int(asp.get('fxrl_thcnt')) or 0}명·"
                     f"단시간 {to_int(asp.get('shcnt_thcnt')) or 0}명·"
                     f"강사 {to_int(asp.get('cce_tcr_cnt')) or 0}명)")
        qs.append(f"방학({days}) 동안 방과후 운영 시간과 담당 인력은 "
                  f"어떻게 되나요?{staff}")

    if (to_int(b.get(f"clcnt{age}")) or 0) == 0:
        year = next_admission_year()
        age_classes = ((web or {}).get("age_classes")
                       if isinstance(web, dict) else None)
        mixed = (mixed_age_summary(age_classes, age)
                 if isinstance(age_classes, dict) else None)
        if mixed:
            qs.append(f"현재 만{age}세는 {mixed}로 운영되는데, {year}학년도 "
                      f"만{age}세 모집 인원과 담임 배치는 어떻게 되나요?")
        elif isinstance(age_classes, dict):
            qs.append(f"현재 공시 학급 구성에는 만{age}세가 포함되지 않는데, "
                      f"{year}학년도 만{age}세 모집 계획이 있나요?")
        elif (to_int(b.get("mixclcnt")) or 0) > 0:
            qs.append(f"{year}학년도에 만{age}세를 모집하나요? "
                      f"혼합반이라면 연령 구성이 어떻게 되나요?")
        else:
            qs.append(f"공시에 만{age}세 반이 없는데, {year}학년도 만{age}세 "
                      f"모집 계획이 있나요?")

    ts = tenure_stats(first_row(sections, "yearOfWork"))
    if ts and ts[2] >= 30:
        qs.append(f"교사 근속 1년 미만이 {ts[2]}%로 공시돼 있는데, "
                  f"최근 교사 변동이 있었나요?")

    fr = fill_rate(b)
    if fr is not None and fr <= 40:
        qs.append(f"정원 대비 원아가 {fr}% 수준인데 특별한 이유가 있나요?")

    _km, _mins, ratio = road_from_home(b)
    if ratio and ratio >= 2.0:
        qs.append(f"직선거리보다 도로가 {ratio:.1f}배 돌아갑니다"
                  f"({_km:.1f}km, 약 {_mins:.0f}분). 등하원 동선이 어떻게 되나요? "
                  f"원 앞 진입로가 일방통행인가요?")

    if bus and bus.get("vhcl_oprn_yn") == "Y":
        qs.append("통학차량 노선이 저희 동네를 지나나요? 차량에 동승 보호자가 있나요?")
    elif bus:
        qs.append("(방문 전) 로드뷰로 원 앞 정차 공간을 보고, 등하원 시간대 "
                  "차량 흐름을 물어보세요.")

    if web and isinstance(web.get("costs"), dict):
        annual = []
        for key in ("교육과정", "방과후"):
            for label, row in (web["costs"].get(key) or {}).items():
                if label.startswith(("합계", "소계")):
                    continue
                cyc = row.get("결제주기") or ""
                if cyc and cyc not in ("-", "월단위") and any(row["금액"].values()):
                    annual.append(f"{label}({cyc})")
        if annual:
            qs.append(f"{', '.join(annual)} 같은 비월단위 항목까지 포함하면 "
                      f"연간 총 부담액이 얼마인가요?")

    if (safe and safe.get("cctv_ist_yn") == "Y"
            and (to_int(safe.get("cctv_ist_out")) or 0) == 0):
        qs.append("실외 CCTV가 0대로 공시돼 있는데, 바깥 놀이 공간 안전 관리는 "
                  "어떻게 하나요?")

    ar = afterschool_rate(asp, b)
    if ar is not None and ar < 50:
        qs.append(f"방과후과정 참여율이 {ar}%인데, 오후 돌봄 운영 규모가 "
                  f"실제로 어느 정도인가요?")

    if lsn and lsn.get("ldnum_blw_yn") == "Y":
        qs.append("법정 수업일수 미달로 공시돼 있는데 사유가 무엇인가요?")

    if (web and isinstance(web.get("violations"), dict)
            and not web["violations"].get("clean", True)):
        qs.insert(0, "공시된 시정명령·행정처분 이력의 경위와 이후 개선 조치를 "
                     "설명해 주실 수 있나요?")

    return qs + VISIT_COMMON_QUESTIONS


def cmd_report(args):
    age, target_note = resolve_search_age(args)
    age = age or 3
    school_year = requested_school_year(args)
    region, names_str = region_names_or_shortlist(args)
    regions = resolve_region(region)
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    if not 1 <= len(names) <= 4:
        sys.exit("[오류] report 는 쉼표로 구분한 유치원 1~4곳을 받습니다.")
    kinders = [find_kinder(regions, n, fresh=args.fresh) for n in names]
    aux = gather_compare_aux(kinders, fresh=args.fresh)

    per = {}
    for k in kinders:
        code = k["kindercode"]
        info = {"sections": gather_sections(k, fresh=args.fresh),
                "web": None, "sched": None, "hours": None, "extended": None}
        if not args.no_web:
            web = {}
            try:
                import kinderweb
                web["age_classes"] = kinderweb.get_age_classes(
                    code, fresh=args.fresh)
                web["violations"] = kinderweb.get_violations(
                    code, fresh=args.fresh)
                web["costs"] = kinderweb.get_costs(code, fresh=args.fresh)
                try:
                    web["eval"] = kinderweb.get_evaluation(
                        code, fresh=args.fresh)
                except Exception:  # noqa: BLE001 — 평가는 곁가지
                    pass
            except Exception as e:  # noqa: BLE001 — 부가 정보
                web["error"] = str(e)
            info["web"] = web
            info["hours"] = gather_hours(k, school_year, fresh=args.fresh)
        if is_annex(k) and get_setting("NEIS_API_KEY"):
            try:
                school, vacs, _events, yr = school_schedule(k, quiet=True)
                if school:
                    info["sched"] = {"school": school["SCHUL_NM"],
                                     "vacs": vacs, "year": yr}
            except Exception:  # noqa: BLE001 — 부가 정보
                pass
        if getattr(args, "extended", False):
            info["extended"] = gather_extended(k, fresh=args.fresh)
        per[code] = info

    trend_map = {}
    if not args.no_trend:
        try:
            trend_map, _ = gather_series(kinders, 5)
        except Exception as e:  # noqa: BLE001 — 추이는 곁가지
            print(f"[안내] 추이 조회 실패({e}) — 추이 없이 계속합니다.",
                  file=sys.stderr)

    L = []
    w = L.append
    today = datetime.now().strftime("%Y-%m-%d")
    w(f"# 유치원 후보 브리핑 — {', '.join(k['kindername'] for k in kinders)}")
    head = f"{today} 생성 · {fmt_pbnttmng(kinders[0].get('pbnttmng'))} 기준"
    if target_note:
        head += f" · 대상: {target_note}"
    w(head)
    w("")

    def _web_of(k):
        return per[k["kindercode"]].get("web") or {}

    def fee_cell(k):
        c = _web_of(k).get("costs")
        if not isinstance(c, dict):
            return None
        total = 0
        for key in ("교육과정", "방과후"):
            t = next((v for kk, v in (c.get(key) or {}).items()
                      if kk.startswith("합계")), None)
            if t:
                total += t["금액"].get(age) or 0
        return f"월 {total:,}원"

    def violation_cell(k):
        v = _web_of(k).get("violations")
        if not isinstance(v, dict):
            return None
        return "없음" if v.get("clean") else f"⚠ {len(v.get('items', []))}건"

    def vacation_cell(k):
        s = per[k["kindercode"]].get("sched")
        if not s or not s.get("vacs"):
            return None
        return f"{sum(d for *_, d in s['vacs'])}일"

    def eval_cell(k):
        e = _web_of(k).get("eval")
        if not isinstance(e, dict):
            return None
        done = [y["학년도"].replace("학년도", "") for y in e.get("실시", [])
                if y.get("실시") == "실시"]
        return (f"{len(done)}회 실시 ({', '.join(done[-3:])})"
                if done else "실시 이력 없음")

    def mixed_cell(k):
        ac = _web_of(k).get("age_classes")
        if not isinstance(ac, dict):
            return None
        return mixed_age_summary(ac, age) or "없음"

    def fill_trend_cell(k):
        ser = trend_map.get(k["kindercode"])
        if not isinstance(ser, list):
            return None
        vals = [r.get("충원율") for r in ser]
        got = [v for v in vals if v is not None]
        if not got:
            return None
        arrow = ""
        if len(got) >= 2:
            d = got[-1] - got[0]
            arrow = " ↗" if d >= 2 else (" ↘" if d <= -2 else " →")
        return "→".join("-" if v is None else str(v) for v in vals) + f"%{arrow}"

    extra = []
    if not args.no_web:
        extra += [(f"만{age}세 포함 혼합반", mixed_cell),
                  (f"원비 합계(만{age}세, 월)", fee_cell),
                  ("시정명령 이력", violation_cell),
                  ("유치원 평가", eval_cell)]
    extra.append(("방학 합계(모초교 실측)", vacation_cell))
    if trend_map:
        extra.append(("충원율 추이(5개 차수)", fill_trend_cell))
    if getattr(args, "extended", False):
        def traffic_cell(k):
            tr = (per[k["kindercode"]].get("extended") or {}).get("traffic") or {}
            if not tr or tr.get("error"):
                return None
            minute_text = (f"{tr['minutes']:.1f}분" if tr["minutes"] < 1
                           else f"{tr['minutes']:.0f}분")
            return (f"{tr['road_km']:.2f}km/{minute_text} · "
                    f"경로 {len(tr['route_hits'])}곳 · 출입구 {len(tr['entrance_hits'])}곳")

        def bus_cell(k):
            info = per[k["kindercode"]]
            bus = (info.get("extended") or {}).get("bus") or {}
            return bus_crosscheck(bus, first_row(info["sections"], "schoolBus")) if bus else None

        extra += [("자차 경로 공식 사고다발지", traffic_cell),
                  ("통학버스 출처 교차확인", bus_cell)]

    w("## 1. 한눈 비교")
    w("")
    hours_map = {k["kindercode"]: per[k["kindercode"]].get("hours")
                 for k in kinders if per[k["kindercode"]].get("hours")}
    L.extend(compare_table_lines(kinders, aux, age, extra_rows=extra,
                                 hours_map=hours_map or None))
    w("")

    w("## 2. 유치원별 상세" + ("와 확인 질문" if args.questions else ""))
    for i, k in enumerate(kinders, 1):
        info = per[k["kindercode"]]
        w("")
        w(f"### {i}. {k['kindername']} — {k.get('establish')} · {k.get('addr')}")
        w("")
        for label, val in summary_items(k, info["sections"],
                                        hours=info.get("hours")):
            if val:
                w(f"- **{label}**: {val}")
        web = info.get("web")
        if web:
            if "error" in web:
                w(f"- ⚠ 웹 공시 조회 실패: {web['error']}")
            else:
                v = web.get("violations")
                if isinstance(v, dict):
                    w("- **시정명령 이력**: "
                      + ("없음" if v.get("clean") else
                         f"⚠ {len(v['items'])}건 — "
                         + " / ".join(it["제목"] for it in v["items"])))
                ac = web.get("age_classes")
                if isinstance(ac, dict):
                    w("**현재 연령별 학급 구성**")
                    w("")
                    for line in age_classes_table_lines(ac):
                        w(line)
                    w("")
                    w("- ※ 혼합반 정원·현원은 포함 연령 전체의 합계입니다.")
                fee = fee_cell(k)
                if fee:
                    w(f"- **원비(만{age}세)**: {fee} — 상세: {web['costs']['url']}")
                ev = web.get("eval")
                if isinstance(ev, dict):
                    pdfs = len(ev.get("보고서", []))
                    w(f"- **유치원 평가**: {eval_cell(k)}"
                      + (f", 평가결과 PDF {pdfs}건 공시" if pdfs else "")
                      + f" — 열람: {ev['url']}")
        s = info.get("sched")
        if s and s.get("vacs"):
            vv = " · ".join(f"{n} {d}일" for n, _s, _e, d in s["vacs"])
            w(f"- **방학({s['year']}학년도, {s['school']} 기준)**: {vv}")
        if roadview_url(k):
            w(f"- 로드뷰(정차 여건): {roadview_url(k)}")
        if k.get("telno"):
            w(f"- 전화: {k['telno']}")
        if info.get("extended"):
            w("")
            w("**확장 정보**")
            for line in extended_lines(k, info["extended"], info["sections"]):
                w(line)
        ser = trend_map.get(k["kindercode"])
        if isinstance(ser, list):
            w("")
            w("**추이 (최근 5개 차수)**")
            w("")
            for line in trend_lines(ser):
                w(line)
        if args.questions:
            w("")
            w("**요청한 확인 질문 목록**")
            for q in visit_questions(k, info["sections"], web, s, age):
                w(f"- [ ] {q}")

    yr = next_admission_year()
    w("")
    w("## 3. 일정과 유의사항")
    w(f"- **처음학교로 접수는 {yr - 1}년 11월경**입니다. 1~3지망, 최대 3곳까지 "
      f"지원할 수 있습니다.")
    w(f"- 공시는 연 2회 갱신됩니다. **{yr - 1}년 10월 말 2차 공시가 뜨면 "
      f"`python kinderinfo.py refresh` 후 이 브리핑을 다시 만드세요.**")
    w("- 원장·교사의 태도, 교실 분위기, 아이와의 궁합은 공시 데이터에 없습니다.")
    w("- 근속 평균은 공시 구간의 중간값 가중 추정치이고, 방학은 모초등학교 기준 근사치입니다.")
    if getattr(args, "extended", False):
        w("- 교통안전 분석은 OSRM 자차 경로와 도로교통공단 공식 사고다발지 폴리곤의 "
          "교차 결과입니다. 실시간 교통은 반영하지 않으며 0건은 안전 판정이 아닙니다.")

    text = "\n".join(L)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"저장했습니다: {args.out} ({len(text):,}자)")
    else:
        print(text)


# -------------------------------------------------------------- cmd: hours
def hours_source_label(info):
    """운영시간 정보의 가장 강한 근거를 짧게 표시."""
    verified = (info or {}).get("verified") or {}
    if verified:
        src = verified.get("source") or {}
        year = verified.get("school_year")
        title = src.get("title") or "공식 계획서"
        return f"{title} 검증" if str(title).startswith(str(year)) else f"{year} {title} 검증"
    web = (info or {}).get("web") or {}
    if web:
        return f"{web.get('기준') or '웹 공시'} (세부 분리 전)"
    return None


def cmd_hours(args):
    """후보들의 실질 운영시간을 출처와 학년도까지 구분해 비교한다."""
    region, names_str = region_names_or_shortlist(args)
    regions = resolve_region(region)
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    if not 1 <= len(names) <= 6:
        sys.exit("[오류] hours 는 쉼표로 구분한 유치원 1~6곳을 받습니다.")
    school_year = requested_school_year(args)
    kinders = [find_kinder(regions, n, fresh=args.fresh) for n in names]
    infos = {k["kindercode"]: gather_hours(
        k, school_year, fresh=args.fresh) for k in kinders}

    if args.json:
        print(json.dumps({
            "requested_school_year": school_year,
            "kindergartens": [{"name": k["kindername"],
                                "kindercode": k["kindercode"],
                                "hours": infos[k["kindercode"]]}
                               for k in kinders],
        }, ensure_ascii=False, indent=1))
        return

    print(f"# 후보 유치원 실질 운영시간 — {school_year}학년도 기준")
    print()
    header = " | ".join(md_escape(k["kindername"]) for k in kinders)
    print(f"| 항목 | {header} |")
    print("|---" * (len(kinders) + 1) + "|")

    def row(label, fn):
        cells = " | ".join(md_escape(fn(infos[k["kindercode"]]) or "-")
                           for k in kinders)
        print(f"| {label} | {cells} |")

    row("공시 전체 운영범위", lambda x: fmt_time_range(
        ((x.get("web") or {}).get("공시전체범위"))))
    row("정규 교육과정", lambda x: hours_value(x, "education"))
    row("일반 방과후", lambda x: hours_value(x, "afterschool"))
    row("조기돌봄", lambda x: hours_value(x, "early_care"))
    row("저녁돌봄", lambda x: hours_value(x, "late_care"))
    row("방학 중 운영", lambda x: hours_value(x, "vacation_hours"))
    row("방학 미운영", lambda x: " · ".join(
        ((x.get("verified") or {}).get("vacation_closures") or [])))
    row("이용 조건·제약", lambda x: " · ".join(
        ((x.get("verified") or {}).get("conditions") or [])))
    row("근거", hours_source_label)

    reference_years = sorted({
        (info.get("verified") or {}).get("school_year")
        for info in infos.values() if (info.get("verified") or {}).get("school_year")
    })
    print()
    if reference_years and school_year not in reference_years:
        print(f"⚠ {school_year}학년도 계획은 아직 확정·공개 전이어서 "
              f"{', '.join(map(str, reference_years))}학년도 공식 계획서를 참고값으로 "
              "표시했습니다. 학년도가 다르면 운영시간과 조건이 바뀔 수 있습니다.")
    print("※ '공시 전체 운영범위'는 정규수업만의 시간이 아니라 조기·방과후·"
          "저녁돌봄까지 합친 가장 이른 시작~가장 늦은 종료 범위입니다.")
    print("※ '-'는 해당 운영이 없다는 뜻이 아니라, 현재 확보한 공식 자료에서 "
          "세부 시간을 분리 확인하지 못했다는 뜻입니다.")


# ----------------------------------------------------------- cmd: schedule
def load_neis():
    """NEIS 모듈을 지연 로드한다. 없어도 다른 기능은 멀쩡히 돌아야 한다."""
    try:
        import neis
    except ImportError:
        sys.exit("[오류] neis.py 를 찾을 수 없습니다. 저장소에서 함께 받아주세요.")
    return neis


def school_schedule(kinder, year=None, quiet=False):
    """병설유치원의 모초등학교 학사일정. (학교정보, 방학목록, 행사목록, 학년도)"""
    neis = load_neis()
    key = get_setting("NEIS_API_KEY")
    school = neis.find_school(key, kinder.get("kindername"),
                              kinder.get("_sido"), kinder.get("addr"))
    if not school:
        return None, [], [], None
    year = year or neis.current_school_year()
    frm, to = neis.school_year_range(year)
    rows = neis.fetch_schedule(key, school["ATPT_OFCDC_SC_CODE"],
                               school["SD_SCHUL_CODE"], frm, to)
    if not quiet:
        print(f"  [NEIS] {school['SCHUL_NM']} {year}학년도 일정 {len(rows)}건",
              file=sys.stderr)
    return school, neis.vacation_periods(rows), neis.key_events(rows), year


def cmd_schedule(args):
    neis = load_neis()
    regions = resolve_region(args.region)
    b = find_kinder(regions, args.name, fresh=args.fresh)

    if not neis.school_name_of(b.get("kindername")):
        print(f"# {b['kindername']}")
        print()
        print("이 유치원은 **초등학교 병설이 아닙니다.** NEIS에는 초·중·고만 있고 "
              "사립·단설 유치원의 학사일정은 공시되지 않습니다.")
        print(f"\n공시 기준 정규 수업일수: "
              f"{attendance_note(rows_for_kinder('lessonDay', b)[0] if rows_for_kinder('lessonDay', b) else None, b)}")
        print("\n사립·단설 유치원의 확정 방학 일정은 공개 데이터에 없습니다.")
        return

    try:
        school, vacs, events, year = school_schedule(b, year=args.year)
    except neis.NeisKeyMissing as e:
        sys.exit(f"[오류] {e}")
    except neis.NeisError as e:
        sys.exit(f"[오류] {e}")

    if not school:
        sys.exit(f"[오류] '{b['kindername']}'의 모초등학교를 NEIS에서 찾지 못했습니다. "
                 f"학교명이 특이하거나 통폐합된 경우일 수 있습니다.")

    if args.json:
        print(json.dumps({
            "kindergarten": b["kindername"], "school": school, "year": year,
            "vacations": [{"name": n, "start": s.isoformat(),
                           "end": e.isoformat(), "days": d} for n, s, e, d in vacs],
            "events": [{"date": d.isoformat(), "name": n} for d, n in events],
        }, ensure_ascii=False, indent=1))
        return

    print(f"# {b['kindername']} — 방학·학사일정")
    print(f"모초등학교: {school['SCHUL_NM']} ({school.get('ORG_RDNMA', '').strip()})")
    print(f"{year}학년도 기준")
    print()

    if vacs:
        print("## 방학")
        total = 0
        for name, start, end, days in vacs:
            total += days
            span = (f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}" if days > 1
                    else f"{start:%Y-%m-%d}")
            print(f"- **{name}**: {span} ({days}일)")
        print(f"\n→ 방학 합계 **{total}일**")
    else:
        print("## 방학\n- (해당 학년도 방학 일정이 아직 공시되지 않았습니다)")
    print()

    if events:
        print("## 주요 행사")
        for d, name in events:
            print(f"- {d:%Y-%m-%d} {name}")
        print()

    try:
        lsn = rows_for_kinder("lessonDay", b)
        note = attendance_note(lsn[0] if lsn else None, b)
    except ApiError:
        note = None
    print("## 함께 볼 것")
    if note:
        print(f"- 공시 기준 {note}")
    print("- **위 일정은 초등학교 것입니다.** 병설유치원은 대체로 이를 따르지만 "
          "정확히 같지는 않습니다(유치원 법정 수업일수 180일, 초등학교 190일).")
    print("- 방학 중에도 방과후과정은 운영되는 것이 보통이나, **누가 돌보는지**는 "
          "유치원마다 다릅니다. profile 의 방과후 전담교사 인원을 함께 보세요.")
    print("- 위 결과는 모초등학교 일정에 따른 참고값이며 유치원 확정 일정은 아닙니다.")

    if args.meals:
        today = datetime.now()
        frm = today.strftime("%Y%m01")
        to = today.strftime("%Y%m%d")
        try:
            meals = neis.fetch_meals(get_setting("NEIS_API_KEY"),
                                     school["ATPT_OFCDC_SC_CODE"],
                                     school["SD_SCHUL_CODE"], frm, to)
        except neis.NeisError as e:
            meals = []
            print(f"\n(급식 조회 실패: {e})")
        if meals:
            print(f"\n## 급식 식단 (모초등학교, 최근 {len(meals)}일)")
            for m in meals[-10:]:
                print(f"- {fmt_date(m['date'])} {m['type']}: {m['menu'][:110]}"
                      + (f" ({m['kcal']})" if m['kcal'] else ""))


# --------------------------------------------------------- cmd: 확장 출처
def _one_kinder(args):
    return find_kinder(resolve_region(args.region), args.name, fresh=args.fresh)


def cmd_health(args):
    b = _one_kinder(args)
    import kinderweb
    data = kinderweb.get_sanitation(b["kindercode"], year=args.year,
                                     month=args.month, fresh=args.fresh)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1)); return
    print(f"# {b['kindername']} 급식·보건·환경")
    print(f"웹 공시 기준: {data.get('기준') or '-'}\n")
    for key in ("식중독", "실내공기질", "소독", "음용수", "미세먼지", "조도"):
        d = data.get(key) or {}
        if d:
            print(f"- **{key}**: " + " · ".join(f"{k} {v}" for k, v in d.items()))
    files = data.get("식단표") or []
    print(f"- **식단표({data.get('식단연월')})**: " +
          (", ".join(f.get("파일명") or "식단표" for f in files) if files
           else "등록 파일 확인되지 않음"))
    print(f"- 원본: {data['url']}")


def cmd_finance(args):
    b = _one_kinder(args)
    import kinderweb
    data = kinderweb.get_finance(b["kindercode"], fresh=args.fresh)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1)); return
    print(f"# {b['kindername']} 예산·결산 추이")
    print("※ 단위는 천원이며 재정 규모 자체를 교육 품질 점수로 환산하지 않습니다.\n")
    print("| 구분 | 연도/차수 | 금액 |\n|---|---:|---:|")
    for r in data.get("예산추이", []):
        print(f"| 예산 | {r['연도차수']} | {r['예산액천원']:,} |")
    for r in data.get("결산추이", []):
        print(f"| 결산(수납/지출) | {r['연도']} | {r['수납액천원']:,} / {r['지출액천원']:,} |")
    print(f"\n- 원본: {data['url']}")


def cmd_mother_school(args):
    b = _one_kinder(args)
    if not is_annex(b):
        sys.exit("[안내] 초등학교 병설유치원만 모초등학교 정보를 연결합니다.")
    import schoolinfo
    data = schoolinfo.context(b, get_setting("SCHOOLINFO_API_KEY"),
                              year=args.year or current_school_year(), fresh=args.fresh)
    if not data:
        sys.exit("[안내] 이름·주소가 일치하는 모초등학교를 찾지 못했습니다.")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1)); return
    print(f"# {b['kindername']} — 모초등학교 보조정보")
    print(f"⚠ 아래 내용은 유치원 자체가 아니라 **{data['school']['SCHUL_NM']}**의 "
          f"{data['year']}년 학교알리미 공시입니다.\n")
    for line in extended_lines(b, {"mother_school": data}):
        print(line)
    health = (data.get("health") or [None])[0]
    if health:
        print(f"- 보건실 평균 이용 학생: 주당 {health.get('WIK_AVRG_IFRMA_UTILZ_STDNT_FGR') or '-'}명")
    building = (data.get("building") or [None])[0]
    if building:
        print(f"- 교실·화장실: 일반교실 {building.get('COL_1') or '-'}실 · "
              f"남자화장실 {building.get('ML_TOI_FGR') or '-'} · 여자화장실 {building.get('FML_TOI_FGR') or '-'}")


def cmd_traffic(args):
    b = _one_kinder(args)
    if not home_coords():
        sys.exit("[오류] 집 위치가 없습니다. 먼저 home <네이버지도 공유 링크>로 설정하세요.")
    import traffic_safety
    data = traffic_safety.analyze(home_coords(), coords_of(b), fresh=args.fresh,
                                  recent_years=args.years)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1)); return
    print(f"# {b['kindername']} 자차 경로 교통안전")
    minute_text = f"{data['minutes']:.1f}분" if data["minutes"] < 1 else f"{data['minutes']:.0f}분"
    print(f"- 자차 경로: {data['road_km']:.2f}km / {minute_text} "
          "(OSRM, 실시간 교통 미반영)")
    print(f"- 경로가 통과하는 공식 사고다발지: **{len(data['route_hits'])}곳**")
    print(f"- 유치원 출입구가 포함된 공식 사고다발지: **{len(data['entrance_hits'])}곳**")
    for hit in data["route_hits"]:
        print(f"  - {hit['year']} {hit['name']} — 사고 {hit['accidents']}건, "
              f"사상자 {hit['casualties']}명 ({hit['kind']})")
    print(f"- 자료연도: {min(data['years'])}~{max(data['years'])}")
    print("- ⚠ 0곳은 안전 판정이 아니라 도로교통공단의 공식 사고다발지 선정 기준에 "
          "해당하는 구간이 확인되지 않았다는 뜻입니다.")


def cmd_bus(args):
    b = _one_kinder(args)
    import schoolbus
    data = schoolbus.query(b.get("kindername"), b.get("addr"), b.get("_sido"),
                           fresh=args.fresh)
    api_rows = rows_for_kinder("schoolBus", b, fresh=args.fresh)
    api_row = api_rows[0] if api_rows else None
    out = {"school_safety": data, "kindergarten_api": api_row,
           "crosscheck": bus_crosscheck(data, api_row)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)); return
    print(f"# {b['kindername']} 통학버스 교차확인")
    print(f"- 판정: **{out['crosscheck']}**")
    if data.get("status"):
        print(f"- 학교안전지원시스템: {data['status']} (없음이 아니라 조회 결과 상태)")
    elif data.get("error"):
        print(f"- 학교안전지원시스템 조회 실패: {data['error']}")
    else:
        print(f"- 학교안전지원시스템 등록차량: {data.get('vehicle_count', 0)}대")
        print("- 차량 규모: " + " · ".join(f"{k} {v}대" for k, v in (data.get("vehicle_sizes") or {}).items()))
        print("- 운행 형태: " + " · ".join(f"{k} {v}대" for k, v in (data.get("ownership") or {}).items()))
        print("- 운전자 교육: " + " · ".join(f"{k} {v}명" for k, v in (data.get("driver_training") or {}).items()))
        print("- 동승자 교육: " + " · ".join(f"{k} {v}명" for k, v in (data.get("companion_training") or {}).items()))
        print("- 동승자 고용형태: " + " · ".join(f"{k} {v}명" for k, v in (data.get("companion_employment") or {}).items()))
    if api_row:
        print(f"- 유치원알리미: {'운행' if api_row.get('vhcl_oprn_yn') == 'Y' else '미운행'} · "
              f"운행 {to_int(api_row.get('opra_vhcnt')) or 0}대 · 신고 {to_int(api_row.get('dclr_vhcnt')) or 0}대")
    print("- 차량번호와 개인 이름은 개인정보 보호를 위해 표시하지 않습니다.")


def cmd_bus_import(args):
    import schoolbus
    data = schoolbus.import_xlsx(args.path)
    print(f"통학버스 엑셀을 가져왔습니다: {data['row_count']}개 유치원 · "
          f"{data['imported_at']} · SHA-256 {data['sha256'][:12]}…")
    print("자동 조회가 실패할 때만 이 자료를 보조로 사용합니다.")


def cmd_bus_status(args):
    import schoolbus
    print(json.dumps(schoolbus.status(), ensure_ascii=False, indent=1))


def cmd_sources(args):
    import schoolbus, traffic_safety
    data = {
        "settings": {"KINDER_API_KEY": bool(get_setting("KINDER_API_KEY")),
                     "NEIS_API_KEY": bool(get_setting("NEIS_API_KEY")),
                     "SCHOOLINFO_API_KEY": bool(get_setting("SCHOOLINFO_API_KEY")),
                     "HOME_LATLNG": bool(home_coords()),
                     "CHILD_BIRTH_YM": bool(get_setting("CHILD_BIRTH_YM"))},
        "traffic": traffic_safety.status(), "schoolbus": schoolbus.status(),
        "web_disclosure": {"key_required": False, "cache_days": 7}}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1)); return
    print("# 데이터 출처 상태")
    for k, v in data["settings"].items(): print(f"- {k}: {'설정됨' if v else '미설정'}")
    print("- 도로교통공단 CSV: " + " · ".join(
        f"{v['label']} {'캐시 있음' if v['cached'] else '미수신'}" for v in data['traffic'].values()))
    imp = data["schoolbus"].get("manual_import")
    print(f"- 통학버스 자동 조회 캐시: {data['schoolbus']['live_cache_count']}건")
    print("- 통학버스 수동 엑셀: " +
          (f"{imp['source_file']} · {imp['row_count']}행 · {imp['imported_at']}" if imp else "없음"))


# ---------------------------------------------------------------- cmd: raw
def cmd_raw(args):
    regions = resolve_region(args.region)
    out = {}
    for sido, sgg, name in regions:
        try:
            out[f"{name}({sgg})"] = fetch(args.endpoint, sido, sgg, fresh=args.fresh)
        except ApiDenied as e:
            out[f"{name}({sgg})"] = {"status": "DENIED", "message": str(e)}
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ------------------------------------------------------------ cmd: regions
def cmd_regions(args):
    table = load_sgg()
    if not table:
        print("저장된 시군구 코드가 없습니다. 예: python kinderinfo.py discover 서울")
        return
    for sido_code, sggs in sorted(table.items()):
        sido_name = next((n for n, c in SIDO.items() if c == sido_code), sido_code)
        total = sum(v.get("count", 0) for v in sggs.values())
        print(f"## {sido_name} ({sido_code}) — {len(sggs)}개 시군구, "
              f"유치원 {total}곳")
        for code, v in sorted(sggs.items()):
            print(f"- {code}: {v['name']} ({v.get('count', '?')}곳)")


# --------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(
        description="유치원알리미 Open API CLI (유치원 검색·비교·종합 리포트)")
    p.add_argument("--version", action="version",
                   version=f"kaic-kinder-info {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp, region=True):
        if region:
            sp.add_argument("region",
                            help="지역: '서울', '서울 강남구', 시군구코드(11680) 등")
        sp.add_argument("--fresh", action="store_true", help="캐시 무시하고 재조회")
        sp.add_argument("--json", action="store_true", help="JSON 출력")

    def add_age(sp):
        sp.add_argument("--age", type=int, choices=list(AGE_CLASSES),
                        help="대상 연령반(만3~5세)")
        sp.add_argument("--target", action="store_true",
                        help=".env 의 CHILD_BIRTH_YM 으로 입학 연령반 자동 계산")
        sp.add_argument("--age3", action="store_true",
                        help=argparse.SUPPRESS)   # 구 옵션(= --age 3)

    sp = sub.add_parser("search", help="지역별 유치원 검색")
    add_common(sp)
    add_age(sp)
    sp.add_argument("--name", help="유치원명 부분일치 필터")
    sp.add_argument("--estab", choices=["공립", "사립", "국립"], help="설립유형 필터")
    sp.add_argument("--near", type=float, metavar="KM",
                    help="집에서 N km 이내만 (--road 와 함께면 도로 거리 기준)")
    sp.add_argument("--road", action="store_true",
                    help="자차 도로 거리·시간 조회(직선거리 대신 실제 경로, 키 불필요)")
    sp.add_argument("--no-web", action="store_true",
                    help="혼합반 연령 구성 웹 확인 생략(빠르지만 미확인 후보 포함)")
    sp.add_argument("--sort", choices=["name", "size", "fill", "dist", "size3"],
                    default="name",
                    help="정렬: name=이름, size=해당 연령 정원 많은 순, "
                         "fill=충원율순, dist=집에서 가까운 순")
    sp.add_argument("--limit", type=int, help="최대 표시 개수")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("profile", help="유치원 1곳 종합 리포트")
    add_common(sp)
    sp.add_argument("name", help="유치원명(부분일치) 또는 kindercode")
    sp.add_argument("--web", action="store_true",
                    help="원비·시정명령 이력을 유치원알리미 웹에서 함께 조회(수 초 추가)")
    sp.add_argument("--extended", action="store_true",
                    help="급식·보건·재정·교통안전·통학버스·병설 모초교까지 확장 조회")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("compare", help="여러 유치원 비교표")
    add_common(sp)
    add_age(sp)
    sp.add_argument("names", help="쉼표로 구분한 유치원명들 (예: '가나,다라,마바')")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("report",
                        help="최종 후보 브리핑(비교표+상세, 저장 가능)")
    add_common(sp, region=False)
    add_age(sp)
    sp.add_argument("region", nargs="?", help="지역 (생략 시 pick 저장 후보 사용)")
    sp.add_argument("names", nargs="?", help="쉼표로 구분한 유치원명 1~4곳 (생략 시 후보)")
    sp.add_argument("--out", help="마크다운 파일로 저장 (예: --out 브리핑.md)")
    sp.add_argument("--no-web", action="store_true",
                    help="원비·시정명령 웹 조회 생략(빠르게)")
    sp.add_argument("--no-trend", action="store_true",
                    help="충원율·근속 추이 생략(빠르게)")
    sp.add_argument("--questions", action="store_true",
                    help="요청할 때만 방문·전화 확인 질문 목록 추가")
    sp.add_argument("--extended", action="store_true",
                    help="급식·보건·재정·교통안전·통학버스·병설 모초교까지 포함")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("hours",
                        help="후보들의 실제 운영시간(정규·방과후·돌봄)을 출처별 비교")
    add_common(sp, region=False)
    sp.add_argument("region", nargs="?", help="지역 (생략 시 pick 저장 후보 사용)")
    sp.add_argument("names", nargs="?", help="쉼표로 구분한 유치원명 1~6곳")
    sp.add_argument("--year", type=int, help="확인할 학년도 (예: 2027)")
    sp.add_argument("--target", action="store_true",
                    help="CHILD_BIRTH_YM으로 첫 입학 학년도 자동 계산")
    sp.set_defaults(func=cmd_hours)

    sp = sub.add_parser("pick", help="후보 저장 — 이후 report/trend/diff 인자 생략 가능")
    sp.add_argument("region", nargs="?", help="지역 (예: '서울 강남구')")
    sp.add_argument("names", nargs="?", help="쉼표로 구분한 유치원명들")
    sp.add_argument("--show", action="store_true", help="저장된 후보 보기")
    sp.add_argument("--clear", action="store_true", help="후보 비우기")
    sp.set_defaults(func=cmd_pick)

    sp = sub.add_parser("trend",
                        help="후보 추이 — 충원율·원아·근속(최근 5개 차수 ≈ 3년)")
    add_common(sp, region=False)
    sp.add_argument("region", nargs="?", help="지역 (생략 시 pick 저장 후보 사용)")
    sp.add_argument("names", nargs="?", help="쉼표로 구분한 유치원명들 (생략 시 후보)")
    sp.add_argument("--periods", type=int, default=5, help="차수 개수 (기본 5)")
    sp.set_defaults(func=cmd_trend)

    sp = sub.add_parser("diff", help="최신 두 공시 차수 사이 후보 변화")
    add_common(sp, region=False)
    sp.add_argument("region", nargs="?", help="지역 (생략 시 pick 저장 후보 사용)")
    sp.add_argument("names", nargs="?", help="쉼표로 구분한 유치원명들 (생략 시 후보)")
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("schedule",
                        help="병설유치원의 방학·학사일정(모초등학교 기준, NEIS)")
    add_common(sp)
    sp.add_argument("name", help="유치원명(부분일치) 또는 kindercode")
    sp.add_argument("--year", type=int, help="학년도 (기본: 현재 학년도)")
    sp.add_argument("--meals", action="store_true", help="모초등학교 급식 식단도 표시")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("raw", help="엔드포인트 원본 JSON")
    sp.add_argument("endpoint", choices=list(ENDPOINTS), help="엔드포인트명")
    add_common(sp)
    sp.set_defaults(func=cmd_raw)

    sp = sub.add_parser("discover", help="시도의 시군구 코드 자동 탐색(최초 1회)")
    sp.add_argument("sido", help="시도명 또는 코드 (예: 서울, 경남, 48)")
    sp.add_argument("--full", action="store_true",
                    help="코드 전수 스캔(경기 등 '일반시+구' 지역용, 호출 5배)")
    sp.add_argument("--fresh", action="store_true", help="기존 결과 무시하고 재탐색")
    sp.set_defaults(func=lambda a: discover(a.sido, full=a.full, fresh=a.fresh))

    sp = sub.add_parser("regions", help="저장된 시군구 코드 목록")
    sp.set_defaults(func=cmd_regions)

    sp = sub.add_parser("refresh", help="캐시를 비워 최신 공시를 새로 받게 함")
    sp.add_argument("--source", choices=["traffic", "bus", "schoolinfo"],
                    help="특정 출처만 갱신")
    sp.add_argument("--all", action="store_true", help="모든 동적 출처를 함께 갱신")
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("home", help="집 좌표 설정 — 네이버지도 공유 링크를 붙여넣기")
    sp.add_argument("link", nargs="?", help="네이버지도 공유 링크 (naver.me/... 등)")
    sp.add_argument("--show", action="store_true", help="현재 집 좌표 보기")
    sp.set_defaults(func=cmd_home)

    sp = sub.add_parser("health", help="유치원 급식·보건·환경 웹 공시")
    add_common(sp)
    sp.add_argument("name", help="유치원명")
    sp.add_argument("--year", type=int, help="식단표 연도")
    sp.add_argument("--month", type=int, choices=range(1, 13), help="식단표 월")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("finance", help="유치원 예산·결산 추이")
    add_common(sp)
    sp.add_argument("name", help="유치원명")
    sp.set_defaults(func=cmd_finance)

    sp = sub.add_parser("mother-school", help="병설유치원 모초등학교 학교알리미 보조정보")
    add_common(sp)
    sp.add_argument("name", help="병설유치원명")
    sp.add_argument("--year", type=int, help="공시연도")
    sp.set_defaults(func=cmd_mother_school)

    sp = sub.add_parser("traffic", help="실제 자차 경로와 어린이 사고다발지 폴리곤 교차")
    add_common(sp)
    sp.add_argument("name", help="유치원명")
    sp.add_argument("--years", type=int, default=5, help="최근 자료연도 수(기본 5)")
    sp.set_defaults(func=cmd_traffic)

    sp = sub.add_parser("bus", help="통학버스 공개 등록현황과 유치원알리미 교차확인")
    add_common(sp)
    sp.add_argument("name", help="유치원명")
    sp.set_defaults(func=cmd_bus)

    sp = sub.add_parser("bus-import", help="학교안전지원시스템 통학버스 엑셀 비상 가져오기")
    sp.add_argument("path", help="다운로드한 XLSX 경로")
    sp.set_defaults(func=cmd_bus_import)

    sp = sub.add_parser("bus-status", help="통학버스 자동 캐시·수동 엑셀 상태")
    sp.set_defaults(func=cmd_bus_status)

    sp = sub.add_parser("sources", help="API 키·자동 갱신·수동 자료 상태")
    sp.add_argument("--json", action="store_true", help="JSON 출력")
    sp.set_defaults(func=cmd_sources)

    args = p.parse_args()
    try:
        args.func(args)
    except ApiKeyError as e:
        sys.exit(f"[오류] 인증키가 거부되었습니다 ({e}).\n\n"
                 f"  .env 의 KINDER_API_KEY 값을 확인하세요. 키 앞뒤에 따옴표나 "
                 f"공백이 들어가면 안 됩니다.\n"
                 f"  키가 없다면 https://e-childschoolinfo.moe.go.kr 의 "
                 f"[자료실 > OPEN API] 에서 신청하세요.")
    except ApiDenied as e:
        sys.exit(f"[안내] 해당 공시 항목이 현재 점검 중입니다: {e}")
    except ApiError as e:
        sys.exit(f"[오류] {e}")


if __name__ == "__main__":
    main()
