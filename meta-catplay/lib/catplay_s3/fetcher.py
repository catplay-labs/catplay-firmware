# SPDX-License-Identifier: MIT
"""Fetch private S3 objects through a pooled local Unix-socket proxy.

Custom endpoints and S3_UPLOAD_* credentials apply only to CATPLAY_S3_BUCKET.
Other s3:// sources use boto3's normal AWS credential/endpoint resolution.
Task clients are lightweight; the proxy owns HTTPS pools across task processes.
Background index workers use the same proxy and backend pool as downloads.
The completed index is also published locally for exec'ed BitBake workers.
"""

import os
import json
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import tempfile
import threading
import time
from urllib.parse import urlsplit

import bb.data
import bb.fetch2
from bb.fetch2 import FetchError, FetchMethod

_clients = threading.local()
_indexes = {}
_index_lock = threading.Lock()
INDEX_WORKERS = 8
INDEX_HEX_GROUPS = 8


class _BucketIndex:
    def __init__(self):
        self.pid = os.getpid()
        self.ready = threading.Event()
        self.keys = None
        self.cancelled = False
        self.aborted = False
        self.filename = None


def _after_fork():
    global _index_lock, _indexes
    # A child cannot join a thread from its parent. Completed immutable sets
    # are safe to inherit; incomplete listings fall back to ordinary HEAD.
    _index_lock = threading.Lock()
    _indexes = {key: index for key, index in _indexes.items() if index.keys is not None}


os.register_at_fork(after_in_child=_after_fork)


def _sdk():
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise FetchError("CatPlay S3 requires boto3 in BitBake's Python; rebuild the builder image (no AWS CLI fallback)") from None
    return boto3, Config


def _credentials(d):
    # Use this invocation's original environment, never the daemon's stale
    # os.environ. Do not promote credentials into recipe variables or log them.
    environment = d.getVar("BB_ORIGENV", False)
    values = tuple(environment.getVar(name, False) if environment is not None else None
                   for name in ("S3_UPLOAD_ACCESS_KEY_ID", "S3_UPLOAD_SECRET_ACCESS_KEY"))
    if not all(values):
        raise FetchError("Private CatPlay S3 requires S3_UPLOAD_ACCESS_KEY_ID and S3_UPLOAD_SECRET_ACCESS_KEY")
    return values


def validate(d):
    _sdk()
    for name in ("CATPLAY_S3_ENDPOINT", "CATPLAY_S3_REGION", "CATPLAY_S3_BUCKET"):
        if not d.getVar(name):
            raise FetchError("Private CatPlay S3 requires " + name)
    _credentials(d)


def _client(d, bucket):
    started = time.monotonic()
    boto3, Config = _sdk()
    custom = bucket == d.getVar("CATPLAY_S3_BUCKET")
    if custom:
        validate(d)
    endpoint = d.getVar("CATPLAY_S3_ENDPOINT") if custom else None
    region = d.getVar("CATPLAY_S3_REGION") if custom else None
    identity = (endpoint, region, *_credentials(d)) if custom else (None,)
    if getattr(_clients, "pid", None) != os.getpid():
        _clients.pid = os.getpid()
        _clients.entries = {}
    # One entry per backend; rotation replaces the old credentials/client.
    scope = bucket if custom else "default-aws"
    previous = _clients.entries.get(scope)
    if previous is None or previous[0] != identity:
        kwargs = {"config": Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"} if custom else {},
            retries={"mode": "standard", "total_max_attempts": 4},
            connect_timeout=10, read_timeout=60, max_pool_connections=max(10, INDEX_WORKERS),
        )}
        if custom:
            kwargs.update(endpoint_url=endpoint, region_name=region,
                          aws_access_key_id=identity[2], aws_secret_access_key=identity[3])
        # A separate Session avoids boto3's shared default session across threads.
        client = boto3.session.Session().client("s3", **kwargs)
        if previous is not None:
            previous[1].close()
        _clients.entries[scope] = (identity, client)
        _trace_download(d, {"operation": "CLIENT", "total_ms": (time.monotonic() - started) * 1000})
    return _clients.entries[scope][1]


