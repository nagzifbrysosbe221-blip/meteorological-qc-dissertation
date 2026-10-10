"""Synthetic correctness fixtures, not empirical research findings."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from acquire import early_parameters
from data import expected_schedule, inventory, numeric_cell, parse_knmi, require_mode, role_for
from records import save_json, sha256


def fixture(*lines, header="# STN,YYYYMMDD,HH,T,U"):
    return parse_knmi((header + "\n" + "\n".join(lines) + "\n").encode("utf-8"))["rows"]


class DataTests(unittest.TestCase):
    def test_units_zero_and_original_tokens(self):
        rows = fixture(" 260,20211231,24, 153, 87", "260,20210101,1,0,0")
        self.assertEqual(rows[0]["T"]["value"], 15.3)
        self.assertEqual(rows[0]["U"]["value"], 87)
        self.assertEqual(rows[0]["raw_tokens"]["T"], " 153")
        self.assertEqual(rows[1]["T"], {"value": 0.0, "state": "finite"})

    def test_year_rollover_preserves_role(self):
        row = fixture("260,20211231,24,0,0")[0]
        self.assertEqual(row["timestamp_utc"], "2022-01-01T00:00:00+00:00")
        self.assertEqual(row["source_role"], "fit")
        require_mode("fit", date.fromisoformat(row["source_date"]))

    def test_validation_midnight_and_boundary_context_are_distinct(self):
        rows = fixture("260,20231231,24,1,2", "260,20240101,1,3,4")
        self.assertEqual([r["source_role"] for r in rows], ["validation", "context_only"])
        self.assertEqual(inventory(rows, 2023, 260)["observed_rows"], 1)

    def test_missing_invalid_nonfinite_and_absent_field(self):
        for token, state in [(" ", "missing"), ("bad", "invalid"), ("NaN", "nonfinite"),
                             ("inf", "nonfinite"), (None, "absent_field")]:
            self.assertEqual(numeric_cell(token, 10), {"value": None, "state": state})
        row = fixture("260,20210101,1,5")[0]
        self.assertEqual(row["U"]["state"], "absent_field")
        self.assertEqual(row["schema_state"], "field_count_mismatch")

    def test_extremes_are_not_clipped(self):
        row = fixture("260,20210101,1,9999,-5")[0]
        self.assertEqual(row["T"]["value"], 999.9)
        self.assertEqual(row["U"]["value"], -5)

    def test_missing_and_invalid_source_time_preserved(self):
        rows = fixture("260,,1,1,1", "260,20210230,1,1,1", "260,20210101,0,1,1",
                       "260,20210101,25,1,1", "260,20210101,1.5,1,1")
        self.assertEqual([r["time_state"] for r in rows], ["missing"] + ["invalid"] * 4)
        self.assertTrue(all(r["timestamp_utc"] is None for r in rows))
        self.assertEqual(len(rows), 5)

    def test_duplicates_retained_and_not_counted_as_usable(self):
        rows = fixture("260,20210101,1,1,1", "260,20210101,1,2,2", "260,20210101,2,3,3")
        result = inventory(rows, 2021, 260)
        self.assertEqual(len({r["record_id"] for r in rows}), 3)
        self.assertEqual(result["duplicate_keys"], 1)
        self.assertEqual(result["additional_duplicate_rows"], 1)
        self.assertEqual(result["absent_slots"], 8758)
        self.assertEqual(result["variables"]["T"]["finite_unique_slots"], 1)

    def test_missing_cell_is_not_an_absent_row(self):
        result = inventory(fixture("260,20210101,1,,0"), 2021, 260)
        self.assertEqual(result["absent_slots"], 8759)
        self.assertEqual(result["variables"]["T"]["cell_states_all_rows"], {"missing": 1})
        self.assertEqual(result["variables"]["U"]["finite_unique_slots"], 1)

    def test_schedule_independent_of_empty_data(self):
        result = inventory([], 2021, 260)
        self.assertEqual(result["expected_slots"], 8760)
        self.assertEqual(result["absent_slots"], 8760)
        self.assertEqual(result["variables"]["T"]["monthly"]["02"]["expected_slots"], 672)

    def test_final_schedule_arithmetic_uses_no_observations(self):
        schedule = list(expected_schedule(date(2024, 1, 2), date(2024, 12, 31), (260,)))
        self.assertEqual(len(schedule), 8760)
        self.assertEqual(schedule[0]["timestamp_utc"], "2024-01-02T01:00:00+00:00")
        self.assertEqual(schedule[-1]["timestamp_utc"], "2025-01-01T00:00:00+00:00")

    def test_mode_guards(self):
        for mode, day in [("fit", date(2022, 1, 1)), ("development", date(2023, 1, 1)),
                          ("validation", date(2024, 1, 1)),
                          ("early_inventory", date(2024, 1, 2)), ("final", date(2024, 2, 1))]:
            with self.assertRaises(ValueError):
                require_mode(mode, day)

    def test_final_acquisition_and_parsing_are_locked(self):
        with self.assertRaises(ValueError):
            early_parameters(2024)
        with self.assertRaises(ValueError):
            fixture("260,20240102,1,123,90")

    def test_extra_fields_and_lineage_are_preserved(self):
        rows = fixture("260,20210101,1,100,80,7", header="# STN,YYYYMMDD,HH,T,U,P")
        self.assertEqual(rows[0]["raw_tokens"]["P"], "7")
        self.assertEqual(rows[0]["line_number"], 2)
        self.assertTrue(rows[0]["record_id"].endswith(":2"))

    def test_unknown_schema_and_rh_substitution_fail_closed(self):
        for header in ["# STN,YYYYMMDD,HH,T,RH", "# STN,YYYYMMDD,HH,T,U,U", "<html>error</html>"]:
            with self.assertRaises(ValueError):
                fixture("260,20210101,1,100,80", header=header)

    def test_repeat_parsing_is_identical(self):
        self.assertEqual(fixture("260,20210101,1,1,2"), fixture("260,20210101,1,1,2"))

    def test_record_writer_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "record.json"
            save_json(path, {"test": 1})
            before = sha256(path)
            with self.assertRaises(FileExistsError):
                save_json(path, {"test": 2})
            self.assertEqual(sha256(path), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
