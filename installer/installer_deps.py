"""Capability-first OS dependencies; importing this module never probes or installs.

check() returns scope, ok, missing (package names), and fatal (capability errors).
ensure() adds installed (the audited package plan), or raises RuntimeError.
Fonts and Python installation belong to the bundled assets and shell bootstrap.
"""

import argparse
from collections import deque
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time


SDL = {"libSDL2-2.0.so.0": "libsdl2-2.0-0",
       "libSDL2_ttf-2.0.so.0": "libsdl2-ttf-2.0-0"}
SPEECH = "libespeak-ng.so.1"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C",
       "DEBIAN_FRONTEND": "noninteractive"}
OPTIONS = ["-o", "Acquire::http::Timeout=20", "-o", "Acquire::https::Timeout=20",
           "-o", "Acquire::Retries=1", "-o", "DPkg::Lock::Timeout=30"]
INSTALL = ["--no-upgrade", "--no-remove", "--no-install-recommends", "install"]
SPEECH_PROBE = r'''
import ctypes as C, json
try:
    lib = C.CDLL("libespeak-ng.so.1")
except OSError as error:
    print(json.dumps({"load_error": str(error)}))
else:
    lib.espeak_Initialize.argtypes = [C.c_int, C.c_int, C.c_char_p, C.c_int]
    lib.espeak_Initialize.restype = C.c_int
    lib.espeak_SetVoiceByName.argtypes = [C.c_char_p]
    lib.espeak_SetVoiceByName.restype = C.c_int
    lib.espeak_Terminate.argtypes = []
    lib.espeak_Terminate.restype = C.c_int
    try:
        # DONT_EXIT lets missing voice data return an error instead of exiting.
        ready = lib.espeak_Initialize(2, 0, None, 0x8000) > 0
        english = ready and lib.espeak_SetVoiceByName(b"en") == 0
        print(json.dumps({"english": english}))
    finally:
        lib.espeak_Terminate()
'''


class DependencyCancelled(RuntimeError):
    """Cancellation before apt, or after an in-flight installation finishes."""


def _absent(name, error):
    # A missing *dependency* or a broken ELF is not permission to replace firmware.
    if not str(error).startswith(name + ": cannot open shared object file: No such file"):
        return False
    directories = ["/lib", "/usr/lib", "/lib64", "/usr/lib64",
                   "/lib/aarch64-linux-gnu", "/usr/lib/aarch64-linux-gnu"]
    if "LD_LIBRARY_PATH" in os.environ:
        directories += os.environ["LD_LIBRARY_PATH"].split(":")
    for directory in directories:
        path = Path(directory or ".") / name
        if path.is_symlink() or path.exists():
            return False
    return True


def check(scope="manager"):
    """Read-only capability checks, without platform, privilege, or package queries."""
    if scope not in ("manager", "app", "all"):
        raise ValueError("Unknown dependency scope: " + str(scope))
    missing, fatal = [], {}
    if sys.version_info < (3, 9):
        fatal["python"] = "Python 3.9 or newer is required; use the shell bootstrap."
    if scope in ("manager", "all"):
        for library, package in SDL.items():
            try:
                ctypes.CDLL(library)
            except OSError as error:
                if _absent(library, error):
                    missing.append(package)
                else:
                    fatal[library] = "Existing SDL library cannot load: " + str(error)
    if scope in ("app", "all"):
        try:
            result = subprocess.run([sys.executable, "-c", SPEECH_PROBE],
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=10)
            if result.returncode:
                raise RuntimeError("eSpeak probe failed: " + result.stderr[-2048:])
            speech = json.loads(result.stdout.strip().splitlines()[-1])
            if not isinstance(speech, dict):
                raise ValueError("Invalid eSpeak probe response")
            if "load_error" in speech:
                if _absent(SPEECH, speech["load_error"]):
                    missing.extend(("libespeak-ng1", "espeak-ng-data"))
                else:
                    fatal[SPEECH] = speech["load_error"]
            elif not speech["english"]:
                missing.append("espeak-ng-data")
        except (OSError, RuntimeError, ValueError, KeyError, IndexError,
                subprocess.TimeoutExpired) as error:
            fatal["speech"] = "Cannot validate eSpeak English data: " + str(error)
        for executable, package in (("arecord", "alsa-utils"), ("aplay", "alsa-utils"),
                                    ("amixer", "alsa-utils"), ("pkill", "procps")):
            if not shutil.which(executable):
                missing.append(package)
    if not shutil.which("curl"):
        missing.append("curl")
    return dict(scope=scope, ok=not missing and not fatal,
                missing=list(dict.fromkeys(missing)), fatal=fatal)


