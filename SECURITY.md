# Security policy

asc-release-kit handles App Store Connect API keys, so we take reports about it
seriously.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting), or go
   straight to <https://github.com/deegitech/asc-release-kit/security/advisories/new>.

Please don't open a public issue for security problems.

Helpful details: the version (`asc-release-kit --version`), Python version and OS,
the command you ran, what happened, and what you expected. **Never include real API
keys, tokens, key IDs, issuer IDs, app IDs or personal data**; use placeholders.

We aim to acknowledge reports within five working days, keep you informed while we
work on a fix, and credit you in the advisory unless you prefer otherwise.

## In scope

- A private key, token, key ID, issuer ID, vendor number or App Review contact detail
  appearing in output, errors, the journal or files the kit writes.
- Credentials sent anywhere other than Apple's API host, or the token sent to a
  download or redirect target.
- A config file (which may come from a repository you don't control) changing where
  credentials go, which program receives the key, or where the kit writes outside
  the project.
- Key files or temporary copies with permissions looser than intended, or temporary
  copies that outlive the command.
- Writes happening without `--apply`, or a submission sent without passing the
  read-back checks.
- Files the kit writes (journal, reports, exports) readable by other users, or
  written through a symbolic link or outside the chosen directory.
- Injection through data the kit exports (for example CSV formula injection).

## Out of scope

- The App Store Connect API itself; report those issues to Apple.
- Attacks that require control of your machine or user account.
- Problems in optional dependencies that already have an upstream advisory (please
  still tell us if the kit needs to change).

## If your own key leaked

Revoke it immediately in App Store Connect (Users and Access > Integrations), create
a new one, and update your configuration. Apple doesn't keep a copy of the private
key, so a leaked key can only be revoked, not "rotated in place".
