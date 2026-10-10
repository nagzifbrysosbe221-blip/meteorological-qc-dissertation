"""Fabricated examples and private annotations; never imported by detectors."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from ewma import FixtureSettings
from replay import Receipt


def schedule(n, start=None):
    start = start or datetime(2000, 1, 25, 1, tzinfo=timezone.utc)
    return [(start + timedelta(hours=i)).isoformat() for i in range(n)]


def receipt(identity, stamp, *, station=260, T="14", U="50", delay=0, timestamp=None):
    return Receipt(identity, station, (datetime.fromisoformat(stamp) + timedelta(minutes=delay)).isoformat(),
                   stamp if timestamp is None else timestamp, T, U)


def fixture_settings():
    return {"T": FixtureSettings("P", 0, 1, 0, 2, 0.10, 1),
            "U": FixtureSettings("S", 50, 0, 0, 1, 0.10, 1)}


def example():
    slots = schedule(270)
    public, private = [], []
    for i, stamp in enumerate(slots):
        target = receipt(f"r{i:04d}a", stamp)
        reference = receipt(f"r{i:04d}b", stamp, station=240, T="10")
        if i in range(228, 231):
            target = replace(target, T=str(14 + (i - 227) * 0.2))
            private.append({"identity": target.identity, "teaching_case": "tiny_gradual_bias",
                            "original_T": "14", "original_timestamp": stamp})
        if i == 231:
            target = replace(target, T="")
        if i == 234:
            target = replace(target, timestamp=(datetime.fromisoformat(stamp) + timedelta(minutes=15)).isoformat())
        if i == 235:
            target = replace(target, timestamp="INVALID_DATE")
        if i == 236:
            target = replace(target, T="51")
        if i == 237:
            target = replace(target, T="bad")
        if i != 232:
            public.append(target)
        if i == 233:
            public.append(replace(target, identity=f"r{i:04d}c", received_at=(datetime.fromisoformat(stamp) + timedelta(minutes=1)).isoformat()))
        if not 240 <= i <= 246:
            public.append(reference)
        if i in {231, 232, 233, 234, 235, 236, 237} or 240 <= i <= 246:
            private.append({"nominal_index": i, "original_timestamp": stamp,
                            "target_identity": target.identity, "original_T": "14",
                            "teaching_case": {231: "missing_cell", 232: "absent_row", 233: "duplicate",
                                              234: "off_grid", 235: "invalid_time", 236: "out_of_range",
                                              237: "invalid_numeric_diagnostic"}.get(i, "reference_unavailable")})
    return slots, public, private


def public_payload(receipts):
    return [asdict(r) for r in receipts]
