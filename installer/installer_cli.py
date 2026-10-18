#!/usr/bin/env python3
"""Explicit, non-interactive headless access to the shared installer backend."""

import argparse
import json
from pathlib import Path
import sys
import traceback

from installer_core import Installer, discover_cards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', action='store_true', required=True)
    parser.add_argument('--roms', type=Path, required=True)
    parser.add_argument('--action', choices=('install', 'update', 'uninstall'), required=True)
    parser.add_argument('--yes', action='store_true', help='Confirm changes, including deletion of chats on uninstall')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--es-cfg', type=Path, default=Path('/etc/emulationstation/es_systems.cfg'))
    parser.add_argument('--themes', type=Path)
    args = parser.parse_args()
    if not args.dry_run and not args.yes:
        parser.error('Changes require --yes; uninstall deletes settings, chats, history and models on the selected card.')
    base = Path(__file__).resolve().parent
    source = args.source or (base / 'bundle' if (base / 'bundle/app').is_dir() else None)
    worker = Installer(args.roms, source_dir=source, es_cfg=args.es_cfg, themes=args.themes,
                       emit=lambda event: print(json.dumps(event), flush=True))
    if args.dry_run:
        try:
            print(json.dumps(discover_cards(), indent=2))
            worker.validate_card()
        except Exception as error:
            print('Dry-run failed: {}'.format(error), file=sys.stderr)
            return 1
        print('DRY RUN: {} on {}. No files written or downloaded.'.format(args.action, worker.prefix))
        if args.action == 'uninstall':
            print('WARNING: uninstall would delete settings, chats, history and models on this card.')
        return 0
    try:
        with (base / 'installer-cli.log').open('a', encoding='utf-8') as log:
            def emit(event):
                line = json.dumps(event)
                print(line, flush=True)
                log.write(line + '\n')
                log.flush()
            worker.emit = emit
            try:
                worker.run(args.action)
            except Exception:
                traceback.print_exc(file=log)
                raise
    except KeyboardInterrupt:
        print('Interrupted. Partial downloads are retained for retry.', file=sys.stderr)
        return 130
    except Exception as error:
        print('Installer failed: {}\nLog: {}'.format(error, base / 'installer-cli.log'), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
