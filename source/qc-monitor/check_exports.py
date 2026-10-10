"""Verify saved early views, round-trip exports and a repeated derivation."""

import csv
import json
from datetime import datetime, timezone

from records import ROOT, code_identity, save_json, sha256, utc_now

VIEWS = [
    "early-2021-20261007T172858741722Z",
    "early-2022-20261007T172859445529Z",
    "early-2023-20261007T172859087578Z",
]
REPEAT = "early-2021-20261007T173011130766Z"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_view(name):
    view = ROOT / "data" / "derived" / name
    report = json.loads((view / "manifest.json").read_text(encoding="utf-8"))
    for filename, identity in report["outputs"].items():
        check(sha256(view / filename) == identity["sha256"], f"Output hash: {filename}")
    snapshot_manifest = ROOT / report["input_manifest"]
    source = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
    check(sha256(snapshot_manifest.parent / "response.txt") == source["sha256"], "Raw hash")
    with (view / "records.jsonl").open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    with (view / "values.csv").open(encoding="utf-8", newline="") as stream:
        exported = list(csv.DictReader(stream))
    check(len(rows) == len(exported) == report["returned_rows"], "Row count")
    check(len({r["record_id"] for r in rows}) == len(rows), "Distinct physical-row lineage")
    for row, flat in zip(rows, exported):
        check(row["record_id"] == flat["record_id"], "Export order and identity")
        check(row["raw_tokens"]["T"] == flat["T_raw"], "Raw T token")
        check(row["raw_tokens"]["U"] == flat["U_raw"], "Raw U token")
        for var, column in [("T", "T_C"), ("U", "U_percent")]:
            value = float(flat[column]) if flat[column] else None
            check(value == row[var]["value"], f"Export numeric value: {var}")
            check(flat[f"{var}_state"] == row[var]["state"], f"Export state: {var}")
        check(row["source_role"] != "reserved_final", "Final values remain unprocessed")
    with (view / "schedule.csv").open(encoding="utf-8", newline="") as stream:
        schedule = list(csv.DictReader(stream))
    check(len(schedule) == sum(i["expected_slots"] for i in report["inventories"]), "Schedule count")
    check(len({(s["station"], s["timestamp_utc"]) for s in schedule}) == len(schedule), "Unique schedule")
    return {"view": name, "rows_round_tripped": len(rows), "schedule_slots": len(schedule),
            "hashes_verified": True, "boundary_source_dates": report["boundary_source_dates"]}


def main():
    record = {"started_utc": utc_now(), "code": code_identity(), "views": []}
    try:
        for name in VIEWS:
            record["views"].append(verify_view(name))
        record["repeat_view"] = verify_view(REPEAT)
        first = ROOT / "data" / "derived" / VIEWS[0]
        second = ROOT / "data" / "derived" / REPEAT
        record["repeat_hashes"] = {}
        for name in ("records.jsonl", "values.csv", "schedule.csv", "issues.jsonl"):
            check(sha256(first / name) == sha256(second / name), f"Repeated payload differs: {name}")
            record["repeat_hashes"][name] = sha256(first / name)
        record["repeat_policy"] = "Byte equality for all four data files; manifests retain distinct timing and paths."
        record["success"] = True
    except Exception as error:
        record.update(success=False, error=repr(error))
    record["finished_utc"] = utc_now()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = ROOT / "evidence" / "data-foundation" / f"exports-{stamp}.json"
    save_json(path, record)
    print(json.dumps(record, indent=2))
    print(path)
    return 0 if record["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
