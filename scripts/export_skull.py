#!/usr/bin/env python3
"""Fetch the pinned Z-Anatomy atlas and export its skull as individually named GLB meshes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import zipfile

import bpy
import numpy as np
import trimesh

from skull_catalogue import GROUPS, catalogue
from source_io import digest, download


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "ca4ad71d24d05a6e81f8aeaadb96165c20dffc75"
SOURCE_BASE = f"https://raw.githubusercontent.com/Z-Anatomy/Models-of-human-anatomy/{SOURCE_COMMIT}"
SOURCE_SHA256 = "e029688545627bd0214b269e1063143abb580aad72b2c2445d6d8a9a0d9da736"
BLEND_SHA256 = "9f08a17ea0115fed80b2a73ecdf0a1bc2ab2f6956f37c593ce23d513ea35afcd"
SOURCE_DIR = ROOT / "data/source"
OUTPUT = ROOT / "assets/skull"


def fetch_source() -> Path:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    archive = SOURCE_DIR / "Z-Anatomy.zip"
    if not archive.exists():
        download(f"{SOURCE_BASE}/Z-Anatomy.zip", archive)
    if digest(archive) != SOURCE_SHA256:
        raise RuntimeError("Source ZIP checksum differs from the pinned atlas; export stopped")
    blend = SOURCE_DIR / "Startup.blend"
    with zipfile.ZipFile(archive) as source:
        # Only read this exact member. Never extract arbitrary archive paths or execute atlas scripts.
        with source.open("Z-Anatomy/Startup.blend") as input_file, blend.open("wb") as output:
            shutil.copyfileobj(input_file, output)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    download(f"{SOURCE_BASE}/License.txt", OUTPUT / "SOURCE-LICENSE.txt")
    return blend


def export(blend: Path) -> None:
    if digest(blend) != BLEND_SHA256:
        raise RuntimeError("The .blend is not the pinned Z-Anatomy atlas; export stopped")
    bpy.ops.wm.open_mainfile(filepath=str(blend.resolve()), load_ui=False, use_scripts=False)
    entries = catalogue()
    missing = [entry["source_name"] for entry in entries if entry["source_name"] not in bpy.data.objects]
    if missing:
        raise RuntimeError(f"Missing anatomical meshes: {missing}")

    # Evaluate the source modifiers, notably the ethmoid's air-cell Boolean cut-outs.
    for collection in bpy.data.collections:
        collection.hide_viewport = False
    for entry in entries:
        obj = bpy.data.objects[entry["source_name"]]
        obj.hide_viewport = False
        obj.hide_set(False)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    rotation = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    meshes = {}
    matrices = {}
    for entry in entries:
        obj = bpy.data.objects[entry["source_name"]]
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        vertices = np.empty((len(mesh.vertices), 3), dtype=np.float64)
        faces = np.empty((len(mesh.loop_triangles), 3), dtype=np.int32)
        normals = np.empty_like(vertices)
        mesh.vertices.foreach_get("co", vertices.ravel())
        mesh.vertices.foreach_get("normal", normals.ravel())
        mesh.loop_triangles.foreach_get("vertices", faces.ravel())
        world = np.array(evaluated.matrix_world, dtype=np.float64)
        vertices = (vertices @ world[:3, :3].T + world[:3, 3]) @ rotation.T * 1000
        normal_matrix = rotation @ np.linalg.inv(world[:3, :3]).T
        normals = normals @ normal_matrix.T
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        if not len(faces) or not np.isfinite(vertices).all():
            raise RuntimeError(f"Invalid source geometry: {obj.name}")
        meshes[entry["id"]] = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=normals, process=False)
        matrices[entry["id"]] = world.tolist()
        entry["source_modifiers"] = [modifier.type for modifier in obj.modifiers if modifier.show_viewport]
        evaluated.to_mesh_clear()

    skull_meshes = [meshes[e["id"]] for e in entries if e["group"] in ("cranial", "facial")]
    bounds = np.array([mesh.bounds for mesh in skull_meshes])
    center = (bounds[:, 0].min(axis=0) + bounds[:, 1].max(axis=0)) / 2
    scene = trimesh.Scene()
    for entry in entries:
        mesh = meshes[entry["id"]]
        mesh.apply_translation(-center)
        mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            name=entry["id"], baseColorFactor=[*bytes.fromhex(entry["color"][1:]), 255],
            metallicFactor=0.0, roughnessFactor=0.65, doubleSided=True,
        ))
        mesh.metadata = {"source_name": entry["source_name"], "catalogue_id": entry["id"]}
        scene.add_geometry(mesh, node_name=entry["id"], geom_name=entry["id"])
        entry.update({
            "vertices": len(mesh.vertices),
            "triangles": len(mesh.faces),
            "bounds": mesh.bounds.round(5).tolist(),
            "center": mesh.bounds.mean(axis=0).round(5).tolist(),
            "source_matrix_world": matrices[entry["id"]],
        })

    OUTPUT.mkdir(parents=True, exist_ok=True)
    glb = OUTPUT / "skull.glb"
    glb.write_bytes(scene.export(file_type="glb"))
    # Reload the actual output; identity, coverage and positions must survive serialization.
    reloaded = trimesh.load(glb, force="scene", process=False)
    expected = {entry["id"] for entry in entries}
    if set(reloaded.geometry) != expected:
        raise RuntimeError("Serialized GLB lost or renamed anatomical structures")
    for entry in entries:
        geometry = reloaded.geometry[entry["id"]]
        if len(geometry.faces) != entry["triangles"] or not np.allclose(geometry.bounds, entry["bounds"], atol=0.001):
            raise RuntimeError(f"Serialized geometry mismatch: {entry['id']}")
    assert sum(entry["group"] in ("cranial", "facial") for entry in entries) == 22
    assert sum(entry["group"] == "ossicles" for entry in entries) == 6
    assert sum(entry["kind"] == "tooth" for entry in entries) == 28
    manifest = {
        "title": "Human skull",
        "model": "assets/skull/skull.glb",
        "sha256": digest(glb),
        "units": "millimetres",
        "axes": {"x": "patient left", "y": "superior", "z": "anterior"},
        "source": {
            "name": "Z-Anatomy / BodyParts3D",
            "url": "https://github.com/Z-Anatomy/Models-of-human-anatomy",
            "commit": SOURCE_COMMIT,
            "archive_sha256": SOURCE_SHA256,
            "license": "assets/skull/ATTRIBUTION.txt",
            "modifications": "Selected named meshes; evaluated existing source modifiers; applied world transforms; rotated axes, converted metres to millimetres and centered the skull; assigned display colours; exported GLB. No remeshing, decimation or smoothing.",
            "viewer_center_before_translation_mm": center.tolist(),
        },
        "coverage": {"cranial": 8, "facial": 14, "ossicles": 6, "teeth": 28, "associated": 1},
        "defaults": {"colors": False, "gloss": 25, "ambientOcclusion": True},
        "groups": GROUPS,
        "structures": entries,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Exported and reloaded {len(entries)} structures, {sum(e['triangles'] for e in entries):,} triangles, {glb.stat().st_size / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Use a local copy of the pinned atlas .blend instead of fetching it")
    args = parser.parse_args()
    export(args.source if args.source else fetch_source())
