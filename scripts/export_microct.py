"""Export an un-smoothed real skull-vault fragment from the Halle micro-CT mask.

This is an independent study specimen. The source supplies only bone/non-bone,
not tissue labels for the cortical tables or diploe. The three educational pins
therefore annotate locations on one intact fragment; they do not divide tissue.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import zlib

import numpy as np
from skimage.measure import marching_cubes
import trimesh

from source_io import digest, download


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/source/halle"
OUTPUT = ROOT / "assets/bone-microct"
SOURCE_URL = "https://zenodo.org/api/records/6108435/files/halle_skull_segmented.nrrd/content"
SOURCE_MD5 = "7ae22945a353069bdb695b63fa2f20bd"
SOURCE_SHA256 = "7cbb67ff56c2682515f3dd681044a36231be8526b9101332f9b720dec9c185e8"
RAW_SHA256 = "46518b1b21d8ea1417661fa7ec90c150d01f68a25a513842e18e7cd3e14f028b"
SIZES_XYZ = (1150, 1700, 1400)
SPACING_MM = 0.125
# Fixed, visually inspected vault region, away from the preparation cut.
# Intervals are half-open source voxel indices; NumPy storage is z, y, x.
CROP_XYZ = ((650, 790), (760, 900), (1200, 1380))
IDENTITY = "skull-vault-fragment"


def source_volume() -> tuple[np.memmap, dict[str, str]]:
    SOURCE.mkdir(parents=True, exist_ok=True)
    source = SOURCE / "halle_skull_segmented.nrrd"
    if not source.exists():
        download(SOURCE_URL, source)
    with source.open("rb") as stream:
        md5 = hashlib.file_digest(stream, "md5").hexdigest()
    if md5 != SOURCE_MD5 or digest(source) != SOURCE_SHA256:
        raise RuntimeError("Halle segmentation differs from the pinned source")

    with source.open("rb") as stream:
        if stream.readline().strip() != b"NRRD0004":
            raise RuntimeError("Unexpected NRRD version")
        header = {}
        while (line := stream.readline().strip()):
            if not line.startswith(b"#"):
                key, value = line.decode("ascii").split(":", 1)
                header[key.strip()] = value.strip()
        offset = stream.tell()
    expected = {
        "type": "short", "dimension": "3", "sizes": "1150 1700 1400",
        "encoding": "gzip", "endian": "little",
        "space": "left-posterior-superior", "space origin": "(0,0,0)",
        "space directions": "(0.125,0,0) (0,0.125,0) (0,0,0.125)",
    }
    if any(header.get(key) != value for key, value in expected.items()):
        raise RuntimeError("NRRD layout changed; inspect its geometry before export")

    raw = SOURCE / "segmented.raw"
    expected_bytes = int(np.prod(SIZES_XYZ)) * 2
    if not raw.exists() or raw.stat().st_size != expected_bytes:
        partial = raw.with_suffix(".partial")
        decoder = zlib.decompressobj(wbits=31)
        with source.open("rb") as stream, partial.open("wb") as target:
            stream.seek(offset)
            while chunk := stream.read(1024 * 1024):
                target.write(decoder.decompress(chunk))
            target.write(decoder.flush())
        # The pinned NRRD has a newline after its gzip stream. Validate the
        # stream's CRC/EOF rather than asking gzip to parse another member.
        if not decoder.eof or decoder.unused_data != b"\n":
            raise RuntimeError("Unexpected compressed payload or trailing bytes")
        if partial.stat().st_size != expected_bytes:
            raise RuntimeError("The decoded NRRD has the wrong byte count")
        partial.replace(raw)
    if digest(raw) != RAW_SHA256:
        raise RuntimeError("Decoded voxel cache checksum differs from the source")
    return np.memmap(raw, dtype="<i2", mode="r", shape=SIZES_XYZ[::-1]), header


def export() -> None:
    volume, header = source_volume()
    (x0, x1), (y0, y1), (z0, z1) = CROP_XYZ
    crop = np.asarray(volume[z0:z1, y0:y1, x0:x1], dtype=np.uint8)
    if set(np.unique(crop)) != {0, 1}:
        raise RuntimeError("Expected a binary bone/non-bone segmentation")
    crop_sha256 = hashlib.sha256(crop.tobytes(order="C")).hexdigest()
    vertices_zyx, faces, _, _ = marching_cubes(
        np.pad(crop, 1), level=0.5, spacing=(SPACING_MM,) * 3,
        allow_degenerate=False, method="lewiner", step_size=1,
    )
    if len(faces) != 776546:
        raise RuntimeError(f"Unexpected source-isosurface triangle count: {len(faces)}")

    # Undo the padding, retain true voxel spacing, and orient the vault with
    # its outer table upward and one artificial crop face toward the viewer.
    # Source axis names are not used as measured patient laterality: the NRRD
    # header's claimed LPS orientation is inconsistent with visual skull pose.
    source_xyz_mm = vertices_zyx[:, ::-1] + np.array([x0 - 1, y0 - 1, z0 - 1]) * SPACING_MM
    rotation = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float)
    vertices = source_xyz_mm @ rotation.T
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    vertices -= center
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    if not np.isfinite(mesh.vertices).all() or not mesh.is_watertight:
        raise RuntimeError("Invalid cropped binary isosurface")
    _ = mesh.vertex_normals
    color = "#cbaa7c"
    mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
        name=IDENTITY, baseColorFactor=[*bytes.fromhex(color[1:]), 255],
        metallicFactor=0, roughnessFactor=0.9, doubleSided=True,
    ))
    mesh.metadata = {"catalogue_id": IDENTITY, "source": "Halle micro-CT bone mask", "tissue_labels": "binary bone/non-bone only"}
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name=IDENTITY, geom_name=IDENTITY)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    model = OUTPUT / "fragment.glb"
    model.write_bytes(scene.export(file_type="glb"))
    reloaded = trimesh.load(model, force="scene", process=False)
    if set(reloaded.geometry) != {IDENTITY}:
        raise RuntimeError("Serialized fragment identity changed")
    actual = reloaded.geometry[IDENTITY]
    if len(actual.faces) != len(faces) or not np.allclose(actual.bounds, mesh.bounds, atol=1e-5):
        raise RuntimeError("Serialized fragment geometry differs from the isosurface")

    landmarks = []
    for identity, name, source_z, description in [
        ("outer-cortical-table", "Outer cortical table", 1362,
         "Dense external plate of the skull vault. This pin identifies an illustrative location on the real binary bone surface; the source does not supply a separate cortical-table tissue label."),
        ("diploe", "Diploë", 1339,
         "Porous bone between the compact tables. The visible bony network comes directly from the micro-CT bone mask. This is an illustrative location, not a separately segmented diploë region."),
        ("inner-cortical-table", "Inner cortical table", 1316,
         "Dense plate facing the cranial cavity. This pin identifies an illustrative location on the real binary bone surface; the source does not supply a separate cortical-table tissue label."),
    ]:
        source_x, source_y = 710, 760
        if volume[source_z, source_y, source_x] != 1:
            raise RuntimeError(f"Layer pin no longer touches a source bone voxel: {identity}")
        point = np.array([source_x, source_y - 0.5, source_z]) * SPACING_MM @ rotation.T - center
        landmarks.append({
            "id": identity, "name": name, "position": point.round(6).tolist(),
            "normal": [0, 0, 1], "boneIds": [IDENTITY], "approximate": True,
            "category": "Bone microstructure", "description": description,
            "source_voxel_xyz": [source_x, source_y, source_z],
        })

    structure = {
        "id": IDENTITY, "name": "Skull-vault micro-CT fragment", "group": "microstructure",
        "kind": "bone", "side": "study sample", "color": color, "visible": True,
        "description": "A real 17.5 × 17.5 mm surface patch cropped from a separate donor skull's 125 µm micro-CT bone mask. It preserves the compact tables and porous interior in a single mesh. Rectangular cut faces are computational crop boundaries. Layer pins are educational locations, not supplied tissue labels. This fragment is not registered to either full-skull model.",
        "vertices": len(mesh.vertices), "triangles": len(mesh.faces),
        "bounds": mesh.bounds.round(6).tolist(), "center": mesh.bounds.mean(axis=0).round(6).tolist(),
    }
    manifest = {
        "title": "Bone microstructure · Halle micro-CT", "model": "assets/bone-microct/fragment.glb",
        "sha256": digest(model), "units": "millimetres",
        "axes": {"x": "source voxel x", "y": "toward the outer table (source voxel z)", "z": "toward the exposed crop face (negative source voxel y)"},
        "source": {
            "name": "Thomas Kirchner et al. · Halle micro-CT",
            "specimen": "Independent 70-year-old male donor skull, Martin-Luther University Halle-Wittenberg",
            "url": "https://zenodo.org/records/6108435", "doi": "10.5281/zenodo.6108435",
            "methods_url": "https://arxiv.org/html/2202.11519v1", "download_url": SOURCE_URL,
            "license": "assets/bone-microct/ATTRIBUTION.txt", "license_id": "CC-BY-4.0",
            "nrrd_md5": SOURCE_MD5, "nrrd_sha256": SOURCE_SHA256, "decoded_sha256": RAW_SHA256,
            "acquisition": "125 µm isotropic cone-beam micro-CT; Nikon XT H 225, 150 kV, 400 µA; semi-manual binary bone segmentation",
            "nrrd_header": header, "crop_voxel_xyz_half_open": CROP_XYZ,
            "crop_array_order": "zyx", "crop_uint8_sha256": crop_sha256,
            "crop_size_mm_xyz": [(hi - lo) * SPACING_MM for lo, hi in CROP_XYZ],
            "voxel_spacing_mm": [SPACING_MM] * 3,
            "source_xyz_to_display_rotation": rotation.tolist(), "display_center_before_translation_mm": center.round(6).tolist(),
            "extraction": {"algorithm": "Lewiner marching cubes", "scikit_image_version": importlib.metadata.version("scikit-image"), "level": 0.5, "step_size": 1, "padding_voxels": 1, "allow_degenerate": False},
            "modifications": "Cropped the pinned binary mask at the recorded voxel bounds; zero-padded it by one voxel to expose rectangular cut faces; extracted every marching-cubes triangle at native 0.125 mm spacing; rigidly reoriented and centered; computed smooth vertex normals. No mesh decimation, geometric smoothing, generated trabeculae, assigned tissue segmentation or registration to another skull.",
            "limitations": "The source is bone/non-bone only. Compact tables and diploë are visible but are not separately supplied tissue labels. Interpretive layer pins are approximate locations. The donor skull has a preparation cut and a small right-parietal trepanation elsewhere; the crop avoids the visible cut. Header LPS labels do not establish patient orientation of the specimen. This is a separate study sample, not an enlargement or measured thickness map of OUVC 10503.",
        },
        "coverage": {"microstructure_fragments": 1, "layer_markers": 3},
        "groups": [{"id": "microstructure", "name": "Bone microstructure", "description": "One real vault fragment · three interpretive layer locations"}],
        "structures": [structure], "landmarks": landmarks,
        "defaults": {"colors": False, "gloss": 5, "ambientOcclusion": True},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (OUTPUT / "ATTRIBUTION.txt").write_text(
        "A micro-CT of a human skull (2022), Thomas Kirchner and contributors.\n"
        "Dataset: https://doi.org/10.5281/zenodo.6108435\n"
        "Descriptor: https://arxiv.org/abs/2202.11519\n"
        "Institutions: Martin-Luther University Halle-Wittenberg; Helmholtz Centre for Environmental Research UFZ.\n"
        "Contributors include John Maximilian Köhne, Hans-Jörg Vogel, Jan Laufer and Heike Kielstein.\n"
        "Licence: Creative Commons Attribution 4.0 International (CC BY 4.0).\n"
        "https://creativecommons.org/licenses/by/4.0/\n\n"
        "Adaptation: an approximately 17.5 x 17.5 mm skull-vault surface patch from the published binary\n"
        "bone mask. Native-spacing marching cubes, rectangular computational crop faces, rigid\n"
        "orientation/centering, display colour and smooth vertex normals; no decimation or geometric smoothing.\n"
        "All porosity comes from the source segmentation; no artificial trabeculae were generated.\n"
        "Three interpretive educational pins mark approximate cortical-table/diploe locations;\n"
        "the source contains no separate tissue labels for those layers.\n"
        "This independent donor fragment is not registered to the full-skull models.\n"
        "Exact source checksums, voxel crop, transform and extraction settings are in manifest.json.\n"
    )
    print(f"Exported and reloaded {len(mesh.faces):,} native-spacing triangles, {model.stat().st_size / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    export()
