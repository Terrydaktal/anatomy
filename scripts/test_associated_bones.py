#!/usr/bin/env python3
"""Independently check imported-bone transforms, unchanged native assets and faces."""

import hashlib
import json
import math
import os
import shutil

from playwright.sync_api import sync_playwright

from test_anatomy import load
from test_regions import SourceTriangles, assert_on_triangle, select_feature
from test_viewer import ARTIFACTS, ROOT, by_id, local_server, settle


def moved(point, matrix):
    return [sum(matrix[row][column] * point[column] for column in range(3)) + matrix[row][3] for row in range(3)]


def main():
    report = {'result': 'failed', 'checks': [], 'errors': []}
    try:
        associated = json.loads((ROOT / 'assets/skull-associated/manifest.json').read_text())
        reference = json.loads((ROOT / 'assets/skull/manifest.json').read_text())
        native = json.loads((ROOT / 'assets/skull-ct/manifest.json').read_text())
        assert hashlib.sha256((ROOT / native['model']).read_bytes()).hexdigest() == associated['target_model_sha256'] == native['sha256']
        assert hashlib.sha256((ROOT / associated['model']).read_bytes()).hexdigest() == associated['sha256']
        assert associated['model'] == 'assets/skull-associated/bones.glb'
        assert (ROOT / associated['model']).stat().st_size < (ROOT / reference['model']).stat().st_size / 5
        assert len(associated['structures']) == 7
        for entry in associated['structures']:
            matrix = entry['reference_transform']
            columns = [[matrix[r][c] for r in range(3)] for c in range(3)]
            lengths = [math.sqrt(sum(x*x for x in v)) for v in columns]
            assert max(lengths) - min(lengths) < 1e-8, 'Imported bone has anisotropic scaling'
            assert all(abs(sum(a*b for a,b in zip(columns[i], columns[j]))) < 1e-8 for i,j in [(0,1),(0,2),(1,2)]), 'Imported bone was sheared'
            assert .6 < lengths[0] < 1.2, 'Implausible atlas/display-unit conversion'
            assert entry['placement_estimated'] and entry['source_model_sha256'] == reference['sha256']
        for side in ['left','right']:
            chain = [e for e in associated['structures'] if e['id'].endswith('-'+side)]
            assert len(chain) == 3 and all(e['reference_transform'] == chain[0]['reference_transform'] for e in chain), 'Chain relationships were changed independently'
        report['checks'].append('Native skull hashes preserved; seven reference bones use uniform transforms, intact chain relationships and explicit estimated-placement provenance')
        source = SourceTriangles('reference')
        with local_server() as url, sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('ANATOMY_CHROME_PATH') or shutil.which('google-chrome-stable') or shutil.which('chromium'),headless=True,
                env={k:v for k,v in os.environ.items() if k not in {'DISPLAY','WAYLAND_DISPLAY'}},args=['--enable-webgl','--ignore-gpu-blocklist','--disable-software-rasterizer','--enable-features=Vulkan','--use-angle=vulkan'])
            try:
                page=browser.new_page(viewport={'width':1500,'height':1000})
                page.on('pageerror',lambda e:report['errors'].append(str(e)))
                for dataset in ['ct']:
                    load(page,url,f'?dataset={dataset}')
                    bones=by_id(page)
                    records=associated['structures'] if dataset=='ct' else [e for e in reference['structures'] if e['group'] in ['ossicles','associated']]
                    for entry in records:
                        actual=bones[entry['id']]
                        assert actual['triangles']==entry['triangles']
                        assert max(abs(a-b) for row,expected in zip(actual['bounds'],entry['bounds']) for a,b in zip(row,expected)) < .001
                    for name in ['Left base of stapes','Right base of stapes']:
                        identity=select_feature(page,name)
                        audit=page.evaluate('id=>window.skullAtlas.regionGeometry(id)',identity)
                        assert audit['methods']==['source-authored-faces'] and audit['parts']
                        for part in audit['parts']:
                            record=next(e for e in records if e['id']==part['owner'])
                            matrix=record.get('reference_transform',[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
                            for i,face in enumerate(part['sourceFaces']):
                                triangle=[moved(v,matrix) for v in source.triangle(part['owner'],face)]
                                for j in range(3):
                                    assert_on_triangle(part['positions'][i*9+j*3:i*9+j*3+3],triangle)
                    report['checks'].append(f'{dataset}: all seven bones retain triangles and transformed source bounds; both footplate highlights retain exact source face identities on the actual placed bones')
                assert not report['errors'],report['errors']
            finally: browser.close()
        report['result']='passed'
    except Exception as error:
        report['failure']=str(error)
        raise
    finally:
        (ARTIFACTS/'associated-bones-report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
