#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""공식 계획서 등으로 사람이 확인한 운영시간의 로컬 저장소.

유치원알리미 웹 표는 정규 교육과정 시간과 전체 운영 범위를 제공하지만, 일반 방과후와
아침·저녁돌봄을 분리하지 않는다. 사람이 공식 문서를 확인한 결과만 이 파일을 통해 합친다.
실제 데이터 파일(verified_hours.json)은 후보 지역과 선호가 드러나므로 Git에 올리지 않는다.
"""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "verified_hours.json"
SCHEMA_VERSION = 1
RANGE_KEYS = ("education", "afterschool", "early_care", "late_care", "vacation_hours")


class VerifiedHoursError(ValueError):
    """로컬 검증정보의 형식 오류."""


def _clock(value):
    text = str(value or "").strip()
    m = re.fullmatch(r"([0-2]?\d):([0-5]\d)", text)
    if not m or int(m.group(1)) > 23:
        raise VerifiedHoursError(f"시간 형식이 올바르지 않습니다: {value!r} (HH:MM 필요)")
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def _range(value, label):
    if value in (None, "", []):
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise VerifiedHoursError(f"{label}은 [시작, 종료] 두 값이어야 합니다")
    start, end = _clock(value[0]), _clock(value[1])
    if start >= end:
        raise VerifiedHoursError(f"{label}의 시작시간은 종료시간보다 빨라야 합니다")
    return {"시작": start, "종료": end}


def validate_record(record):
    """한 학년도 레코드를 검산하고 출력용 사본을 반환한다."""
    if not isinstance(record, dict):
        raise VerifiedHoursError("학년도 운영시간 레코드는 객체여야 합니다")
    out = {}
    for key in RANGE_KEYS:
        value = _range(record.get(key), key)
        if value:
            out[key] = value
    conditions = record.get("conditions") or []
    if not isinstance(conditions, list) or not all(isinstance(x, str) for x in conditions):
        raise VerifiedHoursError("conditions는 문자열 배열이어야 합니다")
    out["conditions"] = [x.strip() for x in conditions if x.strip()]
    closures = record.get("vacation_closures") or []
    if not isinstance(closures, list) or not all(isinstance(x, str) for x in closures):
        raise VerifiedHoursError("vacation_closures는 문자열 배열이어야 합니다")
    out["vacation_closures"] = [x.strip() for x in closures if x.strip()]
    source = record.get("source") or {}
    if not isinstance(source, dict):
        raise VerifiedHoursError("source는 객체여야 합니다")
    out["source"] = {
        "type": str(source.get("type") or "official_plan").strip(),
        "title": str(source.get("title") or "").strip(),
        "url": str(source.get("url") or "").strip(),
        "verified_at": str(source.get("verified_at") or "").strip(),
    }
    return out


def load(path=DATA_FILE):
    if not Path(path).exists():
        return {"version": SCHEMA_VERSION, "kindergartens": {}}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise VerifiedHoursError(f"검증 운영시간 파일을 읽지 못했습니다: {e}") from e
    if data.get("version") != SCHEMA_VERSION or not isinstance(data.get("kindergartens"), dict):
        raise VerifiedHoursError("verified_hours.json의 버전 또는 구조가 올바르지 않습니다")
    return data


def get(kindercode, requested_year=None, path=DATA_FILE):
    """요청 학년도의 레코드, 없으면 가장 가까운 과거 참고 레코드를 반환한다."""
    item = (load(path).get("kindergartens") or {}).get(str(kindercode))
    if not isinstance(item, dict):
        return None
    years = item.get("years") or {}
    parsed = []
    for key, value in years.items():
        try:
            parsed.append((int(key), value))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None
    requested = int(requested_year) if requested_year else None
    if requested is not None:
        exact = next(((year, value) for year, value in parsed if year == requested), None)
        if exact:
            year, value = exact
        else:
            past = [(year, value) for year, value in parsed if year < requested]
            year, value = max(past or parsed, key=lambda x: x[0])
    else:
        year, value = max(parsed, key=lambda x: x[0])
    return {"kindercode": str(kindercode), "name": item.get("name", ""),
            "school_year": year, **validate_record(value)}


def save_record(kindercode, name, school_year, record, path=DATA_FILE):
    """에이전트가 공식 문서를 검증한 뒤 안전하게 한 레코드를 저장한다."""
    clean = validate_record(record)
    data = load(path)
    item = data["kindergartens"].setdefault(str(kindercode), {"name": name, "years": {}})
    item["name"] = name
    item.setdefault("years", {})[str(int(school_year))] = {
        key: ([value["시작"], value["종료"]] if key in RANGE_KEYS else value)
        for key, value in clean.items()
    }
    if not item["years"][str(int(school_year))]["source"].get("verified_at"):
        item["years"][str(int(school_year))]["source"]["verified_at"] = date.today().isoformat()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return clean

