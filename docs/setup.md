# Setup from zero (about 15 minutes)

This guide takes you from "nothing installed" to a first dry run of a release,
using only this page. The key, vendor-number and analytics steps were clicked
through in production; the rest follow Apple's documentation.

How to read the click paths:

- `>` separates menu levels: **Users and Access > Integrations** means "open Users
  and Access, then the Integrations tab".
- Apple renames menus and buttons from time to time. Where a label has changed
  before, the older or alternative name follows in brackets. **Names may differ**
  from what you see; look for the closest match.
- Behaviour that Apple doesn't document is marked **observed (Oct 2026)**.

When something fails, `asc-release-kit doctor` (step 5) says which step to redo,
and [troubleshooting.md](troubleshooting.md) lists every error message with its
fix.

## 0. What you need first

| Requirement | Why |
|---|---|
| A paid Apple Developer Program membership, and the app's team in App Store Connect | The App Store Connect API belongs to the team |
| The **Admin** role (or Account Holder) in App Store Connect, for step 2 only | Only they can create team API keys |
| API access switched on once by the **Account Holder** (step 2.3) | Until then there is no key to create |
| Apple's current agreements accepted by the Account Holder | Otherwise requests fail with `403 FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED` |
| The app record in App Store Connect (**Apps > + > New App**) | `release`, `aso` and the reports work on an existing app |
| macOS or Linux with Python 3.10 or newer | The kit is a Python package |
| `openssl` (preinstalled on macOS and most Linux systems) or the `cryptography` package | Signing the API tokens |
| Xcode, on a Mac | Only for `upload-build` |

Pick the key's role by what you'll run. Use the least privileged key that works,
and separate keys for CI and for people.

