"""Submitted Appendix C scoring primitives. Inputs are completed saved records.

No detector imports, fitting, calibration, point adjustment or data acquisition.
Private labels enter here only after raw predictions/episodes have been produced.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from evaluation_adapter import CONFIGS, IncompleteRun, combined, time, unit_key

VERSION = "submitted-v2-evaluator-fixtures-1"
COMPATIBLE = {
    "missing_cell": {"Q02"}, "absent_row": {"Q01"},
    "duplicate_receipt": {"Q06"}, "off_grid_timestamp": {"Q05", "Q01"},
    "invalid_timestamp": {"Q04", "Q01"},
    "out_of_range": {"Q03", "S01"}, "gradual_bias": {"Q03", "S01"},
}
ENABLED = {"B0": {"Q01", "Q02", "Q03"},
           "R": {"Q01", "Q02", "Q03", "Q04", "Q05", "Q06"},
           "H": {"Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "S01"}}


def ratio(numerator, denominator, reason):
    if denominator < 0 or numerator < 0:
        raise ValueError("Counts cannot be negative")
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator/denominator if denominator else None,
            "reason": None if denominator else reason}


def confusion(pairs):
    counts = dict.fromkeys(("TP", "FP", "FN", "TN"), 0)
    for truth, prediction in pairs:
        if type(truth) is not bool or type(prediction) is not bool:
            raise ValueError("Confusion counts require eligible Boolean truth and predictions")
        counts[("TP" if prediction else "FN") if truth else ("FP" if prediction else "TN")] += 1
    tp, fp, fn, tn = (counts[k] for k in ("TP", "FP", "FN", "TN"))
    return {**counts, "n": tp+fp+fn+tn,
            "precision": ratio(tp, tp+fp, "no_predicted_positives"),
            "recall": ratio(tp, tp+fn, "no_truth_positives"),
            "fpr": ratio(fp, fp+tn, "no_truth_negatives")}


def exclusion(unit, label):
    reasons = []
    if label.get("scope") != "scored":
        reasons.append("outside_point_scope" if label.get("scope") else "scope_unknown")
    if not unit["applicable"]:
        reasons.append("inapplicable")
    if type(label.get("truth")) is not bool:
        reasons.append("unknown_truth")
    if unit["task"] == "value" and not label.get("usable", False):
        reasons.append("structurally_unusable")
    if not unit["complete"]:
        reasons.append("required_checks_incomplete")
    if label.get("origin") == "natural_positive":
        reasons.append("independent_natural_positive")
    return reasons


def point_scores(units, truth):
    """Separate task/variable strata, own/common IDs, counts, coverage, exclusions.

    Cases remain separate. Any later monthly pooling must explicitly retain the
    exact family, variable/typed task and variant. Natural structural positives
    are listed separately from synthetic counts.
    """
    by_key = defaultdict(dict)
    groups = defaultdict(list)
    for u in units:
        if u["configuration"] in by_key[u["key"]]:
            raise IncompleteRun("Duplicate scoring unit")
        by_key[u["key"]][u["configuration"]] = u
    if set(truth)-set(by_key):
        raise ValueError("Private truth refers to unknown scoring units")
    for key, configs in by_key.items():
        if set(configs) != set(CONFIGS):
            raise IncompleteRun("Missing configuration for scoring unit")
        u = configs["B0"]
        groups[(u["case_id"], u["task"], u["variable"])].append(key)
    reports = []
    for (case, task, var), keys in sorted(groups.items(), key=str):
        participants = CONFIGS if task in {"value", "availability"} else ("R", "H")
        own = {c: {k for k in keys if not exclusion(by_key[k][c], truth.get(k, {}))} for c in participants}
        common = set.intersection(*(own[c] for c in participants))
        for cfg in CONFIGS:
            eligible = own.get(cfg, set())
            scored = [k for k in keys if truth.get(k, {}).get("scope") == "scored"]
            natural = [k for k in scored if truth[k].get("origin") == "natural_positive"]
            def metrics(selected):
                return confusion((truth[k]["truth"], by_key[k][cfg]["prediction"]) for k in sorted(selected))
            shared = common if cfg in participants else set()
            reports.append({"case_id": case, "task": task, "variable": var, "configuration": cfg,
                            "status": "applicable" if cfg in participants else "inapplicable",
                            "own_ids": sorted(eligible), "common_ids": sorted(shared),
                            "own": metrics(eligible), "common": metrics(shared),
                            "own_coverage": ratio(len(eligible), len(scored), "no_scored_subjects"),
                            "common_coverage": ratio(len(shared), len(scored), "no_scored_subjects"),
                            "exclusions": {k: exclusion(by_key[k][cfg], truth.get(k, {})) for k in keys if k not in eligible},
                            "natural_structural": [{"key": k, "prediction": by_key[k][cfg]["prediction"],
                                                    "complete": by_key[k][cfg]["complete"]} for k in natural]})
    return reports


def root_scores(roots, units, health):
    """One credit per root/configuration, with execution failures outside pairs.

    Sparse members are private (check, subject_id) relations. Overlapping root
    ownership is rejected: the submitted inventory consists of isolated cases.
    Health must certify the complete declared window/drain for every config.
    """
    rows, lookup = [], {}
    for u in units:
        for r in u["checks"]:
            key = (u["case_id"], r["configuration"], r["subject_id"], r["check"])
            if key in lookup and lookup[key] != r:
                raise IncompleteRun("Conflicting result copies")
            if key not in lookup:
                rows.append((u["case_id"], r))
                lookup[key] = r
    ownership, root_ids = {}, set()
    outcomes = []
    for root in roots:
        rid, case, family = root["root_id"], root["case_id"], root["family"]
        if rid in root_ids or family not in COMPATIBLE:
            raise ValueError("Invalid root identity/family")
        root_ids.add(rid)
        status = root["construction"]
        if status not in {"effective", "no_effect", "blocked", "generation_error", "pending"}:
            raise ValueError("Unknown construction status")
        if status in {"effective", "no_effect"}:
            if root["N"] != root["C"]+root["Z"]+root["U"] or min(root[k] for k in ("N", "C", "Z", "U")) < 0:
                raise ValueError("Construction accounting must satisfy N=C+Z+U")
            if (root["C"] > 0) != (status == "effective"):
                raise ValueError("Effective roots require actual changes")
        members = {tuple(x) for x in root.get("members", [])}
        if len(members) != len(root.get("members", [])) or any(c not in COMPATIBLE[family] for c, _ in members):
            raise ValueError("Duplicate or incompatible private relation")
        for check, subject in members:
            key = (case, check, subject)
            if key in ownership:
                raise ValueError("One result cannot belong to two isolated roots")
            ownership[key] = rid
        result = {"root_id": rid, "case_id": case, "family": family,
                  "variable": root.get("variable"), "variant": root.get("variant"),
                  "support": root.get("support"), "construction": status,
                  "paired": False, "pair_exclusions": {}, "configurations": {}}
        outcomes.append(result)
        if status != "effective":
            if root.get("first_effect") is not None or members:
                raise ValueError("Non-effective root cannot have realised effects")
            continue
        e = time(root["first_effect"])
        onset = time(root["planned_onset"])
        if root["length_hours"] <= 0:
            raise ValueError("Positive intended length required")
        h = onset+timedelta(hours=root["length_hours"]-1, minutes=5)
        if not onset <= e <= h or not members:
            raise ValueError("Invalid realised onset/mask")
        result.update(first_effect=e.isoformat(), horizon=h.isoformat())
        for cfg in CONFIGS:
            state = health.get((case, cfg), {"valid": False, "reason": "missing_run"})
            missing = [(c, s) for c, s in members if c in ENABLED[cfg] and (case, cfg, s, c) not in lookup]
            if not state["valid"] or missing:
                result["pair_exclusions"][cfg] = state.get("reason") or "missing_expected_result"
        if result["pair_exclusions"]:
            continue
        result["paired"] = True
        for cfg in CONFIGS:
            enabled = COMPATIBLE[family] & ENABLED[cfg]
            candidates = [r for c, r in rows if c == case and r["configuration"] == cfg and
                          (r["check"], r["subject_id"]) in members and r["check"] in enabled and
                          e <= time(r["emitted_at"]) <= h]
            evaluated = [r for r in candidates if r["execution"] == "evaluated"]
            positive = sorted((r for r in evaluated if r["prediction"] is True),
                              key=lambda r: (time(r["emitted_at"]), r["check"], r["subject_id"], r["result_id"]))
            category = ("hit" if positive else "evaluated_miss" if evaluated else
                        "unavailable_miss" if enabled else "absent_capability_miss")
            first = positive[0] if positive else None
            touched = [u for u in units if u["case_id"] == case and u["configuration"] == cfg and
                       any((r["check"], r["subject_id"]) in members and
                           e <= time(r["emitted_at"]) <= h for r in u["checks"])]
            # Numeric roots have a single value unit per changed slot. Structural
            # roots may touch dependent T/U availability units; retain identities.
            result["configurations"][cfg] = {
                "category": category, "result_id": first["result_id"] if first else None,
                "delay_seconds": (time(first["emitted_at"])-e).total_seconds() if first else None,
                "compatible_evaluated_results": len(evaluated),
                "statistical_opportunities": sum(r["check"] == "S01" for r in evaluated),
                "required_complete_unit_ids": sorted(u["key"] for u in touched if u["complete"]),
                "required_unit_ids": sorted(u["key"] for u in touched),
                "full_required_coverage": ratio(sum(u["complete"] for u in touched), len(touched), "no_required_units"),
                "interpretation": "flagged_during_intervention_not_proof_of_causal_response" if first else None}
    return outcomes


def case_accounting(outcomes):
    """Construction/execution inventory only, with no cross-family ranking."""
    construction = Counter(r["construction"] for r in outcomes)
    paired = [r for r in outcomes if r["paired"]]
    planned, effective, n = len(outcomes), construction["effective"], len(paired)
    return {"planned": planned, "construction": dict(construction), "effective": effective,
              "paired": n, "missing_or_failed_pairs": effective-n,
              "construction_coverage": ratio(effective, planned, "no_planned_cases"),
              "completion": ratio(n, effective, "no_effective_cases")}


def require_stratum(outcomes, family_rollup=False):
    fields = ("family", "variable", "support") + (() if family_rollup else ("variant",))
    strata = {tuple(r[f] for f in fields) for r in outcomes}
    if len(strata) > 1:
        raise ValueError("Do not pool different family/variable/support/variant strata")


def root_summary(outcomes, *, family_rollup=False):
    """Exact strata by default; optional within-family roll-up weights roots equally."""
    require_stratum(outcomes, family_rollup)
    result = {**case_accounting(outcomes), "configurations": {}}
    paired = [r for r in outcomes if r["paired"]]
    n, planned = len(paired), len(outcomes)
    for cfg in CONFIGS:
        counts = Counter(r["configurations"][cfg]["category"] for r in paired)
        hit, opportunity = counts["hit"], counts["hit"]+counts["evaluated_miss"]
        result["configurations"][cfg] = {"counts": dict(counts),
            "detection": ratio(hit, n, "no_valid_pairs"),
            "opportunity_coverage": ratio(opportunity, n, "no_valid_pairs"),
            "conditional_detection": ratio(hit, opportunity, "no_evaluated_opportunities"),
            "workflow_yield": ratio(hit, planned, "no_planned_cases")}
    return result


def paired_differences(outcomes, *, family_rollup=False):
    require_stratum(outcomes, family_rollup)
    pairs = [("H", "B0"), ("R", "B0"), ("H", "R")]
    roster = [r for r in outcomes if r["paired"]]
    result = []
    for a, b in pairs:
        both, a_only, b_only, neither = [], [], [], []
        for r in roster:
            x, y = r["configurations"][a], r["configurations"][b]
            ah, bh = x["category"] == "hit", y["category"] == "hit"
            if ah and bh:
                both.append({"root_id": r["root_id"], "delay_difference_seconds": x["delay_seconds"]-y["delay_seconds"]})
            else:
                (a_only if ah else b_only if bh else neither).append(r["root_id"])
        result.append({"comparison": a+"-"+b, "paired_ids": [r["root_id"] for r in roster],
                       "detection_difference": (len(a_only)-len(b_only))/len(roster) if roster else None,
                       "common_hits": both, "first_only": a_only, "second_only": b_only, "neither": neither})
    return result


def raw_nodes(units):
    """Union timestamp checks once per receipt, retaining partial positives."""
    nodes, receipts = [], defaultdict(list)
    for u in units:
        if u["task"] in {"value", "availability"}:
            nodes.append({**u, "domain": u["task"], "contributors": [r["result_id"] for r in u["checks"] if r["prediction"] is True]})
        else:
            receipts[(u["case_id"], u["configuration"], u["subject"])].append(u)
    for (case, cfg, subject), typed in receipts.items():
        if {u["task"] for u in typed} != {"Q04", "Q05", "Q06"}:
            raise IncompleteRun("Timestamp union requires all three explicit task rows")
        checks = [r for u in typed for r in u["checks"]]
        nodes.append({**typed[0], "key": unit_key(case, "timestamp", subject),
                      "task": "timestamp", "domain": "timestamp", "checks": checks,
                      **combined(checks), "contributors": [r["result_id"] for r in checks if r["prediction"] is True]})
    return nodes


def raw_episodes(nodes):
    """Truth-free episodes; no case/variable/domain/configuration/month resets."""
    groups = defaultdict(list)
    for n in nodes:
        groups[(n["case_id"], n["configuration"], n["domain"], n["variable"])].append(n)
    episodes = []
    for group, items in sorted(groups.items(), key=str):
        current, previous = None, None
        for n in sorted(items, key=lambda x: (time(x["time"]), x["order"], x["key"])):
            delta = (time(n["time"])-time(previous["time"])).total_seconds() if previous else None
            adjacent = delta is not None and (0 <= delta <= 3600 if n["domain"] == "timestamp" else delta == 3600)
            if n["prediction"] is True:
                if current is None or not adjacent:
                    current = {"episode_id": "episode:"+str(len(episodes)+1), "group": list(group), "nodes": []}
                    episodes.append(current)
                current["nodes"].append(n)
            else:
                current = None
            previous = n
    return episodes


def normal_ids(units, truth, cfg, cohort="common"):
    if cohort not in {"common", "own"}:
        raise ValueError("Unknown normal cohort")
    index = {(u["key"], u["configuration"]): u for u in units}
    allowed = set()
    for u in units:
        if u["configuration"] != cfg:
            continue
        configs = (CONFIGS if u["task"] in {"value", "availability"} else ("R", "H")) if cohort == "common" else (cfg,)
        if u["task"] in {"value", "availability"}:
            label = truth.get(u["key"], {})
            if label.get("truth") is False and all(not exclusion(index[(u["key"], c)], label) for c in configs):
                allowed.add(u["key"])
        elif cfg != "B0":
            keys = [unit_key(u["case_id"], task, u["subject"]) for task in ("Q04", "Q05", "Q06")]
            # All three independently negative conditions and all three complete.
            if all(truth.get(k, {}).get("truth") is False and all(
                    not exclusion(index[(k, c)], truth[k]) for c in configs) for k in keys):
                allowed.add(unit_key(u["case_id"], "timestamp", u["subject"]))
    return allowed


def project_burden(nodes, episodes, allowed, source_months):
    """One case/config/domain/variable at a time; exact negative exposure.

    source_months uses original source dates, never converted observation months.
    Segments crossing a month keep one start, while later months get carry-in.
    """
    groups = {(n["case_id"], n["configuration"], n["domain"], n["variable"]) for n in nodes}
    if len(groups) != 1:
        raise ValueError("Burden must keep case/configuration/domain/variable separate")
    if any(tuple(e["group"]) not in groups for e in episodes):
        raise ValueError("Episodes and exposure must refer to the same group")
    keys = {n["key"] for n in nodes}
    if not allowed <= keys:
        raise ValueError("Normal exposure contains unknown subjects")
    if any(k not in source_months for k in allowed):
        raise ValueError("Original source month is required for every exposure subject")
    segments = []
    for episode in episodes:
        retained = []
        def finish():
            if retained:
                segments.append({"parent_episode_id": episode["episode_id"],
                                 "ids": [n["key"] for n in retained],
                                 "truncated_start": retained[0]["key"] != episode["nodes"][0]["key"],
                                 "truncated_end": retained[-1]["key"] != episode["nodes"][-1]["key"]})
        for n in episode["nodes"]:
            if n["key"] in allowed:
                retained.append(n)
            else:
                finish()
                retained = []
        finish()
    factor = 1000 if nodes[0]["domain"] == "timestamp" else 720
    monthly = {}
    for month in sorted({source_months[k] for k in allowed}):
        exposure = sum(source_months[k] == month for k in allowed)
        starts = sum(source_months[s["ids"][0]] == month for s in segments)
        carry = sum(source_months[s["ids"][0]] != month and any(source_months[k] == month for k in s["ids"]) for s in segments)
        monthly[month] = {"exposure": exposure, "segment_starts": starts, "carry_in": carry,
                          "burden": ratio(factor*starts, exposure, "no_eligible_negative_exposure")}
    return {"exposure": len(allowed), "segments": segments, "segment_count": len(segments),
            "rate_factor": factor, "burden": ratio(factor*len(segments), len(allowed), "no_eligible_negative_exposure"),
            "monthly": monthly, "raw_positive_units": sum(n["prediction"] is True for n in nodes),
            "raw_episodes": len(episodes), "interpretation": "false_segments_are_not_notifications"}


def excess_response(injected, counterpart, changed_keys, injected_identity, counterpart_identity):
    """Offline numeric diagnostic, after both monitors finish; no point rewriting."""
    if injected_identity != counterpart_identity:
        raise ValueError("Counterpart configuration/model/settings must match")
    if any(u["task"] != "value" for u in injected+counterpart):
        raise ValueError("Excess response is a value-composite diagnostic")
    configs = {u["configuration"] for u in injected+counterpart}
    if len(configs) != 1:
        raise ValueError("Compare one configuration at a time")
    left = {(u["time"], u["variable"]): u for u in injected}
    right = {(u["time"], u["variable"]): u for u in counterpart}
    if len(left) != len(injected) or len(right) != len(counterpart):
        raise ValueError("Duplicate counterpart slot")
    opportunities, hits = [], []
    for key in sorted(changed_keys):
        a, b = left.get(tuple(key)), right.get(tuple(key))
        if a is None or b is None:
            raise IncompleteRun("Changed slot missing from injected/counterpart output")
        if a["complete"] and b["complete"]:
            opportunities.append(list(key))
            if a["prediction"] is True and b["prediction"] is False:
                hits.append(list(key))
    return {"comparable_opportunities": opportunities, "hit_slots": hits, "diagnostic_hit": bool(hits)}


def recovery_units(units, planned_onset, length_hours, month_last_slot):
    end = time(planned_onset)+timedelta(hours=length_hours-1)
    return [u for u in units if u["task"] in {"value", "availability"} and end < time(u["time"]) <= time(month_last_slot)]


def quantile7(values, p):
    if not 0 <= p <= 1:
        raise ValueError("Quantile probability outside [0,1]")
    values = sorted(values)
    if not values:
        return None
    h = (len(values)-1)*p
    i = int(h)
    return values[i]+(h-i)*(values[min(i+1, len(values)-1)]-values[i])


def monthly_description(contributions):
    """Saved (month,numerator,denominator) contributions, same exact stratum."""
    if len({x["month"] for x in contributions}) != len(contributions):
        raise ValueError("Duplicate source month")
    values = [x["numerator"]/x["denominator"] for x in contributions if x["denominator"]]
    leave_out = []
    for omitted in contributions:
        remaining = [x for x in contributions if x is not omitted]
        leave_out.append({"omitted_month": omitted["month"], **ratio(sum(x["numerator"] for x in remaining),
                          sum(x["denominator"] for x in remaining), "no_remaining_denominator")})
    defined = [x["value"] for x in leave_out if x["value"] is not None]
    q1, q3 = quantile7(values, .25), quantile7(values, .75)
    return {"defined_months": len(values), "undefined_months": len(contributions)-len(values),
            "median": quantile7(values, .5), "q1": q1, "q3": q3, "iqr": q3-q1 if values else None,
            "leave_one_month_out": leave_out, "leave_out_range": [min(defined), max(defined)] if defined else None}


def sensitivity_contract(primary, variant, factor):
    """Validate saved inputs/settings identity; caller recomputes each cohort.

    This validates metadata only; it does not calibrate or run sensitivities.
    """
    if primary["input_hash"] != variant["input_hash"] or primary["scope"] != variant["scope"]:
        raise ValueError("Sensitivity must reuse identical injected inputs and scope")
    a, b = primary["settings"], variant["settings"]
    if set(a) != set(CONFIGS) or set(b) != set(CONFIGS):
        raise ValueError("All three configurations must be retained")
    for cfg in CONFIGS:
        changes = {k for k in set(a[cfg]) | set(b[cfg]) if a[cfg].get(k) != b[cfg].get(k)}
        if factor == "alpha":
            allowed = {"alpha", "cutoff"} if cfg == "H" else set()
            if not changes <= allowed or (cfg == "H" and (b[cfg]["alpha"] not in {.005, .02} or b[cfg]["cutoff"] <= 0)):
                raise ValueError("Only H alpha/corresponding cutoff may change")
        elif factor == "T_bounds":
            if changes != {"T_bounds"} or b[cfg]["T_bounds"] != [-30, 45]:
                raise ValueError("T bounds must change across all configurations with EWMA fixed")
        else:
            raise ValueError("Undeclared sensitivity factor")
    return {"valid": True, "factor": factor, "cohort_policy": "recompute_strict_own_and_common_for_each_variant"}
