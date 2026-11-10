import * as THREE from '../vendor/build/three.module.js';
import { assetUrl, fetchAsset } from './asset-url.js';
import { HighlightBatches } from './highlight-batches.js';

// Region polygons are clipped from the loaded bone triangles. Drawing and
// hit-testing share those polygons; no sphere, decal or displaced shell is used.
const EPSILON = 1e-7;
function regionMaterials(colour, roughness) {
  return [new THREE.MeshPhysicalMaterial({
    color: colour, emissive: colour, emissiveIntensity: 0.1, side: THREE.DoubleSide,
    roughness, metalness: 0,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  }), new THREE.MeshBasicMaterial({
    color: colour, side: THREE.DoubleSide, transparent: true, opacity: 0.5,
    depthWrite: false, depthFunc: THREE.GreaterDepth,
    // All walls of the active preview share one stencil bit. Once a hidden
    // sample is tinted, deeper faces cannot blend the colour into it again.
    stencilWrite: true, stencilWriteMask: 1, stencilFuncMask: 1,
    stencilRef: 1, stencilFunc: THREE.NotEqualStencilFunc,
    stencilFail: THREE.KeepStencilOp, stencilZFail: THREE.KeepStencilOp,
    stencilZPass: THREE.ReplaceStencilOp, forceSinglePass: true,
    polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4,
  })];
}
const axes = [];
for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
  if (x || y || z) axes.push(new THREE.Vector3(x, y, z).normalize());
}

export function regionColour(id) {
  const layers = { 'outer-cortical-table': '#65b8e5', diploe: '#e6b75e', 'inner-cortical-table': '#c28de1' };
  if (layers[id]) return layers[id];
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
  return `#${new THREE.Color().setHSL((hash % 3600) / 3600, 0.67, 0.6, THREE.SRGBColorSpace).getHexString()}`;
}

function surfaceSeed(entry, owner, dataset) {
  const geometry = owner.mesh.geometry;
  const guide = new THREE.Vector3(...(entry.positions?.[dataset]?.point ?? entry.placement.point));
  const nearest = geometry.boundsTree.closestPointToPoint(guide);
  if (!nearest || dataset === 'microct' || /cranial fossa|hypophyseal|internal surface|inner surface|orbital plate|orbital part|cribriform/.test(entry.name.toLowerCase())) return nearest;
  const normal = new THREE.Vector3(...({ anterior: [0, 0, 1], posterior: [0, 0, -1], left: [1, 0, 0], right: [-1, 0, 0], superior: [0, 1, 0], inferior: [0, -1, 0] }[entry.view] ?? [0, 0, 1]));
  const span = geometry.boundingBox.getSize(new THREE.Vector3()).length();
  const ray = new THREE.Ray(guide.clone().addScaledVector(normal, span + 1), normal.clone().negate());
  const hit = geometry.boundsTree.raycastFirst(ray, THREE.DoubleSide);
  // Fit an externally viewed feature to the first actual surface in its named
  // anatomical view. A nearby inner cortical wall must not become its region.
  return hit && hit.point.distanceTo(guide) < Math.max(8, span * 0.25) ? { point: hit.point, faceIndex: hit.faceIndex } : nearest;
}

function clip(polygon, evaluate) {
  if (!polygon.length) return polygon;
  const result = [];
  let previous = polygon.at(-1), before = evaluate(previous);
  for (const current of polygon) {
    const after = evaluate(current);
    if ((after >= 0) !== (before >= 0)) {
      const t = before / (before - after);
      result.push(previous.map((value, index) => value + (current[index] - value) * t));
    }
    if (after >= 0) result.push(current);
    previous = current; before = after;
  }
  return result;
}

function vertex(geometry, id) {
  const p = geometry.attributes.position, n = geometry.attributes.normal;
  return [p.getX(id), p.getY(id), p.getZ(id), n.getX(id), n.getY(id), n.getZ(id)];
}

function pointKey(point) { return point.slice(0, 3).map((value) => Math.round(value * 1e5)).join(','); }

