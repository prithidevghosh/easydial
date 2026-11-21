#!/bin/bash
set -euo pipefail
set -a
source /etc/easydial/host.env
source /etc/easydial/release.env
set +a
exec docker compose --project-name easydial -f /opt/easydial/deploy/aws/compose.yaml "$@"
