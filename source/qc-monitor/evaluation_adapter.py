"""Read-only adapter from completed synthetic replay records to scoring units.

This module never imports or calls the detector. Expected rows are reconstructed
from the public schedule/receipts, not from truth or the rows that happen to exist.
"""

from collections import defaultdict
from datetime import datetime, timedelta

CONFIGS = ("B0", "R", "H")
TASKS = ("value", "availability", "Q04", "Q05", "Q06")


class IncompleteRun(ValueError):
    """An execution/completeness problem, never a scientific miss."""


def time(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Explicit UTC required")
    return value


def unit_key(case, task, subject, variable=None):
    return "|".join((case, task, subject, variable or "-"))


def combined(rows):
    applicable = [r for r in rows if r["execution"] != "inapplicable"]
    complete = bool(applicable) and all(r["execution"] == "evaluated" for r in applicable)
    flag = any(r["prediction"] is True for r in applicable)
    return {"applicable": bool(applicable), "complete": complete,
            "prediction": True if flag else (False if complete else None)}


def adapt(run, public, slots, *, case_id):
    """Validate a complete fixture run and return units without consulting truth.

    Complete means the whole *declared fixture window* and its one-hour drain.
    It does not assert a tiny fixture is a complete scientific source month.
    """
    if run.get("run_status") != "complete":
        raise IncompleteRun("Run is not complete")
    dates = [time(s) for s in slots]
    if not dates or any(b-a != timedelta(hours=1) for a, b in zip(dates, dates[1:])):
        raise IncompleteRun("Schedule must be nonempty, unique and hourly")
    if run.get("slots_closed") != len(slots) or time(run["drain_until"]) != dates[-1]+timedelta(hours=1):
        raise IncompleteRun("Declared window/drain was truncated")
    if len({r["identity"] for r in public}) != len(public):
        raise IncompleteRun("Duplicate opaque receipt identity")
    rows = run["ledger"]
    index = {}
    ids = set()
    for r in rows:
        key = (r["configuration"], r["subject_id"], r["check"])
        if key in index or r["result_id"] in ids:
            raise IncompleteRun("Duplicate result identity or check subject")
        if r["execution"] not in {"evaluated", "unevaluated", "inapplicable"}:
            raise IncompleteRun("Failed/invalid check execution")
        if (r["execution"] == "evaluated" and type(r["prediction"]) is not bool) or (
                r["execution"] != "evaluated" and r["prediction"] is not None):
            raise IncompleteRun("Prediction/execution contract violated")
        index[key] = r
        ids.add(r["result_id"])
    expected, units = set(), []

    def get(cfg, subject, check, emission):
        key = (cfg, subject, check)
        expected.add(key)
        if key not in index:
            raise IncompleteRun(f"Missing expected result: {key}")
        row = index[key]
        if time(row["emitted_at"]) != emission:
            raise IncompleteRun(f"Wrong emission time: {key}")
        return row

    def add(cfg, task, subject, var, stamp, order, checks):
        units.append({"key": unit_key(case_id, task, subject, var), "case_id": case_id,
                      "configuration": cfg, "task": task, "variable": var,
                      "subject": subject, "time": stamp, "order": order,
                      "checks": checks, **combined(checks)})

    target = sorted(((i, r) for i, r in enumerate(public) if r["station"] == 260),
                    key=lambda item: (time(item[1]["received_at"]), item[0]))
    aligned = defaultdict(list)
    for order, r in target:
        delivery = time(r["received_at"])
        if not dates[0] <= delivery <= dates[-1]+timedelta(hours=1):
            raise IncompleteRun("Receipt outside declared replay window/drain")
        try:
            stamp = time(r["timestamp"])
        except (TypeError, ValueError):
            stamp = None
        if stamp in set(dates) and delivery <= stamp+timedelta(minutes=5):
            aligned[stamp].append(r)
        for cfg in CONFIGS:
            for check in ("Q04", "Q05", "Q06"):
                row = get(cfg, r["identity"], check, delivery)
                should_apply = cfg != "B0" and (check == "Q04" or stamp is not None)
                if (row["execution"] != "inapplicable") != should_apply:
                    raise IncompleteRun("Timestamp applicability disagrees with current input")
                add(cfg, check, r["identity"], None, r["received_at"], order, [row])
    summaries = {}
    for s in run["summaries"]:
        key = (s["configuration"], s["slot_utc"], s["variable"], s["domain"])
        if key in summaries:
            raise IncompleteRun("Duplicate domain summary")
        summaries[key] = s
    expected_summaries = set()
    for order, (stamp_text, stamp) in enumerate(zip(slots, dates)):
        group = aligned[stamp]
        slot = "slot:" + stamp_text
        emission = stamp + timedelta(minutes=5)
        for cfg in CONFIGS:
            q01 = get(cfg, slot, "Q01", emission)
            for var in ("T", "U"):
                availability = [q01] + [get(cfg, r["identity"]+":"+var, "Q02", emission) for r in group]
                subject = (group[0]["identity"] if len(group) == 1 else slot) + ":" + var
                value = [get(cfg, subject, "Q03", emission)]
                if cfg == "H":
                    value.append(get(cfg, subject, "S01", emission))
                for task, checks in (("value", value), ("availability", availability)):
                    if any(r["execution"] == "inapplicable" for r in checks):
                        raise IncompleteRun("Required hourly check incorrectly inapplicable")
                    add(cfg, task, stamp_text, var, stamp_text, order, checks)
                    key = (cfg, stamp_text, var, task)
                    expected_summaries.add(key)
                    s = summaries.get(key)
                    state = combined(checks)
                    if s is None or s["contributors"] != [r["result_id"] for r in checks] or any(
                            s[k] != state[k] for k in ("complete", "prediction")):
                        raise IncompleteRun("Missing/inconsistent summary or contributors")
    if expected != set(index) or expected_summaries != set(summaries):
        raise IncompleteRun("Unexpected ledger/summary rows")
    states = {s["state_id"]: s for s in run["states"]}
    if len(states) != len(run["states"]) or len(states) != 2*len(slots):
        raise IncompleteRun("State export is incomplete or duplicated")
    linked = set()
    for r in rows:
        if r["check"] == "S01":
            state_id = r["evidence"]["state_id"]
            s = states.get(state_id)
            if s is None or any(s[k] != r[k] for k in ("prediction", "execution", "emitted_at", "variable")):
                raise IncompleteRun("Broken statistical state link")
            linked.add(state_id)
    if linked != set(states):
        raise IncompleteRun("Unreferenced/missing statistical states")
    return units


def run_health(run, public, slots, *, case_id):
    """Keep an invalid run outside scientific denominators, with its reason."""
    try:
        units = adapt(run, public, slots, case_id=case_id)
    except (ValueError, KeyError, TypeError) as exc:
        return {"valid": False, "reason": str(exc), "units": []}
    return {"valid": True, "reason": None, "units": units}
