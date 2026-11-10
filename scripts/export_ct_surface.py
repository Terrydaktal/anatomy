#!/usr/bin/env python3
"""Recover authored U3D corner normals without moving or renumbering CT triangles."""

from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pikepdf
from scipy.spatial import cKDTree
import trimesh

from export_ct_skull import decoder, source_meshes, SOURCE, PDF_SHA256, DECODER_SHA256, slug
from source_io import digest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'assets/skull-ct'


def decode_normals(pair):
    """Read the normal stream that the pinned geometry-only decoder skips."""
    name, declaration, base, progressive = pair
    if progressive:
        raise ValueError(f'Unsupported progressive mesh: {name}')
    module = decoder()
    bits = module.BitStream(declaration, no_compression=False)
    bits.read_string(); bits.read_u32()
    excluded = bool(bits.read_u32() & 1)
    for _ in range(6): bits.read_u32()
    materials = []
    for _ in range(bits.read_u32()):
        attributes = bits.read_u32()
        layers = bits.read_u32()
        for _ in range(layers): bits.read_u32()
        bits.read_u32()
        materials.append((bool(attributes & 1), bool(attributes & 2), layers))
    bits = module.BitStream(base, no_compression=False)
    bits.read_string(); bits.read_u32()
    faces, positions, normals, diffuse, specular, textures = [bits.read_u32() for _ in range(6)]
    if excluded or not normals:
        raise ValueError(f'Source has no supplied normals: {name}')
    for _ in range(positions * 3): bits.read_f32()
    supplied = np.array([bits.read_f32() for _ in range(normals * 3)], dtype=np.float32).reshape(-1, 3)
    for _ in range((diffuse + specular) * 4 + textures * 4): bits.read_f32()
    native_faces = np.empty((faces, 3), np.int32)
    normal_faces = np.empty((faces, 3), np.int32)
    for face in range(faces):
        material = bits.read_compressed_u32(module.uACContextBaseShadingID)
        has_diffuse, has_specular, layers = materials[material]
        for corner in range(3):
            native_faces[face, corner] = bits.read_compressed_u32(module.uACStaticFull + positions)
            normal_faces[face, corner] = bits.read_compressed_u32(module.uACStaticFull + normals)
            if has_diffuse: bits.read_compressed_u32(module.uACStaticFull + diffuse)
            if has_specular: bits.read_compressed_u32(module.uACStaticFull + specular)
            for _ in range(layers): bits.read_compressed_u32(module.uACStaticFull + textures)
    original = np.load(SOURCE / 'decoded' / f'{slug(name)}.npz')
    if not np.array_equal(native_faces, original['faces']):
        raise ValueError(f'Normal stream disagrees with independently decoded triangles: {name}')
    if normal_faces.min() < 0 or normal_faces.max() >= len(supplied) or not np.isfinite(supplied).all():
        raise ValueError(f'Invalid source normals: {name}')
    if not np.allclose(np.linalg.norm(supplied, axis=1), 1, atol=1e-5):
        raise ValueError(f'Source normals are not unit vectors: {name}')
    np.savez_compressed(SOURCE / 'shading' / f'{slug(name)}.npz', normals=supplied, normal_faces=normal_faces)
    return name


