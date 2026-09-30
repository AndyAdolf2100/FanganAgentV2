// Width comparison detects likely fallback without requesting access to the local font list.
// It cannot guarantee that every glyph is supplied by the requested font.
export function detectFont(font: string, sample: string, measure: (family: string, text: string) => number): boolean {
  const quoted = JSON.stringify(font.replace(/[\r\n]/g, ''));
  return ['monospace', 'serif', 'sans-serif'].some(fallback =>
    Math.abs(measure(`${quoted}, ${fallback}`, sample) - measure(fallback, sample)) > 0.01);
}
