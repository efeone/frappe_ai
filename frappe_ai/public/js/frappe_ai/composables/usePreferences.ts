/**
 * The user's own choices for the assistant: which model answers, and how changes are approved.
 *
 * Both lists come from the server and both choices are saved there, per user: the model list from
 * the agent (so the browser can never name a model the agent does not offer), the approval modes
 * from the site's settings (so nobody can pick a looser mode than the administrator allows).
 * Module-level state, so every part of the sidebar sees the same choice.
 */

import { readonly, ref } from "vue";

export interface ModelOption {
  id: string;
  label: string;
}

interface ModelsResponse {
  models?: ModelOption[];
  default?: string | null;
  current?: string | null;
}

interface ApprovalResponse {
  current?: string;
  allowed?: string[];
  site?: string;
}

/** Short names for the menu; the full ones stay in the option's title. */
export const APPROVAL_LABELS: Record<string, string> = {
  "Ask every time": "Ask first",
  "Auto-approve creates and updates": "Auto-approve edits",
  "Auto-approve all changes": "Auto-approve all",
  "Read only": "Read only",
};

const models = ref<ModelOption[]>([]);
const currentModel = ref<string | null>(null);
const approvalModes = ref<string[]>([]);
const currentApproval = ref<string | null>(null);
const error = ref<string | null>(null);

function call<T>(method: string, args: Record<string, unknown> = {}): Promise<T | undefined> {
  return new Promise((resolve, reject) => {
    frappe.call<T>({
      method,
      args,
      callback: (r) => resolve(r?.message),
      error: reject,
    });
  });
}

function applyModels(r: ModelsResponse | undefined): void {
  models.value = Array.isArray(r?.models) ? r.models : [];
  currentModel.value = r?.current ?? r?.default ?? null;
}

function applyApproval(r: ApprovalResponse | undefined): void {
  approvalModes.value = Array.isArray(r?.allowed) ? r.allowed : [];
  currentApproval.value = r?.current ?? null;
}

export function usePreferences() {
  async function loadPreferences(): Promise<void> {
    if (typeof frappe === "undefined" || typeof frappe.call !== "function") return;
    // each list on its own: an agent that is down must not hide the approval menu, or the reverse
    await Promise.allSettled([
      call<ModelsResponse>("frappe_ai.api.chat.get_models").then(applyModels),
      call<ApprovalResponse>("frappe_ai.api.confirm.get_approval_modes").then(applyApproval),
    ]);
  }

  async function setModel(id: string): Promise<void> {
    const previous = currentModel.value;
    currentModel.value = id; // the menu answers at once; a refusal puts it back
    try {
      applyModels(await call<ModelsResponse>("frappe_ai.api.chat.set_model", { model: id }));
      error.value = null;
    } catch {
      currentModel.value = previous;
      error.value = "That model could not be selected.";
    }
  }

  async function setApprovalMode(mode: string): Promise<void> {
    const previous = currentApproval.value;
    currentApproval.value = mode;
    try {
      applyApproval(
        await call<ApprovalResponse>("frappe_ai.api.confirm.set_approval_mode", { mode }),
      );
      error.value = null;
    } catch {
      currentApproval.value = previous;
      error.value = "That approval mode is not available on this site.";
    }
  }

  return {
    models: readonly(models),
    currentModel: readonly(currentModel),
    approvalModes: readonly(approvalModes),
    currentApproval: readonly(currentApproval),
    error: readonly(error),
    loadPreferences,
    setModel,
    setApprovalMode,
  };
}
