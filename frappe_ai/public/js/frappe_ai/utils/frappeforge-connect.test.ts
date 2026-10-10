import { describe, expect, it, vi } from "vitest";
import { connectFrappeForge, rotateFrappeForgeKey, snippetFrom } from "./frappeforge-connect";

type Fields = Array<{ fieldname: string; default?: unknown; reqd?: number }>;

function fakeFrappe(message: Record<string, unknown>) {
  const dialogs: Array<{ opts: any; shown: boolean; hidden: boolean }> = [];
  const call = vi.fn(async () => ({ message }));
  class Dialog {
    entry: { opts: any; shown: boolean; hidden: boolean };
    constructor(opts: any) {
      this.entry = { opts, shown: false, hidden: false };
      dialogs.push(this.entry);
    }
    show() {
      this.entry.shown = true;
    }
    hide() {
      this.entry.hidden = true;
    }
  }
  return { f: { call, ui: { Dialog }, msgprint: vi.fn() }, call, dialogs };
}

const RESULT = {
  connection: "abc123",
  site_url: "https://erp.example.com",
  api_key: "k",
  api_secret: "s",
  expires_on: "2026-11-09 10:00:00",
  snippet: '{"site_url":"https://erp.example.com","api_key":"k","api_secret":"s"}',
};

describe("snippetFrom", () => {
  it("builds the JSON the FrappeForge IDE accepts as paste", () => {
    expect(JSON.parse(snippetFrom(RESULT))).toEqual({ site_url: "https://erp.example.com", api_key: "k", api_secret: "s" });
  });
});

describe("connectFrappeForge", () => {
  it("asks for a label, expiry, allowed IPs and read roles", () => {
    const { f, dialogs } = fakeFrappe(RESULT);
    connectFrappeForge(f as any);
    const fields = dialogs[0].opts.fields as Fields;
    expect(fields.map((x) => x.fieldname)).toEqual(
      expect.arrayContaining(["label", "expires_in_days", "allowed_ips", "read_roles"]),
    );
    expect(fields.find((x) => x.fieldname === "expires_in_days")?.default).toBe(30);
    expect(fields.find((x) => x.fieldname === "label")?.reqd).toBe(1);
    expect(dialogs[0].shown).toBe(true);
  });

  it("creates the connection and shows the snippet once", async () => {
    const { f, call, dialogs } = fakeFrappe(RESULT);
    connectFrappeForge(f as any);
    await dialogs[0].opts.primary_action({ label: "Team A", expires_in_days: 7, allowed_ips: "", read_roles: "" });
    expect(call).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "frappe_ai.api.frappeforge.create_connection",
        args: { label: "Team A", expires_in_days: 7, allowed_ips: "", read_roles: "" },
      }),
    );
    expect(dialogs[0].hidden).toBe(true);
    const result = dialogs[1].opts;
    const snippet = result.fields.find((x: any) => x.fieldname === "snippet");
    expect(snippet.default).toBe(snippetFrom(RESULT));
    expect(JSON.stringify(result.fields)).toMatch(/shown once/i);
  });

  it("refuses an expiry outside 1 to 365 days before calling the site", async () => {
    const { f, call, dialogs } = fakeFrappe(RESULT);
    connectFrappeForge(f as any);
    await dialogs[0].opts.primary_action({ label: "x", expires_in_days: 400 });
    expect(call).not.toHaveBeenCalled();
    expect(f.msgprint).toHaveBeenCalled();
  });
});

describe("rotateFrappeForgeKey", () => {
  it("rotates and shows the new snippet", async () => {
    const { f, call, dialogs } = fakeFrappe(RESULT);
    await rotateFrappeForgeKey("abc123", f as any);
    expect(call).toHaveBeenCalledWith(
      expect.objectContaining({ method: "frappe_ai.api.frappeforge.rotate_key", args: { connection_name: "abc123" } }),
    );
    expect(dialogs[0].opts.fields.find((x: any) => x.fieldname === "snippet").default).toBe(snippetFrom(RESULT));
  });
});
