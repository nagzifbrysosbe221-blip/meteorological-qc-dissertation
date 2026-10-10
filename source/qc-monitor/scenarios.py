"""Appendix C1-C2 deterministic FABRICATED inventory and scenario construction.

Original records and construction metadata are private. Only Receipt fields cross
the monitor boundary. Physical-unit teaching scales are never accepted fit results.
"""

import calendar
import copy
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from data import numeric_cell
from replay import Receipt, utc

TEACHING_SCALES = {"kind": "fabricated_teaching_only", "T": 2.0, "U": 4.0}
FAMILIES = ("missing_cell", "absent_row", "duplicate_receipt", "off_grid_timestamp",
            "invalid_timestamp", "out_of_range", "gradual_bias")


def opaque(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:24]


def inventory():
    """Exactly 78 submitted variants per month, using fabricated source year 2000."""
    cases = []
    for month in range(1, 13):
        variants = []
        for var in ("T", "U"):
            variants += [dict(family="missing_cell", variable=var, length_hours=d) for d in (1, 6, 7, 24)]
        variants += [dict(family="absent_row", length_hours=d) for d in (1, 6, 7, 24)]
        for family, field, options in (("duplicate_receipt", "copies", (1, 2)),
                                       ("off_grid_timestamp", "minutes", (15, 30)),
                                       ("invalid_timestamp", "invalid_field", ("hour_0", "INVALID_DATE"))):
            variants += [dict(family=family, length_hours=d, **{field: v}) for v in options for d in (1, 6, 7)]
        for var, bounds in (("T", (-41, 51)), ("U", (-1, 101))):
            variants += [dict(family="out_of_range", variable=var, replacement=v, side=side, length_hours=d)
                         for side, v in zip(("lower", "upper"), bounds) for d in (1, 6, 24)]
            variants += [dict(family="gradual_bias", variable=var, sign=s, scale_multiple=a,
                              ramp_hours=d, length_hours=2*d)
                         for s in (-1, 1) for a in (.5, 1, 2) for d in (24, 72, 168)]
        for v in variants:
            variant = json.dumps(v, sort_keys=True, separators=(",", ":"))
            cases.append({**v, "variable": v.get("variable"), "variant": variant,
                          "case_id": f"synthetic-2000-{month:02d}-"+opaque(variant), "source_month": f"2000-{month:02d}",
                          "planned_onset": datetime(2000, month, 8, 1, tzinfo=timezone.utc).isoformat()})
    return cases


def month_window(month):
    """Source HH1..24: midnight belongs to the preceding source day/month.

    January begins source day 2; source day 1 is available as unscored context.
    Other months have 14 preceding source days. Drain adds no slots.
    """
    first = datetime(2000, month, 2 if month == 1 else 1, 1, tzinfo=timezone.utc)
    last = datetime(2000, month, calendar.monthrange(2000, month)[1], tzinfo=timezone.utc)+timedelta(days=1)
    start = max(first-timedelta(days=14), datetime(2000, 1, 1, 1, tzinfo=timezone.utc))
    slots = [(start+timedelta(hours=i)).isoformat() for i in range(int((last-start).total_seconds()/3600)+1)]
    return {"month": f"2000-{month:02d}", "slots": slots, "first_scored": first.isoformat(),
            "last_scored": last.isoformat(), "drain_until": (last+timedelta(hours=1)).isoformat(),
            "context_hours": int((first-start).total_seconds()/3600),
            "scored_hours": int((last-first).total_seconds()/3600)+1}


def fabricated_originals(slots):
    rows = []
    for stamp in slots:
        t = utc(stamp)
        source_date = (t-timedelta(hours=1)).date().isoformat()
        for station in (260, 240):
            rows.append({"identity": opaque(station, stamp), "station": station,
                         "source_date": source_date, "source_hour": t.hour or 24,
                         "timestamp": stamp, "T": "10", "U": "50",
                         "nominal_delivery": stamp})
    return rows


def original_time(row):
    try:
        return utc(row["timestamp"])
    except (ValueError, TypeError):
        return None


