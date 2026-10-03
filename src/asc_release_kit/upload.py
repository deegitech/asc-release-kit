"""``upload-build``: hand a build to Apple with an API key, using Apple's own tools.

* ``--archive App.xcarchive``: ``xcodebuild -exportArchive`` with an export options
  plist whose ``destination`` is ``upload`` (method ``app-store-connect``);
* ``--ipa App.ipa``: ``xcrun altool --upload-package`` (bundle ID, version and build
  are read from the IPA's Info.plist);
* ``--notarize App.zip|.dmg|.pkg``: ``xcrun notarytool submit --wait``, which is
  notarization for software distributed *outside* the Mac App Store, not an App
  Store upload.

The key reaches these tools as a file path: the configured ``.p8`` file, or a
0600 temporary copy that is deleted when the tool exits. They run with
``ASC_PRIVATE_KEY`` removed from their environment. Key ID, issuer ID and paths
are masked in everything printed. Plan mode (the default) only shows the command
it would run; it doesn't load the key.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .context import Context
from .credentials import ApiKey, child_env, key_file
from .errors import Blocked, CheckFailed, UsageError
from .hints import upload_hint
from .states import attrs, find_builds

ALTOOL_PLATFORMS = {"IOS": "ios", "MAC_OS": "macos", "TV_OS": "appletvos", "VISION_OS": "visionos"}
INTERESTING = re.compile(
    r"error|warning: .*sign|\*\* (ARCHIVE|EXPORT) |Upload(ed| succeeded| failed)|No errors uploading|"
    r"status:|Processing complete|Successfully|FAILED|Invalid|Accepted",
    re.I,
)
BUILD_POLL_SECONDS = 30.0

#: Streams a command's combined output line by line; returns the exit code.
StreamRunner = Callable[[list, Callable[[str], None]], int]

_sleep: Callable[[float], None] = time.sleep
_monotonic: Callable[[], float] = time.monotonic


TERMINATE_GRACE_SECONDS = 10.0


def stream_process(argv: list[str], on_line: Callable[[str], None]) -> int:
    """Run ``argv`` and hand each output line to ``on_line``; return the exit code.

    Output is decoded as UTF-8 with replacement characters, so odd bytes can't abort
    the run. If anything goes wrong while reading (an exception in ``on_line``,
    Ctrl-C, SIGTERM), the tool is stopped before the error propagates, so it never
    outlives the temporary key copy it was given.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=child_env(),
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
    except BaseException:
        proc.terminate()
        try:
            proc.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise
    finally:
        proc.stdout.close()
    return proc.wait()


@dataclass
class BuildInfo:
    bundle_id: str | None
    version: str | None
    build: str | None


