#!/usr/bin/env python3
"""Register published landmark regions to each viewer skull's native bone faces.

Input: checksum-verified data/source/mapped-regions from fetch_region_sources.py.
Output: registered-regions.json/.bin, with source provenance, fit diagnostics,
rejections, and original target triangle IDs. Cross-specimen fits remain estimates.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import trimesh

from fetch_region_sources import CACHE, verify, asset_id
from region_registration import BoneRegistration, BoneSurfaceCorrespondence, RegionField, Surface, apply, clip_native_region, similarity, source_mesh, fossafinder_meshes, load_native_bones
from source_io import digest


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'assets/landmark-regions'
GROUPS = {
    'Frontal bone': ['frontal-bone'], 'Parietal bone': ['parietal-bone-left', 'parietal-bone-right'],
    'Temporal bone': ['temporal-bone-left', 'temporal-bone-right'], 'Occipital bone': ['occipital-bone'],
    'Sphenoid bone': ['sphenoid-bone'], 'Ethmoid bone': ['ethmoid-bone'],
    'Maxilla': ['maxilla-left', 'maxilla-right'], 'Mandible': ['mandible'],
    'Lacrimal bone': ['lacrimal-bone-left', 'lacrimal-bone-right'],
    'Palatine bone': ['palatine-bone-left', 'palatine-bone-right'],
    'Zygomatic bone': ['zygomatic-bone-left', 'zygomatic-bone-right'],
    'Nasal bone': ['nasal-bone-left', 'nasal-bone-right'], 'Vomer': ['vomer'],
    'Inferior nasal concha': ['inferior-nasal-concha-left', 'inferior-nasal-concha-right'],
}
# Explicit identity aliases only. Lacrimal-fossa identity is constrained to the
# publisher's lacrimal-bone group, so it cannot match the frontal gland fossa.
ALIASES = {'lacrimal fossa': 'fossa for lacrimal sac', 'incisive fossa': 'incisive fossa of maxilla',
           'lateral pterygold plate': 'lateral pterygoid plate', 'optic foramen': 'optic canal'}


def normalize(value: str) -> str:
    value = re.sub(r'^(left|right)\s+', '', value.lower())
    value = ALIASES.get(value, value)
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', value).split())


def names(entry: dict) -> set:
    values = {normalize(value) for value in [entry['name'], *entry.get('aliases', [])]}
    # Anatomical owner qualifiers are redundant after an exact owner match.
    for value in list(values):
        values.add(re.sub(r' of (the )?(sphenoid|occipital|frontal|temporal|parietal|ethmoid|maxilla|mandible)( bone)?$', '', value))
    return values


def source_bones(cache: Path, lock: dict) -> dict:
    result = {}
    for record in lock['biodigital']['records']:
        if record['role'] != 'bone':
            continue
        name = record['name']
        generic = re.sub(r'^(Left|Right) ', '', name)
        generic = generic[:1].upper() + generic[1:]
        if generic not in GROUPS:
            continue
        owners = GROUPS[generic]
        if name.startswith(('Left ', 'Right ')):
            side = name.split()[0].lower()
            owners = [owner for owner in owners if owner.endswith('-' + side)]
        mesh = source_mesh(cache, lock, record)
        for owner in owners:
            if len(owners) == 2 and not name.startswith(('Left ', 'Right ')):
                sign = -1 if owner.endswith('-left') else 1
                selected = mesh.triangles_center[:, 0] * sign >= 0
                result[owner] = mesh.submesh([np.where(selected)[0]], append=True)
            else:
                result[owner] = mesh.copy()
    return result


def matching_features(record: dict, catalogue: list[dict]) -> list[dict]:
    group = next((value for value in reversed(record['parents']) if value in GROUPS), None)
    owners = set(GROUPS[group]) if group else None
    identity = normalize(record['name'])
    return [entry for entry in catalogue if identity in names(entry)
            and (owners is None or owners.intersection(entry['owners']))]


def write_binary(records: list[dict], pieces: list[tuple], output: Path) -> dict:
    chunks = []
    offset = 0
    for record, (positions, faces) in zip(records, pieces):
        position_bytes, face_bytes = positions.astype('<f4').tobytes(), faces.astype('<u4').tobytes()
        record.update(positions_byte_offset=offset, positions_count=positions.size,
                      source_faces_byte_offset=offset + len(position_bytes), source_faces_count=len(faces))
        chunks.extend([position_bytes, face_bytes])
        offset += len(position_bytes) + len(face_bytes)
    raw = b''.join(chunks)
    (output / 'registered-regions.bin.partial').write_bytes(raw)
    return {'url': 'assets/landmark-regions/registered-regions.bin', 'byte_length': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def prepare_patch(mask: trimesh.Trimesh, source: trimesh.Trimesh, target: trimesh.Trimesh,
                  registration: BoneRegistration, side: str | None, source_cranial_center=None,
                  compartment=None) -> tuple[trimesh.Trimesh, dict, np.ndarray, np.ndarray]:
    patch = mask.copy()
    closed = patch.is_watertight
    if side:
        sign = -1 if side == 'left' else 1  # Source laterality comes from its named paired bones.
        if closed:
            components = [component for component in patch.split(only_watertight=False)
                          if component.centroid[0] * sign >= 0 or component.bounds[0, 0] < 0 < component.bounds[1, 0]]
            patch = trimesh.util.concatenate(components) if components else trimesh.Trimesh()
        else:
            patch = patch.submesh([np.where(patch.triangles_center[:, 0] * sign >= 0)[0]], append=True)
    if not len(patch.faces):
        raise ValueError('Source region contains no surface on this side')
    factor = registration.metrics['scale']
    # Extract the publisher's selection on its own bone first. Intersecting a
    # thin warped selection volume with a different skull creates random islands
    # wherever the two specimens differ in thickness or local shape.
    source_side = 'right' if side == 'left' else 'left' if side else None
    candidate_mask = None
    if compartment in {'inner', 'outer'} and source_cranial_center is not None:
        radial = source.triangles_center - source_cranial_center
        radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
        sign = -1 if compartment == 'inner' else 1
        candidate_mask = np.sum(source.face_normals * radial, axis=1) * sign >= -0.1
    source_positions, _, boundaries = clip_native_region(
        source, patch, step=0.8 / factor, volume_tolerance=0.2 / factor,
        padding=2 / factor, minimum_altitude=0.01 / factor, maximum_distance=1.8 / factor,
        side=source_side, midline=0, return_boundaries=True, candidate_mask=candidate_mask)
    footprint = trimesh.Trimesh(source_positions.reshape(-1, 3),
                               np.arange(source_positions.size // 3).reshape(-1, 3), process=True)
    if len(boundaries) != len(footprint.faces):
        raise ValueError('Source footprint preprocessing changed its triangle identity')
    good = footprint.nondegenerate_faces(height=1e-7)
    footprint.update_faces(good)
    boundaries = boundaries[good]
    footprint.remove_unreferenced_vertices()
    if not len(footprint.faces):
        raise ValueError('Source selection has no resolved footprint on this bone')
    source_area = footprint.area * factor ** 2
    source_footprint = footprint.copy()
    moved = registration.move(footprint.vertices)
    footprint.vertices = moved
    # Preserve transported source-bone normals. Recomputing them after nearest
    # projection can flip small faces and erase the middle of a thin process.
    normals = footprint.face_normals.copy()
    coverage_source = footprint.copy()
    projected, _, _, distances = registration.target.oriented_nearest(moved, footprint.vertex_normals)
    p95 = float(np.quantile(distances, 0.95))
    if p95 > 4.5:
        raise ValueError(f'Region transfer p95 distance {p95:.3f} exceeds 4.5 display units')
    footprint.vertices = projected
    good = footprint.nondegenerate_faces(height=1e-7)
    footprint.update_faces(good)
    normals = normals[good]
    boundaries = boundaries[good]
    footprint.remove_unreferenced_vertices()
    if not len(footprint.faces):
        raise ValueError('Projected source footprint collapsed')
    footprint.metadata['coverage_source_surface'] = coverage_source
    footprint.metadata['original_source_surface'] = source_footprint
    return footprint, {
        'before_projection_median_distance': float(np.median(distances)),
        'before_projection_p95_distance': p95,
        'source_region_triangles': len(mask.faces), 'source_footprint_triangles': len(source_positions),
        'projected_region_triangles': len(footprint.faces),
        'source_footprint_area_scaled': float(source_area),
        'projected_footprint_area': float(footprint.area),
        'source_intersection_tolerance_display_units': 0.2,
        'source_compartment_constraint': compartment,
        'transfer_representation': 'source-bone surface footprint',
        'projection': 'normal-constrained nearest native bone surface',
        'outline_source': 'Source selection constrained by forward/inverse surface agreement; internal subdivision edges are not region borders',
    }, normals, boundaries


def check_footprint_coverage(patch, positions, target, normals, maximum_distance=1.0):
    surface = trimesh.Trimesh(positions.reshape(-1, 3), np.arange(positions.size // 3).reshape(-1, 3), process=False)
    # Sample the transported source surface BEFORE independent vertex projection:
    # projected triangles can fold, overlap and invent area across another wall.
    original = patch.metadata.get('coverage_source_surface', patch)
    points, faces = trimesh.sample.sample_surface(original, 500, seed=9381)
    points = target.compatible_nearest(points, original.face_normals[faces])[0]
    distances = Surface(surface).nearest(points)[3]
    coverage = float(np.mean(distances <= maximum_distance))
    if coverage < 0.95:
        raise ValueError(f'Transferred surface retains only {coverage:.1%} of the source footprint within its {maximum_distance:.3f} display-unit fit tolerance; requires 95%')
    return {'projected_source_coverage_fraction': coverage, 'coverage_sample_count': len(points),
            'coverage_source': 'Transported source-bone surface before independent vertex projection, projected to matching-facing native surfaces',
            'coverage_distance': maximum_distance, 'coverage_distance_basis': 'max(1 display unit, source-to-native projection p95)',
            'coverage_p95_distance': float(np.quantile(distances, 0.95))}


def check_correspondence_fit(correspondence, patch, positions):
    surface = trimesh.Trimesh(positions.reshape(-1, 3), np.arange(positions.size // 3).reshape(-1, 3), process=False)
    points, _ = trimesh.sample.sample_surface(surface, 1000, seed=4831)
    # Measure against the actual transported author selection. Whole-bone
    # proximity could accept extra paint merely because another bone surface is
    # nearby; a normal-constrained lookup can also be far along a ridge sidewall.
    distances = Surface(patch.metadata['coverage_source_surface']).nearest(points)[3]
    p95 = float(np.quantile(distances, 0.95))
    if p95 > correspondence.fit_limit:
        raise ValueError(f'Whole-region source correspondence p95 distance {p95:.3f} exceeds the fixed {correspondence.fit_limit:.3f} display-unit limit')
    return {'correspondence': 'Barycentric label pullback through the complete deformed source bone',
            'surface_correspondence_version': 2,
            'normal_source': 'Barycentrically interpolated native GLB vertex normals',
            'forward_inverse_agreement_distance': 1.8,
            'orientation_rule': 'Corresponding source faces must face the native surface; orientation chooses correspondences, never punches individual native-face holes',
            'membership': 'One scalar per native point using its interpolated surface orientation',
            'footprint_projection_role': 'Forward surface-proximity constraint and fit/coverage diagnostics; internal projected triangle edges do not define borders',
            'correspondence_sample_count': len(points), 'correspondence_p95_distance': p95,
            'native_bone_correspondence_p95_distance': correspondence.native_distance_p95,
            'correspondence_fit_limit': correspondence.fit_limit,
            'correspondence_fit_basis': 'Fixed 4.5 display-unit limit, independent of whole-bone mismatch',
            'correspondence_fit_surface': 'Transported author selection, not the entire source bone',
            'fit_rejection_scope': 'whole region, never individual interior triangles'}


VAULT_BONES = ('frontal-bone', 'parietal-bone-left', 'parietal-bone-right', 'occipital-bone')


def semantic_compartment(identity, owner):
    if owner not in VAULT_BONES:
        return None
    if re.search(r'internal|cerebral|meningeal|sagittal-sinus|transverse-sinus', identity):
        return 'inner'
    if re.search(r'external|nuchal-line', identity):
        return 'outer'
    return None


def vault_center(bones):
    return np.average([bones[owner].centroid for owner in VAULT_BONES], axis=0,
                      weights=[bones[owner].area for owner in VAULT_BONES])


def radial_signature(triangles, normals, center):
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2
    radial = triangles.mean(axis=1) - center
    radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
    alignment = np.sum(normals * radial, axis=1)
    return {'mean_alignment': float(np.average(alignment, weights=areas)),
            'inner_fraction': float(areas[alignment < -0.1].sum() / areas.sum()),
            'outer_fraction': float(areas[alignment > 0.1].sum() / areas.sum())}


def check_surface_side(owner, patch, native, positions, faces, source_center, target_center, source_bone):
    if owner not in VAULT_BONES:
        return {'scope': 'orientation-constrained surface correspondence; radial compartment test not applicable to this bone'}
    original = patch.metadata['original_source_surface']
    source = radial_signature(original.triangles, original.face_normals, source_center)
    target = radial_signature(positions, native.face_normals[faces], target_center)
    side = ('inner' if source['mean_alignment'] < -0.35 and source['outer_fraction'] <= 0.01 else
            'outer' if source['mean_alignment'] > 0.35 and source['inner_fraction'] <= 0.01 else 'mixed')
    spill = target['outer_fraction'] if side == 'inner' else target['inner_fraction'] if side == 'outer' else None
    if spill is not None and spill > 0.01:
        raise ValueError(f'Source {side} cranial surface spills {spill:.1%} of area onto the opposite native surface; limit 1%')
    exposure = None
    if side in {'inner', 'outer'}:
        native_region = trimesh.Trimesh(positions.reshape(-1, 3), np.arange(positions.size // 3).reshape(-1, 3), process=False)
        original_points = trimesh.sample.sample_surface(original, 500, seed=6942)[0]
        native_points = trimesh.sample.sample_surface(native_region, 500, seed=6942)[0]
        exposure = {'source': Surface(source_bone).cortical_exposure(original_points, source_center, side),
                    'native': Surface(native).cortical_exposure(native_points, target_center, side),
                    'scope': 'First-hit cortical exposure from seven cavity or exterior viewpoints; not anatomical boundary validation'}
        if exposure['source']['exposed_fraction'] < 0.95:
            raise ValueError(f'{side.capitalize()} source selection has no resolved exposed-cortex footprint; transfer withheld')
        if exposure['native']['exposed_fraction'] < 0.95:
            raise ValueError(f"{side.capitalize()} source footprint is exposed but only {exposure['native']['exposed_fraction']:.1%} of the native transfer reaches its exposed cortex; buried surface transfer withheld")
    return {'scope': 'Source-derived cranial vault inner/outer surface invariant, not anatomical boundary validation',
            'source_compartment': side, 'source_signature': source, 'native_signature': target,
            'cavity_exposure_check': exposure,
            'opposite_surface_area_fraction': spill, 'maximum_opposite_surface_fraction': 0.01,
            'source_cranial_center': source_center.round(8).tolist(), 'native_cranial_center': target_center.round(8).tolist()}


def close_wall_gaps(native, wall, facing):
    """Retain the source outline and repair enclosed, angle-rejected native faces."""
    field = RegionField(wall, check_normals=False)
    positions, faces = clip_native_region(native, wall, field=field)
    original_side = native.face_normals @ facing > 0.15
    selected = np.zeros(len(native.faces), dtype=bool)
    selected[np.unique(faces[original_side[faces]])] = True
    # Only a complete native face inside the original author-derived footprint
    # can be repaired. Partial outline faces and source-authored holes cannot grow.
    area = trimesh.triangles.area(positions.astype(float))
    covered = np.bincount(faces.astype(int), weights=area, minlength=len(native.faces))
    full = (covered > 1e-10) & (covered >= native.area_faces * 0.999)
    candidate = full & ~selected
    adjacency = [[] for _ in range(len(native.faces))]
    for a, b in native.face_adjacency:
        adjacency[a].append(int(b)); adjacency[b].append(int(a))
    repairs = np.zeros(len(native.faces), dtype=bool)
    seen = np.zeros(len(native.faces), dtype=bool)
    for seed in np.flatnonzero(candidate):
        if seen[seed]: continue
        component, pending, enclosed = [], [int(seed)], True
        seen[seed] = True
        while pending:
            face = pending.pop(); component.append(face)
            # Missing/nonmanifold neighbours are real mesh boundaries, never fill.
            if len(adjacency[face]) != 3: enclosed = False
            for neighbor in adjacency[face]:
                if selected[neighbor]: continue
                if not candidate[neighbor]:
                    enclosed = False
                elif not seen[neighbor]:
                    seen[neighbor] = True; pending.append(neighbor)
        if enclosed:
            repairs[component] = True
    keep = original_side[faces] | repairs[faces]
    return positions[keep], faces[keep], {
        'method': 'Fill only topology-enclosed native faces rejected by the per-face angle test, wholly inside the unchanged source-derived wall outline',
        'repaired_native_faces': np.flatnonzero(repairs).tolist(),
        'repaired_area': float(covered[repairs].sum()),
        'source_holes_preserved': True, 'native_openings_preserved': True,
        'boundary_expansion': False,
    }


def transfer_fossafinder(dataset, target, manifest, cache, lock, records, pieces, fits, rejected):
    source, walls, assembly = fossafinder_meshes(cache)
    owners = ['sphenoid-bone', 'maxilla-left', 'maxilla-right', 'palatine-bone-left', 'palatine-bone-right']
    native = trimesh.util.concatenate([target[owner] for owner in owners])
    initial = np.eye(4)
    initial[:3, :3] *= float(np.mean(native.extents / source.extents))
    initial[:3, 3] = native.bounds.mean(axis=0) - apply(source.bounds.mean(axis=0)[None], initial)[0]
    center_x = float(source.bounds[:, 0].mean())
    definitions = [('Anterior_Wall', 'maxilla-{side}', 'skull__visuals.fbx'),
                   ('Posterior_Wall', 'sphenoid-bone', 'posterior.fbx'),
                   ('Medial_Wall', 'palatine-bone-{side}', 'medial.fbx')]
    parts, constraints = [], {}
    for side in ['left', 'right']:
        for name, owner_pattern, filename in definitions:
            owner = owner_pattern.format(side=side)
            part = walls[name].copy()
            reflected = bool((part.centroid[0] > center_x) != (side == 'left'))
            if reflected:
                reflection = np.diag([-1, 1, 1, 1])
                reflection[0, 3] = 2 * center_x
                part.apply_transform(reflection)
            constraint_id = f'{side}:{name}'
            constraints[constraint_id] = (part, target[owner])
            parts.append((side, name, owner, filename, reflected, constraint_id))
    print(f'Fitting {dataset}:FossaFinder midface with named wall/bone constraints', flush=True)
    registration = BoneRegistration(source, native, initial, constraints=constraints)
    key = f'{dataset}:fossafinder-midface'
    fits[key] = {**registration.metrics, **assembly}
    for side, name, owner, filename, reflected, constraint_id in parts:
        entry_id = f'{side}-pterygopalatine-fossa'
        try:
            part = constraints[constraint_id][0].copy()
            moved = registration.move(part.vertices)
            projected, normals, _, distances = registration.constraint_targets[constraint_id].nearest(moved)
            p95 = float(np.quantile(distances, 0.95))
            if p95 > 3:
                raise ValueError(f'Named wall transfer p95 distance {p95:.3f} exceeds 3 display units')
            part.vertices = projected
            if np.median(np.sum(part.vertex_normals * normals, axis=1)) < 0:
                part.invert()
            part.update_faces(part.nondegenerate_faces(height=1e-7))
            part.remove_unreferenced_vertices()
            facing = registration.constraint_directions[constraint_id]
            positions, faces, continuity = close_wall_gaps(target[owner], part, facing)
            triangle = positions.astype(float)
            areas = np.linalg.norm(np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0]), axis=1) / 2
            if areas.sum() < 0.05:
                raise ValueError('Named source wall has negligible intersection with this bone')
            center = np.average(triangle.mean(axis=1), axis=0, weights=areas)
            anchor = triangle.mean(axis=1)[np.argmin(np.linalg.norm(triangle.mean(axis=1) - center, axis=1))]
            records.append({'dataset': dataset, 'feature_id': entry_id, 'owner': owner, 'source_id': 'fossafinder',
                            'source_name': f'Pterygopalatine fossa — authored {name.replace("_", " ")}',
                            'source_object_id': f'Assets/Scenes/Main.unity:{name}',
                            'source_geometry_sha256': next(f['sha256'] for f in lock['files'] if f['path'] == f'fossafinder/{filename}'),
                            'source_url': lock['fossafinder']['url'], 'source_model_sha256': manifest['sha256'],
                            'method': 'registered-author-surface', 'approximate': True, 'independently_validated': False,
                            'anatomical_accuracy_percent': None, 'mapping_scope': 'cross-specimen-source-region',
                            'identity_evidence': 'Explicitly named anterior, posterior and medial wall meshes and wall/bone descriptions in the university scene; full scene transform chains. Source side inferred from skull axes; reflection is estimated.',
                            'fit_id': key, 'fit': {**registration.metrics['wall_constraints'][constraint_id],
                                                   'mirrored_source': reflected, 'source_wall': name,
                                                   'source_representation': 'explicitly authored open bony wall surface',
                                                   'bone_constraint': owner, 'minimum_facing_normal_alignment': 0.15,
                                                   'facing_normal_direction': facing.tolist(),
                                                   'surface_correspondence_version': 2,
                                                   'continuity_repair': continuity,
                                                   'maximum_projection_distance': float(distances.max()),
                                                   'source_vertices_within_3_units_fraction': float(np.mean(distances <= 3)),
                                                   'intersection_tolerance': 0,
                                                   'native_clipping_resolution': 0.8, 'minimum_native_face_altitude': 0.01},
                            'center': center.round(6).tolist(), 'surface_anchor': anchor.round(6).tolist(),
                            'area': float(areas.sum()), 'triangle_count': len(faces),
                            'coordinate_units': 'millimetres' if dataset == 'reference' else 'uncalibrated-display-units'})
            pieces.append((positions, faces))
            print(f'Mapped {dataset}:{entry_id} / {owner} / {name}: {len(faces)} native triangles', flush=True)
        except ValueError as error:
            rejected.append({'dataset': dataset, 'feature_id': entry_id, 'owner': owner, 'source': 'FossaFinder',
                             'source_wall': name, 'reason': str(error)})


def export(cache: Path, output: Path, datasets: list[str], fossae_only: bool = False, update: bool = False,
           features: set[str] | None = None) -> dict:
    lock = json.loads((cache / 'sources.json').read_text())
    verify(cache, lock)
    catalogue = json.loads((ROOT / 'assets/landmarks/catalogue.json').read_text())['landmarks']
    bones = source_bones(cache, lock)
    source_cranial_center = vault_center(bones)
    records, pieces, rejected, unmatched, models, fits = [], [], [], [], {}, {}
    if update or features:
        previous = json.loads((output / 'registered-regions.json').read_text())
        raw = (ROOT / previous['binary']['url']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != previous['binary']['sha256']:
            raise ValueError('Existing registered geometry checksum changed')
        keep = lambda record: record.get('dataset') not in datasets or features is not None and record.get('feature_id') not in features
        records = [record for record in previous['regions'] if keep(record)]
        pieces = [(np.frombuffer(raw, '<f4', r['positions_count'], r['positions_byte_offset']).reshape(-1, 3, 3),
                   np.frombuffer(raw, '<u4', r['source_faces_count'], r['source_faces_byte_offset'])) for r in records]
        rejected = [record for record in previous['rejected'] if keep(record)]
        unmatched = [record for record in previous['unmatched'] if features or record.get('dataset') not in datasets]
        models = {key: value for key, value in previous['models'].items() if features or key not in datasets}
        fits = {key: value for key, value in previous['registrations'].items() if features or key.split(':')[0] not in datasets}
    source_records = [r for r in lock['biodigital']['records'] if r['role'] == 'region'
                      and (not fossae_only or 'fossa' in r['name'].lower())]
    for dataset in datasets:
        directory = 'skull-ct' if dataset == 'ct' else 'skull'
        manifest = json.loads((ROOT / f'assets/{directory}/manifest.json').read_text())
        if digest(ROOT / manifest['model']) != manifest['sha256']:
            raise ValueError(f'{dataset} target model checksum changed')
        target = load_native_bones(ROOT / manifest['model'])
        target_cranial_center = vault_center(target)
        models[dataset] = {'model': manifest['model'], 'sha256': manifest['sha256'],
                           'winding_corrections': {owner: bone.metadata['winding_corrections'] for owner, bone in target.items() if bone.metadata['winding_corrections']}}
        common = [identity for identity in bones if identity in target]
        initial = similarity(np.array([bones[i].centroid for i in common]), np.array([target[i].centroid for i in common]))
        registrations, correspondences = {}, {}
        for record in source_records:
            candidates = matching_features(record, catalogue)
            if features:
                candidates = [entry for entry in candidates if entry['id'] in features]
                if not candidates:
                    continue
            if not candidates:
                unmatched.append({'source': record['name'], 'dataset': dataset, 'reason': 'No exact catalogue name/alias and owning-bone match'})
                continue
            try:
                mask = source_mesh(cache, lock, record)
            except ValueError as error:
                rejected.append({'source': record['name'], 'dataset': dataset, 'reason': str(error)})
                continue
            for entry in candidates:
                if dataset not in entry['positions']:
                    continue
                if entry['id'] in {'bregma', 'lambda'}:
                    unmatched.append({'source': record['name'], 'dataset': dataset,
                                      'reason': 'Point landmark uses the native three-bone junction fit; independent bone-area masks are not meaningful.'})
                    continue
                for owner in entry['owners']:
                    if owner not in bones or owner not in target:
                        continue
                    try:
                        key = f'{dataset}:{owner}'
                        if owner not in registrations:
                            print(f'Fitting {key}', flush=True)
                            registrations[owner] = BoneRegistration(bones[owner], target[owner], initial)
                            fits[key] = registrations[owner].metrics
                            correspondences[owner] = BoneSurfaceCorrespondence(bones[owner], registrations[owner],
                                source_cranial_center if owner in VAULT_BONES else None,
                                target_cranial_center if owner in VAULT_BONES else None)
                        registration = registrations[owner]
                        side = entry['id'].split('-')[0] if entry['id'].startswith(('left-', 'right-')) else None
                        compartment = semantic_compartment(entry['id'], owner)
                        patch, fit, transported_normals, boundaries = prepare_patch(mask, bones[owner], target[owner], registration, side,
                            source_cranial_center if owner in VAULT_BONES else None, compartment)
                        correspondence = correspondences[owner]
                        positions, faces = clip_native_region(target[owner], patch, padding=2,
                                                             field=correspondence.field(mask, side, patch, compartment))
                        try:
                            fit.update(check_correspondence_fit(correspondence, patch, positions))
                        except ValueError as first_error:
                            # Refine to the already-declared source proximity
                            # limit. Never increase the limit to admit a fringe.
                            print(f"Refining {dataset}:{entry['id']} / {owner}: {first_error}", flush=True)
                            positions, faces = clip_native_region(target[owner], patch, padding=2,
                                field=correspondence.field(mask, side, patch, compartment, source_limit=4.5))
                            fit.update(check_correspondence_fit(correspondence, patch, positions))
                            fit['source_proximity_fringe_removed'] = True
                        fit.update(check_footprint_coverage(patch, positions, registration.target, transported_normals,
                                                            maximum_distance=max(1.0, fit['before_projection_p95_distance'])))
                        fit['surface_side_check'] = check_surface_side(owner, patch, target[owner], positions, faces,
                                                                       source_cranial_center, target_cranial_center, bones[owner])
                        fit.update(source_representation='closed selection volume' if mask.is_watertight else 'open surface selection',
                                   intersection_tolerance=0,
                                   native_clipping_resolution=0.8, minimum_native_face_altitude=0.01)
                        triangles = positions.astype(float)
                        areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2
                        if areas.sum() < 0.05:
                            raise ValueError('Transfer contains less than 0.05 square display units of native bone')
                        center = np.average(triangles.mean(axis=1), axis=0, weights=areas)
                        anchor = triangles.mean(axis=1)[np.argmin(np.linalg.norm(triangles.mean(axis=1) - center, axis=1))]
                        records.append({'dataset': dataset, 'feature_id': entry['id'], 'owner': owner,
                                        'source_id': 'biodigital', 'source_name': record['name'], 'source_object_id': record['object_id'],
                                        'source_geometry_sha256': lock['biodigital']['assets'][asset_id('geometry', record['geometry_id'])]['sha256'],
                                        'source_url': lock['biodigital']['url'], 'source_model_sha256': manifest['sha256'],
                                        'method': 'registered-author-surface', 'approximate': True, 'independently_validated': False,
                                        'anatomical_accuracy_percent': None, 'mapping_scope': 'cross-specimen-source-region',
                                        'identity_evidence': 'Exact normalized source name/explicit alias, constrained by source bone hierarchy',
                                        'fit_id': key, 'fit': fit, 'center': center.round(6).tolist(),
                                        'surface_anchor': anchor.round(6).tolist(), 'area': float(areas.sum()),
                                        'triangle_count': len(faces), 'coordinate_units': 'millimetres' if dataset == 'reference' else 'uncalibrated-display-units'})
                        pieces.append((positions, faces))
                        print(f"Mapped {dataset}:{entry['id']} / {owner}: {len(faces)} native triangles", flush=True)
                    except ValueError as error:
                        rejected.append({'dataset': dataset, 'feature_id': entry['id'], 'owner': owner, 'source': record['name'], 'reason': str(error)})
                        print(f"Withheld {dataset}:{entry['id']} / {owner}: {error}", flush=True)
        if not features:
            transfer_fossafinder(dataset, target, manifest, cache, lock, records, pieces, fits, rejected)
    output.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 1, 'title': 'Published skull regions registered onto native specimen surfaces',
              'anatomical_accuracy_percent': None, 'approximate': True,
              'validation_note': 'File integrity, bone registration residuals and native-triangle membership are checked. These are cross-specimen estimates, not independently validated anatomical boundaries.',
              'models': models, 'sources': [{**{k: v for k, v in lock['biodigital'].items() if k not in {'records', 'assets'}}, 'id': 'biodigital'},
                                           {**lock['fossafinder'], 'id': 'fossafinder'}],
              'source_files': lock['files'], 'registrations': fits, 'regions': records, 'rejected': rejected, 'unmatched': unmatched,
              'binary': write_binary(records, pieces, output)}
    serialized = json.dumps(result, indent=2) + '\n'
    (output / 'registered-regions.json.partial').write_text(serialized)
    (output / 'registered-regions.bin.partial').replace(output / 'registered-regions.bin')
    (output / 'registered-regions.json.partial').replace(output / 'registered-regions.json')
    print(json.dumps({'mapped_parts': len(records), 'features_by_dataset': {d: len({r['feature_id'] for r in records if r['dataset'] == d}) for d in datasets},
                      'rejected': len(rejected), 'unmatched': len(unmatched)}, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=CACHE)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--dataset', choices=['ct', 'reference', 'both'], default='both')
    parser.add_argument('--fossae-only', action='store_true', help='Transfer only explicitly named source fossa regions')
    parser.add_argument('--fossafinder-only', action='store_true', help='Rebuild only the UBC transfers in an existing complete transfer package')
    parser.add_argument('--update', action='store_true', help='Replace the selected datasets while retaining the other dataset in an existing package')
    parser.add_argument('--feature', action='append', help='Rebuild one BioDigital feature ID in an existing complete package; repeat for several features')
    args = parser.parse_args()
    datasets = ['ct', 'reference'] if args.dataset == 'both' else [args.dataset]
    if args.fossafinder_only:
        lock = json.loads((args.cache / 'sources.json').read_text())
        verify(args.cache, lock)
        path = args.output / 'registered-regions.json'
        data = json.loads(path.read_text())
        binary_path = ROOT / data['binary']['url']
        raw = binary_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != data['binary']['sha256']:
            raise ValueError('Existing registered geometry checksum changed')
        records = [record for record in data['regions'] if record['source_id'] != 'fossafinder' or record['dataset'] not in datasets]
        pieces = [(np.frombuffer(raw, '<f4', r['positions_count'], r['positions_byte_offset']).reshape(-1, 3, 3),
                   np.frombuffer(raw, '<u4', r['source_faces_count'], r['source_faces_byte_offset'])) for r in records]
        rejected = [r for r in data['rejected'] if r['source'] != 'FossaFinder' or r['dataset'] not in datasets]
        for dataset in datasets:
            directory = 'skull-ct' if dataset == 'ct' else 'skull'
            manifest = json.loads((ROOT / f'assets/{directory}/manifest.json').read_text())
            if digest(ROOT / manifest['model']) != data['models'][dataset]['sha256']:
                raise ValueError('Target model identity changed')
            target = load_native_bones(ROOT / manifest['model'])
            transfer_fossafinder(dataset, target, manifest, args.cache, lock, records, pieces, data['registrations'], rejected)
        data.update(regions=records, rejected=rejected, source_files=lock['files'], binary=write_binary(records, pieces, args.output))
        serialized = json.dumps(data, indent=2) + '\n'
        (args.output / 'registered-regions.json.partial').write_text(serialized)
        (args.output / 'registered-regions.bin.partial').replace(args.output / 'registered-regions.bin')
        (args.output / 'registered-regions.json.partial').replace(path)
    else:
        if args.feature and any('pterygopalatine-fossa' in identity for identity in args.feature):
            parser.error('Use --fossafinder-only for pterygopalatine wall transfers')
        export(args.cache, args.output, datasets, args.fossae_only, update=args.update,
               features=set(args.feature) if args.feature else None)


if __name__ == '__main__':
    main()