def _failure(operation, error, url):
    # SDK exceptions may contain request details: expose only their code/type.
    from .proxy import error_code
    code = error_code(error)
    source = urlsplit(url)
    # BitBake hides mirror FetchErrors at DEBUG before reporting a misleading
    # file:// failure. Log here, without URL credentials, query or SDK text.
    bb.warn("boto3 S3 %s failed (%s): s3://%s%s" %
            (operation, code, source.hostname or "", source.path))
    raise FetchError("boto3 S3 %s failed (%s)" % (operation, code), url) from None


def _fetch_client(d, bucket):
    if bucket != d.getVar("CATPLAY_S3_BUCKET"):
        return _client(d, bucket)
    from . import proxy_client
    started = time.monotonic()
    access_key, secret_key = _credentials(d)
    config = {"endpoint": d.getVar("CATPLAY_S3_ENDPOINT"),
              "region": d.getVar("CATPLAY_S3_REGION"), "bucket": bucket,
              "access_key": access_key, "secret_key": secret_key}
    directory = proxy_client.runtime_directory(d)
    identity = (str(directory), *config.values())
    if getattr(_clients, "pid", None) != os.getpid():
        _clients.pid = os.getpid()
        _clients.entries = {}
        _clients.proxies = {}
    entries = getattr(_clients, "proxies", {})
    previous = entries.get(bucket)
    if previous is None or previous[0] != identity:
        entries[bucket] = (identity, proxy_client.Client(directory, config))
        _clients.proxies = entries
        _trace_download(d, {"operation": "CLIENT", "total_ms": (time.monotonic() - started) * 1000})
    return entries[bucket][1]


def _trace_download(d, record):
    """Opt-in per-object timings; never record credentials or signed headers."""
    filename = d.getVar("CATPLAY_S3_TRACE_FILE")
    if filename:
        record.update(pid=os.getpid(), timestamp=time.time())
        record.setdefault("operation", "GET")
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(record) + "\n").encode())
        finally:
            os.close(descriptor)


def clear_indexes():
    with _index_lock:
        for index in _indexes.values():
            index.cancelled = True
            if index.filename is not None:
                index.filename.unlink(missing_ok=True)
        _indexes.clear()


def _index_identity(d, bucket):
    if bucket != d.getVar("CATPLAY_S3_BUCKET"):
        return None
    return (d.getVar("CATPLAY_S3_ENDPOINT"), d.getVar("CATPLAY_S3_REGION"),
            bucket, *_credentials(d))



def _disk_index_path(d, bucket):
    identity = _index_identity(d, bucket)
    topdir = d.getVar("TOPDIR")
    if identity is None or not topdir:
        return None
    # Partition by backend and credentials without storing secrets on disk.
    fingerprint = hashlib.sha256(repr(identity).encode()).hexdigest()
    return Path(topdir) / "cache" / ("catplay-s3-index-" + fingerprint + ".sqlite3")


