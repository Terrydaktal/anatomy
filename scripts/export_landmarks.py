#!/usr/bin/env python3
"""Export traceable, bone-only skull study landmarks from the pinned atlas.

This exports annotation positions rather than new anatomical mesh surfaces.
Reference author-label anchors and bone-contact paths are preserved separately
from their approximate CT transfer. No source Python is executed by Blender.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

import bpy
import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
import trimesh

from export_skull import BLEND_SHA256, SOURCE_COMMIT
from landmark_catalogue import CATEGORIES, OVERVIEW_URL, SOURCE_LOCATIONS, SUTURES, TERMINOLOGY_URL, curated_details, source_details
from source_io import digest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/source/Startup.blend"
OUTPUT = ROOT / "assets/landmarks"
ROTATION = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
SOURCE_URL = f"https://github.com/Z-Anatomy/Models-of-human-anatomy/tree/{SOURCE_COMMIT}"
VIEWS = {"anterior", "posterior", "left", "right", "superior", "inferior"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def clean_label(name: str) -> str:
    return re.sub(r"\.[st]$", "", name).strip("() ")


def rounded(point: np.ndarray | list) -> list:
    return np.asarray(point).round(5).tolist()


class BoneSurface:
    """Find nearby real mesh surfaces without an optional spatial-index package."""

    def __init__(self, mesh: trimesh.Trimesh):
        self.mesh = mesh
        self.vertices = cKDTree(mesh.vertices)
        self.centroids = cKDTree(mesh.triangles_center)

    def nearest(self, point: np.ndarray) -> tuple[np.ndarray, float]:
        _, vertices = self.vertices.query(point, k=min(16, len(self.mesh.vertices)))
        face_ids = self.mesh.vertex_faces[np.atleast_1d(vertices)].ravel()
        _, near_centroids = self.centroids.query(point, k=min(32, len(self.mesh.faces)))
        face_ids = np.unique(np.concatenate([face_ids[face_ids >= 0], np.atleast_1d(near_centroids)]))
        triangles = self.mesh.triangles[face_ids]
        candidates = trimesh.triangles.closest_point(triangles, np.repeat(point[None, :], len(triangles), axis=0))
        distances = np.linalg.norm(candidates - point, axis=1)
        closest = int(np.argmin(distances))
        return candidates[closest], float(distances[closest])


def owner_union(owners: list[str], entries: dict[str, dict]) -> np.ndarray:
    bounds = np.array([entries[owner]["bounds"] for owner in owners], dtype=float)
    return np.array([bounds[:, 0].min(axis=0), bounds[:, 1].max(axis=0)])


def map_to_ct(points: np.ndarray, owners: list[str], reference: dict, ct: dict) -> np.ndarray | None:
    if any(owner not in ct for owner in owners):
        return None
    source_bounds = owner_union(owners, reference)
    target_bounds = owner_union(owners, ct)
    fractions = (points - source_bounds[0]) / np.maximum(source_bounds[1] - source_bounds[0], 1e-6)
    return target_bounds[0] + fractions * (target_bounds[1] - target_bounds[0])


def positioned(record: dict, point: np.ndarray, reference: dict, ct: dict, method: str, path: np.ndarray | None = None, **metadata) -> dict:
    owners = record["owners"]
    record.update({"id": slug(record["name"]), "point": rounded(point), "source_url": SOURCE_URL if method.startswith("source-") else TERMINOLOGY_URL})
    position = {"point": rounded(point), "method": method, "approximate": True, **metadata}
    if path is not None:
        position["path"] = rounded(path)
    record["positions"] = {"reference": position}
    mapped = map_to_ct(np.asarray(point), owners, reference, ct)
    if mapped is not None:
        ct_position = {"point": rounded(mapped), "method": "owner-bounds-map", "approximate": True}
        record["positions"]["ct"] = ct_position
    record.setdefault("aliases", [])
    record.setdefault("geometry_status", "study-annotation")
    return record


def side_owners(owners: list[str], side: str | None, entries: dict) -> list[str]:
    result = []
    for owner in owners:
        candidate = f"{owner}-{side}" if side else owner
        resolved = candidate if candidate in entries else owner
        if resolved not in entries:
            raise RuntimeError(f"Unknown landmark owner: {resolved}")
        result.append(resolved)
    return result


def mirror(point: np.ndarray, center: np.ndarray) -> np.ndarray:
    result = np.array(point, copy=True)
    # Mirror about Blender's anatomical midsagittal plane, before recentering.
    result[..., 0] = -result[..., 0] - 2 * center[0]
    return result


def source_label_records(reference: dict, ct: dict, center: np.ndarray, surfaces: dict) -> tuple[list[dict], list[dict]]:
    notes = source_details()
    bone_sources = {entry["source_name"]: key for key, entry in reference.items() if entry["kind"] == "bone"}
    labels = []
    omitted = []
    for obj in bpy.data.objects:
        if obj.type != "FONT" or not obj.name.endswith((".s", ".t")):
            continue
        owner = obj.parent
        while owner and owner.name not in bone_sources:
            owner = owner.parent
        if not owner or not any(collection.name == "1: Skeletal system" for collection in obj.users_collection):
            continue
        name = clean_label(obj.name)
        if name == "Stapes":
            continue  # The entire bone already has its own catalogue entry.
        if name not in notes:
            raise RuntimeError(f"A source bone label needs an anatomical note: {obj.name}")
        owner_id = bone_sources[owner.name]
        candidates = []
        for child in obj.children:
            if child.type != "MESH" or len(child.data.polygons) or not len(child.data.vertices):
                continue
            for vertex in child.data.vertices:
                world = np.array(child.matrix_world @ vertex.co)
                # Left-side .s/.i duplicates are unplaced placeholders at world origin.
                if not 1.3 < world[2] < 1.9:
                    continue
                source_point = world @ ROTATION.T * 1000 - center
                resolved_owner = owner_id
                if owner_id.endswith(("-left", "-right")):
                    # Some source right-parented labels are physically on the left.
                    side = "left" if world[0] >= 0 else "right"
                    resolved_owner = re.sub(r"-(left|right)$", f"-{side}", owner_id)
                projected, distance = surfaces[resolved_owner].nearest(source_point)
                candidates.append((distance, child.name, source_point, projected, resolved_owner))
        if not candidates:
            omitted.append({"source_name": obj.name, "reason": "No positioned source leader; unplaced duplicate or missing geometry."})
            continue
        distance, leader, source_point, point, owner_id = min(candidates, key=lambda item: item[0])
        labels.append((name, owner_id, obj.name, leader, source_point, point, distance))

    result = []
    keys = set()
    for name, owner_id, source_name, leader, source_point, point, distance in labels:
        note = notes[name]
        source_side = "left" if owner_id.endswith("-left") else "right" if owner_id.endswith("-right") else None
        paired_owner = source_side is not None
        paired = paired_owner or note["paired"]
        if paired:
            if source_side is None:
                source_side = "left" if source_point[0] + center[0] >= 0 else "right"
            sides = [source_side, "right" if source_side == "left" else "left"]
        else:
            sides = [None]
        for side in sides:
            use_mirror = side is not None and side != source_side
            target_owner = re.sub(r"-(left|right)$", f"-{side}", owner_id) if paired_owner else owner_id
            key = (name, target_owner, side)
            if key in keys:
                continue
            keys.add(key)
            target_point = mirror(point, center) if use_mirror else point
            target_source = mirror(source_point, center) if use_mirror else source_point
            if use_mirror:
                target_point, _ = surfaces[target_owner].nearest(target_point)
            view = note["view"]
            if view in {"left", "right"} and side:
                view = side
            label = f"{side.capitalize()} {name[0].lower() + name[1:]}" if side else name
            record = {"name": label, "category": note["category"], "owners": [target_owner], "description": note["description"], "view": view, "source_name": source_name, "source_leader": leader, "aliases": [name]}
            method = "source-label-mirrored-nearest-bone-surface" if use_mirror else "source-label-nearest-bone-surface"
            if name in SOURCE_LOCATIONS and (distance > 10 or name in {"Frontal angle of parietal bone", "Occipital angle of parietal bone"}):
                target_point = np.array(SOURCE_LOCATIONS[name], dtype=float)
                if side == "right":
                    target_point = mirror(target_point, center)
                # Keep cavity/opening centres in their space rather than snapping
                # them onto an arbitrary neighboring wall.
                if note["category"] in {"parts", "ridges", "processes", "landmarks"}:
                    target_point, _ = surfaces[target_owner].nearest(target_point)
                method = "curated-study-location-from-source-label"
            result.append(positioned(record, target_point, reference, ct, method, source_point=rounded(target_source), projection_distance_mm=round(distance, 5)))
    return result, omitted


def curated_records(reference: dict, ct: dict, center: np.ndarray) -> list[dict]:
    result = []
    for detail in curated_details():
        for side in (["left", "right"] if detail["paired"] else [None]):
            point = np.asarray(detail["point"], dtype=float)
            if side == "right":
                point = mirror(point, center)
            view = detail["view"]
            if side and view in {"left", "right"}:
                view = side
            record = {
                "name": f"{side.capitalize()} {detail['name'][0].lower() + detail['name'][1:]}" if side else detail["name"],
                "category": detail["category"],
                "owners": side_owners(detail["owners"], side, reference),
                "description": detail["description"], "source_name": None,
                "view": view, "aliases": [detail["name"]],
            }
            record.update(detail.get("metadata", {}))
            result.append(positioned(record, point, reference, ct, "curated-study-location"))
    return result


def feature_mesh_records(reference: dict, ct: dict, center: np.ndarray) -> list[dict]:
    definitions = [
        ("Frontal sinus", "Sinus of frontal bone", "frontal-bone", "Air-filled cavity inside the frontal bone above the orbital roof. Its source cavity mesh supplies this study centre.", "anterior"),
        ("Sphenoidal sinus", "Sinus of sphenoid bone", "sphenoid-bone", "Air-filled cavity inside the sphenoid body below the sella turcica. Its source cavity mesh supplies this study centre.", "anterior"),
    ]
    for side in ["left", "right"]:
        for region in ["Anterior", "Middle", "Posterior"]:
            definitions.append((f"{side.capitalize()} {region.lower()} ethmoidal air cells", f"{region} cells of ethmoid bone.{side[0]}", "ethmoid-bone", f"{region} group of bony ethmoidal air spaces between the orbit and nasal cavity. The pin marks the source cavity-mesh centre, not each tiny cell wall.", "anterior"))
    result = []
    for name, source_name, owner, description, view in definitions:
        obj = bpy.data.objects[source_name]
        vertices = np.array([obj.matrix_world @ vertex.co for vertex in obj.data.vertices]) @ ROTATION.T * 1000 - center
        point = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
        result.append(positioned({"name": name, "category": "sinuses", "owners": [owner], "description": description, "source_name": source_name, "view": view}, point, reference, ct, "source-cavity-mesh-centre", source_bounds=rounded([vertices.min(axis=0), vertices.max(axis=0)])))
    return result


def contact_points(first: str, second: str, surfaces: dict) -> tuple[np.ndarray, float]:
    a, b = surfaces[first].mesh, surfaces[second].mesh
    distances, indices = surfaces[second].vertices.query(a.vertices)
    threshold = 2.5
    eligible = distances < threshold
    if eligible.sum() < 5:
        # A source gap is reported instead of inventing a continuous contact path.
        threshold = max(2.5, float(np.partition(distances, min(7, len(distances) - 1))[min(7, len(distances) - 1)]) + 0.01)
        eligible = distances <= threshold
    return (a.vertices[eligible] + b.vertices[indices[eligible]]) / 2, threshold


def trace_contacts(points: np.ndarray, axis: int | None = None, outer_axis: int | None = None, positive: bool = True) -> np.ndarray:
    if axis is None:
        axis = int(np.argmax(np.ptp(points, axis=0)))
    span = float(np.ptp(points[:, axis]))
    if span < 0.2 or len(points) < 4:
        return np.array([points.mean(axis=0)])
    boundaries = np.linspace(points[:, axis].min(), points[:, axis].max() + 1e-5, min(25, max(5, int(span / 3))) + 1)
    path = []
    shell_groups = []
    for low, high in zip(boundaries[:-1], boundaries[1:]):
        group = points[(points[:, axis] >= low) & (points[:, axis] < high)]
        if not len(group):
            continue
        if outer_axis is not None:
            values = group[:, outer_axis] * (1 if positive else -1)
            point = group[int(np.argmax(values))]
            shell_groups.append(group[values >= values.max() - 2])
        else:
            # Exterior surface points have greater distance from the skull centre.
            point = group[int(np.argmax(np.linalg.norm(group, axis=1)))]
        path.append(point)
    path = np.asarray(path)
    # Follow a connected chain through the actual contact cloud instead of
    # drawing long chords between sparse axis bins or disconnected branches.
    unique = np.unique(np.concatenate(shell_groups).round(5), axis=0) if shell_groups else np.unique(points.round(5), axis=0)
    tree = cKDTree(unique)
    distances, neighbours = tree.query(unique, k=min(18, len(unique)))
    distances, neighbours = np.atleast_2d(distances), np.atleast_2d(neighbours)
    rows = np.repeat(np.arange(len(unique)), distances.shape[1])
    cols = neighbours.ravel()
    values = distances.ravel()
    allowed = (values > 1e-6) & (values <= 8)
    graph = csr_matrix((values[allowed], (rows[allowed], cols[allowed])), shape=(len(unique), len(unique)))
    start = int(tree.query(path[0])[1])
    end = int(tree.query(path[-1])[1])
    costs, previous = dijkstra(graph, directed=False, indices=start, return_predecessors=True)
    if np.isfinite(costs[end]):
        indices = [end]
        while indices[-1] != start:
            indices.append(int(previous[indices[-1]]))
        connected = unique[indices[::-1]]
        sampled = [connected[0]]
        for point in connected[1:-1]:
            if np.linalg.norm(point - sampled[-1]) >= 1:
                sampled.append(point)
        sampled.append(connected[-1])
        return np.asarray(sampled)
    if len(path) > 1 and np.linalg.norm(np.diff(path, axis=0), axis=1).max() > 12:
        return np.array([path[len(path) // 2]])
    return path


def suture_records(reference: dict, ct: dict, surfaces: dict) -> list[dict]:
    result = []
    for name, owners, view, description, layout in SUTURES:
        sides = ["left", "right"] if layout == "paired" else [None]
        for side in sides:
            owner_ids = side_owners(owners, side, reference)
            gaps = []
            if layout in {"coronal", "lambdoid"}:
                paths = []
                for part in ["left", "right"]:
                    first = "frontal-bone" if layout == "coronal" else "occipital-bone"
                    points, gap = contact_points(first, f"parietal-bone-{part}", surfaces)
                    gaps.append(gap)
                    if layout == "coronal":
                        path = trace_contacts(points, axis=1, outer_axis=2, positive=True)
                    else:
                        path = trace_contacts(points, axis=0, outer_axis=2, positive=False)
                        if part == "left":
                            path = path[::-1]
                    paths.append(path)
                path = np.concatenate([paths[0], paths[1][::-1]])
            elif layout == "median-palatine":
                first, gap1 = contact_points("maxilla-left", "maxilla-right", surfaces)
                second, gap2 = contact_points("palatine-bone-left", "palatine-bone-right", surfaces)
                first_cut = owner_union(["maxilla-left", "maxilla-right"], reference)
                second_cut = owner_union(["palatine-bone-left", "palatine-bone-right"], reference)
                points = np.concatenate([first[first[:, 1] < first_cut[0, 1] + np.ptp(first_cut[:, 1]) * 0.25], second[second[:, 1] < second_cut[0, 1] + np.ptp(second_cut[:, 1]) * 0.3]])
                gaps.extend([gap1, gap2])
                path = trace_contacts(points, axis=2, outer_axis=1, positive=False)
            elif layout == "transverse-palatine":
                paths = []
                for part in ["left", "right"]:
                    points, gap = contact_points(f"maxilla-{part}", f"palatine-bone-{part}", surfaces)
                    gaps.append(gap)
                    cutoff = points[:, 1].min() + np.ptp(points[:, 1]) * 0.28
                    points = points[points[:, 1] <= cutoff]
                    paths.append(trace_contacts(points, axis=0, outer_axis=1, positive=False))
                path = np.concatenate([paths[1], paths[0]])
            else:
                points, gap = contact_points(owner_ids[0], owner_ids[1], surfaces)
                gaps.append(gap)
                if layout == "sagittal":
                    path = trace_contacts(points, axis=2, outer_axis=1, positive=True)
                else:
                    # Named subregions constrain pairs with larger shared boundaries.
                    if name == "Squamous suture":
                        bounds = np.array(reference[f"temporal-bone-{side}"]["bounds"])
                        points = points[points[:, 2] > bounds[0, 2] + np.ptp(bounds[:, 2]) * 0.15]
                    if name == "Parietomastoid suture":
                        bounds = np.array(reference[f"temporal-bone-{side}"]["bounds"])
                        points = points[points[:, 2] <= bounds[0, 2] + np.ptp(bounds[:, 2]) * 0.15]
                    if name == "Intermaxillary suture":
                        bounds = owner_union(owner_ids, reference)
                        points = points[points[:, 2] > bounds[0, 2] + np.ptp(bounds[:, 2]) * 0.7]
                    if name == "Frontoethmoidal suture":
                        points = points[points[:, 0] * (1 if side == "left" else -1) > 2]
                    if name in {"Sphenofrontal suture", "Sphenoparietal suture"}:
                        points = points[points[:, 0] * (1 if side == "left" else -1) > 2]
                    if name == "Intermaxillary suture":
                        path = trace_contacts(points, axis=1, outer_axis=2, positive=True)
                    elif name == "Squamous suture":
                        path = trace_contacts(points, axis=2, outer_axis=0, positive=side == "left")
                    else:
                        path = trace_contacts(points)
            if not len(path) or not np.isfinite(path).all():
                raise RuntimeError(f"Cannot derive a bounded contact path: {name} {side or ''}")
            point = path[len(path) // 2]
            record = {"name": f"{side.capitalize()} {name[0].lower() + name[1:]}" if side else name, "category": "sutures", "owners": owner_ids, "description": description, "source_name": " / ".join(reference[owner]["source_name"] for owner in owner_ids), "view": side if view == "left" and side else view, "aliases": [name], "geometry_status": "inferred-bone-contact-path"}
            # Gapped source joints receive a point, not a misleading continuous line.
            continuous = len(path) > 1 and np.linalg.norm(np.diff(path, axis=0), axis=1).max() <= 12
            draw_path = path if max(gaps) <= 3.0 and continuous else None
            result.append(positioned(record, point, reference, ct, "source-bone-contact-path", path=draw_path, contact_threshold_mm=round(max(gaps), 5)))
    return result


def deduplicate(records: list[dict]) -> list[dict]:
    """Merge anatomical synonyms while retaining their names as search aliases."""
    canonical = {
        "Orbital plate of frontal bone": "Orbital part of frontal bone",
        "Maxillary dental arch": "Alveolar process of maxilla",
        "Mandibular dental arch": "Alveolar part of mandible",
        "Supra-orbital notch": "Supraorbital notch or foramen",
        "Supraorbital foramen": "Supraorbital notch or foramen",
    }
    result = {}
    for entry in records:
        match = re.match(r"^(Left|Right) (.+)$", entry["name"])
        side = match[1] if match else None
        base = match[2][0].upper() + match[2][1:] if match else entry["name"]
        # The source whole-arch entry covers the paired manually placed regions.
        if base == "Alveolar part of mandible" and side:
            continue
        original_name = entry["name"]
        final_base = canonical.get(base, base)
        final_name = f"{side} {final_base[0].lower() + final_base[1:]}" if side else final_base
        entry["name"], entry["id"] = final_name, slug(final_name)
        aliases = {original_name, base, final_base, *entry.get("aliases", [])}
        aliases.update(alias.replace("-", "") for alias in list(aliases) if "-" in alias)
        if final_base == "Supraorbital notch or foramen":
            aliases.update(["Supraorbital notch", "Supraorbital foramen"])
            entry["description"] = "Passage in the superior frontal orbital rim, appearing as an open supraorbital notch or an enclosed foramen. These are alternative forms of the same landmark."
        if final_base == "Alveolar part of mandible":
            aliases.add("Mandibular dental arch")
            entry["description"] = "Curved tooth-bearing superior part of the mandibular body containing the lower dental alveoli."
        entry["aliases"] = sorted(aliases - {final_name})
        key = (entry["id"], tuple(sorted(entry["owners"])))
        if key not in result:
            result[key] = entry
            continue
        existing = result[key]
        combined_aliases = sorted(set(existing["aliases"] + entry["aliases"]))
        # Prefer the explicitly named canonical entry over an alternative label.
        chosen = entry if base == final_base else existing
        chosen["aliases"] = combined_aliases
        chosen["source_names"] = sorted({name for item in [existing, entry] for name in [item.get("source_name"), *item.get("source_names", [])] if name})
        result[key] = chosen
    return list(result.values())


def validate(records: list[dict], reference: dict, ct: dict) -> None:
    ids = [entry["id"] for entry in records]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Duplicate landmark IDs: {[key for key, count in Counter(ids).items() if count > 1]}")
    anatomical_keys = [(re.sub(r"[^a-z0-9]", "", entry["name"].lower()), tuple(sorted(entry["owners"]))) for entry in records]
    if len(anatomical_keys) != len(set(anatomical_keys)):
        raise RuntimeError("Duplicate normalized anatomical name and bone owner")
    categories = {category["id"] for category in CATEGORIES}
    for entry in records:
        if entry["category"] not in categories or entry["view"] not in VIEWS:
            raise RuntimeError(f"Invalid category/view: {entry['id']}")
        if not entry["owners"] or any(owner not in reference or reference[owner]["kind"] != "bone" for owner in entry["owners"]):
            raise RuntimeError(f"Non-bone owner: {entry['id']}")
        if len(entry["description"]) < 35:
            raise RuntimeError(f"Missing useful anatomical note: {entry['id']}")
        for dataset, placement in entry["positions"].items():
            entries = reference if dataset == "reference" else ct
            if any(owner not in entries for owner in entry["owners"]):
                raise RuntimeError(f"Landmark targets absent bone in {dataset}: {entry['id']}")
            if placement.get('location_status') == 'unlocated':
                if placement['point'] is not None or placement.get('label_fit', {}).get('attachment') != 'unlocated':
                    raise RuntimeError(f"Unlocated landmark has a spatial target: {entry['id']} {dataset}")
                continue
            points = np.array([placement["point"], *placement.get("path", [])])
            if points.shape[1] != 3 or not np.isfinite(points).all() or np.abs(points).max() > 200:
                raise RuntimeError(f"Invalid or untransformed landmark coordinates: {entry['id']}")
            bounds = owner_union(entry["owners"], entries)
            outside = np.maximum(bounds[0] - points, 0) + np.maximum(points - bounds[1], 0)
            if np.linalg.norm(outside, axis=1).max() > 30:
                raise RuntimeError(f"Landmark lies too far outside its bone region: {entry['id']} {dataset}")
            if "path" in placement and np.linalg.norm(np.diff(placement["path"], axis=0), axis=1).max() > 12:
                raise RuntimeError(f"Discontinuous anatomical contact path: {entry['id']} {dataset}")
            if dataset == "ct" and "path" in placement:
                raise RuntimeError("Reference contact paths must not be transferred to a different CT subject")
    if set(Counter(entry["category"] for entry in records)) != categories:
        raise RuntimeError("A landmark category has no entries")
    if len(records) < 300:
        raise RuntimeError("Incomplete landmark export")


def export(source: Path, output: Path) -> None:
    if digest(source) != BLEND_SHA256:
        raise RuntimeError("Refusing to read a different source atlas; the pinned .blend digest must match")
    reference_manifest = json.loads((ROOT / "assets/skull/manifest.json").read_text())
    ct_manifest = json.loads((ROOT / "assets/skull-ct/manifest.json").read_text())
    reference = {entry["id"]: entry for entry in reference_manifest["structures"]}
    ct = {entry["id"]: entry for entry in ct_manifest["structures"]}
    center = np.array(reference_manifest["source"]["viewer_center_before_translation_mm"])
    glb = ROOT / reference_manifest["model"]
    if digest(glb) != reference_manifest["sha256"]:
        raise RuntimeError("Reference GLB differs from its manifest")
    scene = trimesh.load(glb, force="scene", process=False)
    surfaces = {key: BoneSurface(mesh) for key, mesh in scene.geometry.items() if reference[key]["kind"] == "bone"}
    bpy.ops.wm.open_mainfile(filepath=str(source.resolve()), load_ui=False, use_scripts=False)
    bpy.context.view_layer.update()
    records, omitted = source_label_records(reference, ct, center, surfaces)
    records.extend(curated_records(reference, ct, center))
    records.extend(feature_mesh_records(reference, ct, center))
    records.extend(suture_records(reference, ct, surfaces))
    records = deduplicate(records)
    records.sort(key=lambda entry: (next(i for i, category in enumerate(CATEGORIES) if category["id"] == entry["category"]), entry["name"]))
    from fit_point_landmarks import fit_points
    fit_points(records)
    validate(records, reference, ct)
    coverage = Counter(entry["category"] for entry in records)
    result = {
        "schema_version": 1,
        "title": "Skull bone landmarks",
        "units": "millimetres",
        "axes": reference_manifest["axes"],
        "source": {
            "name": "Z-Anatomy / BodyParts3D bone labels with curated study terminology",
            "url": SOURCE_URL, "commit": SOURCE_COMMIT, "blend_sha256": BLEND_SHA256,
            "reference_model_sha256": reference_manifest["sha256"], "ct_model_sha256": ct_manifest["sha256"],
            "license": "assets/landmarks/ATTRIBUTION.txt",
            "terminology_url": TERMINOLOGY_URL, "overview_url": OVERVIEW_URL,
            "placement_notes": "All markers are approximate study annotations, not anatomical segmentations or validated surgical coordinates. Reference source-label anchors use the endpoint of the authored leader nearest the named bone and project it onto that bone's real mesh. Labels placed over 10 mm from the surface use explicitly curated study locations when provided; the original leader endpoint and distance are retained. Missing opposite-side anchors are explicitly mirrored. Curated locations are identified separately. Reference suture paths are inferred continuous exterior-contact chains on real bone meshes, not traced source curves. Bregma and lambda instead fit an exterior three-bone junction independently on each native specimen. Other CT positions transfer each reference point through the owning bones' combined bounding boxes; individual skull shape can shift fine landmarks substantially. Reference suture paths are never transferred to CT. No reference mesh is overlaid on the CT specimen.",
        },
        "categories": [{**category, "count": coverage[category["id"]]} for category in CATEGORIES],
        "coverage": {
            "landmarks": len(records), "reference": len(records),
            "ct": sum("ct" in entry["positions"] for entry in records),
            "source_label_entries": sum("source_leader" in entry for entry in records),
            "curated_source_corrections": sum(entry["positions"]["reference"]["method"] == "curated-study-location-from-source-label" for entry in records),
            "source_cavity_centres": 8,
            "suture_paths": sum("path" in entry["positions"]["reference"] for entry in records if entry["category"] == "sutures"),
            "categories": dict(coverage),
            "internal_layers": {"status": "separate-study", "related_view": "microct", "description": "Compact outer and inner tables, diploe and trabecular architecture require a separate real micro-CT bone specimen. The skull meshes do not segment these tissue layers."},
            "limitations": [
                "Individual nutrient foramina, diploic canals, every tiny olfactory opening and variable accessory foramina are not individually segmented or counted.",
                "Internal canal courses, bony labyrinth and air-cell partitions are not reconstructed by point annotations.",
                "Metopic, accessory occipital, squamomastoid and incisive sutures, infant fontanelles and other developmental variants are not fabricated on this adult skull.",
                "Reference auditory-ossicle and hyoid landmarks are available only in the reference model; these bones are absent from the CT specimen.",
            ],
        },
        "landmarks": records,
    }
    from refine_label_positions import refine_catalogue
    refinement = refine_catalogue(result)
    result['coverage']['unlocated'] = {dataset: sum(entry['positions'].get(dataset, {}).get('location_status') == 'unlocated' for entry in records)
                                       for dataset in ['reference', 'ct']}
    validate(records, reference, ct)
    output.mkdir(parents=True, exist_ok=True)
    (output / "catalogue.json").write_text(json.dumps(result, indent=2) + "\n")
    report = {"landmarks": len(records), "categories": dict(coverage), "source_labels_not_directly_positioned": omitted, "ct_unavailable": [{"id": entry["id"], "owners": entry["owners"]} for entry in records if "ct" not in entry["positions"]], "source_projection_over_10_mm": [{"id": entry["id"], "distance_mm": entry["positions"]["reference"]["projection_distance_mm"], "placement_method": entry["positions"]["reference"]["method"]} for entry in records if entry["positions"]["reference"].get("projection_distance_mm", 0) > 10]}
    report['unlocated'] = [{"id": entry['id'], "datasets": [dataset for dataset, placement in entry['positions'].items() if placement.get('location_status') == 'unlocated']}
                           for entry in records if any(placement.get('location_status') == 'unlocated' for placement in entry['positions'].values())]
    (output / "export-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "label-refinement-report.json").write_text(json.dumps(refinement, indent=2) + "\n")
    (output / "ATTRIBUTION.txt").write_text(
        "Skull study landmark catalogue\n\n"
        f"Anatomical label identities, leaders, cavity geometry and reference bone geometry: Z-Anatomy / BodyParts3D, pinned commit {SOURCE_COMMIT}.\n"
        f"Source: {SOURCE_URL}\n"
        "Source model attribution and license: ../skull/ATTRIBUTION.txt and ../skull/SOURCE-LICENSE.txt (CC BY-SA 4.0).\n"
        "Retain the source attribution and applicable terms: Z-Anatomy CC BY-SA 4.0; BodyParts3D CC BY-SA 2.1 Japan.\n"
        "The source atlas additionally credits University of Dundee School of Medicine, Anatomy of the Inner Ear, CC BY-NC-SA 4.0.\n"
        "Retain those noncommercial share-alike terms for ossicle-derived placements; the atlas supplies no per-object license map.\n"
        "License links and full credits are retained in the reference model attribution files cited above.\n"
        "Anatomical terms are public domain. Terminology reference: FIPAT, Terminologia Anatomica, second edition, 2019 (IFAA adoption 2020).\n"
        f"Terminology: {TERMINOLOGY_URL}\n"
        f"General anatomy reference: {OVERVIEW_URL}\n"
        "Descriptions and additional study annotations were written for this viewer.\n\n"
        "Reference pins are projected source leaders, mirrored pins, actual source cavity centres or expressly curated study locations.\n"
        "Source labels displaced more than 10 mm can use explicitly curated replacement positions; original leader points/distances remain recorded.\n"
        "CT label locations use bone-shape registration, native cortex, supported native fits and available source-region anchors. Original study coordinates remain in initial_placement. These are not independently validated anatomical coordinates.\n"
        "Accepted mapped-region label anchors retain BioDigital/UBC source URLs per placement; retain ../landmark-regions/REGISTERED-SOURCES.txt and publisher terms for those local derivatives.\n"
        "Suture polylines are inferred continuous exterior reference bone-contact locations, not separately segmented fibrous joints. They are omitted on CT.\n"
        "No bony microstructure or missing canal walls have been synthesized. See catalogue.json coverage for limits and the separate micro-CT study.\n"
    )
    print(f"Exported {len(records)} study landmarks across {len(CATEGORIES)} categories; {result['coverage']['ct']} available in CT; {result['coverage']['suture_paths']} reference suture paths.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    export(args.source, args.output)
