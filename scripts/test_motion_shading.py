#!/usr/bin/env python3
"""Compare shading during real drags with an independent, fresh GTAO pass."""

import argparse
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import shutil

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_viewer import ARTIFACTS, ROOT, local_server, settle


PROBE = '''
  window.shadingProbe=()=>{
    render();
    const actual=canvas.toDataURL('image/png');
    const state={moving:ambientOcclusion.moving,size:[ambientOcclusion.width,ambientOcclusion.height],
      buffer:renderer.getDrawingBufferSize(new THREE.Vector2()).toArray(),
      depthSize:[ambientOcclusion.depthTexture.image.width,ambientOcclusion.depthTexture.image.height],
      sharedDepthSource:ambientOcclusion.stillTargets.normal.depthTexture.source===ambientOcclusion.motionTargets.normal.depthTexture.source};
    const values=ambientOcclusion.gtaoMaterial.uniforms;
    const oracle=new OracleGTAOPass(scene,camera,ambientOcclusion.width,ambientOcclusion.height,undefined,
      {radius:values.radius.value,thickness:values.thickness.value,samples:16,screenSpaceRadius:false,scale:values.scale.value});
    oracle.normalMaterial.side=THREE.DoubleSide;
    oracle.blendIntensity=ambientOcclusion.blendIntensity;
    oracle.gtaoMaterial.uniforms.tNoise.value=ambientOcclusion.gtaoMaterial.uniforms.tNoise.value;
    oracle.pdMaterial.uniforms.tNoise.value=ambientOcclusion.pdMaterial.uniforms.tNoise.value;
    const index=composer.passes.indexOf(ambientOcclusion);
    composer.passes[index]=oracle;
    try {
      composer.render();
      return {actual,expected:canvas.toDataURL('image/png'),state};
    } finally {
      composer.passes[index]=ambientOcclusion;
      oracle.dispose();oracle.blendMaterial.dispose();dirty=true;
    }
  };
'''


def png(data):
    return Image.open(BytesIO(base64.b64decode(data.split(',')[1]))).convert('RGB')


def compare(page, label, checks):
    captured=page.evaluate('window.shadingProbe()')
    actual,expected=png(captured['actual']),png(captured['expected'])
    difference=ImageChops.difference(actual,expected)
    result={'frame':label,**captured['state'],'difference_bounds':difference.getbbox(),'difference_extrema':difference.getextrema()}
    checks.append(result)
    if difference.getbbox():
        actual.save(ARTIFACTS/f'{label}-actual.png')
        expected.save(ARTIFACTS/f'{label}-expected.png')
        difference.save(ARTIFACTS/f'{label}-difference.png')
    elif label.endswith('left-0') or label.endswith('exploded-pan'):
        actual.save(ARTIFACTS/f'{label}-fixed.png')
    assert difference.getbbox() is None,f'{label}: moving shading differs from an independent fresh render'
    assert not captured['state']['sharedDepthSource'],'Full-size and motion targets share depth storage'


def check(page,url,dataset,checks):
    load(page,url,f'?dataset={dataset}')
    page.locator('#landmark-estimates').uncheck()
    page.locator('[data-view="superior"]').click()
    page.mouse.move(5,5)
    settle(page)
    for button in ['left','right']:
        rect=page.locator('#skull-canvas').bounding_box()
        cx,cy=rect['x']+rect['width']*.55,rect['y']+rect['height']*.55
        before=page.evaluate('window.skullAtlas.snapshot()')
        page.mouse.move(cx,cy)
        page.mouse.down(button=button)
        for i,(dx,dy) in enumerate([(45,25),(-70,55),(110,-50)]):
            page.mouse.move(cx+dx,cy+dy,steps=4)
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            compare(page,f'rotation-{dataset}-{button}-{i}',checks)
            assert checks[-1]['moving']
            assert all(abs(checks[-1]['size'][i]-checks[-1]['buffer'][i]/2)<=1 for i in [0,1])
        after=page.evaluate('window.skullAtlas.snapshot()')
        assert before['camera']!=after['camera'],'The drag did not move the camera'
        page.mouse.up(button=button)
        page.mouse.move(5,5)
        settle(page)
        compare(page,f'rotation-{dataset}-{button}-released',checks)
        assert not checks[-1]['moving'] and checks[-1]['size']==checks[-1]['buffer']
    page.locator('#explode').fill('65')
    page.locator('#fit').click()
    page.mouse.move(5,5)
    settle(page)
    rect=page.locator('#skull-canvas').bounding_box()
    cx,cy=rect['x']+rect['width']*.55,rect['y']+rect['height']*.55
    page.mouse.move(cx,cy)
    page.mouse.down(button='right')
    page.mouse.move(cx+80,cy+40,steps=6)
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    compare(page,f'rotation-{dataset}-exploded-pan',checks)
    page.mouse.up(button='right')
    page.mouse.move(5,5)
    settle(page)
    compare(page,f'rotation-{dataset}-exploded-released',checks)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--datasets',nargs='+',choices=['ct'],default=['ct'])
    parser.add_argument('--width',type=int,default=1100)
    parser.add_argument('--height',type=int,default=1000)
    parser.add_argument('--dpr',type=float,default=1)
    parser.add_argument('--report',type=Path,default=ARTIFACTS/'motion-shading-report.json')
    args=parser.parse_args()
    report={'result':'failed','frames':[],'errors':[],'viewport':[args.width,args.height],'dpr':args.dpr}
    app=(ROOT/'src/app.js').read_text()
    app="import { GTAOPass as OracleGTAOPass } from 'three/addons/postprocessing/GTAOPass.js';\n"+app
    app=app.replace('  window.skullAtlas = Object.freeze({',PROBE+'\n  window.skullAtlas = Object.freeze({')
    try:
        with local_server() as url,sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('google-chrome-stable') or shutil.which('chromium'),headless=True,
                env={k:v for k,v in os.environ.items() if k not in {'DISPLAY','WAYLAND_DISPLAY'}},
                args=['--enable-webgl','--ignore-gpu-blocklist','--disable-software-rasterizer','--enable-features=Vulkan','--use-angle=vulkan'])
            try:
                page=browser.new_page(viewport={'width':args.width,'height':args.height},device_scale_factor=args.dpr)
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.route('**/src/app.js*',lambda route:route.fulfill(status=200,content_type='text/javascript',body=app))
                if args.snapshot:
                    preserved=(args.snapshot/'bone-occlusion-pass.js').read_text()
                    page.route('**/src/bone-occlusion-pass.js*',lambda route:route.fulfill(status=200,content_type='text/javascript',body=preserved))
                for dataset in args.datasets:
                    check(page,url,dataset,report['frames'])
                assert not report['errors'],report['errors']
                report['result']='passed'
            finally:
                browser.close()
    except Exception as error:
        report['failure']=str(error)
        raise
    finally:
        args.report.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
