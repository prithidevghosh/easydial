# AWS deployment handoff — 22 November 2026

Target account: **137420770749**. No other account is authorized. `deploy.py` verifies STS identity before every cloud operation; CloudFormation also enforces the account ID.

For console provisioning and GitHub Actions, follow [AWS_QUICKSTART.md](AWS_QUICKSTART.md).

## Current status

Deployment packaging and infrastructure are being prepared and validated locally. AWS resources and a public URL must not be claimed until account access, stack creation and remote acceptance pass. The account screenshot shows `WSParticipantRole`, suggesting workshop/federated access rather than an established IAM Identity Center portal. The console's displayed region is `us-east-1`; verify the workshop's permitted regions and account expiry before provisioning. A workshop cleanup can remove infrastructure and invalidate its URL.

Instamart login remains unverified and can return a generic merchant error even in visible Chrome after locality selection. That unresolved provider issue is not fixed by deploying. Blinkit is the primary end-to-end acceptance target. AWS-IP/browser acceptance is a separate live test.

## Deployment shape

- One x86 EC2 `t3.large` worker (8 GiB), one application process and one SQLite owner. No autoscaling or duplicate consumers. This is a persistent pilot, not a horizontally scalable production cluster.
- Private subnet, no public instance IP, no SSH ingress. Outbound merchant/OpenAI/Meta requests use a NAT Gateway with a stable public IP.
- CloudFront's AWS-provided `https://<distribution>.cloudfront.net` hostname gives a stable HTTPS origin without buying a domain. VPC origin connects privately to EC2; only CloudFront origin traffic reaches port 80. Dynamic routes have caching disabled, including error responses.
- Separate encrypted 30 GiB gp3 EBS data volume retained on stack deletion/replacement. SQLite and per-customer encrypted session files live under `/srv/easydial`, mounted at `/data`.
- Secrets Manager contains runtime credentials, including the existing customer encryption key. No credentials in the image, Git, CloudFormation parameters, user data, command history, public logs or startup output.
- ECR immutable commit tags; releases run by image digest. The Python environment uses the existing frozen `uv.lock`. Chrome stable is captured in the built image; publishing a new image may update Chrome and must rerun acceptance.
- Non-root container; Chrome headed mode runs on Xvfb for Blinkit, matching the locally configured behavior. Other workers retain their current global headless default. No stealth flags or challenge bypass are added.
- AWS Systems Manager is the administration path. Container logs go to CloudWatch with 14-day retention; HTTP access logging is disabled to keep verification tokens/payment-link URLs out of logs.
- Daily consistent snapshot of database + encrypted sessions into a private encrypted/versioned S3 bucket. Backup briefly stops the single worker at 03:30 India time, so OTP challenges then need restarting. Daily objects expire after 30 days. Database and encryption key must both be recoverable.

This design incurs ongoing EC2, NAT Gateway, EBS, public IPv4, CloudFront, storage and logging charges. Check the AWS Pricing Calculator/workshop credit limit before creating it. NAT and EC2 are the main always-on components. No price estimate is represented as a quote.

## Account authentication

AWS CLI v2 is installed locally. For the workshop/federated console session:

```sh
aws login --profile easydial --region us-east-1
```

Approve the correct account in the browser if the role permits `SignInLocalDevelopmentAccess`. If denied, use the workshop page's **Get AWS CLI credentials** and configure its temporary access key, secret and session token locally under profile `easydial`. Never paste them into chat or commit them. A session token is required for these temporary credentials. Do not create root or permanent access keys for this task.

If it actually is IAM Identity Center, use `aws configure sso --profile easydial` with the portal URL and Identity Center region, then `aws sso login --profile easydial`.

Verify:

```sh
uv --directory backend run python ../deploy/aws/deploy.py identity --profile easydial --region us-east-1
```

The role needs CloudFormation, VPC/EC2/EBS/NAT, IAM role creation and PassRole, ECR, Secrets Manager, CloudFront including VPC origins/service-linked-role creation, CloudWatch Logs/alarms, S3, and SSM command permissions. A workshop policy may prohibit some of these. Do not silently widen IAM permissions or work around an explicit denial.

## Prepare, publish and stage

Run from the repository root after committing and pushing the deployment revision. These stages are intentionally explicit; provisioning costs money and is never triggered by tests.

```sh
uvx cfn-lint deploy/aws/infrastructure.yaml
docker build --platform linux/amd64 -f deploy/container/Dockerfile -t easydial:aws-check .
uv --directory backend run python ../deploy/aws/deploy.py provision --profile easydial --region us-east-1
uv --directory backend run python ../deploy/aws/deploy.py upload-secrets --profile easydial --region us-east-1
uv --directory backend run python ../deploy/aws/deploy.py publish --profile easydial --region us-east-1
```

