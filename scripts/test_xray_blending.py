#!/usr/bin/env python3
"""Check that hidden region layers contribute one blend, including on real skulls."""

import argparse
import json

from PIL import ImageChops
from playwright.sync_api import sync_playwright

from test_precomputed_highlights import launch, ready
from test_viewer import ARTIFACTS, ROOT, canvas_image, local_server, settle, snapshot

HOOK = '''window.xrayBlendOracle = {
  copies: [],
  duplicate: count => {
    window.xrayBlendOracle.clear();
    const id = details.hovered ?? details.selected?.id;
    for (const part of details.regions.cache.get(id).parts) for (let i = 0; i < count; i++) {
      const copy = part.ghost.clone();
      part.mesh.add(copy); window.xrayBlendOracle.copies.push(copy);
    }
    dirty = true;
  },
  clear: () => { for (const copy of window.xrayBlendOracle.copies) copy.removeFromParent(); window.xrayBlendOracle.copies = []; dirty = true; },
  pixels: () => {
    const material = details.regions.cache.get('sella-turcica').parts[0].ghost.material.clone();
    material.color.setRGB(1, 0, 0); material.toneMapped = false;
    const target = composer.readBuffer.clone(); target.texture.type = THREE.UnsignedByteType; target.setSize(64, 64);
    const previous = renderer.getRenderTarget(), clipping = renderer.clippingPlanes;
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10); camera.position.z = 5;
    const cases = [], geometries = [];
    const render = (name, layers, owners = 1, box = false, front = false) => {
      const scene = new THREE.Scene();
      const base = new THREE.MeshBasicMaterial({color: new THREE.Color(0.2, 0.2, 0.2), toneMapped: false});
      const background = new THREE.PlaneGeometry(2, 2); geometries.push(background);
      scene.add(new THREE.Mesh(background, base));
      for (let owner = 0; owner < owners; owner++) {
        let geometry;
        if (box) geometry = new THREE.BoxGeometry(1.5, 1.5, 0.1);
        else {
          const positions = [];
          for (let layer = 0; layer < layers; layer++) {
            const z = (front ? 0.2 : -0.2) - layer * 0.04 - owner * 0.002;
            positions.push(-0.75,-0.75,z, 0.75,-0.75,z, 0.75,0.75,z, -0.75,-0.75,z, 0.75,0.75,z, -0.75,0.75,z);
          }
          geometry = new THREE.BufferGeometry();
          geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        }
        geometries.push(geometry);
        const ghost = new THREE.Mesh(geometry, material); ghost.renderOrder = 10000;
        if (box) ghost.position.z = -0.3;
        scene.add(ghost);
      }
      new RenderPass(scene, camera).render(renderer, null, target);
      const pixel = new Uint8Array(4); renderer.readRenderTargetPixels(target, 32, 32, 1, 1, pixel);
      cases.push({name, layers, owners, pixel: [...pixel]});
      base.dispose();
    };
    try {
      renderer.clippingPlanes = [];
      render('one layer', 1);
      render('eight layers in one mesh', 8);
      render('three owners', 1, 3);
      render('overlapping owners and layers', 16, 3);
      render('closed double-sided surface', 1, 1, true);
      render('foreground excluded', 1, 1, false, true);
      render('next frame clears the mask', 1);
      return {opacity: material.opacity, stencil: [composer.readBuffer.stencilBuffer, composer.writeBuffer.stencilBuffer], cases};
    } finally {
      renderer.clippingPlanes = clipping; renderer.setRenderTarget(previous);
      material.dispose(); target.dispose(); for (const geometry of geometries) geometry.dispose(); dirty = true;
    }
  }
};
'''


def check(page, url, report):
    ready(page, url)
    page.locator('#landmark-search').fill('Sella turcica')
    page.get_by_role('button', name='Sella turcica', exact=True).hover()
    settle(page)
    pixels = page.evaluate('window.xrayBlendOracle.pixels()')
    report['pixel_oracle'] = pixels
    assert pixels['opacity'] == 0.5, 'The original single-layer transparency changed.'
    for case in pixels['cases']:
        # Linear RGB: 0.5 * red + 0.5 * grey, or just grey if in front.
        expected = [51, 51, 51] if case['name'] == 'foreground excluded' else [153, 26, 26]
        assert all(abs(actual - target) <= 1 for actual, target in zip(case['pixel'][:3], expected)), f'{case["name"]}: {case["pixel"]} differs from the single-blend result {expected}'
    assert pixels['stencil'] == [True, True]

    for name in ('Sella turcica', 'Right pterygopalatine fossa', 'Right occipitomastoid suture'):
        page.mouse.move(0, 0)
        page.locator('#landmark-search').fill(name)
        page.get_by_role('button', name=name, exact=True).hover()
        settle(page)
        state = snapshot(page)
        assert state['anatomy']['hoveredRegion'] and any(region['xrayRendered'] for region in state['anatomy']['regions'])
        original = canvas_image(page)
        page.evaluate('window.xrayBlendOracle.duplicate(8)')
        settle(page)
        duplicate = canvas_image(page)
        difference = ImageChops.difference(original, duplicate)
        report['real_regions'].append({'name': name, 'difference_bounds': difference.getbbox()})
        original.save(ARTIFACTS / f'xray-{name.lower().replace(" ", "-")}.png')
        assert difference.getbbox() is None, f'{name}: duplicate hidden surfaces accumulate colour.'
        page.evaluate('window.xrayBlendOracle.clear()')
        settle(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutant', action='store_true', help='Replay the old layered blending in isolated browser responses.')
    parser.add_argument('--report', default='xray-blending-report.json')
    args = parser.parse_args()
    report = {'result': 'failed', 'mutant': args.mutant, 'real_regions': [], 'errors': []}
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        with local_server() as url, sync_playwright() as p:
            browser = launch(p)
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                app = (ROOT / 'src/app.js').read_text()
                page.route('**/src/app.js*', lambda route: route.fulfill(content_type='text/javascript', body=app.replace('  window.skullAtlas = Object.freeze({', HOOK + '\n  window.skullAtlas = Object.freeze({')))
                if args.mutant:
                    module = (ROOT / 'src/surface-regions.js').read_text().replace('stencilWrite: true,', 'stencilWrite: false,').replace('forceSinglePass: true,', 'forceSinglePass: false,')
                    page.route('**/src/surface-regions.js*', lambda route: route.fulfill(content_type='text/javascript', body=module))
                page.on('pageerror', lambda error: report['errors'].append(str(error)))
                check(page, url, report)
                assert not report['errors'], report['errors']
                report['result'] = 'passed'
            finally:
                browser.close()
    except Exception as error:
        report['errors'].append(str(error))
    (ARTIFACTS / args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(report['result'] != 'passed')


if __name__ == '__main__':
    main()
