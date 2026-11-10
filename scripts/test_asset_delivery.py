#!/usr/bin/env python3
"""Exercise negotiated bytes, cache validators and edits against a real HTTP server."""

from contextlib import contextmanager
from functools import partial
import gzip
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import tempfile
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import brotli
import zstandard

from serve_viewer import ViewerHandler


def request(url, encoding='identity', method='GET', headers=None):
    query = Request(url, headers={'Accept-Encoding': encoding, **(headers or {})}, method=method)
    try:
        response = urlopen(query, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix='skull-http-test-') as folder:
        root = Path(folder)
        (root / 'assets').mkdir()
        (root / 'index.html').write_text('<html><head><link href="styles.css"></head><body><script src="src/app.js"></script></body></html>')
        (root / 'styles.css').write_text('body { color: black; }')
        (root / 'private.txt').write_text('private development data')
        source = root / 'assets/sample.glb'
        data = bytes(range(256)) * 1000
        source.write_bytes(data)
        variants = {'gzip': gzip.compress(data, mtime=0), 'br': brotli.compress(data), 'zstd': zstandard.ZstdCompressor().compress(data)}
        metadata = {}
        for codec, raw in variants.items():
            target = root / f'assets/sample.glb.{codec}'
            target.write_bytes(raw)
            metadata[codec] = {'path': str(target.relative_to(root)), 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        stat = source.stat()
        (root / 'viewer-assets.compression.json').write_text(json.dumps({'assets': {'assets/sample.glb': {
            'sha256': hashlib.sha256(data).hexdigest(), 'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns, 'encodings': metadata}}}))
        class Quiet(ViewerHandler):
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield root, f'http://127.0.0.1:{server.server_port}', data, variants
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


def main():
    with fixture() as (root, url, data, variants):
        status, headers, index = request(url + '/')
        assert status == 200 and headers['Cache-Control'] == 'no-cache'
        version = re.search(rb'__SKULL_ASSET_VERSION__="([0-9a-f]+)"', index)[1].decode()
        target = url + '/assets/sample.glb?v=' + version
        decoders = {'gzip': gzip.decompress, 'br': brotli.decompress, 'zstd': zstandard.ZstdDecompressor().decompress}
        for codec in variants:
            status, headers, body = request(target, codec)
            assert status == 200 and headers['Content-Encoding'] == codec
            assert body == variants[codec] and decoders[codec](body) == data
            assert headers['Cache-Control'] == 'public, max-age=31536000, immutable'
            assert headers['Vary'] == 'Accept-Encoding'
            etag = headers['ETag']
            status, headers, body = request(target, codec, headers={'If-None-Match': etag})
            assert status == 304 and body == b''
            status, headers, body = request(target, codec, method='HEAD')
            assert status == 200 and body == b'' and int(headers['Content-Length']) == len(variants[codec])
        assert request(target, 'gzip;q=0.2, br;q=0.7, zstd;q=0.9')[1]['Content-Encoding'] == 'zstd'
        assert request(target, 'gzip;q=1, br;q=0.2, zstd;q=0.1')[1]['Content-Encoding'] == 'gzip'
        assert request(target, 'gzip;q=0, br;q=0, zstd;q=0')[2] == data
        assert request(url + '/assets/sample.glb', 'gzip')[1]['Cache-Control'] == 'no-cache'
        assert request(url + '/assets/sample.glb?v=old', 'gzip')[1]['Cache-Control'] == 'no-cache'
        status, headers, body = request(target, 'identity')
        assert status == 200 and body == data and headers['Content-Encoding'] is None
        # A corrupt sidecar of the same length must never be delivered.
        corrupt = bytearray(variants['gzip']); corrupt[0] ^= 1
        (root / 'assets/sample.glb.gzip').write_bytes(corrupt)
        status, headers, body = request(target, 'gzip')
        assert status == 200 and body == data and headers['Content-Encoding'] is None
        # A source edit changes the URL immediately, before recompression.
        changed = b'updated native data' * 100
        (root / 'assets/sample.glb').write_bytes(changed)
        _, _, index = request(url + '/')
        next_version = re.search(rb'__SKULL_ASSET_VERSION__="([0-9a-f]+)"', index)[1].decode()
        assert version != next_version
        status, headers, body = request(url + '/assets/sample.glb?v=' + next_version, 'gzip, br, zstd')
        assert body == changed and headers['Content-Encoding'] is None
        assert request(target, 'gzip')[1]['Cache-Control'] == 'no-cache'
        for path in ('/private.txt', '/assets/../private.txt', '/viewer-assets.compression.json', '/assets/'):
            assert request(url + path)[0] == 404
    print('Passed: exact negotiated bytes, validators, HEAD, quality preferences, stale/corrupt variants, live version changes and private-path protection.')


if __name__ == '__main__':
    main()
