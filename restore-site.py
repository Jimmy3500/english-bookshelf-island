#!/usr/bin/env python3
"""Restore the public static site from pinned sources using only Python's standard library."""
import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
IMAGE_COMMIT = 'a56e6dbe7aeeb8ea3e53d9941664416e2e7356ad'
IMAGE_URL = 'https://codeload.github.com/global-asp/asp-imagebank/zip/' + IMAGE_COMMIT
AUDIO_ROOT = 'https://storybookscanada.ca/audio/en/'
TIMEOUT = 45
ATTEMPTS = 3


def safe_relative(value):
    relative = PurePosixPath(value)
    if not relative.parts or relative.is_absolute() or '..' in relative.parts or '\\' in value or ':' in relative.parts[0]:
        raise ValueError('Unsafe site path: ' + value)
    if relative.as_posix() != value:
        raise ValueError('Noncanonical site path: ' + value)
    return relative


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest():
    manifest = json.loads((HERE / 'site-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or manifest['imagebank']['commit'] != IMAGE_COMMIT:
        raise ValueError('Unsupported manifest or image source revision')
    if manifest['frontend']['archive'] != 'site-frontend.zip':
        raise ValueError('Unexpected frontend archive')
    inventory = {}
    for record in manifest['frontend']['files'] + manifest['media']:
        safe_relative(record['path'])
        if record['path'] in inventory:
            raise ValueError('Duplicate site path: ' + record['path'])
        if not isinstance(record['bytes'], int) or record['bytes'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', record['sha256']):
            raise ValueError('Invalid file inventory record')
        inventory[record['path']] = record
    for record in manifest['media']:
        if not re.fullmatch(r'books/\d{4}/\d{2}\.(jpg|mp3)', record['path']):
            raise ValueError('Unexpected media path: ' + record['path'])
    if any(record['path'].startswith('books/') for record in manifest['frontend']['files']):
        raise ValueError('Media must be listed separately from the frontend')
    if len(inventory) != manifest['site_file_count'] or sum(record['bytes'] for record in inventory.values()) != manifest['total_uncompressed_bytes']:
        raise ValueError('Manifest inventory totals do not agree')
    if 'index.html' not in inventory:
        raise ValueError('The site must contain index.html at its root')
    return manifest, inventory


def write_verified(reader, target, expected):
    if target.exists() or target.is_symlink():
        raise ValueError('Refusing to overwrite site file: ' + expected['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.part')
    digest = hashlib.sha256()
    size = 0
    try:
        with partial.open('wb') as writer:
            for chunk in iter(lambda: reader.read(128 * 1024), b''):
                size += len(chunk)
                if size > expected['bytes']:
                    raise ValueError('Unexpected file size: ' + expected['path'])
                writer.write(chunk)
                digest.update(chunk)
        if size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
            raise ValueError('Size/SHA256 mismatch: ' + expected['path'])
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def restore_frontend(manifest, output):
    frontend = manifest['frontend']
    archive = HERE / frontend['archive']
    if archive.stat().st_size != frontend['bytes'] or digest_file(archive) != frontend['sha256']:
        raise ValueError('Frontend archive size/SHA256 mismatch')
    expected = {record['path']: record for record in frontend['files']}
    seen = set()
    with zipfile.ZipFile(archive) as bundle:
        bad = bundle.testzip()
        if bad:
            raise ValueError('Frontend archive CRC mismatch: ' + bad)
        for member in bundle.infolist():
            relative = safe_relative(member.filename.rstrip('/') if member.is_dir() else member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Symlinks are not allowed in the frontend archive')
            if member.is_dir():
                continue
            name = relative.as_posix()
            if name not in expected or name in seen or member.file_size != expected[name]['bytes']:
                raise ValueError('Unexpected or duplicate frontend member: ' + name)
            seen.add(name)
            with bundle.open(member) as reader:
                write_verified(reader, output.joinpath(*relative.parts), expected[name])
    if seen != set(expected):
        raise ValueError('Frontend archive inventory is incomplete')
    print(f'Frontend: {len(seen)} files verified.', flush=True)


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'story-island-pages/1.0'}), timeout=TIMEOUT)


def fetch_image_archive(target):
    for attempt in range(ATTEMPTS):
        try:
            with request(IMAGE_URL) as reader, target.open('wb') as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            return
        except Exception:
            if attempt + 1 == ATTEMPTS:
                raise
            time.sleep(attempt + 1)


def restore_images(images, output):
    expected = {record['path']: record for record in images}
    with tempfile.TemporaryDirectory(prefix='story-island-images-') as directory:
        archive = Path(directory) / 'imagebank.zip'
        print('Downloading the pinned imagebank archive once...', flush=True)
        fetch_image_archive(archive)
        members = {}
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                parts = PurePosixPath(member.filename).parts
                if len(parts) != 4 or parts[1] != 'medium':
                    continue
                name = 'books/' + '/'.join(parts[2:])
                if name not in expected:
                    continue
                safe_relative(member.filename)
                if stat.S_ISLNK(member.external_attr >> 16) or member.is_dir() or name in members:
                    raise ValueError('Unsafe or duplicate source image: ' + name)
                if member.file_size != expected[name]['bytes']:
                    raise ValueError('Source image size mismatch: ' + name)
                members[name] = member
            if set(members) != set(expected):
                raise ValueError('Pinned imagebank does not contain every required image')
            for count, (name, record) in enumerate(expected.items(), 1):
                with bundle.open(members[name]) as reader:
                    write_verified(reader, output / name, record)
                if count % 500 == 0 or count == len(images):
                    print(f'Images: {count}/{len(images)} verified.', flush=True)


def restore_audio(audio, output):
    def fetch(record):
        _, book, filename = record['path'].split('/')
        url = AUDIO_ROOT + book + '/mp3/' + filename
        for attempt in range(ATTEMPTS):
            try:
                with request(url) as reader:
                    write_verified(reader, output / record['path'], record)
                return record['path']
            except Exception:
                if attempt + 1 == ATTEMPTS:
                    raise
                time.sleep(attempt + 1)
    with ThreadPoolExecutor(max_workers=12) as workers:
        pending = {workers.submit(fetch, record): record['path'] for record in audio}
        for count, task in enumerate(as_completed(pending), 1):
            try:
                task.result()
            except Exception as error:
                for other in pending:
                    other.cancel()
                raise RuntimeError('Could not restore audio ' + pending[task]) from error
            if count % 25 == 0 or count == len(audio):
                print(f'Audio: {count}/{len(audio)} verified.', flush=True)


def restore_local(records, source, output, label):
    source = source.resolve()
    if not source.is_dir():
        raise ValueError('Local media directory does not exist')
    for count, record in enumerate(records, 1):
        relative = PurePosixPath(record['path'])
        existing = source.joinpath(*relative.parts[1:]).resolve()
        if not existing.is_relative_to(source) or not existing.is_file():
            raise ValueError('Missing or unsafe local media: ' + record['path'])
        with existing.open('rb') as reader:
            write_verified(reader, output / record['path'], record)
        if count % 500 == 0 or count == len(records):
            print(f'{label}: {count}/{len(records)} verified from local files.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='_site', type=Path)
    parser.add_argument('--media-from', type=Path, help='Read media from an existing local site/books directory instead of the network')
    args = parser.parse_args()
    manifest, inventory = load_manifest()
    if args.out.is_symlink():
        raise ValueError('Output must not be a symlink')
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Output directory must be empty')
    restore_frontend(manifest, output)
    images = [record for record in manifest['media'] if record['path'].endswith('.jpg')]
    audio = [record for record in manifest['media'] if record['path'].endswith('.mp3')]
    if args.media_from:
        restore_local(images, args.media_from, output, 'Images')
        restore_local(audio, args.media_from, output, 'Audio')
    else:
        restore_images(images, output)
        restore_audio(audio, output)
    actual = {file.relative_to(output).as_posix(): file for file in output.rglob('*') if file.is_file()}
    if set(actual) != set(inventory):
        raise ValueError('Restored site inventory mismatch')
    total = 0
    for name, file in actual.items():
        record = inventory[name]
        if file.stat().st_size != record['bytes'] or digest_file(file) != record['sha256']:
            raise ValueError('Final site size/SHA256 mismatch: ' + name)
        total += file.stat().st_size
    if len(actual) != manifest['site_file_count'] or total != manifest['total_uncompressed_bytes']:
        raise ValueError('Final site totals mismatch')
    print(f'Complete: {len(actual)} files, {total} bytes; every SHA256 verified.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Site restoration failed: {error}', file=sys.stderr)
        raise SystemExit(1)
