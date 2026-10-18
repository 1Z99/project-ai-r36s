import ast
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))
import installer_ui as ui


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.cards = [dict(path=path, label=label, available=available, free=10 ** 10,
                           total=2 * 10 ** 10, installed=False, reason="")
                      for path, label, available in (("/roms", "SD1", True), ("/roms2", "SD2", False))]
        self.model = ui.Manager(cards=self.cards)

    def test_no_implicit_destination_even_when_only_one_available(self):
        self.model.button("down")
        self.model.button("ok")
        self.assertIsNone(self.model.chosen)
        self.assertEqual(self.model.focus, 0)
        self.assertEqual(self.model.state, "home")

    def test_unavailable_card_cannot_be_selected(self):
        self.model.button("right")
        self.model.button("ok")
        self.assertIsNone(self.model.chosen)
        self.assertEqual(self.model.focus, 0)

    def test_either_card_can_be_chosen_independent_of_roms_mode(self):
        self.cards[1]["available"] = True
        self.model.button("right")
        self.model.button("ok")
        self.assertEqual(self.model.chosen, 1)
        self.assertEqual(self.model.focus, 1)
        self.model.button("ok")
        self.assertEqual(self.model.state, "confirm")
        self.assertEqual(self.model.action, "install")
        self.assertFalse(self.model.confirm_yes)

    def test_destructive_confirmation_defaults_to_no(self):
        self.model.button("ok")
        self.model.button("down")
        self.model.button("down")
        self.model.button("ok")
        self.assertEqual(self.model.action, "uninstall")
        self.model.button("ok")
        self.assertEqual(self.model.state, "home")
        self.assertIsNone(self.model.thread)

    def test_update_preserves_correct_action_and_explicit_confirm(self):
        self.model.button("ok")
        self.model.button("down")
        self.model.button("ok")
        self.assertEqual(self.model.action, "update")
        with patch.object(self.model, "start") as start:
            self.model.button("right")
            self.model.button("ok")
            start.assert_called_once()

    def test_cancel_requires_confirmation(self):
        self.model.state = "working"
        self.model.button("back")
        self.assertFalse(self.model.cancel.is_set())
        self.model.button("ok")
        self.assertEqual(self.model.state, "working")
        self.assertFalse(self.model.cancel.is_set())
        self.model.button("back")
        self.model.button("right")
        self.model.button("ok")
        self.assertTrue(self.model.cancel.is_set())

    def test_failed_worker_stays_open_with_details_and_retry(self):
        class BrokenWorker:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, action):
                raise RuntimeError("Wi-Fi connection interrupted")

        self.model.worker = BrokenWorker
        self.model.chosen, self.model.action = 0, "install"
        self.model.start()
        self.model.thread.join(timeout=2)
        self.model.update()
        self.assertEqual(self.model.state, "failed")
        self.assertTrue(self.model.running)
        self.assertIn("Wi-Fi", self.model.error)
        self.model.button("refresh")
        self.assertEqual(self.model.state, "details")
        self.model.button("back")
        with patch.object(self.model, "start") as start:
            self.model.button("ok")
            start.assert_called_once()

    def test_cancelled_worker_is_not_error_or_exit(self):
        self.model.emit(dict(phase="result", result="cancelled", detail="Partial retained"))
        self.model.update()
        self.assertEqual(self.model.state, "cancelled")
        self.assertTrue(self.model.running)

    def test_source_failure_event_is_not_terminal_until_worker_finishes(self):
        self.model.state = "working"
        self.model.emit(dict(phase="error", label="Primary source failed", detail="HTTP 503"))
        self.model.emit(dict(phase="download", label="Trying mirror", speed=1000))
        self.model.update()
        self.assertEqual(self.model.state, "working")
        self.assertEqual(self.model.progress["label"], "Trying mirror")

    def test_refresh_requires_selection_again(self):
        self.model.chosen = 0
        with patch.object(ui, "discover_cards", return_value=self.cards):
            self.model.button("refresh")
        self.assertIsNone(self.model.chosen)

    def test_formatters_and_python39_syntax(self):
        self.assertEqual(ui.duration(None), "--:--")
        self.assertEqual(ui.duration(82), "01:22")
        self.assertEqual(ui.size(1024 ** 2), "1.0 MB")
        self.assertEqual(ui.size(None), "--")
        ast.parse(Path(ui.__file__).read_text(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
