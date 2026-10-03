# 명령과 옵션 선택

도구 위치는 `{{TOOL_DIR}}`다. 현재 CLI의 `--help`가 지원 옵션의 기준이다.
설치 스킬은 이 문서와 함께 배포되지만 실행 코드는 도구 저장소에 있다.

## 요청별 명령

| 요청 | CLI (`kinderinfo.py` 뒤 인수) | 필요한 구분 |
|---|---|---|
| 지역 목록·연령반 | `search "서울 강남구" --age 3` | 전용반과 해당 연령 포함 혼합반; 확인 실패는 미확인 |
| 다음 학년도 아이 연령 | `search "서울 강남구" --target` | 기존 CHILD_BIRTH_YM과 계산된 학년도가 요청과 일치할 때 |
| 유치원 상세 | `profile "서울 강남구" "A유치원"` | 동명이름이면 지역/주소로 대상 확인 |
| 혼합반·원비·시정명령 | `profile "서울 강남구" "A유치원" --web` | 웹 출처 추가; 대상 조회와 API 권한은 별개 |
| 출처 전체 확장 | `profile "서울 강남구" "A유치원" --extended` | 보건·재정·교통·버스·모초교를 모두 요청한 경우 |
| 후보 비교 | `compare "대구 수성구" "A유치원,B유치원" --age 3` | 요청한 기준과 후보 유지 |
| 브리핑 | `report "대구 수성구" "A유치원,B유치원" --age 3` | 방문 질문은 `--questions` 명시 요청 때만 |
| 파일로 브리핑 | 위 report에 `--out "새_브리핑.md"` | 파일 저장 요청·기존 파일 충돌 확인 |
| 세부 운영시간 | `hours "대구 수성구" "A유치원,B유치원" --year 2027` | 요청 학년도와 근거 학년도 비교 |
| 추이 / 두 차수 차이 | `trend "광주 북구" "A유치원,B유치원"` / `diff`에 같은 대상 | 저장 후보가 없어도 명시 대상 사용 가능 |
| 병설 학사일정 | `schedule "광주 북구" "A초등학교병설" --year 2027` | 모초등학교 참고 일정; NEIS 선택 키 |
| 모초교 보조정보 | `mother-school "광주 북구" "A초등학교병설" --year 2026` | 유치원 자체 정보 아님; SCHOOLINFO 선택 키 |
| 보건 / 재정 | `health "광주 북구" "A유치원"` / `finance`에 같은 대상 | 누락·파서 오류를 무운영/0원으로 바꾸지 않음 |
| 경로 안전 / 버스 | `traffic "광주 북구" "A유치원"` / `bus`에 같은 대상 | 직선거리로 안전 판단 금지; 버스 노선 미공시 |
| 원자료 | `raw basicInfo2 11680` | 개인정보를 그대로 답변·로그에 붙이지 않음 |
| 지역·출처·버스 캐시 상태 | `regions`, `sources`, `bus-status` | 진단은 전수 수집/설정 변경이 아님 |
| 새 지역 코드 탐색 | `discover 경남` | 정확한 시도 필요; `--full`은 호출이 크게 증가 |
| 캐시 갱신 | `refresh`, `refresh --source traffic|bus|schoolinfo`, `refresh --all` | 출처 범위별 갱신/삭제, 보통 최신 조회는 먼저 `--fresh` |
| 후보 저장·보기 | `pick "대구 수성구" "A유치원,B유치원"`, `pick --show` | 저장 요청 때만; 단순 후보 언급으로 실행하지 않음 |
| 집 위치 설정 | `home "https://naver.me/XXXX"` | 링크는 예시; 좌표 추출·.env 저장 요청 확인 |
| 버스 수동 자료 갱신 | `bus-import "자료.xlsx"`, 이어서 `bus-status` | 사용자가 첨부·요청한 실제 XLSX 확인 |

지역 입력은 시군구 코드, 시도+시군구, 또는 저장 코드표에서 유일한 시군구명이다.
시도 전체 입력은 여러 지역 조회/자동 코드 탐색을 유발할 수 있다. 특정 구 질문을 전수 조사로 넓히지 않는다.

## 옵션과 도구 전환

- search 전용: `--name`, `--estab 공립|사립|국립`, `--near KM`, `--road`, `--sort name|size|fill|dist`, `--limit N`, `--no-web`.
- `--fresh`·`--json`은 이를 지원하는 조회 명령에서만 사용한다. `--out`은 report, `--year`는 hours/schedule/mother-school 등 실제 지원 명령에서만 사용한다.
- `--no-web`은 혼합반 확인/웹 정보 누락을 동반한다. 속도 우선 요청에서만 생략하고 미확인 범위를 설명한다.
- report는 이름 1~4곳, hours는 1~6곳을 받는다. 후보가 더 많으면 요청을 임의 축소하지 말고 나눠 조회·취합한다. 이 도구 제한을 입학 지원 개수 규칙으로 해석하지 않는다.
- search의 `--target`은 다음 학년도를 계산하고 `--age`보다 우선한다. 지정 연령을 적용할 때는 target을 함께 쓰지 않는다.
- hours의 `--target`은 출생연도+4의 첫 입학 학년도다. 원하는 연도는 `--year`로 직접 지정한다.
- MCP 검색·상세의 `fresh=True`는 기본 False의 선택 옵션이다. 상세의 `web=True`, `extended=True`와 조합 가능하다.
- MCP에 요청 옵션이 없을 때만 CLI로 전환한다. MCP의 실제 스키마를 확인하고 동일 조회를 중복 수행하지 않는다.

## 셸 예시

Python 실행 파일이 실제 응답하는지 확인한 뒤 절대 경로를 사용한다. 아래 도움말은 API 호출·조회 완료 검증이 아니다.

```powershell
$env:PYTHONUTF8 = '1'
& '<검증한 python.exe 절대 경로>' '{{TOOL_DIR}}/kinderinfo.py' search --help
```

Bash에서는 `python "{{TOOL_DIR}}/kinderinfo.py" search --help`처럼 실행한다.
Windows MCP 등록에는 Store 스텁이 아닌 Python과 `mcp_server.py`의 절대 경로를 사용한다.

