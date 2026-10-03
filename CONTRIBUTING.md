# Contributing

Thanks for helping! Bug reports, observations of App Store Connect behaviour, docs
fixes and code are all welcome.

## Development setup

```sh
git clone https://github.com/deegitech/asc-release-kit
cd asc-release-kit
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```

Before opening a pull request, also run `ruff check .`, `ruff format .` and `mypy`
(all pinned in the `dev` extra; `mypy` checks `src/` with Python 3.10 semantics).

The tests run fully offline against `tests/mock_asc.py`, an in-process mock of the
App Store Connect API that also checks every token's ES256 signature. They also work
with the standard library runner: `cd tests && python -m unittest discover -s . -t .`.
To exercise the `openssl` signer without `cryptography`, install with
`python -m pip install -e . pytest` instead.

## Ground rules

- **Never use real identifiers.** No real key IDs, issuer IDs, app IDs, vendor
  numbers, team IDs, bundle IDs, e-mail addresses, phone numbers or names in code,
  tests, docs or examples. Use placeholders such as `ABC123DEFG` (key ID),
  `00000000-0000-0000-0000-000000000000` (issuer ID), `1234567890` (app ID),
  `87654321` (vendor number), `ABCDE12345` (team ID), `com.example.mygame` and
  `you@example.com`.
- **Never commit key material.** Tests generate throwaway keys at runtime. CI runs
  gitleaks on every push; run it locally before you open a pull request:
  `gitleaks dir --redact .`
- **Keep the safety model.** Writes only with `--apply` (a plan's `AscClient`
  refuses POST, PATCH and DELETE, so tests catch slips); every write goes through
  `AscClient` (so it's journaled); every write path ends with a read-back check; no
  retries of writes after a 5xx or a missing answer. Settings that decide where
  credentials go stay environment-only, and local files go through `fsutil` (mode
  `0600`).
- **Standard library first.** New runtime dependencies need a strong reason and
  belong in an optional extra.
- **Python 3.10 compatible.** CI runs 3.10 to 3.14.
- **Label observations.** If you change behaviour because of something App Store
  Connect did, add it to `docs/observed-behaviour.md` with the date, and reproduce it
  in the mock.
- **Errors come with fixes.** When you raise an error that has a clear fix, pass it
  as `fix=` (one line: a command, a menu path or a role). New App Store Connect error
  codes go into `API_RULES` in `src/asc_release_kit/hints.py` with a case in
  `tests/test_hints.py`. Add a row to `docs/troubleshooting.md` either way.

## Updating the storefront table

Apple's storefront/language table lives in `src/asc_release_kit/storefronts.py`.
After editing it (and its retrieval date), regenerate the docs and run the tests:

```sh
python scripts/render_storefront_doc.py
python -m pytest tests/test_storefronts.py
```

## Releasing (maintainers)

1. Update `CHANGELOG.md` and the version in `pyproject.toml` and
   `src/asc_release_kit/__init__.py`.
2. Check exactly what will be published: commit, then run
   `gitleaks git --redact .` and `gitleaks dir --redact .` on a fresh clone (a
   working copy can hold ignored files such as `__pycache__/` that a clone
   doesn't).
3. Push a tag `vX.Y.Z`. `.github/workflows/release.yml` checks that the tag matches
   the version, builds the sdist and wheel, and publishes them to PyPI with Trusted
   Publishing (configure the trusted publisher on PyPI once: workflow `release.yml`,
   environment `pypi`).

## Pull requests

- One topic per pull request, with tests for new behaviour.
- Update `README.md`, `docs/` and `CHANGELOG.md` (under "Unreleased") when behaviour
  changes.
- Describe how you tested, especially anything you checked against the real API.

By contributing you agree that your contributions are licensed under the MIT
License, and you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md).
