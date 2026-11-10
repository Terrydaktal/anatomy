#!/usr/bin/env python3
"""Fit selected foramen rims to real native-mesh depth openings.

An anatomical guide only chooses the search window. The fitted region follows
a closed depth depression measured on the specimen's own triangle mesh. Its
irregular rim is clipped from those original faces; no disks, caps, reference
registration, or synthetic tunnel surfaces are generated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

# Importing bpy registers the standalone wheel's mathutils extension modules.
import bpy  # noqa: F401
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import numpy as np
from scipy import ndimage
import trimesh

from source_io import digest
from region_registration import load_native_bones


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/landmark-regions"
TARGETS = [f"{side}-{part}-foramen" for part in ["infraorbital", "mental"] for side in ["left", "right"]]
STEP = 0.125
HALF_WINDOW = 12.0
DEPTH_THRESHOLD = 1.6
OUTER_RIM = 0.7
INNER_RIM = 0.4
MAX_WALL_DEPTH = 6.0
MAX_PROJECTED_EDGE = 0.2


def frame_for(entry: dict, dataset: str) -> dict:
    side = -1 if entry["id"].startswith("right-") else 1
    angle = np.deg2rad(45 if "mental-foramen" in entry["id"] else 0)
    normal = np.array([side * np.sin(angle), 0, np.cos(angle)])
    horizontal = np.array([np.cos(angle), 0, -side * np.sin(angle)])
    vertical = np.array([0, 1, 0])
    return {"origin": np.array(entry["positions"][dataset]["point"]), "normal": normal, "u": horizontal, "v": vertical}


def depth_map(mesh: trimesh.Trimesh, tree: BVHTree, frame: dict) -> dict:
    count = round(HALF_WINDOW * 2 / STEP) + 1
    us = np.linspace(-HALF_WINDOW, HALF_WINDOW, count)
    vs = np.linspace(HALF_WINDOW, -HALF_WINDOW, count)
    depth = np.full((count, count), np.nan)
    faces = np.full((count, count), -1, dtype=np.int32)
    direction = Vector((-frame["normal"]).tolist())
    for row, v in enumerate(vs):
        for column, u in enumerate(us):
            origin = frame["origin"] + frame["u"] * u + frame["v"] * v + frame["normal"] * 150
            point, _normal, face, _distance = tree.ray_cast(Vector(origin.tolist()), direction, 400)
            if point is not None:
                depth[row, column] = np.dot(point, frame["normal"])
                faces[row, column] = face
    valid = np.isfinite(depth)
    if valid.sum() < count * count * 0.25:
        raise ValueError("The search view does not contain enough native bone surface")
    filled = np.where(valid, depth, float(np.nanmedian(depth)))
    # A local morphological closing measures a drop below the surrounding
    # cortex without treating the general facial slope as the aperture.
    baseline = ndimage.grey_closing(filled, size=(49, 49))
    residual = baseline - filled
    deep = (residual > DEPTH_THRESHOLD) | ~valid
    labels, label_count = ndimage.label(deep)
    candidates = []
    for label in range(1, label_count + 1):
        mask = labels == label
        rows, columns = np.nonzero(mask)
        if not len(rows) or np.any(rows == 0) or np.any(columns == 0) or np.any(rows == count - 1) or np.any(columns == count - 1):
            continue
        area = len(rows) * STEP * STEP
        center = np.array([us[columns].mean(), vs[rows].mean()])
        guide_distance = float(np.linalg.norm(center))
        peak = float(np.max(residual[mask]))
        if 0.7 <= area <= 22 and guide_distance <= 7.5 and peak >= 2.5:
            candidates.append({"label": label, "mask": mask, "area": area, "center": center, "guide_distance": guide_distance, "depth_peak": peak})
    if not candidates:
        raise ValueError("No closed, sufficiently deep native aperture was resolved near the anatomical guide")
    chosen = min(candidates, key=lambda candidate: candidate["guide_distance"])
    mask = ndimage.binary_fill_holes(chosen["mask"])
    signed = (ndimage.distance_transform_edt(~mask) - ndimage.distance_transform_edt(mask)) * STEP
    signed = ndimage.gaussian_filter(signed, sigma=0.6)
    return {"us": us, "vs": vs, "depth": depth, "faces": faces, "baseline": baseline, "residual": residual, "mask": mask, "signed": signed, "selected": chosen, "frame": frame}


def interpolate(field: np.ndarray, uv: np.ndarray, cval: float = 1000) -> np.ndarray:
    rows = (HALF_WINDOW - uv[:, 1]) / STEP
    columns = (uv[:, 0] + HALF_WINDOW) / STEP
    return ndimage.map_coordinates(field, [rows, columns], order=1, mode="constant", cval=cval)


def clip(polygon: list[np.ndarray], field: int) -> list[np.ndarray]:
    if not polygon:
        return []
    result = []
    previous = polygon[-1]
    before = previous[field]
    for current in polygon:
        after = current[field]
        if (before >= 0) != (after >= 0):
            t = before / (before - after)
            result.append(previous + (current - previous) * t)
        if after >= 0:
            result.append(current)
        previous, before = current, after
    return result


def subdivisions(triangle: np.ndarray, frame: dict) -> np.ndarray:
    triangles = triangle[None, :, :]
    for _ in range(9):
        edges = triangles - np.roll(triangles, -1, axis=1)
        projected = np.stack([edges @ frame["u"], edges @ frame["v"]], axis=2)
        if np.linalg.norm(projected, axis=2).max() <= MAX_PROJECTED_EDGE:
            break
        a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        triangles = np.concatenate([np.stack([a, ab, ca], axis=1), np.stack([ab, b, bc], axis=1), np.stack([ca, bc, c], axis=1), np.stack([ab, bc, ca], axis=1)])
    return triangles


def fitted_triangles(mesh: trimesh.Trimesh, depth: dict) -> tuple[np.ndarray, np.ndarray]:
    frame = depth["frame"]
    # Only source faces actually hit beside the observed aperture are eligible.
    # The central opening stays excluded; it must never become a painted cap.
    search_ring = (depth["signed"] >= -INNER_RIM - STEP * 2) & (depth["signed"] <= OUTER_RIM + STEP * 2)
    candidate_faces = np.unique(depth["faces"][search_ring])
    candidate_faces = candidate_faces[candidate_faces >= 0]
    positions, source_faces = [], []
    for source_face in candidate_faces:
        pieces = subdivisions(mesh.triangles[source_face], frame)
        vertices = pieces.reshape(-1, 3)
        uv = np.column_stack([(vertices - frame["origin"]) @ frame["u"], (vertices - frame["origin"]) @ frame["v"]])
        distance = interpolate(depth["signed"], uv)
        baseline = interpolate(depth["baseline"], uv)
        wall = vertices @ frame["normal"] - baseline + MAX_WALL_DEPTH
        values = np.column_stack([vertices, OUTER_RIM - distance, INNER_RIM + distance, wall]).reshape(-1, 3, 6)
        for corners in values:
            polygon = list(corners)
            for field in [3, 4, 5]:
                polygon = clip(polygon, field)
            if len(polygon) < 3:
                continue
            for j in range(1, len(polygon) - 1):
                triangle = np.array([polygon[0][:3], polygon[j][:3], polygon[j + 1][:3]])
                if np.linalg.norm(np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])) < 1e-9:
                    continue
                positions.append(triangle)
                source_faces.append(source_face)
    if not positions:
        raise ValueError("Detected depth aperture has no valid native rim polygons")
    return np.asarray(positions), np.asarray(source_faces, dtype=np.uint32)


def validate_triangles(mesh: trimesh.Trimesh, triangles: np.ndarray, face_ids: np.ndarray, depth: dict) -> None:
    source = mesh.triangles[face_ids]
    for corner in range(3):
        barycentric = trimesh.triangles.points_to_barycentric(source, triangles[:, corner])
        reconstructed = np.einsum("ij,ijk->ik", barycentric, source)
        if barycentric.min() < -1e-5 or barycentric.max() > 1 + 1e-5 or np.abs(reconstructed - triangles[:, corner]).max() > 1e-5:
            raise RuntimeError("A fitted aperture polygon is not contained in its recorded original triangle")
    frame = depth["frame"]
    centers = triangles.mean(axis=1)
    uv = np.column_stack([(centers - frame["origin"]) @ frame["u"], (centers - frame["origin"]) @ frame["v"]])
    distances = interpolate(depth["signed"], uv)
    if distances.min() < -INNER_RIM - STEP or distances.max() > OUTER_RIM + STEP:
        raise RuntimeError("Clipped aperture rim leaked into the central opening or distant solid cortex")
    if len(set(face_ids.tolist())) < 4:
        raise ValueError("Too few independent native faces support a reliable aperture rim")


def diagnostic_image(path: Path, depth: dict, triangles: np.ndarray, title: str) -> None:
    from PIL import Image, ImageDraw

    filled = np.where(np.isfinite(depth["depth"]), depth["depth"], depth["baseline"] - 20)
    middle = float(np.median(depth["baseline"]))
    gray = np.uint8(np.clip((filled - middle + 13) / 26, 0, 1) * 220 + 20)
    rgb = np.repeat(gray[:, :, None], 3, axis=2)
    image = Image.fromarray(rgb).resize((772, 772))
    draw = ImageDraw.Draw(image, "RGBA")
    frame = depth["frame"]
    for triangle in triangles:
        local = triangle - frame["origin"]
        points = [(float((u + HALF_WINDOW) / STEP * 4), float((HALF_WINDOW - v) / STEP * 4)) for u, v in zip(local @ frame["u"], local @ frame["v"])]
        draw.polygon(points, fill=(65, 208, 232, 125))
    boundary = ndimage.binary_dilation(depth["mask"]) & ~depth["mask"]
    for row, column in zip(*np.nonzero(boundary)):
        draw.rectangle((int(column * 4), int(row * 4), int(column * 4 + 3), int(row * 4 + 3)), fill=(255, 200, 80, 220))
    draw.text((12, 12), title, fill=(255, 255, 255, 255))
    center = (HALF_WINDOW / STEP * 4, HALF_WINDOW / STEP * 4)
    draw.line((center[0] - 9, center[1], center[0] + 9, center[1]), fill=(100, 245, 160, 255), width=2)
    draw.line((center[0], center[1] - 9, center[0], center[1] + 9), fill=(100, 245, 160, 255), width=2)
    image.save(path)


def export(output: Path, diagnostic_dir: Path | None) -> None:
    catalogue = json.loads((ROOT / "assets/landmarks/catalogue.json").read_text())
    features = {entry["id"]: entry for entry in catalogue["landmarks"]}
    regions, unavailable, models, geometries, packed = [], [], {}, {}, []
    offset = 0
    for dataset, directory in [("ct", "skull-ct"), ("reference", "skull")]:
        manifest = json.loads((ROOT / f"assets/{directory}/manifest.json").read_text())
        model = ROOT / manifest["model"]
        if digest(model) != manifest["sha256"]:
            raise RuntimeError(f"{dataset} model differs from its source manifest")
        scene = trimesh.load(model, force="scene", process=False)
        corrected = load_native_bones(model)
        models[dataset] = {"model": manifest["model"], "sha256": manifest["sha256"]}
        trees = {}
        for feature_id in TARGETS:
            entry = features[feature_id]
            owner = entry["owners"][0]
            mesh = corrected[owner]
            if owner not in trees:
                trees[owner] = BVHTree.FromPolygons(mesh.vertices.tolist(), mesh.faces.tolist(), all_triangles=True)
            geometry_key = f"{dataset}:{owner}"
            geometries[geometry_key] = {"vertices": len(mesh.vertices), "triangles": len(mesh.faces), "positions_sha256": hashlib.sha256(np.asarray(mesh.vertices, dtype="<f4").tobytes()).hexdigest(), "indices_sha256": hashlib.sha256(np.asarray(scene.geometry[owner].faces, dtype="<u4").tobytes()).hexdigest(), "winding_corrections": mesh.metadata['winding_corrections']}
            try:
                depth = depth_map(mesh, trees[owner], frame_for(entry, dataset))
                triangles, face_ids = fitted_triangles(mesh, depth)
                validate_triangles(mesh, triangles, face_ids, depth)
                triangle_areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2
                area = float(triangle_areas.sum())
                centers = triangles.mean(axis=1)
                area_center = np.average(centers, axis=0, weights=triangle_areas)
                anchor = centers[int(np.argmin(np.linalg.norm(centers - area_center, axis=1)))]
                selected = depth["selected"]
                local_center = np.array(selected["center"])
                axial = float(interpolate(depth["baseline"], local_center[None, :])[0])
                frame = depth["frame"]
                center = frame["origin"] + frame["u"] * local_center[0] + frame["v"] * local_center[1]
                center += frame["normal"] * (axial - np.dot(center, frame["normal"]))
                position_data = np.asarray(triangles, dtype="<f4").tobytes()
                face_data = np.asarray(face_ids, dtype="<u4").tobytes()
                record = {
                    "dataset": dataset, "source_model_sha256": manifest["sha256"],
                    "feature_id": feature_id, "owner": owner,
                    "method": "native-depth-fitted-aperture-rim",
                    "evidence": "Closed depth depression measured from the specimen's original mesh; anatomical identity and depth contour are inferred. Rim polygons are subdivisions/clips of recorded original source triangles, never a synthetic aperture cap.",
                    "approximate": True,
                    "bounds": np.array([triangles.reshape(-1, 3).min(axis=0), triangles.reshape(-1, 3).max(axis=0)]).round(5).tolist(),
                    "center": center.round(5).tolist(), "surface_anchor": anchor.round(5).tolist(),
                    "area": round(area, 6), "triangle_count": len(triangles),
                    "coordinate_units": "millimetres" if dataset == "reference" else "uncalibrated-display-units",
                    "area_units": "square millimetres" if dataset == "reference" else "square display units",
                    "positions_byte_offset": offset, "positions_count": triangles.size,
                    "source_faces_byte_offset": offset + len(position_data), "source_faces_count": len(face_ids),
                    "fit": {
                        "frame": {key: value.round(7).tolist() for key, value in frame.items()},
                        "depth_resolution": STEP, "search_half_width": HALF_WINDOW,
                        "depth_threshold": DEPTH_THRESHOLD,
                        "observed_aperture_area": round(float(selected["area"]), 6),
                        "guide_offset": round(float(selected["guide_distance"]), 6),
                        "aperture_offset_uv": local_center.round(5).tolist(),
                        "depth_peak": round(float(selected["depth_peak"]), 6),
                        "outer_rim_width": OUTER_RIM, "inner_rim_width": INNER_RIM,
                        "max_wall_depth": MAX_WALL_DEPTH, "max_projected_subtriangle_edge": MAX_PROJECTED_EDGE,
                        "native_source_faces": len(set(face_ids.tolist())),
                        "central_opening_excluded": True,
                        "coordinate_note": "Distances/areas use viewer coordinates. The CT model's display scale is not physically calibrated.",
                    },
                }
                packed.extend([position_data, face_data])
                offset += len(position_data) + len(face_data)
                regions.append(record)
                if diagnostic_dir:
                    diagnostic_dir.mkdir(parents=True, exist_ok=True)
                    diagnostic_image(diagnostic_dir / f"{dataset}-{feature_id}.png", depth, triangles, f"{dataset}: {feature_id} | cyan native rim, gold depth contour, green guide")
                print(f"{dataset} {feature_id}: aperture {selected['area']:.3f}, guide offset {selected['guide_distance']:.3f}, native rim area {area:.3f}, {len(triangles)} clipped triangles / {len(set(face_ids.tolist()))} source faces", flush=True)
            except ValueError as error:
                unavailable.append({"dataset": dataset, "feature_id": feature_id, "owner": owner, "reason": str(error)})
                print(f"{dataset} {feature_id}: unavailable: {error}", flush=True)
    output.mkdir(parents=True, exist_ok=True)
    binary = output / "apertures.bin"
    binary.write_bytes(b"".join(packed))
    if binary.stat().st_size != offset:
        raise RuntimeError("Aperture binary packing lost a record")
    metadata = {
        "schema_version": 1,
        "title": "Native geometry-fitted skull aperture rims",
        "models": models, "geometries": geometries,
        "binary": {"url": "assets/landmark-regions/apertures.bin", "sha256": digest(binary), "byte_length": offset, "positions_type": "float32-le", "source_faces_type": "uint32-le"},
        "notes": "Only supported, closed apertures near named anatomical guides are exported. Anterior views are used for infraorbital foramina and 45-degree anterolateral views for mental foramina. Each rim follows the irregular native depth contour; it is not a circular guide neighborhood. Anatomical identity, thresholds and contour fitting remain inferred rather than manually segmented anatomy. The actual central opening is excluded. Every emitted vertex remains on its recorded original specimen triangle. Source face IDs refer to the manifest GLB order and must not be reordered by a BVH. No geometry or region boundary is transferred between specimens.",
        "coverage": {"regions": len(regions), "ct": sum(region["dataset"] == "ct" for region in regions), "reference": sum(region["dataset"] == "reference" for region in regions)},
        "regions": regions, "unavailable": unavailable,
    }
    (output / "apertures.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Exported {len(regions)} native aperture rims, {offset:,} bytes; {len(unavailable)} unsupported targets.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--diagnostic-dir", type=Path)
    args = parser.parse_args()
    export(args.output, args.diagnostic_dir)
