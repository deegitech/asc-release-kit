# Storefront locales: which localizations each App Store storefront supports

This table lists, for every App Store storefront, the **default language** and the
**additional languages** Apple says the storefront supports for app metadata, with
the App Store Connect locale code for each language.

- Source: Apple, [App Store localizations](https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations) (App Store Connect Help).
- Locale codes: Apple, [Managing metadata in your app by using locale shortcodes](https://developer.apple.com/documentation/appstoreconnectapi/managing-metadata-in-your-app-by-using-locale-shortcodes).
- Transcribed on **2026-10-03**. Apple edits this table from time to time, so check
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

## Locale codes (50)

| Language | Code |
|---|---|
| Arabic | `ar-SA` |
| Bangla | `bn-BD` |
| Catalan | `ca` |
| Chinese (Simplified) | `zh-Hans` |
| Chinese (Traditional) | `zh-Hant` |
| Croatian | `hr` |
| Czech | `cs` |
| Danish | `da` |
| Dutch | `nl-NL` |
| English (Australia) | `en-AU` |
| English (Canada) | `en-CA` |
| English (U.K.) | `en-GB` |
| English (U.S.) | `en-US` |
| Finnish | `fi` |
| French | `fr-FR` |
| French (Canada) | `fr-CA` |
| German | `de-DE` |
| Greek | `el` |
| Gujarati | `gu-IN` |
| Hebrew | `he` |
| Hindi | `hi` |
| Hungarian | `hu` |
| Indonesian | `id` |
| Italian | `it` |
| Japanese | `ja` |
| Kannada | `kn-IN` |
| Korean | `ko` |
| Malay | `ms` |
| Malayalam | `ml-IN` |
| Marathi | `mr-IN` |
| Norwegian | `no` |
| Odia | `or-IN` |
| Polish | `pl` |
| Portuguese (Brazil) | `pt-BR` |
| Portuguese (Portugal) | `pt-PT` |
| Punjabi | `pa-IN` |
| Romanian | `ro` |
| Russian | `ru` |
| Slovak | `sk` |
| Slovenian | `sl-SI` |
| Spanish (Mexico) | `es-MX` |
| Spanish (Spain) | `es-ES` |
| Swedish | `sv` |
| Tamil | `ta-IN` |
| Telugu | `te-IN` |
| Thai | `th` |
| Turkish | `tr` |
| Ukrainian | `uk` |
| Urdu | `ur-PK` |
| Vietnamese | `vi` |

## Storefronts (175)

| Code | Storefront | Default language | Additional languages |
|---|---|---|---|
| AFG | Afghanistan | English (U.K.) `en-GB` | - |
| ALB | Albania | English (U.K.) `en-GB` | - |
| DZA | Algeria | English (U.K.) `en-GB` | Arabic `ar-SA`, French `fr-FR` |
| AGO | Angola | English (U.K.) `en-GB` | - |
| AIA | Anguilla | English (U.K.) `en-GB` | - |
| ATG | Antigua and Barbuda | English (U.K.) `en-GB` | - |
| ARG | Argentina | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| ARM | Armenia | English (U.K.) `en-GB` | - |
| AUS | Australia | English (Australia) `en-AU` | English (U.K.) `en-GB` |
| AUT | Austria | German `de-DE` | English (U.K.) `en-GB` |
| AZE | Azerbaijan | English (U.K.) `en-GB` | - |
| BHS | Bahamas | English (U.K.) `en-GB` | - |
| BHR | Bahrain | English (U.K.) `en-GB` | Arabic `ar-SA` |
| BRB | Barbados | English (U.K.) `en-GB` | - |
| BLR | Belarus | English (U.K.) `en-GB` | - |
| BEL | Belgium | English (U.K.) `en-GB` | Dutch `nl-NL`, French `fr-FR` |
| BLZ | Belize | English (U.K.) `en-GB` | Spanish (Mexico) `es-MX` |
| BEN | Benin | English (U.K.) `en-GB` | French `fr-FR` |
| BMU | Bermuda | English (U.K.) `en-GB` | - |
| BTN | Bhutan | English (U.K.) `en-GB` | - |
| BOL | Bolivia | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| BIH | Bosnia and Herzegovina | English (U.K.) `en-GB` | Croatian `hr` |
| BWA | Botswana | English (U.K.) `en-GB` | - |
| BRA | Brazil | Portuguese (Brazil) `pt-BR` | English (U.K.) `en-GB` |
| VGB | British Virgin Islands | English (U.K.) `en-GB` | - |
| BRN | Brunei | English (U.K.) `en-GB` | - |
| BGR | Bulgaria | English (U.K.) `en-GB` | - |
| BFA | Burkina Faso | English (U.K.) `en-GB` | French `fr-FR` |
| KHM | Cambodia | English (U.K.) `en-GB` | French `fr-FR` |
| CMR | Cameroon | French `fr-FR` | English (U.K.) `en-GB` |
| CAN | Canada | English (Canada) `en-CA` | French (Canada) `fr-CA` |
| CPV | Cape Verde | English (U.K.) `en-GB` | - |
| CYM | Cayman Islands | English (U.K.) `en-GB` | - |
| TCD | Chad | English (U.K.) `en-GB` | French `fr-FR` |
| CHL | Chile | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| CHN | China mainland | Chinese (Simplified) `zh-Hans` | English (U.K.) `en-GB` |
| COL | Colombia | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| COD | Congo, Democratic Republic of the | English (U.K.) `en-GB` | French `fr-FR` |
| COG | Congo, Republic of the | English (U.K.) `en-GB` | French `fr-FR` |
| CRI | Costa Rica | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| CIV | Côte d'Ivoire | French `fr-FR` | English (U.K.) `en-GB` |
| HRV | Croatia | English (U.K.) `en-GB` | Croatian `hr` |
| CYP | Cyprus | English (U.K.) `en-GB` | Greek `el`, Turkish `tr` |
| CZE | Czechia | English (U.K.) `en-GB` | Czech `cs` |
| DNK | Denmark | English (U.K.) `en-GB` | Danish `da` |
| DMA | Dominica | English (U.K.) `en-GB` | - |
| DOM | Dominican Republic | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| ECU | Ecuador | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| EGY | Egypt | English (U.K.) `en-GB` | Arabic `ar-SA`, French `fr-FR` |
| SLV | El Salvador | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| EST | Estonia | English (U.K.) `en-GB` | - |
| SWZ | Eswatini | English (U.K.) `en-GB` | - |
| FJI | Fiji | English (U.K.) `en-GB` | - |
| FIN | Finland | English (U.K.) `en-GB` | Finnish `fi` |
| FRA | France | French `fr-FR` | English (U.K.) `en-GB` |
| GAB | Gabon | French `fr-FR` | English (U.K.) `en-GB` |
| GMB | Gambia | English (U.K.) `en-GB` | - |
| GEO | Georgia | English (U.K.) `en-GB` | - |
| DEU | Germany | German `de-DE` | English (U.K.) `en-GB` |
| GHA | Ghana | English (U.K.) `en-GB` | - |
| GRC | Greece | English (U.K.) `en-GB` | Greek `el` |
| GRD | Grenada | English (U.K.) `en-GB` | - |
| GTM | Guatemala | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| GNB | Guinea-Bissau | English (U.K.) `en-GB` | French `fr-FR` |
| GUY | Guyana | English (U.K.) `en-GB` | French `fr-FR` |
| HND | Honduras | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| HKG | Hong Kong | Chinese (Traditional) `zh-Hant` | English (U.K.) `en-GB` |
| HUN | Hungary | English (U.K.) `en-GB` | Hungarian `hu` |
| ISL | Iceland | English (U.K.) `en-GB` | - |
| IND | India | English (U.K.) `en-GB` | Bangla `bn-BD`, Gujarati `gu-IN`, Hindi `hi`, Kannada `kn-IN`, Malayalam `ml-IN`, Marathi `mr-IN`, Odia `or-IN`, Punjabi `pa-IN`, Tamil `ta-IN`, Telugu `te-IN`, Urdu `ur-PK` |
| IDN | Indonesia | English (U.K.) `en-GB` | Indonesian `id` |
| IRQ | Iraq | English (U.K.) `en-GB` | Arabic `ar-SA` |
| IRL | Ireland | English (U.K.) `en-GB` | - |
| ISR | Israel | English (U.K.) `en-GB` | Hebrew `he` |
| ITA | Italy | Italian `it` | English (U.K.) `en-GB` |
| JAM | Jamaica | English (U.K.) `en-GB` | - |
| JPN | Japan | Japanese `ja` | English (U.S.) `en-US` |
| JOR | Jordan | English (U.K.) `en-GB` | Arabic `ar-SA` |
| KAZ | Kazakhstan | English (U.K.) `en-GB` | - |
| KEN | Kenya | English (U.K.) `en-GB` | - |
| XKS | Kosovo | English (U.K.) `en-GB` | - |
| KWT | Kuwait | English (U.K.) `en-GB` | Arabic `ar-SA` |
| KGZ | Kyrgyzstan | English (U.K.) `en-GB` | - |
| LAO | Laos | English (U.K.) `en-GB` | French `fr-FR` |
| LVA | Latvia | English (U.K.) `en-GB` | - |
| LBN | Lebanon | English (U.K.) `en-GB` | Arabic `ar-SA`, French `fr-FR` |
| LBR | Liberia | English (U.K.) `en-GB` | - |
| LBY | Libya | English (U.K.) `en-GB` | Arabic `ar-SA` |
| LTU | Lithuania | English (U.K.) `en-GB` | - |
| LUX | Luxembourg | English (U.K.) `en-GB` | French `fr-FR`, German `de-DE` |
| MAC | Macau | Chinese (Traditional) `zh-Hant` | English (U.K.) `en-GB` |
| MDG | Madagascar | English (U.K.) `en-GB` | French `fr-FR` |
| MWI | Malawi | English (U.K.) `en-GB` | - |
| MYS | Malaysia | English (U.K.) `en-GB` | Malay `ms` |
| MDV | Maldives | English (U.K.) `en-GB` | - |
| MLI | Mali | English (U.K.) `en-GB` | French `fr-FR` |
| MLT | Malta | English (U.K.) `en-GB` | - |
| MRT | Mauritania | English (U.K.) `en-GB` | Arabic `ar-SA`, French `fr-FR` |
| MUS | Mauritius | English (U.K.) `en-GB` | French `fr-FR` |
| MEX | Mexico | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| FSM | Micronesia | English (U.K.) `en-GB` | - |
| MDA | Moldova | English (U.K.) `en-GB` | - |
| MNG | Mongolia | English (U.K.) `en-GB` | - |
| MNE | Montenegro | English (U.K.) `en-GB` | Croatian `hr` |
| MSR | Montserrat | English (U.K.) `en-GB` | - |
| MAR | Morocco | English (U.K.) `en-GB` | Arabic `ar-SA`, French `fr-FR` |
| MOZ | Mozambique | English (U.K.) `en-GB` | - |
| MMR | Myanmar | English (U.K.) `en-GB` | - |
| NAM | Namibia | English (U.K.) `en-GB` | - |
| NRU | Nauru | English (U.K.) `en-GB` | - |
| NPL | Nepal | English (U.K.) `en-GB` | - |
| NLD | Netherlands | Dutch `nl-NL` | English (U.K.) `en-GB` |
| NZL | New Zealand | English (Australia) `en-AU` | English (U.K.) `en-GB` |
| NIC | Nicaragua | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| NER | Niger | English (U.K.) `en-GB` | French `fr-FR` |
| NGA | Nigeria | English (U.K.) `en-GB` | - |
| MKD | North Macedonia | English (U.K.) `en-GB` | - |
| NOR | Norway | English (U.K.) `en-GB` | Norwegian `no` |
| OMN | Oman | English (U.K.) `en-GB` | Arabic `ar-SA` |
| PAK | Pakistan | English (U.K.) `en-GB` | Urdu `ur-PK` |
| PLW | Palau | English (U.K.) `en-GB` | - |
| PAN | Panama | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| PNG | Papua New Guinea | English (U.K.) `en-GB` | - |
| PRY | Paraguay | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| PER | Peru | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| PHL | Philippines | English (U.K.) `en-GB` | - |
| POL | Poland | English (U.K.) `en-GB` | Polish `pl` |
| PRT | Portugal | Portuguese (Portugal) `pt-PT` | English (U.K.) `en-GB` |
| QAT | Qatar | English (U.K.) `en-GB` | Arabic `ar-SA` |
| KOR | Republic of Korea | Korean `ko` | English (U.K.) `en-GB` |
| ROU | Romania | English (U.K.) `en-GB` | Romanian `ro` |
| RUS | Russia | Russian `ru` | English (U.K.) `en-GB`, Ukrainian `uk` |
| RWA | Rwanda | English (U.K.) `en-GB` | French `fr-FR` |
| STP | São Tomé and Príncipe | English (U.K.) `en-GB` | - |
| SAU | Saudi Arabia | English (U.K.) `en-GB` | Arabic `ar-SA` |
| SEN | Senegal | English (U.K.) `en-GB` | French `fr-FR` |
| SRB | Serbia | English (U.K.) `en-GB` | Croatian `hr` |
| SYC | Seychelles | English (U.K.) `en-GB` | French `fr-FR` |
| SLE | Sierra Leone | English (U.K.) `en-GB` | - |
| SGP | Singapore | English (U.K.) `en-GB` | Chinese (Simplified) `zh-Hans` |
| SVK | Slovakia | English (U.K.) `en-GB` | Slovak `sk` |
| SVN | Slovenia | English (U.K.) `en-GB` | Slovenian `sl-SI` |
| SLB | Solomon Islands | English (U.K.) `en-GB` | - |
| ZAF | South Africa | English (U.K.) `en-GB` | - |
| ESP | Spain | Spanish (Spain) `es-ES` | Catalan `ca`, English (U.K.) `en-GB` |
| LKA | Sri Lanka | English (U.K.) `en-GB` | - |
| KNA | St. Kitts and Nevis | English (U.K.) `en-GB` | - |
| LCA | St. Lucia | English (U.K.) `en-GB` | - |
| VCT | St. Vincent and the Grenadines | English (U.K.) `en-GB` | - |
| SUR | Suriname | English (U.K.) `en-GB` | Dutch `nl-NL` |
| SWE | Sweden | Swedish `sv` | English (U.K.) `en-GB` |
| CHE | Switzerland | German `de-DE` | English (U.K.) `en-GB`, French `fr-FR`, Italian `it` |
| TWN | Taiwan | Chinese (Traditional) `zh-Hant` | English (U.K.) `en-GB` |
| TJK | Tajikistan | English (U.K.) `en-GB` | - |
| TZA | Tanzania | English (U.K.) `en-GB` | - |
| THA | Thailand | English (U.K.) `en-GB` | Thai `th` |
| TON | Tonga | English (U.K.) `en-GB` | - |
| TTO | Trinidad and Tobago | English (U.K.) `en-GB` | French `fr-FR` |
| TUN | Tunisia | English (U.K.) `en-GB` | Arabic `ar-SA`, French `fr-FR` |
| TUR | Türkiye | English (U.K.) `en-GB` | Turkish `tr` |
| TKM | Turkmenistan | English (U.K.) `en-GB` | - |
| TCA | Turks and Caicos Islands | English (U.K.) `en-GB` | - |
| UGA | Uganda | English (U.K.) `en-GB` | - |
| UKR | Ukraine | English (U.K.) `en-GB` | Russian `ru`, Ukrainian `uk` |
| ARE | United Arab Emirates | English (U.K.) `en-GB` | Arabic `ar-SA` |
| GBR | United Kingdom | English (U.K.) `en-GB` | - |
| USA | United States | English (U.S.) `en-US` | Arabic `ar-SA`, Chinese (Simplified) `zh-Hans`, Chinese (Traditional) `zh-Hant`, French `fr-FR`, Korean `ko`, Portuguese (Brazil) `pt-BR`, Russian `ru`, Spanish (Mexico) `es-MX`, Vietnamese `vi` |
| URY | Uruguay | English (U.K.) `en-GB` | Spanish (Mexico) `es-MX` |
| UZB | Uzbekistan | English (U.K.) `en-GB` | - |
| VUT | Vanuatu | English (U.K.) `en-GB` | French `fr-FR` |
| VEN | Venezuela | Spanish (Mexico) `es-MX` | English (U.K.) `en-GB` |
| VNM | Vietnam | English (U.K.) `en-GB` | Vietnamese `vi` |
| YEM | Yemen | English (U.K.) `en-GB` | Arabic `ar-SA` |
| ZMB | Zambia | English (U.K.) `en-GB` | - |
| ZWE | Zimbabwe | English (U.K.) `en-GB` | - |
