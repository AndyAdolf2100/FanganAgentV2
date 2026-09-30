import type { EnterpriseTemplate, TemplateElement } from './types';
type Effect = NonNullable<TemplateElement['imageColorChange']>;
const rgb = (value: string) => [1, 3, 5].map(i => parseInt(value.slice(i, i + 2), 16));

export function replaceImageColor(pixels: Uint8ClampedArray, effect: Effect, tolerance: number) {
  const from = rgb(effect.from), to = rgb(effect.to);
  for (let i = 0; i < pixels.length; i += 4) {
    if (from.every((channel, j) => Math.abs(pixels[i + j] - channel) <= tolerance)) {
      to.forEach((channel, j) => { pixels[i + j] = channel; });
      pixels[i + 3] = Math.round(pixels[i + 3] * effect.opacity);
    }
  }
}

async function processImage(asset: EnterpriseTemplate['assets'][number], effect: Effect): Promise<string> {
  const image = new Image();
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve(); image.onerror = () => reject(new Error('图片透明色处理失败，请检查原图片'));
    image.src = `data:${asset.mime};base64,${asset.data}`;
  });
  if (image.naturalWidth * image.naturalHeight > 20_000_000) throw new Error('图片透明色处理超过2000万像素限制');
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
  try {
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('当前浏览器无法处理图片透明色');
    ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
    replaceImageColor(data.data, effect, asset.mime === 'image/jpeg' ? 8 : 0);
    ctx.putImageData(data, 0, 0);
    return canvas.toDataURL('image/png').split(',')[1];
  } finally { canvas.width = canvas.height = 0; }
}

// Import produces complete PNG resources before anything is sent to the server.
export async function prepareImageEffects(template: EnterpriseTemplate, process = processImage) {
  const assets = new Map(template.assets.map(a => [a.id, a]));
  const cache = new Map(template.assets.filter(a => a.derivedKey).map(a => [a.derivedKey!, a.id]));
  let nextId = Math.max(0, ...template.assets.map(a => Number(a.id.slice(6)))) + 1;
  for (const page of template.pages) for (const element of page.elements) {
    const effect = element.imageColorChange;
    if (!effect) continue;
    const original = assets.get(element.asset || '');
    if (!original) throw new Error('图片透明色处理缺少原图片');
    // Match the previous server cache keys to preserve existing derived assets.
    const serialized = `{"from": "${effect.from}", "opacity": ${effect.opacity}, "to": "${effect.to}"}`;
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(original.data + serialized));
    const key = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
    let id = cache.get(key);
    if (!id) {
      if (template.assets.length >= 400) throw new Error('模板图片过多');
      const data = await process(original, effect);
      id = `asset-${nextId++}`;
      template.assets.push({ id, mime: 'image/png', data, derivedKey: key }); cache.set(key, id);
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    element.renderAsset = id;
  }
}
