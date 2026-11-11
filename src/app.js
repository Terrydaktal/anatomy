import * as THREE from 'three';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { assetUrl, fetchAsset } from './asset-url.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { BoneOcclusionPass } from './bone-occlusion-pass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { computeBoundsTree, acceleratedRaycast } from 'three-mesh-bvh';
import { AnatomyDetails } from './anatomy-details.js';

const $ = (id) => document.getElementById(id);
THREE.DefaultLoadingManager.setURLModifier(assetUrl);
const canvas = $('skull-canvas');
const requestedDataset = new URLSearchParams(location.search).get('dataset');
const dataset = requestedDataset === 'microct' ? 'microct' : 'ct';
if (requestedDataset === 'reference') {
  const url = new URL(location.href);
  url.searchParams.delete('dataset');
  history.replaceState(null, '', url);
}
const state = { colors: false, depthShading: true, opacity: 1, explode: 0, jaw: 0, gloss: 0.25, selected: null, hovered: null, section: 'none', sectionPosition: 50, flip: false };
const entries = new Map();
const groupElements = new Map();
const rowElements = new Map();
const scene = new THREE.Scene();
scene.background = new THREE.Color('#11191c');
const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 5000);
camera.position.set(240, 110, 430);
const sectionPlane = new THREE.Plane();
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

function alignTriangleWinding(geometry) {
  const positions = geometry.attributes.position, normals = geometry.attributes.normal, indices = geometry.index;
  if (!normals || !indices) return 0;
  const a = new THREE.Vector3(), b = a.clone(), c = a.clone(), cross = a.clone(), supplied = a.clone();
  let corrected = 0;
  for (let offset = 0; offset < indices.count; offset += 3) {
    const first = indices.getX(offset), second = indices.getX(offset + 1), third = indices.getX(offset + 2);
    a.fromBufferAttribute(positions, first); b.fromBufferAttribute(positions, second).sub(a); c.fromBufferAttribute(positions, third).sub(a);
    cross.crossVectors(b, c);
    supplied.set(normals.getX(first) + normals.getX(second) + normals.getX(third), normals.getY(first) + normals.getY(second) + normals.getY(third), normals.getZ(first) + normals.getZ(second) + normals.getZ(third));
    if (cross.dot(supplied) < 0) {
      indices.setX(offset + 1, third); indices.setX(offset + 2, second); corrected++;
    }
  }
  if (corrected) indices.needsUpdate = true;
  return corrected; // Only vertex order within a triangle changes; face IDs stay stable.
}
const jawPivot = new THREE.Group();
const hinge = new THREE.Vector3();
scene.add(jawPivot);
let renderer;
let composer;
let ambientOcclusion;
let controls;
let manifest;
let details;
let cavityOpacity = null;
let skullBounds;
let restorePoint = null;
let dirty = true;
let pinsDirty = false;
let pointerDirty = false;
let pointerInside = false;
let interacting = false;
let pointerStart = null;
let toastTimer;
let lastPointer = { x: 0, y: 0 };
let hoverTooltipKey = '';
let contextBoneId = null;
let contextPointer = null;
const activePointers = new Set();
const neutralColor = '#dcd1b9';

const explodedOffsets = {
  'frontal-bone': [0, 60, 50],
  'parietal-bone-left': [70, 65, -10], 'parietal-bone-right': [-70, 65, -10],
  'temporal-bone-left': [95, 0, 0], 'temporal-bone-right': [-95, 0, 0],
  'occipital-bone': [0, 15, -90], 'sphenoid-bone': [0, 0, 15], 'ethmoid-bone': [0, 10, 95],
  'vomer': [0, -20, 90], 'mandible': [0, -95, 40], 'hyoid-bone': [0, -110, 0],
};

function explosionOffset(entry) {
  if (explodedOffsets[entry.id]) return new THREE.Vector3(...explodedOffsets[entry.id]);
  if (entry.kind === 'dentition' && entry.arch === 'upper') return new THREE.Vector3(0, -35, 110);
  const side = entry.side === 'left' ? 1 : -1;
  if (entry.group === 'ossicles') return new THREE.Vector3(side * 145, 15, 0);
  if (entry.id.startsWith('maxilla') || (entry.kind === 'tooth' && entry.arch === 'upper')) return new THREE.Vector3(side * 55, -25, 75);
  if (entry.id.startsWith('zygomatic')) return new THREE.Vector3(side * 95, -5, 55);
  if (entry.id.startsWith('nasal')) return new THREE.Vector3(side * 20, 30, 95);
  if (entry.id.startsWith('lacrimal')) return new THREE.Vector3(side * 50, 35, 65);
  if (entry.id.startsWith('palatine')) return new THREE.Vector3(side * 30, -55, 20);
  if (entry.id.startsWith('inferior-nasal')) return new THREE.Vector3(side * 45, -10, 110);
  return new THREE.Vector3();
}

function groupName(id) { return manifest.groups.find((group) => group.id === id)?.name ?? id; }
function groupEntries(id) { return [...entries.values()].filter((entry) => entry.group === id); }
function isJaw(entry) { return entry.id === 'mandible' || entry.arch === 'lower'; }
function isReady() { return document.documentElement.dataset.ready === 'true'; }
function displayColor(entry) { return entry.kind === 'bone' && !entry.useColor ? neutralColor : entry.color; }
function surfaceColor(entry) { return entry.kind === 'bone' && entry.id === state.hovered ? entry.color : displayColor(entry); }

function matches(entry) {
  const words = $('search').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const text = `${entry.name} ${entry.group} ${groupName(entry.group)} ${entry.kind} ${entry.side}`.toLowerCase();
  return words.every((word) => text.includes(word));
}

function applyVisibility(entry) {
  let retained = entry.visible;
  if (retained && state.section !== 'none') {
    const box = entry.mesh.geometry.boundingBox.clone().applyMatrix4(entry.mesh.matrixWorld);
    const normal = sectionPlane.normal;
    const furthestInside = new THREE.Vector3(
      normal.x >= 0 ? box.max.x : box.min.x,
      normal.y >= 0 ? box.max.y : box.min.y,
      normal.z >= 0 ? box.max.z : box.min.z,
    );
    // Entirely discarded source meshes need no rasterization. In particular,
    // degenerate source triangles cannot leave MSAA specks at section extremes.
    retained = sectionPlane.distanceToPoint(furthestInside) >= 0;
  }
  entry.mesh.visible = retained;
}

