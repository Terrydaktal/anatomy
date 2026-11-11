#!/usr/bin/env python3
"""Verify real bone-study assets and their visible browser interactions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct

from playwright.sync_api import sync_playwright

from test_viewer import ARTIFACTS, ROOT, anatomy_bbox, by_id, capture, local_server, settle, snapshot, visible


def manifest_asset(path: str) -> dict:
    manifest = json.loads((ROOT / path).read_text())
    raw = (ROOT / manifest["model"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest["sha256"]
    length = struct.unpack_from("<I", raw, 12)[0]
    gltf = json.loads(raw[20:20 + length])
    meshes = {mesh["name"]: mesh for mesh in gltf["meshes"]}
    assert set(meshes) == {entry["id"] for entry in manifest["structures"]}
    for entry in manifest["structures"]:
        primitive = meshes[entry["id"]]["primitives"][0]
        assert gltf["accessors"][primitive["indices"]]["count"] // 3 == entry["triangles"]
    return manifest


def asset_checks() -> dict:
    cavities = manifest_asset("assets/skull-cavities/manifest.json")
    assert len(cavities["structures"]) == 6
    assert sum(entry["triangles"] for entry in cavities["structures"]) == 189_964
    skull = json.loads((ROOT / "assets/skull-ct/manifest.json").read_text())
    assert cavities["source"]["pdf_sha256"] == skull["source"]["pdf_sha256"]
    assert all(not entry["visible"] for entry in cavities["structures"])
    microct = manifest_asset("assets/bone-microct/manifest.json")
    assert microct["structures"][0]["triangles"] == 776_546
    assert microct["source"]["voxel_spacing_mm"] == [0.125] * 3
    assert microct["source"]["license_id"] == "CC-BY-4.0"
    assert {entry["id"] for entry in microct["landmarks"]} == {"outer-cortical-table", "inner-cortical-table", "diploe"}
    catalogue = json.loads((ROOT / "assets/landmarks/catalogue.json").read_text())
    assert len(catalogue["landmarks"]) > 150
    identities = [entry["id"] for entry in catalogue["landmarks"]]
    assert len(set(identities)) == len(identities)
    reference = json.loads((ROOT / "assets/skull/manifest.json").read_text())
    owner_ids = {entry["id"] for entry in reference["structures"]}
    category_ids = {entry["id"] for entry in catalogue["categories"]}
    for entry in catalogue["landmarks"]:
        assert entry["owners"] and set(entry["owners"]) <= owner_ids
        assert entry["category"] in category_ids and entry["description"]
        for placement in entry["positions"].values():
            if placement is not None:
                if placement.get('location_status') == 'unlocated':
                    assert placement['point'] is None and placement['label_fit']['status'] == 'unlocated'
                else:
                    assert len(placement["point"]) == 3 and all(math.isfinite(value) for value in placement["point"])
    return catalogue


def load(page, url: str, suffix: str = "") -> None:
    page.goto(url + "/" + suffix, wait_until="networkidle")
    page.wait_for_function("document.documentElement.dataset.ready === 'true'", timeout=60000)
    settle(page)


def microct_checks(page, url: str, checks: list) -> None:
    load(page, url, "?dataset=microct")
    state = snapshot(page)
    assert state["dataset"] == "microct" and len(state["structures"]) == 1
    assert state["structures"][0]["triangles"] == 776_546
    assert not state["structures"][0]["useColor"]
    assert state["anatomy"]["landmarkCount"] == 3
    assert not state["anatomy"]["markers"] and state["anatomy"]["includeEstimates"]
    assert page.locator(".landmark-row:not([hidden])").count() == 3
    page.locator("#landmark-estimates").check()
    page.locator("#landmark-pins").check()
    assert len(snapshot(page)["anatomy"]["markers"]) == 3
    assert page.locator("#bone-catalogue").is_visible() and page.locator("#bone-studies").is_visible()
    assert page.locator("#jaw").is_disabled() and page.locator("#explode").is_disabled()
    assert anatomy_bbox(page)
    page.locator("#landmark-labels").check()
    capture(page, "bone-microct-layers")
    for name in ("Outer cortical table", "Diploë", "Inner cortical table"):
        page.locator('#landmark-search').fill(name)
        row = page.locator(".landmark-row").filter(has=page.get_by_role("button", name=name, exact=True))
        row.get_by_role("button", name=name, exact=True).click()
        assert page.locator("#anatomy-name").inner_text() == name
        page.locator("#anatomy-focus").click()
        assert anatomy_bbox(page)
        page.locator("#anatomy-hide").click()
    assert not snapshot(page)["anatomy"]["markers"]
    page.locator("#landmark-pins").check()
    page.locator('#landmark-search').fill('')
    page.locator("#bone-catalogue").scroll_into_view_if_needed()
    page.locator("#hide-all").click()
    settle(page)
    assert not visible(page) and not any(entry["visible"] for entry in snapshot(page)["anatomy"]["landmarks"])
    page.locator("#show-all").click()
    page.locator("#reset").click()
    page.locator(".more-controls > summary").click()
    page.locator("#section").select_option("sagittal")
    page.locator("#section-position").fill("100")
    assert anatomy_bbox(page) is None
    page.locator("#section").select_option("none")
    assert anatomy_bbox(page)
    checks.append("Real 125 µm micro-CT fragment, all three layer pins, focus/hide, bone visibility and section extremes")


def landmark_checks(page, url: str, dataset: str, checks: list) -> None:
    load(page, url, f"?dataset={dataset}")
    initial = snapshot(page)
    assert initial["anatomy"]["landmarkCount"] > 150
    assert not initial["anatomy"]["markers"]
    assert all(not entry["useColor"] for entry in initial["structures"] if entry["group"] != "teeth")
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    assert page.locator("#landmark-panel").get_attribute("open") is not None
    page.locator("#landmark-estimates").check()
    categories = page.locator('.landmark-level-1').all()
    for category in categories:
        category.locator(':scope > summary').click()
        owner = category.locator('.landmark-level-2').first
        owner.locator(':scope > summary').click()
        assert owner.locator('.landmark-row:visible').count() > 0
        category.locator(':scope > summary').click()
    page.locator("#landmark-search").fill("infraorbital")
    rows = page.locator(".landmark-row:visible")
    assert rows.count() >= 2
    button = rows.first.locator(".landmark-name")
    identity = rows.first.get_attribute("data-landmark")
    button.click()
    assert snapshot(page)["anatomy"]["selected"] == {"id": identity, "kind": "landmark"}
    assert identity in snapshot(page)["anatomy"]["markers"]
    page.locator("#anatomy-focus").click()
    settle(page)
    region = next(entry for entry in snapshot(page)["anatomy"]["regions"] if entry["id"] == identity)
    if dataset == 'ct':
        assert region["rendered"] and region["triangles"] > 0
    else:
        package = ROOT / 'assets/landmark-regions/registered-regions.json'
        mapped = json.loads(package.read_text()) if package.exists() else {'regions': [], 'rejected': []}
        accepted = any(r['dataset'] == dataset and r['feature_id'] == identity for r in mapped['regions'])
        if accepted:
            assert region['rendered'] and region['triangles'] > 0
        else:
            assert not region['rendered'] and region['triangles'] == 0, 'An unsupported reference label must remain unpainted'
            assert any(text in page.locator('#anatomy-placement').inner_text() for text in ('Location guide only', 'Source transfer withheld'))
    assert not page.locator(f'.anatomy-pin[data-landmark="{identity}"]').is_visible()
    capture(page, f"{dataset}-landmark-infraorbital")
    page.locator("#anatomy-isolate").click()
    owners = next(entry["owners"] for entry in snapshot(page)["anatomy"]["landmarks"] if entry["id"] == identity)
    assert visible(page) == set(owners)
    page.locator("#anatomy-restore").click()
    assert visible(page) == {entry["id"] for entry in initial["structures"] if entry["visible"]}
    page.locator("#anatomy-hide").click()
    assert identity not in snapshot(page)["anatomy"]["markers"]
    page.locator("#landmark-search").fill("unmatchable-landmark")
    assert page.locator(".landmark-row:visible").count() == 0
    page.locator("#landmark-search").fill("suture")
    assert page.locator(".landmark-row:visible").count() >= 4
    page.locator("#landmark-pins").check()
    assert len(snapshot(page)["anatomy"]["markers"]) >= 4
    page.locator("#landmark-labels").check()
    capture(page, f"{dataset}-suture-markers")
    page.locator("#reset").click()
    assert not snapshot(page)["anatomy"]["markers"]
    assert snapshot(page)["anatomy"]["selected"] is None
    checks.append(f"{dataset}: every landmark category, search, surface highlight and focus without pins, owner isolation/restoration, sutures and neutral reset")


def cavity_checks(page, url: str, checks: list) -> None:
    load(page, url)
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator("#cavity-panel > summary").click()
    initial = snapshot(page)
    assert len(initial["anatomy"]["cavities"]) == 6
    assert not any(entry["visible"] for entry in initial["anatomy"]["cavities"])
    expected = {entry["id"]: entry for entry in json.loads((ROOT / "assets/skull-cavities/manifest.json").read_text())["structures"]}
    for entry in initial["anatomy"]["cavities"]:
        assert entry["triangles"] == expected[entry["id"]]["triangles"]
        row = page.locator(f'.cavity-row[data-cavity="{entry["id"]}"]')
        row.locator('input[type="checkbox"]').check()
        settle(page)
        current = snapshot(page)
        assert next(cavity["rendered"] for cavity in current["anatomy"]["cavities"] if cavity["id"] == entry["id"])
        assert all(abs(bone["opacity"] - 0.18) < 1e-8 for bone in current["structures"])
        assert not current["depthShading"]
        row.locator("button").click()
        page.locator("#anatomy-isolate").click()
        assert not visible(page)
        assert anatomy_bbox(page), f"The actual {entry['id']} surface must be visible alone"
        capture(page, f"cavity-{entry['id']}")
        page.locator("#fit").click()
        page.locator("#anatomy-restore").click()
        assert len(visible(page)) == 31
        row.locator('input[type="checkbox"]').uncheck()
        assert all(abs(bone["opacity"] - 1) < 1e-8 for bone in snapshot(page)["structures"])
    page.locator("#reset").click()
    assert not any(entry["visible"] for entry in snapshot(page)["anatomy"]["cavities"])
    assert snapshot(page)["depthShading"]
    checks.append("All six original CT cavity surfaces, exact triangles, independent toggles, faded context, cavity-only framing and restored opacity")


def related_bone_checks(page, url: str, checks: list) -> None:
    for study, ids in [("ossicles", {f"{name}-{side}" for name in ("malleus", "incus", "stapes") for side in ("left", "right")}), ("hyoid", {"hyoid-bone"})]:
        load(page, url, f"?dataset=reference&study={study}")
        assert ids <= visible(page) and len(visible(page)) == 31
        assert anatomy_bbox(page)
        capture(page, f"related-bones-{study}")
        for identity in ids:
            row = page.locator(f'.bone-row[data-id="{identity}"]')
            row.locator('input[type="checkbox"]').uncheck()
            assert not by_id(page)[identity]["rendered"]
            row.locator('input[type="checkbox"]').check()
            row.locator(".bone-color-toggle").click()
            assert by_id(page)[identity]["useColor"]
        page.locator("#reset").click()
        assert len(visible(page)) == 31
    checks.append("Legacy ear/hyoid links open the CT skull with supplementary bones at estimated placements, independent visibility/colour and reset")


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {"result": "failed", "checks": []}
    try:
        asset_checks()
        report["checks"].append("Landmark identities/ownership/positions, cavity source identity, all GLB checksums and actual exported triangle counts")
        chrome = os.environ.get("ANATOMY_CHROME_PATH") or shutil.which("google-chrome-stable") or shutil.which("google-chrome") or shutil.which("chromium")
        with local_server() as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=chrome, headless=True,
                env={key: value for key, value in os.environ.items() if key not in {"DISPLAY", "WAYLAND_DISPLAY"}},
                args=["--disable-extensions", "--enable-webgl", "--ignore-gpu-blocklist", "--disable-software-rasterizer", "--enable-features=Vulkan", "--use-angle=vulkan"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            errors, external = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url + "/") or route.request.url.startswith("data:") else (external.append(route.request.url), route.abort()))
            try:
                microct_checks(page, url, report["checks"])
                for dataset in ("ct",):
                    landmark_checks(page, url, dataset, report["checks"])
                cavity_checks(page, url, report["checks"])
                related_bone_checks(page, url, report["checks"])
                load(page, url)
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#landmark-panel").evaluate("e=>e.open=true")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                capture(page, "anatomy-studies-mobile")
                assert not external and not errors, {"external": external, "errors": errors}
                report["checks"].append("Mobile study controls, offline assets and zero browser errors across all studies")
            finally:
                browser.close()
        report["result"] = "passed"
    finally:
        (ARTIFACTS / "anatomy-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
