#!/usr/bin/env python3
"""Preserve author-selected bone surface domains in the reference GLB's face order.

The material face domains are source-authored. Their association with a named
anatomical feature is a separate statement, recorded only where the material
and location identify an articular surface. Numeric sculpt face sets remain
unnamed metadata. No boundaries are transferred to another CT specimen.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import bpy
import numpy as np
import trimesh

from export_skull import BLEND_SHA256, SOURCE_COMMIT
from source_io import digest


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/landmark-regions"
ROTATION = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)


def fingerprint(array: np.ndarray, dtype: str) -> str:
    return hashlib.sha256(np.asarray(array, dtype=dtype).tobytes(order="C")).hexdigest()


def region_geometry(mesh: trimesh.Trimesh, face_ids: np.ndarray) -> dict:
    triangles = mesh.triangles[face_ids]
    areas = mesh.area_faces[face_ids]
    centroids = triangles.mean(axis=1)
    center = np.average(centroids, weights=areas, axis=0)
    anchor = centroids[int(np.argmin(np.linalg.norm(centroids - center, axis=1)))]
    vertices = triangles.reshape(-1, 3)
    return {
        "bounds": np.array([vertices.min(axis=0), vertices.max(axis=0)]).round(5).tolist(),
        "center": center.round(5).tolist(),
        "surface_anchor": anchor.round(5).tolist(),
        "area_mm2": round(float(areas.sum()), 6),
    }


def components_by_material(mesh: trimesh.Trimesh, labels: np.ndarray, material_names: list[str]) -> list[tuple[int, np.ndarray]]:
    parent = np.arange(len(mesh.faces))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    include = np.array([bool(name and not name.lower().startswith("bone")) for name in material_names])
    for first, second in mesh.face_adjacency:
        if labels[first] == labels[second] and include[labels[first]]:
            root1, root2 = find(int(first)), find(int(second))
            if root1 != root2:
                parent[root2] = root1
    groups = defaultdict(list)
    for face in np.flatnonzero(include[labels]):
        groups[(int(labels[face]), find(int(face)))].append(int(face))
    result = [(material, np.array(faces, dtype=np.uint32)) for (material, _), faces in groups.items()]
    return sorted(result, key=lambda item: (item[0], -len(item[1]), int(item[1][0])))


def named_articular_mapping(owner: str, material: str, components: list[dict]) -> None:
    """Identify the well-localized author cartilage patches without widening them."""
    if material != "Cartilage":
        return
    side = "left" if owner.endswith("-left") else "right" if owner.endswith("-right") else None
    if owner == "occipital-bone" and len(components) == 2:
        for component in components:
            part = "left" if component["center"][0] > 0 else "right"
            component.update({"feature_ids": [f"{part}-occipital-condyle"], "mapping_scope": "articular-surface-of-feature", "identity_evidence": "cartilage material on paired inferior occipital condylar surfaces"})
    elif owner == "mandible" and len(components) == 2:
        for component in components:
            part = "left" if component["center"][0] > 0 else "right"
            component.update({"feature_ids": [f"{part}-head-of-mandible"], "mapping_scope": "articular-surface-of-feature", "identity_evidence": "cartilage material on paired superior mandibular condylar heads"})
    elif owner.startswith("malleus-") and len(components) == 1:
        components[0].update({"feature_ids": [f"{side}-articular-facet-of-head-of-malleus"], "mapping_scope": "named-articular-surface", "identity_evidence": "single cartilage patch on malleus head facing the incus"})
    elif owner.startswith("incus-") and len(components) == 2:
        upper, lower = sorted(components, key=lambda item: -item["center"][1])
        upper.update({"feature_ids": [f"{side}-articular-facet-for-malleus"], "mapping_scope": "named-articular-surface", "identity_evidence": "superior incus body cartilage patch facing the malleus head"})
        lower.update({"feature_ids": [f"{side}-articular-facet-for-stapes"], "mapping_scope": "named-articular-surface", "identity_evidence": "inferior incus lenticular-process cartilage patch facing the stapes head"})
    elif owner.startswith("stapes-") and len(components) == 2:
        medial, lateral = sorted(components, key=lambda item: abs(item["center"][0]))
        medial.update({"feature_ids": [f"{side}-base-of-stapes"], "mapping_scope": "articular-surface-of-feature", "identity_evidence": "medial stapes footplate cartilage surface facing the vestibular window"})
        lateral.update({"feature_ids": [f"{side}-articular-facet-of-head-of-stapes"], "mapping_scope": "named-articular-surface", "identity_evidence": "lateral stapes head cartilage patch facing the incus lenticular process"})


def export(source: Path, output: Path) -> None:
    if digest(source) != BLEND_SHA256:
        raise RuntimeError("The Blender atlas is not the pinned source; export stopped")
    manifest = json.loads((ROOT / "assets/skull/manifest.json").read_text())
    landmarks = json.loads((ROOT / "assets/landmarks/catalogue.json").read_text())
    landmark_by_id = {entry["id"]: entry for entry in landmarks["landmarks"]}
    model_path = ROOT / manifest["model"]
    if digest(model_path) != manifest["sha256"]:
        raise RuntimeError("Reference model does not match its manifest; export stopped")
    scene = trimesh.load(model_path, force="scene", process=False)
    entries = [entry for entry in manifest["structures"] if entry["kind"] == "bone"]
    center = np.array(manifest["source"]["viewer_center_before_translation_mm"])
    bpy.ops.wm.open_mainfile(filepath=str(source.resolve()), load_ui=False, use_scripts=False)
    for collection in bpy.data.collections:
        collection.hide_viewport = False
    for entry in entries:
        obj = bpy.data.objects[entry["source_name"]]
        obj.hide_viewport = False
        obj.hide_set(False)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bones = {}
    regions = []
    face_sets = []
    packed = []
    byte_offset = 0
    for entry in entries:
        owner = entry["id"]
        obj = bpy.data.objects[entry["source_name"]]
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            vertices = np.empty((len(mesh.vertices), 3), dtype=np.float64)
            triangles = np.empty((len(mesh.loop_triangles), 3), dtype=np.int32)
            polygon_ids = np.empty(len(mesh.loop_triangles), dtype=np.int32)
            material_ids = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.vertices.foreach_get("co", vertices.ravel())
            mesh.loop_triangles.foreach_get("vertices", triangles.ravel())
            mesh.loop_triangles.foreach_get("polygon_index", polygon_ids)
            mesh.polygons.foreach_get("material_index", material_ids)
            world = np.array(evaluated.matrix_world, dtype=np.float64)
            vertices = (vertices @ world[:3, :3].T + world[:3, 3]) @ ROTATION.T * 1000 - center
            exported = scene.geometry[owner]
            if vertices.shape != exported.vertices.shape or triangles.shape != exported.faces.shape:
                raise RuntimeError(f"Source and actual GLB shape differ: {owner}")
            if not np.array_equal(triangles, exported.faces):
                raise RuntimeError(f"Source triangle indices/order differ from actual GLB: {owner}")
            max_error = float(np.max(np.abs(vertices - exported.vertices)))
            if max_error > 0.001:
                raise RuntimeError(f"Source vertex order/positions differ from actual GLB: {owner}: {max_error}")
            position_bytes = np.asarray(exported.vertices, dtype="<f4").tobytes(order="C")
            index_bytes = np.asarray(exported.faces, dtype="<u4").tobytes(order="C")
            material_names = [slot.material.name if slot.material else "" for slot in obj.material_slots]
            bones[owner] = {
                "source_name": entry["source_name"],
                "vertices": len(exported.vertices), "triangles": len(exported.faces),
                "positions_sha256": hashlib.sha256(position_bytes).hexdigest(),
                "indices_sha256": hashlib.sha256(index_bytes).hexdigest(),
                "geometry_sha256": hashlib.sha256(position_bytes + index_bytes).hexdigest(),
                "max_position_error_mm": round(max_error, 9),
                "source_materials": material_names,
            }
            labels = material_ids[polygon_ids]
            component_numbers = Counter()
            owner_regions = []
            for material_id, face_ids in components_by_material(exported, labels, material_names):
                component_numbers[material_id] += 1
                number = component_numbers[material_id]
                material = material_names[material_id]
                articular = material == "Cartilage"
                joint = material.lower().startswith("suture")
                component = {
                    "id": f"{owner}--material-{material_id}--component-{number}",
                    "owner": owner,
                    "kind": "articular" if articular else "joint" if joint else "unassigned",
                    "source_material": material, "source_material_index": material_id,
                    "source_component": number,
                    "evidence": "source-material-face-domain",
                    "identity_evidence": "unassigned anatomical feature; the source material names a surface domain only",
                    "feature_ids": [], "mapping_scope": "source-material-domain-only",
                    "byte_offset": byte_offset, "count": len(face_ids),
                    **region_geometry(exported, face_ids),
                }
                if not articular and not joint:
                    raise RuntimeError(f"Unexpected non-bone source material needs review: {owner} {material}")
                packed.append(np.asarray(face_ids, dtype="<u4").tobytes())
                byte_offset += len(face_ids) * 4
                owner_regions.append(component)
            for material in {component["source_material"] for component in owner_regions}:
                named_articular_mapping(owner, material, [component for component in owner_regions if component["source_material"] == material])
            regions.extend(owner_regions)
            attribute = mesh.attributes.get(".sculpt_face_set")
            if attribute and attribute.domain == "FACE":
                values = np.array([datum.value for datum in attribute.data], dtype=np.int32)[polygon_ids]
                for value in sorted(set(values.tolist())):
                    face_ids = np.flatnonzero(values == value)
                    if not len(face_ids):
                        continue
                    face_sets.append({"owner": owner, "source_attribute": ".sculpt_face_set", "value": value, "triangles": len(face_ids), "evidence": "unnamed-source-sculpt-face-set", "anatomically_named": False, **region_geometry(exported, face_ids)})
        finally:
            evaluated.to_mesh_clear()

    for region in regions:
        for feature_id in region["feature_ids"]:
            if feature_id not in landmark_by_id or region["owner"] not in landmark_by_id[feature_id]["owners"]:
                raise RuntimeError(f"Articular source patch targets a wrong or missing anatomical feature: {feature_id}")
    binary = b"".join(packed)
    expected = byte_offset
    if len(binary) != expected or not regions:
        raise RuntimeError("Packed region indices are incomplete")
    claimed = defaultdict(set)
    for region in regions:
        face_ids = np.frombuffer(binary, dtype="<u4", count=region["count"], offset=region["byte_offset"])
        if len(set(face_ids.tolist())) != region["count"] or int(face_ids.max()) >= bones[region["owner"]]["triangles"]:
            raise RuntimeError(f"Invalid or duplicate source-domain triangle indices: {region['id']}")
        if claimed[region["owner"]].intersection(face_ids.tolist()):
            raise RuntimeError(f"Source material components overlap: {region['id']}")
        claimed[region["owner"]].update(face_ids.tolist())

    output.mkdir(parents=True, exist_ok=True)
    binary_path = output / "source-regions.bin"
    binary_path.write_bytes(binary)
    metadata = {
        "schema_version": 1,
        "dataset": "reference",
        "model": manifest["model"],
        "units": "millimetres",
        "source": {
            "name": "Z-Anatomy / BodyParts3D source-authored bone material domains",
            "url": f"https://github.com/Z-Anatomy/Models-of-human-anatomy/tree/{SOURCE_COMMIT}",
            "commit": SOURCE_COMMIT, "blend_sha256": BLEND_SHA256,
            "model_sha256": manifest["sha256"],
            "license": "assets/landmarks/ATTRIBUTION.txt",
            "notes": "These indices preserve exact author material selections on the actual reference bone geometry. Source material names identify cartilage/articular or suture/joint surface domains, not a complete anatomical region atlas. Named feature association is separate and recorded explicitly. Condyle, mandibular-head and stapes-footplate mappings cover their articular source patches only. Numeric sculpt face sets have no anatomical names and are metadata only. No reference domains are transferred to the CT specimen.",
        },
        "binary": {"url": "assets/landmark-regions/source-regions.bin", "type": "uint32", "endianness": "little", "sha256": digest(binary_path), "byte_length": len(binary)},
        "fingerprint_format": "positions_sha256 hashes canonical C-order float32 little-endian XYZ vertices; indices_sha256 hashes C-order uint32 little-endian local triangle vertex indices; geometry_sha256 hashes their concatenation. Region byte offsets address uint32 local triangle IDs in this exact GLB face order.",
        "coverage": {"bones_verified": len(bones), "source_components": len(regions), "joint_components": sum(region["kind"] == "joint" for region in regions), "articular_components": sum(region["kind"] == "articular" for region in regions), "named_feature_mappings": sum(len(region["feature_ids"]) for region in regions), "source_domain_triangles": sum(region["count"] for region in regions), "unnamed_face_sets": len(face_sets), "ct_domains": 0},
        "bones": bones, "regions": regions, "face_sets": face_sets,
    }
    (output / "source-regions.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Verified exact GLB face order for {len(bones)} bones; exported {len(regions)} author-selected material components, {metadata['coverage']['named_feature_mappings']} articular feature mappings, {len(binary):,} index bytes.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/source/Startup.blend")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    export(args.source, args.output)
