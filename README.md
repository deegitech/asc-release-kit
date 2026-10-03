# asc-release-kit

[![CI](https://github.com/deegitech/asc-release-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/deegitech/asc-release-kit/actions/workflows/ci.yml)
[![CodeQL](https://github.com/deegitech/asc-release-kit/actions/workflows/codeql.yml/badge.svg)](https://github.com/deegitech/asc-release-kit/actions/workflows/codeql.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](pyproject.toml)
[![Runtime dependencies](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**App Store Connect release and metadata chores from the command line.** Ship a
version, push App Store metadata in many languages, pull analytics, sales and
reviews, and upload builds with an API key, without a Ruby toolchain.

Every command **plans first**. Nothing is written to App Store Connect until you
add `--apply`, every write is **journaled**, and every change is **read back**
before the kit says it worked.

```console
$ asc-release-kit release --version 1.2.0 --build 42 --whats-new whats-new.json
release 1.2.0 (42) · app 1234567890 · IOS · PLAN (nothing is written; add --apply)
  ✓ build 42 is VALID (uploaded 2030-01-15)
  ✓ live version: 1.1.0 (READY_FOR_DISTRIBUTION)
  (plan) create version 1.2.0 with build 42 (Apple's default release type)
  (plan) what's new [en-US]: 28 chars
  (plan) what's new [de-DE]: 35 chars
  (plan) App Review details: copy from 1.1.0 if App Store Connect didn't copy them
  (plan) Game Center: make sure the new version is linked (copied from the live version's setting)

$ asc-release-kit release --version 1.2.0 --build 42 --whats-new whats-new.json --apply --submit
  ...
Read-back verification:
  ✓ version 1.2.0 is PREPARE_FOR_SUBMISSION
  ✓ version string reads back as 1.2.0
  ✓ build 42 attached
  ✓ what's new [en-US] matches
  ✓ what's new [de-DE] matches
  ✓ App Review contact details are filled in (values not shown)
  ✓ Game Center link is on

Submission:
  ✎ add version 1.2.0 to the submission
  ✓ version 1.2.0 is in review submission 5e1c…
  ✎ submit review submission 5e1c…
  ✓ submitted: review submission 5e1c… is WAITING_FOR_REVIEW
```

## First 15 minutes

New to the App Store Connect API? [docs/setup.md](docs/setup.md) walks through every
click, from creating the key to a first dry run. In short:

1. **Install** (1 min): `pipx install "git+https://github.com/deegitech/asc-release-kit@main"`
2. **Create a team API key** (3 min): App Store Connect > Users and Access >
   Integrations > App Store Connect API > Team Keys > **+**, role App Manager (the
   first time, the Account Holder clicks **Request Access** on that page first).
   Download the `.p8` (Apple allows it **once**) and note the Key ID and the Issuer ID.
3. **Store the key and tell the kit** (3 min): a `chmod 600` file, the macOS
   Keychain (with `-w "$(base64 < AuthKey_ABC123DEFG.p8)"`, never the interactive
   prompt, which keeps only 128 characters), a CI secret or AWS SSM. With a key file:

   ```sh
   mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit
   mv ~/Downloads/AuthKey_ABC123DEFG.p8 ~/.config/asc-release-kit/
   chmod 600 ~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
   export ASC_KEY_ID=ABC123DEFG
   export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000
   export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
   ```

4. **Check** (1 min): `asc-release-kit doctor` runs read-only checks in order and
   prints the exact fix under everything that fails.
5. **Dry run** (2 min): find the app's Apple ID, then plan a release; nothing is
   written without `--apply`:

   ```sh
   asc-release-kit apps list          # first column: the Apple ID
   export ASC_APP_ID=1234567890
   asc-release-kit release --version 1.2.0 --build 42 --whats-new-text "Bug fixes."
   ```

Stuck? [docs/troubleshooting.md](docs/troubleshooting.md) maps the kit's error
messages and App Store Connect's error codes to their fixes, and every error the kit
knows a fix for ends with a `fix:` line.

## Contents

- [First 15 minutes](#first-15-minutes)
- [Why](#why)
- [Features](#features)
- [Install](#install)
- [Quickstart](#quickstart)
- [Commands](#commands)
- [Configuration reference](#configuration-reference)
- [Security model](#security-model)
- [Limitations and honest caveats](#limitations-and-honest-caveats)
- [FAQ](#faq)
- [About / Built by DEEGITECH](#about--built-by-deegitech)
- [License](#license)

More detail lives in [`docs/`](docs): [setup from zero](docs/setup.md),
[troubleshooting](docs/troubleshooting.md), [release flow](docs/release-flow.md),
[metadata (ASO)](docs/aso.md), [storefront locales](docs/storefront-locales.md),
[analytics, sales and reviews](docs/analytics-sales-reviews.md), [uploading builds](docs/upload-build.md),
[credentials](docs/credentials.md) and
[observed App Store Connect behaviour](docs/observed-behaviour.md).

## Why

[fastlane](https://fastlane.tools) is excellent and does far more than this kit.
But for a small team that wants to ship an update, keep its store text in git and
pull its numbers, it means a Ruby toolchain and many gems in CI. This kit is one
Python package with **no required dependencies**, aimed at the release and
metadata chores only.

It also encodes lessons from shipping a real game, where the App Store Connect API
did surprising things:

- **A review submission went to App Review without the app in it.** Adding the
  version to the submission returned HTTP 500, the other items were added, and the
  submit call succeeded. `release --submit` now re-reads the submission and refuses
  to submit unless the version is really in it.
- **Creating a localization returned 409** because App Store Connect had already
  created it on its own. `aso apply` re-reads and updates instead.
- **Attaching a different build switched the Game Center link off.** `release`
  re-checks the link after changing the build and turns it back on.

Details and dates: [docs/observed-behaviour.md](docs/observed-behaviour.md).

## Features

| Command | What it does |
|---|---|
| `doctor` | Checks the whole setup in order, read-only: settings, the key (permissions, format, a Keychain item cut off at 128 characters), the token, Apple's answer, the clock, the app, the vendor number, the analytics request and Xcode for uploads. Prints the exact fix under every failure. |
| `auth check` | Loads the key, signs an ES256 token and (unless `--offline`) calls the API. Shows which config file is in use and where each setting came from, never key ID, issuer ID or key path. |
| `apps list` | Lists the apps the key can see (Apple ID, bundle ID, primary locale, name). |
| `release` | Creates or reuses a version, attaches a **VALID** build, sets What's New per locale (or one text for all), copies App Review details and the Game Center link from the live version, verifies everything by reading it back, and with `--submit` submits for review **after asserting the version is in the review submission**. |
| `aso validate` | Offline checks of one JSON file with all locales: Apple's limits (30/30/100 bytes/170/4000/4000), keyword comma format, duplicate words across name, subtitle and keywords, a trademark/competitor denylist (Guideline 2.3.7), URLs and HTML. |
| `aso apply` | Writes App Info and version localizations (creating new locales, coping with auto-created ones), then verifies by reading back. Promotional text and URLs can also be updated on a live version. |
| `aso pull` | Writes that JSON file from what App Store Connect holds now, so you don't start from a blank page. Read-only on Apple's side. |
| `aso storefronts` | Shows which storefronts support your locales (from Apple's table) and where two of your locales repeat a keyword word inside one storefront. |
| `analytics request \| list \| download` | Analytics Reports API: create `ONGOING` / `ONE_TIME_SNAPSHOT` requests, list reports, download segments with MD5 and size checks. |
| `sales download` | Sales and Trends reports by vendor number (single date or a daily range), checked against Apple's table of valid report combinations, gzip-validated, optional `.tsv`. |
| `reviews export` | Customer reviews (with developer responses) to CSV, JSON Lines or JSON, CSV-injection safe. |
| `upload-build` | Uploads with `xcodebuild -exportArchive` or `altool --upload-package` using the API key, or notarizes with `notarytool`. |

Every command takes `--json` for machine-readable output, including the events
collected before a failure.

## Install

Requirements: Python 3.10 or newer on macOS or Linux, plus either the `openssl`
command (preinstalled on macOS and most Linux distributions) or the optional
`cryptography` package. `upload-build` needs macOS with Xcode.

Install from GitHub:

```sh
# recommended: an isolated install with pipx
pipx install "git+https://github.com/deegitech/asc-release-kit@main"
pipx inject asc-release-kit cryptography   # optional: sign in-process instead of with openssl

# or into the current environment (with the optional extra)
python3 -m pip install "asc-release-kit[crypto] @ git+https://github.com/deegitech/asc-release-kit@main"
```

`@main` is the latest code. To pin a release instead, replace it with a tag from the
[Releases](https://github.com/deegitech/asc-release-kit/releases) page (for example
`@v0.1.0`); for automation, pin the commit you reviewed, since tags can be moved.

Optional extras: `crypto` (the `cryptography` package) and `toml` (`tomli`, for
TOML config files on Python 3.10). Without extras the kit has no dependencies at
all and signs tokens with the `openssl` command. The release workflow also builds
each tagged version for PyPI through Trusted Publishing; once a version is on
PyPI, `pipx install asc-release-kit` works too.

The command is `asc-release-kit`; `asckit` is a short alias.

## Quickstart

1. **Create an API key** in App Store Connect: Users and Access > Integrations >
   App Store Connect API. A *team* key with the App Manager role covers releases
   and metadata; see [docs/credentials.md](docs/credentials.md) for the roles each
   command needs. Apple lets you download the `.p8` file **once**.

2. **Store the key where only you can read it:**

   ```sh
   mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit
   mv ~/Downloads/AuthKey_ABC123DEFG.p8 ~/.config/asc-release-kit/
   chmod 600 ~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
   ```

   The kit refuses key files that other users can read. The macOS Keychain, a CI
   secret or AWS SSM work too ([docs/setup.md](docs/setup.md#3-store-the-private-key-3-minutes)).

3. **Point the kit at it** (shell profile, a direnv `.envrc`, or a config file):

   ```sh
   export ASC_KEY_ID=ABC123DEFG
   export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000
   export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
   ```

4. **Check it and find your app's Apple ID** (the first column of `apps list`):

   ```sh
   asc-release-kit doctor      # read-only; prints the fix under anything that fails
   asc-release-kit apps list
   export ASC_APP_ID=1234567890
   ```

5. **Write What's New:** create `whats-new.json` with one entry per locale your
   app has (see [examples/whats-new.json](examples/whats-new.json)):

   ```json
   {"en-US": "Bug fixes and 20 new levels.", "de-DE": "Fehlerbehebungen und 20 neue Level."}
   ```

   Same text everywhere? Skip the file and pass `--whats-new-text "Bug fixes."`.

6. **Release:** run the plan, then apply, then submit when you're ready:

   ```sh
   asc-release-kit release --version 1.2.0 --build 42 --whats-new whats-new.json
   asc-release-kit release --version 1.2.0 --build 42 --whats-new whats-new.json --apply
   asc-release-kit release --version 1.2.0 --build 42 --whats-new whats-new.json --apply --submit
   ```

Re-running any command is safe: it compares with what App Store Connect holds and
only writes the difference. Writes are recorded in `./asc-journal.jsonl`; add that
file to your `.gitignore`.

## Commands

All commands accept `--config PATH`, `--json`, `--ascii` (plain status markers)
and `-v/--verbose` (each API request to stderr, with secrets masked). Write
commands accept `--apply`. Run `asc-release-kit <command> --help` for every flag.

### `doctor`

```sh
asc-release-kit doctor                 # every check, including read-only API calls
asc-release-kit doctor --offline       # nothing is sent to Apple (an SSM key is still read with the AWS CLI)
asc-release-kit doctor --json          # one document: each check with id, status and fix
asc-release-kit doctor --strict        # warnings fail too (for CI)
```

The checks run in order, and later ones are skipped while an earlier one fails:
settings (key ID, issuer ID format, app ID, API host), the private key (one source,
file permissions, inside a git repository, file name against the key ID, a Keychain
item cut off by macOS's 128-character prompt, the key's format), the token, then App
Store Connect (token accepted, clock against Apple's, hourly rate limit, the app and
its bundle ID, Sales and Trends with your vendor number, the analytics report
request) and Xcode for `upload-build` (`xcode-select -p` must point into an
Xcode app, not only the Command Line Tools). Each `✗` is followed by the exact fix: a
command, a menu path or a role. `!` marks something that works but looks wrong, and
`·` an optional feature that isn't set up yet, with how to set it up.

`doctor` writes nothing (its HTTP client refuses writes and it keeps no journal) and
masks the key ID, issuer ID, key path and vendor number like every other command.
Exit code 0 when nothing failed (with `--strict`, no warnings either), else 1.
Sample output: [docs/setup.md, step 5](docs/setup.md#5-check-everything-with-doctor-1-minute).

### `auth check`

```sh
asc-release-kit auth check            # load key, sign a token, call GET /v1/apps
asc-release-kit auth check --offline  # key and signing only; nothing is sent to Apple
```

It also prints the config file in use (if any) and where the key ID, issuer ID,
key type, app ID, platform and vendor number came from (flag, environment, config
file or default), never their values.

### `apps list`

```sh
asc-release-kit apps list
asc-release-kit apps list --json | jq -r '.result.apps[] | "\(.id) \(.bundleId)"'
```

### `release`

```sh
asc-release-kit release --version 1.2.0 --build 42 \
  --whats-new whats-new.json \
  --review-notes review-notes.txt \
  --release-type MANUAL \
  --wait-for-build 30 \
  --apply --submit
```

- `--whats-new`: `{"en-US": "...", "de-DE": "..."}`, or an ASO file (its `whatsNew`
  fields). A `"*"` entry is the text for every locale the file doesn't list.
- `--whats-new-text TEXT`: What's New for every locale `--whats-new` doesn't list
  (it replaces whatever was typed in App Store Connect for those locales).
- `--review-notes`: optional; otherwise the notes are copied from the live version.
- `--release-type`: `AFTER_APPROVAL`, `MANUAL` or `SCHEDULED` (with
  `--earliest-release-date`, an ISO 8601 time with a time zone such as
  `2030-11-01T09:00:00Z`).
- `--wait-for-build 30`: wait up to 30 minutes for Apple to finish processing the build.

What it checks before writing anything: the build is `VALID` and not expired; no
other version is in review; every What's New locale exists on the version; and with
`--submit`, that no locale would be left with an empty What's New and no other
review submission is still open. Then it creates (or reuses) the version, attaches
the build, sets What's New, copies App Review details and the Game Center link when
missing, reads everything back, and only then submits.

Without `--submit`, a locale with empty What's New is a warning, so you can
prepare the version with the kit and type the text in App Store Connect.
`--reuse-editable` renames an existing editable version instead of stopping.
`--game-center auto|require|skip` controls the Game Center step (auto mirrors the
live version). Step by step, with endpoints: [docs/release-flow.md](docs/release-flow.md).

If App Store Connect reports the version as already waiting for review, in review
or live, the command says so and exits 0 without writing.

### `aso pull`, `aso validate`, `aso apply`, `aso storefronts`

One JSON file holds every locale ([examples/aso.json](examples/aso.json)):

```json
{
  "app": {"id": "1234567890", "version": "1.2.0", "platform": "IOS"},
  "denylist": ["SomeCompetitor"],
  "localizations": {
    "en-US": {
      "name": "Tile Garden: Calm Puzzles",
      "subtitle": "Relaxing match-three levels",
      "keywords": "brain,zen,offline,casual,logic,blocks,colors,stress,mind,flower,cozy,board",
      "promotionalText": "Autumn update: 50 new levels and a cozy new soundtrack.",
      "description": "Tile Garden is a calm puzzle game about growing a garden one tile at a time...",
      "whatsNew": "50 new autumn levels, smoother animations and bug fixes.",
      "supportUrl": "https://example.com/support",
      "marketingUrl": "https://example.com",
      "privacyPolicyUrl": "https://example.com/privacy"
    }
  }
}
```

Start from your live metadata with `aso pull`, or from
[examples/aso.json](examples/aso.json) and [examples/denylist.txt](examples/denylist.txt):

```sh
asc-release-kit aso pull --out aso.json                                 # what App Store Connect holds now
asc-release-kit aso validate aso.json --denylist denylist.txt           # offline; exit 1 on errors
asc-release-kit aso validate aso.json --strict                          # warnings fail too
asc-release-kit aso apply aso.json                                      # plan
asc-release-kit aso apply aso.json --apply                              # write + read back
asc-release-kit aso apply aso.json --version 1.1.0 --fields promotionalText --apply   # live version
asc-release-kit aso storefronts aso.json                                # coverage + overlaps
```

`aso pull` reads the editable version (or the live one, or `--version`), leaves
What's New out (it belongs to one release), writes the file with mode `0600` and
won't replace an existing file without `--force`.

`aso apply` takes the app from `--app`. Without it, the file's `app.id` is used,
and when `ASC_APP_ID` or the config file names an app too, both must agree, so a
file for one app can't be applied to another by accident. The platform works the
same way.

Validation rules, the meaning of each warning, and the 409 handling are described
in [docs/aso.md](docs/aso.md). Apple's storefront table with locale codes is in
[docs/storefront-locales.md](docs/storefront-locales.md). `aso apply` never submits
anything.

### `analytics`

```sh
asc-release-kit analytics request --apply                     # once per app (ONGOING)
asc-release-kit analytics request --access-type ONE_TIME_SNAPSHOT --apply
asc-release-kit analytics list --reports --category APP_STORE_ENGAGEMENT
asc-release-kit analytics download --report "App Store Discovery and Engagement Standard" \
  --granularity DAILY --since 2030-02-01 --out analytics --decompress
```

Apple generates the first reports one to two days after a request. Downloads are
checked against Apple's MD5 and size; files already on disk are skipped.

### `sales download`

```sh
export ASC_VENDOR_NUMBER=87654321   # Payments and Financial Reports, top left
asc-release-kit sales download --date 2030-03-01 --decompress
asc-release-kit sales download --from 2030-02-01 --to 2030-02-28 --out sales
asc-release-kit sales download --frequency MONTHLY --date 2030-02
asc-release-kit sales download --report-type SUBSCRIPTION --date 2030-03-01   # daily only; version 1_3
asc-release-kit sales download --date 2030-03-01 --allow-missing              # for scheduled jobs
```

Report type, sub-type and frequency are checked against Apple's table of allowed
combinations before anything is requested (subscription reports are daily only, for
example), and the sub-type is filled in when the table allows just one. Apple
answers 404 when there were no sales that day or the report isn't out yet; the kit
lists those days as missing. When nothing at all was downloaded the command exits
1, or 0 with `--allow-missing`.

### `reviews export`

```sh
asc-release-kit reviews export --format csv --out reviews.csv --since 2030-02-01
asc-release-kit reviews export --format jsonl --territory USA --rating 1
```

### `upload-build`

```sh
asc-release-kit upload-build --archive build/MyGame.xcarchive --team-id ABCDE12345 --apply
asc-release-kit upload-build --ipa build/MyGame.ipa --wait-processing 30 --apply
asc-release-kit upload-build --notarize dist/MyMacApp.zip --apply   # Developer ID notarization
```

The plan shows the exact command with key ID, issuer ID and key path masked, and
doesn't load the key. Details, export options and caveats:
[docs/upload-build.md](docs/upload-build.md).

## Configuration reference

Precedence: **command-line flag > environment variable > config file > default**.
(`aso apply` also reads the app and platform from the ASO file; see
[above](#aso-pull-aso-validate-aso-apply-aso-storefronts).)

| Setting | Environment variable | Config file key | Default |
|---|---|---|---|
| Key ID | `ASC_KEY_ID` | `auth.key_id` | required |
| Issuer ID (team keys) | `ASC_ISSUER_ID` | `auth.issuer_id` | required for team keys |
| Key type | `ASC_KEY_TYPE` | `auth.key_type` | `team` (or `individual`) |
| Key file (chmod 600) | `ASC_PRIVATE_KEY_PATH` | `auth.private_key_path` | - |
| Key PEM text (CI secret) | `ASC_PRIVATE_KEY` | *(not allowed in files)* | - |
| macOS Keychain service / account | `ASC_KEYCHAIN_SERVICE` / `ASC_KEYCHAIN_ACCOUNT` | `auth.keychain_service` / `auth.keychain_account` | - |
| AWS SSM SecureString | `ASC_SSM_PARAMETER` | `auth.ssm_parameter` | - |
| Token lifetime (s, 60-1200) | `ASC_TOKEN_TTL` | `auth.token_ttl_seconds` | `900` |
| Signer | `ASC_SIGNER` | *(environment only)* | `auto` (`cryptography`, else `openssl`) |
| openssl program (absolute path) | `ASC_OPENSSL` | *(environment only)* | `openssl` on `PATH` |
| App Apple ID | `ASC_APP_ID` / `--app` | `app.id` | - |
| Platform | `ASC_PLATFORM` / `--platform` | `app.platform` | `IOS` |
| Bundle ID (upload-build safety check) | `ASC_BUNDLE_ID` | `app.bundle_id` | - |
| Vendor number | `ASC_VENDOR_NUMBER` / `--vendor` | `reports.vendor_number` | - |
| Journal file | `ASC_JOURNAL` | `journal.path` (relative, inside the project) | `./asc-journal.jsonl` |
| API base URL (`https://*.apple.com`, scheme and host only) | `ASC_API_BASE_URL` | *(environment only)* | Apple's App Store Connect API |
| Allow loopback API URLs (local mock servers only) | `ASC_ALLOW_INSECURE_LOOPBACK=1` | - | off |
| Config file | `ASC_CONFIG` / `--config` | - | `./asc-release-kit.toml`, then `./asc-release-kit.json` |

Configure **exactly one** private key source. Sources set by flags or environment
variables win over the config file; two sources at the same level are an error.

The config file holds identifiers and preferences, never secrets: keys named like
`private_key`, `token` or `password` are refused, and so are the settings marked
*environment only* above (see [Security model](#security-model)). Every command
names the config file it uses. TOML needs Python 3.11+, or
`pip install "asc-release-kit[toml]"` on 3.10; JSON works everywhere with the same
sections. See [examples/asc-release-kit.toml](examples/asc-release-kit.toml) and
[examples/asc-release-kit.json](examples/asc-release-kit.json).

**Exit codes:** `0` success (including plans), `1` validation or read-back
verification failed (or `doctor` found a failed check, or `sales download` got
nothing, without `--allow-missing`), `2` usage or configuration error, `3` App Store
Connect API or network error, `4` stopped because of App Store Connect's current
state (for example, the build isn't processed yet), `70` unexpected error, `130`
interrupted with Ctrl-C, `128+N` stopped by signal N (`143` for SIGTERM). Checks run
before writes, so a `4` usually means nothing was written; the output and the
journal show anything that was.

**Errors come with fixes.** When the kit knows how to fix an error (an App Store
Connect error code, a network error, a Keychain or AWS CLI exit code, a config
mistake, an upload code), the message ends with a `fix:` line, and `--json` output
carries it as `fix`. Any other App Store Connect error points to
[docs/troubleshooting.md](docs/troubleshooting.md), which lists every case.

**CI:** [examples/github-actions-release.yml](examples/github-actions-release.yml)
shows a manually triggered GitHub Actions release that gives the key only to the
steps that call App Store Connect.

## Security model

What the kit guarantees, and how:

- **Plan by default, enforced in the client.** Without `--apply`, nothing is
  written to App Store Connect: the HTTP client a plan uses refuses POST, PATCH and
  DELETE before anything is sent. `release` also needs `--submit` to send anything
  to App Review. `upload-build` without `--apply` only prints the command (with
  secrets masked) and doesn't load the key. Download and export commands
  (`analytics download`, `sales download`, `reviews export --out`, `aso pull`)
  write local files only.
- **One key source, tight permissions.** The private key comes from exactly one of:
  an environment variable, a key file, the macOS Keychain or AWS SSM. A key file
  must be readable by its owner only (`0600`), belong to you and not sit in a
  directory everyone can write to; symbolic links are resolved, and the file that
  is checked is the file that is read (one open file descriptor). Private keys and
  tokens are refused in config files.
- **A config file can't redirect credentials.** A config file is often committed,
  so it may come from a branch or pull request you haven't reviewed. The settings
  that decide where the token goes or which program sees the key
  (`ASC_API_BASE_URL`, `ASC_SIGNER`, `ASC_OPENSSL`) are read from the environment
  only and refused in config files, a journal path in a config file must stay
  inside the project, and every command names the config file it uses. A
  working-directory config holds identifiers and preferences only, but those still
  choose *which* app a command works on: read the plan before you add `--apply`.
- **Secrets stay out of argv, URLs, logs and state.** The key is never passed on a
  command line. Tokens live in memory only, are minted with a 15-minute lifetime (20
  maximum, Apple's limit), and are scrubbed from every message. Key ID, issuer ID,
  key path, the vendor number (in sales output), e-mail addresses and international
  phone numbers are masked in output, errors and the journal. Helper programs
  (`openssl`, `security`, `aws`, `xcodebuild`, `altool`, `notarytool`) run with
  `ASC_PRIVATE_KEY` removed from their environment, and `ASC_OPENSSL` must be an
  absolute path outside the current directory that only its owner can modify.
- **Short-lived key copies.** `openssl`, `xcodebuild`, `altool` and `notarytool` need
  a key *file*. When the key lives in memory, the kit writes a `0600` copy into a
  fresh `0700` temporary directory and deletes it as soon as the tool exits, even on
  errors, Ctrl-C, SIGTERM or SIGHUP (a cancelled CI job, for example): those signals
  end the kit through its normal cleanup, and a running tool is stopped first.
- **The token only goes to Apple.** The API base URL must be an `https://*.apple.com`
  host (scheme and host only, no path). Loopback addresses, for local mock servers,
  additionally need `ASC_ALLOW_INSECURE_LOOPBACK=1`. The `Authorization` header is
  attached as an unredirected header, so redirects can't carry it elsewhere;
  pagination links that leave the API host are refused; pre-signed report downloads
  are fetched without the token. TLS verification uses Python's defaults and can't
  be turned off.
- **No blind write retries.** Reads retry on 429/5xx with backoff. Writes retry only
  on 429. A write that fails with a 5xx or gets no answer is never repeated: the kit
  stops (the review-submission steps re-read to decide), and re-running re-reads and
  continues.
- **Read-back verification.** After writing, the kit reads the resource back and
  compares it with what you asked for: version string, release type and date, build,
  What's New, App Review contact details and the Game Center link. `release --submit`
  re-reads the review submission and **refuses to submit unless the version is in
  it**, then reads the submission back again.
- **Journal with redaction.** Every write (also one that got no answer) and every
  verification is appended to a JSON Lines journal with mode `0600`, which the kit
  checks before the first write and never writes through a symbolic link. App Review
  contact and demo-account fields are replaced with `[redacted]`; review notes are
  stored only as length + SHA-256 prefix.
- **Private local files.** Review exports, sales and analytics reports and pulled
  metadata are written with mode `0600` (an existing file is tightened too) into
  directories created with mode `0700`; report files never land outside `--out`.
- **Idempotent.** Commands compute the difference against App Store Connect and only
  write what differs, so a run that stopped halfway can simply be repeated.
- **Safe exports.** Review exports guard CSV cells against spreadsheet formula
  injection.
- **Small supply chain.** No required runtime dependencies; `cryptography` is an
  optional extra. CI runs the tests on Python 3.10-3.14 plus ruff, mypy, gitleaks
  and CodeQL; actions are pinned to commit SHAs and the linters to exact versions,
  and Dependabot keeps both current. The release workflow publishes to PyPI through
  Trusted Publishing, so no upload token is stored anywhere.

**Don't run the kit with credentials on a checkout you don't trust.** Its ASO and
What's New files are exactly what `--apply` sends to the App Store, and its config
file picks the app. In CI, give `ASC_PRIVATE_KEY` only to workflows that run on
reviewed code, never to jobs that check out pull requests from forks (for example
`pull_request_target` workflows). `aso validate` needs no credentials and is fine
anywhere.

What it can't protect against: a compromised machine or user account; anything
running as you can read your key file and the kit's memory. `xcodebuild`, `altool`
and `notarytool` receive the key ID and issuer ID as arguments (they're identifiers,
not secrets, but other processes of the same user can see them while the tool runs).
A `SIGKILL` or a crash of the machine during an upload can leave the temporary key
copy in the system temp directory. Report vulnerabilities privately:
[SECURITY.md](SECURITY.md).

## Limitations and honest caveats

- **Not affiliated with Apple.** The App Store Connect API changes; endpoints or
  behaviour this kit relies on may change too.
- **Version 0.1.0.** The logic is ported from scripts we used to ship our own app in
  2026, but this packaged version is new. Its test suite (over 200 tests) runs
  against a local mock of the API, not against Apple. Run the plan first.
- **Observed behaviour is labelled as such.** Some handling exists because of things
  we saw in September and October 2026 (see
  [docs/observed-behaviour.md](docs/observed-behaviour.md)), not because Apple
  documents them.
- **Out of scope for now:** screenshots and app previews, in-app purchases and
  subscriptions, Game Center content, TestFlight, in-app events, pricing and
  availability, phased release control, App Review attachments (they aren't copied
  to a new version), and Apple's newer BuildUploads API.
- **Character counting** uses Unicode code points; App Store Connect may count emoji
  and some scripts differently, so the validator warns when UTF-16 length exceeds a
  limit.
- **Keyword advice is heuristic.** Duplicate-word and cross-locale overlap warnings
  follow common ASO practice; Apple doesn't document ranking.
- **Sales report versions:** subscription reports (`SUBSCRIPTION`,
  `SUBSCRIPTION_EVENT`, `SUBSCRIBER`) are requested as version `1_3` by default,
  because Apple retired `1_2`; for other reports no `filter[version]` is sent and
  Apple picks. Pin it with `--report-version` if your parser needs stable columns.
  The table of valid combinations was transcribed from Apple's documentation in
  October 2026; if Apple changes it, the kit needs an update.
- **Platforms:** API commands are tested on macOS and Linux. `upload-build` needs
  macOS with Xcode (altool flags follow the Xcode 26 help text). Windows isn't tested.
- **Review notes** are capped at 4,000 characters by the kit as a conservative limit;
  Apple's API documentation doesn't state one.

## FAQ

**Something doesn't work. Where do I start?** Run `asc-release-kit doctor`. It checks
the setup step by step and prints the fix for whatever fails. Error messages and API
codes are also listed in [docs/troubleshooting.md](docs/troubleshooting.md).

**Does it replace fastlane?** No. It covers releases, metadata, reports and uploads
for teams that want a small tool. If you use fastlane's match, snapshot, scan or
plugins, keep fastlane.

**Does it store my key or send it anywhere?** No. It reads the key from the one
source you configure, keeps it in memory, and only sends signed tokens to Apple's
API host. `upload-build` passes the key file to Apple's own tools, which talk to
Apple's upload and notary services. There's no telemetry.

**Why does `--submit` refuse to submit even though everything else passed?** The
version wasn't an item of the review submission when the kit re-read it. App Store
Connect has accepted a submission without the app version before, so the kit stops
rather than sending other items to review without your app. Re-run later or submit
from App Store Connect.

**Can I use it in CI?** Yes. Put the key's PEM text in a secret and expose it as
`ASC_PRIVATE_KEY` to the steps that call App Store Connect; see
[examples/github-actions-release.yml](examples/github-actions-release.yml).

**Can I run it on pull requests?** `aso validate` needs no credentials, so it is a
good pull-request check. Commands that use the key belong in workflows that run
on reviewed code only (see the [Security model](#security-model)).

**Team key or individual key?** Team keys work everywhere. Apple doesn't let
individual keys use Sales and Finance, notarytool or provisioning, and `xcodebuild`
needs an issuer ID.

**How do I undo a change?** There is no automatic rollback. The journal records
what was written (with personal data removed), so you can see what changed and put
back the previous text.

**Can it manage several apps?** Yes: pass `--app` per run or keep one config file
per app.

## About / Built by DEEGITECH

asc-release-kit is built and maintained by **DEEGITECH**, an independent software
studio from Türkiye. We wrote these tools while shipping our iOS game
[Wide Molly Hooked](https://apps.apple.com/app/id6813081261) and open-sourced them
so other small teams don't have to rediscover the same sharp edges.

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and our
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) © 2026 DEEGITECH Teknoloji ve Yazılım Ltd. Şti.

App Store, App Store Connect, Game Center, TestFlight and Xcode are trademarks of
Apple Inc. This project is not affiliated with or endorsed by Apple.
