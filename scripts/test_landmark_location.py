#!/usr/bin/env python3
"""Verify broad-list hover, honest location cues and hidden-region previews."""

import argparse
import json
import os
import shutil

from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_regions import SourceTriangles, assert_on_triangle, audit_geometry, changed_pixels, select_feature, visible_point
from test_viewer import ARTIFACTS, ROOT, canvas_image, capture, local_server, settle, snapshot


def check(page, url, dataset, checks):
    load(page, url, f'?dataset={dataset}')
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator('#landmark-estimates').check()
    identity = select_feature(page, 'Left greater wing')
    page.locator('#anatomy-isolate').click()
    settle(page)
    geometry = audit_geometry(page, SourceTriangles(dataset), identity)
    for part in geometry['parts']:
        assert_on_triangle(part['anchor'], SourceTriangles(dataset).triangle(part['owner'], part['anchorFace']))
    point = visible_point(page, geometry)
    page.locator(f'.landmark-row[data-landmark="{identity}"] input').uncheck()
    page.locator('#landmark-search').fill('')
    page.locator('#landmark-search').fill('sphenoid')
    assert page.locator('.landmark-row:visible').count() > 24
    page.mouse.move(point['x'], point['y'])
    page.wait_for_function('!window.skullAtlas.snapshot().anatomy.preparingRegions')
    page.mouse.move(0, 0)
    # A suture at the wing's edge legitimately wins an overlap. Find a visible
    # source-triangle point in the wing interior under the complete candidate set.
    point = visible_point(page, geometry)
    page.mouse.move(point['x'], point['y'])
    try:
        page.wait_for_function('id => window.skullAtlas.snapshot().anatomy.hoveredRegion === id', arg=identity, timeout=5000)
    except Exception:
        capture(page, f'location-{dataset}-hover-failure')
        print(json.dumps({'expected': identity, 'point': point, 'actual': snapshot(page)['anatomy']['hoveredRegion'],
                          'pick': page.evaluate('p=>window.skullAtlas.regionAt(p.x,p.y)', point),
                          'cached': [{k:r[k] for k in ['id','area','rendered']} for r in snapshot(page)['anatomy']['regions']]}, indent=2), flush=True)
        raise
    assert identity not in snapshot(page)['anatomy']['enabledRegions']
    assert snapshot(page)['hovered'] is None
    capture(page, f'location-{dataset}-broad-sphenoid-hover')
    checks.append(f'{dataset}: inactive greater wing discovered with the full sphenoid list, without enabling it or colouring the whole bone')

    before = snapshot(page)
    assert not before['anatomy']['selected'] and identity not in before['anatomy']['enabledRegions']
    for query in ('mental foramen', 'no-matching-surface-landmark'):
        page.mouse.move(0, 0)
        page.locator('#landmark-search').fill(query)
        assert page.locator(f'.landmark-row[data-landmark="{identity}"]').is_hidden()
        page.mouse.move(point['x'], point['y'])
        settle(page)
        current = snapshot(page)
        assert current['anatomy']['hoveredRegion'] == identity, f'{dataset}: search {query!r} blocked an unrelated physical surface hover'
        assert page.locator('#hover-label').get_attribute('data-hover-id') == identity
        assert current['anatomy']['enabledRegions'] == before['anatomy']['enabledRegions']
        assert current['structures'] == before['structures']
    assert page.locator('#landmark-no-results').is_visible()
    checks.append(f'{dataset}: inactive surface hover ignores catalogue text, including zero-result searches, while list filtering and bone visibility stay intact')

    page.mouse.move(0, 0)
    page.locator('#landmark-search').fill('Left pterygoid fossa')
    settle(page)
    plain = canvas_image(page)
    page.get_by_role('button', name='Left pterygoid fossa', exact=True).hover()
    settle(page)
    guide = page.evaluate('window.skullAtlas.regionGeometry("left-pterygoid-fossa")')
    assert not guide['parts'] and guide['area'] == 0
    assert page.locator('#landmark-location-aid').is_visible()
    assert 'Approximate location' in page.locator('#landmark-location-status').inner_text()
    assert 'no surface boundary' in page.locator('#landmark-location-status').inner_text()
    assert changed_pixels(plain, canvas_image(page)) == 0, 'A guide must not acquire a painted surface'
    capture(page, f'location-{dataset}-guide')
    page.mouse.move(0, 0)
    settle(page)
    assert page.locator('#landmark-location-aid').is_hidden()
    checks.append(f'{dataset}: unsupported sphenoid guide gets a labelled locator on hover and no anatomical patch')

    load(page, url, f'?dataset={dataset}')
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator('#landmark-estimates').check()
    page.locator('#landmark-search').fill('Sella turcica')
    page.get_by_role('button', name='Sella turcica', exact=True).click()
    page.mouse.move(0, 0)
    settle(page)
    assert page.locator('#landmark-location-aid').is_visible()
    assert 'Hidden behind bone' in page.locator('#landmark-location-status').inner_text()
    state = snapshot(page)
    assert not page.locator('#landmark-markers').is_checked()
    coloured = canvas_image(page)
    target = page.evaluate('window.skullAtlas.projectLandmark("sella-turcica")')
    canvas_box = page.locator('#skull-canvas').bounding_box()
    target_x = round((target['x'] - canvas_box['x']) * coloured.width / canvas_box['width'])
    target_y = round((target['y'] - canvas_box['y']) * coloured.height / canvas_box['height'])
    capture(page, f'location-{dataset}-hidden-sella')
    page.locator('#landmark-xray').uncheck()
    settle(page)
    plain = canvas_image(page)
    pixels = changed_pixels(coloured, plain)
    assert pixels > 30, f'The covered sella preview is not visible: {pixels} pixels'
    target_box = (target_x - 2, target_y - 2, target_x + 3, target_y + 3)
    assert changed_pixels(coloured.crop(target_box), plain.crop(target_box)) > 0, 'The see-through preview misses its independently occluded anchor'
    assert all(not region['xrayRendered'] for region in snapshot(page)['anatomy']['regions'])
    assert snapshot(page)['structures'] == state['structures'], 'A see-through cue changed bone appearance or visibility'
    assert snapshot(page)['camera'] == state['camera']
    page.locator('#landmark-xray').check()
    page.locator('#landmark-locate').uncheck()
    settle(page)
    assert page.locator('#landmark-location-aid').is_hidden()
    assert any(region['xrayRendered'] for region in snapshot(page)['anatomy']['regions'])
    page.locator('#landmark-locate').check()
    page.locator('.more-controls > summary').click()
    page.locator('#section').select_option('sagittal')
    page.locator('#section-position').fill('100')
    settle(page)
    region = next(r for r in snapshot(page)['anatomy']['regions'] if r['id'] == 'sella-turcica')
    assert not region['rendered'] and not region['xrayRendered']
    assert 'Location outside section' in page.locator('#landmark-location-status').inner_text()
    page.locator('#section').select_option('none')
    page.locator('.bone-row[data-id="sphenoid-bone"] input[type="checkbox"]').evaluate(
        'element => { element.checked=false; element.dispatchEvent(new Event("change",{bubbles:true})); }')
    settle(page)
    region = next(r for r in snapshot(page)['anatomy']['regions'] if r['id'] == 'sella-turcica')
    assert not region['rendered'] and not region['xrayRendered']
    assert 'Bone hidden' in page.locator('#landmark-location-status').inner_text()
    checks.append(f'{dataset}: covered sella is visible through bone only with the aid enabled; bone appearance, visibility and camera remain intact')
    page.locator('#reset').click()
    settle(page)
    assert page.locator('#landmark-location-aid').is_hidden()
    assert snapshot(page)['anatomy']['includeEstimates']
    assert not any(r['xrayRendered'] for r in snapshot(page)['anatomy']['regions'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutant', choices=['broad-hover', 'xray-depth'], help='Deliberately faulty isolated module response; the regression must fail')
    args = parser.parse_args()
    report = {'result': 'failed', 'checks': [], 'page_errors': []}
    report['mutant'] = args.mutant
    chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    try:
        with local_server() as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=chrome, headless=True,
                env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
                args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                if args.mutant:
                    name = 'anatomy-details.js' if args.mutant == 'broad-hover' else 'surface-regions.js'
                    module = (ROOT / 'src' / name).read_text()
                    before, after = (('const candidates = this.hoverLandmarks.filter((entry) => entry.owners.includes(owner) && this.hasSurface(entry));',
                                      'const candidates = this.hoverLandmarks.length <= 24 ? this.hoverLandmarks.filter((entry) => entry.owners.includes(owner) && this.hasSurface(entry)) : [];')
                                     if args.mutant == 'broad-hover' else ('depthFunc: THREE.GreaterDepth', 'depthFunc: THREE.LessEqualDepth'))
                    assert module.count(before) == 1
                    module = module.replace(before, after)
                    page.route(f'**/src/{name}*', lambda route: route.fulfill(body=module, content_type='text/javascript'))
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                for dataset in ['ct']:
                    check(page, url, dataset, report['checks'])
                page.set_viewport_size({'width': 390, 'height': 844})
                page.emulate_media(reduced_motion='reduce')
                load(page, url, '?dataset=ct')
                page.locator("#landmark-panel").evaluate("e=>e.open=true")
                page.locator('#landmark-estimates').check()
                page.locator('#landmark-search').fill('Sella turcica')
                page.get_by_role('button', name='Sella turcica', exact=True).click()
                settle(page)
                callout = page.locator('#landmark-location-caption').bounding_box()
                canvas = page.locator('#skull-canvas').bounding_box()
                assert canvas['x'] <= callout['x'] and callout['x'] + callout['width'] <= canvas['x'] + canvas['width']
                point = page.evaluate('window.skullAtlas.projectLandmark("sella-turcica")')
                card = page.locator('#anatomy-selection').bounding_box()
                assert not (card['x'] <= point['x'] <= card['x'] + card['width'] and card['y'] <= point['y'] <= card['y'] + card['height']), 'Mobile selection card hides the location cue'
                assert not page.locator('#anatomy-information').evaluate('element=>element.open')
                capture(page, 'location-mobile')
                assert not report['page_errors'], report['page_errors']
                report['checks'].append('Mobile location callout stays in the canvas with reduced motion enabled')
                report['result'] = 'passed'
            finally:
                browser.close()
    finally:
        suffix = f'-{args.mutant}-mutant' if args.mutant else ''
        (ARTIFACTS / f'landmark-location{suffix}-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
