// Stable release URLs let the browser reuse the negotiated compressed response.
export function assetUrl(path) {
  const version = globalThis.__SKULL_ASSET_VERSION__;
  if (!version || /^(?:data|blob):/i.test(String(path))) return path;
  const url = new URL(path, globalThis.location.href);
  if (url.origin !== globalThis.location.origin) return path;
  url.searchParams.set('v', version);
  return url.href;
}

export function fetchAsset(path, options) { return fetch(assetUrl(path), options); }
