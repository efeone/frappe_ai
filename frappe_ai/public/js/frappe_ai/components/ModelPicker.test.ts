import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import ModelPicker from "./ModelPicker.vue";

const many = Array.from({ length: 180 }, (_, i) => ({
  id: `vendor${i % 3}/model-${i}`,
  label: `Vendor ${i % 3}: Model ${i}`,
}));

describe("ModelPicker", () => {
  it("searches a long list and shows a capped number of matches", async () => {
    const wrapper = mount(ModelPicker, { props: { models: many, current: many[0].id }, attachTo: document.body });
    await wrapper.find("button").trigger("click");
    expect(wrapper.findAll(".frappe-ai-model-option")).toHaveLength(50);
    expect(wrapper.text()).toContain("130 more: keep typing to narrow.");
    await wrapper.find("input").setValue("model-17");
    const names = wrapper.findAll(".frappe-ai-model-option").map((o) => o.text());
    expect(names).toEqual(["Vendor 2: Model 17", "Vendor 2: Model 170", "Vendor 0: Model 171", "Vendor 1: Model 172",
      "Vendor 2: Model 173", "Vendor 0: Model 174", "Vendor 1: Model 175", "Vendor 2: Model 176",
      "Vendor 0: Model 177", "Vendor 1: Model 178", "Vendor 2: Model 179"]);
    wrapper.unmount();
  });

  it("picks with the keyboard and emits the id", async () => {
    const wrapper = mount(ModelPicker, { props: { models: many, current: many[0].id }, attachTo: document.body });
    await wrapper.find("button").trigger("click");
    const input = wrapper.find("input");
    await input.setValue("vendor1");
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select")).toEqual([["vendor1/model-4"]]);
    expect(wrapper.find(".frappe-ai-model-menu").exists()).toBe(false);
    wrapper.unmount();
  });

  it("says when nothing matches, and closes on Escape", async () => {
    const wrapper = mount(ModelPicker, { props: { models: many, current: null }, attachTo: document.body });
    await wrapper.find("button").trigger("click");
    await wrapper.find("input").setValue("zzz");
    expect(wrapper.text()).toContain("No model matches.");
    await wrapper.find("input").trigger("keydown", { key: "Escape" });
    expect(wrapper.find(".frappe-ai-model-menu").exists()).toBe(false);
    wrapper.unmount();
  });
});
