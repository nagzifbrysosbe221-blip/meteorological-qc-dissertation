"""Build a verified early-data view; this is not a detection experiment."""

import argparse
import csv
import json
import os
import platform
import shutil
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from acquire import early_parameters
from data import expected_schedule, inventory, parse_knmi, require_mode
from records import ROOT, code_identity, save_json, settings, sha256, utc_now


def json_line(stream, value):
    stream.write(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n")


def build(snapshot, year):
    require_mode("early_inventory", date(year, 1, 1))
    if settings()["final_access_enabled"]:
        raise ValueError("Final intake is not implemented in this component")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest["parameters"] != early_parameters(year):
        raise ValueError("Snapshot request does not match the selected early year")
    raw = snapshot / "response.txt"
    if manifest["sha256"] != sha256(raw):
        raise ValueError("Raw snapshot hash mismatch")
    started = utc_now()
    tick = time.perf_counter()
    parsed = parse_knmi(raw.read_bytes())
    rows = parsed["rows"]
    inventories = [inventory(rows, year, s) for s in (240, 260)]
    prefix = f"{year}-"
    selected = [r for r in rows if r["source_date"] and r["source_date"].startswith(prefix)]
    boundary = [r for r in rows if r["source_date"] and not r["source_date"].startswith(prefix)]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    view = ROOT / "data" / "derived" / f"early-{year}-{stamp}"
    view.mkdir(parents=True, exist_ok=False)
    issues = []
    key_counts = Counter((r["station"], r["timestamp_utc"]) for r in selected
                         if r["time_state"] == "valid_hour")
    with (view / "records.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            reasons = []
            if row["source_date"] is None:
                reasons.append("unassignable_source_date")
            elif not row["source_date"].startswith(prefix):
                reasons.append("outside_requested_source_year")
            if row["station"] not in (240, 260):
                reasons.append("unknown_or_unselected_station")
            if row["time_state"] != "valid_hour":
                reasons.append("missing_or_invalid_source_time")
            if row["schema_state"] != "complete":
                reasons.append("field_count_mismatch")
            if (row["source_date"] and row["source_date"].startswith(prefix)
                    and key_counts[(row["station"], row["timestamp_utc"])] > 1):
                reasons.append("ambiguous_duplicate_key")
            for var in ("T", "U"):
                if row[var]["state"] != "finite":
                    reasons.append(f"{var}_{row[var]['state']}")
            json_line(stream, row | {"view_reasons": reasons})
            if reasons:
                issues.append({"record_id": row["record_id"], "reasons": reasons})
    with (view / "schedule.csv").open("x", encoding="utf-8", newline="") as stream:
        fields = ["station", "source_date", "source_hour", "source_role", "timestamp_utc"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for slot in expected_schedule(date(year, 1, 1), date(year, 12, 31)):
            writer.writerow(slot)
            if key_counts[(slot["station"], slot["timestamp_utc"])] == 0:
                issues.append({"slot": slot, "reasons": ["absent_expected_slot"]})
    with (view / "values.csv").open("x", encoding="utf-8", newline="") as stream:
        fields = ["record_id", "station", "source_date", "source_role", "source_hour",
                  "timestamp_utc", "time_state", "schema_state", "T_raw", "T_C", "T_state",
                  "U_raw", "U_percent", "U_state"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fields[:8]} | {
                "T_raw": row["raw_tokens"]["T"], "T_C": row["T"]["value"], "T_state": row["T"]["state"],
                "U_raw": row["raw_tokens"]["U"], "U_percent": row["U"]["value"], "U_state": row["U"]["state"]})
    with (view / "issues.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for issue in issues:
            json_line(stream, issue)
    report = {
        "kind": "early_structural_inventory_not_detector_results", "year": year,
        "started_utc": started, "finished_utc": utc_now(),
        "elapsed_seconds": time.perf_counter() - tick,
        "input_manifest": str((snapshot / "manifest.json").relative_to(ROOT)),
        "input_sha256": parsed["sha256"], "code": code_identity(),
        "source_columns": parsed["columns"], "source_comments": parsed["comments"],
        "returned_rows": len(rows), "selected_year_rows": len(selected),
        "boundary_rows": len(boundary),
        "boundary_source_dates": dict(Counter(r["source_date"] for r in boundary)),
        "unassignable_date_rows": sum(r["source_date"] is None for r in rows),
        "unexpected_station_rows": sum(r["station"] not in (240, 260) for r in rows),
        "inventories": inventories, "issues": len(issues),
        "snapshot_terms_record": "data/source-docs/Source-Access-and-Terms-2026-10-07.md",
        "environment": {"python": sys.version, "executable": sys.executable,
                        "os": platform.platform(), "machine": platform.machine(),
                        "logical_cpus": os.cpu_count(), "free_disk_bytes": shutil.disk_usage(ROOT).free,
                        "runtime_dependencies": "Python standard library only"},
        "outputs": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                    for p in sorted(view.iterdir())},
        "acceptance": "structural_inventory_complete; scientific_acceptance_pending",
        "limitations": ["No model fit, calibration, detector or evaluation has run.",
                        "Station-history and independent uncertainty review remain pending.",
                        "Complete archive coverage does not establish physical sensor health.",
                        "Timing is data preparation only, not replay or full-batch feasibility."],
    }
    save_json(view / "manifest.json", report)
    print(json.dumps({"view": str(view.relative_to(ROOT)), "returned_rows": len(rows),
                      "selected_year_rows": len(selected), "boundary_rows": len(boundary),
                      "elapsed_seconds": report["elapsed_seconds"],
                      "inventories": [{k: item[k] for k in ("station", "expected_slots", "observed_rows",
                                                            "absent_slots", "duplicate_keys")}
                                      for item in inventories]}))
    return view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True,
                        help="Snapshot directory relative to 19-Completion-Work, or absolute")
    parser.add_argument("--year", type=int, choices=[2021, 2022, 2023], required=True)
    args = parser.parse_args()
    snapshot = args.snapshot if args.snapshot.is_absolute() else ROOT / args.snapshot
    try:
        build(snapshot, args.year)
    except Exception as error:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        save_json(ROOT / "evidence" / "data-foundation" / f"failed-{stamp}.json",
                  {"outcome": "execution_failed", "finished_utc": utc_now(),
                   "snapshot": str(snapshot), "year": args.year,
                   "error": repr(error), "code": code_identity()})
        raise


if __name__ == "__main__":
    main()
