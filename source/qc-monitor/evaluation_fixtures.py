"""Small fabricated ledgers/public inputs and separate private truth builders.

Never imported by replay/ewma. Expected answers live in evaluator_expected.json,
not in these builders and not computed by evaluator functions.
"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from evaluation_adapter import unit_key
from fixtures import fixture_settings, receipt, schedule


def label(value, usable=True, scope="scored", origin=None, month="2000-01"):
    return {"truth": value, "usable": usable, "scope": scope,
            "origin": origin or ("synthetic_positive" if value else "assumed_normal"), "source_month": month}


def check(cfg, subject, code, flag, stamp, case="manual", execution=None):
    return {"result_id": f"{case}/{cfg}/{subject}/{code}", "configuration": cfg,
            "subject_id": subject, "check": code, "prediction": flag,
            "execution": execution or ("unevaluated" if flag is None else "evaluated"), "emitted_at": stamp}


def value_unit(cfg, subject, stamp, q03, s01=False, case="points", order=0):
    checks = [check(cfg, subject, "Q03", q03, stamp, case)]
    if cfg == "H":
        checks.append(check(cfg, subject, "S01", s01, stamp, case))
    # Fixture composites are explicitly determined here, independent of evaluator.
    complete = q03 is not None and (cfg != "H" or s01 is not None)
    flag = q03 is True or (cfg == "H" and s01 is True)
    return {"key": unit_key(case, "value", subject, "T"), "case_id": case, "configuration": cfg,
            "task": "value", "variable": "T", "subject": subject, "time": stamp, "order": order,
            "checks": checks, "applicable": True, "complete": complete,
            "prediction": True if flag else False if complete else None}


def cohort_fixture():
    units, truth = [], {}
    stamps = schedule(6)
    # a: partial H flag; b: missed positive; c: H false positive; d: warm-up;
    # e: unknown label; f: unusable value. Flag sign must not select the cohort.
    for i, (subject, y, usable, q03, stat) in enumerate([
        ("a", True, True, True, None), ("b", True, True, False, False),
        ("c", False, True, False, True), ("d", False, True, False, None),
        ("e", None, True, False, False), ("f", None, False, None, None)]):
        for cfg in ("B0", "R", "H"):
            units.append(value_unit(cfg, subject, stamps[i], q03, stat, order=i))
        truth[unit_key("points", "value", subject, "T")] = label(y, usable)
    return units, truth


def tiny_public():
    slots = schedule(9)
    rows = []
    for i, s in enumerate(slots):
        rows.append(receipt("ref"+str(i), s, station=240, T="10"))
        if i == 2:
            continue
        r = receipt(chr(97+i), s, T="" if i == 1 else "51" if i == 6 else "14")
        if i == 4:
            r = replace(r, timestamp=(datetime.fromisoformat(s)+timedelta(minutes=15)).isoformat())
        if i == 5:
            r = replace(r, timestamp="INVALID_DATE")
        rows.append(r)
        if i == 3:
            rows.append(replace(r, identity="d2", received_at=(datetime.fromisoformat(s)+timedelta(minutes=1)).isoformat()))
    return slots, rows, fixture_settings()


def root(rid, case, family, stamp, members, length=1, variable=None):
    return {"root_id": rid, "case_id": case, "family": family, "variable": variable,
            "variant": "tiny_teaching", "support": "full", "construction": "effective",
            "N": length, "C": length, "Z": 0, "U": 0, "planned_onset": stamp,
            "length_hours": length, "first_effect": stamp, "members": [list(x) for x in members]}


def tiny_private():
    """Explicit independent masks for the nine-hour fixture; no detector reads."""
    slots = schedule(9)
    truth = {}
    for i, s in enumerate(slots):
        for var in ("T", "U"):
            available_positive = i in ({1, 2, 4, 5} if var == "T" else {2, 4, 5})
            truth[unit_key("tiny", "availability", s, var)] = label(available_positive, origin="structural_inventory")
            usable = i in ({0, 6, 7, 8} if var == "T" else {0, 1, 6, 7, 8})
            truth[unit_key("tiny", "value", s, var)] = label(i == 6 and var == "T" if usable else None, usable)
    for subject in ("a", "b", "d", "d2", "e", "f", "g", "h", "i"):
        for task in ("Q04", "Q05", "Q06"):
            y = subject == {"Q04": "f", "Q05": "e", "Q06": "d2"}[task]
            truth[unit_key("tiny", task, subject)] = label(None if subject == "f" and task != "Q04" else y,
                                                        origin="structural_inventory")
    roots = [root("missing", "tiny", "missing_cell", slots[1], [("Q02", "b:T")], variable="T"),
             root("absent", "tiny", "absent_row", slots[2], [("Q01", "slot:"+slots[2])]),
             root("duplicate", "tiny", "duplicate_receipt", slots[3], [("Q06", "d2")]),
             root("offgrid", "tiny", "off_grid_timestamp", slots[4], [("Q05", "e"), ("Q01", "slot:"+slots[4])]),
             root("invalid", "tiny", "invalid_timestamp", slots[5], [("Q04", "f"), ("Q01", "slot:"+slots[5])]),
             root("range", "tiny", "out_of_range", slots[6], [("Q03", "g:T"), ("S01", "g:T")], variable="T")]
    return {"purpose": "private synthetic truth, joined only after monitoring", "units": truth, "roots": roots}


def healthy(cases):
    return {(c, cfg): {"valid": True, "reason": None} for c in cases for cfg in ("B0", "R", "H")}


def hourly_nodes():
    start = datetime(2000, 1, 31, 23, tzinfo=timezone.utc)
    nodes = []
    for i, flag in enumerate((True, True, True, True, None, True, False, True)):
        nodes.append({"key": str(i), "case_id": "episode", "configuration": "H", "variable": "T",
                      "domain": "value", "prediction": flag, "time": (start+timedelta(hours=i)).isoformat(),
                      "order": i, "contributors": ["r"+str(i)] if flag else []})
    return nodes
