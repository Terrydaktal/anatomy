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
    page.locator('#anatomy-close').click()
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


def orbital_foramina_check(page, url, checks):
    load(page, url, '?dataset=ct')
    page.locator('#landmark-panel').evaluate('e=>e.open=true')
    page.locator('[data-view="anterior"]').click()
    page.mouse.move(0, 0)
    settle(page)
    state = snapshot(page)
    identities = {entry['id'] for entry in state['anatomy']['landmarks']}
    features = ['cranio-orbital foramen', 'zygomaticoorbital foramen', 'zygomaticofacial foramen',
                'zygomaticotemporal foramen', 'anterior ethmoidal foramen', 'posterior ethmoidal foramen']
    unlocated = {f'{side}-{feature.replace(" ", "-")}' for side in ['left', 'right'] for feature in features}
    for side in ['left', 'right']:
        for feature in [name.replace(' ', '-') for name in features]:
            assert f'{side}-{feature}' in identities, f'Missing named orbital opening: {side}-{feature}'
    expected = {'left-cranio-orbital-foramen', 'right-cranio-orbital-foramen'}
    for query in ['cranio-orbital', 'cranioorbital', 'meningo-orbital', 'meningoorbital', 'sphenofrontal foramen', 'foramen cranioorbitale']:
        page.locator('#landmark-search').fill(query)
        matches = page.locator('.landmark-row:not([hidden])').evaluate_all('rows=>rows.map(row=>row.dataset.landmark)')
        assert set(matches) == expected and len(matches) == 2, f'{query}: missing or duplicate cranio-orbital synonyms: {matches}'
    for name in [f'{side} {feature}' for side in ['Left', 'Right'] for feature in features]:
        identity = name.lower().replace(' ', '-')
        page.locator('#landmark-search').fill(name)
        page.get_by_role('button', name=name, exact=True).click()
        page.mouse.move(0, 0)
        settle(page)
        assert page.locator('#landmark-location-aid').is_hidden(), f'{identity}: an unlocated foramen still points to a guessed surface location'
        current = snapshot(page)
        assert current['anatomy']['selected'] == {'id': identity, 'kind': 'landmark'}
        assert current['structures'] == state['structures'] and current['camera'] == state['camera'], 'Selecting an unlocated name changed the bones or camera'
        entry = next(entry for entry in current['anatomy']['landmarks'] if entry['id'] == identity)
        assert not entry['located'] and entry['point'] is None and entry['placementStatus'] == 'unlocated'
        assert page.evaluate('id=>window.skullAtlas.projectLandmark(id)', identity) is None
        assert page.locator(f'.landmark-row[data-landmark="{identity}"] input').is_disabled()
        assert 'not located on this skull' in page.locator(f'.landmark-row[data-landmark="{identity}"]').inner_text()
        assert 'Not located on this skull' in page.locator('#anatomy-placement').text_content()
        for control in ['anatomy-focus', 'anatomy-isolate', 'anatomy-hide']:
            assert page.locator(f'#{control}').is_disabled()
        geometry = page.evaluate('id=>window.skullAtlas.regionGeometry(id)', identity)
        assert not geometry['parts'] and geometry['area'] == 0, 'A named variable opening acquired an invented boundary'
        assert 'FIPAT-TA2-Part-2.pdf' in page.locator('#anatomy-source').get_attribute('href')
        if 'cranio-orbital' in identity:
            capture(page, f'location-{identity}')
    page.locator('#landmark-search').fill('')
    page.locator('#landmark-pins').check()
    page.locator('#landmark-markers').check()
    page.locator('#landmark-labels').check()
    settle(page)
    current = snapshot(page)
    assert not unlocated.intersection(current['anatomy']['enabledRegions'])
    assert all(not entry['visible'] for entry in current['anatomy']['landmarks'] if entry['id'] in unlocated)
    assert current['anatomy']['locationAid'] is None
    page.locator('#landmark-search').fill('cranio-orbital')
    page.locator('#landmark-estimates').uncheck()
    settle(page)
    assert page.locator('.landmark-row:not([hidden])').count() == 0
    assert snapshot(page)['anatomy']['selected'] is None
    assert page.locator('#landmark-location-aid').is_hidden()
    checks.append('All 12 orbital-foramen names remain searchable with source links; unlocated entries have null coordinates, no targets/pins/paint, disabled spatial actions and no camera/bone changes, including Show all with Pins and Names enabled')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutant', choices=['broad-hover', 'xray-depth', 'orbital-guesses'], help='Deliberately faulty isolated response; the regression must fail')
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
                if args.mutant in ['broad-hover', 'xray-depth']:
                    name = 'anatomy-details.js' if args.mutant == 'broad-hover' else 'surface-regions.js'
                    module = (ROOT / 'src' / name).read_text()
                    before, after = (('const candidates = this.hoverLandmarks.filter((entry) => entry.owners.includes(owner) && this.hasSurface(entry));',
                                      'const candidates = this.hoverLandmarks.length <= 24 ? this.hoverLandmarks.filter((entry) => entry.owners.includes(owner) && this.hasSurface(entry)) : [];')
                                     if args.mutant == 'broad-hover' else ('depthFunc: THREE.GreaterDepth', 'depthFunc: THREE.LessEqualDepth'))
                    assert module.count(before) == 1
                    module = module.replace(before, after)
                    page.route(f'**/src/{name}*', lambda route: route.fulfill(body=module, content_type='text/javascript'))
                elif args.mutant == 'orbital-guesses':
                    catalogue = json.loads((ROOT / 'assets/landmarks/catalogue.json').read_text())
                    for entry in catalogue['landmarks']:
                        for dataset, placement in entry['positions'].items():
                            if placement.get('location_status') == 'unlocated':
                                restored = {**placement, **placement['rejected_location']}
                                restored.pop('location_status')
                                entry['positions'][dataset] = restored
                    page.route('**/assets/landmarks/catalogue.json*', lambda route: route.fulfill(body=json.dumps(catalogue), content_type='application/json'))
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                for dataset in ['ct']:
                    check(page, url, dataset, report['checks'])
                orbital_foramina_check(page, url, report['checks'])
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
