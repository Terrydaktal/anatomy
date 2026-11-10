#!/usr/bin/env python3
"""Cache the published skull landmark geometry and its surrounding source bones.

Sources are retained under data/source, with hashes for reproducible offline
registration. This does not change the viewer or claim a redistribution licence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/source/mapped-regions"
BOOKMARK = "https://human.biodigital.com/search/bookmarks/data?be=4a8V"
VIEWER = "https://human.biodigital.com/viewer/?be=4a8V"
COMMIT = "b40226daf1906eddc5b75b3ec0476ecd8eae678c"
FOSSA_FILES = {
    "PPF and Skull.fbx": ("500cb18070bb84644948fe760f34c40d29d6dbabcd53f16a2bf4162486582be1", 71019676),
    "boundary.obj": ("2058141ce10ba4d1e557eb0bda91d7c53b19a97f3522855ba21171bea6ddbb36", 267824),
    "anterior wall.obj": ("88dbfd50ab9ee7eda2ae1318dfdbb2f2c9091c3781c377012215e194770a5f72", 94916),
    "medial wall.obj": ("a5f684e8785d5dd3ca5374e0c006bde7047299499d9f9ad9b5e6a25d800d08ed", 52463),
    "skull__visuals.fbx": ("80b11b75fcd4cc8b5de22dcbcc5bbe4922da98ad3e3edbbf578baf683f024586", 6160576),
    'PPF_Region_v4.fbx': ('7d68e44dcdcf92d554d26a9954dc3a4af98c09736339ef9b2b4df41e1c67ff50', 3810144),
    'PPF_Region_v5.fbx': ('4b0f596a7be194bf217ac7ff3ffc5f02fd34e5c318036b126be91bcdeaee3881', 5115648),
    'posterior.fbx': ('58b984a6d716c427c6f1c7aef48d40bc0e523385c86e5f1d1ca717cff0e01267', 29008),
    'medial.fbx': ('0fc6d3fb2d5c63dea01666a26fdb380287e94cd6c07681bd2d40305c190fb970', 30816),
}
FOSSA_TEXT = {
    'Assets/Models/skull__visuals.fbx.meta': ('skull__visuals.fbx.meta', '0472d2bde1901b2e8da565637fac1f9a0a765d1deea6b6d43f183424566ae34d'),
    'Assets/Prefabs/skull__visuals.prefab': ('skull__visuals.prefab', '9f7a72357841bf3883994f4a8a7ddb57a65ef3d78d71f750a8ec7e63fd8c0ade'),
    'Assets/Models/PPF_Region_v4.fbx.meta': ('PPF_Region_v4.fbx.meta', 'b78cede89de7efacf6a4dd823cda6d5e09990f21b42e29daa9f1ef1f0095537e'),
    'Assets/Models/PPF_Region_v5.fbx.meta': ('PPF_Region_v5.fbx.meta', '821658f73e33a0c2e09713f430fadff2938506a58715af609a0e1d4bf90ac5b4'),
    'Assets/Scenes/Main.unity': ('Main.unity', '8ef50da293faa1e1ff07c18b939b3136e0b80f4a7eb1e3e7c7183b608db9e5e5'),
    'Assets/Models/posterior.fbx.meta': ('posterior.fbx.meta', '89fd798370e0ce5c8f4a0fae4fdd3b91fc3d5e2502f4547d00f10e8b67485966'),
    'Assets/Models/medial.fbx.meta': ('medial.fbx.meta', '1bab3d3fb6cb2df60a8fd4bdeeb82271a2c681a1c871c3e559bb3d52a4bd7c72'),
}


def expected_snapshots() -> dict:
    pins = ROOT / 'assets/landmark-regions/registered-source-snapshot.json'
    if pins.exists():
        return {entry['path']: entry['sha256'] for entry in json.loads(pins.read_text())['files']}
    shipped = ROOT / 'assets/landmark-regions/registered-regions.json'
    return {entry['path']: entry['sha256'] for entry in json.loads(shipped.read_text()).get('source_files', [])} if shipped.exists() else {}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def flatten(nodes: list, parents: tuple = (), transforms: tuple = ()):
    for node in nodes:
        chain = (*transforms, node["transformId"]) if node.get("transformId") else transforms
        if node.get("geometryId") and "Skull" in parents:
            role = "region" if node["objectId"].startswith("bd_bony_landmarks_master-") else "bone"
            yield {"name": node["displayName"], "parents": list(parents), "role": role,
                   "object_id": node["objectId"], "geometry_id": node["geometryId"], "transforms": list(chain)}
        yield from flatten(node.get("objects", []), (*parents, node.get("displayName", "")), chain)


def asset_id(kind: str, identity: str) -> str:
    module, name = identity.split("/", 1)
    return f"{module}/{kind}/{name}"


def fetch_assets(identities: list[str]) -> dict:
    # The same public asset requests and binary format used by the published
    # viewer. No logged-in profile, account credentials, or access override.
    from playwright.sync_api import sync_playwright

    chrome = shutil.which("google-chrome-stable") or shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise RuntimeError("Chrome/Chromium and the dev dependency group are required for source retrieval")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page()
        try:
            page.goto(VIEWER, wait_until="domcontentloaded", timeout=30_000)
            return page.evaluate("""ids => new Promise((resolve, reject) => {
                const pending = new Set(ids), output = {}, socket = new WebSocket('wss://assets-human.biodigital.com/assets/ws3');
                socket.binaryType = 'arraybuffer';
                const timeout = setTimeout(() => { socket.close(); reject(new Error('Asset retrieval timed out: ' + pending.size)); }, 45000);
                const finish = () => { if (!pending.size) { clearTimeout(timeout); socket.close(); resolve(output); } };
                socket.onopen = () => socket.send(JSON.stringify({assets: ids}));
                socket.onmessage = event => {
                    if (typeof event.data === 'string') {
                        if (!event.data.startsWith('{')) return;
                        const value = JSON.parse(event.data);
                        if (!pending.delete(value.id)) return;
                        output[value.id] = value.error ? {error: value.error} : {asset: value.asset}; finish(); return;
                    }
                    const buffer = event.data, header = new Uint32Array(buffer, 0, 11);
                    const id = new TextDecoder().decode(new Uint8Array(buffer, header[0] === 17 ? 44 : header[3], header[1])).trim();
                    if (!pending.delete(id)) return;
                    const bytes = new Uint8Array(buffer); let binary = '';
                    for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
                    output[id] = {base64: btoa(binary)}; finish();
                };
                socket.onerror = () => { clearTimeout(timeout); socket.close(); reject(new Error('Public asset connection failed')); };
            })""", identities)
        finally:
            browser.close()


def verify(cache: Path, lock: dict) -> None:
    expected = expected_snapshots()
    for record in lock["files"]:
        raw = (cache / record["path"]).read_bytes()
        if len(raw) != record["bytes"] or sha(raw) != record["sha256"] or expected.get(record['path'], record['sha256']) != record['sha256']:
            raise ValueError(f"Source checksum changed: {record['path']}")


def cache_fossafinder(cache: Path, lock: dict) -> None:
    """Add newly supported pinned wall assets without refetching BioDigital."""
    expected_files = expected_snapshots()
    known = {entry['path']: entry for entry in lock['files']}
    requests = [(name, expected, length,
                 f'https://media.githubusercontent.com/media/ubcemergingmedialab/FossaFinder/{COMMIT}/Assets/Models/{urllib.parse.quote(name)}')
                for name, (expected, length) in FOSSA_FILES.items()]
    requests.extend((name, expected, None,
                     f'https://raw.githubusercontent.com/ubcemergingmedialab/FossaFinder/{COMMIT}/{urllib.parse.quote(source_path)}')
                    for source_path, (name, expected) in FOSSA_TEXT.items())
    for name, expected, length, url in requests:
        path = f'fossafinder/{name}'
        destination = cache / path
        content = destination.read_bytes() if destination.exists() else urllib.request.urlopen(url, timeout=30).read()
        if sha(content) != expected or (length is not None and len(content) != length) or expected_files.get(path, expected) != expected:
            raise ValueError(f'Pinned FossaFinder asset changed: {name}')
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        record = {'path': path, 'bytes': len(content), 'sha256': expected}
        if path not in known:
            lock['files'].append(record)
        elif known[path] != record:
            raise ValueError(f'FossaFinder cache record changed: {name}')


def download(cache: Path) -> dict:
    lock_path = cache / "sources.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        verify(cache, lock)
        cache_fossafinder(cache, lock)
        lock_path.write_text(json.dumps(lock, indent=2) + '\n')
        return lock
    cache.mkdir(parents=True, exist_ok=True)
    raw = urllib.request.urlopen(BOOKMARK, timeout=30).read()
    expected_files = expected_snapshots()
    if expected_files.get('biodigital/catalogue.json', sha(raw)) != sha(raw):
        raise ValueError('The public catalogue has changed from the shipped source snapshot')
    catalogue = json.loads(raw)["bookmark"]
    records = list(flatten(catalogue["sceneGraph"]))
    identities = sorted({asset_id("geometry", record["geometry_id"]) for record in records}
                        | {asset_id("transforms", transform) for record in records for transform in record["transforms"]})
    responses = fetch_assets(identities)
    lock = {"schema_version": 1, "biodigital": {"url": BOOKMARK, "terms_url": "https://www.biodigital.com/terms",
            "licence": "Proprietary BioDigital content; technical access is not a redistribution or private-app reuse licence.",
            "records": records, "assets": {}}, "files": [], "fossafinder": {"commit": COMMIT,
            "url": "https://github.com/ubcemergingmedialab/FossaFinder", "licence": "No explicit model reuse licence verified; retain source attribution and clarify permission before redistribution."}}

    def save(path: str, content: bytes) -> None:
        if expected_files.get(path, sha(content)) != sha(content):
            raise ValueError(f'Source differs from the shipped snapshot: {path}')
        destination = cache / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        lock["files"].append({"path": path, "bytes": len(content), "sha256": sha(content)})

    save("biodigital/catalogue.json", raw)
    for identity, response in responses.items():
        if "error" in response:
            lock["biodigital"]["assets"][identity] = response
            continue
        path = f"biodigital/{hashlib.sha256(identity.encode()).hexdigest()}.{'bda' if 'base64' in response else 'json'}"
        content = base64.b64decode(response["base64"]) if "base64" in response else json.dumps(response["asset"]).encode()
        save(path, content)
        lock["biodigital"]["assets"][identity] = {"path": path, "sha256": sha(content)}
    cache_fossafinder(cache, lock)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    return lock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--offline", action="store_true", help="Verify an existing cache without network requests")
    args = parser.parse_args()
    if args.offline:
        lock = json.loads((args.cache / "sources.json").read_text())
        verify(args.cache, lock)
    else:
        lock = download(args.cache)
    print(json.dumps({"cache": str(args.cache), "files": len(lock["files"]),
                      "source_region_objects": sum(r["role"] == "region" for r in lock["biodigital"]["records"]),
                      "unavailable_assets": [k for k, v in lock["biodigital"]["assets"].items() if "error" in v]}, indent=2))


if __name__ == "__main__":
    main()
