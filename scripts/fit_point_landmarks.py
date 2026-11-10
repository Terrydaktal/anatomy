#!/usr/bin/env python3
"""Fit bregma/lambda to each native skull's exterior three-bone junction.

Input: catalogue guide locations and checksum-verified viewer GLBs.
Output: updated catalogue point locations, fit evidence and a small display radius.
The radius is a visual cue, not an anatomical region boundary or error estimate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from region_registration import Surface, load_native_bones
from source_io import digest


ROOT = Path(__file__).resolve().parents[1]
TARGETS = {'bregma': [0, 1, 0], 'lambda': [0, 0, -1]}
DEFINITION = 'https://pmc.ncbi.nlm.nih.gov/articles/PMC7366040/'


def fit_points(records: list[dict], root: Path = ROOT) -> None:
    for dataset, directory in [('ct', 'skull-ct'), ('reference', 'skull')]:
        manifest = json.loads((root / f'assets/{directory}/manifest.json').read_text())
        if digest(root / manifest['model']) != manifest['sha256']:
            raise ValueError(f'{dataset} skull checksum differs from its manifest')
        bones = load_native_bones(root / manifest['model'])
        for entry in records:
            if entry['id'] not in TARGETS or dataset not in entry['positions']:
                continue
            placement = entry['positions'][dataset]
            guide = np.array(placement.get('guide_point', placement['point']))
            direction = np.array(TARGETS[entry['id']])
            surfaces, cloud = [], []
            for owner in entry['owners']:
                bone = bones[owner]
                eligible = (bone.face_normals @ direction > 0.3) & (np.linalg.norm(bone.triangles_center - guide, axis=1) < 25)
                if not eligible.any():
                    raise ValueError(f'No exterior search surface for {dataset}:{entry["id"]}:{owner}')
                exterior = bone.submesh([np.flatnonzero(eligible)], append=True)
                surfaces.append(Surface(exterior))
                cloud.append(exterior.vertices)
            points = np.unique(np.concatenate(cloud).round(4), axis=0)
            distances = np.stack([surface.nearest(points)[3] for surface in surfaces], axis=1)
            starts = np.argsort(np.sum(distances ** 2, axis=1))[:20:2]
            solutions = []
            for start in starts:
                fit = least_squares(lambda point: np.array([surface.nearest(point[None])[3][0] for surface in surfaces]),
                                    points[start], bounds=(guide - 25, guide + 25), diff_step=1e-3,
                                    max_nfev=100, ftol=1e-9, xtol=1e-9, gtol=1e-9)
                gaps = np.array([surface.nearest(fit.x[None])[3][0] for surface in surfaces])
                score = np.sum(gaps ** 2) + 0.00001 * np.linalg.norm(fit.x - guide) ** 2
                solutions.append((score, fit.x, gaps))
            _, point, gaps = min(solutions, key=lambda solution: solution[0])
            if gaps.max() > 0.8:
                raise ValueError(f'{dataset}:{entry["id"]} has no shared exterior junction within 0.8 display unit')
            placement.update(point=point.round(6).tolist(), guide_point=guide.tolist(),
                             method='native-suture-junction', approximate=True,
                             native_point_fit={
                                 'source_model_sha256': manifest['sha256'],
                                 'method': 'exterior three-bone nearest-surface least-squares junction',
                                 'owner_distances': dict(zip(entry['owners'], gaps.round(6).tolist())),
                                 'maximum_owner_distance': float(gaps.max()),
                                 'view_direction': direction.tolist(), 'display_radius': 2,
                                 'definition_url': DEFINITION, 'independently_validated': False,
                                 'anatomical_accuracy_percent': None,
                                 'note': 'One shared specimen-fitted point. Display radius is a surface indication only; the anatomical landmark has no region boundary.'})
            print(f'Fitted {dataset}:{entry["id"]}: {placement["point"]}; maximum bone distance {gaps.max():.4f}', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', type=Path, default=ROOT / 'assets/landmarks/catalogue.json')
    args = parser.parse_args()
    catalogue = json.loads(args.catalogue.read_text())
    fit_points(catalogue['landmarks'])
    note = ' Bregma and lambda instead use independently fitted exterior three-bone junctions on each native skull; their small surface highlights indicate points, not anatomical regions.'
    if note.strip() not in catalogue['source']['placement_notes']:
        catalogue['source']['placement_notes'] += note
    temporary = args.catalogue.with_suffix('.json.partial')
    temporary.write_text(json.dumps(catalogue, indent=2) + '\n')
    temporary.replace(args.catalogue)


if __name__ == '__main__':
    main()
