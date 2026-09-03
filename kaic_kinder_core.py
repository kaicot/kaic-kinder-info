"""Stable Python facade for kaic-kinder-info consumers."""

from kinderinfo import (
    __version__, afterschool_participants, afterschool_rate, age_class_for,
    attendance_days, coords_of, current_school_year, fill_rate, find_kinder,
    gather_sections, haversine_km, next_admission_year, parse_birth_ym,
    per_class, region_basic, resolve_region, tenure_stats, to_int,
    total_classes, total_pupils,
)

__all__ = [
    "__version__", "afterschool_participants", "afterschool_rate",
    "age_class_for", "attendance_days", "coords_of", "current_school_year",
    "fill_rate", "find_kinder", "gather_sections", "haversine_km",
    "next_admission_year", "parse_birth_ym", "per_class", "region_basic",
    "resolve_region", "tenure_stats", "to_int", "total_classes",
    "total_pupils",
]
