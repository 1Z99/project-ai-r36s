"""Read-only timeline/safety tests; no website, SDL or media rendering."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "dev/render_web_walkthrough.py"
SPEC = importlib.util.spec_from_file_location("web_walkthrough", SOURCE)
walkthrough = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(walkthrough)


class WalkthroughPlanTests(unittest.TestCase):
    def test_contiguous_seek_chapters_and_provenance(self):
        plan = walkthrough.timeline()
        self.assertEqual(plan["duration"], 78)
        self.assertEqual((plan["width"], plan["height"], plan["fps"]), (640, 480, 20))
        self.assertEqual(len(plan["chapters"]), 9)
        self.assertEqual([(c["id"], c["start"], c["end"]) for c in plan["chapters"]], [
            ("intro", 0, 2), ("loading", 2, 4), ("keyboard", 4, 12),
            ("answer", 12, 20), ("read-aloud", 20, 36), ("settings", 36, 50),
            ("dictation", 50, 64), ("history", 64, 73), ("outro", 73, 78),
        ])
        self.assertEqual(plan["chapters"][0]["start"], 0)
        self.assertEqual(plan["chapters"][-1]["end"], plan["duration"])
        for index, chapter in enumerate(plan["chapters"]):
            self.assertLess(chapter["start"], chapter["end"])
            if index:
                self.assertEqual(plan["chapters"][index - 1]["end"], chapter["start"])
        for flag in ("simulatedMic", "sampleReplies", "localPC", "productionRenderer"):
            self.assertIs(plan[flag], True)
        for flag in ("hasAudio", "liveModel", "microphoneAccess", "deviceAccess"):
            self.assertIs(plan[flag], False)
        self.assertEqual(plan["model"], "fast")
        plan["chapters"][0]["start"] = 99
        self.assertEqual(walkthrough.timeline()["chapters"][0]["start"], 0)

    def test_validate_only_cannot_render_probe_or_write(self):
        output = io.StringIO()
        with patch.object(walkthrough, "render", side_effect=AssertionError("render")), \
                patch.object(walkthrough.subprocess, "run", side_effect=AssertionError("subprocess")), \
                patch.object(walkthrough.tempfile, "TemporaryDirectory", side_effect=AssertionError("write")), \
                patch.object(walkthrough.ctypes, "CDLL", side_effect=AssertionError("load SDL")), \
                redirect_stdout(output):
            self.assertEqual(walkthrough.main(["--validate-only", "--output-dir", "/nonexistent",
                                               "--speech-root", "/nonexistent"]), 0)
        self.assertEqual(json.loads(output.getvalue()), walkthrough.timeline())

    def test_actions_require_review_then_explicit_send(self):
        events = walkthrough.actions()
        self.assertEqual(events, sorted(events, key=lambda event: event[0]))
        self.assertLess(events[-1][0], walkthrough.DURATION)
        typed = "".join(" " if value == "SPACE" else value
                        for _, kind, value in events if kind == "key" and value not in ("MIC", "SEND"))
        self.assertEqual(typed, walkthrough.QUESTION)
        self.assertEqual([(t, value) for t, kind, value in events if kind == "key" and value == "SEND"],
                         [(11.4, "SEND"), (60.0, "SEND")])
        self.assertIn((57.0, "transcript", walkthrough.DICTATION), events)
        timings = walkthrough.approximate_timings(14.4)
        self.assertEqual([walkthrough.REPLY[a:b] for _, a, b in timings], walkthrough.REPLY.split())
        self.assertTrue(all(0 <= t < 14.4 for t, _, _ in timings))

    def test_local_speech_is_isolated_and_uses_production_synthesis(self):
        root = Path("/temporary/speech")
        app = SimpleNamespace()
        result = SimpleNamespace(returncode=0, stdout="[[0.0, 0, 1]]", stderr="")
        with patch.dict(walkthrough.os.environ, {"LD_LIBRARY_PATH": "/existing/lib"}), \
                patch.object(walkthrough.subprocess, "run", return_value=result) as run, \
                patch.object(walkthrough.ctypes, "CDLL", side_effect=AssertionError("parent loaded speech")):
            wav, timings, backend, timing_kind = walkthrough.speech(app, Path("/output"), root)
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertIn("app.synthesize_speech", command[2])
        self.assertIn("'speech_rate': 1.0", command[2])
        self.assertEqual(command[-1], walkthrough.REPLY)
        self.assertEqual(environment["LD_LIBRARY_PATH"], "/temporary/speech/usr/lib:/existing/lib")
        self.assertEqual(environment["ESPEAK_DATA_PATH"], "/temporary/speech/usr/share/espeak-ng-data")
        self.assertEqual((wav, timings, backend, timing_kind),
                         (Path("/output/speech.wav"), [[0.0, 0, 1]], "espeak-ng", "speech-word-events"))

    def test_explicit_speech_root_never_silently_falls_back(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="missing speech data")
        with patch.object(walkthrough.subprocess, "run", return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, "Explicit speech root failed"):
                walkthrough.speech(SimpleNamespace(), Path("/output"), Path("/temporary/speech"))
        self.assertEqual(run.call_count, 1)

    def test_prerecorded_playback_captions_are_explicit(self):
        plan = walkthrough.timeline()
        plan.update(hasAudio=True, prerecordedMicPlayback=True)
        text = walkthrough.captions(plan, walkthrough.approximate_timings(9.27), 29.77)
        self.assertIn("00:00:44.000 --> 00:00:48.000", text)
        self.assertIn("Prerecorded synthetic sample, not live microphone audio", text)
        self.assertIn(walkthrough.MIC_PLAYBACK, text)


if __name__ == "__main__":
    unittest.main()
