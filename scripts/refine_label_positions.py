#!/usr/bin/env python3
"""Refine every skull label using native surfaces and bone-shape correspondence.

Input: landmark catalogue, checksum-verified skull GLBs and optional region assets.
Output: atomic catalogue update and per-placement provenance/diagnostics report.
Original coordinates are retained. Geometry fits do not establish anatomical truth.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import hashlib
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
import trimesh

from region_registration import BoneRegistration, BoneSurfaceCorrespondence, Surface, load_native_bones
from source_io import digest


ROOT = Path(__file__).resolve().parents[1]
DEFINITION = 'https://pmc.ncbi.nlm.nih.gov/articles/PMC7366040/'
EXTREMA_DEFINITION = 'https://pmc.ncbi.nlm.nih.gov/articles/PMC6898705/'
DEFINITIONS = {
    'nasion': 'https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2022.1013862/full',
    'glabella': 'https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2022.1013862/full',
    'menton': 'https://academic.oup.com/view-large/97191171',
    'pogonion': 'https://academic.oup.com/view-large/97191171',
    'gnathion': 'https://academic.oup.com/view-large/97191171',
    'vertex': 'https://www.mdpi.com/1943-3883/7/2/28',
}
VIEWS = {'anterior': [0, 0, 1], 'posterior': [0, 0, -1], 'superior': [0, 1, 0],
         'inferior': [0, -1, 0], 'left': [1, 0, 0], 'right': [-1, 0, 0]}
VAULT = {'frontal-bone', 'parietal-bone-left', 'parietal-bone-right', 'occipital-bone'}
INNER_NAMES = ('internal ', 'inner ', 'endocranial', 'cerebral ', 'cerebellar ', 'sinus groove', 'groove for transverse sinus',
               'groove for sigmoid sinus', 'groove for superior sagittal sinus', 'internal occipital')
OUTER_NAMES = ('external ', 'outer ', 'nuchal ', 'temporal line', 'parietal eminence', 'frontal eminence')
LIMITS = {'junction_distance': .8, 'projection_distance': 4.5, 'round_trip_distance': 4.5,
          'junction_search_radius': 25., 'contact_search_radius': 12.}


def compartment(entry, owner):
    if owner not in VAULT:
        return None
    name = entry['name'].lower()
    if any(word in name for word in INNER_NAMES):
        return 'inner'
    if any(word in name for word in OUTER_NAMES):
        return 'outer'
    return None


def vault_center(bones):
    return np.mean([bones[key].centroid for key in VAULT], axis=0)


def baseline(placement):
    return deepcopy(placement.get('initial_placement', placement))


def moved_normal(registration, point, normal):
    # Normals are covectors: J^{-T} n, rather than a deformed displacement J n.
    # The distinction matters on sheared thin processes and cavity walls.
    moved = registration.move(point[None])[0]
    transpose_jacobian = (registration.move(point + np.eye(3) * .01) - moved) / .01
    result = np.linalg.solve(transpose_jacobian, normal)
    return result / np.linalg.norm(result)


def native_projection(surface, point, center, side=None, normal=None):
    point = np.asarray(point, float)
    if side:
        # A normal alone cannot distinguish a buried wall from exposed cortex.
        # First-hit rays approach the intended cortex from its actual space.
        radial = point - center
        radial /= max(np.linalg.norm(radial), 1e-12)
        origin = center if side == 'inner' else center + radial * surface.mesh.extents.max() * 3
        direction = radial if side == 'inner' else -radial
        from mathutils import Vector
        hit = surface.tree.ray_cast(Vector(origin.tolist()), Vector(direction.tolist()))
        if hit[0] is not None:
            position, face = np.array(hit[0]), int(hit[2])
            position = trimesh.triangles.closest_point(surface.mesh.triangles[face:face + 1], position[None])[0]
            sign = np.dot(surface.mesh.face_normals[face], position - center)
            if sign * (-1 if side == 'inner' else 1) > 0:
                return position, surface.mesh.face_normals[face], face, float(np.linalg.norm(position - point))
        raise ValueError(f'No exposed {side} cortical surface in the guide direction')
    query = point[None]
    result = surface.compatible_nearest(query, np.asarray(normal)[None]) if normal is not None else surface.nearest(query)
    face = int(result[2][0])
    position = trimesh.triangles.closest_point(surface.mesh.triangles[face:face + 1], result[0])[0]
    return position, result[1][0], face, float(np.linalg.norm(position - point))


def region_anchors(root, models, bones):
    """Read original face IDs/triangles; never use a free-space centroid as anchor."""
    output = {}
    directory = root / 'assets/landmark-regions'
    source_path = directory / 'source-regions.json'
    if source_path.exists():
        source = json.loads(source_path.read_text())
        if source['source']['model_sha256'] != models['reference']['sha256']:
            raise ValueError('Reference selections belong to a different specimen')
        raw = (root / source['binary']['url']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['binary']['sha256']:
            raise ValueError('Reference surface selection checksum differs')
        for domain in source['regions']:
            ids = np.frombuffer(raw, dtype='<u4', count=domain['count'], offset=domain['byte_offset'])
            mesh = bones['reference'][domain['owner']]
            triangles = mesh.triangles[ids]
            area = mesh.area_faces[ids]
            if not len(triangles):
                continue
            centers = triangles.mean(axis=1)
            center = np.average(centers, axis=0, weights=area)
            selected = int(np.argmin(np.linalg.norm(centers - center, axis=1)))
            for identity in domain['feature_ids']:
                candidate = {'point': centers[selected].tolist(), 'owner': domain['owner'], 'surface_face': int(ids[selected]),
                             'area': float(area.sum()), 'basis': 'native-source-selection', 'source_url': source['source']['url']}
                if candidate['area'] > output.get(('reference', identity), {}).get('area', -1):
                    output['reference', identity] = candidate
    for path, basis in [(directory / 'apertures.json', 'native-opening-fit'), (directory / 'registered-regions.json', 'registered-source-region')]:
        if not path.exists():
            continue
        package = json.loads(path.read_text())
        if digest(root / package['binary']['url']) != package['binary']['sha256']:
            raise ValueError('Registered surface binary checksum differs')
        raw = (root / package['binary']['url']).read_bytes()
        for domain in package['regions']:
            dataset, identity = domain['dataset'], domain['feature_id']
            if domain['source_model_sha256'] != models[dataset]['sha256']:
                raise ValueError('Mapped label region belongs to a different specimen')
            if domain.get('source_id') == 'biodigital':
                side_check = domain['fit'].get('surface_side_check')
                if domain['fit'].get('surface_correspondence_version') != 2 or not side_check:
                    continue
                exposure = side_check.get('cavity_exposure_check')
                if side_check.get('source_compartment') in {'inner', 'outer'} and (not exposure or min(exposure['source']['exposed_fraction'], exposure['native']['exposed_fraction']) < .95):
                    continue
                signature = side_check.get('native_signature', {})
                side = compartment({'name': identity.replace('-', ' ')}, domain['owner'])
                if signature.get('outer_fraction' if side == 'inner' else 'inner_fraction', 0) > .01 and side:
                    continue
            if output.get((dataset, identity), {}).get('basis') in {'native-source-selection', 'native-opening-fit'}:
                continue
            if domain['area'] <= output.get((dataset, identity), {}).get('area', -1):
                continue
            triangles = np.frombuffer(raw, dtype='<f4', count=domain['positions_count'],
                                      offset=domain['positions_byte_offset']).reshape(-1, 3, 3)
            faces = np.frombuffer(raw, dtype='<u4', count=domain['source_faces_count'], offset=domain['source_faces_byte_offset'])
            centers = triangles.mean(axis=1)
            areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
            center = np.average(centers, axis=0, weights=areas)
            chosen = int(np.argmin(np.linalg.norm(centers - center, axis=1)))
            output[dataset, identity] = {'point': centers[chosen].astype(float).tolist(), 'owner': domain['owner'],
                                         'surface_face': int(faces[chosen]), 'area': domain['area'],
                                         'basis': basis, 'source_url': domain.get('source_url', models[dataset]['source']['url'])}
    return output


def cavity_anchors(root, plane):
    manifest = json.loads((root / 'assets/skull-cavities/manifest.json').read_text())
    if digest(root / manifest['model']) != manifest['sha256']:
        raise ValueError('Native cavity model checksum differs')
    cavities = load_native_bones(root / manifest['model'])
    normal, offset, _ = plane
    mapping = {'frontal-sinus': 'frontal-sinuses', 'sphenoidal-sinus': 'sphenoid-sinus',
               **{f'{side}-maxillary-sinus': 'maxillary-sinuses' for side in ['left', 'right']},
               **{f'{side}-mastoid-air-cells': 'mastoid-air-cells' for side in ['left', 'right']}}
    result = {}
    for identity, key in mapping.items():
        mesh = cavities[key]
        if identity.startswith(('left-', 'right-')):
            sign = 1 if identity.startswith('left-') else -1
            faces = np.flatnonzero((mesh.triangles_center @ normal - offset) * sign > 0)
            mesh = mesh.submesh([faces], append=True)
        # A point on the actual segmented cavity is preferable to a bone wall or
        # bounding-box centre that may lie outside an irregular cavity entirely.
        center = np.average(mesh.triangles_center, axis=0, weights=mesh.area_faces)
        point = Surface(mesh).nearest(center[None])[0][0]
        result[identity] = {'point': point, 'source_url': manifest['source']['url'], 'cavity_id': key,
                            'basis': 'native-segmented-cavity', 'attachment': 'space', 'status': 'source-selection'}
    return result


def junction(entry, guide, bones, direction, radius, maximum_gap):
    surfaces, cloud = [], []
    owners = entry['owners']
    if entry['category'] == 'sutures' and len(owners) > 2:
        # A long named suture can have several bilateral owners. Its label
        # belongs at the nearby pair, rather than forcing a distant junction.
        owners = sorted(owners, key=lambda key: Surface(bones[key]).nearest(guide[None])[3][0])[:2]
    for owner in owners:
        mesh = bones[owner]
        eligible = (np.linalg.norm(mesh.triangles_center - guide, axis=1) < radius)
        # View-facing surfaces exclude deep, internal intersections of vault bones.
        eligible &= mesh.face_normals @ direction > .15
        if not eligible.any():
            raise ValueError(f'No facing native search surface on {owner}')
        part = mesh.submesh([np.flatnonzero(eligible)], append=True)
        surfaces.append(Surface(part))
        cloud.append(part.vertices)
    points = np.unique(np.concatenate(cloud).round(5), axis=0)
    distances = np.stack([surface.nearest(points)[3] for surface in surfaces], axis=1)
    ranking = np.sum(distances ** 2, axis=1) + .0001 * np.linalg.norm(points - guide, axis=1) ** 2
    starts = np.argsort(ranking)[:12:2]
    candidates = []
    for start in starts:
        point = np.clip(points[start], guide - radius + 1e-5, guide + radius - 1e-5)
        fit = least_squares(lambda p: np.r_[[s.nearest(p[None])[3][0] for s in surfaces], (p - guide) * .005],
                            point, bounds=(guide - radius, guide + radius), diff_step=1e-3, max_nfev=80)
        gaps = np.array([s.nearest(fit.x[None])[3][0] for s in surfaces])
        candidates.append((float(np.sum(gaps ** 2) + .0001 * np.linalg.norm(fit.x - guide) ** 2), fit.x, gaps))
    _, point, gaps = min(candidates, key=lambda item: item[0])
    if gaps.max() > maximum_gap:
        raise ValueError(f'No shared native junction: maximum bone gap {gaps.max():.3f}')
    return point, {'owner_distances': dict(zip(owners, gaps.round(6).tolist())),
                   'maximum_owner_distance': float(gaps.max()), 'acceptance_distance': maximum_gap,
                   'view_direction': direction.tolist(), 'definition_url': DEFINITIONS.get(entry['id'], DEFINITION) if entry['category'] == 'landmarks' else entry['source_url'],
                   'method': 'view-facing native bone-junction least-squares fit'}


def symmetry_plane(mesh):
    """Fit a reflection plane to the native bone; the mandible has its own plane."""
    points = trimesh.sample.sample_surface(mesh, 2500, seed=4813)[0]
    from scipy.spatial import cKDTree
    tree = cKDTree(mesh.vertices)
    def plane(parameters):
        normal = np.array([1., parameters[0], parameters[1]])
        return normal / np.linalg.norm(normal), parameters[2]
    def residual(parameters):
        normal, offset = plane(parameters)
        reflected = points - 2 * (points @ normal - offset)[:, None] * normal
        return tree.query(reflected)[0]
    seed = float(np.median(mesh.bounds[:, 0]))
    fitted = least_squares(residual, [0., 0., seed], bounds=([-.25, -.25, seed - 15], [.25, .25, seed + 15]),
                           loss='soft_l1', f_scale=1., diff_step=1e-3, max_nfev=60)
    normal, offset = plane(fitted.x)
    return normal, float(offset), float(np.median(residual(fitted.x)))


def extremum(entry, guide, bones, plane, reference_axes=None):
    normal, offset, residual = plane
    matrix = np.eye(3) if reference_axes is None else reference_axes
    up, front = matrix @ np.array([0., 1., 0.]), matrix @ np.array([0., 0., 1.])
    up -= normal * np.dot(up, normal)
    up /= np.linalg.norm(up)
    front -= normal * np.dot(front, normal) + up * np.dot(front, up)
    front /= np.linalg.norm(front)
    segments = []
    for owner in entry['owners']:
        segments.extend(trimesh.intersections.mesh_plane(bones[owner], normal, normal * offset))
    if not segments:
        raise ValueError('No native sagittal bone contour')
    # Segment endpoints are exact native triangle/plane intersections.
    points = np.unique(np.asarray(segments).reshape(-1, 3).round(7), axis=0)
    identity = entry['id']
    if identity == 'vertex':
        score, direction = points @ up, up
    elif identity == 'menton':
        score, direction = -points @ up, -up
    else:
        radius = 18 if identity in {'pogonion', 'gnathion'} else 15
        if identity == 'pogonion':
            # The alveolar process may protrude beyond the chin. Restrict the
            # extremum to the inferior fifth of the native mandibular body.
            mesh = bones['mandible']
            points = points[points @ up <= (points @ up).min() + np.ptp(mesh.vertices @ up) * .2]
        points = points[np.linalg.norm(points - guide, axis=1) < radius]
        if not len(points):
            raise ValueError('No sagittal contour in the anatomical search window')
        direction = front - up if identity == 'gnathion' else front.copy()
        direction /= np.linalg.norm(direction)
        score = points @ direction
    point = points[int(np.argmax(score))]
    return point, {'method': 'extremum of original native triangle intersections with fitted symmetry plane',
                   'view_direction': direction.tolist(), 'definition_url': DEFINITIONS.get(identity, EXTREMA_DEFINITION),
                   'symmetry_normal': normal.tolist(), 'symmetry_offset': offset,
                   'symmetry_median_residual': residual, 'orientation': 'reference atlas axes, matched bone orientation on CT; no Frankfort reorientation',
                   'superior_axis': up.tolist(), 'anterior_axis': front.tolist(),
                   'search_guide': guide.tolist(), 'criterion': identity}


def refine_catalogue(catalogue, root=ROOT):
    models, bones, surfaces, centers = {}, {}, {}, {}
    for dataset, directory in [('reference', 'skull'), ('ct', 'skull-ct')]:
        models[dataset] = json.loads((root / f'assets/{directory}/manifest.json').read_text())
        if digest(root / models[dataset]['model']) != models[dataset]['sha256']:
            raise ValueError(f'{dataset} skull checksum differs from its manifest')
        bones[dataset] = load_native_bones(root / models[dataset]['model'])
        surfaces[dataset] = {key: Surface(mesh) for key, mesh in bones[dataset].items()}
        centers[dataset] = vault_center(bones[dataset])
    anchors = region_anchors(root, models, bones)
    originals = {(dataset, entry['id']): baseline(p) for entry in catalogue['landmarks'] for dataset, p in entry['positions'].items()}
    registrations, inverse = {}, {}
    report = {'schema_version': 1, 'models': {d: m['sha256'] for d, m in models.items()}, 'limits': LIMITS,
              'anatomical_accuracy_percent': None, 'independently_validated': False,
              'note': 'Source-region agreement and geometric checks are not independent anatomical validation.',
              'placements': [], 'registrations': {}, 'anchor_comparison': []}
    planes = {}
    for dataset in ['reference', 'ct']:
        cranium = trimesh.util.concatenate([bones[dataset][owner] for owner in VAULT])
        planes[dataset] = {'cranium': symmetry_plane(cranium), 'mandible': symmetry_plane(bones[dataset]['mandible'])}
        native_cavities = cavity_anchors(root, planes[dataset]['cranium']) if dataset == 'ct' else {}
        for entry in catalogue['landmarks']:
            if dataset not in entry['positions']:
                continue
            original = originals[dataset, entry['id']]
            placement = deepcopy(original)
            point = np.array(original['point'], float)
            fit = {'source_model_sha256': models[dataset]['sha256'], 'initial_method': original['method'],
                   'anatomical_accuracy_percent': None, 'independently_validated': False, 'status': 'approximate'}
            owner = entry['owners'][0]
            side = compartment(entry, owner)
            anchor = anchors.get((dataset, entry['id'])) if entry['category'] != 'landmarks' else None
            if original.get('native_point_fit'):
                fit.update(basis='native-junction', owner=owner, status='geometry-fitted', attachment='junction')
            elif entry['id'] in native_cavities:
                cavity = native_cavities[entry['id']]
                point = cavity['point']
                fit.update({key: value for key, value in cavity.items() if key != 'point'})
                placement['method'] = 'native-segmented-cavity-anchor'
            elif anchor:
                point = np.array(anchor['point'])
                owner = anchor['owner']
                fit.update({key: value for key, value in anchor.items() if key not in {'point', 'area'}})
                fit['status'] = {'native-source-selection': 'source-selection', 'native-opening-fit': 'geometry-fitted'}.get(anchor['basis'], 'estimated-source-transfer')
                placement['method'] = anchor['basis'] + '-anchor'
            else:
                if dataset == 'ct':
                    source_placement = entry['positions']['reference']
                    owner = source_placement.get('label_fit', {}).get('owner', owner)
                    if owner not in bones['ct']:
                        owner = entry['owners'][0]
                    if owner not in registrations:
                        a, b = bones['reference'][owner], bones['ct'][owner]
                        factor = np.cbrt(np.prod(b.extents) / np.prod(a.extents))
                        initial = np.eye(4)
                        initial[:3, :3] *= factor
                        initial[:3, 3] = b.centroid - factor * a.centroid
                        registrations[owner] = BoneRegistration(a, b, initial)
                        inverse[owner] = BoneSurfaceCorrespondence(a, registrations[owner], centers['reference'] if owner in VAULT else None,
                                                                   centers['ct'] if owner in VAULT else None)
                        report['registrations'][owner] = registrations[owner].metrics
                        print(f'Registered label bone {owner}', flush=True)
                    registration = registrations[owner]
                    source_point = np.array(source_placement['point'])
                    source_near = surfaces['reference'][owner].nearest(source_point[None])
                    source_normal = surfaces['reference'][owner].smooth_normals(source_near[0])[0]
                    point = registration.move(source_point[None])[0]
                    transformed_normal = moved_normal(registration, source_point, source_normal)
                    attachment = source_placement.get('label_fit', {}).get('attachment', 'surface')
                    if attachment == 'surface':
                        projected, _, face, distance = native_projection(surfaces[dataset][owner], point, centers[dataset], side, transformed_normal)
                        returned = inverse[owner].original_points(projected[None])[0][0]
                        round_trip = float(np.linalg.norm(returned - source_point))
                        point = projected
                        fit.update(surface_face=face, projection_distance=distance, round_trip_distance=round_trip)
                        if distance > LIMITS['projection_distance'] or round_trip > LIMITS['round_trip_distance']:
                            fit['status'] = 'uncertain-fit'
                    fit.update(basis='bone-shape-registration', owner=owner, source_point=source_point.tolist(), attachment=attachment,
                               source_placement_basis=source_placement['method'])
                    placement['method'] = 'bone-shape-registration'
                else:
                    nearest = [(surfaces[dataset][key].nearest(point[None]), key) for key in entry['owners']]
                    result, owner = min(nearest, key=lambda item: item[0][3][0])
                    side = compartment(entry, owner)
                    # Preserve actual empty-space guides instead of snapping a
                    # foramen/canal centre to whichever wall happens to be nearest.
                    space = entry['category'] in {'canals', 'foramina', 'fissures', 'sinuses', 'fossae'} and result[3][0] > 1.5 and not side
                    if not space:
                        point, _, face, distance = native_projection(surfaces[dataset][owner], point, centers[dataset], side)
                        fit.update(surface_face=face, projection_distance=distance)
                    fit.update(basis='native-surface-guide' if not space else 'source-space-guide', owner=owner,
                               attachment='space' if space else 'surface')
                    placement['method'] = fit['basis']
                if side:
                    fit['cortical_compartment'] = side
                if entry['category'] == 'sutures' or entry['id'] in {'nasion', 'left-asterion', 'right-asterion'}:
                    direction = np.array(VIEWS.get(entry.get('view'), [0, 0, 1]), float)
                    radius = LIMITS['junction_search_radius'] if entry['category'] == 'landmarks' else LIMITS['contact_search_radius']
                    try:
                        point, native_fit = junction(entry, point, bones[dataset], direction, radius, LIMITS['junction_distance'])
                        fit.update(basis='native-junction', status='geometry-fitted', **native_fit)
                        fit.pop('surface_face', None)
                        fit['attachment'] = 'junction'
                        placement['method'] = 'native-suture-junction' if entry['category'] == 'landmarks' else 'native-bone-contact-anchor'
                        if entry['category'] == 'landmarks':
                            placement['native_point_fit'] = {**native_fit, 'source_model_sha256': models[dataset]['sha256'],
                                                             'display_radius': 2, 'independently_validated': False, 'anatomical_accuracy_percent': None,
                                                             'note': 'Compact cue for a fitted junction point, not an anatomical region boundary.'}
                    except ValueError as error:
                        fit['native_fit_rejection'] = str(error)
                elif entry['id'] in {'menton', 'pogonion', 'gnathion', 'glabella', 'vertex'}:
                    try:
                        plane = planes[dataset]['mandible' if owner == 'mandible' else 'cranium']
                        axes = registrations[owner].matrix[:3, :3] if dataset == 'ct' else None
                        point, native_fit = extremum(entry, point, bones[dataset], plane, axes)
                        fit.update(basis='native-contour-extremum', status='geometry-fitted', **native_fit)
                        owner = min(entry['owners'], key=lambda key: surfaces[dataset][key].nearest(point[None])[3][0])
                        fit.update(owner=owner, surface_face=int(surfaces[dataset][owner].nearest(point[None])[2][0]), attachment='surface')
                        placement['method'] = 'native-contour-extremum'
                        placement['native_point_fit'] = {**native_fit, 'source_model_sha256': models[dataset]['sha256'],
                                                         'display_radius': 1.5, 'independently_validated': False, 'anatomical_accuracy_percent': None,
                                                         'note': 'Native contour extremum in atlas orientation; no independent anatomical validation.'}
                    except ValueError as error:
                        fit['native_fit_rejection'] = str(error)
            fit.setdefault('owner', owner)
            fit.setdefault('attachment', 'surface')
            fit['displacement_from_initial'] = float(np.linalg.norm(point - np.array(original['point'])))
            placement.update(point=point.round(6).tolist(), approximate=True, label_fit=fit, initial_placement=original)
            entry['positions'][dataset] = placement
            report['placements'].append({'dataset': dataset, 'id': entry['id'], 'point': placement['point'], **fit})

    # Held-out selections never constrain these bone registrations. Compare the
    # same source anchors, not differently placed catalogue guesses.
    for entry in catalogue['landmarks']:
        a, b = anchors.get(('reference', entry['id'])), anchors.get(('ct', entry['id']))
        if not a or not b or a['owner'] != b['owner'] or a['owner'] not in registrations:
            continue
        owner = a['owner']
        ref = np.array(a['point'])
        expected = np.array(b['point'])
        moved = registrations[owner].move(ref[None])[0]
        normal = surfaces['reference'][owner].smooth_normals(ref[None])[0]
        direction = moved_normal(registrations[owner], ref, normal)
        after = native_projection(surfaces['ct'][owner], moved, centers['ct'], compartment(entry, owner), direction)[0]
        lo, hi = bones['reference'][owner].bounds
        nlo, nhi = bones['ct'][owner].bounds
        before = nlo + (ref - lo) / (hi - lo) * (nhi - nlo)
        report['anchor_comparison'].append({'id': entry['id'], 'owner': owner,
                                            'before_error': float(np.linalg.norm(before - expected)),
                                            'after_error': float(np.linalg.norm(after - expected))})
    report['counts'] = {d: dict(Counter(p['basis'] for p in report['placements'] if p['dataset'] == d)) for d in models}
    comparisons = report['anchor_comparison']
    if comparisons:
        report['comparison_summary'] = {'count': len(comparisons), 'improved': sum(c['after_error'] < c['before_error'] for c in comparisons),
                                        'before_median': float(np.median([c['before_error'] for c in comparisons])),
                                        'after_median': float(np.median([c['after_error'] for c in comparisons])),
                                        'before_p95': float(np.quantile([c['before_error'] for c in comparisons], .95)),
                                        'after_p95': float(np.quantile([c['after_error'] for c in comparisons], .95))}
    catalogue['source']['placement_notes'] = ('Original atlas leaders, mirrored leaders and curated study coordinates remain in initial_placement. '
        'Label anchors prefer original native selections and accepted source-region transfers. Other CT guides use per-bone similarity ICP and '
        'regularized nonrigid shape registration, with normal-compatible surface projection and round-trip diagnostics. Explicit inner/outer vault '
        'labels use exposed native cortex. Supported junctions and sagittal contour extrema are fitted directly on each specimen. Empty-space guides '
        'are not snapped onto arbitrary walls. All positions remain approximate and lack independent anatomical ground truth; no accuracy percentage is claimed.')
    catalogue['label_refinement'] = {'version': 1, 'models': report['models'], 'counts': report['counts'],
                                   'limits': LIMITS, 'independently_validated': False, 'anatomical_accuracy_percent': None}
    report['catalogue_sha256'] = hashlib.sha256((json.dumps(catalogue, indent=2) + '\n').encode()).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', type=Path, default=ROOT / 'assets/landmarks/catalogue.json')
    parser.add_argument('--output', type=Path, help='Refined catalogue destination; defaults to updating the input atomically')
    parser.add_argument('--report', type=Path, default=ROOT / 'artifacts/verification/label-refinement-report.json')
    args = parser.parse_args()
    catalogue = json.loads(args.catalogue.read_text())
    report = refine_catalogue(catalogue)
    destination = args.output or args.catalogue
    for path, data in [(destination, catalogue), (args.report, report)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.json.partial')
        temporary.write_text(json.dumps(data, indent=2) + '\n')
        temporary.replace(path)
    print(json.dumps({'counts': report['counts'], 'comparison': report.get('comparison_summary')}, indent=2))


if __name__ == '__main__':
    main()
