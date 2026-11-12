"""Integration tests for native apply_fillet, export_cad, and Mock Bridge flows.

Covers:
1. Live Autodesk Fusion 360 tests (guarded by IN_FUSION; skipped when unavailable).
2. Stubbed adsk runtime integration tests exercising the actual native `apply_fillet`
   and `export_cad` implementations via `ToolDispatcher`.
3. Mock Bridge end-to-end fillet + export integration flows via `CADPilotClient`.
"""

import os
import sys
import tempfile
import types
import unittest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import addin.core.tools.features as features_mod
import addin.core.tools.export as export_mod
from addin.core.dispatcher import ToolDispatcher
from addin.core.errors import ENTITY_NOT_FOUND, EXPORT_FAILED, GEOMETRY_ERROR, INVALID_PARAMS
from client.test_client import CADPilotClient
from mock.mock_bridge import MockBridgeHTTPHandler, SimulatedCADState


class _StubCollection:
    def __init__(self, items=None):
        self._items = list(items or [])

    def add(self, item):
        self._items.append(item)

    def item(self, idx):
        return self._items[idx]

    @property
    def count(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


class _StubVertex:
    def __init__(self, x, y, z):
        self.geometry = types.SimpleNamespace(x=x, y=y, z=z)


class _StubEdge:
    def __init__(self, p1, p2):
        self.startVertex = _StubVertex(*p1)
        self.endVertex = _StubVertex(*p2)


class _StubFilletInput:
    def __init__(self):
        self.edge_sets = []

    def addConstantRadiusEdgeSet(self, edges, radius_val, is_tangent):
        self.edge_sets.append((edges, radius_val, is_tangent))


class _StubFilletFeatures:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.last_input = None

    def createInput(self):
        return _StubFilletInput()

    def add(self, fillet_input):
        self.last_input = fillet_input
        if self.should_fail:
            raise RuntimeError("InternalValidationError: Fillet radius exceeds wall thickness")
        return types.SimpleNamespace(name="Fillet1")


class _StubExportManager:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.executed_paths = []

    def createSTEPExportOptions(self, output_path):
        return ("step", output_path)

    def createSTLExportOptions(self, body, output_path):
        return types.SimpleNamespace(fmt="stl", body=body, output_path=output_path, meshRefinement=None)

    def createFusionArchiveExportOptions(self, output_path):
        return ("f3d", output_path)

    def execute(self, opts):
        if self.should_fail:
            raise IOError("Simulated Fusion export failure")
        path = opts.output_path if hasattr(opts, "output_path") else opts[1]
        self.executed_paths.append(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("CADPILOT_STUB_EXPORT_PAYLOAD")


def _build_stub_adsk(edges=None, body_count=1, fail_fillet=False, fail_export=False):
    fillet_features = _StubFilletFeatures(should_fail=fail_fillet)
    export_mgr = _StubExportManager(should_fail=fail_export)

    if edges is None:
        edges = [
            _StubEdge((0.0, 0.0, 0.0), (0.0, 0.0, 0.4)),
            _StubEdge((4.23, 0.0, 0.0), (4.23, 0.0, 0.4)),
            _StubEdge((4.23, 4.23, 0.0), (4.23, 4.23, 0.4)),
            _StubEdge((0.0, 4.23, 0.0), (0.0, 4.23, 0.4)),
            _StubEdge((0.0, 0.0, 0.0), (4.23, 0.0, 0.0)),  # horizontal edge
        ]

    bodies = [types.SimpleNamespace(name="Body1", edges=_StubCollection(edges)) for _ in range(body_count)]
    root_comp = types.SimpleNamespace(
        bRepBodies=_StubCollection(bodies),
        features=types.SimpleNamespace(filletFeatures=fillet_features),
    )
    design = types.SimpleNamespace(rootComponent=root_comp, exportManager=export_mgr)
    app = types.SimpleNamespace(activeProduct=design)

    adsk_core = types.SimpleNamespace(
        Application=types.SimpleNamespace(get=lambda: app),
        ObjectCollection=types.SimpleNamespace(create=lambda: _StubCollection()),
        ValueInput=types.SimpleNamespace(createByReal=lambda val: types.SimpleNamespace(real=val)),
    )
    adsk_fusion = types.SimpleNamespace(
        Design=types.SimpleNamespace(cast=lambda product: product),
        MeshRefinementSettings=types.SimpleNamespace(MeshRefinementHigh="high"),
    )
    return adsk_core, adsk_fusion, fillet_features, export_mgr


class TestNativeFilletAndExportLiveFusion(unittest.TestCase):
    """Live Fusion 360 tests — skipped unless running inside Autodesk Fusion."""

    @unittest.skipUnless(features_mod.IN_FUSION, "Live Autodesk Fusion 360 (adsk) runtime not available")
    def test_live_fusion_apply_fillet_and_export(self):
        resp = features_mod.apply_fillet({"target": "all_vertical_edges", "radius": 1.0})
        self.assertEqual(resp.get("status"), "success")


class TestNativeFilletAndExportStubbedDispatcher(unittest.TestCase):
    """Tests actual native `apply_fillet` and `export_cad` via `ToolDispatcher` using stubbed `adsk`."""

    def setUp(self):
        self._orig_feat_in_fusion = features_mod.IN_FUSION
        self._orig_exp_in_fusion = export_mod.IN_FUSION
        self._orig_feat_adsk = getattr(features_mod, "adsk", None)
        self._orig_exp_adsk = getattr(export_mod, "adsk", None)
        self.dispatcher = ToolDispatcher()

    def tearDown(self):
        features_mod.IN_FUSION = self._orig_feat_in_fusion
        export_mod.IN_FUSION = self._orig_exp_in_fusion
        if self._orig_feat_adsk is None:
            features_mod.__dict__.pop("adsk", None)
        else:
            features_mod.adsk = self._orig_feat_adsk
        if self._orig_exp_adsk is None:
            export_mod.__dict__.pop("adsk", None)
        else:
            export_mod.adsk = self._orig_exp_adsk

    def _install_stub(self, **kwargs):
        adsk_core, adsk_fusion, fillet_feats, export_mgr = _build_stub_adsk(**kwargs)
        adsk_pkg = types.SimpleNamespace(core=adsk_core, fusion=adsk_fusion)
        features_mod.adsk = adsk_pkg
        export_mod.adsk = adsk_pkg
        features_mod.IN_FUSION = True
        export_mod.IN_FUSION = True
        return fillet_feats, export_mgr

    def test_01_apply_fillet_vertical_edges_success_and_unit_conversion(self):
        fillet_feats, _ = self._install_stub()
        resp = self.dispatcher.dispatch({
            "jsonrpc": "2.0",
            "method": "apply_fillet",
            "params": {"target": "all_vertical_edges", "radius": 2.0},
            "id": 1,
        })
        self.assertIn("result", resp)
        res = resp["result"]
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["radius_mm"], 2.0)
        self.assertEqual(res["edges_filleted"], 4)
        # Verify mm -> cm unit conversion (2.0mm -> 0.2cm)
        _, radius_val, _ = fillet_feats.last_input.edge_sets[0]
        self.assertAlmostEqual(radius_val.real, 0.2, places=6)

    def test_02_apply_fillet_invalid_radius_returns_invalid_params(self):
        self._install_stub()
        resp = self.dispatcher.dispatch({
            "jsonrpc": "2.0",
            "method": "apply_fillet",
            "params": {"target": "all_vertical_edges", "radius": 0.0},
            "id": 2,
        })
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], INVALID_PARAMS)
        self.assertIn("hint", resp["error"]["data"])

    def test_03_apply_fillet_no_edges_returns_entity_not_found(self):
        self._install_stub(edges=[])
        resp = self.dispatcher.dispatch({
            "jsonrpc": "2.0",
            "method": "apply_fillet",
            "params": {"target": "all_vertical_edges", "radius": 1.5},
            "id": 3,
        })
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], ENTITY_NOT_FOUND)

    def test_04_apply_fillet_kernel_failure_returns_geometry_error_with_hint(self):
        self._install_stub(fail_fillet=True)
        resp = self.dispatcher.dispatch({
            "jsonrpc": "2.0",
            "method": "apply_fillet",
            "params": {"target": "all_vertical_edges", "radius": 10.0},
            "id": 4,
        })
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], GEOMETRY_ERROR)
        self.assertIn("hint", resp["error"]["data"])
        self.assertIn("4.0mm", resp["error"]["data"]["hint"])

    def test_05_export_cad_step_stl_f3d_success(self):
        self._install_stub()
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, fmt in enumerate(("step", "stl", "f3d"), 1):
                out_path = os.path.join(tmpdir, f"part.{fmt}")
                resp = self.dispatcher.dispatch({
                    "jsonrpc": "2.0",
                    "method": "export_cad",
                    "params": {"format": fmt, "output_path": out_path},
                    "id": idx,
                })
                self.assertIn("result", resp, f"Export failed for {fmt}: {resp}")
                res = resp["result"]
                self.assertEqual(res["status"], "success")
                self.assertEqual(res["format"], fmt)
                self.assertTrue(os.path.exists(out_path))
                self.assertGreater(res["file_size_bytes"], 0)

    def test_06_export_cad_failure_cases(self):
        # Missing output_path -> INVALID_PARAMS
        self._install_stub()
        resp_missing = self.dispatcher.dispatch({
            "jsonrpc": "2.0",
            "method": "export_cad",
            "params": {"format": "step", "output_path": ""},
            "id": 10,
        })
        self.assertEqual(resp_missing["error"]["code"], INVALID_PARAMS)

        # Unsupported format -> EXPORT_FAILED
        with tempfile.TemporaryDirectory() as tmpdir:
            resp_bad_fmt = self.dispatcher.dispatch({
                "jsonrpc": "2.0",
                "method": "export_cad",
                "params": {"format": "obj", "output_path": os.path.join(tmpdir, "part.obj")},
                "id": 11,
            })
            self.assertEqual(resp_bad_fmt["error"]["code"], EXPORT_FAILED)

            # STL with 0 bodies -> EXPORT_FAILED
            self._install_stub(body_count=0)
            resp_no_body = self.dispatcher.dispatch({
                "jsonrpc": "2.0",
                "method": "export_cad",
                "params": {"format": "stl", "output_path": os.path.join(tmpdir, "empty.stl")},
                "id": 12,
            })
            self.assertEqual(resp_no_body["error"]["code"], EXPORT_FAILED)

            # Export manager failure -> EXPORT_FAILED
            self._install_stub(fail_export=True)
            resp_io_err = self.dispatcher.dispatch({
                "jsonrpc": "2.0",
                "method": "export_cad",
                "params": {"format": "step", "output_path": os.path.join(tmpdir, "fail.step")},
                "id": 13,
            })
            self.assertEqual(resp_io_err["error"]["code"], EXPORT_FAILED)
            self.assertIn("hint", resp_io_err["error"]["data"])


