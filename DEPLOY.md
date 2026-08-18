# Deploying KEEL to production

KEEL is free. There is no payment provider to configure and nothing to
activate — this is only about running it safely.

## 1. Prerequisites
- A server (VM or container host) and a domain with TLS (Caddy/Nginx/Cloud LB).

## 2. Configure

```bash
cp .env.example .env
echo "KEEL_SECRET_KEY=$(openssl rand -hex 32)" >> .env   # signs sessions
echo "KEEL_SIGNING_KEY_PEM=\"$(keel keygen --quiet)\"" >> .env
echo "KEEL_HTTPS=1" >> .env
echo "KEEL_AUTH_REQUIRED=1" >> .env
```

### The two keys, and why they are different

| Variable | Signs | If you lose or rotate it |
|---|---|---|
| `KEEL_SECRET_KEY` | session cookies | everyone is logged out; nothing else breaks |
| `KEEL_SIGNING_KEY_PEM` | **every certificate + the transparency-log root** | **every certificate ever issued stops verifying** |

`KEEL_SIGNING_KEY_PEM` is the one to get right. If it is unset, KEEL falls back
to a key file under `KEEL_DATA_DIR`. On a host with an ephemeral filesystem
(Render's free tier, Fly without a volume, any fresh container) that file is
regenerated on every deploy — so a customer verifying last week's certificate
gets a signature failure, while the service reports itself perfectly healthy.
For a product whose premise is a durable audit trail, that is the most damaging
failure available, and it is silent.

Check which mode you are in at any time:

```bash
curl -s https://your-domain.com/api/ops/durability \
  -H "Authorization: Bearer $KEEL_API_KEY"
# {"signing_key_source":"env","durable":true,"warnings":[]}
```

A file-backed key on a hardened deployment also prints a warning at startup.

### Other settings worth knowing

| Variable | Default | Purpose |
|---|---|---|
| `KEEL_SIGNUP` | `open` | `open` \| `invite` \| `closed` — who may create an account |
| `KEEL_INVITE_CODE` | — | required when `KEEL_SIGNUP=invite` |
| `KEEL_TRUSTED_PROXY` | `0` | set to `1` **only** behind a reverse proxy, so `X-Forwarded-For` is used for rate limiting. Setting it without a proxy lets a caller forge their address and evade every limit |
| `KEEL_RATELIMIT` | `1` | set to `0` to disable rate limiting |
| `KEEL_API_EXPLORER` | off when hardened | `1` re-enables the interactive API explorer |
| `KEEL_LEGAL_ENTITY`, `KEEL_LEGAL_ADDRESS`, `KEEL_SUPPORT_EMAIL` | — | operator identity on `/terms`, `/privacy`, `/contact`. Until set, those pages state they are not in force |
| `GEMINI_API_KEY` | — | enables the LLM reviewer (advisory only — it may lower a decision, never raise it) |

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

Set `KEEL_HTTPS=1` so session cookies are marked Secure and HSTS is sent, and
`KEEL_TRUSTED_PROXY=1` so rate limiting sees real client addresses.

## 5. First run

- Visit `https://your-domain.com` → the marketing site.
- `/app` → **Create your account** (first signup), then sign in.
- Open **Account** in the console and copy your API key.
- Point an agent at it:

```bash
export KEEL_URL=https://your-domain.com
export KEEL_API_KEY=keel_ak_...
python examples/gateway_quickstart.py
```

Set `KEEL_SIGNUP=invite` with an invite code before announcing the URL, unless
you intend anyone to be able to register.

## 6. Backups

Back up the `/data` volume — accounts, the transparency ledger, and (if you did
not set `KEEL_SIGNING_KEY_PEM`) the signing key. Store the signing key itself in
a secret manager, not only on the volume.

## What's enforced

- `KEEL_AUTH_REQUIRED=1` → every `/api` and `/a2a` route requires a session
  cookie or an account API key. The gate is **deny-by-default**: a route is
  public only by appearing in an explicit allowlist, so a new endpoint cannot
  ship unauthenticated by forgetting a check.
- **Account isolation.** Agents, decisions, calibration, integration settings
  (including the Slack webhook), evidence schedules and audit packs are scoped
  to the owning account. Audit packs fail closed: a certificate that cannot be
  attributed to one of your agents is excluded rather than included.
- **Rate limiting** on sign-in (per account *and* per address), sign-up, and
  expensive endpoints.
- **Security headers**: HSTS (with `KEEL_HTTPS=1`), a restrictive CSP,
  `X-Frame-Options: DENY`, `nosniff`, and a `Referrer-Policy`.
- Every feature is available to every account. There is no paywall and no
  endpoint returns `402`.
