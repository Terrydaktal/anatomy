#!/usr/bin/env python3
"""Verify the combined catalogue, associated bones, context menu and explosion."""

import argparse
import json
import math
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_viewer import ARTIFACTS, ROOT, QuietHandler, by_id, capture, local_server, settle, snapshot


def unified_catalogue_checks(page):
    assert page.locator('input[type="search"]').count() == 2
    assert page.get_by_role('searchbox', name='Find a bone', exact=True).count() == 1
    assert page.get_by_role('searchbox', name='Find a landmark', exact=True).count() == 1
    assert page.locator('#search').bounding_box()['y'] < page.locator('#landmark-search').bounding_box()['y']
    assert page.locator('#catalogue select').count() == 0, 'Landmarks still use dropdown filters'
    assert page.locator('#catalogue-total').inner_text() == '768'
    assert page.locator('.landmark-level-1').count() == 13
    assert page.locator('.landmark-row').evaluate_all('rows => new Set(rows.map(row => row.dataset.landmark)).size') == 737
    assert page.locator('.sidebar-scroll').evaluate('node => [...node.children].map(child => child.id || child.className)') == ['bone-catalogue', 'about', 'display-controls', 'dataset-control']
    assert page.locator('.about #landmark-note, .about #landmark-coverage').count() == 2
    assert page.locator('#bone-catalogue #landmark-note, #bone-catalogue #landmark-coverage').count() == 0

    category = page.locator('[data-landmark-group="foramina"]')
    owner = page.locator('[data-landmark-group="foramina:maxilla-left"]')
    category.locator(':scope > summary .group-label').click()
    owner.locator(':scope > summary .group-label').click()
    assert owner.locator('.landmark-row:visible').count() > 0
    capture(page, 'catalogue-type-and-bone')
    page.locator('#search').fill('sphenoid')
    assert page.locator('.bone-row:visible').count() == 1
    assert page.locator('.landmark-row:not([hidden])').count() == 737, 'Bone search filtered landmarks'
    assert not page.locator('[data-landmark-group="sutures"]').evaluate('node => node.open'), 'Bone search expanded unrelated landmark groups'
    page.locator('#landmark-search').fill('sphenoid')
    assert page.locator('.landmark-row:visible').count() > 24
    assert page.locator('.landmark-row:visible').count() == page.locator('.landmark-row:not([hidden])').count(), 'Search left matches inside collapsed branches'
    assert '1 structure' in page.locator('#search-results').inner_text()
    capture(page, 'catalogue-separate-searches')
    page.locator('#search').fill('')
    assert page.locator('.bone-row:not([hidden])').count() == 31
    assert page.locator('.landmark-row:not([hidden])').count() < 737, 'Clearing bone search cleared landmark search'
    page.locator('#landmark-search').fill('')
    assert category.evaluate('node => node.open') and owner.evaluate('node => node.open'), 'Search lost the manually expanded branch'
    assert not page.locator('[data-landmark-group="sutures"]').evaluate('node => node.open'), 'Clearing search left unrelated categories expanded'

    page.locator('#landmark-search').fill('Left pterygopalatine fossa')
    matches = set(page.locator('.landmark-row:not([hidden])').evaluate_all('rows => rows.map(row => row.dataset.landmark)'))
    assert matches and page.locator('.bone-row:not([hidden])').count() == 31, 'Landmark search filtered bones'
    group = page.locator('.landmark-level-1:not([hidden])').first
    group_matches = set(group.locator('.landmark-row:not([hidden])').evaluate_all('rows => rows.map(row => row.dataset.landmark)'))
    group_checkbox = group.locator(':scope > summary input')
    group_checkbox.check()
    assert set(snapshot(page)['anatomy']['markers']) == group_matches
    assert page.locator('#landmark-pins').evaluate('node => node.indeterminate')
    assert all(entry['visible'] for entry in by_id(page).values()), 'Landmark group selection altered bone visibility'
    group_checkbox.uncheck()
    assert not snapshot(page)['anatomy']['markers']
    page.locator('#landmark-pins').check()
    assert set(snapshot(page)['anatomy']['markers']) == matches
    page.locator('#search').fill('sphenoid')
    page.locator('#hide-all').click()
    assert set(snapshot(page)['anatomy']['markers']) == matches, 'Bone visibility action cleared landmark highlights'
    assert not by_id(page)['sphenoid-bone']['visible']
    assert all(entry['visible'] for key, entry in by_id(page).items() if key != 'sphenoid-bone'), 'Hide matches hid a nonmatching bone'
    page.locator('#show-all').click()
    assert all(entry['visible'] for entry in by_id(page).values())
    page.locator('#landmark-pins').uncheck()
    assert not snapshot(page)['anatomy']['markers']
    sphenoid_checkbox = page.get_by_role('checkbox', name='Show sphenoid bone', exact=True)
    sphenoid_checkbox.uncheck()
    hidden_view = {key: (entry['visible'], entry['rendered']) for key, entry in by_id(page).items()}
    for name, identity in [('Right pterygopalatine fossa', 'right-pterygopalatine-fossa'), ('Left pterygopalatine fossa', 'left-pterygopalatine-fossa'), ('Sella turcica', 'sella-turcica')]:
        page.locator('#landmark-search').fill(name)
        page.get_by_role('button', name=name, exact=True).click()
        settle(page)
        assert snapshot(page)['anatomy']['selected'] == {'id': identity, 'kind': 'landmark'}
        assert {key: (entry['visible'], entry['rendered']) for key, entry in by_id(page).items()} == hidden_view, f'Selecting {name} changed bone visibility'
        assert not sphenoid_checkbox.is_checked(), f'Selecting {name} enabled a hidden owner checkbox'
    page.locator('.bone-row[data-id="sphenoid-bone"] .bone-name').click()
    assert snapshot(page)['selected'] == 'sphenoid-bone'
    assert {key: (entry['visible'], entry['rendered']) for key, entry in by_id(page).items()} == hidden_view, 'Selecting a hidden bone showed it'
    page.locator('#clear-selection').click()
    page.locator('#landmark-search').fill('Right pterygopalatine fossa')
    page.get_by_role('button', name='Right pterygopalatine fossa', exact=True).click()
    page.locator('#anatomy-isolate').click()
    assert by_id(page)['sphenoid-bone']['visible'], 'Explicit Reveal no longer shows the landmark owners'
    page.locator('#anatomy-restore').click()
    settle(page)
    assert {key: (entry['visible'], entry['rendered']) for key, entry in by_id(page).items()} == hidden_view, 'Restore lost the visibility choices made before selecting the landmark'
    page.locator('#landmark-clear').click()
    sphenoid_checkbox.check()
    page.locator('#landmark-search').fill('Left pterygopalatine fossa')
    page.locator('#search').fill('unmatchable-catalogue-query')
    assert page.locator('#no-results').is_visible()
    assert page.locator('#show-all').is_disabled() and page.locator('#hide-all').is_disabled()
    assert page.locator('.landmark-row:not([hidden])').count() == len(matches)
    page.locator('#landmark-search').fill('unmatchable-landmark-query')
    assert page.locator('#landmark-no-results').is_visible()
    page.locator('#search').fill('')
    assert page.locator('#show-all').is_enabled() and page.locator('.landmark-row:not([hidden])').count() == 0
    page.locator('#landmark-search').fill('')

    page.locator('.about > summary').click()
    about = page.locator('.about').inner_text()
    for text in ['OUVC 10503', '29 bones', '737 landmark', '1,421,276', '625 µm', 'unvalidated', '75 source regions', '10 point landmarks', 'BioDigital', 'FossaFinder', 'display units', 'Download CT skull']:
        assert text in about, f'About this model omits {text}'
    page.locator('#model-catalogue-credits').scroll_into_view_if_needed()
    capture(page, 'catalogue-about-sources')
    page.locator('.about > summary').click()
    page.locator('#dataset').scroll_into_view_if_needed()
    capture(page, 'catalogue-bottom-controls')
    page.locator('#reset').click()


