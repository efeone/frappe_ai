<script setup lang="ts">
import { frappeIcon } from "../utils/frappe-icon";

const emit = defineEmits<{
	clear: [];
	close: [];
	history: [];
}>();
</script>

<template>
	<div class="frappe-ai-header">
		<div class="frappe-ai-header-left">
			<span class="frappe-ai-header-title">AI Sidebar</span>
			<span class="frappe-ai-header-brand">from Vyogo</span>
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
