# Publishing KEEL

KEEL is free. There is no paid tier, no payment provider, and nothing to
activate — so this document is only about shipping the package.

## Publish the package (PyPI)

```bash
pip install build twine

rm -rf build/ dist/ *.egg-info/   # do not skip this
python -m build                   # builds dist/keel-<version>.whl + .tar.gz
twine check dist/*                # metadata sanity
twine upload dist/*               # needs a PyPI account + token
```

**Always clean `build/` and `dist/` first.** setuptools copies sources into
`build/lib` and never prunes files that have since been deleted from the tree,
so a module you removed can survive into a "fresh" wheel. This has already
happened once here: a wheel in `dist/` was found still carrying the payment
integration weeks after it was deleted from the repo — and the publish step is
`twine upload dist/*`, which would have pushed it to PyPI. `.dockerignore`
excludes the same directories so an image build cannot pick up a stale tree
either.

Verify before uploading, rather than trusting the build:

```bash
python - <<'PY'
import glob, zipfile
for w in glob.glob("dist/*.whl"):
    names = zipfile.ZipFile(w).namelist()
    print(w, len(names), "files")
    src = zipfile.ZipFile(w).read("keel/billing.py").decode()
    for gone in ("razorpay", "stripe", "create_checkout", "PRICE_INR"):
        assert gone not in src.lower(), f"{w} still contains {gone}"
    print("  clean")
PY
```

After upload, anyone can `pip install keel`.

## Source (GitHub)

Push the repo. The certificate schema and the reference verifier being public
is deliberate — a certificate nobody outside KEEL can verify is not evidence.
CI lives in `.github/workflows/ci.yml`.

## Deployment configuration

The variables an operator actually needs are documented in `DEPLOY.md`. The one
that is easy to miss and expensive to get wrong:

- **`KEEL_SIGNING_KEY_PEM`** — the Ed25519 authority key. On a host with an
  ephemeral filesystem (Render's free tier, Fly, a fresh container) a
  file-backed key is regenerated on every deploy, which silently invalidates
  every certificate ever issued and the transparency-log root. The service
  keeps reporting healthy the whole time. Generate one with `keel keygen` and
  set it before anyone relies on a certificate.
