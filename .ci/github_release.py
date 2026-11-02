#!/usr/bin/env python3
"""Create/update a GitHub release and upload the files from an artifact directory."""
import argparse
from email.utils import parsedate_to_datetime
import functools
import http.client
import json
import mimetypes
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class APIError(RuntimeError):
    def __init__(self, error):
        self.status = error.code
        self.headers = error.headers
        self.detail = error.read().decode("utf-8", "replace")
        error.close()
        super().__init__(f"GitHub API HTTP {self.status}: {self.detail}")


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def repo_slug(value):
    repo = re.sub(r"^https://github\.com/", "", value.strip()).rstrip("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise argparse.ArgumentTypeError("expected OWNER/REPO or a GitHub repository URL")
    return repo


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=repo_slug, required=True, help="destination OWNER/REPO")
    parser.add_argument("--tag", required=True, help="release tag")
    parser.add_argument("--release-notes", type=Path, required=True, help="UTF-8 release notes file")
    parser.add_argument("--artifacts-dir", type=Path, required=True,
                        help="upload all non-hidden regular files directly in this directory")
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("--token", help="GitHub API token; prefer --token-file to keep it out of process arguments")
    auth.add_argument("--token-file", type=Path, help="file containing the GitHub API token; accepts /dev/stdin")
    parser.add_argument("--target-commitish", help="target branch or SHA; defaults to the destination repository's default branch")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--upload-attempts", type=positive_int, default=5,
                        help="maximum attempts per artifact, including the first (default: 5)")
    parser.add_argument("--retry-delay", type=positive_int, default=2,
                        help="initial retry delay in seconds, doubles up to 60 (default: 2)")
    parser.add_argument("--request-timeout", type=positive_int, default=300,
                        help="socket timeout in seconds (default: 300)")
    args = parser.parse_args(argv)
    args.tag = args.tag.strip()
    if not args.tag:
        parser.error("--tag must not be empty")
    if not args.release_notes.is_file():
        parser.error("--release-notes must point to a file")
    if not args.artifacts_dir.is_dir():
        parser.error("--artifacts-dir must point to a directory")
    return args


def github_request(method, url, *, token, timeout, data=None,
                   content_type="application/json", content_length=None, allow_not_found=False):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "catplay-release",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = content_type
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404 and allow_not_found:
            error.close()
            return None
        raise APIError(error) from None
    return json.loads(body) if body else {}


def existing_assets(request, release):
    page = 1
    while True:
        separator = "&" if "?" in release["assets_url"] else "?"
        batch = request("GET", release["assets_url"] + f"{separator}per_page=100&page={page}")
        yield from batch
        if len(batch) < 100:
            break
        page += 1


def retryable(error):
    if not isinstance(error, APIError):
        return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException))
    if error.status in (408, 429) or 500 <= error.status <= 599:
        return True
    if error.status == 403:
        return (error.headers.get("Retry-After") is not None
                or error.headers.get("X-RateLimit-Remaining") == "0"
                or "rate limit" in error.detail.lower())
    # An earlier POST can have succeeded even if its response was lost.
    return error.status == 422 and "already_exists" in error.detail


def retry_delay(error, initial, attempt):
    delay = min(initial * 2 ** (attempt - 1), 60)
    if not isinstance(error, APIError):
        return delay
    after = error.headers.get("Retry-After")
    if after is not None:
        try:
            wait = float(after)
        except ValueError:
            try:
                wait = parsedate_to_datetime(after).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                wait = 0
        delay = max(delay, wait)
    if error.headers.get("X-RateLimit-Remaining") == "0":
        try:
            delay = max(delay, float(error.headers["X-RateLimit-Reset"]) - time.time() + 1)
        except (KeyError, ValueError):
            delay = max(delay, 60)
    elif error.status in (403, 429) and after is None:
        delay = max(delay, 60)
    return delay


def upload_asset(request, release, asset, *, attempts=5, initial_delay=2):
    url = release["upload_url"].split("{", 1)[0]
    url += "?" + urllib.parse.urlencode({"name": asset.name})
    content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    for attempt in range(1, attempts + 1):
        try:
            # Reconcile on every attempt, including leftover 'starter' assets
            # after a 502 or a successful upload whose response never arrived.
            matches = [item for item in existing_assets(request, release) if item["name"] == asset.name]
            for item in matches:
                request("DELETE", item["url"], allow_not_found=True)
            # Reopen from byte zero on retry; stream large archives instead of
            # holding the complete artifact in RAM. Content-Length avoids chunked encoding.
            with asset.open("rb") as stream:
                request("POST", url, data=iter(lambda: stream.read(1024 * 1024), b""),
                        content_type=content_type, content_length=asset.stat().st_size)
            print(f"uploaded {asset}", flush=True)
            return
        except (APIError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as error:
            if attempt == attempts or not retryable(error):
                raise
            delay = retry_delay(error, initial_delay, attempt)
            print(f"upload {asset.name}: attempt {attempt}/{attempts} failed ({error}); "
                  f"retrying in {delay:.1f}s", file=sys.stderr, flush=True)
            time.sleep(delay)


def main(argv=None):
    args = parse_args(argv)
    token = (args.token if args.token is not None else args.token_file.read_text(encoding="utf-8")).strip()
    if not token:
        raise ValueError("GitHub API token is empty")
    notes = args.release_notes.read_text(encoding="utf-8")
    assets = sorted(path for path in args.artifacts_dir.iterdir()
                    if path.is_file() and not path.is_symlink() and not path.name.startswith("."))
    if not assets:
        raise ValueError(f"no artifacts found in {args.artifacts_dir}")
    request = functools.partial(github_request, token=token, timeout=args.request_timeout)
    api = f"https://api.github.com/repos/{args.repo}"
    release = request("GET", f"{api}/releases/tags/{urllib.parse.quote(args.tag, safe='')}",
                      allow_not_found=True)
    metadata = {"tag_name": args.tag, "name": args.tag, "body": notes, "prerelease": args.prerelease}
    if args.target_commitish is not None:
        metadata["target_commitish"] = args.target_commitish
    payload = json.dumps(metadata).encode("utf-8")
    if release is None:
        release = request("POST", f"{api}/releases", data=payload)
    else:
        release = request("PATCH", release["url"], data=payload)
    for asset in assets:
        upload_asset(request, release, asset, attempts=args.upload_attempts, initial_delay=args.retry_delay)
    print(f"GitHub release ready: {args.repo} {args.tag}")


if __name__ == "__main__":
    try:
        main()
    except (APIError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError, ValueError) as error:
        print(f"github_release.py: {error}", file=sys.stderr)
        sys.exit(1)