def _run(command):
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace",
                              timeout=120, env=ENV)
    except (OSError, subprocess.TimeoutExpired) as error:
        output = getattr(error, "stdout", "") or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        raise RuntimeError("System dependency command failed: {}\n{}".format(
            error, output[-4096:])) from error


def _authority():
    release = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key in ("ID", "ID_LIKE"):
                release[key] = " ".join(shlex.split(value)).lower()
    except (OSError, ValueError) as error:
        raise RuntimeError("Cannot identify a supported OS: " + str(error)) from error
    family = (release.get("ID", "") + " " + release.get("ID_LIKE", "")).split()
    apt, dpkg = shutil.which("apt-get"), shutil.which("dpkg")
    if not {"debian", "ubuntu"}.intersection(family) or os.uname().machine != "aarch64" or not apt or not dpkg:
        raise RuntimeError("Automatic setup supports only Debian/Ubuntu aarch64 with apt-get and dpkg.")
    architecture = _run([dpkg, "--print-architecture"])
    if architecture.returncode or architecture.stdout.strip() != "arm64":
        raise RuntimeError("Automatic setup requires dpkg architecture arm64. " + architecture.stdout[-2048:])
    if os.geteuid() == 0:
        return [apt]
    sudo = shutil.which("sudo")
    if not sudo:
        raise RuntimeError("Administrator access requires root or noninteractive sudo -n; no password is requested.")
    authorized = _run([sudo, "-n", "--", "true"])
    if authorized.returncode:
        raise RuntimeError("Noninteractive sudo -n was denied; no password is stored or requested.\n" +
                           authorized.stdout[-2048:])
    # Set these after sudo too: sudo's environment policy may otherwise remove them.
    return [sudo, "-n", "--", "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C", apt]


def _audit(output, missing):
    plan = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Remv "):
            raise RuntimeError("Refusing package removal: " + line)
        if not line.startswith("Inst "):
            continue
        match = re.fullmatch(r"Inst ([a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?) \(([A-Za-z0-9.+:~\-]+)(?: [^\n]*)?\)", line)
        if not match:
            raise RuntimeError("Refusing upgrade or unrecognized apt plan: " + line)
        name, version = match.groups()
        base = name.split(":")[0]
        if re.match(r"(?:mesa|libegl|libgles|libgl|libgbm|libdrm|libmali|xserver|firmware)", base):
            raise RuntimeError("Refusing protected graphics/firmware package: " + line)
        if base in SDL.values() and base not in missing:
            raise RuntimeError("Refusing SDL installation without its exact failed library probe: " + line)
        if name in plan:
            raise RuntimeError("Duplicate package in apt plan: " + name)
        plan[name] = version
    if not set(missing).issubset({name.split(":")[0] for name in plan}):
        raise RuntimeError("Apt cannot supply missing capabilities without reinstalling existing packages.")
    return plan


