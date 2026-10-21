<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useChat } from "../composables/useChat";
import { useSettings, WRITE_APPROVAL_ASK } from "../composables/useSettings";
import { accentForeground, isAccent } from "../utils/accent";
import ChatHeader from "./ChatHeader.vue";
import ChatToolbar from "./ChatToolbar.vue";
import ChatHistory from "./ChatHistory.vue";
import ChatMessages from "./ChatMessages.vue";
import ChatInput from "./ChatInput.vue";

const props = defineProps<{
	sidebarWidth: number;
	keyboardShortcut: string;
}>();

const emit = defineEmits<{ close: [] }>();

const {
	messages,
	isLoading,
	canCancel,
	sendMessage,
	allowWrite,
	denyWrite,
	cancelMessage,
	clearMessages,
	loadRecentConversation,
	currentConversation,
} = useChat();

const showHistory = ref(false);

async function openConversation(id: string) {
	showHistory.value = false;
	await loadRecentConversation(id);
}

const { settings } = useSettings();

// The width, plus the brand accent when the site or its host set one: it
// overrides the stylesheet's neutral --ai-accent for everything inside.
const sidebarStyle = computed(() => {
	const style: Record<string, string> = { width: props.sidebarWidth + "px" };
	const accent = settings.value.accentColor;
	if (isAccent(accent)) {
		style["--ai-accent"] = accent;
		style["--ai-accent-fg"] = accentForeground(accent) as string;
	}
	return style;
});

// The offer only makes sense where cards are shown at all, and only when the
// administrator turned it on.
const canAllowConversation = computed(
	() => settings.value.allowSessionAutoApprove && settings.value.writeApproval === WRITE_APPROVAL_ASK,
);

// Hydrate from server-side history on first mount so a page reload doesn't
// erase the user's last chat. Failures are swallowed in the composable.
onMounted(() => {
	loadRecentConversation();
});

function handleSend(content: string) {
	sendMessage(content);
}

function handleAllow(confirmId: string, forConversation: boolean) {
	allowWrite(confirmId, { forConversation });
}

function handleDeny(confirmId: string) {
	denyWrite(confirmId);
}

function handleStop() {
	cancelMessage();
}

function handleClear() {
	showHistory.value = false;
	clearMessages();
}

function handleClose() {
	emit("close");
}
</script>

<template>
	<div class="frappe-ai-sidebar" :style="sidebarStyle">
		<ChatHeader
			:name="settings.assistantName"
			:byline="settings.assistantByline"
			:logo-url="settings.assistantLogoUrl"
			@clear="handleClear"
			@close="handleClose"
			@history="showHistory = !showHistory"
		/>
		<ChatHistory
			v-if="showHistory"
			:current-id="currentConversation()"
			@open="openConversation"
			@close="showHistory = false"
		/>
		<ChatMessages
			v-show="!showHistory"
			:messages="messages"
			:can-allow-conversation="canAllowConversation"
			@send="handleSend"
			@allow="handleAllow"
			@deny="handleDeny"
		/>
		<ChatInput
			:busy="isLoading"
			:can-cancel="canCancel"
			@send="handleSend"
			@stop="handleStop"
		>
			<template #footer><ChatToolbar /></template>
		</ChatInput>
	</div>
</template>
