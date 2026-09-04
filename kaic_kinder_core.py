"""Stable Python facade for kaic-kinder-info consumers."""

import kinderinfo as _impl

from kinderinfo import (
    __version__, afterschool_participants, afterschool_rate, age_class_for,
    attendance_days, coords_of, current_school_year, fill_rate, find_kinder,
    gather_sections, haversine_km, next_admission_year, parse_birth_ym,
    per_class, region_basic, resolve_region, tenure_stats, to_int,
    total_classes, total_pupils,
)


def _match_regional_rows(rows, kinder):
    """Match one kindergarten inside a region-wide endpoint response."""
    code = kinder.get("kindercode")
    name = str(kinder.get("kindername", "")).replace(" ", "")
    hits = [row for row in rows if row.get("kindercode") == code]
    if not hits and name:
        hits = [
            row for row in rows
            if str(row.get("kindername", "")).replace(" ", "") == name
        ]
    return hits


def collect_region(sido, sgg, sgg_name=None, fresh=False):
    """Collect one region while calling every disclosure endpoint at most once.

    Returns ``[(basic_row, sections), ...]``. Endpoint-level failures are copied
    to every kindergarten as the same ``DENIED:...`` or ``ERROR:...`` value,
    matching :func:`gather_sections` without repeating regional API requests.
    """
    region_name = sgg_name or sgg
    basics = region_basic([(sido, sgg, region_name)], fresh=fresh)
    regional = {}
    for endpoint, _ in _impl.PROFILE_SECTIONS:
        try:
            regional[endpoint] = _impl.fetch(endpoint, sido, sgg, fresh=fresh)
        except _impl.ApiDenied as error:
            regional[endpoint] = f"DENIED:{error}"
        except _impl.ApiError as error:
            regional[endpoint] = f"ERROR:{error}"

    collected = []
    for basic in basics:
        sections = {}
        for endpoint, _ in _impl.PROFILE_SECTIONS:
            value = regional[endpoint]
            sections[endpoint] = (
                value if isinstance(value, str)
                else _match_regional_rows(value, basic)
            )
        collected.append((basic, sections))
    return collected

__all__ = [
    "__version__", "afterschool_participants", "afterschool_rate",
    "age_class_for", "attendance_days", "collect_region", "coords_of", "current_school_year",
    "fill_rate", "find_kinder", "gather_sections", "haversine_km",
    "next_admission_year", "parse_birth_ym", "per_class", "region_basic",
    "resolve_region", "tenure_stats", "to_int", "total_classes",
    "total_pupils",
]
