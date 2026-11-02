#!/usr/bin/env python3
"""Publish and load the exact Docker image archive for this workflow attempt."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from boto3.s3.transfer import TransferConfig

from ci_common import Store

TRANSFER = TransferConfig(multipart_threshold=64 * 1024**2,
                          multipart_chunksize=64 * 1024**2, max_concurrency=8)


def scope(store):
    return store.key('ci-images', os.environ['GITHUB_REPOSITORY'],
                     os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']) + '/'


def validate(image_id, checksum):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id or ''):
        raise ValueError('Expected a full Docker image ID')
    if not re.fullmatch(r'[0-9a-f]{64}', checksum or ''):
        raise ValueError('Expected an archive SHA-256')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def object_key(store, image_id, checksum):
    validate(image_id, checksum)
    return scope(store) + image_id.removeprefix('sha256:') + '/' + checksum + '.tar.gz'


def publish(store, archive, image_id, manifest_digest):
    checksum = sha256(archive)
    validate(manifest_digest, checksum)
    key = object_key(store, image_id, checksum)
    options = {'StorageClass': store.storage_class} if store.storage_class else {}
    print(f'ci-image: uploading {archive.stat().st_size} bytes for {image_id}', flush=True)
    store.client.upload_file(str(archive), store.bucket, key,
                             ExtraArgs=options, Config=TRANSFER)
    # Outputs identify immutable content; no credentials, bucket or endpoint.
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'image-id={image_id}\nmanifest-digest={manifest_digest}\narchive-sha256={checksum}\n')
    print(f'ci-image: published archive sha256:{checksum}', flush=True)


def load(store, image_id, checksum, tag, manifest_digest):
    validate(manifest_digest, checksum)
    key = object_key(store, image_id, checksum)
    print(f'ci-image: downloading archive for {image_id}', flush=True)
    with tempfile.TemporaryDirectory(prefix='ci-image-', dir=os.environ.get('RUNNER_TEMP')) as directory:
        archive = Path(directory) / 'image.tar.gz'
        store.client.download_file(store.bucket, key, str(archive), Config=TRANSFER)
        if sha256(archive) != checksum:
            raise ValueError('S3 image archive checksum mismatch; refusing docker load')
        subprocess.run(['docker', 'load', '--input', str(archive)], check=True)
        actual = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                                check=True, capture_output=True, text=True).stdout.strip()
        # Image identity differs between Engine image-store implementations:
        # classic stores report config IDs; manifest-backed stores can report
        # the manifest digest. Both come from this build's trusted metadata.
        if actual not in (image_id, manifest_digest):
            raise ValueError(f'Loaded Docker image ID mismatch: expected config {image_id} '
                             f'or manifest {manifest_digest}, got {actual}')
    print(f'ci-image: verified {tag} = {actual}', flush=True)


def cleanup(store):
    # Scope is this run AND attempt; never touch BuildKit cache or another run.
    keys = [item['Key'] for page in store.client.get_paginator('list_objects_v2').paginate(
        Bucket=store.bucket, Prefix=scope(store)) for item in page.get('Contents', [])]
    for key in keys:
        store.client.delete_object(Bucket=store.bucket, Key=key)
    print(f'ci-image: deleted {len(keys)} temporary object(s)', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    upload = commands.add_parser('publish')
    upload.add_argument('--archive', required=True, type=Path)
    upload.add_argument('--image-id', required=True)
    upload.add_argument('--manifest-digest', required=True)
    download = commands.add_parser('load')
    download.add_argument('--image-id', required=True)
    download.add_argument('--sha256', required=True)
    download.add_argument('--manifest-digest', required=True)
    download.add_argument('--tag', default='catplay-bitbake:ci')
    commands.add_parser('cleanup')
    args = parser.parse_args()
    store = Store()
    if args.command == 'publish':
        publish(store, args.archive, args.image_id, args.manifest_digest)
    elif args.command == 'load':
        load(store, args.image_id, args.sha256, args.tag, args.manifest_digest)
    else:
        cleanup(store)


if __name__ == '__main__':
    main()
