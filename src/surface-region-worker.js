import * as THREE from '../vendor/build/three.module.js';
import { MeshBVH } from '../vendor/three-mesh-bvh/index.module.js';
import { SurfaceRegions } from './surface-regions.js';

let regions;

function pack(region) {
  const transfers = [];
  const parts = region.parts.map((part) => {
    const sourceFaces = new Uint32Array(part.sourceFaces);
    // A compact face lookup replaces nested per-triangle JS arrays on the UI
    // thread. Native face IDs and rendered triangle order remain unchanged.
    const order = Array.from(sourceFaces, (_, i) => i).sort((a, b) => sourceFaces[a] - sourceFaces[b]);
    const faces = [], starts = [];
    for (let i = 0; i < order.length; i++) if (!i || sourceFaces[order[i]] !== sourceFaces[order[i - 1]]) {
      faces.push(sourceFaces[order[i]]); starts.push(i);
    }
    starts.push(order.length);
    const faceIndex = { ids: new Uint32Array(faces), offsets: new Uint32Array(starts), triangles: new Uint32Array(order) };
    const positions = part.geometry.attributes.position.array, normals = part.geometry.attributes.normal.array;
    const bounds = [part.geometry.boundingBox.min.toArray(), part.geometry.boundingBox.max.toArray()];
    const sphere = [part.geometry.boundingSphere.center.toArray(), part.geometry.boundingSphere.radius];
    const data = { positions, normals, sourceFaces, faceIndex, bounds, sphere, ownerId: part.ownerId, method: part.method,
      scope: part.scope, fit: part.fit, source: part.source, area: part.area, anchor: part.anchor.toArray(), anchorFace: part.anchorFace };
    transfers.push(positions.buffer, normals.buffer, sourceFaces.buffer, ...Object.values(faceIndex).map((a) => a.buffer));
    regions.context.scene.remove(part.mesh);
    part.geometry.dispose(); part.mesh.material.dispose(); part.ghost.material.dispose();
    return data;
  });
  regions.cache.delete(region.id);
  return [{ id: region.id, parts, area: region.area, methods: region.methods, colour: region.colour }, transfers];
}

self.onmessage = ({ data }) => {
  try {
    if (data.type === 'init') {
      const scene = new THREE.Scene(), entries = new Map();
      for (const record of data.entries) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(record.position, 3));
        geometry.setAttribute('normal', new THREE.BufferAttribute(record.normal, 3));
        geometry.setIndex(new THREE.BufferAttribute(record.index, 1));
        geometry.computeBoundingBox(); geometry.computeBoundingSphere();
        geometry.boundsTree = new MeshBVH(geometry, { indirect: true, maxLeafTris: 10 });
        const mesh = new THREE.Mesh(geometry, new THREE.MeshPhysicalMaterial());
        entries.set(record.id, { id: record.id, mesh }); scene.add(mesh);
      }
      regions = new SurfaceRegions({ scene, entries, dataset: data.dataset, manifest: data.manifest });
      for (const key of ['sourceDomains', 'apertureDomains', 'registeredDomains', 'registeredRejections']) regions[key] = data[key];
      regions.pointFits = new Map(data.pointFits); regions.pointLandmarks = new Set(data.pointLandmarks);
      self.postMessage({ type: 'ready' });
    } else if (data.type === 'build') {
      const [region, transfer] = pack(regions.build(data.entry));
      self.postMessage({ type: 'built', id: data.entry.id, region }, transfer);
    }
  } catch (error) {
    if (data.type === 'init') throw error;
    self.postMessage({ type: 'built', id: data.entry.id, error: error.message });
  }
};
