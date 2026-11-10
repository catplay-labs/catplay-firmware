# SPDX-License-Identifier: MIT
"""Standalone Unix-socket S3 read proxy. No BitBake imports or disk credentials."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import stat
import struct
import threading
import time

PROTOCOL = 2
WORKERS = 32
POOL_CONNECTIONS = 64
MAX_BACKENDS = 16
IDLE_TIMEOUT = 300
IO_TIMEOUT = 300
MAX_MESSAGE = 1024 * 1024
CHUNK = 1024 * 1024


class ProxyError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__("S3 proxy: " + code)


def send_message(stream, message):
    data = json.dumps(message, separators=(",", ":")).encode()
    if len(data) > MAX_MESSAGE:
        raise ProxyError("MessageTooLarge")
    stream.sendall(struct.pack("!I", len(data)) + data)


def receive_exact(stream, size):
    chunks = []
    while size:
        data = stream.recv(min(size, CHUNK))
        if not data:
            raise ProxyError("IncompleteResponse")
        chunks.append(data)
        size -= len(data)
    return b"".join(chunks)


def receive_message(stream):
    size, = struct.unpack("!I", receive_exact(stream, 4))
    if size > MAX_MESSAGE:
        raise ProxyError("MessageTooLarge")
    return json.loads(receive_exact(stream, size))


def effective_uid():
    # pseudo interposes geteuid(), but SO_PEERCRED reports the kernel identity.
    # /tmp is outside PSEUDO_INCLUDE_PATHS, so its ownership uses this UID too.
    left, right = socket.socketpair()
    try:
        _, uid, _ = struct.unpack("3i", left.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return uid
    finally:
        left.close()
        right.close()


def check_peer(stream):
    _, uid, _ = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != effective_uid():
        raise ProxyError("PeerDenied")


def error_code(error):
    code = getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)
    return code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code) else "UpstreamError"


def private_directory(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != effective_uid() or info.st_mode & 0o077:
        raise ProxyError("UnsafeSocketDirectory")
    return directory


class Backends:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = OrderedDict()

    @contextmanager
    def client(self, config):
        from urllib.parse import urlsplit
        from botocore.config import Config
        import boto3
        names = ("endpoint", "region", "bucket", "access_key", "secret_key")
        identity = tuple(config[name] for name in names)
        if not all(isinstance(value, str) and value for value in identity):
            raise ProxyError("InvalidBackend")
        endpoint = urlsplit(identity[0])
        if endpoint.scheme not in ("https", "http") or not endpoint.netloc or endpoint.username:
            raise ProxyError("InvalidEndpoint")
        with self.lock:
            entry = self.entries.get(identity)
            if entry is None:
                if len(self.entries) >= MAX_BACKENDS:
                    victim = next((key for key, value in self.entries.items() if value[1] == 0), None)
                    if victim is None:
                        raise ProxyError("ProxyBusy")
                    self.entries.pop(victim)[0].close()
                sdk = boto3.session.Session().client("s3", endpoint_url=identity[0], region_name=identity[1],
                    aws_access_key_id=identity[3], aws_secret_access_key=identity[4],
                    config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                  retries={"mode": "standard", "total_max_attempts": 4},
                                  connect_timeout=10, read_timeout=60, max_pool_connections=POOL_CONNECTIONS))
                entry = [sdk, 0]
                self.entries[identity] = entry
            self.entries.move_to_end(identity)
            entry[1] += 1
        try:
            yield entry[0]
        finally:
            with self.lock:
                entry[1] -= 1

    def close(self):
        for client, _ in self.entries.values():
            client.close()


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        stream = self.request
        stream.settimeout(IO_TIMEOUT)
        streaming = False
        try:
            check_peer(stream)
            # Admit before reading the request. A saturated server rejects on
            # accept; clients must not race that close while sending credentials.
            send_message(stream, {"ok": True, "protocol": PROTOCOL})
            request = receive_message(stream)
            if request.get("protocol") != PROTOCOL:
                raise ProxyError("ProtocolMismatch")
            operation = request["operation"]
            if operation in ("PING", "SHUTDOWN"):
                send_message(stream, {"ok": True, "protocol": PROTOCOL, "pid": os.getpid()})
                if operation == "SHUTDOWN":
                    self.server.stopping.set()
                return
            config = request["config"]
            arguments = request["arguments"]
            if arguments.get("Bucket") != config["bucket"]:
                raise ProxyError("BucketMismatch")
            with self.server.backends.client(config) as client:
                started = time.monotonic()
                if operation == "HEAD":
                    response = client.head_object(**arguments)
                    send_message(stream, {"ok": True, "response": {
                        "ContentLength": response["ContentLength"],
                        "ResponseMetadata": {"RetryAttempts": response.get("ResponseMetadata", {}).get("RetryAttempts", 0)},
                        "CatplayTiming": {"headers_ms": (time.monotonic() - started) * 1000}}})
                elif operation == "LIST":
                    response = client.list_objects_v2(**arguments)
                    send_message(stream, {"ok": True, "response": {
                        "Contents": [{"Key": entry["Key"]} for entry in response.get("Contents", [])],
                        "IsTruncated": response.get("IsTruncated", False),
                        "NextContinuationToken": response.get("NextContinuationToken"),
                        "ResponseMetadata": {"RetryAttempts": response.get("ResponseMetadata", {}).get("RetryAttempts", 0)}}})
                elif operation == "GET":
                    response = client.get_object(**arguments)
                    headers_ready = time.monotonic()
                    body = response["Body"]
                    try:
                        first = body.read(1)
                        first_ready = time.monotonic()
                        length = response["ContentLength"]
                        send_message(stream, {"ok": True, "response": {
                            "ContentLength": length,
                            "ResponseMetadata": {"RetryAttempts": response.get("ResponseMetadata", {}).get("RetryAttempts", 0)},
                            "CatplayTiming": {"headers_ms": (headers_ready - started) * 1000,
                                "ttfb_ms": (first_ready - started) * 1000 if first else None}}})
                        streaming = True
                        stream.sendall(first)
                        transferred = len(first)
                        while data := body.read(CHUNK):
                            stream.sendall(data)
                            transferred += len(data)
                        if transferred != length:
                            raise ProxyError("IncompleteResponse")
                    finally:
                        body.close()
                else:
                    raise ProxyError("UnsupportedOperation")
        except Exception as error:
            # After a successful GET header, EOF conveys an incomplete body.
            # Never inject error JSON into an object or expose SDK exception text.
            if not streaming:
                try:
                    send_message(stream, {"ok": False, "code": error_code(error)})
                except OSError:
                    pass


class ControlHandler(socketserver.BaseRequestHandler):
    def handle(self):
        # A separate socket/thread keeps health checks out of the transfer queue.
        self.request.settimeout(1)
        try:
            check_peer(self.request)
            message = receive_message(self.request)
            if message.get("protocol") != PROTOCOL:
                raise ProxyError("ProtocolMismatch")
            if message.get("operation") not in ("PING", "SHUTDOWN"):
                raise ProxyError("UnsupportedOperation")
            send_message(self.request, {"ok": True, "protocol": PROTOCOL, "pid": os.getpid()})
            if message["operation"] == "SHUTDOWN":
                self.server.stopping.set()
        except Exception as error:
            try:
                send_message(self.request, {"ok": False, "code": error_code(error)})
            except OSError:
                pass


def control_path(filename):
    return Path(filename).with_suffix('.control.sock')


class Server(socketserver.UnixStreamServer):
    request_queue_size = WORKERS * 4

    def __init__(self, filename):
        self.backends = Backends()
        self.pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="s3-proxy")
        self.slots = threading.BoundedSemaphore(WORKERS * 2)
        self.state_lock = threading.Lock()
        self.active = 0
        self.last_activity = time.monotonic()
        self.stopping = threading.Event()
        super().__init__(filename, Handler)
        self.timeout = 1

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                send_message(request, {"ok": False, "code": "ProxyBusy"})
            finally:
                self.shutdown_request(request)
            return
        with self.state_lock:
            self.active += 1
        self.pool.submit(self.run_request, request, address)

    def run_request(self, request, address):
        try:
            self.finish_request(request, address)
        finally:
            self.shutdown_request(request)
            with self.state_lock:
                self.active -= 1
                self.last_activity = time.monotonic()
            self.slots.release()

    def idle(self, seconds):
        with self.state_lock:
            return not self.active and time.monotonic() - self.last_activity >= seconds

    def server_close(self):
        super().server_close()
        self.pool.shutdown(wait=True)
        self.backends.close()


def serve(filename, idle_timeout=IDLE_TIMEOUT):
    filename = Path(filename)
    private_directory(filename.parent)
    previous_umask = os.umask(0o077)
    try:
        control = socketserver.UnixStreamServer(str(control_path(filename)), ControlHandler)
        try:
            server = Server(str(filename))
        except BaseException:
            control.server_close()
            control_path(filename).unlink(missing_ok=True)
            raise
        for path in (filename, control_path(filename)):
            os.chmod(path, 0o600)
    finally:
        os.umask(previous_umask)
    control.stopping = server.stopping
    control_thread = threading.Thread(target=control.serve_forever,
                                      kwargs={"poll_interval": .1}, name="s3-proxy-control", daemon=True)
    control_thread.start()
    inode = filename.stat().st_ino
    try:
        signal.signal(signal.SIGTERM, lambda *_: server.stopping.set())
        signal.signal(signal.SIGINT, lambda *_: server.stopping.set())
        while not server.stopping.is_set() and not server.idle(idle_timeout):
            server.handle_request()
    finally:
        control.shutdown()
        control_thread.join()
        control.server_close()
        control_path(filename).unlink(missing_ok=True)
        server.server_close()
        if filename.exists() and filename.stat().st_ino == inode:
            filename.unlink()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--idle-timeout", type=float, default=IDLE_TIMEOUT)
    options = parser.parse_args()
    serve(options.socket, options.idle_timeout)
