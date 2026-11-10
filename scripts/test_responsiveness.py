#!/usr/bin/env python3
"""Measure real catalogue input/rotation and verify worker geometry equivalence."""

import argparse
import json
import math
import os
import shutil

from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_viewer import ARTIFACTS, ROOT, QuietHandler, local_server


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, math.ceil(len(values) * fraction) - 1)]


def hover(page, identity, name):
    handler = page.evaluate('''id => {
      window.hoverFrames = []; window.hoverTick = true;
      let last = performance.now();
      function frame(now) { window.hoverFrames.push(now-last); last=now; if(window.hoverTick) requestAnimationFrame(frame); }
      requestAnimationFrame(frame);
      const start=performance.now();
      document.querySelector(`[data-landmark="${id}"] .landmark-name`).dispatchEvent(new PointerEvent('pointerenter'));
      return performance.now()-start;
    }''', identity)
    assert handler < 16, f'{identity}: catalogue handler blocks input for {handler:.1f} ms'
    page.wait_for_function('''name => document.getElementById('landmark-location-name').textContent===name
      && /Estimated surface|Supplied surface|Fitted point/.test(document.getElementById('landmark-location-status').textContent)''', arg=name, timeout=5000)
    timing = page.evaluate('''() => {window.hoverTick=false;return window.hoverFrames;}''')
    assert timing and max(timing) < 100, f'{identity}: first highlight freezes the frame loop: {max(timing):.1f} ms'
    # The inspector only reads this cached, completed result.
    assert page.evaluate('id=>window.skullAtlas.regionGeometry(id)?.parts.some(p=>p.rendered)', identity), 'The responsive handler never drew its highlight'
    page.evaluate('id=>document.querySelector(`[data-landmark="${id}"] .landmark-name`).dispatchEvent(new PointerEvent("pointerleave"))', identity)
    return {'id': identity, 'handler_ms': handler, 'largest_frame_gap_ms': max(timing)}


def rotate(page):
    rect = page.locator('#skull-canvas').bounding_box()
    cx, cy = rect['x'] + rect['width'] * .55, rect['y'] + rect['height'] * .5
    before = page.evaluate('window.skullAtlas.snapshot().camera')
    page.mouse.move(cx, cy)
    page.mouse.down()
    frames = page.evaluate('''({cx,cy}) => new Promise(resolve => {
      const canvas=document.getElementById('skull-canvas'), frames=[];
      let last=performance.now(), i=0;
      function frame(now) {
        frames.push(now-last); last=now;
        canvas.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:1,pointerType:'mouse',buttons:1,
          clientX:cx+Math.sin(i*.04)*160,clientY:cy+Math.cos(i*.04)*80}));
        if(++i<100) requestAnimationFrame(frame); else resolve(frames.slice(2));
      }
      requestAnimationFrame(frame);
    })''', {'cx': cx, 'cy': cy})
    page.mouse.up()
    after = page.evaluate('window.skullAtlas.snapshot().camera')
    displacement = math.dist(before, after)
    assert displacement > 5, 'The frame probe did not actually rotate the skull'
    assert percentile(frames, .95) < 33.5, f'Rotation p95 exceeds two 60 Hz frames: {percentile(frames,.95):.1f} ms'
    assert max(frames) < 100, f'Rotation freezes for {max(frames):.1f} ms'
    return {'median_ms': percentile(frames,.5), 'p95_ms': percentile(frames,.95), 'maximum_ms': max(frames), 'camera_displacement': displacement}


def move_pointer(page):
    page.locator('[data-view="anterior"]').click()
    point = page.evaluate('window.skullAtlas.project("frontal-bone")')
    before = page.evaluate('window.skullAtlas.snapshot().camera')
    timing = page.evaluate('''point => new Promise(resolve => {
      const canvas=document.getElementById('skull-canvas'), frames=[];
      let last=performance.now(), i=0, shown=0;
      function frame(now) {
        frames.push(now-last); last=now;
        if(!document.getElementById('hover-label').hidden) shown++;
        canvas.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:1,pointerType:'mouse',buttons:0,
          clientX:point.x+Math.sin(i*.1)*24,clientY:point.y+Math.cos(i*.1)*12}));
        if(++i<100) requestAnimationFrame(frame); else resolve({frames:frames.slice(2),shown});
      }
      requestAnimationFrame(frame);
    })''',point)
    assert timing['shown'] > 25, 'The hover probe did not actually identify anatomy'
    assert math.dist(before,page.evaluate('window.skullAtlas.snapshot().camera')) < .0001, 'The hover probe unexpectedly rotated the skull'
    frames=timing['frames']
    assert percentile(frames,.95) < 33.5, f'Pointer picking p95 exceeds two frames: {percentile(frames,.95):.1f} ms'
    assert max(frames) < 100, f'Pointer picking freezes for {max(frames):.1f} ms'
    return {'p95_ms':percentile(frames,.95),'maximum_ms':max(frames),'frames_with_tooltip':timing['shown']}


