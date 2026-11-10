import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { SurfaceRegions, regionColour } from './surface-regions.js';
import { fetchAsset } from './asset-url.js';

const $ = (id) => document.getElementById(id);
const POINT_HOVER_RADIUS = 14; // CSS pixels, independent of zoom and display density.

export class AnatomyDetails {
  constructor(context) {
    this.context = context;
    this.landmarks = new Map();
    this.cavities = new Map();
    this.rows = new Map();
    this.landmarkGroups = [];
    this.searchText = new Map();
    this.enabled = new Set();
    this.visiblePins = new Set();
    this.selected = null;
    this.categoryNames = new Map();
    this.raycaster = new THREE.Raycaster();
    this.raycaster.firstHitOnly = true;
    this.point = new THREE.Vector3();
    this.regions = new SurfaceRegions(context);
    this.previewQueue = new Set();
    this.preparing = false;
    this.hovered = null;
    this.hoverSource = null;
    this.hoverOverlaps = [];
    this.locationState = null;
    this.compact = window.matchMedia('(max-width: 700px)');
    $('anatomy-information').open = !this.compact.matches;
    this.compact.addEventListener('change', () => { $('anatomy-information').open = !this.compact.matches; this.context.invalidate(); });
  }

  async load() {
    const { dataset, manifest, entries } = this.context;
    let catalogue;
    if (dataset === 'microct') {
      catalogue = {
        categories: [{ id: 'microstructure', name: 'Internal bone layers' }],
        landmarks: manifest.landmarks.map((entry) => ({
          ...entry, owners: entry.boneIds, point: entry.position, category: 'microstructure',
          positions: { microct: { point: entry.position, approximate: true, method: 'micro-CT study pin' } },
          source_url: manifest.source.url, view: 'anterior',
        })),
      };
      $('landmark-note').textContent = 'The micro-CT source labels bone only. Layer names have optional location guides; separate tissue boundaries are unavailable.';
      $('landmark-panel').open = true;
    } else {
      const response = await fetchAsset('assets/landmarks/catalogue.json');
      if (!response.ok) throw new Error(`Landmark catalogue returned HTTP ${response.status}`);
      catalogue = await response.json();
    }
    this.categoryNames = new Map(catalogue.categories.map((entry) => [entry.id, entry.name]));
    for (const record of catalogue.landmarks) {
      let placement = record.positions?.[dataset];
      const owners = record.owners.filter((id) => entries.has(id));
      const imported = owners.length && entries.get(owners[0]).reference_transform;
      if (!placement && imported && record.positions?.reference) {
        const source = record.positions.reference;
        const matrix = new THREE.Matrix4().fromArray(imported.flat()).transpose();
        placement = { ...source, point: new THREE.Vector3(...source.point).applyMatrix4(matrix).toArray(),
          method: 'registered-reference-bone', approximate: true, imported_reference: true,
          label_fit: { ...source.label_fit, status: 'approximate', basis: 'registered-reference-bone',
            source_model_sha256: entries.get(owners[0]).source_model_sha256 } };
        if (source.path) placement.path = source.path.map((point) => new THREE.Vector3(...point).applyMatrix4(matrix).toArray());
      }
      if (!placement?.point || !owners.length) continue;
      if (placement.native_point_fit) {
        if (placement.native_point_fit.source_model_sha256 !== this.context.manifest.sha256) throw new Error('Point fit belongs to a different skull model.');
        this.regions.pointFits.set(record.id, placement);
      }
      if (placement.label_fit && placement.label_fit.source_model_sha256 !== (imported ? entries.get(owners[0]).source_model_sha256 : this.context.manifest.sha256)) throw new Error('Label fit belongs to a different skull model.');
      if (dataset !== 'microct' && !placement.label_fit && !placement.native_point_fit) {
        const surface = entries.get(owners[0]).mesh.geometry.boundsTree.closestPointToPoint(new THREE.Vector3(...placement.point));
        if (surface) placement = { ...placement, point: surface.point.toArray(), surfaceDistance: surface.distance };
      }
      this.landmarks.set(record.id, { ...record, owners, placement, pointFit: placement.native_point_fit });
      if (record.category === 'landmarks') this.regions.pointLandmarks.add(record.id);
    }
    await this.regions.loadSources(this.landmarks);
    for (const entry of this.landmarks.values()) {
      const mapped = this.regions.registeredPlacement(entry.id);
      if (mapped) {
        entry.placement = { ...entry.placement, point: mapped.surface_anchor, approximate: true, method: 'registered-author-region' };
        entry.mappedSource = mapped;
        entry.mappedOwners = [...new Set(this.regions.registeredDomains.filter((part) => part.feature_id === entry.id).map((part) => part.owner))];
        entry.partialMapping = entry.owners.some((owner) => !entry.mappedOwners.includes(owner));
      }
    }
    await this.regions.startWorker();
    const supplied = [...this.landmarks.keys()].filter((id) => this.regions.hasSuppliedBoundary(id)).length;
    const mappedCount = [...this.landmarks.keys()].filter((id) => this.regions.hasRegisteredBoundary(id)).length;
    $('landmark-coverage').textContent = supplied
      ? `${supplied} supplied surface selections. Other landmarks need a fitted source region or remain location guides. Supplied selections are not independently validated anatomy.`
      : dataset === 'microct'
        ? 'This scan labels bone only. The three layer locations are guides; no separate layer boundaries are painted.'
        : 'This CT source supplies bone and cavity geometry, but no named landmark boundaries. Fitted source regions and location guides are available separately.';
    if (mappedCount) $('landmark-coverage').textContent += ` ${mappedCount} source regions have been mapped from other skulls; their fit is estimated.`;
    if (this.regions.pointFits.size) $('landmark-coverage').textContent += ` ${this.regions.pointFits.size} point landmarks are fitted to this skull's native geometry.`;
    $('mapped-region-controls').hidden = !mappedCount && !this.regions.pointFits.size;
    $('model-coverage').textContent = dataset === 'microct'
      ? `${entries.size} bone-fragment mesh and ${this.landmarks.size} layer location guides.`
      : `${[...entries.values()].filter((entry) => entry.group !== 'teeth').length} bones: eight cranial, 14 facial, six auditory ossicles and the hyoid. Teeth are grouped into two dental arches, not separate tooth meshes. The catalogue contains ${this.landmarks.size} landmark entries, including point guides, organised by type and bone.`;
    this.buildLandmarks();
    this.bindControls();
    $('cavity-panel').hidden = dataset !== 'ct';
    if (dataset === 'ct') await this.loadCavities();
    this.filter();
  }

