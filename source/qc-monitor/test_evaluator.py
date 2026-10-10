"""Independent tiny answers for the nine submitted C6 checks and boundary guards."""

import copy
import json
import unittest
from collections import Counter
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

from evaluation_adapter import CONFIGS, IncompleteRun, adapt, run_health, time, unit_key
from evaluation_fixtures import (check, cohort_fixture, healthy, hourly_nodes, label,
                                 root, tiny_private, tiny_public, value_unit)
from evaluator import (case_accounting, confusion, excess_response, monthly_description,
                       normal_ids, paired_differences, point_scores, project_burden,
                       quantile7, raw_episodes, raw_nodes, recovery_units, root_scores,
                       root_summary, sensitivity_contract)
from fixtures import schedule
from replay import Receipt, replay

EXPECTED = json.loads(Path(__file__).with_name("evaluator_expected.json").read_text())


def report(reports, task, cfg, var=None):
    return next(r for r in reports if (r["task"], r["configuration"], r["variable"]) == (task, cfg, var))


def tiny():
    slots, receipts, settings = tiny_public()
    public = [asdict(r) for r in receipts]
    run = replay(receipts, slots, settings, run_id="evaluator-tiny")
    units = adapt(run, public, slots, case_id="tiny")
    return run, units, public, slots


def event_units(case, stamp, flag, stat=None):
    return [value_unit(c, "changed", stamp, flag, stat, case=case) for c in CONFIGS]


