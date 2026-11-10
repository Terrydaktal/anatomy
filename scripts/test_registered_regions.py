#!/usr/bin/env python3
"""Check transferred geometry, source provenance, region picking and evidence modes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct

import numpy as np
from playwright.sync_api import sync_playwright

from fetch_region_sources import CACHE, verify
from region_registration import Surface, source_mesh
from test_regions import SourceTriangles, assert_on_triangle, select_feature, visible_point
from test_anatomy import load
from test_viewer import ROOT, ARTIFACTS, local_server, snapshot, settle, capture


def asset_checks():
    manifest = json.loads((ROOT / 'assets/landmark-regions/registered-regions.json').read_text())
    raw = (ROOT / manifest['binary']['url']).read_bytes()
    assert len(raw) == manifest['binary']['byte_length']
    assert hashlib.sha256(raw).hexdigest() == manifest['binary']['sha256']
    assert manifest['anatomical_accuracy_percent'] is None
    lock = json.loads((CACHE / 'sources.json').read_text())
    verify(CACHE, lock)
    random = np.random.default_rng(2751)
    # Compare the bounded-memory BVH parity implementation with the independent
    # trimesh/R-tree ray implementation on the real closed source fossa volumes.
    for record in lock['biodigital']['records']:
        if record['role'] != 'region' or 'fossa' not in record['name'].lower():
            continue
        mesh = source_mesh(CACHE, lock, record)
        points = random.uniform(mesh.bounds[0] - 0.1, mesh.bounds[1] + 0.1, (350, 3))
        expected = mesh.contains(points)
        actual = Surface(mesh).contains(points)
        assert np.array_equal(actual, expected), f"Volume membership mismatch: {record['name']}"
    checked = 0
    for dataset in ['ct', 'reference']:
        source = SourceTriangles(dataset)
        model = manifest['models'][dataset]
        assert hashlib.sha256((ROOT / model['model']).read_bytes()).hexdigest() == model['sha256']
        records = [r for r in manifest['regions'] if r['dataset'] == dataset]
        assert not any(r['feature_id'] in {'bregma', 'lambda'} for r in records), 'Junction points must not use independent bone-area masks'
        ids = {r['feature_id'] for r in records}
        for side in ['left', 'right']:
            for name in ['sublingual-fossa', 'submandibular-fossa', 'incisive-fossa-of-maxilla',
                         'canine-fossa', 'fossa-for-lacrimal-sac', 'pterygopalatine-fossa']:
                identity = f'{side}-{name}'
                if identity not in ids:
                    # Whole-region checks now withhold these reference fits;
                    # availability of a source mesh does not establish a fit.
                    assert dataset == 'reference' and identity in {
                        'left-sublingual-fossa', 'right-sublingual-fossa',
                        'left-submandibular-fossa', 'right-submandibular-fossa',
                        'left-incisive-fossa-of-maxilla'}, f'Missing mapped fossa: {dataset}:{identity}'
                    failure = next(r for r in manifest['rejected'] if r['dataset'] == dataset and r.get('feature_id') == identity)
                    assert 'source correspondence p95' in failure['reason'] or 'source footprint' in failure['reason']
            ppf = [r for r in records if r['feature_id'] == f'{side}-pterygopalatine-fossa']
            assert {r['owner'] for r in ppf} == {'sphenoid-bone', f'maxilla-{side}', f'palatine-bone-{side}'}, 'A single surviving wall must not count as a complete three-bone transfer'
            assert {r['fit']['source_wall'] for r in ppf} == {'Anterior_Wall', 'Posterior_Wall', 'Medial_Wall'}
            assert all(r['fit']['before_projection_p95_distance'] <= 3 for r in ppf)
        assert all(step['sampled_minimum_jacobian'] >= 0.1
                   for step in manifest['registrations'][f'{dataset}:fossafinder-midface']['warp_checks'])
        for record in records:
            assert record['approximate'] and not record['independently_validated']
            assert record['anatomical_accuracy_percent'] is None
            assert record['source_model_sha256'] == model['sha256']
            assert record['source_faces_count'] * 9 == record['positions_count'] > 0
            assert record['area'] > 0 and record['source_geometry_sha256']
            if record['source_id'] == 'biodigital':
                assert record['fit']['transfer_representation'] == 'source-bone surface footprint'
                assert record['fit']['projected_source_coverage_fraction'] >= .95
                assert record['fit']['outline_source'].startswith('Source selection constrained')
                assert record['fit']['correspondence'].startswith('Barycentric label pullback')
                assert record['fit']['correspondence_p95_distance'] <= record['fit']['correspondence_fit_limit']
                assert record['fit']['correspondence_fit_limit'] == 4.5
                assert record['fit']['surface_correspondence_version'] == 2
                assert record['fit']['forward_inverse_agreement_distance'] == 1.8
                assert record['fit']['correspondence_fit_surface'].startswith('Transported author selection')
                assert record['fit']['coverage_source'].startswith('Transported source-bone surface before')
                side = record['fit']['surface_side_check']
                if side.get('source_compartment') in {'inner', 'outer'}:
                    assert side['cavity_exposure_check']['source']['exposed_fraction'] >= .95
                    assert side['cavity_exposure_check']['native']['exposed_fraction'] >= .95
                assert 1 <= record['fit']['coverage_distance'] <= 4.5
                assert record['fit']['fit_rejection_scope'] == 'whole region, never individual interior triangles'
            faces = np.frombuffer(raw, '<u4', record['source_faces_count'], record['source_faces_byte_offset'])
            positions = np.frombuffer(raw, '<f4', record['positions_count'], record['positions_byte_offset']).reshape(-1, 3, 3)
            assert np.isfinite(positions).all()
            if record['source_id'] == 'fossafinder':
                # Reject paint leaking onto the back of a thin palatine plate.
                for face in np.unique(faces):
                    triangle = np.asarray(source.triangle(record['owner'], int(face)), dtype=float)
                    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
                    supplied_normal = np.asarray(source.triangle_normals(record['owner'], int(face))).mean(axis=0)
                    if normal @ supplied_normal < 0:
                        normal *= -1
                    normal /= np.linalg.norm(normal)
                    repaired = record['fit'].get('continuity_repair', {}).get('repaired_native_faces', [])
                    if int(face) not in repaired:
                        assert normal @ record['fit']['facing_normal_direction'] > 0.149
            for index in random.choice(len(faces), min(90, len(faces)), replace=False):
                native = source.triangle(record['owner'], int(faces[index]))
                for point in positions[index]:
                    assert_on_triangle(point.tolist(), native)
                    checked += 1
    return manifest, checked


def browser_checks(url):
    chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    checks, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True,
                    env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
                    args=['--disable-extensions', '--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
        page = browser.new_page(viewport={'width': 1500, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            for dataset in ['ct']:
                load(page, url, f'?dataset={dataset}')
                page.locator("#landmark-panel").evaluate("e=>e.open=true")
                assert page.locator('#landmark-estimates').is_checked()
                assert snapshot(page)['anatomy']['registeredRegionCount'] >= 12
                page.locator('#landmark-explore-mapped').click()
                assert page.locator('#landmark-estimates').is_checked()
                assert page.locator('#landmark-mapped-only').is_checked()
                checks.append(f'{dataset}: explicit mapped-region opt-in')
                for name in ['Left canine fossa', 'Left pterygopalatine fossa']:
                    identity = select_feature(page, name)
                    audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
                    assert audit['methods'] == ['fitted:registered-author-region']
                    assert audit['parts'] and all(p['scope'] == 'cross-specimen-source-region' for p in audit['parts'])
                    assert 'estimated' in page.locator('#anatomy-placement').inner_text().lower()
                    assert ('FossaFinder' if 'pterygopalatine' in identity else 'biodigital.com') in page.locator('#anatomy-source').get_attribute('href')
                    assert page.locator('.anatomy-pin:visible').count() == 0
                    if 'pterygopalatine' in identity:
                        assert {p['owner'] for p in audit['parts']} == {'sphenoid-bone', 'maxilla-left', 'palatine-bone-left'}
                        assert 'All three named source walls' in page.locator('#anatomy-placement').inner_text()
                        page.locator('#anatomy-isolate').click()
                        # Open the space between its three owning bones. A
                        # native internal wall may be hidden in assembled view.
                        page.locator('#explode').fill('35')
                        page.locator('#anatomy-focus').click()
                        settle(page)
                        audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
                    point = visible_point(page, audit)
                    page.mouse.move(point['x'], point['y'])
                    page.wait_for_function('id => window.skullAtlas.snapshot().anatomy.hoveredRegion === id', arg=identity)
                    capture(page, f'registered-{dataset}-{identity}')
                    checks.append(f'{dataset}:{identity}: mapped native surface draws and picks')
                    if 'pterygopalatine' in identity:
                        owners = [part['owner'] for part in audit['parts']]
                        for part in audit['parts']:
                            # Exercise real bone-visibility handlers, then find
                            # a view of this internal wall without its neighbours.
                            for owner in owners:
                                page.locator(f'.bone-row[data-id="{owner}"] input[type="checkbox"]').evaluate(
                                    '(element, shown) => { element.checked = shown; element.dispatchEvent(new Event("change", {bubbles:true})); }', owner == part['owner'])
                            for view in ['left', 'right', 'anterior', 'posterior', 'superior', 'inferior']:
                                page.locator(f'[data-view="{view}"]').click()
                                settle(page)
                                try:
                                    point = visible_point(page, {**audit, 'parts': [part]})
                                    break
                                except AssertionError:
                                    continue
                            else:
                                raise AssertionError(f"No pickable view of the {dataset}:{part['owner']} wall")
                            page.mouse.move(point['x'], point['y'])
                            page.wait_for_function('id => window.skullAtlas.snapshot().anatomy.hoveredRegion === id', arg=identity)
                            capture(page, f'registered-{dataset}-{identity}-{part["owner"]}')
                            checks.append(f'{dataset}:{part["owner"]}: individual PPF wall visible and pickable')
                    page.locator('#landmark-search').fill('')
                page.locator('#landmark-estimates').uncheck()
                settle(page)
                assert not page.locator('#landmark-mapped-only').is_checked()
                assert not any(r['rendered'] for r in snapshot(page)['anatomy']['regions'] if 'fitted:registered-author-region' in r['methods'])
                checks.append(f'{dataset}: source-only mode removes mapped highlights')
                page.locator('#reset').click()
                assert page.locator('#landmark-estimates').is_checked()
                assert not snapshot(page)['anatomy']['mappedOnly']
            assert not errors, errors
        finally:
            browser.close()
    return checks


def main():
    manifest, vertices = asset_checks()
    with local_server() as url:
        checks = browser_checks(url)
    report = {'checks': checks, 'native_vertices_independently_checked': vertices,
              'parts': len(manifest['regions']), 'anatomical_accuracy_percent': None}
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / 'registered-regions-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
