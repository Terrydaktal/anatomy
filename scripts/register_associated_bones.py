#!/usr/bin/env python3
"""Fit intact reference ear chains and the hyoid into the CT display frame."""

from copy import deepcopy
import json
from pathlib import Path

import numpy as np

from region_registration import BoneRegistration, apply, load_native_bones, similarity
from source_io import digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    reference = json.loads((ROOT / 'assets/skull/manifest.json').read_text())
    target = json.loads((ROOT / 'assets/skull-ct/manifest.json').read_text())
    for model in [reference, target]:
        assert digest(ROOT / model['model']) == model['sha256']
    source = load_native_bones(ROOT / reference['model'])
    native = load_native_bones(ROOT / target['model'])
    shared = ['frontal-bone', 'parietal-bone-left', 'parietal-bone-right', 'occipital-bone',
              'temporal-bone-left', 'temporal-bone-right', 'maxilla-left', 'maxilla-right', 'mandible']
    whole = similarity(np.array([source[k].centroid for k in shared]), np.array([native[k].centroid for k in shared]))
    matrices, fits = {'hyoid-bone': whole}, {}
    for side in ['left', 'right']:
        owner = f'temporal-bone-{side}'
        registration = BoneRegistration(source[owner], native[owner], whole)
        chain = [f'{bone}-{side}' for bone in ['malleus', 'incus', 'stapes']]
        points = np.concatenate([source[k].vertices for k in chain])
        center = points.mean(axis=0)
        # Use the host bone's local orientation, while keeping all three ossicles
        # in one similarity transform. No bending or independent chain offsets.
        probes = center + np.vstack([np.zeros(3), np.eye(3)])
        moved = registration.move(probes)
        jacobian = (moved[1:] - moved[0]).T
        u, _, vt = np.linalg.svd(jacobian)
        rotation = u @ np.diag([1, 1, np.linalg.det(u @ vt)]) @ vt
        scale = np.cbrt(np.linalg.det(registration.matrix[:3, :3]))
        matrix = np.eye(4)
        matrix[:3, :3] = rotation * scale
        matrix[:3, 3] = moved[0] - matrix[:3, :3] @ center
        for identity in chain:
            matrices[identity] = matrix
        fits[side] = {**registration.metrics, 'chain_scale': float(scale), 'source_chain_center': center.tolist(),
                      'target_chain_center': apply(center[None], matrix)[0].tolist(),
                      'method': 'Local temporal-bone registration, one rigid orientation and uniform display-unit conversion for the intact chain'}
        print(f'Fitted {side} chain: {fits[side]["target_chain_center"]}', flush=True)
    structures = []
    for original in reference['structures']:
        if original['id'] not in matrices:
            continue
        entry = deepcopy(original)
        matrix = matrices[entry['id']]
        points = apply(source[entry['id']].vertices, matrix)
        entry.update(visible=True, bounds=[points.min(0).tolist(), points.max(0).tolist()],
                     center=points.mean(0).tolist(), reference_transform=matrix.tolist(), source_model_sha256=reference['sha256'],
                     source_dataset='reference', placement_estimated=True)
        entry['description'] += ' Reference-atlas bone fitted to this CT display; its placement is estimated, not segmented from the CT specimen.'
        structures.append(entry)
    output = ROOT / 'assets/skull-associated'
    output.mkdir(exist_ok=True)
    manifest = {'schema_version': 1, 'model': reference['model'], 'sha256': reference['sha256'],
                'target_model_sha256': target['sha256'], 'groups': [g for g in reference['groups'] if g['id'] in ['ossicles', 'associated']],
                'structures': structures, 'registrations': fits, 'hyoid_transform': whole.tolist(),
                'note': 'Reference geometry retains source triangles and within-chain relationships. CT placement is an unvalidated cross-specimen estimate. Hyoid pose follows the atlas via whole-skull similarity, not a measured articulation.',
                'source_url': reference['source']['url'], 'license': reference['source']['license']}
    from export_associated_bones import package
    package(manifest)


if __name__ == '__main__':
    main()