def _publish_disk_index(filename, keys):
    if filename is None:
        return
    filename.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=filename.parent, prefix=".catplay-s3-index-", delete=False) as output:
            temporary = Path(output.name)
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("CREATE TABLE objects (key TEXT PRIMARY KEY) WITHOUT ROWID")
            connection.executemany("INSERT INTO objects VALUES (?)", ((key,) for key in sorted(keys)))
            connection.commit()
        finally:
            connection.close()
        temporary.replace(filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _disk_status(d, bucket, key):
    filename = _disk_index_path(d, bucket)
    if filename is None or not filename.is_file():
        return None
    try:
        connection = sqlite3.connect(filename.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            return connection.execute("SELECT 1 FROM objects WHERE key = ?", (key,)).fetchone() is not None
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        # Corrupt/inaccessible worker cache must never produce false negatives.
        return None


def _index_ranges(d, bucket):
    # Split around Yocto's hash fanout, including uninative's native sstate
    # namespace. These are lexical ranges, NOT prefix filters: together they
    # still cover every key in the bucket, including unrelated namespaces.
    source = urlsplit(d.getVar("SOURCE_MIRROR_URL") or "")
    if source.scheme != "s3" or source.netloc != bucket or not source.path.endswith("/downloads"):
        return [("", None)]
    root = source.path.lstrip("/").removesuffix("downloads") + "sstate-cache/"
    boundaries = sorted({root + namespace + format(i, "x")
                         for namespace in ("", "universal/")
                         for i in range(0, 16, 16 // INDEX_HEX_GROUPS)})
    return list(zip([""] + boundaries, boundaries + [None]))


def _list_range(client, bucket, start, end, index):
    request = {"Bucket": bucket, "MaxKeys": 1000}
    if start:
        request["StartAfter"] = start
    keys = set()
    pages = retries = 0
    try:
        while not (index.cancelled or index.aborted):
            response = client.list_objects_v2(**request)
            pages += 1
            retries += response.get("ResponseMetadata", {}).get("RetryAttempts", 0)
            batch = [item["Key"] for item in response.get("Contents", [])]
            keys.update(key for key in batch if end is None or key <= end)
            if not response.get("IsTruncated") or (batch and end is not None and batch[-1] >= end):
                break
            token = response.get("NextContinuationToken")
            if not token or token == request.get("ContinuationToken"):
                raise ValueError("invalid S3 listing pagination")
            request.pop("StartAfter", None)
            request["ContinuationToken"] = token
    except Exception:
        index.aborted = True
        raise
    return keys, pages, retries


def _list_bucket(client, d, bucket, index):
    ranges = _index_ranges(d, bucket)
    if len(ranges) == 1:
        return _list_range(client, bucket, "", None, index)
    # Small buckets need only one request, not one per planned range. Reuse
    # this first page when splitting the remainder of a larger bucket.
    first = client.list_objects_v2(Bucket=bucket, MaxKeys=1000)
    keys = {item["Key"] for item in first.get("Contents", [])}
    pages = 1
    retries = first.get("ResponseMetadata", {}).get("RetryAttempts", 0)
    if not first.get("IsTruncated"):
        return keys, pages, retries
    if not keys:
        raise ValueError("truncated S3 listing without keys")
    last = max(keys)
    ranges = [(max(start, last), end) for start, end in ranges if end is None or end > last]
    # A pre-created boto3 client is thread-safe within one process.
    with ThreadPoolExecutor(max_workers=min(INDEX_WORKERS, len(ranges))) as pool:
        parts = pool.map(lambda bounds: _list_range(client, bucket, *bounds, index), ranges)
        for part_keys, part_pages, part_retries in parts:
            keys.update(part_keys)
            pages += part_pages
            retries += part_retries
    return keys, pages, retries


def prefetch_index(d):
    """Start one paginated whole-bucket listing; no network at module import."""
    bucket = d.getVar("CATPLAY_S3_BUCKET")
    if not bucket:
        return
    endpoint = d.getVar("CATPLAY_S3_ENDPOINT")
    try:
        bb.fetch2.check_network_access(d, "boto3 S3 LIST", endpoint)
    except (bb.fetch2.NetworkAccess, bb.fetch2.UntrustedUrl):
        return
    identity = _index_identity(d, bucket)
    with _index_lock:
        if identity in _indexes:
            return
        index = _BucketIndex()
        index.filename = _disk_index_path(d, bucket)
        if index.filename is not None:
            # Clear a previous build/crashed server before collecting this build.
            index.filename.unlink(missing_ok=True)
        _indexes[identity] = index
    # Freeze this build's configuration before another thread reads it.
    config = bb.data.createCopy(d)

    def collect():
        started = time.monotonic()
        try:
            client = _fetch_client(config, bucket)
            keys, pages, retries = _list_bucket(client, config, bucket, index)
            if index.cancelled:
                return
            # Publish only the complete snapshot, never partial results.
            # Serialize publication with BuildCompleted/new BuildStarted:
            # a cancelled listing must never recreate a stale worker snapshot.
            with _index_lock:
                if index.cancelled:
                    return
                try:
                    _publish_disk_index(index.filename, keys)
                except (OSError, sqlite3.Error):
                    bb.warn("S3 index: worker cache unavailable; workers will use HEAD")
                index.keys = frozenset(keys)
            bb.note("S3 index: %d objects, %d LIST requests, %d retries in %.1fs" %
                    (len(keys), pages, retries, time.monotonic() - started))
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)
            if not index.cancelled:
                bb.warn("S3 index: listing failed (%s); using per-object HEAD checks" % code)
        finally:
            index.ready.set()

    bb.note("S3 index: listing bucket in background")
    threading.Thread(target=collect, name="catplay-s3-index", daemon=True).start()


def begin_build(d):
    # BuildStarted runs even when ConfigParsed is skipped by an idle server.
    clear_indexes()
    prefetch_index(d)


def _indexed_status(d, bucket, key):
    identity = _index_identity(d, bucket)
    if identity is None:
        return None
    with _index_lock:
        index = _indexes.get(identity)
    if index is None:
        # Only scheduled workers may use this build's published snapshot.
        # Configuration-time fetches must not see a previous crashed build.
        return _disk_status(d, bucket, key) if d.getVar("BB_WORKERCONTEXT") == "1" else None
    if index.keys is None:
        if index.pid != os.getpid():
            return None
        index.ready.wait()
    return key in index.keys if index.keys is not None else None


class S3(FetchMethod):
    _catplay_boto3 = True

    def supports(self, ud, d):
        return ud.type == "s3"

    def recommends_checksum(self, ud):
        return True

    def urldata_init(self, ud, d):
        ud.basename = ud.parm.get("downloadfilename", os.path.basename(ud.path))
        ud.localfile = ud.basename

    def _access(self, ud, d, operation):
        custom = ud.host == d.getVar("CATPLAY_S3_BUCKET")
        url = d.getVar("CATPLAY_S3_ENDPOINT") if custom else ud.url
        # Preserve BB_NO_NETWORK and BB_ALLOWED_NETWORKS for SDK requests too.
        bb.fetch2.check_network_access(d, "boto3 S3 " + operation, url or ud.url)

    def checkstatus(self, fetch, ud, d):
        started = time.monotonic()
        self._access(ud, d, "HEAD")
        indexed = _indexed_status(d, ud.host, ud.path.lstrip("/"))
        if indexed is not None:
            if not indexed:
                raise FetchError("boto3 S3 object absent from build index (404)", ud.url)
            return True
        try:
            client = _fetch_client(d, ud.host)
            client_ready = time.monotonic()
            response = client.head_object(Bucket=ud.host, Key=ud.path.lstrip("/"))
            finished = time.monotonic()
            _trace_download(d, {"operation": "HEAD", "key": ud.path.lstrip("/"),
                               "client_ms": (client_ready - started) * 1000,
                               "headers_ms": response.get("CatplayTiming", {}).get("headers_ms", (finished - client_ready) * 1000),
                               "total_ms": (finished - started) * 1000,
                               "retries": response.get("ResponseMetadata", {}).get("RetryAttempts", 0)})
        except Exception as error:
            _failure("HEAD", error, ud.url)
        return True

    def download(self, ud, d):
        started = time.monotonic()
        self._access(ud, d, "GET")
        temporary = None
        try:
            client = _fetch_client(d, ud.host)
            client_ready = time.monotonic()
            response = client.get_object(Bucket=ud.host, Key=ud.path.lstrip("/"))
            headers_ready = time.monotonic()
            body = response["Body"]
            try:
                # Measure first payload byte, not just get_object() returning headers.
                first_byte = body.read(1)
                first_byte_ready = time.monotonic()
                ttfb_ms = (first_byte_ready - client_ready) * 1000 if first_byte else None
                timing = response.get("CatplayTiming", {})
                ttfb_ms = timing.get("ttfb_ms", ttfb_ms)
                target = Path(ud.localpath)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".catplay-s3-", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(first_byte)
                    shutil.copyfileobj(body, output, length=1024 * 1024)
                temporary.replace(target)
                finished = time.monotonic()
                size = target.stat().st_size
                bb.verbnote("S3 GET %s: %d bytes in %.3fs, TTFB %s" %
                            (ud.path.lstrip("/"), size, finished - started,
                             "%.1fms" % ttfb_ms if ttfb_ms is not None else "n/a"))
                _trace_download(d, {
                    "key": ud.path.lstrip("/"), "bytes": size,
                    "client_ms": (client_ready - started) * 1000,
                    "headers_ms": timing.get("headers_ms", (headers_ready - client_ready) * 1000),
                    "ttfb_ms": ttfb_ms,
                    "body_ms": (finished - headers_ready) * 1000,
                    "total_ms": (finished - started) * 1000,
                    "retries": response.get("ResponseMetadata", {}).get("RetryAttempts", 0),
                })
            finally:
                body.close()
        except Exception as error:
            _failure("GET", error, ud.url)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True


def install():
    # Remove both upstream and previously registered instances, including on
    # configuration reload. A missing SDK is an error, not a CLI fallback.
    bb.fetch2.methods[:] = [method for method in bb.fetch2.methods
                           if method.__class__.__module__ not in ("bb.fetch2.s3", "bb.fetch.s3")
                           and not getattr(method, "_catplay_boto3", False)] + [S3()]


install()