def export():
    manifest_path = OUTPUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if digest(ROOT / manifest['model']) != manifest['sha256']:
        raise ValueError('Native CT model identity has changed')
    # Also verifies the pinned source PDF and decoder, and populates geometry caches.
    source = source_meshes()
    shading = SOURCE / 'shading'
    shading.mkdir(exist_ok=True)
    names = {entry['source_name'] for entry in manifest['structures']}
    signature = f'{PDF_SHA256}:{DECODER_SHA256}:corner-normals-v1'
    cache_path = shading / 'index.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    if cache.get('signature') != signature or any(not (shading / f'{slug(name)}.npz').exists() for name in names):
        with pikepdf.open(SOURCE / 'skull.pdf') as document:
            streams = [bytes(obj.read_bytes()) for obj in document.objects if isinstance(obj, pikepdf.Stream) and str(obj.get('/Subtype')) == '/U3D']
        if len(streams) != 1:
            raise ValueError('Expected one verified U3D stream in the source PDF')
        pairs = [pair for pair in decoder()._collect_clod_pairs(decoder().parse_blocks(streams[0])) if pair[0] in names]
        if {pair[0] for pair in pairs} != names:
            raise ValueError('Missing CT shading resources')
        with ProcessPoolExecutor(max_workers=4) as pool:
            for name in pool.map(decode_normals, pairs):
                print(f'Recovered source normals: {name}', flush=True)
        cache_path.write_text(json.dumps({'signature': signature}, indent=2) + '\n')
    c = 1 / np.sqrt(2)
    rotation = np.array([[1, 0, 0], [0, -c, c], [0, -c, -c]])
    bounds = np.array([source[entry['source_name']].bounds for entry in manifest['structures'] if entry['kind'] == 'bone'])
    center = (bounds[:, 0].min(0) + bounds[:, 1].max(0)) / 2
    native = trimesh.load(ROOT / manifest['model'], force='scene', process=False)
    result = trimesh.Scene()
    records = []
    for entry in manifest['structures']:
        mesh = native.geometry[entry['id']]
        original = source[entry['source_name']]
        supplied = np.load(shading / f"{slug(entry['source_name'])}.npz")
        normals = supplied['normals'] @ rotation.T
        # Conchae are disconnected pieces of one source resource. Match face
        # centres and then corners, preserving the GLB's existing face order.
        distances, source_faces = cKDTree(original.triangles_center - center).query(mesh.triangles_center)
        if distances.max() > 5e-5 or len(np.unique(source_faces)) != len(mesh.faces):
            raise ValueError(f'CT face correspondence failed: {entry["id"]}')
        triangles = original.triangles[source_faces] - center
        corner_distances = np.linalg.norm(mesh.triangles[:, :, None] - triangles[:, None], axis=3)
        corners = corner_distances.argmin(axis=2)
        if np.take_along_axis(corner_distances, corners[:, :, None], axis=2).max() > 5e-5:
            raise ValueError(f'CT corner correspondence failed: {entry["id"]}')
        normal_ids = np.take_along_axis(supplied['normal_faces'][source_faces], corners, axis=1)
        # Split only authored normal seams. All positions, triangle corners,
        # triangle order and hence landmark source-face identities stay exact.
        pairs, indices = np.unique(np.column_stack([mesh.faces.ravel(), normal_ids.ravel()]), axis=0, return_inverse=True)
        vertices = mesh.vertices[pairs[:, 0]]
        restored = normals[pairs[:, 1]]
        faces = indices.reshape(-1, 3)
        if not np.array_equal(vertices[faces], mesh.triangles):
            raise ValueError('Restoring normals moved native CT triangle corners')
        updated = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=restored, process=False)
        updated.visual = deepcopy(mesh.visual)
        updated.metadata = deepcopy(mesh.metadata)
        result.add_geometry(updated, node_name=entry['id'], geom_name=entry['id'])
        old = mesh.vertex_normals[mesh.faces]
        recovered = restored[faces]
        angles = np.degrees(np.arccos(np.clip(np.sum(old * recovered, axis=2), -1, 1)))
        records.append({'id': entry['id'], 'triangles': len(faces), 'vertices': len(vertices),
                        'normal_change_degrees_p95': float(np.quantile(angles, .95)),
                        'normal_change_degrees_max': float(angles.max())})
    output = OUTPUT / 'surface.glb'
    temporary = output.with_suffix('.glb.partial')
    temporary.write_bytes(result.export(file_type='glb'))
    reloaded = trimesh.load(temporary, file_type='glb', force='scene', process=False)
    for name, mesh in native.geometry.items():
        if not np.array_equal(mesh.triangles, reloaded.geometry[name].triangles):
            raise ValueError(f'Display export changed native face geometry: {name}')
    temporary.replace(output)
    manifest['surface'] = {'model': 'assets/skull-ct/surface.glb', 'sha256': digest(output),
                           'native_model_sha256': manifest['sha256'], 'source_pdf_sha256': PDF_SHA256,
                           'method': 'Original U3D per-corner surface normals; split authored normal seams only. Triangle positions, order, count and face IDs are unchanged. No subdivision or geometric smoothing.',
                           'structures': records}
    temporary_manifest = manifest_path.with_suffix('.json.partial')
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary_manifest.replace(manifest_path)
    print(f'Restored authored shading on {sum(r["triangles"] for r in records):,} unchanged CT triangles: {output.stat().st_size / 1e6:.1f} MB', flush=True)


if __name__ == '__main__':
    export()
