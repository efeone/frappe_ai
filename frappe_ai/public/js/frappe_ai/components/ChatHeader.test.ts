import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import ChatHeader from "./ChatHeader.vue";

describe("ChatHeader", () => {
  it("renders a neutral title, no byline or logo, and three icon buttons by default", () => {
    const wrapper = mount(ChatHeader);
    expect(wrapper.find(".frappe-ai-header-title").text()).toBe("AI Sidebar");
    expect(wrapper.find(".frappe-ai-header-brand").exists()).toBe(false);
    expect(wrapper.find("img.frappe-ai-header-logo").exists()).toBe(false);
    const buttons = wrapper.findAll("button.frappe-ai-icon-btn");
    expect(buttons).toHaveLength(3);
  });

  it("shows the configured name, byline and logo", () => {
    const wrapper = mount(ChatHeader, {
      props: { name: "Wren", byline: "from Example", logoUrl: "/files/mark.png" },
    });
    expect(wrapper.find(".frappe-ai-header-title").text()).toBe("Wren");
    expect(wrapper.find(".frappe-ai-header-brand").text()).toBe("from Example");
    expect(wrapper.find("img.frappe-ai-header-logo").attributes("src")).toBe("/files/mark.png");
  });

  it("emits 'history' from the history button", async () => {
    const wrapper = mount(ChatHeader);
    await wrapper.find('button[aria-label="Chat history"]').trigger("click");
    expect(wrapper.emitted("history")).toHaveLength(1);
  });

  it("emits 'clear' when the new-conversation button is clicked", async () => {
    const wrapper = mount(ChatHeader);
    const clearBtn = wrapper.find('button[aria-label="New conversation"]');
    expect(clearBtn.exists()).toBe(true);
    await clearBtn.trigger("click");
    expect(wrapper.emitted("clear")).toHaveLength(1);
  });

  it("emits 'close' when the close button is clicked (a11y: labelled)", async () => {
    const wrapper = mount(ChatHeader);
    const closeBtn = wrapper.find('button[aria-label="Close sidebar"]');
    expect(closeBtn.exists()).toBe(true);
    expect(closeBtn.attributes("aria-label")).toBe("Close sidebar");
    await closeBtn.trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });
});
