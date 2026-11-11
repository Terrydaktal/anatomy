#!/usr/bin/env python3
"""Verify surface overlays against independent shipped GLB triangles and real picking."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct

from PIL import ImageChops
from playwright.sync_api import sync_playwright

from test_viewer import ARTIFACTS, ROOT, anatomy_bbox, canvas_image, capture, local_server, settle, snapshot
from test_anatomy import load


class SourceTriangles:
    def __init__(self, dataset: str):
        manifest = json.loads((ROOT / f"assets/{'skull-ct' if dataset == 'ct' else 'skull'}/manifest.json").read_text())
        raw = (ROOT / manifest["model"]).read_bytes()
        length = struct.unpack_from("<I", raw, 12)[0]
        self.gltf = json.loads(raw[20:20 + length])
        self.buffer = raw[28 + length:]
        self.meshes = {mesh["name"]: mesh["primitives"][0] for mesh in self.gltf["meshes"]}

    def accessor(self, index: int):
        accessor = self.gltf["accessors"][index]
        view = self.gltf["bufferViews"][accessor["bufferView"]]
        return accessor, view, view.get("byteOffset", 0) + accessor.get("byteOffset", 0)

    def triangle(self, owner: str, face: int):
        mesh = self.meshes[owner]
        accessor, view, offset = self.accessor(mesh["indices"])
        code, width = {5123: ("H", 2), 5125: ("I", 4)}[accessor["componentType"]]
        indices = struct.unpack_from("<" + code * 3, self.buffer, offset + face * 3 * width)
        accessor, view, offset = self.accessor(mesh["attributes"]["POSITION"])
        assert accessor["componentType"] == 5126
        stride = view.get("byteStride", 12)
        return [struct.unpack_from("<3f", self.buffer, offset + index * stride) for index in indices]

    def triangle_normals(self, owner: str, face: int):
        mesh = self.meshes[owner]
        accessor, view, offset = self.accessor(mesh['indices'])
        code, width = {5123: ('H', 2), 5125: ('I', 4)}[accessor['componentType']]
        indices = struct.unpack_from('<' + code * 3, self.buffer, offset + face * 3 * width)
        accessor, view, offset = self.accessor(mesh['attributes']['NORMAL'])
        assert accessor['componentType'] == 5126
        stride = view.get('byteStride', 12)
        return [struct.unpack_from('<3f', self.buffer, offset + index * stride) for index in indices]


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def assert_on_triangle(point, triangle):
    a, b, c = triangle
    ab, ac, ap = sub(b, a), sub(c, a), sub(point, a)
    normal = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]]
    length = math.sqrt(dot(normal, normal))
    assert length > 1e-10
    assert abs(dot(ap, normal)) / length < 5e-5, "Highlight vertex floated off its original source face"
    aa, bb, cc, pa, pc = dot(ab, ab), dot(ab, ac), dot(ac, ac), dot(ap, ab), dot(ap, ac)
    denominator = aa * cc - bb * bb
    u, v = (cc * pa - bb * pc) / denominator, (aa * pc - bb * pa) / denominator
    assert min(u, v, 1 - u - v) > -0.003, "Highlight extended beyond the source triangle"


def audit_geometry(page, source: SourceTriangles, identity: str):
    audit = page.evaluate("id => window.skullAtlas.regionGeometry(id)", identity)
    assert audit and audit["area"] > 0 and audit["parts"]
    count = 0
    for part in audit["parts"]:
        positions, faces = part["positions"], part["sourceFaces"]
        assert len(positions) == len(faces) * 9
        for index, face in enumerate(faces):
            triangle = source.triangle(part["owner"], face)
            for offset in range(3):
                assert_on_triangle(positions[index * 9 + offset * 3:index * 9 + offset * 3 + 3], triangle)
                count += 1
    assert count >= 3
    return audit


def select_feature(page, name: str):
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator("#landmark-search").fill(name)
    button = page.get_by_role("button", name=name, exact=True)
    button.click()
    identity = snapshot(page)["anatomy"]["selected"]["id"]
    page.locator("#anatomy-focus").click()
    settle(page)
    return identity


def changed_pixels(a, b):
    mask = ImageChops.difference(a, b).convert("L").point(lambda value: 255 if value > 4 else 0)
    return mask.histogram()[255]


def visible_point(page, audit):
    canvas = page.locator("#skull-canvas").bounding_box()
    card = page.locator("#anatomy-selection").bounding_box()
    for part in audit["parts"]:
        points = part["positions"]
        stride = max(1, len(part["sourceFaces"]) // 70)
        for face in range(0, len(part["sourceFaces"]), stride):
            center = [sum(points[face * 9 + corner * 3 + axis] for corner in range(3)) / 3 for axis in range(3)]
            projected = page.evaluate("({point, owner}) => window.skullAtlas.projectPoint(point, owner)", {"point": center, "owner": part["owner"]})
            x, y = projected["x"], projected["y"]
            if not (canvas["x"] + 20 < x < canvas["x"] + canvas["width"] - 20 and canvas["y"] + 185 < y < canvas["y"] + canvas["height"] - 40):
                continue
            if card and card["x"] - 5 < x < card["x"] + card["width"] + 5 and card["y"] - 5 < y < card["y"] + card["height"] + 5:
                continue
            if page.evaluate("({x,y}) => window.skullAtlas.regionAt(x,y)", projected) == audit["id"]:
                return projected
    raise AssertionError("The highlighted surface has no visible, pickable source triangle")


def feature_checks(page, url, dataset, name, checks, reveal=False):
    load(page, url, f"?dataset={dataset}")
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator("#landmark-estimates").check()
    source = SourceTriangles(dataset)
    identity = select_feature(page, name)
    if reveal:
        page.locator("#anatomy-isolate").click()
        settle(page)
    assert not page.locator("#landmark-markers").is_checked()
    assert page.locator(".anatomy-pin:visible").count() == 0
    audit = audit_geometry(page, source, identity)
    if dataset == "ct" and identity in {"left-infraorbital-foramen", "right-infraorbital-foramen", "left-mental-foramen", "right-mental-foramen"}:
        assert audit["methods"] == ["fitted:geometry-detected-aperture-rim"]
        assert all(part["scope"] == "opening-rim" and part["fit"] for part in audit["parts"])
        assert "opening remains unfilled" in page.locator("#anatomy-placement").inner_text().lower()
        aperture_face_check(audit)
    point = visible_point(page, audit)
    page.mouse.move(point["x"], point["y"])
    page.wait_for_function("id => window.skullAtlas.snapshot().anatomy.hoveredRegion === id", arg=identity)
    assert snapshot(page)["hovered"] is None, "Hovering a surface region must not colour the complete parent bone"
    page.mouse.click(point["x"], point["y"])
    assert snapshot(page)["anatomy"]["selected"] == {"id": identity, "kind": "landmark"}
    page.mouse.move(0, 0)
    settle(page)
    coloured = canvas_image(page)
    capture(page, f"region-{dataset}-{identity}")
    row = page.locator(f'.landmark-row[data-landmark="{identity}"]')
    assert not row.locator('input[type="checkbox"]').is_checked()
    page.locator('#anatomy-close').click()
    settle(page)
    plain = canvas_image(page)
    pixels = changed_pixels(coloured, plain)
    assert 30 < pixels < coloured.width * coloured.height * 0.65, f"Expected a visible local surface highlight, got {pixels} changed pixels"
    row.locator(".landmark-name").hover()
    settle(page)
    assert identity not in snapshot(page)["anatomy"]["enabledRegions"]
    assert snapshot(page)["anatomy"]["hoveredRegion"] == identity
    assert changed_pixels(canvas_image(page), plain) > 30
    page.mouse.move(0, 0)
    settle(page)
    assert not next(region["rendered"] for region in snapshot(page)["anatomy"]["regions"] if region["id"] == identity)
    assert not page.locator("#bone-colors").is_checked()
    checks.append(f"{dataset} {name}: all overlay vertices verified on source triangles; visible local fill, exact drawn-region picking and temporary hover without pins")
    return identity, audit


def aperture_face_check(audit):
    manifest = json.loads((ROOT / "assets/landmark-regions/apertures.json").read_text())
    raw = (ROOT / manifest["binary"]["url"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest["binary"]["sha256"]
    record = next(region for region in manifest["regions"] if region["dataset"] == "ct" and region["feature_id"] == audit["id"])
    assert record["approximate"] and record["coordinate_units"] == "uncalibrated-display-units"
    assert len(audit["parts"]) == 1
    part = audit["parts"][0]
    faces = list(struct.unpack_from(f'<{record["source_faces_count"]}I', raw, record["source_faces_byte_offset"]))
    positions = list(struct.unpack_from(f'<{record["positions_count"]}f', raw, record["positions_byte_offset"]))
    assert part["sourceFaces"] == faces and part["positions"] == positions
    frame, center = record["fit"]["frame"], record["fit"]["aperture_offset_uv"]
    # Independently project each native patch into its measured opening view.
    # A painted cap would cover the observed opening's central coordinate.
    for offset in range(0, len(positions), 9):
        projected = []
        for corner in range(3):
            p = sub(positions[offset + corner * 3:offset + corner * 3 + 3], frame["origin"])
            projected.append([dot(p, frame[axis]) - center[i] for i, axis in enumerate(("u", "v"))])
        crosses = [a[0] * b[1] - a[1] * b[0] for a, b in zip(projected, projected[1:] + projected[:1])]
        if abs(sum(crosses)) > 1e-9:
            assert min(crosses) < -1e-8 < max(crosses), "A native rim patch covered the centre of its opening"


def source_face_check(audit):
    manifest = json.loads((ROOT / "assets/landmark-regions/source-regions.json").read_text())
    raw = (ROOT / manifest["binary"]["url"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest["binary"]["sha256"]
    expected = {}
    for region in manifest["regions"]:
        if audit["id"] in region["feature_ids"]:
            faces = struct.unpack_from("<" + "I" * region["count"], raw, region["byte_offset"])
            expected.setdefault(region["owner"], set()).update(faces)
    assert expected
    for part in audit["parts"]:
        assert part["method"] == "source-authored-faces"
        assert set(part["sourceFaces"]) == expected[part["owner"]], "Source-defined articular selection must retain every original selected face"


def transform_checks(page, url, checks):
    load(page, url, "?dataset=ct")
    identity = select_feature(page, "Left submandibular fossa")
    source = SourceTriangles("ct")
    before = audit_geometry(page, source, identity)
    assert 'estimated' in page.locator("#anatomy-placement").inner_text().lower()
    page.locator(".more-controls > summary").click()
    page.locator("#jaw").fill("20")
    page.locator("#explode").fill("35")
    settle(page)
    moved = page.evaluate("id => window.skullAtlas.regionGeometry(id)", identity)
    assert before["parts"][0]["positions"] == moved["parts"][0]["positions"]
    assert before["parts"][0]["matrix"] != moved["parts"][0]["matrix"]
    page.locator("#opacity").fill("50")
    settle(page)
    assert all(abs(part["opacity"] - 0.5) < 1e-8 for part in page.evaluate("id => window.skullAtlas.regionGeometry(id)", identity)["parts"])
    page.locator("#opacity").fill("100")
    page.locator("#section").select_option("sagittal")
    page.locator("#section-position").fill("100")
    settle(page)
    assert anatomy_bbox(page) is None
    assert not any(part["rendered"] for part in page.evaluate("id => window.skullAtlas.regionGeometry(id)", identity)["parts"])
    page.locator("#section").select_option("none")
    page.locator("#bone-catalogue").scroll_into_view_if_needed()
    page.get_by_role("checkbox", name="Show mandible", exact=True).uncheck()
    settle(page)
    assert not any(part["rendered"] for part in page.evaluate("id => window.skullAtlas.regionGeometry(id)", identity)["parts"])
    page.locator("#reset").click()
    settle(page)
    assert not any(region["rendered"] for region in snapshot(page)["anatomy"]["regions"])
    checks.append("Mapped CT mandibular faces follow jaw motion, explosion, opacity, hidden bones, section cuts and reset")


def source_mode_checks(page, url, checks):
    load(page, url, "?dataset=ct")
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    assert page.locator("#landmark-estimates").is_checked()
    assert page.locator(".landmark-row:not([hidden])").count() == 737
    page.locator("#landmark-estimates").uncheck()
    assert snapshot(page)["anatomy"]["suppliedRegionCount"] == 10
    assert page.locator(".landmark-row:not([hidden])").count() == 10
    assert not page.locator("#landmark-pins").is_disabled()
    page.locator("#landmark-estimates").check()
    identity = select_feature(page, "Left pterygopalatine fossa")
    assert any(region["rendered"] for region in snapshot(page)["anatomy"]["regions"] if region["id"] == identity)
    assert any(word in page.locator("#anatomy-placement").inner_text() for word in ['inferred', 'estimated'])
    page.locator("#landmark-estimates").uncheck()
    settle(page)
    state = snapshot(page)["anatomy"]
    assert not state["selected"] and not state["hoveredRegion"] and not state["enabledRegions"]
    assert not any(region["rendered"] for region in state["regions"])

    assert page.locator('#landmark-reference').count() == 0
    assert snapshot(page)["anatomy"]["suppliedRegionCount"] == 10
    page.locator('#landmark-search').fill('')
    assert page.locator(".landmark-row:not([hidden])").count() == 10
    supplied = select_feature(page, "Left base of stapes")
    assert page.evaluate("id => window.skullAtlas.regionGeometry(id).methods", supplied) == ["source-authored-faces"]
    page.locator(f'.landmark-row[data-landmark="{supplied}"] input[type="checkbox"]').check()
    page.locator("#landmark-estimates").check()
    select_feature(page, "Left pterygopalatine fossa")
    page.locator("#landmark-estimates").uncheck()
    settle(page)
    state = snapshot(page)["anatomy"]
    assert state["enabledRegions"] == [supplied]
    assert not state["selected"] and not state["hoveredRegion"]
    assert {region["id"] for region in state["regions"] if region["rendered"]} == {supplied}
    page.locator("#reset").click()
    assert page.locator("#landmark-estimates").is_checked()
    checks.append("Estimates enabled by default; source-only mode retains 10 exact imported ear-bone selections on CT, removes estimates immediately, and preserves supplied selections across mode changes")


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {"result": "failed", "checks": []}
    try:
        chrome = os.environ.get("ANATOMY_CHROME_PATH") or shutil.which("google-chrome-stable") or shutil.which("chromium")
        with local_server() as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=chrome, headless=True,
                env={key: value for key, value in os.environ.items() if key not in {"DISPLAY", "WAYLAND_DISPLAY"}},
                args=["--disable-extensions", "--enable-webgl", "--ignore-gpu-blocklist", "--disable-software-rasterizer", "--enable-features=Vulkan", "--use-angle=vulkan"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                source_mode_checks(page, url, report["checks"])
                feature_checks(page, url, 'ct', 'Left infraorbital foramen', report['checks'])
                for name in ("Right infraorbital foramen", "Left mental foramen", "Right mental foramen"):
                    feature_checks(page, url, "ct", name, report["checks"])
                feature_checks(page, url, "ct", "Left greater wing", report["checks"], reveal=True)
                feature_checks(page, url, "ct", "Coronal suture", report["checks"])
                transform_checks(page, url, report["checks"])
                assert not errors, errors
            finally:
                browser.close()
        report["result"] = "passed"
    finally:
        (ARTIFACTS / "regions-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
