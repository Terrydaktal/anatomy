#!/usr/bin/env python3
"""Prove the catalogue audit rejects invented regions with a test-only mutant."""

import json
import os
import shutil

from playwright.sync_api import sync_playwright

from audit_landmarks import runtime_failures
from test_anatomy import load
from test_regions import select_feature
from test_viewer import ARTIFACTS, ROOT, local_server


def main():
    report_path = ARTIFACTS / 'landmark-policy-report.json'
    report_path.write_text(json.dumps({'result': 'incomplete'}) + '\n')
    identity = 'left-pterygoid-fossa'
    row = {'dataset': 'ct', 'id': identity, 'kind': 'location-guide', 'owners': ['sphenoid-bone']}
    source = (ROOT / 'src/surface-regions.js').read_text()
    guard = 'continue; // A label coordinate cannot supply an anatomical area boundary.'
    assert source.count(guard) == 1
    mutant = source.replace(guard, '''
        const center = new THREE.Vector3(...entry.placement.point), radius = 8;
        const fields = axes.map((axis) => (v) => radius - axis.x * (v[0] - center.x) - axis.y * (v[1] - center.y) - axis.z * (v[2] - center.z));
        const bounds = new THREE.Box3(center.clone().addScalar(-radius), center.clone().addScalar(radius));
        polygons = polygonsFor(geometry, fields, bounds);
        method = 'mutant:invented-guide-region';
    ''')
    chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    results, errors = [], []
    with local_server() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True,
            env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
            args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
        try:
            for label, module in [('production', source), ('invented-boundary-mutant', mutant)]:
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                page.add_init_script('globalThis.__SKULL_BUILD_HIGHLIGHTS__ = true;')
                if label != 'production':
                    page.route('**/src/surface-regions.js*', lambda route, _request, module=module: route.fulfill(body=module, content_type='text/javascript'))
                page.on('pageerror', lambda error: errors.append(str(error)))
                load(page, url, '?dataset=ct')
                page.locator("#landmark-panel").evaluate("e=>e.open=true")
                page.locator('#landmark-estimates').check()
                assert select_feature(page, 'Left pterygoid fossa') == identity
                geometry = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
                result = {'area': geometry['area'], 'owners': [p['owner'] for p in geometry['parts']]}
                detected = runtime_failures(row, result)
                if label == 'production':
                    assert result['area'] == 0 and not detected
                    assert 'Location guide only' in page.locator('#anatomy-placement').inner_text()
                else:
                    assert result['area'] > 0, 'The mutant did not exercise a filled guide region'
                    assert any('unsupported location' in failure for failure in detected), 'The catalogue audit failed to detect the mutant'
                results.append({'case': label, **result, 'detected_failures': detected})
                page.close()
        finally:
            browser.close()
    assert not errors, errors
    report = {'result': 'passed', 'cases': results, 'page_errors': errors,
              'note': 'Only isolated HTTP responses were mutated; production files and assets were unchanged.'}
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