class EvaluatorChecks(unittest.TestCase):
    def assertCounts(self, actual, expected):
        self.assertEqual({k: actual[k] for k in expected}, expected)

    def test_C6_01_identities_predictions_and_no_event_backfill(self):
        run, units, _, _ = tiny()
        before = copy.deepcopy((run, units))
        private = tiny_private()
        points = point_scores(units, private["units"])
        events = root_scores(private["roots"], units, healthy(["tiny"]))
        r = next(x for x in events if x["root_id"] == "range")["configurations"]["H"]
        self.assertEqual(r["category"], "hit")
        self.assertEqual(r["statistical_opportunities"], 0)
        self.assertEqual(r["full_required_coverage"]["value"], 0)
        self.assertEqual(report(points, "value", "H", "T")["own"]["n"], EXPECTED["C6_01"]["H_value_points"])
        self.assertEqual((run, units), before)

    def test_C6_02_flag_independent_own_and_common_cohorts(self):
        units, truth = cohort_fixture()
        points = point_scores(units, truth)
        for cfg, cohort, name in (("B0", "own", "B0_own"), ("H", "own", "H_own"), ("B0", "common", "B0_common")):
            self.assertCounts(report(points, "value", cfg, "T")[cohort], EXPECTED["C6_02"][name])
        expected_ids = [unit_key("points", "value", s, "T") for s in EXPECTED["C6_02"]["common_subjects"]]
        self.assertEqual(report(points, "value", "H", "T")["common_ids"], expected_ids)
        changed = copy.deepcopy(units)
        for u in changed:
            if u["prediction"] is not None:
                u["prediction"] = not u["prediction"]
            for r in u["checks"]:
                if r["prediction"] is not None:
                    r["prediction"] = not r["prediction"]
        after = point_scores(changed, truth)
        self.assertEqual([(r["own_ids"], r["common_ids"]) for r in points],
                         [(r["own_ids"], r["common_ids"]) for r in after])

    def test_C6_03_null_ratios_and_separate_denominators(self):
        empty = confusion([])
        negative = confusion([(False, False)])
        for metric in ("precision", "recall", "fpr"):
            self.assertIsNone(empty[metric]["value"])
            self.assertIsNotNone(empty[metric]["reason"])
        self.assertIsNone(negative["precision"]["value"])
        self.assertIsNone(negative["recall"]["value"])
        self.assertEqual(negative["fpr"]["value"], 0)
        _, units, _, _ = tiny()
        points = point_scores(units, tiny_private()["units"])
        expected = EXPECTED["tiny_replay"]
        for var in ("T", "U"):
            for cfg in CONFIGS:
                self.assertCounts(report(points, "availability", cfg, var)["common"], expected["availability_"+var])
            self.assertCounts(report(points, "value", "B0", var)["own"], expected["value_"+var+"_B0_own"])
        for task in ("Q04", "Q05", "Q06"):
            for cfg in ("R", "H"):
                self.assertCounts(report(points, task, cfg)["common"], expected[task])
            self.assertEqual(report(points, task, "B0")["status"], "inapplicable")
        # Nine received records, never eighteen from duplicating T/U.
        self.assertEqual(report(points, "Q04", "H")["common"]["n"], 9)

    def test_C6_04_compatible_single_root_credit_and_stable_order(self):
        _, units, _, _ = tiny()
        roots = tiny_private()["roots"]
        events = root_scores(roots, units, healthy(["tiny"]))
        self.assertEqual(len(events), EXPECTED["C6_04"]["tiny_roots"])
        for cfg in CONFIGS:
            self.assertEqual(sum(e["configurations"][cfg]["category"] == "hit" for e in events), EXPECTED["C6_04"][cfg+"_hits"])
        dup = next(e for e in events if e["root_id"] == "duplicate")
        self.assertEqual(dup["configurations"]["B0"]["category"], "absent_capability_miss")
        self.assertIn("/d2/Q06", dup["configurations"]["R"]["result_id"])
        # Off-grid and invalid root each get one credit, though Q01 also flags.
        self.assertIn("/e/Q05", next(e for e in events if e["root_id"] == "offgrid")["configurations"]["H"]["result_id"])
        self.assertEqual(events, root_scores(roots, list(reversed(units)), healthy(["tiny"])))
        overlap = copy.deepcopy(roots[0]); overlap["root_id"] = "illegal-overlap"
        with self.assertRaises(ValueError):
            root_scores(roots+[overlap], units, healthy(["tiny"]))
        stamp = schedule(1)[0]
        r = root("tie", "tie", "gradual_bias", stamp, [("Q03", "changed"), ("S01", "changed")], variable="T")
        tied = root_scores([r], event_units("tie", stamp, True, True), healthy(["tie"]))
        self.assertTrue(tied[0]["configurations"]["H"]["result_id"].endswith("/Q03"))

    def test_C6_05_inclusive_horizons_and_no_recovery_credit(self):
        onset = schedule(1)[0]
        end = (time(onset)+timedelta(hours=1, minutes=5)).isoformat()
        for stamp, expected_delay in ((onset, 0), (end, 3900)):
            r = root("edge", "edge", "gradual_bias", onset, [("Q03", "changed"), ("S01", "changed")], length=2)
            event = root_scores([r], event_units("edge", stamp, True), healthy(["edge"]))[0]
            self.assertEqual(event["configurations"]["B0"]["delay_seconds"], expected_delay)
        for stamp in ((time(end)+timedelta(microseconds=1)).isoformat(),
                      (time(onset)-timedelta(microseconds=1)).isoformat()):
            event = root_scores([r], event_units("edge", stamp, True), healthy(["edge"]))[0]
            self.assertEqual(event["configurations"]["B0"]["category"], "unavailable_miss")
            self.assertIsNone(event["configurations"]["B0"]["delay_seconds"])
        # A flagged unchanged recovery subject is never a sparse-positive member.
        units = event_units("edge", onset, False, False)
        units += [value_unit(c, "recovery", end, True, True, case="edge") for c in CONFIGS]
        event = root_scores([r], units, healthy(["edge"]))[0]
        self.assertEqual(event["configurations"]["H"]["category"], "evaluated_miss")
        units = [value_unit("H", str(i), s, False) for i, s in enumerate(schedule(4))]
        recovered = recovery_units(units, onset, 2, schedule(4)[-1])
        self.assertEqual([u["subject"] for u in recovered], ["2", "3"])

    def test_C6_06_capability_unavailability_failure_and_inventory(self):
        stamp = schedule(1)[0]
        roots, units = [], []
        for case, flag, stat in (("hit", True, None), ("miss", False, False), ("unavailable", None, None), ("failed", False, False)):
            roots.append(root(case, case, "gradual_bias", stamp, [("Q03", "changed"), ("S01", "changed")]))
            units += event_units(case, stamp, flag, stat)
        dup = root("duplicate", "dup", "duplicate_receipt", stamp, [("Q06", "added")])
        roots.append(dup)
        for cfg in CONFIGS:
            row = check(cfg, "added", "Q06", None if cfg == "B0" else True, stamp, "dup",
                        "inapplicable" if cfg == "B0" else "evaluated")
            units.append({"key": cfg+"dup", "case_id": "dup", "configuration": cfg,
                          "checks": [row], "complete": cfg != "B0"})
        for status in ("no_effect", "blocked", "generation_error", "pending"):
            roots.append({"root_id": status, "case_id": status, "family": "gradual_bias", "construction": status,
                          "N": 1, "C": 0, "Z": 1, "U": 0, "first_effect": None, "members": []})
        health = healthy([r["case_id"] for r in roots])
        health[("failed", "H")] = {"valid": False, "reason": "constructed execution failure"}
        events = root_scores(roots, units, health)
        accounting = case_accounting(events)
        for k in ("planned", "effective", "paired", "missing_or_failed_pairs"):
            self.assertEqual(accounting[k], EXPECTED["C6_06"][k])
        for cfg in ("B0", "H"):
            counts = dict(Counter(e["configurations"][cfg]["category"] for e in events if e["paired"]))
            self.assertEqual(counts, EXPECTED["C6_06"][cfg+"_counts"])
        self.assertEqual(next(e for e in events if e["root_id"] == "failed")["configurations"], {})
        # Missing row is a failed pair, not the present-null unavailable example.
        broken = [u for u in units if not (u["case_id"] == "hit" and u["configuration"] == "H")]
        self.assertFalse(root_scores(roots, broken, health)[0]["paired"])

    def test_C6_07_episode_breaks_exact_exposure_and_timestamp_union(self):
        nodes = hourly_nodes()
        episodes = raw_episodes(nodes)
        eligible = {"0", "1", "3", "5", "6", "7"}
        months = {str(i): "2000-01" if i < 2 else "2000-02" for i in range(8)}
        burden = project_burden(nodes, episodes, eligible, months)
        for k, expected_key in (("raw_episodes", "raw_episodes"), ("raw_positive_units", "raw_positive_units"),
                                ("exposure", "eligible_negative_slots"), ("segment_count", "false_segments")):
            self.assertEqual(burden[k], EXPECTED["C6_07"][expected_key])
        self.assertEqual(burden["burden"]["value"], 480)
        self.assertEqual([s["ids"] for s in burden["segments"]], [["0", "1"], ["3"], ["5"], ["7"]])
        self.assertTrue(burden["segments"][0]["truncated_end"])
        self.assertTrue(burden["segments"][1]["truncated_start"])
        zero = project_burden(nodes, episodes, set(), months)
        self.assertIsNone(zero["burden"]["value"])
        # Equal-time nonpositive receipt breaks two positive records; Q05/Q06
        # union on the first receipt still counts one record and one episode.
        typed = []
        for order, (subject, flags) in enumerate((("a", [False, True, True]), ("b", [False]*3), ("c", [False, False, True]))):
            for task, flag in zip(("Q04", "Q05", "Q06"), flags):
                r = check("H", subject, task, flag, schedule(1)[0])
                typed.append({"key": unit_key("ts", task, subject), "case_id": "ts", "configuration": "H",
                              "subject": subject, "variable": None, "task": task, "time": r["emitted_at"],
                              "order": order, "checks": [r], "complete": True, "prediction": flag})
        nodes = raw_nodes(typed)
        episodes = raw_episodes(nodes)
        ids = {n["key"] for n in nodes}
        burden = project_burden(nodes, episodes, ids, {k: "2000-01" for k in ids})
        self.assertEqual(burden["raw_positive_units"], 2)
        self.assertEqual(burden["segment_count"], 2)
        self.assertEqual(burden["exposure"], 3)
        self.assertAlmostEqual(burden["burden"]["value"], EXPECTED["C6_07"]["timestamp_burden"])

    def test_C6_08_counterpart_and_truth_isolation(self):
        stamps = schedule(3)
        injected = [value_unit("H", str(i), s, True, None if i == 2 else False) for i, s in enumerate(stamps)]
        controls = [value_unit("H", str(i), s, i == 1, False, case="control") for i, s in enumerate(stamps)]
        keys = [(s, "T") for s in stamps]
        result = excess_response(injected, controls, keys, {"model": "teaching", "settings": 1}, {"model": "teaching", "settings": 1})
        self.assertEqual(len(result["comparable_opportunities"]), 2)
        self.assertEqual(len(result["hit_slots"]), 1)
        self.assertFalse(excess_response(injected[-1:], controls[-1:], keys[-1:], "same", "same")["diagnostic_hit"])
        with self.assertRaises(ValueError):
            excess_response(injected, controls, keys, "model-A", "model-B")
        slots, receipts, settings = tiny_public()
        run = replay(receipts, slots, settings)
        before = copy.deepcopy(run)
        units = adapt(run, [asdict(r) for r in receipts], slots, case_id="tiny")
        truth = tiny_private()["units"]
        episodes = raw_episodes(raw_nodes(units))
        for v in truth.values():
            v["truth"] = None if v["truth"] is None else not v["truth"]
        point_scores(units, truth)
        self.assertEqual(before, run)
        self.assertEqual(run, replay(receipts, slots, settings))
        self.assertEqual(episodes, raw_episodes(raw_nodes(units)))
        with self.assertRaises(ValueError):
            Receipt.from_public(asdict(receipts[0]) | {"truth": True})

    def test_C6_09_sensitivity_inputs_settings_and_recomputed_cohorts(self):
        primary = {"input_hash": "fixture-sha", "scope": "tiny-scored",
                   "settings": {c: {"T_bounds": [-40, 50], "model": "teaching", "lambda": .1, "alpha": .01, "cutoff": 1} for c in CONFIGS}}
        for alpha in (.005, .02):
            variant = copy.deepcopy(primary)
            variant["settings"]["H"].update(alpha=alpha, cutoff=2)
            self.assertTrue(sensitivity_contract(primary, variant, "alpha")["valid"])
        bounds = copy.deepcopy(primary)
        for cfg in CONFIGS:
            bounds["settings"][cfg]["T_bounds"] = [-30, 45]
        self.assertTrue(sensitivity_contract(primary, bounds, "T_bounds")["valid"])
        for key, value in (("input_hash", "different"), ("scope", "different")):
            bad = copy.deepcopy(bounds); bad[key] = value
            with self.assertRaises(ValueError):
                sensitivity_contract(primary, bad, "T_bounds")
        bad = copy.deepcopy(bounds); bad["settings"]["H"]["lambda"] = .2
        with self.assertRaises(ValueError):
            sensitivity_contract(primary, bad, "T_bounds")
        units, truth = cohort_fixture()
        changed = copy.deepcopy(units)
        for u in changed:
            if u["prediction"] is not None:
                u["prediction"] = not u["prediction"]
        before, after = point_scores(units, truth), point_scores(changed, truth)
        self.assertEqual([r["common_ids"] for r in before], [r["common_ids"] for r in after])
        self.assertNotEqual(report(before, "value", "H", "T")["common"], report(after, "value", "H", "T")["common"])


