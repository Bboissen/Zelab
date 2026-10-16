#!/usr/bin/env python3
"""The nine supported Make commands."""

from console import heading


def main():
    heading('ZELAB COMMANDS')
    print('  Run these commands from the checkout.\n')
    print('  make          Install/reapply or resume; keep state and open the guide')
    print('  make clean    Reset repo choices and generated files; keep installed data')
    print('  make fclean   Preview, confirm, then remove this site and its application data')
    print('  make re       fclean, then install again')
    print('  make help     Show these commands')
    print('  make backup   Create and verify an encrypted application backup')
    print('  make restore  Select, verify and restore an encrypted backup, with confirmation')
    print('  make bot      Add, set up or remove Hermes bots (terminal only)')
    print('  make notes    Sync the server vault with its private Git remote (docs/NOTES.md)')
    print('\n  clean/fclean reset local resource and app choices.')
    print('  Host connections and installed resource names/locations are kept.')
    print('  fclean/re also delete Zelab notes, tasks and application logins. Backups,')
    print('  config/projects.yaml, SSH/Tailscale and unrelated services are kept.')
    print('  Generated checkout builds/caches are cleared; fclean also removes owned')
    print('  qualification builds and VMs shown in its preview, retaining reports/bundles.')
    print('  Restore a file: make restore RESTORE_ARCHIVE=/absolute/path/backup.tar.gpg')
    print('  Restore needs an installed compatible site; run make first after fclean.')
    print('  On a laptop: make REMOTE_HOST=user@server REMOTE_DIR=/absolute/checkout')
    print('\n  Setup: docs/SETUP.md | Maintenance: docs/OPERATIONS.md\n')


if __name__ == '__main__':
    main()
