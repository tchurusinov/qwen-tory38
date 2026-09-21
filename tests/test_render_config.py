"""MAP 1.5 / S1.6 / S1.7: hermetic tests for the subagent-engine.yaml
writer + schema validation (bin/bonsai2_render_config.py).

The S1.7 "unit half": "YAML schema validation of subagent-engine.yaml
(port math, ctx/parallel bounds, sampling ranges)". The validator is a
pure function on dicts — the hermetic suite drives it with in-memory
configs built from a copy of the tracked template (no yaml dep needed:
the template is loaded via PyYAML when present on the box, exactly the
gate.sh YAML-sanity convention; when absent the suite still runs, the
template-copy tests just parse a stdlib-constructed dict).

No GPU, no network (hermetic-suite convention, tests/test_smoke.py).
The tool is loaded from bin/ via importlib.util (same convention as
tests/test_model_fetch.py).

Run: `python3 -m unittest discover -s tests` from the repo root.
"""

import copy
import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL = REPO_ROOT / "bin" / "bonsai2_render_config.py"
TEMPLATE = REPO_ROOT / "etc" / "subagent-engine.yaml"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bonsai2_render_config", str(_TOOL))
    assert spec and spec.loader, f"could not load {_TOOL}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


render = _load_tool()

VALID_DIGEST = "sha256:" + "cd" * 32
VALID_COMMIT = "9a9394a895b96003ca842a6041cb28ac49a108f7"


def _template_dict() -> dict:
    """A valid in-memory config in the S1.6 template shape (all pins
    filled — the rendered form install.sh produces)."""
    return copy.deepcopy(
        {
            "engine": {
                "binary": "/opt/bonsai2/bin/llama-server",
                "pin": {
                    "repo": "https://github.com/PrismML-Eng/llama.cpp",
                    "commit": VALID_COMMIT,
                },
            },
            "image": {"pin": {"tag": "bonsai2-server:1.0", "sha256": VALID_DIGEST}},
            "model": {
                "repo": "prism-ml/Ternary-Bonsai-2-27B-gguf",
                "revision": "main",
                "file": "bonsai-2-27b-gguf-PQ2_0.gguf",
                "fallback_file": "bonsai-2-27b-gguf-PTQ1_0.gguf",
                "source_object": "Ternary-Bonsai-2-27B-PQ2_0.gguf",
                "fallback_source_object": "Ternary-Bonsai-2-27B-PTQ1_0.gguf",
                "sha256": "3907dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec1",
                "size_bytes": 7206168928,
                "fallback_sha256": "53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3",
                "fallback_size_bytes": 5946648928,
            },
            "instances": {
                "count": 3,
                "port_base": 30101,
                "ctx_size": 262144,
                "max_num_seqs": 8,
                "per_instance_budget_gb": 11.5,
            },
            "sampling": {
                "temperature": 1.0,
                "top_p": 0.9,
                "top_k": 1,
                "min_p": 0.01,
                "repeat_penalty": 1.0,
                "repeat_last_n": 512,
                "stop_token_ids": [248046, 248044],
            },
            "reasoning_format": "deepseek_r1",
            "alias": "qwen38-27b-subagent",
            "api_key": "bonsai2-local",
        }
    )


def _template_from_yaml() -> dict | None:
    """The tracked template as a dict (PyYAML when present — the box
    has it; gate.sh's YAML-sanity step uses the same convention)."""
    try:
        import yaml  # box dep (edge-only, like the tool itself)
    except ImportError:
        return None
    return yaml.safe_load(TEMPLATE.read_text())


class _Base(unittest.TestCase):
    def setUp(self):
        self.cfg = _template_dict()

    def validate(self, cfg=None):
        return render.validate_engine_config(cfg if cfg is not None else self.cfg)

    def assert_valid(self, cfg=None):
        errors, _warnings = self.validate(cfg)
        self.assertEqual(errors, [], f"expected valid, got: {errors}")

    def assert_invalid(self, cfg=None, needle=None):
        errors, _ = self.validate(cfg)
        self.assertTrue(errors, "expected an error, got none")
        if needle:
            self.assertTrue(
                any(needle in e for e in errors),
                f"expected an error containing {needle!r}, got: {errors}",
            )


