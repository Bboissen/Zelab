#!/usr/bin/env python3
"""The seven supported Make commands."""

from console import heading


def main():
    heading('ZELAB COMMANDS')
    print('  Prefix each command with rtk proxy; run from the checkout.\n')
    print('  make          Install/reapply or resume; keep state and open the guide')
    print('  make clean    Reset repo choices and generated files; keep installed data')
    print('  make fclean   Preview, confirm, then remove this site and its application data')
    print('  make re       fclean, then install again')
    print('  make help     Show these commands')
    print('  make backup   Create and verify an encrypted application backup')
    print('  make restore  Select, verify and restore an encrypted backup, with confirmation')
    print('\n  clean/fclean reset local harness, model, resource, app and profile choices.')
    print('  Host connections and installed resource names/locations are kept.')
    print('  fclean/re also delete Zelab notes, tasks and application logins. Backups,')
    print('  personal harness logins, SSH/Tailscale and unrelated services are kept.')
    print('  Restore a file: make restore RESTORE_ARCHIVE=/absolute/path/backup.tar.gpg')
    print('  Restore needs an installed compatible site; run make first after fclean.')
    print('  On a laptop: make REMOTE_HOST=user@server REMOTE_DIR=/absolute/checkout')
    print('\n  Setup: docs/SETUP.md | Maintenance: docs/OPERATIONS.md\n')


if __name__ == '__main__':
    main()
