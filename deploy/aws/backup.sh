#!/bin/bash
set -euo pipefail
umask 077
source /etc/easydial/host.env
[ -f /etc/easydial/release.env ] || exit 0
mountpoint -q /srv/easydial
# Stop briefly to take one consistent SQLite + encrypted-session snapshot.
# Never back up runtime.env or Chrome inspection screenshots.
work=$(mktemp -d)
restart=false
cleanup() {
  if $restart; then systemctl start easydial; fi
  rm -rf "$work"
}
trap cleanup EXIT
if systemctl is-active --quiet easydial; then restart=true; systemctl stop easydial; fi
tar -C /srv/easydial --exclude='*.lock' -czf "$work/state.tgz" .
if $restart; then systemctl start easydial; restart=false; fi
aws s3 cp "$work/state.tgz" "s3://$BACKUP_BUCKET/daily/$(date -u +%Y-%m-%dT%H-%M-%SZ).tgz" --region "$AWS_REGION" --sse AES256 --only-show-errors
echo 'EasyDial backup uploaded'
