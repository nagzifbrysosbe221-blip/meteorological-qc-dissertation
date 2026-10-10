"""Private structural/numeric fixture truth, independent of detector predictions.

Call after monitoring. Fabricated finite unchanged values are assumed normal;
this function is not an early-data acceptance decision or a real-data labeller.
"""

from collections import defaultdict
from datetime import timedelta

from data import numeric_cell
from evaluation_adapter import unit_key
from replay import utc
from scenarios import delivery


def primary_task(root, task, var):
    family = root["family"]
    if family in {"out_of_range", "gradual_bias"}: return task == "value" and var == root["variable"]
    if family == "missing_cell": return task == "availability" and var == root["variable"]
    if family == "absent_row": return task == "availability"
    return task == {"duplicate_receipt": "Q06", "off_grid_timestamp": "Q05", "invalid_timestamp": "Q04"}[family]


def private_truth(generated, window, *, counterpart=False):
    public = generated["counterpart" if counterpart else "public"]
    if public is None: raise ValueError("Blocked/failed construction has no evaluable truth")
    root = generated["root"]
    case = root["case_id"]+("-control" if counterpart else "")
    changed = set() if counterpart else set(root["actual_change_mask"])
    added = set() if counterpart else set(root["added_identities"])
    members = set() if counterpart else {tuple(x) for x in root["members"]}
    originals = {x["identity"]: x["original"] for x in generated["lineage"] if "original" in x}
    parents = {x["identity"]: x["parent_id"] for x in generated["lineage"]}
    aligned, seen, labels = defaultdict(list), set(), {}
    slots = set(window["slots"])

    def label(task, subject, var, value, usable, nominal, source_month, synthetic=False):
        scope = "scored" if utc(window["first_scored"]) <= utc(nominal) <= utc(window["last_scored"]) else "context"
        labels[unit_key(case, task, subject, var)] = {
            "truth": value, "usable": usable, "scope": scope, "source_month": source_month,
            "origin": "synthetic_positive" if synthetic and value is True else "natural_positive" if value is True else
                      "unknown" if value is None else "assumed_normal" if task == "value" else "structural_inventory",
            "primary_task": primary_task(root, task, var)}

    for _, row in sorted(enumerate(public), key=lambda x: (utc(x[1]["received_at"]), x[0])):
        if row["station"] != 260: continue
        parent = originals[parents[row["identity"]]]
        nominal = delivery(parent)  # valid original observation time is authoritative
        try: stamp = utc(row["timestamp"])
        except (ValueError, TypeError): stamp = None
        key = (260, stamp)
        invalid = stamp is None
        offgrid = None if invalid else bool(stamp.minute or stamp.second or stamp.microsecond)
        duplicate = None if invalid else key in seen
        if stamp is not None:
            seen.add(key)
            if stamp.isoformat() in slots and utc(row["received_at"]) <= stamp+timedelta(minutes=5):
                aligned[stamp.isoformat()].append(row)
        for task, value in (("Q04", invalid), ("Q05", offgrid), ("Q06", duplicate)):
            synthetic = (task, row["identity"]) in members or (task == "Q06" and row["identity"] in added)
            label(task, row["identity"], None, value, value is not None, nominal, parent["source_date"][:7], synthetic)
    for slot in window["slots"]:
        group = aligned[slot]
        month = (utc(slot)-timedelta(hours=1)).strftime("%Y-%m")
        for var in ("T", "U"):
            cells = [numeric_cell(r[var], 1) for r in group]
            absent = not group
            missing = any(c["state"] == "missing" for c in cells)
            unknown_availability = any(r[var] is None for r in group)
            availability = True if absent or missing else None if unknown_availability else False
            synthetic_availability = ("Q01", "slot:"+slot) in members or any(("Q02", r["identity"]+":"+var) in members for r in group)
            label("availability", slot, var, availability, True, slot, month, synthetic_availability)
            usable = len(group) == 1 and cells[0]["state"] == "finite"
            positive = slot in changed and var == root["variable"] and root["family"] in {"out_of_range", "gradual_bias"}
            label("value", slot, var, positive if usable else None, usable, slot, month, positive)
    return {"case_id": case, "units": labels, "roots": [] if counterpart else [root],
            "purpose": "private fabricated truth only; not acceptance of real observations"}
