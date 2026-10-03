# Observed App Store Connect behaviour

Things we saw while shipping an iOS app through the App Store Connect API in
September and October 2026, and how the kit reacts. These are **observations**
(with dates), not documented rules: Apple may have changed or fixed them since.
Documented behaviour is linked to Apple's pages.

## A submission went to review without the app version (observed September 2026)

- `POST /v1/reviewSubmissionItems` for the app version returned **HTTP 500**.
- Adding the other items of the same draft succeeded.
- `PATCH /v1/reviewSubmissions/{id}` with `submitted: true` then returned **200**,
  and the submission went to App Review **without the app version**.
- Retries over the next ten minutes, including on a brand-new draft submission,
  also returned 500 (the legacy `POST /v1/appStoreVersionSubmissions` returned 403)
  before a later attempt succeeded.

**What the kit does:** after adding the version it always re-reads the
submission's items and submits only if an item for that version exists and isn't
`REMOVED` or `REJECTED`. It never retries a write after a 5xx or a missing answer;
in the submission steps it re-reads instead and tells you to check App Store
Connect.

## The version localization was created automatically (observed October 2026)

- `POST /v1/appInfoLocalizations` for a locale the app didn't have yet succeeded.
- The following `POST /v1/appStoreVersionLocalizations` for the same locale
  returned **409**: App Store Connect had already created the version localization
  on its own.

**What the kit does:** `aso apply` re-reads the version's localizations before
creating one, and on a 409 re-reads and updates the existing one.

## Changing the build switched the Game Center link off (observed 2026-09)

- `PATCH /v1/appStoreVersions/{id}/relationships/build` replaced the build of a
  version that was linked to Game Center.
- Afterwards the version's `gameCenterAppVersion` had `enabled: false`, although
  both builds had the same Game Center entitlement.

**What the kit does:** `release` checks the Game Center link after the build step,
re-enables it with `PATCH /v1/gameCenterAppVersions/{id}` when it's off, and
verifies it by reading back.

## New versions start with the previous version's localizations (observed 2026-09)

Creating a version with `POST /v1/appStoreVersions` gave it the localizations of the
previous version. The kit relies on this for What's New, but checks it: if a locale
in your What's New file is missing on the version, `release` stops before writing.

## Sales reports answer 404 when there's nothing to report

Developers report `404` with "There were no sales for the date specified" when a
day had no sales or the report isn't out yet (Apple Developer Forums). The kit
reports such days as missing instead of failing with an API error.

## Documented by Apple (for reference)

- Tokens may live at most 20 minutes for most endpoints; individual keys use
  `sub: "user"` instead of `iss`
  ([Generating tokens for API requests](https://developer.apple.com/documentation/appstoreconnectapi/generating-tokens-for-api-requests)).
- `appStoreState` is deprecated in favour of `appVersionState` on versions and
  `state` on app infos (API reference).
- Individual keys can't use provisioning endpoints, Sales and Finance, or notarytool
  ([Creating API keys](https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api)).
- Analytics: first reports in one to two days; requests stop after long inactivity
  ([Downloading analytics reports](https://developer.apple.com/documentation/appstoreconnectapi/downloading-analytics-reports)).
- What's New isn't available for an app's first version; keywords are limited to
  100 bytes
  ([Platform version information](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information)).

If you see different behaviour, please open an issue with the date, the endpoint and
the status code (never include keys, tokens or personal data).
