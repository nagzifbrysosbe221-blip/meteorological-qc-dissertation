"""Small independent construction answers; existing C6 tests remain in the suite."""

import copy
import json
import unittest
from collections import Counter
from datetime import timedelta
from pathlib import Path

from evaluation_adapter import CONFIGS, adapt, run_health, unit_key
from evaluator import excess_response, point_scores, root_scores
from fixtures import fixture_settings
from replay import Receipt, replay, utc
from scenarios import (TEACHING_SCALES, build, fabricated_originals, inventory, month_window,
                       planned_root)
from scenario_truth import private_truth

EXPECTED = json.loads(Path(__file__).with_name("scenario_expected.json").read_text())


def pick(family, **fields):
    return copy.deepcopy(next(c for c in inventory() if c["family"] == family and
                              c["source_month"] == "2000-01" and all(c.get(k) == v for k, v in fields.items())))


def small(case, n=None):
    slots = [(utc(case["planned_onset"])+timedelta(hours=i)).isoformat() for i in range(n or case["length_hours"]+1)]
    return fabricated_originals(slots), {"slots": slots, "first_scored": slots[0], "last_scored": slots[-1]}


def score(generated, window):
    case = generated["root"]["case_id"]
    run = replay([Receipt.from_public(r) for r in generated["public"]], window["slots"], fixture_settings())
    before = copy.deepcopy(run)
    units = adapt(run, generated["public"], window["slots"], case_id=case)
    private = private_truth(generated, window)
    health = {(case, c): {"valid": True, "reason": None} for c in CONFIGS}
    roots = root_scores(private["roots"], units, health)
    points = point_scores(units, private["units"])
    assert before == run
    return roots[0], points, units, run


