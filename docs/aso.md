# App Store metadata (`aso pull`, `aso validate`, `aso apply`, `aso storefronts`)

Keep all of your store text in one JSON file under version control, check it
offline, and push it to App Store Connect with read-back verification.

## Starting from what you have (`aso pull`)

```sh
asc-release-kit aso pull --out aso.json               # the editable version, else the live one
asc-release-kit aso pull --version 1.1.0 --out aso.json --force
```

`aso pull` only reads from App Store Connect. It writes the file described below
with every non-empty field of each locale: name, subtitle and privacy URLs from the
App Info localization (the editable record while a version is being prepared,
otherwise the live one), and keywords, promotional text, description and URLs from
the version localization. What's New is left out on purpose: it belongs to one
release, and `release --whats-new` sets it. The file gets mode `0600`, an existing
file is only replaced with `--force`, and the command ends with a validation summary
of what it wrote.

## File format

```json
{
  "app": {"id": "1234567890", "version": "1.2.0", "platform": "IOS"},
  "denylist": ["SomeCompetitor", "free"],
  "localizations": {
    "en-US": {
      "name": "…", "subtitle": "…", "keywords": "one,two,three",
      "promotionalText": "…", "description": "…", "whatsNew": "…",
      "supportUrl": "https://…", "marketingUrl": "https://…",
      "privacyPolicyUrl": "https://…", "privacyChoicesUrl": "https://…"
    }
  }
}
```

- `app` is optional; `--app`, `--version` and `--platform` override it. Without
  `--app`, `aso apply` uses `app.id`, and when `ASC_APP_ID` or the config file names
  an app too, the two must agree (otherwise the command stops and asks for `--app`).
  The platform works the same way. This keeps a file written for one app from being
  applied to another.
- `denylist` is optional and adds to `--denylist FILE`.
- Locale keys use App Store Connect codes (`en-US`, `de-DE`, `ja`, `zh-Hans`, … all
  50 are listed in [storefront-locales.md](storefront-locales.md)). Case is
  forgiven with a warning.
- Only fields present in the file are written. Leave a field out to leave it alone.
- Keys starting with `_` are comments (`"_note": "..."`).
- Text is normalized before comparing and sending: CRLF becomes LF and surrounding
  whitespace is trimmed.

Which record each field lives on:

| App Info localization | Version localization |
|---|---|
| `name`, `subtitle`, `privacyPolicyUrl`, `privacyChoicesUrl`, `privacyPolicyText` (tvOS) | `description`, `keywords`, `promotionalText`, `whatsNew`, `supportUrl`, `marketingUrl` |

## Validation rules

Errors fail `aso validate` (exit 1) and stop `aso apply` before any request.
Warnings are printed; `--strict` makes them fail too.

| Rule | Level | Source |
|---|---|---|
| name 2-30 characters, subtitle ≤ 30 | error | Apple, App information |
| promotional text ≤ 170, description ≤ 4,000, What's New ≤ 4,000 characters | error | Apple, Platform version information |
| keywords ≤ **100 bytes** (UTF-8: most non-Latin letters take 2-3 bytes) | error | Apple: "up to 100 bytes" |
| name, description, keywords, support URL present but empty | error | required by App Store Connect |
| unknown locale code | error | Apple's locale shortcodes |
| denylisted term in name, subtitle, keywords or promotional text (configurable with `--denylist-fields`) | error | App Review Guideline 2.3.7 |
| URL not a valid http(s) URL | error | |
| spaces around commas in keywords (they cost bytes) | warning | |
| empty keyword between commas, leading or trailing comma | warning | |
| duplicate keyword | warning | |
| Latin-script keyword of two characters or fewer | warning | Apple: keywords "each greater than two characters" |
| keyword word already in the name or subtitle; name and subtitle sharing a word | warning | Apple: the app name is already searchable, "you shouldn't duplicate these values in the keyword list" |
| description that looks like HTML | warning | Apple: plain text only |
| `http://` URL | warning | |
| text within the limit in code points but over it in UTF-16 units (emoji) | warning | App Store Connect's counting isn't documented |
| empty What's New | warning | required for updates |
| unknown field name (typo) | warning | ignored when applying |

Keywords may be separated with the English comma or the full-width (Chinese) comma.
The duplicate-word heuristic ignores words shorter than three characters (two for
non-Latin scripts) and a handful of English stop words.

### The denylist

Guideline 2.3.7 asks for metadata without trademarked terms, other apps' names,
pricing terms such as "free", or other irrelevant phrases used to game search. Put
the terms you never want in your metadata in a text file, one per line:

```text
# competitors and brands (a "# " line is a comment)
SomeCompetitor
Another Brand
# pricing words
free
#1
```

Matching is case-insensitive and whole-word for terms that start and end with a
letter or digit, so `Uno` doesn't match "unordered". A line that is just `#` or
starts with `# ` is a comment; `#1` is a term.

## Applying

```sh
asc-release-kit aso apply aso.json            # plan: what would change, per locale and field
asc-release-kit aso apply aso.json --apply    # write, then read back and compare
```

- The target version must be editable (`PREPARE_FOR_SUBMISSION`, `READY_FOR_REVIEW`,
  or rejected). Name and subtitle need the editable App Info record that App Store
  Connect opens while a version is being prepared.
- Apple documents promotional text and the support, marketing and privacy URLs as
  editable at any time. Restrict a run to them with `--fields` to update a **live**
  version: `aso apply aso.json --version 1.1.0 --fields promotionalText --apply`.
- What's New is skipped for an app's first version (Apple doesn't accept it there).
- A new locale needs at least a `name`. The kit creates the App Info localization,
  then the version localization.
- **Observed October 2026:** creating an App Info localization made App Store
  Connect create the version localization by itself, and the kit's own create then
  failed with 409. The kit now re-reads before creating, and on a 409 re-reads and
  updates.
- Unchanged fields are not sent. A second run writes nothing.
- After writing, the kit reads every touched localization back and compares field by
  field (URLs ignoring a trailing slash). A mismatch fails the run with exit code 1.
- `aso apply` never submits anything for review.

`--fields description,keywords` and `--locales en-US,de-DE` narrow a run (locale
codes are accepted in any case, like in the file).

## Storefront coverage

```sh
asc-release-kit aso storefronts aso.json                      # summary + keyword overlaps
asc-release-kit aso storefronts aso.json --all                # every storefront your locales reach
asc-release-kit aso storefronts --locales en-GB,tr --storefront TUR
```

The data comes from Apple's table ([storefront-locales.md](storefront-locales.md)).
By default the command prints how many storefronts use one of your locales as the
default language and how many support at least one of them, plus every storefront
where two of your locales repeat a **keyword**. `--all` or `--storefront` lists the
storefronts themselves: the default language (and whether you provide it) and your
other supported locales. A storefront that supports none of your locales is marked
as such: shoppers there see your primary language.

Why overlaps matter: Apple says a locale's keywords are searchable in every
storefront that supports its language, so in Germany both your `de-DE` and `en-GB`
keyword fields apply, and a word in both is likely wasted bytes. How much each field
weighs in ranking isn't documented, so treat the warning as advice.
