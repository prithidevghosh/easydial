#!/bin/bash
set -euo pipefail
umask 077
source /etc/easydial/host.env
image=${1:?Image digest required}
origin=${2:?HTTPS CloudFront origin required}
[[ "$image" =~ ^137420770749\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-zA-Z0-9/_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
[[ "$origin" =~ ^https://[a-z0-9]+\.cloudfront\.net$ ]] || exit 2
[ -f /etc/easydial/bootstrap-complete ]
registry=${image%%/*}
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$registry"
docker pull "$image"
if [ -f /etc/easydial/release.env ]; then cp /etc/easydial/release.env /etc/easydial/previous-release.env; fi
printf 'EASYDIAL_IMAGE=%s\nEASYDIAL_PUBLIC_URL=%s\n' "$image" "$origin" > /etc/easydial/release.env
systemctl restart easydial || true
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1/health | python3 -c 'import sys,json; sys.exit(json.load(sys.stdin).get("status") != "ok")'; then
    echo 'EasyDial runtime healthy'; exit 0
  fi
  sleep 3
done
echo 'New release failed health check; restoring previous image if available'
if [ -f /etc/easydial/previous-release.env ]; then
  cp /etc/easydial/previous-release.env /etc/easydial/release.env
  systemctl restart easydial
else
  systemctl stop easydial
fi
exit 1
