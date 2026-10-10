"""Shared causal B0/R/H kernel and the guarded teaching replay entry point.

Inputs contain current fields and receipt times only. Private truth and original
repair timestamps are deliberately absent from this module's API.
"""

from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

from data import numeric_cell
from ewma import EWMA, FixtureSettings

CONFIGS = ("B0", "R", "H")
BOUNDS = {"T": (-40.0, 50.0), "U": (0.0, 100.0)}
UNITS = {"T": "degC", "U": "%"}
PROTOCOL = "submitted-v2-synthetic-replay-1"


def utc(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Explicit UTC time is required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Receipt:
    identity: str
    station: int
    received_at: str
    timestamp: str
    T: str | None
    U: str | None

    @classmethod
    def from_public(cls, value):
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Unexpected or missing public fields; private metadata is forbidden")
        return cls(**value)

    def current_time(self):
        try:
            return utc(self.timestamp)
        except (ValueError, TypeError):
            return None


def outcome(execution, prediction, reason, condition, **evidence):
    if execution in {"unevaluated", "inapplicable"} and prediction is not None:
        raise ValueError("Unavailable predictions must be null")
    return {"execution": execution, "prediction": prediction, "reason": reason,
            "data_condition": condition, "evidence": evidence}


def composite(results):
    """Keep partial flags; an unavailable check never becomes a clean negative."""
    applicable = [r for r in results if r["execution"] != "inapplicable"]
    complete = bool(applicable) and all(r["execution"] == "evaluated" for r in applicable)
    positive = any(r["prediction"] is True for r in applicable)
    execution = "evaluated" if positive or complete else ("unevaluated" if applicable else "inapplicable")
    return {"execution": execution, "prediction": True if positive else (False if complete else None),
            "complete": complete, "contributors": [r["result_id"] for r in results]}


def replay(receipts, slots, settings, *, mode="synthetic", run_id="synthetic-demo"):
    """Replay a supplied independent schedule. No labels, model fit or defaults.

    A timestamp belongs to the hourly grid if its minute/second are zero; range
    membership is separately handled by the supplied schedule. Equal-time receipt
    order is the persisted input order, before closure. Drain is one hour.
    """
    if mode != "synthetic":
        raise ValueError("Only correctness demonstrations are enabled; scientific replay is not accepted")
    if any(type(p) is not FixtureSettings for p in settings.values()):
        raise ValueError("Teaching replay requires explicit FixtureSettings")
    boundary = datetime(2001, 1, 1, tzinfo=timezone.utc)
    if not slots or any(utc(s).year != 2000 and utc(s) != boundary for s in slots):
        raise ValueError("Fixture schedule must use fabricated year 2000")
    return _replay(receipts, slots, settings, run_id=run_id, protocol=PROTOCOL,
                   purpose="synthetic correctness demonstration; not research findings", bounds=BOUNDS)


def _replay(receipts, slots, settings, *, run_id, protocol, purpose, bounds, sinks=None):
    """Shared causal kernel. Entry points own mode, provenance and source-role gates.

    This internal function is not an acquisition or final-experiment interface.
    It receives no labels, original repair times or scenario family.
    """
    if set(settings) != {"T", "U"}:
        raise ValueError("Explicit settings are required for both variables")
    monitors = {v: EWMA(settings[v]) for v in BOUNDS}
    slots = tuple(utc(s) for s in slots)
    if not slots:
        raise ValueError("Nonempty schedule required")
    if any(t.minute or t.second or t.microsecond for t in slots):
        raise ValueError("Schedule must contain whole UTC hours")
    if any(b - a != timedelta(hours=1) for a, b in zip(slots, slots[1:])):
        raise ValueError("Schedule must be unique, ordered and hourly")
    if len({r.identity for r in receipts}) != len(receipts):
        raise ValueError("Every receipt needs a unique opaque identity")
    drain = slots[-1] + timedelta(hours=1)
    events = []
    for i, r in enumerate(receipts):
        if type(r) is not Receipt or not r.identity or r.station not in {240, 260}:
            raise ValueError("Invalid public receipt contract")
        if not isinstance(r.timestamp, str) or any(x is not None and not isinstance(x, str) for x in (r.T, r.U)):
            raise ValueError("Current timestamp/cell tokens must remain strings or absent cells")
        delivery = utc(r.received_at)
        if delivery > drain or delivery < slots[0]:
            raise ValueError("Delivery outside fixture replay/drain; do not silently truncate")
        events.append((delivery, 0, i, r))
    events.extend((t + timedelta(minutes=5), 1, i, t) for i, t in enumerate(slots))
    events.sort(key=lambda e: e[:3])
    groups, seen = defaultdict(list), defaultdict(list)
    # Optional append-only stores preserve the same emitted rows while avoiding
    # a full year of dictionaries in RAM. State itself remains continuous.
    if sinks is not None and set(sinks) != {'ledger', 'states', 'summaries', 'receipt_audit'}:
        raise ValueError('All four evidence streams are required')
    ledger, states, summaries, receipt_audit = ([sinks[k] for k in
        ('ledger', 'states', 'summaries', 'receipt_audit')] if sinks is not None else [[], [], [], []])
    schedule_set = set(slots)
    closed = set()

    def emit(config, subject, kind, variable, check, emission, result, inputs):
        row = {"result_id": f"{run_id}/{config}/{subject}/{check}", "run_id": run_id,
               "configuration": config, "protocol": protocol, "subject_id": subject,
               "subject_kind": kind, "station": 260, "variable": variable,
               "units": UNITS.get(variable), "check": check, "check_version": 1,
               "emitted_at": emission.isoformat(), "input_ids": list(inputs),
               "estimated_onset": None, **result}
        ledger.append(row)
        return row

    for emission, event_type, _, item in events:
        if event_type == 0:
            r = item
            stamp = r.current_time()
            grid = stamp is not None and stamp.minute == stamp.second == stamp.microsecond == 0
            key = (r.station, stamp)
            previous = list(seen[key]) if stamp is not None else []
            checks = {
                "Q04": outcome("evaluated", stamp is None, "invalid_time" if stamp is None else "valid_time",
                               "invalid_timestamp" if stamp is None else "valid_timestamp", raw_timestamp=r.timestamp),
                "Q05": outcome("evaluated", not grid, "off_grid" if not grid else "on_grid", "valid_timestamp",
                               raw_timestamp=r.timestamp) if stamp is not None else
                       outcome("inapplicable", None, "invalid_time_has_no_grid_position", "invalid_timestamp"),
                "Q06": outcome("evaluated", bool(previous), "additional_receipt" if previous else "first_receipt",
                               "duplicate" if previous else "unique_so_far", earlier_receipt_ids=previous) if stamp is not None else
                       outcome("inapplicable", None, "invalid_time_has_no_station_time_key", "invalid_timestamp")}
            if r.station == 260:
                for cfg in CONFIGS:
                    for check, result in checks.items():
                        if cfg == "B0":
                            result = outcome("inapplicable", None, "check_not_enabled", "not_applicable")
                        emit(cfg, r.identity, "received_timestamp", None, check, emission, result, [r.identity])
            if stamp is not None:
                seen[key].append(r.identity)
                if stamp in schedule_set and stamp not in closed:
                    groups[key].append(r)
            receipt_audit.append({"identity": r.identity, "received_at": r.received_at,
                                  "timestamp": r.timestamp, "station": r.station,
                                  "alignment": "closed_slot" if stamp in closed else
                                  ("open_slot" if stamp in schedule_set else "unaligned")})
            continue

        t = item
        target, reference = groups.pop((260, t), []), groups.pop((240, t), [])
        closed.add(t)
        slot = "slot:" + t.isoformat()
        ids = [r.identity for r in target]
        q01 = outcome("evaluated", not target, "no_aligned_row" if not target else "row_present",
                      "absent" if not target else ("ambiguous" if len(target) > 1 else "present"),
                      slot_utc=t.isoformat(), closure_minutes=5, aligned_count=len(target))
        q01_rows = {cfg: emit(cfg, slot, "expected_slot", None, "Q01", emission, q01, ids) for cfg in CONFIGS}
        for var in BOUNDS:
            cell_rows = {cfg: [] for cfg in CONFIGS}
            for r in target:
                token = getattr(r, var)
                cell = numeric_cell(token, 1)
                q02 = outcome("unevaluated", None, "cell_field_absent", cell["state"]) if token is None else outcome(
                    "evaluated", cell["state"] == "missing", "explicit_missing" if cell["state"] == "missing" else "cell_not_blank",
                    cell["state"], raw_token=token)
                for cfg in CONFIGS:
                    cell_rows[cfg].append(emit(cfg, r.identity + ":" + var, "received_cell", var, "Q02", emission, q02, [r.identity]))
            subject = (target[0].identity if len(target) == 1 else slot) + ":" + var
            cell = numeric_cell(getattr(target[0], var), 1) if len(target) == 1 else None
            reason = ("target_absent" if not target else "target_ambiguous") if len(target) != 1 else (
                None if cell["state"] == "finite" else "target_" + cell["state"])
            y = cell["value"] if reason is None else None
            lo, hi = bounds[var]
            q03 = outcome("unevaluated", None, reason, reason, limits=[lo, hi]) if reason else outcome(
                "evaluated", not lo <= y <= hi, "out_of_range" if not lo <= y <= hi else "within_inclusive_range",
                "finite", value=y, limits=[lo, hi], raw_token=getattr(target[0], var))
            q03_rows = {cfg: emit(cfg, subject, "value_point" if len(target) == 1 else "slot_value",
                                 var, "Q03", emission, q03, ids) for cfg in CONFIGS}
            ref_cell = numeric_cell(getattr(reference[0], var), 1) if len(reference) == 1 else None
            ref_value = ref_cell["value"] if ref_cell and ref_cell["state"] == "finite" else None
            ref_reason = ("reference_absent" if not reference else "reference_ambiguous") if len(reference) != 1 else (
                None if ref_cell["state"] == "finite" else "reference_" + ref_cell["state"])
            # Preserve historical fixture reasons; scientific runs retain the exact gap reason.
            input_reason = reason or (ref_reason if protocol != PROTOCOL and settings[var].kind == "P" else None)
            s = monitors[var].step(f"{run_id}/{slot}/{var}", y, ref_value, input_reason, timestamp=t)
            s.update({"slot_utc": t.isoformat(), "emitted_at": emission.isoformat(), "variable": var,
                      "target_ids": ids, "reference_ids": [r.identity for r in reference],
                      "reference_value": ref_value, "reference_count": len(reference),
                      "settings_id": getattr(settings[var], "settings_id", "fixture-settings:" + var)})
            states.append(s)
            s01 = emit("H", subject, "value_point" if len(target) == 1 else "slot_value", var, "S01", emission,
                       outcome(s["execution"], s["prediction"], s["reason"], reason or "finite",
                               state_id=s["state_id"], settings_id=s["settings_id"]), ids)
            for cfg in CONFIGS:
                for domain, items in (("availability", [q01_rows[cfg]] + cell_rows[cfg]),
                                      ("value", [q03_rows[cfg]] + ([s01] if cfg == "H" else []))):
                    summaries.append({"summary_id": f"{run_id}/{cfg}/{slot}/{var}/{domain}",
                                      "configuration": cfg, "slot_utc": t.isoformat(), "variable": var,
                                      "domain": domain, "emitted_at": emission.isoformat(), **composite(items)})
    return {"run_status": "complete", "purpose": purpose,
            "protocol": protocol, "run_id": run_id, "slots_closed": len(closed),
            "drain_until": drain.isoformat(), "settings": {v: asdict(p) for v, p in settings.items()},
            "ledger": ledger, "states": states, "summaries": summaries, "receipt_audit": receipt_audit}
