"""Policy pages: terms, privacy, refunds, contact.

These exist because anyone trusting a service with their data is entitled to
know who operates it, what happens to that data, and how to reach a human —
before they hand any of it over. KEEL takes no payments, so none of this is
about billing.

The operator's identity is read from the environment rather than hardcoded:
these are legal documents naming a real business, so the details must come
from whoever is actually operating the deployment. Until they are set, each
page renders an unmissable banner and says plainly that it is not yet in
force — a policy page with invented company details is worse than none.
"""
from __future__ import annotations

import html
import os
from datetime import date, timezone, datetime

# KEEL takes no payments. There is deliberately no price constant here.


def operator() -> dict[str, str]:
    """Who is legally operating this deployment."""
    return {
        "entity": os.environ.get("KEEL_LEGAL_ENTITY", "").strip(),
        "address": os.environ.get("KEEL_LEGAL_ADDRESS", "").strip(),
        "email": os.environ.get("KEEL_SUPPORT_EMAIL", "").strip(),
        "jurisdiction": os.environ.get("KEEL_LEGAL_JURISDICTION", "India").strip(),
        "site": os.environ.get("KEEL_PUBLIC_URL", "https://keel.best").strip(),
    }


def is_configured() -> bool:
    op = operator()
    return bool(op["entity"] and op["address"] and op["email"])


def _effective_date() -> str:
    raw = os.environ.get("KEEL_LEGAL_EFFECTIVE", "").strip()
    if raw:
        return html.escape(raw)
    return datetime.now(timezone.utc).date().isoformat()


_STYLE = """
.legal{max-width:46rem;margin:0 auto;padding:3rem 1.25rem 5rem}
.legal h1{font-size:2rem;margin:0 0 .35rem}
.legal .eff{color:var(--muted,#6b7280);font-size:.9rem;margin-bottom:2rem}
.legal h2{font-size:1.15rem;margin:2.25rem 0 .6rem}
.legal p,.legal li{line-height:1.7}
.legal ul{padding-left:1.15rem}
.legal dt{font-weight:600;margin-top:.9rem}
.legal dd{margin:.15rem 0 0 0}
.legal .todo{border:1px solid #B45309;background:#FEF3C7;color:#7C2D12;
  padding:1rem 1.15rem;border-radius:8px;margin-bottom:2rem}
.legal .todo code{background:rgba(0,0,0,.07);padding:.1rem .3rem;border-radius:3px}
.legal .box{border:1px solid var(--line,#DDE2D9);border-radius:8px;padding:1rem 1.15rem;margin:1rem 0}
.legal a{color:var(--accent,#2F49C9)}
@media (prefers-color-scheme:dark){
  .legal .todo{background:#3b2f0b;color:#FDE68A;border-color:#B45309}
}
"""


def _shell(title: str, body: str, description: str) -> str:
    op = operator()
    banner = "" if is_configured() else (
        '<div class="todo"><b>This policy is not yet in force.</b> The operator '
        'details have not been configured, so this document does not name a '
        'responsible legal entity. Set <code>KEEL_LEGAL_ENTITY</code>, '
        '<code>KEEL_LEGAL_ADDRESS</code> and <code>KEEL_SUPPORT_EMAIL</code> in '
        'the server environment. Anyone trusting this service with their data '
        'is entitled to know who operates it and how to reach them.</div>')
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — KEEL</title>
<meta name="description" content="{html.escape(description)}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23FFFFFF' stroke='%23DDE2D9'/%3E%3Cpath d='M8 6v20M8 16l12-10M8 16l12 10' stroke='%232F49C9' stroke-width='2.6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="stylesheet" href="/site/site.css">
<style>{_STYLE}</style>
</head>
<body>
<header class="nav">
  <div class="wrap">
    <a href="/" class="brand" style="text-decoration:none">KE<b>E</b>L</a>
    <nav><a href="/">Home</a><a href="/docs">Docs</a><a href="/app">Console</a></nav>
    <span class="spacer"></span>
    <a href="/app" class="btn primary">Open console →</a>
  </div>
</header>
<main class="legal">
  {banner}
  <h1>{html.escape(title)}</h1>
  <p class="eff">Effective {_effective_date()}</p>
  {body}
  <h2>Contact</h2>
  <p>{_operator_block()}</p>
  <p style="margin-top:2.5rem"><a href="/terms">Terms</a> · <a href="/privacy">Privacy</a>
     · <a href="/refunds">Refunds &amp; cancellation</a> · <a href="/contact">Contact</a></p>
