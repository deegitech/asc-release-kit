# Credentials

The kit authenticates with an App Store Connect API key: a key ID, an issuer ID (for
team keys) and the private key (`AuthKey_<KEY_ID>.p8`). It signs short-lived ES256
tokens itself; nothing is sent to Apple except those tokens.

New to this? [setup.md](setup.md) walks through every click, and
`asc-release-kit doctor` checks the result step by step. Error messages and their
fixes are in [troubleshooting.md](troubleshooting.md).

## Creating a key

App Store Connect > Users and Access > Integrations (older: Keys) > App Store
Connect API > Team Keys > **+**. The first time, the Account Holder may have to
click **Request Access** on that page. Names may differ; [setup.md, step
2](setup.md#2-create-an-app-store-connect-api-key-3-minutes) has the details.

- **Team keys** (recommended) have a role and access to all apps of the team.
- **Individual keys** act with your own user's access. Apple: individual keys can't
  use provisioning endpoints, Sales and Finance, or notarytool. `xcodebuild` also
  needs an issuer ID, so `upload-build --archive` requires a team key.
- Apple lets you download the private key **once** and doesn't keep a copy. If you
  suspect a key leaked, revoke it in the same screen.

Typical roles (Apple changes role permissions over time; check Apple's role table):

| Commands | Role that usually works |
|---|---|
| `release`, `aso apply`, `upload-build --ipa` | App Manager (or Admin) |
| `upload-build --archive` with automatic (cloud) signing | Admin (App Manager keys get "Cloud signing permission error"; see [troubleshooting.md](troubleshooting.md#uploads-upload-build)) |
| `analytics request` | Admin (per Apple's analytics docs) |
| `analytics list/download` | Admin, Sales and Reports, or Finance |
| `sales download` | a team key whose role can see Sales and Trends |
| `reviews export` | App Manager, Admin or Customer Support |

Use the least privileged key that works, and separate keys for CI and for people.
A key has exactly one role: for jobs that need different roles, use one key per job,
each with its own `ASC_*` variables or `--config` file.

## Where the private key may live

Configure exactly one source. Flags and environment variables win over the config
file; two sources at the same level are an error, so a stray variable can't swap
the key silently.

### 1. A key file (`ASC_PRIVATE_KEY_PATH`)

```sh
mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit
mv ~/Downloads/AuthKey_ABC123DEFG.p8 ~/.config/asc-release-kit/
chmod 600 ~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
```

The kit refuses the file if group or others can read it, if it belongs to another
user, if it isn't a regular file, or if it sits in a directory every user can write
to (unless that directory has the sticky bit, like `/tmp`). A symbolic link is
resolved first and the real file is checked. The checks run on the same open file
the key is then read from, so the file can't be swapped in between. Keep the key
outside every git repository.

### 2. An environment variable (`ASC_PRIVATE_KEY`): CI secrets

Put the full PEM text (`-----BEGIN PRIVATE KEY-----` … `-----END PRIVATE KEY-----`)
into your CI secret store and expose it as `ASC_PRIVATE_KEY`. PEM with literal
`\n` sequences and base64 of the whole file are accepted too. See
[examples/github-actions-release.yml](../examples/github-actions-release.yml).
Environment variables of a process can be read by other processes of the same user
on some systems, so prefer the file or Keychain on shared machines. The kit doesn't
pass `ASC_PRIVATE_KEY` on: `openssl`, `security`, `aws` and Xcode's tools run with
it removed from their environment. In CI, set it only on the steps that call App
Store Connect, as the example workflow does.

### 3. The macOS Keychain (`ASC_KEYCHAIN_SERVICE`, optional `ASC_KEYCHAIN_ACCOUNT`)

Store the key as a generic password, base64-encoded so it is one line, and pass it
as the **value** of `-w`:

```sh
security add-generic-password -U -s asc-release-kit -a ABC123DEFG \
  -w "$(base64 < AuthKey_ABC123DEFG.p8)"
security find-generic-password -s asc-release-kit -a ABC123DEFG -w | wc -c   # 300-350; 129 or less is broken
export ASC_KEYCHAIN_SERVICE=asc-release-kit ASC_KEYCHAIN_ACCOUNT=ABC123DEFG
```

Don't leave `-w` empty: `security` then prompts for the password and silently keeps
only the first 128 characters, about a third of a base64 key (**observed Oct
2026**). The kit recognises such an item and says so. If you stored the PEM text
itself (for example with `-w "$(pbpaste)"`), `security` prints it as hex, so the
length check shows 480-520. Never run the lookup without `| wc -c`: it prints the
key. The value is on `security`'s
command line while it runs, so on a machine shared with other people prefer the key
file. Storing from the clipboard (`-w "$(pbpaste)"`) and its pitfalls are covered in
[setup.md, step 3b](setup.md#3b-the-macos-keychain).

The kit reads it with `security find-generic-password -s … -a … -w` and accepts
base64, PEM, or the hex form `security` prints for multi-line secrets. macOS may ask
you to allow access the first time. A failed lookup ends with a `fix:` line for the
common exit codes (44: no such item; 36: a locked Keychain over SSH or in a
scheduled job).

### 4. AWS Systems Manager Parameter Store (`ASC_SSM_PARAMETER`)

For build servers on AWS: store the PEM as a `SecureString` and set
`ASC_SSM_PARAMETER=/your/parameter/name`. The kit runs `aws ssm get-parameter
--with-decryption` with the AWS CLI's normal credential chain and region settings,
so the AWS CLI must be installed and allowed to decrypt that parameter.

## Identifiers

`ASC_KEY_ID` and `ASC_ISSUER_ID` are identifiers, not secrets, but the kit still
never prints them, and masks them in errors, verbose logs and the journal. They may
live in the config file:

```toml
[auth]
key_id = "ABC123DEFG"
issuer_id = "00000000-0000-0000-0000-000000000000"
private_key_path = "~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8"
```

## Tokens

- ES256, header `kid` = key ID, payload `aud: "appstoreconnect-v1"`, `iat`, `exp`,
  plus `iss` (team keys) or `sub: "user"` (individual keys), as in Apple's
  "Generating tokens for API requests".
- Lifetime 15 minutes by default (`ASC_TOKEN_TTL`, 60-1200 seconds; Apple rejects
  more than 20 minutes for most endpoints). A token is reused until about a minute
  before it expires.
- Signing uses the `cryptography` package when installed, else the `openssl`
  command (OpenSSL or LibreSSL): `openssl dgst -sha256 -sign key.p8`, whose DER
  signature the kit converts to the raw 64-byte form JWS requires. `openssl` from
  `PATH` must be found through an absolute `PATH` entry; `ASC_OPENSSL` must be the
  absolute path of a program outside the current directory that neither the group
  nor others can modify, because that program gets the key file.
- Tokens exist only in memory and are masked if they ever appear in a message.

`asc-release-kit auth check` shows which source and signer were used, the config
file in use, and where each identifier came from (flag, environment, config file or
default), without any of the values. `asc-release-kit doctor` goes further: file
permissions, Keychain truncation, the key's format, the clock, the app, the vendor
number and the analytics request, each with a fix when it fails.

## Settings that only come from the environment

A config file in your project may come from a branch or pull request you haven't
reviewed, so three settings that decide where credentials go are refused there and
read from the environment only:

| Variable | What it controls |
|---|---|
| `ASC_API_BASE_URL` | the host that receives the token (must be `https://*.apple.com`; loopback hosts for local mock servers also need `ASC_ALLOW_INSECURE_LOOPBACK=1`) |
| `ASC_SIGNER` | `auto`, `cryptography` or `openssl` |
| `ASC_OPENSSL` | the openssl program that gets the key file |

A journal path in a config file must be relative and stay inside the project; use
`ASC_JOURNAL` for anything else.

## Rotating a key

API keys don't expire; they work until revoked. Rotate on a schedule and whenever
someone who had the key leaves:

1. Create a new key in App Store Connect and download it.
2. Replace the file, Keychain item or secret, and update `ASC_KEY_ID`.
3. Run `asc-release-kit doctor`.
4. Revoke the old key (Team Keys > the old row > Revoke).