class TestValidConfigs(_Base):
    def test_template_shape_is_valid(self):
        self.assert_valid()

    def test_tracked_template_validates_no_errors(self):
        # The tracked etc/subagent-engine.yaml must always be error-free,
        # in either install state: pre-MAP 1.4 (placeholder image pin ->
        # warning only) or post-MAP 1.4 (digest stamped in -> no warning).
        data = _template_from_yaml()
        if data is None:
            self.skipTest("PyYAML not available (template-load skipped)")
        errors, _warnings = render.validate_engine_config(data)
        self.assertEqual(errors, [], f"tracked template must be error-free: {errors}")

    def test_placeholder_image_pin_warns_not_errors(self):
        # The pre-MAP 1.4 template state: image pin still the documented
        # placeholder -> the validator reports it as a warning, not an
        # error (the MAP 1.4 build records the real digest).
        cfg = self.cfg
        cfg["image"]["pin"]["tag"] = "bonsai2-server:<tag-at-install>"
        cfg["image"]["pin"]["sha256"] = "<docker-content-digest-at-install>"
        errors, warnings = self.validate(cfg)
        self.assertEqual(errors, [], f"placeholder pin must not be an error: {errors}")
        self.assertTrue(any("image.pin" in w for w in warnings))

    def test_count_zero_is_valid_main_only(self):
        self.cfg["instances"]["count"] = 0
        self.assert_valid()

    def test_count_eight_is_valid(self):
        self.cfg["instances"]["count"] = 8
        self.assert_valid()

    def test_port_overrides_distinct_is_valid(self):
        self.cfg["instances"]["count"] = 2
        self.cfg["instances"]["ports"] = {0: 30200, 1: 30201}
        self.assert_valid()

    def test_ctx_floor_is_valid(self):
        # S2.5: 8192 is the floor AND a multiple of 8192.
        self.cfg["instances"]["ctx_size"] = 8192
        self.assert_valid()


class TestEngineBlock(_Base):
    def test_missing_block(self):
        del self.cfg["engine"]
        self.assert_invalid(needle="engine: block missing")

    def test_bad_commit(self):
        self.cfg["engine"]["pin"]["commit"] = "not-a-sha"
        self.assert_invalid(needle="engine.pin.commit")

    def test_short_commit(self):
        self.cfg["engine"]["pin"]["commit"] = "9a9394a8"
        self.assert_invalid(needle="engine.pin.commit")

    def test_missing_repo(self):
        del self.cfg["engine"]["pin"]["repo"]
        self.assert_invalid(needle="engine.pin.repo")

    def test_empty_binary(self):
        self.cfg["engine"]["binary"] = "  "
        self.assert_invalid(needle="engine.binary")


class TestImageBlock(_Base):
    def test_missing_block(self):
        del self.cfg["image"]
        self.assert_invalid(needle="image: block missing")

    def test_bare_hex_digest_rejected(self):
        # the S1.6 pin is a CONTENT digest: `sha256:` + 64 hex.
        self.cfg["image"]["pin"]["sha256"] = "cd" * 32
        self.assert_invalid(needle="image.pin.sha256")

    def test_placeholder_digest_is_warning_not_error(self):
        # the documented template placeholder (pre-MAP-1.4 state) is a
        # warning — the template must validate before the first build.
        self.cfg["image"]["pin"]["sha256"] = "<docker-content-digest-at-install>"
        errors, warnings = self.validate()
        self.assertEqual(errors, [])
        self.assertTrue(any("image.pin.sha256" in w for w in warnings))

    def test_placeholder_tag_is_warning_not_error(self):
        self.cfg["image"]["pin"]["tag"] = "bonsai2-server:<tag-at-install>"
        errors, warnings = self.validate()
        self.assertEqual(errors, [])
        self.assertTrue(any("image.pin.tag" in w for w in warnings))

    def test_garbage_digest_rejected(self):
        self.cfg["image"]["pin"]["sha256"] = "sha256:xyz"
        self.assert_invalid(needle="image.pin.sha256")


class TestModelBlock(_Base):
    def test_bad_sha(self):
        self.cfg["model"]["sha256"] = "beef"
        self.assert_invalid(needle="model.sha256")

    def test_size_zero_rejected(self):
        self.cfg["model"]["size_bytes"] = 0
        self.assert_invalid(needle="model.size_bytes")

    def test_size_float_rejected(self):
        self.cfg["model"]["size_bytes"] = 7206168928.5
        self.assert_invalid(needle="model.size_bytes")

    def test_missing_fallback_keys(self):
        for k in (
            "fallback_file",
            "fallback_source_object",
            "fallback_sha256",
            "fallback_size_bytes",
        ):
            del self.cfg["model"][k]
        self.assert_invalid(needle="model.fallback_sha256")


