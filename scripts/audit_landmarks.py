#!/usr/bin/env python3
"""Audit every catalogue placement and every exported native highlight vertex.

Geometry and source coverage are reported separately from anatomical validation.
--package can replay a preserved JSON/binary pair without changing viewer assets.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

import numpy as np
from playwright.sync_api import sync_playwright
import trimesh
from trimesh.ray.ray_pyembree import RayMeshIntersector

from test_anatomy import load
from test_regions import SourceTriangles, select_feature, visible_point
from test_viewer import ROOT, ARTIFACTS, capture, local_server, settle


VAULT = ('frontal-bone', 'parietal-bone-left', 'parietal-bone-right', 'occipital-bone')


class NativeArrays(SourceTriangles):
    """Read original GLB accessors; never use the mapper's imported geometry."""
    def array(self, index):
        accessor, view, offset = self.accessor(index)
        width = {'SCALAR': 1, 'VEC3': 3}[accessor['type']]
        dtype = np.dtype({5123: '<u2', 5125: '<u4', 5126: '<f4'}[accessor['componentType']])
        return np.ndarray((accessor['count'], width), dtype=dtype, buffer=self.buffer,
                          offset=offset, strides=(view.get('byteStride', width * dtype.itemsize), dtype.itemsize))

    def bone(self, owner):
        mesh = self.meshes[owner]
        positions = self.array(mesh['attributes']['POSITION']).astype(float)
        normals = self.array(mesh['attributes']['NORMAL']).astype(float)
        faces = self.array(mesh['indices']).reshape(-1, 3)
        triangles = positions[faces]
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        area = np.linalg.norm(cross, axis=1) / 2
        normal = cross / np.maximum(2 * area[:, None], 1e-20)
        normal[np.sum(normal * normals[faces].mean(axis=1), axis=1) < 0] *= -1
        return triangles, normal, area


