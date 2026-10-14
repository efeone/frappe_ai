<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import type { ModelOption } from "../composables/usePreferences";

// A searchable menu, not a <select>: a provider can list hundreds of models.
const props = defineProps<{ models: readonly ModelOption[]; current: string | null }>();
const emit = defineEmits<{ select: [id: string] }>();

const SHOWN = 50;
const open = ref(false);
const query = ref("");
const active = ref(0);
const root = ref<HTMLElement>();
const search = ref<HTMLInputElement>();

/** The configured label, or the model's own name without its provider: "gpt-4.1-mini". */
function nameOf(m: ModelOption): string {
	return m.label && m.label !== m.id ? m.label : (m.id.split("/").pop() ?? m.id);
}

const currentName = computed(() => {
	const m = props.models.find((x) => x.id === props.current);
	return m ? nameOf(m) : (props.current ?? "Model");
});

const matches = computed(() => {
	const q = query.value.trim().toLowerCase();
	const hits = q
		? props.models.filter((m) => m.id.toLowerCase().includes(q) || nameOf(m).toLowerCase().includes(q))
		: props.models;
	return { shown: hits.slice(0, SHOWN), more: Math.max(0, hits.length - SHOWN) };
});

function onOutside(event: MouseEvent) {
	if (root.value && !root.value.contains(event.target as Node)) close();
}

async function toggle() {
	if (open.value) return close();
	open.value = true;
	query.value = "";
	active.value = 0;
	document.addEventListener("mousedown", onOutside);
	await nextTick();
	search.value?.focus();
}

function close() {
	open.value = false;
	document.removeEventListener("mousedown", onOutside);
}

function choose(id: string) {
	close();
	if (id !== props.current) emit("select", id);
}

function onKey(event: KeyboardEvent) {
	const list = matches.value.shown;
	if (event.key === "Escape") close();
	else if (event.key === "ArrowDown") active.value = Math.min(active.value + 1, list.length - 1);
	else if (event.key === "ArrowUp") active.value = Math.max(active.value - 1, 0);
	else if (event.key === "Enter" && list[active.value]) choose(list[active.value].id);
	else return;
	event.preventDefault();
}

onBeforeUnmount(() => document.removeEventListener("mousedown", onOutside));
</script>

<template>
	<div ref="root" class="frappe-ai-model-picker">
		<button
			type="button"
			class="frappe-ai-select frappe-ai-select--model"
			:aria-label="`Model: ${currentName}`"
			aria-haspopup="listbox"
			:aria-expanded="open"
			:disabled="models.length < 2"
			:title="current ?? ''"
			@click="toggle"
		>
			{{ currentName }}
		</button>
		<div v-if="open" class="frappe-ai-model-menu">
			<input
				ref="search"
				v-model="query"
				class="frappe-ai-model-search"
				type="search"
				placeholder="Search models"
				aria-label="Search models"
				@input="active = 0"
				@keydown="onKey"
			/>
			<ul class="frappe-ai-model-list" role="listbox" aria-label="Models">
				<li
					v-for="(m, i) in matches.shown"
					:key="m.id"
					role="option"
					:aria-selected="m.id === current"
					:class="[
						'frappe-ai-model-option',
						i === active ? 'frappe-ai-model-option--active' : '',
						m.id === current ? 'frappe-ai-model-option--current' : '',
					]"
					:title="m.id"
					@mouseenter="active = i"
					@click="choose(m.id)"
				>
					{{ nameOf(m) }}
				</li>
				<li v-if="matches.shown.length === 0" class="frappe-ai-model-empty">No model matches.</li>
				<li v-if="matches.more > 0" class="frappe-ai-model-empty">
					{{ matches.more }} more: keep typing to narrow.
				</li>
			</ul>
		</div>
	</div>
</template>
