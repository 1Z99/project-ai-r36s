"""Bootstrap integration tests use temporary cards, never the handheld mounts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
UI = '''import json, os, sys
print(json.dumps(dict(args=sys.argv[1:], video=os.environ.get('SDL_VIDEODRIVER'),
                     render=os.environ.get('SDL_RENDER_DRIVER'))))
'''


def apt_plan(*names, python_version='3.13.1-1'):
    return '\n'.join('Inst {} ({} Debian:stable [arm64])'.format(
        name, python_version if name.split(':')[0] == 'python3' else '1.2-3') for name in names)


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='227 bootstrap ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = dict(os.environ, SDL_VIDEODRIVER='desktop-test', SDL_RENDER_DRIVER='desktop-render')
        self.env.pop('PYTHONPATH', None)
        # Never let a graphical failure test open a real terminal dialog.
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name in ('sh', 'dirname', 'basename', 'awk', 'env', 'mktemp', 'tee', 'cat',
                     'sort', 'cmp', 'mkdir', 'rm', 'mv', 'cp', 'chmod', 'tail'):
            (self.bin / name).symlink_to(shutil.which(name))
        self.python = self.bin / '.python-template'
        self.python.write_text('#!/bin/sh\nif [ "$1" = -B ] && [ "$2" = -c ]; then exit 0; fi\n'
                               'exec ' + repr(sys.executable) + ' "$@"\n')
        self.python.chmod(0o755)
        shutil.copy2(self.python, self.bin / 'python3')
        self.curl = self.bin / '.curl-template'
        self.curl.write_text('#!' + sys.executable + '\n' + '''import pathlib, shutil, sys
from urllib.parse import urlparse, unquote
args = sys.argv[1:]
url = args[-1]
with open(''' + repr(str(self.base / 'curl.calls')) + ''', 'a') as log:
    log.write(url + '\\n')
if not url.startswith('file://'):
    print('Network forbidden by bootstrap fixture: ' + url, file=sys.stderr)
    sys.exit(99)
flag = '--output' if '--output' in args else '-o'
try:
    shutil.copyfile(unquote(urlparse(url).path), args[args.index(flag) + 1])
except OSError as error:
    print(str(error), file=sys.stderr)
    sys.exit(22)
''')
        self.curl.chmod(0o755)
        shutil.copy2(self.curl, self.bin / 'curl')
        for name in ('apt-get', 'dpkg', 'sudo', 'uname', 'id'):
            stub = self.bin / name
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "Unmocked system command: ' + name + '" >&2\nexit 97\n')
            stub.chmod(0o755)
        dialog = self.bin / 'dialog'
        dialog.write_text('#!/bin/sh\nexit 0\n')
        dialog.chmod(0o755)
        self.env['PATH'] = str(self.bin)
        self.script = self.base / 'install.sh'
        shutil.copy2(REPO / 'install227ai.sh', self.script)

    def run_sh(self, *args, script=None):
        return subprocess.run(['sh', str(script or self.script), *map(str, args)],
                              env=self.env, cwd=self.base, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=15)

    def snapshot(self):
        return {str(path.relative_to(self.base)): (path.stat().st_mtime_ns, path.read_bytes())
                for path in self.base.rglob('*') if path.is_file()}

    def core(self, folder=None):
        folder = folder or self.base
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / 'installer/installer_core.py', folder / 'installer_core.py')
        return folder

    def target(self):
        card = self.base / 'target'
        card.mkdir()
        es = self.base / 'es_systems.cfg'
        es.write_text('<systemList></systemList>\n')
        return ['--cli', '--roms', str(card), '--action', 'install', '--es-cfg', str(es)]

    def graphical(self, repo=False):
        folder = self.core(self.base / 'installer' if repo else self.base)
        (folder / 'installer_ui.py').write_text(UI)
        (folder / 'installer_cli.py').write_text('# CLI fixture\n')
        (folder / 'installer_deps.py').write_text('# No-op dependency fixture; never load host SDL or apt.\n')
        self.font_assets(self.base)
        if repo:
            (self.base / 'app').mkdir()
        return folder

    def font_assets(self, root):
        for relative in ('assets/DejaVuSansMono.ttf', 'assets/DejaVuSansMono-Bold.ttf',
                         'licenses/DejaVu-fonts-copyright'):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / relative, target)

    def executable(self, name, code):
        path = self.bin / name
        path.write_text('#!' + sys.executable + '\n' + code)
        path.chmod(0o755)

    def system_tools(self, missing=('python3', 'curl'), release='ID=debian\n',
                     machine='aarch64', architecture='arm64', uid=0, authorized=True,
                     plans=None, update_code=0, install_code=0, install_tools=True):
        """Fake the complete system boundary, including every apt invocation."""
        for name in missing:
            (self.bin / name).unlink(missing_ok=True)
        release_path = self.base / 'os-release'
        release_path.write_text(release)
        logs = self.base / 'setup-logs'
        logs.mkdir(exist_ok=True)
        self.script.write_text((REPO / 'install227ai.sh').read_text()
                               .replace('/etc/os-release', str(release_path))
                               .replace('/tmp/227-ai-setup.XXXXXX', str(logs / '227-ai-setup.XXXXXX')))
        self.executable('uname', 'print(' + repr(machine) + ')\n')
        self.executable('id', 'print(' + repr(str(uid)) + ')\n')
        self.executable('dpkg', r'''import re, sys
args = sys.argv[1:]
if args == ['--print-architecture']:
    print(''' + repr(architecture) + ''')
elif len(args) == 4 and args[0] == '--compare-versions' and args[2:] == ['ge', '3.9']:
    version = tuple(map(int, re.findall(r'\\d+', args[1].split(':')[-1])[:2]))
    sys.exit(0 if version >= (3, 9) else 1)
else:
    sys.exit(98)
''')
        self.executable('sudo', '''import os, sys
args = sys.argv[1:]
if args[:2] != ['-n', '--']:
    sys.exit(98)
if not ''' + repr(authorized) + ''':
    print('sudo: a password is required', file=sys.stderr)
    sys.exit(1)
if args[2:] != ['true']:
    os.execv(args[2], args[2:])
''')
        plans = plans if plans is not None else [(apt_plan(*missing), 0)]
        self.executable('apt-get', '''import json, os, pathlib, shutil, sys
args = sys.argv[1:]
calls = pathlib.Path(''' + repr(str(self.base / 'apt.calls')) + ''')
history = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
with calls.open('a') as stream:
    stream.write(json.dumps(dict(args=args, env=dict(os.environ))) + '\\n')
if '--simulate' in args:
    plans = ''' + repr(plans) + '''
    index = sum('--simulate' in call['args'] for call in history)
    output, code = plans[min(index, len(plans) - 1)]
    print(output)
    sys.exit(code)
if 'update' in args:
    print('package lists refreshed' if ''' + repr(update_code == 0) + ''' else 'mock network unavailable')
    sys.exit(''' + repr(update_code) + ''')
if '--yes' not in args or 'install' not in args:
    sys.exit(98)
print('Get:1 mock tools')
print('Setting up mock tools')
if ''' + repr(install_tools and install_code == 0) + ''':
    for template, target in ''' + repr([(str(self.python if name == 'python3' else self.curl),
                                       str(self.bin / name)) for name in missing]) + ''':
        shutil.copy2(template, target)
sys.exit(''' + repr(install_code) + ''')
''')

    def apt_calls(self):
        path = self.base / 'apt.calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def setup_logs(self):
        return list((self.base / 'setup-logs').glob('*/setup.log'))

    def test_help_standalone_no_writes_or_sdl(self):
        before = self.snapshot()
        result = self.run_sh('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--cli', result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_core_only_no_writes_or_ui(self):
        for layout in ('root', 'repo'):
            with self.subTest(layout=layout):
                folder = self.core(self.base if layout == 'root' else self.base / 'installer')
                args = self.target()
                before = self.snapshot()
                result = self.run_sh(*args, '--dry-run')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('No files written or downloaded', result.stdout)
                self.assertEqual(self.snapshot(), before)
                (folder / 'installer_core.py').unlink()
                shutil.rmtree(self.base / 'target')
                (self.base / 'es_systems.cfg').unlink()

    def test_dry_run_without_core_refuses_bootstrap(self):
        args = self.target()
        before = self.snapshot()
        result = self.run_sh(*args, '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Nothing was downloaded or written', result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_flags_and_unconfirmed_changes_never_bootstrap(self):
        for args in (('--roms', '/roms2'), ('--yes',), ('--dry-run',),
                     ('--cli', '--action', 'install', '--dry-run'),
                     ('--cli', '--roms', '/roms2', '--action', 'uninstall'),
                     ('--cli', '--roms', '/roms2', '--action', 'install', '--yes', '--desktop'),
                     ('--source',), ('--unknown',)):
            with self.subTest(args=args):
                before = self.snapshot()
                result = self.run_sh(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshot(), before)

    def test_self_locating_manager_no_args_sets_handheld_sdl(self):
        folder = self.graphical()
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads((folder / 'bootstrap.log').read_text())
        self.assertEqual(event, dict(args=[], video='kmsdrm', render='software'))

    def test_repo_source_default_and_explicit_override_desktop(self):
        folder = self.graphical(repo=True)
        result = self.run_sh('--desktop')
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads((folder / 'bootstrap.log').read_text())
        self.assertEqual(event['args'], ['--desktop', '--source', str(self.base)])
        self.assertEqual(event['video'], 'desktop-test')
        self.assertEqual(event['render'], 'desktop-render')
        result = self.run_sh('--source', self.base / 'another bundle')
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads((folder / 'bootstrap.log').read_text().splitlines()[-1])
        self.assertEqual(event['args'], ['--source', str(self.base / 'another bundle')])

    def test_startup_failure_retains_log_and_returns_failure(self):
        folder = self.graphical()
        (folder / 'installer_ui.py').write_text("raise RuntimeError('SDL fixture failure')\n")
        result = self.run_sh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SDL fixture failure', (folder / 'bootstrap.log').read_text())
        self.assertIn(str(folder / 'bootstrap.log'), result.stderr)

    def test_cli_route_passes_explicit_target_and_source_without_sdl_override(self):
        folder = self.graphical(repo=True)
        (folder / 'installer_cli.py').write_text(UI)
        args = self.target() + ['--yes']
        result = self.run_sh(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads(result.stdout)
        self.assertEqual(event['args'], args + ['--source', str(self.base)])
        self.assertEqual(event['video'], 'desktop-test')

    def fresh_cards(self, mounted=True):
        cards = tempfile.TemporaryDirectory(prefix='227-cards-')
        self.addCleanup(cards.cleanup)
        first, second = Path(cards.name) / 'sd1', Path(cards.name) / 'sd2'
        for path in (first, second):
            (path / 'tools').mkdir(parents=True)
        mounts = self.base / 'mounts'
        mounts.write_text(''.join('/dev/test {} ext4 rw 0 0\n'.format(card)
                                  for card in (first, second)) if mounted else '')
        # Redirect fixed production mountpoints and /proc/mounts into the fixture.
        text = (REPO / 'install227ai.sh').read_text().replace('/roms2', str(second)).replace('/roms', str(first))
        text = text.replace('/proc/mounts', str(mounts))
        script = first / 'tools/install.sh'
        script.write_text(text)
        payload = self.base / 'payload/installer'
        payload.mkdir(parents=True)
        (payload / 'installer_core.py').write_text('# core fixture\n')
        (payload / 'installer_ui.py').write_text(UI)
        (payload / 'installer_cli.py').write_text('# CLI fixture\n')
        (payload / 'installer_deps.py').write_text('# No-op dependency fixture\n')
        self.font_assets(payload.parent)
        self.env['PROJECT_AI_RAW_BASE'] = payload.parent.as_uri()
        return first, second, script, payload

    def test_fresh_bootstrap_same_tools_card_and_direct_offline_relaunch(self):
        first, second, script, _ = self.fresh_cards()
        result = self.run_sh(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        manager = first / 'tools/227-ai-manager'
        self.assertTrue((manager / 'install.sh').is_file())
        self.assertFalse((second / 'tools/227-ai-manager').exists())
        self.assertFalse(list(manager.glob('.bootstrap.*')))
        self.assertEqual(json.loads((manager / 'bootstrap.log').read_text().splitlines()[-1])['args'], [])
        self.env['PROJECT_AI_RAW_BASE'] = 'file:///does-not-exist'
        result = subprocess.run([str(first / 'tools/Install 227 AI.sh')], cwd='/', env=self.env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_download_does_not_publish_modules(self):
        first, _, script, payload = self.fresh_cards()
        (payload / 'installer_ui.py').write_text('not valid Python !!!')
        result = self.run_sh(script=script)
        self.assertNotEqual(result.returncode, 0)
        manager = first / 'tools/227-ai-manager'
        self.assertFalse(list(manager.glob('*.py')))
        self.assertFalse(list(manager.glob('.bootstrap.*')))
        self.assertIn('SyntaxError', (manager / 'bootstrap.log').read_text())

    def test_download_failure_preserves_existing_module_and_error_log(self):
        first, _, script, payload = self.fresh_cards()
        manager = first / 'tools/227-ai-manager'
        manager.mkdir()
        core = manager / 'installer_core.py'
        core.write_text('# previously installed core\n')
        (payload / 'installer_ui.py').unlink()
        result = self.run_sh(script=script)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(core.read_text(), '# previously installed core\n')
        self.assertFalse((manager / 'installer_ui.py').exists())
        self.assertFalse(list(manager.glob('.bootstrap.*')))
        self.assertIn('Cannot download installer_ui.py', (manager / 'bootstrap.log').read_text())

    def test_standalone_bootstrap_uses_mounted_fallback(self):
        first, second, script, _ = self.fresh_cards()
        shutil.copy2(script, self.script)
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((second / 'tools/227-ai-manager/installer_ui.py').is_file())
        self.assertFalse((first / 'tools/227-ai-manager').exists())

    def test_readonly_tools_card_is_skipped_for_fresh_bootstrap(self):
        first, second, script, _ = self.fresh_cards()
        (self.base / 'mounts').write_text('/dev/one {} ext4 ro 0 0\n/dev/two {} ext4 rw 0 0\n'.format(first, second))
        result = self.run_sh(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((first / 'tools/227-ai-manager').exists())
        self.assertTrue((second / 'tools/227-ai-manager/installer_ui.py').is_file())

    def test_deployed_manager_never_migrates_to_other_tools_card(self):
        first, second, script, _ = self.fresh_cards()
        result = self.run_sh(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.base / 'mounts').write_text('/dev/two {} ext4 rw 0 0\n'.format(second))
        result = self.run_sh(script=first / 'tools/227-ai-manager/install.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((second / 'tools/227-ai-manager').exists())

    def test_unmounted_tools_directories_are_not_cards(self):
        first, second, script, _ = self.fresh_cards(mounted=False)
        result = self.run_sh(script=script)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((first / 'tools/227-ai-manager').exists())
        self.assertFalse((second / 'tools/227-ai-manager').exists())

    def test_missing_python_and_curl_use_audited_pinned_plan(self):
        folder = self.graphical()
        self.system_tools(plans=[(apt_plan('python3', 'curl', 'libcurl4'), 0)])
        self.env['APT_CONFIG'] = '/untrusted'
        result = self.run_sh('--source', 'path with spaces')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.apt_calls()
        self.assertEqual(len(calls), 3)
        self.assertIn('--simulate', calls[0]['args'])
        self.assertNotIn('update', [arg for call in calls for arg in call['args']])
        self.assertEqual(calls[-1]['args'][-3:], ['curl=1.2-3', 'libcurl4=1.2-3', 'python3=3.13.1-1'])
        for call in calls:
            for option in ('Acquire::http::Timeout=20', 'Acquire::https::Timeout=20',
                           'Acquire::Retries=1', 'DPkg::Lock::Timeout=30',
                           '--no-upgrade', '--no-remove', '--no-install-recommends'):
                self.assertIn(option, call['args'])
            self.assertEqual(call['env']['DEBIAN_FRONTEND'], 'noninteractive')
            self.assertEqual(call['env']['LC_ALL'], 'C')
            self.assertNotIn('APT_CONFIG', call['env'])
        self.assertIn('Get:1 mock tools', result.stdout)
        logs = self.setup_logs()
        self.assertEqual(len(logs), 1)
        self.assertIn('Setting up mock tools', logs[0].read_text())
        self.assertEqual(json.loads((folder / 'bootstrap.log').read_text())['args'],
                         ['--source', 'path with spaces'])
        self.assertTrue((folder / 'fonts/DejaVuSansMono.ttf').is_file())

    def test_only_actually_missing_tools_are_requested_and_cli_args_survive(self):
        folder = self.graphical()
        (folder / 'installer_cli.py').write_text(UI)
        self.system_tools(missing=('curl',))
        args = self.target() + ['--yes', '--source', 'another bundle']
        result = self.run_sh(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.apt_calls()
        self.assertEqual(calls[0]['args'][-2:], ['install', 'curl'])
        self.assertEqual(calls[-1]['args'][-1], 'curl=1.2-3')
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['args'], args)
        self.assertFalse((folder / 'fonts').exists())

    def test_python_only_bootstrap_accepts_id_like_and_sudo_without_rooting_ui(self):
        folder = self.graphical()
        self.system_tools(missing=('python3',), release='ID=custom\nID_LIKE="ubuntu debian"\n', uid=1000)
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.apt_calls()[-1]['args'][-1], 'python3=3.13.1-1')
        self.assertTrue((folder / 'bootstrap.log').is_file())

    def test_python_too_old_or_broken_is_never_replaced(self):
        for code in (1, 42):
            with self.subTest(code=code):
                self.system_tools(missing=('curl',))
                (self.bin / 'python3').write_text('#!/bin/sh\nexit ' + str(code) + '\n')
                before = self.snapshot()
                result = self.run_sh()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Existing Python is broken or older than 3.9', result.stderr)
                self.assertEqual(self.apt_calls(), [])
                self.assertEqual(self.setup_logs(), [])
                self.assertEqual(self.snapshot(), before)

    def test_ineligible_modes_missing_python_never_write_or_install(self):
        self.system_tools()
        for args in (('--help',), ('--unknown',), ('--desktop',),
                     ('--cli', '--roms', '/roms2', '--action', 'install', '--dry-run'),
                     ('--cli', '--roms', '/roms2', '--action', 'install'),
                     ('--cli', '--roms', '/roms2', '--action', 'uninstall', '--yes')):
            with self.subTest(args=args):
                before = self.snapshot()
                result = self.run_sh(*args)
                self.assertEqual(result.returncode == 0, args == ('--help',), result.stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.apt_calls(), [])
                self.assertEqual(self.setup_logs(), [])

    def test_unsupported_platform_and_no_authority_do_not_create_logs(self):
        variants = (dict(release='ID=arch\n'), dict(machine='x86_64'), dict(architecture='armhf'),
                    dict(uid=1000, authorized=False))
        for variant in variants:
            with self.subTest(variant=variant):
                self.system_tools(**variant)
                before = self.snapshot()
                result = self.run_sh()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Missing packages: python3 curl', result.stderr)
                self.assertIn('Manual setup: sudo apt-get', result.stderr)
                self.assertEqual(self.apt_calls(), [])
                self.assertEqual(self.setup_logs(), [])
                self.assertEqual(self.snapshot(), before)

    def test_missing_apt_dpkg_or_sudo_refuses_without_writes(self):
        for name in ('apt-get', 'dpkg', 'sudo'):
            with self.subTest(name=name):
                self.system_tools(uid=1000)
                (self.bin / name).unlink()
                before = self.snapshot()
                result = self.run_sh()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Missing packages: python3 curl', result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_stale_index_refreshes_once_then_reaudits(self):
        self.graphical()
        self.system_tools(plans=[('mock stale index', 100), (apt_plan('python3', 'curl'), 0)])
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.apt_calls()
        self.assertEqual(sum('update' in call['args'] for call in calls), 1)
        self.assertEqual(sum('--simulate' in call['args'] for call in calls), 3)
        self.assertIn('Refreshing package lists', result.stdout)

    def test_refresh_plan_and_install_errors_point_to_retained_logs(self):
        variants = ((dict(plans=[('stale', 100)], update_code=100), 'mock network unavailable'),
                    (dict(plans=[('no candidate', 100)]), 'Cannot plan setup'),
                    (dict(install_code=100), 'System tool setup failed'))
        for options, message in variants:
            with self.subTest(options=options):
                self.system_tools(**options)
                (self.base / 'apt.calls').unlink(missing_ok=True)
                result = self.run_sh()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout + result.stderr)
                self.assertIn('setup.log', result.stderr)
                self.assertTrue(all(log.is_file() for log in self.setup_logs()))
                self.assertFalse((self.base / 'bootstrap.log').exists())

    def test_bootstrap_rejects_upgrades_removals_and_protected_dependencies(self):
        unsafe = ['Remv unrelated [1]', 'Inst libc6 [2.31] (2.36 Debian [arm64])',
                  'Inst python3 [3.8] (3.13 Debian [arm64])']
        unsafe += [apt_plan(name) for name in ('mesa-utils', 'libegl1', 'libgles2', 'libgl1', 'libgbm1',
                                              'libdrm2', 'libmali-rk', 'xserver-xorg-core', 'firmware-linux',
                                              'libsdl2-2.0-0')]
        for line in unsafe:
            with self.subTest(line=line):
                self.system_tools(plans=[(apt_plan('python3', 'curl') + '\n' + line, 0)])
                (self.base / 'apt.calls').unlink(missing_ok=True)
                result = self.run_sh()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Unsafe apt plan', result.stderr)
                self.assertEqual(len(self.apt_calls()), 1)

    def test_malformed_duplicate_missing_and_changed_plans_are_rejected(self):
        valid = apt_plan('python3', 'curl')
        variants = ([(valid + '\nInst curl (1.2-3 Debian [arm64])', 0)],
                    [(apt_plan('curl'), 0)], [(valid + '\nInst bad/name (1 Debian [arm64])', 0)],
                    [(valid + '\nInst other (1;touch/tmp/x Debian [arm64])', 0)],
                    [(valid, 0), (valid + '\n' + apt_plan('new-dependency'), 0)],
                    [(valid, 0), (valid.replace('1.2-3', '1.3-4'), 0)])
        for plans in variants:
            with self.subTest(plans=plans):
                self.system_tools(plans=plans)
                (self.base / 'apt.calls').unlink(missing_ok=True)
                result = self.run_sh()
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(all('--yes' not in call['args'] for call in self.apt_calls()))

    def test_python_candidate_older_than_39_is_rejected(self):
        self.system_tools(plans=[(apt_plan('python3', 'curl', python_version='3.8.10-1'), 0)])
        result = self.run_sh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python candidate is older than 3.9', result.stderr)
        self.assertEqual(len(self.apt_calls()), 1)

    def test_working_firmware_python_is_not_replaced_by_untracked_python_packages(self):
        self.system_tools(missing=('curl',), plans=[(apt_plan('curl', 'python3-minimal'), 0)])
        result = self.run_sh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsafe apt plan', result.stderr)
        self.assertEqual(len(self.apt_calls()), 1)

    def test_commands_are_rechecked_after_install(self):
        self.system_tools(install_tools=False)
        result = self.run_sh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.9+ still unavailable', result.stderr)
        self.assertEqual(len(self.apt_calls()), 3)

    def test_dry_run_with_python_but_no_curl_remains_read_only(self):
        self.core()
        (self.bin / 'curl').unlink()
        args = self.target() + ['--dry-run']
        before = self.snapshot()
        result = self.run_sh(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.apt_calls(), [])

    def test_desktop_and_uninstall_skip_missing_curl_helper_and_fonts(self):
        folder = self.graphical()
        (folder / 'installer_deps.py').write_text('raise RuntimeError("Must not run")\n')
        (folder / 'installer_cli.py').write_text(UI)
        (self.bin / 'curl').unlink()
        result = self.run_sh('--desktop')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((folder / 'fonts').exists())
        args = self.target() + ['--yes']
        args[args.index('install')] = 'uninstall'
        (folder / 'installer_deps.py').unlink()
        result = self.run_sh(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((folder / 'fonts').exists())
        self.assertFalse((self.base / 'curl.calls').exists())
        self.assertEqual(self.apt_calls(), [])

    def test_manager_helper_receives_only_manager_scope_and_errors_are_visible(self):
        folder = self.graphical()
        helper = folder / 'installer_deps.py'
        helper.write_text('import sys\nassert sys.argv[1:] == ["--scope", "manager", "--install"]\n'
                          'print("dependency diagnostic")\nsys.exit(1)\n')
        result = self.run_sh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('dependency diagnostic', result.stderr)
        self.assertIn('dependency diagnostic', (folder / 'bootstrap.log').read_text())
        self.assertFalse((folder / 'fonts').exists())

    def test_local_fonts_copyright_and_valid_offline_cache(self):
        folder = self.graphical()
        self.env['PROJECT_AI_RAW_BASE'] = 'file:///not-available'
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        fonts = folder / 'fonts'
        for name in ('DejaVuSansMono.ttf', 'DejaVuSansMono-Bold.ttf', 'DejaVu-fonts-copyright'):
            root = 'licenses' if name.endswith('copyright') else 'assets'
            self.assertEqual((fonts / name).read_bytes(), (REPO / root / name).read_bytes())
        before = {path.name: path.stat().st_mtime_ns for path in fonts.iterdir()}
        shutil.rmtree(self.base / 'assets')
        shutil.rmtree(self.base / 'licenses')
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {path.name: path.stat().st_mtime_ns for path in fonts.iterdir()})
        self.assertFalse((self.base / 'curl.calls').exists())
        self.assertFalse(list(folder.glob('.fonts.*')))

    def test_corrupt_cached_fonts_are_atomically_replaced_from_local_assets(self):
        folder = self.graphical()
        fonts = folder / 'fonts'
        fonts.mkdir()
        (fonts / 'DejaVuSansMono.ttf').write_bytes(b'corrupt')
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((fonts / 'DejaVuSansMono.ttf').read_bytes(),
                         (REPO / 'assets/DejaVuSansMono.ttf').read_bytes())
        self.assertFalse((self.base / 'curl.calls').exists())

    def test_source_and_manager_bundle_fonts_work_without_repo_assets(self):
        folder = self.graphical()
        shutil.rmtree(self.base / 'assets')
        shutil.rmtree(self.base / 'licenses')
        for source in (self.base / 'explicit source', folder / 'bundle'):
            with self.subTest(source=source):
                self.font_assets(source)
                args = ('--source', source) if source.name == 'explicit source' else ()
                result = self.run_sh(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                shutil.rmtree(folder / 'fonts')
                shutil.rmtree(source)
        self.assertFalse((self.base / 'curl.calls').exists())

    def test_symlink_font_storage_or_target_is_refused_without_external_writes(self):
        folder = self.graphical()
        outside = self.base / 'outside'
        outside.mkdir()
        fonts = folder / 'fonts'
        fonts.symlink_to(outside, target_is_directory=True)
        result = self.run_sh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing symlink font storage', result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        fonts.unlink()
        fonts.mkdir()
        (fonts / 'DejaVuSansMono.ttf').symlink_to(outside / 'absent.ttf')
        result = self.run_sh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing symlink font file', result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(list(folder.glob('.fonts.*')))

    def test_fresh_download_includes_all_four_modules_and_three_font_assets(self):
        first, _, script, _ = self.fresh_cards()
        result = self.run_sh(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        manager = first / 'tools/227-ai-manager'
        self.assertEqual({path.name for path in manager.glob('*.py')},
                         {'installer_core.py', 'installer_ui.py', 'installer_cli.py', 'installer_deps.py'})
        self.assertEqual(len(list((manager / 'fonts').iterdir())), 3)
        self.assertEqual(len((self.base / 'curl.calls').read_text().splitlines()), 7)
        self.assertFalse(list(manager.glob('.fonts.*')))

    def test_missing_cached_helper_refreshes_matched_set(self):
        first, _, script, payload = self.fresh_cards()
        result = self.run_sh(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        manager = first / 'tools/227-ai-manager'
        (manager / 'installer_deps.py').unlink()
        (payload / 'installer_core.py').write_text('# updated core\n')
        (self.base / 'curl.calls').unlink()
        result = self.run_sh(script=manager / 'install.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((manager / 'installer_core.py').read_text(), '# updated core\n')
        urls = (self.base / 'curl.calls').read_text().splitlines()
        self.assertEqual(len(urls), 4)
        self.assertTrue(all('/installer/' in url for url in urls))

    def test_bad_helper_compile_does_not_publish_refreshed_set(self):
        first, _, script, payload = self.fresh_cards()
        result = self.run_sh(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        manager = first / 'tools/227-ai-manager'
        before = (manager / 'installer_core.py').read_bytes()
        (manager / 'installer_deps.py').unlink()
        (payload / 'installer_deps.py').write_text('not valid !!!')
        result = self.run_sh(script=manager / 'install.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((manager / 'installer_core.py').read_bytes(), before)
        self.assertFalse((manager / 'installer_deps.py').exists())

    def test_deployed_helper_symlink_is_refused_before_cache_refresh(self):
        first, _, script, _ = self.fresh_cards()
        manager = first / 'tools/227-ai-manager'
        manager.mkdir()
        (manager / 'installer_core.py').write_text('# core\n')
        (manager / 'installer_deps.py').symlink_to(self.base / 'missing-helper')
        result = self.run_sh(script=script)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing symlink manager file: installer_deps.py', result.stderr)
        self.assertFalse((self.base / 'curl.calls').exists())

    def test_corrupt_font_download_fails_hash_and_keeps_previous_file(self):
        first, _, script, payload = self.fresh_cards()
        (payload.parent / 'assets/DejaVuSansMono.ttf').write_bytes(b'corrupt response')
        result = self.run_sh(script=script)
        self.assertNotEqual(result.returncode, 0)
        manager = first / 'tools/227-ai-manager'
        self.assertIn('Bundled font checksum mismatch', result.stderr)
        self.assertFalse((manager / 'fonts/DejaVuSansMono.ttf').exists())
        self.assertFalse(list(manager.glob('.fonts.*')))

    def test_direct_cli_dry_run_no_bytecode_or_logs(self):
        self.core()
        cli = self.base / 'installer_cli.py'
        shutil.copy2(REPO / 'installer/installer_cli.py', cli)
        args = self.target()
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', str(cli), *args, '--dry-run'],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_direct_cli_uninstall_without_yes_is_rejected_without_writes(self):
        self.core()
        cli = self.base / 'installer_cli.py'
        shutil.copy2(REPO / 'installer/installer_cli.py', cli)
        args = self.target()
        args[args.index('install')] = 'uninstall'
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', str(cli), *args], env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('require --yes', result.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
