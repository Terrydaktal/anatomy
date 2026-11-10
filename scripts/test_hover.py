#!/usr/bin/env python3
"""Verify pointer tooltips, exact overlap lists and radius-only point guides."""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_regions import assert_on_triangle, changed_pixels, select_feature, visible_point
from test_viewer import ARTIFACTS, ROOT, QuietHandler, canvas_image, capture, local_server, settle, snapshot


def beside_pointer(page, point):
    tooltip = page.locator('#hover-label')
    assert tooltip.is_visible(), 'The pointer tooltip is hidden'
    box = tooltip.bounding_box()
    canvas = page.locator('#skull-canvas').bounding_box()
    dx = max(box['x'] - point['x'], point['x'] - box['x'] - box['width'], 0)
    dy = max(box['y'] - point['y'], point['y'] - box['y'] - box['height'], 0)
    assert math.hypot(dx, dy) <= 35, f'Tooltip remains away from the pointer: {box}, {point}'
    assert canvas['x'] <= box['x'] and box['x'] + box['width'] <= canvas['x'] + canvas['width']
    assert canvas['y'] <= box['y'] and box['y'] + box['height'] <= canvas['y'] + canvas['height']
    assert tooltip.evaluate('e=>getComputedStyle(e).pointerEvents') == 'none'


def bone_check(page, url, dataset):
    load(page, url, f'?dataset={dataset}')
    page.locator('#landmark-estimates').uncheck()
    page.locator('[data-view="anterior"]').click()
    point = page.evaluate('window.skullAtlas.project("frontal-bone")')
    page.mouse.move(point['x'], point['y'])
    page.wait_for_function('window.skullAtlas.snapshot().hovered === "frontal-bone"')
    beside_pointer(page, point)
    assert page.locator('#hover-name').inner_text() == 'Frontal bone'
    first = page.locator('#hover-label').bounding_box()
    point = {'x': point['x'] + 25, 'y': point['y'] + 12}
    page.mouse.move(point['x'], point['y'])
    settle(page)
    beside_pointer(page, point)
    second = page.locator('#hover-label').bounding_box()
    assert abs(second['x'] - first['x']) + abs(second['y'] - first['y']) > 20, 'Tooltip does not follow motion over the same bone'
    assert page.locator('#hover-overlaps').is_hidden()
    capture(page, f'hover-{dataset}-bone-{page.viewport_size["width"]}')
    page.mouse.down()
    settle(page)
    assert page.locator('#hover-label').is_hidden(), 'Tooltip covers a rotation gesture'
    page.mouse.up()
    page.mouse.move(0, 0)
    settle(page)
    assert page.locator('#hover-label').is_hidden()
    return f'{dataset}: bone tooltip follows the pointer, stays within the canvas and clears on drag/leave'


