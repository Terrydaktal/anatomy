#!/usr/bin/env python3
"""Verify broad-list hover, honest location cues and hidden-region previews."""

import argparse
import json
import math
import os
import shutil
import struct

from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_regions import SourceTriangles, assert_on_triangle, audit_geometry, changed_pixels, select_feature, visible_point
from test_viewer import ARTIFACTS, ROOT, canvas_image, capture, local_server, settle, snapshot


PIN_HOOK = '''const pinUpdate = details.update.bind(details), pinRender=composer.render.bind(composer), pinTimes=[],pinFrames=[];
let pinRecording=false,pinReleasedAt=null,pinRenderCount=0,pinReleaseRender=0;
composer.render=(...args)=>{pinRenderCount++;return pinRender(...args);};
canvas.addEventListener('pointerup',()=>{if(pinRecording){pinReleasedAt=performance.now();pinFrames.length=0;pinReleaseRender=pinRenderCount;}});
window.pinVerification = {
  visible: () => [...details.rows].filter(([id, row]) => !row.pin.hidden).map(([id, row]) => {
    const box = row.pin.getBoundingClientRect(), hit = pickHit({x:box.x+box.width/2,y:box.y+box.height/2});
    return {id, target:details.visibleProjections.get(id)?.world.toArray(),
      hit:hit ? {owner:hit.object.userData.entryId, face:hit.faceIndex,
        point:hit.object.worldToLocal(hit.point.clone()).toArray(), world:hit.point.toArray()} : null};
  }),
  timings: () => {const times=[]; for(let i=0;i<5;i++) {const start=performance.now();details.update();times.push(performance.now()-start);}return times;},
  startTiming: () => {pinTimes.length=0;pinFrames.length=0;pinReleasedAt=null;pinRecording=true;details.update=(...args)=>{
    const start=performance.now();pinUpdate(...args);pinTimes.push(performance.now()-start);
    if(pinReleasedAt!==null)pinFrames.push({ms:performance.now()-pinReleasedAt,ids:[...details.visiblePins],pending:details.preparingPins});
  };},
  stopTiming: () => {details.update=pinUpdate;pinRecording=false;return pinTimes;},
  latency: () => ({samples:pinFrames,renders:pinRenderCount-pinReleaseRender})
};'''


def assert_pins_on_visible_surfaces(page, identities=None):
    metadata = json.loads((ROOT / 'assets/landmark-highlights/manifest.json').read_text())
    regions = {entry['id']: entry for entry in metadata['regions']}
    catalogue = json.loads((ROOT / 'assets/landmarks/catalogue.json').read_text())
    native_points = {entry['id']: entry['positions']['ct']['point'] for entry in catalogue['landmarks']
                     if (entry.get('positions', {}).get('ct') or {}).get('native_point_fit')}
    binary = (ROOT / metadata['binary']['url']).read_bytes()
    pins = page.evaluate('window.pinVerification.visible()')
    camera = snapshot(page)['camera']
    if identities is not None:
        assert {pin['id'] for pin in pins} == set(identities)
    for pin in pins:
        region = regions[pin['id']]
        # A fitted point can sit in a joint gap. Its painted cue is not an
        # anatomical boundary; preserve the canonical point and its sightline.
        if not region['parts'] or pin['id'] in native_points:
            if pin['id'] in native_points:
                assert math.dist(pin['target'], native_points[pin['id']]) < 1e-5, 'A fitted point was moved onto an arbitrary part of its display cue'
            if pin['hit']:
                assert math.dist(camera, pin['target']) <= math.dist(camera, pin['hit']['world']) + 0.03, f'{pin["id"]}: a guide pin is behind bone'
            continue
        assert pin['hit'], f'{pin["id"]}: region pin points outside the skull'
        inside = False
        for part in region['parts']:
            if part['ownerId'] != pin['hit']['owner']:
                continue
            faces = part['sourceFaces']
            values = memoryview(binary)[faces['byte_offset']:faces['byte_offset'] + faces['count'] * 4].cast('I')
            for triangle, face in enumerate(values):
                if face != pin['hit']['face']:
                    continue
                corners = struct.unpack_from('<9f', binary, part['positions']['byte_offset'] + triangle * 36)
                try:
                    assert_on_triangle(pin['hit']['point'], [corners[i:i + 3] for i in [0, 3, 6]])
                    inside = True
                    break
                except AssertionError:
                    pass
            if inside:
                break
        assert inside, f'{pin["id"]}: pin is covered by another surface rather than on the visible highlight'
    return len(pins)