</main>
</body>
</html>"""


def _operator_block() -> str:
    op = operator()
    if not is_configured():
        return ("The operator of this deployment has not published contact "
                "details. Do not send them personal data until they do.")
    return (f'{html.escape(op["entity"])}<br>'
            f'{html.escape(op["address"]).replace(chr(10), "<br>")}<br>'
            f'<a href="mailto:{html.escape(op["email"])}">{html.escape(op["email"])}</a>')


# ── the four documents ───────────────────────────────────────────────────────

def terms_html() -> str:
    op = operator()
    entity = html.escape(op["entity"]) or "the operator"
    return _shell("Terms of Service", f"""
<p>These terms govern your use of KEEL at {html.escape(op["site"])} (the
"Service"), operated by {entity}. By creating an account you agree to them.</p>

<h2>What the Service does</h2>
<p>KEEL evaluates actions proposed by AI agents against policy and statistical
evidence, returns a verdict, and issues a signed certificate recording that
decision. It is a decision-support and audit control.</p>

<h2>What the Service is not</h2>
<p>KEEL reduces the rate of unsafe agent actions; it does not eliminate it. Its
guarantees are statistical and hold only under the assumptions stated in the
documentation, including that calibration data is representative of live
traffic. <b>You remain responsible for the actions your systems take.</b> KEEL
is not a substitute for your own controls, testing, insurance, or professional,
legal, financial or medical judgement. Do not use it as the sole safeguard on
anything that could cause injury, irreversible loss, or legal harm.</p>

<h2>Your account</h2>
<ul>
  <li>You are responsible for activity under your account and API keys, and for
      keeping credentials secret.</li>
  <li>One account per organisation. Do not share API keys outside it.</li>
  <li>Tell us promptly at {html.escape(op["email"]) or "the support address"} if
      you believe a credential has been exposed.</li>
</ul>

<h2>Acceptable use</h2>
<p>Do not use the Service to break the law; to attack, overload, or probe it or
any third party without authorisation; to process data you have no right to
process; or to disguise unsafe automation as reviewed. We may suspend an
account that does, and will tell you why.</p>

<h2>Cost</h2>
<p>The Service is provided free of charge. Every feature is included; there is
no paid tier, no licence to buy, and no payment is ever taken. We do not ask
for card details and do not operate a payment system. See
<a href="/refunds">Refunds &amp; cancellation</a>.</p>

<h2>Availability</h2>
<p>The Service is provided on an "as is" and "as available" basis, with no
uptime commitment unless we have agreed one with you in writing. We may change
or discontinue features; where a change materially reduces what the Service
does, we will give notice in advance and you will be able to export your
certificates and decision history before it takes effect.</p>

<h2>Your data</h2>
<p>You keep ownership of everything you submit. You grant us only the licence
needed to operate the Service for you. See <a href="/privacy">Privacy</a>.</p>

<h2>Liability</h2>
<p>The Service is supplied free of charge. We are stating a cap in money rather
than tying it to "what you paid", because you paid nothing and a cap of zero
would be neither meaningful to you nor, in several jurisdictions, enforceable.
To the extent the law allows, our total liability for all claims relating to
the Service is limited to US$100 in aggregate, and we are not liable for
indirect or consequential loss, including lost profits or lost data. Nothing
here limits liability that cannot lawfully be limited — including liability for
death or personal injury caused by negligence, or for fraud.</p>
<p>Please read this together with <b>What the Service is not</b> above: KEEL
reduces the rate of unsafe agent actions, it does not eliminate it, and you
remain responsible for what your systems do.</p>

<h2>Termination</h2>
<p>You may close your account at any time. We may suspend or terminate an
account for a material breach of these terms, or where required by law. On
termination you may export your certificates and decision history.</p>

<h2>Changes</h2>
<p>We will post any change here and update the effective date. Material changes
will be notified to your account email before they take effect.</p>

<h2>Governing law</h2>
<p>These terms are governed by the laws of
{html.escape(op["jurisdiction"]) or "the operator's jurisdiction"}, and its
courts have exclusive jurisdiction.</p>
""", "The terms governing use of the KEEL service.")


def privacy_html() -> str:
    op = operator()
    entity = html.escape(op["entity"]) or "the operator"
    return _shell("Privacy Policy", f"""
<p>{entity} operates KEEL. This explains what we collect, why, and what you can
do about it. We sell personal data to no one.</p>

<h2>What we collect</h2>
<dl>
  <dt>Account data</dt>
  <dd>Your email address and a hash of your password (scrypt — we never store
      the password itself). Used to sign you in and to contact you about your
      account.</dd>
  <dt>Content you submit</dt>
  <dd>The agent actions, policies, evidence and outcomes you send for
      evaluation, and the certificates produced from them. Used only to provide
      the Service to you.</dd>
  <dt>Payment data — none</dt>
  <dd><b>KEEL takes no payments and collects no payment information.</b> There
      is no paid tier, so we never ask for card details, never store them, and
      operate no payment system at all. If a page or email ever asks you to pay
      for KEEL, it is not from us.</dd>
  <dt>Operational logs</dt>
  <dd>Request metadata needed to run and secure the Service.</dd>
  <dt>Analytics</dt>
  <dd>Aggregate usage of the public marketing pages, to understand what people
      find useful. Not applied to the contents of your workspace.</dd>
