#!/usr/bin/env python3
"""Package only the six ear bones and hyoid needed by the CT viewer."""

from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import trimesh

from source_io import digest

ROOT = Path(__file__).resolve().parents[1]


def package(manifest):
    reference = json.loads((ROOT / 'assets/skull/manifest.json').read_text())
    if digest(ROOT / reference['model']) != reference['sha256']:
        raise ValueError('Supplementary bone source identity has changed')
    atlas = trimesh.load(ROOT / reference['model'], force='scene', process=False)
    selected = trimesh.Scene()
    for record in manifest['structures']:
        original = atlas.geometry[record['id']]
        mesh = original.copy()
        # Trimesh.copy drops the loaded normal cache unless explicitly retained.
        # Recalculation reverses normals on the atlas's mirrored source bones.
        mesh.vertex_normals = original.vertex_normals.copy()
        selected.add_geometry(mesh, node_name=record['id'], geom_name=record['id'])
    output = ROOT / 'assets/skull-associated/bones.glb'
    output.parent.mkdir(exist_ok=True)
    temporary = output.with_suffix('.glb.partial')
    temporary.write_bytes(selected.export(file_type='glb', unitize_normals=False))
    reloaded = trimesh.load(temporary, file_type='glb', force='scene', process=False)
    for name, mesh in reloaded.geometry.items():
        original = atlas.geometry[name]
        if not np.array_equal(mesh.triangles, original.triangles) or not np.array_equal(mesh.vertex_normals[mesh.faces], original.vertex_normals[original.faces]):
            raise ValueError(f'Packaging changed supplementary source triangles or normals: {name}')
    temporary.replace(output)
    manifest = deepcopy(manifest)
    manifest.update(model='assets/skull-associated/bones.glb', sha256=digest(output))
    temporary_manifest = output.parent / 'manifest.json.partial'
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary_manifest.replace(output.parent / 'manifest.json')
    (output.parent / 'ATTRIBUTION.txt').write_text('Six auditory ossicles and hyoid extracted without changing source vertices, face order or surface normals. Source: Z-Anatomy / BodyParts3D; see ../skull/ATTRIBUTION.txt and ../skull/SOURCE-LICENSE.txt.\nCT positions are estimated by register_associated_bones.py; these bones are not segmented from the CT specimen. The complete reference skull is not loaded by the viewer.\n')
    print(f'Packaged {len(reloaded.geometry)} supplementary bones only: {output.stat().st_size / 1e6:.2f} MB')


if __name__ == '__main__':
    package(json.loads((ROOT / 'assets/skull-associated/manifest.json').read_text()))