def pin_latency_metrics(page):
    result = page.evaluate('window.pinVerification.latency()')
    samples = result['samples']
    assert samples, 'No pin updates were measured after releasing the real drag'
    final = set(samples[-1]['ids'])
    assert final and not samples[-1]['pending']
    last_change = max((i for i, sample in enumerate(samples) if set(sample['ids']) != final), default=-1) + 1
    last_missing = max((i for i, sample in enumerate(samples) if len(set(sample['ids']) & final) < len(final) * 0.95), default=-1) + 1
    metrics = {'visible': len(final), '95_percent_ms': samples[last_missing]['ms'],
               'last_pin_ms': samples[last_change]['ms'], 'complete_ms': samples[-1]['ms'], 'scene_renders': result['renders']}
    assert metrics['95_percent_ms'] < 150 and metrics['last_pin_ms'] < 250, f'Visible pins still trickle in after rotation: {metrics}'
    assert metrics['scene_renders'] <= 3, f'Background pin batches redraw the skull: {metrics}'
    return metrics


def large_pin_latency_check(page, url, checks):
    page.set_viewport_size({'width': 3840, 'height': 2160})
    load(page, url, '?dataset=ct')
    page.locator('#landmark-pins').check()
    page.locator('#landmark-markers').check()
    page.mouse.move(0, 0)
    settle(page)
    box = page.locator('#skull-canvas').bounding_box()
    x, y = box['x'] + box['width'] - 40, box['y'] + box['height'] * 0.5
    page.mouse.move(x, y)
    page.evaluate('window.pinVerification.startTiming()')
    page.mouse.down()
    for step in range(1, 17):
        page.mouse.move(x - step * 7, y + step * 4)
        page.evaluate('() => new Promise(resolve=>requestAnimationFrame(resolve))')
    page.mouse.up()
    settle(page)
    timings = sorted(page.evaluate('window.pinVerification.stopTiming()'))
    metrics = pin_latency_metrics(page)
    p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
    assert p95 < 20, f'4K pin visibility makes rotation lag: {p95:.1f} ms'
    assert_pins_on_visible_surfaces(page)
    checks.append(f'4K Show all: 95% of visible pins settle in {metrics["95_percent_ms"]:.0f} ms, the last pin in {metrics["last_pin_ms"]:.0f} ms; {metrics["scene_renders"]} scene redraw(s), pin update p95 {p95:.1f} ms, all pins retain independent surface/sightline checks')


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


