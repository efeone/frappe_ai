import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

const g = globalThis as Record<string, unknown>;

type CallOpts = { method: string; args?: Record<string, unknown>; callback?: (r: unknown) => void; error?: (e: unknown) => void };

function fakeFrappe(overrides: Record<string, (args: Record<string, unknown>) => unknown> = {}) {
  const calls: CallOpts[] = [];
  const answers: Record<string, (args: Record<string, unknown>) => unknown> = {
    "frappe_ai.api.chat.get_models": () => ({
      models: [
        { id: "p/a", label: "Model A" },
        { id: "p/b", label: "Model B" },
      ],
      default: "p/a",
      current: "p/a",
    }),
    "frappe_ai.api.confirm.get_approval_modes": () => ({
      current: "Ask every time",
      allowed: ["Ask every time", "Auto-approve creates and updates", "Read only"],
      site: "Ask every time",
    }),
    "frappe_ai.api.chat.set_model": (a) => ({
      models: [
        { id: "p/a", label: "Model A" },
        { id: "p/b", label: "Model B" },
      ],
      current: a.model,
    }),
    "frappe_ai.api.confirm.set_approval_mode": (a) => ({
      current: a.mode,
      allowed: ["Ask every time", "Auto-approve creates and updates", "Read only"],
    }),
    ...overrides,
  };
  g.frappe = {
    call: vi.fn((opts: CallOpts) => {
      calls.push(opts);
      try {
        opts.callback?.({ message: answers[opts.method]?.(opts.args ?? {}) });
      } catch (e) {
        opts.error?.(e);
      }
    }),
    realtime: { on: vi.fn(), off: vi.fn() },
  };
  return calls;
}

async function mountToolbar() {
  vi.resetModules();
  const { default: ChatToolbar } = await import("./ChatToolbar.vue");
  const wrapper = mount(ChatToolbar);
  await flushPromises();
  return wrapper;
}

describe("ChatToolbar", () => {
  beforeEach(() => fakeFrappe());
  afterEach(() => vi.restoreAllMocks());

  it("lists the agent's models and the approval modes the site allows", async () => {
    const wrapper = await mountToolbar();
    const approval = wrapper.find('select[aria-label="How changes are approved"]');
    const model = wrapper.find(".frappe-ai-model-picker button");
    expect(model.text()).toBe("Model A");
    await model.trigger("click");
    expect(wrapper.findAll(".frappe-ai-model-option").map((o) => o.text())).toEqual(["Model A", "Model B"]);
    expect(approval.findAll("option").map((o) => o.text())).toEqual([
      "Ask first",
      "Auto-approve edits",
      "Read only",
    ]);
  });

  it("saves a new model and a new approval mode on the server", async () => {
    const calls = fakeFrappe();
    const wrapper = await mountToolbar();
    await wrapper.find(".frappe-ai-model-picker button").trigger("click");
    await wrapper.findAll(".frappe-ai-model-option")[1].trigger("click");
    await wrapper.find('select[aria-label="How changes are approved"]').setValue("Auto-approve creates and updates");
    await flushPromises();
    const saved = calls.filter((c) => c.method.includes(".set_"));
    expect(saved.map((c) => [c.method, c.args])).toEqual([
      ["frappe_ai.api.chat.set_model", { model: "p/b" }],
      ["frappe_ai.api.confirm.set_approval_mode", { mode: "Auto-approve creates and updates" }],
    ]);
    // an auto mode is marked, since writes then happen without a card
    expect(wrapper.find("select.frappe-ai-select--auto").exists()).toBe(true);
  });

  it("puts the choice back and says so when the server refuses it", async () => {
    fakeFrappe({
      "frappe_ai.api.confirm.set_approval_mode": () => {
        throw new Error("not allowed");
      },
    });
    const wrapper = await mountToolbar();
    const approval = wrapper.find('select[aria-label="How changes are approved"]');
    await approval.setValue("Auto-approve creates and updates");
    await flushPromises();
    expect((approval.element as HTMLSelectElement).value).toBe("Ask every time");
    expect(wrapper.text()).toContain("not available on this site");
  });

  it("hides the model menu when the agent offers no list, and still shows approval", async () => {
    fakeFrappe({ "frappe_ai.api.chat.get_models": () => ({ models: [], default: null }) });
    const wrapper = await mountToolbar();
    expect(wrapper.find(".frappe-ai-model-picker").exists()).toBe(false);
    expect(wrapper.find('select[aria-label="How changes are approved"]').exists()).toBe(true);
  });

  it("locks a menu with a single choice", async () => {
    fakeFrappe({
      "frappe_ai.api.confirm.get_approval_modes": () => ({ current: "Read only", allowed: ["Read only"] }),
    });
    const wrapper = await mountToolbar();
    const approval = wrapper.find('select[aria-label="How changes are approved"]');
    expect((approval.element as HTMLSelectElement).disabled).toBe(true);
  });

  it("names a model without a label by its own name, without the provider", async () => {
    fakeFrappe({
      "frappe_ai.api.chat.get_models": () => ({
        models: [{ id: "openai/gpt-4.1-mini", label: "openai/gpt-4.1-mini" }],
        current: "openai/gpt-4.1-mini",
      }),
    });
    const wrapper = await mountToolbar();
    expect(wrapper.find(".frappe-ai-model-picker button").text()).toBe("gpt-4.1-mini");
  });
});