| You want to run | Role the key needs |
|---|---|
| `release`, `aso`, `reviews export`, `upload-build --ipa` | **App Manager** (Admin works too) |
| `upload-build --archive` with the generated export options (automatic signing) | **Admin**: Xcode then signs in the cloud, and App Manager keys get "Cloud signing permission error" (reported on Apple's developer forums). With App Manager, install the distribution certificate on the Mac and export with manual signing |
| `sales download` | **Sales** (also called Sales and Reports) or **Finance**, on a *team* key |
| `analytics request` | **Admin** |
| `analytics list`, `analytics download` | Admin, Sales and Reports, or Finance |

A key has exactly one role, and `doctor` checks the key you configured. To run
releases, sales and analytics together, use one Admin key, or one key per job (for
example App Manager for releases and a Sales key for the report job), each with its
own `ASC_*` variables or `--config` file.

## 1. Install the kit (1 minute)

```sh
pipx install "git+https://github.com/deegitech/asc-release-kit@main"
pipx inject asc-release-kit cryptography   # optional: sign in-process instead of with openssl
asc-release-kit --version
```

`@main` installs the latest code; to pin a release, use a tag from the
[Releases](https://github.com/deegitech/asc-release-kit/releases) page instead (for
example `@v0.1.0`).

No pipx yet? `brew install pipx` on macOS, or your Linux distribution's `pipx`
package, then `pipx ensurepath` and open a new terminal. The README's
[Install](../README.md#install) section lists the other ways.

## 2. Create an App Store Connect API key (3 minutes)

1. Sign in at <https://appstoreconnect.apple.com>.
2. Open **Users and Access** (older: Users and Roles), then the **Integrations** tab
   (older: Keys), then **App Store Connect API** in the sidebar.
3. **First time for the team only:** if you see **Request Access**, the Account
   Holder clicks it, accepts the terms and submits. After that, Admins can create
   keys.
4. Open the **Team Keys** tab and click **+** (or **Generate API Key**).
5. Name the key after where it will live, for example `asc-release-kit laptop` or
   `asc-release-kit CI`. Under **Access** pick the role from the table above.
   Click **Generate**.
6. In the new row, click **Download** (Download API Key). You get
   `AuthKey_ABC123DEFG.p8`. **Apple lets you download it once** and keeps no copy;
   if you lose it, revoke the key and create a new one.
7. Note two identifiers from the same page. Neither is a secret, but the kit masks
   both in its output:
   - **Key ID**: the KEY ID column of your row, 10 characters such as `ABC123DEFG`.
   - **Issuer ID**: shown above the table, with a Copy button. It looks like
     `00000000-0000-0000-0000-000000000000`.

Keys made elsewhere don't work here. Push-notification (APNs) and Sign in with
Apple keys from developer.apple.com > Certificates, Identifiers & Profiles > Keys
are also files named `AuthKey_….p8`, but App Store Connect rejects them (HTTP 401).

**Individual keys**, made from your own profile instead of the Team Keys tab (your
name at the top right > Edit Profile > Individual API Key; names may differ), act
with your own access. Apple doesn't let them use Sales and Finance, notarytool
or provisioning, and `xcodebuild` needs a team key. Prefer team keys.

## 3. Store the private key (3 minutes)

Pick **one** place. The kit refuses to start when two are set at the same level, so
a stray variable can't swap the key silently.

| Where | Good for | Set |
|---|---|---|
| a. A key file with mode 0600 | your own Mac or Linux machine | `ASC_PRIVATE_KEY_PATH` |
| b. The macOS Keychain | a Mac where you'd rather keep no key file | `ASC_KEYCHAIN_SERVICE`, `ASC_KEYCHAIN_ACCOUNT` |
| c. An environment variable | CI secrets | `ASC_PRIVATE_KEY` |
| d. AWS SSM Parameter Store | servers on AWS | `ASC_SSM_PARAMETER` |

### 3a. A key file (simplest)

```sh
mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit
mv ~/Downloads/AuthKey_ABC123DEFG.p8 ~/.config/asc-release-kit/
chmod 600 ~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
```

The kit refuses a key file that other users can read, that belongs to someone
else, or that sits in a directory everyone can write to. Keep it outside every git
repository. Copying, unzipping or syncing a file often resets its mode to `0644`;
run the `chmod 600` again after you move it.

### 3b. The macOS Keychain

Store the key base64-encoded, so it is one line, and pass it **as the value of
`-w`**:

```sh
cd ~/Downloads
security add-generic-password -U -s asc-release-kit -a ABC123DEFG \
  -w "$(base64 < AuthKey_ABC123DEFG.p8)"

# Check the length without printing the key: 300-350 for this base64 form, 480-520 if the
# PEM text itself was stored (security prints that as hex); 129 or less is broken.
security find-generic-password -s asc-release-kit -a ABC123DEFG -w | wc -c

export ASC_KEYCHAIN_SERVICE=asc-release-kit ASC_KEYCHAIN_ACCOUNT=ABC123DEFG
```

> **Gotcha, observed (Oct 2026):** if you leave `-w` without a value, `security`
> asks for the password interactively and **silently keeps only the first 128
> characters**. A base64 key is a little over 300 characters, so the stored key is
> broken. The kit then reports "The Keychain item holds only 128 characters".
> Always use the `-w "$(...)"` form above.

If the key text is on your clipboard instead (from a password manager, say):

1. Type or paste the command line **first**, without pressing Enter:
   `security add-generic-password -U -s asc-release-kit -a ABC123DEFG -w "$(pbpaste)"`
2. **Then** copy the key text.
3. Press Enter, then clear the clipboard: `pbcopy </dev/null`

Never paste the command and the key together as two lines: the first line would
run straight away and store whatever was on the clipboard (the command text
itself) as the key.

Never run `security find-generic-password … -w` without `| wc -c`: on its own it
prints the key. Don't paste the `.p8` text, or that output, into an issue or a chat.

Notes:

- `-U` updates an existing item instead of failing.
- The first time the kit reads the item, macOS may ask whether `security` may use
  it. Click **Always Allow** (or **Allow** each time).
- While the store command runs, the value is on its command line and visible in
  the process list. On a machine you share with other people, use the key file.
- Keep a backup of the `.p8` in your password manager before you delete the
  download (`rm ~/Downloads/AuthKey_ABC123DEFG.p8`).

### 3c. An environment variable (CI)

Put the **whole** `.p8` file, from `-----BEGIN PRIVATE KEY-----` to
`-----END PRIVATE KEY-----`, into your CI's secret store as `ASC_PRIVATE_KEY`. For
GitHub Actions, in the repository: **Settings > Secrets and variables > Actions >
New repository secret**, or with the GitHub CLI, which reads the file and never
shows it:

```sh
gh secret set ASC_PRIVATE_KEY < AuthKey_ABC123DEFG.p8
gh variable set ASC_KEY_ID --body ABC123DEFG
gh variable set ASC_ISSUER_ID --body 00000000-0000-0000-0000-000000000000
gh variable set ASC_APP_ID --body 1234567890
```

Give the secret only to the steps that call App Store Connect, as
[examples/github-actions-release.yml](../examples/github-actions-release.yml) does,
and never to workflows that run code from forks. The kit also accepts the PEM with
literal `\n` sequences, or base64 of the whole file.

### 3d. AWS SSM Parameter Store (servers)

```sh
aws ssm put-parameter --name /example/asc/private-key --type SecureString \
  --value file://AuthKey_ABC123DEFG.p8
export ASC_SSM_PARAMETER=/example/asc/private-key
```

The machine's AWS identity needs `ssm:GetParameter` on that parameter, plus
`kms:Decrypt` when the parameter uses a customer-managed KMS key. The kit runs
`aws ssm get-parameter --with-decryption` on every run and keeps the key in memory
only.

If the AWS CLI shouldn't run with every command, fetch the key once at boot into a
private file on a RAM-backed file system and use it as a key file.
`$XDG_RUNTIME_DIR` (`/run/user/<uid>`, mode 0700) is one on most systemd-based Linux
distributions; a systemd service can get its own with `RuntimeDirectory=` and
`RuntimeDirectoryMode=0700`.

```sh
umask 077
aws ssm get-parameter --name /example/asc/private-key --with-decryption \
  --query Parameter.Value --output text > "$XDG_RUNTIME_DIR/AuthKey_ABC123DEFG.p8"
export ASC_PRIVATE_KEY_PATH="$XDG_RUNTIME_DIR/AuthKey_ABC123DEFG.p8"
```

## 4. Tell the kit who you are (1 minute)

Environment variables, for example in `~/.zshrc` or in a direnv `.envrc` that git
ignores:

```sh
export ASC_KEY_ID=ABC123DEFG
export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000
# plus the variable of the key store you chose in step 3, e.g. ASC_PRIVATE_KEY_PATH
```

Or a config file in your project, `asc-release-kit.toml` (identifiers and
preferences only: the kit refuses keys, tokens and passwords in it). Start from
[examples/asc-release-kit.toml](../examples/asc-release-kit.toml):

```toml
[auth]
key_id = "ABC123DEFG"
issuer_id = "00000000-0000-0000-0000-000000000000"
private_key_path = "~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8"
```

## 5. Check everything with `doctor` (1 minute)

```sh
asc-release-kit doctor
```

`doctor` runs read-only checks in order and prints `✓` (works), `✗` (broken),
`!` (works but looks wrong) or `·` (optional, not set up). Under every `✗` and `!`
it prints the fix. It never writes anything and never prints the key, a token, the
key ID, the issuer ID, the key path or the vendor number.

```console
$ asc-release-kit doctor
doctor · read-only checks (nothing is written)

Settings
  ✓ config file: none (settings come from ASC_* variables and flags)
  ✓ key ID is set (from the environment)
  ✓ issuer ID is set (from the environment) and is a UUID
  · app ID is not set: release, aso, analytics and reviews then need --app
    to set up: once the key works, `asc-release-kit apps list` prints it (first column); export ASC_APP_ID=1234567890
  ✓ API host: Apple's App Store Connect API

Private key
  ✓ one key source: key file (path not shown)
  ✓ key file: a regular file you own, readable by you only
  ✓ the key file is outside any git repository
  ✓ the key file's name matches the key ID
  ✓ the key is an unencrypted EC P-256 private key, the kind App Store Connect issues

Token
  ✓ signer: cryptography (in-process)
  ✓ token signed (ES256); lifetime 900 s (Apple's limit is 1200 s); audience appstoreconnect-v1

App Store Connect
  ✓ Apple accepted the token; 2 app(s) visible to this key
  ✓ this computer's clock is within 60 s of Apple's
  ✓ rate limit: 3500 of 3600 requests left this hour
  · vendor number is not set: only `sales download` needs it
    to set up: App Store Connect > Payments and Financial Reports shows it at the top left ("Vendor #"), e.g. 87654321; then export ASC_VENDOR_NUMBER=87654321

Local tools
  ✓ Xcode is selected (Xcode.app): upload-build can run xcodebuild, altool and notarytool

15 passed, 0 warning(s), 0 failed
```

A failed check looks like this (here, a Keychain item stored through the
interactive prompt):

```console
Private key
  ✓ one key source: macOS Keychain item
  ✗ The Keychain item holds only 128 characters, so it isn't a complete key: macOS's interactive password prompt (`security add-generic-password ... -w` with no value) keeps only the first 128 characters (observed October 2026).
    fix: store it again with the value on the command line (your service and account names): security add-generic-password -U -s asc-release-kit -a ABC123DEFG -w "$(base64 < AuthKey_ABC123DEFG.p8)"

App Store Connect
  · skipped until the checks above pass
```

| Option | Effect |
|---|---|
| `--offline` | nothing is sent to Apple (a key in AWS SSM is still read with the AWS CLI) |
| `--app 1234567890`, `--vendor 87654321` | check these instead of the configured ones |
| `--strict` | exit 1 on warnings (`!`) too |
| `--json` | one JSON document; each check has an `id`, a `status` and a `fix` |

Exit code: `0` when nothing failed, `1` otherwise. `asc-release-kit auth check` is
the short version: key, token and one API call.

## 6. Find your app and run a first dry run (2 minutes)

```console
$ asc-release-kit apps list
1234567890   com.example.mygame                       en-US    Example Game
$ export ASC_APP_ID=1234567890
```

The first column is the app's **Apple ID**: a number, not the bundle ID. App Store
Connect shows it under **Apps > your app > App Information > Apple ID** (in the
General section).

Now a release plan. Without `--apply` nothing is written:

```console
$ asc-release-kit release --version 1.2.0 --build 42 --whats-new-text "Bug fixes."
release 1.2.0 (42) · app 1234567890 · IOS · PLAN (nothing is written; add --apply)
  ✓ build 42 is VALID (uploaded 2030-01-15)
  ✓ live version: 1.1.0 (READY_FOR_DISTRIBUTION)
  (plan) create version 1.2.0 with build 42 (Apple's default release type)
  (plan) what's new [en-US]: 10 chars
  (plan) App Review details: copy from 1.1.0 if App Store Connect didn't copy them

Plan only. The exact remaining steps are computed after the version exists. Re-run with --apply to create it; verification runs then.
```

Also read-only on Apple's side: `asc-release-kit aso pull --out aso.json` writes
your current store text into a local file (mode 0600) that `aso validate` and
`aso apply` work with.

When the plan looks right, add `--apply`, and `--submit` when you want the version
to go to App Review. [release-flow.md](release-flow.md) explains each step.

## 7. Optional: Sales and Trends (1 minute)

1. Find your **vendor number**: App Store Connect > **Payments and Financial
   Reports**, at the top left ("Vendor #"; names may differ). It is a number such
   as `87654321`.
2. The key must be a **team** key with the Sales (Sales and Reports), Finance or
   Admin role.
3. `export ASC_VENDOR_NUMBER=87654321`, run `asc-release-kit doctor`, then
   `asc-release-kit sales download --date YYYY-MM-DD --out sales` with a recent day
   that had downloads.

Apple answers 404 for a day without sales or downloads, or before that day's
report is published; the kit lists such days as missing.

## 8. Optional: App Analytics reports (1 minute now, 1-2 days of waiting)

```sh
asc-release-kit analytics request --apply   # once per app; needs an Admin key
```

- Data starts the day after the request; the first files arrive about 24-48 hours
  later.
- An `ONGOING` request **doesn't backfill**. For history, also create a snapshot:
  `asc-release-kit analytics request --access-type ONE_TIME_SNAPSHOT --apply`.
- Rows for campaign links (`ct=` parameters) stay hidden while fewer than five
  users are counted, and arrive one to three days late: **observed (Oct 2026)**.
- Apple stops a request whose reports nobody downloads for a long time; `doctor`
  and `analytics list` show that, and a new request starts it again.

Then: `asc-release-kit analytics list --reports` and `analytics download`
([analytics-sales-reviews.md](analytics-sales-reviews.md)).

## 9. Optional: uploads from a Mac

`upload-build` uses Apple's own tools with the API key, so it needs Xcode and a
team key. Check which developer tools are active with `xcode-select -p`. It should
print a path inside Xcode, such as `/Applications/Xcode.app/Contents/Developer`.
`/Library/Developer/CommandLineTools` means only the Command Line Tools are active,
which can't upload: run `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`.
`doctor` runs the same check. Then plan an upload (this prints the command and runs
nothing):

```sh
asc-release-kit upload-build --archive build/MyGame.xcarchive --team-id ABCDE12345
```

With the generated export options, Xcode signs the archive in the cloud. That needs
an **Admin** key; with an App Manager key, see "Cloud signing permission error" in
[troubleshooting.md](troubleshooting.md#uploads-upload-build). Details:
[upload-build.md](upload-build.md).

## 10. Expiry, renewal and rotation

- **API keys don't expire.** A key works until someone revokes it.
- **Tokens need no renewal.** The kit signs a fresh token for every run, valid for
  15 minutes (`ASC_TOKEN_TTL` in seconds, 60-1200; Apple's limit is 20 minutes). It
  never stores one.
- **Agreements do change.** When Apple publishes new terms, requests fail with
  `403 FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED` until the Account Holder
  accepts them (App Store Connect > Business, formerly Agreements, Tax, and
  Banking).
- **Analytics requests** stop after a long time without downloads (see step 8).
- **Rotate keys** on a schedule (a yearly calendar reminder works) and whenever
  someone who had the key leaves:
  1. Create a new key (step 2) and store it (step 3), replacing the old one.
  2. Update `ASC_KEY_ID`, and run `asc-release-kit doctor`.
  3. Revoke the old key: Users and Access > Integrations > App Store Connect API >
     Team Keys > the old row > **Revoke**.
- **If a key leaked**, revoke it first, then rotate. A revoked key can't be
  restored.

## Common mistakes

> | Mistake | What happens | Do this |
> |---|---|---|
> | Leaving `-w` empty in `security add-generic-password` | the Keychain keeps 128 characters; the key is broken (observed Oct 2026) | `-w "$(base64 < AuthKey_ABC123DEFG.p8)"` (step 3b) |
> | Pasting the command and the key as two lines | the clipboard (the command text) is stored as the key | command line first, then copy the key, then Enter |
> | Key ID in `ASC_ISSUER_ID`, or the team ID | HTTP 401 for every request | the Issuer ID is the UUID above the Team Keys table |
> | An APNs or Sign in with Apple `.p8` | HTTP 401 | create the key under Users and Access > Integrations |
> | Key file still mode `0644` after copying or syncing | the kit refuses it | `chmod 600` the file |
> | The `.p8` inside a project folder | it can end up in git | keep it in `~/.config/asc-release-kit` |
> | The bundle ID in `ASC_APP_ID` | "The app ID is the numeric Apple ID" | `asc-release-kit apps list` |
> | An individual key for sales, `xcodebuild` or notarytool | refused | a team key |
> | An App Manager key for `upload-build --archive` | "Cloud signing permission error" | an Admin key, or a local distribution certificate with manual signing (step 9) |
> | Printing the Keychain item (`-w` without `\| wc -c`) or pasting the `.p8` anywhere | the key is exposed | check the length only; if it leaked, revoke the key (step 10) |
> | Changing name, subtitle or keywords on the live version | refused: they only change with a new version (a new build) | `release` a new version; promotional text can change any time |
> | Expecting history from an `ONGOING` analytics request | it starts tomorrow | add a `ONE_TIME_SNAPSHOT` request |
> | Losing the downloaded `.p8` | it can't be downloaded again | revoke, create a new key |
> | Agreements not accepted | `403 FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED` | the Account Holder accepts them (step 10) |