def pin_visibility_check(page, url, checks):
    load(page, url, '?dataset=ct')
    page.locator('#landmark-panel').evaluate('e=>e.open=true')
    page.locator('[data-view="anterior"]').click()
    page.locator('#landmark-markers').check()
    original = snapshot(page)
    previous_pin = None
    for name in ['Intermaxillary suture', 'Right ramus of mandible']:
        identity = name.lower().replace(' ', '-')
        pin = page.locator(f'.anatomy-pin[data-landmark="{identity}"]')
        page.locator('#landmark-search').fill(name)
        button = page.get_by_role('button', name=name, exact=True)
        button.hover()
        settle(page)
        assert pin.is_visible(), f'{identity}: a visible region has no hover pin'
        if previous_pin:
            assert previous_pin.is_visible(), 'Hovering another landmark lost the selected pin'
        button.click()
        page.mouse.move(0, 0)
        settle(page)
        assert pin.is_visible(), f'{identity}: a visible region has no selected pin'
        if previous_pin:
            assert previous_pin.is_hidden(), 'A replaced temporary selection left a stale pin'
        state = snapshot(page)
        assert not state['anatomy']['enabledRegions'], 'A temporary pin enabled a persistent highlight'
        assert state['camera'] == original['camera'] and state['structures'] == original['structures']
        assert_pins_on_visible_surfaces(page, [identity])
        previous_pin = pin

    page.locator('#anatomy-close').click()
    page.mouse.move(0, 0)
    settle(page)
    assert previous_pin.is_hidden(), 'Closing the selection left its temporary pin'
    names = ['Intermaxillary suture', 'Right ramus of mandible']
    identities = {name.lower().replace(' ', '-') for name in names}
    for name in names:
        identity = name.lower().replace(' ', '-')
        page.locator('#landmark-search').fill(name)
        page.locator(f'.landmark-row[data-landmark="{identity}"] input').check()
    page.mouse.move(0, 0)
    settle(page)
    assert set(snapshot(page)['anatomy']['enabledRegions']) == identities
    for identity in identities:
        pin = page.locator(f'.anatomy-pin[data-landmark="{identity}"]')
        assert pin.is_visible(), f'{identity}: a visible region lost its checked pin'
        assert 'is-occluded' not in pin.get_attribute('class'), 'A visible pin is incorrectly treated as hidden'
    assert_pins_on_visible_surfaces(page, identities)
    capture(page, 'location-visible-region-pins')
    page.locator('.anatomy-pin[data-landmark="intermaxillary-suture"]').click()
    settle(page)
    assert snapshot(page)['anatomy']['selected'] == {'id': 'intermaxillary-suture', 'kind': 'landmark'}, 'A visible pin cannot be selected'
    page.locator('#anatomy-close').click()
    page.mouse.move(0, 0)

    page.locator('#landmark-search').fill('no-matching-landmark')
    page.locator('#landmark-markers').uncheck()
    page.locator('#landmark-labels').check()
    settle(page)
    for identity in identities:
        pin = page.locator(f'.anatomy-pin[data-landmark="{identity}"]')
        assert not pin.evaluate('e=>e.hidden') and pin.locator('span').is_visible(), 'Names-only mode lost a located label'
    page.locator('#landmark-markers').check()
    page.locator('#landmark-xray').uncheck()
    settle(page)
    assert_pins_on_visible_surfaces(page, identities)
    page.locator('#landmark-xray').check()
    for view in ['right', 'left', 'posterior', 'anterior']:
        page.locator(f'[data-view="{view}"]').click()
        page.mouse.move(0, 0)
        settle(page)
        assert_pins_on_visible_surfaces(page)
        if view == 'right':
            assert page.locator('.anatomy-pin[data-landmark="right-ramus-of-mandible"]').is_visible()
        if view == 'anterior':
            assert_pins_on_visible_surfaces(page, identities)
    for name in ['Sella turcica', 'Left pterygoid fossa']:
        identity = name.lower().replace(' ', '-')
        page.locator('#landmark-search').fill(name)
        button = page.get_by_role('button', name=name, exact=True)
        button.hover()
        settle(page)
        assert page.locator(f'.anatomy-pin[data-landmark="{identity}"]').is_hidden(), 'Hovering a covered landmark created an occluded pin'
        button.click()
        page.mouse.move(0, 0)
        settle(page)
        assert page.locator(f'.anatomy-pin[data-landmark="{identity}"]').is_hidden(), 'Selecting a covered landmark created an occluded pin'
        assert page.locator('#landmark-location-aid').is_visible(), 'The separate hidden-landmark locator was lost'
    page.locator('#anatomy-close').click()
    page.mouse.move(0, 0)
    page.locator('.bone-row[data-id="mandible"] input[type="checkbox"]').evaluate(
        'e=>{e.checked=false;e.dispatchEvent(new Event("change",{bubbles:true}));}')
    settle(page)
    assert page.locator('.anatomy-pin[data-landmark="right-ramus-of-mandible"]').is_hidden(), 'A hidden bone retained its pin'
    assert page.locator('.anatomy-pin[data-landmark="intermaxillary-suture"]').is_visible()
    page.locator('.more-controls > summary').click()
    page.locator('#section').select_option('sagittal')
    page.locator('#section-position').fill('100')
    settle(page)
    assert all(page.locator(f'.anatomy-pin[data-landmark="{identity}"]').is_hidden() for identity in identities), 'Clipped pins remain visible'
    page.locator('#reset').click()
    settle(page)
    assert not any(entry['visible'] for entry in snapshot(page)['anatomy']['landmarks'])
    checks.append('Intermaxillary suture and right ramus pins attach to independently checked visible highlight triangles, including view changes; hover/selection stays temporary, covered regions/guides get no pins even with See through bone, and Names, hidden bones, sections and reset work')


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
    actual_pins = {entry['id'] for entry in current['anatomy']['landmarks'] if entry['visible']}
    assert {'intermaxillary-suture', 'right-ramus-of-mandible'} <= actual_pins
    assert 'sella-turcica' not in actual_pins, 'Show all exposed a covered region pin'
    assert len(actual_pins) < len(current['anatomy']['enabledRegions']) / 2, 'Show all produced a cloud of hidden pins'
    checked_pins = assert_pins_on_visible_surfaces(page, actual_pins)
    page.locator('#landmark-labels').uncheck()
    settle(page)
    capture(page, 'location-show-all-visible-pins')
    page.locator('#landmark-labels').check()
    settle(page)
    assert current['anatomy']['locationAid'] is None
    cached_times = page.evaluate('window.pinVerification.timings()')
    assert max(cached_times) < 8, f'Static pin visibility was recomputed on every hover: {cached_times}'
    box = page.locator('#skull-canvas').bounding_box()
    start_x, start_y = box['x'] + box['width'] - 45, box['y'] + box['height'] * 0.45
    page.mouse.move(start_x, start_y)
    camera = snapshot(page)['camera']
    page.evaluate('window.pinVerification.startTiming()')
    page.mouse.down()
    for step in range(1, 17):
        page.mouse.move(start_x - step * 5, start_y + step)
        page.evaluate('() => new Promise(resolve=>requestAnimationFrame(resolve))')
    page.mouse.up()
    settle(page)
    timings = sorted(page.evaluate('window.pinVerification.stopTiming()'))
    assert math.dist(camera, snapshot(page)['camera']) > 1, 'Pin performance check did not rotate the skull'
    p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
    assert p95 < 20, f'Pin visibility makes rotation lag: p95 {p95:.1f} ms, samples {timings}'
    latency = pin_latency_metrics(page)
    assert_pins_on_visible_surfaces(page)
    page.locator('#landmark-search').fill('cranio-orbital')
    page.locator('#landmark-estimates').uncheck()
    settle(page)
    assert page.locator('.landmark-row:not([hidden])').count() == 0
    assert snapshot(page)['anatomy']['selected'] is None
    assert page.locator('#landmark-location-aid').is_hidden()
    checks.append('All 12 orbital-foramen names remain searchable with source links; unlocated entries have null coordinates, no targets/pins/paint, disabled spatial actions and no camera/bone changes, including Show all with Pins and Names enabled')
    checks.append(f'Show all displays {checked_pins} surface-visible pins; every region pin is independently checked against the first native bone hit and the saved clipped highlight triangles')
    checks.append(f'Visibility reuses the static view cache (max {max(cached_times):.1f} ms per update), budgets background surface searches and keeps real rotation pin updates at p95 {p95:.1f} ms')
    checks.append(f'Desktop Show all: 95% of visible pins settle in {latency["95_percent_ms"]:.0f} ms, the last pin in {latency["last_pin_ms"]:.0f} ms; {latency["scene_renders"]} scene redraw(s)')


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
                app = (ROOT / 'src/app.js').read_text()
                page.route('**/src/app.js*', lambda route: route.fulfill(content_type='text/javascript',
                    body=app.replace('  window.skullAtlas = Object.freeze({', PIN_HOOK + '\n  window.skullAtlas = Object.freeze({')))
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
                pin_visibility_check(page, url, report['checks'])
                orbital_foramina_check(page, url, report['checks'])
                large_pin_latency_check(page, url, report['checks'])
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
