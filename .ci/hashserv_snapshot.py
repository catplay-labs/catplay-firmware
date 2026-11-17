"""Portable S3 snapshot transfer and offline merging of BitBake hashserv databases."""
from contextlib import closing, contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from botocore.exceptions import ClientError

from ci_common import backup_database, s3_client, validate_database


def s3_location(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 's3' or not parsed.netloc or not parsed.path.strip('/')
            or parsed.username is not None or parsed.password is not None
            or ':' in parsed.netloc or parsed.query or parsed.fragment
            or any(char.isspace() for char in url)):
        raise ValueError('Hashserv snapshot requires s3://BUCKET/KEY without credentials or query')
    return parsed.netloc, parsed.path.lstrip('/')


@contextmanager
def local_hashserv(bitbake_dir, snapshot_url, upload_url):
    """Own one server for a build, including cleanup after a failed build."""
    for url in (snapshot_url, upload_url):
        if url:
            _, key = s3_location(url)
            if not key.endswith(('.json', '.sqlite3.gz')):
                raise ValueError('Hashserv snapshot must end in .json or .sqlite3.gz')
    if upload_url and not upload_url.endswith('.sqlite3.gz'):
        raise ValueError('Hashserv upload must end in .sqlite3.gz')
    store = S3SnapshotStore()
    # A short absolute path fits Unix socket path limits, including on desktops.
    with tempfile.TemporaryDirectory(prefix='hashserv-', dir='/tmp') as temporary:
        directory = Path(temporary)
        database = directory / 'database.sqlite3'
        if snapshot_url:
            if store.download(snapshot_url, database, missing_ok=True):
                print(f'hashserv: restored {snapshot_url}', flush=True)
            else:
                print(f'WARNING: hashserv snapshot not found: {snapshot_url}; starting empty',
                      file=sys.stderr, flush=True)
        endpoint = 'unix://' + str(directory / 'server.sock')
        command = [sys.executable, str(Path(bitbake_dir) / 'bin/bitbake-hashserv'),
                   '--bind', endpoint, '--database', str(database), '--anon-perms', '@read,@report']
        # A dedicated session keeps terminal Ctrl-C from interrupting server
        # cleanup before the worker has stopped its own BitBake invocation.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('HASHSERVER_')}
        process = subprocess.Popen(command, start_new_session=True, env=environment)
        ready = False
        try:
            deadline = time.monotonic() + 60
            while not (directory / 'server.sock').exists():
                if process.poll() is not None:
                    raise RuntimeError(f'Local hashserv exited during startup ({process.returncode})')
                if time.monotonic() >= deadline:
                    raise RuntimeError('Local hashserv startup timed out')
                time.sleep(.1)
            ready = True
            print(f'hashserv: local server at {endpoint}', flush=True)
            yield endpoint
            if process.poll() is not None:
                raise RuntimeError(f'Local hashserv exited during build ({process.returncode})')
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if ready and upload_url:
                    store.upload(upload_url, database)
                    print(f'hashserv: uploaded {upload_url}', flush=True)
            except Exception as error:
                if not failed:
                    raise
                print(f'WARNING: hashserv finalization failed ({type(error).__name__}); '
                      'original build error preserved', file=sys.stderr, flush=True)