class ScenarioChecks(unittest.TestCase):
    def test_01_complete_inventory(self):
        cases = inventory()
        self.assertEqual(len(cases), 936)
        self.assertEqual(len({x["case_id"] for x in cases}), 936)
        self.assertEqual(Counter(x["family"] for x in cases), EXPECTED["annual_counts"])
        for month in range(1, 13):
            selected = [x for x in cases if x["source_month"] == f"2000-{month:02d}"]
            self.assertEqual(Counter(x["family"] for x in selected), EXPECTED["monthly_counts"])
            self.assertTrue(all(x["planned_onset"] == f"2000-{month:02d}-08T01:00:00+00:00" for x in selected))
        self.assertEqual(inventory(), cases)

    def test_02_every_variant_dimension(self):
        cases = [c for c in inventory() if c["source_month"] == "2000-01"]
        bias = [c for c in cases if c["family"] == "gradual_bias"]
        self.assertEqual({(c["variable"],c["sign"],c["scale_multiple"],c["ramp_hours"]) for c in bias},
                         {(v,s,a,d) for v in ("T","U") for s in (-1,1) for a in (.5,1,2) for d in (24,72,168)})
        self.assertTrue(all(c["length_hours"] == 2*c["ramp_hours"] for c in bias))
        for family, field, variants in (("duplicate_receipt","copies",{1,2}), ("off_grid_timestamp","minutes",{15,30}),
                                        ("invalid_timestamp","invalid_field",{"hour_0","INVALID_DATE"})):
            rows=[c for c in cases if c["family"]==family]
            self.assertEqual({(c[field],c["length_hours"]) for c in rows},{(v,d) for v in variants for d in (1,6,7)})
        self.assertEqual({(c["variable"],c["replacement"]) for c in cases if c["family"]=="out_of_range"},
                         {("T",-41),("T",51),("U",-1),("U",101)})

    def test_03_calendar_source_midnight_context_drain(self):
        for m,name in ((1,"January"),(2,"February"),(3,"March"),(12,"December")):
            w=month_window(m)
            self.assertEqual(w["scored_hours"],EXPECTED["calendar"][name+"_scored"])
            self.assertEqual(utc(w["drain_until"])-utc(w["last_scored"]),timedelta(hours=1))
        self.assertEqual(month_window(1)["context_hours"],24)
        self.assertEqual(month_window(3)["context_hours"],336)
        self.assertEqual(month_window(12)["last_scored"],"2001-01-01T00:00:00+00:00")
        last=fabricated_originals([month_window(12)["last_scored"]])[0]
        self.assertEqual((last["source_date"],last["source_hour"]),("2000-12-31",24))

    def test_04_partial_missing_hand_accounting(self):
        c=pick("missing_cell",variable="T",length_hours=6); rows,w=small(c)
        rows[2]["T"]=""  # supported unchanged hour 2
        rows=[r for r in rows if r["identity"] != rows[4]["identity"]] # hole hour 3
        duplicate=copy.deepcopy(next(r for r in rows if r["station"]==260 and r["timestamp"]==w["slots"][3]))
        duplicate["identity"]="independent-original-duplicate"; rows.append(duplicate)
        g=build(c,rows); root=g["root"]
        self.assertEqual({k:root[k] for k in ("N","C","Z","U")},EXPECTED["six_hour_missing"])
        self.assertEqual(root["support"],"partial")
        self.assertEqual(root["actual_change_mask"],[w["slots"][i] for i in (0,4,5)])

    def test_05_missing_no_effect_and_no_support(self):
        c=pick("missing_cell",variable="T",length_hours=1); rows,w=small(c)
        rows[0]["T"]="   "
        g=build(c,rows); self.assertEqual((g["root"]["Z"],g["root"]["construction"]),(1,"no_effect"))
        self.assertEqual(g["public"],g["counterpart"]); self.assertIsNone(g["root"]["first_effect"])
        g=build(c,rows[1:]); self.assertEqual((g["root"]["U"],g["root"]["support"]),(1,"none"))

    def test_06_numeric_finite_prerequisite_and_no_effect(self):
        c=pick("out_of_range",variable="T",replacement=51,length_hours=1)
        for token in ("",None,"bad","nan","inf"):
            rows,w=small(c); rows[0]["T"]=token; g=build(c,rows)
            self.assertEqual(g["root"]["U"],1); self.assertEqual(g["public"],g["counterpart"])
        rows,w=small(c); rows[0]["T"]="51"; g=build(c,rows)
        self.assertEqual((g["root"]["C"],g["root"]["Z"]),(0,1))

    def test_07_absent_tombstone_and_dependent_variables(self):
        c=pick("absent_row",length_hours=1); rows,w=small(c,3); g=build(c,rows)
        self.assertEqual(g["root"]["deleted_identities"],[rows[0]["identity"]])
        self.assertEqual(len(g["root"]["consequences"]),1)
        p=private_truth(g,w)["units"]
        for var in ("T","U"):
            self.assertIsNone(p[unit_key(c["case_id"],"value",w["slots"][0],var)]["truth"])
        event,_,_,_=score(g,w)
        self.assertTrue(all(event["configurations"][cfg]["delay_seconds"]==300 for cfg in CONFIGS))

    def test_08_duplicate_added_only_and_ambiguity(self):
        c=pick("duplicate_receipt",copies=2,length_hours=6); rows,w=small(c); g=build(c,rows)
        self.assertEqual(len(g["public"])-len(g["counterpart"]),12)
        self.assertEqual(len(g["root"]["members"]),12)
        self.assertNotIn(["Q06",rows[0]["identity"]],g["root"]["members"])
        p=private_truth(g,w)["units"]
        self.assertFalse(p[unit_key(c["case_id"],"Q06",rows[0]["identity"])]["truth"])
        self.assertIsNone(p[unit_key(c["case_id"],"value",w["slots"][0],"T")]["truth"])
        event,_,_,_=score(g,w)
        self.assertEqual(event["configurations"]["B0"]["category"],"absent_capability_miss")
        self.assertEqual(event["configurations"]["R"]["delay_seconds"],0)

    def test_09_offgrid_collision_is_unsupported(self):
        c=pick("off_grid_timestamp",minutes=15,length_hours=1); rows,w=small(c)
        extra=copy.deepcopy(rows[0]); extra.update(identity="collision",timestamp=(utc(w["slots"][0])+timedelta(minutes=15)).isoformat())
        rows.append(extra); g=build(c,rows)
        self.assertEqual(g["unit_accounting"][0]["reason"],"off_grid_destination_collision")
        self.assertEqual(g["public"],g["counterpart"])

    def test_10_timestamp_variants_one_root_consequences(self):
        for c in (pick("invalid_timestamp",invalid_field="hour_0",length_hours=1),
                  pick("invalid_timestamp",invalid_field="INVALID_DATE",length_hours=1),
                  pick("off_grid_timestamp",minutes=30,length_hours=1)):
            rows,w=small(c,3); g=build(c,rows)
            self.assertEqual(len(g["root"]["consequences"]),1)
            self.assertEqual(g["root"]["first_effect"],w["slots"][0])
            self.assertEqual(g["public"][0]["received_at"],w["slots"][0])
            event,points,_,_=score(g,w)
            self.assertEqual(event["configurations"]["R"]["delay_seconds"],0)
            self.assertEqual(event["configurations"]["B0"]["delay_seconds"],300)
            if c["family"]=="invalid_timestamp":
                self.assertIsNone(Receipt.from_public(g["public"][0]).current_time())
                for task,n in (("Q04",3),("Q05",2),("Q06",2)):
                    self.assertEqual(next(p for p in points if p["task"]==task and p["configuration"]=="R")["own"]["n"],n)

    def test_11_invalid_original_blocks_without_invented_delivery(self):
        c=pick("absent_row",length_hours=1); rows,w=small(c)
        rows[1].update(timestamp="INVALID_DATE",nominal_delivery=None)
        g=build(c,rows); self.assertEqual(g["root"]["construction"],"blocked"); self.assertIsNone(g["public"])
        rows[1]["nominal_delivery"]=w["slots"][0]
        g=build(c,rows); self.assertEqual(g["root"]["construction"],"effective")
        self.assertEqual(g["public"][0]["timestamp"],"INVALID_DATE")

    def test_12_bias_precision_hold_restore_and_holes(self):
        c=pick("gradual_bias",variable="T",sign=1,scale_multiple=.5,ramp_hours=24)
        rows,w=small(c); g=build(c,rows); expected=EXPECTED["half_scale_T_24_ramp"]
        target=[r for r in g["public"] if r["station"]==260]
        for index,name in ((0,"first"),(1,"second"),(23,"ramp_end"),(47,"hold_end"),(48,"restored")):
            self.assertEqual(float(target[index]["T"]),expected[name])
        rows=rows[1:]; g=build(c,rows)
        self.assertEqual(g["root"]["first_effect"],w["slots"][1])
        self.assertEqual(float(g["public"][1]["T"]),expected["second"])
        self.assertEqual((g["root"]["C"],g["root"]["U"]),(47,1))

    def test_13_scale_blocked_distinct_from_float_no_effect(self):
        c=pick("gradual_bias",variable="T",sign=1,scale_multiple=.5,ramp_hours=24); rows,w=small(c)
        for scale in (None,{}, {"kind":"fabricated_teaching_only","T":0}, {"kind":"fitted","T":2}):
            self.assertEqual(build(c,rows,scale)["root"]["construction"],"blocked")
        for r in rows:
            if r["station"]==260:r["T"]="1e100"
        g=build(c,rows); self.assertEqual((g["root"]["C"],g["root"]["Z"]),(0,48))

    def test_14_generation_error_and_pending_not_misses(self):
        c=pick("absent_row",length_hours=1); rows,w=small(c); rows.append(copy.deepcopy(rows[0]))
        g=build(c,rows); self.assertEqual(g["root"]["construction"],"generation_error")
        self.assertIsNone(g["public"]); self.assertEqual(g["root"]["members"],[])
        pending=planned_root(c); self.assertFalse(pending["accounting_assessed"])
        self.assertEqual(root_scores([pending],[],{})[0]["construction"],"pending")

    def test_15_original_reference_context_other_channel_repeat(self):
        c=pick("out_of_range",variable="U",replacement=101,length_hours=1)
        rows,w=small(c,3); prefix=fabricated_originals([(utc(w["slots"][0])-timedelta(hours=1)).isoformat()])
        rows=prefix+rows; before=copy.deepcopy(rows); g=build(c,rows)
        self.assertEqual(rows,before); self.assertEqual(g,build(c,rows))
        for left,right in zip(g["public"],g["counterpart"]):
            self.assertEqual(left["T"],right["T"])
            if left["station"]==240 or left["timestamp"]!=c["planned_onset"]:self.assertEqual(left,right)
        self.assertTrue(all(set(r)==set(Receipt.__dataclass_fields__) for r in g["public"]))

    def test_16_point_denominators_and_failure_are_preserved(self):
        c=pick("missing_cell",variable="T",length_hours=1); rows,w=small(c,3); g=build(c,rows)
        event,points,units,run=score(g,w)
        p=next(p for p in points if p["task"]=="availability" and p["variable"]=="T" and p["configuration"]=="B0")
        self.assertEqual((p["own"]["TP"],p["own"]["TN"]),(1,2))
        p=next(p for p in points if p["task"]=="value" and p["variable"]=="T" and p["configuration"]=="H")
        self.assertEqual(p["own"]["n"],0)
        run["slots_closed"]-=1
        health=run_health(run,g["public"],w["slots"],case_id=c["case_id"])
        event=root_scores([g["root"]],units,{(c["case_id"],cfg):health for cfg in CONFIGS})[0]
        self.assertFalse(event["paired"])

    def test_17_counterpart_consistency_and_truth_isolation(self):
        c=pick("out_of_range",variable="T",replacement=51,length_hours=1); rows,w=small(c,3); g=build(c,rows)
        _,_,units,run=score(g,w)
        control=replay([Receipt.from_public(r) for r in g["counterpart"]],w["slots"],fixture_settings())
        cu=adapt(control,g["counterpart"],w["slots"],case_id=c["case_id"]+"-control")
        answer=excess_response([u for u in units if u["task"]=="value" and u["configuration"]=="B0"],
                              [u for u in cu if u["task"]=="value" and u["configuration"]=="B0"],
                              {(w["slots"][0],"T")},"same-teaching-settings","same-teaching-settings")
        self.assertEqual(answer["hit_slots"],[[w["slots"][0],"T"]])
        private=private_truth(g,w); private["roots"][0]["reason"]="deliberately changed private label"
        self.assertEqual(run,replay([Receipt.from_public(r) for r in g["public"]],w["slots"],fixture_settings()))

    def test_18_range_crossing_stays_bias(self):
        c=pick("gradual_bias",variable="T",sign=1,scale_multiple=2,ramp_hours=24); rows,w=small(c)
        for r in rows:
            if r["station"]==260:r["T"]="49"
        g=build(c,rows); self.assertTrue(g["root"]["range_crossing"])
        self.assertEqual(g["root"]["family"],"gradual_bias")

    def test_19_december_boundary_replay_and_context_truth(self):
        c=pick("absent_row",length_hours=1)
        c.update(planned_onset="2000-12-31T23:00:00+00:00")
        rows,w=small(c,2); g=build(c,rows); score(g,w)
        w["first_scored"]=w["slots"][1]
        truth=private_truth(g,w)["units"]
        self.assertEqual(truth[unit_key(c["case_id"],"availability",w["slots"][0],"T")]["scope"],"context")
        self.assertEqual(truth[unit_key(c["case_id"],"value",w["slots"][1],"T")]["source_month"],"2000-12")

    def test_20_original_delivery_is_established_from_valid_time(self):
        c=pick("missing_cell",variable="T",length_hours=1); rows,w=small(c,3)
        rows[0]["nominal_delivery"]=(utc(w["slots"][0])-timedelta(days=1)).isoformat()
        g=build(c,rows)
        self.assertEqual(g["public"][0]["received_at"],w["slots"][0])
        truth=private_truth(g,w)["units"]
        self.assertEqual(truth[unit_key(c["case_id"],"Q04",rows[0]["identity"])]["scope"],"scored")

    def test_21_overflow_atomic_error_not_partial_case(self):
        c=pick("gradual_bias",variable="T",sign=1,scale_multiple=2,ramp_hours=24); rows,w=small(c)
        g=build(c,rows,{"kind":"fabricated_teaching_only","T":1e308})
        self.assertEqual(g["root"]["construction"],"generation_error")
        self.assertIsNone(g["public"]);self.assertEqual(g["root"]["actual_change_mask"],[])

    def test_22_original_structural_positive_stays_separate(self):
        c=pick("missing_cell",variable="T",length_hours=1); rows,w=small(c,3)
        rows[0]["T"]="";g=build(c,rows)
        truth=private_truth(g,w)["units"]
        self.assertEqual(truth[unit_key(c["case_id"],"availability",w["slots"][0],"T")]["origin"],"natural_positive")

    def test_23_runner_import_and_saved_scope_contract(self):
        import scenario_demo
        self.assertTrue(callable(scenario_demo.measure_month))

    def test_24_full_month_sized_csv_field_roundtrip(self):
        import csv
        import tempfile
        from evaluator_demo import export_points
        from records import ROOT
        before=csv.field_size_limit()
        with tempfile.TemporaryDirectory(dir=ROOT/'working') as directory:
            export_points(Path(directory),[{"cohort_ids":["opaque-identity-"+str(i) for i in range(12000)],"null":None,"zero":0}])
        self.assertEqual(csv.field_size_limit(),before)


if __name__=="__main__":unittest.main()
