"""KNMI source parsing and independent schedules, before any detector exists."""

import csv
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from records import digest

EARLY_ROLES = {2021: "fit", 2022: "development", 2023: "validation"}
REQUIRED = {"STN", "YYYYMMDD", "HH", "T", "U"}


def source_date(token):
    token = token.strip()
    if not re.fullmatch(r"[0-9]{8}", token):
        return None
    try:
        return datetime.strptime(token, "%Y%m%d").date()
    except ValueError:
        return None


def role_for(day):
    if day is None:
        return "unassigned"
    if day.year in EARLY_ROLES:
        return EARLY_ROLES[day.year]
    if day == date(2024, 1, 1):
        return "context_only"
    if date(2024, 1, 2) <= day <= date(2024, 12, 31):
        return "reserved_final"
    return "outside_study"


def require_mode(mode, day):
    allowed = {"fit": {"fit"}, "development": {"development"},
               "validation": {"validation"},
               "early_inventory": {"fit", "development", "validation"}}
    if mode not in allowed or role_for(day) not in allowed[mode]:
        raise ValueError(f"Source date {day} is not permitted in mode {mode}")


def integer(token):
    token = token.strip()
    return int(token) if re.fullmatch(r"[0-9]{1,9}", token) else None


def numeric_cell(token, divisor):
    if token is None:
        return {"value": None, "state": "absent_field"}
    if not token.strip():
        return {"value": None, "state": "missing"}
    try:
        value = float(token)
    except ValueError:
        return {"value": None, "state": "invalid"}
    if not math.isfinite(value):
        return {"value": None, "state": "nonfinite"}
    return {"value": value / divisor, "state": "finite"}


def utc_for(day, hour):
    return datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=hour)


def parse_knmi(payload):
    """Retain physical-line lineage and all tokens; fail closed on unknown schema."""
    parent = digest(payload)
    header = None
    rows = []
    comments = []
    for line_number, line in enumerate(payload.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            comments.append(line)
            candidate = next(csv.reader([line.lstrip()[1:].strip()]))
            columns = [item.strip() for item in candidate]
            if columns and columns[0] == "STN" and "YYYYMMDD" in columns:
                if header is not None or len(columns) != len(set(columns)):
                    raise ValueError("Repeated or duplicate-column schema")
                if not REQUIRED <= set(columns):
                    raise ValueError("Missing required KNMI column; U cannot be replaced by RH")
                header = columns
            continue
        if header is None:
            raise ValueError(f"Data before recognised KNMI header at line {line_number}")
        tokens = next(csv.reader([line], strict=True))
        raw = {name: tokens[i] if i < len(tokens) else None for i, name in enumerate(header)}
        day_token = raw["YYYYMMDD"] or ""
        day = source_date(day_token)
        role = role_for(day)
        # This early reader cannot turn reserved-final rows into analytical values.
        if role == "reserved_final" or (day and day.year > 2024):
            raise ValueError(f"Reserved/unpermitted source date at line {line_number}; intake halted")
        hour = integer(raw["HH"] or "")
        stamp = None
        if not day_token.strip() or not (raw["HH"] or "").strip():
            time_state = "missing"
        elif day is None or hour is None or not 1 <= hour <= 24:
            time_state = "invalid"
        else:
            stamp = utc_for(day, hour).isoformat()
            time_state = "valid_hour"
        station = integer(raw["STN"] or "")
        rows.append({
            "record_id": f"{parent}:{line_number}", "parent_sha256": parent,
            "line_number": line_number, "raw_tokens": raw,
            "extra_tokens": tokens[len(header):],
            "schema_state": "complete" if len(tokens) == len(header) else "field_count_mismatch",
            "station": station, "source_date": day.isoformat() if day else None,
            "source_role": role, "source_hour": hour,
            "timestamp_utc": stamp, "time_state": time_state,
            "T": numeric_cell(raw["T"], 10), "U": numeric_cell(raw["U"], 1),
        })
    if header is None:
        raise ValueError("No recognised KNMI header")
    return {"columns": header, "comments": comments, "rows": rows, "sha256": parent}


def expected_schedule(start, end, stations=(240, 260)):
    """Generate expected source keys without consulting observed rows."""
    if end < start:
        raise ValueError("Reversed schedule dates")
    day = start
    while day <= end:
        for station in stations:
            for hour in range(1, 25):
                yield {"station": station, "source_date": day.isoformat(), "source_hour": hour,
                       "source_role": role_for(day), "timestamp_utc": utc_for(day, hour).isoformat()}
        day += timedelta(days=1)


def inventory(rows, year, station):
    """Structural counts only. No detector-based filtering or clean-data labels."""
    require_mode("early_inventory", date(year, 1, 1))
    selected = [r for r in rows if r["source_date"] and
                r["source_date"].startswith(f"{year}-") and r["station"] == station]
    schedule = list(expected_schedule(date(year, 1, 1), date(year, 12, 31), (station,)))
    expected = {(s["source_date"], s["source_hour"]) for s in schedule}
    grouped = defaultdict(list)
    for row in selected:
        if row["time_state"] == "valid_hour":
            grouped[(row["source_date"], row["source_hour"])].append(row)
    absent = expected - grouped.keys()
    ambiguous = {key for key, group in grouped.items() if len(group) > 1}
    unique = {key: group[0] for key, group in grouped.items()
              if len(group) == 1 and key in expected and group[0]["schema_state"] == "complete"}
    variables = {}
    for var in ("T", "U"):
        finite = {k: r[var]["value"] for k, r in unique.items() if r[var]["state"] == "finite"}
        month_coverage = {}
        for month in range(1, 13):
            prefix = f"{year}-{month:02d}-"
            denominator = sum(k[0].startswith(prefix) for k in expected)
            numerator = sum(k[0].startswith(prefix) for k in finite)
            month_coverage[f"{month:02d}"] = {"finite_unique_slots": numerator,
                                            "expected_slots": denominator,
                                            "coverage": numerator / denominator}
        variables[var] = {"cell_states_all_rows": dict(Counter(r[var]["state"] for r in selected)),
                          "finite_unique_slots": len(finite), "coverage": len(finite) / len(expected),
                          "minimum": min(finite.values(), default=None),
                          "maximum": max(finite.values(), default=None),
                          "monthly": month_coverage}
    return {"year": year, "station": station, "source_role": EARLY_ROLES[year],
            "expected_slots": len(expected), "observed_rows": len(selected),
            "aligned_unique_keys": len(grouped.keys() & expected),
            "unambiguous_complete_rows": len(unique), "absent_slots": len(absent),
            "duplicate_keys": len(ambiguous),
            "additional_duplicate_rows": sum(len(v) - 1 for v in grouped.values()),
            "invalid_or_missing_time_rows": sum(r["time_state"] != "valid_hour" for r in selected),
            "schema_mismatch_rows": sum(r["schema_state"] != "complete" for r in selected),
            "first_expected_utc": schedule[0]["timestamp_utc"],
            "last_expected_utc": schedule[-1]["timestamp_utc"], "variables": variables}