class S3SnapshotStore:
    def __init__(self, client=None):
        self.client = client if client is not None else s3_client()
        self.storage_class = os.environ.get('S3_UPLOAD_STORAGE_CLASS', '').strip()

    def _get(self, bucket, key):
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if error.response['Error']['Code'] in {'NoSuchKey', '404'}:
                return None
            raise
        with response['Body'] as body:
            return body.read(), response.get('Metadata', {})

    def download(self, url, destination, *, missing_ok=False):
        bucket, key = s3_location(url)
        if not key.endswith(('.json', '.sqlite3.gz')):
            raise ValueError('Hashserv snapshot must end in .json or .sqlite3.gz')
        result = self._get(bucket, key)
        if result is None:
            if missing_ok:
                return False
            raise FileNotFoundError(f'Missing hashserv snapshot: {url}')
        payload, metadata = result
        if key.endswith('.json'):
            pointer = json.loads(payload)
            prefix = key.rpartition('/')[0]
            expected_prefix = (prefix + '/' if prefix else '') + 'snapshots/'
            if (not pointer['key'].startswith(expected_prefix)
                    or not pointer['key'].endswith('.sqlite3.gz')):
                raise ValueError('Snapshot pointer is outside hashserv snapshots')
            result = self._get(bucket, pointer['key'])
            if result is None:
                raise ValueError('Snapshot pointer target is missing')
            payload, _ = result
            checksum = pointer['sha256']
        else:
            checksum = metadata.get('sha256')
        if not checksum or hashlib.sha256(payload).hexdigest() != checksum:
            raise ValueError('Hashserv snapshot checksum mismatch or missing checksum')
        # Validate before replacing a caller's database, even on corrupt input.
        destination = Path(destination)
        with tempfile.TemporaryDirectory(prefix='hashserv-download-', dir=destination.parent) as directory:
            temporary = Path(directory) / 'database.sqlite3'
            try:
                temporary.write_bytes(gzip.decompress(payload))
                validate_database(temporary)
            except (OSError, EOFError, sqlite3.DatabaseError, RuntimeError) as error:
                raise ValueError('Invalid hashserv snapshot database') from error
            temporary.replace(destination)
        return True

    def upload(self, url, source):
        bucket, key = s3_location(url)
        if not key.endswith('.sqlite3.gz'):
            raise ValueError('Hashserv upload must end in .sqlite3.gz')
        with tempfile.TemporaryDirectory(prefix='hashserv-upload-') as directory:
            target = Path(directory) / 'snapshot.sqlite3'
            backup_database(source, target)
            payload = gzip.compress(target.read_bytes(), compresslevel=6, mtime=0)
        checksum = hashlib.sha256(payload).hexdigest()
        options = {'StorageClass': self.storage_class} if self.storage_class else {}
        self.client.put_object(Bucket=bucket, Key=key, Body=payload,
                               ContentType='application/gzip', Metadata={'sha256': checksum}, **options)
        return {'key': key, 'sha256': checksum}


# The schema at the BitBake revision pinned by this repository. Refuse to
# silently discard new fields after a future BitBake schema change.
COLUMNS = {
    'unihashes_v3': ('id', 'method', 'taskhash', 'unihash', 'gc_mark'),
    'outhashes_v2': ('id', 'method', 'taskhash', 'outhash', 'created', 'owner',
                     'PN', 'PV', 'PR', 'task', 'outhash_siginfo'),
    'users': ('id', 'username', 'token', 'permissions'),
    'config': ('id', 'name', 'value'),
}
UNIQUE = {
    'unihashes_v3': ('method', 'taskhash'),
    'outhashes_v2': ('method', 'taskhash', 'outhash'),
    'users': ('username',), 'config': ('name',),
}


def database_schema(db, schema='main'):
    tables = {row[0] for row in db.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables != set(COLUMNS):
        raise ValueError('Unsupported hashserv database schema (tables)')
    result = {}
    for table, expected in COLUMNS.items():
        columns = db.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall()
        if tuple(row[1] for row in columns) != expected:
            raise ValueError(f'Unsupported hashserv database schema ({table})')
        uniques = []
        for row in db.execute(f'PRAGMA {schema}.index_list("{table}")').fetchall():
            if row[2]:
                # Index names come from the database; quote embedded quotes.
                name = row[1].replace('"', '""')
                uniques.append(tuple(col[2] for col in db.execute(
                    f'PRAGMA {schema}.index_info("{name}")')))
        if UNIQUE[table] not in uniques:
            raise ValueError(f'Unsupported hashserv database schema (uniqueness of {table})')
        result[table] = columns
    return result


def merge_database(destination, source):
    """Union records, preserving established taskhash mappings and sstate names.

    BitBake's insert_unihash is first-wins, including reports for a taskhash
    that already has a different unihash. Never renumber/normalize unihashes:
    independently built sstate objects are stored under those original hashes.
    Numeric row IDs are local to each database and must not be copied.
    """
    validate_database(destination)
    validate_database(source)
    with closing(sqlite3.connect(Path(destination).resolve().as_uri(), uri=True)) as db:
        schema = database_schema(db)
        db.execute('ATTACH DATABASE ? AS incoming', (Path(source).resolve().as_uri() + '?mode=ro',))
        if database_schema(db, 'incoming') != schema:
            raise ValueError('Incompatible hashserv database schema')
        with db:
            for table in ('unihashes_v3', 'outhashes_v2'):
                columns = ','.join(f'"{column}"' for column in COLUMNS[table][1:])
                db.execute(f'INSERT OR IGNORE INTO main.{table} ({columns}) '
                           f'SELECT {columns} FROM incoming.{table} ORDER BY id')
            # Keep the base's users/config. Incoming credentials and GC state
            # are not build results and must not replace server policy.
            db.execute("UPDATE unihashes_v3 SET gc_mark=COALESCE("
                       "(SELECT value FROM config WHERE name='gc-mark'), '')")
    validate_database(destination)
