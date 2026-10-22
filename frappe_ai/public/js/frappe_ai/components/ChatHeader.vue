<script setup lang="ts">
import { frappeIcon } from "../utils/frappe-icon";

withDefaults(
	defineProps<{
		/** The assistant's name; the neutral default unless the site or its host brands it. */
		name?: string;
		/** A short line after the name, e.g. who provides it. Hidden when empty. */
		byline?: string;
		/** A small square mark before the name. Hidden when empty. */
		logoUrl?: string;
	}>(),
	{ name: "Vyogo AI", byline: "", logoUrl: "" },
);

const emit = defineEmits<{
	clear: [];
	close: [];
	history: [];
}>();
</script>

<template>
	<div class="frappe-ai-header">
		<div class="frappe-ai-header-left">
			<img v-if="logoUrl" class="frappe-ai-header-logo" :src="logoUrl" alt="" />
			<span class="frappe-ai-header-title">{{ name }}</span>
			<span v-if="byline" class="frappe-ai-header-brand">{{ byline }}</span>
		</div>
		<div class="frappe-ai-header-actions">
			<button
				class="frappe-ai-icon-btn"
				title="Chat history"
				aria-label="Chat history"
				@click="emit('history')"
			>
				<!-- eslint-disable-next-line vue/no-v-html -->
				<span v-html="frappeIcon('history', 'sm')" />
			</button>
			<button
				class="frappe-ai-icon-btn"
				title="New conversation"
				aria-label="New conversation"
				@click="emit('clear')"
			>
				<!-- eslint-disable-next-line vue/no-v-html -->
				<span v-html="frappeIcon('rotate-ccw', 'sm')" />
			</button>
			<button
				class="frappe-ai-icon-btn"
				title="Close sidebar"
				aria-label="Close sidebar"
				@click="emit('close')"
			>
				<!-- eslint-disable-next-line vue/no-v-html -->
				<span v-html="frappeIcon('x', 'sm')" />
			</button>
		</div>
	</div>
</template>
