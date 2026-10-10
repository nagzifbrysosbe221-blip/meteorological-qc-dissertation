"""Independent expected answers for synthetic correctness, never performance."""

import copy
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict, replace
from datetime import datetime, timedelta

from ewma import EWMA, FixtureSettings
from fixtures import example, fixture_settings, receipt, schedule
from replay import Receipt, composite, replay
from records import ROOT
from demo import export_ledger, verify_exports, save_demo


def constant_settings(**changes):
    p = FixtureSettings("S", 10, 0, 0, 2, 0.10, 1)
    return replace(p, **changes)


def small_run(rows, slots):
    return replay(rows, slots, {"T": constant_settings(), "U": constant_settings(intercept=50, scale=1)})


def results(run, check, cfg="H", var=None):
    return [r for r in run["ledger"] if r["check"] == check and r["configuration"] == cfg and r["variable"] == var]


class EWMATests(unittest.TestCase):
    def test_independent_hand_recurrence_centre_scale_and_unclipped_value(self):
        m = EWMA(constant_settings(centre=2))
        # y=16, prediction=10, centre=2, scale=2 -> u=2; then u=-1.
        a, b = m.step("a", 16), m.step("b", 10)
        self.assertEqual(a["standardised_residual"], 2)
        self.assertAlmostEqual(a["z"], 0.2)
        self.assertAlmostEqual(b["z"], 0.08)
        c = m.step("c", 1000)
        self.assertEqual(c["residual"], 990)
        self.assertAlmostEqual(c["z"], 49.472)
        self.assertEqual(c["previous_valid_state_id"], "b")

    def test_ready_only_on_228th_valid_update(self):
        m = EWMA(constant_settings())
        for i in range(227):
            r = m.step(str(i), 14)
            self.assertIsNone(r["prediction"])
            self.assertEqual(r["reason"], "warm_up")
        r = m.step("228", 14)
        self.assertEqual(r["valid_updates"], 228)
        self.assertTrue(r["ready"])
        self.assertAlmostEqual(r["z"], 2 * (1 - 0.9 ** 228))
        self.assertTrue(r["prediction"])

    def test_strict_cutoff_ties_and_two_sides(self):
        for y, expected in [(30, False), (30.0001, True), (-10, False), (-10.0001, True)]:
            m = EWMA(constant_settings())
            for i in range(227):
                m.step(str(i), 10)
            self.assertEqual(m.step("228", y)["prediction"], expected)

    def test_six_gaps_hold_seventh_resets_and_next_rewarms(self):
        m = EWMA(constant_settings())
        for i in range(228):
            ready = m.step(str(i), 14)
        for i in range(1, 7):
            r = m.step("gap" + str(i))
            self.assertEqual((r["z"], r["valid_updates"], r["unavailable_hours"]), (ready["z"], 228, i))
            self.assertIsNone(r["prediction"])
            self.assertEqual(r["action"], "hold")
        r = m.step("gap7")
        self.assertEqual((r["z"], r["valid_updates"], r["unavailable_hours"], r["action"]), (0, 0, 7, "reset"))
        r = m.step("restart", 14)
        self.assertEqual((r["valid_updates"], r["unavailable_hours"], r["previous_valid_state_id"]), (1, 0, None))
        self.assertAlmostEqual(r["z"], 0.2)

    def test_valid_after_six_preserves_ready_state(self):
        m = EWMA(constant_settings())
        for i in range(228):
            m.step(str(i), 14)
        for i in range(6):
            m.step("gap" + str(i))
        r = m.step("resume", 14)
        self.assertEqual((r["valid_updates"], r["unavailable_hours"], r["ready"]), (229, 0, True))

    def test_P_never_falls_back_to_S(self):
        m = EWMA(constant_settings(kind="P", reference_weight=1, intercept=0))
        self.assertEqual(m.step("a", 14)["reason"], "reference_unavailable")
        self.assertEqual(m.step("b", 14, reference=10)["model_prediction"], 10)

    def test_settings_fail_closed(self):
        for changes in ({"scale": 0}, {"cutoff": 0}, {"weight": 0.3}, {"centre": float("nan")},
                        {"kind": "fallback"}, {"reference_weight": 1}):
            with self.assertRaises(ValueError):
                EWMA(constant_settings(**changes))

    def test_arithmetic_overflow_fails_instead_of_clean(self):
        m = EWMA(constant_settings(scale=1e-300))
        with self.assertRaises(ArithmeticError):
            m.step("a", 1e300)


