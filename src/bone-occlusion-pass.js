import { NoBlending, Source } from 'three';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';

// Bone colours and surface previews do not change the depth/normal image. Keep
// its denoised shading until the camera, geometry, visibility or section changes.
export class BoneOcclusionPass extends GTAOPass {
  constructor(...args) {
    super(...args);
    this.moving = false;
    this.stillTargets = this.targets();
    this.motionTargets = {
      normal: this.normalRenderTarget.clone(), ao: this.gtaoRenderTarget.clone(),
      denoised: this.pdRenderTarget.clone(), key: null,
    };
    // RenderTarget.clone isolates its colour image, but DepthTexture.clone shares
    // its Source. Different target sizes must have independent depth storage.
    this.motionTargets.normal.depthTexture.source = new Source({ ...this.depthTexture.image });
  }

  targets() {
    return { normal: this.normalRenderTarget, ao: this.gtaoRenderTarget, denoised: this.pdRenderTarget, key: null };
  }

  useTargets(targets) {
    this.normalRenderTarget = targets.normal;
    this.gtaoRenderTarget = targets.ao;
    this.pdRenderTarget = targets.denoised;
    this.width = targets.ao.width; this.height = targets.ao.height;
    this.depthTexture = targets.normal.depthTexture;
    this.normalTexture = targets.normal.texture;
    for (const material of [this.gtaoMaterial, this.pdMaterial]) {
      material.uniforms.resolution.value.set(this.width, this.height);
      material.uniforms.tDepth.value = this.depthTexture;
      material.uniforms.tNormal.value = this.normalTexture;
    }
    this.pdMaterial.uniforms.tDiffuse.value = targets.ao.texture;
    this.depthRenderMaterial.uniforms.tDepth.value = this.depthTexture;
  }

  setSize(width, height) {
    this.useTargets(this.stillTargets);
    super.setSize(width, height);
    // Only the soft depth shading uses fewer pixels during a drag. The skull,
    // highlights, antialiasing and picking retain their original resolution.
    for (const target of [this.motionTargets.normal, this.motionTargets.ao, this.motionTargets.denoised]) {
      target.setSize(Math.max(1, Math.ceil(width / 2)), Math.max(1, Math.ceil(height / 2)));
    }
    this.stillTargets.key = null; this.motionTargets.key = null;
  }

  render(renderer, writeBuffer, readBuffer, ...args) {
    const targets = this.moving ? this.motionTargets : this.stillTargets;
    this.useTargets(targets);
    const uniforms = this.gtaoMaterial.uniforms;
    const signature = [this.camera.matrixWorld.elements, this.camera.projectionMatrix.elements,
      uniforms.radius.value, uniforms.thickness.value, uniforms.scale.value,
      renderer.clippingPlanes.map((plane) => [...plane.normal.toArray(), plane.constant])];
    this.scene.traverseVisible((object) => {
      if (object.isMesh && !object.userData.surfaceRegion) {
        signature.push([object.id, object.geometry.id, object.geometry.attributes.position.version,
          object.geometry.index?.version, object.matrixWorld.elements]);
      }
    });
    const key = JSON.stringify(signature);
    if (targets.key === key && this.output === GTAOPass.OUTPUT.Default) {
      this.copyMaterial.uniforms.tDiffuse.value = readBuffer.texture;
      this.copyMaterial.blending = NoBlending;
      const output = this.renderToScreen ? null : writeBuffer;
      this.renderPass(renderer, this.copyMaterial, output);
      this.blendMaterial.uniforms.intensity.value = this.blendIntensity;
      this.blendMaterial.uniforms.tDiffuse.value = targets.denoised.texture;
      this.renderPass(renderer, this.blendMaterial, output);
      return;
    }
    // Coincident highlights must not compete with their original bone faces in
    // the depth buffer. Their colour is already present in the colour pass.
    const overlays = [];
    this.scene.traverse((object) => {
      if (object.visible && object.userData.surfaceRegion) { overlays.push(object); object.visible = false; }
    });
    try {
      super.render(renderer, writeBuffer, readBuffer, ...args);
      targets.key = key;
    } finally { overlays.forEach((object) => { object.visible = true; }); }
  }

  dispose() {
    this.useTargets(this.stillTargets);
    for (const target of [this.motionTargets.normal, this.motionTargets.ao, this.motionTargets.denoised]) target.dispose();
    super.dispose();
  }
}
