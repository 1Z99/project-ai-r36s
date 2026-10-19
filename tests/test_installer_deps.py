import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
from unittest import mock


FILE = Path(__file__).resolve().parents[1] / "installer/installer_deps.py"
SPEC = importlib.util.spec_from_file_location("installer_deps", FILE)
deps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deps)


def result(output="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, output, stderr)


def status(missing=(), fatal=None, scope="manager"):
    return dict(scope=scope, ok=not missing and not fatal,
                missing=list(missing), fatal=fatal or {})


def plan(*packages):
    return "\n".join("Inst {} (1.2-3 Debian:stable [arm64])".format(name)
                     for name in packages)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        # Every host boundary is blocked by default, including read-only probes.
        # Tests must explicitly provide a fake; apt can never fall through.
        self.guards = {}
        for target in ("ctypes.CDLL", "shutil.which", "subprocess.run", "subprocess.Popen",
                       "os.uname", "os.geteuid", "Path.read_text", "Path.is_symlink", "Path.exists"):
            owner, attribute = target.split(".")
            guard = mock.patch.object(getattr(deps, owner), attribute,
                                      side_effect=AssertionError("Unmocked host action: " + target))
            self.guards[target] = guard.start()
            self.addCleanup(guard.stop)
        version = mock.patch.object(deps.sys, "version_info", (3, 13, 0))
        version.start()
        self.addCleanup(version.stop)

    def allow(self, name, value):
        guard = self.guards[name]
        guard.side_effect = None
        guard.return_value = value
        return guard

    def capabilities(self, absent=(), library_errors=None, speech=None):
        self.allow("Path.is_symlink", False)
        self.allow("Path.exists", False)
        self.guards["shutil.which"].side_effect = lambda name: None if name in absent else "/usr/bin/" + name
        errors = library_errors or {}

        def load(name):
            if name in errors:
                raise OSError(errors[name])
            return mock.Mock()

        self.guards["ctypes.CDLL"].side_effect = load
        self.allow("subprocess.run", result(json.dumps(speech or {"english": True})))

    def platform(self, release='ID=debian\n', machine="aarch64", uid=0):
        self.allow("Path.read_text", release)
        self.allow("os.uname", types.SimpleNamespace(machine=machine))
        self.allow("os.geteuid", uid)
        self.guards["shutil.which"].side_effect = lambda name: "/usr/bin/" + name

    def workflow(self, missing=("curl",), output=None):
        checker = mock.patch.object(deps, "check", side_effect=[status(missing), status()])
        check = checker.start()
        self.addCleanup(checker.stop)
        self.platform()
        output = output if output is not None else plan(*missing)
        run = self.allow("subprocess.run", None)
        run.side_effect = [result("arm64\n"), result(output), result(output)]
        process = mock.Mock()
        process.stdout = io.StringIO("Get:1 package\nSetting up package\n")
        process.poll.return_value = 0
        process.wait.return_value = 0
        self.allow("subprocess.Popen", process)
        return check, run, process

    def test_import_has_no_effects(self):
        fresh = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(fresh)
        for guard in self.guards.values():
            guard.assert_not_called()

    def test_all_present_uses_capabilities_not_os_or_package_database(self):
        self.capabilities()
        answer = deps.ensure("all")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["missing"], [])
        self.assertEqual(answer["installed"], [])
        self.assertEqual([call.args[0] for call in self.guards["ctypes.CDLL"].call_args_list], list(deps.SDL))
        self.guards["Path.read_text"].assert_not_called()
        self.guards["os.uname"].assert_not_called()
        self.guards["os.geteuid"].assert_not_called()
        self.guards["subprocess.Popen"].assert_not_called()
        command = self.guards["subprocess.run"].call_args
        self.assertEqual(command.args[0], [sys.executable, "-c", deps.SPEECH_PROBE])
        self.assertEqual(command.kwargs["timeout"], 10)
        self.assertNotIn("env", command.kwargs)  # Preserve firmware library/data paths.

    def test_manager_scope_and_missing_sdl_exact_soname(self):
        self.capabilities(absent=("curl",), library_errors={
            name: name + ": cannot open shared object file: No such file or directory"
            for name in deps.SDL})
        answer = deps.check()
        self.assertEqual(answer["missing"], list(deps.SDL.values()) + ["curl"])
        self.assertEqual(answer["fatal"], {})
        self.guards["subprocess.run"].assert_not_called()
        self.assertEqual([call.args[0] for call in self.guards["shutil.which"].call_args_list], ["curl"])

    def test_broken_sdl_and_missing_transitive_library_are_fatal(self):
        for error in ("libSDL2-2.0.so.0: invalid ELF header",
                      "libdrm.so.2: cannot open shared object file: No such file or directory",
                      "libSDL2-2.0.so.0: cannot open shared object file: Permission denied"):
            with self.subTest(error=error):
                self.capabilities(library_errors={"libSDL2-2.0.so.0": error})
                answer = deps.check()
                self.assertEqual(answer["missing"], [])
                self.assertIn("libSDL2-2.0.so.0", answer["fatal"])
                with self.assertRaisesRegex(RuntimeError, "SDL library cannot load"):
                    deps.ensure()
                self.guards["Path.read_text"].assert_not_called()
                self.guards["subprocess.Popen"].assert_not_called()

    def test_existing_library_or_dangling_symlink_is_fatal_even_on_exact_missing_error(self):
        for symlink in (True, False):
            with self.subTest(symlink=symlink):
                self.capabilities(library_errors={"libSDL2-2.0.so.0":
                    "libSDL2-2.0.so.0: cannot open shared object file: No such file or directory"})
                self.allow("Path.is_symlink", symlink)
                self.allow("Path.exists", not symlink)
                answer = deps.check()
                self.assertEqual(answer["missing"], [])
                self.assertIn("libSDL2-2.0.so.0", answer["fatal"])
                if symlink:
                    self.guards["Path.exists"].assert_not_called()

    def test_app_tools_are_deduplicated_and_fonts_are_not_packages(self):
        self.capabilities(absent=("arecord", "aplay", "amixer", "pkill", "curl"),
                          speech={"english": False})
        self.assertEqual(deps.check("app")["missing"], ["espeak-ng-data", "alsa-utils", "procps", "curl"])
        self.guards["ctypes.CDLL"].assert_not_called()

    def test_all_scope_combines_missing_capabilities(self):
        self.capabilities(absent=("curl", "arecord", "aplay", "amixer", "pkill"),
                          library_errors={name: name + ": cannot open shared object file: No such file or directory"
                                          for name in deps.SDL},
                          speech={"english": False})
        answer = deps.check("all")
        self.assertEqual(answer["missing"], list(deps.SDL.values()) +
                         ["espeak-ng-data", "alsa-utils", "procps", "curl"])
        self.assertEqual(answer["fatal"], {})

    def test_speech_missing_library_requests_library_and_data(self):
        self.capabilities(speech={"load_error": deps.SPEECH + ": cannot open shared object file: No such file or directory"})
        self.assertEqual(deps.check("app")["missing"], ["libespeak-ng1", "espeak-ng-data"])

    def test_speech_broken_library_is_not_reinstalled(self):
        self.capabilities(speech={"load_error": "libstdc++.so.6: version not found"})
        answer = deps.check("app")
        self.assertEqual(answer["missing"], [])
        self.assertIn(deps.SPEECH, answer["fatal"])

    def test_speech_timeout_crash_and_malformed_output_are_fatal(self):
        for response in (subprocess.TimeoutExpired("probe", 10), result("", -11, "segfault"),
                         result("not json"), result("[]")):
            with self.subTest(response=response):
                self.capabilities()
                if isinstance(response, Exception):
                    self.guards["subprocess.run"].side_effect = response
                else:
                    self.allow("subprocess.run", response)
                answer = deps.check("app")
                self.assertIn("speech", answer["fatal"])
                self.assertEqual(answer["missing"], [])

    def test_speech_child_uses_synchronous_no_audio_mode_and_terminates(self):
        for initialized, voice, english in ((22050, 0, True), (22050, -1, False), (-1, 0, False)):
            with self.subTest(initialized=initialized, voice=voice):
                library = mock.Mock()
                library.espeak_Initialize.return_value = initialized
                library.espeak_SetVoiceByName.return_value = voice
                fake = types.SimpleNamespace(CDLL=mock.Mock(return_value=library), c_int=object(), c_char_p=object())
                output = io.StringIO()
                with mock.patch.dict(sys.modules, {"ctypes": fake}), contextlib.redirect_stdout(output):
                    exec(deps.SPEECH_PROBE, {})
                self.assertEqual(json.loads(output.getvalue()), {"english": english})
                library.espeak_Initialize.assert_called_once_with(2, 0, None, 0x8000)
                library.espeak_Terminate.assert_called_once_with()
                if initialized > 0:
                    library.espeak_SetVoiceByName.assert_called_once_with(b"en")
                else:
                    library.espeak_SetVoiceByName.assert_not_called()

    def test_python_is_validation_only(self):
        self.capabilities()
        with mock.patch.object(deps.sys, "version_info", (3, 8, 20)):
            answer = deps.ensure(allow_install=False)
        self.assertIn("Python 3.9", answer["fatal"]["python"])
        self.assertEqual(answer["missing"], [])
        self.guards["Path.read_text"].assert_not_called()

    def test_check_and_allow_install_false_never_query_packages_or_platform(self):
        self.capabilities(absent=("curl",))
        self.assertEqual(deps.check()["missing"], ["curl"])
        answer = deps.ensure(allow_install=False)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["installed"], [])
        self.guards["Path.read_text"].assert_not_called()
        self.guards["os.geteuid"].assert_not_called()
        self.guards["subprocess.run"].assert_not_called()

    def test_invalid_scope(self):
        with self.assertRaisesRegex(ValueError, "scope"):
            deps.check("typo")
        for guard in self.guards.values():
            guard.assert_not_called()

    def test_unsupported_os_architecture_and_package_tools(self):
        for release, machine, absent, arch in (("ID=arch", "aarch64", None, "arm64"),
                                               ("ID=debian", "x86_64", None, "arm64"),
                                               ("ID=ubuntu", "aarch64", "apt-get", "arm64"),
                                               ("ID=ubuntu", "aarch64", "dpkg", "arm64"),
                                               ("ID=ubuntu", "aarch64", None, "armhf")):
            with self.subTest(release=release, machine=machine, absent=absent, arch=arch):
                with mock.patch.object(deps, "check", return_value=status(["curl"])):
                    self.platform(release, machine)
                    self.guards["shutil.which"].side_effect = lambda name: None if name == absent else "/usr/bin/" + name
                    run = self.allow("subprocess.run", result(arch))
                    run.reset_mock()
                    with self.assertRaisesRegex(RuntimeError, "Missing packages: curl.*Manual setup"):
                        deps.ensure()
                    self.assertTrue(all("apt-get" not in call.args[0][0] for call in run.call_args_list))
                    self.guards["subprocess.Popen"].assert_not_called()

    def test_unreadable_os_release_is_descriptive_and_nonmutating(self):
        with mock.patch.object(deps, "check", return_value=status(["curl"])):
            self.guards["Path.read_text"].side_effect = OSError("unreadable")
            with self.assertRaisesRegex(RuntimeError, r"unreadable[\s\S]*Missing packages: curl"):
                deps.ensure()
        self.guards["subprocess.run"].assert_not_called()

    def test_no_admin_denies_without_update_or_install(self):
        for sudo_present in (True, False):
            with self.subTest(sudo_present=sudo_present), mock.patch.object(deps, "check", return_value=status(["curl"])):
                self.platform(uid=1000)
                self.guards["shutil.which"].side_effect = lambda name: None if name == "sudo" and not sudo_present else "/usr/bin/" + name
                run = self.allow("subprocess.run", None)
                run.side_effect = [result("arm64"), result("sudo: a password is required", 1)]
                run.reset_mock()
                with self.assertRaisesRegex(RuntimeError, r"[Nn]oninteractive[\s\S]*Missing packages: curl"):
                    deps.ensure()
                self.assertTrue(all("apt-get" not in call.args[0][0] for call in run.call_args_list))
                self.guards["subprocess.Popen"].assert_not_called()

    def test_success_pins_complete_plan_reports_progress_and_reprobes(self):
        check, run, process = self.workflow(output=plan("curl", "libcurl4"))
        events = []
        with mock.patch.dict(deps.os.environ, {"APT_CONFIG": "/untrusted", "DEBIAN_FRONTEND": "dialog"}):
            answer = deps.ensure(emit=events.append)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["installed"], ["curl", "libcurl4"])
        self.assertEqual(check.call_args_list, [mock.call("manager"), mock.call("manager")])
        command = self.guards["subprocess.Popen"].call_args
        self.assertEqual(command.args[0], ["/usr/bin/apt-get"] + deps.OPTIONS + ["--yes"] + deps.INSTALL +
                         ["curl=1.2-3", "libcurl4=1.2-3"])
        self.assertEqual(command.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(command.kwargs["start_new_session"])
        self.assertNotIn("APT_CONFIG", command.kwargs["env"])
        self.assertEqual(command.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(command.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(len(run.call_args_list), 3)
        self.assertIn("--simulate", run.call_args_list[1].args[0])
        self.assertIn("curl=1.2-3", run.call_args_list[2].args[0])
        self.assertTrue(any("Get:1" in event["detail"] for event in events))
        self.assertTrue(any("Setting up" in event["detail"] for event in events))
        for event in events:
            self.assertEqual(event["phase"], "dependencies")
            self.assertEqual(set(event), {"phase", "label", "detail", "done", "total", "speed", "elapsed"})
            self.assertIsNone(event["total"])
            self.assertIsNone(event["speed"])
            self.assertGreaterEqual(event["elapsed"], 0)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_missing_english_data_is_installed_and_actually_reprobed(self):
        self.platform()
        run = self.allow("subprocess.run", None)
        run.side_effect = [result('{"english": false}'), result("arm64"),
                           result(plan("espeak-ng-data")), result(plan("espeak-ng-data")),
                           result('{"english": true}')]
        process = mock.Mock()
        process.stdout = io.StringIO("Setting up espeak-ng-data\n")
        process.poll.return_value = process.wait.return_value = 0
        self.allow("subprocess.Popen", process)
        answer = deps.ensure("app")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["installed"], ["espeak-ng-data"])
        self.assertEqual(run.call_count, 5)
        self.assertEqual(run.call_args_list[0].args[0], [sys.executable, "-c", deps.SPEECH_PROBE])
        self.assertEqual(run.call_args_list[-1].args[0], [sys.executable, "-c", deps.SPEECH_PROBE])
        self.guards["ctypes.CDLL"].assert_not_called()

    def test_id_like_and_sudo_noninteractive_environment(self):
        self.workflow()
        self.platform('ID=custom\nID_LIKE="ubuntu debian"\n', uid=1000)
        run = self.allow("subprocess.run", None)
        run.side_effect = [result("arm64"), result(), result(plan("curl")), result(plan("curl"))]
        deps.ensure()
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/sudo", "-n", "--", "true"])
        command = self.guards["subprocess.Popen"].call_args.args[0]
        self.assertEqual(command[:7], ["/usr/bin/sudo", "-n", "--", "/usr/bin/env",
                                     "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C", "/usr/bin/apt-get"])

    def test_simulation_failure_refreshes_once_with_bounded_network_and_lock(self):
        self.workflow()
        run = self.guards["subprocess.run"]
        run.side_effect = [result("arm64"), result("stale indexes", 100), result("Hit:1 packages"),
                           result(plan("curl")), result(plan("curl"))]
        events = []
        deps.ensure(emit=events.append)
        updates = [call for call in run.call_args_list if "update" in call.args[0]]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].args[0], ["/usr/bin/apt-get"] + deps.OPTIONS + ["update"])
        self.assertEqual(updates[0].kwargs["timeout"], 120)
        self.assertTrue(any("stale indexes" in event["detail"] for event in events))

    def test_refresh_or_second_simulation_failure_stops_with_last_output(self):
        for responses, message in (([result("arm64"), result("stale", 100), result("offline", 100)], "offline"),
                                   ([result("arm64"), result("stale", 100), result(), result("no candidate", 100)], "no candidate")):
            with self.subTest(message=message):
                self.workflow()
                self.guards["subprocess.run"].side_effect = responses
                with self.assertRaisesRegex(RuntimeError, message):
                    deps.ensure()
                self.guards["subprocess.Popen"].assert_not_called()

    def test_removals_and_any_installed_package_upgrade_are_rejected(self):
        for line in ("Remv firmware-old [1]", "Inst libc6 [2.31] (2.36 Debian [arm64])",
                     "Inst python3 [3.9] (3.13 Debian [arm64])", "Inst curl [1] (2 Debian [arm64])"):
            with self.subTest(line=line):
                self.workflow(output=plan("curl") + "\n" + line)
                with self.assertRaisesRegex(RuntimeError, "Refusing"):
                    deps.ensure()
                self.guards["subprocess.Popen"].assert_not_called()

    def test_all_protected_graphics_and_firmware_dependencies_are_rejected(self):
        for name in ("mesa-utils", "libegl1", "libgles2", "libgl1", "libgbm1", "libdrm2",
                     "libmali-rk", "xserver-xorg-core", "firmware-linux"):
            with self.subTest(name=name):
                self.workflow(output=plan("curl", name))
                with self.assertRaisesRegex(RuntimeError, "protected graphics/firmware"):
                    deps.ensure()
                self.guards["subprocess.Popen"].assert_not_called()

    def test_sdl_only_allowed_for_its_own_missing_capability(self):
        for package in deps.SDL.values():
            with self.subTest(package=package):
                self.workflow(output=plan("curl", package))
                with self.assertRaisesRegex(RuntimeError, "exact failed library probe"):
                    deps.ensure()
                self.guards["subprocess.Popen"].assert_not_called()
                self.workflow(missing=(package,))
                self.assertEqual(deps.ensure()["installed"], [package])
                self.guards["subprocess.Popen"].reset_mock()

    def test_conf_is_allowed_but_missing_package_must_be_in_plan(self):
        self.workflow(output=plan("curl") + "\nConf curl (1.2-3 Debian [arm64])")
        self.assertTrue(deps.ensure()["ok"])
        self.guards["subprocess.Popen"].reset_mock()
        self.workflow(output="curl is already the newest version")
        with self.assertRaisesRegex(RuntimeError, "without reinstalling"):
            deps.ensure()
        self.guards["subprocess.Popen"].assert_not_called()

    def test_malformed_duplicate_and_changed_plans_are_rejected(self):
        for changed in ("Inst curl (2.0 Debian [arm64])", plan("curl", "new-dependency"),
                        plan("curl") + "\nRemv other [1]", "Inst curl (1.2-3); touch /tmp/x"):
            with self.subTest(changed=changed):
                self.workflow()
                self.guards["subprocess.run"].side_effect = [result("arm64"), result(plan("curl")), result(changed)]
                with self.assertRaises(RuntimeError):
                    deps.ensure()
                self.guards["subprocess.Popen"].assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            deps._audit(plan("curl", "curl"), ["curl"])

    def test_multiarch_and_epoch_versions_are_pinned(self):
        self.workflow(output="Inst curl:arm64 (1:8.0~rc1-2 Debian [arm64])")
        self.assertEqual(deps.ensure()["installed"], ["curl:arm64"])
        self.assertIn("curl:arm64=1:8.0~rc1-2", self.guards["subprocess.Popen"].call_args.args[0])

    def test_cancel_before_work_does_not_probe(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(deps.DependencyCancelled):
            deps.ensure(cancel=cancel)
        for guard in self.guards.values():
            guard.assert_not_called()

    def test_cancel_immediately_before_install_does_not_start_apt(self):
        self.workflow()
        cancel = threading.Event()

        def emit(event):
            if event["label"] == "Installing system dependencies":
                cancel.set()

        with self.assertRaises(deps.DependencyCancelled):
            deps.ensure(cancel=cancel, emit=emit)
        self.guards["subprocess.Popen"].assert_not_called()

    def test_cancel_during_install_is_deferred_until_wait_and_recheck(self):
        check, _, process = self.workflow()
        cancel, events = threading.Event(), []

        def start(*args, **kwargs):
            cancel.set()
            return process

        self.guards["subprocess.Popen"].side_effect = start
        with self.assertRaises(deps.DependencyCancelled):
            deps.ensure(cancel=cancel, emit=events.append)
        process.wait.assert_called_once_with()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertEqual(check.call_count, 2)
        self.assertTrue(any(event["label"] == "Finishing system setup safely..." for event in events))
        self.assertTrue(any(event["label"] == "Rechecking system dependencies" for event in events))

    def test_failed_apt_is_not_hidden_by_cancellation_and_reports_bounded_tail(self):
        _, _, process = self.workflow()
        cancel = threading.Event()

        def start(*args, **kwargs):
            cancel.set()
            return process

        self.guards["subprocess.Popen"].side_effect = start
        process.wait.return_value = 100
        process.stdout = io.StringIO("earliest\n" + "x" * 10000 + "\n" + "later\n" * 30 + "dpkg failed finally\n")
        with self.assertRaisesRegex(RuntimeError, "dpkg failed finally") as caught:
            deps.ensure(cancel=cancel)
        self.assertNotIn("earliest", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 42000)

    def test_post_install_capability_failure_is_reported(self):
        check, _, _ = self.workflow()
        check.side_effect = [status(["curl"]), status(["curl"], {"speech": "English still unavailable"})]
        with self.assertRaisesRegex(RuntimeError, "still unavailable after apt: curl English still unavailable"):
            deps.ensure()

    def test_broken_progress_callback_still_drains_and_waits_for_apt(self):
        _, _, process = self.workflow()
        process.stdout = io.StringIO("log output\n" * 1000)

        def report(label, detail):
            raise ValueError("UI gone")

        with self.assertRaisesRegex(RuntimeError, "after apt finished: UI gone"):
            deps._install(["fake-apt"], report, None)
        process.wait.assert_called_once_with()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_silent_install_reports_deferred_cancel_without_killing_dpkg(self):
        check, _, process = self.workflow()
        cancel, release = threading.Event(), threading.Event()
        stream = mock.Mock()

        def readline(limit):
            self.assertEqual(limit, 2048)
            if not release.wait(2):
                raise OSError("test stream was not released")
            return ""

        def start(*args, **kwargs):
            cancel.set()
            return process

        def emit(event):
            if event["label"] == "Finishing system setup safely...":
                release.set()

        stream.readline.side_effect = readline
        process.stdout = stream
        self.guards["subprocess.Popen"].side_effect = start
        with self.assertRaises(deps.DependencyCancelled):
            deps.ensure(cancel=cancel, emit=emit)
        self.assertTrue(release.is_set())
        self.assertEqual(check.call_count, 2)
        process.wait.assert_called_once_with()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_run_timeout_includes_last_output(self):
        self.guards["subprocess.run"].side_effect = subprocess.TimeoutExpired("fake", 120, output=b"network stalled")
        with self.assertRaisesRegex(RuntimeError, "network stalled"):
            deps._run(["fake"])

    def test_cli_check_is_read_only_and_returns_failure_for_missing(self):
        self.capabilities(absent=("curl",))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = deps.main(["--scope", "manager", "--check"])
        self.assertEqual(code, 1)
        self.assertIn("Missing packages: curl", output.getvalue())
        self.guards["Path.read_text"].assert_not_called()
        self.guards["subprocess.Popen"].assert_not_called()

    def test_cli_install_and_failure_diagnostics(self):
        self.workflow()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deps.main(["--install"]), 0)
        output = io.StringIO()
        with mock.patch.object(deps, "ensure", side_effect=RuntimeError("manual setup needed")), contextlib.redirect_stderr(output):
            self.assertEqual(deps.main(["--scope", "all", "--install"]), 1)
        self.assertIn("manual setup needed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