function applyAppearance(entry) {
  const material = entry.mesh.material;
  material.color.set(surfaceColor(entry));
  const opacity = state.opacity * entry.opacity;
  const transparent = opacity < 0.999;
  if (material.transparent !== transparent) { material.transparent = transparent; material.needsUpdate = true; }
  material.opacity = opacity;
  material.depthWrite = !transparent;
  material.roughness = THREE.MathUtils.lerp(0.94, 0.35, state.gloss);
  material.clearcoat = state.gloss * 0.12;
  material.clearcoatRoughness = 0.4;
  material.emissive.set(entry.id === state.selected ? '#35442b' : entry.id === state.hovered ? '#273e2d' : '#000000');
  material.emissiveIntensity = entry.id === state.selected ? 0.5 : 0.4;
  if (entry.id === state.hovered) $('hover-dot').style.background = surfaceColor(entry);
  applyVisibility(entry);
  dirty = true;
}

function updateAppearance() { entries.forEach(applyAppearance); }

function updateTransforms() {
  jawPivot.position.copy(hinge).addScaledVector(new THREE.Vector3(...explodedOffsets.mandible), state.explode);
  jawPivot.rotation.x = THREE.MathUtils.degToRad(state.jaw);
  entries.forEach((entry) => {
    if (!isJaw(entry)) entry.mesh.position.copy(explosionOffset(entry)).multiplyScalar(state.explode);
  });
  scene.updateMatrixWorld(true);
  details?.syncTransforms();
  updateSection();
  pointerDirty = true;
  dirty = true;
}

function visibleBox(ids) {
  const box = new THREE.Box3();
  scene.updateMatrixWorld(true);
  entries.forEach((entry) => {
    if ((ids ? ids.includes(entry.id) : entry.visible)) box.union(entry.mesh.geometry.boundingBox.clone().applyMatrix4(entry.mesh.matrixWorld));
  });
  return box;
}

function fitVisible(ids, suppliedMeshes = null, clearanceMeshes = []) {
  const meshes = suppliedMeshes ?? [...entries.values()].filter((entry) => ids ? ids.includes(entry.id) : entry.visible).map((entry) => entry.mesh);
  if (!meshes.length && !ids && !suppliedMeshes && details) meshes.push(...[...details.cavities.values()].filter((entry) => entry.visible).flatMap((entry) => entry.parts));
  scene.updateMatrixWorld(true);
  const box = new THREE.Box3();
  meshes.forEach((mesh) => box.union(mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld)));
  if (box.isEmpty()) { toast('Show a structure to fit it in view.'); return; }
  const direction = camera.position.clone().sub(controls.target).normalize();
  if (!direction.lengthSq()) direction.set(0.5, 0.2, 1).normalize();
  const center = box.getCenter(new THREE.Vector3());
  camera.position.copy(center).addScaledVector(direction, 400);
  camera.lookAt(center);
  const inverseRotation = camera.quaternion.clone().invert();
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const canvasTop = canvas.getBoundingClientRect().top;
  const safeTop = Math.min(height * 0.42, document.querySelector('.view-presets').getBoundingClientRect().bottom - canvasTop + 22);
  const selectedCard = state.selected ? $('selection').getBoundingClientRect() : details?.selected ? $('anatomy-selection').getBoundingClientRect() : null;
  const reserveRight = selectedCard && width > 700 ? selectedCard.width + 54 : 0;
  const safeBottom = selectedCard && width <= 700 ? Math.min(height * 0.48, selectedCard.height + 60) : 48;
  const usableHeight = Math.max(100, height - safeTop - safeBottom);
  const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const horizontal = tan * camera.aspect * (width - reserveRight) / width * 0.88;
  const vertical = tan * usableHeight / height * 0.94;
  // Fit the real surface vertices in perspective, rather than a loose rotated
  // skull-sized cuboid. Tiny isolated ossicles use exactly the same calculation.
  let distance = 6;
  const point = new THREE.Vector3();
  meshes.forEach((mesh) => {
    const positions = mesh.geometry.attributes.position;
    for (let index = 0; index < positions.count; index++) {
      point.fromBufferAttribute(positions, index).applyMatrix4(mesh.matrixWorld).sub(center).applyQuaternion(inverseRotation);
      distance = Math.max(distance, Math.abs(point.x) / horizontal + point.z, Math.abs(point.y) / vertical + point.z);
    }
  });
  // A small internal region can put the camera inside its surrounding bone.
  // Keep it in front of visible owning-bone bounds without fitting their width.
  for (const mesh of clearanceMeshes) {
    const bounds = mesh.geometry.boundingBox;
    for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
      point.set(x, y, z).applyMatrix4(mesh.matrixWorld).sub(center);
      distance = Math.max(distance, point.dot(direction) + camera.near * 2);
    }
  }
  camera.setViewOffset(width, height, reserveRight / 2, -(safeTop - safeBottom) / 2, width, height);
  controls.target.copy(center);
  camera.position.copy(controls.target).addScaledVector(direction, Math.max(distance, 6));
  camera.lookAt(controls.target);
  controls.update();
  pointerDirty = true;
  dirty = true;
}

