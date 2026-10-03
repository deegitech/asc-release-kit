# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-04

First public release.

### Added

- `doctor`: read-only checks of the whole setup in order (settings, the key's
  source, permissions, location and format, the token, Apple's answer, the clock,
  the hourly rate limit, the app and its bundle ID, Sales and Trends with the vendor
  number, the analytics request, Xcode for `upload-build`), each failure with the
  exact fix. `--offline`, `--strict` and `--json`; exit 1 when a check fails.
- `fix:` lines: errors with a known fix end with one (in `--json` output as `fix`):
  App Store Connect status codes and error codes (for example 401, 403
  `FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED`, 409
  `ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE`, `STATE_ERROR`, 5xx on writes), network
  errors, the exit codes of `security` and `aws`, config mistakes, Apple's upload
  codes ITMS-90189, ITMS-90186 and ITMS-90062, and the upload messages "Cloud signing
  permission error", "No signing certificate" and "No suitable application records
  were found". Any other API error points to `docs/troubleshooting.md`.
- A Keychain item cut off by macOS's interactive password prompt (128 characters,
  observed October 2026) is recognised and explained.
- `docs/setup.md` (setup from zero, every console step) and
  `docs/troubleshooting.md` (every error message and API code with its fix).
- `auth check`: load the API key from exactly one source (key file with owner-only
  permissions, `ASC_PRIVATE_KEY`, macOS Keychain or AWS SSM), sign an ES256 token
  with `cryptography` or the `openssl` command, and optionally call the API.
- `apps list`.
- `release`: create or reuse a version, attach a VALID build, set What's New per
  locale, copy App Review details and the Game Center link from the live version,
  verify by reading back, and submit with an assertion that the version is in the
  review submission.
- `release --whats-new-text` and a `"*"` entry in the What's New file: one text for
  every locale the file doesn't list. With `--submit`, an empty What's New or an
  open review submission stops the run before anything is written.
- `aso validate`, `aso apply` and `aso storefronts`: multi-locale metadata from one
  JSON file with Apple's limits, keyword checks, a Guideline 2.3.7 denylist, handling
  of auto-created localizations (409), and Apple's storefront/locale table.
- `aso pull`: write that JSON file from what App Store Connect holds now.
- `analytics request | list | download` for the Analytics Reports API with MD5 and
  size verification.
- `sales download` for Sales and Trends reports, including daily ranges, checked
  against Apple's table of valid report combinations, with `--allow-missing` for
  scheduled jobs.
- `reviews export` to CSV, JSON Lines or JSON with CSV-injection protection.
- `upload-build` wrapping `xcodebuild -exportArchive`, `altool --upload-package` and
  `notarytool submit` with the API key.
- Plan-by-default writes (enforced in the API client), a redacted JSON Lines
  journal, `--json` output that keeps the events of a failed run, and an offline
  test suite against a mock App Store Connect server.

### Security

- `ASC_API_BASE_URL`, `ASC_SIGNER` and `ASC_OPENSSL` are environment-only and
  refused in config files; loopback API hosts need `ASC_ALLOW_INSECURE_LOOPBACK=1`.
- Helper programs run without `ASC_PRIVATE_KEY` in their environment; SIGTERM and
  SIGHUP run the normal cleanup, so temporary key copies don't outlive the command.
- Key files are checked and read through one file descriptor; local files the kit
  writes (journal, reports, exports, pulled metadata) are private (`0600`/`0700`).

[0.1.0]: https://github.com/deegitech/asc-release-kit/releases/tag/v0.1.0
