"""tests/test_bench_endpoints.py — the S5.4 endpoint resolver (MAP 4.2).

Hermetic: drives ``bench.endpoints`` with in-memory engine/harness dicts
(no files, no yaml at the pure core). The file-read edge
(``load_engine_identity`` / ``load_harness_main``) is covered with tmp
YAMLs (PyYAML at the edge, the repo convention).

Asserts the S1.6/S3.6 wiring:
  * subagent ports = port_base + i with the per-instance override.
  * the pool N is clamped to 0..8 (the S1.4 range).
  * the main endpoint parses the port out of the harness.yaml url.
  * resolve_for_scenario: M6 -> main; a 0-pool -> main fallback;
    the subagent pool otherwise.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bench import endpoints


def _engine(count: int, base: int = 30101, ports: dict | None = None) -> dict:
    return {
        "instances": {
            "count": count,
            "port_base": base,
            **({"ports": ports} if ports else {}),
        },
        "alias": "qwen38-27b-subagent",
        "api_key": "bonsai2-local",
    }


def _harness(url: str = "http://localhost:30001") -> dict:
    return {"main_endpoint": url}


class SubagentPortsTest(unittest.TestCase):
    def test_ports_are_base_plus_i(self):
        n, eps = endpoints.subagent_ports(_engine(3))
        self.assertEqual(n, 3)
        self.assertEqual([e["port"] for e in eps], [30101, 30102, 30103])
        self.assertTrue(all(e["kind"] == "subagent" for e in eps))
        self.assertEqual(eps[0]["alias"], "qwen38-27b-subagent")
        self.assertEqual(eps[0]["api_key"], "bonsai2-local")

    def test_per_instance_port_override(self):
        _, eps = endpoints.subagent_ports(_engine(2, ports={1: 30999}))
        self.assertEqual(eps[1]["port"], 30999)
        self.assertEqual(eps[0]["port"], 30101)

    def test_zero_pool(self):
        n, eps = endpoints.subagent_ports(_engine(0))
        self.assertEqual(n, 0)
        self.assertEqual(eps, [])

    def test_non_int_count_is_zero_pool(self):
        n, _ = endpoints.subagent_ports({"instances": {"count": "two"}})
        self.assertEqual(n, 0)

    def test_count_clamped_to_8(self):
        n, eps = endpoints.subagent_ports(_engine(12))
        self.assertEqual(n, 8)
        self.assertEqual(len(eps), 8)

    def test_negative_count_is_zero(self):
        n, _ = endpoints.subagent_ports(_engine(-2))
        self.assertEqual(n, 0)


class MainEndpointTest(unittest.TestCase):
    def test_port_parsed_from_url(self):
        ep = endpoints.main_endpoint(_harness("http://localhost:30001"))
        self.assertEqual(ep["port"], 30001)
        self.assertEqual(ep["kind"], "main")
        self.assertEqual(ep["api_key"], "")

    def test_non_numeric_url_suffix_falls_back(self):
        ep = endpoints.main_endpoint(_harness("http://localhost"))
        self.assertEqual(ep["port"], 30001)  # the documented default

    # G3c round-14 P2: the schemeless+path edge the round-9/10 diff did
    # not pin. "http://localhost/served" (portless WITH a path) must get
    # the default port carried into the AUTHORITY ("http://localhost:30001/served"),
    # not appended at the end of the url — the port must agree with the
    # POST target, and a path stays AFTER the port.
    def test_portless_url_with_path_normalizes_authority(self):
        ep = endpoints.main_endpoint(_harness("http://localhost/served"))
        self.assertEqual(ep["port"], 30001)
        self.assertEqual(ep["url"], "http://localhost:30001/served")

    def test_schemeless_portless_url_with_path_normalizes_authority(self):
        ep = endpoints.main_endpoint(_harness("localhost/served"))
        self.assertEqual(ep["port"], 30001)
        self.assertEqual(ep["url"], "localhost:30001/served")

    def test_schemeless_port_with_path_kept(self):
        ep = endpoints.main_endpoint(_harness("localhost:30002/served"))
        self.assertEqual(ep["port"], 30002)
        self.assertEqual(ep["url"], "localhost:30002/served")

    def test_main_api_key_flows_through(self):
        # H3 (post-live-run): a key-walled main stack (no-key -> 401) needs
        # the Bearer key for completions. main_endpoint reads
        # harness.main_api_key into the endpoint dict's api_key; empty/absent
        # = keyless (the pre-amendment behavior).
        ep = endpoints.main_endpoint(
            {"main_endpoint": "http://localhost:30001", "main_api_key": "sk-live-key"}
        )
        self.assertEqual(ep["api_key"], "sk-live-key")
        # absent key -> keyless (the empty-string default)
        ep2 = endpoints.main_endpoint({"main_endpoint": "http://localhost:30001"})
        self.assertEqual(ep2["api_key"], "")


class ResolveForScenarioTest(unittest.TestCase):
    def test_m6_is_main_only(self):
        eps = endpoints.resolve_for_scenario("M6", _engine(2), _harness())
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0]["kind"], "main")

    def test_zero_pool_falls_back_to_main(self):
        eps = endpoints.resolve_for_scenario("M1", _engine(0), _harness())
        self.assertEqual(eps[0]["kind"], "main")

    def test_subagent_pool_sized(self):
        eps = endpoints.resolve_for_scenario("M1", _engine(3), _harness())
        self.assertEqual(len(eps), 3)
        self.assertTrue(all(e["kind"] == "subagent" for e in eps))

    def test_pool_size_cap(self):
        eps = endpoints.resolve_for_scenario("M7", _engine(3), _harness(), pool_size=2)
        self.assertEqual(len(eps), 2)


class LoadEdgeTest(unittest.TestCase):
    def test_load_engine_identity_from_env_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "subagent-engine.yaml"
            p.write_text(
                "alias: sub\napi_key: k\ninstances:\n  count: 2\n  port_base: 40000\n"
            )
            engine = endpoints.load_engine_identity({"BONSAI2_ENGINE_YAML": str(p)})
            self.assertEqual(engine["alias"], "sub")
            n, eps = endpoints.subagent_ports(engine)
            self.assertEqual(n, 2)
            self.assertEqual(eps[0]["port"], 40000)

    def test_load_harness_main_from_env_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "harness.yaml"
            p.write_text("main_endpoint: http://localhost:9999\n")
            h = endpoints.load_harness_main({"BONSAI2_CONFIG": str(p)})
            self.assertEqual(endpoints.main_endpoint(h)["port"], 9999)

    def test_missing_engine_yaml_raises(self):
        with self.assertRaises(FileNotFoundError):
            endpoints.load_engine_identity(
                {"BONSAI2_ENGINE_YAML": "/nonexistent/subagent-engine.yaml"}
            )


if __name__ == "__main__":
    unittest.main()
