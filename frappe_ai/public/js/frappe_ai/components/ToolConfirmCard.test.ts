import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import ToolConfirmCard from "./ToolConfirmCard.vue";
import type { ToolConfirm } from "../types/messages";

function makeConfirm(overrides: Partial<ToolConfirm> = {}): ToolConfirm {
  return {
    id: "c1",
    name: "create_document",
    arguments: { doctype: "Print Format", data: { name: "Clean Invoice", doc_type: "Sales Invoice" } },
    status: "pending",
    timestamp: new Date("2026-01-01T12:00:00Z"),
    ...overrides,
  };
}

describe("ToolConfirmCard", () => {
  it("describes the change in plain words from the arguments, with the record's fields", () => {
    const wrapper = mount(ToolConfirmCard, { props: { toolConfirm: makeConfirm() } });
    expect(wrapper.text()).toContain("Allow this change?");
    expect(wrapper.text()).toContain("Create a new Print Format record.");
    const fields = wrapper.findAll(".frappe-ai-confirm-fields dt").map((n) => n.text());
    expect(fields).toEqual(["name", "doc_type"]);
    expect(wrapper.text()).toContain("Clean Invoice");
    // raw JSON only on request
    expect(wrapper.find("pre.frappe-ai-tool-pre").exists()).toBe(false);
  });

  it("emits allow / deny with the card's id", async () => {
    const wrapper = mount(ToolConfirmCard, { props: { toolConfirm: makeConfirm() } });
    const buttons = wrapper.findAll(".frappe-ai-confirm-actions button");
    expect(buttons.map((b) => b.text())).toEqual(["Allow", "Deny"]);
    await buttons[0].trigger("click");
    expect(wrapper.emitted("allow")).toEqual([["c1", false]]);
    await buttons[1].trigger("click");
    expect(wrapper.emitted("deny")).toEqual([["c1"]]);
  });

  it("offers 'allow all in this conversation' only when the site allows it, and never for a delete", async () => {
    const offered = mount(ToolConfirmCard, {
      props: { toolConfirm: makeConfirm(), canAllowConversation: true },
    });
    const all = offered.findAll(".frappe-ai-confirm-actions button");
    expect(all.map((b) => b.text())).toContain("Allow all in this conversation");
    await all[2].trigger("click");
    expect(offered.emitted("allow")).toEqual([["c1", true]]);

    const del = mount(ToolConfirmCard, {
      props: {
        toolConfirm: makeConfirm({
          name: "delete_document",
          arguments: { doctype: "Customer", name: "CUST-1" },
        }),
        canAllowConversation: true,
      },
    });
    expect(del.text()).toContain("Delete Customer CUST-1. This cannot be undone.");
    expect(del.findAll(".frappe-ai-confirm-actions button").map((b) => b.text())).toEqual([
      "Allow",
      "Deny",
    ]);
  });

  it("hides the buttons once the card is settled", () => {
    const allowed = mount(ToolConfirmCard, {
      props: { toolConfirm: makeConfirm({ status: "allowed" }) },
    });
    expect(allowed.text()).toContain("Allowed");
    expect(allowed.find(".frappe-ai-confirm-actions").exists()).toBe(false);

    const denied = mount(ToolConfirmCard, {
      props: { toolConfirm: makeConfirm({ status: "denied" }) },
    });
    expect(denied.text()).toContain("Not done");
    expect(denied.find(".frappe-ai-confirm-actions").exists()).toBe(false);
  });

  it("shows the raw call under Details", async () => {
    const wrapper = mount(ToolConfirmCard, { props: { toolConfirm: makeConfirm() } });
    await wrapper.find(".frappe-ai-tool-expand-btn").trigger("click");
    const pre = wrapper.find("pre.frappe-ai-tool-pre");
    expect(pre.exists()).toBe(true);
    expect(pre.text()).toContain('"doctype": "Print Format"');
    expect(wrapper.text()).toContain("create_document");
  });

  it("handles risk: always-ask by revealing full details and hiding conversation auto-approve", () => {
    const wrapper = mount(ToolConfirmCard, {
      props: {
        toolConfirm: makeConfirm({
          risk: "always-ask",
          name: "create_document",
          arguments: {
            doctype: "Client Script",
            data: {
              dt: "Sales Order",
              script: "frappe.ui.form.on('Sales Order', { refresh(frm) { console.log('test'); } });",
            },
          },
        }),
        canAllowConversation: true,
      },
    });

    // Does not offer "Allow all in this conversation"
    const buttons = wrapper.findAll(".frappe-ai-confirm-actions button").map((b) => b.text());
    expect(buttons).not.toContain("Allow all in this conversation");
    expect(buttons).toEqual(["Allow", "Deny"]);

    // Details are open by default showing full code
    const pre = wrapper.find("pre.frappe-ai-tool-pre");
    expect(pre.exists()).toBe(true);
    expect(pre.text()).toContain("console.log('test')");
  });

  it("shows a batch as a table of every row, with one Allow for all of them", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({ subject: `Examplia task ${i + 1}`, project: "PROJ-1" }));
    const wrapper = mount(ToolConfirmCard, {
      props: {
        toolConfirm: makeConfirm({ name: "create_documents", arguments: { doctype: "Task", rows } }),
      },
    });
    expect(wrapper.text()).toContain("Create 12 new Task records, all or nothing.");
    const header = wrapper.findAll(".frappe-ai-confirm-batch th").map((th) => th.text());
    expect(header).toEqual(["#", "subject", "project"]);
    const body = wrapper.findAll(".frappe-ai-confirm-batch tbody tr");
    expect(body).toHaveLength(12);
    expect(body[11].text()).toContain("Examplia task 12");
    expect(wrapper.findAll(".frappe-ai-confirm-actions button").map((b) => b.text())).toContain("Allow");
  });

  it("names the fields a wide batch keeps in Details", () => {
    const row = Object.fromEntries(Array.from({ length: 9 }, (_, i) => [`field_${i}`, i]));
    const wrapper = mount(ToolConfirmCard, {
      props: {
        toolConfirm: makeConfirm({ name: "create_documents", arguments: { doctype: "Task", rows: [row] } }),
      },
    });
    expect(wrapper.findAll(".frappe-ai-confirm-batch th")).toHaveLength(7); // # plus six columns
    expect(wrapper.text()).toContain("3 more fields in Details.");
    expect(wrapper.text()).toContain("Create 1 new Task record, all or nothing.");
  });
});

