"""Worker-thread backend. The caller confirms uninstall and marshals events to its UI.

No manager/bootstrap files are owned here. Errors (including cancellation) escape
run(); callers should retain the event log and offer retry rather than exit.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote
from xml.sax.saxutils import escape


RAW = "https://raw.githubusercontent.com/1Z99/project-ai-r36s/main"
REL = "https://github.com/1Z99/project-ai-r36s/releases/latest/download"
RUNTIME = "project-ai-r36s-runtime-arm64.tar.gz"
RUNTIME_SHA = "4ad9f9a9aa29d05370982e3b7bdf53b4b9879893a41a4ffa9e7fd72b39a8c1b2"
MODELS = (
    ("LFM2.5-350M-QAD-Q4_0.gguf",
     "3d10b6ab8fc91a919534b9558e266255aca0bbc7f6d015963599aa9e74e05b1d",
     "https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF/resolve/main/", "models"),
    ("ggml-tiny.en-q5_1.bin",
     "c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b",
     "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/", "models/whisper"),
)
MODEL_SIZES = {"LFM2.5-350M-QAD-Q4_0.gguf": 219312832,
               "ggml-tiny.en-q5_1.bin": 32166155}
MIB = 1024 * 1024
APP_FILES = ("project_ai_r36s.py", "project_ai_settings.py")
ASSET_FILES = ("logo.rgba", "intro-frames.zip", "intro-background.rgba",
               "words.txt", "227-art.jpg")
LICENSE_FILES = ("llama.cpp-MIT.txt", "whisper.cpp-MIT.txt",
                 "LFM2.5-Open-License.txt", "DejaVu-fonts-copyright", "espeak-ng-NOTICE.txt")
SYSTEM_FILES = ("227 AI.sh", "gamelist.xml", "227-theme.xml")
CARD_PATHS = ((Path("/roms"), "Main SD / SD1"), (Path("/roms2"), "Second SD / SD2"))
DEFAULT_CONFIG = dict(model="fast", context=768, max_tokens=160, temperature=0.25,
                      highlight=True, speech_rate=1.0, background=20, intro=True)


class Cancelled(Exception):
    """The worker stopped at the caller's request; cached downloads are retained."""


def _no_symlinks(path):
    path = Path(path)
    for part in (path,) + tuple(path.parents):
        if part.is_symlink():
            raise ValueError("Refusing symlink target: {}".format(part))


def _mounts():
    mounts = {}
    with open("/proc/mounts", encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) >= 4:
                name = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[1])
                mounts[name] = fields[3].split(",")
    return mounts


def _card_info(path, label, mounts):
    info = dict(path=str(path), label=label, available=False, free=0, total=0,
                installed=False, reason="")
    try:
        _no_symlinks(path)
        if str(path) not in mounts:
            raise ValueError("No SD card mounted at {}".format(path))
        if not path.is_dir():
            raise ValueError("Card directory is missing: {}".format(path))
        info["installed"] = (path / "tools/project_ai_r36s.py").is_file()
        usage = shutil.disk_usage(path)
        info.update(free=usage.free, total=usage.total)
        if "ro" in mounts[str(path)] or not os.access(path, os.W_OK | os.X_OK):
            raise ValueError("Card is not writable: {}".format(path))
        info["available"] = True
    except (OSError, ValueError) as error:
        info["reason"] = str(error)
    return info


def discover_cards():
    """Describe both fixed card mountpoints, independently of ES ROM layout."""
    mounts = _mounts()
    return [_card_info(path, label, mounts) for path, label in CARD_PATHS]


