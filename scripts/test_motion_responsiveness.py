#!/usr/bin/env python3
"""Check real wheel previews, exploded panning and depth-shading cache validity."""

import argparse
from io import BytesIO
import base64
import json
import math
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_responsiveness import percentile
from test_viewer import ARTIFACTS, ROOT, by_id, local_server, settle


# Browser-only observation and an independent fresh-render oracle. Nothing is
# added to the shipped viewer's inspection API or its cache implementation.
OBSERVER = '''
  const motionCounts={depth:0,renders:0};
  const depthRender=ambientOcclusion.renderOverride.bind(ambientOcclusion);
  ambientOcclusion.renderOverride=(...args)=>{motionCounts.depth++;return depthRender(...args);};
  const originalRender=ambientOcclusion.render.bind(ambientOcclusion);
  ambientOcclusion.render=(...args)=>{motionCounts.renders++;return originalRender(...args);};
  window.motionCheck={
    state:()=>({counts:{...motionCounts},moving:!!ambientOcclusion.moving,
      ao:[ambientOcclusion.width,ambientOcclusion.height],
      buffer:renderer.getDrawingBufferSize(new THREE.Vector2()).toArray()}),
    fresh:()=>{if(ambientOcclusion.stillTargets)ambientOcclusion.stillTargets.key=null;dirty=true;},
  };
'''


def frame_probe(page, enabled):
    if enabled:
        page.evaluate('''()=>{
          window.motionFrames=[];window.recordMotion=true;let last=performance.now();
          function frame(now){if(!window.recordMotion)return;window.motionFrames.push(now-last);last=now;requestAnimationFrame(frame);}
          requestAnimationFrame(frame);
        }''')
        return None
    return page.evaluate('()=>{window.recordMotion=false;return window.motionFrames.slice(3);}')


def image(page):
    data = page.locator('#skull-canvas').evaluate('e=>e.toDataURL("image/png").split(",")[1]')
    return Image.open(BytesIO(base64.b64decode(data))).convert('RGB')


def fresh_matches(page):
    before = page.evaluate('window.skullAtlas.snapshot().anatomy.hoveredRegion')
    cached = image(page)
    page.evaluate('window.motionCheck.fresh()')
    settle(page)
    fresh = image(page)
    difference = ImageChops.difference(cached, fresh)
    if difference.getbbox() is not None:
        prefix = ARTIFACTS / f'motion-pixel-failure-{page.viewport_size["width"]}'
        cached.save(str(prefix)+'-cached.png')
        fresh.save(str(prefix)+'-fresh.png')
        difference.save(str(prefix)+'-difference.png')
        (prefix.with_suffix('.json')).write_text(json.dumps({'bbox':difference.getbbox(),'extrema':difference.getextrema(),'hover_before':before,'hover_after':page.evaluate('window.skullAtlas.snapshot().anatomy.hoveredRegion'),'state':page.evaluate('window.motionCheck.state()')},indent=2))
    # Static noise and the same triangles must yield the same pixels. A cache
    # that survives a geometry/camera change fails this independent redraw.
    assert difference.getbbox() is None, 'Cached shading differs from a fresh full-quality render'


def hover(page, identity):
    button = page.locator(f'[data-landmark="{identity}"] .landmark-name')
    button.hover()
    page.wait_for_function('id=>window.skullAtlas.snapshot().anatomy.hoveredRegion===id', arg=identity)
    settle(page)
    assert page.evaluate('window.skullAtlas.snapshot().anatomy.hoveredRegion') == identity


