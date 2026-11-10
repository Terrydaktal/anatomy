#!/usr/bin/env python3
"""Regress compact native junction points and visible, continuous arch highlights."""

import argparse
import colorsys
import json
import os
import shutil

import numpy as np
from PIL import ImageColor
from playwright.sync_api import sync_playwright
import trimesh

from region_registration import load_native_bones, BoneRegistration, similarity, source_mesh
from export_registered_regions import source_bones, prepare_patch
from fetch_region_sources import CACHE
from test_anatomy import load
from test_regions import SourceTriangles, assert_on_triangle, canvas_image, select_feature, visible_point
from test_viewer import ROOT, ARTIFACTS, local_server, settle, capture, snapshot


def arch_columns(page, audit):
    image = canvas_image(page).convert('HSV')
    rgb = ImageColor.getrgb(audit['parts'][0]['colour'])
    hue = colorsys.rgb_to_hsv(*(value / 255 for value in rgb))[0] * 255
    canvas = page.locator('#skull-canvas').bounding_box()
    points = []
    for part in audit['parts']:
        positions = np.asarray(part['positions']).reshape(-1, 3)
        for point in positions[::max(1, len(positions) // 120)]:
            projected = page.evaluate('({point, owner}) => window.skullAtlas.projectPoint(point, owner)', {'point': point.tolist(), 'owner': part['owner']})
            points.append([projected['x'] - canvas['x'], projected['y'] - canvas['y']])
    points = np.array(points)
    lower = np.maximum(np.floor(points.min(axis=0)) - 2, 0).astype(int)
    upper = np.minimum(np.ceil(points.max(axis=0)) + 2, image.size).astype(int)
    crop = np.array(image)[lower[1]:upper[1], lower[0]:upper[0]].astype(float)
    difference = np.abs(crop[:, :, 0] - hue)
    difference = np.minimum(difference, 255 - difference)
    coloured = (difference < 8) & (crop[:, :, 1] > 90) & (crop[:, :, 2] > 35)
    yy, xx = np.nonzero(coloured)
    assert len(xx) > 150, 'Arch highlight has no substantial visible fill'
    left, right = np.quantile(xx, [.025, .975]).astype(int)
    bins = np.linspace(left, right + 1, 25).astype(int)
    filled = [coloured[:, a:b].sum() >= 4 * max(1, b - a) for a, b in zip(bins[:-1], bins[1:])]
    return {'occupied_width_fraction': float(np.mean(filled)), 'coloured_pixels': len(xx)}


def check(dataset, url, page):
    checks = []
    load(page, url, f'?dataset={dataset}&study=mapped-regions')
    page.locator('#landmark-xray').uncheck()
    source = SourceTriangles(dataset)
    catalogue = json.loads((ROOT / 'assets/landmarks/catalogue.json').read_text())['landmarks']
    model = json.loads((ROOT / f'assets/{"skull-ct" if dataset == "ct" else "skull"}/manifest.json').read_text())
    bones = load_native_bones(ROOT / model['model'])
    if dataset == 'reference':
        viewed = {entry['id']: entry for entry in snapshot(page)['structures']}
        assert viewed['maxilla-left']['windingCorrections'] > 0, 'Mirrored render mesh still has reversed winding'
    lock = json.loads((CACHE / 'sources.json').read_text())
    source_models = source_bones(CACHE, lock)
    source_record = next(record for record in lock['biodigital']['records'] if record['name'] == 'Zygomatic arch')
    source_mask = source_mesh(CACHE, lock, source_record)
    common = [owner for owner in source_models if owner in bones]
    initial = similarity(np.array([source_models[owner].centroid for owner in common]), np.array([bones[owner].centroid for owner in common]))
    for owner, bone in bones.items():
        for face in np.linspace(0, len(bone.faces) - 1, min(30, len(bone.faces)), dtype=int):
            expected = np.asarray(source.triangle(owner, int(face)))
            actual = bone.vertices[bone.faces[face]]
            assert all(np.min(np.linalg.norm(expected - point, axis=1)) < 1e-5 for point in actual), 'Normal correction changed native triangle identity'
            normals = np.asarray(source.triangle_normals(owner, int(face))).mean(axis=0)
            assert bone.face_normals[face] @ normals >= -1e-7, 'Mirrored bone still faces opposite its supplied normals'
    for identity in ['bregma', 'lambda']:
        entry = next(record for record in catalogue if record['id'] == identity)
        placement = entry['positions'][dataset]
        center = np.array(placement['point'])
        fit = placement['native_point_fit']
        assert fit['maximum_owner_distance'] <= 0.8 and fit['anatomical_accuracy_percent'] is None
        for owner in entry['owners']:
            # Independent R-tree triangle queries, rather than the fitter's BVH.
            distance = trimesh.proximity.closest_point(bones[owner], center[None])[1][0]
            assert distance <= 0.8
        selected = select_feature(page, entry['name'])
        audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', selected)
        assert audit['methods'] == ['fitted:native-suture-junction']
        assert {part['owner'] for part in audit['parts']} == set(entry['owners'])
        for part in audit['parts']:
            points = np.array(part['positions']).reshape(-1, 3)
            assert np.linalg.norm(points - center, axis=1).max() < 2.4, 'Detached or oversized junction patch'
            for index in range(0, len(part['sourceFaces']), max(1, len(part['sourceFaces']) // 40)):
                for point in points[index * 3:index * 3 + 3]:
                    assert_on_triangle(point.tolist(), source.triangle(part['owner'], part['sourceFaces'][index]))
        point = visible_point(page, audit)
        page.mouse.move(point['x'], point['y'])
        page.wait_for_function('id => window.skullAtlas.snapshot().anatomy.hoveredRegion === id', arg=identity)
        assert 'point' in page.locator('#anatomy-placement').inner_text().lower()
        capture(page, f'shape-{dataset}-{identity}')
        checks.append(f'{dataset}:{identity}: compact pickable native three-bone junction')
    arches = []
    for side in ['left', 'right']:
        load(page, url, f'?dataset={dataset}&study=mapped-regions')
        page.locator('#landmark-xray').uncheck()
        identity = select_feature(page, f'{side.title()} zygomatic arch')
        audit = page.evaluate('id => window.skullAtlas.regionGeometry(id)', identity)
        assert {part['owner'] for part in audit['parts']} == {f'zygomatic-bone-{side}', f'temporal-bone-{side}'}
        assert all(part['fit']['projected_source_coverage_fraction'] >= .95 for part in audit['parts'])
        independent_coverage = {}
        for part in audit['parts']:
            owner = part['owner']
            registration = BoneRegistration(source_models[owner], bones[owner], initial)
            footprint, _, normals, _ = prepare_patch(source_mask, source_models[owner], bones[owner], registration, side)
            samples, _ = trimesh.sample.sample_surface(footprint.metadata['coverage_source_surface'], 500, seed=9381)
            expected = registration.target.nearest(samples)[0]
            positions = np.array(part['positions']).reshape(-1, 3)
            actual = trimesh.Trimesh(positions, np.arange(len(positions)).reshape(-1, 3), process=False)
            # Independently verify coverage against the rendered output using
            # trimesh's R-tree queries rather than the exporter's Blender BVH.
            distances = trimesh.proximity.closest_point(actual, expected)[1]
            fraction = float(np.mean(distances <= part['fit']['coverage_distance']))
            assert fraction >= .95, f'Arch loses source surface: {dataset}:{owner}: {fraction}'
            independent_coverage[owner] = fraction
        measure = arch_columns(page, audit)
        assert measure['occupied_width_fraction'] >= .9, f'Arch has visible gaps: {dataset}:{side}: {measure}'
        capture(page, f'shape-{dataset}-{identity}')
        arches.append({'id': identity, **measure, 'independent_source_coverage': independent_coverage})
        checks.append(f'{dataset}:{identity}: both native bone portions and continuous visible fill')
    return checks, arches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=['ct'], default='ct')
    args = parser.parse_args()
    datasets = ['ct', 'reference'] if args.dataset == 'both' else [args.dataset]
    chrome = os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    report = {'checks': [], 'arches': {}, 'anatomical_accuracy_percent': None}
    errors = []
    with local_server() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True,
            env={k: v for k, v in os.environ.items() if k not in {'DISPLAY', 'WAYLAND_DISPLAY'}},
            args=['--enable-webgl', '--ignore-gpu-blocklist', '--disable-software-rasterizer', '--enable-features=Vulkan', '--use-angle=vulkan'])
        try:
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            for dataset in datasets:
                checks, arches = check(dataset, url, page)
                report['checks'].extend(checks)
                report['arches'][dataset] = arches
            assert not errors, errors
        finally:
            browser.close()
    (ARTIFACTS / 'annotation-shapes-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
