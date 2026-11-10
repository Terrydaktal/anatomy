#!/usr/bin/env python3
"""Serve the local skull viewer and its public assets."""

from __future__ import annotations

import argparse
from email.utils import formatdate
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, unquote, urlsplit

from asset_pipeline import HIGHLIGHT_BUILD_ONLY, HIGHLIGHT_INPUTS, digest
from viewer_resources import prepare, resources


ROOT = Path(__file__).resolve().parents[1]
_STATE = {}
_HASHES = {}
_LOCK = Lock()
ENCODINGS = ('zstd', 'br', 'gzip')


def file_hash(path):
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _HASHES:
        _HASHES[key] = digest(path)
    return _HASHES[key]


def delivery_state(root):
    paths = [root / name for name in ('index.html', 'styles.css')]
    for name in ('assets', 'src', 'vendor'):
        paths.extend(path for path in (root / name).rglob('*') if path.is_file()
                     and path.suffix not in {'.zst', '.br', '.gz', '.partial'})
    paths.extend(root / name for name in HIGHLIGHT_INPUTS if (root / name).is_file())
    manifest_path = root / 'viewer-assets.compression.json'
    paths.append(manifest_path)
    fingerprint = tuple((str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns)
                        for path in sorted(set(paths)) if path.is_file())
    with _LOCK:
        cached = _STATE.get(root)
        if cached and cached['fingerprint'] == fingerprint:
            return cached
        try:
            manifest = json.loads(manifest_path.read_text())
        except (FileNotFoundError, ValueError):
            manifest = {}
        version = hashlib.sha256(repr(fingerprint).encode()).hexdigest()[:24]
        inputs = {name: file_hash(root / name) for name in HIGHLIGHT_INPUTS if (root / name).is_file()}
        try:
            saved = json.loads((root / 'assets/landmark-highlights/manifest.json').read_text())
        except (OSError, ValueError):
            saved = {}
        for name in HIGHLIGHT_BUILD_ONLY:
            if name not in inputs and name in saved.get('inputs', {}):
                inputs[name] = saved['inputs'][name]
        assets = dict(manifest.get('assets', {}))
        for record in resources(root):
            source = root / record['path']
            if not source.is_file() or file_hash(source) != record['sha256']:
                continue
            stat = source.stat()
            old = assets.get(record['path'], {})
            if old.get('sha256') != record['sha256'] or old.get('source_mtime_ns') != stat.st_mtime_ns:
                archive = record['archive']
                assets[record['path']] = {'sha256': record['sha256'], 'source_size': stat.st_size,
                    'source_mtime_ns': stat.st_mtime_ns,
                    'encodings': {'zstd': {'path': archive['path'], 'sha256': archive['sha256'], 'size': archive['byte_length']}}}
        state = {'fingerprint': fingerprint, 'version': version, 'inputs': inputs, 'assets': assets}
        _STATE[root] = state
        return state


def choose_encoding(header, available):
    qualities = {}
    for item in (header or '').split(','):
        fields = item.strip().split(';')
        if not fields[0]:
            continue
        quality = 1.0
        for field in fields[1:]:
            key, separator, value = field.strip().partition('=')
            if key == 'q' and separator:
                try:
                    quality = min(1.0, max(0.0, float(value)))
                except ValueError:
                    quality = 0.0
        qualities[fields[0].lower()] = quality
    ranked = [(qualities.get(codec, qualities.get('*', 0)), -rank, codec)
              for rank, codec in enumerate(ENCODINGS) if codec in available]
    return max((row for row in ranked if row[0] > 0), default=(0, 0, None))[2]


class ViewerHandler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def is_public_path(self) -> bool:
        root = Path(self.directory).resolve()
        path = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return False
        relative = target.relative_to(root)
        return str(relative) in {"index.html", "styles.css"} or relative.parts[0] in {"src", "assets", "vendor"}

    def send_head(self):
        self._cache_control, self._vary = 'no-cache', False
        if not self.is_public_path():
            self.send_error(404)
            return None
        root = Path(self.directory).resolve()
        relative = unquote(urlsplit(self.path).path).lstrip('/') or 'index.html'
        source = (root / relative).resolve()
        relative = str(source.relative_to(root))
        state = delivery_state(root)
        if relative == 'index.html':
            data = source.read_text()
            globals_script = '<script>globalThis.__SKULL_ASSET_VERSION__=' + json.dumps(state['version']) + ';globalThis.__SKULL_ASSET_HASHES__=' + json.dumps(state['inputs']) + ';</script>'
            data = data.replace('</head>', globals_script + '</head>')
            data = data.replace('href="styles.css"', f'href="styles.css?v={state["version"]}"')
            data = data.replace('src="src/app.js"', f'src="src/app.js?v={state["version"]}"')
            body = data.encode()
            return self._respond(source, BytesIO(body), len(body), hashlib.sha256(body).hexdigest(), None)
        entry = state['assets'].get(relative)
        stat = source.stat()
        fresh = entry and entry.get('source_size') == stat.st_size and entry.get('source_mtime_ns') == stat.st_mtime_ns
        requested_version = parse_qs(urlsplit(self.path).query).get('v', [''])[0]
        # The version includes live source stats, so editing files also changes
        # the URLs before the next compression run.
        if requested_version == state['version']:
            self._cache_control = 'public, max-age=31536000, immutable'
        selected = None
        variants = {}
        if fresh:
            self._vary = True
            for codec, metadata in entry.get('encodings', {}).items():
                target = (root / metadata['path']).resolve()
                if target.is_relative_to(root) and target.is_file() and target.stat().st_size == metadata.get('size'):
                    if file_hash(target) == metadata.get('sha256'):
                        variants[codec] = (target, metadata['size'])
            selected = choose_encoding(self.headers.get('Accept-Encoding'), variants)
        if selected:
            target, length = variants[selected]
            return self._respond(source, target.open('rb'), length, entry['sha256'], selected)
        return self._respond(source, source.open('rb'), stat.st_size, file_hash(source), None)

    def _respond(self, source, stream, length, sha, encoding):
        etag = f'"{sha[:24]}-{encoding or "identity"}"'
        if any(value.strip().removeprefix('W/') in {'*', etag} for value in self.headers.get('If-None-Match', '').split(',')):
            stream.close()
            self.send_response(304)
            self.send_header('ETag', etag)
            if encoding:
                self.send_header('Content-Encoding', encoding)
            self.end_headers()
            return None
        self.send_response(200)
        self.send_header('Content-Type', self.guess_type(str(source)))
        self.send_header('Content-Length', str(length))
        self.send_header('Last-Modified', formatdate(source.stat().st_mtime, usegmt=True))
        self.send_header('ETag', etag)
        if encoding:
            self.send_header('Content-Encoding', encoding)
        self.end_headers()
        return stream

    def end_headers(self) -> None:
        self.send_header("Cache-Control", getattr(self, '_cache_control', 'no-cache'))
        if getattr(self, '_vary', False):
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    restored = prepare(ROOT)
    if restored:
        print(f'Restored {len(restored)} packaged viewer resources.', flush=True)
    server = ThreadingHTTPServer((args.host, args.port), partial(ViewerHandler, directory=str(ROOT)))
    print(f"Skull viewer: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
