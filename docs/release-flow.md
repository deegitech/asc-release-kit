# How `release` works

`release` prepares one App Store version and, with `--submit`, sends it to App
Review. It reads everything it needs first, refuses early when App Store Connect is
in a state that won't work, and only then writes. Without `--apply` it stops after
printing the plan.

```sh
asc-release-kit release --version 1.2.0 --build 42 --whats-new whats-new.json [--apply [--submit]]
asc-release-kit release --version 1.2.0 --build 42 --whats-new-text "Bug fixes." --apply --submit
```

What's New comes from `--whats-new` (a JSON object of locale to text, or an ASO
file), from `--whats-new-text` (one text for every locale the file doesn't list),
or from a `"*"` entry in the file (the same, written in the file). For the locales
it covers, the kit replaces whatever was typed in App Store Connect.

## 1. Read and refuse early (no writes)

| Check | Request | Refuses when |
|---|---|---|
| The build exists and is processed | `GET /v1/builds?filter[app]=…&filter[version]=42&filter[preReleaseVersion.version]=1.2.0&filter[preReleaseVersion.platform]=IOS` | not found, `PROCESSING` (unless `--wait-for-build`), `INVALID`/`FAILED`, or expired |
| Export compliance | the same build's `usesNonExemptEncryption` | unanswered: a warning, or a refusal with `--apply --submit` |
| Versions of the app | `GET /v1/apps/{id}/appStoreVersions?filter[platform]=IOS` | another version is waiting for review, in review or pending release; or another editable version exists (unless `--reuse-editable`) |
| Live version's App Review details and Game Center link | `GET /v1/appStoreVersions/{live}/appStoreReviewDetail`, `…/gameCenterAppVersion` | (read for copying later) |
| What's New locales | `GET /v1/appStoreVersions/{id}/appStoreVersionLocalizations` (`locale,whatsNew`) | the file names a locale the version doesn't have |
| What's New coverage | the same localizations (a new version gets the live version's locales, with What's New empty) | with `--apply --submit`: some locale would be left without What's New. Otherwise a warning |
| Open review submissions | `GET /v1/reviewSubmissions?filter[app]=…&filter[platform]=IOS&filter[state]=…` | with `--apply --submit`: a submission is `WAITING_FOR_REVIEW`, `IN_REVIEW`, `UNRESOLVED_ISSUES`, `CANCELING` or `COMPLETING`. In a plan, a warning |

State names: the kit reads both `appVersionState` and the deprecated
`appStoreState`, preferring the newer field. If the requested version is already
`WAITING_FOR_REVIEW`, `IN_REVIEW`, pending release or live, `release` reports it and
exits 0 without writing anything.

## 2. Get a version record

- **New version:** `POST /v1/appStoreVersions` with the version string, platform,
  optional `releaseType`/`earliestReleaseDate`, and the build relationship. App Store
  Connect copies the previous version's localizations into it (observed; the kit
  checks rather than assumes, see step 3).
- **Existing editable version** (`PREPARE_FOR_SUBMISSION`, `READY_FOR_REVIEW`,
  `DEVELOPER_REJECTED`, `REJECTED`, `METADATA_REJECTED`, `INVALID_BINARY`): reused.
- **Another editable version with a different number:** with `--reuse-editable`,
  renamed with `PATCH /v1/appStoreVersions/{id}` (`versionString`).

## 3. Bring the version in line (each step only if needed)

| Step | Request |
|---|---|
| Release type / scheduled date | `PATCH /v1/appStoreVersions/{id}` (the date is compared as a moment in time, so a reformatted value isn't rewritten) |
| Attach the build | `PATCH /v1/appStoreVersions/{id}/relationships/build` |
| What's New per locale | `PATCH /v1/appStoreVersionLocalizations/{id}` (`whatsNew`) |
| App Review details, if the version has none | `POST /v1/appStoreReviewDetails` with the live version's contact, demo-account and notes fields (notes from `--review-notes` when given) |
| App Review notes, if `--review-notes` differs | `PATCH /v1/appStoreReviewDetails/{id}` |
| Game Center link (`--game-center auto` mirrors the live version) | `POST /v1/gameCenterAppVersions`, or `PATCH /v1/gameCenterAppVersions/{id}` with `enabled: true` |

The Game Center step runs after the build step on purpose: changing the build has
been observed to switch the link off.

Contact details are never printed. In the journal they appear as `[redacted]`, and
notes as a length and a short hash.

## 4. Verify by reading back

The kit re-reads the version, its build, every localization's What's New, the App
Review details and the Game Center link:

- the version is still editable and its version string is the one requested (a
  rename that didn't stick fails here);
- release type and earliest release date are what you asked for;
- the build is the one requested;
- What's New matches for every locale the kit set. With `--submit`, it **must not be
  empty for any locale** (App Store Connect requires it for updates); without
  `--submit`, an empty one is a warning;
- contact first and last name, phone and e-mail are filled in (values not shown),
  and demo-account fields are filled in when a demo account is required;
- notes match `--review-notes` when given;
- the Game Center link is on when the live version uses Game Center.

Any failure stops the command with exit code 1. Nothing is submitted. With
`--json`, the failed checks are in the output's `events`.

## 5. Submit (`--apply --submit`)

1. `GET /v1/reviewSubmissions?filter[app]=…&filter[platform]=IOS&filter[state]=…`:
   stop if a submission is `WAITING_FOR_REVIEW`, `IN_REVIEW`, `UNRESOLVED_ISSUES`,
   `CANCELING` or `COMPLETING`. This is checked once before any write (step 1 of the
   whole flow) and again here. (Resubmitting a rejected submission that is in
   `UNRESOLVED_ISSUES` isn't automated yet; do that in App Store Connect.)
2. Use the draft (`READY_FOR_REVIEW`) submission, or create one with
   `POST /v1/reviewSubmissions`.
3. If the version isn't an item yet, `POST /v1/reviewSubmissionItems`. A 409 or 5xx
   here isn't trusted either way: the next step decides.
4. **Re-read `GET /v1/reviewSubmissions/{id}/items` and assert that an item whose
   `appStoreVersion` is this version exists and isn't `REMOVED` or `REJECTED`.** If
   not, stop with exit code 1 and don't submit. Other items in the draft (in-app
   purchases, in-app events, Game Center versions you added in App Store Connect) are
   listed so you can see what goes to review together.
5. `PATCH /v1/reviewSubmissions/{id}` with `submitted: true`. A 5xx here is followed
   by a read-back rather than a retry.
6. Read the submission back: it must be `WAITING_FOR_REVIEW` (or `IN_REVIEW`) and
   must still contain the version.

Why step 4 exists: in September 2026 the item for the app version failed with HTTP
500 while other items were added, and the submit call then succeeded, so the
submission went to App Review without the app. See
[observed-behaviour.md](observed-behaviour.md).

## Re-running

Each step compares with App Store Connect before writing, so re-running after a
failure continues where it stopped, and re-running after success writes nothing.
