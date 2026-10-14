<template>
	<div
		:class="[
			'frappe-ai-confirm-card',
			`frappe-ai-confirm-card--${toolConfirm.status}`,
			isAlwaysAsk ? 'frappe-ai-confirm-card--always-ask' : '',
		]"
		role="group"
		:aria-label="`Change waiting for approval: ${summary}`"
	>
		<div class="frappe-ai-confirm-header">
			<span class="frappe-ai-confirm-title">{{ title }}</span>
			<span class="frappe-ai-tool-time">{{ formattedTime }}</span>
		</div>
		<p class="frappe-ai-confirm-summary">{{ summary }}</p>
		<div v-if="batchRows.length > 0" class="frappe-ai-confirm-batch">
			<table>
				<thead>
					<tr>
						<th scope="col">#</th>
						<th v-for="col in batchColumns" :key="col" scope="col">{{ col }}</th>
					</tr>
				</thead>
				<tbody>
					<tr v-for="(row, i) in batchRows" :key="i">
						<td>{{ i + 1 }}</td>
						<td v-for="col in batchColumns" :key="col">{{ cell(row[col]) }}</td>
					</tr>
				</tbody>
			</table>
			<p v-if="hiddenColumns > 0" class="frappe-ai-confirm-batch-note">
				{{ hiddenColumns }} more field{{ hiddenColumns === 1 ? "" : "s" }} in Details.
			</p>
		</div>
		<dl v-if="fields.length > 0" class="frappe-ai-confirm-fields">
			<template v-for="field in fields" :key="field.key">
				<dt>{{ field.key }}</dt>
				<dd>{{ field.value }}</dd>
			</template>
		</dl>
		<button
			:class="['frappe-ai-tool-expand-btn', detailsOpen ? 'frappe-ai-tool-expand-btn--open' : '']"
			:aria-expanded="detailsOpen"
			type="button"
			@click="detailsOpen = !detailsOpen"
		>
			<!-- eslint-disable-next-line vue/no-v-html -->
			<span aria-hidden="true" v-html="frappeIcon('chevron-right', 'xs')" />
			Details
		</button>
		<div v-if="detailsOpen" class="frappe-ai-tool-section">
			<p class="frappe-ai-tool-label">{{ toolConfirm.name }}</p>
			<pre class="frappe-ai-tool-pre">{{ formattedArgs }}</pre>
		</div>
		<div v-if="toolConfirm.status === 'pending'" class="frappe-ai-confirm-actions">
			<button
				type="button"
				class="frappe-ai-btn frappe-ai-btn--primary"
				@click="emit('allow', toolConfirm.id, false)"
			>
				Allow
			</button>
			<button type="button" class="frappe-ai-btn" @click="emit('deny', toolConfirm.id)">
				Deny
			</button>
			<button
				v-if="canAllowConversation && !destructive && !isAlwaysAsk"
				type="button"
				class="frappe-ai-btn frappe-ai-btn--link"
				@click="emit('allow', toolConfirm.id, true)"
			>
				Allow all in this conversation
			</button>
		</div>
	</div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { frappeIcon } from "../utils/frappe-icon";
import type { ToolConfirm } from "../types/messages";

const props = defineProps<{
	toolConfirm: ToolConfirm;
	/** Whether "Allow all in this conversation" is on offer (a site setting). */
	canAllowConversation?: boolean;
}>();

const emit = defineEmits<{
	allow: [id: string, forConversation: boolean];
	deny: [id: string];
}>();

const isAlwaysAsk = computed(() => props.toolConfirm.risk === "always-ask");

// For always-ask cards, details (full code/payload) are open by default.
const detailsOpen = ref(isAlwaysAsk.value);

// A delete cannot be undone, and always-ask actions require explicit single confirmation.
const destructive = computed(() => props.toolConfirm.name === "delete_document");

