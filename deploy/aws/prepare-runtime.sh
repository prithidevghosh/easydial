#!/bin/bash
set -euo pipefail
umask 077
source /etc/easydial/host.env
mountpoint -q /srv/easydial
install -d -m 700 /run/easydial
aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$SECRET_ARN" \
  --query SecretString --output text | python3 /opt/easydial/deploy/aws/runtime_env.py /run/easydial/runtime.env
