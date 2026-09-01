#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""유치원알리미 MCP 서버 (kaic-kinder-info).

kinderinfo.py 의 CLI 를 같은 프로세스 안에서 호출해 MCP 도구로 노출한다.
(Windows 에서 MCP stdio 서버 내부의 subprocess 호출이 교착되는 문제가 있어
서브프로세스 대신 in-process 로 실행하고, stdout 캡처 + SystemExit 처리로 감쌌다.)

등록 — 반드시 파이썬 절대 경로 사용('python'은 스토어 스텁으로 잡혀 연결 실패):
  Claude Code:
    claude mcp add --scope user kaic-kinder-info -- "<파이썬 절대경로>" "<저장소 경로>/mcp_server.py"
  Codex:
    codex mcp add kaic-kinder-info -- "<파이썬 절대경로>" "<저장소 경로>/mcp_server.py"

파이썬 절대 경로는 `where python`(Windows) / `which python3`(Mac·Linux)로 확인한다.
자세한 내용은 README.md 와 AGENTS.md 참고.
"""
import contextlib
import io
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kinderinfo  # noqa: E402  (프로젝트 로컬 모듈)
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    "kaic-kinder-info",
    instructions=(
        f"유치원알리미(교육부) 공시 데이터 조회 도구 v{kinderinfo.__version__}. "
        "지역별 유치원 검색, 1곳 종합 리포트, 여러 곳 비교표를 마크다운으로 돌려준다. "
        "원비·모집요강·경쟁률은 이 데이터에 없으므로 지어내지 말 것."),
)
_lock = threading.Lock()  # redirect_stdout 이 전역이라 동시 호출 직렬화


def _run(*argv: str) -> str:
    with _lock:
        buf_out, buf_err = io.StringIO(), io.StringIO()
        old_argv = sys.argv
        sys.argv = ["kinderinfo.py", *argv]
        code = 0
        try:
            with contextlib.redirect_stdout(buf_out), \
                 contextlib.redirect_stderr(buf_err):
                kinderinfo.main()
        except SystemExit as e:  # sys.exit("...") 메시지 전달
            code = e.code
        except Exception as e:  # noqa: BLE001 — 서버는 죽지 않게
            code = f"내부 오류: {e!r}"
        finally:
            sys.argv = old_argv
    out = buf_out.getvalue().strip()
    err = buf_err.getvalue().strip()
    parts = [out]
    if code not in (0, None):
        parts.append(str(code))
    if not out and err:  # discover 진행 로그 등은 stderr 로 나옴
        parts.append(err[-1500:])
    result = "\n".join(p for p in parts if p).strip()
    return result or "(빈 결과)"


@mcp.tool()
def search_kindergartens(region: str, age: int = 0, target: bool = False,
                         estab: str = "", name: str = "", near_km: float = 0,
                         sort: str = "name") -> str:
    """지역별 유치원 검색 — 학급/원아/정원/충원율/운영시간을 마크다운 표로 반환.

    Args:
        region: '서울'(시도 전체), '서울 강남구', 또는 시군구코드(예: 11680)
        age: 대상 연령반(3|4|5). 0이면 연령 필터 없음
        target: True면 .env 의 CHILD_BIRTH_YM 으로 입학 연령반을 자동 계산
        estab: 설립유형 필터 — '공립' | '사립' | '국립' (빈값=전체)
        name: 유치원명 부분일치 필터
        near_km: 집(.env 의 HOME_LATLNG)에서 직선 N km 이내만. 0이면 전체
        sort: 'name'(이름순) | 'size'(해당 연령 정원 많은순) | 'fill'(충원율순)
              | 'dist'(집에서 가까운 순)
    """
    args = ["search", region, "--sort", sort]
    if target:
        args.append("--target")
    if age in (3, 4, 5):
        args += ["--age", str(age)]
    if near_km and near_km > 0:
        args += ["--near", str(near_km)]
    if estab:
        args += ["--estab", estab]
    if name:
        args += ["--name", name]
    return _run(*args)


@mcp.tool()
def kindergarten_profile(region: str, name: str, web: bool = False) -> str:
    """유치원 1곳의 전체 공시 항목 종합 리포트(기본현황·건물·수업일수·교사근속·
    통학차량·안전점검·CCTV·환경위생·보험·방과후 등 + 파생 지표).

    Args:
        region: '서울 강남구' 같은 지역 (검색 범위)
        name: 유치원명 부분일치(예: '햇살') 또는 kindercode
        web: True면 원비·시정명령 이력을 유치원알리미 웹에서 함께 조회(수 초 추가).
             원비나 행정처분을 물으면 True로 호출할 것
    """
    args = ["profile", region, name]
    if web:
        args.append("--web")
    return _run(*args)


@mcp.tool()
def compare_kindergartens(region: str, names: str, age: int = 0,
                          target: bool = False) -> str:
    """여러 유치원의 핵심 지표(연령별 학급당 원아, 충원율, 교사 근속, CCTV,
    통학차량, 방과후 포함 운영일수 등)를 마크다운 비교표로 반환.

    Args:
        region: '서울 강남구' 또는 '서울' 같은 지역
        names: 쉼표로 구분한 유치원명들 (예: '햇살,푸른숲,○○초등학교병설')
        age: 기준 연령반(3|4|5). 0이면 만3세 기준
        target: True면 .env 의 CHILD_BIRTH_YM 으로 기준 연령을 자동 계산
    """
    args = ["compare", region, names]
    if target:
        args.append("--target")
    if age in (3, 4, 5):
        args += ["--age", str(age)]
    return _run(*args)


@mcp.tool()
def kindergarten_schedule(region: str, name: str, year: int = 0,
                          meals: bool = False) -> str:
    """초등학교 병설유치원의 방학·학사일정(모초등학교 기준, NEIS).
    "방학 언제야?" 류 질문에 사용. 사립·단설은 조회되지 않으며 그 사실과
    연락처를 안내한다. .env 에 NEIS_API_KEY 필요(없으면 발급 안내가 나온다).

    Args:
        region: '서울 강남구' 같은 지역
        name: 유치원명 부분일치 (병설유치원)
        year: 학년도(예: 2026). 0이면 현재 학년도
        meals: True면 모초등학교 급식 식단도 함께 표시
    """
    args = ["schedule", region, name]
    if year:
        args += ["--year", str(year)]
    if meals:
        args.append("--meals")
    return _run(*args)


@mcp.tool()
def kindergarten_report(region: str, names: str, age: int = 0,
                        target: bool = False, no_web: bool = False) -> str:
    """최종 후보 브리핑 — 비교표 + 유치원별 상세 + **방문·전화 질문지**를
    한 문서로 만든다. 후보를 2~3곳으로 좁힌 뒤 "브리핑/보고서 만들어줘",
    "방문 때 뭘 물어볼까" 류 요청에 사용. 원비·시정명령(웹 공시)도 기본 포함.

    Args:
        region: '서울 강남구' 같은 지역
        names: 쉼표로 구분한 유치원명 1~4곳
        age: 기준 연령반(3|4|5). 0이면 만3세
        target: True면 .env 의 CHILD_BIRTH_YM 으로 기준 연령 자동 계산
        no_web: True면 원비·시정명령 웹 조회 생략(빠르게)
    """
    args = ["report", region, names]
    if target:
        args.append("--target")
    if age in (3, 4, 5):
        args += ["--age", str(age)]
    if no_web:
        args.append("--no-web")
    return _run(*args)


@mcp.tool()
def raw_data(endpoint: str, region: str) -> str:
    """특정 공시 항목의 원본 JSON. endpoint: basicInfo2(기본현황), building(건물),
    classArea(교실면적), teachersInfo(교직원), lessonDay(수업일수), schoolMeal(급식),
    schoolBus(통학차량), yearOfWork(근속), environmentHygiene(환경위생),
    safetyEdu(안전점검), deductionSociety(공제회), insurance(보험),
    afterSchoolPresent(방과후).

    Args:
        endpoint: 위 엔드포인트명 중 하나
        region: '서울 강남구' 또는 시군구코드
    """
    return _run("raw", endpoint, region)


@mcp.tool()
def list_regions() -> str:
    """저장된 시도·시군구 코드와 지역별 유치원 수 목록."""
    return _run("regions")


@mcp.tool()
def discover_region(sido: str, full: bool = False) -> str:
    """새 시도의 시군구 코드를 자동 탐색해 저장(최초 1회, 1~2분 소요).
    경기처럼 '일반시+구' 구조인 지역은 full=True 가 필요하다(5~10분).

    Args:
        sido: 시도명 또는 코드 (예: '경남', '서울', '48')
        full: 코드 전수 스캔 여부 (호출량 5배, 5~10분)
    """
    args = ["discover", sido]
    if full:
        args.append("--full")
    return _run(*args)


if __name__ == "__main__":
    mcp.run()
