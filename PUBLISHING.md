# Publishing KEEL & enabling real payments

## Publish the package (PyPI)
```bash
pip install build twine
python -m build                    # builds dist/keel-<version>.whl + .tar.gz
twine upload dist/*                # needs a PyPI account + token
```
After that, anyone can `pip install keel`.

## Source (GitHub)
Push the repo; the certificate schema and reference verifier being public is
part of the standards play. A CI workflow is in `.github/workflows/ci.yml`.

## Enable real payments

KEEL auto-selects the provider: **Razorpay** (India) when its keys are set,
**Stripe** otherwise. Set `KEEL_PAYMENT_PROVIDER` to force one.

### India — Razorpay (UPI, cards, netbanking)
1. Create a Razorpay account; get Key ID + Key Secret (Settings → API Keys).
2. Set on the KEEL server:
   ```bash
   export RAZORPAY_KEY_ID=rzp_live_xxx
   export RAZORPAY_KEY_SECRET=xxx
   export RAZORPAY_WEBHOOK_SECRET=xxx        # any strong secret you also set below
   export KEEL_PRICE_INR=830                 # ~$10; change freely
   ```
3. Razorpay Dashboard → Settings → Webhooks → add
   `https://YOUR_HOST/api/billing/webhook/razorpay`, event `payment_link.paid`,
   with the same secret.
4. Done. **Upgrade · Team** opens a Razorpay payment link for ₹830 (UPI/cards);
   on payment the Team plan activates automatically. Test with Razorpay test
   keys and their test UPI / card `4111 1111 1111 1111`.

### Elsewhere — Stripe
1. Stripe account → keys. 2. `export STRIPE_SECRET_KEY=... STRIPE_WEBHOOK_SECRET=...`
3. Stripe Dashboard webhook `https://YOUR_HOST/api/billing/webhook`, event
   `checkout.session.completed`. 4. Test card `4242 4242 4242 4242`.

## Local evaluation (no Stripe)
The console's **Activate (dev / evaluation)** button unlocks Team locally using
the deployment unlock code (`KEEL_UNLOCK_CODE`, default `DEV-UNLOCK`). This is
for trying the features, not a paid record.

## What "paid" enforces
- Managed/cloud deployment: the license is issued and verified server-side —
  the paywall is enforced.
- Self-hosted OSS: the operator holds the signing key, so the gate is a license
  mechanism; the paid value (managed hosting, HSM key custody, support) is what
  self-hosting doesn't provide anyway.
