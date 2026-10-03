# Uploading builds (`upload-build`)

`upload-build` hands a build to Apple with your App Store Connect API key, using
Apple's own tools. It needs macOS with Xcode selected: `xcode-select -p` should
print a path inside Xcode, such as `/Applications/Xcode.app/Contents/Developer`, not
`/Library/Developer/CommandLineTools` (`asc-release-kit doctor` checks this). Without `--apply` it prints the exact
command it would run, with the key ID, issuer ID and key path masked. A plan doesn't
load the key (no key file read, no Keychain prompt, no AWS call), runs no tool and
writes no file; `asc-release-kit doctor --offline` is the way to test the key.

| Mode | Runs | Use it for |
|---|---|---|
| `--archive App.xcarchive` | `xcodebuild -exportArchive … -allowProvisioningUpdates -authenticationKeyPath … -authenticationKeyID … -authenticationKeyIssuerID …` | App Store and TestFlight uploads from an archive (recommended) |
| `--ipa App.ipa` | `xcrun altool --upload-package App.ipa -t ios --apple-id … --bundle-id … --bundle-short-version-string … --bundle-version … --api-key … --p8-file-path …` | an IPA built elsewhere |
| `--notarize App.zip` | `xcrun notarytool submit App.zip --key … --key-id … --issuer … --wait --output-format json` | **notarizing** macOS software distributed outside the Mac App Store (not an App Store upload) |

## Archives (`xcodebuild -exportArchive`)

With `destination: upload` in the export options, `xcodebuild -exportArchive`
signs and uploads in one step. If you don't pass `--export-options`, the kit
generates this plist:

| Key | Value | Why |
|---|---|---|
| `method` | `app-store-connect` | the current name (`app-store` is deprecated in Xcode's help) |
| `destination` | `upload` | upload instead of exporting locally |
| `manageAppVersionAndBuildNumber` | `false` | Xcode's default is `YES`, which lets Xcode change the build number while uploading; then `release --build 42` wouldn't find build 42 |
| `uploadSymbols` | `true` | crash reports get symbolicated |
| `signingStyle` | `automatic` | with `-allowProvisioningUpdates` and the API key, Xcode can create or download profiles |
| `teamID` | `--team-id`, if given | needed when the archive's team is ambiguous |

When you pass your own plist, the kit warns if `destination` isn't `upload`, if the
method isn't `app-store-connect`, or if `manageAppVersionAndBuildNumber` is on.
[examples/ExportOptions.plist](../examples/ExportOptions.plist) is a starting point.

`xcodebuild` requires an issuer ID, so this mode needs a **team** key.

With automatic signing (the generated plist), Xcode signs the archive with Apple's
cloud-managed distribution certificate. API keys can use it only with the **Admin**
role; an App Manager key fails with "Cloud signing permission error" (reported on
Apple's developer forums: App Store Connect can give users that access, but not
keys). With an App Manager key, install the distribution certificate and its private
key on the Mac and pass your own export options with `signingStyle` `manual` and the
provisioning profiles, or upload an IPA you signed yourself with `--ipa`.

## IPAs (`altool --upload-package`)

Bundle ID, version and build number are read from `Payload/<App>.app/Info.plist`
inside the IPA; the app's Apple ID comes from `--app` / `ASC_APP_ID`. The key is
passed with `--p8-file-path` (option names as printed by `xcrun altool --help` in
Xcode 26). Individual keys get `--api-key-subject user` instead of `--api-issuer`.
Apple's own help page still documents `altool --upload-app` with a username and
password; this kit uses the API key instead.

## Notarization (`notarytool`)

`notarytool` is for Developer ID distribution outside the Mac App Store; it doesn't
upload to App Store Connect. The kit waits for the result (`--wait`, 60 minutes by
default or `--wait-processing MINUTES`) and exits 1 unless the status is
`Accepted`. Apple's API-key documentation says individual keys can't use
notarytool; the kit warns but lets you try.

## When an upload fails

On a failure the last 40 lines of the tool's output are shown. When they contain one
of Apple's upload validation codes, the error ends with a `fix:` line: ITMS-90189
means the build number was already uploaded for this version (raise
`CFBundleVersion`); ITMS-90186 and ITMS-90062 mean the version number no longer
takes builds or must be higher than the last approved one (raise
`CFBundleShortVersionString`). "Cloud signing permission error", "No signing
certificate" and "No suitable application records were found" get a `fix:` line
too. More in [troubleshooting.md](troubleshooting.md#uploads-upload-build).

## After the upload

Apple then processes the build, usually within minutes but sometimes much longer.
`--wait-processing MINUTES` polls `GET /v1/builds` until the build is `VALID` (or
fails), and `release --wait-for-build MINUTES` does the same before a release.

## How the key reaches the tools

These tools need the key as a file path. If your key already is a file
(`ASC_PRIVATE_KEY_PATH`), that path is used (after the kit has checked it). Otherwise
(environment variable, Keychain or SSM) the kit writes a copy named
`AuthKey_<KEY_ID>.p8` with mode `0600` into a fresh `0700` temporary directory and
deletes it when the tool exits, even if it fails. The tools run with
`ASC_PRIVATE_KEY` removed from their environment, so the PEM text doesn't reach
them or anything they start. Their output is streamed with the key ID, issuer ID and
paths masked (odd bytes are replaced rather than aborting the run); on failure the
last 40 lines are shown.

If the kit is stopped while a tool runs (Ctrl-C, SIGTERM from a cancelled CI job,
SIGHUP), it stops the tool first and then removes the temporary copy; the exit code
is 130 for Ctrl-C and 128 + the signal number otherwise (143 for SIGTERM).

Caveats: the key ID and issuer ID appear in the tool's arguments, which other
processes of the same user can see while it runs. A `SIGKILL` or a crash of the
machine in the middle of an upload would leave the temporary copy behind.

## Not implemented (yet)

- Apple's BuildUploads API (announced at WWDC25) for uploading without Xcode tools.
- Transporter (`iTMSTransporter`).
- Building the archive itself: run `xcodebuild archive` (or your existing build
  script) first.