class TestMockBridgeFilletAndExportIntegration(unittest.TestCase):
    """Integration tests for fillet and export flows against Mock Bridge via CADPilotClient."""

    def setUp(self):
        MockBridgeHTTPHandler.cad_state = SimulatedCADState()
        self.client = CADPilotClient(endpoint_url="mock")
        self.client.reset_design()
        self.client.create_sketch(
            plane="xy",
            shapes=[{"type": "rectangle", "center": [0.0, 0.0], "width": 50.0, "height": 50.0}],
        )
        self.client.extrude_profile(distance=5.0)

    def test_01_fillet_and_multi_format_export_flow(self):
        fillet_resp = self.client.apply_fillet(target="all_vertical_edges", radius=2.0)
        self.assertEqual(fillet_resp["result"]["status"], "success")
        self.assertEqual(fillet_resp["result"]["radius_mm"], 2.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            for fmt in ("step", "stl", "f3d"):
                out_file = os.path.join(tmpdir, f"bracket.{fmt}")
                exp_resp = self.client.export_cad(format=fmt, output_path=out_file)
                self.assertEqual(exp_resp["result"]["status"], "success")
                self.assertEqual(exp_resp["result"]["format"], fmt)
                self.assertTrue(os.path.exists(out_file))
                self.assertGreater(exp_resp["result"]["file_size_bytes"], 0)

    def test_02_oversized_fillet_returns_structured_hint(self):
        err_resp = self.client.apply_fillet(target="all_vertical_edges", radius=8.0)
        self.assertIn("error", err_resp)
        self.assertEqual(err_resp["error"]["code"], GEOMETRY_ERROR)
        self.assertIn("Reduce fillet radius", err_resp["error"]["data"]["hint"])


if __name__ == "__main__":
    unittest.main()
