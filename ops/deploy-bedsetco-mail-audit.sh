#!/bin/sh
set -eu

PROGRAM_SOURCE=/tmp/bedsetco-codex-mail-audit.new
PUBLIC_KEY_SOURCE=/tmp/codex-mail-audit.pub
PROGRAM_TARGET=/usr/local/libexec/bedsetco-codex-mail-audit
AUTHORIZED_KEYS=/root/.ssh/authorized_keys
KEY_COMMENT=codex-bedsetco-mail-audit

cleanup() {
    rm -f -- "$PROGRAM_SOURCE" "$PUBLIC_KEY_SOURCE" /tmp/deploy-bedsetco-mail-audit.sh
}
trap cleanup EXIT HUP INT TERM

test "$(id -u)" -eq 0
test -f "$PROGRAM_SOURCE"
test -f "$PUBLIC_KEY_SOURCE"
test -f "$AUTHORIZED_KEYS"

python3 -m py_compile "$PROGRAM_SOURCE"

public_key=$(tr -d '\r\n' < "$PUBLIC_KEY_SOURCE")
set -- $public_key
test "$#" -eq 3
test "$1" = ssh-ed25519
test "$3" = "$KEY_COMMENT"

install -o root -g root -m 0755 "$PROGRAM_SOURCE" "$PROGRAM_TARGET"

if grep -Fq " $KEY_COMMENT" "$AUTHORIZED_KEYS"; then
    existing_key=$(awk -v comment="$KEY_COMMENT" '$NF == comment { print $(NF-1); exit }' "$AUTHORIZED_KEYS")
    test "$existing_key" = "$2"
else
    backup_path="${AUTHORIZED_KEYS}.backup-$(date -u +%Y%m%dT%H%M%SZ)"
    cp -a -- "$AUTHORIZED_KEYS" "$backup_path"
    printf '%s\n' "restrict,command=\"$PROGRAM_TARGET\" $public_key" >> "$AUTHORIZED_KEYS"
    chmod 0600 "$AUTHORIZED_KEYS"
fi

printf '%s\n' 'mail audit endpoint installed'