def point_check(page, url, dataset):
    identity = 'external-surface-of-squamous-part-of-frontal-bone'
    load(page, url, f'?dataset={dataset}')
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator('#landmark-estimates').check()
    page.locator('#landmark-search').fill('External surface of squamous part of frontal bone')
    page.locator('[data-view="anterior"]').click()
    settle(page)
    point = page.evaluate('id=>window.skullAtlas.projectLandmark(id)', identity)
    for offset, inside in [(20, False), (13, True), (15, False), (-13, True), (-15, False), (0, True)]:
        pointer = {'x': point['x'] + offset, 'y': point['y']}
        page.mouse.move(pointer['x'], pointer['y'])
        settle(page)
        actual = snapshot(page)['anatomy']['hoveredRegion']
        assert (actual == identity) == inside, f'{dataset}: point guide picked outside its 14 px radius, or missed inside it: {offset}, {actual}'
        if inside:
            beside_pointer(page, pointer)
            assert page.locator('#hover-label').get_attribute('data-hover-id') == identity
            overlaps = page.locator('#hover-overlaps li').evaluate_all('rows => rows.map(row => row.dataset.hoverId)')
            assert overlaps[-1] == 'frontal-bone', 'Guide hover lost its parent-bone context'
            surface = page.evaluate('p => window.skullAtlas.regionAt(p.x, p.y)', pointer)
            if surface:
                assert overlaps[0] == surface, 'Guide hover omitted the physical region under the pointer'
            else:
                assert overlaps == ['frontal-bone']
            assert identity not in overlaps and len(overlaps) == len(set(overlaps))
            assert page.locator('#landmark-location-caption').is_hidden(), 'Duplicate landmark and pointer tooltips'
    assert not page.locator('#landmark-markers').is_checked() and not page.locator('#landmark-labels').is_checked()
    audit = page.evaluate('id=>window.skullAtlas.regionGeometry(id)', identity)
    assert not audit['parts'] and not audit['area'], 'Hovering a point guide fabricated a painted region'
    assert identity not in snapshot(page)['anatomy']['enabledRegions']
    capture(page, f'hover-{dataset}-point')
    for query in ('mandible', 'no-matching-hover-guide'):
        retained = page.locator('#landmark-search').evaluate('''(input, query) => {
            input.value = query; input.dispatchEvent(new Event('input', {bubbles: true}));
            return window.skullAtlas.snapshot().anatomy.hoveredRegion;
        }''', query)
        assert retained == identity, f'{dataset}: search text cleared the stationary canvas hover'
        assert page.locator(f'.landmark-row[data-landmark="{identity}"]').is_hidden()
        page.mouse.move(point['x'] + 1, point['y'])
        settle(page)
        assert snapshot(page)['anatomy']['hoveredRegion'] == identity, f'{dataset}: search {query!r} removed an unrelated physical point-guide target'
        beside_pointer(page, point)
    assert page.locator('#landmark-no-results').is_visible()
    page.mouse.click(point['x'], point['y'])
    assert snapshot(page)['anatomy']['selected'] == {'id': identity, 'kind': 'landmark'}
    # Moving the camera changes the projection, not the CSS-pixel hit radius.
    page.mouse.wheel(0, -150)
    settle(page)
    point = page.evaluate('id=>window.skullAtlas.projectLandmark(id)', identity)
    for offset, inside in [(13, True), (15, False)]:
        page.mouse.move(point['x'] + offset, point['y'])
        settle(page)
        assert (snapshot(page)['anatomy']['hoveredRegion'] == identity) == inside
    page.locator('#landmark-search').fill('Internal surface of squamous part of frontal bone')
    point = page.evaluate('window.skullAtlas.projectLandmark("internal-surface-of-squamous-part-of-frontal-bone")')
    page.mouse.move(point['x'], point['y'])
    settle(page)
    assert snapshot(page)['anatomy']['hoveredRegion'] != 'internal-surface-of-squamous-part-of-frontal-bone', 'Picked a guide through opaque cortex'
    page.locator('#landmark-search').fill('External surface of squamous part of frontal bone')
    point = page.evaluate('id=>window.skullAtlas.projectLandmark(id)', identity)
    page.mouse.move(point['x'], point['y'])
    settle(page)
    assert snapshot(page)['anatomy']['hoveredRegion'] == identity
    page.locator('.bone-row[data-id="frontal-bone"] input[type="checkbox"]').evaluate('e=>{e.checked=false;e.dispatchEvent(new Event("change",{bubbles:true}));}')
    settle(page)
    assert snapshot(page)['anatomy']['hoveredRegion'] != identity, 'Hidden bone retained a point target'
    page.locator('.bone-row[data-id="frontal-bone"] input[type="checkbox"]').evaluate('e=>{e.checked=true;e.dispatchEvent(new Event("change",{bubbles:true}));}')
    page.locator('.more-controls > summary').click()
    page.locator('#section').select_option('sagittal')
    page.locator('#section-position').fill('100')
    settle(page)
    assert snapshot(page)['anatomy']['hoveredRegion'] != identity, 'Section removed the bone but retained its point target'
    page.locator('#section').select_option('none')
    page.locator('#landmark-estimates').uncheck()
    page.mouse.move(point['x'], point['y'])
    settle(page)
    assert snapshot(page)['anatomy']['hoveredRegion'] != identity, 'Supplied-only mode retained a guide target'
    return f'{dataset}: point guides remain hoverable/selectable with unrelated or empty searches, obey their 14 CSS-pixel radius, occlusion, hidden bones, sections and estimate policy, and paint no invented surface'


