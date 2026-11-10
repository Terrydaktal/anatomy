#!/usr/bin/env python3
"""Export the full-detail OUVC 10503 CT skull from Ohio University's public 3D PDF."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import struct

import numpy as np
import pikepdf
import trimesh

from skull_catalogue import GROUPS, catalogue
from source_io import digest, download


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/source/witmer"
OUTPUT = ROOT / "assets/skull-ct"
PDF_URL = "https://people.ohio.edu/witmerl/3D_human/OU-HCOM_3D_skull_highest-res.pdf"
PDF_SHA256 = "323cb4b3ab608da1dda6bb7582d65a0560f637131e56bd197fc4a5c8bcd2f869"
DECODER_COMMIT = "71c5f4428ae3bcff9c2521c4e00dd938a703a648"
DECODER_URL = f"https://raw.githubusercontent.com/svhss/u3d-mesh-extract/{DECODER_COMMIT}/scripts/u3d2mesh.py"
DECODER_SHA256 = "d2a42da14e1bb6933f976c863f6b0c83fc08a7b2143eb33a62d33ef055ec6289"
DECODER_PATH = ROOT / "data/source/tools/u3d2mesh.py"

SOURCE_NAMES = {
    "frontal-bone": "Frontal bone", "parietal-bone-left": "Left Parietal", "parietal-bone-right": "Right Parietal",
    "temporal-bone-left": "Left Temporal", "temporal-bone-right": "Right Temporal", "occipital-bone": "Occipital bone",
    "sphenoid-bone": "Sphenoid bone", "ethmoid-bone": "Ethmoid bone", "maxilla-left": "Left Maxilla", "maxilla-right": "Right Maxilla",
    "zygomatic-bone-left": "Left Zygomatic", "zygomatic-bone-right": "Right Zygomatic", "nasal-bone-left": "Left Nasal", "nasal-bone-right": "Right Nasal",
    "lacrimal-bone-left": "Left Lacrimal", "lacrimal-bone-right": "Right Lacrimal", "palatine-bone-left": "Left Palatine", "palatine-bone-right": "Right Palatine",
    "inferior-nasal-concha-left": "Inferior turbinates", "inferior-nasal-concha-right": "Inferior turbinates", "vomer": "Vomer", "mandible": "Mandible",
}
ARCHES = {"upper-dentition": "Maxillary teeth", "lower-dentition": "Mandible teeth"}


@lru_cache(maxsize=1)
def decoder():
    spec = importlib.util.spec_from_file_location("pinned_u3d_decoder", DECODER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def decode_component(pair: tuple) -> dict:
    name, declaration, base, progressive = pair
    if progressive:
        raise RuntimeError(f"Progressive U3D content is unsupported; refusing an incomplete mesh: {name}")
    mesh = decoder()._decode_clod(declaration, base, False)
    path = SOURCE / "decoded" / f"{slug(name)}.npz"
    np.savez_compressed(path, vertices=np.asarray(mesh["positions"], dtype=np.float64), faces=np.asarray(mesh["faces"], dtype=np.int32))
    return {"name": name, "file": path.name, "vertices": len(mesh["positions"]), "faces": len(mesh["faces"])}


def source_meshes(extra_names: set[str] | None = None) -> dict[str, trimesh.Trimesh]:
    SOURCE.mkdir(parents=True, exist_ok=True)
    DECODER_PATH.parent.mkdir(parents=True, exist_ok=True)
    pdf = SOURCE / "skull.pdf"
    for url, path, checksum in [(PDF_URL, pdf, PDF_SHA256), (DECODER_URL, DECODER_PATH, DECODER_SHA256)]:
        if not path.exists():
            download(url, path)
        if digest(path) != checksum:
            raise RuntimeError(f"Pinned source checksum mismatch: {path.name}")
    with pikepdf.open(pdf) as document:
        streams = [bytes(obj.read_bytes()) for obj in document.objects if isinstance(obj, pikepdf.Stream) and str(obj.get("/Subtype")) == "/U3D"]
    if len(streams) != 1:
        raise RuntimeError("Expected exactly one U3D stream in the pinned skull PDF")
    raw = streams[0]
    blocks = decoder().parse_blocks(raw)
    header = next(data for kind, data, _ in blocks if kind == decoder().BT_FileHeader)
    profile = struct.unpack_from("<I", header, 4)[0]
    if profile != 8:
        raise RuntimeError("Unexpected U3D profile; decoder assumptions must be reviewed")
    wanted = set(SOURCE_NAMES.values()) | set(ARCHES.values()) | (extra_names or set())
    pairs = [pair for pair in decoder()._collect_clod_pairs(blocks) if pair[0] in wanted]
    if {pair[0] for pair in pairs} != wanted:
        raise RuntimeError("The PDF is missing one or more named anatomical resources")

    cache = SOURCE / "decoded"
    cache.mkdir(exist_ok=True)
    index = cache / "index.json"
    signature = f"{PDF_SHA256}:{DECODER_SHA256}"
    existing = json.loads(index.read_text()) if index.exists() else {}
    if existing.get("signature") != signature or any(not (cache / f"{slug(name)}.npz").exists() for name in wanted):
        records = []
        with ProcessPoolExecutor(max_workers=min(16, os.cpu_count() or 1)) as pool:
            for future in as_completed([pool.submit(decode_component, pair) for pair in pairs]):
                record = future.result()
                records.append(record)
                print(f"Decoded {record['name']}: {record['faces']:,} source triangles", flush=True)
        index.write_text(json.dumps({"signature": signature, "resources": records}, indent=2) + "\n")

    # All bone resources share the same U3D presentation rotation. Their local
    # vertices already share a common specimen frame; replace only that common
    # presentation rotation with an upright anatomical display rotation.
    nodes = decoder().parse_node_tree(raw)
    matrices = decoder()._world_transforms(nodes)
    transforms = [np.asarray(matrices[node["name"]]).reshape(4, 4, order="F") for node in nodes if node.get("resource") in wanted]
    if any(not np.allclose(transform, transforms[0], atol=1e-6) for transform in transforms):
        raise RuntimeError("Source resources no longer share one rigid presentation transform")
    c = 1 / np.sqrt(2)
    rotation = np.array([[1, 0, 0], [0, -c, c], [0, -c, -c]])
    result = {}
    for name in sorted(wanted):
        data = np.load(cache / f"{slug(name)}.npz")
        vertices, faces = data["vertices"], data["faces"]
        if not np.isfinite(vertices).all() or faces.min() < 0 or faces.max() >= len(vertices):
            raise RuntimeError(f"Invalid decoded source mesh: {name}")
        # The source U3D has an invalid zero unit-scale field. These are DISPLAY
        # units, deliberately not claimed to be calibrated millimetres.
        result[name] = trimesh.Trimesh(vertices=vertices @ rotation.T * 10, faces=faces, process=False)
    return result


def export() -> None:
    meshes = source_meshes()
    bones = [entry for entry in catalogue() if entry["group"] in {"cranial", "facial"}]
    conchae = meshes["Inferior turbinates"].split(only_watertight=False)
    if len(conchae) != 2:
        raise RuntimeError("The two inferior nasal conchae cannot be separated losslessly")
    conchae = sorted(conchae, key=lambda mesh: mesh.bounds.mean(axis=0)[0])
    for entry in bones:
        entry["source_name"] = SOURCE_NAMES[entry["id"]]
    entries = bones + [{
        "id": identity, "name": f"{arch.capitalize()} dentition", "source_name": name,
        "group": "teeth", "side": "bilateral", "kind": "dentition", "arch": arch,
        "color": "#e2d8bf", "visible": True,
        "description": f"The {arch} dental arch of the same CT-scanned specimen. The source groups these teeth into one mesh; this viewer preserves that grouping rather than inventing individual tooth boundaries.",
    } for identity, name in ARCHES.items() for arch in [identity.split("-")[0]]]
    bounds = np.array([meshes[entry["source_name"]].bounds for entry in bones])
    center = (bounds[:, 0].min(axis=0) + bounds[:, 1].max(axis=0)) / 2
    scene = trimesh.Scene()
    for entry in entries:
        mesh = meshes[entry["source_name"]].copy()
        if entry["id"].startswith("inferior-nasal-concha"):
            mesh = conchae[1 if entry["side"] == "left" else 0].copy()
        mesh.apply_translation(-center)
        # Angle-weighted smooth normals follow the actual dense CT surface.
        # No subdivision, geometric smoothing or invented surface texture.
        _ = mesh.vertex_normals
        mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            name=entry["id"], baseColorFactor=[*bytes.fromhex(entry["color"][1:]), 255], metallicFactor=0, roughnessFactor=0.8, doubleSided=True,
        ))
        mesh.metadata = {"source_name": entry["source_name"], "specimen": "OUVC 10503", "catalogue_id": entry["id"]}
        scene.add_geometry(mesh, node_name=entry["id"], geom_name=entry["id"])
        entry.update({"vertices": len(mesh.vertices), "triangles": len(mesh.faces), "bounds": mesh.bounds.round(5).tolist(), "center": mesh.bounds.mean(axis=0).round(5).tolist()})
    OUTPUT.mkdir(parents=True, exist_ok=True)
    model = OUTPUT / "skull.glb"
    model.write_bytes(scene.export(file_type="glb"))
    reloaded = trimesh.load(model, force="scene", process=False)
    assert set(reloaded.geometry) == {entry["id"] for entry in entries}
    for entry in entries:
        mesh = reloaded.geometry[entry["id"]]
        assert len(mesh.faces) == entry["triangles"]
        assert np.allclose(mesh.bounds, entry["bounds"], atol=0.001)
    expected_faces = sum(len(mesh.faces) for mesh in meshes.values())
    assert sum(entry["triangles"] for entry in entries) == expected_faces, "Every source triangle must survive export"
    groups = [dict(group) for group in GROUPS if group["id"] in {"cranial", "facial", "teeth"}]
    next(group for group in groups if group["id"] == "teeth")["description"] = "Upper and lower dental arches · original source grouping"
    manifest = {
        "title": "CT skull · OUVC 10503", "model": "assets/skull-ct/skull.glb", "sha256": digest(model),
        "units": "display units", "axes": {"x": "patient left", "y": "superior", "z": "anterior"},
        "source": {
            "name": "Ohio University · WitmerLab", "specimen": "OUVC 10503",
            "url": "https://people.ohio.edu/witmerl/3D_human.htm", "methods_url": "https://people.ohio.edu/witmerl/3D_human_methods.htm",
            "download_url": PDF_URL, "pdf_sha256": PDF_SHA256,
            "decoder_url": DECODER_URL, "decoder_sha256": DECODER_SHA256,
            "acquisition": "CT, 625 µm slice thickness, GE LightSpeed Ultra Multi-Slice, 120 kV, 200 mA",
            "license": "assets/skull-ct/ATTRIBUTION.txt",
            "scale_note": "The U3D unit-scale field is zero. Native coordinates are multiplied by 10 for display; viewer coordinates are not calibrated physical measurements.",
            "modifications": "Decoded every source triangle at full detail; separated the two disconnected inferior conchae without changing geometry; retained source dental-arch grouping; rigidly rotated, centered and uniformly scaled for display; computed smooth surface normals. No decimation, subdivision, geometric smoothing, synthetic pores or registration to another subject.",
        },
        "coverage": {"cranial": 8, "facial": 14, "dentition_meshes": 2}, "groups": groups, "structures": entries,
        "defaults": {"colors": False, "gloss": 10, "ambientOcclusion": True},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Exported and reloaded {len(entries)} structures, {expected_faces:,} original CT triangles, {model.stat().st_size / 1e6:.1f} MB", flush=True)
    from export_ct_surface import export as export_surface
    export_surface()


if __name__ == "__main__":
    export()
