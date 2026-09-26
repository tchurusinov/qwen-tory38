"""tests/test_bench_config.py — the S5.8 config test (MAP 4.1).

Hermetic: drives ``bench.config`` with in-memory dicts (the validation
core is pure on dicts) + tmp-file YAMLs for the loader. No yaml dep at
import time (PyYAML only at the file-read edge, the repo convention).

The S5.8 case set:
  * the tracked template loads (all S5.7 keys, defaults filled).
  * invalid scenario id -> the exit-2 path (ConfigError).
  * duplicate ids / empty scenarios / non-mapping top level -> ConfigError.
  * quick not a subset of scenarios -> ConfigError.
  * repetitions not a positive int -> ConfigError.
  * the two pct fields outside (0, 100] -> ConfigError.
  * a results_dir that exists but is a file -> ConfigError.
  * unknown top-level keys -> a warning, not an error (forward-compat).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bench.config as cfg


def _write_yaml(tmp: Path, data: str) -> str:
    p = tmp / "bench.yaml"
    p.write_text(data)
    return str(p)


class BenchConfigValidateTest(unittest.TestCase):
    def test_tracked_template_is_valid(self):
        template = Path(__file__).resolve().parents[1] / "etc" / "bench.yaml"
        data = _load_text(template)
        errors, _warnings = cfg.validate_bench_config(data)
        self.assertEqual(errors, [], f"tracked template must be valid: {errors}")

    def test_defaults_fill_missing_keys(self):
        c = cfg.load_bench_config()  # the tracked template, no args
        for key in cfg.BENCH_CONFIG_DEFAULTS:
            self.assertIn(key, c, f"missing S5.7 key: {key}")
        self.assertEqual(c["scenarios"], list(cfg.BENCH_CONFIG_DEFAULTS["scenarios"]))
        self.assertIn("config_path", c)

    def test_invalid_scenario_id_is_config_error(self):
        """The S5.8 'invalid scenario id -> exit 2' case, named."""
        data = {"scenarios": ["M1", "M11"]}
        errors, _ = cfg.validate_bench_config(data)
        self.assertTrue(any("unknown scenario id 'M11'" in e for e in errors), errors)

    def test_duplicate_scenario_ids(self):
        errors, _ = cfg.validate_bench_config({"scenarios": ["M1", "M1"]})
        self.assertTrue(any("duplicate ids" in e for e in errors), errors)

    def test_empty_scenarios_list(self):
        errors, _ = cfg.validate_bench_config({"scenarios": []})
        self.assertTrue(any("non-empty list" in e for e in errors), errors)

    def test_scenarios_not_a_list(self):
        errors, _ = cfg.validate_bench_config({"scenarios": "M1"})
        self.assertTrue(any("non-empty list" in e for e in errors), errors)

    def test_top_level_not_a_mapping(self):
        errors, _ = cfg.validate_bench_config(["M1"])
        self.assertEqual(len(errors), 1)
        self.assertIn("not a mapping", errors[0])

    def test_quick_not_subset_of_scenarios(self):
        errors, _ = cfg.validate_bench_config(
            {"scenarios": ["M1", "M2"], "quick": ["M7"]}
        )
        self.assertTrue(any("not in scenarios" in e for e in errors), errors)

    def test_quick_empty(self):
        errors, _ = cfg.validate_bench_config({"scenarios": ["M1"], "quick": []})
        self.assertTrue(any("quick" in e and "non-empty" in e for e in errors), errors)

    def test_repetitions_not_positive_int(self):
        for bad in (0, -1, 2.0, "3", True):
            errors, _ = cfg.validate_bench_config(
                {"scenarios": ["M1"], "repetitions": bad}
            )
            self.assertTrue(
                any("repetitions" in e for e in errors),
                f"repetitions={bad!r}: {errors}",
            )

    def test_pct_out_of_range(self):
        for key in ("regression_tps_pct", "main_guardrail_pct"):
            for bad in (0, 101, -5, "10", True):
                data: dict = {"scenarios": ["M1"]}
                data[key] = bad
                errors, _ = cfg.validate_bench_config(data)
                self.assertTrue(
                    any(key in e for e in errors), f"{key}={bad!r}: {errors}"
                )

    def test_results_dir_exists_but_is_a_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "notadir"
            f.write_text("x")
            errors, _ = cfg.validate_bench_config(
                {"scenarios": ["M1"], "results_dir": str(f)}
            )
            self.assertTrue(any("not a directory" in e for e in errors), errors)

    def test_unknown_top_level_key_is_warning_only(self):
        errors, warnings = cfg.validate_bench_config(
            {"scenarios": ["M1"], "some_future_key": 1}
        )
        self.assertEqual(errors, [])
        self.assertTrue(any("unknown top-level key" in w for w in warnings), warnings)


class BenchConfigLoaderTest(unittest.TestCase):
    def test_load_invalid_yaml_is_config_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write_yaml(Path(td), "scenarios: [M1\n")  # unparseable
            with self.assertRaises(cfg.ConfigError):
                cfg.load_bench_config(p)

    def test_load_missing_file_is_config_error(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(cfg.ConfigError):
            cfg.load_bench_config(str(Path(td) / "absent.yaml"))

    def test_load_invalid_schema_is_config_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write_yaml(Path(td), "scenarios: [M1, M99]\n")
            with self.assertRaises(cfg.ConfigError):
                cfg.load_bench_config(p)

    def test_load_valid_file_returns_filled_config(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write_yaml(
                Path(td),
                f"scenarios: [M1, M2]\nquick: [M1, M2]\nresults_dir: {Path(td) / 'results'}\n",
            )
            c = cfg.load_bench_config(p)
            self.assertEqual(c["scenarios"], ["M1", "M2"])
            self.assertEqual(c["results_dir"], str(Path(td) / "results"))
            # defaults fill the missing S5.7 keys
            self.assertEqual(
                c["main_guardrail_pct"], cfg.BENCH_CONFIG_DEFAULTS["main_guardrail_pct"]
            )

    def test_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write_yaml(Path(td), "scenarios: [M1, M99]\n")
            self.assertEqual(cfg.main(["--config", p]), cfg.EXIT_CONFIG)
            p2 = _write_yaml(Path(td), "scenarios: [M1]\n")
            self.assertEqual(cfg.main(["--config", p2]), cfg.EXIT_OK)


def _load_text(p: Path):
    import yaml

    return yaml.safe_load(p.read_text())


if __name__ == "__main__":
    unittest.main()
