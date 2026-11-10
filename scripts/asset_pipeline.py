"""Shared file identities for generated highlights and HTTP asset delivery."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPRESSION_MANIFEST = ROOT / 'viewer-assets.compression.json'
HIGHLIGHT_MANIFEST = ROOT / 'assets/landmark-highlights/manifest.json'
# These fitting inputs are retained locally; their prepared surfaces and source
# identities are shipped in the highlight package instead of duplicating them.
HIGHLIGHT_BUILD_ONLY = (
    'assets/landmark-regions/registered-regions.json',
    'assets/landmark-regions/registered-regions.bin',
)
HIGHLIGHT_INPUTS = (
    'assets/skull-ct/manifest.json', 'assets/skull-ct/skull.glb', 'assets/skull-ct/surface.glb',
    'assets/skull-associated/manifest.json', 'assets/skull-associated/bones.glb',
    'assets/landmarks/catalogue.json',
    'assets/landmark-regions/source-regions.json', 'assets/landmark-regions/source-regions.bin',
    'assets/landmark-regions/apertures.json', 'assets/landmark-regions/apertures.bin',
    'assets/landmark-regions/registered-regions.json', 'assets/landmark-regions/registered-regions.bin',
    'src/app.js', 'src/anatomy-details.js', 'src/surface-regions.js',
    'src/surface-region-worker.js', 'src/highlight-batches.js', 'src/asset-url.js',
    'vendor/build/three.module.js', 'vendor/three-mesh-bvh/index.module.js',
    'scripts/precompute_highlights.py', 'scripts/asset_pipeline.py',
)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def highlight_identities():
    return {name: digest(ROOT / name) for name in HIGHLIGHT_INPUTS}


def public_files():
    paths = [ROOT / 'index.html', ROOT / 'styles.css']
    for folder in ('assets', 'src', 'vendor'):
        paths.extend(path for path in (ROOT / folder).rglob('*') if path.is_file()
                     and path.suffix not in {'.zst', '.br', '.gz', '.partial'})
    return sorted(paths)
