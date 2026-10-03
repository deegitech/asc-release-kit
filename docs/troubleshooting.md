# Troubleshooting

Start with `asc-release-kit doctor`: it runs every read-only check in order and
prints the fix under each failure (see [setup.md, step 5](setup.md#5-check-everything-with-doctor-1-minute)).
Every error the kit knows a fix for also ends with a `fix:` line. This page is the
long form: find the message (or part of it) and apply the fix.

Behaviour that Apple doesn't document is marked **observed (Oct 2026)**. Console
menus change names from time to time, so menu paths are given with the older or
alternative label in brackets; **names may differ**. Identifiers below are
placeholders (`ABC123DEFG`, `1234567890`, `87654321`).

- [Setup and credentials](#setup-and-credentials)
- [Configuration file](#configuration-file)
- [App Store Connect API errors (status and code)](#app-store-connect-api-errors)
- [Releases](#releases-release)
- [Store metadata](#store-metadata-aso)
- [Reports](#reports-analytics-sales-reviews)
- [Uploads](#uploads-upload-build)
- [Exit codes](#exit-codes)

## Setup and credentials

| Message (or part of it) | What it means | Fix |
|---|---|---|
| `No App Store Connect API key configured` | No key source is set. | Create a key and store it ([setup.md](setup.md), steps 2-3), e.g. `export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8`. |
| `Several private key sources are configured at once` | Two of `ASC_PRIVATE_KEY`, `ASC_PRIVATE_KEY_PATH`, `ASC_KEYCHAIN_SERVICE`, `ASC_SSM_PARAMETER` are set at the same level. | Unset all but one. Flags and environment variables win over the config file. |
| `Set ASC_KEY_ID` / doctor: `key ID is not set` | The key's ID is missing. | App Store Connect > Users and Access > Integrations (older: Keys) > App Store Connect API > Team Keys: copy the KEY ID column, then `export ASC_KEY_ID=ABC123DEFG`. |
| doctor: `key ID ... doesn't look like one` | Key IDs are 10 capital letters and digits. | Copy the KEY ID column of the row for this key. |
| `Team keys need ASC_ISSUER_ID` | The issuer ID is missing. | Copy the Issuer ID shown above the Team Keys table, then `export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000`. |
| doctor: `issuer ID ... isn't a UUID` | The key ID or the team ID went into `ASC_ISSUER_ID`. | The Issuer ID is the UUID above the Team Keys table. |
| `Individual keys don't use an issuer ID` | `ASC_KEY_TYPE=individual` with an issuer ID. | Unset `ASC_ISSUER_ID` (and `auth.issuer_id`), or use `ASC_KEY_TYPE=team` for a team key. |
| `The private key file is readable by other users` | Mode looser than `0600`; copying, unzipping or syncing often leaves `0644`. | `chmod 600 "$ASC_PRIVATE_KEY_PATH"` |
| `The private key file belongs to another user` | Someone else owns the file. | Copy it into a directory of yours (`~/.config/asc-release-kit`, mode 0700) and `chmod 600` the copy. |
| `...in a directory that every user can write to` | Anyone could swap the key. | `mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit`, move the key there. |
| `The configured private key file does not exist` | Wrong path. | Apple's download is `AuthKey_<KEY_ID>.p8`, usually in `~/Downloads`. |
| `...not a regular file` / `...far too large to be a .p8 key` | The path points at a directory, a device or another file. | Point `ASC_PRIVATE_KEY_PATH` at the `.p8` file itself. |
| doctor: `the key file is inside a git repository` | It can be committed by accident. | Move it to `~/.config/asc-release-kit` and update `ASC_PRIVATE_KEY_PATH`. |
| doctor: `the key file's name ... names a different key ID` | `AuthKey_<ID>.p8` doesn't match `ASC_KEY_ID`, so Apple will answer 401. | Use the Key ID of the same row as this key in Team Keys. |
| `The Keychain item holds only 128 characters` | The key was stored through macOS's interactive password prompt (`-w` with no value), which keeps only the first 128 characters: **observed (Oct 2026)**. A base64 key is a little over 300 characters. | Store it again with the value on the command line: `security add-generic-password -U -s asc-release-kit -a ABC123DEFG -w "$(base64 < AuthKey_ABC123DEFG.p8)"` ([setup.md, step 3b](setup.md#3b-the-macos-keychain)). To check an item, `security find-generic-password -s asc-release-kit -a ABC123DEFG -w \| wc -c` prints only its length: 300-350 for base64, 480-520 for PEM text (printed as hex). Never run it without `\| wc -c`. |
| `The Keychain item holds only 27 characters` (or another small number) | Usually the first line of the PEM pasted at the prompt, or the command text stored from the clipboard after pasting two lines at once. | Same fix. With `"$(pbpaste)"`: type the command first, then copy the key, then press Enter. |
| `Keychain lookup failed (security exited with 44)` | No item matches the service (and account). | Check `ASC_KEYCHAIN_SERVICE` / `ASC_KEYCHAIN_ACCOUNT`. `security find-generic-password -s asc-release-kit` (without `-w`) shows whether the item exists, without printing it. |
| `...exited with 36` | Usually: the Keychain is locked and macOS can't ask for its password here, for example over SSH or in a scheduled job. | `security unlock-keychain` first, or use a key file on that machine. |
| `...exited with 51` | Usually: the Keychain refused the password. | `security unlock-keychain` and try again. |
| `...exited with 128` | Usually: access was denied in the macOS dialog. | Run again and click **Allow** (or **Always Allow**). |
| `The macOS Keychain source only works on macOS` | `ASC_KEYCHAIN_SERVICE` on Linux. | Use a key file or AWS SSM there. |
| `ASC_SSM_PARAMETER needs the AWS CLI` | `aws` isn't on `PATH`. | Install the AWS CLI version 2; check with `aws sts get-caller-identity`. |
| `AWS SSM lookup failed (aws exited with 254)` | AWS answered with an error (wrong name or region, or no permission). | Check the name and `AWS_REGION`; the identity needs `ssm:GetParameter`, plus `kms:Decrypt` for a customer-managed KMS key. |
| `...aws exited with 253` | No usable AWS credentials or region. | `aws configure list` (or the instance role) and `AWS_REGION`. |
| `...aws exited with 252` or `255` | Bad arguments, or another AWS CLI failure. | `aws ssm get-parameter --name /example/asc/private-key --query Parameter.Name` shows the error without printing the value. |
| `The private key is not PEM text` | `ASC_PRIVATE_KEY` (or the Keychain, or SSM) holds something else, or only part of the key. | Store the whole `.p8` file again. The `fix:` line names the command for the source in use: `gh secret set ASC_PRIVATE_KEY < AuthKey_ABC123DEFG.p8` for a CI secret, the `security add-generic-password ... -w "$(base64 < AuthKey_ABC123DEFG.p8)"` command for the Keychain, `aws ssm put-parameter --overwrite --type SecureString --name /example/asc/private-key --value file://AuthKey_ABC123DEFG.p8` for SSM. |
| `The private key PEM is truncated (no END line)` | Part of the key was pasted or stored. | Store every line of the `.p8` file, from the `BEGIN PRIVATE KEY` line through the `END PRIVATE KEY` line, with the same command for your source. |
| `Encrypted private keys are not supported` / `must be the PEM .p8 file` / `could not be parsed` / `this key is not` (EC P-256) | A certificate, a `.p12`, a converted or a re-encrypted key. | Use the `AuthKey_<KEY_ID>.p8` file exactly as App Store Connect gave it. |
| HTTP 401 with a key that parses fine | Often a push-notification (APNs) or Sign in with Apple key: also `AuthKey_….p8`, but from developer.apple.com > Certificates, Identifiers & Profiles > Keys. | Create an App Store Connect API key under Users and Access > Integrations. |
| Lost the downloaded `.p8` | Apple lets you download a key once and keeps no copy. | Revoke the key and create a new one. |
| `No signer available` | Neither `cryptography` nor `openssl` is usable. | `pipx inject asc-release-kit cryptography` (or `python3 -m pip install "asc-release-kit[crypto]"`). |
| `` `openssl` was found through a relative PATH entry`` | `PATH` contains `.` or another relative directory. | Remove it from `PATH`, or `export ASC_OPENSSL=/usr/bin/openssl`, or install `cryptography`. |
| `ASC_OPENSSL must be the absolute path...` / `points inside the current directory` / `other users can modify` | The kit only hands the key to an openssl you control. | `export ASC_OPENSSL="$(command -v openssl)"` with a system openssl, or unset it. |
| `openssl could not sign the token` | The key file isn't a valid unencrypted `.p8`. | Use the file as downloaded; `asc-release-kit doctor` shows which step fails. |
| `Refusing to send App Store Connect credentials to ...` | `ASC_API_BASE_URL` isn't an `https://*.apple.com` host. | Unset `ASC_API_BASE_URL`. |
| `ASC_API_BASE_URL must be scheme://host[:port] only` | The URL includes a path such as `/v1`. | Remove the path; the kit adds `/v1/...` itself. |
| `Loopback addresses are for local mock servers only` | A local URL without the explicit switch. | For a mock server only: `ASC_ALLOW_INSECURE_LOOPBACK=1`. |
| doctor: `this computer's clock is ... ahead of / behind Apple's` | Tokens carry the local time; a wrong clock can get them rejected. | macOS: System Settings > General > Date & Time > Set time and date automatically. Linux: `sudo timedatectl set-ntp true`. |
| doctor: `Apple accepted the token, but this key sees no apps` | The team has no app record yet, or the key belongs to another team. | App Store Connect > Apps > + > New App, or a key of the team that owns the app. |
| doctor: `analytics request not checked: the app check above failed` | The app ID failed one line above; checking analytics for it would only fail again. | Fix the app ID first (`asc-release-kit apps list`). |
| doctor: `only the Command Line Tools are selected, not Xcode` | `xcode-select -p` points to `/Library/Developer/CommandLineTools`; `upload-build` needs Xcode's `xcodebuild` and `altool`. Every other command works. | Install Xcode, then `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`. |
| doctor: `no Xcode is selected` | No developer tools are installed or selected. | Install Xcode from the Mac App Store, open it once, then `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`. |
| doctor: `upload-build runs on macOS only` | You are on Linux (or another system). | Nothing to fix: every other command works there. Run uploads on a Mac. |
| A network error (`network error: ...`) | No connection to Apple's API host. | Check the connection and any proxy (`HTTPS_PROXY`), then run the command again. A write that got no answer may have gone through: re-run the same command, which re-reads App Store Connect first. |

## Configuration file

| Message | What it means | Fix |
|---|---|---|
| `'auth.signer' can't be set in a config file` (also `auth.openssl`, `api.base_url`) | A config file can come from a branch you haven't reviewed, so settings that decide where credentials go are environment-only. | Delete the line and export `ASC_SIGNER`, `ASC_OPENSSL` or `ASC_API_BASE_URL` instead. |
| `The config file contains 'auth.private_key'` (or `token`, `password`, ...) | A secret in the config file. | Delete it; store the key as [setup.md, step 3](setup.md#3-store-the-private-key-3-minutes) shows. |
| `Unknown config setting` / `must be a table/object` / `must contain sections` | A typo or a missing section. | Compare with [examples/asc-release-kit.toml](../examples/asc-release-kit.toml): `[auth]`, `[app]`, `[reports]`, `[journal]`. |
| `Could not read asc-release-kit.toml` / `.json` | A syntax error. | Fix the file at the position shown. |
| `Reading a .toml config on Python 3.10 needs tomli` | Python 3.10 has no TOML reader. | `pipx inject asc-release-kit tomli`, or use `asc-release-kit.json` with the same sections. |
| `The app ID is the numeric Apple ID of the app` | The bundle ID (or a name) in `ASC_APP_ID`. | `asc-release-kit apps list` prints the Apple ID (first column); in App Store Connect: Apps > your app > App Information > Apple ID. |
| `Pass --app APP_ID or set ASC_APP_ID` | The command needs to know the app. | `export ASC_APP_ID=1234567890`. |
| `The vendor number must be numeric` / `Pass --vendor or set ASC_VENDOR_NUMBER` | Sales reports need the vendor number. | App Store Connect > Payments and Financial Reports, top left ("Vendor #"): `export ASC_VENDOR_NUMBER=87654321`. |
| `token_ttl_seconds must be between 60 and 1200` | Apple accepts tokens of at most 20 minutes. | Leave it out (900 is the default). |
| `Key type must be 'team' or 'individual'` | A typo in `ASC_KEY_TYPE`. | `team` for keys from the Team Keys tab. |
| `journal.path in a config file must be a relative path inside the project` | The config file tried to move the journal elsewhere. | Use a relative path, or `export ASC_JOURNAL=...`. |
| `... is a symbolic link; the kit doesn't write through links.` | The journal path is a link. | Point `ASC_JOURNAL` at a regular file. |

## App Store Connect API errors

An API error prints the request, Apple's status, code and detail, then a `fix:`
line (`--json`: the `fix` field). The rules live in `src/asc_release_kit/hints.py`;
the first matching row wins. An error no row matches gets a `fix:` line pointing to
this page.

| Status · code (where) | What it means | Fix |
|---|---|---|
| `401` · `NOT_AUTHORIZED` | Apple rejected the token: wrong key ID or issuer ID, a team key used as individual (or the other way round), a revoked key, a key that isn't an App Store Connect API key, or a wrong clock. | Run `asc-release-kit doctor`; it checks each of these. |
| `403` · `FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED` (or a detail about agreements) | The Account Holder hasn't accepted Apple's latest agreements. | App Store Connect > Business (formerly Agreements, Tax, and Banking), or the notice at developer.apple.com > Account. |
| `403` on `/v1/salesReports` | The key can't read Sales and Trends. | A **team** key with the Sales (Sales and Reports), Finance or Admin role. Individual keys can't access Sales and Finance. |
| `403` on `POST /v1/analyticsReportRequests` | Creating (or deleting) analytics requests needs Admin. | Run `analytics request` with an Admin key once. |
| `403` on other analytics paths | Reading analytics needs Admin, Sales and Reports, or Finance. | Use such a key for `analytics list` and `download`. |
| `403` on `customerReviews` | Reading reviews needs Customer Support, App Manager or Admin. | Use such a key for `reviews export`. |
| `403` · `FORBIDDEN_ERROR` (other paths) | The key's role doesn't allow the request. | A team key with a role that does: App Manager covers releases, metadata and uploads ([credentials.md](credentials.md)). |
| `404` on `/v1/apps/<id>...` | No app with this Apple ID is visible to the key. | Check `ASC_APP_ID` (`asc-release-kit apps list`), and that the key belongs to the team that owns the app. |
| `404` on `/v1/salesReports` | No report for that date: no sales or downloads that day, or not published yet. | Normal; the kit lists the day as missing. Use `--allow-missing` in scheduled jobs. |
| `409` · `ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE` on `POST /v1/appStoreVersions` | App Store Connect already has a version with this number for this platform, or had one before. Re-running usually hits the same error. | Check the app's versions in App Store Connect, and give the new release a higher version number (`CFBundleShortVersionString`, in a new build). |
| `409` · `ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE` (other paths) | The item already exists. Creating an App Info localization makes App Store Connect create the matching version localization by itself, so a second create answers 409: **observed (Oct 2026)**. | Re-run: the kit re-reads and updates instead of creating (`aso apply` does this on its own). |
| `409` · `STATE_ERROR...` | The item's state doesn't allow the change, for example a version in review, or in a draft review submission. | Check the item in App Store Connect. Name, subtitle and keywords only change with a new version. |
| Screenshots or previews can't be deleted (in App Store Connect or through the API) | Media can't be deleted while the version sits in a draft review submission: **observed (Oct 2026)**. | Remove the version from the draft submission, change the media, then add it back (`release --apply --submit` adds it again). |
| `409` · `ENTITY_ERROR.RELATIONSHIP...` | A link between two items was refused, usually a build that doesn't fit the version. | Check that the build belongs to this app, version number and platform and is `VALID`. |
| `409` · `ENTITY_ERROR.ATTRIBUTE...` | A field value was refused; the detail names it. | `asc-release-kit aso validate` checks Apple's limits offline. |
| `409` · other `ENTITY_ERROR` | A conflict with the item's current state. | Re-run (the kit re-reads first) and check the item in App Store Connect. |
| `400` · `PARAMETER_ERROR...` | A request parameter was refused; the detail names it. | Check the flag values: dates, report type, vendor number. |
| `429` · `RATE_LIMIT_EXCEEDED` | The hourly request limit is used up (the kit already retried with backoff). | Wait and re-run. `doctor` shows how many requests are left this hour. |
| `5xx` on a write (`POST`, `PATCH`, `DELETE`) | Apple's server failed; the write may or may not have happened, so the kit doesn't repeat it. | Re-run the same command: it re-reads App Store Connect and continues where it stopped. |
| `5xx` on a read, after retries | Apple's servers keep failing. | Try later; <https://developer.apple.com/system-status/> shows outages. |
| Any other status or code | No rule above matches. | The `fix:` line points here: look up the status and code in this table, and in Apple's App Store Connect API documentation. |

## Releases (`release`)

| Message | What it means | Fix |
|---|---|---|
| `Build 1.2.0 (42) is not VALID (PROCESSING)` | Apple is still processing the build. | `--wait-for-build 30`. |
| `Build 1.2.0 (42) is not VALID (not found)` | The version or build number is wrong, `ASC_APP_ID` / `--app` points to another app, or the upload hasn't reached App Store Connect yet. | Check the numbers on the app's TestFlight tab, and `ASC_APP_ID` against the first column of `asc-release-kit apps list`; for a fresh upload, `--wait-for-build 30`. |
| `...has expired; upload a new build` | The build is too old to submit. | Upload a new build. |
| `...is INVALID` / `FAILED` | Processing failed. | Apple's processing e-mail says why. |
| `export compliance isn't answered for this build` | `--submit` would be refused. | Set `ITSAppUsesNonExemptEncryption` in Info.plist for future builds (Apple's export compliance documentation explains the value), or answer the question for this build in App Store Connect. |
| `Version ... is still with App Review or waiting for release` | One version per platform can be in flight. | Wait for the review, release the pending version, or remove it from review in App Store Connect. |
| `An editable version ... already exists` | Another version number is being prepared. | `--reuse-editable` renames it, or remove it in App Store Connect. |
| `Version ... is ... and can't be edited` | The version is in a state the kit doesn't change. | Use a new version number. |
| `What's New names locale(s) the version doesn't have` | The file has a language the app doesn't. | Add the locale first with `asc-release-kit aso apply`, or remove it from the file. |
| `What's New would be empty for ...` | App Store Connect requires What's New for every language of an update. | Add those locales, or pass `--whats-new-text` for every locale the file doesn't list. |
| `review submission ... is WAITING_FOR_REVIEW` (or `IN_REVIEW`, `UNRESOLVED_ISSUES`, ...) | Another submission is still open. | Resolve or cancel it in App Store Connect, then re-run. |
| `NOT SUBMITTED: version ... is not an item of review submission ...` | Adding the version to the submission failed. In September 2026 that call answered HTTP 500 and the submission could then be sent without the app version: **observed**. The kit refuses to submit. | Re-run later, or add the version to the draft in App Store Connect and submit there ([observed-behaviour.md](observed-behaviour.md)). |
| `Read-back verification failed; the version was NOT submitted` | Something didn't stick; the `✗` lines above say what. | Fix that item, then re-run. |
| `The version has no App Review details and there is no live version to copy them from` | First release of the app. | Fill in App Review Information once in App Store Connect, then re-run. |
| `Game Center: re-enable the version's link (App Store Connect turned it off)` | Attaching a different build switched the Game Center link off: **observed (Sep 2026)**. | Nothing: the kit turns it back on and verifies it. |

## Store metadata (`aso`)

| Message or symptom | What it means | Fix |
|---|---|---|
| `Version ... is READY_FOR_DISTRIBUTION. Only promotional text and the support, marketing and privacy URLs can change now` | Name, subtitle and keywords only change with a new app version (a new build). Promotional text can change any time, without review. | `--fields promotionalText`, or prepare a new version with `release` first. |
| `There is no editable App Info record` | Name and subtitle live on the App Info record that App Store Connect opens while a version is prepared. | Create the new version first. |
| `adding a new locale needs at least a name` | A new language needs its App Info localization first. | Add `name` for that locale. |
| `... bytes, limit 100 bytes` (keywords) | The limit is 100 **bytes**, not characters: Turkish, German and other accented letters take 2 bytes, CJK 3. | Shorten them; `aso validate` counts bytes. |
| `contains denylisted term(s)` | A competitor, a trademark or a pricing word (App Review Guideline 2.3.7). | Remove it from name, subtitle, keywords and promotional text. |
| `repeat word(s) already in the name or subtitle` (warning) | Apple combines words from name, subtitle and keywords; a repeat wastes bytes. | Remove the repeat from the keywords. |
| `aso storefronts`: `... both apply here and repeat keyword(s)` | A storefront indexes several locales: the United States also indexes `es-MX`, `pt-BR` and more; Türkiye indexes `en-GB` and `tr`. | Use different words per locale ([storefront-locales.md](storefront-locales.md)). Adding `en-GB` also changes what many storefronts fall back to. |
| The store page's **Languages** line lists fewer languages than your metadata | That line comes from the localizations inside the app bundle (`.lproj` folders), not from the store text; `CFBundleLocalizations` in Info.plist alone wasn't enough: **observed (Oct 2026)**. | Add a folder per language to the app, e.g. `tr.lproj/InfoPlist.strings`, and ship a new build. |

## Reports (`analytics`, `sales`, `reviews`)

| Message or symptom | What it means | Fix |
|---|---|---|
| `No active analytics report request` | Nothing was requested yet, or the request stopped. | `asc-release-kit analytics request --apply` (Admin key) and wait: data starts the next day, first files about 24-48 h later. |
| `no instances match (reports appear 1-2 days after the request ...)` | Too early, or another granularity. | Wait, or try `--granularity WEEKLY` / `--all`. |
| `stopped (inactive; request again)` / doctor: `the analytics report request stopped` | Apple stops requests whose reports nobody downloads for a long time. | Create a new request. |
| No data from before the request | `ONGOING` requests don't backfill. | `asc-release-kit analytics request --access-type ONE_TIME_SNAPSHOT --apply`. |
| Campaign rows (`ct=` links) missing | Hidden while fewer than five users are counted, and one to three days late: **observed (Oct 2026)**. | Wait; aggregate over longer periods. |
| `MD5 mismatch; not saved` / `size ... Apple says ...` | A damaged download. | Re-run; files already downloaded are skipped. |
| `No report named or identified as ...` | The name doesn't match a report of the active requests. | `asc-release-kit analytics list --reports` lists the names. |
| `no report (Apple answers 404 ...)` | No sales that day, or not published yet. | Normal; try another date. |
| `No report downloaded: Apple had none for the requested date(s)` | Exit code 1. | `--allow-missing` for scheduled jobs. |
| `... reports are DAILY only (Apple's table for Sales and Trends)` or `need --report-sub-type` | The report type, sub-type and frequency don't combine. | Use a combination from [analytics-sales-reviews.md](analytics-sales-reviews.md). |
| doctor: `Sales and Trends has no report for the latest day` | The vendor number can't be confirmed from an empty day. | `asc-release-kit sales download --date YYYY-MM-DD --out sales` with a recent day that had downloads. |
| `--json reports a summary; use --format json` | `reviews export` writes review data with `--format`. | `--format json --out reviews.json`. |

## Uploads (`upload-build`)

| Message | What it means | Fix |
|---|---|---|
| `xcodebuild needs a team API key` | xcodebuild requires an issuer ID. | Use a team key. |
| `Xcode's command-line tools (xcrun) aren't available here` | Not a Mac with Xcode. | Install Xcode; `xcode-select -p` shows the active one. |
| `Cloud signing permission error` / `You haven't been given access to cloud-managed distribution certificates` | `--archive` with automatic signing (the generated export options) lets Xcode sign with Apple's cloud-managed distribution certificate. API keys get that access only with the Admin role: App Store Connect can grant it to users, not to keys (reported on Apple's developer forums; Apple's docs don't spell it out). | Run `--archive` with an Admin key; or install the distribution certificate (with its private key) on the Mac and pass `--export-options` with `signingStyle` `manual` and your profiles; or sign the IPA yourself and use `--ipa`. |
| `No signing certificate "iOS Distribution" found` | Signing needs a distribution certificate with its private key, and none is installed on this Mac. | Import the certificate (`.p12`) into the login keychain, or let Xcode sign in the cloud (automatic signing, with an Admin key). |
| `No suitable application records were found` | App Store Connect has no app record with the build's bundle ID for this key's team: the app wasn't created yet, the bundle ID differs, or the key belongs to another team. | Create the app (Apps > + > New App) with exactly the build's bundle ID, or use a key of the team that owns the app. |
| `ITMS-90189` in the tool's output | This build number was already uploaded for this version. | Raise `CFBundleVersion`, archive again, upload. |
| `ITMS-90186` | The version number no longer takes builds (approved or released). | Raise `CFBundleShortVersionString`, archive again, upload. |
| `ITMS-90062` | The version number must be higher than the last approved one. | Raise `CFBundleShortVersionString`. |
| `The build's bundle ID is ..., but the configured app is ...` | The archive is another app. | Check `ASC_BUNDLE_ID` and the archive. |
| `manageAppVersionAndBuildNumber is on` (warning) | Xcode may renumber the build while uploading, so `release --build 42` wouldn't find it. | Set it to `false` in your export options. |
| `Build ... processing ended INVALID` | Apple rejected the processed build. | Apple's e-mail says why. |
| `Notarization status: Invalid` | Notarization failed. | `xcrun notarytool log <submission id>` shows why. |
| `failed (exit N)` with no known code | The tool failed; the last 40 lines are shown. | Read those lines; plan mode shows the exact command (with the key masked). |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, including plans; `doctor`: nothing failed |
| `1` | Validation or read-back verification failed; `doctor`: a check failed (or a warning with `--strict`); `sales download` got nothing without `--allow-missing` |
| `2` | Usage or configuration error, including credentials |
| `3` | App Store Connect API or network error |
| `4` | Stopped because of App Store Connect's current state; checks run before writes, so usually nothing was written |
| `70` | Unexpected error (`--verbose` adds a traceback, secrets masked) |
| `130` | Interrupted with Ctrl-C |
| `128+N` | Stopped by signal N (`143` for SIGTERM) |

Still stuck? Open an issue with the command, the exit code and the output (the kit
masks secrets, but replace the app ID and other identifiers with placeholders
anyway): <https://github.com/deegitech/asc-release-kit/issues>. Never paste the
`.p8` text, or the output of `security find-generic-password … -w`, into an issue
or a chat; if that happened, revoke the key ([setup.md, step 10](setup.md#10-expiry-renewal-and-rotation)).