def mobile_and_source_checks(page, url):
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#search').fill('maxilla')
    page.locator('#landmark-search').fill('Left pterygopalatine fossa')
    assert page.locator('.bone-row:not([hidden])').count() == 2
    page.get_by_role('button', name='Left pterygopalatine fossa', exact=True).click()
    assert snapshot(page)['anatomy']['selected'] == {'id': 'left-pterygopalatine-fossa', 'kind': 'landmark'}
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Catalogue overflows on mobile'
    capture(page, 'catalogue-mobile-search')
    with page.expect_navigation(wait_until='networkidle'):
        page.locator('#dataset').select_option('microct')
    page.wait_for_function("document.documentElement.dataset.ready === 'true'", timeout=60000)
    assert snapshot(page)['dataset'] == 'microct'
    page.locator('.about > summary').click()
    about = page.locator('.about').inner_text()
    for text in ['separate donor', '776,546', '0.125 mm', '3 layer location guides']:
        assert text in about, f'Micro-CT About omits {text}'
    assert page.locator('#model-cavity-info').is_hidden()
    assert page.locator('#model-catalogue-credits').is_hidden()
    page.locator('#landmark-search').fill('Diploë')
    page.get_by_role('button', name='Diploë', exact=True).click()
    assert snapshot(page)['anatomy']['selected'] == {'id': 'diploe', 'kind': 'landmark'}
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    return 'Mobile independent bone and landmark searches, selection, reachable bottom source selector and accurate micro-CT About information'


