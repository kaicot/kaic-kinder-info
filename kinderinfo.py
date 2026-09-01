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

__version__ = "1.3.0"

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


def bus_note(bus_row):
    """통학차량 운행 여부. 운행하면 거리보다 노선이 중요해진다."""
    if not bus_row:
        return None
    if bus_row.get("vhcl_oprn_yn") == "Y":
        n = to_int(bus_row.get("opra_vhcnt")) or 0
        return f"운행 {n}대 — **노선이 우리 동네를 지나는지 전화로 확인**"
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
    if age:
        rows = [r for r in rows
                if (to_int(r.get(f"clcnt{age}")) or 0) > 0
                or (to_int(r.get(f"ag{age}fpcnt")) or 0) > 0]

    home = home_coords()
    if args.near:
        if not home:
            sys.exit("[오류] --near 를 쓰려면 .env 에 HOME_LATLNG=위도,경도 를 설정하세요.\n"
                     "  지도 앱(카카오맵·구글지도)에서 집을 우클릭하면 좌표가 나옵니다.\n"
                     "  예: HOME_LATLNG=37.5665,126.9780")
        rows = [r for r in rows
                if (distance_from_home(r) or 1e9) <= args.near]

    size_key = f"ag{age or 3}fpcnt"
    keyf = {
        "name": lambda r: str(r.get("kindername", "")),
        "size": lambda r: -(to_int(r.get(size_key)) or 0),
        "size3": lambda r: -(to_int(r.get(size_key)) or 0),   # 구 옵션 별칭
        "fill": lambda r: -(fill_rate(r) or -1),
        "dist": lambda r: distance_from_home(r) if distance_from_home(r) is not None else 1e9,
    }[args.sort]
    rows.sort(key=keyf)
    if args.limit:
        rows = rows[:args.limit]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    reg_label = ", ".join(dict.fromkeys(n for _, _, n in regions))
    pb = fmt_pbnttmng(rows[0].get("pbnttmng")) if rows else ""
    print(f"# 유치원 검색: {reg_label} — {len(rows)}곳"
          + (f" ({pb} 기준)" if pb else ""))
    if target_note:
        print(f"대상: {target_note}")
    flt = []
    if age:
        flt.append(f"만{age}세반 있는 곳만")
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
    print(f"| 유치원명 {dist_col}| 시군구 | 설립 | {age_col} | 혼합반 | "
          "전체 원아/정원(충원율) | 운영시간 | 전화 |")
    print(f"|---{dist_sep}|---|---|---|---|---|---|---|")
    for r in rows:
        dist_cell = f"| {fmt_km(distance_from_home(r)) or '-'} " if home else ""
        if age:
            cell = (f"{to_int(r.get(f'clcnt{age}')) or 0}학급/"
                    f"{to_int(r.get(f'ppcnt{age}')) or 0}명/"
                    f"{to_int(r.get(f'ag{age}fpcnt')) or 0}명")
        else:
            cell = "/".join(str(to_int(r.get(f"clcnt{a}")) or 0)
                            for a in AGE_CLASSES)
        mix = to_int(r.get("mixclcnt")) or 0
        tp, cap, fr = total_pupils(r), to_int(r.get("prmstfcnt")), fill_rate(r)
        whole = f"{tp}/{cap}" + (f" ({fr}%)" if fr is not None else "")
        print(f"| {md_escape(r.get('kindername'))} {dist_cell}| {r.get('_sgg_name')} "
              f"| {r.get('establish')} | {cell} "
              f"| {mix or '-'} | {whole} | {r.get('opertime') or '-'} "
              f"| {r.get('telno') or '-'} |")
    print()
    print("혼합연령 학급에도 해당 연령이 포함될 수 있으니, 관심 유치원은 "
          "profile 로 상세 확인을 권장합니다.")
    if home:
        print("\n직선거리는 **언덕과 도로를 무시한 하한값**입니다. 부산처럼 지형이 험한 곳에서는 "
              "실제 이동 편의와 다를 수 있습니다.\n"
              "자차 등하원이라면 **유치원 앞에 잠시 정차할 수 있는지**가 관건인데 이는 어떤 "
              "데이터에도 없습니다. profile 의 로드뷰 링크로 직접 확인하세요.")


# ------------------------------------------------------------ cmd: profile
PROFILE_SECTIONS = [
    ("building", None), ("classArea", None), ("lessonDay", None),
    ("teachersInfo", None), ("yearOfWork", None), ("schoolMeal", None),
    ("schoolBus", None), ("safetyEdu", None), ("environmentHygiene", None),
    ("insurance", None), ("deductionSociety", None), ("afterSchoolPresent", None),
]