def read_archive_info(archive: str) -> BuildInfo:
    path = os.path.join(archive, "Info.plist")
    try:
        with open(path, "rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        raise UsageError("Not an Xcode archive: <archive>/Info.plist is missing or unreadable.") from None
    props = data.get("ApplicationProperties") or {}
    return BuildInfo(
        props.get("CFBundleIdentifier"), props.get("CFBundleShortVersionString"), props.get("CFBundleVersion")
    )


def read_ipa_info(ipa: str) -> BuildInfo:
    try:
        with zipfile.ZipFile(ipa) as archive:
            names = [n for n in archive.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
            if len(names) != 1:
                raise UsageError("Couldn't find exactly one Payload/<App>.app/Info.plist in the .ipa.")
            data = plistlib.loads(archive.read(names[0]))
    except (OSError, zipfile.BadZipFile, plistlib.InvalidFileException, ValueError):
        raise UsageError("Not a readable .ipa file.") from None
    return BuildInfo(
        data.get("CFBundleIdentifier"), data.get("CFBundleShortVersionString"), data.get("CFBundleVersion")
    )


def default_export_options(team_id: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "method": "app-store-connect",
        "destination": "upload",
        # Xcode otherwise may renumber the build during upload (its default is YES).
        "manageAppVersionAndBuildNumber": False,
        "uploadSymbols": True,
        "signingStyle": "automatic",
    }
    if team_id:
        options["teamID"] = team_id
    return options


def check_export_options(path: str, reporter: Any) -> None:
    try:
        with open(path, "rb") as handle:
            options = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        raise UsageError("The export options plist can't be read.") from None
    if options.get("destination") != "upload":
        reporter.warn(
            "export options: destination isn't 'upload', so xcodebuild will export locally instead of uploading"
        )
    if options.get("method") not in ("app-store-connect", "app-store"):
        reporter.warn("export options: method isn't 'app-store-connect'")
    if options.get("manageAppVersionAndBuildNumber", True):
        reporter.warn(
            "export options: manageAppVersionAndBuildNumber is on (Xcode's default), so Xcode may change the build "
            "number while uploading; set it to false to release exactly what you built"
        )


def xcodebuild_argv(archive: str, options_plist: str, export_path: str, key_path: str, key: ApiKey) -> list[str]:
    if key.key_type != "team" or not key.issuer_id:
        raise UsageError("xcodebuild needs a team API key (it requires an issuer ID).")
    return [
        "xcodebuild",
        "-exportArchive",
        "-archivePath",
        archive,
        "-exportOptionsPlist",
        options_plist,
        "-exportPath",
        export_path,
        "-allowProvisioningUpdates",
        "-authenticationKeyPath",
        key_path,
        "-authenticationKeyID",
        key.key_id,
        "-authenticationKeyIssuerID",
        key.issuer_id,
    ]


def altool_argv(
    ipa: str, info: BuildInfo, app_id: str, platform: str, key_path: str, key: ApiKey, wait: bool
) -> list[str]:
    argv = [
        "xcrun",
        "altool",
        "--upload-package",
        ipa,
        "-t",
        ALTOOL_PLATFORMS[platform],
        "--apple-id",
        app_id,
        "--bundle-id",
        str(info.bundle_id),
        "--bundle-short-version-string",
        str(info.version),
        "--bundle-version",
        str(info.build),
        "--api-key",
        key.key_id,
        "--p8-file-path",
        key_path,
    ]
    if key.key_type == "team" and key.issuer_id:
        argv += ["--api-issuer", key.issuer_id]
    else:
        argv += ["--api-key-subject", "user"]
    if wait:
        argv.append("--wait")
    return argv


def notarytool_argv(path: str, key_path: str, key: ApiKey, wait_minutes: float) -> list[str]:
    argv = ["xcrun", "notarytool", "submit", path, "--key", key_path, "--key-id", key.key_id]
    if key.key_type == "team" and key.issuer_id:
        argv += ["--issuer", key.issuer_id]
    argv += ["--output-format", "json", "--no-progress"]
    if wait_minutes > 0:
        argv += ["--wait", "--timeout", f"{int(wait_minutes)}m"]
    return argv


def masked_command(argv: list[str], key: ApiKey, key_path: str | None) -> str:
    replacements = {key.key_id: "<key-id>"}
    if key.issuer_id:
        replacements[key.issuer_id] = "<issuer-id>"
    if key_path:
        replacements[key_path] = "<key-file>"
    return shlex.join([replacements.get(arg, arg) for arg in argv])


@dataclass
class UploadOptions:
    archive: str | None = None
    ipa: str | None = None
    notarize: str | None = None
    export_options: str | None = None
    export_path: str | None = None
    team_id: str | None = None
    app_id: str | None = None
    platform: str = "IOS"
    wait_minutes: float = 0.0


def _wait_for_processing(ctx: Context, app_id: str, info: BuildInfo, platform: str, minutes: float) -> str:
    r = ctx.reporter
    client = ctx.client()
    deadline = _monotonic() + minutes * 60
    last = None
    while True:
        builds = find_builds(client, app_id, str(info.version), str(info.build), platform)
        states = sorted({str(attrs(b).get("processingState")) for b in builds})
        if "VALID" in states:
            r.ok(f"build {info.version} ({info.build}) processed: VALID")
            return "VALID"
        if any(s in ("INVALID", "FAILED") for s in states):
            raise CheckFailed(f"Build {info.version} ({info.build}) processing ended {', '.join(states)}.")
        current = ", ".join(states) if states else "not visible yet"
        if current != last:
            r.info(f"build processing: {current}")
            last = current
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise CheckFailed(f"Build {info.version} ({info.build}) is still {current}; check again later.")
        _sleep(min(BUILD_POLL_SECONDS, remaining))


def _command(
    opts: UploadOptions, info: BuildInfo, key: ApiKey, key_path: str, options_plist: str, export_path: str
) -> list[str]:
    if opts.archive:
        return xcodebuild_argv(opts.archive, options_plist, export_path, key_path, key)
    if opts.ipa:
        return altool_argv(opts.ipa, info, str(opts.app_id), opts.platform, key_path, key, wait=False)
    return notarytool_argv(str(opts.notarize), key_path, key, opts.wait_minutes or 60)


def run_upload(ctx: Context, opts: UploadOptions, runner: StreamRunner | None = None) -> dict[str, Any]:
    modes = [m for m in (opts.archive, opts.ipa, opts.notarize) if m]
    if len(modes) != 1:
        raise UsageError("Pass exactly one of --archive, --ipa or --notarize.")
    r = ctx.reporter
    settings = ctx.settings
    key_type = settings.key_type
    for path in modes:
        if not os.path.exists(path):
            raise UsageError(f"Not found: {path}")
    mode = "APPLY" if ctx.apply else "PLAN (nothing runs; add --apply)"

    info = BuildInfo(None, None, None)
    if opts.archive:
        info = read_archive_info(opts.archive)
        kind = "xcodebuild -exportArchive (destination: upload)"
        if key_type != "team":
            raise UsageError("xcodebuild needs a team API key (it requires an issuer ID).")
    elif opts.ipa:
        info = read_ipa_info(opts.ipa)
        kind = "altool --upload-package"
        if not opts.app_id:
            raise UsageError("--ipa needs the app's Apple ID: pass --app or set ASC_APP_ID.")
        if opts.platform not in ALTOOL_PLATFORMS:
            raise UsageError("Unsupported platform for altool.")
    else:
        kind = "notarytool submit (Developer ID notarization, not an App Store upload)"
        if key_type == "individual":
            r.warn(
                "Apple's API-key documentation says individual keys can't use notarytool; use a team key if this fails"
            )
    if opts.wait_minutes and not opts.notarize and not opts.app_id:
        raise UsageError("--wait-processing needs the app's Apple ID: pass --app or set ASC_APP_ID.")
    if opts.export_options:
        check_export_options(opts.export_options, r)
    expected_bundle = ctx.settings.bundle_id
    if expected_bundle and info.bundle_id and info.bundle_id != expected_bundle:
        raise UsageError(
            f"The build's bundle ID is {info.bundle_id}, but the configured app is {expected_bundle} "
            "(ASC_BUNDLE_ID / app.bundle_id). Refusing to upload the wrong app."
        )

    r.title(f"upload-build · {kind} · {mode}")
    if info.version:
        r.info(f"{info.bundle_id or '?'} {info.version} ({info.build})")
    result: dict[str, Any] = {"mode": kind, "version": info.version, "build": info.build, "exitCode": None}

    if not ctx.apply:
        # Show the command without loading the key (no Keychain prompt, no SSM call) or creating files.
        stand_in = ApiKey(
            key_id=settings.key_id or "<key-id>",
            issuer_id=(settings.issuer_id or "<issuer-id>") if key_type == "team" else None,
            key_type=key_type,
            pem=b"",
            source="plan",
        )
        preview = _command(
            opts,
            info,
            stand_in,
            "<key-file>",
            opts.export_options or "<generated ExportOptions.plist>",
            opts.export_path or "<temporary directory>",
        )
        r.plain("  $ " + masked_command(preview, stand_in, None))
        if opts.archive and not opts.export_options:
            r.info("export options would be generated: method app-store-connect, destination upload, build number kept")
        r.info("the key isn't loaded in plan mode; `asc-release-kit doctor --offline` tests it")
        r.blank()
        r.plain("Plan only. Re-run with --apply to run it.")
        return result

    key = ctx.api_key()
    ctx.journal()  # checks the journal before Apple's tools run
    if shutil.which("xcrun") is None:
        raise Blocked(
            "Xcode's command-line tools (xcrun) aren't available here; uploads need macOS with Xcode.",
            fix="install Xcode, then run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer "
            "(`asc-release-kit doctor` checks it)",
        )

    temp_dirs: list[str] = []
    try:
        with key_file(key) as key_path:
            ctx.redactor.add_secret(key_path)
            options_plist = opts.export_options or ""
            export_path = opts.export_path or ""
            if opts.archive:
                if not options_plist:
                    tmp = tempfile.mkdtemp(prefix="asc-release-kit-export-")
                    temp_dirs.append(tmp)
                    options_plist = os.path.join(tmp, "ExportOptions.plist")
                    with open(options_plist, "wb") as handle:
                        plistlib.dump(default_export_options(opts.team_id), handle)
                    r.info(
                        "export options: generated (method app-store-connect, destination upload, build number kept)"
                    )
                if not export_path:
                    export_path = tempfile.mkdtemp(prefix="asc-release-kit-out-")
                    temp_dirs.append(export_path)
            argv = _command(opts, info, key, key_path, options_plist, export_path)
            r.plain("  $ " + masked_command(argv, key, key_path))

            tail: deque[str] = deque(maxlen=40)
            raw_lines: list[str] = []

            def on_line(line: str) -> None:
                clean = ctx.redactor.text(line)
                tail.append(clean)
                if opts.notarize:
                    raw_lines.append(line)
                if INTERESTING.search(clean):
                    r.plain("    " + clean)

            started = time.monotonic()
            code = (runner or stream_process)(argv, on_line)
            result["exitCode"] = code
            tool = argv[1] if argv[0] == "xcrun" else argv[0]
            ctx.journal().record("upload", tool=tool, exit=code, version=info.version, build=info.build)
            if code != 0:
                for line in tail:
                    r.plain("    | " + line)
                raise CheckFailed(f"{kind} failed (exit {code}).", fix=upload_hint(tail))
            r.ok(f"finished in {time.monotonic() - started:.0f} s")
    finally:
        for directory in temp_dirs:
            shutil.rmtree(directory, ignore_errors=True)

    if opts.notarize:
        status = _notary_status(raw_lines)
        result["notaryStatus"] = status
        if status and status != "Accepted":
            raise CheckFailed(f"Notarization status: {status}. Run `xcrun notarytool log <submission id>` for details.")
        r.ok(f"notarization status: {status or 'submitted'}")
        return result

    if opts.wait_minutes:
        result["processing"] = _wait_for_processing(ctx, str(opts.app_id), info, opts.platform, opts.wait_minutes)
    else:
        r.info("Apple now processes the build (often 5-30 minutes); `release --wait-for-build` can wait for it")
    return result


def _notary_status(lines: list[str]) -> str | None:
    text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data.get("status") if isinstance(data, dict) else None