class Installer:
    def __init__(self, prefix, source_dir=None,
                 es_cfg="/etc/emulationstation/es_systems.cfg", themes=None,
                 emit=None, cancel=None):
        self.prefix = Path(prefix)
        self.source_dir = Path(source_dir).absolute() if source_dir is not None else None
        self.es_cfg = Path(es_cfg)
        self.themes = Path(themes) if themes is not None else self._detect_themes()
        self.emit = emit
        self.cancel = cancel if cancel is not None else threading.Event()
        self._event_lock = threading.Lock()
        self._started = time.monotonic()
        self.tools = self.prefix / "tools"
        self.root = self.tools / "project_ai"
        self.sysdir = self.prefix / "227ai"
        self.cache = self.prefix / ".227-ai-cache"
        self.raw = os.environ.get("PROJECT_AI_RAW_BASE", RAW).rstrip("/")
        self.rel = os.environ.get("PROJECT_AI_REL_BASE", REL).rstrip("/")
        self._finalizing = None
        self._cancel_reported = False
        self._journal = None
        self._created_dirs = []

    discover_cards = staticmethod(discover_cards)

    def _detect_themes(self):
        for candidate in (Path("/etc/emulationstation/themes").resolve(),
                          Path("/roms/themes"), Path("/roms2/themes"), self.prefix / "themes"):
            if candidate.is_dir():
                return candidate
        return None

    def _check(self):
        if self.cancel.is_set():
            # Publication/deletion cannot safely stop halfway. Keep checks in
            # helpers, but report the real phase instead of raising here.
            if self._finalizing is not None:
                if not self._cancel_reported:
                    self._cancel_reported = True
                    self._event(self._finalizing, "Cancellation ignored; finalizing safely",
                                "This operation must finish before it can stop")
                return
            raise Cancelled("Installation cancelled; partial downloads retained in {}".format(self.cache))

    def _event(self, phase, label, detail="", done=0, total=None, speed=None,
               elapsed=None, eta=None, overall=None):
        if self.emit is not None:
            if self._finalizing is not None and self.cancel.is_set() and phase not in ("error", "complete"):
                phase = self._finalizing
                if not label.startswith("Cancellation ignored"):
                    label = "Cancellation ignored; finalizing: " + label
            event = dict(phase=phase, label=label, detail=str(detail), done=done,
                         total=total, speed=speed,
                         elapsed=time.monotonic() - self._started if elapsed is None else elapsed,
                         eta=eta, overall=overall)
            with self._event_lock:
                self.emit(event)

    def validate_card(self):
        """Validate before writing. Non-system absolute directories support offline tests."""
        path = self.prefix
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Card prefix must be an absolute path without '..'")
        _no_symlinks(path)
        if path in (Path("/roms"), Path("/roms2")):
            info = next(card for card in discover_cards() if card["path"] == str(path))
            if not info["available"]:
                raise ValueError(info["reason"])
        else:
            if len(path.parts) < 3 or path.parts[1] in (
                    "etc", "usr", "bin", "sbin", "lib", "lib64", "boot", "dev",
                    "proc", "sys", "run", "var", "roms", "roms2"):
                raise ValueError("Unknown or dangerous card target: {}".format(path))
            if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
                raise ValueError("Card directory must exist and be writable: {}".format(path))
        for target in (self.tools, self.root, self.sysdir, self.cache,
                       self.tools / "mic_test.py") + tuple(self.tools / name for name in APP_FILES):
            _no_symlinks(target)
            if target.exists():
                directory = target in (self.tools, self.root, self.sysdir, self.cache)
                if not (target.is_dir() if directory else target.is_file()):
                    raise ValueError("Unexpected managed target type: {}".format(target))
        # Refuse linked managed contents, even on uninstall; never follow them.
        for target in (self.root, self.sysdir, self.cache):
            if target.is_dir():
                for directory, dirs, files in os.walk(str(target), followlinks=False):
                    for name in dirs + files:
                        if (Path(directory) / name).is_symlink():
                            raise ValueError("Refusing symlink in managed tree: {}".format(Path(directory) / name))
        _no_symlinks(self.es_cfg)
        if not self.es_cfg.is_file():
            raise ValueError("EmulationStation config is missing: {}".format(self.es_cfg))
        if self.themes is not None:
            _no_symlinks(self.themes)
        return path

    def _hash(self, path):
        self._check()
        digest = hashlib.sha256()
        total = path.stat().st_size
        done = 0
        last = 0.0
        with path.open("rb") as stream:
            while True:
                self._check()
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                done += len(chunk)
                if time.monotonic() - last >= .2:
                    self._event("hash", "Verifying " + path.name, str(path), done, total)
                    last = time.monotonic()
        self._event("hash", "Hashed " + path.name, str(path), done, total)
        return digest.hexdigest()

    def _copy(self, source, target, phase="files"):
        self._check()
        _no_symlinks(target)
        self._mkdir(target.parent)
        fd, temporary = tempfile.mkstemp(prefix=".227-", dir=str(target.parent))
        temporary = Path(temporary)
        try:
            total = source.stat().st_size
            done = 0
            last = 0.0
            with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
                while True:
                    self._check()
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last >= .2:
                        self._event(phase, "Writing " + target.name, str(target), done, total)
                        last = time.monotonic()
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o755 if target.suffix == ".sh" else 0o644)
            self._replace(temporary, target)
            self._event(phase, "Wrote " + target.name, str(target), done, total)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _response(headers):
        """Use the last HTTP response, never a redirect's Content-Length."""
        status, fields = None, {}
        if headers.exists():
            for line in headers.read_text(encoding="iso-8859-1").splitlines():
                if line.startswith("HTTP/"):
                    fields = {}
                    status = int(line.split()[1])
                elif ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.lower()] = value.strip()
        return status, fields

    @staticmethod
    def _part_path(dest, url):
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return dest.with_name(dest.name + "." + key + ".part")

    def _download(self, dest, sha, urls):
        """Two bounded attempts per source, source-isolated persistent partials.

        Known hashes guard resumed content across upstream revisions. For raw
        assets, use the server's validator with If-Range when one is available.
        """
        self._check()
        _no_symlinks(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if sha and dest.is_file() and self._hash(dest) == sha:
            return dest
        errors = []
        for url in urls:
            part = self._part_path(dest, url)
            headers = part.with_suffix(".headers")
            log = part.with_suffix(".curl.log")
            for target in (part, headers, log):
                _no_symlinks(target)
            attempt, restarted = 0, False
            while attempt < 2:
                attempt += 1
                self._check()
                # A fully downloaded part may survive cancellation before rename.
                if sha and part.exists() and self._hash(part) == sha:
                    os.replace(str(part), str(dest))
                    return dest
                offset = part.stat().st_size if part.exists() else 0
                previous_status, previous_headers = self._response(headers)
                validator = None
                if previous_status in (200, 206):
                    etag = previous_headers.get("etag", "")
                    validator = (etag if etag and not etag.startswith("W/") else
                                 previous_headers.get("last-modified"))
                if offset and not sha and not validator:
                    # Failed HTTP responses may have replaced the old headers.
                    # Without a validator, a raw partial may belong to any version.
                    part.unlink()
                    offset = 0
                    self._event("download", "No resume validator; restarting source", url)
                headers.unlink(missing_ok=True)
                command = ["curl", "--fail", "--location", "--silent", "--show-error",
                           "--connect-timeout", "15", "--speed-time", "30",
                           "--speed-limit", "1024", "--max-time", "3600",
                           "--header", "Cache-Control: no-cache", "--dump-header", str(headers),
                           "--output", str(part)]
                if offset:
                    command += ["--continue-at", str(offset)]
                    if validator and not sha:
                        command += ["--header", "If-Range: " + validator]
                command += [url]
                started = time.monotonic()
                previous, previous_at = offset, started
                with log.open("ab") as output:
                    output.write(("\nAttempt {}: {}\n".format(attempt, url)).encode("utf-8"))
                    output.flush()
                    process = subprocess.Popen(command, stdout=output, stderr=output)
                    try:
                        while True:
                            self._check()
                            now = time.monotonic()
                            done = part.stat().st_size if part.exists() else 0
                            status, fields = self._response(headers)
                            total = None
                            content_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", fields.get("content-range", ""))
                            if status == 206 and content_range:
                                total = int(content_range[3])
                            elif status == 200 and fields.get("content-length", "").isdigit():
                                total = int(fields["content-length"])
                            speed = max(0, done - previous) / max(.001, now - previous_at)
                            self._event("download", "Downloading " + dest.name, url, done, total,
                                        speed, now - started,
                                        max(0, total - done) / speed if total is not None and speed else None)
                            previous, previous_at = done, now
                            if process.poll() is not None:
                                break
                            self.cancel.wait(.2)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                code = process.returncode
                status, fields = self._response(headers)
                if offset and (code == 33 or status == 416):
                    # Ignored/out-of-bounds Range, not an ordinary transport loss.
                    # Allow one restart without consuming a transport retry.
                    part.unlink(missing_ok=True)
                    errors.append("Server refused Range for " + url)
                    self._event("download", "Server cannot resume; restarting source", url)
                    if not restarted:
                        attempt -= 1
                        restarted = True
                    continue
                if not code and status is not None and status >= 400:
                    code = 22
                if code:
                    detail = "curl {} (HTTP {}) for {}: {} (log: {})".format(
                        code, status, url, log.read_text(encoding="utf-8", errors="replace")[-4096:], log)
                    errors.append(detail)
                    self._event("error", "Download source failed", detail)
                    continue
                if not part.exists() or not part.stat().st_size:
                    errors.append("Empty download from " + url)
                    continue
                if sha and self._hash(part) != sha:
                    part.unlink()
                    detail = "SHA-256 checksum mismatch for {} from {}".format(dest.name, url)
                    errors.append(detail)
                    self._event("error", "Checksum mismatch", detail)
                    continue
                os.replace(str(part), str(dest))
                return dest
        raise RuntimeError("Could not download {}. Partial files retained in {}.\n{}".format(
            dest.name, dest.parent, "\n".join(errors)))

    def _stage_files(self, stage):
        for folder, names in (("app", APP_FILES), ("assets", ASSET_FILES),
                              ("licenses", LICENSE_FILES), ("system", SYSTEM_FILES)):
            for name in names:
                target = stage / folder / name
                if self.source_dir is not None:
                    source = self.source_dir / folder / name
                    if not source.is_file():
                        raise FileNotFoundError("Local bundle file missing: {}".format(source))
                    self._copy(source, target)
                else:
                    cached = self.cache / "bundle" / folder / name
                    self._download(cached, None, [self.raw + "/" + folder + "/" + quote(name)])
                    self._copy(cached, target)
                if not target.stat().st_size:
                    raise ValueError("Empty bundle file: {}".format(target))
        for name in APP_FILES:
            compile((stage / "app" / name).read_bytes(), name, "exec")
        # The old release has a second microphone launcher. Publish only 227 AI.
        gamelist = stage / "system/gamelist.xml"
        tree = ET.parse(str(gamelist))
        for game in list(tree.getroot()):
            if game.tag != "game" or game.findtext("path") != "./227 AI.sh":
                tree.getroot().remove(game)
        if len(tree.getroot()) != 1:
            raise ValueError("Bundle must contain exactly one 227 AI gamelist entry")
        tree.write(str(gamelist), encoding="utf-8", xml_declaration=True)
        ET.parse(str(stage / "system/227-theme.xml"))

    def _runtime_valid(self):
        marker = self.root / ".installer-runtime.json"
        if not marker.is_file():
            return False
        try:
            manifest = json.loads(marker.read_text(encoding="utf-8"))
            files = manifest["files"]
            if manifest["archive_sha256"] != RUNTIME_SHA or not files:
                return False
            if not {"runtime/llama-server", "whisper/whisper-cli"}.issubset(files):
                return False
            for name, sha in files.items():
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or path.parts[0] not in ("runtime", "whisper"):
                    return False
                target = self.root / name
                if not target.is_file() or self._hash(target) != sha:
                    return False
            return all(os.access(self.root / name, os.X_OK)
                       for name in ("runtime/llama-server", "whisper/whisper-cli"))
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return False

    def _stage_runtime(self, stage, verified=None):
        if verified is None:
            verified = self._runtime_valid()
        if verified:
            self._event("runtime", "Keeping verified runtime")
            return False
        local = os.environ.get("PROJECT_AI_RUNTIME_LOCAL")
        if not local and self.source_dir is not None and (self.source_dir / RUNTIME).is_file():
            local = str(self.source_dir / RUNTIME)
        checksum = self.cache / (RUNTIME + ".sha256")
        if local:
            self._copy(Path(local + ".sha256"), checksum)
            urls = [Path(local).absolute().as_uri()]
        else:
            self._download(checksum, None, [self.rel + "/" + RUNTIME + ".sha256"])
            urls = [self.rel + "/" + RUNTIME]
        tokens = checksum.read_text(encoding="ascii").split()
        if not tokens or tokens[0].lower() != RUNTIME_SHA:
            raise ValueError("Runtime sidecar does not match pinned SHA-256 " + RUNTIME_SHA)
        archive = self._download(self.cache / RUNTIME, RUNTIME_SHA, urls)
        self._extract_runtime(archive, stage / "runtime-tree")
        return True

    def _extract_runtime(self, archive, target):
        target.mkdir(parents=True, exist_ok=True)
        hashes = {}
        with tarfile.open(str(archive), "r:gz") as tar:
            members = tar.getmembers()
            seen = set()
            for member in members:
                self._check()
                path = PurePosixPath(member.name)
                if (path.is_absolute() or ".." in path.parts or not path.parts
                        or path.parts[0] not in ("runtime", "whisper")
                        or not (member.isdir() or member.isfile()) or str(path) in seen):
                    raise ValueError("Unsafe runtime archive member: {}".format(member.name))
                seen.add(str(path))
            total = sum(member.size for member in members if member.isfile())
            done, last = 0, 0.0
            for member in members:
                self._check()
                dest = target / member.name
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with tar.extractfile(member) as source, dest.open("wb") as output:
                    while True:
                        self._check()
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if time.monotonic() - last >= .2:
                            self._event("extract", "Extracting runtime", member.name, done, total)
                            last = time.monotonic()
                dest.chmod(0o755 if member.mode & 0o111 else 0o644)
                hashes[member.name] = digest.hexdigest()
        for name in ("runtime/llama-server", "whisper/whisper-cli"):
            if not (target / name).is_file():
                raise ValueError("Runtime archive is missing " + name)
            (target / name).chmod(0o755)
        (target / ".installer-runtime.json").write_text(
            json.dumps(dict(archive_sha256=RUNTIME_SHA, files=hashes)), encoding="utf-8")
        self._event("extract", "Runtime extracted", str(target), done, total)

    def _es_text(self, remove=False):
        text = self.es_cfg.read_text(encoding="utf-8")
        root = ET.fromstring(text)
        if root.tag != "systemList":
            raise ValueError("Invalid EmulationStation systemList: {}".format(self.es_cfg))
        # Edit only exact system blocks, leaving unrelated text/comments intact.
        found = False
        def replace(match):
            nonlocal found
            block = match[0]
            if block.startswith("<!--") or block.startswith("<![CDATA["):
                return block
            system = ET.fromstring(block)
            if (system.findtext("name") or "").strip() != "227":
                return block
            found = True
            current = (system.findtext("path") or "").strip().rstrip("/")
            if remove:
                current_path = Path(os.path.normpath(current))
                belongs = current_path == self.prefix or self.prefix in current_path.parents
                return "" if current_path.is_absolute() and belongs else block
            if system.find("path") is None:
                raise ValueError("Existing ES system 227 has no path")
            block, count = re.subn(r"<path\s*/>|<path>[^<]*</path>",
                                  lambda m: "<path>" + escape(str(self.sysdir) + "/") + "</path>",
                                  block, count=1)
            if count != 1:
                raise ValueError("Cannot safely edit existing ES system 227 path")
            return block
        text = re.sub(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<system\b[^>]*>.*?</system\s*>",
                      replace, text, flags=re.S)
        if not remove and not found:
            block = ("\t<system>\n\t\t<name>227</name>\n\t\t<fullname>227</fullname>\n"
                     "\t\t<path>{}/</path>\n\t\t<extension>.sh .SH</extension>\n"
                     "\t\t<command>%ROM%</command>\n\t\t<platform>227</platform>\n"
                     "\t\t<theme>227</theme>\n\t\t<manufacturer>1Z99</manufacturer>\n"
                     "\t\t<release>2026</release>\n\t</system>\n").format(escape(str(self.sysdir)))
            index = text.rfind("</systemList>")
            if index < 0:
                raise ValueError("Missing </systemList> in {}".format(self.es_cfg))
            text = text[:index] + block + text[index:]
        ET.fromstring(text)
        return text

    def _write_text(self, target, text, phase):
        _no_symlinks(target)
        self._mkdir(target.parent)
        fd, name = tempfile.mkstemp(prefix=".227-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(name, target.stat().st_mode & 0o777 if target.exists() else 0o644)
            self._check()
            self._replace(Path(name), target)
            self._event(phase, "Updated " + target.name, str(target), 1, 1)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _themes(self, remove=False, verify=False):
        self._event("theme", "Verifying theme targets" if verify else
                    "Removing theme artwork" if remove else "Installing theme artwork")
        if self.themes is None or not self.themes.is_dir():
            return
        if remove and not verify:
            # Also honor another registration, even if its card is currently absent.
            systems = ET.parse(str(self.es_cfg)).getroot()
            if any((s.findtext("name") or "").strip() == "227" for s in systems):
                return
            for path, _ in CARD_PATHS:
                if path != self.prefix and (path / "tools/project_ai_r36s.py").is_file():
                    return
        for theme in sorted(self.themes.iterdir()):
            self._check()
            if theme.is_symlink() or not theme.is_dir() or not (theme / "theme.xml").is_file():
                continue
            target = theme / "227"
            poster = theme / "_art/posters/227.jpg"
            _no_symlinks(target / "theme.xml")
            _no_symlinks(poster)
            _no_symlinks(poster.with_suffix(".png"))
            if target.exists() and not target.is_dir():
                raise ValueError("Expected theme directory: {}".format(target))
            for managed in (target / "theme.xml", poster, poster.with_suffix(".png")):
                if managed.exists() and not managed.is_file():
                    raise ValueError("Expected theme file: {}".format(managed))
            if verify:
                continue
            if remove:
                (target / "theme.xml").unlink(missing_ok=True)
                if target.is_dir() and not any(target.iterdir()):
                    target.rmdir()
                poster.unlink(missing_ok=True)
                poster.with_suffix(".png").unlink(missing_ok=True)
                continue
            template = (self.root / "227-theme.xml").read_text(encoding="utf-8")
            # Stock ES themes may put comments before an XML declaration; ES
            # accepts these although ElementTree does not. Never rewrite them.
            metadata = (theme / "theme.xml").read_text(encoding="utf-8", errors="replace")
            metadata = re.sub(r"<!--.*?-->", "", metadata, flags=re.S)
            match = re.search(r"<formatVersion>\s*(\d+)\s*</formatVersion>", metadata)
            version = match[1] if match else "7"
            template = re.sub(r"<formatVersion>\d+</formatVersion>",
                              lambda m: "<formatVersion>" + escape(version) + "</formatVersion>", template)
            self._write_text(target / "theme.xml", template, "theme")
            if poster.parent.is_dir():
                self._copy(self.root / "227-art.jpg", poster, "theme")
                self._remove(poster.with_suffix(".png"))

    def _mkdir(self, path):
        if self._journal is not None and not path.exists():
            self._mkdir(path.parent)
            path.mkdir()
            self._created_dirs.append(path)
        else:
            path.mkdir(parents=True, exist_ok=True)

    def _backup(self, target, source=None):
        if self._journal is None:
            return
        _no_symlinks(target)
        backup = None
        if target.exists():
            # Renames keep large runtime/model backups cheap and on the target's
            # filesystem, including ES/themes outside the selected card.
            directory = Path(tempfile.mkdtemp(prefix=".227-backup-", dir=str(target.parent)))
            backup = directory / target.name
            try:
                os.replace(str(target), str(backup))
            except Exception:
                directory.rmdir()
                raise
        self._journal.append((target, backup, source))

    def _replace(self, source, target, retain_source=False):
        self._mkdir(target.parent)
        self._backup(target, source if retain_source else None)
        os.replace(str(source), str(target))

    def _remove(self, target):
        self._backup(target)
        target.unlink(missing_ok=True)

    def _end_transaction(self, rollback=False):
        errors = []
        for target, backup, source in reversed(self._journal):
            try:
                if rollback:
                    if target.exists():
                        if source is not None:
                            os.replace(str(target), str(source))
                        elif target.is_dir():
                            shutil.rmtree(str(target))
                        else:
                            target.unlink()
                    if backup is not None:
                        os.replace(str(backup), str(target))
                if backup is not None:
                    if backup.exists():
                        if backup.is_dir():
                            shutil.rmtree(str(backup))
                        else:
                            backup.unlink()
                    backup.parent.rmdir()
            except Exception as error:
                # Never clean up a backup whose restoration failed.
                errors.append("{} (backup: {}): {}".format(target, backup, error))
        if rollback:
            for directory in reversed(self._created_dirs):
                try:
                    if directory.is_dir() and not any(directory.iterdir()):
                        directory.rmdir()
                except OSError as error:
                    errors.append("{}: {}".format(directory, error))
        self._journal = None
        self._created_dirs = []
        if errors:
            raise RuntimeError(("Rollback incomplete; backups retained: " if rollback else
                                "Backup cleanup failed: ") + "\n".join(errors))

    def _model_urls(self, name, hf):
        urls = [self.rel + "/" + name, hf + name]
        if self.source_dir is not None and (self.source_dir / name).is_file():
            urls.insert(0, (self.source_dir / name).as_uri())
        return urls

    def _preflight(self):
        """Budget additional free space, not space already occupied by reuse."""
        self._event("space", "Checking free space and reusable downloads")
        runtime = self._runtime_valid()
        installed = set()
        allowance = 75 * MIB if runtime else 0
        downloads = [(self.cache / RUNTIME, RUNTIME_SHA, 29 * MIB,
                      [self.rel + "/" + RUNTIME], 29 * MIB if runtime else 0)]
        local = os.environ.get("PROJECT_AI_RUNTIME_LOCAL")
        if not local and self.source_dir is not None and (self.source_dir / RUNTIME).is_file():
            local = str(self.source_dir / RUNTIME)
        if local:
            downloads[0][3].insert(0, Path(local).absolute().as_uri())
        for name, sha, hf, folder in MODELS:
            target = self.root / folder / name
            size = MODEL_SIZES[name]
            credit = 0
            if target.is_file() and self._hash(target) == sha:
                installed.add(name)
                credit = min(size, target.stat().st_size)
            downloads.append((self.cache / name, sha, size, self._model_urls(name, hf), credit))
        for dest, sha, size, urls, credit in downloads:
            if dest.is_file() and self._hash(dest) == sha:
                credit = max(credit, min(size, dest.stat().st_size))
            # Mirrors are alternatives, not additive savings. Ignore unrelated
            # cache entries and cap every allowance to its known destination.
            for url in urls:
                part = self._part_path(dest, url)
                if part.is_file():
                    credit = max(credit, min(size, part.stat().st_size))
            allowance += credit
        # Fresh budget: app staging ~15 MiB, runtime 75 + 29 MiB,
        # models ~240 MiB, plus conservative filesystem/retry headroom.
        needed = 650 * MIB - allowance
        free = shutil.disk_usage(self.prefix).free
        detail = "Need {:.1f} MiB free; {:.1f} MiB available on {}".format(
            needed / MIB, free / MIB, self.prefix)
        if free < needed:
            raise OSError("Not enough free space. " + detail + ". Free space or retry with retained downloads.")
        self._event("space", "Free space checked", detail, free, needed)
        return runtime, installed

    def _commit(self, stage, runtime, models):
        for folder in (self.tools, self.root, self.root / "licenses", self.sysdir):
            self._mkdir(folder)
        if runtime:
            tree = stage / "runtime-tree"
            for name in ("runtime", "whisper"):
                self._check()
                dest = self.root / name
                self._replace(tree / name, dest)
            self._copy(tree / ".installer-runtime.json", self.root / ".installer-runtime.json", "runtime")
        for source, target in models:
            self._check()
            _no_symlinks(target)
            self._replace(source, target, retain_source=True)
            self._event("files", "Installed model", str(target), 1, 1)
        for folder, names, dest in (("app", APP_FILES, self.tools),
                                   ("assets", ASSET_FILES, self.root),
                                   ("licenses", LICENSE_FILES, self.root / "licenses")):
            for name in names:
                self._copy(stage / folder / name, dest / name)
        self._copy(stage / "system/227-theme.xml", self.root / "227-theme.xml")
        self._copy(self.root / "licenses/LFM2.5-Open-License.txt", self.root / "models/LICENSE-LFM2.5.txt")
        if not (self.root / "config.json").exists():
            self._write_text(self.root / "config.json", json.dumps(DEFAULT_CONFIG, indent=2) + "\n", "config")
        else:
            self._event("config", "Keeping existing settings and conversations")
        self._remove(self.tools / "mic_test.py")
        self._remove(self.sysdir / "MIC TEST.sh")
        self._copy(self.root / "227-art.jpg", self.sysdir / "227-art.jpg")
        self._copy(stage / "system/gamelist.xml", self.sysdir / "gamelist.xml")
        self._themes()
        # Last: publish a usable launcher, then make ES aware of it.
        self._copy(stage / "system/227 AI.sh", self.sysdir / "227 AI.sh")
        self._write_text(self.es_cfg, self._es_text(), "es")

    def run(self, action):
        """Run synchronously in a worker. Calling uninstall asserts UI confirmation.

        install/update have identical preservation semantics. A persistent card
        lock prevents concurrent runs; shared ES/theme changes use a second lock.
        Cancellation is honored before publication/deletion, then deferred until
        the entire commit, rollback, or destructive uninstall has finished.
        No sudo, service restart, prompt, sys.exit, or manager self-update occurs.
        """
        if action not in ("install", "update", "uninstall"):
            raise ValueError("Unknown installer action: {}".format(action))
        self._started = time.monotonic()
        stage = None
        try:
            self._check()
            self.validate_card()
            lockpath = self.prefix / ".227-ai.lock"
            _no_symlinks(lockpath)
            with lockpath.open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Another installer is already modifying {}".format(self.prefix))
                self._event("prepare", action.title() + " 227 AI", str(self.prefix))
                if action != "uninstall":
                    verified_runtime, installed_models = self._preflight()
                    self.cache.mkdir(exist_ok=True)
                    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=str(self.cache)))
                    self._stage_files(stage)
                    runtime = self._stage_runtime(stage, verified_runtime)
                    models = []
                    for name, sha, hf, folder in MODELS:
                        target = self.root / folder / name
                        if name in installed_models:
                            self._event("files", "Keeping verified model", str(target))
                            continue
                        urls = self._model_urls(name, hf)
                        source = self._download(self.cache / name, sha, urls)
                        models.append((source, target))
                es_lockpath = self.es_cfg.with_name(self.es_cfg.name + ".227-ai.lock")
                _no_symlinks(es_lockpath)
                with es_lockpath.open("a+b") as es_lock:
                    while True:
                        self._check()
                        try:
                            fcntl.flock(es_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            self._event("es", "Waiting for EmulationStation configuration lock")
                            self.cancel.wait(.2)
                    self._es_text(remove=action == "uninstall")
                    self._themes(remove=action == "uninstall", verify=True)
                    self._check()
                    self._cancel_reported = False
                    self._finalizing = "uninstall" if action == "uninstall" else "commit"
                    self._event(self._finalizing, "Finalizing uninstall" if action == "uninstall" else
                                "Committing installation", "Cancellation is deferred until this phase finishes")
                    if action == "uninstall":
                        # Never restore a whole backup or touch tools/227-ai-manager.
                        self._write_text(self.es_cfg, self._es_text(remove=True), "es")
                        for name in APP_FILES + ("mic_test.py",):
                            self._check()
                            (self.tools / name).unlink(missing_ok=True)
                            self._event("files", "Removed " + name)
                        for target in (self.root, self.sysdir, self.cache):
                            self._check()
                            if target.exists():
                                shutil.rmtree(str(target))
                            self._event("files", "Removed app data", str(target))
                        self._themes(remove=True)
                    else:
                        self._journal = []
                        try:
                            self._commit(stage, runtime, models)
                            self._check()
                        except Exception:
                            self._finalizing = "rollback"
                            self._event("rollback", "Restoring previous installation")
                            self._end_transaction(rollback=True)
                            raise
                        else:
                            self._end_transaction()
                    self._check()
                self._event("complete", "227 AI " + ("uninstalled" if action == "uninstall" else "installed"),
                            "Restart EmulationStation to refresh its systems", 1, 1, overall=1.0)
        except Cancelled as error:
            self._event("cancelled", "Cancelled", str(error))
            raise
        except Exception as error:
            self._event("error", "Installer failed", str(error))
            raise
        finally:
            self._finalizing = None
            if stage is not None:
                shutil.rmtree(str(stage), ignore_errors=True)
