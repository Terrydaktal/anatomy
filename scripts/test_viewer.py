#!/usr/bin/env python3
"""Verify shipped skull coverage and real browser interactions; retain QA screenshots."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from functools import partial
import hashlib
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import urllib.error
import urllib.request

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

from serve_viewer import ROOT, ViewerHandler


ARTIFACTS = ROOT / "artifacts/verification"
SKULL_IDS = {
    "frontal-bone", "parietal-bone-left", "parietal-bone-right",
    "temporal-bone-left", "temporal-bone-right", "occipital-bone",
    "sphenoid-bone", "ethmoid-bone", "maxilla-left", "maxilla-right",
    "zygomatic-bone-left", "zygomatic-bone-right", "nasal-bone-left", "nasal-bone-right",
    "lacrimal-bone-left", "lacrimal-bone-right", "palatine-bone-left", "palatine-bone-right",
    "inferior-nasal-concha-left", "inferior-nasal-concha-right", "vomer", "mandible",
}
OSSICLE_IDS = {f"{bone}-{side}" for bone in ("malleus", "incus", "stapes") for side in ("left", "right")}


class QuietHandler(ViewerHandler):
    def log_message(self, *_args) -> None:
        pass


@contextmanager
def local_server(handler=QuietHandler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def asset_checks() -> dict:
    manifest = json.loads((ROOT / "assets/skull/manifest.json").read_text())
    structures = manifest["structures"]
    assert len(structures) == 57
    assert {entry["id"] for entry in structures if entry["group"] in {"cranial", "facial"}} == SKULL_IDS
    assert {entry["id"] for entry in structures if entry["group"] == "ossicles"} == OSSICLE_IDS
    assert Counter(entry["group"] for entry in structures) == {"cranial": 8, "facial": 14, "ossicles": 6, "teeth": 28, "associated": 1}
    assert all(entry["visible"] is True for entry in structures)
    bones = [entry for entry in structures if entry["kind"] == "bone"]
    assert len({entry["color"] for entry in bones}) == len(bones), "Every bone, including left/right partners, needs its own colour"
    model = ROOT / manifest["model"]
    data = model.read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest["sha256"]
    magic, version, length, json_length, chunk_type = struct.unpack_from("<5I", data)
    assert (magic, version, length, chunk_type) == (0x46546C67, 2, len(data), 0x4E4F534A)
    gltf = json.loads(data[20:20 + json_length])
    assert {mesh["name"] for mesh in gltf["meshes"]} == {entry["id"] for entry in structures}
    for mesh in gltf["meshes"]:
        primitive = mesh["primitives"][0]
        assert primitive["mode"] == 4
        assert gltf["accessors"][primitive["indices"]]["count"] > 0
    bone_ids = {entry["id"] for entry in bones}
    bone_material_colors = {
        tuple(gltf["materials"][mesh["primitives"][0]["material"]]["pbrMetallicRoughness"]["baseColorFactor"])
        for mesh in gltf["meshes"] if mesh["name"] in bone_ids
    }
    assert len(bone_material_colors) == len(bones), "Standalone GLB bone colours must also be unique"
    assert all("uri" not in buffer for buffer in gltf["buffers"]), "GLB must be self-contained"
    return manifest


def snapshot(page) -> dict:
    return page.evaluate("window.skullAtlas.snapshot()")


def visible(page) -> set[str]:
    return {entry["id"] for entry in snapshot(page)["structures"] if entry["visible"]}


def by_id(page) -> dict:
    return {entry["id"]: entry for entry in snapshot(page)["structures"]}


def settle(page) -> None:
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    # Highlights are prepared off-thread. Wait for real completion rather than
    # assuming that a fixed pair of animation frames also finishes geometry.
    page.wait_for_function("!window.skullAtlas?.snapshot().anatomy.preparingRegions", polling=100)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def canvas_image(page) -> Image.Image:
    import base64
    raw = page.locator("#skull-canvas").evaluate("canvas => canvas.toDataURL('image/png').split(',')[1]")
    return Image.open(BytesIO(base64.b64decode(raw))).convert("RGB")


def anatomy_bbox(page):
    settle(page)
    image = canvas_image(page)
    background = Image.new("RGB", image.size, image.getpixel((0, 0)))
    difference = ImageChops.difference(image, background).convert("L").point(lambda pixel: 255 if pixel > 22 else 0)
    return difference.getbbox()


def capture(page, name: str) -> None:
    settle(page)
    page.screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=True)


def hover_color_checks(page, manifest: dict, prefix: str) -> None:
    page.locator("#landmark-estimates").uncheck()
    bones = [entry for entry in manifest["structures"] if entry["kind"] == "bone"]
    master = page.locator("#bone-colors")
    toggle = page.get_by_role("button", name="Unique colour for frontal bone", exact=True)
    swatch = toggle.locator(".bone-swatch")
    assert manifest["defaults"]["colors"] is False
    assert not master.is_checked() and not master.evaluate("element => element.indeterminate")
    assert all(not by_id(page)[entry["id"]]["useColor"] for entry in bones)
    neutral = by_id(page)["frontal-bone"]["displayedColor"]
    assert all(by_id(page)[entry["id"]]["displayedColor"] == neutral for entry in bones)
    swatch_color = swatch.evaluate("element => element.style.background")

    page.locator('[data-view="anterior"]').click()
    point = page.evaluate("window.skullAtlas.project('frontal-bone')")
    page.mouse.move(point["x"], point["y"])
    page.wait_for_function("window.skullAtlas.snapshot().hovered === 'frontal-bone'")
    current = by_id(page)
    assert current["frontal-bone"]["displayedColor"] == current["frontal-bone"]["color"]
    assert all(not current[entry["id"]]["useColor"] for entry in bones), "Hover must not enable persistent colour"
    assert all(current[entry["id"]]["displayedColor"] == neutral for entry in bones if entry["id"] != "frontal-bone")
    assert not master.is_checked() and not master.evaluate("element => element.indeterminate")
    assert toggle.get_attribute("aria-pressed") == "false"
    assert swatch.evaluate("element => element.style.background") == swatch_color
    capture(page, f"{prefix}-hover-neutral")
    page.mouse.move(0, 0)
    page.wait_for_function("window.skullAtlas.snapshot().hovered === null")
    assert by_id(page)["frontal-bone"]["displayedColor"] == neutral

    # Both individual and global colour modes retain the existing emissive hover
    # highlight, without replacing their persistent material colour.
    toggle.click()
    for all_coloured in (False, True):
        if all_coloured:
            master.check()
        page.mouse.move(point["x"], point["y"])
        page.wait_for_function("window.skullAtlas.snapshot().hovered === 'frontal-bone'")
        frontal = by_id(page)["frontal-bone"]
        assert frontal["useColor"] and frontal["displayedColor"] == frontal["color"]
        assert frontal["emissiveColor"] == "#273e2d" and frontal["emissiveIntensity"] == 0.4
        capture(page, f"{prefix}-hover-{'all-coloured' if all_coloured else 'coloured'}")
        page.mouse.move(0, 0)
        page.wait_for_function("window.skullAtlas.snapshot().hovered === null")
        frontal = by_id(page)["frontal-bone"]
        assert frontal["useColor"] and frontal["displayedColor"] == frontal["color"]
        assert frontal["emissiveColor"] == "#000000"
    page.locator("#reset").click()
    page.locator("#landmark-estimates").uncheck()
    assert not master.is_checked() and not master.evaluate("element => element.indeterminate")
    assert all(not by_id(page)[entry["id"]]["useColor"] and by_id(page)[entry["id"]]["displayedColor"] == neutral for entry in bones)


def bone_control_checks(page, manifest: dict, prefix: str) -> None:
    bones = [entry for entry in manifest["structures"] if entry["kind"] == "bone"]
    initial_visibility = visible(page)
    master = page.locator("#bone-colors")
    assert not master.is_checked()
    neutral_colors = {by_id(page)[entry["id"]]["displayedColor"] for entry in bones}
    assert len(neutral_colors) == 1
    neutral = next(iter(neutral_colors))
    assert visible(page) == initial_visibility
    capture(page, f"{prefix}-default-colour")
    page.get_by_role("button", name="Unique colour for frontal bone", exact=True).click()
    current = by_id(page)
    assert current["frontal-bone"]["displayedColor"] == current["frontal-bone"]["color"]
    assert all(current[entry["id"]]["displayedColor"] == neutral for entry in bones if entry["id"] != "frontal-bone")
    assert master.evaluate("element => element.indeterminate")
    master.check()
    assert len({by_id(page)[entry["id"]]["displayedColor"] for entry in bones}) == len(bones)
    capture(page, f"{prefix}-unique-colours")

    opened = []
    for group in {entry["group"] for entry in bones}:
        expand = page.locator(f'[data-group="{group}"] .group-expand')
        if expand.get_attribute("aria-expanded") == "false":
            expand.click()
            opened.append(group)
    # Exercise the real controls for every bone, including hidden associated
    # bones. Check actual mesh visibility and material colour, not just the UI.
    for entry in bones:
        identity = entry["id"]
        row = page.locator(f'.bone-row[data-id="{identity}"]')
        checkbox = row.locator('input[type="checkbox"]')
        color_toggle = row.locator(".bone-color-toggle")
        checkbox.uncheck()
        settle(page)
        assert not by_id(page)[identity]["rendered"]
        color_toggle.click()
        current = by_id(page)
        assert not current[identity]["useColor"]
        assert current[identity]["displayedColor"] == neutral
        assert not current[identity]["rendered"], "Colour changes must not reveal a hidden bone"
        if identity == "frontal-bone":
            capture(page, f"{prefix}-frontal-hidden")
        checkbox.check()
        settle(page)
        assert by_id(page)[identity]["rendered"]
        assert by_id(page)[identity]["displayedColor"] == neutral
        color_toggle.click()
        current = by_id(page)
        assert current[identity]["displayedColor"] == entry["color"]
        assert all(current[other["id"]]["displayedColor"] == other["color"] for other in bones)
        if identity not in initial_visibility:
            checkbox.uncheck()
    for group in opened:
        page.locator(f'[data-group="{group}"] .group-expand').click()
    page.get_by_role("button", name="Unique colour for right parietal bone", exact=True).click()
    assert by_id(page)["parietal-bone-right"]["displayedColor"] == neutral
    assert by_id(page)["parietal-bone-left"]["displayedColor"] != neutral
    capture(page, f"{prefix}-mixed-colours")
    master.check()
    assert visible(page) == initial_visibility
    assert not master.evaluate("element => element.indeterminate")


def browser_checks(url: str, manifest: dict, report: dict) -> None:
    checks = report["checks"]
    chrome = os.environ.get("ANATOMY_CHROME_PATH") or shutil.which("google-chrome-stable") or shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise RuntimeError("Chrome/Chromium is required; set ANATOMY_CHROME_PATH")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chrome, headless=True,
            env={key: value for key, value in os.environ.items() if key not in {"DISPLAY", "WAYLAND_DISPLAY"}},
            args=["--disable-extensions", "--enable-webgl", "--ignore-gpu-blocklist", "--disable-software-rasterizer", "--enable-features=Vulkan", "--use-angle=vulkan"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1, accept_downloads=True)
        errors = []
        external = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url + "/") or route.request.url.startswith("data:") else (external.append(route.request.url), route.abort()))
        try:
            native = json.loads((ROOT / 'assets/skull-ct/manifest.json').read_text())
            associated = json.loads((ROOT / 'assets/skull-associated/manifest.json').read_text())
            manifest = {**native, 'structures': native['structures'] + associated['structures']}
            page.goto(url + "/?dataset=reference", wait_until="networkidle")
            page.wait_for_function("document.documentElement.dataset.ready === 'true'", timeout=60000)
            assert snapshot(page)['dataset'] == 'ct' and 'dataset=reference' not in page.url
            assert page.locator('#dataset option').evaluate_all('options=>options.map(e=>e.value)') == ['ct', 'microct']
            renderer = page.locator("canvas").evaluate("""canvas => {
                const gl = canvas.getContext('webgl2');
                if (!gl) return 'NO_WEBGL2';
                const extension = gl.getExtension('WEBGL_debug_renderer_info');
                return extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
            }""")
            assert renderer != "NO_WEBGL2" and "SwiftShader" not in renderer, renderer
            report["renderer"] = renderer
            assert not external
            assert visible(page) == SKULL_IDS | OSSICLE_IDS | {"hyoid-bone", "upper-dentition", "lower-dentition"}
            loaded = by_id(page)
            for entry in manifest["structures"]:
                assert loaded[entry["id"]]["triangles"] == entry["triangles"]
                assert max(abs(a - b) for actual, source in zip(loaded[entry["id"]]["bounds"], entry["bounds"]) for a, b in zip(actual, source)) < 0.001
            capture(page, "assembled")
            assert anatomy_bbox(page) is not None
            checks.append("CT-only skull viewer: 31 meshes retain native positions and triangle counts; retired reference links resolve to CT")
            hover_color_checks(page, manifest, "ct")
            checks.append("CT: ivory defaults, temporary assigned hover colour, existing coloured hover highlight and neutral reset")
            bone_control_checks(page, manifest, "ct")
            checks.append("CT: unique colours and independent show/hide and colour/ivory controls exercised for all 29 bones")

            page.locator('[data-view="anterior"]').click()
            point = page.evaluate("window.skullAtlas.project('frontal-bone')")
            page.mouse.move(point["x"], point["y"])
            page.wait_for_function("window.skullAtlas.snapshot().hovered === 'frontal-bone'")
            page.mouse.click(point["x"], point["y"])
            assert snapshot(page)["selected"] == "frontal-bone"
            assert page.locator("#selected-name").inner_text() == "Frontal bone"
            capture(page, "selection")
            page.locator("#clear-selection").click()
            before_drag = snapshot(page)["camera"]
            page.mouse.move(point["x"], point["y"])
            page.mouse.down(); page.mouse.move(point["x"] + 120, point["y"] + 30, steps=12); page.mouse.up()
            assert snapshot(page)["selected"] is None
            assert snapshot(page)["camera"] != before_drag
            checks.append("Surface hover and click identify the visible bone; camera drags do not select anatomy")

            page.locator("#reset").click()
            page.locator("#landmark-estimates").uncheck()
            page.get_by_role("checkbox", name="Show frontal bone", exact=True).uncheck()
            assert "frontal-bone" not in visible(page)
            assert page.get_by_role("checkbox", name="Show all cranial bones", exact=True).evaluate("element => element.indeterminate")
            page.get_by_role("checkbox", name="Show all cranial bones", exact=True).check()
            assert SKULL_IDS <= visible(page)
            page.locator("#hide-all").click()
            assert not visible(page)
            assert anatomy_bbox(page) is None
            page.locator("#show-all").click()
            assert len(visible(page)) == 31
            checks.append("Individual and group visibility, tri-state controls, hide all and show all")

            page.locator("#search").fill("left stapes")
            assert page.locator(".bone-row:visible").count() == 1
            before_isolation = snapshot(page)
            page.get_by_role("button", name="Isolate and focus left stapes", exact=True).click()
            assert visible(page) == {"stapes-left"}
            box = anatomy_bbox(page)
            assert box and box[2] - box[0] > 100 and box[3] - box[1] > 100
            capture(page, "stapes")
            page.locator("#restore").click()
            assert len(visible(page)) == 31
            restored = snapshot(page)
            assert restored["viewOffset"] == before_isolation["viewOffset"]
            assert max(abs(a - b) for a, b in zip(restored["camera"], before_isolation["camera"])) < 1e-6
            page.locator("#search").fill("missing-structure")
            assert page.locator("#no-results").is_visible()
            assert page.locator(".bone-row:visible").count() == 0
            page.locator("#search").fill("")
            checks.append("Search, empty results, isolation and framing of a millimetre-scale ossicle, and restoration")

            page.get_by_role("button", name="Frontal bone", exact=True).click()
            page.locator(".selected-display > summary").click()
            page.locator("#selected-color").evaluate("element => { element.value = '#e75442'; element.dispatchEvent(new Event('input', {bubbles:true})); }")
            assert by_id(page)["frontal-bone"]["displayedColor"] == "#e75442"
            page.locator("#selected-opacity").fill("50")
            page.locator("#opacity").fill("60")
            assert abs(by_id(page)["frontal-bone"]["opacity"] - 0.3) < 1e-8
            page.locator("#bone-colors").check()
            page.locator("#bone-colors").uncheck()
            assert len({entry["displayedColor"] for entry in by_id(page).values() if entry["group"] != "teeth"}) == 1
            checks.append("Custom colours, global and individual opacity, and neutral bone palette")

            page.locator("#reset").click()
            page.locator("#landmark-estimates").uncheck()
            original = by_id(page)
            page.locator("#explode").fill("75")
            exploded = by_id(page)
            assert exploded["temporal-bone-left"]["bounds"][0][0] > original["temporal-bone-left"]["bounds"][0][0] + 60
            for identity in ("mandible", "lower-dentition"):
                delta = [exploded[identity]["bounds"][0][i] - original[identity]["bounds"][0][i] for i in range(3)]
                assert max(abs(a - b) for a, b in zip(delta, (0, -71.25, 30))) < 0.001
            capture(page, "exploded")
            page.locator("#explode").fill("0")
            page.locator(".more-controls > summary").click()
            page.locator("#jaw").fill("25")
            opened = by_id(page)
            assert opened["lower-dentition"]["bounds"][0][1] < original["lower-dentition"]["bounds"][0][1] - 10
            assert opened["upper-dentition"]["bounds"] == original["upper-dentition"]["bounds"]
            capture(page, "jaw-open")
            checks.append("Exploded transforms retain tooth-to-jaw alignment; jaw opening moves lower teeth only")

            page.locator("#jaw").fill("0")
            for axis in ("sagittal", "coronal", "axial"):
                page.locator("#section").select_option(axis)
                page.locator("#section-position").fill("50")
                assert anatomy_bbox(page)
                capture(page, f"section-{axis}")
            page.locator("#section").select_option("sagittal")
            page.locator("#section-position").fill("100")
            assert anatomy_bbox(page) is None, "The section must actually clip every fragment"
            page.mouse.move(point["x"], point["y"])
            settle(page)
            assert snapshot(page)["hovered"] is None, "Picking must reject clipped fragments"
            page.locator("#section-flip").check()
            assert anatomy_bbox(page)
            page.get_by_role("button", name="Frontal bone", exact=True).click()
            page.locator("#isolate").click()
            assert snapshot(page)["section"] == "none"
            page.locator("#restore").click()
            assert snapshot(page)["section"] == "sagittal"
            assert page.locator("#section-flip").is_checked()
            checks.append("All section axes, position extremes, side flipping, clipped-surface picking and section restoration")

            page.locator("#reset").click()
            page.locator("#landmark-estimates").uncheck()
            for view in ("anterior", "posterior", "left", "right", "superior", "inferior"):
                page.locator(f'[data-view="{view}"]').click()
                assert anatomy_bbox(page)
                assert page.locator(f'[data-view="{view}"]').get_attribute("aria-pressed") == "true"
                capture(page, f"view-{view}")
            page.locator("#reset-camera").click()
            with page.expect_download() as result:
                page.locator("#screenshot").click()
            result.value.save_as(str(ARTIFACTS / "download.png"))
            assert Image.open(ARTIFACTS / "download.png").size == canvas_image(page).size
            checks.append("All six anatomical views and PNG image download")

            page.set_viewport_size({"width": 390, "height": 844})
            settle(page)
            page.wait_for_function("document.documentElement.scrollWidth <= innerWidth")
            assert anatomy_bbox(page)
            capture(page, "mobile")
            page.set_viewport_size({"width": 3840, "height": 2160})
            settle(page)
            assert anatomy_bbox(page)
            capture(page, "4k")
            checks.append("390-pixel mobile layout and 4K rendering")
            assert not errors, errors
            assert not external, external

            ct_browser_checks(page, url, checks)
            assert not errors, errors
            assert not external, external

            failure = browser.new_page()
            failure.route("**/surface.glb", lambda route: route.fulfill(status=404, body="Missing model"))
            failure.goto(url, wait_until="networkidle")
            failure.get_by_role("heading", name="Unable to load the skull").wait_for()
            assert failure.locator("#control-panel").evaluate("element => element.disabled")
            assert failure.locator("#opacity").is_disabled()
            capture(failure, "missing-model")
            failure.close()
            checks.append("Missing model produces a visible error with unavailable controls disabled")
        except Exception:
            capture(page, "failure")
            raise
        finally:
            browser.close()


def ct_browser_checks(page, url: str, checks: list) -> None:
    manifest = json.loads((ROOT / "assets/skull-ct/manifest.json").read_text())
    data = (ROOT / manifest["model"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest["sha256"]
    structures = manifest["structures"]
    assert {entry["id"] for entry in structures if entry["kind"] == "bone"} == SKULL_IDS
    assert len(structures) == 24
    assert sum(entry["triangles"] for entry in structures) == 1_421_276
    assert manifest["units"] == "display units"
    assert manifest["source"]["pdf_sha256"] == "323cb4b3ab608da1dda6bb7582d65a0560f637131e56bd197fc4a5c8bcd2f869"
    reference = {entry["id"]: entry for entry in json.loads((ROOT / "assets/skull/manifest.json").read_text())["structures"]}
    for entry in structures:
        if entry["id"] in {"frontal-bone", "parietal-bone-left", "parietal-bone-right"}:
            assert entry["triangles"] > reference[entry["id"]]["triangles"] * 19
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(url, wait_until="networkidle")
    page.wait_for_function("document.documentElement.dataset.ready === 'true'", timeout=60000)
    assert snapshot(page)["dataset"] == "ct"
    assert snapshot(page)["depthShading"]
    assert page.locator("#catalogue-total").inner_text() == "766"
    assert not page.locator("#bone-colors").is_checked()
    loaded = by_id(page)
    for entry in structures:
        actual = loaded[entry["id"]]
        assert actual["triangles"] == entry["triangles"]
        assert max(abs(a - b) for bounds, expected in zip(actual["bounds"], entry["bounds"]) for a, b in zip(bounds, expected)) < 0.001
    assert len(visible(page)) == 31
    capture(page, "ct-assembled")
    assert anatomy_bbox(page)
    checks.append("Default CT skull: all 22 bones, both native dental arches, exact source triangles and positions; >19× vault geometry")
    assert len({entry["color"] for entry in structures if entry["kind"] == "bone"}) == 22
    hover_color_checks(page, manifest, "ct")
    checks.append("CT: ivory defaults, temporary assigned hover colour, existing coloured hover highlight and neutral reset")
    bone_control_checks(page, manifest, "ct")
    checks.append("CT: all 22 bones have distinct colours, working individual visibility and colour/ivory toggles, plus a global toggle")

    page.locator('[data-view="anterior"]').click()
    point = page.evaluate("window.skullAtlas.project('frontal-bone')")
    page.mouse.move(point["x"], point["y"])
    page.wait_for_function("window.skullAtlas.snapshot().hovered === 'frontal-bone'")
    page.mouse.click(point["x"], point["y"])
    assert snapshot(page)["selected"] == "frontal-bone"
    capture(page, "ct-selected")
    page.locator("#clear-selection").click()
    page.locator("#bone-colors").check()
    page.locator("#reset-camera").click()
    capture(page, "ct-coloured")
    page.locator("#reset").click()
    page.locator("#landmark-estimates").uncheck()
    page.locator("#search").fill("left temporal")
    page.get_by_role("button", name="Isolate and focus left temporal bone", exact=True).click()
    assert visible(page) == {"temporal-bone-left"}
    page.locator('[data-view="left"]').click()
    capture(page, "ct-temporal-detail")
    page.locator("#restore").click()
    page.locator("#reset").click()
    page.locator("#landmark-estimates").uncheck()
    page.locator("#explode").fill("65")
    assert by_id(page)["temporal-bone-left"]["bounds"][0][0] > loaded["temporal-bone-left"]["bounds"][0][0] + 60
    capture(page, "ct-exploded")
    checks.append("Dense CT surface picking, colours, temporal-bone isolation/restoration and exploded view")

    page.locator("#explode").fill("0")
    page.locator(".more-controls > summary").click()
    page.locator("#jaw").fill("20")
    opened = by_id(page)
    assert opened["lower-dentition"]["bounds"] != loaded["lower-dentition"]["bounds"]
    assert opened["upper-dentition"]["bounds"] == loaded["upper-dentition"]["bounds"]
    page.locator("#jaw").fill("0")
    for axis in ("sagittal", "coronal", "axial"):
        page.locator("#section").select_option(axis)
        page.locator("#section-position").fill("50")
        assert snapshot(page)["depthShading"]
        assert anatomy_bbox(page)
        capture(page, f"ct-section-{axis}")
    assert page.locator("#section-value").inner_text().endswith("units")
    page.locator("#section").select_option("sagittal")
    page.locator("#section-position").fill("100")
    assert anatomy_bbox(page) is None, "AO must not resurrect fully clipped CT fragments"
    page.locator("#section").select_option("none")
    page.locator("#opacity").fill("55")
    settle(page)
    assert not snapshot(page)["depthShading"]
    assert page.locator("#depth-shading").is_disabled()
    assert anatomy_bbox(page)
    capture(page, "ct-transparent")
    page.locator("#opacity").fill("100")
    settle(page)
    assert snapshot(page)["depthShading"]
    checks.append("CT jaw/dentition motion, all section axes with matching AO clipping, and transparency without ghost occlusion")

    page.locator("#reset").click()
    page.locator("#landmark-estimates").uncheck()
    for view in ("anterior", "posterior", "left", "right", "superior", "inferior"):
        page.locator(f'[data-view="{view}"]').click()
        assert anatomy_bbox(page)
        capture(page, f"ct-view-{view}")
    page.locator("#reset-camera").click()
    page.set_viewport_size({"width": 390, "height": 844})
    settle(page)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert anatomy_bbox(page)
    capture(page, "ct-mobile")
    page.set_viewport_size({"width": 3840, "height": 2160})
    settle(page)
    assert anatomy_bbox(page)
    capture(page, "ct-4k")
    assert page.locator("#model-download").get_attribute("href") == "assets/skull-ct/surface.glb"
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator("#dataset").select_option("microct")
    page.wait_for_function("document.documentElement.dataset.ready === 'true' && window.skullAtlas?.snapshot().dataset === 'microct'", timeout=60000)
    assert len(visible(page)) == 1
    checks.append("CT anatomical views, mobile and 4K rendering, source-normal GLB download and retained micro-CT fragment selector")


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "result": "failed"}
    try:
        manifest = asset_checks()
        report["checks"].append("GLB checksum, explicit 22 skull bones and six ossicles, 28 teeth and hyoid in the main skull")
        with local_server() as url:
            for path in ("/pyproject.toml", "/scripts/serve_viewer.py", "/data/source/Startup.blend", "/.venv/", "/src/", "/assets/../pyproject.toml"):
                try:
                    urllib.request.urlopen(url + path)
                except urllib.error.HTTPError as error:
                    assert error.code == 404
                else:
                    raise AssertionError(f"Development path is exposed: {path}")
            report["checks"].append("Local HTTP server restricts development files, source data, traversal and directory listing")
            browser_checks(url, manifest, report)
        report["result"] = "passed"
    finally:
        (ARTIFACTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
