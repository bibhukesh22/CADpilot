"""Full end-to-end integration tests for the NEMA 17 workflow on Mock infrastructure."""

import http.server
import os
import sys
import tempfile
import threading
import unittest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from agent.agent import CADPilotAgent
from benchmarks import load_prompt_by_id
from mock.mock_bridge import MockBridgeHTTPHandler, SimulatedCADState
from validation import ValidationEngine


class TestNEMA17AgentIntegration(unittest.TestCase):
    """End-to-end integration tests for CADPilotAgent on NEMA 17 mounting plates."""

    def setUp(self):
        MockBridgeHTTPHandler.cad_state = SimulatedCADState()

    def test_01_full_nema17_agent_workflow_and_export(self):
        bm1 = load_prompt_by_id("BM-001")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            export_path = os.path.join(tmpdir, "nema17_integration.step").lower()
            prompt = (
                "Create a 42.3mm NEMA 17 mount plate with 4mm thickness, a 22mm center pilot hole, "
                f"and 4x M3 holes spaced 31mm apart with 2mm fillets, export to {export_path}"
            )

            agent = CADPilotAgent(endpoint_url="mock", verbose=False)
            state = agent.run(prompt)

            self.assertEqual(state["status"], "completed")
            self.assertTrue(state["feasibility"]["is_feasible"])
            self.assertEqual(state["reflection"]["retry_count"], 0)

            expected_tools = [
                "reset_design",
                "create_sketch",
                "extrude_profile",
                "cut_hole",
                "cut_hole",
                "cut_hole",
                "cut_hole",
                "cut_hole",
                "apply_fillet",
                "inspect_brep",
                "export_cad",
            ]
            actual_tools = [obs["tool"] for obs in state["observations"]]
            self.assertEqual(actual_tools, expected_tools)
            self.assertTrue(all(obs["status"] == "success" for obs in state["observations"]))

            # Verify B-Rep inspection output
            brep = state["final_output"]["brep"]
            self.assertEqual(brep["body_count"], 1)
            body = brep["bodies"][0]
            self.assertEqual(body["bounding_box"]["dimensions"], [42.3, 42.3, 4.0])

            cyls = body["cylindrical_features"]
            self.assertEqual(len(cyls), 5)
            diameters = sorted(c["diameter_mm"] for c in cyls)
            self.assertEqual(diameters, [3.0, 3.0, 3.0, 3.0, 22.0])

            # Verify Export Node output and artifact on disk
            export_info = state["final_output"]["export"]
            self.assertEqual(export_info["status"], "success")
            self.assertEqual(export_info["format"], "step")
            self.assertEqual(export_info["output_path"], export_path)
            self.assertTrue(os.path.exists(export_path))
            self.assertGreater(export_info["file_size_bytes"], 0)

            # Verify Validation Engine acceptance
            report = ValidationEngine().validate(brep, bm1["expected_outcome"])
            self.assertTrue(report.passed, f"Validation failed: {report.errors}")

    def test_02_nema17_oversized_fillet_self_correction_and_export(self):
        bm29 = load_prompt_by_id("BM-029")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            export_path = os.path.join(tmpdir, "nema17_recovered.step").lower()
            prompt = (
                "Create a 42.3mm NEMA 17 mount plate with 4mm thickness, 22mm center hole, "
                f"4x M3 holes spaced 31mm apart, and 12mm fillets, export to {export_path}"
            )

            agent = CADPilotAgent(endpoint_url="mock", verbose=False)
            state = agent.run(prompt)

            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["reflection"]["retry_count"], 1)
            self.assertLessEqual(state["spec"]["fillets"]["radius"], 2.0)
            self.assertTrue(os.path.exists(export_path))

            report = ValidationEngine().validate(state["final_output"]["brep"], bm29["expected_outcome"])
            self.assertTrue(report.passed, f"Recovered validation failed: {report.errors}")

    def test_03_nema17_over_http_mock_bridge(self):
        try:
            server = http.server.HTTPServer(("127.0.0.1", 0), MockBridgeHTTPHandler)
        except PermissionError:
            self.skipTest("Localhost socket binding is restricted in the current sandbox.")
            return

        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from client.test_client import CADPilotClient

            client = CADPilotClient(endpoint_url=f"http://127.0.0.1:{port}/")
            ping_resp = client.ping()
            if client._in_process_handler is not None:
                self.skipTest("Loopback HTTP client connections are restricted in the current sandbox.")
                return
            self.assertEqual(ping_resp["result"]["status"], "ok")

            with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
                export_path = os.path.join(tmpdir, "nema17_http.step").lower()
                prompt = (
                    "Create a 42.3mm NEMA 17 mount plate with 4mm thickness, 22mm center hole, "
                    f"and 4x M3 holes spaced 31mm apart with 2mm fillets, export to {export_path}"
                )
                agent = CADPilotAgent(endpoint_url=f"http://127.0.0.1:{port}/", client=client, verbose=False)
                state = agent.run(prompt)
                self.assertEqual(state["status"], "completed")
                self.assertTrue(os.path.exists(export_path))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