class TestInstancesBlock(_Base):
    def test_count_nine_rejected(self):
        # S1.4 clamp range [0, 8].
        self.cfg["instances"]["count"] = 9
        self.assert_invalid(needle="instances.count")

    def test_count_negative_rejected(self):
        self.cfg["instances"]["count"] = -1
        self.assert_invalid(needle="instances.count")

    def test_port_base_below_range(self):
        self.cfg["instances"]["port_base"] = 80
        self.assert_invalid(needle="instances.port_base")

    def test_port_base_above_range(self):
        self.cfg["instances"]["port_base"] = 70000
        self.assert_invalid(needle="instances.port_base")

    def test_port_overflow_with_count(self):
        # port math: base 65534 + count 3 -> last port 65536 > 65535.
        self.cfg["instances"]["port_base"] = 65534
        self.cfg["instances"]["count"] = 3
        self.assert_invalid(needle="overflows the port range")

    def test_port_overflow_boundary_ok(self):
        # base 65533 + 3 = last port 65535: exactly the range end.
        self.cfg["instances"]["port_base"] = 65533
        self.cfg["instances"]["count"] = 3
        self.assert_valid()

    def test_port_collision_base_vs_override(self):
        # instance 0's derived port (30101) is overridden for instance
        # 1 onto the same port: collision.
        self.cfg["instances"]["ports"] = {1: 30101}
        self.assert_invalid(needle="port 30101 assigned to multiple instances")

    def test_port_collision_between_overrides(self):
        self.cfg["instances"]["count"] = 3
        self.cfg["instances"]["ports"] = {1: 30102, 2: 30102}
        self.assert_invalid(needle="port 30102 assigned to multiple instances")

    def test_override_index_out_of_range_rejected(self):
        self.cfg["instances"]["count"] = 2
        self.cfg["instances"]["ports"] = {5: 30199}
        self.assert_invalid(needle="instances.ports[5]")

    def test_override_bad_port_rejected(self):
        self.cfg["instances"]["ports"] = {0: 300000}
        self.assert_invalid(needle="instances.ports[0]")

    def test_override_not_a_mapping_rejected(self):
        self.cfg["instances"]["ports"] = [30101, 30102]
        self.assert_invalid(needle="instances.ports: not a mapping")

    def test_ctx_below_floor(self):
        self.cfg["instances"]["ctx_size"] = 4096
        self.assert_invalid(needle="instances.ctx_size")

    def test_ctx_above_ceiling(self):
        # S1.3: the model's 262K ceiling.
        self.cfg["instances"]["ctx_size"] = 270336
        self.assert_invalid(needle="instances.ctx_size")

    def test_ctx_not_a_multiple_of_8192(self):
        # S2.5 drift rule: reductions keep a multiple of 8192.
        self.cfg["instances"]["ctx_size"] = 100000
        self.assert_invalid(needle="multiple of 8192")

    def test_ctx_boundary_values(self):
        self.cfg["instances"]["ctx_size"] = 262144
        self.assert_valid()
        self.cfg["instances"]["ctx_size"] = 8192
        self.assert_valid()

    def test_parallel_zero_rejected(self):
        self.cfg["instances"]["max_num_seqs"] = 0
        self.assert_invalid(needle="instances.max_num_seqs")

    def test_parallel_65_rejected(self):
        self.cfg["instances"]["max_num_seqs"] = 65
        self.assert_invalid(needle="instances.max_num_seqs")

    def test_parallel_boundary_values(self):
        for v in (1, 64):
            self.cfg["instances"]["max_num_seqs"] = v
            self.assert_valid()

    def test_budget_zero_rejected(self):
        self.cfg["instances"]["per_instance_budget_gb"] = 0
        self.assert_invalid(needle="per_instance_budget_gb")

    def test_budget_negative_rejected(self):
        self.cfg["instances"]["per_instance_budget_gb"] = -1.5
        self.assert_invalid(needle="per_instance_budget_gb")


