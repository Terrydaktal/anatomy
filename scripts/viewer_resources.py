#!/usr/bin/env python3
"""Package native viewer binaries once and restore ignored copies when serving."""

import argparse
import hashlib
import json
from pathlib import Path

import zstandard

from asset_pipeline import ROOT, digest

MANIFEST = 'assets/viewer-resources.json'
RESOURCE_PATHS = (
    'assets/skull-ct/skull.glb', 'assets/skull-ct/surface.glb',
    'assets/skull-associated/bones.glb', 'assets/skull-cavities/cavities.glb',
    'assets/skull/skull.glb', 'assets/bone-microct/fragment.glb',
    'assets/landmark-highlights/highlights.bin',
)


def write_json(path, value):
    text = json.dumps(value, indent=2) + '\n'
    if path.is_file() and path.read_text() == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(text)
    temporary.replace(path)


def resources(root=ROOT):
    path = root / MANIFEST
    if not path.is_file():
        return []
    payload = json.loads(path.read_text())
    if payload.get('schema_version') != 1:
        raise ValueError('Unsupported viewer resource package version.')
    records = payload['resources']
    if len(records) != len(RESOURCE_PATHS) or {record['path'] for record in records} != set(RESOURCE_PATHS):
        raise ValueError('Viewer resource package has an incomplete binary inventory.')
    for record in records:
        if record['archive']['path'] != record['path'] + '.zst':
            raise ValueError(f'Invalid resource archive: {record["path"]}')
        for name in (record['path'], record['archive']['path']):
            if not (root / name).resolve().is_relative_to(root.resolve()):
                raise ValueError('Resource path escapes the project.')
        for entry in (record, record['archive']):
            if not isinstance(entry['byte_length'], int) or entry['byte_length'] < 0:
                raise ValueError('Invalid resource byte length.')
            if len(entry['sha256']) != 64 or any(char not in '0123456789abcdef' for char in entry['sha256']):
                raise ValueError('Invalid resource SHA-256.')
    return records


def package(root=ROOT):
    """Version one lossless representation; native files remain generated caches."""
    try:
        previous = {record['path']: record for record in resources(root)}
    except (OSError, ValueError, KeyError, TypeError):
        previous = {}
    try:
        encodings = json.loads((root / 'viewer-assets.compression.json').read_text())['assets']
    except (OSError, ValueError, KeyError):
        encodings = {}
    records = []
    for name in RESOURCE_PATHS:
        source = root / name
        source_hash = digest(source)
        archive = root / (name + '.zst')
        known = previous.get(name, {})
        codec = encodings.get(name, {})
        expected = (known.get('archive') if known.get('sha256') == source_hash else None)
        if not expected and codec.get('sha256') == source_hash:
            expected = codec.get('encodings', {}).get('zstd')
        reusable = (expected and archive.is_file() and digest(archive) == expected.get('sha256'))
        if not reusable:
            temporary = archive.with_suffix('.zst.partial')
            parameters = zstandard.ZstdCompressionParameters.from_level(19, window_log=23)
            temporary.write_bytes(zstandard.ZstdCompressor(compression_params=parameters).compress(source.read_bytes()))
            with temporary.open('rb') as incoming, zstandard.ZstdDecompressor().stream_reader(incoming) as decoded:
                if hashlib.file_digest(decoded, 'sha256').hexdigest() != source_hash:
                    temporary.unlink()
                    raise ValueError(f'Packaging changed source bytes: {name}')
            temporary.replace(archive)
        records.append({'path': name, 'sha256': source_hash, 'byte_length': source.stat().st_size,
                        'archive': {'path': name + '.zst', 'sha256': digest(archive), 'byte_length': archive.stat().st_size}})
    write_json(root / MANIFEST, {'schema_version': 1, 'resources': records})
    return records


def prepare(root=ROOT):
    """Restore/check packaged resources without changing any versioned file."""
    state_path = root / '.cache/viewer-resources.json'
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = {}
    state, restored = {}, []
    for record in resources(root):
        target = root / record['path']
        actual = digest(target) if target.is_file() else None
        if actual != record['sha256']:
            if actual and actual != previous.get(record['path']):
                raise ValueError(f'Local changes in {record["path"]} were preserved. Repackage them or move the file before starting.')
            archive = root / record['archive']['path']
            if archive.stat().st_size != record['archive']['byte_length'] or digest(archive) != record['archive']['sha256']:
                raise ValueError(f'Damaged viewer archive: {record["archive"]["path"]}')
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + '.partial')
            checksum, length = hashlib.sha256(), 0
            try:
                with archive.open('rb') as incoming, zstandard.ZstdDecompressor().stream_reader(incoming) as decoded, temporary.open('wb') as output:
                    while chunk := decoded.read(1024 * 1024):
                        length += len(chunk)
                        if length > record['byte_length']:
                            raise ValueError(f'Expanded resource exceeds expected size: {record["path"]}')
                        checksum.update(chunk)
                        output.write(chunk)
                if length != record['byte_length'] or checksum.hexdigest() != record['sha256']:
                    raise ValueError(f'Expanded viewer resource checksum differs: {record["path"]}')
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            restored.append(record['path'])
        state[record['path']] = record['sha256']
    if state:
        write_json(state_path, state)
    return restored


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', action='store_true', help='Rebuild the versioned lossless archives from current native assets.')
    args = parser.parse_args()
    if args.package:
        records = package()
        print(f'Packaged {len(records)} resources ({sum(record["archive"]["byte_length"] for record in records) / 1e6:.1f} MB).')
    else:
        restored = prepare()
        print(f'Verified viewer resources; restored {len(restored)} native files.')
