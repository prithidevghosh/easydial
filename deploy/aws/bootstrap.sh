#!/bin/bash
set -euo pipefail
umask 077
source /etc/easydial/host.env
command -v aws >/dev/null || dnf install -y awscli2
version=v2.39.4
install -d /usr/local/lib/docker/cli-plugins
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl -fsSL "https://github.com/docker/compose/releases/download/$version/docker-compose-linux-x86_64" -o "$work/docker-compose-linux-x86_64"
curl -fsSL "https://github.com/docker/compose/releases/download/$version/checksums.txt" -o "$work/checksums.txt"
(cd "$work" && grep -E ' [*]?docker-compose-linux-x86_64$' checksums.txt | sha256sum -c -)
install -m 755 "$work/docker-compose-linux-x86_64" /usr/local/lib/docker/cli-plugins/docker-compose
serial=${DATA_VOLUME_ID//-/}
device="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_$serial"
for attempt in $(seq 1 60); do
  [ -b "$device" ] && break
  sleep 5
done
[ -b "$device" ] || { echo 'Data volume did not attach'; exit 1; }
if ! blkid "$device" >/dev/null; then mkfs.ext4 -L easydial-data "$device"; fi
install -d /srv/easydial
uuid=$(blkid -s UUID -o value "$device")
grep -q "UUID=$uuid " /etc/fstab || echo "UUID=$uuid /srv/easydial ext4 defaults,nofail 0 2" >> /etc/fstab
mount /srv/easydial
chown 10001:10001 /srv/easydial
chmod 700 /srv/easydial
chmod 755 /opt/easydial/deploy/aws/*.sh
cat > /etc/systemd/system/easydial.service <<'UNIT'
[Unit]
Description=EasyDial WhatsApp worker (single process)
After=network-online.target docker.service
Requires=docker.service
RequiresMountsFor=/srv/easydial
ConditionPathExists=/etc/easydial/release.env
[Service]
Type=simple
WorkingDirectory=/opt/easydial/deploy/aws
ExecStartPre=/opt/easydial/deploy/aws/prepare-runtime.sh
ExecStart=/opt/easydial/deploy/aws/run.sh up --remove-orphans
ExecStop=/opt/easydial/deploy/aws/run.sh down --timeout 90
Restart=on-failure
RestartSec=10
TimeoutStartSec=300
TimeoutStopSec=120
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/easydial-backup.service <<'UNIT'
[Unit]
Description=Consistent encrypted-storage EasyDial backup
RequiresMountsFor=/srv/easydial
[Service]
Type=oneshot
ExecStart=/opt/easydial/deploy/aws/backup.sh
UNIT
cat > /etc/systemd/system/easydial-backup.timer <<'UNIT'
[Unit]
Description=Daily EasyDial backup at 03:30 India time
[Timer]
OnCalendar=*-*-* 22:00:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable easydial.service
systemctl enable --now easydial-backup.timer
touch /etc/easydial/bootstrap-complete
