/**
 * Desk dialogs that connect this site to FrappeForge (see docs/FRAPPEFORGE.md).
 *
 * The site creates a dedicated agent user per connection and returns its API key once; the
 * System Manager pastes the snippet into the FrappeForge IDE's "Connect live site" dialog.
 * Exposed to Desk form scripts as `window.frappe_ai_frappeforge`.
 */

interface FrappeLike {
  call: (opts: { method: string; args?: Record<string, unknown>; freeze?: boolean }) => Promise<{ message: any }>;
  ui: { Dialog: new (opts: any) => { show: () => void; hide: () => void } };
  msgprint: (msg: string | Record<string, unknown>) => void;
}

interface ConnectionResult {
  site_url: string;
  api_key: string;
  api_secret: string;
  expires_on?: string;
  connection?: string;
}

const API = "frappe_ai.api.frappeforge";
const MAX_DAYS = 365;

const __ = (text: string): string => ((window as any).__ ? (window as any).__(text) : text);

export function snippetFrom(result: ConnectionResult): string {
  return JSON.stringify({ site_url: result.site_url, api_key: result.api_key, api_secret: result.api_secret });
}

export function connectFrappeForge(f: FrappeLike = (window as any).frappe): void {
  const dialog = new f.ui.Dialog({
    title: __("Connect to FrappeForge"),
    fields: [
      {
        fieldtype: "HTML",
        fieldname: "intro",
        options: `<p class="text-muted">${__(
          "FrappeForge gets its own user on this site. It can read customizations and propose releases; only a System Manager can approve them.",
        )}</p>`,
      },
      { fieldtype: "Data", fieldname: "label", label: __("Label"), reqd: 1, description: __("e.g. the FrappeForge workspace") },
      {
        fieldtype: "Int",
        fieldname: "expires_in_days",
        label: __("Valid for (days)"),
        default: 30,
        reqd: 1,
      },
      {
        fieldtype: "Small Text",
        fieldname: "allowed_ips",
        label: __("Allowed IP addresses"),
        description: __("Comma-separated; leave empty to allow any. Use FrappeForge's published egress addresses."),
      },
      {
        fieldtype: "Small Text",
        fieldname: "read_roles",
        label: __("Additional read roles"),
        description: __("One per line, e.g. Sales User. Writes stay blocked whatever these roles allow."),
      },
    ],
    primary_action_label: __("Create connection"),
    primary_action: async (values: Record<string, any>) => {
      const days = Number(values.expires_in_days);
      if (!Number.isInteger(days) || days < 1 || days > MAX_DAYS) {
        f.msgprint(__("A connection lasts between 1 and 365 days."));
        return;
      }
      const r = await f.call({
        method: `${API}.create_connection`,
        args: {
          label: values.label,
          expires_in_days: days,
          allowed_ips: values.allowed_ips || "",
          read_roles: values.read_roles || "",
        },
        freeze: true,
      });
      dialog.hide();
      showSnippet(f, r.message);
    },
  });
  dialog.show();
}

export async function rotateFrappeForgeKey(connection: string, f: FrappeLike = (window as any).frappe): Promise<void> {
  const r = await f.call({ method: `${API}.rotate_key`, args: { connection_name: connection }, freeze: true });
  showSnippet(f, r.message);
}

function showSnippet(f: FrappeLike, result: ConnectionResult): void {
  const dialog = new f.ui.Dialog({
    title: __("Paste this into FrappeForge"),
    fields: [
      {
        fieldtype: "HTML",
        fieldname: "warning",
        options: `<div class="alert alert-warning">${__(
          "The API secret is shown once. Paste the snippet into FrappeForge's Connect live site dialog now; rotate the key if it is lost.",
        )}</div>`,
      },
      { fieldtype: "Code", fieldname: "snippet", label: __("Connection snippet"), options: "JSON", read_only: 1, default: snippetFrom(result) },
      { fieldtype: "Data", fieldname: "expires_on", label: __("Expires on"), read_only: 1, default: result.expires_on || "" },
    ],
    primary_action_label: __("Done"),
    primary_action: () => dialog.hide(),
  });
  dialog.show();
}
