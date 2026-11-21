#!/bin/bash
# Run from an authenticated AWS CloudShell. Does not start the application.
set -euo pipefail
export AWS_PAGER=''
region=${AWS_REGION:-us-east-1}
stack=easydial-pilot
root=$(cd "$(dirname "$0")/../.." && pwd)
account=$(aws sts get-caller-identity --query Account --output text)
[ "$account" = 137420770749 ] || { echo 'Wrong AWS account; refusing deployment'; exit 1; }
commit=$(git -C "$root" rev-parse HEAD)
[ -z "$(git -C "$root" status --porcelain)" ] || { echo 'Use a clean published checkout'; exit 1; }
prefix=$(aws ec2 describe-managed-prefix-lists --region "$region" \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text)
[[ "$prefix" == pl-* ]] || { echo 'CloudFront prefix list not found'; exit 1; }
aws cloudformation deploy --region "$region" --stack-name "$stack" \
  --template-file "$root/deploy/aws/infrastructure.yaml" --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides SourceCommit="$commit" CloudFrontPrefixList="$prefix" InstanceType=t3.large
output() {
  aws cloudformation describe-stacks --region "$region" --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text
}
instance=$(output InstanceId)
repository=$(output ImageRepository)
providers=$(aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text)
create_provider=true
if [[ "$providers" == *oidc-provider/token.actions.githubusercontent.com* ]]; then create_provider=false; fi
# Preserve ownership on reruns: never remove the provider created by this stack.
existing=$(aws cloudformation list-stacks --region "$region" \
  --query "StackSummaries[?StackName=='easydial-github' && StackStatus!='DELETE_COMPLETE'].StackId | [0]" --output text)
if [ "$existing" != None ]; then
  create_provider=$(aws cloudformation describe-stacks --region "$region" --stack-name easydial-github \
    --query "Stacks[0].Parameters[?ParameterKey=='CreateGitHubProvider'].ParameterValue | [0]" --output text)
fi
aws cloudformation deploy --region "$region" --stack-name easydial-github \
  --template-file "$root/deploy/aws/github-oidc.yaml" --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides InfrastructureStack="$stack" WorkerInstanceId="$instance" \
    RepositoryName="${repository#*/}" CreateGitHubProvider="$create_provider"
aws cloudformation describe-stacks --region "$region" --stack-name "$stack" --query 'Stacks[0].Outputs' --output table
aws cloudformation describe-stacks --region "$region" --stack-name easydial-github --query 'Stacks[0].Outputs' --output table
echo 'Infrastructure ready. Populate the runtime secret and migrate state before enabling GitHub deployment.'
