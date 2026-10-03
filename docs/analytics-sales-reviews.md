# Analytics, sales and reviews

All three only read from App Store Connect, except `analytics request`, which
creates a report request and therefore needs `--apply`. What they download is
private to you: files are written with mode `0600` (an existing file is tightened
too) into directories created with mode `0700`, because reports hold units and
proceeds and reviews carry reviewer nicknames.

## Analytics Reports API

Apple's flow ("Downloading analytics reports"):

1. **Request** reports for an app once: `ONGOING` (daily, weekly and monthly data
   from now on, the usual choice) or `ONE_TIME_SNAPSHOT` (history up to today, no new
   data afterwards). Apple says the first reports take one to two days.
2. Apple generates **reports** (for example "App Store Discovery and Engagement
   Standard"), grouped by category: `APP_USAGE`, `APP_STORE_ENGAGEMENT`, `COMMERCE`,
   `FRAMEWORK_USAGE`, `PERFORMANCE`. Some come in Standard and Detailed variants.
3. Each report has **instances** per granularity (`DAILY`, `WEEKLY`, `MONTHLY`) and
   processing date.
4. Each instance has **segments**: pre-signed file URLs with an MD5 `checksum` and
   `sizeInBytes`.

```sh
asc-release-kit analytics request --apply                       # skips if an active ONGOING request exists
asc-release-kit analytics list --reports                        # requests and their reports
asc-release-kit analytics download --report "App Store Discovery and Engagement Standard" \
  --granularity DAILY --since 2030-02-01 --out analytics --decompress
```

- `download` picks the report by exact name (case-insensitive) or ID among active
  requests, preferring `ONGOING`. By default it downloads the newest instance;
  `--date`, `--since` or `--all` choose others.
- Files land in `OUT/<report-name>/<granularity>/<processing-date>/part-NNN.csv.gz`
  (`.csv` too with `--decompress`). Apple describes the content as CSV data; the kit
  doesn't parse it.
- Every segment is checked against Apple's size and MD5 before it's kept, and
  written atomically. Segments already on disk with the right checksum are skipped.
- The processing date becomes a directory name, so only `YYYY-MM-DD` is accepted
  (anything else is skipped with a warning), and no file is written outside `OUT`.
- Segment URLs are fetched **without** the API token.
- Apple: if you don't retrieve data for a long time, the request becomes
  `stoppedDueToInactivity` and you need a new one. `analytics list` shows that.
- Apple's analytics docs say creating or deleting requests needs the Admin role;
  listing and downloading also works with Sales and Reports or Finance.

## Sales and Trends (`sales download`)

```sh
asc-release-kit sales download --vendor 87654321 --date 2030-03-01 --decompress
asc-release-kit sales download --from 2030-02-01 --to 2030-02-28
asc-release-kit sales download --frequency MONTHLY --date 2030-02
asc-release-kit sales download --report-type SUBSCRIPTION --report-version 1_3 --date 2030-03-01
asc-release-kit sales download --date 2030-03-01 --allow-missing     # e.g. a nightly job
```

- Endpoint: `GET /v1/salesReports` with `filter[frequency]`, `filter[reportType]`,
  `filter[reportSubType]`, `filter[vendorNumber]`, optional `filter[reportDate]` and
  `filter[version]`. Apple returns a gzip file (`application/a-gzip`) of
  tab-separated text.
- Dates: `YYYY-MM-DD` for daily and weekly reports, `YYYY-MM` monthly, `YYYY` yearly.
  A daily report without `--date` asks Apple for the latest one. `--from/--to`
  downloads a daily range (up to 400 days per run).
- **Valid combinations.** Apple's reference for this endpoint has a table of which
  sub-types, frequencies and versions each report type allows. The kit checks your
  request against it (transcribed in October 2026) before calling Apple:

  | Report type | Sub-type | Frequency | Version |
  |---|---|---|---|
  | `SALES`, `PRE_ORDER` | `SUMMARY` | daily, weekly, monthly, yearly | `1_0` |
  | `NEWSSTAND` | `DETAILED` | daily, weekly | `1_0` |
  | `SUBSCRIPTION`, `SUBSCRIPTION_EVENT` | `SUMMARY` | daily | `1_3` |
  | `SUBSCRIBER` | `DETAILED` | daily | `1_3` |
  | `SUBSCRIPTION_OFFER_CODE_REDEMPTION`, `WIN_BACK_ELIGIBILITY` | `SUMMARY` | daily | `1_0` |
  | `FIRST_ANNUAL` | `DETAILED` / `SUMMARY` | daily / yearly | `1_0` |
  | `INSTALLS` | `SUMMARY`, `DETAILED` | monthly | `1_2` |
  | `INSTALLS` | `DETAILED`, `SUMMARY_CHANNEL`, `SUMMARY_INSTALL_TYPE`, `SUMMARY_TERRITORY` | yearly | `1_0`, `1_1` |

  Without `--report-sub-type` the kit takes the only sub-type the table allows for
  the type and frequency, or `SUMMARY` when it is one of several; otherwise it asks
  you to choose. A version that isn't in the table is sent anyway, with a warning.
- Report versions: subscription reports (`SUBSCRIPTION`, `SUBSCRIPTION_EVENT`,
  `SUBSCRIBER`) are requested as `1_3` unless you pass `--report-version`, because
  Apple says `1_2` is no longer available. For the others no `filter[version]` is
  sent and Apple picks one; pin it with `--report-version 1_0` (for example) when
  your parser needs fixed columns. Developers have reported Apple briefly rejecting
  a documented version in the past (Apple Developer Forums, January 2024).
- A 404 means "no report for that date": no sales that day, or not published yet.
  In a range, missing days are listed and the run succeeds if anything downloaded.
  When nothing at all was downloaded, the command exits `1`; with `--allow-missing`
  it exits `0` (useful for a scheduled job that pulls yesterday's report).
- The vendor number is masked in all output. Files are named
  `sales-<type>-<subtype>-<frequency>-<date>.tsv.gz` (no vendor number) and written
  with mode `0600`.
- Apple: individual keys can't access Sales and Finance; use a team key whose role
  can see Sales and Trends.

## Customer reviews (`reviews export`)

```sh
asc-release-kit reviews export --format csv --out reviews.csv
asc-release-kit reviews export --format jsonl --since 2030-02-01 --territory USA --rating 1
```

- Endpoint: `GET /v1/apps/{id}/customerReviews`, newest first, including the
  developer response (body, state, last modified) when there is one.
- Columns: `id, createdDate, rating, territory, title, body, reviewerNickname,
  responseBody, responseState, responseLastModifiedDate`. `--no-nicknames` leaves
  the reviewer nickname out.
- `--since` stops at the first review older than the date; `--limit` caps the count.
- **CSV injection guard:** cells starting with `=`, `+`, `-`, `@`, tab or carriage
  return get a leading apostrophe so spreadsheet apps don't execute them (OWASP's
  advice). `--raw-csv` turns this off.
- `--out` files are written with mode `0600`, also when the file already existed
  with looser permissions.
