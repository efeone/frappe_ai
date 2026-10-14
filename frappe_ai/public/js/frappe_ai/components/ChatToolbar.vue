<script setup lang="ts">
import { onMounted } from "vue";
import { APPROVAL_LABELS, usePreferences } from "../composables/usePreferences";
import ModelPicker from "./ModelPicker.vue";

const {
	models,
	currentModel,
	approvalModes,
	currentApproval,
	error,
	loadPreferences,
	setModel,
	setApprovalMode,
} = usePreferences();

onMounted(() => {
	loadPreferences();
});

function onApproval(event: Event) {
	setApprovalMode((event.target as HTMLSelectElement).value);
}
</script>

<template>
	<div class="frappe-ai-toolbar">
		<ModelPicker
			v-if="models.length > 0"
			:models="models"
			:current="currentModel"
			@select="setModel"
		/>
		<label v-if="approvalModes.length > 0" class="frappe-ai-picker frappe-ai-picker--approval">
			<span class="frappe-ai-sr-only">Approval</span>
			<select
				:class="[
					'frappe-ai-select',
					currentApproval && currentApproval.startsWith('Auto') ? 'frappe-ai-select--auto' : '',
				]"
				aria-label="How changes are approved"
				:value="currentApproval ?? ''"
				:disabled="approvalModes.length < 2"
				@change="onApproval"
			>
				<option v-for="mode in approvalModes" :key="mode" :value="mode" :title="mode">
					{{ APPROVAL_LABELS[mode] ?? mode }}
				</option>
			</select>
		</label>
		<p v-if="error" class="frappe-ai-toolbar-error" role="status">{{ error }}</p>
	</div>
</template>