def check(page, url, dataset, width):
    load(page, url, f'?dataset={dataset}')
    gl = page.evaluate('''()=>{const gl=document.getElementById('skull-canvas').getContext('webgl2');const ext=gl.getExtension('WEBGL_debug_renderer_info');return gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);}''')
    assert not any(name in gl.lower() for name in ['swiftshader', 'llvmpipe']), 'Hardware WebGL is required'
    original = by_id(page)
    page.evaluate('''()=>{
      const status=document.getElementById('landmark-region-status');
      window.statusHeights=[status.getBoundingClientRect().height];
      window.statusObserver=new MutationObserver(()=>window.statusHeights.push(status.getBoundingClientRect().height));
      window.statusObserver.observe(status,{childList:true});
    }''')
    before = page.evaluate('window.motionCheck.state()')
    hover(page, 'nasion')
    hover(page, 'coronal-suture')
    heights=page.evaluate('()=>{window.statusObserver.disconnect();return window.statusHeights;}')
    assert max(heights)-min(heights)<.01, 'Preparing a preview moves the list under the stationary pointer'
    after = page.evaluate('window.motionCheck.state()')
    assert after['counts']['renders'] > before['counts']['renders'], 'Hover never repainted a preview'
    assert after['counts']['depth'] == before['counts']['depth'], 'Static catalogue hover rebuilds unchanged depth shading'
    fresh_matches(page)

    page.locator('#landmark-list').evaluate('e=>e.scrollTop=0')
    page.locator('#landmark-list').scroll_into_view_if_needed()
    box = page.locator('#landmark-list').bounding_box()
    x, y = box['x'] + box['width'] * .55, box['y'] + box['height'] * .55
    page.mouse.move(x, y)
    settle(page)
    before = page.evaluate('window.motionCheck.state()')
    page.evaluate('''()=>{
      window.previewNames=new Set();window.previewObserver=new MutationObserver(()=>window.previewNames.add(document.getElementById('landmark-location-name').textContent));
      window.previewObserver.observe(document.getElementById('landmark-location-name'),{childList:true});
    }''')
    frame_probe(page, True)
    for _ in range(45):
        page.mouse.wheel(0, 34)
        page.wait_for_timeout(12)
    page.wait_for_timeout(180)
    frames = frame_probe(page, False)
    names = page.evaluate('()=>{window.previewObserver.disconnect();return [...window.previewNames];}')
    (ARTIFACTS/f'motion-{dataset}-{width}-scroll-frames.json').write_text(json.dumps({'frames':frames,'preview_names':names})+'\n')
    after = page.evaluate('window.motionCheck.state()')
    assert page.locator('#landmark-list').evaluate('e=>e.scrollTop') > 1000, 'Wheel probe did not scroll the catalogue'
    assert len(names) > 15, 'Scrolling lost the previews under the pointer'
    assert after['counts']['depth'] == before['counts']['depth'], 'Scrolling rebuilds unchanged depth shading'
    assert percentile(frames, .95) < 25 and max(frames) < 100, f'Wheel previews stutter: p95={percentile(frames,.95):.1f}, max={max(frames):.1f} ms'
    page.wait_for_function('''({x,y})=>{
      const button=document.elementFromPoint(x,y)?.closest('.landmark-name');
      return button&&window.skullAtlas.snapshot().anatomy.hoveredRegion===button.closest('[data-landmark]').dataset.landmark;
    }''', arg={'x':x,'y':y})
    scroll = {'p95_ms':percentile(frames,.95),'max_ms':max(frames),'previews':len(names)}

    page.mouse.move(5, 5)
    settle(page)
    page.locator('.more-controls').evaluate('e=>e.open=true')
    # Each mutation must invalidate shading, even when an older pose is cached.
    mutations = [('#explode','65'),('#jaw','12'),('#section','sagittal'),('#section-position','45'),('#section','none')]
    for selector, value in mutations:
        before = page.evaluate('window.motionCheck.state().counts.depth')
        if selector == '#section':
            page.locator(selector).select_option(value)
        else:
            page.locator(selector).fill(value)
        settle(page)
        assert page.evaluate('window.motionCheck.state().counts.depth') > before, f'{selector} reused obsolete shading'
        fresh_matches(page)
    checkbox = page.get_by_role('checkbox',name='Show frontal bone',exact=True)
    for visible in [False,True]:
        before = page.evaluate('window.motionCheck.state().counts.depth')
        checkbox.set_checked(visible)
        settle(page)
        assert page.evaluate('window.motionCheck.state().counts.depth') > before, 'Bone visibility reused obsolete shading'
        fresh_matches(page)
    page.locator('#jaw').fill('0')
    page.locator('#fit').click()
    settle(page)
    canvas = page.locator('#skull-canvas').bounding_box()
    cx, cy = canvas['x'] + canvas['width'] * .55, canvas['y'] + canvas['height'] * .55
    page.mouse.move(cx, cy)
    page.mouse.down(button='right')
    before = page.evaluate('window.skullAtlas.snapshot()')
    frame_probe(page, True)
    page.evaluate('''({cx,cy})=>new Promise(resolve=>{
      const canvas=document.getElementById('skull-canvas');let i=0;
      function frame(){canvas.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:1,pointerType:'mouse',buttons:2,
        clientX:cx+Math.sin(i*.05)*180,clientY:cy+Math.cos(i*.05)*80}));if(++i<100)requestAnimationFrame(frame);else resolve();}
      requestAnimationFrame(frame);
    })''', {'cx':cx,'cy':cy})
    pan_frames = frame_probe(page, False)
    (ARTIFACTS/f'motion-{dataset}-{width}-pan-frames.json').write_text(json.dumps({'frames':pan_frames})+'\n')
    moving = page.evaluate('window.motionCheck.state()')
    assert moving['moving'] and all(abs(moving['ao'][i] - moving['buffer'][i] / 2) <= 1 for i in [0,1]), 'Drag shading did not reduce its pixel work'
    page.mouse.up(button='right')
    settle(page)
    final = page.evaluate('window.skullAtlas.snapshot()')
    still = page.evaluate('window.motionCheck.state()')
    assert not still['moving'] and still['ao'] == still['buffer'], 'Full-quality shading was not restored on release'
    assert math.dist(before['target'], final['target']) > 5, 'Probe did not actually pan the exploded skull'
    assert page.locator('#bone-context-menu').is_hidden(), 'Panning opened the Hide menu'
    assert percentile(pan_frames,.95) < 25 and max(pan_frames) < 100, f'Exploded panning freezes input: p95={percentile(pan_frames,.95):.1f}, max={max(pan_frames):.1f} ms'
    fresh_matches(page)
    final_bones = by_id(page)
    for identity, bone in original.items():
        assert final_bones[identity]['triangles'] == bone['triangles']
        assert all(abs((final_bones[identity]['bounds'][1][i]-final_bones[identity]['bounds'][0][i])-(bone['bounds'][1][i]-bone['bounds'][0][i])) < 1e-5 for i in range(3)), 'Motion changed bone size'
    page.screenshot(path=str(ARTIFACTS/f'motion-{dataset}-{width}-still.png'))
    return {'dataset':dataset,'width':width,'renderer':gl,'scroll':scroll,'pan':{'p95_ms':percentile(pan_frames,.95),'max_ms':max(pan_frames)},'fresh_render_comparisons':len(mutations)+4}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--widths',type=int,nargs='+',default=[1500,3840])
    parser.add_argument('--datasets',choices=['ct'],nargs='+',default=['ct'])
    parser.add_argument('--report',type=Path,default=ARTIFACTS/'motion-responsiveness-report.json')
    args=parser.parse_args()
    report={'result':'failed','checks':[],'errors':[]}
    source=args.snapshot or ROOT/'src'
    app=(source/'app.js').read_text().replace('  window.skullAtlas = Object.freeze({',OBSERVER+'\n  window.skullAtlas = Object.freeze({')
    assert 'window.motionCheck=' in app
    try:
        with local_server() as url,sync_playwright() as pw:
            for width in args.widths:
                for dataset in args.datasets:
                    # Release each case's render/readback resources completely
                    # before the next hardware timing capture.
                    browser=pw.chromium.launch(executable_path=shutil.which('google-chrome-stable') or shutil.which('chromium'),headless=True,
                        env={k:v for k,v in os.environ.items() if k not in {'DISPLAY','WAYLAND_DISPLAY'}},
                        args=['--enable-webgl','--ignore-gpu-blocklist','--disable-software-rasterizer','--enable-features=Vulkan','--use-angle=vulkan'])
                    try:
                        report['active_case']={'dataset':dataset,'width':width}
                        page=browser.new_page(viewport={'width':width,'height':2160 if width>2000 else 1000},device_scale_factor=1)
                        page.on('pageerror',lambda error:report['errors'].append(str(error)))
                        def route_source(route):
                            name=Path(urlsplit(route.request.url).path).name
                            if name=='app.js':
                                route.fulfill(status=200,content_type='text/javascript',body=app)
                            elif args.snapshot and (source/name).exists():
                                route.fulfill(status=200,content_type='text/javascript',body=(source/name).read_text())
                            else:
                                route.continue_()
                        page.route('**/src/*.js*',route_source)
                        report['checks'].append(check(page,url,dataset,width))
                        page.close()
                    finally:
                        browser.close()
            report.pop('active_case',None)
            assert not report['errors'],report['errors']
            report['result']='passed'
    except Exception as error:
        report['failure']=str(error)
        raise
    finally:
        args.report.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