def cmd_profile(args):
    regions = resolve_region(args.region)
    b = find_kinder(regions, args.name, fresh=args.fresh)

    sections = {}   # ep -> rows | ApiDenied 메시지
    for ep, _ in PROFILE_SECTIONS:
        try:
            sections[ep] = rows_for_kinder(ep, b, fresh=args.fresh)
        except ApiDenied as e:
            sections[ep] = f"DENIED:{e}"
        except ApiError as e:
            sections[ep] = f"ERROR:{e}"

    if args.json:
        out = {"basicInfo2": b}
        for ep, v in sections.items():
            out[ep] = v if isinstance(v, list) else {"status": v}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    yow = sections.get("yearOfWork")
    yow_row = yow[0] if isinstance(yow, list) and yow else None
    asp = sections.get("afterSchoolPresent")
    asp_row = asp[0] if isinstance(asp, list) and asp else None
    ca = sections.get("classArea")
    ca_row = ca[0] if isinstance(ca, list) and ca else None
    safe = sections.get("safetyEdu")
    safe_row = safe[0] if isinstance(safe, list) and safe else None
    bus = sections.get("schoolBus")
    bus_row = bus[0] if isinstance(bus, list) and bus else None
    lsn = sections.get("lessonDay")
    lsn_row = lsn[0] if isinstance(lsn, list) and lsn else None

    print(f"# {b['kindername']} 종합 리포트")
    print(f"{b.get('_sgg_name')} · {one_line_summary(b)} · "
          f"{fmt_pbnttmng(b.get('pbnttmng'))} 기준")
    print()
    print("## 핵심 요약(파생 지표)")
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
        ("운영시간", operating_note(b, asp_row)),
        ("집에서 직선거리",
         (f"{fmt_km(distance_from_home(b))} (언덕·도로 무시한 하한값)"
          if distance_from_home(b) is not None else None)),
        ("통학차량 관점", bus_note(bus_row)),
    ]
    for label, val in items:
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


# ------------------------------------------------------------ cmd: compare
def cmd_compare(args):
    age, target_note = resolve_search_age(args)
    age = age or 3   # 비교표는 기준 연령이 하나 필요
    regions = resolve_region(args.region)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if len(names) < 2:
        sys.exit("[오류] compare 는 쉼표로 구분한 2곳 이상이 필요합니다.")
    kinders = [find_kinder(regions, n, fresh=args.fresh) for n in names]

    aux = {}
    for k in kinders:
        code = k["kindercode"]
        aux[code] = {}
        for ep in ("yearOfWork", "schoolBus", "safetyEdu", "classArea",
                   "afterSchoolPresent", "lessonDay"):
            try:
                rows = rows_for_kinder(ep, k, fresh=args.fresh)
                aux[code][ep] = rows[0] if rows else None
            except ApiError:
                aux[code][ep] = None

    def metric_row(label, fn):
        cells = " | ".join(md_escape(fn(k) or "-") for k in kinders)
        print(f"| {label} | {cells} |")

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

    print(f"# 유치원 비교 ({fmt_pbnttmng(kinders[0].get('pbnttmng'))} 기준)")
    if target_note:
        print(f"대상: {target_note}")
    print()
    header = " | ".join(md_escape(k["kindername"]) for k in kinders)
    print(f"| 항목 | {header} |")
    print("|---" * (len(kinders) + 1) + "|")
    metric_row("시군구", lambda k: k.get("_sgg_name"))
    metric_row("설립유형", lambda k: k.get("establish"))
    metric_row("개원일", lambda k: fmt_date(k.get("odate")))
    metric_row("운영시간", lambda k: k.get("opertime"))
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
    metric_row("통학차량 관점",
               lambda k: (bus_note(aux[k["kindercode"]].get("schoolBus")) or "")
               .replace("**", ""))
    metric_row("주소", lambda k: k.get("addr"))
    metric_row("전화", lambda k: k.get("telno"))
    metric_row("로드뷰(정차 여건 확인)", roadview_url)
    print()
    print("근속 평균은 공시 구간(1년 미만~6년 이상)의 중간값 가중 추정치입니다. "
          "교직원 수·급식 항목은 현재 공시 점검 중이면 표시되지 않을 수 있습니다.")


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
        print("\n방학 일정은 유치원에 직접 문의하셔야 합니다"
              + (f" (전화 {b.get('telno')})" if b.get("telno") else "") + ".")
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
    print(f"- 최종 확인은 유치원에 직접 문의"
          + (f" (전화 {b.get('telno')})" if b.get("telno") else "") + "하세요.")

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
                    help="집(.env 의 HOME_LATLNG)에서 직선 N km 이내만")
    sp.add_argument("--sort", choices=["name", "size", "fill", "dist", "size3"],
                    default="name",
                    help="정렬: name=이름, size=해당 연령 정원 많은 순, "
                         "fill=충원율순, dist=집에서 가까운 순")
    sp.add_argument("--limit", type=int, help="최대 표시 개수")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("profile", help="유치원 1곳 종합 리포트")
    add_common(sp)
    sp.add_argument("name", help="유치원명(부분일치) 또는 kindercode")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("compare", help="여러 유치원 비교표")
    add_common(sp)
    add_age(sp)
    sp.add_argument("names", help="쉼표로 구분한 유치원명들 (예: '가나,다라,마바')")
    sp.set_defaults(func=cmd_compare)

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