class ReplayTests(unittest.TestCase):
    def test_receipt_vs_five_minute_closure(self):
        s = schedule(1)
        run = small_run([receipt("a", s[0], delay=2)], s)
        self.assertEqual(results(run, "Q04")[0]["emitted_at"], "2000-01-25T01:02:00+00:00")
        self.assertEqual(results(run, "Q03", var="T")[0]["emitted_at"], "2000-01-25T01:05:00+00:00")

    def test_equal_time_arrival_processed_before_closure(self):
        s = schedule(1)
        r = small_run([receipt("a", s[0], delay=5)], s)
        self.assertFalse(results(r, "Q01")[0]["prediction"])
        self.assertEqual(results(r, "Q03", var="T")[0]["execution"], "evaluated")

    def test_late_arrival_cannot_rewrite_closed_slot(self):
        s = schedule(1)
        empty = small_run([], s)
        late = small_run([receipt("a", s[0], delay=6)], s)
        for check, var in [("Q01", None), ("Q03", "T"), ("S01", "T")]:
            self.assertEqual(results(empty, check, var=var), results(late, check, var=var))
        self.assertEqual(late["states"], empty["states"])
        self.assertEqual(late["receipt_audit"][0]["alignment"], "closed_slot")

    def test_duplicate_first_receipt_unchanged_and_values_ambiguous(self):
        s = schedule(1)
        a = receipt("a", s[0])
        single = small_run([a], s)
        duplicate = small_run([a, receipt("b", s[0], delay=1)], s)
        q = results(duplicate, "Q06")
        self.assertEqual([r["prediction"] for r in q], [False, True])
        self.assertEqual(q[0], results(single, "Q06")[0])
        self.assertEqual(results(duplicate, "Q03", var="T")[0]["reason"], "target_ambiguous")
        self.assertEqual(results(duplicate, "S01", var="T")[0]["reason"], "target_ambiguous")

    def test_equal_time_duplicate_order_is_saved_input_order(self):
        s = schedule(1)
        r = small_run([receipt("z", s[0]), receipt("a", s[0])], s)
        self.assertEqual([(x["subject_id"], x["prediction"]) for x in results(r, "Q06")], [("z", False), ("a", True)])

    def test_late_duplicate_signals_without_rewinding(self):
        s = schedule(1)
        a = receipt("a", s[0])
        before = small_run([a], s)
        after = small_run([a, receipt("b", s[0], delay=6)], s)
        self.assertEqual(before["states"], after["states"])
        self.assertTrue(results(after, "Q06")[-1]["prediction"])

    def test_invalid_and_offgrid_are_not_repaired_from_delivery(self):
        s = schedule(2)
        run = small_run([receipt("a", s[0], timestamp="INVALID_DATE"),
                         receipt("b", s[1], timestamp="2000-01-25T02:15:00+00:00")], s)
        self.assertEqual([r["prediction"] for r in results(run, "Q01")], [True, True])
        self.assertEqual([r["prediction"] for r in results(run, "Q04")], [True, False])
        self.assertEqual([r["prediction"] for r in results(run, "Q05")], [None, True])
        self.assertTrue(all(r["prediction"] is None for r in run["ledger"] if r["configuration"] == "B0" and r["check"] in {"Q04", "Q05", "Q06"}))

    def test_missing_cell_differs_from_absent_row_and_invalid_numeric(self):
        s = schedule(4)
        r = small_run([receipt("a", s[0], T=""), receipt("c", s[2], T="bad"), receipt("d", s[3], T=None)], s)
        self.assertEqual([x["prediction"] for x in results(r, "Q01")], [False, True, False, False])
        self.assertEqual([x["prediction"] for x in results(r, "Q02", var="T")], [True, False, None])
        self.assertTrue(all(x["prediction"] is None for x in results(r, "Q03", var="T")))
        self.assertFalse(any(x["prediction"] is True for x in r["ledger"] if x["subject_id"].startswith("c:")))

    def test_inclusive_boundaries_both_variables(self):
        for var, values in {"T": [(-41, True), (-40, False), (50, False), (51, True)],
                            "U": [(-1, True), (0, False), (100, False), (101, True)]}.items():
            for value, flag in values:
                s = schedule(1)
                r = small_run([receipt("a", s[0], **{var: str(value)})], s)
                self.assertEqual(results(r, "Q03", var=var)[0]["prediction"], flag)

    def test_rules_identical_in_R_and_H(self):
        s, rows, _ = example()
        run = replay(rows, s, fixture_settings())
        strip = lambda x: {k: v for k, v in x.items() if k not in {"configuration", "result_id"}}
        for check, var in [("Q01", None), ("Q02", "T"), ("Q02", "U"), ("Q03", "T"), ("Q03", "U"),
                           ("Q04", None), ("Q05", None), ("Q06", None)]:
            self.assertEqual([strip(x) for x in results(run, check, "R", var)],
                             [strip(x) for x in results(run, check, "H", var)])

    def test_future_input_cannot_change_earlier_outputs(self):
        s = schedule(4)
        rows = [receipt(str(i), t) for i, t in enumerate(s)]
        before = small_run(rows, s)
        rows[-1] = replace(rows[-1], T="9999", timestamp="INVALID_DATE")
        after = small_run(rows, s)
        for table in ("ledger", "states", "summaries"):
            self.assertEqual([r for r in before[table] if r["emitted_at"] < s[-1]],
                             [r for r in after[table] if r["emitted_at"] < s[-1]])

    def test_private_truth_is_rejected_and_mutating_it_cannot_change_replay(self):
        s, rows, truth = example()
        before = replay(rows, s, fixture_settings())
        for item in truth:
            item.update({"label": "opposite", "original_T": "999999"})
        self.assertEqual(before, replay(rows, s, fixture_settings()))
        for key in ("truth", "family", "original_timestamp", "original_T"):
            with self.assertRaises(ValueError):
                Receipt.from_public(asdict(rows[0]) | {key: "hidden"})

    def test_no_variable_or_run_state_leakage(self):
        s = schedule(2)
        rows = [receipt(str(i), t) for i, t in enumerate(s)]
        before = small_run(rows, s)
        changed = small_run([replace(r, T="1000") for r in rows], s)
        self.assertEqual([r for r in before["states"] if r["variable"] == "U"],
                         [r for r in changed["states"] if r["variable"] == "U"])
        self.assertEqual(before, small_run(rows, s))

    def test_reference_must_be_unique_finite_and_released_by_closure(self):
        s = schedule(1)
        a = receipt("a", s[0])
        b = receipt("b", s[0], station=240, T="10")
        for refs in ([], [replace(b, received_at="2000-01-25T01:06:00+00:00")],
                     [b, replace(b, identity="c")], [replace(b, T="bad")]):
            run = replay([a] + refs, s, fixture_settings())
            self.assertEqual(results(run, "S01", var="T")[0]["reason"], "reference_unavailable")

    def test_large_finite_out_of_range_value_still_updates_EWMA(self):
        s = schedule(1)
        r = small_run([receipt("a", s[0], T="1000")], s)
        self.assertEqual(r["states"][0]["residual"], 990)
        self.assertAlmostEqual(r["states"][0]["z"], 49.5)

    def test_month_boundary_and_alarm_do_not_reset(self):
        s = schedule(240)
        r = small_run([receipt(str(i), t) for i, t in enumerate(s)], s)
        state = [x for x in r["states"] if x["variable"] == "T"]
        self.assertEqual(state[-1]["valid_updates"], 240)
        self.assertTrue(all(x["prediction"] for x in state[227:]))
        self.assertTrue(any(x["slot_utc"].startswith("2000-02-") for x in state))

    def test_schedule_and_drain_never_add_or_remove_slots(self):
        s = schedule(2)
        r = small_run([receipt("a", s[-1], delay=60)], s)
        self.assertEqual(r["slots_closed"], 2)
        self.assertEqual(len(results(r, "Q01")), 2)
        self.assertEqual(len(r["states"]), 4)
        with self.assertRaises(ValueError):
            small_run([receipt("a", s[-1], delay=61)], s)

    def test_duplicate_keys_are_station_specific(self):
        s = schedule(1)
        r = small_run([receipt("ref", s[0], station=240), receipt("target", s[0])], s)
        self.assertFalse(results(r, "Q06")[0]["prediction"])

    def test_bad_schedule_modes_and_missing_settings_fail_closed(self):
        s = schedule(1)
        for bad in ([s[0], s[0]], [s[0], schedule(3)[-1]], ["2024-01-02T01:00:00+00:00"]):
            with self.assertRaises(ValueError):
                small_run([], bad)
        with self.assertRaises(ValueError):
            replay([], s, fixture_settings(), mode="final")
        with self.assertRaises(ValueError):
            replay([], s, {})

    def test_ledger_unique_identity_and_null_contract(self):
        s, rows, _ = example()
        r = replay(rows, s, fixture_settings())
        self.assertEqual(len(r["ledger"]), len({x["result_id"] for x in r["ledger"]}))
        for x in r["ledger"]:
            if x["execution"] != "evaluated":
                self.assertIsNone(x["prediction"])
            self.assertIsNone(x["estimated_onset"])


