"""Pure-plan checks: TV source, native libraries and a network are not required."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "dev/render_tv_captures.py"
spec = importlib.util.spec_from_file_location("tv_captures", SCRIPT)
captures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(captures)


class TVCaptureTest(unittest.TestCase):
    def test_plan_is_independent_and_covers_nine_native_states(self):
        first = captures.capture_plan()
        self.assertEqual(len(first["captures"]), 9)
        first["captures"].clear()
        self.assertEqual(len(captures.capture_plan()["captures"]), 9)
        self.assertEqual((first["width"], first["height"]), (640, 480))
        self.assertFalse(first["videoDecoding"])
        self.assertFalse(first["installation"])

    def test_validate_needs_no_checkout_or_writable_output(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--validate-only",
                                 "--source-dir", "/does/not/exist", "--output-dir", "/does/not/exist"],
                                check=True, capture_output=True, text=True)
        plan = json.loads(result.stdout)
        self.assertEqual(len(plan["captures"]), 9)
        self.assertFalse(plan["networkAccess"])
        self.assertNotIn("app", sys.modules)

    def test_forbidden_operations_fail_closed(self):
        with self.assertRaises(RuntimeError):
            captures.forbidden()

    def test_live_plan_requires_explicit_flag_without_loading_sources(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--validate-only", "--capture-streams"],
                                check=True, capture_output=True, text=True)
        plan = json.loads(result.stdout)
        self.assertTrue(plan["realPlayback"] and plan["networkAccess"] and plan["videoDecoding"])
        self.assertFalse(plan["fictionalChannels"] or plan["stillPhotographs"])

    def test_capture_without_network_opt_in_fails_before_loading_sources(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--source-dir", "/does/not/exist"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--capture-streams is required", result.stderr)
