#!/usr/bin/env python3
"""Export the current exact highlight buffers for loading without runtime fitting."""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright

from asset_pipeline import HIGHLIGHT_MANIFEST, ROOT, digest, highlight_identities
from serve_viewer import ViewerHandler
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread


@contextmanager
def export_server():
    class QuietHandler(ViewerHandler):
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


EXPORT_HOOK = '''window.exportPreparedHighlights = async () => {
  const stripArrays = records => records.map(({positions, faces, ...metadata}) => metadata);
  const output = {schema_version: 1, dataset,
    model_sha256: manifest.sha256, surface_sha256: manifest.surface?.sha256,
    domains: {sourceDomains: stripArrays(details.regions.sourceDomains),
      apertureDomains: stripArrays(details.regions.apertureDomains),
      registeredDomains: stripArrays(details.regions.registeredDomains),
      registeredRejections: details.regions.registeredRejections}, regions: []};
  let offset = 0;
  const arrayRecord = async array => {
    const bytes = new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
    const record = {byte_offset: offset, count: array.length, type: array.constructor.name};
    for (let start = 0; start < bytes.length; start += 262144) {
      const chunk = bytes.subarray(start, start + 262144), strings = [];
      for (let i = 0; i < chunk.length; i += 16384) strings.push(String.fromCharCode(...chunk.subarray(i, i + 16384)));
      await window.writeHighlightBytes(btoa(strings.join('')));
    }
    offset += bytes.byteLength;
    return record;
  };
  for (const entry of details.landmarks.values()) {
    const region = details.hasSurface(entry) ? await details.regions.prepare(entry) : details.regions.empty(entry);
    const record = {id: region.id, area: region.area, methods: region.methods, colour: region.colour, parts: []};
    for (const part of region.parts) {
      const geometry = part.geometry;
      const exported = {ownerId: part.ownerId, method: part.method, scope: part.scope, fit: part.fit, source: part.source,
        area: part.area, anchor: part.anchor.toArray(), anchorFace: part.anchorFace,
        bounds: [geometry.boundingBox.min.toArray(), geometry.boundingBox.max.toArray()],
        sphere: [geometry.boundingSphere.center.toArray(), geometry.boundingSphere.radius]};
      for (const [name, array] of Object.entries({positions: geometry.attributes.position.array,
        normals: geometry.attributes.normal.array, sourceFaces: part.sourceFaces,
        faceIds: part.faceIndex.ids, faceOffsets: part.faceIndex.offsets, faceTriangles: part.faceIndex.triangles})) {
        exported[name] = await arrayRecord(array);
      }
      record.parts.push(exported);
    }
    output.regions.push(record);
    if (output.regions.length % 100 === 0) await window.highlightExportProgress(output.regions.length);
  }
  output.binary_bytes = offset;
  return output;
};
'''


def current(inputs):
    try:
        payload = json.loads(HIGHLIGHT_MANIFEST.read_text())
        binary = (ROOT / payload['binary']['url']).resolve()
        return (payload.get('schema_version') == 1 and payload.get('inputs') == inputs
                and binary.is_relative_to(HIGHLIGHT_MANIFEST.parent) and binary.is_file()
                and binary.stat().st_size == payload['binary']['byte_length']
                and digest(binary) == payload['binary']['sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def export(force=False, check=False):
    inputs = highlight_identities()
    if not force and current(inputs):
        print('Precomputed highlights are current.', flush=True)
        return
    if check:
        raise SystemExit('Precomputed highlights are missing or out of date. Run this script without --check.')
    HIGHLIGHT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    binary_path = HIGHLIGHT_MANIFEST.parent / 'highlights.bin'
    temporary = binary_path.with_suffix('.bin.partial')
    errors = []
    with temporary.open('wb') as stream, export_server() as url, sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which('google-chrome-stable') or shutil.which('chromium'), headless=True,
            env={key: value for key, value in os.environ.items() if key not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
            args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
        try:
            page = browser.new_page(viewport={'width': 1200, 'height': 900})
            page.add_init_script('globalThis.__SKULL_BUILD_HIGHLIGHTS__ = true;')
            page.on('pageerror', lambda error: errors.append(str(error)))
            app = (ROOT / 'src/app.js').read_text()
            anchor = '  window.skullAtlas = Object.freeze({'
            assert app.count(anchor) == 1
            page.route('**/src/app.js*', lambda route: route.fulfill(status=200, content_type='text/javascript', body=app.replace(anchor, EXPORT_HOOK + '\n' + anchor)))
            page.expose_function('writeHighlightBytes', lambda text: stream.write(base64.b64decode(text)))
            page.expose_function('highlightExportProgress', lambda count: print(f'Prepared {count} landmarks', flush=True))
            page.goto(url + '/', wait_until='networkidle')
            page.wait_for_function("document.documentElement.dataset.ready === 'true'", timeout=60000)
            payload = page.evaluate('window.exportPreparedHighlights()')
            if errors:
                raise ValueError(errors)
        finally:
            browser.close()
    if temporary.stat().st_size != payload.pop('binary_bytes') or highlight_identities() != inputs:
        raise ValueError('Highlight data or build inputs changed during export')
    payload['inputs'] = inputs
    payload['binary'] = {'url': str(binary_path.relative_to(ROOT)), 'sha256': digest(temporary), 'byte_length': temporary.stat().st_size}
    temporary.replace(binary_path)
    metadata = HIGHLIGHT_MANIFEST.with_suffix('.json.partial')
    metadata.write_text(json.dumps(payload, separators=(',', ':')) + '\n')
    metadata.replace(HIGHLIGHT_MANIFEST)
    painted = sum(bool(region['parts']) for region in payload['regions'])
    print(f'Published {len(payload["regions"])} entries, {painted} surface highlights; {binary_path.stat().st_size / 1e6:.1f} MB', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    export(args.force, args.check)
