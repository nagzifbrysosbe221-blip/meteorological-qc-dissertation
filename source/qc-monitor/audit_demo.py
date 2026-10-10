"""Read-only audit of saved synthetic evidence and preservation of source files."""

import argparse
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from demo import verify_exports
from records import ROOT, code_identity, save_json, sha256


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href")


def audit(folder):
    manifest = json.loads((folder / "completed.json").read_text())
    for name, expected in manifest["files"].items():
        if sha256(folder / name) != expected["sha256"]:
            raise ValueError("Changed saved evidence: " + name)
    if manifest["code"] != code_identity():
        raise ValueError("Saved run and current code identities differ")
    load = lambda name: [json.loads(x) for x in (folder / name).read_text().splitlines()]
    ledger, states, summaries = load("ledger.jsonl"), load("states.jsonl"), load("summaries.jsonl")
    public = {r["identity"]: r for r in json.loads((folder / "public-inputs.json").read_text())}
    state_ids = {s["state_id"] for s in states}
    result_ids = {r["result_id"] for r in ledger}
    assert len(result_ids) == len(ledger)
    for row in ledger:
        assert set(row["input_ids"]) <= public.keys()
        assert all(public[i]["received_at"] <= row["emitted_at"] for i in row["input_ids"])
        if row["check"] == "S01":
            assert row["evidence"]["state_id"] in state_ids
    for state in states:
        assert state["previous_valid_state_id"] is None or state["previous_valid_state_id"] in state_ids
        assert set(state["target_ids"] + state["reference_ids"]) <= public.keys()
        assert all(public[i]["received_at"] <= state["emitted_at"] for i in state["target_ids"] + state["reference_ids"])
    for summary in summaries:
        assert set(summary["contributors"]) <= result_ids
    verify_exports(folder, ledger)
    html = (folder / "report.html").read_text(encoding="utf-8")
    parser = Links()
    parser.feed(html)
    assert parser.hrefs and all((folder / link).is_file() for link in parser.hrefs)
    assert "not dissertation research findings" in html
    assert "Hidden hours remain in the full exports" in html
    # Reconfirm unchanged submitted PDFs and raw bytes against existing records.
    submitted = [ROOT / "reference/Chapters-1-to-3-Supervisor-Review-v2.pdf",
                 ROOT.parent / "Chapters-1-to-3-Supervisor-Review/v2/Chapters-1-to-3-Supervisor-Review-v2.pdf"]
    expected_hash = json.loads((ROOT / "working/qc-monitor/settings.json").read_text())["submitted_sha256"]
    submitted_hashes = {str(p): sha256(p) for p in submitted}
    assert all(h == expected_hash for h in submitted_hashes.values())
    raw_hashes = {}
    for p in sorted((ROOT / "data/raw").glob("knmi-*/manifest.json")):
        acquisition = json.loads(p.read_text())
        raw = ROOT / acquisition["raw_path"]
        actual = sha256(raw)
        assert actual == acquisition["sha256"]
        raw_hashes[str(raw)] = actual
    assert len(raw_hashes) == 3
    return {"run_folder": str(folder), "saved_hashes_verified": True,
            "ledger_rows": len(ledger), "states": len(states), "summaries": len(summaries),
            "input_state_contributor_links_resolve": True, "inputs_released_by_emission": True,
            "exports_roundtrip_exact": True, "html_links_exist": parser.hrefs,
            "html_content_checked": True, "html_visual_inspection": "not_completed_browser_file_policy_block",
            "submitted_hashes": submitted_hashes, "raw_snapshot_hashes": raw_hashes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    result = audit(args.folder)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = ROOT / "evidence/synthetic-replay" / ("audit-" + stamp + ".json")
    save_json(path, result)
    print(path)


if __name__ == "__main__":
    main()
