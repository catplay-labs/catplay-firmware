"""S3 rendezvous and consistent SQLite snapshots for one CI hashserv session."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class Store:
    def __init__(self):
        self.storage_class = os.environ.get('S3_UPLOAD_STORAGE_CLASS', '').strip()
        self.bucket = os.environ['S3_UPLOAD_BUCKET']
        self.root = os.environ.get('S3_UPLOAD_PREFIX', '').strip('/')
        repository = os.environ['GITHUB_REPOSITORY']
        self.session = self.key('ci-hashserv', repository, os.environ['GITHUB_RUN_ID'],
                                os.environ['GITHUB_RUN_ATTEMPT'])
        self.database = self.key('hashserv', repository)
        self.client = boto3.client(
            's3', endpoint_url=os.environ['S3_UPLOAD_ENDPOINT'],
            region_name=os.environ['S3_UPLOAD_REGION'],
            aws_access_key_id=os.environ['S3_UPLOAD_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['S3_UPLOAD_SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                          retries={'mode': 'standard', 'total_max_attempts': 4},
                          connect_timeout=10, read_timeout=30))

    def key(self, *parts):
        return '/'.join(part.strip('/') for part in (self.root, *parts) if part.strip('/'))

    def get(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response['Error']['Code'] in {'NoSuchKey', '404'}:
                return None
            raise
        with response['Body'] as body:
            return body.read()

    def put(self, key, data):
        options = {'StorageClass': self.storage_class} if self.storage_class else {}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **options)

    def read(self, name):
        data = self.get(f'{self.session}/{name}.json')
        return json.loads(data) if data is not None else None

    def write(self, name, data):
        self.put(f'{self.session}/{name}.json', json.dumps(data).encode())

    def delete_session(self):
        # Only this attempt, never persistent snapshots or another run.
        for page in self.client.get_paginator('list_objects_v2').paginate(
                Bucket=self.bucket, Prefix=self.session + '/'):
            objects = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
            if objects:
                result = self.client.delete_objects(Bucket=self.bucket,
                                                     Delete={'Objects': objects})
                if result.get('Errors'):
                    raise RuntimeError('S3 rendezvous cleanup failed')


def validate_database(filename):
    with sqlite3.connect(f'{Path(filename).resolve().as_uri()}?mode=ro', uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Invalid hashserv SQLite database')


def backup_database(source, destination):
    with sqlite3.connect(f'{Path(source).resolve().as_uri()}?mode=ro', uri=True,
                         timeout=30) as original, sqlite3.connect(destination) as backup:
        original.backup(backup)
    validate_database(destination)


def restore(store, destination):
    data = store.get(store.database + '/current.json')
    if data is None:
        return False
    pointer = json.loads(data)
    if not pointer['key'].startswith(store.database + '/snapshots/'):
        raise RuntimeError('Snapshot pointer is outside hashserv snapshots')
    if not pointer['key'].endswith('.sqlite3.gz'):
        raise RuntimeError('Expected a gzip-compressed .sqlite3.gz snapshot')
    snapshot = store.get(pointer['key'])
    if snapshot is None or hashlib.sha256(snapshot).hexdigest() != pointer['sha256']:
        raise RuntimeError('Missing or corrupt hashserv snapshot')
    Path(destination).write_bytes(gzip.decompress(snapshot))
    validate_database(destination)
    return True


def snapshot(store, source, directory, *, publish=True):
    target = Path(directory) / f'backup-{uuid.uuid4().hex}.sqlite3'
    try:
        backup_database(source, target)
        # gzip's standard compression level (6), with a reproducible header.
        payload = gzip.compress(target.read_bytes(), compresslevel=6, mtime=0)
        key = f'{store.database}/snapshots/{time.time_ns()}-{uuid.uuid4().hex}.sqlite3.gz'
        store.put(key, payload)
        pointer = {'key': key, 'sha256': hashlib.sha256(payload).hexdigest()}
        if publish:
            store.put(store.database + '/current.json', json.dumps(pointer).encode())
        return pointer
    finally:
        target.unlink(missing_ok=True)