const title = computed(() => {
	switch (props.toolConfirm.status) {
		case "allowed":
			return "Allowed";
		case "denied":
			return "Not done";
		default:
			return isAlwaysAsk.value ? "Review & allow this change" : "Allow this change?";
	}
});

function text(value: unknown): string {
	return typeof value === "string" ? value.trim() : "";
}

/** One plain sentence for the call, from its arguments alone — never from model text. */
// A batch shows as a table: one row per new record, the fields as columns, so a long list can be
// read before one Allow saves all of it.
const MAX_COLUMNS = 6;
const MAX_CELL_CHARS = 60;
const batchRows = computed<Array<Record<string, unknown>>>(() => {
	if (props.toolConfirm.name !== "create_documents") return [];
	const rows = props.toolConfirm.arguments?.rows;
	return Array.isArray(rows)
		? rows.filter((r): r is Record<string, unknown> => !!r && typeof r === "object" && !Array.isArray(r))
		: [];
});
const batchColumns = computed<string[]>(() => {
	const seen: string[] = [];
	for (const row of batchRows.value) {
		for (const key of Object.keys(row)) if (!seen.includes(key)) seen.push(key);
	}
	return seen.slice(0, MAX_COLUMNS);
});
const hiddenColumns = computed(() => {
	const all = new Set(batchRows.value.flatMap((r) => Object.keys(r)));
	return Math.max(0, all.size - batchColumns.value.length);
});
function cell(value: unknown): string {
	if (value === null || value === undefined) return "";
	const shown = typeof value === "object" ? JSON.stringify(value) : String(value);
	return shown.length > MAX_CELL_CHARS ? shown.slice(0, MAX_CELL_CHARS) + "…" : shown;
}

const summary = computed(() => {
	const args = props.toolConfirm.arguments ?? {};
	const doctype = text(args.doctype);
	const name = text(args.name);
	switch (props.toolConfirm.name) {
		case "create_document":
			return doctype ? `Create a new ${doctype} record.` : "Create a new record.";
		case "update_document":
			return doctype && name ? `Change ${doctype} ${name}.` : "Change a record.";
		case "delete_document":
			return doctype && name
				? `Delete ${doctype} ${name}. This cannot be undone.`
				: "Delete a record. This cannot be undone.";
		case "create_documents": {
			const n = batchRows.value.length;
			const what = doctype || "";
			return `Create ${n} new ${what ? what + " " : ""}record${n === 1 ? "" : "s"}, all or nothing.`;
		}
		default:
			return `Run ${props.toolConfirm.name}.`;
	}
});

const MAX_FIELDS = 8;
const MAX_VALUE_CHARS = 120;

/** The record's fields. For always-ask calls, show full code and text without truncation. */
const fields = computed<Array<{ key: string; value: string }>>(() => {
	const data = props.toolConfirm.arguments?.data;
	if (!data || typeof data !== "object" || Array.isArray(data)) return [];
	const out: Array<{ key: string; value: string }> = [];
	for (const [key, raw] of Object.entries(data as Record<string, unknown>)) {
		if (!isAlwaysAsk.value && out.length >= MAX_FIELDS) {
			out.push({ key: "…", value: "see Details" });
			break;
		}
		let value: string;
		if (raw === null || raw === undefined) value = "";
		else if (typeof raw === "object") value = JSON.stringify(raw);
		else value = String(raw);
		if (!isAlwaysAsk.value && value.length > MAX_VALUE_CHARS) {
			value = value.slice(0, MAX_VALUE_CHARS) + "…";
		}
		out.push({ key, value });
	}
	return out;
});

const formattedArgs = computed(() => {
	try {
		return JSON.stringify(props.toolConfirm.arguments ?? {}, null, 2);
	} catch {
		return String(props.toolConfirm.arguments);
	}
});

const formattedTime = computed(() => {
	const ts = props.toolConfirm.timestamp ?? new Date();
	return ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
});
</script>
