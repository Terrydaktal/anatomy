#!/usr/bin/env python3
"""Verify the staged/released viewer outside this working tree and source caches."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from asset_pipeline import ROOT, digest
from test_precomputed_highlights import launch, ready
from test_viewer import ARTIFACTS, settle, snapshot
from viewer_resources import MANIFEST, RESOURCE_PATHS, package, prepare, resources, write_json


def cache_checks(report):
    with tempfile.TemporaryDirectory(prefix='viewer-cache-test-') as temporary:
        root = Path(temporary)
        expected = {name: f'original resource: {name}'.encode() for name in RESOURCE_PATHS}
        for name, data in expected.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        package(root)
        for name in expected:
            (root / name).unlink()
        assert set(prepare(root)) == set(expected)
        assert all((root / name).read_bytes() == data for name, data in expected.items())
        mtimes = {name: (root / name).stat().st_mtime_ns for name in expected}
        assert prepare(root) == []
        assert mtimes == {name: (root / name).stat().st_mtime_ns for name in expected}

        changed, custom, damaged = RESOURCE_PATHS[:3]
        (root / changed).write_bytes(b'updated resource after a pull')
        package(root)
        (root / changed).write_bytes(expected[changed])  # The consumer still has the previous extracted version.
        assert prepare(root) == [changed]
        assert (root / changed).read_bytes() == b'updated resource after a pull'

        (root / custom).write_bytes(b'local edits must survive')
        try:
            prepare(root)
        except ValueError as error:
            assert 'Local changes' in str(error)
        else:
            raise AssertionError('Local custom geometry was overwritten.')
        assert (root / custom).read_bytes() == b'local edits must survive'
        (root / custom).write_bytes(expected[custom])

        archive = root / (damaged + '.zst')
        original_archive = archive.read_bytes()
        (root / damaged).unlink()
        archive.write_bytes(original_archive[:-3])
        try:
            prepare(root)
        except ValueError as error:
            assert 'Damaged viewer archive' in str(error)
        else:
            raise AssertionError('A damaged archive was accepted.')
        assert not (root / damaged).exists()
        archive.write_bytes(original_archive)
        manifest = json.loads((root / MANIFEST).read_text())
        next(record for record in manifest['resources'] if record['path'] == damaged)['sha256'] = '0' * 64
        write_json(root / MANIFEST, manifest)
        try:
            prepare(root)
        except ValueError as error:
            assert 'checksum differs' in str(error)
        else:
            raise AssertionError('Expanded bytes with the wrong checksum were published.')
        assert not (root / damaged).exists() and not list(root.rglob('*.partial'))
        report['cache_checks'] = ['first extraction', 'idempotent restart', 'update replaces old managed copies',
                                  'local changes preserved', 'damaged archive rejected', 'wrong expanded bytes rejected atomically']


@contextmanager
def staged_checkout(ref):
    with tempfile.TemporaryDirectory(prefix='portable viewer ', dir=ARTIFACTS) as temporary:
        base = Path(temporary)
        archive = base / 'release.tar'
        with archive.open('wb') as output:
            subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', ref], check=True, stdout=output)
        checkout = base / 'checkout with spaces'
        checkout.mkdir()
        with tarfile.open(archive) as source:
            source.extractall(checkout, filter='data')
        yield checkout, base


@contextmanager
def server(checkout, outside, log_path):
    command = [shutil.which('uv'), 'run', '--project', str(checkout), 'python', str(checkout / 'scripts/serve_viewer.py'), '--port', '0']
    with log_path.open('wb') as output:
        process = subprocess.Popen(command, cwd=outside, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                log = log_path.read_text(errors='replace')
                match = re.search(r'Skull viewer: (http://127\.0\.0\.1:\d+)', log)
                if match:
                    yield match[1]
                    return
                if process.poll() is not None:
                    raise AssertionError(f'Clean-checkout launch failed: {log[-4000:]}')
                time.sleep(0.1)
            raise AssertionError(f'Clean-checkout server did not become ready: {log[-4000:]}')
        finally:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)


def browser_checks(p, url, report):
    browser = launch(p)
    try:
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errors, requests, encodings = [], set(), {}
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.add(request.url))
        page.on('response', lambda response: encodings.update({urlsplit(response.url).path: response.headers.get('content-encoding')}))
        ready(page, url)
        initial = snapshot(page)
        report['initial'] = {'landmarks': initial['anatomy']['landmarkCount'], 'precomputed': initial['anatomy']['precomputed'],
                             'cached_regions': len(initial['anatomy']['regions']), 'structures': len(initial['structures'])}
        assert initial['anatomy']['precomputed'], 'A clean checkout lost the supplied prepared highlights.'
        assert len(initial['structures']) == 31 and initial['anatomy']['landmarkCount'] == 735
        assert not page.workers and initial['anatomy']['batching']['enabled']
        page.locator('#landmark-pins').click()
        settle(page)
        current = snapshot(page)
        painted = sum(record['rendered'] for record in current['anatomy']['regions'])
        assert painted == 150, f'Clean checkout painted {painted} of 150 supported landmarks.'
        assert not current['anatomy']['preparingRegions'] and current['drawCalls'] < 80
        report['painted_regions'] = painted
        report['draw_calls'] = current['drawCalls']
        page.screenshot(path=str(ARTIFACTS / 'portable-viewer-all.png'))
        for path in ('/assets/skull-ct/surface.glb', '/assets/landmark-highlights/highlights.bin', '/assets/skull-cavities/cavities.glb'):
            assert encodings.get(path) == 'zstd', f'Packaged resource was not served compressed: {path}'
        assert not any('/registered-regions.' in request for request in requests)
        assert all(urlsplit(request).hostname == '127.0.0.1' for request in requests if request.startswith(('http:', 'https:')))
        page.locator('#reset').click()
        page.locator('#landmark-search').fill('Right pterygopalatine fossa')
        page.get_by_role('button', name='Right pterygopalatine fossa', exact=True).click()
        settle(page)
        selected = snapshot(page)
        fossa = next(record for record in selected['anatomy']['regions'] if record['id'] == selected['anatomy']['selected']['id'])
        assert fossa['rendered'] and len(fossa['owners']) == 3, 'The mapped fossa lost one of its three bone walls.'
        page.locator('#dataset').select_option('microct')
        page.wait_for_function("document.documentElement.dataset.ready === 'true' && window.skullAtlas?.snapshot().dataset === 'microct'", timeout=60000)
        microct = snapshot(page)
        assert len(microct['structures']) == 1 and microct['anatomy']['landmarkCount'] == 3
        assert not errors, errors
        report['browser_checks'] = ['all 735 entries and 150 prepared surfaces', 'three-wall pterygopalatine fossa',
                                    '31 loaded structures', 'Zstd delivery without a generated compression manifest',
                                    'no fitting source downloads', 'no external anatomical requests', 'micro-CT fragment']
    finally:
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Git commit/tree to test; defaults to the staged index.')
    parser.add_argument('--report', default='portable-viewer-report.json')
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    ref = args.ref or subprocess.check_output(['git', '-C', str(ROOT), 'write-tree'], text=True).strip()
    report = {'result': 'failed', 'tree': ref, 'environment': 'Linux, headless Chrome, NVIDIA ANGLE Vulkan', 'errors': []}
    try:
        cache_checks(report)
        with staged_checkout(ref) as (checkout, outside):
            assert not (checkout / 'data/source').exists() and not (checkout / '.venv').exists()
            assert not (checkout / 'assets/landmark-regions/registered-regions.bin').exists()
            assert not (checkout / 'assets/landmark-highlights/highlights.bin').exists()
            tracked = {str(path.relative_to(checkout)): digest(path) for path in checkout.rglob('*') if path.is_file()}
            with server(checkout, outside, ARTIFACTS / 'portable-viewer-server.log') as url, sync_playwright() as p:
                browser_checks(p, url, report)
            for record in resources(checkout):
                assert digest(checkout / record['path']) == record['sha256']
            assert all(digest(checkout / name) == checksum for name, checksum in tracked.items()), 'Starting the viewer modified a versioned file.'
            with server(checkout, outside, ARTIFACTS / 'portable-viewer-restart.log') as url:
                assert 'Restored ' not in (ARTIFACTS / 'portable-viewer-restart.log').read_text()
            subprocess.run(['git', '-C', str(checkout), 'init', '--initial-branch=main', '--quiet'], check=True)
            for name in (*RESOURCE_PATHS, '.venv', '.cache/viewer-resources.json'):
                result = subprocess.run(['git', '-C', str(checkout), 'check-ignore', '--quiet', name])
                assert result.returncode == 0, f'Generated state would obstruct a pull: {name}'
            report['tracked_files_unchanged'] = len(tracked)
            report['restart'] = 'reused all native resources without rewriting them'
        report['result'] = 'passed'
    except Exception as error:
        report['errors'].append(str(error))
    (ARTIFACTS / args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(report['result'] != 'passed')


if __name__ == '__main__':
    main()