def overlap_check(page, url, dataset):
    load(page, url, f'?dataset={dataset}')
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator('#landmark-estimates').check()
    for name in ['Coronal suture', 'Sagittal suture', 'Bregma']:
        select_feature(page, name)
    page.locator('#landmark-search').fill('')
    page.locator('#landmark-search').fill('Junctions and reference points')
    settle(page)
    audit = page.evaluate('window.skullAtlas.regionGeometry("bregma")')
    others = [page.evaluate('id=>window.skullAtlas.regionGeometry(id)', identity) for identity in ['coronal-suture', 'sagittal-suture']]
    # A point cue extends beyond the narrow suture mask. Choose an independently
    # intersecting native triangle, rather than assuming every cue pixel overlaps.
    polygons = {}
    for region in others:
        for part in region['parts']:
            for i, face in enumerate(part['sourceFaces']):
                p = part['positions'][i * 9:i * 9 + 9]
                polygons.setdefault((part['owner'], face), []).append((region['id'], [p[j:j + 3] for j in [0, 3, 6]]))
    point = None
    for part in audit['parts']:
        for i, face in enumerate(part['sourceFaces']):
            p = part['positions'][i * 9:i * 9 + 9]
            center = [sum(p[a::3]) / 3 for a in range(3)]
            expected = set()
            for region_id, triangle in polygons.get((part['owner'], face), []):
                try:
                    assert_on_triangle(center, triangle)
                    expected.add(region_id)
                except AssertionError:
                    pass
            if not expected:
                continue
            candidate = page.evaluate('p=>window.skullAtlas.projectPoint(p.point,p.owner)', {'point': center, 'owner': part['owner']})
            if page.evaluate('p=>window.skullAtlas.regionAt(p.x,p.y)', candidate) == 'bregma':
                point = candidate
                expected_owner = part['owner']
                expected_regions = expected
                break
        if point:
            break
    assert point, f'{dataset}: no visible native bregma/suture overlap in the fixture'
    page.mouse.move(point['x'], point['y'])
    settle(page)
    assert page.locator('#hover-label').get_attribute('data-hover-id') == 'bregma'
    rows = page.locator('#hover-overlaps li')
    ids = rows.evaluate_all('rows=>rows.map(e=>e.dataset.hoverId)')
    areas = {region['id']: region['area'] for region in snapshot(page)['anatomy']['regions']}
    expected_ids = sorted(expected_regions, key=lambda identity: (areas[identity], identity)) + [expected_owner]
    assert ids == expected_ids, f'Missing or unrelated larger overlaps/parent bone: {ids}; expected {expected_ids}'
    assert len(ids) == len(set(ids)) and 'bregma' not in ids
    assert all(areas[a] <= areas[b] for a, b in zip(ids[:-2], ids[1:-1])), 'Overlaps are not ordered by increasing area'
    main_size = page.locator('#hover-name').evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')
    assert rows.evaluate_all('(rows,size)=>rows.every(e=>parseFloat(getComputedStyle(e).fontSize)<size)', main_size)
    boxes = [rows.nth(i).bounding_box() for i in range(rows.count())]
    assert all(a['y'] + a['height'] <= b['y'] for a, b in zip(boxes, boxes[1:])), 'Overlap names are not a vertical list'
    beside_pointer(page, point)
    assert page.locator('#landmark-location-caption').is_hidden()
    capture(page, f'hover-{dataset}-overlaps')
    page.mouse.click(point['x'], point['y'])
    assert snapshot(page)['anatomy']['selected']['id'] == 'bregma'
    page.mouse.move(0, 0)
    settle(page)
    assert page.locator('#hover-label').is_hidden()
    assert page.locator('#landmark-location-caption').is_visible(), 'Selection lost its locator after the pointer left'
    return f'{dataset}: the smallest exact hit leads; larger exact overlaps and the parent bone form a smaller vertical list in one tooltip'


