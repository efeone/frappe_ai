<script setup lang="ts">
/** Buttons under an answer that carry the reader into the Desk with the
 *  answer's own context already applied — book the recommended practitioner,
 *  or open the exact set of records the figures came from. */
import type { ActionItem, ActionsBlock } from "../../types/blocks";

defineProps<{ block: ActionsBlock }>();

function run(item: ActionItem) {
	if (typeof frappe === "undefined") return;
	if (item.kind === "new_doc") {
		// route_options is how the Desk prefills a brand-new form.
		frappe.route_options = { ...(item.values || {}) };
		frappe.new_doc(item.doctype);
		return;
	}
	if (item.kind === "list") {
		frappe.route_options = { ...(item.filters || {}) };
		frappe.set_route("List", item.doctype);
		return;
	}
	if (item.kind === "open" && item.name) {
		frappe.set_route("Form", item.doctype, item.name);
	}
}

function icon(kind: string): string {
	if (kind === "new_doc") return "+";
	if (kind === "list") return "☰";
	return "→";
}
</script>

<template>
	<div class="frappe-ai-actions">
		<button
			v-for="(item, i) in block.items"
			:key="i"
			type="button"
			class="frappe-ai-action"
			:class="{ 'frappe-ai-action-primary': item.kind === 'new_doc' }"
			:title="item.label"
			@click="run(item)"
		>
			<span class="frappe-ai-action-icon" aria-hidden="true">{{ icon(item.kind) }}</span>
			<span class="frappe-ai-action-label">{{ item.label }}</span>
		</button>
	</div>
</template>

<style scoped>
.frappe-ai-actions {
	display: flex;
	flex-wrap: wrap;
	gap: 8px;
	margin-top: 10px;
}
.frappe-ai-action {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	padding: 6px 12px;
	border-radius: 6px;
	border: 1px solid var(--dark-border-color, #d1d8dd);
	background: var(--control-bg, #f4f5f6);
	color: var(--text-color, #1f272e);
	font-size: var(--text-sm, 12px);
	font-weight: 500;
	line-height: 1.4;
	cursor: pointer;
	transition: background 0.12s ease, border-color 0.12s ease;
	max-width: 100%;
}
.frappe-ai-action:hover {
	background: var(--fg-hover-color, #ebeef0);
	border-color: var(--text-muted, #8d99a6);
}
.frappe-ai-action:focus-visible {
	outline: 2px solid var(--primary, #2490ef);
	outline-offset: 1px;
}
.frappe-ai-action-primary {
	background: var(--primary, #2490ef);
	border-color: var(--primary, #2490ef);
	color: #fff;
}
.frappe-ai-action-primary:hover {
	background: var(--primary, #2490ef);
	filter: brightness(0.94);
	border-color: var(--primary, #2490ef);
}
.frappe-ai-action-icon {
	font-size: 11px;
	opacity: 0.85;
	flex-shrink: 0;
}
.frappe-ai-action-label {
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}
@media (prefers-reduced-motion: reduce) {
	.frappe-ai-action { transition: none; }
}
</style>
