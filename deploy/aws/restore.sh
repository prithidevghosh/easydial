#!/bin/bash
# Restore only into an empty data volume while the application is stopped.
set -euo pipefail
umask 077
source /etc/easydial/host.env
key=${1:?S3 object key required}
[[ "$key" =~ ^(daily|migration)/[a-zA-Z0-9._-]+\.tgz$ ]] || exit 2
mountpoint -q /srv/easydial
if systemctl is-active --quiet easydial; then echo 'Stop EasyDial before restoring'; exit 1; fi
[ ! -e /srv/easydial/whatsapp.sqlite3 ] || { echo 'Refusing to overwrite an existing database'; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
aws s3 cp "s3://$BACKUP_BUCKET/$key" "$work/state.tgz" --region "$AWS_REGION" --only-show-errors
python3 - "$work/state.tgz" <<'PY'
import pathlib, sys, tarfile
root = pathlib.Path('/srv/easydial').resolve()
with tarfile.open(sys.argv[1]) as archive:
    for entry in archive.getmembers():
        path = pathlib.PurePosixPath(entry.name)
        if path.is_absolute() or '..' in path.parts or entry.issym() or entry.islnk() or entry.isdev():
            raise SystemExit('Unsafe archive member; restore refused')
        if path.parts and path.parts[0] not in {'.', 'customers', 'whatsapp.sqlite3'}:
            raise SystemExit('Unexpected archive member; restore refused')
    archive.extractall(root, filter='data')
PY
chown -R 10001:10001 /srv/easydial
chmod 700 /srv/easydial
echo 'State restored. Verify the matching session encryption key before starting EasyDial.'
