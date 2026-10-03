"""One-line fixes for the walls people hit while setting up and running the kit.

* ``api_hint`` maps an App Store Connect error response (HTTP status, the JSON:API
  error ``code`` and ``detail``, method and path) to the fix a person should apply.
  ``ApiError`` carries it as its ``fix``: the command line prints a ``fix:`` line and
  ``--json`` output a ``fix`` field. An error no rule knows points to
  docs/troubleshooting.md.
* ``NETWORK_FIX`` / ``NETWORK_WRITE_FIX``: the fix for a request that got no answer.
* ``keychain_exit_hint`` and ``aws_exit_hint`` explain the exit codes of the
  ``security`` and ``aws`` programs that read the private key.
* ``upload_hint`` recognises Apple's upload validation codes and signing errors in
  the output of xcodebuild and altool.

The rules are deliberately literal: each one names the status, code or path it
matches, and the first matching rule wins. docs/troubleshooting.md has the long
form of every entry; keep the two in sync.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DOCS = "https://github.com/deegitech/asc-release-kit/blob/main/docs/"
TROUBLESHOOTING_URL = DOCS + "troubleshooting.md"
SETUP_URL = DOCS + "setup.md"

#: Where App Store Connect API keys are created. Apple renames menus now and then.
KEYS_PAGE = "App Store Connect > Users and Access > Integrations > App Store Connect API"

WRITE_METHODS = frozenset({"POST", "PATCH", "DELETE"})

# -- App Store Connect API errors -------------------------------------------------------------

TOKEN_REJECTED = (
    "App Store Connect rejected the token: check that ASC_KEY_ID is the Key ID of this .p8 file, that "
    "ASC_ISSUER_ID is the Issuer ID shown above the Team Keys list (individual keys: ASC_KEY_TYPE=individual "
    "and no issuer ID), that the key is an App Store Connect API key that hasn't been revoked, and that this "
    "computer's clock is correct."
)
AGREEMENTS = (
    "The Account Holder has to accept Apple's latest agreements: App Store Connect > Business (formerly "
    "Agreements, Tax, and Banking; names may differ), or the notice at developer.apple.com > Account."
)
SALES_ROLE = (
    "This key can't read Sales and Trends: it needs a team key with the Sales (or Sales and Reports), Finance or "
    "Admin role (individual keys can't access Sales and Finance). Create one under " + KEYS_PAGE + " > Team Keys."
)
ANALYTICS_CREATE = (
    "Creating or deleting analytics report requests needs a key with the Admin role (Apple's analytics "
    "documentation); create one under " + KEYS_PAGE + " > Team Keys."
)
ANALYTICS_READ = "Reading analytics reports needs a key with the Admin, Sales and Reports, or Finance role."
REVIEWS_ROLE = "Reading customer reviews needs a key with the Customer Support, App Manager or Admin role."
ROLE = (
    "The API key's role doesn't allow this request: create a team key with a role that does (App Manager covers "
    "releases, metadata and uploads; docs/credentials.md lists the others) under " + KEYS_PAGE + "."
)
APP_NOT_FOUND = (
    "No app with this Apple ID is visible to the key: check ASC_APP_ID or --app (`asc-release-kit apps list` "
    "prints the IDs), and that the key belongs to the team that owns the app."
)
VERSION_EXISTS = (
    "App Store Connect already has a version with this number for this platform, or had one before: check the "
    "app's versions in App Store Connect, and give a new release a higher version number (CFBundleShortVersionString)."
)
DUPLICATE = (
    "App Store Connect already has this item (it creates some localizations by itself): re-run the command; "
    "the kit re-reads and updates instead of creating."
)
STATE = (
    "The item's current state doesn't allow this change (for example the version is in review, or in a draft "
    "review submission, which also locks its media): check it in App Store Connect. Name, subtitle and keywords "
    "only change with a new version."
)
RELATIONSHIP = (
    "App Store Connect refused a link between two items: check that the build belongs to this app, version "
    "number and platform, and that it is VALID."
)
ATTRIBUTE = (
    "App Store Connect refused a field value (the line above names it); `asc-release-kit aso validate` checks "
    "Apple's limits offline."
)
CONFLICT = (
    "App Store Connect refused the change in the item's current state: re-run (the kit re-reads first) and check "
    "the item in App Store Connect."
)
PARAMETER = (
    "App Store Connect refused a request parameter (the line above names it): check the flag values, such as "
    "dates, the report type and the vendor number."
)
RATE_LIMIT = (
    "App Store Connect's hourly request limit was reached and the kit's retries ran out: wait a while and re-run."
)
WRITE_FAILED = (
    "Apple's server failed. The write may or may not have taken effect, so the kit didn't repeat it: re-run the "
    "same command; it re-reads App Store Connect and continues where it stopped."
)
READ_FAILED = (
    "Apple's servers kept failing after several retries: try again later (developer.apple.com/system-status "
    "shows outages)."
)
UNKNOWN = "look up the HTTP status and the error code above in " + TROUBLESHOOTING_URL

#: A request that got no answer at all (DNS, TLS, proxy, a dropped connection, a timeout).
NETWORK_FIX = "check the internet connection and any proxy (HTTPS_PROXY), then run the command again"
NETWORK_WRITE_FIX = (
    "check the internet connection and any proxy (HTTPS_PROXY), then re-run the same command: it re-reads App "
    "Store Connect first, so a write that did go through isn't made twice"
)


@dataclass(frozen=True)
class ApiRule:
    """One mapping. Every condition that is set must match; the first matching rule wins."""

    fix: str
    status: int | None = None
    #: matches this status and every higher one (``500``: any server error)
    min_status: int | None = None
    #: an App Store Connect error code; a trailing ``*`` matches it as a prefix
    code: str | None = None
    #: regular expression searched in the errors' ``detail`` and ``title`` texts
    detail: str | None = None
    #: regular expression searched in the request path (``/v1/...``, without the query)
    path: str | None = None
    methods: frozenset[str] | None = None

    def matches(self, status: int, method: str, path: str, codes: Sequence[str], details: str) -> bool:
        if self.status is not None and status != self.status:
            return False
        if self.min_status is not None and status < self.min_status:
            return False
        if self.methods is not None and method.upper() not in self.methods:
            return False
        if self.path is not None and not re.search(self.path, path):
            return False
        if self.code is not None and not any(_code_matches(self.code, code) for code in codes):
            return False
        return not (self.detail is not None and not re.search(self.detail, details))


def _code_matches(pattern: str, code: str) -> bool:
    if pattern.endswith("*"):
        return code.startswith(pattern[:-1])
    return code == pattern


API_RULES: tuple[ApiRule, ...] = (
    ApiRule(AGREEMENTS, code="FORBIDDEN.REQUIRED_AGREEMENTS*"),
    ApiRule(AGREEMENTS, status=403, detail=r"(?i)\bagreements?\b"),
    ApiRule(TOKEN_REJECTED, status=401),
    ApiRule(SALES_ROLE, status=403, path=r"^/v1/(salesReports|financeReports)\b"),
    ApiRule(
        ANALYTICS_CREATE, status=403, path=r"^/v1/analyticsReportRequests\b", methods=frozenset({"POST", "DELETE"})
    ),
    ApiRule(ANALYTICS_READ, status=403, path=r"(?i)analytics"),
    ApiRule(REVIEWS_ROLE, status=403, path=r"customerReview"),
    ApiRule(ROLE, status=403),
    ApiRule(APP_NOT_FOUND, status=404, path=r"^/v1/apps/[^/]+(/|$)"),
    ApiRule(
        VERSION_EXISTS,
        code="ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE",
        path=r"^/v1/appStoreVersions$",
        methods=frozenset({"POST"}),
    ),
    ApiRule(DUPLICATE, code="ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE"),
    ApiRule(STATE, code="STATE_ERROR*"),
    ApiRule(RELATIONSHIP, code="ENTITY_ERROR.RELATIONSHIP*"),
    ApiRule(ATTRIBUTE, code="ENTITY_ERROR.ATTRIBUTE*"),
    ApiRule(CONFLICT, status=409),
    ApiRule(PARAMETER, code="PARAMETER_ERROR*"),
    ApiRule(RATE_LIMIT, status=429),
    ApiRule(WRITE_FAILED, min_status=500, methods=WRITE_METHODS),
    ApiRule(READ_FAILED, min_status=500),
    ApiRule(UNKNOWN),  # last: every other error points to the troubleshooting page
)


def api_hint(status: int, method: str, path: str, errors: Iterable[Mapping[str, Any]] = ()) -> str:
    """The one-line fix for an App Store Connect error response (the last rule matches any error)."""
    errors = list(errors)
    codes = [str(err.get("code") or "") for err in errors]
    details = " ".join(f"{err.get('title') or ''} {err.get('detail') or ''}" for err in errors)
    for rule in API_RULES:
        if rule.matches(status, method, path, codes, details):
            return rule.fix
    return ""


# -- helper programs that read the private key -------------------------------------------------

#: ``security find-generic-password`` exits with the low byte of macOS's OSStatus.
KEYCHAIN_EXIT_HINTS = {
    44: (
        "no Keychain item matches ASC_KEYCHAIN_SERVICE (and ASC_KEYCHAIN_ACCOUNT): check both names; "
        "`security find-generic-password -s asc-release-kit` (without -w) shows whether the item exists"
    ),
    36: (
        "usually the Keychain is locked and macOS can't ask for its password here (for example over SSH or in a "
        "scheduled job): run `security unlock-keychain` first, or use a key file"
    ),
    51: "usually the Keychain refused the password: unlock it with `security unlock-keychain` and try again",
    128: "usually access was denied in the macOS dialog: run the command again and click Allow (or Always Allow)",
}
KEYCHAIN_DEFAULT_HINT = "check ASC_KEYCHAIN_SERVICE / ASC_KEYCHAIN_ACCOUNT, or store the key again (docs/setup.md)"


def keychain_exit_hint(code: int) -> str:
    return KEYCHAIN_EXIT_HINTS.get(code, KEYCHAIN_DEFAULT_HINT)


#: The AWS CLI's documented return codes.
AWS_EXIT_HINTS = {
    252: "the AWS CLI refused its arguments: check ASC_SSM_PARAMETER (a name such as /example/asc/private-key)",
    253: (
        "the AWS CLI found no usable credentials or region: check `aws configure list` (or the instance role) "
        "and AWS_REGION"
    ),
    254: (
        "AWS answered with an error: check the parameter name and region, and that your identity may call "
        "ssm:GetParameter (plus kms:Decrypt when the parameter uses a customer-managed KMS key)"
    ),
}
AWS_DEFAULT_HINT = (
    "`aws ssm get-parameter --name <name> --query Parameter.Name` shows the AWS error without printing the value"
)


def aws_exit_hint(code: int) -> str:
    return AWS_EXIT_HINTS.get(code, AWS_DEFAULT_HINT)


# -- Apple's upload tools ------------------------------------------------------------------------

UPLOAD_RULES: tuple[tuple[str, str], ...] = (
    (
        "ITMS-90189",
        "this build number was already uploaded for this version: raise CFBundleVersion (the build number), "
        "archive again and upload",
    ),
    (
        "ITMS-90186",
        "this version number no longer takes new builds (it was approved or released): raise "
        "CFBundleShortVersionString, archive again and upload",
    ),
    (
        "ITMS-90062",
        "the version number must be higher than the last approved one: raise CFBundleShortVersionString, archive "
        "again and upload",
    ),
    (
        "Cloud signing permission error",
        "this API key may not use Apple's cloud-managed distribution certificates (keys with the App Manager role "
        "get this error): run --archive with an Admin key, or install the distribution certificate on this Mac and "
        "export with manual signing (--export-options)",
    ),
    (
        "No signing certificate",
        "no distribution certificate with its private key is installed on this Mac: import it into the login "
        "keychain, or let Xcode sign in the cloud (automatic signing, which needs an Admin key)",
    ),
    (
        "No suitable application records were found",
        "App Store Connect has no app record with this bundle ID for this key's team: create it (Apps > + > New "
        "App) with exactly the build's bundle ID, or use a key of the team that owns the app",
    ),
)


def upload_hint(lines: Iterable[str]) -> str:
    """The fix for the first known Apple upload code or signing message in a tool's output, or ``""``."""
    text = "\n".join(lines)
    for code, fix in UPLOAD_RULES:
        if code in text:
            return fix
    return ""