Provisioning outputs the instance ID, immutable repository, private secret ARN, backup bucket and HTTPS URLs. CloudFront VPC origin deployment may take approximately 15 minutes. Before release, check EC2's cloud-init log through Session Manager and `/etc/easydial/bootstrap-complete`. No application starts until a release is selected and required secrets validate.

`upload-secrets` reads only allowlisted fields from `backend/.env` into a permission-0600 temporary JSON file and uploads it to Secrets Manager, then deletes the temporary file. `WHATSAPP_PUBLIC_URL` and `/data` paths are supplied by deployment, not copied from localhost. Use a durable Meta token; deploying does not extend an expiring token.

## Migration and webhook cutover (one active host)

1. Finish live payment/order attempts and drain inbound/outbound messages. Do not migrate mid-OTP or mid-submission.
2. Stop the local WhatsApp server. Do not stop it while still preparing AWS infrastructure; keep the working service available until cutover.
3. Export the stopped pilot:

   ```sh
   uv --directory backend run python ../deploy/aws/export_state.py ../.state/aws-migration.tgz
   ```

   The exporter takes the application's file lock, rejects queued/in-flight messages and active order checks, takes a SQLite backup, preserves encrypted customer sessions, and normalizes the multiuser database binding away from Mac-specific legacy profile paths. It never exports the encryption key. The same key must be present in Secrets Manager.
4. Upload the private archive with `aws s3 cp` to the stack's backup bucket under `migration/<timestamp>.tgz`, specifying SSE-S3. On EC2, while the application is stopped and data volume empty, run `sudo /opt/easydial/deploy/aws/restore.sh migration/<timestamp>.tgz`. It rejects overwriting an existing database or unsafe archive paths.
5. Release:

   ```sh
   uv --directory backend run python ../deploy/aws/deploy.py release --profile easydial --region us-east-1
   ```

   The deployment command waits for SSM success and public HTTPS health. Inspect the printed SSM command ID if it fails. The release waits for application health and restores the previous image if startup fails. A schema migration may require a data backup restore as well; image rollback alone is not guaranteed backward compatibility.
6. Verify public `/health`, signature rejection, verification challenge and `/join/qr.png` before routing customers.
7. In Meta, change the callback URL to the stack's `WebhookUrl`, retaining the existing verification token. Verify `messages` remains subscribed. The URL is stable across normal process restarts and image deployments; replacing/deleting the CloudFront distribution changes it.
8. Send one new WhatsApp message and confirm receipt and delivery from AWS. Keep the local worker stopped. Reusing the same Meta app with two processing hosts can duplicate effects.
9. Revalidate the migrated merchant sessions from the actual AWS worker. Sessions may be device/IP-sensitive; reconnect through the explicit-consent WhatsApp onboarding if rejected. Do not claim local cookies guarantee cloud login.

If cutover fails, stop the AWS worker before restoring the Meta callback/local worker. If AWS processed customer changes, migrate its latest consistent state back; do not restart a stale local database and replay an old basket/payment.

## Browser recovery without public VNC

No VNC daemon runs by default. For a supervised inspection, use SSM to start `x11vnc` inside the running container on display `:99`, listening on container port 5901. Docker binds that port to **127.0.0.1 on EC2 only**. Then use `AWS-StartPortForwardingSession` to port 5901 and a local VNC viewer. Access requires AWS SSM authorization; stop x11vnc afterward. Never open port 5901 or 22 to the internet. Never copy a developer's personal Chrome profile into the image.

## Release acceptance (not complete until observed)

- [ ] Correct AWS account/region and workshop resource lifetime confirmed.
- [ ] CloudFormation stack, SSM access, EBS mount, CloudFront origin and TLS validated.
- [ ] Cloud secrets validated; no local paths or secrets in image/logs.
- [ ] Health succeeds after EC2/container restart; state and consent persist.
- [ ] Valid Meta webhook signature accepted, invalid signature rejected; verification query preserved by CloudFront.
- [ ] Text/voice WhatsApp messages produce ordered English replies from AWS.
- [ ] Blinkit consent/login/OTP, saved-address selection, multi-item search/choice/cart review work from AWS.
- [ ] Read-only provider checks and user-authorized cart clear succeed; no dummy fallback.
- [ ] QR image/expiry and COD confirmation preserve once-only submission; any actual paid/COD order requires the user's explicit approval.
- [ ] Status checks read live history without navigating the payment tab.
- [ ] Logout removes per-user sessions; a second test customer remains isolated.
- [ ] Backup upload and restore into a separate empty volume tested; encryption key recovery documented.
- [ ] Configure actionable alarm notifications before public launch. The included EC2 alarm has no notification subscription yet.
- [ ] Meta test-number recipient restrictions/business onboarding reviewed before sharing the QR publicly.

## Sources

- [CloudFront private VPC origins and prerequisites](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html)
- [CloudFormation VPC origin resource](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-cloudfront-vpcorigin.html)
- [EC2 Session Manager access](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-with-systems-manager-session-manager.html)
- [AWS CLI browser login for console/federated identities](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
