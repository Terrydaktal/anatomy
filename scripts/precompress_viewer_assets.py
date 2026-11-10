#!/usr/bin/env python3
"""Generate lossless Zstd, Brotli and gzip HTTP variants without changing source assets."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import json
from pathlib import Path

import brotli
import zstandard

from asset_pipeline import COMPRESSION_MANIFEST, ROOT, digest, public_files

SETTINGS = {'zstd_level': 19, 'zstd_window_log': 23, 'brotli_quality': 11, 'gzip_level': 6}


def compress(path, old, force):
    initial_stat = path.stat()
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    suffixes = {'zstd': '.zst', 'br': '.br', 'gzip': '.gz'}
    outputs = {codec: path.with_name(path.name + suffix) for codec, suffix in suffixes.items()}
    reusable = not force and old.get('sha256') == sha and all(
        target.is_file() and target.stat().st_size == old.get('encodings', {}).get(codec, {}).get('size')
        and digest(target) == old.get('encodings', {}).get(codec, {}).get('sha256')
        for codec, target in outputs.items())
    if not reusable:
        params = zstandard.ZstdCompressionParameters.from_level(19, window_log=23)
        encoded = {'zstd': zstandard.ZstdCompressor(compression_params=params).compress(data),
                   'br': brotli.compress(data, quality=11), 'gzip': gzip.compress(data, compresslevel=6, mtime=0)}
        for codec, payload in encoded.items():
            # Check the serialized representation before publishing it.
            restored = {'zstd': lambda: zstandard.ZstdDecompressor().decompress(payload),
                        'br': lambda: brotli.decompress(payload), 'gzip': lambda: gzip.decompress(payload)}[codec]()
            if restored != data:
                raise ValueError(f'Compression changed source bytes: {path}')
            temporary = outputs[codec].with_suffix(outputs[codec].suffix + '.partial')
            temporary.write_bytes(payload)
            temporary.replace(outputs[codec])
    stat = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (initial_stat.st_size, initial_stat.st_mtime_ns):
        raise ValueError(f'Source changed during compression: {path}. Run compression again.')
    return {'sha256': sha, 'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
            'encodings': {codec: {'path': str(target.relative_to(ROOT)), 'size': target.stat().st_size,
                                  'sha256': digest(target)} for codec, target in outputs.items()}}


def prepare(jobs=4, force=False):
    old = json.loads(COMPRESSION_MANIFEST.read_text()) if COMPRESSION_MANIFEST.is_file() else {}
    paths = public_files()
    entries = {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        work = {pool.submit(compress, path, old.get('assets', {}).get(str(path.relative_to(ROOT)), {}),
                            force or old.get('settings') != SETTINGS): path for path in paths}
        for future in as_completed(work):
            path = work[future]
            entries[str(path.relative_to(ROOT))] = future.result()
            print(f'Prepared {len(entries)}/{len(paths)}: {path.relative_to(ROOT)}', flush=True)
    version = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
    payload = {'schema_version': 1, 'asset_version': version, 'settings': SETTINGS, 'assets': dict(sorted(entries.items()))}
    temporary = COMPRESSION_MANIFEST.with_suffix('.json.partial')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.replace(COMPRESSION_MANIFEST)
    print(f'Prepared {len(entries)} assets; version {version}', flush=True)
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--force', action='store_true')
    arguments = parser.parse_args()
    prepare(max(1, arguments.jobs), arguments.force)