class CompositeTests(unittest.TestCase):
    def test_status_truth_table(self):
        def r(state, flag):
            return {"execution": state, "prediction": flag, "result_id": state + str(flag)}
        flag, clean = r("evaluated", True), r("evaluated", False)
        unavailable, na = r("unevaluated", None), r("inapplicable", None)
        for inputs, expected in [([flag, unavailable], (True, False, "evaluated")),
                                 ([clean, unavailable], (None, False, "unevaluated")),
                                 ([clean, clean], (False, True, "evaluated")),
                                 ([na], (None, False, "inapplicable")),
                                 ([clean, na], (False, True, "evaluated"))]:
            v = composite(inputs)
            self.assertEqual((v["prediction"], v["complete"], v["execution"]), expected)


class SavedRunTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / "working" / "test-temporary"
        self.parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_preserves_types_precision_and_rejects_truncation(self):
        s = schedule(1)
        r = small_run([receipt("a", s[0], T="0.12345678901234567", U="0")], s)
        export_ledger(self.folder, r["ledger"])
        verify_exports(self.folder, r["ledger"])
        p = self.folder / "ledger.jsonl"
        p.write_text(p.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            verify_exports(self.folder, r["ledger"])

    def test_failed_execution_has_failure_manifest_not_completion(self):
        destination = self.folder / "bad-mode"
        with self.assertRaises(ValueError):
            save_demo(destination, schedule(1), [], fixture_settings(), mode="final")
        self.assertFalse((destination / "completed.json").exists())
        failure = json.loads((destination / "failed.json").read_text())
        self.assertEqual(failure["run_status"], "failed")
        self.assertFalse(failure["scientific_use_valid"])

    def test_incomplete_export_invalidates_run(self):
        destination = self.folder / "bad-export"
        with patch("demo.export_ledger", side_effect=OSError("constructed interrupted export")):
            with self.assertRaises(OSError):
                save_demo(destination, schedule(1), [], fixture_settings())
        self.assertTrue((destination / "failed.json").exists())
        self.assertFalse((destination / "completed.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