  buildLandmarks() {
    const { entries } = this.context;
    const categories = new Map(), owners = new Map();
    const makeGroup = (id, name, parent, level) => {
      const node = document.createElement('details'); node.className = `landmark-subgroup landmark-level-${level}`;
      node.dataset.landmarkGroup = id;
      const summary = document.createElement('summary'); summary.className = 'group-heading';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
      checkbox.setAttribute('aria-label', `Highlight all ${name.toLowerCase()}${level === 2 ? ` landmarks in ${parent.name.toLowerCase()}` : ''}`);
      checkbox.addEventListener('click', (event) => event.stopPropagation());
      const chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); chevron.classList.add('catalogue-chevron');
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', '#icon-chevron'); chevron.append(use);
      const title = document.createElement('span'); title.className = 'group-label'; title.textContent = name;
      const count = document.createElement('span'); count.className = 'group-count';
      summary.append(checkbox, chevron, title, count);
      const list = document.createElement('div'); list.className = 'landmark-branch';
      node.append(summary, list); (parent?.list ?? $('landmark-list')).append(node);
      const group = { node, checkbox, count, list, ids: [], name };
      checkbox.addEventListener('change', () => {
        const matches = new Set(this.filteredLandmarks.map((entry) => entry.id));
        this.setMarkers(group.ids.filter((entryId) => matches.has(entryId)), checkbox.checked);
      });
      this.landmarkGroups.push(group);
      return group;
    };
    for (const [id, name] of this.categoryNames) {
      if ([...this.landmarks.values()].some((entry) => entry.category === id)) categories.set(id, makeGroup(id, name, null, 1));
    }
    for (const entry of this.landmarks.values()) {
      const category = categories.get(entry.category);
      const ownerId = entry.owners.length === 1 ? entry.owners[0] : 'multiple-bones';
      const ownerKey = `${entry.category}:${ownerId}`;
      const ownerName = ownerId === 'multiple-bones' ? 'Multiple bones' : entries.get(ownerId).name;
      if (!owners.has(ownerKey)) owners.set(ownerKey, makeGroup(ownerKey, ownerName, category, 2));
      const owner = owners.get(ownerKey);
      category.ids.push(entry.id); owner.ids.push(entry.id);
      const ownerText = entry.owners.map((id) => {
        const bone = entries.get(id), group = this.context.manifest.groups.find((item) => item.id === bone.group);
        return `${bone.name} ${bone.side} ${group?.name ?? bone.group}`;
      }).join(' ');
      this.searchText.set(entry.id, `landmarks ${entry.name} ${(entry.aliases ?? []).join(' ')} ${entry.description} ${entry.category} ${category.name} ${ownerName} ${ownerText}`.toLowerCase());
      const row = document.createElement('div'); row.className = 'landmark-row'; row.dataset.landmark = entry.id;
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
      checkbox.setAttribute('aria-label', `Highlight ${entry.name.toLowerCase()}`);
      checkbox.addEventListener('change', () => this.toggleMarker(entry.id, checkbox.checked));
      const button = document.createElement('button'); button.className = 'landmark-name';
      button.setAttribute('aria-label', entry.name);
      const title = document.createElement('span'); title.textContent = entry.name;
      const subtitle = document.createElement('small');
      subtitle.textContent = entry.owners.map((id) => entries.get(id).name).join(' · ');
      if (entry.mappedSource) subtitle.textContent += entry.partialMapping ? ' · mapped source (partial)' : ' · mapped source';
      if (entry.pointFit) subtitle.textContent += entry.placement.method === 'native-contour-extremum' ? ' · fitted midline contour · point' : ' · fitted suture junction · point';
      if (!entry.mappedSource && !entry.pointFit && !this.regions.hasSuppliedBoundary(entry.id)
          && !this.regions.apertureDomains.some((domain) => domain.feature_id === entry.id)
          && !(entry.category === 'sutures' && entry.owners.length > 1)) subtitle.textContent += ' · location guide only';
      subtitle.title = subtitle.textContent;
      button.append(title, subtitle);
      button.addEventListener('click', () => this.selectLandmark(entry.id));
      button.addEventListener('pointerenter', () => this.setRegionHover(entry.id));
      button.addEventListener('pointerleave', () => this.setRegionHover(null));
      button.addEventListener('focus', () => this.setRegionHover(entry.id));
      button.addEventListener('blur', () => { if (this.hovered === entry.id) this.setRegionHover(null); });
      row.append(checkbox, button); owner.list.append(row);
      const pin = document.createElement('button'); pin.className = 'anatomy-pin';
      pin.dataset.landmark = entry.id; pin.hidden = true; pin.title = entry.name;
      pin.setAttribute('aria-label', `Locate ${entry.name.toLowerCase()}`);
      pin.setAttribute('aria-pressed', 'false');
      const label = document.createElement('span'); label.textContent = entry.name; pin.append(label);
      pin.addEventListener('click', () => this.selectLandmark(entry.id));
      pin.addEventListener('pointerenter', () => this.setRegionHover(entry.id));
      pin.addEventListener('pointerleave', () => this.setRegionHover(null));
      $('landmark-overlay').append(pin);
      this.rows.set(entry.id, { row, checkbox, button, pin, path: null });
    }
    $('landmark-total').textContent = String(this.landmarks.size);
  }

  bindControls() {
    $('landmark-search').addEventListener('input', () => this.filter());
    $('landmark-mapped-only').disabled = !$('landmark-estimates').checked;
    $('landmark-estimates').addEventListener('change', () => {
      this.previewQueue.clear(); this.setRegionHover(null);
      if (!$('landmark-estimates').checked) {
        $('landmark-mapped-only').checked = false;
        for (const id of this.enabled) if (!this.regions.hasSuppliedBoundary(id)) this.enabled.delete(id);
        if (this.selected?.kind === 'landmark' && !this.regions.hasSuppliedBoundary(this.selected.id)) this.clearSelection();
      }
      $('landmark-mapped-only').disabled = !$('landmark-estimates').checked;
      this.filter(); this.context.invalidate(true);
    });
    $('landmark-mapped-only').addEventListener('change', () => this.filter());
    $('landmark-explore-mapped').addEventListener('click', () => this.exploreMapped());
    $('landmark-pins').addEventListener('click', (event) => event.stopPropagation());
    $('landmark-pins').addEventListener('change', (event) => this.setMarkers(this.filtered().map((entry) => entry.id), event.target.checked));
    $('landmark-labels').addEventListener('change', () => {
      $('landmark-overlay').classList.toggle('show-names', $('landmark-labels').checked);
      this.context.invalidate();
    });
    $('landmark-markers').addEventListener('change', () => {
      $('landmark-overlay').classList.toggle('show-pins', $('landmark-markers').checked);
      this.context.invalidate();
    });
    for (const id of ['landmark-locate', 'landmark-xray']) $(id).addEventListener('change', () => this.context.invalidate());
    $('landmark-clear').addEventListener('click', () => {
      this.enabled.clear(); this.setRegionHover(null); this.clearSelection(); this.updateControls(); this.context.invalidate();
    });
    $('anatomy-close').addEventListener('click', () => this.clearSelection());
    $('anatomy-focus').addEventListener('click', () => this.focus());
    $('anatomy-isolate').addEventListener('click', () => this.reveal());
    $('anatomy-hide').addEventListener('click', () => {
      if (this.selected?.kind === 'cavity') this.toggleCavity(this.selected.id, false);
      else if (this.selected) this.toggleMarker(this.selected.id, false);
      this.clearSelection();
    });
    $('cavity-hide-all').addEventListener('click', () => {
      this.cavities.forEach((entry) => { entry.visible = false; entry.checkbox.checked = false; });
      if (this.selected?.kind === 'cavity') this.clearSelection();
      this.context.cavityContext(false); this.context.invalidate();
      if (![...this.context.entries.values()].some((entry) => entry.visible)) this.context.restoreSkull();
    });
  }

  showLandmarks() {
    $('landmark-panel').open = true;
    this.setRegionHover(null);
    this.prepareFiltered();
  }

  filtered() {
    const words = $('landmark-search').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    return [...this.landmarks.values()].filter((entry) => {
      if (!this.canHighlight(entry.id)) return false;
      if ($('landmark-mapped-only').checked && !this.regions.hasRegisteredBoundary(entry.id) && !this.regions.pointFits.has(entry.id)) return false;
      return words.every((word) => this.searchText.get(entry.id).includes(word));
    });
  }

  canHighlight(id) {
    return this.landmarks.has(id) && ($('landmark-estimates').checked || this.regions.hasSuppliedBoundary(id));
  }

  hasSurface(entry) {
    return this.regions.hasSuppliedBoundary(entry.id) || this.regions.hasRegisteredBoundary(entry.id)
      || this.regions.pointFits.has(entry.id) || this.regions.apertureDomains.some((part) => part.feature_id === entry.id)
      || (entry.category === 'sutures' && entry.owners.length > 1);
  }

  exploreMapped() {
    $('landmark-estimates').checked = true;
    $('landmark-mapped-only').disabled = false; $('landmark-mapped-only').checked = true;
    $('landmark-search').value = '';
    $('landmark-panel').open = true;
    this.showLandmarks(); this.filter(); this.context.invalidate(true);
  }

  filter(notify = true) {
    this.filteredLandmarks = this.filtered();
    const matches = new Set(this.filteredLandmarks.map((entry) => entry.id));
    this.rows.forEach(({ row }, id) => { row.hidden = !matches.has(id); });
    const searching = Boolean($('landmark-search').value.trim());
    $('landmark-results').hidden = !searching;
    $('landmark-results').textContent = `${matches.size} ${matches.size === 1 ? 'landmark' : 'landmarks'}`;
    $('landmark-no-results').hidden = matches.size > 0;
    const expandForSearch = (node) => {
      if (searching && !node.hidden) {
        if (!node.hasAttribute('data-before-search')) node.dataset.beforeSearch = String(node.open);
        node.open = true;
      } else if (!searching && node.hasAttribute('data-before-search')) {
        node.open = node.dataset.beforeSearch === 'true'; delete node.dataset.beforeSearch;
      }
    };
    $('landmark-panel').hidden = !matches.size;
    expandForSearch($('landmark-panel'));
    for (const group of this.landmarkGroups) {
      group.matches = group.ids.filter((id) => matches.has(id));
      group.node.hidden = group.matches.length === 0;
      expandForSearch(group.node);
    }
    if (this.hovered && !matches.has(this.hovered)) this.setRegionHover(null);
    this.updateControls();
    this.prepareFiltered();
    this.context.invalidate(true);
    if (notify) this.context.catalogueChanged?.();
    return matches.size;
  }

  prepareFiltered() {
    const matches = this.filteredLandmarks ?? this.filtered();
    if (!$('bone-studies').hidden && matches.length <= 24) {
      matches.forEach((entry) => this.previewQueue.add(entry.id));
      this.prepareQueue();
    }
  }

  async prepareQueue() {
    if (this.preparing) return;
    this.preparing = true;
    try {
      while (true) {
        const pending = [...new Set([this.hovered, this.selected?.kind === 'landmark' ? this.selected.id : null, ...this.enabled, ...this.previewQueue])]
          .filter((id) => this.canHighlight(id) && !this.regions.cache.has(id));
        if (!pending.length) break;
        $('landmark-region-status').textContent = `Preparing surface highlights · ${pending.length} remaining`;
        const id = pending[0], entry = this.landmarks.get(id);
        if (this.hasSurface(entry)) await this.regions.prepare(entry);
        else this.regions.empty(entry);
        if (this.hovered === id) this.describeHover(entry);
        if (this.selected?.kind === 'landmark' && this.selected.id === id) $('anatomy-placement').textContent = this.regionDescription(entry);
        this.context.invalidate(true);
      }
      $('landmark-region-status').textContent = '';
    } catch (error) {
      $('landmark-region-status').textContent = 'The surface highlight could not be prepared.';
      console.error(error);
    } finally { this.preparing = false; }
    this.context.invalidate(true);
  }

  updateControls() {
    const filtered = this.filteredLandmarks ?? this.filtered();
    const count = filtered.filter((entry) => this.enabled.has(entry.id)).length;
    $('landmark-pins').disabled = filtered.length === 0;
    $('landmark-pins').checked = count > 0 && count === filtered.length;
    $('landmark-pins').indeterminate = count > 0 && count < filtered.length;
    $('landmark-total').textContent = filtered.length === this.landmarks.size ? String(this.landmarks.size) : `${filtered.length}/${this.landmarks.size}`;
    for (const group of this.landmarkGroups) {
      const matches = group.matches ?? group.ids;
      const marked = matches.filter((id) => this.enabled.has(id)).length;
      group.checkbox.disabled = matches.length === 0;
      group.checkbox.checked = marked > 0 && marked === matches.length;
      group.checkbox.indeterminate = marked > 0 && marked < matches.length;
      group.count.textContent = String(matches.length);
    }
    for (const [id, row] of this.rows) {
      row.checkbox.checked = this.enabled.has(id);
      const selected = this.selected?.kind === 'landmark' && this.selected.id === id;
      row.row.classList.toggle('is-selected', selected);
      row.button.setAttribute('aria-pressed', String(selected));
      row.pin.setAttribute('aria-pressed', String(selected));
    }
  }

  toggleMarker(id, shown, update = true) {
    if (shown && !this.canHighlight(id)) return;
    if (shown) this.enabled.add(id); else this.enabled.delete(id);
    if (!shown && this.selected?.id === id) this.clearSelection();
    if (shown) this.prepareQueue();
    if (update) { this.updateControls(); this.context.invalidate(); }
  }

  setMarkers(ids, shown) {
    for (const id of ids) {
      if (shown && this.canHighlight(id)) this.enabled.add(id);
      else if (!shown) this.enabled.delete(id);
    }
    if (!shown && this.selected?.kind === 'landmark' && ids.includes(this.selected.id)) this.clearSelection();
    this.updateControls();
    if (shown) this.prepareQueue();
    this.context.invalidate();
  }

  worldPoint(entry, position = entry.placement.point) {
    return new THREE.Vector3(...position).applyMatrix4(this.context.entries.get(entry.mappedSource?.owner ?? entry.placement.label_fit?.owner ?? entry.owners[0]).mesh.matrixWorld);
  }

  showCard(entry, kind) {
    this.context.clearBoneSelection();
    this.selected = { id: entry.id, kind };
    $('anatomy-selection').hidden = false;
    $('anatomy-kind').textContent = kind === 'cavity' ? 'Bony cavity · same CT specimen' : this.categoryNames.get(entry.category) ?? entry.category;
    $('anatomy-name').textContent = entry.name;
    $('anatomy-description').textContent = entry.description;
    $('anatomy-location').textContent = entry.owners.map((id) => this.context.entries.get(id).name).join(' · ');
    if (entry.placement?.imported_reference) $('anatomy-location').textContent += ' · Reference bone fitted to CT; estimated placement';
    $('anatomy-placement').textContent = kind === 'cavity'
      ? 'Original segmented cavity boundary, aligned to this skull. Bones fade while cavity surfaces are shown.'
      : this.regionDescription(entry);
    $('anatomy-source').href = entry.pointFit?.definition_url ?? entry.mappedSource?.source_url ?? entry.placement?.label_fit?.source_url ?? entry.source_url ?? this.context.manifest.source.url;
    $('anatomy-isolate').textContent = kind === 'cavity' ? 'Cavity only' : 'Reveal';
    this.updateControls(); this.context.invalidate();
  }

  selectLandmark(id) {
    const entry = this.landmarks.get(id);
    if (!entry || !this.canHighlight(id)) return;
    this.enabled.add(id);
    this.showCard(entry, 'landmark');
    this.prepareQueue();
  }

  regionDescription(entry) {
    const region = this.regions.cache.get(entry.id);
    if (!region && this.hasSurface(entry)) return 'Preparing the surface highlight…';
    if (!region?.parts.length) {
      const rejected = this.regions.registeredRejections.find((record) => record.feature_id === entry.id);
      const reason = rejected ? `Source transfer withheld: ${rejected.reason}.` : 'Location guide only. The source supplies no resolved boundary or fitted point for this landmark, so no surface area is painted.';
      const fit = entry.placement.label_fit;
      if (fit?.status === 'uncertain-fit') return `${reason} The location transfer failed its distance checks and remains uncertain.`;
      if (fit?.basis === 'native-segmented-cavity') return `${reason} The locator is anchored to this skull’s original segmented cavity.`;
      if (fit?.basis === 'bone-shape-registration') return `${reason} Its approximate location follows a match of the bone shapes; anatomical placement remains unvalidated.`;
      return reason;
    }
    if (entry.category === 'landmarks') {
      return entry.pointFit
        ? `${entry.placement.method === 'native-contour-extremum' ? 'Point landmark fitted to this skull’s midline surface contour in the atlas orientation.' : 'Point landmark fitted to the exterior junction of its bones on this skull.'} The small surface highlight is a visual indication of that point, not an anatomical area. Its location remains an estimate.`
        : 'Estimated point location. The small surface highlight indicates a point; it does not define an anatomical region boundary.';
    }
    if (region.methods.includes('fitted:registered-author-region')) {
      const source = entry.mappedSource?.source_id === 'fossafinder' ? 'UBC FossaFinder' : 'BioDigital';
      const coverage = entry.partialMapping ? ` Accepted surface portions cover ${entry.mappedOwners.map((id) => this.context.entries.get(id).name).join(', ')}; the other owning bones have no accepted transfer.`
        : entry.mappedSource?.source_id === 'fossafinder' ? ' All three named source walls are represented; this does not establish complete anatomical boundary coverage.' : '';
      return `${source} region mapped from another skull onto these bone surfaces. The transfer is estimated and has no independent anatomical accuracy measurement.${coverage}`;
    }
    if (region.methods.every((method) => method === 'source-authored-faces')) {
      const scope = region.parts[0].scope;
      return (scope === 'articular-surface-of-feature' ? 'Source-defined articular surface of this feature. Original reference face selection.' : 'Source-defined surface. Original reference face selection.') + ' Anatomical identity is inferred from source material and location; no independent accuracy measurement is available.';
    }
    if (region.methods.some((method) => method.startsWith('source-joint-faces'))) return 'Source-defined joint surfaces · association with this named suture is fitted from neighbouring bones.';
    if (region.methods.includes('fitted:geometry-detected-aperture-rim')) return 'Opening rim fitted to this specimen’s geometry · anatomical boundary is inferred. The opening remains unfilled.';
    return 'Fitted surface region · anatomical boundary is inferred. All coloured geometry lies on the original bone surface.';
  }

  setRegionHover(id, { source = 'catalogue', overlaps = [] } = {}) {
    if (id && !this.canHighlight(id)) id = null;
    if (!id) { source = null; overlaps = []; }
    if (this.hovered === id && this.hoverSource === source && this.hoverOverlaps.join('|') === overlaps.join('|')) return;
    this.hovered = id; this.regions.hovered = id;
    this.hoverSource = source; this.hoverOverlaps = overlaps;
    const entry = this.landmarks.get(id);
    if (entry && !this.hasSurface(entry)) this.regions.empty(entry);
    this.describeHover(entry);
    if (entry) this.prepareQueue();
    this.context.invalidate();
  }

  describeHover(entry) {
    const id = entry?.id;
    const overlapping = this.hoverOverlaps.map((owner) => {
      const landmark = this.landmarks.get(owner), bone = this.context.entries.get(owner);
      return { id: owner, name: landmark?.name ?? bone?.name };
    }).filter((item) => item.name);
    this.context.regionHover(entry ? { id, name: entry.name, group: this.locationStatus(entry), colour: regionColour(id), source: this.hoverSource, overlapping } : null);
  }

  regionsAtHit(hit) {
    if ($('bone-studies').hidden || !hit) return [];
    const ids = new Set(this.enabled);
    if (this.selected?.kind === 'landmark') ids.add(this.selected.id);
    // Prepare supported candidates on the hit bone, even with a broad list.
    // All exact surface hits participate, smallest first; guides have no area.
    const owner = hit.object.userData.entryId;
    const candidates = (this.filteredLandmarks ?? this.filtered()).filter((entry) => entry.owners.includes(owner) && this.hasSurface(entry));
    for (const entry of candidates) { ids.add(entry.id); this.previewQueue.add(entry.id); }
    this.prepareQueue();
    return this.regions.matching(hit, [...ids].filter((id) => this.canHighlight(id)));
  }

  regionAtHit(hit) {
    return this.regionsAtHit(hit)[0]?.id ?? null;
  }

  pointAtPointer(pointer) {
    if ($('bone-studies').hidden) return null;
    const { canvas, entries, sectionPlane } = this.context, rect = canvas.getBoundingClientRect();
    const x = pointer.x - rect.left, y = pointer.y - rect.top;
    const candidates = new Set([...(this.filteredLandmarks ?? this.filtered()).map((entry) => entry.id), ...this.enabled]);
    const nearby = [];
    for (const id of candidates) {
      const entry = this.landmarks.get(id);
      if (!this.canHighlight(id) || this.hasSurface(entry) || !entry.owners.some((owner) => entries.get(owner).mesh.visible)) continue;
      const projected = this.project(entry, null, rect);
      if (projected.z < -1 || projected.z > 1 || projected.x < 0 || projected.x > rect.width || projected.y < 0 || projected.y > rect.height) continue;
      if (this.context.sectionEnabled() && sectionPlane.distanceToPoint(projected.world) < 0) continue;
      const distance = Math.hypot(projected.x - x, projected.y - y);
      if (distance <= POINT_HOVER_RADIUS) nearby.push({ entry, projected, distance });
    }
    if (!nearby.length) return null;
    const opaque = [...entries.values()].filter((entry) => entry.mesh.visible && entry.mesh.material.opacity > 0.7).map((entry) => entry.mesh);
    nearby.sort((a, b) => a.distance - b.distance || a.entry.id.localeCompare(b.entry.id));
    return nearby.find((item) => !this.isOccluded(item.projected.world, opaque))?.entry ?? null;
  }

  hoverAt(hit, pointer) {
    const regions = this.regionsAtHit(hit);
    const guide = this.pointAtPointer(pointer);
    const id = guide?.id ?? regions[0]?.id ?? null;
    const overlaps = regions.filter((region) => region.id !== id).map((region) => region.id);
    const owner = hit?.object.userData.entryId;
    if (id && owner) overlaps.push(owner);
    return { id, overlaps: [...new Set(overlaps)] };
  }

  clearSelection() {
    this.selected = null; $('anatomy-selection').hidden = true;
    this.updateControls(); this.context.invalidate();
  }

  async focus() {
    if (!this.selected) return;
    if (this.selected.kind === 'cavity') {
      const entry = this.cavities.get(this.selected.id);
      this.context.frameMeshes(entry.parts, 'anterior');
    } else {
      const entry = this.landmarks.get(this.selected.id);
      const region = this.hasSurface(entry) ? await this.regions.prepare(entry) : this.regions.empty(entry);
      if (this.selected?.id !== entry.id) return;
      if (region.parts.length) {
        this.syncRegions();
        const side = entry.mappedSource?.fit?.surface_side_check;
        const internalCenter = side?.source_compartment === 'inner'
          ? new THREE.Vector3(...side.native_cranial_center).applyMatrix4(this.context.entries.get(entry.mappedSource.owner).mesh.matrixWorld)
          : null;
        this.context.frameRegion(region.parts.map((part) => part.mesh), entry.view ?? 'anterior', internalCenter);
      } else this.context.focusLocation(this.worldPoint(entry), entry.view ?? 'anterior');
    }
  }

  reveal() {
    if (!this.selected) return;
    if (this.selected.kind === 'cavity') {
      const entry = this.cavities.get(this.selected.id);
      this.context.isolateCavity();
      this.context.frameMeshes(entry.parts, 'anterior');
    } else {
      const entry = this.landmarks.get(this.selected.id);
      this.context.isolateOwners(entry.owners);
      this.focus();
    }
  }

  async loadCavities() {
    const response = await fetchAsset('assets/skull-cavities/manifest.json');
    if (!response.ok) throw new Error(`Cavity manifest returned HTTP ${response.status}`);
    const manifest = await response.json();
    const gltf = await new GLTFLoader().loadAsync(manifest.model);
    const meshes = new Map(); gltf.scene.traverse((object) => { if (object.isMesh) meshes.set(object.name, object); });
    for (const record of manifest.structures) {
      const source = meshes.get(record.id);
      if (!source) throw new Error(`Missing cavity geometry: ${record.name}`);
      const positions = source.geometry.attributes.position;
      const indices = source.geometry.index;
      const partitions = record.owners.map(() => []);
      const ownerCenters = record.owners.map((id) => this.context.entries.get(id).mesh.geometry.boundingBox.getCenter(new THREE.Vector3()));
      const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
      // Bilateral source resources stay one catalogue item; their disconnected
      // sides follow the corresponding bone during illustrative explosion.
      for (let i = 0; i < indices.count; i += 3) {
        a.fromBufferAttribute(positions, indices.getX(i));
        b.fromBufferAttribute(positions, indices.getX(i + 1));
        c.fromBufferAttribute(positions, indices.getX(i + 2));
        a.add(b).add(c).divideScalar(3);
        let owner = 0;
        for (let j = 1; j < ownerCenters.length; j++) if (a.distanceToSquared(ownerCenters[j]) < a.distanceToSquared(ownerCenters[owner])) owner = j;
        partitions[owner].push(indices.getX(i), indices.getX(i + 1), indices.getX(i + 2));
      }
      const parts = [];
      partitions.forEach((partition, i) => {
        if (!partition.length) return;
        const geometry = source.geometry.clone(); geometry.setIndex(partition);
        geometry.computeBoundingBox(); geometry.computeBoundingSphere();
        const mesh = new THREE.Mesh(geometry, new THREE.MeshPhysicalMaterial({
          color: record.color, roughness: 0.65, metalness: 0, side: THREE.DoubleSide,
          transparent: true, opacity: 0.8, depthWrite: false,
        }));
        mesh.matrixAutoUpdate = false; mesh.visible = false;
        mesh.userData.cavityId = record.id; mesh.userData.ownerId = record.owners[i];
        this.context.scene.add(mesh); parts.push(mesh);
      });
      source.geometry.dispose(); source.material.dispose();
      const row = document.createElement('div'); row.className = 'cavity-row'; row.dataset.cavity = record.id;
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', `Show ${record.name.toLowerCase()}`);
      checkbox.addEventListener('change', () => this.toggleCavity(record.id, checkbox.checked));
      const swatch = document.createElement('span'); swatch.className = 'bone-swatch'; swatch.style.background = record.color;
      const name = document.createElement('button'); name.textContent = record.name;
      name.addEventListener('click', () => { this.toggleCavity(record.id, true); this.showCard(this.cavities.get(record.id), 'cavity'); this.focus(); });
      row.append(checkbox, swatch, name); $('cavity-list').append(row);
      this.cavities.set(record.id, { ...record, parts, checkbox, visible: false, source_url: manifest.source.url });
    }
    this.syncTransforms();
  }

  toggleCavity(id, visible) {
    const entry = this.cavities.get(id); if (!entry) return;
    entry.visible = visible; entry.checkbox.checked = visible;
    if (!visible && this.selected?.id === id) this.clearSelection();
    this.context.cavityContext([...this.cavities.values()].some((cavity) => cavity.visible));
    if (![...this.cavities.values()].some((cavity) => cavity.visible) && ![...this.context.entries.values()].some((bone) => bone.visible)) this.context.restoreSkull();
    this.syncTransforms(); this.context.invalidate();
  }

  syncTransforms() {
    this.context.scene.updateMatrixWorld(true);
    for (const entry of this.cavities.values()) for (const part of entry.parts) {
      part.matrix.copy(this.context.entries.get(part.userData.ownerId).mesh.matrixWorld);
      part.matrixWorldNeedsUpdate = true;
      part.visible = entry.visible;
      if (part.visible && this.context.sectionEnabled()) {
        const box = part.geometry.boundingBox.clone().applyMatrix4(part.matrix);
        const normal = this.context.sectionPlane.normal;
        const inside = new THREE.Vector3(normal.x >= 0 ? box.max.x : box.min.x, normal.y >= 0 ? box.max.y : box.min.y, normal.z >= 0 ? box.max.z : box.min.z);
        part.visible = this.context.sectionPlane.distanceToPoint(inside) >= 0;
      }
    }
    this.syncRegions();
  }

  syncRegions() {
    const selected = this.selected?.kind === 'landmark' ? this.selected.id : null;
    const active = this.hovered ?? selected;
    const xray = $('landmark-xray').checked && !$('bone-studies').hidden && this.canHighlight(active) ? active : null;
    this.regions.sync(this.enabled, selected, xray);
  }

  project(entry, position = null, rect = this.context.canvas.getBoundingClientRect()) {
    const region = this.regions.cache.get(entry.id);
    const visible = region?.parts.filter((item) => this.context.entries.get(item.ownerId).mesh.visible);
    const part = (visible?.length ? visible : region?.parts)?.reduce((largest, item) => !largest || item.area > largest.area ? item : largest, null);
    const world = position || entry.pointFit || !part
      ? this.worldPoint(entry, position ?? entry.pointFit?.point ?? entry.placement.point)
      : part.anchor.clone().applyMatrix4(this.context.entries.get(part.ownerId).mesh.matrixWorld);
    const point = world.clone().project(this.context.camera);
    return { x: (point.x + 1) * rect.width / 2, y: (1 - point.y) * rect.height / 2, z: point.z, world };
  }

  isOccluded(world, meshes) {
    const direction = world.clone().sub(this.context.camera.position);
    const distance = direction.length();
    this.raycaster.set(this.context.camera.position, direction.normalize());
    this.raycaster.firstHitOnly = !this.context.sectionEnabled();
    this.raycaster.far = Math.max(0, distance - Math.max(0.002, distance * 1e-5));
    return this.raycaster.intersectObjects(meshes, false).some((hit) => !this.context.sectionEnabled() || this.context.sectionPlane.distanceToPoint(hit.point) >= 0);
  }

  locationStatus(entry) {
    const supported = Boolean(this.regions.cache.get(entry.id)?.parts.length);
    if (!this.regions.cache.has(entry.id) && this.hasSurface(entry)) return 'Preparing highlight…';
    if (supported) return entry.pointFit ? 'Fitted point' : this.regions.hasSuppliedBoundary(entry.id) ? 'Supplied surface' : 'Estimated surface';
    if (entry.placement.label_fit?.status === 'uncertain-fit') return 'Uncertain location · transfer checks failed';
    if (entry.placement.label_fit?.basis === 'native-segmented-cavity') return 'Native cavity location · no bone surface boundary';
    return 'Approximate location · no surface boundary';
  }

  updateLocationAid(opaqueMeshes, rect) {
    const aid = $('landmark-location-aid');
    aid.classList.toggle('is-pointer-hover', this.hoverSource === 'canvas');
    const id = this.hovered ?? (this.selected?.kind === 'landmark' ? this.selected.id : null);
    const entry = this.landmarks.get(id);
    this.locationState = null;
    aid.hidden = !$('landmark-locate').checked || $('bone-studies').hidden || !entry || !this.canHighlight(id);
    if (aid.hidden) return;
    const { canvas, entries } = this.context;
    const projected = this.project(entry, null, rect);
    const top = Math.min(rect.height * 0.45, document.querySelector('.view-presets').getBoundingClientRect().bottom - rect.top + 12);
    const card = $('anatomy-selection').hidden ? null : $('anatomy-selection').getBoundingClientRect();
    const supported = Boolean(this.regions.cache.get(id)?.parts.length);
    const owners = supported ? this.regions.cache.get(id).parts.map((part) => part.ownerId) : entry.owners;
    const ownerVisible = owners.some((owner) => entries.get(owner).visible);
    const ownerRendered = owners.some((owner) => entries.get(owner).mesh.visible);
    const clipped = this.context.sectionEnabled() && this.context.sectionPlane.distanceToPoint(projected.world) < 0;
    const onscreen = projected.z > -1 && projected.z < 1 && projected.x >= 0 && projected.x <= rect.width && projected.y >= 0 && projected.y <= rect.height;
    const occluded = ownerRendered && !clipped && this.isOccluded(projected.world, opaqueMeshes);
    let status = this.locationStatus(entry);
    if (!ownerVisible) status += ' · Bone hidden';
    else if (clipped) status += ' · Location outside section';
    else if (!onscreen) status += ' · Outside view · use Focus';
    else if (occluded) status += supported && $('landmark-xray').checked ? ' · Hidden behind bone · see-through preview' : ' · Hidden behind bone · use Reveal';
    if ($('landmark-location-name').textContent !== entry.name) $('landmark-location-name').textContent = entry.name;
    if ($('landmark-location-status').textContent !== status) $('landmark-location-status').textContent = status;
    aid.classList.toggle('is-obscured', occluded || !ownerVisible || clipped || !onscreen);
    aid.style.setProperty('--location-colour', regionColour(id));
    const x = THREE.MathUtils.clamp(projected.x, 18, rect.width - 18);
    const y = THREE.MathUtils.clamp(projected.y, 18, rect.height - 30);
    $('landmark-location-target').setAttribute('transform', `translate(${x},${y})`);
    const caption = $('landmark-location-caption'), width = Math.min(220, rect.width - 32);
    caption.style.width = `${width}px`;
    const height = caption.offsetHeight;
    let left = THREE.MathUtils.clamp(x < rect.width / 2 ? x + 28 : x - width - 28, 12, rect.width - width - 12);
    let labelY = y - height / 2;
    if (x + 28 + width > rect.width - 12 && x - width - 28 < 12) {
      left = THREE.MathUtils.clamp(x - width / 2, 12, rect.width - width - 12);
      labelY = y - height - 28 >= top ? y - height - 28 : y + 28;
    }
    labelY = THREE.MathUtils.clamp(labelY, top, Math.max(top, rect.height - height - 30));
    if (card && left + rect.left < card.right && left + rect.left + width > card.left && labelY + rect.top < card.bottom && labelY + rect.top + height > card.top) {
      labelY = Math.max(top, card.top - rect.top - height - 12);
    }
    caption.style.left = `${left}px`; caption.style.top = `${labelY}px`;
    const lineX = left > x ? left : left + width, lineY = labelY + height / 2;
    $('landmark-location-leader').setAttribute('d', `M ${x} ${y} L ${lineX} ${lineY}`);
    this.locationState = { id, supported, occluded, ownerVisible, clipped, onscreen, status, point: projected.world.toArray() };
  }

  update() {
    const { entries, canvas } = this.context;
    this.context.scene.updateMatrixWorld(true);
    const rect = canvas.getBoundingClientRect();
    const opaqueMeshes = [...entries.values()].filter((entry) => entry.mesh.visible && entry.mesh.material.opacity > 0.7).map((entry) => entry.mesh);
    const nextVisible = new Set();
    const showPins = $('landmark-markers').checked || $('landmark-labels').checked;
    for (const id of showPins ? this.enabled : []) {
      const entry = this.landmarks.get(id);
      const { pin, path } = this.rows.get(entry.id);
      const projected = this.project(entry, null, rect);
      const ownerVisible = entry.owners.some((id) => entries.get(id).mesh.visible);
      const retained = !this.context.sectionEnabled() || this.context.sectionPlane.distanceToPoint(projected.world) >= 0;
      const onscreen = projected.z > -1 && projected.z < 1 && projected.x >= 10 && projected.x < rect.width - 10 && projected.y >= 10 && projected.y < rect.height - 10;
      let shown = this.enabled.has(entry.id) && ownerVisible && retained && onscreen;
      const occluded = shown && this.isOccluded(projected.world, opaqueMeshes);
      if (occluded && this.selected?.id !== entry.id) shown = false;
      pin.hidden = !shown;
      if (shown) nextVisible.add(entry.id);
      pin.classList.toggle('is-occluded', occluded);
      pin.style.left = `${projected.x}px`; pin.style.top = `${projected.y}px`;
      pin.style.setProperty('--pin-color', regionColour(entry.id));
      if (path) {
        path.style.display = shown && !occluded ? '' : 'none';
        path.setAttribute('stroke', entries.get(entry.owners[0]).color);
        path.setAttribute('points', entry.placement.path.map((point) => { const p = this.project(entry, point, rect); return `${p.x},${p.y}`; }).join(' '));
      }
    }
    for (const id of this.visiblePins) if (!nextVisible.has(id)) {
      const { pin, path } = this.rows.get(id);
      pin.hidden = true;
      if (path) path.style.display = 'none';
    }
    this.visiblePins = nextVisible;
    this.updateLocationAid(opaqueMeshes, rect);
  }

  reset() {
    this.enabled.clear();
    this.previewQueue.clear(); this.setRegionHover(null);
    this.cavities.forEach((entry) => { entry.visible = false; entry.checkbox.checked = false; });
    this.context.cavityContext(false);
    $('landmark-search').value = '';
    $('landmark-labels').checked = false; $('landmark-markers').checked = false; $('landmark-estimates').checked = true;
    $('landmark-locate').checked = true; $('landmark-xray').checked = true;
    $('landmark-mapped-only').checked = false; $('landmark-mapped-only').disabled = false;
    $('landmark-overlay').classList.remove('show-names', 'show-pins');
    this.clearSelection(); this.filter(); this.syncTransforms();
  }

  snapshot() {
    this.context.scene.updateMatrixWorld(true);
    return {
      selected: this.selected, landmarkCount: this.landmarks.size,
      precomputed: this.regions.precomputed, precomputedIssue: this.regions.precomputedIssue,
      batching: this.regions.batches.snapshot(),
      includeEstimates: $('landmark-estimates').checked,
      suppliedRegionCount: [...this.landmarks.keys()].filter((id) => this.regions.hasSuppliedBoundary(id)).length,
      registeredRegionCount: [...this.landmarks.keys()].filter((id) => this.regions.hasRegisteredBoundary(id)).length,
      mappedOnly: $('landmark-mapped-only').checked,
      categories: [...new Set([...this.landmarks.values()].map((entry) => entry.category))],
      markers: [...this.enabled],
      enabledRegions: [...this.enabled], hoveredRegion: this.hovered, preparingRegions: this.preparing,
      locationAid: this.locationState,
      regions: this.regions.snapshot(),
      landmarks: [...this.landmarks.values()].map((entry) => ({ id: entry.id, name: entry.name, category: entry.category, owners: entry.owners, point: this.worldPoint(entry).toArray(), placementMethod: entry.placement.method, placementStatus: entry.placement.label_fit?.status ?? 'approximate', visible: !this.rows.get(entry.id).pin.hidden })),
      cavities: [...this.cavities.values()].map((entry) => ({ id: entry.id, visible: entry.visible, rendered: entry.parts.some((part) => part.visible), triangles: entry.parts.reduce((sum, part) => sum + part.geometry.index.count / 3, 0), bounds: entry.parts.map((part) => { const box = part.geometry.boundingBox.clone().applyMatrix4(part.matrix); return [box.min.toArray(), box.max.toArray()]; }) })),
    };
  }

  projectLandmark(id) {
    const entry = this.landmarks.get(id); if (!entry) return null;
    this.context.scene.updateMatrixWorld(true);
    const result = this.project(entry); const rect = this.context.canvas.getBoundingClientRect();
    return { x: rect.left + result.x, y: rect.top + result.y };
  }
}