def delivery(row):
    t = original_time(row)
    if t is not None:
        return t.isoformat()
    # Only an independently supplied nominal delivery can release an invalid original.
    return utc(row["nominal_delivery"]).isoformat() if row.get("nominal_delivery") else None


def public_copy(row):
    return {k: row[k] for k in ("identity", "station", "timestamp", "T", "U")} | {"received_at": delivery(row)}


def planned_root(case, status="pending", reason=None):
    n = case["length_hours"]
    onset = utc(case["planned_onset"])
    return {**copy.deepcopy(case), "root_id": "root:"+case["case_id"], "construction": status,
            "N": n, "C": 0, "Z": 0, "U": n, "support": "none", "reason": reason,
            "accounting_assessed": status != "pending", "unassessed_units": n if status == "pending" else 0,
            "planned_end": (onset+timedelta(hours=n-1)).isoformat(),
            "restoration_at": (onset+timedelta(hours=n)).isoformat(),
            "first_effect": None, "last_effect": None, "members": [], "actual_change_mask": [],
            "added_identities": [], "deleted_identities": [], "consequences": [], "range_crossing": False}


def build(case, originals, scales=TEACHING_SCALES):
    return _build(case, originals, scales, 'fabricated_teaching_only')


def _build(case, originals, scales, scale_kind):
    """Return public/control copies and private lineage, or an explicit blocked/error.

    Atomic construction: an error returns no executable partial copy. Originals
    are never changed. Unsupported units stay untouched at their original anchor.
    """
    root = planned_root(case)
    result = {"public": None, "counterpart": None, "root": root, "unit_accounting": [],
              "lineage": [], "scales": copy.deepcopy(scales), "purpose": "fabricated correctness only"}
    try:
        if case["family"] not in FAMILIES or case["length_hours"] <= 0:
            raise ValueError("Unknown family or invalid duration")
        if any(delivery(r) is None for r in originals):
            root.update(construction="blocked", reason="original_invalid_time_without_nominal_delivery", accounting_assessed=True, unassessed_units=0)
            return result
        if case["family"] == "gradual_bias" and (not scales or scales.get("kind") != scale_kind or
                not isinstance(scales.get(case["variable"]), (int, float)) or
                not math.isfinite(scales[case["variable"]]) or scales[case["variable"]] <= 0):
            root.update(construction="blocked", reason="missing_or_invalid_explicit_teaching_scale", accounting_assessed=True, unassessed_units=0)
            return result
        if len({r["identity"] for r in originals}) != len(originals):
            raise ValueError("Original identities must be unique even for duplicate keys")
        control = [public_copy(r) for r in originals]
        current = {r["identity"]: copy.deepcopy(r) for r in control}
        groups = defaultdict(list)
        for r in originals:
            groups[(r["station"], original_time(r))].append(r)
        added, removed = defaultdict(list), set()
        root.update(C=0, Z=0, U=0, accounting_assessed=True, unassessed_units=0)
        family, var = case["family"], case.get("variable")
        for j in range(1, case["length_hours"]+1):
            stamp = (utc(case["planned_onset"])+timedelta(hours=j-1)).isoformat()
            group = groups[(260, utc(stamp))]
            reason = "original_row_absent" if not group else "original_key_not_unique" if len(group) != 1 else None
            r = group[0] if len(group) == 1 else None
            cell = numeric_cell(r[var], 1) if r and var else None
            if reason is None and family in {"out_of_range", "gradual_bias"} and cell["state"] != "finite":
                reason = "selected_value_"+cell["state"]
            destination = (utc(stamp)+timedelta(minutes=case["minutes"])).isoformat() if family == "off_grid_timestamp" else None
            if reason is None and destination and groups[(260, utc(destination))]:
                reason = "off_grid_destination_collision"
            entry = {"nominal_slot": stamp, "scheduled_j": j, "parent_ids": [x["identity"] for x in group],
                     "status": "unsupported" if reason else None, "reason": reason}
            result["unit_accounting"].append(entry)
            if reason:
                root["U"] += 1
                continue
            rid = r["identity"]
            old = copy.deepcopy(current[rid])
            new = current[rid]
            if family == "missing_cell":
                new[var] = ""
                changed = cell["state"] != "missing"
                if not changed: new[var] = old[var]  # preserve an already missing raw token
            elif family == "absent_row":
                removed.add(rid)
                changed = True
                root["deleted_identities"].append(rid)
            elif family == "duplicate_receipt":
                for k in range(case["copies"]):
                    extra = {**new, "identity": opaque(case["case_id"], rid, k)}
                    if extra["identity"] in current:
                        raise ValueError("Added identity collision")
                    added[rid].append(extra)
                    root["added_identities"].append(extra["identity"])
                changed = True
            elif family == "off_grid_timestamp":
                new["timestamp"] = destination
                changed = True
            elif family == "invalid_timestamp":
                # Raw source tokens, not ISO midnight: source HH=0 is INVALID.
                new["timestamp"] = (r["source_date"].replace("-", "")+",0" if case["invalid_field"] == "hour_0"
                                    else "INVALID_DATE,"+str(r["source_hour"]))
                changed = True
            else:
                value = float(case["replacement"]) if family == "out_of_range" else (
                    cell["value"]+case["sign"]*case["scale_multiple"]*scales[var]*min(j/case["ramp_hours"], 1))
                if not math.isfinite(value):
                    raise ValueError("Numeric transformation overflow; no partial scenario is executable")
                changed = value != cell["value"]
                if changed: new[var] = repr(value)
                lo, hi = (-40, 50) if var == "T" else (0, 100)
                if family == "gradual_bias" and changed and not lo <= value <= hi:
                    root["range_crossing"] = True
            entry["status"] = "changed" if changed else "supported_unchanged"
            root["C" if changed else "Z"] += 1
            if not changed:
                continue
            root["actual_change_mask"].append(stamp)
            subjects = []
            if family == "missing_cell": subjects = [["Q02", rid+":"+var]]
            elif family == "duplicate_receipt": subjects = [["Q06", x["identity"]] for x in added[rid]]
            elif family in {"out_of_range", "gradual_bias"}: subjects = [[c, rid+":"+var] for c in ("Q03", "S01")]
            else:
                subjects = [["Q01", "slot:"+stamp]]
                if family != "absent_row": subjects.insert(0, ["Q05" if family == "off_grid_timestamp" else "Q04", rid])
                root["consequences"].append({"root_id": root["root_id"], "kind": "absent_slot", "slot": stamp,
                                             "parent_id": rid, "subject_id": "slot:"+stamp})
            root["members"].extend(subjects)
        public = []
        for r in originals:
            rid = r["identity"]
            if rid not in removed: public.append(current[rid])
            public.extend(added[rid])  # same delivery; original is first in stable order
            result["lineage"].append({"identity": rid, "parent_id": rid, "original": copy.deepcopy(r),
                                      "disposition": "deleted" if rid in removed else "changed" if current[rid] != public_copy(r) else "unchanged"})
            for extra in added[rid]:
                result["lineage"].append({"identity": extra["identity"], "parent_id": rid, "disposition": "added"})
        if len({r["identity"] for r in public}) != len(public): raise ValueError("Nonunique output identity")
        for row in public: Receipt.from_public(row)
        root.update(construction="effective" if root["C"] else "no_effect",
                    support="full" if root["U"] == 0 else "none" if root["U"] == root["N"] else "partial",
                    first_effect=root["actual_change_mask"][0] if root["C"] else None,
                    last_effect=root["actual_change_mask"][-1] if root["C"] else None)
        assert root["N"] == root["C"]+root["Z"]+root["U"]
        result.update(public=public, counterpart=control)
        return result
    except Exception as exc:
        result.update(public=None, counterpart=None, lineage=[], unit_accounting=[])
        result["root"] = planned_root(case, "generation_error", type(exc).__name__+": "+str(exc))
        return result