def check(page, url, width):
    load(page, url)
    assert page.workers and any(w.url.split('?', 1)[0].endswith('/src/surface-region-worker.js') for w in page.workers), 'No background geometry worker'
    renderer = page.evaluate('''() => {const gl=document.getElementById('skull-canvas').getContext('webgl2');
      const ext=gl.getExtension('WEBGL_debug_renderer_info');return gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);}''')
    assert not any(name in renderer.lower() for name in ['swiftshader','llvmpipe']), 'The performance gate requires hardware WebGL'
    models = json.loads((ROOT/'assets/skull-ct/manifest.json').read_text())
    before = page.evaluate('window.skullAtlas.snapshot()')
    assert sum(e['triangles'] for e in before['structures'] if e['id'] in {m['id'] for m in models['structures']}) == sum(e['triangles'] for e in models['structures']), 'Performance changed native mesh detail'
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator('#landmark-panel').evaluate('e=>e.open=true')
    page.locator('#landmark-estimates').check()
    hovers = [hover(page, identity, name) for identity,name in [('coronal-suture','Coronal suture'),('left-greater-wing','Left greater wing')]]
    # Leave another expensive, cold contact fit running while mouse input rotates
    # the complete skull. The user thread must stay responsive during this work.
    page.evaluate('''() => document.querySelector('[data-landmark="lambdoid-suture"] .landmark-name').dispatchEvent(new PointerEvent('pointerenter'))''')
    background = rotate(page)
    evidence = json.loads((ROOT/'assets/landmark-regions/evidence.json').read_text())
    records = evidence['regions']
    guides = [entry['id'] for entry in records if entry.get('datasets',{}).get('ct',{}).get('kind') == 'location-guide'][:20]
    assert len(guides) == 20
    page.mouse.move(5,5)
    page.evaluate('''ids => {for(const id of ids){const box=document.querySelector(`[data-landmark="${id}"] input`);
      box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}));}}''', guides)
    page.locator('#landmark-labels').check()
    labelled = rotate(page)
    assert page.evaluate('window.skullAtlas.snapshot().anatomy.enabledRegions.length') >= 20
    pointing=move_pointer(page)
    return {'width':width,'renderer':renderer,'cold_hovers':hovers,'rotation_during_build':background,'rotation_with_20_labels':labelled,'broad_catalogue_pointer_hover':pointing}


PARITY_HOOK = '''window.regionWorkerParity = async id => {
  const entry=details.landmarks.get(id);
  const [packed,duplicate]=await Promise.race([Promise.all([details.regions.prepare(entry),details.regions.prepare(entry)]),
    new Promise((_,reject)=>setTimeout(()=>reject(new Error('Concurrent highlight requests did not finish')),5000))]);
  details.regions.cache.delete(id);
  const local=details.regions.build(entry);
  const same=(a,b)=>a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
  const equal=packed===duplicate&&packed.area===local.area&&JSON.stringify(packed.methods)===JSON.stringify(local.methods)
    &&packed.parts.length===local.parts.length&&packed.parts.every((p,i)=>{
      const q=local.parts[i];return p.ownerId===q.ownerId&&p.anchorFace===q.anchorFace&&same(p.anchor.toArray(),q.anchor.toArray())
        &&same(p.geometry.attributes.position.array,q.geometry.attributes.position.array)
        &&same(p.geometry.attributes.normal.array,q.geometry.attributes.normal.array)&&same([...p.sourceFaces],q.sourceFaces);
    });
  for(const p of local.parts){scene.remove(p.mesh);p.geometry.dispose();p.mesh.material.dispose();p.ghost.material.dispose();}
  details.regions.cache.set(id,packed);
  return {equal,parts:packed.parts.length,triangles:packed.parts.reduce((n,p)=>n+p.sourceFaces.length,0)};
};'''


def parity(page, url):
    app=(ROOT/'src/app.js').read_text()
    page.route('**/src/app.js*', lambda route:route.fulfill(status=200,content_type='text/javascript',body=app.replace('  window.skullAtlas = Object.freeze({',PARITY_HOOK+'\n  window.skullAtlas = Object.freeze({')))
    checked=[]
    for dataset in ['ct']:
        load(page,url,f'?dataset={dataset}')
        for identity in ['coronal-suture','bregma','nasion','left-pterygopalatine-fossa','left-greater-wing','left-zygomatic-arch'] + (['left-mental-foramen'] if dataset=='ct' else ['left-occipital-condyle']):
            result=page.evaluate('id=>window.regionWorkerParity(id)',identity)
            assert result['equal'] and result['parts'] and result['triangles'], f'Worker changed anatomy/normal/face identity: {dataset}:{identity}'
            checked.append({'dataset':dataset,'id':identity,**result})
    return checked