def check(page, url, dataset):
    load(page, url, f'?dataset={dataset}')
    assert page.locator('#bone-catalogue').is_visible() and page.locator('#bone-studies').is_visible(), 'Bones and landmarks still require separate catalogue views'
    assert page.locator('.anatomy-tabs').count() == 0
    assert page.locator('#landmark-estimates').is_checked()
    assert page.locator('.landmark-row:not([hidden])').count() == 737
    assert page.locator('a[href*="study=ossicles"],a[href*="study=hyoid"]').count() == 0
    assert page.locator('.bone-row[data-id="frontal-bone"]').bounding_box()['height'] <= 26
    unified_catalogue_checks(page)
    initial = snapshot(page)
    bones = by_id(page)
    associated = {f'{name}-{side}' for name in ['malleus', 'incus', 'stapes'] for side in ['left', 'right']} | {'hyoid-bone'}
    assert associated.issubset(bones) and all(bones[k]['visible'] and bones[k]['rendered'] for k in associated)
    assert len([e for e in initial['structures'] if e['group'] != 'teeth']) == 29
    assert len({e['color'] for e in initial['structures'] if e['group'] != 'teeth'}) == 29
    for side, sign in [('left', 1), ('right', -1)]:
        center = [sum(sum(bones[f'{name}-{side}']['bounds'][end][axis] for end in [0, 1]) / 2 for name in ['malleus', 'incus', 'stapes']) / 3 for axis in range(3)]
        assert sign * center[0] > 25, f'{dataset}: ossicles are still rearranged into a display pair'
        host = bones[f'temporal-bone-{side}']['bounds']
        assert all(host[0][i] <= center[i] <= host[1][i] for i in range(3)), 'Chain is outside its temporal bone frame'
    assert bones['hyoid-bone']['bounds'][1][1] < bones['mandible']['bounds'][1][1], 'Hyoid is not inferior to the jaw'
    for value in ['35', '75', '100', '0']:
        page.locator('#explode').fill(value)
        settle(page)
        moved = snapshot(page)
        assert math.dist(initial['camera'], moved['camera']) < 1e-6, 'Exploding automatically zoomed out'
        assert math.dist(initial['target'], moved['target']) < 1e-6 and moved['viewOffset'] == initial['viewOffset']
        actual = by_id(page)
        for identity, original in bones.items():
            for axis in range(3):
                before = original['bounds'][1][axis] - original['bounds'][0][axis]
                after = actual[identity]['bounds'][1][axis] - actual[identity]['bounds'][0][axis]
                assert abs(before - after) < .00001, f'Explosion scaled {identity}'
            assert actual[identity]['triangles'] == original['triangles']
        if value == '35':
            capture(page, f'catalogue-{dataset}-exploded')
    page.locator('[data-view="anterior"]').click()
    point = page.evaluate('window.skullAtlas.project("frontal-bone")')
    page.mouse.click(point['x'], point['y'], button='right')
    page.wait_for_function('!document.getElementById("bone-context-menu").hidden')
    menu = page.locator('#bone-context-menu').bounding_box()
    assert math.hypot(menu['x'] - point['x'], menu['y'] - point['y']) < 35
    page.mouse.move(point['x'] - 10, point['y'] + 5)
    settle(page)
    assert page.locator('#bone-context-menu').is_visible(), 'Moving the pointer after right-click dismissed the menu as a pan'
    page.screenshot(path=str(ARTIFACTS / f'catalogue-{dataset}-context-menu.png'))
    page.get_by_role('menuitem', name='Hide frontal bone', exact=True).click()
    assert not by_id(page)['frontal-bone']['visible']
    assert all(e['visible'] for k, e in by_id(page).items() if k != 'frontal-bone')
    assert not page.get_by_role('checkbox', name='Show frontal bone', exact=True).is_checked()
    page.get_by_role('checkbox', name='Show frontal bone', exact=True).check()
    page.mouse.click(point['x'], point['y'], button='right')
    page.keyboard.press('Escape')
    assert page.locator('#bone-context-menu').is_hidden()
    before = snapshot(page)
    page.mouse.move(point['x'], point['y'])
    page.mouse.down(button='right')
    page.mouse.move(point['x'] + 70, point['y'] + 25, steps=10)
    page.mouse.up(button='right')
    settle(page)
    assert page.locator('#bone-context-menu').is_hidden(), 'Right-drag pan opened the hide menu'
    assert math.dist(before['target'], snapshot(page)['target']) > 1, 'Right-drag no longer pans'
    assert all(e['visible'] for e in by_id(page).values())
    page.locator('#landmark-estimates').uncheck()
    assert page.locator('.landmark-row:not([hidden])').count() == 10
    assert page.locator('#catalogue-total').inner_text() == '41 / 768'
    page.locator('#reset').click()
    assert page.locator('#landmark-estimates').is_checked()
    assert all(by_id(page)[k]['visible'] for k in associated)
    assert page.locator('#search').input_value() == '' and page.locator('#landmark-search').input_value() == ''
    return f'{dataset}: separate bone/landmark searches in one catalogue, independent empty results and visibility actions, restored expansion, nested type/bone groups, model About, 737 landmarks, 29 bones, pointer Hide menu, pan and translation-only explosion'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--mutant', choices=['refit'])
    parser.add_argument('--report', type=lambda name: ARTIFACTS / name, default=ARTIFACTS / 'catalogue-controls-report.json')
    args = parser.parse_args()
    result = {'result': 'failed', 'checks': [], 'errors': [], 'mutant': args.mutant}

    class Handler(QuietHandler):
        def translate_path(self, path):
            native = Path(super().translate_path(path))
            original = args.snapshot / native.relative_to(ROOT) if args.snapshot else None
            return str(original if original and original.is_file() else native)

        def do_GET(self):
            if args.mutant == 'refit' and urlsplit(self.path).path == '/src/app.js':
                original = (ROOT / 'src/app.js').read_text()
                changed = original.replace("if (key === 'jaw') fitVisible();", 'fitVisible();')
                assert original != changed
                raw = changed.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/javascript')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
                return
            super().do_GET()

    try:
        with local_server(Handler) as url, sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium'), headless=True,
                env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
                args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                page.on('pageerror', lambda error: result['errors'].append(str(error)))
                for dataset in ['ct']:
                    result['checks'].append(check(page, url, dataset))
                result['checks'].append(mobile_and_source_checks(page, url))
                assert not result['errors'], result['errors']
                result['result'] = 'passed'
            finally:
                if result['result'] != 'passed':
                    page.screenshot(path=str(args.report.with_suffix('.png')), full_page=True)
                browser.close()
    except Exception as error:
        result['failure'] = str(error)
        raise
    finally:
        args.report.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