class TestSamplingBlock(_Base):
    def test_missing_block(self):
        del self.cfg["sampling"]
        self.assert_invalid(needle="sampling: block missing")

    def test_temperature_out_of_range(self):
        for v in (-0.1, 2.5):
            self.cfg["sampling"]["temperature"] = v
            self.assert_invalid(needle="sampling.temperature")

    def test_top_p_out_of_range(self):
        for v in (0.0, 1.5):
            self.cfg["sampling"]["top_p"] = v
            self.assert_invalid(needle="sampling.top_p")

    def test_top_p_one_is_valid(self):
        self.cfg["sampling"]["top_p"] = 1.0
        self.assert_valid()

    def test_top_k_zero_rejected(self):
        self.cfg["sampling"]["top_k"] = 0
        self.assert_invalid(needle="sampling.top_k")

    def test_top_k_float_rejected(self):
        self.cfg["sampling"]["top_k"] = 1.5
        self.assert_invalid(needle="sampling.top_k")

    def test_min_p_out_of_range(self):
        for v in (-0.01, 1.5):
            self.cfg["sampling"]["min_p"] = v
            self.assert_invalid(needle="sampling.min_p")

    def test_repeat_penalty_out_of_range(self):
        for v in (0.4, 2.5):
            self.cfg["sampling"]["repeat_penalty"] = v
            self.assert_invalid(needle="sampling.repeat_penalty")

    def test_repeat_last_n_negative_rejected(self):
        self.cfg["sampling"]["repeat_last_n"] = -1
        self.assert_invalid(needle="sampling.repeat_last_n")

    def test_repeat_last_n_zero_valid(self):
        self.cfg["sampling"]["repeat_last_n"] = 0
        self.assert_valid()

    def test_stop_ids_empty_rejected(self):
        self.cfg["sampling"]["stop_token_ids"] = []
        self.assert_invalid(needle="sampling.stop_token_ids")

    def test_stop_ids_non_int_rejected(self):
        self.cfg["sampling"]["stop_token_ids"] = [248046, "stop"]
        self.assert_invalid(needle="sampling.stop_token_ids")

    def test_stop_ids_missing_rejected(self):
        del self.cfg["sampling"]["stop_token_ids"]
        self.assert_invalid(needle="sampling.stop_token_ids")


class TestScalarLeaves(_Base):
    def test_empty_alias(self):
        self.cfg["alias"] = "  "
        self.assert_invalid(needle="alias")

    def test_missing_reasoning_format(self):
        del self.cfg["reasoning_format"]
        self.assert_invalid(needle="reasoning_format")

    def test_api_key_missing(self):
        del self.cfg["api_key"]
        self.assert_invalid(needle="api_key")


class TestRenderConfig(_Base):
    def test_render_with_fresh_pin(self):
        # The MAP 1.4 flow: template + the build's digest -> a fully-pinned
        # rendered config. Driven with an in-memory placeholder template so
        # the test is state-independent of whether the tracked file has
        # already been stamped by a real MAP 1.4 build.
        tmpl = _template_dict()
        tmpl["image"]["pin"]["tag"] = "bonsai2-server:<tag-at-install>"
        tmpl["image"]["pin"]["sha256"] = "<docker-content-digest-at-install>"
        rendered, errors = render.render_config(
            tmpl, {"tag": "bonsai2-server:1.0", "sha256": VALID_DIGEST}
        )
        self.assertEqual(errors, [], f"rendered config must be error-free: {errors}")
        self.assertEqual(rendered["image"]["pin"]["tag"], "bonsai2-server:1.0")
        self.assertEqual(rendered["image"]["pin"]["sha256"], VALID_DIGEST)

    def test_render_overwrites_stamped_pin(self):
        # Re-running install.sh with a new digest must overwrite an already
        # stamped image pin (idempotency: S6.1 step 2 "digest re-recorded").
        cfg = _template_dict()  # fully pinned, stamped form
        new_digest = "sha256:" + "ab" * 32
        rendered, errors = render.render_config(
            cfg, {"tag": "bonsai2-server:1.1", "sha256": new_digest}
        )
        self.assertEqual(errors, [])
        self.assertEqual(rendered["image"]["pin"]["sha256"], new_digest)
        self.assertEqual(rendered["image"]["pin"]["tag"], "bonsai2-server:1.1")

    def test_render_without_pin_keeps_placeholders(self):
        # No pin override -> a placeholder pin is kept as-is (still a
        # warning, never an error). In-memory so it is state-independent.
        tmpl = _template_dict()
        tmpl["image"]["pin"]["tag"] = "bonsai2-server:<tag-at-install>"
        tmpl["image"]["pin"]["sha256"] = "<docker-content-digest-at-install>"
        rendered, errors = render.render_config(tmpl)
        self.assertEqual(errors, [], f"rendered config must be error-free: {errors}")
        self.assertIn("<", rendered["image"]["pin"]["sha256"])

    def test_render_deep_copies(self):
        out, _ = render.render_config(self.cfg)
        out["instances"]["count"] = 7
        self.assertEqual(self.cfg["instances"]["count"], 3)


if __name__ == "__main__":
    unittest.main()
