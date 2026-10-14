<script setup lang="ts">
import { onMounted, ref } from "vue";

interface SessionRow {
	id: string;
	title: string;
	updated: string | null;
}

const props = defineProps<{ currentId?: string | null }>();
const emit = defineEmits<{ open: [id: string]; close: [] }>();

const sessions = ref<SessionRow[]>([]);
const loading = ref(true);
const failed = ref(false);

onMounted(() => {
	frappe.call<{ sessions?: SessionRow[] }>({
		method: "frappe_ai.api.chat.list_sessions",
		args: { limit: 30 },
		callback: (r) => {
			sessions.value = r?.message?.sessions ?? [];
			loading.value = false;
		},
		error: () => {
			failed.value = true;
			loading.value = false;
		},
	});
});

/** "Today 14:05", "Yesterday", or "12 Oct". */
function when(iso: string | null): string {
	if (!iso) return "";
	const d = new Date(iso);
	const now = new Date();
	const days = Math.floor((new Date(now.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 86_400_000);
	if (days === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
	if (days === 1) return "Yesterday";
	return d.toLocaleDateString([], { day: "numeric", month: "short" });
}
</script>

<template>
	<div class="frappe-ai-history" role="dialog" aria-label="Chat history">
		<div class="frappe-ai-history-head">
			<span class="frappe-ai-history-title">Chats</span>
			<button type="button" class="frappe-ai-btn frappe-ai-btn--link" @click="emit('close')">Back</button>
		</div>
		<p v-if="loading" class="frappe-ai-history-note">Loading…</p>
		<p v-else-if="failed" class="frappe-ai-history-note">Could not load your chats.</p>
		<p v-else-if="sessions.length === 0" class="frappe-ai-history-note">No past chats yet.</p>
		<ul v-else class="frappe-ai-history-list">
			<li v-for="s in sessions" :key="s.id">
				<button
					type="button"
					:class="['frappe-ai-history-item', s.id === props.currentId ? 'frappe-ai-history-item--current' : '']"
					@click="emit('open', s.id)"
				>
					<span class="frappe-ai-history-item-title">{{ s.title }}</span>
					<span class="frappe-ai-history-item-when">{{ when(s.updated) }}</span>
				</button>
			</li>
		</ul>
	</div>
</template>
