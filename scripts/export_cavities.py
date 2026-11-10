#!/usr/bin/env python3
"""Export the skull specimen's original segmented bony cavity boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh

from export_ct_skull import SOURCE_NAMES, source_meshes
from source_io import digest


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/skull-cavities"
CAVITIES = [
    ("frontal-sinuses", "Frontal sinuses", "Frontal sinuses", ["frontal-bone"], "#e9b95d",
     "The segmented air spaces inside the frontal bone, above the orbits. This surface outlines the cavity against its bony walls."),
    ("maxillary-sinuses", "Maxillary sinuses", "Maxillary sinuses", ["maxilla-left", "maxilla-right"], "#74c8cf",
     "The paired air spaces in the maxillae, below the orbital floors and above the upper dental arches. Their original bilateral source grouping is retained."),
    ("ethmoid-air-cells", "Ethmoidal air cells", "Ethmoid sinuses", ["ethmoid-bone"], "#cc9aca",
     "The labyrinth of air cells within the ethmoid between the nasal cavity and the medial orbital walls. The surface follows the original segmented cavity boundaries."),
    ("sphenoid-sinus", "Sphenoidal sinus", "Sphenoid sinus", ["sphenoid-bone"], "#b7c968",
     "The segmented air space within the body of the sphenoid, behind the nasal cavity and below the sella turcica."),
    ("mastoid-air-cells", "Mastoid air cells", "Mastoid air cells", ["temporal-bone-left", "temporal-bone-right"], "#90b899",
     "The communicating air spaces inside the mastoid portions of the temporal bones. These are the specimen's original cavity surfaces, rather than additional bones."),
    ("bony-labyrinths", "Bony labyrinth cavities", "Inner ear labyrinths", ["temporal-bone-left", "temporal-bone-right"], "#a4a4e2",
     "The original segmented inner-ear cavity boundaries in the petrous temporal bones, including the cochlear and semicircular regions. The surface depicts spaces enclosed by bone, not soft inner-ear tissues."),
]


def export() -> None:
    meshes = source_meshes({entry[2] for entry in CAVITIES})
    bounds = np.array([meshes[name].bounds for name in set(SOURCE_NAMES.values())])
    center = (bounds[:, 0].min(axis=0) + bounds[:, 1].max(axis=0)) / 2
    scene = trimesh.Scene()
    structures = []
    for identity, name, source, owners, color, description in CAVITIES:
        mesh = meshes[source].copy()
        mesh.apply_translation(-center)
        _ = mesh.vertex_normals
        mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            name=identity, baseColorFactor=[*bytes.fromhex(color[1:]), 255], metallicFactor=0,
            roughnessFactor=0.75, doubleSided=True,
        ))
        scene.add_geometry(mesh, node_name=identity, geom_name=identity)
        structures.append({
            "id": identity, "name": name, "source_name": source, "kind": "cavity", "owners": owners,
            "color": color, "visible": False, "description": description,
            "triangles": len(mesh.faces), "vertices": len(mesh.vertices),
            "bounds": mesh.bounds.round(5).tolist(), "center": mesh.bounds.mean(axis=0).round(5).tolist(),
        })
    OUTPUT.mkdir(parents=True, exist_ok=True)
    model = OUTPUT / "cavities.glb"
    model.write_bytes(scene.export(file_type="glb"))
    reloaded = trimesh.load(model, force="scene", process=False)
    assert set(reloaded.geometry) == {entry["id"] for entry in structures}
    for entry in structures:
        geometry = reloaded.geometry[entry["id"]]
        assert len(geometry.faces) == entry["triangles"]
        assert np.allclose(geometry.bounds, entry["bounds"], atol=0.001)
    assert sum(entry["triangles"] for entry in structures) == 189_964
    skull = json.loads((ROOT / "assets/skull-ct/manifest.json").read_text())
    manifest = {
        "title": "Bony cavity boundaries · OUVC 10503", "model": "assets/skull-cavities/cavities.glb",
        "sha256": digest(model), "units": skull["units"], "source": skull["source"],
        "placement": "Original same-specimen coordinates, rotation and skull-centering translation. No registration or reshaping.",
        "representation": "Surfaces outline spaces enclosed by bone. They are not additional solid bones or soft tissues. Bilateral resource groupings are retained.",
        "structures": structures,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Exported and reloaded {len(structures)} registered cavity groups, 189,964 original source triangles", flush=True)


if __name__ == "__main__":
    export()