function setView(name, fit = true) {
  const views = {
    oblique: [[0.56, 0.22, 1], [0, 1, 0]],
    anterior: [[0, 0, 1], [0, 1, 0]], posterior: [[0, 0, -1], [0, 1, 0]],
    left: [[1, 0, 0], [0, 1, 0]], right: [[-1, 0, 0], [0, 1, 0]],
    superior: [[0, 1, 0], [0, 0, -1]], inferior: [[0, -1, 0], [0, 0, 1]],
  };
  const [direction, up] = Array.isArray(name) ? [name, [0, 1, 0]] : views[name];
  camera.up.set(...up);
  camera.position.copy(controls.target).add(new THREE.Vector3(...direction).normalize().multiplyScalar(400));
  camera.lookAt(controls.target);
  if (fit) fitVisible();
  document.querySelectorAll('[data-view]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.view === name)));
}

function updateSection() {
  const enabled = state.section !== 'none';
  $('section-controls').hidden = !enabled;
  if (enabled && skullBounds) {
    const axis = { sagittal: 'x', coronal: 'z', axial: 'y' }[state.section];
    const box = visibleBox();
    if (box.isEmpty()) box.copy(skullBounds);
    const location = THREE.MathUtils.lerp(box.min[axis] - 0.1, box.max[axis] + 0.1, state.sectionPosition / 100);
    const normal = new THREE.Vector3();
    normal[axis] = (state.section === 'axial' ? -1 : 1) * (state.flip ? -1 : 1);
    sectionPlane.set(normal, -normal[axis] * location);
    $('section-value').textContent = `${location.toFixed(1)} ${manifest.units === 'millimetres' ? 'mm' : 'units'}`;
  }
  // Global clipping also applies to the depth/normal pass used for depth shading.
  renderer.clippingPlanes = enabled ? [sectionPlane] : [];
  entries.forEach((entry) => {
    applyVisibility(entry);
  });
  details?.syncTransforms();
  pointerDirty = true;
  dirty = true;
}

function updateCounts() {
  let bones = 0; let teeth = 0; let arches = 0; let hyoid = false;
  entries.forEach((entry) => {
    if (!entry.visible) return;
    if (entry.group === 'associated') { hyoid = true; bones++; }
    else if (entry.kind === 'bone') bones++;
    else if (entry.kind === 'dentition') arches++;
    else teeth++;
  });
  const boneTotal = manifest.structures.filter((entry) => entry.kind === 'bone').length;
  const toothTotal = manifest.structures.filter((entry) => entry.kind === 'tooth').length;
  $('visible-count').textContent = dataset === 'microct' ? `${bones} bone fragment · native 125 µm micro-CT` : `${bones} / ${boneTotal} bones · ${toothTotal ? `${teeth} / ${toothTotal} teeth` : `${arches} dental arches`}${hyoid ? ' · hyoid' : ''}`;
  const triangles = manifest.structures.reduce((sum, entry) => sum + entry.triangles, 0);
  $('status').textContent = `${(triangles / 1e6).toFixed(2)} million triangles · ${dataset === 'microct' ? 'micro-CT study' : manifest.source.specimen ?? 'reference atlas'}`;
  const boneEntries = [...entries.values()].filter((entry) => entry.kind === 'bone');
  const coloredCount = boneEntries.filter((entry) => entry.useColor).length;
  state.colors = coloredCount === boneEntries.length;
  $('bone-colors').checked = state.colors;
  $('bone-colors').indeterminate = coloredCount > 0 && coloredCount < boneEntries.length;
  for (const [id, elements] of groupElements) {
    const members = groupEntries(id);
    const shown = members.filter((entry) => entry.visible).length;
    elements.checkbox.checked = shown === members.length;
    elements.checkbox.indeterminate = shown > 0 && shown < members.length;
    elements.count.textContent = `${shown}/${members.length}`;
  }
  entries.forEach((entry) => {
    const elements = rowElements.get(entry.id);
    elements.checkbox.checked = entry.visible;
    elements.row.classList.toggle('is-hidden', !entry.visible);
    elements.row.classList.toggle('is-selected', state.selected === entry.id);
    elements.name.setAttribute('aria-pressed', String(state.selected === entry.id));
    elements.swatch.style.background = displayColor(entry);
    if (elements.colorToggle) {
      elements.colorToggle.setAttribute('aria-pressed', String(entry.useColor));
      elements.colorToggle.title = `${entry.useColor ? 'Use default ivory for' : 'Use unique colour for'} ${entry.name.toLowerCase()}`;
    }
  });
  $('restore').hidden = !restorePoint;
  $('anatomy-restore').hidden = !restorePoint;
}

function changeVisibility(members, visible) {
  members.forEach((entry) => { entry.visible = visible; applyAppearance(entry); });
  updateCounts();
  updateSection();
  if (state.hovered && !entries.get(state.hovered).visible) setHover(null);
  dirty = true;
}

function select(id) {
  if (id) details?.clearSelection();
  const previous = state.selected;
  state.selected = id;
  if (previous) applyAppearance(entries.get(previous));
  $('selection').hidden = !id;
  if (id) {
    const entry = entries.get(id);
    $('selected-name').textContent = entry.name;
    $('selected-group').textContent = `${groupName(entry.group)} · ${entry.side === 'midline' ? 'unpaired' : entry.side}`;
    $('selected-description').textContent = entry.description;
    $('selected-color').value = entry.color;
    $('selected-opacity').value = Math.round(entry.opacity * 100);
    $('selected-opacity-value').textContent = `${Math.round(entry.opacity * 100)}%`;
    applyAppearance(entry);
  }
  updateCounts();
}

function isolate(ids, allowEmpty = false) {
  if (!ids.length && !allowEmpty) return;
  if (!restorePoint) restorePoint = {
    visible: [...entries.values()].filter((entry) => entry.visible).map((entry) => entry.id),
    camera: camera.position.clone(), target: controls.target.clone(), up: camera.up.clone(),
    view: camera.view ? { ...camera.view } : null,
    section: state.section, sectionPosition: state.sectionPosition, flip: state.flip,
  };
  const wanted = new Set(ids);
  entries.forEach((entry) => { entry.visible = wanted.has(entry.id); applyAppearance(entry); });
  // A previous section would otherwise make an isolated tiny bone disappear.
  state.section = 'none'; $('section').value = 'none';
  setHover(null);
  updateCounts(); updateSection(); if (ids.length) fitVisible();
}

function restoreView() {
  if (!restorePoint) return;
  const saved = restorePoint;
  entries.forEach((entry) => { entry.visible = saved.visible.includes(entry.id); applyAppearance(entry); });
  camera.position.copy(saved.camera); controls.target.copy(saved.target); camera.up.copy(saved.up);
  if (saved.view?.enabled) {
    const width = canvas.clientWidth; const height = canvas.clientHeight;
    camera.setViewOffset(width, height, saved.view.offsetX / saved.view.fullWidth * width, saved.view.offsetY / saved.view.fullHeight * height, width, height);
  } else camera.clearViewOffset();
  camera.lookAt(controls.target); controls.update();
  state.section = saved.section; state.sectionPosition = saved.sectionPosition; state.flip = saved.flip;
  $('section').value = state.section; $('section-position').value = state.sectionPosition; $('section-flip').checked = state.flip;
  restorePoint = null;
  updateCounts(); updateSection();
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#icon-${name}`);
  svg.append(use); return svg;
}

function buildCatalogue() {
  $('catalogue-total').textContent = String(entries.size);
  for (const group of manifest.groups) {
    const section = document.createElement('section'); section.className = 'bone-group'; section.dataset.group = group.id;
    const heading = document.createElement('div'); heading.className = 'group-heading';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', `Show all ${group.name.toLowerCase()}`);
    checkbox.addEventListener('change', () => changeVisibility(groupEntries(group.id), checkbox.checked));
    const expand = document.createElement('button'); expand.className = 'group-expand'; expand.setAttribute('aria-expanded', String(['cranial', 'facial'].includes(group.id)));
    const list = document.createElement('div'); list.className = 'bone-list'; list.id = `list-${group.id}`;
    expand.setAttribute('aria-controls', list.id); expand.append(icon('chevron'), document.createTextNode(group.name));
    const count = document.createElement('span'); count.className = 'group-count';
    const isolateButton = document.createElement('button'); isolateButton.className = 'icon-button row-focus'; isolateButton.title = `Isolate ${group.name.toLowerCase()}`; isolateButton.setAttribute('aria-label', isolateButton.title); isolateButton.append(icon('focus'));
    isolateButton.addEventListener('click', () => isolate(groupEntries(group.id).filter(matches).map((entry) => entry.id)));
    heading.append(checkbox, expand, count, isolateButton);
    const description = document.createElement('p'); description.className = 'group-description'; description.textContent = group.description;
    expand.addEventListener('click', () => {
      const open = expand.getAttribute('aria-expanded') !== 'true';
      expand.setAttribute('aria-expanded', String(open)); list.hidden = !open; description.hidden = !open;
    });
    list.hidden = expand.getAttribute('aria-expanded') !== 'true'; description.hidden = list.hidden;
    for (const entry of groupEntries(group.id)) {
      const row = document.createElement('div'); row.className = 'bone-row'; row.dataset.id = entry.id;
      const toggle = document.createElement('input'); toggle.type = 'checkbox'; toggle.setAttribute('aria-label', `Show ${entry.name.toLowerCase()}`);
      toggle.addEventListener('change', () => changeVisibility([entry], toggle.checked));
      const swatch = document.createElement('span'); swatch.className = 'bone-swatch';
      let colorToggle = null;
      if (entry.kind === 'bone') {
        colorToggle = document.createElement('button'); colorToggle.className = 'bone-color-toggle';
        colorToggle.setAttribute('aria-label', `Unique colour for ${entry.name.toLowerCase()}`);
        colorToggle.append(swatch);
        colorToggle.addEventListener('click', () => { entry.useColor = !entry.useColor; applyAppearance(entry); updateCounts(); });
      }
      const name = document.createElement('button'); name.className = 'bone-name'; name.textContent = entry.name;
      name.addEventListener('click', () => select(entry.id));
      name.addEventListener('dblclick', () => { select(entry.id); isolate([entry.id]); });
      const focus = document.createElement('button'); focus.className = 'row-focus'; focus.title = `Isolate and focus ${entry.name.toLowerCase()}`; focus.setAttribute('aria-label', focus.title); focus.append(icon('focus'));
      focus.addEventListener('click', () => { select(entry.id); isolate([entry.id]); });
      row.append(toggle, colorToggle ?? swatch, name, focus); list.append(row);
      rowElements.set(entry.id, { row, checkbox: toggle, name, swatch, colorToggle });
    }
    section.append(heading, description, list); $('catalogue').insertBefore(section, $('bone-studies'));
    groupElements.set(group.id, { section, checkbox, count, expand, description, list });
  }
  updateCounts();
}

function filterCatalogue() {
  let count = 0;
  const searching = Boolean($('search').value.trim());
  entries.forEach((entry) => { const match = matches(entry); rowElements.get(entry.id).row.hidden = !match; if (match) count++; });
  groupElements.forEach((elements, id) => {
    const any = groupEntries(id).some(matches);
    elements.section.hidden = !any;
    if (searching && any) {
      if (!elements.expand.dataset.beforeSearch) elements.expand.dataset.beforeSearch = elements.expand.getAttribute('aria-expanded');
      elements.expand.setAttribute('aria-expanded', 'true'); elements.list.hidden = false; elements.description.hidden = false;
    } else if (!searching && elements.expand.dataset.beforeSearch) {
      const wasOpen = elements.expand.dataset.beforeSearch === 'true';
      elements.expand.setAttribute('aria-expanded', String(wasOpen)); elements.list.hidden = !wasOpen; elements.description.hidden = !wasOpen;
      delete elements.expand.dataset.beforeSearch;
    }
  });
  const landmarkCount = details?.filter(false) ?? 0;
  const total = entries.size + (details?.landmarks.size ?? 0);
  const matched = count + landmarkCount;
  $('no-results').hidden = count > 0;
  $('search-results').hidden = !searching;
  $('search-results').textContent = `${count} ${count === 1 ? 'structure' : 'structures'}`;
  $('show-all').textContent = searching ? 'Show matches' : 'Show all';
  $('hide-all').textContent = searching ? 'Hide matches' : 'Hide all';
  $('show-all').disabled = count === 0; $('hide-all').disabled = count === 0;
  $('catalogue-total').textContent = matched === total ? String(total) : `${matched} / ${total}`;
}

function pickHit(event) {
  const rect = canvas.getBoundingClientRect();
  pointer.set((event.x - rect.left) / rect.width * 2 - 1, -(event.y - rect.top) / rect.height * 2 + 1);
  camera.updateMatrixWorld(); scene.updateMatrixWorld(true);
  raycaster.setFromCamera(pointer, camera);
  raycaster.firstHitOnly = state.section === 'none';
  const hits = raycaster.intersectObjects([...entries.values()].filter((entry) => entry.mesh.visible).map((entry) => entry.mesh), false);
  return hits.find((hit) => state.section === 'none' || sectionPlane.distanceToPoint(hit.point) >= 0) ?? null;
}

function positionHoverTooltip() {
  const label = $('hover-label');
  if (label.hidden) return;
  const rect = canvas.getBoundingClientRect(), width = label.offsetWidth, height = label.offsetHeight;
  const x = lastPointer.x - rect.left, y = lastPointer.y - rect.top;
  const left = x + 18 + width <= rect.width - 12 ? x + 18 : x - width - 18;
  const top = y + 18 + height <= rect.height - 12 ? y + 18 : y - height - 18;
  label.style.left = `${THREE.MathUtils.clamp(left, 12, Math.max(12, rect.width - width - 12))}px`;
  label.style.top = `${THREE.MathUtils.clamp(top, 12, Math.max(12, rect.height - height - 12))}px`;
}

function showHoverTooltip(item, overlapping = []) {
  const label = $('hover-label');
  label.hidden = !item || !pointerInside || interacting || activePointers.size > 0 || !$('bone-context-menu').hidden;
  if (label.hidden) return;
  const key = JSON.stringify([item.id, item.name, item.group, overlapping]);
  if (key !== hoverTooltipKey) {
    hoverTooltipKey = key;
    label.dataset.hoverId = item.id;
    $('hover-name').textContent = item.name; $('hover-group').textContent = item.group;
    const list = $('hover-overlaps');
    list.replaceChildren(...overlapping.map((entry) => {
      const row = document.createElement('li'); row.textContent = entry.name; row.dataset.hoverId = entry.id;
      return row;
    }));
    list.hidden = overlapping.length === 0;
  }
  $('hover-dot').style.background = item.colour;
  positionHoverTooltip();
}

function setHover(id) {
  const previous = state.hovered; state.hovered = id;
  if (previous !== id) {
    if (previous) applyAppearance(entries.get(previous));
    if (id) applyAppearance(entries.get(id));
  }
  canvas.style.cursor = id ? 'pointer' : 'grab';
  const entry = entries.get(id);
  showHoverTooltip(entry ? { id, name: entry.name, group: groupName(entry.group), colour: surfaceColor(entry) } : null);
}

function toast(message) {
  clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 2600);
}