def pending_state_checks(page):
    page.locator("#landmark-panel").evaluate("e=>e.open=true")
    page.locator('#landmark-panel').evaluate('e=>e.open=true')
    page.locator('#landmark-estimates').check()
    page.evaluate('''() => {
      document.querySelector('[data-landmark="lambdoid-suture"] .landmark-name').dispatchEvent(new PointerEvent('pointerenter'));
      const box=document.getElementById('landmark-estimates');box.checked=false;box.dispatchEvent(new Event('change'));
    }''')
    page.wait_for_function('!window.skullAtlas.snapshot().anatomy.preparingRegions',polling=100)
    state=page.evaluate('window.skullAtlas.snapshot().anatomy')
    assert not state['includeEstimates'] and not state['hoveredRegion'] and not any(r['rendered'] or r['xrayRendered'] for r in state['regions']), 'A late response restored disabled estimates'
    page.locator('#landmark-estimates').check()
    page.evaluate('''() => {
      document.querySelector('[data-landmark="sagittal-suture"] .landmark-name').dispatchEvent(new PointerEvent('pointerenter'));
      document.getElementById('reset').click();
    }''')
    page.wait_for_function('!window.skullAtlas.snapshot().anatomy.preparingRegions',polling=100)
    state=page.evaluate('window.skullAtlas.snapshot().anatomy')
    assert state['includeEstimates'] and not state['enabledRegions'] and not any(r['rendered'] or r['xrayRendered'] for r in state['regions']), 'A late response survived reset'
    page.locator('#landmark-estimates').check()
    page.evaluate('''() => {
      for(const id of ['right-greater-wing','right-pterygopalatine-fossa'])
        document.querySelector(`[data-landmark="${id}"] .landmark-name`).dispatchEvent(new PointerEvent('pointerenter'));
    }''')
    page.wait_for_function('''() => document.getElementById('landmark-location-name').textContent==='Right pterygopalatine fossa'
      &&document.getElementById('landmark-location-status').textContent.includes('Estimated surface')''')
    state=page.evaluate('window.skullAtlas.snapshot().anatomy')
    assert state['hoveredRegion']=='right-pterygopalatine-fossa' and not state['enabledRegions'], 'An obsolete hover response changed the active landmark'
    page.evaluate('''() => document.querySelector('[data-landmark="right-pterygopalatine-fossa"] .landmark-name').dispatchEvent(new PointerEvent('pointerleave'))''')
    before=page.evaluate('window.skullAtlas.snapshot().camera')
    page.evaluate('''() => {
      document.querySelector('[data-landmark="right-mental-foramen"] .landmark-name').click();
      document.getElementById('anatomy-focus').click();
      document.querySelector('[data-landmark="bregma"] .landmark-name').click();
    }''')
    page.wait_for_function('!window.skullAtlas.snapshot().anatomy.preparingRegions',polling=100)
    page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    state=page.evaluate('window.skullAtlas.snapshot()')
    assert state['anatomy']['selected']['id']=='bregma' and math.dist(before,state['camera'])<.0001, 'A stale Focus completion moved the camera'
    return ['Pending responses respect estimate opt-out and reset','Latest hover wins without enabling regions','Concurrent requests share a result; stale Focus cannot move the camera']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutant',choices=['main-build'])
    parser.add_argument('--report',type=lambda v:ARTIFACTS/v,default=ARTIFACTS/'responsiveness-report.json')
    args=parser.parse_args()
    report={'result':'failed','cases':[],'parity':[],'errors':[],'mutant':args.mutant}
    chrome=shutil.which('google-chrome-stable') or shutil.which('chromium')
    try:
        with local_server() as url,sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=chrome,headless=True,
              env={k:v for k,v in os.environ.items() if k not in {'DISPLAY','WAYLAND_DISPLAY'}},
              args=['--enable-webgl','--ignore-gpu-blocklist','--disable-software-rasterizer','--enable-features=Vulkan','--use-angle=vulkan'])
            try:
                for width,height in [(1500,1000),(3840,2160)]:
                    page=browser.new_page(viewport={'width':width,'height':height})
                    page.add_init_script('globalThis.__SKULL_BUILD_HIGHLIGHTS__ = true;')
                    page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    if args.mutant:
                        original=(ROOT/'src/anatomy-details.js').read_text()
                        old='    this.describeHover(entry);\n    if (entry) this.prepareQueue();'
                        changed=original.replace(old,'    this.describeHover(entry);\n    if (entry) this.regions.build(entry);\n    if (entry) this.prepareQueue();')
                        assert changed!=original
                        page.route('**/src/anatomy-details.js*',lambda route:route.fulfill(status=200,content_type='text/javascript',body=changed))
                    report['cases'].append(check(page,url,width));page.close()
                page=browser.new_page(viewport={'width':1500,'height':1000})
                page.add_init_script('globalThis.__SKULL_BUILD_HIGHLIGHTS__ = true;')
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                report['parity']=parity(page,url)
                report['pending_state_checks']=pending_state_checks(page)
                assert not report['errors'],report['errors']
                report['result']='passed'
            finally:
                browser.close()
    except Exception as error:
        report['failure']=str(error)
        raise
    finally:
        args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
