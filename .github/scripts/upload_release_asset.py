"""Safely replace one firmware asset on an immutable GitHub release."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_URL = "https://api.github.com"
UPLOADS_URL = "https://uploads.github.com"
API_VERSION = "2026-03-10"
DEFAULT_PORTS = {"http": 80, "https": 443}
TRANSIENT_URLERROR_TYPES = (
    TimeoutError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
)


class GitHubRequestError(RuntimeError):
    """Raised when GitHub cannot confirm a release-asset operation."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        """Store optional transport metadata used by bounded GET retries.

        Args:
            message: Human-readable request failure.
            status: HTTP status returned by GitHub, when available.
            retryable: Whether the underlying transport failure is transient.
        """
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class HttpResponse:
    """A bounded HTTP response returned by the injectable transport.

    Attributes:
        status: HTTP response status code.
        body: Raw response body bytes.
    """

    status: int
    body: bytes


def normalized_origin(url: str) -> tuple[str, str, int | None] | None:
    """Return a URL origin with its effective port normalized.

    Args:
        url: URL whose origin should be normalized.

    Returns:
        Scheme, lowercase hostname, and effective port, or ``None`` for an
        invalid URL.
    """
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not scheme or not hostname:
        return None
    effective_port = port if port is not None else DEFAULT_PORTS.get(scheme)
    return scheme, hostname.casefold(), effective_port


def is_transient_url_error(error: URLError) -> bool:
    """Classify URL errors that are safe to retry for idempotent GETs.

    Args:
        error: URL error raised by the HTTP transport.

    Returns:
        Whether the underlying cause is a transient network or DNS failure.
    """
    reason = error.reason
    if isinstance(reason, TRANSIENT_URLERROR_TYPES):
        return True
    return isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN


class _GitHubRedirectHandler(HTTPRedirectHandler):
    """Follow redirects while withholding bearer tokens from other origins."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        """Build a redirect request and strip authorization across origins.

        Args:
            req: Original authenticated request.
            fp: Response file object supplied by urllib.
            code: HTTP redirect status code.
            msg: HTTP redirect message.
            headers: Response headers supplied by urllib.
            newurl: Redirect target URL.

        Returns:
            Redirect request, or ``None`` when urllib declines the redirect.
        """
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        source_origin = normalized_origin(req.full_url)
        target_origin = normalized_origin(redirected.full_url)
        if source_origin is None or target_origin is None or source_origin != target_origin:
            redirected.remove_header("Authorization")
        return redirected


def parse_args() -> argparse.Namespace:
    """Parse immutable release-asset upload arguments.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-id", type=int, required=True)
    parser.add_argument("--expected-tag", required=True)
    parser.add_argument("--expected-prerelease", choices=("true", "false"), required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--asset", type=Path)
    parser.add_argument("--asset-name")
    parser.add_argument(
        "--content-type",
        choices=("application/octet-stream", "application/zip"),
    )
    return parser.parse_args()


def http_request(
    method: str, url: str, headers: dict[str, str], body: bytes | None = None
) -> HttpResponse:
    """Issue one HTTP request without exposing sensitive request headers.

    Args:
        method: HTTP method.
        url: Fully qualified GitHub endpoint URL.
        headers: Request headers including the in-memory bearer token.
        body: Optional raw request body.

    Returns:
        HTTP status and raw response bytes.

    Raises:
        GitHubRequestError: If the request cannot complete or GitHub returns an HTTP error.
    """
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(_GitHubRedirectHandler()).open(request, timeout=30) as response:
            return HttpResponse(status=response.status, body=response.read())
    except HTTPError as error:
        raise GitHubRequestError(
            f"GitHub API request returned HTTP status {error.code}.", status=error.code
        ) from error
    except URLError as error:
        raise GitHubRequestError(
            "GitHub API request could not be completed.",
            retryable=is_transient_url_error(error),
        ) from error


def repository_path(repository: str) -> str:
    """Validate and URL-encode a GitHub owner and repository pair.

    Args:
        repository: Repository in ``owner/repository`` form.

    Returns:
        URL-safe repository path.

    Raises:
        ValueError: If the repository does not have exactly one owner separator.
    """
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise ValueError("Repository must use owner/repository form.")
    return f"{quote(owner, safe='')}/{quote(name, safe='')}"


def release_endpoint(repository: str, release_id: int) -> str:
    """Return the exact GitHub release endpoint.

    Args:
        repository: Repository in ``owner/repository`` form.
        release_id: Positive immutable GitHub release ID.

    Returns:
        Exact API endpoint for the identified release.
    """
    return f"{API_URL}/repos/{repository_path(repository)}/releases/{release_id}"


def request_json(
    method: str,
    url: str,
    token: str,
    expected_status: int,
    body: bytes | None = None,
    content_type: str = "application/vnd.github+json",
) -> dict[str, Any]:
    """Make an authenticated GitHub request and require an object JSON response.

    Args:
        method: HTTP method.
        url: Fully qualified GitHub endpoint URL.
        token: GitHub bearer token held only in memory.
        expected_status: Required HTTP response status.
        body: Optional raw request body.
        content_type: Request content type.

    Returns:
        Parsed response object.

    Raises:
        GitHubRequestError: If GitHub returns an unexpected status or response shape.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "X-GitHub-Api-Version": API_VERSION,
    }
    if method == "GET":
        response = http_request_with_retry(method, url, headers, body)
    else:
        response = http_request(method, url, headers, body)
    if response.status != expected_status:
        raise GitHubRequestError(
            f"GitHub API request returned unexpected HTTP status {response.status}."
        )
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as error:
        raise GitHubRequestError("GitHub API response was not valid JSON.") from error
    if not isinstance(payload, dict):
        raise GitHubRequestError("GitHub API response was not an object.")
    return payload


RETRYABLE_HTTP_STATUSES = frozenset({500, 502, 503, 504})
MAX_GET_ATTEMPTS = 4


def http_request_with_retry(
    method: str, url: str, headers: dict[str, str], body: bytes | None = None
) -> HttpResponse:
    """Issue a GET request with bounded retries for transient failures.

    Args:
        method: HTTP method, expected to be ``GET``.
        url: Fully qualified GitHub endpoint URL.
        headers: Request headers including the in-memory bearer token.
        body: Optional raw request body.

    Returns:
        HTTP status and raw response bytes.

    Raises:
        GitHubRequestError: If a transport failure persists or is not retryable.
    """
    if method != "GET":
        return http_request(method, url, headers, body)
    last_error: GitHubRequestError | None = None
    for attempt in range(MAX_GET_ATTEMPTS):
        try:
            response = http_request(method, url, headers, body)
        except HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUSES:
                raise GitHubRequestError(
                    f"GitHub API request returned HTTP status {error.code}.", status=error.code
                ) from error
            last_error = GitHubRequestError(
                f"GitHub API request returned HTTP status {error.code}.", status=error.code
            )
        except URLError as error:
            if not is_transient_url_error(error):
                raise GitHubRequestError("GitHub API request could not be completed.") from error
            last_error = GitHubRequestError(
                "GitHub API request could not be completed.", retryable=True
            )
        except GitHubRequestError as error:
            if not (
                error.retryable
                or (error.status is not None and error.status in RETRYABLE_HTTP_STATUSES)
            ):
                raise
            last_error = error
        else:
            if response.status not in RETRYABLE_HTTP_STATUSES:
                return response
            if attempt == MAX_GET_ATTEMPTS - 1:
                return response
            last_error = GitHubRequestError(
                f"GitHub API request returned HTTP status {response.status}.",
                status=response.status,
            )
        if attempt == MAX_GET_ATTEMPTS - 1:
            if last_error is not None:
                raise last_error
            raise GitHubRequestError("GitHub API request could not be completed.")
        time.sleep(2**attempt)
    raise GitHubRequestError("GitHub API request could not be completed.")


def require_valid_release_inputs(
    release_id: int, expected_tag: str, expected_prerelease: bool
) -> None:
    """Validate release identity arguments shared by both command flows.

    Args:
        release_id: Positive immutable GitHub release ID.
        expected_tag: Expected release tag name.
        expected_prerelease: Expected prerelease state.

    Raises:
        ValueError: If a release identity argument is invalid.
    """
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("Release ID must be a positive integer.")
    if not expected_tag:
        raise ValueError("Expected release tag must not be empty.")
    if type(expected_prerelease) is not bool:
        raise ValueError("Expected prerelease state must be a boolean.")


def require_release_identity(
    release: dict[str, Any], release_id: int, expected_tag: str, expected_prerelease: bool
) -> list[dict[str, Any]]:
    """Prove a fetched release is the published event release.

    Args:
        release: GitHub release response object.
        release_id: Expected positive immutable release ID.
        expected_tag: Expected release tag name.
        expected_prerelease: Expected prerelease state.

    Returns:
        Release assets as response objects.

    Raises:
        GitHubRequestError: If release identity or asset response shape is invalid.
    """
    if (
        type(release.get("id")) is not int
        or release["id"] != release_id
        or release.get("tag_name") != expected_tag
        or release.get("draft") is not False
        or type(release.get("prerelease")) is not bool
        or release["prerelease"] is not expected_prerelease
        or not isinstance(release.get("published_at"), str)
        or not release["published_at"]
    ):
        raise GitHubRequestError("GitHub release does not match the expected published identity.")
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(asset, dict) for asset in assets):
        raise GitHubRequestError("GitHub release assets were not a list of objects.")
    return assets


def named_assets(assets: list[dict[str, Any]], asset_name: str) -> list[dict[str, Any]]:
    """Find only the assets with the requested exact display name.

    Args:
        assets: Assets returned by the exact release endpoint.
        asset_name: Expected display name.

    Returns:
        Assets whose names exactly match ``asset_name``.

    Raises:
        GitHubRequestError: If more than one matching asset exists.
    """
    matches = [asset for asset in assets if asset.get("name") == asset_name]
    if len(matches) > 1:
        raise GitHubRequestError("GitHub release contains duplicate assets with the expected name.")
    return matches


def require_uploaded_asset(asset: dict[str, Any], asset_name: str, size: int, digest: str) -> int:
    """Require GitHub's uploaded asset response to prove exact content identity.

    Args:
        asset: GitHub release-asset response object.
        asset_name: Expected asset display name.
        size: Expected local byte size.
        digest: Expected lowercase SHA-256 digest.

    Returns:
        Positive immutable GitHub asset ID.

    Raises:
        GitHubRequestError: If the asset response is incomplete or mismatched.
    """
    asset_id = asset.get("id")
    if (
        type(asset_id) is not int
        or asset_id <= 0
        or asset.get("name") != asset_name
        or asset.get("state") != "uploaded"
        or asset.get("size") != size
        or asset.get("digest") != f"sha256:{digest}"
    ):
        raise GitHubRequestError("GitHub uploaded asset does not match the local artifact.")
    return asset_id


def read_asset(path: Path) -> tuple[bytes, int, str]:
    """Read a regular asset once and calculate its immutable content identity.

    Args:
        path: Asset path supplied by the release workflow.

    Returns:
        Raw bytes, byte size, and lowercase SHA-256 digest.

    Raises:
        ValueError: If the asset is not a regular file or changes while read.
    """
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Release asset must be a regular file: {path}")
        content = path.read_bytes()
        after = path.lstat()
    except OSError as error:
        raise ValueError(f"Could not read release asset: {path}") from error
    if (
        before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or len(content) != after.st_size
    ):
        raise ValueError("Release asset changed while it was read.")
    return content, len(content), hashlib.sha256(content).hexdigest()


def upload_release_asset(
    repository: str,
    release_id: int,
    expected_tag: str,
    expected_prerelease: bool,
    asset_path: Path,
    asset_name: str,
    content_type: str,
    token: str,
) -> None:
    """Upload a missing named asset only on the exact published GitHub release.

    Args:
        repository: Repository in ``owner/repository`` form.
        release_id: Positive immutable GitHub release ID.
        expected_tag: Expected release tag name.
        expected_prerelease: Expected prerelease state.
        asset_path: Local release asset path.
        asset_name: Exact GitHub asset display name.
        content_type: Approved media type for the uploaded asset.
        token: GitHub bearer token held only in memory.

    Raises:
        ValueError: If command inputs or the local asset are unsafe.
        GitHubRequestError: If GitHub cannot prove release or asset identity.
    """
    require_valid_release_inputs(release_id, expected_tag, expected_prerelease)
    if not asset_name or "/" in asset_name or "\\" in asset_name:
        raise ValueError("Asset name must be a non-empty filename.")
    if content_type not in {"application/octet-stream", "application/zip"}:
        raise ValueError("Asset content type is not approved.")
    if not token:
        raise ValueError("GITHUB_TOKEN is required.")

    content, size, digest = read_asset(asset_path)
    endpoint = release_endpoint(repository, release_id)
    release = request_json("GET", endpoint, token, expected_status=200)
    existing_assets = named_assets(
        require_release_identity(release, release_id, expected_tag, expected_prerelease), asset_name
    )
    if existing_assets:
        try:
            require_uploaded_asset(existing_assets[0], asset_name, size, digest)
        except GitHubRequestError as error:
            raise GitHubRequestError(
                "Existing GitHub release asset conflicts with the candidate; preserve it and recover manually."
            ) from error
        else:
            return

    release = request_json("GET", endpoint, token, expected_status=200)
    if named_assets(
        require_release_identity(release, release_id, expected_tag, expected_prerelease), asset_name
    ):
        raise GitHubRequestError("A same-name release asset appeared before upload.")

    upload_url = (
        f"{UPLOADS_URL}/repos/{repository_path(repository)}/releases/{release_id}/assets"
        f"?name={quote(asset_name, safe='')}"
    )
    uploaded = request_json(
        "POST",
        upload_url,
        token,
        expected_status=201,
        body=content,
        content_type=content_type,
    )
    uploaded_id = require_uploaded_asset(uploaded, asset_name, size, digest)

    release = request_json("GET", endpoint, token, expected_status=200)
    final_assets = named_assets(
        require_release_identity(release, release_id, expected_tag, expected_prerelease), asset_name
    )
    if (
        len(final_assets) != 1
        or require_uploaded_asset(final_assets[0], asset_name, size, digest) != uploaded_id
    ):
        raise GitHubRequestError(
            "GitHub release did not retain the uploaded asset on the exact release."
        )


def verify_release(
    repository: str,
    release_id: int,
    expected_tag: str,
    expected_prerelease: bool,
    token: str,
) -> None:
    """Require the exact release object to remain published and unchanged.

    Args:
        repository: GitHub owner and repository name.
        release_id: Positive immutable GitHub release ID.
        expected_tag: Expected release tag name.
        expected_prerelease: Expected prerelease state.
        token: GitHub bearer token held only in memory.

    Raises:
        ValueError: If command inputs are invalid.
        GitHubRequestError: If GitHub cannot prove the exact published release identity.
    """
    require_valid_release_inputs(release_id, expected_tag, expected_prerelease)
    if not token:
        raise ValueError("GITHUB_TOKEN is required.")
    release = request_json(
        "GET", release_endpoint(repository, release_id), token, expected_status=200
    )
    require_release_identity(release, release_id, expected_tag, expected_prerelease)


def main() -> int:
    """Upload the requested asset using the runtime GitHub token.

    Returns:
        Zero when GitHub confirms the exact asset on the exact release; otherwise one.
    """
    args = parse_args()
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        expected_prerelease = args.expected_prerelease == "true"
        if args.verify_only:
            if (
                args.asset is not None
                or args.asset_name is not None
                or args.content_type is not None
            ):
                raise ValueError("verify-only does not accept asset upload arguments.")
            verify_release(
                args.repository, args.release_id, args.expected_tag, expected_prerelease, token
            )
        else:
            if args.asset is None or args.asset_name is None or args.content_type is None:
                raise ValueError("Asset, asset-name, and content-type are required for upload.")
            upload_release_asset(
                args.repository,
                args.release_id,
                args.expected_tag,
                expected_prerelease,
                args.asset,
                args.asset_name,
                args.content_type,
                token,
            )
    except (GitHubRequestError, ValueError) as error:
        print(f"Release asset upload failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
