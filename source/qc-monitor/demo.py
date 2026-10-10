"""Save and verify a synthetic replay, its public inputs and separate private notes."""

import csv
import html
import json
import platform
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ewma import FixtureSettings
from fixtures import example, fixture_settings, public_payload
from records import ROOT, code_identity, save_json, sha256, utc_now
from replay import Receipt, replay


def write_jsonl(path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n")


def export_ledger(folder, rows):
    """JSON-in-cell CSV preserves null/bool/number/list types without rounding."""
    write_jsonl(folder / "ledger.jsonl", rows)
    with (folder / "ledger.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=True, allow_nan=False, separators=(",", ":")) for k, v in row.items()})
    verify_exports(folder, rows)


def verify_exports(folder, expected):
    lines = [json.loads(line) for line in (folder / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    with (folder / "ledger.csv").open(encoding="utf-8", newline="") as stream:
        csv_rows = [{k: json.loads(v) for k, v in row.items()} for row in csv.DictReader(stream)]
    if lines != expected or csv_rows != expected:
        raise ValueError("Incomplete or changed export; run invalid")


def process_peak_bytes():
    """Windows process peak working set, including interpreter and all allocations."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    p = Counters()
    p.cb = ctypes.sizeof(p)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(p), p.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return p.PeakWorkingSetSize


def render_report(folder, run):
    # Fixed display selection, not chosen for favourable performance.
    states = [s for s in run["states"] if s["variable"] == "T"]
    chosen = [(i + 1, s) for i, s in enumerate(states) if i < 3 or i >= 226]
    def td(x):
        return "<td>" + html.escape(str(x)) + "</td>"
    rows = []
    for hour, s in chosen:
        rows.append("<tr>" + "".join(td(x) for x in (hour, s["slot_utc"], s["z"], s["valid_updates"],
                     s["unavailable_hours"], s["action"], s["reason"], s["prediction"])) + "</tr>")
    issues = [r for r in run["ledger"] if r["configuration"] == "H" and r["prediction"] is True and r["check"] != "S01"]
    issue_rows = ["<tr>" + "".join(td(r[k]) for k in ("check", "subject_id", "emitted_at", "reason", "input_ids")) + "</tr>" for r in issues]
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Synthetic QC correctness demonstration</title><style>
body{font:16px/1.5 system-ui,sans-serif;margin:32px;color:#172332;background:#f8fafc}main{max-width:1300px;margin:auto}
h1{font-size:30px}h2{margin-top:30px}table{border-collapse:collapse;background:white;font-size:13px;width:100%}td,th{padding:8px;border:1px solid #ccd5df;text-align:left}th{background:#eaf0f6}.scroll{overflow:auto}.notice{padding:16px;border-left:5px solid #ad6a00;background:#fff2d8}code{overflow-wrap:anywhere}
</style><main><h1>Synthetic QC correctness demonstration</h1>
<p class="notice">Fabricated year-2000 values. These are software correctness examples, not dissertation research findings or evidence about real sensors.</p>
<p>Run complete: 270 hourly slots and the one-hour drain. No slots are added during the drain. Times are UTC. T is in degrees Celsius; U is in percent.</p>
<h2>How the example works</h2><p>A target T of 14 °C and a reference of 10 °C give residual 4 °C. With centre 0, scale 2 °C and weight 0.1, the standardised residual is 2 and the first states are 0.2, 0.38 and 0.542. Fixed fixture cutoff 1 is a teaching choice, not calibrated.</p>
<p>B0 checks availability and ranges. R adds timestamp and duplicate checks. H adds EWMA. An unavailable result is null, not a negative. A partial flag stays visible with incomplete coverage.</p>
<h2>Temperature state trace</h2><p>Display scope: hours 1–3 and 227–270 of 270. Hidden hours remain in the full exports. A gap holds state without decay; the seventh consecutive unavailable hour resets it.</p>
<div class="scroll"><table><tr><th>Hour</th><th>Slot UTC</th><th>State z</th><th>Valid updates</th><th>Gap hours</th><th>Action</th><th>Reason</th><th>Prediction</th></tr>""" + "".join(rows) + """</table></div>
<h2>H rule issues</h2><p>Display scope: all positive Q01–Q06 results for H in this fixture; excludes statistical flags. R uses identical rule outcomes. This table does not score faults.</p>
<div class="scroll"><table><tr><th>Check</th><th>Subject</th><th>Emission UTC</th><th>Reason</th><th>Input identities</th></tr>""" + "".join(issue_rows) + """</table></div>
<h2>Inspect the evidence</h2><p><a href="ledger.jsonl">Full check ledger</a> · <a href="ledger.csv">Full typed CSV</a> · <a href="states.jsonl">Full states</a> · <a href="summaries.jsonl">Coverage summaries</a> · <a href="public-inputs.json">Public inputs</a> · <a href="settings.json">Fixture settings</a> · <a href="completed.json">Run verification</a></p>
<p>CSV cells contain JSON values to preserve null, zero, booleans and full precision. Each issue names its public input; S01 names a saved state and settings. Estimated onset remains null. Private annotations are stored separately and are not read by replay or this report.</p>
<p>No fitted seasonal model, calibrated cutoff, evaluator, full 936-case generator, empirical performance result or usability study is claimed.</p></main></html>"""
    (folder / "report.html").write_text(page, encoding="utf-8")


def save_demo(folder, slots, receipts, settings, *, mode="synthetic"):
    folder.mkdir(parents=True, exist_ok=False)
    started = utc_now()
    save_json(folder / "started.json", {"started_utc": started, "run_status": "running",
                                        "purpose": "synthetic_correctness", "code": code_identity()})
    try:
        save_json(folder / "public-inputs.json", public_payload(receipts))
        save_json(folder / "schedule.json", slots)
        save_json(folder / "settings.json", {v: asdict(p) for v, p in settings.items()})
        # Read the public files back. This execution path never receives truth.
        loaded = [Receipt.from_public(r) for r in json.loads((folder / "public-inputs.json").read_text())]
        params = {v: FixtureSettings(**p) for v, p in json.loads((folder / "settings.json").read_text()).items()}
        begin = time.perf_counter()
        run = replay(loaded, slots, params, mode=mode)
        duration = time.perf_counter() - begin
        peak = process_peak_bytes()
        repeat = replay(loaded, slots, params, mode=mode)
        if repeat != run:
            raise ValueError("Repeated ordered scientific payload differs")
        export_ledger(folder, run["ledger"])
        for name in ("states", "summaries", "receipt_audit"):
            write_jsonl(folder / (name + ".jsonl"), run[name])
        render_report(folder, run)
        files = {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()}
        save_json(folder / "completed.json", {"started_utc": started, "finished_utc": utc_now(),
                  "run_status": "complete", "purpose": run["purpose"], "protocol": run["protocol"],
                  "settings_kind": "explicit_synthetic_constants_not_fitted_or_calibrated",
                  "slots_closed": run["slots_closed"], "drain_until": run["drain_until"],
                  "receipts": len(receipts), "ledger_rows": len(run["ledger"]), "state_rows": len(run["states"]),
                  "summary_rows": len(run["summaries"]), "repeat_payload_exact": True,
                  "csv_jsonl_roundtrip_exact": True, "first_replay_seconds": duration,
                  "process_peak_working_set_bytes_after_first_replay": peak,
                  "resource_scope": "First 270-hour in-memory synthetic replay; excludes repeat/export timing. Process peak includes Python. Not a full-batch estimate.",
                  "python": sys.version, "platform": platform.platform(), "code": code_identity(),
                  "output_bytes_before_completion_manifest": sum(x["bytes"] for x in files.values()), "files": files})
        return run
    except Exception as exc:
        save_json(folder / "failed.json", {"finished_utc": utc_now(), "run_status": "failed",
                                           "exception": type(exc).__name__, "message": str(exc),
                                           "traceback": traceback.format_exc(), "scientific_use_valid": False})
        raise


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "evidence" / "synthetic-replay" / ("demo-" + stamp)
    slots, public, private = example()
    # Physical separation as well as an input-schema barrier. No evaluator yet.
    save_json(ROOT / "data" / "synthetic-private" / ("demo-" + stamp + ".json"),
              {"purpose": "private teaching annotations; not research truth", "items": private})
    save_demo(folder, slots, public, fixture_settings())
    print(folder)


if __name__ == "__main__":
    main()
