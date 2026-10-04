#!/usr/bin/env python3
"""Restore the existing restricted mail wrapper to sales@bedsetco.com.

Run on the mail server as root after restoring its administrator connection.
This script does not send messages or change SSH, SMTP, or mailbox settings.
"""

import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


WRAPPER = Path('/usr/local/libexec/bedsetco-codex-mailer-send')
ORIGINAL_BACKUP = Path('/root/bedsetco-codex-mailer-send-20260926')
OUTREACH_SHA256 = 'd4bfdf7aff41bc02e76bd9f38f871d183441b454386c3c4eff4dfac818a32e18'
SALES_SHA256 = 'c9c3d03ab575f21e06ad79e284206aa3a251dcb54b245f17f303c7df99600fe3'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare_candidate(current, original_backup=None):
    current_hash = digest(current)
    if current_hash == SALES_SHA256:
        return current
    if current_hash != OUTREACH_SHA256:
        raise RuntimeError('Wrapper differs from the verified version; review it before changing it.')
    candidate = current.replace(b'outreach@bedsetco.com', b'sales@bedsetco.com')
    if original_backup is not None:
        if digest(original_backup) != SALES_SHA256:
            raise RuntimeError('The original backup does not match its verified sales checksum.')
        candidate = original_backup
    if digest(candidate) != SALES_SHA256:
        raise RuntimeError('Restored wrapper does not match the verified sales version.')
    tree = ast.parse(candidate.decode('utf-8'), filename=str(WRAPPER))
    senders = [
        statement.value.value
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'SALES' for target in statement.targets)
        and isinstance(statement.value, ast.Constant)
    ]
    if senders != ['sales@bedsetco.com']:
        raise RuntimeError('Restored wrapper must contain exactly one sales sender constant.')
    return candidate


def atomic_write(path, raw, original_stat):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.migration-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fchown(stream.fileno(), original_stat.st_uid, original_stat.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(original_stat.st_mode))
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if os.name != 'posix' or os.geteuid() != 0:
        raise RuntimeError('Run this script as root on the BedSetCo mail server.')
    metadata = WRAPPER.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise RuntimeError('The wrapper must be a regular root-owned file without group/world write access.')
    current = WRAPPER.read_bytes()
    original = None
    if ORIGINAL_BACKUP.exists():
        backup_metadata = ORIGINAL_BACKUP.lstat()
        if not stat.S_ISREG(backup_metadata.st_mode) or backup_metadata.st_uid != 0:
            raise RuntimeError('The original backup must be a regular root-owned file.')
        original = ORIGINAL_BACKUP.read_bytes()
    candidate = prepare_candidate(current, original)
    backup_path = None
    if candidate != current:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_path = Path('/root') / ('bedsetco-codex-mailer-send-before-sales-' + stamp + '.bak')
        with backup_path.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(current)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck immediately before replacement, keeping the existing controls intact.
        if WRAPPER.read_bytes() != current:
            raise RuntimeError('Wrapper changed during preparation; no replacement was made.')
        atomic_write(WRAPPER, candidate, metadata)
        if digest(WRAPPER.read_bytes()) != SALES_SHA256:
            atomic_write(WRAPPER, current, metadata)
            raise RuntimeError('Readback checksum failed; restored the previous wrapper.')
    print(json.dumps({
        'wrapper_updated': candidate != current,
        'sender': 'sales@bedsetco.com',
        'sha256': digest(WRAPPER.read_bytes()),
        'backup': str(backup_path) if backup_path else None,
        'message_sent': False,
        'local_send_gate_remains_paused': True,
    }))


if __name__ == '__main__':
    main()