</dl>

<h2>What we do not do</h2>
<ul>
  <li>We do not sell or rent personal data.</li>
  <li>We do not use your workspace content to train models.</li>
  <li>We do not share your content with other customers. Ever.</li>
</ul>

<h2>Third parties</h2>
<p>We use processors for hosting, payments, and — where you enable them —
model inference and notifications. Each receives only what its function
requires. If you configure an outbound integration (a webhook, a model
provider), data flows to it because you asked for it; their policy then also
applies.</p>

<h2>Retention</h2>
<p>Account and workspace data is kept while your account is open. Delete your
account and we remove it within 30 days, except where we must keep records for
legal or accounting reasons. Certificates you have exported are yours and are
unaffected.</p>

<h2>Security</h2>
<p>Traffic is encrypted with TLS. Passwords are hashed with scrypt. API access
is authenticated and denied by default. Certificates are signed with Ed25519
and recorded in an append-only transparency log, so tampering is detectable.
No system is perfectly secure; if a breach affects you we will tell you
promptly.</p>

<h2>Your rights</h2>
<p>You can ask us to access, correct, export, or delete your personal data, or
object to a use of it. Write to {html.escape(op["email"]) or "the support address"}
and we will respond within 30 days. You may also complain to your local data
protection authority.</p>

<h2>Children</h2>
<p>The Service is for organisations and is not directed at children under 16.</p>

<h2>Changes</h2>
<p>Changes are posted here with a new effective date; material changes are sent
to your account email first.</p>
""", "What data KEEL collects, why, and your rights over it.")


def refunds_html() -> str:
    op = operator()
    return _shell("Refunds & Cancellation", f"""
<p><b>KEEL is free. No payment is ever taken, so there is nothing to refund and
nothing to cancel.</b></p>

<p>This page exists because people reasonably look for it before trusting a
service, and because other pages link here. The whole policy is the sentence
above.</p>

<h2>To be specific</h2>
<ul>
  <li>Every feature is included at no cost. There is no paid tier and no
      licence to buy.</li>
  <li>We never ask for card details, and we store none. See
      <a href="/privacy">Privacy</a>.</li>
  <li>There is no subscription, so nothing renews and nothing can lapse.</li>
  <li>You will never receive an invoice from us. If something claiming to be
      KEEL asks you to pay, it is not us — please
      <a href="/contact">tell us</a>.</li>
</ul>

<h2>Closing your account</h2>
<p>You can stop using the Service at any time; there is nothing to unsubscribe
from. To have your account and its data deleted, email
{html.escape(op["email"]) or "the support address"} from your account address.
We remove it within 30 days, except where we must keep records for legal
reasons. Certificates you have already exported are yours and are unaffected.
See <a href="/privacy">Privacy</a> for the full retention position.</p>

<h2>If something went wrong</h2>
<p>There is no money involved, but we do still want to know. Bug reports,
outages and complaints go to
{html.escape(op["email"]) or "the support address"} — see
<a href="/contact">Contact</a> for what to include.</p>
""", "KEEL is free — no payment is taken, so there is nothing to refund or cancel.")


def contact_html() -> str:
    op = operator()
    email = html.escape(op["email"])
    mail = (f'<a href="mailto:{email}">{email}</a>' if email
            else "not yet published")
    return _shell("Contact", f"""
<p>A person reads this address. We aim to reply within one business day.</p>

<div class="box">
  <p style="margin:0"><b>Email</b><br>{mail}</p>
</div>

<h2>What to include</h2>
<ul>
  <li><b>Your account</b> — sign in trouble, or a request to close it and have
      your data deleted. Write from your account address. KEEL is free, so
      there is never a bill to query — see <a href="/refunds">Refunds</a>.</li>
  <li><b>A bug</b> — what you did, what happened, what you expected, and the
      certificate id if a decision was involved.</li>
  <li><b>Security</b> — please report privately to the address above rather
      than in public. Tell us how to reproduce it. We will confirm receipt
      within 2 business days, keep you updated, and will not pursue action
      against good-faith research that respects user privacy and avoids
      service disruption.</li>
  <li><b>Data requests</b> — access, export, correction or deletion, from your
      account address. See <a href="/privacy">Privacy</a>.</li>
</ul>

<h2>Registered address</h2>
<p>{_operator_block()}</p>
""", "How to reach the team behind KEEL — support, billing, security, and data requests.")
