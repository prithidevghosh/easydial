# Launch EasyDial in your AWS account

Account: **137420770749**. Region: **US East (N. Virginia), us-east-1** (matching the existing workshop console). If the organizer requires a different region, tell us before provisioning. This is a persistent single-worker pilot with ongoing EC2, NAT, IPv4, disk and storage charges. Confirm the account will remain available: workshop expiry can delete the infrastructure and its webhook hostname.

## Exact instance specification

| Setting | Value |
|---|---|
| Instance | `t3.large`, 2 vCPU, 8 GiB RAM, On-Demand |
| Operating system | Latest Amazon Linux 2023, **x86_64** |
| Root disk | 30 GiB gp3, encrypted |
| Application disk | Separate 30 GiB gp3, encrypted and retained |
| Network | Private subnet, no public IP; NAT for outgoing traffic |
| Incoming traffic | Port 80 from CloudFront's managed prefix list only |
| Administration | Systems Manager Session Manager; no SSH key required |
| Public endpoint | CloudFront HTTPS; caching disabled |

Use the supplied CloudFormation stack to create this configuration together. Launching an isolated EC2 instance through the EC2 wizard will not provide the required storage mounts, permissions, private origin or HTTPS endpoint.

## 1. Create the instance and supporting services

1. Sign into the AWS console. Confirm account **137420770749** in the top-right account menu.
2. Select **US East (N. Virginia)** in the region selector.
3. Open **CloudShell** using the terminal icon in the console toolbar. This uses your console session, so local CLI login and MCP authorization are unnecessary.
4. Paste these commands:

   ```bash
   git clone https://github.com/prithidevghosh/easydial.git easydial-deploy
   cd easydial-deploy
   export AWS_REGION=us-east-1
   bash deploy/aws/provision-console.sh
   ```

   If the directory already exists, enter it and run `git pull --ff-only` before the last two commands. Keep the shell open until the command finishes. CloudFront can take 15–30 minutes. Monitor **CloudFormation → Stacks → easydial-pilot → Events**.

5. The script creates `easydial-pilot` and `easydial-github`. Both should reach `CREATE_COMPLETE` (or `UPDATE_COMPLETE`). It prints the URLs, instance ID, secret ARN and GitHub deployment role ARN. These identifiers are safe to share; credentials are not.
6. In **EC2 → Instances**, select `easydial-pilot-worker` → **Connect → Session Manager → Connect**. Run:

   ```bash
   sudo test -f /etc/easydial/bootstrap-complete && echo 'Bootstrap complete'
   sudo lsblk
   ```

   The application disk must be mounted at `/srv/easydial`. If bootstrap is not complete, inspect `sudo tail -n 80 /var/log/cloud-init-output.log`.

If CloudShell, IAM role creation, `iam:PassRole`, OIDC provider creation or a service is denied, share the error. A workshop administrator must grant the required deployment permissions; spinning up EC2 alone cannot replace them. Do not delete a partially created stack before inspecting its events.

The script does **not** start the app or change the Meta webhook.

## 2. Set runtime credentials and migrate local state

Keep the local service running while infrastructure is created. Before the first cloud release:

- In **Secrets Manager**, open the secret identified by the stack's `SecretArn` output. Choose **Retrieve secret value → Edit → Plaintext** and save a JSON object containing the allowed values from the local `backend/.env`. Required: `OPENAI_API_KEY`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_VERIFY_TOKEN`, `META_APP_SECRET`, `CUSTOMER_SESSION_KEY`, `WHATSAPP_BUSINESS_NUMBER`, `WHATSAPP_SUPPORT_CONTACT`. Optional runtime/model settings are listed in `deploy/aws/runtime_env.py`. Preserve the exact existing customer encryption key. Do not add local paths or the temporary tunnel URL.
- Finish any in-flight messages, OTPs and payment/order checks. Stop the local worker, export its state, upload the private archive to the stack's backup bucket, and restore it through Session Manager. Follow the migration sequence in [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md). We can do the local export together once the instance is ready.
- Never run the local and AWS processing workers against the same WhatsApp account simultaneously. Leave automatic deployment disabled until the state and secret are ready.

## 3. Enable GitHub deployment

In [repository Actions variables](https://github.com/prithidevghosh/easydial/settings/variables/actions), create **repository variables** (not secrets):

| Name | Value |
|---|---|
| `AWS_REGION` | `us-east-1` |
| `AWS_STACK` | `easydial-pilot` |
| `AWS_DEPLOY_ROLE_ARN` | `GitHubRoleArn` from `easydial-github` Outputs |
| `AWS_DEPLOY_ENABLED` | `true` only after step 2 is complete |

No AWS access key or secret key goes into GitHub. The role accepts only this repository's `main` branch. It can push to this ECR repository and run deployment commands on this instance. SSM command access permits executing code on the worker, so only trusted maintainers should have write access to `main`. Runtime secrets are not supplied to the build job.

Open **Actions → AWS deployment → Run workflow → main → Run workflow**. The workflow runs backend lint/tests, validates both CloudFormation templates, builds Linux amd64, tests headed/headless Chrome, publishes the commit image to ECR, deploys through SSM, waits for the command result, and verifies the public HTTPS health endpoint. Subsequent pushes to `main` repeat this process. PRs only validate. Releases are serialized; a newer push never cancels a release already running.

The workflow is present but cloud deployment is skipped while `AWS_DEPLOY_ENABLED` is unset. Provisioning is a one-time console operation; Actions does not create or delete infrastructure. Updates to bootstrap/systemd/infrastructure require a separate reviewed infrastructure change.

## 4. Switch the webhook and verify real usage

After the first workflow succeeds:

1. Open the stack's `PublicUrl` followed by `/health`; expect `"status":"ok"`.
2. In the Meta app's WhatsApp webhook configuration, set Callback URL to the stack's **WebhookUrl**. Keep the existing verification token and the `messages` subscription.
3. Send a real WhatsApp text and voice note. Validate grocery login, address selection, search and cart review from AWS. Existing local merchant sessions may need login again after the IP change. Instamart's existing login limitation is not fixed by hosting on AWS.
4. Keep the local worker stopped after cutover.

The webhook hostname stays fixed through application releases and restarts. Deleting/replacing CloudFront or workshop cleanup can change it.

## Failure and recovery

A failed SSM command or public health check fails the workflow; command submission alone is not success. If container startup fails, the release script attempts to restore the previous image. Database/schema changes may require restoring a backup too. A public HTTPS failure needs CloudFront/origin inspection and does not automatically roll back a locally healthy image.

To pause automatic releases, set `AWS_DEPLOY_ENABLED=false`. This does not stop the running app. To roll back code, revert the offending change on `main` and push; this creates a new, tested release. Do not restore an old database over newer customer transactions without reconciling them.

Reference: [AWS T3 specifications](https://aws.amazon.com/ec2/instance-types/t3/), [GitHub OIDC for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), [CloudFront VPC origins](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html).
