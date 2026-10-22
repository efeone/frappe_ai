/** Load Vyogo AI settings from the backend. Module-level singleton. */

import { ref, readonly, type Ref } from "vue";

export interface FrappeAISettings {
  enabled: boolean;
  /** Turned off by the host, so the settings form cannot turn it back on. */
  enabledManaged: boolean;
  sidebarWidth: number;
  keyboardShortcut: string;
  /** The site's answer to a write the agent wants to make; "Ask every time" shows a card. */
  writeApproval: string;
  /** Whether the card may offer "Allow all in this conversation". */
  allowSessionAutoApprove: boolean;
  /** How the assistant presents itself; the server has already validated these. */
  assistantName: string;
  assistantByline: string;
  assistantLogoUrl: string;
  accentColor: string;
}

interface FrappeAISettingsResponse {
  enabled?: boolean;
  enabled_managed?: boolean;
  sidebar_width?: number;
  keyboard_shortcut?: string;
  write_approval?: string;
  allow_session_auto_approve?: boolean;
  assistant_name?: string;
  assistant_byline?: string;
  assistant_logo_url?: string;
  accent_color?: string;
}

export const WRITE_APPROVAL_ASK = "Ask every time";

const DEFAULT_SETTINGS: FrappeAISettings = {
  enabled: false,
  sidebarWidth: 380,
  // Matches the doctype default; `Ctrl+/` is reserved by Frappe v16.
  keyboardShortcut: "Alt+/",
  writeApproval: WRITE_APPROVAL_ASK,
  allowSessionAutoApprove: false,
  assistantName: "Vyogo AI",
  assistantByline: "",
  assistantLogoUrl: "",
  accentColor: "",
  enabledManaged: false,
};

const settings: Ref<FrappeAISettings> = ref({ ...DEFAULT_SETTINGS });
const loaded = ref(false);

export function useSettings() {
  async function loadSettings(): Promise<void> {
    if (typeof frappe === "undefined") {
      loaded.value = true;
      return;
    }
    try {
      const result = await frappe.call<FrappeAISettingsResponse>({
        method: "frappe_ai.api.get_settings",
        async: true,
      });
      const msg = result?.message;
      if (msg) {
        settings.value = {
          enabled: msg.enabled ?? false,
          sidebarWidth: msg.sidebar_width ?? 380,
          keyboardShortcut: msg.keyboard_shortcut ?? "Alt+/",
          writeApproval: msg.write_approval ?? WRITE_APPROVAL_ASK,
          allowSessionAutoApprove: msg.allow_session_auto_approve ?? false,
          assistantName: msg.assistant_name || DEFAULT_SETTINGS.assistantName,
          assistantByline: msg.assistant_byline ?? "",
          assistantLogoUrl: msg.assistant_logo_url ?? "",
          accentColor: msg.accent_color ?? "",
          enabledManaged: msg.enabled_managed ?? false,
        };
      }
    } catch {
      // fall back to defaults
    } finally {
      loaded.value = true;
    }
  }

  return {
    settings: readonly(settings),
    loaded: readonly(loaded),
    loadSettings,
  };
}
