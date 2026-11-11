#!/usr/bin/env python3
"""Verify saved/live geometry parity, drawing, cold toggles and persistent HTTP caching."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import urlsplit

from PIL import ImageChops, ImageStat
from playwright.sync_api import sync_playwright

from test_viewer import ARTIFACTS, ROOT, canvas_image, local_server, settle, snapshot

HOOK = '''window.highlightOptimizationOracle = {
  batch: enabled => { details.regions.batches.enabled = enabled; details.syncTransforms(); dirty = true; },
  fingerprints: async () => {
    const hash = async array => [...new Uint8Array(await crypto.subtle.digest('SHA-256', array))].map(x=>x.toString(16).padStart(2,'0')).join('');
    const records = [];
    for (const entry of details.landmarks.values()) {
      const region = details.hasSurface(entry) ? await details.regions.prepare(entry) : details.regions.empty(entry);
      const parts = [];
      for (const part of region.parts) parts.push({owner: part.ownerId, anchor: part.anchor.toArray(), anchorFace: part.anchorFace,
        positions: await hash(part.geometry.attributes.position.array), normals: await hash(part.geometry.attributes.normal.array),
        faces: await hash(part.sourceFaces), ids: await hash(part.faceIndex.ids),
        offsets: await hash(part.faceIndex.offsets), triangles: await hash(part.faceIndex.triangles)});
      records.push({id:region.id, area:region.area, methods:region.methods, parts});
    }
    return records;
  }
};
'''


def launch(p, profile=None):
    options = dict(executable_path=os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable'), headless=True,
        env={key: value for key, value in os.environ.items() if key not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
        args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan', '--disk-cache-size=536870912'])
    return p.chromium.launch_persistent_context(profile, **options) if profile else p.chromium.launch(**options)


def ready(page, url):
    page.goto(url, wait_until='networkidle')
    page.wait_for_function("document.documentElement.dataset.ready === 'true'", timeout=60000)
    settle(page)


def compare_batch(page, name, report):
    images, states = {}, {}
    for mode, enabled in [('individual', False), ('batched', True)]:
        page.evaluate('flag => window.highlightOptimizationOracle.batch(flag)', enabled)
        settle(page)
        images[mode] = canvas_image(page)
        states[mode] = snapshot(page)
        images[mode].save(ARTIFACTS / f'optimized-{name}-{mode}.png')
    difference = ImageChops.difference(images['individual'], images['batched'])
    channels = ImageStat.Stat(difference)
    changed = difference.convert('L').point(lambda value: 255 if value > 3 else 0).histogram()[255]
    fraction = changed / (difference.width * difference.height)
    assert states['individual']['camera'] == states['batched']['camera']
    assert states['individual']['anatomy']['enabledRegions'] == states['batched']['anatomy']['enabledRegions']
    assert sum(channels.mean) / 3 < 0.15 and fraction < 0.003, f'{name}: batched drawing changed {fraction:.2%} of pixels'
    record = {'view': name, 'mean_channel_difference': sum(channels.mean) / 3, 'pixels_changed_over_3': fraction,
              'individual_draw_calls': states['individual']['drawCalls'], 'batched_draw_calls': states['batched']['drawCalls']}
    report['visual_checks'].append(record)
    return record


def viewer_checks(p, url, report):
    browser = launch(p)
    try:
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        app = (ROOT / 'src/app.js').read_text()
        page.route('**/src/app.js*', lambda route: route.fulfill(content_type='text/javascript', body=app.replace('  window.skullAtlas = Object.freeze({', HOOK + '\n  window.skullAtlas = Object.freeze({')))
        requests = set()
        page.on('response', lambda response: requests.add(urlsplit(response.url).path))
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.on('console', lambda message: report['errors'].append(message.text) if message.type == 'error' else None)
        ready(page, url)
        state = snapshot(page)
        assert state['anatomy']['precomputed'] and not state['anatomy']['precomputedIssue']
        assert not page.workers and state['anatomy']['batching']['enabled']
        assert not any(path.endswith(('/registered-regions.bin', '/apertures.bin', '/source-regions.bin')) for path in requests), 'Saved highlights still download fitting inputs'
        elapsed = page.evaluate('''() => new Promise(resolve => {
          const started=performance.now();document.getElementById('landmark-pins').click();
          requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(performance.now()-started)));
        })''')
        assert elapsed < 1000, f'Cold Show all waited {elapsed:.0f} ms'
        state = snapshot(page)
        located = {entry['id'] for entry in state['anatomy']['landmarks'] if entry['located']}
        assert len(located) == 725
        assert not state['anatomy']['preparingRegions'] and set(state['anatomy']['enabledRegions']) == located
        assert sum(region['rendered'] for region in state['anatomy']['regions']) == 150
        report['cold_show_all_ms'] = elapsed
        base = compare_batch(page, 'all', report)
        assert base['batched_draw_calls'] < base['individual_draw_calls'] * 0.5
        page.screenshot(path=str(ARTIFACTS / 'optimized-all-interface.png'))
        page.locator('#landmark-search').fill('Right pterygopalatine fossa')
        page.get_by_role('button', name='Right pterygopalatine fossa', exact=True).click()
        page.mouse.move(1450, 980)
        compare_batch(page, 'selected-fossa', report)
        page.locator('.more-controls > summary').click()
        page.locator('#explode').fill('35')
        page.locator('#jaw').fill('15')
        compare_batch(page, 'exploded-jaw', report)
        page.locator('#section').select_option('sagittal')
        page.locator('#section-position').fill('50')
        compare_batch(page, 'section', report)
        page.locator('#section').select_option('none')
        page.locator('#opacity').fill('55')
        compare_batch(page, 'transparent', report)
        assert snapshot(page)['anatomy']['batching']['visibleGroups'] == 0
        page.locator('#reset').click()
        saved = page.evaluate('window.highlightOptimizationOracle.fingerprints()')
        page.add_init_script('globalThis.__SKULL_BUILD_HIGHLIGHTS__ = true;')
        ready(page, url)
        assert not snapshot(page)['anatomy']['precomputed'] and page.workers
        live = page.evaluate('window.highlightOptimizationOracle.fingerprints()')
        assert saved == live, 'Saved positions, normals, native-face lookup, anchors or methods differ from the live builder'
        report['identical_landmark_records'] = len(saved)
        report['identical_painted_regions'] = sum(bool(region['parts']) for region in saved)
        assert not report['errors'], report['errors']
    finally:
        browser.close()


def rejection_checks(p, url, report):
    original = json.loads((ROOT / 'assets/landmark-highlights/manifest.json').read_text())
    for kind in ('stale-input', 'wrong-checksum', 'unavailable-manifest'):
        metadata = json.loads(json.dumps(original))
        if kind == 'stale-input':
            metadata['inputs']['src/surface-regions.js'] = '0' * 64
        elif kind == 'wrong-checksum':
            metadata['binary']['sha256'] = '0' * 64
        browser = launch(p)
        try:
            page = browser.new_page(viewport={'width': 1100, 'height': 800})
            if kind == 'unavailable-manifest':
                page.route('**/assets/landmark-highlights/manifest.json*', lambda route: route.abort('failed'))
            else:
                page.route('**/assets/landmark-highlights/manifest.json*', lambda route: route.fulfill(content_type='application/json', body=json.dumps(metadata)))
            ready(page, url)
            state = snapshot(page)['anatomy']
            assert not state['precomputed'] and state['precomputedIssue'] and page.workers
            assert not state['markers'] and not any(region['rendered'] for region in state['regions'])
            report['rejected_packages'].append({'kind': kind, 'reason': state['precomputedIssue']})
        finally:
            browser.close()


def cache_checks(p, url, report):
    with tempfile.TemporaryDirectory(prefix='skull-cache-profile-') as profile:
        for attempt in range(2):
            context = launch(p, profile)
            try:
                page = context.pages[0]
                page.set_viewport_size({'width': 1200, 'height': 900})
                session = context.new_cdp_session(page)
                session.send('Network.enable')
                records = []
                session.on('Network.responseReceived', lambda event: records.append(event['response']))
                ready(page, url)
                assert snapshot(page)['anatomy']['precomputed']
                relevant = [r for r in records if urlsplit(r['url']).path in {
                    '/assets/skull-ct/surface.glb', '/assets/skull-associated/bones.glb',
                    '/assets/skull-cavities/cavities.glb', '/assets/landmark-highlights/highlights.bin'}]
                assert len(relevant) == 4
                for response in relevant:
                    headers = {key.lower(): value for key, value in response['headers'].items()}
                    assert headers.get('content-encoding') == 'zstd', headers
                    assert 'immutable' in headers.get('cache-control', '')
                    if attempt:
                        assert response.get('fromDiskCache'), f'{response["url"]} did not survive browser restart in the disk cache'
                report['cache_loads'].append({'attempt': attempt + 1,
                    'assets': [{'path': urlsplit(r['url']).path, 'from_disk_cache': r.get('fromDiskCache', False)} for r in relevant]})
            finally:
                context.close()


def main():
    report = {'result': 'failed', 'visual_checks': [], 'errors': [], 'rejected_packages': [], 'cache_loads': []}
    started = time.monotonic()
    try:
        with local_server() as url, sync_playwright() as p:
            viewer_checks(p, url, report)
            rejection_checks(p, url, report)
            cache_checks(p, url, report)
        report['result'] = 'passed'
    finally:
        report['seconds'] = time.monotonic() - started
        (ARTIFACTS / 'precomputed-highlights-report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