def binary(manifest):
    raw = (ROOT / manifest['binary']['url']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest['binary']['sha256']
    return raw


def membership_error(positions, parents):
    a, ab, ac = parents[:, 0], parents[:, 1] - parents[:, 0], parents[:, 2] - parents[:, 0]
    normal = np.cross(ab, ac)
    length = np.linalg.norm(normal, axis=1)
    relative = positions - a[:, None]
    plane = np.abs(np.sum(relative * normal[:, None], axis=2)) / np.maximum(length[:, None], 1e-20)
    aa, bb, cc = np.sum(ab * ab, axis=1), np.sum(ab * ac, axis=1), np.sum(ac * ac, axis=1)
    pa, pc = np.sum(relative * ab[:, None], axis=2), np.sum(relative * ac[:, None], axis=2)
    denominator = np.maximum(aa * cc - bb * bb, 1e-20)[:, None]
    u, v = (cc[:, None] * pa - bb[:, None] * pc) / denominator, (aa[:, None] * pc - bb[:, None] * pa) / denominator
    return float(plane.max()), float(np.minimum(np.minimum(u, v), 1 - u - v).min())


def cortical_exposure(triangles, native_triangles, center, intersector, compartment):
    # Independent GLB geometry, Embree ray engine and area/barycentric
    # samples. The producer uses Blender's BVH and a different sampling seed.
    rng = np.random.default_rng(95174)
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
    samples = triangles[rng.choice(len(triangles), 500, p=areas / areas.sum())]
    uv = rng.random((len(samples), 2))
    root = np.sqrt(uv[:, 0])[:, None]
    points = samples[:, 0] * (1 - root) + root * ((1 - uv[:, 1, None]) * samples[:, 1] + uv[:, 1, None] * samples[:, 2])
    span = float(np.ptp(native_triangles.reshape(-1, 3), axis=0).max())
    offsets = np.vstack([np.zeros(3), np.eye(3) * span * 0.15, -np.eye(3) * span * 0.15])
    origins = np.tile(center, (len(points), 1))
    if compartment == 'outer':
        radial = points - center
        radial /= np.linalg.norm(radial, axis=1)[:, None]
        origins = center + radial * span * 3
    depth = np.full(len(points), np.inf)
    for offset in offsets:
        viewpoint = origins + offset
        direction = points - viewpoint
        distance = np.linalg.norm(direction, axis=1)
        # Bound query batches without changing samples or depth tolerances.
        for first in range(0, len(points), 32):
            last = first + 32
            locations, rays, _ = intersector.intersects_location(viewpoint[first:last], direction[first:last] / distance[first:last, None], multiple_hits=False)
            rays += first
            depth[rays] = np.minimum(depth[rays], np.abs(distance[rays] - np.linalg.norm(locations - viewpoint[rays], axis=1)))
    return {'exposed_fraction': float(np.mean(depth <= span * 0.0002)),
            'median_burial_depth': float(np.median(depth)), 'sample_count': len(points),
            'viewpoint_count': len(offsets), 'depth_tolerance': span * 0.0002, 'compartment': compartment}


def geometry_checks(manifest, raw, archive=False):
    failures, results, models = [], [], {}
    openings = json.loads((ROOT / 'assets/landmark-regions/apertures.json').read_text())
    opening_raw = binary(openings)
    for dataset in ['ct', 'reference']:
        exposure_trees = {}
        model = json.loads((ROOT / f'assets/{"skull-ct" if dataset == "ct" else "skull"}/manifest.json').read_text())
        assert hashlib.sha256((ROOT / model['model']).read_bytes()).hexdigest() == model['sha256'] == manifest['models'][dataset]['sha256']
        source = NativeArrays(dataset)
        models[dataset] = bones = {owner: source.bone(owner) for owner in source.meshes}
        center = np.average([np.average(bones[owner][0].mean(axis=1), axis=0, weights=bones[owner][2]) for owner in VAULT],
                            axis=0, weights=[bones[owner][2].sum() for owner in VAULT])
        domains = [(r, raw) for r in manifest['regions'] if r['dataset'] == dataset]
        domains += [(dict(r, source_id='native-opening'), opening_raw) for r in openings['regions'] if r['dataset'] == dataset]
        for record, domain_raw in domains:
            identity = f"{dataset}:{record['feature_id']}:{record['owner']}"
            positions = np.frombuffer(domain_raw, '<f4', record['positions_count'], record['positions_byte_offset']).reshape(-1, 3, 3).astype(float)
            faces = np.frombuffer(domain_raw, '<u4', record['source_faces_count'], record['source_faces_byte_offset'])
            native, normals, _ = bones[record['owner']]
            assert len(positions) == len(faces) and np.isfinite(positions).all() and faces.max() < len(native)
            plane, bary = membership_error(positions, native[faces])
            row = {'id': identity, 'vertices_checked': len(positions) * 3, 'maximum_plane_error': plane, 'minimum_barycentric_coordinate': bary}
            if plane > 5e-5 or bary < -0.003:
                failures.append(f'{identity}: highlight leaves native triangles: {plane=}, {bary=}')
            if record['owner'] in VAULT:
                radial = positions.mean(axis=1) - center
                radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
                alignment = np.sum(normals[faces] * radial, axis=1)
                area = np.linalg.norm(np.cross(positions[:, 1] - positions[:, 0], positions[:, 2] - positions[:, 0]), axis=1) / 2
                side = record['fit'].get('surface_side_check', {}).get('source_compartment')
                # Terminology supplies a separate oracle for named inner/outer
                # vault surfaces; it does not depend on the mapper's side flag.
                if re.search(r'internal|cerebral|meningeal|sagittal-sinus|transverse-sinus', record['feature_id']):
                    side = 'inner'
                elif re.search(r'external|nuchal-line', record['feature_id']):
                    side = 'outer'
                if side in {'inner', 'outer'}:
                    wrong = alignment > 0.1 if side == 'inner' else alignment < -0.1
                    spill = float(area[wrong].sum() / area.sum())
                    row.update(expected_compartment=side, opposite_surface_area_fraction=spill)
                    if spill > 0.01:
                        failures.append(f'{identity}: {spill:.1%} of area is on the opposite cranial surface')
                    if record['owner'] not in exposure_trees:
                        carrier = trimesh.Trimesh(native.reshape(-1, 3), np.arange(native.size // 3).reshape(-1, 3), process=False)
                        exposure_trees[record['owner']] = RayMeshIntersector(carrier)
                    print(f'Checking exposed cortex: {identity} ({side})', flush=True)
                    exposed = cortical_exposure(positions, native, center, exposure_trees[record['owner']], side)
                    row['independent_cortical_exposure'] = exposed
                    if exposed['exposed_fraction'] < 0.95:
                        failures.append(f"{identity}: only {exposed['exposed_fraction']:.1%} of the {side} transfer reaches its exposed cortex; buried-surface transfer")
            if not archive and record['source_id'] == 'biodigital':
                fit = record['fit']
                if fit.get('surface_correspondence_version') != 2 or 'surface_side_check' not in fit:
                    failures.append(f'{identity}: missing orientation/compartment checks')
                if fit['correspondence_p95_distance'] > 4.5:
                    failures.append(f'{identity}: source region proximity exceeds 4.5 display units')
            results.append(row)
    authored = json.loads((ROOT / 'assets/landmark-regions/source-regions.json').read_text())
    authored_raw = binary(authored)
    assert authored['source']['model_sha256'] == manifest['models']['reference']['sha256']
    for domain in authored['regions']:
        if not domain['feature_ids']:
            continue
        faces = np.frombuffer(authored_raw, '<u4', domain['count'], domain['byte_offset'])
        assert len(faces) and faces.max() < len(models['reference'][domain['owner']][0])
        results.append({'id': f"reference:source-face-selection:{domain['id']}", 'vertices_checked': len(faces) * 3,
                        'maximum_plane_error': 0.0, 'minimum_barycentric_coordinate': 0.0})
    return results, failures, models


def catalogue_inventory(manifest, models):
    catalogue = json.loads((ROOT / 'assets/landmarks/catalogue.json').read_text())['landmarks']
    authored = json.loads((ROOT / 'assets/landmark-regions/source-regions.json').read_text())
    supplied = {identity for domain in authored['regions'] for identity in domain['feature_ids']}
    openings = {(r['dataset'], r['feature_id']) for r in json.loads((ROOT / 'assets/landmark-regions/apertures.json').read_text())['regions']}
    mapped = defaultdict(list)
    for record in manifest['regions']:
        mapped[(record['dataset'], record['feature_id'])].append(record)
    rejected = defaultdict(list)
    for record in manifest['rejected']:
        rejected[(record['dataset'], record.get('feature_id'))].append(record['reason'])
    rows = []
    for dataset in ['ct', 'reference']:
        for entry in catalogue:
            placement = entry.get('positions', {}).get(dataset)
            if not placement or not set(entry['owners']).intersection(models[dataset]):
                continue
            key = dataset, entry['id']
            kind = ('unlocated' if placement.get('location_status') == 'unlocated' else
                    'source-face-selection' if dataset == 'reference' and entry['id'] in supplied else
                    'native-point-fit' if placement.get('native_point_fit') else
                    'native-opening-fit' if key in openings else
                    'location-guide' if entry['category'] == 'landmarks' else
                    'registered-estimate' if key in mapped else
                    'contact-estimate' if entry['category'] == 'sutures' and len(entry['owners']) > 1 else
                    'withheld' if key in rejected else 'location-guide')
            rows.append({'dataset': dataset, 'id': entry['id'], 'name': entry['name'], 'kind': kind,
                         'owners': entry['owners'], 'source_url': entry.get('source_url'),
                         'anatomical_accuracy_percent': None, 'independently_validated': False,
                         'rejection_reasons': rejected.get(key, [])})
    micro = json.loads((ROOT / 'assets/bone-microct/manifest.json').read_text())
    for entry in micro['landmarks']:
        rows.append({'dataset': 'microct', 'id': entry['id'], 'name': entry['name'], 'kind': 'location-guide',
                     'owners': entry['boneIds'], 'source_url': micro['source']['url'],
                     'anatomical_accuracy_percent': None, 'independently_validated': False, 'rejection_reasons': []})
    return rows


def runtime_failures(row, result):
    identity = f"{row['dataset']}:{row['id']}"
    failures = []
    if row['kind'] in {'unlocated', 'location-guide', 'withheld'} and result['area']:
        failures.append(f'{identity}: an unsupported location was painted as an anatomical area')
    if row['kind'] in {'source-face-selection', 'native-point-fit', 'native-opening-fit', 'registered-estimate'} and not result['area']:
        failures.append(f'{identity}: an accepted region produced no geometry')
    if not set(result['owners']).issubset(row['owners']):
        failures.append(f'{identity}: painted a different bone')
    return failures


def browser_checks(rows):
    # Test-only access to the actual production builders. The shipped app gains
    # no diagnostic branch and the supported journeys below use its normal UI.
    app = (ROOT / 'src/app.js').read_text()
    hook = '''window.landmarkAudit = async (id) => {
      const entry = details.landmarks.get(id), cached = details.regions.cache.has(id);
      const region = details.hasSurface(entry) ? await details.regions.prepare(entry) : details.regions.empty(entry);
      const result = {area:region.area, methods:region.methods, owners:region.parts.map(p=>p.ownerId)};
      if (!cached && !details.enabled.has(id)) {
        for (const part of region.parts) { scene.remove(part.mesh); part.geometry.dispose(); part.mesh.material.dispose(); part.ghost.material.dispose(); }
        details.regions.cache.delete(id);
      }
      return result;
    };
    window.skullAtlas = Object.freeze({'''
    assert app.count('window.skullAtlas = Object.freeze({') == 1
    app = app.replace('window.skullAtlas = Object.freeze({', hook)
    chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    failures, errors = [], []
    with local_server() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True,
            env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
            args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
        try:
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            page.add_init_script('globalThis.__SKULL_BUILD_HIGHLIGHTS__ = true;')
            page.route('**/src/app.js*', lambda route: route.fulfill(body=app, content_type='text/javascript'))
            page.on('pageerror', lambda error: errors.append(str(error)))
            for dataset in ['ct', 'reference', 'microct']:
                load(page, url, f'?dataset={dataset}')
                for row in [row for row in rows if row['dataset'] == dataset]:
                    result = page.evaluate('id => window.landmarkAudit(id)', row['id'])
                    row['runtime'] = result
                    failures.extend(runtime_failures(row, result))
                if dataset == 'microct':
                    continue
                page.locator("#landmark-panel").evaluate("e=>e.open=true")
                page.locator('#landmark-estimates').check()
                identity = select_feature(page, 'Internal occipital crest')
                page.locator('#anatomy-isolate').click()
                page.locator('#anatomy-focus').click()
                settle(page)
                audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
                capture(page, f'audit-{dataset}-internal-occipital-crest')
                status = next(row for row in rows if row['dataset'] == dataset and row['id'] == identity)
                if status['kind'] == 'withheld':
                    assert not audit['parts'] and 'Source transfer withheld' in page.locator('#anatomy-placement').inner_text()
                    assert any('buried surface' in reason for reason in status['rejection_reasons']), 'A buried CT crest must have an explicit exposure failure'
                else:
                    assert audit['parts'], f'{dataset}: accepted internal crest must produce geometry'
                    try:
                        point = visible_point(page, audit)
                    except AssertionError as error:
                        raise AssertionError(f'{dataset}: internal crest: {error}') from error
                    assert page.evaluate('({x,y}) => window.skullAtlas.regionAt(x,y)', point) == identity
        finally:
            browser.close()
    return failures + errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, help='Archived JSON with binary of the same stem; geometry replay only')
    parser.add_argument('--skip-browser', action='store_true')
    parser.add_argument('--report', type=Path, default=ARTIFACTS / 'all-landmarks-report.json')
    parser.add_argument('--require-supplied-boundaries', action='store_true')
    args = parser.parse_args()
    path = args.package or ROOT / 'assets/landmark-regions/registered-regions.json'
    manifest = json.loads(path.read_text())
    raw = path.with_suffix('.bin').read_bytes() if args.package else binary(manifest)
    assert hashlib.sha256(raw).hexdigest() == manifest['binary']['sha256']
    geometry, failures, models = geometry_checks(manifest, raw, bool(args.package))
    rows = catalogue_inventory(manifest, models)
    browser_requested = not args.skip_browser and not args.package
    browser_completed = False
    if browser_requested:
        try:
            failures.extend(browser_checks(rows))
            browser_completed = True
        except Exception as error:
            failures.append(f'Browser audit incomplete: {type(error).__name__}: {error}')
    report = {'schema_version': 1, 'anatomical_accuracy_percent': None, 'independently_validated': False,
              'note': 'Complete catalogue audit of software and geometric invariants. Passing does not validate anatomical boundaries or guide coordinates.',
              'models': {dataset: dict(Counter(row['kind'] for row in rows if row['dataset'] == dataset)) for dataset in ['ct', 'reference', 'microct']},
              'catalogue_placements_checked': len(rows), 'native_highlight_vertices_checked': sum(r['vertices_checked'] for r in geometry),
              'registered_binary_sha256': manifest['binary']['sha256'],
              'browser_requested': bool(browser_requested), 'browser_audit_completed': browser_completed,
              'browser_placements_checked': sum('runtime' in row for row in rows),
              'geometry': geometry, 'landmarks': rows, 'failures': failures}
    temporary = args.report.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(args.report)
    print(json.dumps({key: value for key, value in report.items() if key not in {'landmarks', 'geometry'}}, indent=2))
    assert not failures, '\n'.join(failures)
    if args.require_supplied_boundaries and any(row['kind'] != 'source-face-selection' for row in rows):
        parser.exit(2, 'Complete source-defined anatomical boundaries are unavailable; see the per-landmark report.\n')


if __name__ == '__main__':
    main()