def _install(command, report, cancel):
    """Drain bounded chunks while apt owns dpkg. Never kill a running installation."""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace",
                               env=ENV, start_new_session=True)
    lines, recent = queue.Queue(maxsize=64), deque(maxlen=20)

    def read():
        try:
            while True:
                line = process.stdout.readline(2048)
                if not line:
                    break
                lines.put(line.rstrip())
        except OSError as error:
            lines.put(str(error))
        finally:
            lines.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    eof = deferred = False
    callback_error = None
    while not eof or process.poll() is None:
        try:
            if cancel is not None and cancel.is_set() and not deferred:
                deferred = True
                report("Finishing system setup safely...", "Cancellation will take effect after apt finishes.")
            line = lines.get(timeout=.1)
            if line is None:
                eof = True
            else:
                recent.append(line)
                report("Finishing system setup safely..." if deferred else "Installing system dependencies", line)
        except queue.Empty:
            pass
        except Exception as error:
            # Even a failing UI callback must not abandon apt or block its pipe.
            callback_error = error
    reader.join()
    process.stdout.close()
    if process.wait():
        raise RuntimeError("System dependency installation failed:\n" + "\n".join(recent))
    if callback_error is not None:
        raise RuntimeError("Dependency progress callback failed after apt finished: " + str(callback_error))


def ensure(scope="manager", emit=None, cancel=None, allow_install=True):
    """Check capabilities, optionally install a safe plan, then check again.

    cancel is a threading.Event-like object. No package writes occur when
    allow_install=False; the same read-only status dictionary is returned.
    """
    started = time.monotonic()

    def report(label, detail=""):
        if emit is not None:
            emit(dict(phase="dependencies", label=label, detail=str(detail), done=0,
                      total=None, speed=None, elapsed=time.monotonic() - started))

    def checkpoint():
        if cancel is not None and cancel.is_set():
            raise DependencyCancelled("System dependency setup cancelled.")

    checkpoint()
    report("Checking system dependencies")
    status = check(scope)
    status["installed"] = []
    if not allow_install:
        return status
    if status["fatal"]:
        raise RuntimeError("System dependency checks failed: " + "; ".join(status["fatal"].values()))
    if status["ok"]:
        return status
    missing = status["missing"]
    manual = "Missing packages: {}. Manual setup: sudo apt-get {}".format(
        ", ".join(missing), " ".join(INSTALL + missing))
    checkpoint()
    try:
        apt = _authority() + OPTIONS
    except RuntimeError as error:
        raise RuntimeError(str(error) + "\n" + manual) from error
    checkpoint()
    report("Planning system dependencies", ", ".join(missing))
    checkpoint()
    simulation = _run(apt + ["--simulate"] + INSTALL + missing)
    if simulation.returncode:
        checkpoint()
        report("Refreshing package lists", simulation.stdout[-2048:])
        checkpoint()
        update = _run(apt + ["update"])
        if update.returncode:
            raise RuntimeError("Could not refresh package lists:\n" + update.stdout[-4096:])
        checkpoint()
        simulation = _run(apt + ["--simulate"] + INSTALL + missing)
    if simulation.returncode:
        raise RuntimeError("Could not plan system dependencies:\n" + simulation.stdout[-4096:])
    plan = _audit(simulation.stdout, missing)
    pinned = [name + "=" + version for name, version in plan.items()]
    checkpoint()
    # Pin the entire dependency closure and audit again immediately before apt.
    verified = _run(apt + ["--simulate"] + INSTALL + pinned)
    if verified.returncode or _audit(verified.stdout, missing) != plan:
        raise RuntimeError("Apt plan changed; refusing installation:\n" + verified.stdout[-4096:])
    report("Installing system dependencies", ", ".join(pinned))
    checkpoint()
    try:
        _install(apt + ["--yes"] + INSTALL + pinned, report, cancel)
    except OSError as error:
        raise RuntimeError("Cannot start system dependency installation: " + str(error)) from error
    report("Rechecking system dependencies")
    status = check(scope)
    status["installed"] = list(plan)
    if not status["ok"]:
        raise RuntimeError("Dependencies still unavailable after apt: {} {}".format(
            ", ".join(status["missing"]), "; ".join(status["fatal"].values())))
    checkpoint()
    report("System dependencies ready")
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("manager", "app", "all"), default="manager")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--install", action="store_true")
    args = parser.parse_args(argv)
    try:
        status = ensure(args.scope, allow_install=args.install,
                        emit=lambda event: print(event["label"] + ": " + event["detail"], flush=True))
        if status["missing"]:
            print("Missing packages: " + ", ".join(status["missing"]))
        for error in status["fatal"].values():
            print(error)
        return 0 if status["ok"] else 1
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
