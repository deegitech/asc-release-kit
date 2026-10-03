"""App Store Connect state names and shared lookups for versions, builds and app infos.

``appStoreState`` is deprecated in favour of ``appVersionState`` (versions) and
``state`` (app infos). Both are read; the newer field wins when present.
"""

from __future__ import annotations

import re
from typing import Any

from .api import AscClient

#: Versions whose metadata can still change. READY_FOR_REVIEW means "in a draft
#: review submission that hasn't been submitted yet".
EDITABLE_VERSION = frozenset(
    {
        "PREPARE_FOR_SUBMISSION",
        "DEVELOPER_REJECTED",
        "REJECTED",
        "METADATA_REJECTED",
        "INVALID_BINARY",
        "READY_FOR_REVIEW",
    }
)

#: Versions that are with Apple or approved but not yet live.
IN_FLIGHT_VERSION = frozenset(
    {
        "WAITING_FOR_REVIEW",
        "IN_REVIEW",
        "PENDING_APPLE_RELEASE",
        "PENDING_DEVELOPER_RELEASE",
        "PROCESSING_FOR_DISTRIBUTION",
        "PROCESSING_FOR_APP_STORE",
        "WAITING_FOR_EXPORT_COMPLIANCE",
        "ACCEPTED",
        "PENDING_CONTRACT",
    }
)

#: Versions that are (or were last) on the store.
LIVE_VERSION = frozenset(
    {
        "READY_FOR_DISTRIBUTION",
        "READY_FOR_SALE",
        "PREORDER_READY_FOR_SALE",
        "DEVELOPER_REMOVED_FROM_SALE",
        "REMOVED_FROM_SALE",
    }
)

EDITABLE_APP_INFO = frozenset(
    {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED", "READY_FOR_REVIEW"}
)
LIVE_APP_INFO = frozenset({"READY_FOR_DISTRIBUTION", "READY_FOR_SALE", "ACCEPTED", "PENDING_RELEASE"})

#: Review submissions that block a new submission.
OPEN_SUBMISSION = frozenset({"WAITING_FOR_REVIEW", "IN_REVIEW", "UNRESOLVED_ISSUES", "CANCELING", "COMPLETING"})
DRAFT_SUBMISSION = "READY_FOR_REVIEW"
SUBMITTED = frozenset({"WAITING_FOR_REVIEW", "IN_REVIEW"})

#: Review submission items that no longer count as part of the submission.
GONE_ITEM = frozenset({"REMOVED", "REJECTED"})


def attrs(resource: dict[str, Any] | None) -> dict[str, Any]:
    if not resource:
        return {}
    value = resource.get("attributes")
    return value if isinstance(value, dict) else {}


def version_state(resource: dict[str, Any] | None) -> str:
    a = attrs(resource)
    return str(a.get("appVersionState") or a.get("appStoreState") or "UNKNOWN")


def app_info_state(resource: dict[str, Any] | None) -> str:
    a = attrs(resource)
    return str(a.get("state") or a.get("appStoreState") or "UNKNOWN")


def version_key(text: str) -> tuple[int, ...]:
    """Sort key for version strings such as ``1.10.2``."""
    return tuple(int(part) for part in re.findall(r"\d+", text or "")) or (0,)


def list_versions(client: AscClient, app_id: str, platform: str) -> list[dict[str, Any]]:
    return client.get_all(
        f"/v1/apps/{app_id}/appStoreVersions",
        {
            "filter[platform]": platform,
            "fields[appStoreVersions]": "versionString,appStoreState,appVersionState,releaseType,"
            "earliestReleaseDate,platform,createdDate",
        },
    )


def find_version(versions: list[dict[str, Any]], version_string: str) -> dict[str, Any] | None:
    for version in versions:
        if attrs(version).get("versionString") == version_string:
            return version
    return None


def live_version(versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest version that is (or was last) on the store."""
    live = [v for v in versions if version_state(v) in LIVE_VERSION]
    if not live:
        return None
    return max(
        live, key=lambda v: (version_key(str(attrs(v).get("versionString"))), str(attrs(v).get("createdDate") or ""))
    )


def find_builds(
    client: AscClient, app_id: str, version_string: str, build_number: str, platform: str
) -> list[dict[str, Any]]:
    return client.get_all(
        "/v1/builds",
        {
            "filter[app]": app_id,
            "filter[version]": build_number,
            "filter[preReleaseVersion.version]": version_string,
            "filter[preReleaseVersion.platform]": platform,
            "fields[builds]": "version,processingState,expired,uploadedDate,usesNonExemptEncryption,minOsVersion",
        },
        page_size=20,
    )
