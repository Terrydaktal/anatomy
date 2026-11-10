#!/usr/bin/env python3
"""Check real maxillary highlights for enclosed gaps and report visible pieces.

The optional --package path serves an archived transfer package through HTTP,
allowing the same visual regression to demonstrate the original failure.
"""

import argparse
import colorsys
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

import numpy as np
from PIL import ImageColor
from playwright.sync_api import sync_playwright
from scipy.ndimage import binary_fill_holes, label

from test_anatomy import load
from test_regions import select_feature, visible_point
from test_viewer import ARTIFACTS, ROOT, QuietHandler, canvas_image, capture, local_server


def visible_fill(page, colour):
    pixels = np.asarray(canvas_image(page).convert('HSV')).astype(float)
    hue = colorsys.rgb_to_hsv(*(v / 255 for v in ImageColor.getrgb(colour)))[0] * 255
    difference = np.abs(pixels[:, :, 0] - hue)
    difference = np.minimum(difference, 255 - difference)
    # Physical lighting and tone mapping shift the rendered hue from the base
    # material colour. Include that full range, rather than counting lit paint
    # as white gaps. These two region colours remain well apart from ivory.
    coloured = (difference < 24) & (pixels[:, :, 1] > 70) & (pixels[:, :, 2] > 35)
    # Anti-aliased one-pixel seams are not the triangular missing areas in the
    # reported failure. Source outlines and occlusion can split a visible region;
    # report those pieces without inventing paint to join them.
    holes, _ = label(binary_fill_holes(coloured) & ~coloured)
    hole_sizes = np.bincount(holes.ravel())[1:]
    components, _ = label(coloured)
    sizes = np.bincount(components.ravel())[1:]
    return {'coloured_pixels': int(coloured.sum()),
            'enclosed_hole_sizes': sorted(hole_sizes[hole_sizes >= 12].astype(int).tolist(), reverse=True),
            'visible_component_sizes': sorted(sizes[sizes >= 30].astype(int).tolist(), reverse=True)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=['ct'], default='ct')
    parser.add_argument('--package', type=Path, help='Archived registered-regions JSON; binary must have the same stem')
    parser.add_argument('--label', default='continuity', help='Artifact filename prefix')
    args = parser.parse_args()
    manifest = json.loads((args.package or ROOT / 'assets/landmark-regions/registered-regions.json').read_text())
    datasets = ['ct', 'reference'] if args.dataset == 'both' else [args.dataset]
    handler = QuietHandler
    if args.package:
        package = args.package.resolve()

        class PackageHandler(QuietHandler):
            def translate_path(self, path):
                name = urlsplit(path).path.rsplit('/', 1)[-1]
                if name == 'registered-regions.json':
                    return str(package)
                if name == 'registered-regions.bin':
                    return str(package.with_suffix('.bin'))
                return super().translate_path(path)

        handler = PackageHandler
    chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    report, errors, failures = {'regions': [], 'anatomical_accuracy_percent': None}, [], []
    with local_server(handler) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True,
            env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
            args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
        try:
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            if args.package:
                page.add_init_script('globalThis.__SKULL_BUILD_HIGHLIGHTS__ = true;')
                # Explicit historical replay bypasses only the current transfer
                # protocol guard in an isolated response, never production code.
                module = (ROOT / 'src/surface-regions.js').read_text()
                guard = "if (entry.source_id !== 'biodigital') return true;"
                assert module.count(guard) == 1
                module = module.replace(guard, 'return true; // Test-only archived transfer replay')
                page.route('**/src/surface-regions.js*', lambda route: route.fulfill(body=module, content_type='text/javascript'))
            page.on('pageerror', lambda error: errors.append(str(error)))
            for dataset in datasets:
                for side in ['right', 'left']:
                    load(page, url, f'?dataset={dataset}&study=mapped-regions')
                    page.locator('#landmark-xray').uncheck()
                    expected = f'{side}-alveolar-process-of-maxilla'
                    accepted = any(r['dataset'] == dataset and r['feature_id'] == expected for r in manifest['regions'])
                    if not accepted:
                        # An archived reference left maxilla may have failed
                        # coverage. Require explicit withholding, without a blob.
                        assert dataset == 'reference' and side == 'left'
                        rejection = next(r for r in manifest['rejected'] if r['dataset'] == dataset and r.get('feature_id') == expected)
                        page.locator('#landmark-mapped-only').uncheck()
                        identity = select_feature(page, f'{side.title()} alveolar process of maxilla')
                        audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
                        assert not audit['parts']
                        assert 'withheld' in page.locator('#anatomy-placement').inner_text().lower()
                        report['regions'].append({'dataset': dataset, 'id': identity, 'withheld': rejection['reason']})
                        continue
                    identity = select_feature(page, f'{side.title()} alveolar process of maxilla')
                    audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
                    assert audit['parts'] and audit['methods'] == ['fitted:registered-author-region']
                    measure = visible_fill(page, audit['parts'][0]['colour'])
                    capture(page, f'{args.label}-{dataset}-{identity}')
                    point = visible_point(page, audit)
                    assert page.evaluate('({x,y}) => window.skullAtlas.regionAt(x,y)', point) == identity
                    if measure['coloured_pixels'] < 1000 or measure['enclosed_hole_sizes']:
                        failures.append(f'{dataset}:{identity}: {measure}')
                    report['regions'].append({'dataset': dataset, 'id': identity, **measure})
        finally:
            browser.close()
    report['failures'] = failures + errors
    report['archived_protocol_replay'] = bool(args.package)
    (ARTIFACTS / f'{args.label}-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    assert not report['failures'], '\n'.join(report['failures'])


if __name__ == '__main__':
    main()