class EvaluatorGuards(unittest.TestCase):
    def test_public_storage_order_is_not_receipt_order(self):
        slots, receipts, settings = tiny_public()
        a = next(i for i, r in enumerate(receipts) if r.identity == "d")
        b = next(i for i, r in enumerate(receipts) if r.identity == "d2")
        receipts[a], receipts[b] = receipts[b], receipts[a]
        run = replay(receipts, slots, settings)
        units = adapt(run, [asdict(r) for r in receipts], slots, case_id="tiny")
        self.assertEqual(len(units), 189)

    def test_point_scores_do_not_silently_pool_different_cases(self):
        units, truth = cohort_fixture()
        other = copy.deepcopy(units)
        other_truth = {}
        for u in other:
            u["case_id"] = "second"
            original = u["key"]
            u["key"] = original.replace("points|", "second|")
            other_truth[u["key"]] = copy.deepcopy(truth[original])
        results = point_scores(units+other, truth | other_truth)
        self.assertEqual(len(results), 6)
        self.assertEqual({r["case_id"] for r in results}, {"points", "second"})

    def test_missing_check_summary_state_and_truncated_run_fail(self):
        run, _, public, slots = tiny()
        for field in ("ledger", "summaries", "states"):
            bad = copy.deepcopy(run); bad[field].pop()
            health = run_health(bad, public, slots, case_id="tiny")
            self.assertFalse(health["valid"])
            self.assertEqual(health["units"], [])
        bad = copy.deepcopy(run); bad["slots_closed"] -= 1
        self.assertFalse(run_health(bad, public, slots, case_id="tiny")["valid"])
        bad = copy.deepcopy(run); bad["run_status"] = "failed"
        self.assertFalse(run_health(bad, public, slots, case_id="tiny")["valid"])

    def test_corrupt_predictions_duplicate_rows_and_summary_fail(self):
        run, _, public, slots = tiny()
        bad = copy.deepcopy(run); bad["ledger"].append(copy.deepcopy(bad["ledger"][0]))
        self.assertFalse(run_health(bad, public, slots, case_id="tiny")["valid"])
        bad = copy.deepcopy(run); bad["ledger"][0]["prediction"] = True  # B0 timestamp is inapplicable.
        self.assertFalse(run_health(bad, public, slots, case_id="tiny")["valid"])
        bad = copy.deepcopy(run); bad["summaries"][0]["complete"] = False
        self.assertFalse(run_health(bad, public, slots, case_id="tiny")["valid"])

    def test_context_and_drain_excluded_from_point_counts(self):
        _, units, _, _ = tiny()
        truth = tiny_private()["units"]
        truth[unit_key("tiny", "value", schedule(9)[0], "T")]["scope"] = "context"
        truth[unit_key("tiny", "Q04", "i")]["scope"] = "drain"
        points = point_scores(units, truth)
        self.assertEqual(report(points, "value", "B0", "T")["own"]["n"], 3)
        self.assertEqual(report(points, "Q04", "R")["common"]["n"], 8)

    def test_natural_positive_separate_not_synthetic_false_positive(self):
        _, units, _, _ = tiny()
        truth = tiny_private()["units"]
        truth[unit_key("tiny", "Q06", "d2")]["origin"] = "natural_positive"
        p = report(point_scores(units, truth), "Q06", "R")
        self.assertEqual(p["common"]["n"], 7)
        self.assertEqual(p["common"]["TP"], 0)
        self.assertEqual(len(p["natural_structural"]), 1)
        self.assertTrue(p["natural_structural"][0]["prediction"])

    def test_timestamp_normal_requires_three_independent_negatives(self):
        _, units, _, _ = tiny()
        truth = tiny_private()["units"]
        normal = normal_ids(units, truth, "H")
        timestamps = {k for k in normal if "|timestamp|" in k}
        self.assertEqual(timestamps, {unit_key("tiny", "timestamp", k) for k in ("a", "b", "d", "g", "h", "i")})
        self.assertFalse(any("|timestamp|" in k for k in normal_ids(units, truth, "B0")))
        self.assertFalse(any("|value|" in k for k in normal))  # H warm-up removes common value exposure.

    def test_month_boundary_carry_in_and_source_month_allocation(self):
        nodes = hourly_nodes()[:3]
        episodes = raw_episodes(nodes)
        burden = project_burden(nodes, episodes, {"0", "1", "2"}, {"0": "2000-01", "1": "2000-01", "2": "2000-02"})
        self.assertEqual(burden["segment_count"], 1)
        self.assertEqual(burden["monthly"]["2000-01"]["segment_starts"], 1)
        self.assertEqual(burden["monthly"]["2000-01"]["exposure"], 2)
        self.assertEqual(burden["monthly"]["2000-02"]["segment_starts"], 0)
        self.assertEqual(burden["monthly"]["2000-02"]["carry_in"], 1)

    def test_hour_and_receipt_gaps_do_not_join(self):
        nodes = hourly_nodes()[:2]
        nodes[1]["time"] = (time(nodes[0]["time"])+timedelta(hours=2)).isoformat()
        self.assertEqual(len(raw_episodes(nodes)), 2)
        for n in nodes:
            n["domain"] = "timestamp"; n["variable"] = None
        nodes[1]["time"] = (time(nodes[0]["time"])+timedelta(seconds=3600)).isoformat()
        self.assertEqual(len(raw_episodes(nodes)), 1)
        nodes[1]["time"] = (time(nodes[0]["time"])+timedelta(seconds=3601)).isoformat()
        self.assertEqual(len(raw_episodes(nodes)), 2)

    def test_case_variable_domain_and_configuration_do_not_merge(self):
        base = hourly_nodes()[:1]
        for field, other in (("case_id", "other"), ("variable", "U"), ("domain", "availability"), ("configuration", "R")):
            changed = copy.deepcopy(base[0]); changed[field] = other
            self.assertEqual(len(raw_episodes(base+[changed])), 2)

    def test_descriptive_type7_and_leave_one_month_out_saved_counts(self):
        contributions = [{"month": str(i), "numerator": n, "denominator": d} for i, (n, d) in enumerate(((0, 2), (1, 2), (2, 2), (0, 0)))]
        result = monthly_description(contributions)
        for k, v in EXPECTED["descriptive"].items():
            self.assertEqual(result[k], v)
        self.assertEqual(quantile7([2, 6], .25), 3)
        self.assertIsNone(quantile7([], .5))

    def test_common_hit_delay_only_and_no_family_ranking(self):
        _, units, _, _ = tiny()
        outcomes = root_scores(tiny_private()["roots"], units, healthy(["tiny"]))
        with self.assertRaises(ValueError):
            root_summary(outcomes)
        for outcome in outcomes:
            summary = root_summary([outcome])
            self.assertEqual(summary["paired"], 1)
        with self.assertRaises(ValueError):
            paired_differences(outcomes)
        # Three comparable numeric roots, independently specified saved outcomes.
        comparable = []
        for i, (h, b) in enumerate(((300, 600), (300, None), (None, None))):
            comparable.append({"root_id": str(i), "paired": True, "family": "gradual_bias", "variable": "T",
                               "support": "full", "variant": "same", "configurations": {
                cfg: {"category": "hit" if delay is not None else "evaluated_miss", "delay_seconds": delay}
                for cfg, delay in (("H", h), ("R", b), ("B0", b))}})
        differences = paired_differences(comparable)
        primary = differences[0]
        self.assertEqual(primary["comparison"], "H-B0")
        self.assertEqual(len(primary["common_hits"]), 1)
        self.assertEqual(primary["common_hits"][0]["delay_difference_seconds"], -300)
        self.assertEqual(primary["first_only"], ["1"])
        self.assertEqual(primary["second_only"], [])
        self.assertEqual(primary["detection_difference"], 1/3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
