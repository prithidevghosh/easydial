#!/usr/bin/env python3
"""Explicit AWS deployment stages. Never deploy into an unverified account."""
import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = '137420770749'


def run(args, *, input=None, capture=True):
    result = subprocess.run(args, input=input, text=True, capture_output=capture, check=True)
    return result.stdout.strip() if capture else ''


def wait_for_command(aws, command_id, instance_id, *, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # list-command-invocations returns an empty list during SSM propagation.
        result = aws('ssm', 'list-command-invocations', '--command-id', command_id,
                     '--instance-id', instance_id, '--details')
        commands = result.get('CommandInvocations', [])
        if commands:
            status = commands[0]['Status']
            if status == 'Success':
                return
            if status not in {'Pending', 'InProgress', 'Delayed'}:
                raise RuntimeError(f'SSM release {command_id} failed: {status}; inspect SSM command output')
        time.sleep(10)
    raise TimeoutError(f'SSM release {command_id} did not finish; inspect AWS before retrying')


def verify_public_health(origin):
    for attempt in range(12):
        try:
            with urllib.request.urlopen(origin + '/health', timeout=10) as response:
                if json.load(response).get('status') == 'ok':
                    return
        except (OSError, ValueError):
            pass
        time.sleep(5)
    raise RuntimeError('Release ran but public HTTPS health failed; inspect CloudFront and origin')


def runtime_credentials(settings, allowed):
    data = {}
    for key in allowed:
        if hasattr(settings, key.lower()):
            value = getattr(settings, key.lower())
            data[key] = value.get_secret_value() if hasattr(value, "get_secret_value") else value
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['identity', 'provision', 'upload-secrets', 'publish', 'release', 'status'])
    parser.add_argument('--profile', default=None, help='Optional local profile; omit for GitHub OIDC/CloudShell')
    parser.add_argument('--region', default='ap-south-1')
    parser.add_argument('--stack', default='easydial-pilot')
    args = parser.parse_args()
    base = ['aws', '--region', args.region, '--no-cli-pager']
    if args.profile:
        base += ['--profile', args.profile]

    def aws(*parts):
        return json.loads(run([*base, *parts, '--output', 'json']))

    identity = aws('sts', 'get-caller-identity')
    if identity['Account'] != ACCOUNT:
        raise SystemExit('Refusing operation: AWS account does not match 137420770749')
    print(f"Verified AWS account {ACCOUNT}; region {args.region}")
    if args.action == 'identity':
        return
    commit = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
    if args.action in {'provision', 'publish'} and run(['git', '-C', str(ROOT), 'status', '--porcelain']):
        raise SystemExit('Commit deployment changes before provisioning or publishing an image')
    if args.action == 'provision':
        prefix = aws('ec2', 'describe-managed-prefix-lists', '--filters',
                     'Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing')
        prefix_id = prefix['PrefixLists'][0]['PrefixListId']
        run([*base, 'cloudformation', 'deploy', '--stack-name', args.stack,
             '--template-file', str(ROOT / 'deploy/aws/infrastructure.yaml'),
             '--capabilities', 'CAPABILITY_IAM', '--no-fail-on-empty-changeset',
             '--parameter-overrides', f'SourceCommit={commit}', f'CloudFrontPrefixList={prefix_id}'], capture=False)
    outputs = {x['OutputKey']: x['OutputValue'] for x in aws(
        'cloudformation', 'describe-stacks', '--stack-name', args.stack)['Stacks'][0]['Outputs']}
    if args.action == 'upload-secrets':
        sys.path.insert(0, str(ROOT / 'backend/src'))
        from easydial.settings import Settings
        spec = importlib.util.spec_from_file_location('runtime_env', ROOT / 'deploy/aws/runtime_env.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = Settings(_env_file=ROOT / 'backend/.env')
        data = runtime_credentials(settings, module.ALLOWED)
        module.render(data)  # Validate before any cloud write; output remains private.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            path.write_text(json.dumps(data)); path.chmod(0o600)
            aws('secretsmanager', 'put-secret-value', '--secret-id', outputs['SecretArn'],
                '--secret-string', f'file://{path}')
        print('Runtime credentials uploaded privately; no values printed.')
    if args.action == 'publish':
        repository = outputs['ImageRepository']
        name = repository.split('/', 1)[1]
        existing = aws('ecr', 'list-images', '--repository-name', name)
        if any(item.get('imageTag') == commit for item in existing['imageIds']):
            print('Immutable image already published; reusing this commit')
            return
        registry = repository.split('/')[0]
        password = run([*base, 'ecr', 'get-login-password'])
        run(['docker', 'login', '--username', 'AWS', '--password-stdin', registry], input=password, capture=True)
        run(['docker', 'build', '--platform', 'linux/amd64', '-f', str(ROOT / 'deploy/container/Dockerfile'),
             '-t', f'{repository}:{commit}', str(ROOT)], capture=False)
        run(['docker', 'push', f'{repository}:{commit}'], capture=False)
    if args.action == 'release':
        repository = outputs['ImageRepository']
        name = repository.split('/', 1)[1]
        digest = aws('ecr', 'describe-images', '--repository-name', name,
                     '--image-ids', f'imageTag={commit}')['imageDetails'][0]['imageDigest']
        command = (
            f"git -C /opt/easydial fetch origin {shlex.quote(commit)} && "
            f"git -C /opt/easydial checkout {shlex.quote(commit)} && "
            f"bash /opt/easydial/deploy/aws/release.sh {shlex.quote(repository+'@'+digest)} "
            f"{shlex.quote(outputs['PublicUrl'])}"
        )
        response = aws('ssm', 'send-command', '--instance-ids', outputs['InstanceId'],
                       '--document-name', 'AWS-RunShellScript',
                       '--parameters', json.dumps({'commands': [command], 'executionTimeout': ['600']}))
        command_id = response['Command']['CommandId']
        print('Deployment command:', command_id, flush=True)
        wait_for_command(aws, command_id, outputs['InstanceId'])
        verify_public_health(outputs['PublicUrl'])
        print('Remote release succeeded and public HTTPS health passed')
    print(json.dumps(outputs, indent=2))


if __name__ == '__main__':
    os.environ['AWS_PAGER'] = ''
    main()
