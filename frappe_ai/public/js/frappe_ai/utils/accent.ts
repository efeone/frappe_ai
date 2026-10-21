/**
 * The text colour to set on an accent fill: dark ink or white, whichever
 * contrasts more (WCAG relative luminance). A light brand colour such as teal
 * gets dark text, a deep one gets white. Returns null for anything that is not
 * a #rgb / #rrggbb colour, so the stylesheet's default stays in force.
 */
const DARK = "#0f172a";
const LIGHT = "#ffffff";

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((x) => x + x).join("") : m[1];
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: number, b: number): number {
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

export function accentForeground(accent: string): string | null {
  const l = luminance(accent);
  if (l === null) return null;
  const dark = luminance(DARK) as number;
  return contrast(l, dark) >= contrast(l, 1) ? DARK : LIGHT;
}

export function isAccent(value: string | undefined | null): value is string {
  return !!value && luminance(value) !== null;
}
