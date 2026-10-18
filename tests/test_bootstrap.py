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
        dialog = self.bin / 'dialog'
        dialog.write_text('#!/bin/sh\nexit 0\n')
        dialog.chmod(0o755)
        self.env['PATH'] = str(self.bin) + os.pathsep + self.env['PATH']
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
        if repo:
            (self.base / 'app').mkdir()
        return folder

    def test_help_standalone_no_writes_or_sdl(self):
        before = self.snapshot()
        result = self.run_sh('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--cli', result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_old_entry_forwards_arguments_to_canonical_installer(self):
        legacy = self.base / 'legacy.sh'
        shutil.copy2(REPO / 'install.sh', legacy)
        (self.base / 'install227ai.sh').write_text('#!/bin/sh\nexec python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "$@"\n')
        result = self.run_sh('--source', 'path with spaces', '--desktop', script=legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['--source', 'path with spaces', '--desktop'])

    def test_old_download_entry_fetches_new_name_and_preserves_arguments(self):
        legacy = self.base / 'legacy.sh'
        shutil.copy2(REPO / 'install.sh', legacy)
        curl = self.bin / 'curl'
        curl.write_text('''#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then OUT=$2; shift 2; else URL=$1; shift; fi
done
case "$URL" in */main/install227ai.sh) ;; *) exit 99 ;; esac
printf '%s\\n' '#!/bin/sh' 'exec python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "$@"' > "$OUT"
''')
        curl.chmod(0o755)
        result = self.run_sh('--source', 'path with spaces', '--desktop', script=legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['--source', 'path with spaces', '--desktop'])

    def test_old_download_entry_stops_on_transfer_failure(self):
        legacy = self.base / 'legacy.sh'
        shutil.copy2(REPO / 'install.sh', legacy)
        curl = self.bin / 'curl'
        curl.write_text('#!/bin/sh\nexit 22\n')
        curl.chmod(0o755)
        result = self.run_sh('--help', script=legacy)
        self.assertEqual(result.returncode, 22)

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
        self.env['PROJECT_AI_RAW_BASE'] = payload.parent.as_uri()
        return first, second, script, payload

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
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

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
    def test_incomplete_download_does_not_publish_modules(self):
        first, _, script, payload = self.fresh_cards()
        (payload / 'installer_ui.py').write_text('not valid Python !!!')
        result = self.run_sh(script=script)
        self.assertNotEqual(result.returncode, 0)
        manager = first / 'tools/227-ai-manager'
        self.assertFalse(list(manager.glob('*.py')))
        self.assertFalse(list(manager.glob('.bootstrap.*')))
        self.assertIn('SyntaxError', (manager / 'bootstrap.log').read_text())

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
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

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
    def test_standalone_bootstrap_uses_mounted_fallback(self):
        first, second, script, _ = self.fresh_cards()
        shutil.copy2(script, self.script)
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((second / 'tools/227-ai-manager/installer_ui.py').is_file())
        self.assertFalse((first / 'tools/227-ai-manager').exists())

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
    def test_readonly_tools_card_is_skipped_for_fresh_bootstrap(self):
        first, second, script, _ = self.fresh_cards()
        (self.base / 'mounts').write_text('/dev/one {} ext4 ro 0 0\n/dev/two {} ext4 rw 0 0\n'.format(first, second))
        result = self.run_sh(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((first / 'tools/227-ai-manager').exists())
        self.assertTrue((second / 'tools/227-ai-manager/installer_ui.py').is_file())

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
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
