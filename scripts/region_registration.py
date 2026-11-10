"""Geometry utilities for transferring published surface selections between skulls."""

from __future__ import annotations

import json
from pathlib import Path
import struct
import re

import bpy  # noqa: F401; registers mathutils for the standalone bpy wheel
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from scipy.interpolate import RBFInterpolator
import trimesh

from fetch_region_sources import asset_id


def load_native_bones(path: Path) -> dict:
    """Respect supplied GLB normals on mirrored bones, retaining native face IDs."""
    bones = trimesh.load(path, force='scene', process=False).geometry
    for bone in bones.values():
        normals = bone.vertex_normals.copy()
        supplied = normals[bone.faces].mean(axis=1)
        flipped = np.sum(bone.face_normals * supplied, axis=1) < 0
        faces = bone.faces.copy()
        faces[flipped] = faces[flipped][:, [0, 2, 1]]
        bone.faces = faces
        bone.vertex_normals = normals
        bone.metadata['winding_corrections'] = int(flipped.sum())
    return bones


def decode_geometry(raw: bytes) -> trimesh.Trimesh:
    """Decode the viewer's unencrypted v17 float/v18 quantized triangle buffers."""
    header = struct.unpack_from('<9I', raw)
    version, id_length, comments = header[:3]
    if version == 17:
        fields = struct.unpack_from('<11I', raw)
        offset = 44 + id_length + comments
        vertices = np.frombuffer(raw, '<f4', fields[3] // 4, offset).reshape(-1, 3).astype(float)
        indices = np.frombuffer(raw, '<u4', fields[5] // 4, offset + fields[3] + fields[4])
    elif version == 18:
        _, _, _, header_length, index_offset, positions_length, _normals_length, index_length, _ = header
        transform_length, index_width, uv_count = struct.unpack_from('<HBB', raw, 32)
        offset = header_length + id_length + comments
        transform = np.frombuffer(raw, '<f4', transform_length // 4, offset).astype(float)
        offset += transform_length + 16 * uv_count
        vertices = np.frombuffer(raw, '<u2', positions_length // 2, offset).reshape(-1, 3).astype(float)
        if transform_length:
            if transform_length != 24:
                raise ValueError('Unknown position quantization transform')
            vertices = vertices * transform[3:6] + transform[:3]
        indices = np.frombuffer(raw, {1: 'u1', 2: '<u2', 4: '<u4'}[index_width], index_length // index_width, index_offset)
    else:
        raise ValueError(f'Unsupported geometry version {version}')
    if not len(vertices) or len(indices) % 3 or indices.max() >= len(vertices) or not np.isfinite(vertices).all():
        raise ValueError('Source does not contain valid finite triangles')
    # Welding duplicated normal/UV vertices recovers actual open region edges.
    return trimesh.Trimesh(vertices, indices.reshape(-1, 3), process=True)


def transform_matrix(value: dict) -> np.ndarray:
    def xyz(name: str, default: float = 0) -> np.ndarray:
        return np.array([value.get(name, {}).get(axis, default) for axis in 'xyz'], float)

    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_euler('xyz', xyz('rotate'), degrees=True).as_matrix() @ np.diag(xyz('scale', 1))
    pivot = xyz('pivot')
    matrix[:3, 3] = xyz('translate') + pivot - matrix[:3, :3] @ pivot
    return matrix


def source_mesh(cache: Path, source: dict, record: dict) -> trimesh.Trimesh:
    assets = source['biodigital']['assets']
    entry = assets[asset_id('geometry', record['geometry_id'])]
    if 'error' in entry:
        raise ValueError(f"Source geometry unavailable: {record['name']}")
    mesh = decode_geometry((cache / entry['path']).read_bytes())
    matrix = np.eye(4)
    for identity in record['transforms']:
        asset = assets[asset_id('transforms', identity)]
        if 'error' in asset:
            raise ValueError(f"Source transform unavailable: {record['name']}")
        matrix = matrix @ transform_matrix(json.loads((cache / asset['path']).read_text()))
    mesh.apply_transform(matrix)
    return mesh


def fossafinder_meshes(cache: Path) -> tuple[trimesh.Trimesh, dict[str, trimesh.Trimesh], dict]:
    """Reassemble the named bony wall surfaces and their source skull, not air caps."""
    text = (cache / 'fossafinder/Main.unity').read_text()
    names, transforms, filters = {}, {}, {}
    for kind, identity, body in re.findall(r'--- !u!(\d+) &(\d+)\n(.*?)(?=--- !u!|\Z)', text, re.S):
        if kind == '1':
            names[identity] = re.search(r'  m_Name: (.*)', body)[1]
        elif kind == '4':
            match = re.search(r'm_GameObject: \{fileID: (\d+)', body)
            if not match:
                continue
            game = match[1]
            matrix = np.eye(4)
            def values(field, axes):
                line = re.search(field + r': \{([^}]+)', body)[1]
                parts = dict(re.findall(r'(\w): ([-\d.eE+]+)', line))
                return np.array([float(parts[a]) for a in axes])
            matrix[:3, :3] = Rotation.from_quat(values('m_LocalRotation', 'xyzw')).as_matrix() @ np.diag(values('m_LocalScale', 'xyz'))
            matrix[:3, 3] = values('m_LocalPosition', 'xyz')
            parent = re.search(r'm_Father: \{fileID: (\d+)', body)[1]
            transforms[identity] = {'matrix': matrix, 'parent': parent, 'game': game}
        elif kind == '33':
            game = re.search(r'm_GameObject: \{fileID: (\d+)', body)[1]
            match = re.search(r'm_Mesh: \{fileID: (\d+), guid: ([a-f0-9]+)', body)
            if match:
                filters[game] = match.groups()
    by_game = {value['game']: identity for identity, value in transforms.items()}

    def relative_to_head(game):
        chain, identity = [], by_game[game]
        while identity in transforms:
            value = transforms[identity]
            if names.get(value['game']) == 'Head':
                result = np.eye(4)
                for local in reversed(chain):
                    result = result @ local
                return result
            chain.append(value['matrix'])
            identity = value['parent']
        return None

    def assembled(name, filename):
        meta = (cache / f'fossafinder/{filename}.meta').read_text()
        guid = re.search(r'^guid: (.+)$', meta, re.M)[1]
        file_names = dict(re.findall(r'^    (430\d+): (.+)$', meta, re.M))
        choices = [(game, relative_to_head(game)) for game, value in names.items()
                   if value == name and game in filters and filters[game][1] == guid]
        game, matrix = next((game, matrix) for game, matrix in choices if matrix is not None)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(cache / f'fossafinder/{filename}'), use_anim=False)
        objects = {re.sub(r'\.\d+$', '', obj.name): obj for obj in bpy.data.objects if obj.type == 'MESH'}
        obj = objects[file_names[filters[game][0]]]
        obj.data.calc_loop_triangles()
        mesh = trimesh.Trimesh(np.array([tuple(vertex.co) for vertex in obj.data.vertices]),
                               np.array([tuple(face.vertices) for face in obj.data.loop_triangles]), process=True)
        # Unity's FBX importer changes handedness and converts centimetres to
        # metres. The prefab stores its authored object offsets in metres.
        mesh.apply_transform(np.diag([-0.01, 0.01, 0.01, 1]))
        mesh.apply_transform(matrix)
        mesh.apply_scale(1000)  # Work in millimetres before specimen registration.
        return mesh

    skull = assembled('PPF_Region_v5', 'PPF_Region_v5.fbx')
    walls = {
        'Anterior_Wall': assembled('Anterior_Wall', 'skull__visuals.fbx'),
        'Posterior_Wall': assembled('Posterior_Wall', 'posterior.fbx'),
        'Medial_Wall': assembled('Medial_Wall', 'medial.fbx'),
    }
    # The scene contains a separate cavity object, whose position is not shared
    # by these explicitly labelled wall meshes. Cropping that volume by distance
    # to bone loses the anterior and medial walls. Use the author's wall outlines.
    interior = np.mean([wall.centroid for wall in walls.values()], axis=0)
    for wall in walls.values():
        normal = np.average(wall.face_normals, axis=0, weights=wall.area_faces)
        if np.dot(normal, interior - wall.centroid) < 0:
            wall.invert()
    note = {'assembly': 'Pinned Main.unity mesh references and full transform chains relative to Head; FBX centimetres converted through Unity left-handed metres',
            'region_objects': list(walls), 'source_laterality': 'one authored side; opposite side reflected through source skull midline',
            'source_skull_bounds': skull.bounds.round(6).tolist(),
            'source_wall_bounds': {name: wall.bounds.round(6).tolist() for name, wall in walls.items()},
            'wall_owner_evidence': 'Main.unity wall labels describe posterior maxilla, lateral perpendicular plate of palatine, and anterior pterygoid surface of sphenoid.'}
    return skull, walls, note


class Surface:
    def __init__(self, mesh: trimesh.Trimesh):
        self.mesh = mesh
        self.tree = BVHTree.FromPolygons(mesh.vertices.tolist(), mesh.faces.tolist(), all_triangles=True)
        self.directional = {}

    def nearest(self, points: np.ndarray):
        hits = [self.tree.find_nearest(Vector(point.tolist())) for point in points]
        return (np.array([tuple(hit[0]) for hit in hits]), np.array([tuple(hit[1]) for hit in hits]),
                np.array([hit[2] for hit in hits], dtype=np.int64), np.array([hit[3] for hit in hits]))

    def smooth_normals(self, points: np.ndarray) -> np.ndarray:
        closest, _, faces, _ = self.nearest(points)
        barycentric = trimesh.triangles.points_to_barycentric(self.mesh.triangles[faces], closest)
        normals = np.sum(self.mesh.vertex_normals[self.mesh.faces[faces]] * barycentric[:, :, None], axis=1)
        return normals / np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-12)

    def oriented_nearest(self, points: np.ndarray, normals: np.ndarray):
        """Project onto the same facing side of a bone, including thin processes."""
        directions = np.array([(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1) if x or y or z], float)
        directions /= np.linalg.norm(directions, axis=1)[:, None]
        groups = np.argmax(normals @ directions.T, axis=1)
        positions, output_normals = np.empty_like(points), np.empty_like(points)
        faces, distances = np.empty(len(points), int), np.empty(len(points))
        for group in np.unique(groups):
            if group not in self.directional:
                selected = np.flatnonzero(self.mesh.face_normals @ directions[group] > 0.7)
                if not len(selected):
                    raise ValueError('Owning bone has no surface facing the source footprint')
                self.directional[group] = (Surface(self.mesh.submesh([selected], append=True)), selected)
            surface, parents = self.directional[group]
            subset = np.flatnonzero(groups == group)
            positions[subset], output_normals[subset], local_faces, distances[subset] = surface.nearest(points[subset])
            faces[subset] = parents[local_faces]
        return positions, output_normals, faces, distances

    def compatible_nearest(self, points: np.ndarray, normals: np.ndarray):
        """Keep a nearby same-facing correspondence; replace opposite surfaces."""
        positions, output_normals, faces, distances = self.nearest(points)
        # Ridge sidewalls can be nearly perpendicular to an interpolated native
        # normal. Replace genuinely opposing surfaces, not those tangent faces.
        opposite = np.sum(output_normals * normals, axis=1) < -0.2
        if opposite.any():
            positions[opposite], output_normals[opposite], faces[opposite], distances[opposite] = self.oriented_nearest(points[opposite], normals[opposite])
        return positions, output_normals, faces, distances

    def cortical_exposure(self, points: np.ndarray, center: np.ndarray, compartment: str) -> dict:
        """Check that a vault footprint reaches its expected exposed cortex."""
        span = float(np.ptp(self.mesh.vertices, axis=0).max())
        tolerance = span * 0.0002
        offsets = np.vstack([np.zeros(3), np.eye(3) * span * 0.15, -np.eye(3) * span * 0.15])
        origins = np.tile(center, (len(points), 1))
        if compartment == 'outer':
            radial = points - center
            radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
            origins = center + radial * span * 3
        depth = np.full(len(points), np.inf)
        for offset in offsets:
            for index, (point, origin) in enumerate(zip(points, origins + offset)):
                direction = point - origin
                distance = np.linalg.norm(direction)
                hit = self.tree.ray_cast(Vector(origin.tolist()), Vector((direction / distance).tolist()))
                if hit[0] is not None:
                    depth[index] = min(depth[index], abs(distance - hit[3]))
        return {'exposed_fraction': float(np.mean(depth <= tolerance)),
                'median_burial_depth': float(np.median(depth)), 'p95_burial_depth': float(np.quantile(depth, 0.95)),
                'sample_count': len(points), 'viewpoint_count': len(offsets), 'viewpoint_offset': span * 0.15,
                'depth_tolerance': tolerance, 'minimum_exposed_fraction': 0.95, 'compartment': compartment}

    def contains(self, points: np.ndarray) -> np.ndarray:
        # BVH ray parity keeps memory bounded for large selection volumes.
        direction = Vector((0.925207, 0.267341, 0.270455)).normalized()
        result = np.zeros(len(points), dtype=bool)
        lower, upper = self.mesh.bounds
        eligible = np.where(((points >= lower) & (points <= upper)).all(axis=1))[0]
        for index in eligible:
            origin = Vector(points[index].tolist())
            intersections = 0
            for _ in range(128):
                hit, normal, _, _ = self.tree.ray_cast(origin, direction)
                if hit is None:
                    break
                intersections += 1
                origin = hit + direction * 1e-4 + normal * (1e-4 if normal.dot(direction) >= 0 else -1e-4)
            else:
                raise ValueError('Source volume ray did not leave the mesh')
            result[index] = intersections % 2 == 1
        return result


def apply(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return np.asarray(points) @ matrix[:3, :3].T + matrix[:3, 3]


def similarity(source: np.ndarray, target: np.ndarray, scale: bool = True) -> np.ndarray:
    source_center, target_center = source.mean(axis=0), target.mean(axis=0)
    x, y = source - source_center, target - target_center
    u, singular, vt = np.linalg.svd(x.T @ y)
    sign = np.diag([1, 1, np.linalg.det(vt.T @ u.T)])
    rotation = vt.T @ sign @ u.T
    factor = float(np.sum(singular * np.diag(sign)) / np.sum(x * x)) if scale else 1
    matrix = np.eye(4)
    matrix[:3, :3] = rotation * factor
    matrix[:3, 3] = target_center - matrix[:3, :3] @ source_center
    return matrix


def samples(mesh: trimesh.Trimesh, count: int = 5000) -> np.ndarray:
    # Fixed area samples prevent dense tessellation around one small feature
    # from determining the registration of the whole bone.
    return trimesh.sample.sample_surface(mesh, min(count, len(mesh.faces) * 2), seed=7284)[0]


def rigid_fit(source: trimesh.Trimesh, target: trimesh.Trimesh, initial: np.ndarray,
              iterations: int = 60, allow_scale: bool = True) -> tuple[np.ndarray, dict]:
    points = samples(source)
    vertices = np.unique(target.vertices, axis=0)
    tree = cKDTree(vertices)
    matrix = initial.copy()
    previous = float('inf')
    for iteration in range(iterations):
        moved = apply(points, matrix)
        distances, indices = tree.query(moved)
        selected = distances <= np.quantile(distances, 0.85)
        update = similarity(moved[selected], vertices[indices[selected]], scale=allow_scale and iteration < 25)
        matrix = update @ matrix
        error = float(np.mean(distances[selected] ** 2))
        if abs(previous - error) < 1e-7:
            break
        previous = error
    surface = Surface(target)
    _, _, _, distances = surface.nearest(apply(samples(source, 3000), matrix))
    return matrix, {'method': 'bone-centroid initialization and trimmed similarity ICP',
                    'iterations': iteration + 1, 'median_surface_distance': float(np.median(distances)),
                    'p95_surface_distance': float(np.quantile(distances, 0.95)),
                    'scale': float(np.cbrt(np.linalg.det(matrix[:3, :3]))), 'matrix': matrix.round(9).tolist()}


class BoneRegistration:
    """Bone alignment with optional source-named wall/bone correspondences."""
    def __init__(self, source: trimesh.Trimesh, target: trimesh.Trimesh, initial: np.ndarray,
                 constraints: dict[str, tuple[trimesh.Trimesh, trimesh.Trimesh]] | None = None):
        self.matrix, self.metrics = rigid_fit(source, target, initial)
        self.target = Surface(target)
        controls = apply(samples(source, 700), self.matrix)
        bone_count = len(controls)
        self.constraint_targets = {}
        self.constraint_directions = {}
        ranges, wall_probes = {}, []
        for name, (wall, bone) in (constraints or {}).items():
            normal = np.average(wall.face_normals, axis=0, weights=wall.area_faces)
            normal = np.linalg.inv(self.matrix[:3, :3]).T @ normal
            normal /= np.linalg.norm(normal)
            self.constraint_directions[name] = normal
            # Keep the side of the bone facing the authored space; nearest-point
            # matching to both sides can jump through a thin palatine plate.
            facing = bone.submesh([np.flatnonzero(bone.face_normals @ normal > 0.15)], append=True)
            self.constraint_targets[name] = Surface(facing)
            moved = apply(samples(wall, 100), self.matrix)
            ranges[name] = slice(len(controls), len(controls) + len(moved))
            controls = np.concatenate([controls, moved])
            wall_probes.append(apply(wall.vertices, self.matrix))
        probes = np.concatenate(wall_probes) if wall_probes else np.empty((0, 3))
        self.warps = []
        # Smooth displacement of shared source-bone controls is also applied to
        # each author's region. Labels/seeds never determine region boundaries.
        for iteration in range(12 if constraints else 2):
            smoothing = 4.0 if iteration == 0 else 1.5
            nearest, _, _, distances = self.target.nearest(controls[:bone_count])
            keep = distances <= np.quantile(distances, 0.95)
            source_groups = [controls[:bone_count][keep]]
            target_groups = [nearest[keep]]
            for name, interval in ranges.items():
                source_groups.append(controls[interval])
                target_groups.append(self.constraint_targets[name].nearest(controls[interval])[0])
            source_points = np.concatenate(source_groups)
            displacement = np.concatenate(target_groups) - source_points
            warp = RBFInterpolator(source_points, displacement, kernel='thin_plate_spline', smoothing=smoothing)
            probe = np.concatenate([controls[::3], probes])
            base = warp(probe)
            derivatives = np.stack([(warp(probe + np.eye(3)[axis] * 0.1) - base) / 0.1 for axis in range(3)], axis=2)
            weight = 1.0
            while np.linalg.det(np.eye(3) + weight * derivatives).min() < 0.1 and weight > 1 / 32:
                weight /= 2
            if np.linalg.det(np.eye(3) + weight * derivatives).min() < 0.1:
                raise ValueError('Registration deformation fails the sampled foldover check')
            self.warps.append((warp, weight))
            controls = controls + weight * warp(controls)
            probes = probes + weight * warp(probes) if len(probes) else probes
            self.metrics.setdefault('warp_checks', []).append({'weight': weight, 'sampled_minimum_jacobian': float(np.linalg.det(np.eye(3) + weight * derivatives).min())})
            if constraints and iteration >= 1 and all(
                np.quantile(self.constraint_targets[name].nearest(self.move(wall.vertices))[3], 0.95) <= 1.5
                for name, (wall, _) in constraints.items()
            ):
                break
        _, _, _, distances = self.target.nearest(controls[:bone_count])
        self.metrics['warped_control_median_distance'] = float(np.median(distances))
        self.metrics['warped_control_p95_distance'] = float(np.quantile(distances, 0.95))
        self.metrics['deformation'] = f'{len(self.warps)} regularized global thin-plate-spline displacement fields, {bone_count} bone surface controls; damping prevents sampled foldovers'
        if constraints:
            self.metrics['constraint_method'] = 'Each explicitly named wall is matched only to its source-described bone and facing surface; author outlines guide a shared smooth deformation.'
            self.metrics['wall_constraints'] = {
                name: {'source_vertices': len(wall.vertices), 'source_triangles': len(wall.faces),
                       'before_deformation_p95_distance': float(np.quantile(self.constraint_targets[name].nearest(apply(wall.vertices, self.matrix))[3], 0.95)),
                       'before_projection_p95_distance': float(np.quantile(self.constraint_targets[name].nearest(self.move(wall.vertices))[3], 0.95))}
                for name, (wall, _) in constraints.items()}

    def move(self, points: np.ndarray) -> np.ndarray:
        moved = apply(points, self.matrix)
        for warp, weight in self.warps:
            moved += weight * warp(moved)
        return moved


class RegionField:
    """Membership in an author's warped region volume or open surface outline."""
    def __init__(self, mesh: trimesh.Trimesh, maximum_distance: float = 1.8, side: str | None = None, midline: float = 0,
                 check_normals: bool = True, volume_tolerance: float = 0.35,
                 surface_normals: np.ndarray | None = None, force_surface: bool = False,
                 boundary_flags: np.ndarray | None = None):
        self.mesh = mesh
        self.surface = Surface(mesh)
        self.maximum_distance = maximum_distance
        self.closed = mesh.is_watertight and not force_surface
        self.side, self.midline = side, midline
        self.check_normals = check_normals
        self.volume_tolerance = volume_tolerance
        self.surface_normals = surface_normals
        if self.closed:
            return
        edges = mesh.edges_sorted
        unique, inverse, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        boundary = counts[inverse].reshape(-1, 3) == 1 if boundary_flags is None else boundary_flags
        if boundary.shape != (len(mesh.faces), 3):
            raise ValueError('Region contour flags do not match its triangles')
        # Face edges are (0,1), (1,2), (2,0); barycentric zeros are opposite.
        self.boundary_opposite = boundary[:, [1, 2, 0]]
        self.edges = mesh.vertices[mesh.edges[boundary.reshape(-1)]]
        self.edge_tree = cKDTree(self.edges.mean(axis=1)) if len(self.edges) else None

    def evaluate(self, points: np.ndarray, normals: np.ndarray) -> np.ndarray:
        closest, source_normals, faces, distances = self.surface.nearest(points)
        if self.closed:
            # Publisher regions are closed translucent selection volumes. Keep
            # that volume intact, intersecting it with the actual native cortex.
            # A fixed 0.35 display-unit tolerance accommodates registration and
            # quantization residuals; it is recorded, not claimed as exact anatomy.
            inside = self.surface.contains(points)
            signed = np.where(inside, distances, -distances) + self.volume_tolerance
            if self.side:
                signed = np.minimum(signed, (points[:, 0] - self.midline) * (1 if self.side == 'left' else -1))
            return signed
        if self.edge_tree is not None:
            bary = trimesh.triangles.points_to_barycentric(self.mesh.triangles[faces], closest)
            outside = ((bary < 2e-5) & self.boundary_opposite[faces]).any(axis=1)
            _, near_edges = self.edge_tree.query(points, k=min(12, len(self.edges)))
            near_edges = np.asarray(near_edges).reshape(len(points), -1)
            segments = self.edges[near_edges]
            a, delta = segments[:, :, 0], segments[:, :, 1] - segments[:, :, 0]
            relative = points[:, None, :] - a
            t = np.clip(np.sum(relative * delta, axis=2) / np.maximum(np.sum(delta * delta, axis=2), 1e-15), 0, 1)
            edge_distance = np.linalg.norm(relative - t[:, :, None] * delta, axis=2).min(axis=1)
            signed = np.where(outside, -np.maximum(distances, 1e-6), edge_distance)
        else:
            signed = np.full(len(points), self.maximum_distance)
        signed = np.minimum(signed, self.maximum_distance - distances)
        if self.side:
            signed = np.minimum(signed, (points[:, 0] - self.midline) * (1 if self.side == 'left' else -1))
        if not self.check_normals:
            return signed
        if self.surface_normals is not None:
            source_normals = self.surface_normals[faces]
        alignment = np.sum(source_normals * normals, axis=1)
        return np.minimum(signed, (alignment - 0.12) * self.maximum_distance)


class BoneSurfaceCorrespondence:
    """Pull labels through the complete deformed bone, keeping its face topology.

    Projecting each selection vertex separately onto a different specimen folds
    its triangles. Query the complete, unprojected deformed source bone instead,
    then use barycentric coordinates to recover a point on the original bone.
    The same native point consequently receives the same source label regardless
    of the native face's local normal or the selection's tessellation.
    """
    def __init__(self, source: trimesh.Trimesh, registration: BoneRegistration,
                 source_cranial_center=None, native_cranial_center=None):
        self.source = source
        self.registration = registration
        self.warped = source.copy()
        self.warped.vertices = registration.move(source.vertices)
        self.surface = Surface(self.warped)
        self.compartments = {}
        self.native_cranial_center = native_cranial_center
        if source_cranial_center is not None:
            radial = source.triangles_center - source_cranial_center
            radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
            signature = np.sum(source.face_normals * radial, axis=1)
            for side in [-1, 1]:
                selected = np.flatnonzero(signature * side >= -0.1)
                self.compartments[side] = (Surface(self.warped.submesh([selected], append=True)), selected)
        native_samples = samples(registration.target.mesh, 2000)
        self.native_distance_p95 = float(np.quantile(self.surface.nearest(native_samples)[3], 0.95))
        # Whole-bone mismatch is diagnostic only. It must not relax the region
        # limit and thereby admit the opposite surface of a thin cranial bone.
        self.fit_limit = 4.5

    def original_points(self, points: np.ndarray):
        normals = self.registration.target.smooth_normals(points)
        if self.compartments:
            radial = points - self.native_cranial_center
            radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
            signature = np.sum(normals * radial, axis=1)
            sides = np.where(signature > 0.1, 1, np.where(signature < -0.1, -1, 0))
            closest, faces, distances = np.empty_like(points), np.empty(len(points), int), np.empty(len(points))
            for side in np.unique(sides):
                subset = np.flatnonzero(sides == side)
                surface, parents = self.compartments.get(side, (self.surface, np.arange(len(self.source.faces))))
                closest[subset], _, local, distances[subset] = surface.compatible_nearest(points[subset], normals[subset])
                faces[subset] = parents[local]
        else:
            closest, _, faces, distances = self.surface.compatible_nearest(points, normals)
        barycentric = trimesh.triangles.points_to_barycentric(self.warped.triangles[faces], closest)
        original = np.sum(self.source.triangles[faces] * barycentric[:, :, None], axis=1)
        return original, faces, distances

    def field(self, region: trimesh.Trimesh, side: str | None = None, forward_patch=None,
              compartment=None, source_limit=None):
        return MappedRegionField(self, region, side, forward_patch, compartment, source_limit)


class MappedRegionField:
    """One source selection scalar per native point, rather than triangle gates."""
    closed = False
    normal_independent = True

    def __init__(self, correspondence: BoneSurfaceCorrespondence, region: trimesh.Trimesh, side: str | None,
                 forward_patch=None, compartment=None, source_limit=None):
        self.correspondence = correspondence
        self.scale = correspondence.registration.metrics['scale']
        self.forward_surface = Surface(forward_patch) if forward_patch is not None else None
        self.author_surface = Surface(forward_patch.metadata['coverage_source_surface']) if source_limit is not None else None
        self.source_limit, self.compartment = source_limit, compartment
        # BioDigital's named left side has negative X in the source frame.
        source_side = 'right' if side == 'left' else 'left' if side else None
        self.source_field = RegionField(region, side=source_side, midline=0,
                                        volume_tolerance=0.2 / self.scale,
                                        maximum_distance=1.8 / self.scale)

    def evaluate(self, points: np.ndarray, normals: np.ndarray) -> np.ndarray:
        original, faces, _ = self.correspondence.original_points(points)
        values = self.source_field.evaluate(original, self.correspondence.source.face_normals[faces]) * self.scale
        if self.forward_surface is not None:
            # The inverse map must agree with the author's forward-projected
            # surface. Bone-wide nearest labels alone can extrapolate across a
            # distant cortex. Distance to the triangle union ignores internal
            # subdivision edges and does not turn them into artificial borders.
            values = np.minimum(values, 1.8 - self.forward_surface.nearest(points)[3])
        if self.author_surface is not None:
            values = np.minimum(values, self.source_limit - self.author_surface.nearest(points)[3])
        if self.compartment in {'inner', 'outer'}:
            normal = self.correspondence.registration.target.smooth_normals(points)
            radial = points - self.correspondence.native_cranial_center
            radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
            sign = -1 if self.compartment == 'inner' else 1
            values = np.minimum(values, (0.1 + sign * np.sum(normal * radial, axis=1)) * 1.8)
        return values


def clip_native_region(target: trimesh.Trimesh, region: trimesh.Trimesh, step: float = 0.8,
                       side: str | None = None, facing: np.ndarray | None = None,
                       volume_tolerance: float = 0.35, padding: float = 2,
                       minimum_altitude: float = 0.01, maximum_distance: float = 1.8,
                       surface_normals: np.ndarray | None = None, force_surface: bool = False,
                       boundary_flags: np.ndarray | None = None, return_boundaries: bool = False,
                       midline: float | None = None, field=None, candidate_mask=None):
    """Clip subdivisions of native target faces to the warped author's outline."""
    # With an explicit facing direction, native face normals already select the
    # correct side below. Projection can locally flip the author's tessellation;
    # its winding must not erase valid native faces inside the same outline.
    if field is None:
        field = RegionField(region, side=side, midline=float(target.bounds[:, 0].mean()) if midline is None else midline, check_normals=facing is None,
                            volume_tolerance=volume_tolerance, maximum_distance=maximum_distance,
                            surface_normals=surface_normals, force_surface=force_surface, boundary_flags=boundary_flags)
    lower, upper = region.bounds + np.array([[-padding] * 3, [padding] * 3])
    candidates = np.where(np.all(target.triangles.max(axis=1) >= lower, axis=1)
                          & np.all(target.triangles.min(axis=1) <= upper, axis=1))[0]
    if candidate_mask is not None:
        candidates = candidates[np.asarray(candidate_mask)[candidates]]
    if facing is not None:
        candidates = candidates[target.face_normals[candidates] @ facing > 0.15]
    native = target.triangles[candidates]
    longest = np.linalg.norm(native - np.roll(native, -1, axis=1), axis=2).max(axis=1)
    altitude = np.linalg.norm(np.cross(native[:, 1] - native[:, 0], native[:, 2] - native[:, 0]), axis=1) / np.maximum(longest, 1e-15)
    # A few original GLB slivers have nearly coincident vertices. Float32
    # subdivision cannot retain reliable barycentric membership in a face only
    # microns wide. Exclude those faces instead of painting beyond their edges.
    candidates = candidates[altitude >= minimum_altitude]
    if not len(candidates):
        raise ValueError('Registered region does not overlap its target bone')
    triangles = target.triangles[candidates].copy()
    face_ids = candidates.copy()
    for _ in range(8):
        longest = np.linalg.norm(triangles - np.roll(triangles, -1, axis=1), axis=2).max(axis=1)
        split = longest > step
        if not split.any():
            break
        a, b, c = triangles[split, 0], triangles[split, 1], triangles[split, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        triangles = np.concatenate([triangles[~split], np.stack([a, ab, ca], axis=1),
                                    np.stack([ab, b, bc], axis=1), np.stack([ca, bc, c], axis=1), np.stack([ab, bc, ca], axis=1)])
        face_ids = np.concatenate([face_ids[~split], np.tile(face_ids[split], 4)])
    points = triangles.reshape(-1, 3)
    # Evaluate shared corners once, retaining their original target face normal.
    if field.closed or getattr(field, 'normal_independent', False):
        unique, inverse = np.unique(points, axis=0, return_inverse=True)
        values = field.evaluate(unique, np.zeros_like(unique))[inverse].reshape(-1, 3)
    else:
        values = field.evaluate(points, np.repeat(target.face_normals[face_ids], 3, axis=0)).reshape(-1, 3)
    output, parents, contours = [], [], []
    for triangle, fields, parent in zip(triangles, values, face_ids):
        polygon = [np.r_[point, value] for point, value in zip(triangle, fields)]
        clipped = []
        previous = polygon[-1]
        for current in polygon:
            if (previous[3] >= 0) != (current[3] >= 0):
                clipped.append(previous + (current - previous) * previous[3] / (previous[3] - current[3]))
            if current[3] >= 0:
                clipped.append(current)
            previous = current
        for corner in range(1, len(clipped) - 1):
            piece = np.array([clipped[0][:3], clipped[corner][:3], clipped[corner + 1][:3]])
            if np.linalg.norm(np.cross(piece[1] - piece[0], piece[2] - piece[0])) > 1e-9:
                output.append(piece)
                parents.append(parent)
                values = np.array([clipped[0][3], clipped[corner][3], clipped[corner + 1][3]])
                contours.append((np.abs(values) < 1e-8) & (np.abs(np.roll(values, -1)) < 1e-8))
    if not output:
        raise ValueError('No native surface passes the source outline, distance and orientation gates')
    result = (np.asarray(output, dtype='<f4'), np.asarray(parents, dtype='<u4'))
    return (*result, np.asarray(contours, dtype=bool)) if return_boundaries else result
