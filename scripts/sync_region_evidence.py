#!/usr/bin/env python3
"""Download pinned terminology and audit which skull features have supplied boundaries.

Terminology matches, authored face selections and geometric fits are distinct
evidence. Neither a name match nor a successful mesh check establishes a
percentage of anatomical accuracy. No new anatomical masks are invented here.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import unicodedata
import urllib.request

from source_io import digest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "ca4ad71d24d05a6e81f8aeaadb96165c20dffc75"
TERMINOLOGY_URL = f"https://raw.githubusercontent.com/Z-Anatomy/Models-of-human-anatomy/{SOURCE_COMMIT}/TA2.csv"
TERMINOLOGY_SHA256 = "0f9092a328b27dcd15d696d9f9a4087deb229a1aad21b75876657622de835974"
CACHE = ROOT / "data/source/region-evidence/TA2.csv"
OUTPUT = ROOT / "assets/landmark-regions/evidence.json"
MESH_INDEX_URL = "https://lifesciencedb.jp/bp3d/get-info.cgi?cmd=upload-all-list&load=1&md_abbr=bp3d&title=obj2FMA&tree=isa&version=4.3"
MESH_INDEX_SHA256 = "cab10eda6338a6935d0216810eaba78dd049877df496323a0a4916d290dd8e3a"
MESH_INDEX_CACHE = ROOT / "data/source/region-evidence/bp3d43-mesh-index"


def terminology_bytes(cache: Path = CACHE, offline: bool = False) -> bytes:
    return pinned_bytes(TERMINOLOGY_URL, cache, TERMINOLOGY_SHA256, 5_000_000, offline)


def model_index_bytes(offline: bool = False) -> bytes:
    return pinned_bytes(MESH_INDEX_URL, MESH_INDEX_CACHE, MESH_INDEX_SHA256, 8_000_000, offline)


def pinned_bytes(url: str, cache: Path, sha256: str, maximum: int, offline: bool) -> bytes:
    if not cache.exists():
        if offline:
            raise ValueError(f"Pinned source cache is missing: {cache.name}; run once without --offline")
        request = urllib.request.Request(url, headers={"User-Agent": "AnatomyRegionSources/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(maximum + 1)
        if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != sha256:
            raise ValueError(f"Downloaded source differs from the pinned snapshot: {cache.name}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    raw = cache.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError(f"Source cache checksum differs from the pinned snapshot: {cache.name}")
    return raw


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"^(left|right)\s+", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def terminology_index(raw: bytes) -> dict:
    index = defaultdict(list)
    for outer in csv.reader(io.StringIO(raw.decode("utf-8-sig"))):
        if not outer:
            continue
        # The upstream CSV quotes an entire semicolon-delimited row.
        row = next(csv.reader([outer[0]], delimiter=";"))
        if len(row) < 3 or not row[0].isdigit() or not row[1]:
            continue
        index[normalize(row[1])].append({"ta2_id": row[0], "english": row[1], "latin": row[2]})
    return dict(index)


def terminology_matches(entry: dict, index: dict) -> list:
    matches = {}
    for name in [entry["name"], *entry.get("aliases", [])]:
        for item in index.get(normalize(name), []):
            matches[item["ta2_id"]] = item
    return sorted(matches.values(), key=lambda item: int(item["ta2_id"]))


class MeshTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.field, self.parts = [], {}, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = {}
        if tag == "td":
            self.field, self.parts = dict(attrs).get("class"), []

    def handle_data(self, data):
        if self.field:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.field:
            self.row[self.field] = "".join(self.parts).strip()
            self.field = None
        if tag == "tr" and self.row.get("art_id", "").startswith("FJ") and self.row.get("cdi_name_e"):
            self.rows.append(self.row)


def external_mesh_index(raw: bytes) -> tuple[dict, int]:
    table = MeshTable()
    table.feed(raw.decode("utf-8-sig"))
    index = defaultdict(list)
    for row in table.rows:
        candidate = {"fma_id": row["cdi_name"], "name": row["cdi_name_e"], "mesh_id": row["art_id"], "representation_id": row["rep_id"],
                     "status": "unregistered-candidate", "note": "Label match to another atlas. Scope, geometry and specimen registration have not been verified; excluded from supplied boundary coverage."}
        for name in [row["cdi_name_e"], *row.get("cdi_syn_e", "").split(";")]:
            if name:
                index[normalize(name)].append(candidate)
    return dict(index), len(table.rows)


def external_candidates(entry: dict, terms: list, index: dict) -> list:
    candidates = {}
    names = [entry["name"], *entry.get("aliases", []), *(term["latin"] for term in terms)]
    for name in names:
        for candidate in index.get(normalize(name), []):
            candidates[(candidate["mesh_id"], candidate["fma_id"])] = candidate
    return sorted(candidates.values(), key=lambda item: (item["fma_id"], item["mesh_id"]))


def checked_json(path: Path) -> dict:
    return json.loads(path.read_text())


def check_binary(root: Path, manifest: dict) -> None:
    record = manifest["binary"]
    path = root / record["url"]
    if path.stat().st_size != record["byte_length"] or digest(path) != record["sha256"]:
        raise ValueError(f"Source boundary binary checksum differs: {record['url']}")


def evidence_for(entry: dict, dataset: str, authored: dict, apertures: dict, registered: dict | None = None,
                 rejections: dict | None = None) -> dict:
    placement = entry.get("positions", {}).get(dataset)
    if not placement:
        return {"available": False, "kind": "unavailable", "supplied_boundary": False}
    common = {"available": True, "placement_method": placement["method"], "supplied_boundary": False,
              "label_placement": placement.get('label_fit'),
              "independently_validated": False, "anatomical_accuracy_percent": None}
    if placement.get('location_status') == 'unlocated':
        return {**common, 'kind': 'unlocated', 'scope': 'no-location-no-painted-region',
                'note': 'Named anatomical entry only. No opening is located on this specimen; no spatial target, pin or painted region is supplied.'}
    domains = authored.get(entry["id"], []) if dataset == "reference" else []
    if domains:
        return {**common, "kind": "source-face-selection", "supplied_boundary": True,
                "scope": sorted({domain["mapping_scope"] for domain in domains}),
                "domain_ids": [domain["id"] for domain in domains],
                "note": "Preserves original author-selected faces. Named-feature association is inferred from material and location; this is not independent anatomical validation."}
    if placement.get('native_point_fit'):
        return {**common, 'kind': 'native-geometry-fit', 'method': placement['method'],
                'scope': 'point-landmark-visualization',
                'note': 'One point fitted to a native bone junction or sagittal contour extremum. A compact visual cue has no anatomical area boundary. Location remains estimated.'}
    fit = apertures.get((dataset, entry["id"]))
    if fit:
        return {**common, "kind": "native-geometry-fit", "method": fit["method"],
                "note": "Opening rim fitted to native depth geometry. The source supplies no named anatomical boundary for this feature."}
    transferred = (registered or {}).get((dataset, entry['id']), [])
    if entry.get('category') == 'landmarks':
        return {**common, 'kind': 'location-guide', 'scope': 'no-painted-region',
                'note': 'Unfitted label location only. A mapped source area does not establish a craniometric point; no surface cue is fabricated.'}
    if transferred:
        mapped_owners = {part['owner'] for part in transferred}
        return {**common, 'kind': 'registered-source-region', 'method': 'registered-author-surface',
                'source_ids': sorted({part['source_id'] for part in transferred}),
                'owners_mapped': sorted({part['owner'] for part in transferred}),
                'missing_owners': sorted(set(entry['owners']) - mapped_owners),
                'partial_owner_coverage': bool(set(entry['owners']) - mapped_owners),
                'scope': 'cross-specimen-source-region',
                'surface_orientation_checked': all(part.get('fit', {}).get('surface_correspondence_version') == 2 or part['source_id'] == 'fossafinder' for part in transferred),
                'cranial_cortical_exposure_checked': all(part.get('fit', {}).get('surface_side_check', {}).get('source_compartment') not in {'inner', 'outer'}
                    or bool(part['fit']['surface_side_check'].get('cavity_exposure_check')) for part in transferred),
                'note': 'Published region geometry fitted to a different skull with surface-orientation checks. Remains an estimate; excluded from supplied-boundary coverage.'}
    if entry["category"] == "sutures" and len(entry["owners"]) > 1:
        return {**common, "kind": "contact-fit",
                "note": "Band inferred from neighbouring bone surfaces; association with this named suture is estimated."}
    failures = (rejections or {}).get((dataset, entry['id']), [])
    if failures:
        return {**common, 'kind': 'withheld-source-transfer', 'scope': 'no-painted-region',
                'reasons': sorted({item['reason'] for item in failures}),
                'note': 'Source transfer failed geometric fit or footprint coverage checks. No generic area substitutes for it.'}
    return {**common, "kind": "location-guide", "scope": "no-painted-region",
            "note": "Approximate label location only. No supplied or fitted boundary; no generic anatomical area is painted."}


def build_report(root: Path, raw: bytes, mesh_inventory: bytes = b"") -> dict:
    catalogue = checked_json(root / "assets/landmarks/catalogue.json")
    source = checked_json(root / "assets/landmark-regions/source-regions.json")
    apertures = checked_json(root / "assets/landmark-regions/apertures.json")
    registered_path = root / 'assets/landmark-regions/registered-regions.json'
    registered = checked_json(registered_path) if registered_path.exists() else None
    transfers = defaultdict(list)
    rejected = defaultdict(list)
    if registered:
        check_binary(root, registered)
        for part in registered['regions']:
            if not part['approximate'] or part['independently_validated'] or part['anatomical_accuracy_percent'] is not None:
                raise ValueError('A registered region was incorrectly promoted to anatomical ground truth')
            transfers[(part['dataset'], part['feature_id'])].append(part)
        for item in registered['rejected']:
            if item.get('feature_id'):
                rejected[(item['dataset'], item['feature_id'])].append(item)
    check_binary(root, source)
    check_binary(root, apertures)
    models = {dataset: checked_json(root / f"assets/{directory}/manifest.json")
              for dataset, directory in (("reference", "skull"), ("ct", "skull-ct"))}
    for dataset, model in models.items():
        if digest(root / model["model"]) != model["sha256"]:
            raise ValueError(f"{dataset} model differs from its source manifest")
        if apertures["models"][dataset]["sha256"] != model["sha256"]:
            raise ValueError(f"Aperture fits belong to a different {dataset} specimen")
        if registered and registered['models'][dataset]['sha256'] != model['sha256']:
            raise ValueError(f'Registered regions belong to a different {dataset} specimen')
        for entry in catalogue['landmarks']:
            point_fit = entry.get('positions', {}).get(dataset, {}).get('native_point_fit')
            if point_fit and point_fit['source_model_sha256'] != model['sha256']:
                raise ValueError(f'Point fit belongs to a different {dataset} specimen')
    for parts in transfers.values():
        for part in parts:
            if part['source_model_sha256'] != models[part['dataset']]['sha256']:
                raise ValueError('A registered region has a different target-model identity')
    if source["dataset"] != "reference" or source["source"]["model_sha256"] != models["reference"]["sha256"]:
        raise ValueError("Authored face selections belong to a different reference model")
    authored = defaultdict(list)
    for domain in source["regions"]:
        for identity in domain["feature_ids"]:
            authored[identity].append(domain)
    fitted = {(record["dataset"], record["feature_id"]): record for record in apertures["regions"]}
    for record in apertures["regions"]:
        if record["source_model_sha256"] != models[record["dataset"]]["sha256"]:
            raise ValueError("A fitted region has a different source-model identity")
    index = terminology_index(raw)
    external, external_count = external_mesh_index(mesh_inventory)
    records = [{"id": entry["id"], "name": entry["name"], "category": entry["category"],
                "owners": entry["owners"], "terminology": terminology_matches(entry, index),
                "datasets": {dataset: evidence_for(entry, dataset, authored, fitted, transfers, rejected) for dataset in models}}
               for entry in catalogue["landmarks"]]
    for entry, record in zip(catalogue["landmarks"], records):
        record["external_mesh_candidates"] = external_candidates(entry, record["terminology"], external)
    summaries = {}
    for dataset, model in models.items():
        available = [entry for entry in records if entry["datasets"][dataset]["available"]]
        counts = Counter(entry["datasets"][dataset]["kind"] for entry in available)
        supplied = sum(entry["datasets"][dataset]["supplied_boundary"] for entry in available)
        summaries[dataset] = {"model": model["model"], "model_sha256": model["sha256"],
                              "catalogued_features": len(available), "supplied_boundaries": supplied,
                              "estimates": len(available) - supplied, "evidence_counts": dict(counts),
                              "terminology_matched_features": sum(bool(entry["terminology"]) for entry in available),
                              "complete_source_boundary_coverage": supplied == len(available)}
    return {"schema_version": 1, "title": "Automatic skull-region source coverage",
            "anatomical_accuracy_percent": None,
            "validation_note": "Source coverage and file integrity are measured here. No independent per-feature anatomical ground truth is supplied; no 99% accuracy claim can be derived from these checks.",
            "terminology_source": {"url": TERMINOLOGY_URL, "commit": SOURCE_COMMIT, "sha256": TERMINOLOGY_SHA256,
                                   "role": "Names and identifiers only; never a source of 3D region boundaries."},
            "external_mesh_inventory": {"url": MESH_INDEX_URL, "version": "4.3", "sha256": MESH_INDEX_SHA256,
                                        "rows": external_count, "matched_catalogue_features": sum(bool(record["external_mesh_candidates"]) for record in records),
                                        "role": "Exact name/alias candidates only. A label in another atlas does not supply a boundary on either viewer specimen."},
            "boundary_sources": [{"url": source["source"]["url"], "dataset": "reference", "model_sha256": source["source"]["model_sha256"],
                                  "binary_sha256": source["binary"]["sha256"], "role": "Source-selected material face domains; named-feature associations are inferred."},
                                 {"url": models["ct"]["source"]["url"], "dataset": "ct", "binary_sha256": apertures["binary"]["sha256"],
                                  "role": "Native opening geometry used for inferred rim fits; not supplied anatomical labels."}]
                                 + ([{'url': item['url'], 'binary_sha256': registered['binary']['sha256'],
                                      'role': 'Published regions transferred across specimens; estimated fit, not supplied boundaries on this skull.'}
                                     for item in registered['sources']] if registered else []),
            "models": summaries, "regions": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Use the checksum-verified local source snapshots")
    parser.add_argument("--require-source-boundaries", action="store_true", help="Exit 2 if any available feature lacks supplied boundaries")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    try:
        report = build_report(ROOT, terminology_bytes(offline=args.offline), model_index_bytes(offline=args.offline))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Region source audit failed: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.output), "models": report["models"], "anatomical_accuracy_percent": None}, indent=2))
    if args.require_source_boundaries and not all(model["complete_source_boundary_coverage"] for model in report["models"].values()):
        parser.exit(2, "Incomplete source boundaries. Terminology matches and estimated shapes do not satisfy this requirement.\n")


if __name__ == "__main__":
    main()