function closeBoneMenu() {
  $('bone-context-menu').hidden = true; contextBoneId = null;
}

function openBoneMenu(event) {
  event.preventDefault();
  if (contextPointer?.moved) { closeBoneMenu(); return; }
  const hit = pickHit({ x: event.clientX, y: event.clientY });
  const entry = entries.get(hit?.object.userData.entryId);
  if (!entry || entry.kind !== 'bone') { closeBoneMenu(); return; }
  contextBoneId = entry.id;
  const menu = $('bone-context-menu'), rect = canvas.getBoundingClientRect();
  $('context-bone-name').textContent = entry.name;
  $('context-hide-bone').textContent = `Hide ${entry.name.toLowerCase()}`;
  menu.hidden = false;
  menu.style.left = `${THREE.MathUtils.clamp(event.clientX - rect.left, 12, Math.max(12, rect.width - menu.offsetWidth - 12))}px`;
  menu.style.top = `${THREE.MathUtils.clamp(event.clientY - rect.top, 12, Math.max(12, rect.height - menu.offsetHeight - 12))}px`;
  $('hover-label').hidden = true;
  $('context-hide-bone').focus({ preventScroll: true });
}

function bindControls() {
  $('dataset').value = dataset;
  $('dataset').addEventListener('change', (event) => {
    const url = new URL(location.href); url.searchParams.set('dataset', event.target.value); url.searchParams.delete('study'); location.assign(url);
  });
  $('bone-colors').addEventListener('change', (event) => {
    entries.forEach((entry) => { if (entry.kind === 'bone') entry.useColor = event.target.checked; });
    updateAppearance(); updateCounts();
  });
  $('depth-shading').addEventListener('change', (event) => { state.depthShading = event.target.checked; dirty = true; });
  for (const [id, key, scale, suffix] of [['opacity', 'opacity', 100, '%'], ['explode', 'explode', 100, '%'], ['jaw', 'jaw', 1, '°'], ['gloss', 'gloss', 100, '%']]) {
    $(id).addEventListener('input', (event) => {
      state[key] = Number(event.target.value) / scale; $(`${id}-value`).textContent = `${event.target.value}${suffix}`;
      if (key === 'explode' || key === 'jaw') { updateTransforms(); if (key === 'jaw') fitVisible(); } else updateAppearance();
    });
  }
  $('section').addEventListener('change', (event) => { state.section = event.target.value; updateSection(); });
  $('section-position').addEventListener('input', (event) => { state.sectionPosition = Number(event.target.value); updateSection(); });
  $('section-flip').addEventListener('change', (event) => { state.flip = event.target.checked; updateSection(); });
  $('search').addEventListener('input', filterCatalogue);
  $('show-all').addEventListener('click', () => {
    const searching = Boolean($('search').value.trim());
    changeVisibility([...entries.values()].filter(matches), true);
    if (!searching) { restorePoint = null; fitVisible(); }
    updateCounts();
  });
  $('hide-all').addEventListener('click', () => {
    changeVisibility([...entries.values()].filter(matches), false);
  });
  $('restore').addEventListener('click', restoreView);
  $('anatomy-restore').addEventListener('click', restoreView);
  $('clear-selection').addEventListener('click', () => select(null));
  $('isolate').addEventListener('click', () => { if (state.selected) isolate([state.selected]); });
  $('focus').addEventListener('click', () => { if (state.selected) { changeVisibility([entries.get(state.selected)], true); fitVisible([state.selected]); } });
  $('hide-selected').addEventListener('click', () => { if (state.selected) { changeVisibility([entries.get(state.selected)], false); select(null); } });
  $('selected-color').addEventListener('input', (event) => {
    if (!state.selected) return;
    const entry = entries.get(state.selected); entry.color = event.target.value; entry.useColor = true;
    applyAppearance(entry); updateCounts();
  });
  $('selected-opacity').addEventListener('input', (event) => {
    if (!state.selected) return;
    const entry = entries.get(state.selected); entry.opacity = Number(event.target.value) / 100;
    $('selected-opacity-value').textContent = `${event.target.value}%`; applyAppearance(entry);
  });
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
  $('fit').addEventListener('click', () => fitVisible());
  $('reset-camera').addEventListener('click', () => setView('oblique'));
  $('reset').addEventListener('click', resetAll);
  $('screenshot').addEventListener('click', () => {
    render();
    canvas.toBlob((blob) => {
      if (!blob) { toast('Image could not be saved.'); return; }
      const url = URL.createObjectURL(blob); const link = document.createElement('a');
      link.href = url; link.download = 'skull-atlas.png'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000); toast('Image saved.');
    }, 'image/png');
  });
  canvas.addEventListener('pointermove', (event) => {
    lastPointer = { x: event.clientX, y: event.clientY }; pointerInside = true; pointerDirty = true;
    if (pointerStart && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 5) pointerStart.moved = true;
    if (contextPointer && (event.buttons & 2) && Math.hypot(event.clientX - contextPointer.x, event.clientY - contextPointer.y) > 5) {
      contextPointer.moved = true; closeBoneMenu();
    }
  });
  canvas.addEventListener('pointerleave', () => { pointerInside = false; setHover(null); details?.setRegionHover(null); });
  canvas.addEventListener('pointerdown', (event) => {
    contextPointer = event.button === 2 ? { x: event.clientX, y: event.clientY, moved: false } : null;
    closeBoneMenu();
    activePointers.add(event.pointerId);
    if (activePointers.size > 1 && pointerStart) pointerStart.moved = true;
    if (activePointers.size === 1 && event.button === 0) pointerStart = { x: event.clientX, y: event.clientY, id: event.pointerId, moved: false };
  });
  canvas.addEventListener('pointerup', (event) => {
    activePointers.delete(event.pointerId);
    if (pointerStart?.id === event.pointerId && !pointerStart.moved && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) < 6) {
      const hit = pickHit({ x: event.clientX, y: event.clientY });
      const regionId = details?.hoverAt(hit, { x: event.clientX, y: event.clientY }).id;
      if (regionId) details.selectLandmark(regionId);
      else { details?.clearSelection(); select(hit?.object.userData.entryId ?? null); }
    }
    if (!activePointers.size) pointerStart = null;
  });
  canvas.addEventListener('pointercancel', (event) => { activePointers.delete(event.pointerId); pointerStart = null; });
  canvas.addEventListener('contextmenu', openBoneMenu);
  $('context-hide-bone').addEventListener('click', () => {
    const entry = entries.get(contextBoneId); closeBoneMenu();
    if (!entry) return;
    changeVisibility([entry], false);
    if (state.selected === entry.id) select(null);
    details?.setRegionHover(null); setHover(null); canvas.focus({ preventScroll: true });
  });
  document.addEventListener('pointerdown', (event) => { if (!$('bone-context-menu').contains(event.target)) closeBoneMenu(); });
  window.addEventListener('scroll', closeBoneMenu, true);
  window.addEventListener('resize', closeBoneMenu);
  canvas.addEventListener('webglcontextlost', (event) => { event.preventDefault(); $('status').textContent = 'Graphics context lost. Reload to restore the viewer.'; $('status-dot').className = 'status-dot error'; });
  canvas.addEventListener('webglcontextrestored', () => location.reload());
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('bone-context-menu').hidden) { closeBoneMenu(); canvas.focus({ preventScroll: true }); event.preventDefault(); return; }
    if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input, select, textarea, button, summary')) return;
    if (event.key === '/') { event.preventDefault(); $('search').focus(); }
    if (event.key === 'Escape') { select(null); details?.clearSelection(); }
    if (event.key.toLowerCase() === 'f') fitVisible();
    if (event.key.toLowerCase() === 'r') setView('oblique');
    if (event.key.toLowerCase() === 'i' && state.selected) isolate([state.selected]);
    if (event.key.toLowerCase() === 'h' && state.selected) { changeVisibility([entries.get(state.selected)], false); select(null); }
  });
}

