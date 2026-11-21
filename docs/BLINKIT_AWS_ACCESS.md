# Blinkit AWS access investigation — 22 November 2026

## Result

The deployment works, but Blinkit blocks the AWS worker before location selection or login. Do not label cloud shopping ready.

| Test | Browser | Result |
| --- | --- | --- |
| Native Chrome on the developer Mac | Headed Chrome 157.0.8103.80, fresh anonymous context | HTTP 200, location selection and catalogue visible |
| Exact deployment image on the developer Mac | Linux amd64, headed Chrome 158.0.8127.36 on Xvfb, fresh anonymous context | HTTP 200, location selection and catalogue visible |
| Same deployment image on EC2 in us-east-1 | Linux amd64, headed Chrome on Xvfb, fresh anonymous context | HTTP 403, Cloudflare access-denied page |

Image digest: `sha256:8343d1f56105f5f849a5509d37417fc458ab0b73043ff8805691fad686d28130`.

This comparison strongly implicates the AWS network/egress context rather than a missing Linux dependency or headed/headless configuration. It does not prove whether Blinkit's rule targets this IP, AWS hosting, US geography, or another network-related signal. Do not claim all AWS regions/IPs are blocked.

## Evidence from AWS

- URL: `https://blinkit.com/`
- HTTP 403; server `cloudflare`; page title `blinkit | Error Page`.
- Visible message: `access denied`, `sorry, you have been blocked!`.
- No verification iframe was present. This was not a location gate or interactive CAPTCHA.
- Outbound NAT IP: `184.193.85.164`.
- Cloudflare Ray ID: `a5e220667a5be6f2-IAD`.
- The page directs blocked visitors to `security@blinkit.com` with the incident and Ray ID.
- No phone number, OTP, authenticated cookies, order or payment was submitted during diagnosis.

## Resolution path

Request Blinkit approval/unblocking for the identified server and intended integration, or obtain a supported commerce integration. Re-run the same anonymous check after access is granted, then validate customer consent/login/address/search/cart/payment preparation end to end. Do not use IP rotation, residential proxies, fingerprint spoofing, or challenge bypass to get around this explicit denial.

No email was sent. Draft for the project owner:

> Subject: Request to review blocked access for EasyDial pilot
>
> Hello Blinkit Security,
>
> We are building EasyDial, a WhatsApp grocery assistant where customers explicitly consent to connecting their own accounts and confirm orders. Our AWS-hosted Chrome browser receives a Cloudflare 403 on your public homepage before login. The same container loads the homepage successfully on our local network.
>
> Could you review the block and advise whether this integration can be approved or whether a supported partner integration is available?
>
> AWS outbound IP: 184.193.85.164
> Cloudflare Ray ID: a5e220667a5be6f2-IAD
> URL: https://blinkit.com/
> Observed: 22 November 2026, approximately 01:04 IST
>
> Thank you.

## Reproduction

`backend/tools/check_store_access.py` makes one anonymous homepage navigation in headed Chrome, prints only diagnostic metadata and closes the browser. It does not read saved customer sessions or change any cart. Exit code 2 denotes observed access denial; 1 denotes another HTTP failure; 0 denotes a successful homepage response (not full ordering acceptance).

On a Mac with Chrome installed:

```sh
uv --directory backend run python tools/check_store_access.py
```

On the AWS worker, run the tool inside the existing container/display through SSM. Keep credentials and customer data out of command output. An HTTP 200 alone is insufficient for end-to-end acceptance.