def selection_check(page, url, dataset):
    load(page, url, f'?dataset={dataset}')
    page.locator('#landmark-panel').evaluate('e=>e.open=true')
    page.locator('[data-view="anterior"]').click()
    page.mouse.move(0, 0)
    settle(page)
    plain = canvas_image(page)
    bones = snapshot(page)['structures']
    first, second = 'right-canine-fossa', 'left-canine-fossa'

    def choose(identity):
        name = identity.replace('-', ' ').capitalize()
        page.locator('#landmark-search').fill(name)
        page.get_by_role('button', name=name, exact=True).click()
        page.mouse.move(0, 0)
        settle(page)

    def assert_highlights(expected, persistent=()):
        state = snapshot(page)
        rendered = {region['id'] for region in state['anatomy']['regions'] if region['rendered']}
        assert rendered == set(expected), f'{dataset}: selection left stale highlights: {rendered}, expected {set(expected)}'
        assert set(state['anatomy']['enabledRegions']) == set(persistent), 'Selecting a landmark changed the persistent checkbox highlights'
        assert state['structures'] == bones, 'Selecting a landmark changed the bones'

    choose(first)
    choose(second)
    assert snapshot(page)['anatomy']['selected'] == {'id': second, 'kind': 'landmark'}
    assert_highlights([second])
    assert changed_pixels(plain, canvas_image(page)) > 100, 'The selected surface was not actually painted'
    page.locator('#anatomy-close').click()
    page.mouse.move(0, 0)
    settle(page)
    assert_highlights([])
    assert changed_pixels(plain, canvas_image(page)) == 0, 'Closing selection left colour on the canvas'

    # Exercise the real canvas click path as well as catalogue buttons. The
    # hit points come from native region triangles, rather than fixed pixels.
    for identity in (first, second, first):
        geometry = page.evaluate('id=>window.skullAtlas.regionGeometry(id)', identity)
        point = visible_point(page, geometry)
        page.mouse.move(point['x'], point['y'])
        page.wait_for_function('id=>window.skullAtlas.snapshot().anatomy.hoveredRegion===id', arg=identity)
        page.mouse.click(point['x'], point['y'])
        page.mouse.move(0, 0)
        settle(page)
        assert snapshot(page)['anatomy']['selected'] == {'id': identity, 'kind': 'landmark'}
        assert_highlights([identity])
    canvas = page.locator('#skull-canvas').bounding_box()
    page.mouse.click(canvas['x'] + 15, canvas['y'] + canvas['height'] - 60)
    page.mouse.move(0, 0)
    settle(page)
    assert snapshot(page)['anatomy']['selected'] is None
    assert_highlights([])

    # An explicit checkbox is independent: changing or closing selection must
    # preserve it, and unchecking it must remove its paint.
    page.locator('#landmark-search').fill('Right canine fossa')
    checkbox = page.locator(f'.landmark-row[data-landmark="{first}"] input')
    checkbox.check()
    choose(second)
    assert_highlights([first, second], [first])
    page.locator('#anatomy-close').click()
    page.mouse.move(0, 0)
    settle(page)
    assert_highlights([first], [first])
    page.locator('#landmark-search').fill('Right canine fossa')
    checkbox.uncheck()
    page.mouse.move(0, 0)
    settle(page)
    assert_highlights([])
    assert changed_pixels(plain, canvas_image(page)) == 0
    capture(page, f'hover-{dataset}-selection-cleared')
    return f'{dataset}: catalogue and canvas clicks replace the selected highlight; close/empty-canvas clicks clear it; explicitly checked regions remain independent'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, help='Read viewer sources from a preserved directory; assets remain native')
    parser.add_argument('--mutant', choices=['fixed-corner', 'global-points', 'drop-overlaps'])
    parser.add_argument('--report', type=lambda name: ARTIFACTS / name, default=ARTIFACTS / 'hover-report.json')
    args = parser.parse_args()
    result = {'result': 'failed', 'checks': [], 'errors': [], 'mutant': args.mutant}

    class Handler(QuietHandler):
        def translate_path(self, path):
            native = Path(super().translate_path(path))
            old = args.snapshot / native.relative_to(ROOT) if args.snapshot else None
            return str(old if old and old.is_file() else native)

        def do_GET(self):
            name = urlsplit(self.path).path.lstrip('/')
            replacement = None
            if args.mutant == 'fixed-corner' and name == 'styles.css':
                replacement = (ROOT / name).read_text() + '\n#hover-label {left:30px!important;top:auto!important;bottom:56px!important;}\n'
            elif args.mutant == 'global-points' and name == 'src/anatomy-details.js':
                original = (ROOT / name).read_text()
                replacement = original.replace('distance <= POINT_HOVER_RADIUS', 'Number.isFinite(distance)')
                assert replacement != original
            elif args.mutant == 'drop-overlaps' and name == 'src/app.js':
                original = (ROOT / name).read_text()
                replacement = original.replace('showHoverTooltip(region, region.overlapping)', 'showHoverTooltip(region, [])')
                assert replacement != original
            if replacement is None:
                return super().do_GET()
            raw = replacement.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/css' if name.endswith('.css') else 'text/javascript')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    try:
        with local_server(Handler) as url, sync_playwright() as playwright:
            chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
            browser = playwright.chromium.launch(executable_path=chrome, headless=True,
                env={key: value for key, value in os.environ.items() if key not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
                args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                page.on('pageerror', lambda error: result['errors'].append(str(error)))
                for dataset in ['ct']:
                    result['checks'].append(selection_check(page, url, dataset))
                    result['checks'].append(bone_check(page, url, dataset))
                    result['checks'].append(point_check(page, url, dataset))
                    result['checks'].append(overlap_check(page, url, dataset))
                page.set_viewport_size({'width': 390, 'height': 844})
                result['checks'].append(bone_check(page, url, 'ct'))
                assert not result['errors'], result['errors']
                result['result'] = 'passed'
            finally:
                if result['result'] != 'passed':
                    page.screenshot(path=str(args.report.with_suffix('.png')))
                browser.close()
    except Exception as error:
        result['failure'] = str(error)
        raise
    finally:
        args.report.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
