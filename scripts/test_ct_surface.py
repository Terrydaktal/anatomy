#!/usr/bin/env python3
"""Check source-faithful CT shading, unchanged anatomy and enclosed wall repairs."""

import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np
from playwright.sync_api import sync_playwright
import trimesh

from test_anatomy import load
from test_regions import select_feature, visible_point
from test_viewer import ROOT, ARTIFACTS, local_server, snapshot, settle, canvas_image


def asset_checks(report):
    manifest = json.loads((ROOT / 'assets/skull-ct/manifest.json').read_text())
    native = trimesh.load(ROOT / manifest['model'], force='scene', process=False).geometry
    surface = manifest['surface']
    assert surface['native_model_sha256'] == manifest['sha256']
    assert hashlib.sha256((ROOT / surface['model']).read_bytes()).hexdigest() == surface['sha256']
    restored = trimesh.load(ROOT / surface['model'], force='scene', process=False).geometry
    assert set(native) == set(restored) and len(restored) == 24
    assert sum(len(mesh.faces) for mesh in restored.values()) == 1_421_276
    for name, mesh in restored.items():
        assert np.array_equal(native[name].triangles, mesh.triangles), f'Restored shading moved or reordered anatomy: {name}'
        assert np.allclose(np.linalg.norm(mesh.vertex_normals, axis=1), 1, atol=2e-6)
    seams = sum(len(mesh.vertices) - len(native[name].vertices) for name, mesh in restored.items())
    assert seams > 0
    report['normal_seam_vertices'] = seams
    report['checks'].append('All 1,421,276 CT triangles retain exact corner positions and face order; authored normal seams are restored')
    associated = json.loads((ROOT / 'assets/skull-associated/manifest.json').read_text())
    pack = trimesh.load(ROOT / associated['model'], force='scene', process=False).geometry
    atlas = trimesh.load(ROOT / 'assets/skull/skull.glb', force='scene', process=False).geometry
    assert set(pack) == {record['id'] for record in associated['structures']} and len(pack) == 7
    for name, mesh in pack.items():
        assert np.array_equal(mesh.triangles, atlas[name].triangles)
        assert np.array_equal(mesh.vertex_normals[mesh.faces], atlas[name].vertex_normals[atlas[name].faces])
    report['checks'].append('Supplementary asset contains only six ossicles and hyoid, with exact original triangles and normals')
    regions = json.loads((ROOT / 'assets/landmark-regions/registered-regions.json').read_text())
    raw = (ROOT / regions['binary']['url']).read_bytes()
    repairs = 0
    for record in regions['regions']:
        if record['dataset'] != 'ct' or record['source_id'] != 'fossafinder': continue
        mesh = native[record['owner']]
        faces = np.frombuffer(raw, '<u4', record['source_faces_count'], record['source_faces_byte_offset'])
        positions = np.frombuffer(raw, '<f4', record['positions_count'], record['positions_byte_offset']).reshape(-1, 3, 3)
        fix = record['fit']['continuity_repair']
        repaired = set(fix['repaired_native_faces'])
        assert fix['native_openings_preserved'] and fix['source_holes_preserved'] and not fix['boundary_expansion']
        original = set(faces.tolist()) - repaired
        painted_area = np.bincount(faces.astype(int), weights=trimesh.triangles.area(positions.astype(float)), minlength=len(mesh.faces))
        neighbors = {face: set() for face in repaired}
        for a, b in mesh.face_adjacency:
            if int(a) in repaired: neighbors[int(a)].add(int(b))
            if int(b) in repaired: neighbors[int(b)].add(int(a))
        for face, adjacent in neighbors.items():
            assert len(adjacent) == 3 and adjacent <= original | repaired, 'A repair expanded onto an open edge or outside the wall'
            assert abs(painted_area[face] - mesh.area_faces[face]) < max(1e-5, mesh.area_faces[face] * .0011)
        repairs += len(repaired)
    assert repairs > 0
    report['repaired_native_faces'] = repairs
    report['checks'].append('Every repaired PPF face is wholly inside its footprint and enclosed by painted native faces; no cap or outer boundary expansion')
    return manifest


def main():
    report = {'result': 'failed', 'checks': [], 'errors': []}
    try:
        manifest = asset_checks(report)
        with local_server() as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which('google-chrome-stable') or shutil.which('chromium'), headless=True,
                env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
                args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 1100})
                requested = []
                page.on('request', lambda request: requested.append(request.url))
                page.on('pageerror', lambda error: report['errors'].append(str(error)))
                # Observe both exports at identical UI-controlled camera poses.
                for original in [True, False]:
                    def source(route):
                        data = dict(manifest)
                        if original: data.pop('surface')
                        route.fulfill(status=200, content_type='application/json', body=json.dumps(data))
                    page.route('**/assets/skull-ct/manifest.json*', source)
                    load(page, url, '?dataset=reference' if original else '')
                    assert snapshot(page)['dataset'] == 'ct' and 'dataset=reference' not in page.url
                    assert page.locator('#dataset option').evaluate_all('values=>values.map(e=>e.value)') == ['ct', 'microct']
                    assert page.locator('#landmark-reference').count() == 0
                    assert len(snapshot(page)['structures']) == 31
                    assert snapshot(page)['anatomy']['landmarkCount'] == 735
                    page.locator('#landmark-estimates').uncheck()
                    page.locator('#search').fill('sphenoid')
                    page.get_by_role('button', name='Isolate and focus sphenoid bone', exact=True).click()
                    page.locator('[data-view="posterior"]').click()
                    page.mouse.move(0, 0); settle(page)
                    canvas_image(page).save(ARTIFACTS / f'ct-surface-{"before" if original else "after"}.png')
                    page.unroute('**/assets/skull-ct/manifest.json', source)
                assert page.locator('#model-download').get_attribute('href') == manifest['surface']['model']
                assert not any('/assets/skull/skull.glb' in item or '/assets/skull/manifest.json' in item for item in requested)
                report['checks'].append('Retired reference links resolve to CT; 29 bones and 735 landmarks remain, with no reference skull network load')
                page.locator('#reset').click()
                page.locator('#landmark-xray').uncheck()
                for side in ['Left', 'Right']:
                    identity = select_feature(page, f'{side} pterygopalatine fossa')
                    page.locator('#anatomy-isolate').click()
                    page.locator('#explode').fill('35')
                    page.locator('#anatomy-focus').click(); settle(page)
                    audit = page.evaluate('id=>window.skullAtlas.regionGeometry(id)', identity)
                    assert {part['owner'] for part in audit['parts']} == {'sphenoid-bone', f'maxilla-{side.lower()}', f'palatine-bone-{side.lower()}'}
                    point = visible_point(page, audit)
                    assert page.evaluate('({x,y})=>window.skullAtlas.regionAt(x,y)', point) == identity
                    page.mouse.move(0, 0)
                    page.screenshot(path=str(ARTIFACTS / f'ct-surface-{side.lower()}-ppf.png'))
                    page.locator('#reset').click()
                report['checks'].append('Both three-bone PPF highlights draw and pick correctly with restored CT geometry and retain estimated-boundary labels')
                assert not report['errors'], report['errors']
            finally:
                browser.close()
        report['result'] = 'passed'
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        (ARTIFACTS / 'ct-surface-report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
