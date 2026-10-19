import ast
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from urllib.parse import unquote

_spec = importlib.util.spec_from_file_location(
    "installer_core", Path(__file__).resolve().parents[1] / "installer/installer_core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class HTTPFixture:
    """Deterministic curl integration server, including interrupted range bodies."""

    def __init__(self):
        self.data = bytes(range(256)) * 4096
        self.requests = []
        self.validators = []
        self.counts = {}
        self.files = {}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def handle(self):
                try:
                    super().handle()
                except ConnectionResetError:
                    pass

            def do_GET(self):
                mode = unquote(self.path.strip("/"))
                fixture.requests.append((mode, self.headers.get("Range")))
                fixture.validators.append(self.headers.get("If-Range"))
                fixture.counts[mode] = fixture.counts.get(mode, 0) + 1
                if mode in ("error", "missing") or (mode == "changed" and fixture.counts[mode] == 2):
                    self.send_response(404 if mode == "missing" else 503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/slow")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = fixture.files.get(mode, b"corrupt" if mode == "bad" else fixture.data)
                etag = '"v1"'
                if mode == "changed" and fixture.counts[mode] > 2:
                    data = b"new payload!" * (len(data) // 12)
                    etag = '"v2"'
                start = 0
                if (self.headers.get("Range") and mode not in ("ignore", "drop-ignore")
                        and self.headers.get("If-Range", etag) == etag):
                    start = int(self.headers["Range"].split("=")[1].split("-")[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */{}".format(len(data)))
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(206 if start else 200)
                if mode != "no-validator":
                    self.send_header("ETag", etag)
                if start:
                    self.send_header("Content-Range", "bytes {}-{}/{}".format(start, len(data) - 1, len(data)))
                if mode != "unknown":
                    self.send_header("Content-Length", str(len(data) - start))
                else:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.end_headers()
                try:
                    if (mode in ("drop", "once", "drop-ignore", "changed", "no-validator")
                            and (mode == "drop" or fixture.counts[mode] == 1)):
                        self.wfile.write(data[start:start + 65536])
                        self.wfile.flush()
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                    elif mode == "slow":
                        for pos in range(start, len(data), 32768):
                            self.wfile.write(data[pos:pos + 32768])
                            self.wfile.flush()
                            time.sleep(.025)
                    else:
                        self.wfile.write(data[start:])
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:{}".format(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.card = self.base / "card"
        self.card.mkdir()
        self.es = self.base / "es_systems.cfg"
        self.unrelated = ('<!-- keep this comment -->\n<system>\n<name>signal227</name>\n'
                          '<path>/roms2/227/</path><command>custom &amp; command</command>\n</system>\n')
        self.es.write_text('<systemList>\n' + self.unrelated + '</systemList>\n')
        self.themes = self.base / "themes"
        self.theme = self.themes / "test-theme"
        (self.theme / "_art/posters").mkdir(parents=True)
        (self.theme / "theme.xml").write_text('<theme><formatVersion>4</formatVersion></theme>')
        self.events = []
        self.worker = core.Installer(self.card, es_cfg=self.es, themes=self.themes, emit=self.events.append)


class InstallerTest(BaseTest):
    def test_stock_theme_with_comment_before_xml_declaration(self):
        self.theme.joinpath("theme.xml").write_text(
            '<!-- stock theme header -->\n<?xml version="1.0"?>\n'
            '<theme><formatVersion>4</formatVersion></theme>')
        self.worker.root.mkdir(parents=True)
        self.worker.root.joinpath("227-theme.xml").write_text(
            '<theme><formatVersion>7</formatVersion></theme>')
        self.worker.root.joinpath("227-art.jpg").write_bytes(b"poster")
        original = self.theme.joinpath("theme.xml").read_bytes()
        self.worker._themes(verify=True)
        self.worker._themes()
        self.assertEqual(self.theme.joinpath("theme.xml").read_bytes(), original)
        self.assertEqual(ET.parse(self.theme / "227/theme.xml").getroot().findtext("formatVersion"), "4")

    def bundle(self):
        bundle = self.base / "bundle"
        for folder, names in (("app", core.APP_FILES), ("assets", core.ASSET_FILES),
                              ("licenses", core.LICENSE_FILES), ("system", core.SYSTEM_FILES)):
            (bundle / folder).mkdir(parents=True)
            for name in names:
                (bundle / folder / name).write_text("# test bundle " + name)
        (bundle / "system/227 AI.sh").write_text('#!/bin/sh\nexec python3 "../tools/project_ai_r36s.py"\n')
        (bundle / "system/gamelist.xml").write_text(
            '<gameList><game><path>./227 AI.sh</path><name>227 AI</name></game>'
            '<game><path>./MIC TEST.sh</path><name>MIC TEST</name></game></gameList>')
        (bundle / "system/227-theme.xml").write_text('<theme><formatVersion>7</formatVersion></theme>')
        archive = bundle / core.RUNTIME
        with tarfile.open(str(archive), "w:gz") as tar:
            for name, data in (("runtime/llama-server", b"llama"),
                               ("runtime/libllama.so", b"library"),
                               ("whisper/whisper-cli", b"whisper")):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755
                tar.addfile(member, io.BytesIO(data))
        sha = digest(archive.read_bytes())
        (bundle / (core.RUNTIME + ".sha256")).write_text(sha + "  " + core.RUNTIME + "\n")
        models = []
        for name, _, hf, folder in core.MODELS:
            data = (name + " model").encode()
            (bundle / name).write_bytes(data)
            models.append((name, digest(data), hf, folder))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(core, "RUNTIME_SHA", sha).start()
        mock.patch.object(core, "MODELS", tuple(models)).start()
        self.worker.source_dir = bundle
        return bundle

    def test_python39_syntax_and_independent_import(self):
        source = Path(core.__file__).read_text()
        ast.parse(source, feature_version=(3, 9))
        spec = importlib.util.spec_from_file_location("standalone_installer", core.__file__)
        standalone = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(standalone)
        self.assertTrue(callable(standalone.Installer))

    def test_staged_repository_bundle_and_real_runtime(self):
        repository = Path(__file__).resolve().parents[1]
        if not (repository / core.RUNTIME).is_file():
            self.skipTest("Local release runtime is not present in this checkout")
        self.worker.source_dir = repository
        stage = self.base / "real-stage"
        stage.mkdir()
        self.worker._stage_files(stage)
        self.assertTrue(self.worker._stage_runtime(stage))
        runtime = stage / "runtime-tree"
        self.assertTrue((runtime / "runtime/llama-server").is_file())
        self.assertTrue((runtime / "whisper/whisper-cli").is_file())
        self.assertFalse(self.worker.sysdir.exists())
        self.assertNotIn("<name>227</name>", self.es.read_text())

    @unittest.skipUnless(shutil.which("curl"), "curl required")
    def test_remote_bundle_install_offline_release_first(self):
        bundle = self.bundle()
        http = HTTPFixture()
        self.addCleanup(http.close)
        for path in bundle.rglob("*"):
            if path.is_file():
                http.files["bundle/" + path.relative_to(bundle).as_posix()] = path.read_bytes()
        self.worker.source_dir = None
        self.worker.raw = http.url + "/bundle"
        self.worker.rel = http.url + "/bundle"
        models = tuple((name, sha, http.url + "/hf/", folder) for name, sha, _, folder in core.MODELS)
        with mock.patch.object(core, "MODELS", models):
            result = self.worker.run("install")
            self.assertEqual(result["outcome"], "installed")
            self.assertEqual(result["source"], "Configured source")
            self.assertIsNone(result["revision"])
            http.requests.clear()
            with mock.patch.object(self.worker, "_commit", side_effect=AssertionError("no-op committed")):
                self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")
            expected = {"bundle/" + folder + "/" + name
                        for folder, names in (("app", core.APP_FILES), ("assets", core.ASSET_FILES),
                                              ("licenses", core.LICENSE_FILES), ("system", core.SYSTEM_FILES))
                        for name in names}
            self.assertEqual({path for path, _ in http.requests}, expected)
            self.assertTrue(all(offset is None for _, offset in http.requests))
            http.files["bundle/assets/words.txt"] = b"fresh remote words"
            result = self.worker.run("update")
            self.assertEqual(result["outcome"], "updated")
            self.assertEqual(result["changes"], [str(self.worker.root / "words.txt")])
            self.assertEqual((self.worker.root / "words.txt").read_bytes(), b"fresh remote words")
        self.assertTrue((self.worker.sysdir / "227 AI.sh").is_file())
        self.assertIn("<name>227</name>", self.es.read_text())
        self.assertFalse(any(path.startswith("hf/") for path, _ in http.requests))
        for name, sha, _, folder in models:
            self.assertEqual(digest((self.worker.root / folder / name).read_bytes()), sha)
        self.assertEqual(self.events[-1]["phase"], "complete")

    def test_existing_corrupt_runtime_is_replaced_from_verified_cache(self):
        self.bundle()
        self.worker.run("install")
        executable = self.worker.root / "runtime/llama-server"
        executable.write_text("corrupt executable")
        result = self.worker.run("update")
        self.assertEqual(result["outcome"], "repaired")
        self.assertEqual(executable.read_bytes(), b"llama")
        self.assertTrue(self.worker._runtime_valid())

    def test_unchanged_runs_skip_commit_and_preserve_all_installed_mtimes(self):
        self.bundle()
        result = self.worker.run("install")
        self.assertEqual(set(result), {"outcome", "source", "revision", "changes", "detail"})
        self.assertEqual(result["outcome"], "installed")
        self.assertEqual(result["source"], "Local bundle")
        self.assertIsNone(result["revision"])
        (self.worker.root / "config.json").write_text("custom settings, not installer defaults")
        (self.worker.root / "chats.json").write_text("private chats")
        # A verified installation needs no archive or model source on recheck.
        for path in self.worker.source_dir.iterdir():
            if path.is_file():
                path.unlink()
        paths = [self.es] + [p for folder in (self.worker.tools, self.worker.sysdir, self.themes)
                            for p in folder.rglob("*")]
        before = {p: (p.stat().st_mode, p.stat().st_mtime_ns,
                      p.read_bytes() if p.is_file() else None) for p in paths}
        with mock.patch.object(self.worker, "_commit", side_effect=AssertionError("no-op committed")):
            for action in ("update", "update", "install"):
                result = self.worker.run(action)
                self.assertEqual(result["outcome"], "up_to_date")
                self.assertEqual(result["changes"], [])
                self.assertIn("up-to-date", self.events[-1]["label"])
                self.assertEqual(self.events[-1]["detail"], result["detail"])
        self.assertEqual(before, {p: (p.stat().st_mode, p.stat().st_mtime_ns,
                                     p.read_bytes() if p.is_file() else None) for p in paths})
        self.assertFalse(list(self.worker.cache.glob("stage-*")))

    def test_all_assets_licenses_and_config_are_checked_for_repair(self):
        self.bundle()
        self.worker.run("install")
        targets = [self.worker.root / name for name in core.ASSET_FILES]
        targets += [self.worker.root / "licenses" / name for name in core.LICENSE_FILES]
        targets += [self.worker.root / "models/LICENSE-LFM2.5.txt", self.worker.root / "config.json"]
        for target in targets:
            with self.subTest(target=target):
                target.unlink()
                result = self.worker.run("update")
                self.assertEqual(result["outcome"], "repaired")
                self.assertIn(str(target), result["changes"])
                self.assertTrue(target.is_file())
                self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")

    def test_missing_execute_permissions_are_repaired(self):
        self.bundle()
        self.worker.run("install")
        for path in (self.worker.sysdir / "227 AI.sh", self.worker.root / "runtime/llama-server",
                     self.worker.root / "whisper/whisper-cli"):
            with self.subTest(path=path):
                path.chmod(0o644)
                result = self.worker.run("update")
                self.assertEqual(result["outcome"], "repaired")
                self.assertTrue(os.access(path, os.X_OK))
                self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")

    def test_model_and_runtime_manifest_damage_are_repaired_not_reused(self):
        self.bundle()
        self.worker.run("install")
        for name, _, _, folder in core.MODELS:
            path = self.worker.root / folder / name
            path.write_text("corrupt model")
            result = self.worker.run("update")
            self.assertEqual(result["outcome"], "repaired")
            self.assertIn(str(path), result["changes"])
        marker = self.worker.root / ".installer-runtime.json"
        for content in ("not json", json.dumps({"archive_sha256": core.RUNTIME_SHA, "files": {}})):
            marker.write_text(content)
            self.assertEqual(self.worker.run("update")["outcome"], "repaired")
            self.assertTrue(self.worker._runtime_valid())

    def test_gamelist_semantic_comparison_and_repair_preserve_es_metadata(self):
        bundle = self.bundle()
        expected = ET.parse(bundle / "system/gamelist.xml")
        game = expected.getroot().find("game")
        for tag, text in (("image", "./227-art.jpg"), ("desc", "A helpful assistant"),
                          ("developer", "1Z99"), ("genre", "AI")):
            ET.SubElement(game, tag).text = text
        expected.write(bundle / "system/gamelist.xml")
        self.worker.run("install")
        path = self.worker.sysdir / "gamelist.xml"
        tree = ET.parse(path)
        game = tree.getroot().find("game")
        for tag, text in (("lastplayed", "20261019T100000"), ("playcount", "42"),
                          ("capturedthumbnail", "./capture.png"), ("favorite", "true")):
            ET.SubElement(game, tag).text = text
        game[:] = list(reversed(list(game)))
        tree.write(path, encoding="utf-8", xml_declaration=True)
        original = path.read_bytes(), path.stat().st_mtime_ns
        self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)
        for tag in ("name", "path", "image", "desc", "developer", "genre"):
            with self.subTest(tag=tag):
                tree = ET.parse(path)
                tree.getroot().find("game/" + tag).text = "stale metadata"
                tree.write(path)
                result = self.worker.run("update")
                self.assertEqual(result["outcome"], "repaired")
                self.assertIn(str(path), result["changes"])
                self.assertEqual(ET.parse(path).findtext("game/playcount"), "42")
                self.assertEqual(ET.parse(path).findtext("game/capturedthumbnail"), "./capture.png")
        tree = ET.parse(path)
        legacy = ET.SubElement(tree.getroot(), "game")
        ET.SubElement(legacy, "path").text = "./MIC TEST.sh"
        tree.write(path)
        (self.worker.tools / "mic_test.py").write_text("legacy")
        (self.worker.sysdir / "MIC TEST.sh").write_text("legacy")
        self.assertEqual(self.worker.run("update")["outcome"], "repaired")
        self.assertEqual(len(ET.parse(path).getroot().findall("game")), 1)
        self.assertEqual(ET.parse(path).findtext("game/playcount"), "42")
        self.assertFalse((self.worker.tools / "mic_test.py").exists())
        self.assertFalse((self.worker.sysdir / "MIC TEST.sh").exists())
        self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")

    def test_es_registration_and_derived_theme_outputs_are_checked(self):
        self.bundle()
        self.worker.run("install")
        self.es.write_text(self.es.read_text().replace(str(self.worker.sysdir), str(self.card / "old")))
        result = self.worker.run("update")
        self.assertEqual(result["outcome"], "repaired")
        self.assertEqual(result["changes"], [str(self.es)])
        self.theme.joinpath("theme.xml").write_text('<theme><formatVersion>9</formatVersion></theme>')
        result = self.worker.run("update")
        self.assertEqual(result["changes"], [str(self.theme / "227/theme.xml")])
        self.assertIn("<formatVersion>9</formatVersion>", (self.theme / "227/theme.xml").read_text())
        for target in (self.theme / "227/theme.xml", self.theme / "_art/posters/227.jpg"):
            target.write_text("stale theme file")
            self.assertEqual(self.worker.run("update")["outcome"], "repaired")
        png = self.theme / "_art/posters/227.png"
        png.write_bytes(b"old PNG")
        self.assertIn(str(png), self.worker.run("update")["changes"])
        self.assertFalse(png.exists())
        self.es.write_text('<systemList>' + self.unrelated + '</systemList>')
        self.assertIn(str(self.es), self.worker.run("update")["changes"])
        self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")

    def test_changed_source_updates_only_changed_outputs(self):
        bundle = self.bundle()
        self.worker.run("install")
        paths = [self.es] + [p for folder in (self.worker.tools, self.worker.sysdir, self.themes)
                            for p in folder.rglob("*") if p.is_file()]
        before = {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in paths}
        changed = {self.worker.tools / core.APP_FILES[0], self.worker.root / "words.txt"}
        (bundle / "app" / core.APP_FILES[0]).write_text("# new source payload\n")
        (bundle / "assets/words.txt").write_text("new source words\n")
        result = self.worker.run("update")
        self.assertEqual(result["outcome"], "updated")
        self.assertEqual(set(result["changes"]), {str(p) for p in changed})
        self.assertIn("Updated / repaired", self.events[-1]["label"])
        for path, snapshot in before.items():
            if path not in changed:
                self.assertEqual((path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns), snapshot)
        self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")

    def test_comparison_errors_never_report_up_to_date(self):
        self.bundle()
        self.worker.run("install")
        self.events.clear()
        compare = self.worker._file_matches

        def fail_comparison(source, target):
            if target == self.worker.root / "words.txt":
                raise OSError("comparison read failed")
            return compare(source, target)

        with mock.patch.object(self.worker, "_file_matches", side_effect=fail_comparison):
            with self.assertRaisesRegex(OSError, "comparison read failed"):
                self.worker.run("update")
        self.assertEqual(self.events[-1]["phase"], "error")
        self.assertFalse(any(event["phase"] == "complete" for event in self.events))

    def test_invalid_installed_xml_aborts_without_publishing(self):
        self.bundle()
        self.worker.run("install")
        for target in (self.worker.sysdir / "gamelist.xml", self.es):
            with self.subTest(target=target):
                original = target.read_bytes()
                target.write_text("<invalid XML")
                self.events.clear()
                with mock.patch.object(self.worker, "_commit", side_effect=AssertionError("invalid comparison committed")):
                    with self.assertRaises(ET.ParseError):
                        self.worker.run("update")
                self.assertFalse(any(event["phase"] == "complete" for event in self.events))
                self.assertEqual(target.read_text(), "<invalid XML")
                target.write_bytes(original)

    def test_configured_source_failure_never_reuses_cached_small_files(self):
        bundle = self.bundle()
        self.worker.run("install")
        self.worker.source_dir = None
        self.worker.raw = "https://configured.invalid/bundle"
        self.events.clear()
        cached = self.worker.cache / "bundle/app" / core.APP_FILES[0]
        cached.parent.mkdir(parents=True)
        cached.write_bytes((bundle / "app" / core.APP_FILES[0]).read_bytes())
        with mock.patch.object(self.worker, "_download", side_effect=RuntimeError("source unavailable")) as download:
            with self.assertRaisesRegex(RuntimeError, "source unavailable"):
                self.worker.run("update")
        self.assertEqual(download.call_args.args[1], None)
        self.assertEqual(download.call_args.args[2], [self.worker.raw + "/app/" + core.APP_FILES[0]])
        self.assertFalse(any(event["phase"] == "complete" for event in self.events))

    def test_dependencies_only_repair_does_not_commit_app_files(self):
        self.bundle()
        self.worker.run("install")
        with mock.patch.object(self.worker, "_dependencies", return_value={"installed": ["espeak-ng"]}), \
                mock.patch.object(self.worker, "_commit", side_effect=AssertionError("unneeded app commit")):
            result = self.worker.run("update")
        self.assertEqual(result["outcome"], "repaired")
        self.assertEqual(result["changes"], ["System dependencies: espeak-ng"])

    def test_dependencies_use_literal_real_card_paths_and_return_status(self):
        deps = mock.Mock()
        deps.DependencyCancelled = type("DependencyCancelled", (Exception,), {})
        deps.ensure.return_value = {"installed": ["espeak-ng"]}
        with mock.patch.dict(sys.modules, installer_deps=deps), \
                mock.patch.object(core, "CARD_PATHS", ((self.card, "mock card"),)):
            self.assertEqual(self.worker._dependencies(), {"installed": []})
            deps.ensure.assert_not_called()
            for prefix in (Path("/roms"), Path("/roms2")):
                worker = core.Installer(prefix, es_cfg=self.es, themes=self.themes)
                self.assertEqual(worker._dependencies(), {"installed": ["espeak-ng"]})
                self.assertEqual(deps.ensure.call_args.args, ("all",))
                deps.ensure.call_args.kwargs["emit"]({"phase": "dependencies", "label": "test"})
            deps.ensure.side_effect = deps.DependencyCancelled("cancelled dependencies")
            with self.assertRaisesRegex(core.Cancelled, "cancelled dependencies"):
                worker._dependencies()

    def test_default_github_source_resolves_fresh_immutable_revision_each_run(self):
        bundle = self.bundle()
        self.worker.run("install")
        self.worker.source_dir = None
        self.worker.raw = core.RAW
        sha = "a" * 40
        requests = []

        def download(dest, checksum, urls):
            requests.append((checksum, urls))
            self.assertIsNone(checksum)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if urls == ["https://api.github.com/repos/1Z99/project-ai-r36s/commits/main"]:
                dest.write_text(json.dumps({"sha": sha}))
            else:
                immutable = core.RAW.rsplit("/", 1)[0] + "/" + sha + "/"
                self.assertTrue(urls[0].startswith(immutable), urls)
                dest.write_bytes((bundle / unquote(urls[0][len(immutable):])).read_bytes())
            return dest

        with mock.patch.object(self.worker, "_download", side_effect=download):
            for sha in ("a" * 40, "b" * 40):
                result = self.worker.run("update")
                self.assertEqual(result["outcome"], "up_to_date")
                self.assertEqual(result["source"], "GitHub " + sha[:7])
                self.assertEqual(result["revision"], sha)
                self.assertEqual(self.worker.raw, core.RAW)
            sha = "c" * 40
            (bundle / "app" / core.APP_FILES[0]).write_text("# changed remote source\n")
            result = self.worker.run("update")
            self.assertEqual(result["outcome"], "updated")
            self.assertEqual(result["revision"], sha)
            self.assertEqual(result["changes"], [str(self.worker.tools / core.APP_FILES[0])])
        self.assertEqual(self.worker.raw, core.RAW)
        self.assertEqual(sum("api.github.com" in urls[0] for _, urls in requests), 3)

    def test_github_check_failure_or_invalid_response_cannot_use_cached_success(self):
        self.bundle()
        self.worker.run("install")
        self.worker.source_dir = None
        self.worker.raw = core.RAW
        metadata = self.worker.cache / "github-main.json"
        for response in (RuntimeError("GitHub rate limit / network failure"), "not JSON", "{}",
                         '{"sha": "main"}', '{"sha": null}'):
            with self.subTest(response=response):
                metadata.write_text(json.dumps({"sha": "a" * 40}))
                self.events.clear()

                def download(dest, checksum, urls):
                    self.assertIsNone(checksum)
                    if isinstance(response, Exception):
                        raise response
                    dest.write_text(response)
                    return dest

                with mock.patch.object(self.worker, "_download", side_effect=download):
                    with self.assertRaisesRegex(RuntimeError, "Cannot verify GitHub main"):
                        self.worker.run("update")
                self.assertEqual(self.worker.raw, core.RAW)
                self.assertFalse(any(event["phase"] == "complete" for event in self.events))

    def test_up_to_date_check_does_not_require_fresh_install_space(self):
        self.bundle()
        self.worker.run("install")
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=25 * core.MIB)):
            self.assertEqual(self.worker.run("update")["outcome"], "up_to_date")

    def test_install_update_uninstall_preserves_user_and_manager(self):
        bundle = self.bundle()
        manager = self.card / "tools/227-ai-manager/manager.py"
        manager.parent.mkdir(parents=True)
        manager.write_text("manager unchanged")
        builtin = self.card / "tools/builtin-microphone.py"
        builtin.write_text("builtin unchanged")
        (self.card / "tools/mic_test.py").write_text("legacy")
        self.worker.sysdir.mkdir()
        (self.worker.sysdir / "MIC TEST.sh").write_text("legacy")
        self.worker.run("install")
        self.assertEqual(json.loads((self.worker.root / "config.json").read_text()), core.DEFAULT_CONFIG)
        self.assertIn(self.unrelated, self.es.read_text())
        self.assertFalse((self.card / "tools/mic_test.py").exists())
        self.assertFalse((self.worker.sysdir / "MIC TEST.sh").exists())
        games = ET.parse(str(self.worker.sysdir / "gamelist.xml")).getroot()
        self.assertEqual([game.findtext("path") for game in games], ["./227 AI.sh"])
        self.assertEqual([p.name for p in self.worker.sysdir.glob("*.sh")], ["227 AI.sh"])
        self.assertTrue(os.access(self.worker.sysdir / "227 AI.sh", os.X_OK))
        self.assertIn("<formatVersion>4</formatVersion>", (self.theme / "227/theme.xml").read_text())
        self.assertTrue((self.theme / "_art/posters/227.jpg").exists())
        user_files = dict(config="user settings", chats="private chats", history="private history")
        for name, data in user_files.items():
            (self.worker.root / (name + ".json")).write_text(data)
        mtimes = {str(p): p.stat().st_mtime_ns for folder in ("runtime", "whisper", "models")
                  for p in (self.worker.root / folder).rglob("*") if p.is_file() and p.suffix != ".txt"}
        (bundle / "app/project_ai_r36s.py").write_text("# UPDATED APP\n")
        (bundle / "assets/words.txt").write_text("updated words")
        (bundle / "system/227 AI.sh").write_text("#!/bin/sh\n# UPDATED LAUNCHER\n")
        # A verified installed runtime must not need even a local archive/sidecar.
        (bundle / core.RUNTIME).unlink()
        (bundle / (core.RUNTIME + ".sha256")).unlink()
        self.worker.run("update")
        self.assertIn("UPDATED APP", (self.worker.tools / "project_ai_r36s.py").read_text())
        self.assertIn("UPDATED LAUNCHER", (self.worker.sysdir / "227 AI.sh").read_text())
        self.assertEqual((self.worker.root / "words.txt").read_text(), "updated words")
        for name, data in user_files.items():
            self.assertEqual((self.worker.root / (name + ".json")).read_text(), data)
        self.assertEqual(mtimes, {p: Path(p).stat().st_mtime_ns for p in mtimes})
        # A stale shell-installer backup must never be restored.
        self.es.with_suffix(".cfg.pre-227ai").write_text("not an ES config")
        result = self.worker.run("uninstall")
        self.assertEqual(result["outcome"], "uninstalled")
        self.assertIn(str(self.worker.root), result["changes"])
        self.assertFalse(self.worker.root.exists())
        self.assertFalse(self.worker.sysdir.exists())
        self.assertEqual(manager.read_text(), "manager unchanged")
        self.assertEqual(builtin.read_text(), "builtin unchanged")
        self.assertIn(self.unrelated, self.es.read_text())
        self.assertNotIn("<name>227</name>", self.es.read_text())
        self.assertFalse((self.theme / "227").exists())
        self.assertFalse((self.theme / "_art/posters/227.jpg").exists())
        required = {"phase", "label", "detail", "done", "total", "speed", "elapsed", "eta", "overall"}
        for event in self.events:
            self.assertEqual(set(event), required)
            self.assertTrue(event["overall"] is None or 0 <= event["overall"] <= 1)
        self.assertTrue({"hash", "extract", "config", "es", "theme", "files", "complete"}.issubset(
            {event["phase"] for event in self.events}))

    def test_other_card_registration_and_theme_survive_uninstall(self):
        other = self.base / "second-card"
        (other / "tools").mkdir(parents=True)
        (other / "tools/project_ai_r36s.py").write_text("other app")
        (self.theme / "227").mkdir()
        (self.theme / "227/theme.xml").write_text("other theme")
        (self.theme / "_art/posters/227.jpg").write_text("other poster")
        self.es.write_text('<systemList>' + self.unrelated + '<system><name>227</name><path>'
                           + str(other) + '/227ai/</path></system></systemList>')
        original = self.es.read_text()
        with mock.patch.object(core, "CARD_PATHS", ((self.card, "first"), (other, "second"))):
            self.worker.run("uninstall")
        self.assertEqual(self.es.read_text(), original)
        self.assertEqual((self.theme / "227/theme.xml").read_text(), "other theme")
        self.assertTrue((other / "tools/project_ai_r36s.py").exists())

    def test_theme_kept_if_other_install_not_registered(self):
        other = self.base / "other"
        (other / "tools").mkdir(parents=True)
        (other / "tools/project_ai_r36s.py").write_text("other")
        (self.theme / "227").mkdir()
        (self.theme / "227/theme.xml").write_text("keep")
        with mock.patch.object(core, "CARD_PATHS", ((self.card, "one"), (other, "two"))):
            self.worker.run("uninstall")
        self.assertTrue((self.theme / "227/theme.xml").exists())

    def test_missing_local_asset_does_not_publish_or_register(self):
        bundle = self.bundle()
        self.worker.tools.mkdir()
        app = self.worker.tools / "project_ai_r36s.py"
        app.write_text("old app")
        (bundle / "assets/words.txt").unlink()
        original = self.es.read_text()
        with self.assertRaisesRegex(FileNotFoundError, "Local bundle file missing"):
            self.worker.run("update")
        self.assertEqual(app.read_text(), "old app")
        self.assertEqual(self.es.read_text(), original)
        self.assertFalse((self.worker.sysdir / "227 AI.sh").exists())
        self.assertFalse(any(self.worker.cache.glob("stage-*")))
        self.assertFalse(any(e["phase"] == "complete" for e in self.events))

    def test_model_failure_does_not_publish_any_app_files(self):
        self.bundle()
        original_download = self.worker._download

        def download(dest, sha, urls):
            if dest.name == core.MODELS[0][0]:
                raise RuntimeError("model mirror unavailable")
            return original_download(dest, sha, urls)

        with mock.patch.object(self.worker, "_download", side_effect=download):
            with self.assertRaisesRegex(RuntimeError, "model mirror unavailable"):
                self.worker.run("install")
        self.assertFalse((self.worker.tools / "project_ai_r36s.py").exists())
        self.assertFalse((self.worker.sysdir / "227 AI.sh").exists())
        self.assertNotIn("<name>227</name>", self.es.read_text())

    def test_bad_runtime_sidecar_is_not_trusted(self):
        bundle = self.bundle()
        (bundle / (core.RUNTIME + ".sha256")).write_text("0" * 64)
        with self.assertRaisesRegex(ValueError, "pinned SHA-256"):
            self.worker.run("install")
        self.assertFalse(self.worker.root.exists())

    def test_unsafe_archives_are_rejected(self):
        for index, (name, kind, link) in enumerate((
                ("../escape", tarfile.REGTYPE, ""),
                ("/absolute", tarfile.REGTYPE, ""),
                ("runtime/../../escape", tarfile.REGTYPE, ""),
                ("runtime/link", tarfile.SYMTYPE, "/tmp"),
                ("runtime/link", tarfile.LNKTYPE, "runtime/llama-server"),
                ("runtime/pipe", tarfile.FIFOTYPE, ""),
                ("unrelated/file", tarfile.REGTYPE, ""))):
            with self.subTest(name=name, kind=kind):
                archive = self.base / "unsafe.tar.gz"
                with tarfile.open(str(archive), "w:gz") as tar:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    item.linkname = link
                    tar.addfile(item)
                with self.assertRaisesRegex(ValueError, "Unsafe runtime archive"):
                    self.worker._extract_runtime(archive, self.base / ("extract-" + str(index)))
        self.assertFalse((self.base / "escape").exists())

    def test_cancel_before_start_has_no_side_effects(self):
        self.worker.cancel.set()
        with self.assertRaises(core.Cancelled):
            self.worker.run("install")
        self.assertEqual(list(self.card.iterdir()), [])
        self.assertEqual(self.events[-1]["phase"], "cancelled")

    def test_cancel_immediately_before_commit_keeps_existing_install(self):
        bundle = self.bundle()
        self.worker.run("install")
        app = self.worker.tools / core.APP_FILES[0]
        original = app.read_bytes()
        (bundle / "app" / app.name).write_text("# new app")
        original_es = self.es.read_bytes()
        original_themes = self.worker._themes

        def verify(*args, **kwargs):
            original_themes(*args, **kwargs)
            if kwargs.get("verify"):
                self.worker.cancel.set()

        with mock.patch.object(self.worker, "_themes", side_effect=verify):
            with self.assertRaises(core.Cancelled):
                self.worker.run("update")
        self.assertEqual(app.read_bytes(), original)
        self.assertEqual(self.es.read_bytes(), original_es)
        self.assertFalse(list(self.base.rglob(".227-backup-*")))

    def test_cancel_during_commit_finishes_runtime_app_and_registration(self):
        bundle = self.bundle()
        self.worker.root.mkdir(parents=True)
        (self.worker.root / "runtime").mkdir()
        (self.worker.root / "runtime/llama-server").write_text("old runtime")
        app = self.worker.tools / core.APP_FILES[0]
        app.write_text("# old app")
        (bundle / "app" / app.name).write_text("# new app")
        replace = self.worker._replace

        def cancel_after_runtime(source, target, **kwargs):
            replace(source, target, **kwargs)
            if target == self.worker.root / "runtime":
                self.worker.cancel.set()

        with mock.patch.object(self.worker, "_replace", side_effect=cancel_after_runtime):
            self.worker.run("update")
        self.assertEqual(self.events[-1]["phase"], "complete")
        self.assertEqual(app.read_text(), "# new app")
        self.assertEqual((self.worker.root / "runtime/llama-server").read_bytes(), b"llama")
        self.worker.cancel.clear()
        self.assertTrue(self.worker._runtime_valid())
        self.assertTrue((self.worker.sysdir / "227 AI.sh").is_file())
        self.assertIn("<name>227</name>", self.es.read_text())
        self.assertTrue(any(e["phase"] == "commit" and "Cancellation ignored" in e["label"]
                            for e in self.events))
        self.assertFalse(list(self.base.rglob(".227-backup-*")))

    def test_commit_error_restores_managed_files_models_es_and_themes(self):
        bundle = self.bundle()
        self.worker.run("install")
        for name in ("config.json", "history.json", "chats.json"):
            (self.worker.root / name).write_text("private " + name)
        (self.worker.root / "runtime/llama-server").write_text("old runtime")
        (self.worker.root / core.MODELS[0][3] / core.MODELS[0][0]).write_text("old model")
        (self.worker.tools / "mic_test.py").write_text("legacy mic")
        (self.worker.sysdir / "MIC TEST.sh").write_text("legacy launcher")
        (self.theme / "_art/posters/227.png").write_text("old png")
        (bundle / "app" / core.APP_FILES[0]).write_text("# new app")
        (bundle / "system/227-theme.xml").write_text('<theme><formatVersion>9</formatVersion></theme>')
        (bundle / "assets/227-art.jpg").write_text("new artwork")
        # Change the registration too, then fail after the final replacement.
        self.es.write_text(self.es.read_text().replace(str(self.worker.sysdir), str(self.card / "227")))
        paths = [self.worker.tools / name for name in core.APP_FILES + ("mic_test.py",)]
        paths += [p for folder in (self.worker.root, self.worker.sysdir, self.themes)
                  for p in folder.rglob("*") if p.is_file()]
        paths.append(self.es)
        before = {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in paths}
        write = self.worker._write_text

        def fail_after_es(target, *args):
            write(target, *args)
            if target == self.es:
                raise OSError("injected commit failure")

        with mock.patch.object(self.worker, "_write_text", side_effect=fail_after_es):
            with self.assertRaisesRegex(OSError, "injected commit failure"):
                self.worker.run("update")
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in paths})
        self.assertTrue((self.worker.cache / core.MODELS[0][0]).is_file())
        self.assertFalse(list(self.base.rglob(".227-backup-*")))
        self.assertFalse(list(self.worker.cache.glob("stage-*")))
        self.assertEqual(self.events[-1]["phase"], "error")

    def test_failed_fresh_commit_removes_published_files_and_new_directories(self):
        self.bundle()
        original = self.es.read_bytes()
        with mock.patch.object(self.worker, "_themes", side_effect=[None, OSError("theme write failed")]):
            with self.assertRaisesRegex(OSError, "theme write failed"):
                self.worker.run("install")
        self.assertEqual(self.es.read_bytes(), original)
        self.assertFalse(self.worker.tools.exists())
        self.assertFalse(self.worker.sysdir.exists())
        for name, _, _, _ in core.MODELS:
            self.assertTrue((self.worker.cache / name).is_file())

    def test_runtime_replace_error_rolls_back_earlier_runtime_directory(self):
        self.bundle()
        self.worker.run("install")
        executable = self.worker.root / "runtime/llama-server"
        executable.write_text("old executable")
        replace = core.os.replace

        def fail_whisper(source, target):
            if Path(source).name == "whisper" and Path(source).parent.name == "runtime-tree":
                raise OSError("whisper publication failed")
            return replace(source, target)

        with mock.patch.object(core.os, "replace", side_effect=fail_whisper):
            with self.assertRaisesRegex(OSError, "whisper publication failed"):
                self.worker.run("update")
        self.assertEqual(executable.read_text(), "old executable")
        self.assertEqual((self.worker.root / "whisper/whisper-cli").read_bytes(), b"whisper")
        self.assertFalse(list(self.base.rglob(".227-backup-*")))

    def test_failed_rollback_retains_backup_outside_deleted_stage(self):
        self.bundle()
        self.worker.run("install")
        executable = self.worker.root / "runtime/llama-server"
        executable.write_text("original executable")
        replace = core.os.replace

        def fail_restore(source, target):
            if ".227-backup-" in str(source) and Path(target) == self.worker.root / "runtime":
                raise OSError("restore failed")
            return replace(source, target)

        with mock.patch.object(core.os, "replace", side_effect=fail_restore), mock.patch.object(
                self.worker, "_themes", side_effect=[None, OSError("commit failed")]):
            with self.assertRaisesRegex(RuntimeError, "Rollback incomplete; backups retained") as caught:
                self.worker.run("update")
        backups = list(self.worker.root.glob(".227-backup-*/runtime/llama-server"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "original executable")
        self.assertIn(str(backups[0].parent), str(caught.exception))
        self.assertFalse(list(self.worker.cache.glob("stage-*")))

    def test_uninstall_cancel_before_deletion_does_not_change_install(self):
        self.bundle()
        self.worker.run("install")
        original = self.es.read_bytes()
        verify = self.worker._themes

        def cancel_before_delete(*args, **kwargs):
            verify(*args, **kwargs)
            if kwargs.get("verify"):
                self.worker.cancel.set()

        with mock.patch.object(self.worker, "_themes", side_effect=cancel_before_delete):
            with self.assertRaises(core.Cancelled):
                self.worker.run("uninstall")
        self.assertEqual(self.es.read_bytes(), original)
        self.assertTrue((self.worker.sysdir / "227 AI.sh").is_file())

    def test_uninstall_cancel_during_deletion_finishes_removal(self):
        self.bundle()
        self.worker.run("install")

        def emit(event):
            self.events.append(event)
            if event["label"] == "Removed " + core.APP_FILES[0]:
                self.worker.cancel.set()

        self.worker.emit = emit
        self.worker.run("uninstall")
        self.assertFalse(self.worker.root.exists())
        self.assertFalse(self.worker.sysdir.exists())
        self.assertFalse(self.worker.cache.exists())
        self.assertNotIn("<name>227</name>", self.es.read_text())
        self.assertFalse((self.theme / "227").exists())
        self.assertTrue(any(e["phase"] == "uninstall" and "Cancellation ignored" in e["label"]
                            for e in self.events))
        self.assertEqual(self.events[-1]["phase"], "complete")

    def test_uninstall_verifies_theme_paths_before_changing_es(self):
        self.bundle()
        self.worker.run("install")
        poster = self.theme / "_art/posters/227.jpg"
        poster.unlink()
        poster.symlink_to(self.es)
        original = self.es.read_bytes()
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.worker.run("uninstall")
        self.assertEqual(self.es.read_bytes(), original)
        self.assertTrue((self.worker.sysdir / "227 AI.sh").is_file())

    def test_uninstall_rejects_wrong_target_types_before_changing_es(self):
        self.bundle()
        self.worker.run("install")
        original = self.es.read_bytes()
        for path in (self.worker.tools / core.APP_FILES[0], self.theme / "_art/posters/227.jpg"):
            with self.subTest(path=path):
                data = path.read_bytes()
                path.unlink()
                path.mkdir()
                with self.assertRaises(ValueError):
                    self.worker.run("uninstall")
                self.assertEqual(self.es.read_bytes(), original)
                self.assertTrue((self.worker.sysdir / "227 AI.sh").is_file())
                path.rmdir()
                path.write_bytes(data)

    def test_fresh_space_preflight_rejects_before_staging_and_reports_ui_error(self):
        self.bundle()
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=649 * core.MIB)):
            with self.assertRaisesRegex(OSError, "Not enough free space.*650.0 MiB.*649.0 MiB"):
                self.worker.run("install")
        self.assertFalse(self.worker.cache.exists())
        self.assertFalse(self.worker.tools.exists())
        self.assertIn("Not enough free space", self.events[-1]["detail"])
        self.assertEqual(self.events[-1]["phase"], "error")

    def test_space_preflight_counts_only_largest_known_source_partial_per_destination(self):
        self.worker.cache.mkdir()
        expected_credit = 0
        for name, _, hf, _ in core.MODELS:
            dest = self.worker.cache / name
            urls = self.worker._model_urls(name, hf)
            for url, size in zip(urls, (10 * core.MIB, 20 * core.MIB)):
                with self.worker._part_path(dest, url).open("wb") as stream:
                    stream.truncate(size)
            with self.worker._part_path(dest, "https://unrelated.invalid/" + name).open("wb") as stream:
                stream.truncate(900 * core.MIB)
            expected_credit += min(core.MODEL_SIZES[name], 20 * core.MIB)
        with (self.worker.cache / "arbitrary-cache-file").open("wb") as stream:
            stream.truncate(900 * core.MIB)
        needed = 650 * core.MIB - expected_credit
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=needed)):
            self.worker._preflight()
        self.assertEqual(self.events[-1]["total"], needed)
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=needed - 1)):
            with self.assertRaisesRegex(OSError, "Not enough free space"):
                self.worker._preflight()

    def test_space_preflight_caps_oversized_partials_and_runtime_credit(self):
        self.worker.cache.mkdir()
        expected_credit = 29 * core.MIB
        destinations = [(self.worker.cache / core.RUNTIME, [self.worker.rel + "/" + core.RUNTIME])]
        for name, _, hf, _ in core.MODELS:
            destinations.append((self.worker.cache / name, self.worker._model_urls(name, hf)))
            expected_credit += core.MODEL_SIZES[name]
        for dest, urls in destinations:
            for url in urls:
                with self.worker._part_path(dest, url).open("wb") as stream:
                    stream.truncate(900 * core.MIB)
        needed = 650 * core.MIB - expected_credit
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=needed)):
            self.worker._preflight()
        self.assertEqual(self.events[-1]["total"], needed)

    def test_space_preflight_reuses_verified_runtime_models_and_cache_without_double_count(self):
        bundle = self.bundle()
        self.worker.run("install")
        model_credit = 0
        for name, _, _, folder in core.MODELS:
            shutil.copyfile(bundle / name, self.worker.cache / name)
            model_credit += (bundle / name).stat().st_size
        runtime_credit = min(29 * core.MIB, (bundle / core.RUNTIME).stat().st_size)
        needed = 25 * core.MIB
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=needed)):
            self.worker.run("update")
        space = [e for e in self.events if e["label"] == "Free space checked"][-1]
        self.assertEqual(space["total"], needed)
        # A corrupt installed runtime gets no extraction allowance, but its
        # verified cached archive is still reusable.
        (self.worker.root / "runtime/llama-server").write_text("corrupt")
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=650 * core.MIB)):
            self.worker._preflight()
        self.assertEqual(self.events[-1]["total"], 650 * core.MIB - runtime_credit - model_credit)

    def test_space_preflight_does_not_credit_unverified_complete_cache(self):
        self.worker.cache.mkdir()
        for name in (core.RUNTIME,) + tuple(model[0] for model in core.MODELS):
            (self.worker.cache / name).write_bytes(b"corrupt cache")
        with mock.patch.object(core.shutil, "disk_usage", return_value=mock.Mock(free=650 * core.MIB)):
            self.worker._preflight()
        self.assertEqual(self.events[-1]["total"], 650 * core.MIB)

    def test_lock_rejects_concurrent_mutation(self):
        import fcntl
        lock = (self.card / ".227-ai.lock").open("a+b")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(RuntimeError, "already modifying"):
            self.worker.run("uninstall")

    def test_invalid_actions_and_targets(self):
        with self.assertRaisesRegex(ValueError, "Unknown installer action"):
            self.worker.run("erase")
        for target in ("relative", "/", "/etc", "/etc/something", "/roms3", "/tmp", "/roms/not-a-card"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    core.Installer(target, es_cfg=self.es).validate_card()
        linked = self.base / "linked"
        linked.symlink_to(self.card, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            core.Installer(linked, es_cfg=self.es).validate_card()
        self.worker.tools.mkdir()
        self.worker.root.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.worker.run("uninstall")

    def test_nested_symlink_not_followed(self):
        self.worker.root.mkdir(parents=True)
        outside = self.base / "private"
        outside.write_text("keep")
        (self.worker.root / "config.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.worker.run("uninstall")
        self.assertEqual(outside.read_text(), "keep")

    def test_discover_mounts_both_cards_independently(self):
        first = self.base / "sd1"
        second = self.base / "sd2"
        first.mkdir()
        (second / "tools").mkdir(parents=True)
        (second / "tools/project_ai_r36s.py").write_text("installed")
        cards = ((first, "Main SD / SD1"), (second, "Second SD / SD2"))
        with mock.patch.object(core, "CARD_PATHS", cards), mock.patch.object(core, "_mounts", return_value={
                str(first): ["rw"], str(second): ["rw"]}):
            found = core.discover_cards()
        self.assertEqual([c["label"] for c in found], [c[1] for c in cards])
        self.assertEqual([c["available"] for c in found], [True, True])
        self.assertEqual([c["installed"] for c in found], [False, True])
        self.assertGreater(found[0]["total"], 0)
        with mock.patch.object(core, "CARD_PATHS", cards), mock.patch.object(core, "_mounts", return_value={str(first): ["ro"]}):
            found = core.discover_cards()
        self.assertIn("not writable", found[0]["reason"])
        self.assertIn("No SD card mounted", found[1]["reason"])
        self.assertFalse(any(c["available"] for c in found))

    def test_real_card_paths_require_mounts(self):
        with mock.patch.object(core, "_mounts", return_value={}):
            for path in ("/roms", "/roms2"):
                with self.assertRaisesRegex(ValueError, "No SD card mounted"):
                    core.Installer(path, es_cfg=self.es).validate_card()

    def test_mount_escape_parsing(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="/dev/mmc /roms ext4 rw,nosuid 0 0\n/dev/sd /media/my\\040card vfat ro 0 0\n")):
            self.assertEqual(core._mounts(), {"/roms": ["rw", "nosuid"], "/media/my card": ["ro"]})

    def test_es_comments_and_legacy_selected_card_path(self):
        comment = '<!-- <system><name>227</name><path>/old/comment/</path></system> -->'
        self.es.write_text('<systemList>' + comment + self.unrelated + '<system custom="keep">'
                           '<name>227</name><path>' + str(self.card) + '/227/</path></system></systemList>')
        text = self.worker._es_text(remove=True)
        self.assertIn(comment, text)
        self.assertIn(self.unrelated, text)
        self.assertEqual(len(ET.fromstring(text)), 1)
        text = self.worker._es_text()
        self.assertIn(comment, text)
        self.assertIn('custom="keep"', text)
        self.assertEqual([s.findtext("path") for s in ET.fromstring(text) if s.findtext("name") == "227"],
                         [str(self.worker.sysdir) + "/"])

    def test_es_uninstall_does_not_match_prefix_substring(self):
        self.es.write_text('<systemList><system><name>227</name><path>' + str(self.card)
                           + '-other/227ai/</path></system></systemList>')
        self.assertEqual(self.worker._es_text(remove=True), self.es.read_text())

    def test_es_empty_path_is_registered_and_missing_path_errors(self):
        self.es.write_text('<systemList><system><name>227</name><path /></system></systemList>')
        self.assertEqual(ET.fromstring(self.worker._es_text()).findtext("system/path"),
                         str(self.worker.sysdir) + "/")
        self.es.write_text('<systemList><system><name>227</name></system></systemList>')
        with self.assertRaisesRegex(ValueError, "has no path"):
            self.worker._es_text()

    def test_es_exact_name_and_selected_card_only(self):
        self.es.write_text('<systemList>' + self.unrelated + '<system><name>227</name><path>'
                           + str(self.worker.sysdir) + '/</path><custom>preserve</custom></system></systemList>')
        text = self.worker._es_text(remove=True)
        self.assertIn(self.unrelated, text)
        self.assertNotIn("<name>227</name>", text)
        self.es.write_text('<systemList>' + self.unrelated + '<system><name>227</name>'
                           '<path>/roms2/227ai/</path><custom>preserve</custom></system></systemList>')
        text = self.worker._es_text()
        self.assertIn("<custom>preserve</custom>", text)
        self.assertIn(self.unrelated, text)
        self.assertIn(str(self.worker.sysdir) + "/</path>", text)


@unittest.skipUnless(shutil.which("curl"), "curl is required for download integration tests")
class DownloadTest(BaseTest):
    def setUp(self):
        super().setUp()
        self.http = HTTPFixture()
        self.addCleanup(self.http.close)
        self.dest = self.card / "download.bin"
        self.sha = digest(self.http.data)

    def fetch(self, *modes):
        return self.worker._download(self.dest, self.sha, [self.http.url + "/" + mode for mode in modes])

    def test_resume_transport_interruption_same_source(self):
        self.fetch("once")
        self.assertEqual(self.dest.read_bytes(), self.http.data)
        self.assertEqual(self.http.requests, [("once", None), ("once", "bytes=65536-")])

    def test_unhashed_asset_resumes_after_transport_failure(self):
        self.worker._download(self.dest, None, [self.http.url + "/once"])
        self.assertEqual(self.dest.read_bytes(), self.http.data)
        self.assertEqual(self.http.requests, [("once", None), ("once", "bytes=65536-")])
        self.assertEqual(self.http.validators, [None, '"v1"'])

    def test_unhashed_partial_without_validator_restarts(self):
        self.worker._download(self.dest, None, [self.http.url + "/no-validator"])
        self.assertEqual(self.dest.read_bytes(), self.http.data)
        self.assertEqual(self.http.requests, [("no-validator", None), ("no-validator", None)])

    def test_unhashed_partial_http503_loses_etag_then_changed_payload_never_combines(self):
        url = self.http.url + "/changed"
        with self.assertRaisesRegex(RuntimeError, "503"):
            self.worker._download(self.dest, None, [url])
        part = self.worker._part_path(self.dest, url)
        self.assertEqual(part.stat().st_size, 65536)
        self.assertEqual(self.worker._response(part.with_suffix(".headers"))[0], 503)
        self.worker._download(self.dest, None, [url])
        self.assertEqual(self.dest.read_bytes(), b"new payload!" * (len(self.http.data) // 12))
        self.assertEqual(self.http.requests, [("changed", None), ("changed", "bytes=65536-"), ("changed", None)])
        self.assertEqual(self.http.validators, [None, '"v1"', None])

    def test_partial_survives_all_failures_and_next_invocation(self):
        with self.assertRaisesRegex(RuntimeError, "curl 18"):
            self.fetch("drop")
        url = self.http.url + "/drop"
        part = self.worker._part_path(self.dest, url)
        self.assertEqual(part.stat().st_size, 131072)
        self.http.counts["drop"] = 0
        with self.assertRaises(RuntimeError):
            self.fetch("drop")
        self.assertEqual(self.http.requests[2], ("drop", "bytes=131072-"))
        self.assertEqual(part.stat().st_size, 262144)

    def test_mirror_never_reuses_primary_partial(self):
        self.fetch("drop", "good")
        self.assertEqual(self.dest.read_bytes(), self.http.data)
        self.assertEqual(self.http.requests[-1], ("good", None))
        self.assertTrue(self.worker._part_path(self.dest, self.http.url + "/drop").exists())

    def test_http_error_and_log_are_surfaced(self):
        with self.assertRaisesRegex(RuntimeError, "503") as caught:
            self.fetch("error", "missing")
        self.assertIn("404", str(caught.exception))
        self.assertIn("log:", str(caught.exception))
        self.assertTrue(list(self.card.glob("*.curl.log")))
        self.assertTrue(any("503" in event["detail"] for event in self.events if event["phase"] == "error"))
        self.assertFalse(self.dest.exists())

    def test_checksum_failure_does_not_replace_existing_file(self):
        self.dest.write_bytes(b"old installed file")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            self.fetch("bad")
        self.assertEqual(self.dest.read_bytes(), b"old installed file")
        self.assertFalse(self.worker._part_path(self.dest, self.http.url + "/bad").exists())

    def test_checksum_failure_tries_mirror(self):
        self.fetch("bad", "good")
        self.assertEqual(self.dest.read_bytes(), self.http.data)

    def test_range_ignored_restarts_only_that_source(self):
        part = self.worker._part_path(self.dest, self.http.url + "/ignore")
        part.write_bytes(self.http.data[:65536])
        self.fetch("ignore")
        self.assertEqual(self.http.requests, [("ignore", "bytes=65536-"), ("ignore", None)])
        self.assertEqual(self.dest.read_bytes(), self.http.data)

    def test_ignored_range_after_transport_failure_still_restarts(self):
        self.fetch("drop-ignore")
        self.assertEqual(self.http.requests, [("drop-ignore", None),
                                              ("drop-ignore", "bytes=65536-"),
                                              ("drop-ignore", None)])
        self.assertEqual(self.dest.read_bytes(), self.http.data)

    def test_http416_restarts_unverified_complete_part(self):
        part = self.worker._part_path(self.dest, self.http.url + "/good")
        part.write_bytes(self.http.data)
        part.with_suffix(".headers").write_text('HTTP/1.1 200 OK\nETag: "v1"\n')
        self.worker._download(self.dest, None, [self.http.url + "/good"])
        self.assertEqual(self.http.requests, [("good", "bytes={}-".format(len(self.http.data))), ("good", None)])
        self.assertEqual(self.dest.read_bytes(), self.http.data)

    def test_http416_restarts_corrupt_oversized_part(self):
        part = self.worker._part_path(self.dest, self.http.url + "/good")
        part.write_bytes(self.http.data + b"corruption")
        self.fetch("good")
        self.assertEqual(len(self.http.requests), 2)
        self.assertEqual(self.dest.read_bytes(), self.http.data)

    def test_complete_part_promoted_without_network(self):
        part = self.worker._part_path(self.dest, self.http.url + "/good")
        part.write_bytes(self.http.data)
        self.fetch("good")
        self.assertEqual(self.http.requests, [])
        self.assertFalse(part.exists())

    def test_progress_uses_final_response_length_and_growth(self):
        self.fetch("redirect")
        events = [e for e in self.events if e["phase"] == "download" and e["total"] is not None]
        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(all(e["total"] == len(self.http.data) for e in events))
        self.assertTrue(any(0 < e["done"] < len(self.http.data) and e["speed"] > 0 and e["eta"] > 0 for e in events))
        self.assertTrue(all(e["elapsed"] >= 0 for e in events))

    def test_range_progress_uses_content_range_total(self):
        part = self.worker._part_path(self.dest, self.http.url + "/slow")
        part.write_bytes(self.http.data[:65536])
        self.fetch("slow")
        events = [e for e in self.events if e["phase"] == "download" and e["total"] is not None]
        self.assertTrue(events)
        self.assertTrue(all(e["total"] == len(self.http.data) for e in events))
        self.assertTrue(all(e["done"] >= 65536 for e in events))

    def test_unknown_length_progress_is_indeterminate(self):
        self.fetch("unknown")
        events = [e for e in self.events if e["phase"] == "download"]
        self.assertTrue(events)
        self.assertTrue(all(e["total"] is None and e["eta"] is None for e in events))

    def test_cancel_terminates_curl_and_retains_partial(self):
        def emit(event):
            self.events.append(event)
            if event["phase"] == "download" and event["done"] > 0:
                self.worker.cancel.set()

        self.worker.emit = emit
        started = time.monotonic()
        with self.assertRaises(core.Cancelled):
            self.fetch("slow")
        self.assertLess(time.monotonic() - started, 3)
        part = self.worker._part_path(self.dest, self.http.url + "/slow")
        self.assertTrue(part.exists())
        self.assertGreater(part.stat().st_size, 0)
        self.assertFalse(self.dest.exists())
        self.worker.cancel.clear()
        self.worker.emit = self.events.append
        self.fetch("slow")
        self.assertEqual(self.dest.read_bytes(), self.http.data)

    def test_timeouts_and_returncode_without_exit(self):
        # Verify the actual curl invocation, not just a timer in Python.
        original = core.subprocess.Popen
        with mock.patch.object(core.subprocess, "Popen", wraps=original) as popen:
            self.fetch("good")
        command = popen.call_args[0][0]
        self.assertIn("--connect-timeout", command)
        self.assertIn("--speed-time", command)
        self.assertIn("--speed-limit", command)
        self.assertIn("--max-time", command)


if __name__ == "__main__":
    unittest.main()
