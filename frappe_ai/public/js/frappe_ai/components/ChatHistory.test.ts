import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import ChatHistory from "./ChatHistory.vue";

const g = globalThis as Record<string, unknown>;

function withSessions(sessions: unknown[] | null) {
  g.frappe = {
    call: vi.fn((opts: { callback?: (r: unknown) => void; error?: (e: unknown) => void }) => {
      if (sessions === null) opts.error?.(new Error("down"));
      else opts.callback?.({ message: { sessions } });
    }),
    realtime: { on: vi.fn(), off: vi.fn() },
  };
}

describe("ChatHistory", () => {
  it("lists past chats and opens the one picked", async () => {
    withSessions([
      { id: "chat-1", title: "Examplia print format", updated: new Date().toISOString() },
      { id: "chat-2", title: "Net profit", updated: "2026-01-02T10:00:00Z" },
    ]);
    const wrapper = mount(ChatHistory, { props: { currentId: "chat-2" } });
    await flushPromises();
    const items = wrapper.findAll(".frappe-ai-history-item");
    expect(items.map((i) => i.find(".frappe-ai-history-item-title").text())).toEqual([
      "Examplia print format",
      "Net profit",
    ]);
    expect(items[1].classes()).toContain("frappe-ai-history-item--current");
    await items[0].trigger("click");
    expect(wrapper.emitted("open")).toEqual([["chat-1"]]);
  });

  it("says so when there are none, or when they cannot be loaded", async () => {
    withSessions([]);
    const empty = mount(ChatHistory);
    await flushPromises();
    expect(empty.text()).toContain("No past chats yet.");
    withSessions(null);
    const failed = mount(ChatHistory);
    await flushPromises();
    expect(failed.text()).toContain("Could not load your chats.");
  });
});
