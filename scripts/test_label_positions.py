#!/usr/bin/env python3
"""Independently check label anchors, native definitions and viewer placement."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np
from playwright.sync_api import sync_playwright
import trimesh

from test_regions import SourceTriangles, assert_on_triangle
from audit_landmarks import NativeArrays
from test_anatomy import load
from test_viewer import ARTIFACTS, ROOT, local_server, snapshot, capture, settle


def contour_points(source, owners, normal, offset):
    """Independent plane/edge intersections from original GLB buffers."""
    result = []
    for owner in owners:
        # Decode arrays directly instead of calling the production mesh loader
        # or its trimesh mesh-plane operation.
        primitive = source.meshes[owner]
        vertices = source.array(primitive['attributes']['POSITION'])
        faces = source.array(primitive['indices']).reshape(-1, 3)
        triangles = vertices[faces].astype(float)
        distances = triangles @ normal - offset
        crossing = (distances.min(axis=1) <= 0) & (distances.max(axis=1) >= 0)
        triangles, distances = triangles[crossing], distances[crossing]
        for a, b in [(0, 1), (1, 2), (2, 0)]:
            keep = distances[:, a] * distances[:, b] <= 0
            keep &= np.abs(distances[:, a] - distances[:, b]) > 1e-10
            t = distances[keep, a] / (distances[keep, a] - distances[keep, b])
            result.extend(triangles[keep, a] + t[:, None] * (triangles[keep, b] - triangles[keep, a]))
    return np.array(result)


def asset_checks(catalogue, report):
    checks, failures = [], []
    from refine_label_positions import moved_normal
    matrix = np.array([[2., .7, 0], [0, 1., .4], [0, 0, .6]])
    class Shear:
        def move(self, points):
            return points @ matrix.T + [3., -2., 4.]
    normal = np.array([1., 2., 3.]) / np.sqrt(14)
    transferred = moved_normal(Shear(), np.array([2., 3., 4.]), normal)
    tangents = [np.cross(normal, [0., 0., 1.]), np.cross(normal, [0., 1., 0.])]
    assert all(abs(transferred @ (matrix @ tangent)) < 1e-9 for tangent in tangents), 'Deformed surface normal is not perpendicular to its tangents'
    checks.append('Thin-bone orientation: transported normal remains perpendicular under shear and anisotropic deformation')
    sources = {dataset: NativeArrays(dataset) for dataset in ['ct', 'reference']}
    models = {dataset: json.loads((ROOT / f'assets/{directory}/manifest.json').read_text())
              for dataset, directory in [('ct', 'skull-ct'), ('reference', 'skull')]}
    # Independent triangle-distance engine; production uses Blender BVH.
    meshes = {dataset: trimesh.load(ROOT / model['model'], force='scene', process=False).geometry for dataset, model in models.items()}
    checked, native, surface, space, unlocated = 0, 0, 0, 0, 0
    for entry in catalogue['landmarks']:
        for dataset, placement in entry['positions'].items():
            checked += 1
            identity = f'{dataset}:{entry["id"]}'
            fit = placement.get('label_fit')
            if not fit:
                failures.append(f'{identity}: missing placement evidence')
                continue
            try:
                assert fit['source_model_sha256'] == models[dataset]['sha256'], 'Different specimen'
                assert placement['method'] != 'owner-bounds-map', 'Bounding-box transfer remains active'
                assert fit['owner'] in entry['owners'], 'Anchor attached to unrelated bone'
                assert 'initial_placement' in placement, 'Original provenance lost'
                assert fit['anatomical_accuracy_percent'] is None and not fit['independently_validated']
                if placement.get('location_status') == 'unlocated':
                    assert placement['point'] is None and fit['attachment'] == 'unlocated'
                    assert placement['method'] == fit['status'] == 'unlocated'
                    assert placement['rejected_location']['point'] is not None, 'Rejected candidate provenance lost'
                    unlocated += 1
                    continue
                point = np.array(placement['point'])
                assert np.isfinite(point).all() and len(point) == 3
                if placement.get('native_point_fit') and placement['method'] == 'native-suture-junction':
                    evidence = placement['native_point_fit']
                    for owner in evidence['owner_distances']:
                        distance = trimesh.proximity.closest_point(meshes[dataset][owner], point[None])[1][0]
                        assert distance <= .801, f'Native junction misses {owner}: {distance}'
                    native += 1
                elif fit['attachment'] == 'junction':
                    for owner in fit['owner_distances']:
                        distance = trimesh.proximity.closest_point(meshes[dataset][owner], point[None])[1][0]
                        assert distance <= .801, f'Native contact misses {owner}: {distance}'
                    native += 1
                elif fit['attachment'] == 'surface':
                    assert_on_triangle(point.tolist(), sources[dataset].triangle(fit['owner'], fit['surface_face']))
                    surface += 1
                else:
                    assert fit['attachment'] == 'space'
                    space += 1
                if placement['method'] == 'native-contour-extremum':
                    evidence = placement['native_point_fit']
                    normal = np.array(evidence['symmetry_normal'])
                    offset = evidence['symmetry_offset']
                    assert abs(point @ normal - offset) < .0001, 'Extremum misses specimen sagittal plane'
                    contour = contour_points(sources[dataset], entry['owners'], normal, offset)
                    up, front = np.array(evidence['superior_axis']), np.array(evidence['anterior_axis'])
                    if entry['id'] == 'vertex':
                        assert abs(point @ up - (contour @ up).max()) < .001, 'Vertex is not the superior midline contour point'
                    elif entry['id'] == 'menton':
                        assert abs(point @ up - (contour @ up).min()) < .001, 'Menton is not the inferior midline mandibular point'
                    elif entry['id'] == 'pogonion':
                        span = np.ptp(meshes[dataset]['mandible'].vertices @ up)
                        contour = contour[contour @ up <= (contour @ up).min() + span * .2]
                        assert abs(point @ front - (contour @ front).max()) < .001, 'Pogonion escaped the bony chin'
                if fit.get('round_trip_distance', 0) > 4.5 or fit.get('projection_distance', 0) > 4.5:
                    if fit['basis'] == 'bone-shape-registration':
                        assert fit['status'] == 'uncertain-fit', 'A failed fit is presented without uncertainty'
            except (AssertionError, KeyError, ValueError) as error:
                failures.append(f'{identity}: {error}')
    assert checked == 1435, f'Incomplete catalogue audit: {checked}'
    checks.append(f'{checked} placements: hashes, owning bones, original provenance and explicit uncertainty')
    checks.append(f'{surface} surface anchors checked against original GLB faces; {native} native junction/contact locations; {space} preserved space/cavity guides')
    assert unlocated == 24
    checks.append(f'{unlocated} unlocated orbital entries retain rejected evidence and supply no active coordinate')
    comparison = report['comparison_summary']
    assert comparison['count'] >= 60, 'Insufficient held-out source anchor coverage'
    assert comparison['after_median'] <= comparison['before_median'] * .75, 'Shape registration does not reduce median mismatch by at least 25%'
    assert comparison['after_p95'] <= comparison['before_p95'] * .9, 'Shape registration does not reduce tail mismatch by at least 10%'
    assert comparison['improved'] >= comparison['count'] * .7, 'Most held-out anchors did not improve'
    checks.append('Held-out author anchors: median mismatch improves by at least 25%, p95 by 10%, at least 70% improve')
    return checks, failures


def browser_checks(catalogue, mutant=None):
    chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    errors, checks = [], []
    with local_server() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True,
            env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
            args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
        try:
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            if mutant == 'resnap':
                original = (ROOT / 'src/anatomy-details.js').read_text()
                changed = original.replace("dataset !== 'microct' && !placement.label_fit && !placement.native_point_fit", "dataset !== 'microct'")
                assert changed != original
                page.route('**/src/anatomy-details.js*', lambda route: route.fulfill(status=200, content_type='text/javascript', body=changed))
            for dataset in ['ct']:
                load(page, url, f'?dataset={dataset}')
                state = snapshot(page)
                actual = {entry['id']: entry for entry in state['anatomy']['landmarks']}
                assert state['anatomy']['includeEstimates'], 'Estimates are not enabled by default'
                displaced = []
                for entry in catalogue['landmarks']:
                    placement = entry['positions'].get(dataset)
                    if not placement or 'registered-source-region' in placement['method']:
                        continue
                    if placement.get('location_status') == 'unlocated':
                        assert actual[entry['id']]['point'] is None and not actual[entry['id']]['located']
                        assert page.evaluate('id=>window.skullAtlas.projectLandmark(id)', entry['id']) is None
                        continue
                    difference = np.linalg.norm(np.array(actual[entry['id']]['point']) - placement['point'])
                    if difference >= .0001:
                        displaced.append((float(difference), entry['id']))
                assert not displaced, f'{dataset}: viewer moved saved anchors; largest displacements {sorted(displaced, reverse=True)[:3]}'
                page.locator("#landmark-panel").evaluate("e=>e.open=true")
                page.locator('#landmark-panel').evaluate('element => element.open = true')
                page.locator('#landmark-estimates').check()
                for identity in ['nasion', 'menton', 'vertex']:
                    entry = next(e for e in catalogue['landmarks'] if e['id'] == identity)
                    page.locator('#landmark-search').fill(entry['name'])
                    page.get_by_role('button', name=entry['name'], exact=True).click()
                    settle(page)
                    assert 'point' in page.locator('#anatomy-placement').inner_text().lower()
                    audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
                    assert audit['parts'] and audit['area'] > 0, f'No native point cue: {dataset}:{identity}'
                    for part in audit['parts']:
                        points = np.asarray(part['positions']).reshape(-1, 3)
                        assert np.linalg.norm(points - entry['positions'][dataset]['point'], axis=1).max() < 2.4
                    capture(page, f'label-{dataset}-{identity}')
                if dataset == 'ct':
                    entry = next(e for e in catalogue['landmarks'] if e['positions'].get('ct', {}).get('label_fit', {}).get('status') == 'uncertain-fit')
                    page.locator('#landmark-search').fill(entry['name'])
                    page.get_by_role('button', name=entry['name'], exact=True).hover()
                    page.wait_for_function("document.getElementById('landmark-location-status').textContent.includes('Uncertain location')")
                    capture(page, 'label-ct-uncertain')
                    page.locator('#landmark-search').fill('Left maxillary sinus')
                    page.get_by_role('button', name='Left maxillary sinus', exact=True).hover()
                    page.wait_for_function("document.getElementById('landmark-location-status').textContent.includes('Native cavity location')")
                    capture(page, 'label-ct-native-cavity')
                checks.append(f'{dataset}: saved anchors remain intact in the viewer; native cues draw; estimate-enabled defaults preserved')
            assert not errors, errors
        finally:
            browser.close()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', type=Path, default=ROOT / 'assets/landmarks/catalogue.json')
    parser.add_argument('--skip-browser', action='store_true')
    parser.add_argument('--mutant', choices=['resnap'])
    parser.add_argument('--report', type=Path, default=ARTIFACTS / 'label-positions-report.json')
    args = parser.parse_args()
    result = {'result': 'failed', 'checks': [], 'failures': [], 'anatomical_accuracy_percent': None, 'mutant': args.mutant}
    try:
        catalogue = json.loads(args.catalogue.read_text())
        report_path = args.catalogue.parent / 'label-refinement-report.json'
        report = json.loads((report_path if report_path.exists() else ARTIFACTS / 'label-refinement-report.json').read_text())
        assert report['catalogue_sha256'] == hashlib.sha256(args.catalogue.read_bytes()).hexdigest(), 'Placement report belongs to a different catalogue'
        result['checks'], result['failures'] = asset_checks(catalogue, report)
        assert not result['failures'], '\n'.join(result['failures'][:12])
        if not args.skip_browser:
            result['checks'].extend(browser_checks(catalogue, args.mutant))
        result['result'] = 'passed'
    except Exception as error:
        result['failures'].append(str(error))
        raise
    finally:
        args.report.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