function resetAll() {
  closeBoneMenu();
  details?.reset();
  const defaults = manifest.defaults ?? { colors: false, gloss: 25, ambientOcclusion: true };
  Object.assign(state, { colors: defaults.colors, depthShading: defaults.ambientOcclusion, opacity: 1, explode: 0, jaw: 0, gloss: defaults.gloss / 100, section: 'none', sectionPosition: 50, flip: false });
  $('bone-colors').checked = state.colors; $('depth-shading').checked = state.depthShading;
  for (const [id, value, suffix] of [['opacity', 100, '%'], ['explode', 0, '%'], ['jaw', 0, '°'], ['gloss', defaults.gloss, '%']]) { $(id).value = value; $(`${id}-value`).textContent = `${value}${suffix}`; }
  $('section').value = 'none'; $('section-position').value = 50; $('section-flip').checked = false;
  entries.forEach((entry) => { entry.visible = entry.defaultVisible; entry.color = entry.defaultColor; entry.useColor = defaults.colors; entry.opacity = 1; });
  restorePoint = null; $('search').value = ''; filterCatalogue(); select(null); setHover(null);
  updateAppearance(); updateTransforms(); updateCounts(); setView('oblique');
}

function render() {
  details?.syncTransforms();
  const distance = camera.position.distanceTo(controls.target);
  camera.near = Math.max(0.01, Math.min(5, distance * 0.01));
  camera.far = Math.max(1500, distance + 1000);
  camera.updateProjectionMatrix();
  lightRig.quaternion.copy(camera.quaternion);
  const transparent = state.opacity < 0.999 || [...entries.values()].some((entry) => entry.visible && entry.opacity < 0.999) || [...(details?.cavities.values() ?? [])].some((entry) => entry.visible);
  ambientOcclusion.enabled = state.depthShading && !transparent;
  ambientOcclusion.moving = interacting;
  $('depth-shading').disabled = transparent;
  $('depth-shading-note').hidden = !transparent;
  if (ambientOcclusion.enabled) {
    const size = visibleBox().getSize(new THREE.Vector3());
    const radius = Math.max(0.05, Math.min(6, Math.max(size.x, size.y, size.z) * 0.025));
    ambientOcclusion.updateGtaoMaterial({ radius, thickness: radius * 0.4 });
  }
  renderer.info.reset(); composer.render(); pinsDirty = false; details?.update(); dirty = false;
}

