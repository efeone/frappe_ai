/**
 * A random v4 UUID, on any page.
 *
 * `crypto.randomUUID()` exists only in secure contexts (HTTPS or localhost),
 * so on a plain-HTTP site it is undefined and every chat send threw
 * "crypto.randomUUID is not a function" before reaching the server.
 * `crypto.getRandomValues()` is available everywhere and is just as random.
 */
export function newId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const bytes = new Uint8Array(16);
  c.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
