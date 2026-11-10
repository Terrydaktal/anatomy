import * as THREE from '../vendor/build/three.module.js';

// Keep the native triangle lookup on each part. Only the opaque drawing buffers
// are combined; transparent surfaces retain the original independent rendering.
export class HighlightBatches {
  constructor(context, materialFactory) {
    this.context = context;
    this.materialFactory = materialFactory;
    this.groups = [];
    this.enabled = false;
  }

  build(regions, renderer) {
    if (this.groups.length || !renderer.extensions.has('WEBGL_multi_draw')) return;
    const owners = new Map();
    const ordered = [...regions.values()].sort((a, b) => b.area - a.area || b.id.localeCompare(a.id));
    for (const region of ordered) for (const part of region.parts) {
      if (!owners.has(part.ownerId)) owners.set(part.ownerId, []);
      owners.get(part.ownerId).push({ region, part });
    }
    for (const [ownerId, records] of owners) {
      if (records.length < 2) continue;
      const owner = this.context.entries.get(ownerId);
      const palette = new THREE.DataTexture(new Float32Array(records.length * 4), records.length, 1, THREE.RGBAFormat, THREE.FloatType);
      const [material, unusedGhost] = this.materialFactory('#ffffff', owner.mesh.material.roughness);
      unusedGhost.dispose();
      material.onBeforeCompile = (shader) => {
        shader.uniforms.regionPalette = { value: palette };
        shader.uniforms.regionPaletteWidth = { value: records.length };
        shader.vertexShader = 'uniform sampler2D regionPalette;\nuniform float regionPaletteWidth;\nvarying vec4 vRegionColour;\n' + shader.vertexShader;
        shader.vertexShader = shader.vertexShader.replace('#include <batching_vertex>',
          '#include <batching_vertex>\nvRegionColour = texture2D(regionPalette, vec2((batchId + 0.5) / regionPaletteWidth, 0.5));');
        shader.fragmentShader = 'varying vec4 vRegionColour;\n' + shader.fragmentShader;
        shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>',
          '#include <color_fragment>\ndiffuseColor.rgb *= vRegionColour.rgb;');
        shader.fragmentShader = shader.fragmentShader.replace('#include <emissivemap_fragment>',
          '#include <emissivemap_fragment>\ntotalEmissiveRadiance = vRegionColour.rgb * vRegionColour.a;');
      };
      material.customProgramCacheKey = () => 'native-highlight-batch-v1';
      const vertices = records.reduce((sum, { part }) => sum + part.geometry.attributes.position.count, 0);
      const mesh = new THREE.BatchedMesh(records.length, vertices, 0, material);
      mesh.name = `landmark-batch-${ownerId}`;
      mesh.userData.surfaceRegion = true;
      mesh.frustumCulled = false;
      mesh.renderOrder = 10;
      // Buffers are appended in the same large-to-small region order as the
      // individual meshes. Picking retains that identical overlap priority.
      mesh.setCustomSort((list) => list.sort((a, b) => a.start - b.start));
      for (const record of records) {
        record.geometryId = mesh.addGeometry(record.part.geometry);
        const colour = new THREE.Color(record.region.colour);
        colour.toArray(palette.image.data, record.geometryId * 4);
        palette.image.data[record.geometryId * 4 + 3] = 0.1;
        mesh.setVisibleAt(record.geometryId, false);
      }
      palette.needsUpdate = true;
      mesh.visible = false;
      this.context.scene.add(mesh);
      this.groups.push({ owner, records, mesh, palette, matrix: new THREE.Matrix4(), matrixReady: false });
    }
    this.enabled = this.groups.length > 0;
  }

  sync(hovered, selected) {
    for (const group of this.groups) {
      const { owner, mesh, records, palette } = group;
      const active = this.enabled && !owner.mesh.material.transparent;
      const moved = !group.matrixReady || !group.matrix.equals(owner.mesh.matrixWorld);
      let visible = false, changed = false;
      for (const { part, region, geometryId } of records) {
        part.mesh.material.visible = !active;
        mesh.setVisibleAt(geometryId, active && part.mesh.visible);
        visible ||= active && part.mesh.visible;
        if (moved) mesh.setMatrixAt(geometryId, owner.mesh.matrixWorld);
        const gain = hovered === region.id ? 0.3 : selected === region.id ? 0.2 : 0.1;
        const offset = geometryId * 4 + 3;
        if (palette.image.data[offset] !== Math.fround(gain)) {
          palette.image.data[offset] = gain;
          changed = true;
        }
      }
      if (moved) { group.matrix.copy(owner.mesh.matrixWorld); group.matrixReady = true; }
      if (changed) palette.needsUpdate = true;
      mesh.material.roughness = owner.mesh.material.roughness;
      mesh.material.opacity = owner.mesh.material.opacity;
      mesh.material.depthWrite = owner.mesh.material.depthWrite;
      mesh.visible = visible;
    }
  }

  async warm(renderer, camera, scene) {
    if (!this.enabled) return;
    this.context.scene.updateMatrixWorld(true);
    this.sync(null, null);
    for (const group of this.groups) {
      group.mesh.visible = true;
      for (const record of group.records) group.mesh.setVisibleAt(record.geometryId, true);
    }
    const previous = renderer.getRenderTarget();
    const target = new THREE.WebGLRenderTarget(1, 1);
    try {
      await renderer.compileAsync(scene, camera);
      renderer.setRenderTarget(target);
      renderer.render(scene, camera); // Upload complete buffers while the loading screen is present.
    } finally {
      renderer.setRenderTarget(previous);
      target.dispose();
      this.sync(null, null);
    }
  }

  snapshot() {
    return { enabled: this.enabled, groups: this.groups.length,
      visibleGroups: this.groups.filter(({ mesh }) => mesh.visible).length,
      batchedParts: this.groups.reduce((sum, group) => sum + group.records.length, 0) };
  }
}