const lightRig = new THREE.Group();
scene.add(new THREE.HemisphereLight(0xe6e8df, 0x383b36, 0.8));
for (const [color, intensity, position] of [[0xfff0d9, 3.0, [-200, 250, 350]], [0xdce9ff, 0.65, [250, 50, 150]], [0xe1e8e3, 1.6, [0, 150, -250]]]) {
  const light = new THREE.DirectionalLight(color, intensity); light.position.set(...position); lightRig.add(light);
}
scene.add(lightRig);

async function init() {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.info.autoReset = false;
  composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4, stencilBuffer: true }));
  composer.addPass(new RenderPass(scene, camera));
  ambientOcclusion = new BoneOcclusionPass(scene, camera, 1, 1, undefined, { radius: 5, thickness: 2, samples: 16, screenSpaceRadius: false, scale: 1 });
  ambientOcclusion.normalMaterial.side = THREE.DoubleSide;
  ambientOcclusion.blendIntensity = 0.8;
  composer.addPass(ambientOcclusion);
  composer.addPass(new OutputPass());
  controls = new TrackballControls(camera, canvas);
  controls.rotateSpeed = 3.0; controls.zoomSpeed = 1.15; controls.panSpeed = 0.5;
  controls.staticMoving = true; controls.minDistance = 1.5; controls.maxDistance = 3000;
  controls.addEventListener('change', () => { dirty = true; pointerDirty = true; });
  controls.addEventListener('start', () => { interacting = true; setHover(null); details?.setRegionHover(null); });
  controls.addEventListener('end', () => { interacting = false; pointerDirty = true; dirty = true; });
  const manifestPaths = { ct: 'assets/skull-ct/manifest.json', microct: 'assets/bone-microct/manifest.json' };
  const response = await fetchAsset(manifestPaths[dataset]);
  if (!response.ok) throw new Error(`Model manifest returned HTTP ${response.status}`);
  manifest = await response.json();
  document.documentElement.dataset.dataset = dataset;
  $('dataset-caption').textContent = 'OUVC 10503 · CT + estimated atlas additions';
  $('specimen-label').textContent = 'CT ANATOMY / OUVC 10503';
  $('model-overview').textContent = `All 22 skull bones and original dental arches from the Ohio University CT specimen ${manifest.source.specimen}. Six auditory ossicles and the hyoid reuse reference-atlas geometry, fitted into this display as estimated cross-specimen placements.`;
  const nativeTriangles = manifest.structures.reduce((sum, entry) => sum + entry.triangles, 0);
  $('model-quality').textContent = `${nativeTriangles.toLocaleString('en-GB')} original CT triangles. The highest-detail published export is retained, with original surface normals and seams restored for shading. No subdivision, geometric smoothing or synthetic pores add anatomical detail. Finer geometry requires higher-resolution source data.`;
  $('model-cavity-info').hidden = dataset !== 'ct';
  $('model-download-note').textContent = dataset === 'ct' ? 'The GLB contains the 22 CT bones and two dental arches. The ear bones and hyoid use a separate supplementary asset in the viewer.' : 'The GLB contains this bone fragment; it is separate from the complete CT skull.';
  if (dataset === 'microct') {
    $('dataset-caption').textContent = 'Independent micro-CT study sample · 125 µm voxels';
    $('specimen-label').textContent = 'BONE MICROSTRUCTURE / HALLE MICRO-CT';
    document.querySelector('.stage-header h2').textContent = 'Inside skull bone';
    $('model-overview').textContent = 'A real skull-vault fragment showing compact tables and porous diploë, extracted at native micro-CT resolution from a separate donor. Rectangular cut faces expose the interior. Bone layers are visible in the source geometry but are not individually segmented tissue labels.';
    $('model-quality').textContent = `${nativeTriangles.toLocaleString('en-GB')} triangles extracted at native 0.125 mm voxel spacing. This is a small vault fragment from a different donor, not a higher-resolution scan of the complete CT skull. Its compact tables and porous diploë come from the scanned geometry.`;
    $('model-landmark-methods').textContent = 'The three layer guides identify study locations on the scanned fragment. They do not define segmented tissue boundaries.';
    for (const id of ['model-landmark-sources', 'model-fossa-info', 'model-catalogue-credits']) $(id).hidden = true;
    $('model-orientation').textContent = 'The fragment has its outer table upward and an exposed cut face toward the viewer. Named directions refer to this study orientation.';
    $('jaw').disabled = true; $('explode').disabled = true;
    for (const button of document.querySelectorAll('[data-view]')) {
      const label = { anterior: 'Cut face', posterior: 'Opposite', left: 'Side 1', right: 'Side 2', superior: 'Outer table', inferior: 'Inner table' }[button.dataset.view];
      if (label) button.textContent = label;
    }
  }
  const credit = document.createElement('a'); credit.href = manifest.source.url; credit.target = '_blank'; credit.rel = 'noreferrer'; credit.textContent = manifest.source.name;
  $('model-provenance').replaceChildren(document.createTextNode('Anatomy source: '), credit, document.createTextNode(dataset === 'ct' ? '. CT: 625 µm slice thickness. Full source geometry with original surface shading restored; no subdivision or synthetic pores.' : '.'));
  if (dataset === 'ct') {
    const associatedCredit = document.createElement('a');
    associatedCredit.href = 'assets/skull-associated/ATTRIBUTION.txt'; associatedCredit.textContent = 'Ear/hyoid source credits';
    $('model-provenance').append(document.createTextNode(' '), associatedCredit, document.createTextNode(' · estimated cross-specimen placement.'));
  }
  $('model-scale').textContent = dataset === 'ct' ? 'The source file does not declare a valid physical scale. Section coordinates are display units, not calibrated millimetres.' : dataset === 'microct' ? 'Native 0.125 mm voxels. Fragment axes describe the study orientation; patient laterality is not inferred. This sample is separate from OUVC 10503.' : '';
  $('model-credits').href = manifest.source.license;
  const surface = manifest.surface;
  if (surface && surface.native_model_sha256 !== manifest.sha256) throw new Error('Restored surface shading belongs to a different CT skull.');
  const displayedModel = surface?.model ?? manifest.model;
  $('model-download').href = displayedModel;
  $('model-download').download = dataset === 'ct' ? 'skull-ct.glb' : 'skull-bone-microct.glb';
  if (dataset === 'ct') $('model-download').lastChild.textContent = 'Download CT skull · GLB';
  const defaults = manifest.defaults ?? { colors: false, gloss: 25, ambientOcclusion: true };
  state.colors = defaults.colors; state.gloss = defaults.gloss / 100; state.depthShading = defaults.ambientOcclusion;
  $('bone-colors').checked = state.colors; $('gloss').value = defaults.gloss; $('gloss-value').textContent = `${defaults.gloss}%`;
  const gltf = await new GLTFLoader().loadAsync(displayedModel, (event) => {
    if (event.total) $('load-progress').value = event.loaded / event.total * 100;
  });
  const sourceMeshes = new Map();
  gltf.scene.traverse((object) => { if (object.isMesh) sourceMeshes.set(object.name, object); });
  if (sourceMeshes.size !== manifest.structures.length) throw new Error('The model and catalogue have different structure counts.');
  if (dataset === 'ct') {
    const response = await fetchAsset('assets/skull-associated/manifest.json');
    if (!response.ok) throw new Error('Associated-bone registration is unavailable.');
    const associated = await response.json();
    if (associated.target_model_sha256 !== manifest.sha256) throw new Error('Associated bones belong to a different skull.');
    const atlas = await new GLTFLoader().loadAsync(associated.model);
    const imported = new Map(); atlas.scene.traverse((object) => { if (object.isMesh) imported.set(object.name, object); });
    for (const record of associated.structures) {
      const original = imported.get(record.id);
      if (!original) throw new Error(`Missing associated bone: ${record.id}`);
      const mesh = new THREE.Mesh(original.geometry.clone().applyMatrix4(new THREE.Matrix4().fromArray(record.reference_transform.flat()).transpose()));
      mesh.name = record.id; sourceMeshes.set(record.id, mesh);
    }
    atlas.scene.traverse((object) => { if (object.isMesh) { object.geometry.dispose(); object.material.dispose(); } });
    manifest.groups.push(...associated.groups); manifest.structures.push(...associated.structures);
  }
  for (const record of manifest.structures) {
    const mesh = sourceMeshes.get(record.id);
    if (!mesh) throw new Error(`Missing mesh: ${record.name}`);
    mesh.material.dispose();
    mesh.material = new THREE.MeshPhysicalMaterial({ side: THREE.DoubleSide, metalness: 0, clippingPlanes: [] });
    mesh.geometry.userData.windingCorrections = alignTriangleWinding(mesh.geometry);
    mesh.geometry.computeBoundingBox(); mesh.geometry.computeBoundingSphere();
    computeBoundsTree.call(mesh.geometry, { indirect: true, maxLeafTris: 10 });
    mesh.raycast = acceleratedRaycast;
    mesh.userData.entryId = record.id;
    const entry = { ...record, mesh, defaultVisible: record.visible, defaultColor: record.color, useColor: defaults.colors, opacity: 1 };
    entries.set(record.id, entry);
    scene.add(mesh);
  }
  skullBounds = visibleBox([...entries.values()].filter((entry) => ['cranial', 'facial'].includes(entry.group)).map((entry) => entry.id));
  if (skullBounds.isEmpty()) skullBounds = visibleBox();
  const jawBounds = entries.get('mandible')?.mesh.geometry.boundingBox;
  if (jawBounds) hinge.set(0, jawBounds.max.y - 4, jawBounds.min.z + 10);
  entries.forEach((entry) => { if (isJaw(entry)) { jawPivot.add(entry.mesh); entry.mesh.position.copy(hinge).negate(); } });
  buildCatalogue(); bindControls(); updateAppearance(); updateTransforms();
  const resize = () => {
    const { width, height } = canvas.getBoundingClientRect();
    renderer.setSize(width, height, false); composer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix();
    controls.handleResize(); fitVisible(); dirty = true;
  };
  new ResizeObserver(resize).observe(canvas);
  resize(); setView('oblique');
  details = new AnatomyDetails({
    dataset, manifest, scene, camera, canvas, entries, sectionPlane,
    catalogueChanged: filterCatalogue,
    invalidate: (repick = false) => { dirty = true; if (repick) pointerDirty = true; },
    invalidatePins: () => { pinsDirty = true; },
    isInteracting: () => interacting,
    sectionEnabled: () => state.section !== 'none',
    clearBoneSelection: () => select(null),
    hover: (id, name) => { setHover(id); if (id && name) $('hover-name').textContent = name; },
    regionHover: (region) => {
      setHover(null); canvas.style.cursor = region ? 'pointer' : 'grab';
      if (region?.source === 'canvas') showHoverTooltip(region, region.overlapping);
    },
    isolateOwners: (ids) => isolate(ids),
    isolateCavity: () => isolate([], true),
    restoreSkull: restoreView,
    frameMeshes: (meshes, view) => { setView(view, false); fitVisible(undefined, meshes); },
    frameRegion: (meshes, view, internalCenter = null) => {
      scene.updateMatrixWorld(true);
      const box = new THREE.Box3();
      meshes.forEach((mesh) => box.union(mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld)));
      if (internalCenter) view = internalCenter.clone().sub(box.getCenter(new THREE.Vector3())).normalize().toArray();
      setView(view, false);
      const minimum = dataset === 'microct' ? 6 : 24;
      const size = box.getSize(new THREE.Vector3()).max(new THREE.Vector3(minimum, minimum, minimum));
      const context = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z));
      context.geometry.computeBoundingBox(); context.position.copy(box.getCenter(new THREE.Vector3())); context.updateMatrixWorld();
      const owners = [...new Set(meshes.map((mesh) => mesh.userData.regionOwner))]
        .map((id) => entries.get(id)?.mesh).filter((mesh) => mesh?.visible);
      fitVisible(undefined, [...meshes, context], owners); context.geometry.dispose(); context.material.dispose();
    },
    focusLocation: (point, view) => {
      setView(view, false);
      const size = dataset === 'microct' ? 8 : 38;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(size, size, size));
      mesh.geometry.computeBoundingBox(); mesh.position.copy(point); mesh.updateMatrixWorld();
      fitVisible(undefined, [mesh]); mesh.geometry.dispose(); mesh.material.dispose();
    },
    cavityContext: (active) => {
      if (active && cavityOpacity === null) { cavityOpacity = state.opacity; state.opacity = 0.18; }
      else if (!active && cavityOpacity !== null) { if (state.opacity === 0.18) state.opacity = cavityOpacity; cavityOpacity = null; }
      $('opacity').value = Math.round(state.opacity * 100); $('opacity-value').textContent = `${Math.round(state.opacity * 100)}%`;
      updateAppearance();
    },
  });
  await details.load();
  // Compile for the colour pass's render target and scene lights before mouse
  // input can request its first highlight. Restore the target before awaiting.
  const previousTarget = renderer.getRenderTarget();
  let highlightShaders;
  try {
    renderer.setRenderTarget(composer.readBuffer);
    highlightShaders = details.regions.prepareShaders(renderer, camera, scene);
  } finally { renderer.setRenderTarget(previousTarget); }
  await highlightShaders;
  if (dataset === 'microct') {
    setView('oblique');
    details.showLandmarks();
  }
  const study = new URLSearchParams(location.search).get('study');
  if (study === 'mapped-regions' && dataset !== 'microct') details.exploreMapped();
  if (dataset === 'ct' && ['ossicles', 'hyoid'].includes(study)) {
    const group = groupElements.get(study === 'ossicles' ? 'ossicles' : 'associated');
    group.expand.setAttribute('aria-expanded', 'true'); group.list.hidden = false; group.description.hidden = false;
  }
  $('control-panel').disabled = false;
  $('loading').hidden = true; $('status-dot').classList.add('ready');
  document.documentElement.dataset.ready = 'true';
  // Small inspection surface for repeatable browser verification of the actual loaded scene.
  window.skullAtlas = Object.freeze({
    snapshot: () => ({
      ready: isReady(), selected: state.selected, hovered: state.hovered,
      dataset, source: manifest.source.name, depthShading: ambientOcclusion.enabled,
      anatomy: details.snapshot(),
      explode: state.explode, jaw: state.jaw, section: state.section,
      camera: camera.position.toArray(), target: controls.target.toArray(),
      viewOffset: camera.view ? [camera.view.offsetX, camera.view.offsetY] : [0, 0],
      structures: [...entries.values()].map((entry) => {
        const box = entry.mesh.geometry.boundingBox.clone().applyMatrix4(entry.mesh.matrixWorld);
        return { id: entry.id, group: entry.group, visible: entry.visible, rendered: entry.mesh.visible, useColor: entry.useColor, color: entry.color, displayedColor: `#${entry.mesh.material.color.getHexString()}`, emissiveColor: `#${entry.mesh.material.emissive.getHexString()}`, emissiveIntensity: entry.mesh.material.emissiveIntensity, opacity: entry.mesh.material.opacity, triangles: entry.mesh.geometry.index.count / 3, windingCorrections: entry.mesh.geometry.userData.windingCorrections, bounds: [box.min.toArray(), box.max.toArray()] };
      }),
      drawCalls: renderer.info.render.calls, triangles: renderer.info.render.triangles,
    }),
    project: (id) => {
      const entry = entries.get(id); if (!entry) return null;
      scene.updateMatrixWorld(true); camera.updateMatrixWorld();
      const point = entry.mesh.geometry.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(entry.mesh.matrixWorld).project(camera);
      const rect = canvas.getBoundingClientRect();
      return { x: rect.left + (point.x + 1) / 2 * rect.width, y: rect.top + (1 - point.y) / 2 * rect.height };
    },
    projectLandmark: (id) => details.projectLandmark(id),
    regionGeometry: (id) => details.regions.audit(id),
    regionAt: (x, y) => details.regionAtHit(pickHit({ x, y })),
    projectPoint: (coordinates, owner) => {
      scene.updateMatrixWorld(true); camera.updateMatrixWorld();
      const point = new THREE.Vector3(...coordinates);
      if (owner) point.applyMatrix4(entries.get(owner).mesh.matrixWorld);
      point.project(camera);
      const rect = canvas.getBoundingClientRect();
      return { x: rect.left + (point.x + 1) * rect.width / 2, y: rect.top + (1 - point.y) * rect.height / 2 };
    },
  });
  renderer.setAnimationLoop(() => {
    controls.update();
    if (pointerDirty && pointerInside && !interacting && !activePointers.size && $('bone-context-menu').hidden) {
      const hit = pickHit(lastPointer), preview = details?.hoverAt(hit, lastPointer);
      if (preview?.id) details.setRegionHover(preview.id, { source: 'canvas', overlaps: preview.overlaps });
      else { details?.setRegionHover(null); setHover(hit?.object.userData.entryId ?? null); }
      positionHoverTooltip();
      pointerDirty = false;
    }
    if (dirty) render();
    else if (pinsDirty) { pinsDirty = false; details?.update(); }
  });
  render();
}

init().catch((error) => {
  console.error(error);
  $('loading').hidden = false; $('loading').querySelector('h3').textContent = 'Unable to load the skull';
  $('loading-message').textContent = `${error.message}. Serve the project using scripts/serve_viewer.py, then reload.`;
  $('load-progress').hidden = true;
  document.querySelector('.loader-ring').hidden = true;
  $('status').textContent = 'Model unavailable'; $('status-dot').classList.add('error');
});