function edgeKey(first, second) {
  const a = pointKey(first), b = pointKey(second);
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

function connected(polygons, point, seedFace) {
  if (!polygons.size) return polygons;
  const edges = new Map(), neighbours = new Map();
  for (const [face, record] of polygons) {
    neighbours.set(face, new Set());
    for (const [a, b] of record.edges) {
      const key = edgeKey(a, b);
      if (edges.has(key)) {
        const other = edges.get(key);
        neighbours.get(face).add(other); neighbours.get(other).add(face);
      } else edges.set(key, face);
    }
  }
  if (!polygons.has(seedFace)) {
    let distance = Infinity;
    const closest = new THREE.Vector3();
    for (const [face, { polygon }] of polygons) {
      for (let j = 1; j + 1 < polygon.length; j++) {
        new THREE.Triangle(new THREE.Vector3(...polygon[0].slice(0, 3)), new THREE.Vector3(...polygon[j].slice(0, 3)), new THREE.Vector3(...polygon[j + 1].slice(0, 3))).closestPointToPoint(point, closest);
        const next = closest.distanceToSquared(point);
        if (next < distance) { distance = next; seedFace = face; }
      }
    }
  }
  const reached = new Set([seedFace]), queue = [seedFace];
  for (let i = 0; i < queue.length; i++) for (const face of neighbours.get(queue[i]) ?? []) {
    if (!reached.has(face)) { reached.add(face); queue.push(face); }
  }
  return new Map([...polygons].filter(([face]) => reached.has(face)));
}

function edgePart(a, b, fields) {
  let start = 0, end = 1;
  for (const field of fields) {
    const before = field(a), after = field(b);
    if (before < 0 && after < 0) return null;
    if (before < 0) start = Math.max(start, before / (before - after));
    if (after < 0) end = Math.min(end, before / (before - after));
  }
  return end - start > 1e-8 ? [a, b] : null;
}

function polygonsFor(geometry, fields, bounds, faceIds = null) {
  const polygons = new Map(), indices = geometry.index;
  const visit = (face) => {
    const corners = [0, 1, 2].map((offset) => vertex(geometry, indices.getX(face * 3 + offset)));
    let polygon = corners;
    for (const field of fields) { polygon = clip(polygon, field); if (polygon.length < 3) return; }
    const edges = [];
    for (let j = 0; j < 3; j++) { const edge = edgePart(corners[j], corners[(j + 1) % 3], fields); if (edge) edges.push(edge); }
    polygons.set(face, { polygon, edges });
  };
  if (faceIds) faceIds.forEach(visit);
  else geometry.boundsTree.shapecast({ intersectsBounds: (box) => box.intersectsBox(bounds), intersectsTriangle: (_triangle, face) => { visit(face); return false; } });
  return polygons;
}

function makeGeometry(polygons) {
  const positions = [], normals = [], faces = [], faceTriangles = new Map();
  let area = 0;
  const a = new THREE.Vector3(), b = a.clone(), c = a.clone(), centroid = a.clone();
  for (const [key, { polygon, sourceFace = key }] of polygons) for (let j = 1; j + 1 < polygon.length; j++) {
    const corners = [polygon[0], polygon[j], polygon[j + 1]];
    a.fromArray(corners[0]); b.fromArray(corners[1]); c.fromArray(corners[2]);
    const triangleArea = new THREE.Triangle(a, b, c).getArea();
    if (triangleArea < 1e-10) continue;
    area += triangleArea;
    centroid.addScaledVector(a.clone().add(b).add(c).divideScalar(3), triangleArea);
    for (const v of corners) { positions.push(...v.slice(0, 3)); normals.push(...v.slice(3, 6)); }
    faces.push(sourceFace);
    if (!faceTriangles.has(sourceFace)) faceTriangles.set(sourceFace, []);
    faceTriangles.get(sourceFace).push(corners.map((v) => v.slice(0, 3)));
  }
  if (!faces.length) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  centroid.divideScalar(area);
  const anchor = new THREE.Vector3();
  let anchorFace, closest = Infinity;
  for (let offset = 0; offset < positions.length; offset += 9) {
    a.fromArray(positions, offset); b.fromArray(positions, offset + 3); c.fromArray(positions, offset + 6);
    a.add(b).add(c).divideScalar(3);
    const distance = a.distanceToSquared(centroid);
    if (distance < closest) { closest = distance; anchor.copy(a); anchorFace = faces[offset / 9]; }
  }
  return { geometry, faceTriangles, sourceFaces: faces, area, anchor, anchorFace };
}

function aperturePolygons(geometry, domain) {
  const polygons = new Map(), barycentric = new THREE.Vector3(), normal = new THREE.Vector3();
  for (let piece = 0; piece < domain.faces.length; piece++) {
    const sourceFace = domain.faces[piece];
    const source = [0, 1, 2].map((offset) => vertex(geometry, geometry.index.getX(sourceFace * 3 + offset)));
    const triangle = new THREE.Triangle(...source.map((v) => new THREE.Vector3(...v.slice(0, 3))));
    const sourceNormals = source.map((v) => new THREE.Vector3(...v.slice(3, 6)));
    const polygon = [0, 1, 2].map((corner) => {
      const offset = piece * 9 + corner * 3;
      const point = new THREE.Vector3().fromArray(domain.positions, offset);
      triangle.getBarycoord(point, barycentric);
      normal.copy(sourceNormals[0]).multiplyScalar(barycentric.x).addScaledVector(sourceNormals[1], barycentric.y).addScaledVector(sourceNormals[2], barycentric.z).normalize();
      return [...point.toArray(), ...normal.toArray()];
    });
    // Several clipped pieces may belong to one native face. Keep every piece
    // and retain the original face ID for the shared surface picker.
    polygons.set(piece, { polygon, sourceFace });
  }
  return polygons;
}

async function regionBinary(manifest, label) {
  const response = await fetchAsset(manifest.binary.url);
  if (!response.ok) throw new Error(`${label} triangles are unavailable.`);
  const buffer = await response.arrayBuffer();
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map((v) => v.toString(16).padStart(2, '0')).join('');
  if (digest !== manifest.binary.sha256 || buffer.byteLength !== manifest.binary.byte_length) throw new Error(`${label} triangle checksum differs from its manifest.`);
  return buffer;
}

export class SurfaceRegions {
  constructor(context) {
    this.context = context;
    this.cache = new Map();
    this.sourceDomains = [];
    this.apertureDomains = [];
    this.registeredDomains = [];
    this.pointFits = new Map();
    this.pointLandmarks = new Set();
    this.registeredRejections = [];
    this.hovered = null;
    this.pending = new Map();
    this.precomputed = false;
    this.precomputedIssue = null;
    this.batches = new HighlightBatches(context, regionMaterials);
  }

  async loadPrecomputed(landmarks) {
    if (!landmarks || this.context.dataset !== 'ct' || globalThis.__SKULL_BUILD_HIGHLIGHTS__) return false;
    const identities = globalThis.__SKULL_ASSET_HASHES__;
    if (!identities) return false; // Generic static hosts retain the live builder.
    try {
      const response = await fetchAsset('assets/landmark-highlights/manifest.json');
      if (response.status === 404) return false;
      if (!response.ok) throw new Error(`Precomputed highlights returned HTTP ${response.status}`);
      const metadata = await response.json();
      if (metadata.schema_version !== 1 || metadata.dataset !== this.context.dataset
          || metadata.model_sha256 !== this.context.manifest.sha256
          || metadata.surface_sha256 !== this.context.manifest.surface?.sha256
          || JSON.stringify(Object.keys(metadata.inputs).sort()) !== JSON.stringify(Object.keys(identities).sort())
          || Object.entries(metadata.inputs).some(([name, hash]) => identities[name] !== hash)) {
        throw new Error('Precomputed highlights are out of date for the current skull, catalogue or generation code.');
      }
      if (metadata.regions.length !== landmarks.size || new Set(metadata.regions.map((region) => region.id)).size !== landmarks.size
          || metadata.regions.some((region) => !landmarks.has(region.id))) throw new Error('Precomputed landmark inventory differs from the catalogue.');
      const buffer = await regionBinary(metadata, 'Precomputed highlight');
      const view = (record, Type) => {
        if (record.type !== Type.name || !Number.isSafeInteger(record.byte_offset) || record.byte_offset < 0 || record.byte_offset % 4
            || !Number.isSafeInteger(record.count) || record.count < 0 || record.byte_offset + record.count * 4 > buffer.byteLength) {
          throw new Error('Invalid precomputed highlight buffer range.');
        }
        return new Type(buffer, record.byte_offset, record.count);
      };
      const prepared = metadata.regions.map((region) => ({ ...region, parts: region.parts.map((part) => {
        const owner = this.context.entries.get(part.ownerId);
        if (!owner || !landmarks.get(region.id).owners.includes(part.ownerId)) throw new Error('Invalid precomputed highlight owner.');
        const positions = view(part.positions, Float32Array), normals = view(part.normals, Float32Array);
        const sourceFaces = view(part.sourceFaces, Uint32Array);
        const faceIndex = { ids: view(part.faceIds, Uint32Array), offsets: view(part.faceOffsets, Uint32Array), triangles: view(part.faceTriangles, Uint32Array) };
        if (positions.length % 9 || normals.length !== positions.length || sourceFaces.length !== positions.length / 9
            || faceIndex.offsets.length !== faceIndex.ids.length + 1 || faceIndex.offsets[0] !== 0
            || faceIndex.offsets[faceIndex.ids.length] !== faceIndex.triangles.length
            || faceIndex.ids.some((id) => id >= owner.mesh.geometry.index.count / 3)) throw new Error('Invalid precomputed native-face lookup.');
        return { ...part, positions, normals, sourceFaces, faceIndex };
      }) }));
      for (const packed of prepared) this.accept(landmarks.get(packed.id), packed);
      for (const key of ['sourceDomains', 'apertureDomains', 'registeredDomains', 'registeredRejections']) this[key] = metadata.domains[key];
      this.precomputed = true;
      return true;
    } catch (error) {
      for (const region of this.cache.values()) for (const part of region.parts) {
        this.context.scene.remove(part.mesh); part.geometry.dispose(); part.mesh.material.dispose(); part.ghost.material.dispose();
      }
      this.cache.clear();
      this.precomputedIssue = error.message;
      console.warn('Saved highlights unavailable; using the native surface builder.', error);
      return false;
    }
  }

  async startWorker() {
    if (this.precomputed || this.context.dataset === 'microct' || typeof Worker === 'undefined') return;
    const worker = new Worker(assetUrl(new URL('./surface-region-worker.js', import.meta.url)), { type: 'module' });
    this.worker = worker;
    this.workerReady = new Promise((resolve, reject) => {
      worker.onmessage = ({ data }) => {
        if (data.type === 'ready') { resolve(); return; }
        const pending = this.pending.get(data.id);
        if (!pending) return;
        this.pending.delete(data.id);
        if (data.error) pending.reject(new Error(data.error));
        else pending.resolve(this.accept(pending.entry, data.region));
      };
      worker.onerror = (event) => {
        const error = new Error(event.message || 'Surface highlight worker failed');
        reject(error);
        for (const pending of this.pending.values()) pending.reject(error);
        this.pending.clear(); worker.terminate(); this.worker = null;
      };
    });
    const entries = [...this.context.entries.values()].map((entry) => ({
      id: entry.id, position: entry.mesh.geometry.attributes.position.array,
      normal: entry.mesh.geometry.attributes.normal.array, index: entry.mesh.geometry.index.array,
    }));
    worker.postMessage({ type: 'init', dataset: this.context.dataset, manifest: this.context.manifest, entries,
      sourceDomains: this.sourceDomains, apertureDomains: this.apertureDomains, registeredDomains: this.registeredDomains,
      registeredRejections: this.registeredRejections, pointFits: [...this.pointFits], pointLandmarks: [...this.pointLandmarks] });
    try { await this.workerReady; }
    catch (error) { console.warn('Background surface preparation unavailable; using the local builder.', error); }
  }

  empty(entry) {
    if (!this.cache.has(entry.id)) this.cache.set(entry.id, { id: entry.id, entry, parts: [], area: 0, methods: [], colour: regionColour(entry.id) });
    return this.cache.get(entry.id);
  }

  async prepare(entry) {
    if (this.cache.has(entry.id)) return this.cache.get(entry.id);
    if (this.pending.has(entry.id)) return this.pending.get(entry.id).promise;
    if (!this.worker) return this.build(entry);
    await this.workerReady;
    if (this.cache.has(entry.id)) return this.cache.get(entry.id);
    if (this.pending.has(entry.id)) return this.pending.get(entry.id).promise;
    const pending = { entry };
    pending.promise = new Promise((resolve, reject) => { pending.resolve = resolve; pending.reject = reject; });
    this.pending.set(entry.id, pending);
    const { id, name, category, owners, view, positions, placement } = entry;
    this.worker.postMessage({ type: 'build', entry: { id, name, category, owners, view, positions, placement } });
    return pending.promise;
  }

  async prepareShaders(renderer, camera, scene) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1], 3));
    // Retain these materials so Three keeps their compiled programs available to
    // real regions. The sample geometry is never added to or drawn in the scene.
    this.warmMaterials = regionMaterials('#ffffff', 0.9);
    const samples = new THREE.Group();
    this.warmMaterials.forEach((material) => samples.add(new THREE.Mesh(geometry, material)));
    try { await renderer.compileAsync(samples, camera, scene); }
    finally { geometry.dispose(); samples.clear(); }
    if (this.precomputed && !globalThis.__SKULL_DISABLE_HIGHLIGHT_BATCHING__) {
      this.batches.build(this.cache, renderer);
      await this.batches.warm(renderer, camera, scene);
    }
  }

  attach(entry, built, ownerId, method, scope, fit, source) {
    const owner = this.context.entries.get(ownerId), colour = regionColour(entry.id);
    const [material, ghostMaterial] = regionMaterials(colour, owner.mesh.material.roughness);
    const mesh = new THREE.Mesh(built.geometry, material);
    mesh.userData.surfaceRegion = true; mesh.userData.regionOwner = ownerId;
    mesh.matrixAutoUpdate = false; mesh.visible = false; mesh.renderOrder = 2;
    const ghost = new THREE.Mesh(built.geometry, ghostMaterial);
    ghost.userData.surfaceRegion = true; ghost.visible = false; ghost.renderOrder = 10000;
    mesh.add(ghost); this.context.scene.add(mesh);
    return { ...built, mesh, ghost, ownerId, method, scope, fit, source };
  }

  accept(entry, packed) {
    if (this.cache.has(entry.id)) return this.cache.get(entry.id);
    const parts = packed.parts.map((part) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(part.positions, 3));
      geometry.setAttribute('normal', new THREE.BufferAttribute(part.normals, 3));
      geometry.boundingBox = new THREE.Box3(new THREE.Vector3(...part.bounds[0]), new THREE.Vector3(...part.bounds[1]));
      geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(...part.sphere[0]), part.sphere[1]);
      const built = { geometry, sourceFaces: part.sourceFaces, area: part.area, anchor: new THREE.Vector3(...part.anchor),
        anchorFace: part.anchorFace, faceIndex: part.faceIndex };
      return this.attach(entry, built, part.ownerId, part.method, part.scope, part.fit, part.source);
    });
    const region = { id: entry.id, entry, parts, area: packed.area, methods: packed.methods, colour: packed.colour };
    this.cache.set(entry.id, region);
    return region;
  }

  async loadSources(landmarks) {
    if (this.context.dataset === 'microct') return;
    if (await this.loadPrecomputed(landmarks)) return;
    await Promise.all([this.loadApertures(), this.loadRegisteredSources(), this.loadAuthoredSources()]);
  }

  async loadAuthoredSources() {
    const response = await fetchAsset('assets/landmark-regions/source-regions.json');
    if (!response.ok) throw new Error('Source region selections are unavailable.');
    const manifest = await response.json();
    if (this.context.dataset === 'reference' && manifest.source.model_sha256 !== this.context.manifest.sha256) throw new Error('Source regions do not match this reference model.');
    const buffer = await regionBinary(manifest, 'Source region');
    this.sourceDomains = manifest.regions.filter((entry) => this.context.dataset === 'reference'
      || this.context.entries.get(entry.owner)?.source_model_sha256 === manifest.source.model_sha256)
      .map((entry) => ({ ...entry, faces: new Uint32Array(buffer, entry.byte_offset, entry.count) }));
  }

  async loadApertures() {
    const response = await fetchAsset('assets/landmark-regions/apertures.json');
    if (!response.ok) throw new Error('Opening rim selections are unavailable.');
    const manifest = await response.json(), dataset = this.context.dataset;
    if (manifest.models[dataset]?.sha256 !== this.context.manifest.sha256) throw new Error('Opening rims do not match this skull model.');
    const buffer = await regionBinary(manifest, 'Opening rim');
    this.apertureDomains = manifest.regions.filter((entry) => entry.dataset === dataset).map((entry) => {
      if (entry.source_model_sha256 !== this.context.manifest.sha256 || entry.positions_count !== entry.source_faces_count * 9) throw new Error('Opening rim geometry does not match its native faces.');
      return { ...entry, positions: new Float32Array(buffer, entry.positions_byte_offset, entry.positions_count), faces: new Uint32Array(buffer, entry.source_faces_byte_offset, entry.source_faces_count) };
    });
  }

  async loadRegisteredSources() {
    const response = await fetchAsset('assets/landmark-regions/registered-regions.json');
    if (response.status === 404) return; // Optional locally generated transfer package.
    if (!response.ok) throw new Error('Mapped source regions are unavailable.');
    const manifest = await response.json(), dataset = this.context.dataset;
    if (manifest.models[dataset]?.sha256 !== this.context.manifest.sha256) throw new Error('Mapped regions belong to a different skull model.');
    const buffer = await regionBinary(manifest, 'Mapped source region');
    this.registeredRejections = manifest.rejected.filter((entry) => entry.dataset === dataset && entry.feature_id);
    this.registeredDomains = manifest.regions.filter((entry) => entry.dataset === dataset).filter((entry) => {
      if (entry.source_id !== 'biodigital') return true;
      let reason;
      if (entry.fit?.surface_correspondence_version !== 2 || !entry.fit?.surface_side_check) reason = 'Transfer predates surface-orientation checks and must be rebuilt';
      const sideCheck = entry.fit?.surface_side_check;
      if (['inner', 'outer'].includes(sideCheck?.source_compartment) && (!sideCheck.cavity_exposure_check
          || sideCheck.cavity_exposure_check.source.exposed_fraction < 0.95
          || sideCheck.cavity_exposure_check.native.exposed_fraction < 0.95)) {
        reason = 'Cranial transfer has no accepted exposed-cortex fit; rebuild or inspect its source fit';
      }
      const vault = ['frontal-bone', 'parietal-bone-left', 'parietal-bone-right', 'occipital-bone'].includes(entry.owner);
      const signature = entry.fit?.surface_side_check?.native_signature;
      if (vault && signature) {
        if (/internal|cerebral|meningeal|sagittal-sinus|transverse-sinus/.test(entry.feature_id) && signature.outer_fraction > 0.01) reason = 'Internal cranial landmark reaches the outer bone surface';
        if (/external|nuchal-line/.test(entry.feature_id) && signature.inner_fraction > 0.01) reason = 'External cranial landmark reaches the inner bone surface';
      }
      if (reason) this.registeredRejections.push({ feature_id: entry.feature_id, owner: entry.owner, reason });
      return !reason;
    }).map((entry) => {
      if (entry.source_model_sha256 !== this.context.manifest.sha256 || entry.positions_count !== entry.source_faces_count * 9 || !entry.approximate || entry.independently_validated) throw new Error('Mapped region identity or evidence is invalid.');
      const owner = this.context.entries.get(entry.owner);
      const positions = new Float32Array(buffer, entry.positions_byte_offset, entry.positions_count);
      const faces = new Uint32Array(buffer, entry.source_faces_byte_offset, entry.source_faces_count);
      if (!owner || [...faces].some((face) => face >= owner.mesh.geometry.index.count / 3) || [...positions].some((value) => !Number.isFinite(value))) throw new Error('Mapped region contains invalid native triangles.');
      return { ...entry, positions, faces };
    });
  }

  hasRegisteredBoundary(id) {
    return !this.pointLandmarks.has(id) && !this.hasSuppliedBoundary(id) && !this.apertureDomains.some((domain) => domain.feature_id === id)
      && this.registeredDomains.some((domain) => domain.feature_id === id);
  }

  registeredPlacement(id) {
    if (!this.hasRegisteredBoundary(id)) return null;
    const domains = this.registeredDomains.filter((domain) => domain.feature_id === id);
    return domains.reduce((best, domain) => !best || domain.area > best.area ? domain : best, null);
  }

  hasSuppliedBoundary(id) {
    // Terminology, guide points, fitted rims and inferred joint associations
    // must never be promoted to supplied feature boundaries.
    return this.sourceDomains.some((domain) => domain.feature_ids.includes(id));
  }

  build(entry) {
    if (this.cache.has(entry.id)) return this.cache.get(entry.id);
    const parts = [], methods = new Set();
    const transferred = this.hasRegisteredBoundary(entry.id);
    const pointFit = this.pointFits.get(entry.id);
    for (const ownerId of entry.owners) {
      const owner = this.context.entries.get(ownerId), geometry = owner.mesh.geometry;
      if (entry.category === 'landmarks' && !pointFit) continue;
      const seed = surfaceSeed(entry, owner, this.context.dataset);
      if (!seed) continue;
      let polygons, method, scope = null;
      const authored = this.sourceDomains.filter((domain) => domain.owner === ownerId && domain.feature_ids.includes(entry.id));
      const aperture = this.apertureDomains.find((domain) => domain.owner === ownerId && domain.feature_id === entry.id);
      const registered = this.registeredDomains.filter((domain) => domain.owner === ownerId && domain.feature_id === entry.id);
      if (entry.category === 'landmarks') {
        // A craniometric point has no anatomical area. Use one shared, compact
        // surface cue; independently fitted masks must not turn it into islands.
        const center = new THREE.Vector3(...pointFit.point);
        const radius = pointFit.native_point_fit.display_radius;
        const fields = axes.map((axis) => (v) => radius - axis.x * (v[0] - center.x) - axis.y * (v[1] - center.y) - axis.z * (v[2] - center.z));
        if (pointFit) {
          const direction = pointFit.native_point_fit.view_direction;
          fields.push((v) => v[3] * direction[0] + v[4] * direction[1] + v[5] * direction[2] - 0.15);
        }
        const bounds = new THREE.Box3(center.clone().addScalar(-radius * 1.3), center.clone().addScalar(radius * 1.3));
        polygons = connected(polygonsFor(geometry, fields, bounds), center, seed.faceIndex);
        method = `fitted:${pointFit.method}`; scope = 'point-landmark-visualization';
      } else if (authored.length) {
        const faces = [...new Set(authored.flatMap((domain) => [...domain.faces]))];
        polygons = polygonsFor(geometry, [], null, faces);
        method = 'source-authored-faces'; scope = authored[0].mapping_scope;
      } else if (aperture) {
        polygons = aperturePolygons(geometry, aperture);
        method = 'fitted:geometry-detected-aperture-rim'; scope = 'opening-rim';
      } else if (transferred) {
        if (!registered.length) continue;
        polygons = new Map();
        for (const domain of registered) for (const record of aperturePolygons(geometry, domain).values()) polygons.set(polygons.size, record);
        method = 'fitted:registered-author-region'; scope = 'cross-specimen-source-region';
      } else if (entry.category === 'sutures' && entry.owners.length > 1) {
        ({ polygons, method } = this.contact(entry, ownerId));
      } else if (this.registeredRejections.some((record) => record.feature_id === entry.id)) {
        continue; // A failed source transfer must not reappear as a generic blob.
      } else {
        continue; // A label coordinate cannot supply an anatomical area boundary.
      }
      const built = makeGeometry(polygons);
      if (!built) continue;
      parts.push(this.attach(entry, built, ownerId, method, scope, pointFit?.native_point_fit ?? aperture?.fit ?? registered[0]?.fit,
        transferred ? registered[0]?.source_id : null)); methods.add(method);
    }
    const region = { id: entry.id, entry, parts, area: parts.reduce((sum, part) => sum + part.area, 0), methods: [...methods], colour: regionColour(entry.id) };
    this.cache.set(entry.id, region);
    return region;
  }

  contact(entry, ownerId) {
    const owner = this.context.entries.get(ownerId), geometry = owner.mesh.geometry;
    const otherIds = ownerId === entry.owners[0] ? entry.owners.slice(1) : [entry.owners[0]];
    const others = otherIds.map((id) => this.context.entries.get(id));
    const width = 1.5;
    const bounds = geometry.boundingBox.clone();
    const otherBounds = new THREE.Box3();
    others.forEach((other) => otherBounds.union(other.mesh.geometry.boundingBox));
    bounds.intersect(otherBounds.expandByScalar(width));
    if (bounds.isEmpty()) return { polygons: new Map(), method: 'fitted:bone-contact-band' };
    const point = new THREE.Vector3(), distances = new Map();
    const field = (v) => {
      const key = pointKey(v);
      if (!distances.has(key)) {
        point.fromArray(v);
        let distance = Infinity;
        for (const other of others) {
          const nearest = other.mesh.geometry.boundsTree.closestPointToPoint(point);
          if (nearest) distance = Math.min(distance, nearest.distance);
        }
        distances.set(key, Number.isFinite(distance) ? width - distance : -width);
      }
      return distances.get(key);
    };
    const fields = [field];
    if (/^(left|right)\b/.test(entry.name.toLowerCase())) {
      const side = entry.name.toLowerCase().startsWith('left') ? 1 : -1;
      const skull = this.context.manifest.structures.filter((record) => ['cranial', 'facial'].includes(record.group));
      const mid = (Math.min(...skull.map((record) => record.bounds[0][0])) + Math.max(...skull.map((record) => record.bounds[1][0]))) / 2;
      fields.push((v) => side * (v[0] - mid));
    }
    const domains = this.sourceDomains.filter((domain) => domain.owner === ownerId && domain.kind === 'joint');
    if (domains.length) {
      const faceIds = [...new Set(domains.flatMap((domain) => [...domain.faces]))];
      return { polygons: polygonsFor(geometry, fields, bounds, faceIds), method: 'source-joint-faces:fitted-neighbour-association' };
    }
    return { polygons: polygonsFor(geometry, fields, bounds), method: 'fitted:bone-contact-band' };
  }

  sync(enabled, selected, xrayId = null) {
    this.context.scene.updateMatrixWorld(true);
    // The smallest overlapping region wins in both the renderer and picker.
    const ordered = [...this.cache.values()].sort((a, b) => b.area - a.area || b.id.localeCompare(a.id));
    for (const [rank, region] of ordered.entries()) for (const part of region.parts) {
      const owner = this.context.entries.get(part.ownerId);
      const visible = enabled.has(region.id) || selected === region.id || this.hovered === region.id;
      part.mesh.matrix.copy(owner.mesh.matrixWorld); part.mesh.matrixWorldNeedsUpdate = true;
      part.mesh.renderOrder = 10 + rank;
      part.mesh.visible = visible && owner.mesh.visible;
      if (part.mesh.visible && this.context.sectionEnabled()) {
        const box = part.geometry.boundingBox.clone().applyMatrix4(part.mesh.matrix);
        const normal = this.context.sectionPlane.normal;
        const inside = new THREE.Vector3(normal.x >= 0 ? box.max.x : box.min.x, normal.y >= 0 ? box.max.y : box.min.y, normal.z >= 0 ? box.max.z : box.min.z);
        part.mesh.visible = this.context.sectionPlane.distanceToPoint(inside) >= 0;
      }
      const source = owner.mesh.material, material = part.mesh.material;
      if (material.transparent !== source.transparent) { material.transparent = source.transparent; material.needsUpdate = true; }
      material.opacity = source.opacity; material.depthWrite = source.depthWrite;
      material.roughness = source.roughness;
      material.emissiveIntensity = this.hovered === region.id ? 0.3 : selected === region.id ? 0.2 : 0.1;
      part.ghost.visible = part.mesh.visible && region.id === xrayId;
      part.ghost.material.opacity = Math.min(0.5, source.opacity);
    }
    this.batches.sync(this.hovered, selected);
  }

  contains(region, hit) {
    const ownerId = hit.object.userData.entryId;
    const local = hit.object.worldToLocal(hit.point.clone());
    const point = new THREE.Vector3();
    for (const part of region.parts) {
      if (part.ownerId !== ownerId) continue;
      if (part.faceIndex) {
        const { ids, offsets, triangles } = part.faceIndex;
        let lo = 0, hi = ids.length;
        while (lo < hi) { const mid = (lo + hi) >>> 1; if (ids[mid] < hit.faceIndex) lo = mid + 1; else hi = mid; }
        if (ids[lo] !== hit.faceIndex) continue;
        const positions = part.geometry.attributes.position;
        const triangle = new THREE.Triangle();
        for (let i = offsets[lo]; i < offsets[lo + 1]; i++) {
          const start = triangles[i] * 3;
          triangle.a.fromBufferAttribute(positions, start); triangle.b.fromBufferAttribute(positions, start + 1); triangle.c.fromBufferAttribute(positions, start + 2);
          triangle.closestPointToPoint(local, point);
          if (point.distanceToSquared(local) < 1e-8) return true;
        }
      } else {
        for (const corners of part.faceTriangles.get(hit.faceIndex) ?? []) {
          new THREE.Triangle(...corners.map((corner) => new THREE.Vector3(...corner))).closestPointToPoint(local, point);
          if (point.distanceToSquared(local) < 1e-8) return true;
        }
      }
    }
    return false;
  }

  matching(hit, ids) {
    if (!hit) return [];
    const candidates = [];
    for (const id of ids) {
      const region = this.cache.get(id);
      if (region?.parts.length && this.contains(region, hit)) candidates.push(region);
    }
    return candidates.sort((a, b) => a.area - b.area || a.id.localeCompare(b.id));
  }

  pick(hit, ids) {
    return this.matching(hit, ids)[0] ?? null;
  }

  snapshot() {
    return [...this.cache.values()].map((region) => ({
      id: region.id, area: region.area, methods: region.methods, colour: region.colour,
      triangles: region.parts.reduce((sum, part) => sum + part.geometry.attributes.position.count / 3, 0),
      rendered: region.parts.some((part) => part.mesh.visible),
      xrayRendered: region.parts.some((part) => part.mesh.visible && part.ghost.visible),
      owners: region.parts.map((part) => part.ownerId),
    }));
  }

  audit(id) {
    const region = this.cache.get(id); if (!region) return null;
    return { id, methods: region.methods, area: region.area, parts: region.parts.map((part) => ({
      owner: part.ownerId, method: part.method, scope: part.scope, fit: part.fit, source: part.source,
      positions: [...part.geometry.attributes.position.array], sourceFaces: [...part.sourceFaces],
      matrix: part.mesh.matrix.toArray(), rendered: part.mesh.visible,
      anchor: part.anchor.toArray(), anchorFace: part.anchorFace,
      xrayRendered: part.mesh.visible && part.ghost.visible,
      opacity: part.mesh.material.opacity, colour: `#${part.mesh.material.color.getHexString()}`,
      renderOrder: part.mesh.renderOrder,
    })) };
  }
}
