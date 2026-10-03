#!/usr/bin/env python3
"""Regenerate docs/storefront-locales.md from src/asc_release_kit/storefronts.py.

When Apple updates its table, edit the table in storefronts.py (keep the retrieval
date current), run this script, and commit both files. tests/test_storefronts.py
fails if the two drift apart.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from asc_release_kit.storefronts import (  # noqa: E402
    LOCALE_CODES_URL,
    LOCALE_TO_LANGUAGE,
    RETRIEVED,
    SOURCE_URL,
    STOREFRONTS,
)

HEADER = f"""# Storefront locales: which localizations each App Store storefront supports

This table lists, for every App Store storefront, the **default language** and the
**additional languages** Apple says the storefront supports for app metadata, with
the App Store Connect locale code for each language.

- Source: Apple, [App Store localizations]({SOURCE_URL}) (App Store Connect Help).
- Locale codes: Apple, [Managing metadata in your app by using locale shortcodes]({LOCALE_CODES_URL}).
- Transcribed on **{RETRIEVED}**. Apple edits this table from time to time, so check
  the source before you rely on a single row.

`asc-release-kit aso storefronts aso.json` prints the same information for the
locales in your own metadata file, and flags keyword words that two of your locales
share inside one storefront.

## What Apple says, and what it doesn't

- Apple ([Localize app information](https://developer.apple.com/help/app-store-connect/manage-app-information/localize-app-information/)),
  using French as its example: "Users can search for your app using localized
  keywords in all countries or regions where the App Store supports French." So a
  locale's keywords are searchable in every storefront that supports its language.
- Apple, same page: "If no localization matches a user's language setting, the next
  most relevant localization is used. In other countries or regions, your metadata
  displays in the primary language."
- Apple doesn't document how much keywords from a storefront's *additional*
  languages weigh in ranking, or whether words from different locales combine into
  phrases. Claims about that come from ASO vendors' testing. This kit therefore only
  *warns* when two of your locales that apply in the same storefront repeat a word
  (a likely waste of keyword bytes); it never blocks on it.

## Examples worth knowing

- **United States (USA):** English (U.S.) `en-US` by default, and also Spanish
  (Mexico) `es-MX`, Portuguese (Brazil) `pt-BR`, French `fr-FR`, Chinese
  (Simplified and Traditional), Korean, Russian, Arabic and Vietnamese.
- **Türkiye (TUR):** English (U.K.) `en-GB` by default, plus Turkish `tr`. If your
  app has no `en-GB` localization, shoppers there see one of your other languages.
- **Most storefronts default to English (U.K.)**, so an `en-GB` localization reaches
  far more storefronts than its name suggests.
- **Brazil, Mexico and most of Latin America** also support English (U.K.).
- **Japan** supports English (U.S.) next to Japanese; most other storefronts use
  English (U.K.) as their English variant.

"""


def language(code: str) -> str:
    return f"{LOCALE_TO_LANGUAGE[code]} `{code}`"


def render() -> str:
    lines = [HEADER.rstrip(), "", "## Locale codes (50)", "", "| Language | Code |", "|---|---|"]
    for code, name in sorted(LOCALE_TO_LANGUAGE.items(), key=lambda item: item[1]):
        lines.append(f"| {name} | `{code}` |")
    lines += [
        "",
        f"## Storefronts ({len(STOREFRONTS)})",
        "",
        "| Code | Storefront | Default language | Additional languages |",
        "|---|---|---|---|",
    ]
    for sf in STOREFRONTS:
        extra = ", ".join(language(code) for code in sf.additional) or "-"
        lines.append(f"| {sf.code} | {sf.name} | {language(sf.default)} | {extra} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    target = os.path.join(ROOT, "docs", "storefront-locales.md")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(render())
    print(f"wrote {os.path.relpath(target, ROOT)}")
