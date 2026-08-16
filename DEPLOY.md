# Deploying KEEL to production

## 1. Prerequisites
- A server (VM or container host) and a domain with TLS (Caddy/Nginx/Cloud LB).
- A payment account: **Razorpay** (India) or **Stripe** (elsewhere).

## 2. Configure
```bash
cp .env.example .env
# generate a signing secret:
echo "KEEL_SECRET_KEY=$(openssl rand -hex 32)" >> .env
# fill in RAZORPAY_* (or STRIPE_*), set KEEL_HTTPS=1
```
`KEEL_SECRET_KEY` signs sessions and licenses — set it once and keep it stable
(rotating it logs everyone out and invalidates licenses).

## 3. Run
```bash
docker compose up -d --build
# → http://SERVER:8347   (put a TLS proxy in front for https://your-domain)
```
Or Kubernetes: `helm install keel deploy/helm/keel --set env.KEEL_AUTH_REQUIRED=1`.
Health check: `GET /healthz`.

## 4. TLS proxy (example: Caddy)
```
your-domain.com {
    reverse_proxy 127.0.0.1:8347
}
```
Set `KEEL_HTTPS=1` so session cookies are marked Secure.

## 5. Payment webhooks
- Razorpay: Dashboard → Webhooks → `https://your-domain.com/api/billing/webhook/razorpay`,
  event `payment_link.paid`, secret = your `RAZORPAY_WEBHOOK_SECRET`.
- Stripe: `https://your-domain.com/api/billing/webhook`, event
  `checkout.session.completed`.

## 6. First run
- Visit `https://your-domain.com` → the marketing site.
- `/app` → **Create your account** (first signup), then log in.
- Each account is isolated: its agents, decisions, billing, and entitlements
  are its own. A user's payment unlocks Team features **for that account only**.
- Agents authenticate to the gateway with the account's API key
  (`Authorization: Bearer keel_ak_…`, shown in the console).

## 7. Backups
Back up the `/data` volume — it holds accounts, licenses, the signing key,
and the transparency ledger. Losing the signing key invalidates certificates
and sessions.

## What's enforced
- `KEEL_AUTH_REQUIRED=1` → the console requires login; the API requires a
  session cookie or an account API key.
- Team features (Slack/ticketing routing, full & scheduled evidence packs,
  HSM key mode) return HTTP 402 unless the account has an active paid license.
- Payments are verified by provider signature (Razorpay HMAC / Stripe webhook
  signature); forged "paid" callbacks are rejected.
