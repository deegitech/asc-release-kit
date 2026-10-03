"""Which App Store localizations each storefront supports, from Apple's reference table.

Source: Apple, "App Store localizations" (App Store Connect Help),
https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations
Locale codes: Apple, "Managing metadata in your app by using locale shortcodes" (App Store Connect API).
Transcribed on 2026-10-03. Apple changes this table from time to time; check the source
before you rely on a single row.

Apple says users can search with a locale's keywords in every storefront that
supports its language ("Localize app information"). How much those keywords weigh
in ranking is not documented by Apple; treat overlap advice built on it as a
heuristic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

RETRIEVED = "2026-10-03"
SOURCE_URL = "https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations"
LOCALE_CODES_URL = (
    "https://developer.apple.com/documentation/appstoreconnectapi/"
    "managing-metadata-in-your-app-by-using-locale-shortcodes"
)

#: Apple's language names (as used in the storefront table) -> App Store Connect locale codes.
LANGUAGE_TO_LOCALE: dict[str, str] = {
    "Arabic": "ar-SA",
    "Bangla": "bn-BD",
    "Catalan": "ca",
    "Chinese (Simplified)": "zh-Hans",
    "Chinese (Traditional)": "zh-Hant",
    "Croatian": "hr",
    "Czech": "cs",
    "Danish": "da",
    "Dutch": "nl-NL",
    "English (Australia)": "en-AU",
    "English (Canada)": "en-CA",
    "English (U.K.)": "en-GB",
    "English (U.S.)": "en-US",
    "Finnish": "fi",
    "French": "fr-FR",
    "French (Canada)": "fr-CA",
    "German": "de-DE",
    "Greek": "el",
    "Gujarati": "gu-IN",
    "Hebrew": "he",
    "Hindi": "hi",
    "Hungarian": "hu",
    "Indonesian": "id",
    "Italian": "it",
    "Japanese": "ja",
    "Kannada": "kn-IN",
    "Korean": "ko",
    "Malay": "ms",
    "Malayalam": "ml-IN",
    "Marathi": "mr-IN",
    "Norwegian": "no",
    "Odia": "or-IN",
    "Polish": "pl",
    "Portuguese (Brazil)": "pt-BR",
    "Portuguese (Portugal)": "pt-PT",
    "Punjabi": "pa-IN",
    "Romanian": "ro",
    "Russian": "ru",
    "Slovak": "sk",
    "Slovenian": "sl-SI",
    "Spanish (Mexico)": "es-MX",
    "Spanish (Spain)": "es-ES",
    "Swedish": "sv",
    "Tamil": "ta-IN",
    "Telugu": "te-IN",
    "Thai": "th",
    "Turkish": "tr",
    "Ukrainian": "uk",
    "Urdu": "ur-PK",
    "Vietnamese": "vi",
}

LOCALE_TO_LANGUAGE: dict[str, str] = {code: name for name, code in LANGUAGE_TO_LOCALE.items()}

#: Every locale App Store Connect accepts for app metadata (50 as of the retrieval date).
ASC_LOCALES: frozenset[str] = frozenset(LANGUAGE_TO_LOCALE.values())
_LOWER = {code.lower(): code for code in ASC_LOCALES}

# ISO 3166-1 alpha-3 | storefront | default language | additional languages (";"-separated)
_TABLE = """AFG|Afghanistan|English (U.K.)|
ALB|Albania|English (U.K.)|
DZA|Algeria|English (U.K.)|Arabic;French
AGO|Angola|English (U.K.)|
AIA|Anguilla|English (U.K.)|
ATG|Antigua and Barbuda|English (U.K.)|
ARG|Argentina|Spanish (Mexico)|English (U.K.)
ARM|Armenia|English (U.K.)|
AUS|Australia|English (Australia)|English (U.K.)
AUT|Austria|German|English (U.K.)
AZE|Azerbaijan|English (U.K.)|
BHS|Bahamas|English (U.K.)|
BHR|Bahrain|English (U.K.)|Arabic
BRB|Barbados|English (U.K.)|
BLR|Belarus|English (U.K.)|
BEL|Belgium|English (U.K.)|Dutch;French
BLZ|Belize|English (U.K.)|Spanish (Mexico)
BEN|Benin|English (U.K.)|French
BMU|Bermuda|English (U.K.)|
BTN|Bhutan|English (U.K.)|
BOL|Bolivia|Spanish (Mexico)|English (U.K.)
BIH|Bosnia and Herzegovina|English (U.K.)|Croatian
BWA|Botswana|English (U.K.)|
BRA|Brazil|Portuguese (Brazil)|English (U.K.)
VGB|British Virgin Islands|English (U.K.)|
BRN|Brunei|English (U.K.)|
BGR|Bulgaria|English (U.K.)|
BFA|Burkina Faso|English (U.K.)|French
KHM|Cambodia|English (U.K.)|French
CMR|Cameroon|French|English (U.K.)
CAN|Canada|English (Canada)|French (Canada)
CPV|Cape Verde|English (U.K.)|
CYM|Cayman Islands|English (U.K.)|
TCD|Chad|English (U.K.)|French
CHL|Chile|Spanish (Mexico)|English (U.K.)
CHN|China mainland|Chinese (Simplified)|English (U.K.)
COL|Colombia|Spanish (Mexico)|English (U.K.)
COD|Congo, Democratic Republic of the|English (U.K.)|French
COG|Congo, Republic of the|English (U.K.)|French
CRI|Costa Rica|Spanish (Mexico)|English (U.K.)
CIV|Côte d'Ivoire|French|English (U.K.)
HRV|Croatia|English (U.K.)|Croatian
CYP|Cyprus|English (U.K.)|Greek;Turkish
CZE|Czechia|English (U.K.)|Czech
DNK|Denmark|English (U.K.)|Danish
DMA|Dominica|English (U.K.)|
DOM|Dominican Republic|Spanish (Mexico)|English (U.K.)
ECU|Ecuador|Spanish (Mexico)|English (U.K.)
EGY|Egypt|English (U.K.)|Arabic;French
SLV|El Salvador|Spanish (Mexico)|English (U.K.)
EST|Estonia|English (U.K.)|
SWZ|Eswatini|English (U.K.)|
FJI|Fiji|English (U.K.)|
FIN|Finland|English (U.K.)|Finnish
FRA|France|French|English (U.K.)
GAB|Gabon|French|English (U.K.)
GMB|Gambia|English (U.K.)|
GEO|Georgia|English (U.K.)|
DEU|Germany|German|English (U.K.)
GHA|Ghana|English (U.K.)|
GRC|Greece|English (U.K.)|Greek
GRD|Grenada|English (U.K.)|
GTM|Guatemala|Spanish (Mexico)|English (U.K.)
GNB|Guinea-Bissau|English (U.K.)|French
GUY|Guyana|English (U.K.)|French
HND|Honduras|Spanish (Mexico)|English (U.K.)
HKG|Hong Kong|Chinese (Traditional)|English (U.K.)
HUN|Hungary|English (U.K.)|Hungarian
ISL|Iceland|English (U.K.)|
IND|India|English (U.K.)|Bangla;Gujarati;Hindi;Kannada;Malayalam;Marathi;Odia;Punjabi;Tamil;Telugu;Urdu
IDN|Indonesia|English (U.K.)|Indonesian
IRQ|Iraq|English (U.K.)|Arabic
IRL|Ireland|English (U.K.)|
ISR|Israel|English (U.K.)|Hebrew
ITA|Italy|Italian|English (U.K.)
JAM|Jamaica|English (U.K.)|
JPN|Japan|Japanese|English (U.S.)
JOR|Jordan|English (U.K.)|Arabic
KAZ|Kazakhstan|English (U.K.)|
KEN|Kenya|English (U.K.)|
XKS|Kosovo|English (U.K.)|
KWT|Kuwait|English (U.K.)|Arabic
KGZ|Kyrgyzstan|English (U.K.)|
LAO|Laos|English (U.K.)|French
LVA|Latvia|English (U.K.)|
LBN|Lebanon|English (U.K.)|Arabic;French
LBR|Liberia|English (U.K.)|
LBY|Libya|English (U.K.)|Arabic
LTU|Lithuania|English (U.K.)|
LUX|Luxembourg|English (U.K.)|French;German
MAC|Macau|Chinese (Traditional)|English (U.K.)
MDG|Madagascar|English (U.K.)|French
MWI|Malawi|English (U.K.)|
MYS|Malaysia|English (U.K.)|Malay
MDV|Maldives|English (U.K.)|
MLI|Mali|English (U.K.)|French
MLT|Malta|English (U.K.)|
MRT|Mauritania|English (U.K.)|Arabic;French
MUS|Mauritius|English (U.K.)|French
MEX|Mexico|Spanish (Mexico)|English (U.K.)
FSM|Micronesia|English (U.K.)|
MDA|Moldova|English (U.K.)|
MNG|Mongolia|English (U.K.)|
MNE|Montenegro|English (U.K.)|Croatian
MSR|Montserrat|English (U.K.)|
MAR|Morocco|English (U.K.)|Arabic;French
MOZ|Mozambique|English (U.K.)|
MMR|Myanmar|English (U.K.)|
NAM|Namibia|English (U.K.)|
NRU|Nauru|English (U.K.)|
NPL|Nepal|English (U.K.)|
NLD|Netherlands|Dutch|English (U.K.)
NZL|New Zealand|English (Australia)|English (U.K.)
NIC|Nicaragua|Spanish (Mexico)|English (U.K.)
NER|Niger|English (U.K.)|French
NGA|Nigeria|English (U.K.)|
MKD|North Macedonia|English (U.K.)|
NOR|Norway|English (U.K.)|Norwegian
OMN|Oman|English (U.K.)|Arabic
PAK|Pakistan|English (U.K.)|Urdu
PLW|Palau|English (U.K.)|
PAN|Panama|Spanish (Mexico)|English (U.K.)
PNG|Papua New Guinea|English (U.K.)|
PRY|Paraguay|Spanish (Mexico)|English (U.K.)
PER|Peru|Spanish (Mexico)|English (U.K.)
PHL|Philippines|English (U.K.)|
POL|Poland|English (U.K.)|Polish
PRT|Portugal|Portuguese (Portugal)|English (U.K.)
QAT|Qatar|English (U.K.)|Arabic
KOR|Republic of Korea|Korean|English (U.K.)
ROU|Romania|English (U.K.)|Romanian
RUS|Russia|Russian|English (U.K.);Ukrainian
RWA|Rwanda|English (U.K.)|French
STP|São Tomé and Príncipe|English (U.K.)|
SAU|Saudi Arabia|English (U.K.)|Arabic
SEN|Senegal|English (U.K.)|French
SRB|Serbia|English (U.K.)|Croatian
SYC|Seychelles|English (U.K.)|French
SLE|Sierra Leone|English (U.K.)|
SGP|Singapore|English (U.K.)|Chinese (Simplified)
SVK|Slovakia|English (U.K.)|Slovak
SVN|Slovenia|English (U.K.)|Slovenian
SLB|Solomon Islands|English (U.K.)|
ZAF|South Africa|English (U.K.)|
ESP|Spain|Spanish (Spain)|Catalan;English (U.K.)
LKA|Sri Lanka|English (U.K.)|
KNA|St. Kitts and Nevis|English (U.K.)|
LCA|St. Lucia|English (U.K.)|
VCT|St. Vincent and the Grenadines|English (U.K.)|
SUR|Suriname|English (U.K.)|Dutch
SWE|Sweden|Swedish|English (U.K.)
CHE|Switzerland|German|English (U.K.);French;Italian
TWN|Taiwan|Chinese (Traditional)|English (U.K.)
TJK|Tajikistan|English (U.K.)|
TZA|Tanzania|English (U.K.)|
THA|Thailand|English (U.K.)|Thai
TON|Tonga|English (U.K.)|
TTO|Trinidad and Tobago|English (U.K.)|French
TUN|Tunisia|English (U.K.)|Arabic;French
TUR|Türkiye|English (U.K.)|Turkish
TKM|Turkmenistan|English (U.K.)|
TCA|Turks and Caicos Islands|English (U.K.)|
UGA|Uganda|English (U.K.)|
UKR|Ukraine|English (U.K.)|Russian;Ukrainian
ARE|United Arab Emirates|English (U.K.)|Arabic
GBR|United Kingdom|English (U.K.)|
USA|United States|English (U.S.)|Arabic;Chinese (Simplified);Chinese (Traditional);French;Korean;Portuguese (Brazil);Russian;Spanish (Mexico);Vietnamese
URY|Uruguay|English (U.K.)|Spanish (Mexico)
UZB|Uzbekistan|English (U.K.)|
VUT|Vanuatu|English (U.K.)|French
VEN|Venezuela|Spanish (Mexico)|English (U.K.)
VNM|Vietnam|English (U.K.)|Vietnamese
YEM|Yemen|English (U.K.)|Arabic
ZMB|Zambia|English (U.K.)|
ZWE|Zimbabwe|English (U.K.)|
"""


@dataclass(frozen=True)
class Storefront:
    code: str
    name: str
    default: str
    additional: tuple[str, ...]

    @property
    def locales(self) -> tuple[str, ...]:
        return (self.default,) + self.additional


def _parse(table: str) -> tuple[Storefront, ...]:
    rows = []
    for line in table.strip().splitlines():
        code, name, default, extra = line.split("|")
        rows.append(
            Storefront(
                code=code,
                name=name,
                default=LANGUAGE_TO_LOCALE[default],
                additional=tuple(LANGUAGE_TO_LOCALE[x] for x in extra.split(";") if x),
            )
        )
    return tuple(rows)


STOREFRONTS: tuple[Storefront, ...] = _parse(_TABLE)
_BY_CODE = {s.code: s for s in STOREFRONTS}


def canonical_locale(code: str) -> str | None:
    """Return Apple's spelling of a locale code (case-insensitive), or ``None`` if unknown."""
    return _LOWER.get(code.strip().lower())


def find(code_or_name: str) -> Storefront | None:
    """Look up a storefront by ISO alpha-3 code (``USA``) or by name (``United States``)."""
    key = code_or_name.strip()
    if key.upper() in _BY_CODE:
        return _BY_CODE[key.upper()]
    for storefront in STOREFRONTS:
        if storefront.name.lower() == key.lower():
            return storefront
    return None


@dataclass(frozen=True)
class Coverage:
    storefront: Storefront
    default_present: bool
    present: tuple[str, ...]  # your locales the storefront supports, default first


def coverage(locales: Iterable[str]) -> list[Coverage]:
    """For each storefront, which of ``locales`` it supports."""
    mine = {canonical_locale(code) or code for code in locales}
    out = []
    for storefront in STOREFRONTS:
        present = tuple(code for code in storefront.locales if code in mine)
        out.append(Coverage(storefront, storefront.default in mine, present))
    return out
