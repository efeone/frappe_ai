/**
 * Message state with socketio-relay streaming support.
 *
 * Flow:
 *   1. Browser subscribes frappe.realtime.on("frappe_ai:chunk:<session_id>", handler)
 *   2. Browser calls frappe_ai.api.chat.start_stream — server enqueues the relay
 *      and returns {session_id} immediately, freeing the gunicorn worker.
 *   3. Background worker (queue=long) consumes the agent SSE stream and publishes
 *      each chunk via frappe.publish_realtime("frappe_ai:chunk:<session_id>", chunk).
 *   4. Browser receives chunks via the existing realtime subscription.
 *
 * Chunk types from the relay:
 *   {type:"content",      text:"..."}              – plain-text token
 *   {type:"tool_confirm", id, name, arguments}     – a write waiting for Allow / Deny
 *   {type:"done",         tools_called:[]}         – stream complete
 *   {type:"error",        message:"..."}           – agent or relay error
 *
 * A turn that ends on tool_confirm has made no change. Allow starts a new
 * turn through start_stream with `confirmation` instead of `message`; the
 * server mints the one-time token the agent needs, so the browser never
 * holds one.
 */

import { ref, readonly } from "vue";
import type { AssistantMessage, Message, ToolConfirmMessage } from "../types/messages";
import { getPageContext } from "../utils/context";

interface Chunk {
  type: "content" | "content_block" | "tool_call" | "tool_confirm" | "done" | "error" | "session";
  text?: string;
  message?: string;
  tools_called?: string[];
  // For chunk.type === "session" the agent echoes back the canonical id it
  // wants the client to use for subsequent turns in this conversation.
  id?: string;
  // For chunk.type === "content_block" the parsed block payload
  // (table | chart | kpi | status | text) the FE will render via the block
  // component registry.
  block?: Record<string, unknown> & { type: string };
  // For chunk.type === "tool_call" — name + arguments of the agent's invocation.
  // For chunk.type === "tool_confirm" — the same, plus `id`, for a call not made yet.
  name?: string;
  arguments?: Record<string, unknown>;
}

/** What one turn sends to start_stream: the user's words, or the write they allowed. */
type Turn =
  | { message: string }
  | { confirmation: { tool: string; arguments: Record<string, unknown> } };

interface StreamResult {
  session_id: string;
}

// Frontend safety-net: bound how long a turn can go without a chunk before
// settle("reject") fires. Keep this generous (and >= the server-side
// `AI Assistant Settings.timeout` so the relay surfaces its own error
// first when it can), since chart prompts that fan out to multiple
// tools routinely take 60–90s end-to-end. The timer restarts on every
// chunk, so a long answer — or a chain of auto-approved writes the relay
// runs on one stream — is bounded by silence, not by its whole length.
// A user-visible "timed out" is preferable to a never-resolving spinner
// if everything else breaks.
const CLIENT_TIMEOUT_MS = 120_000;

// Frappe's `frappe.call` rejects with a plain object (not Error) shaped like
//   { exc_type, _server_messages, exception, message? }
// where `_server_messages` is a JSON-encoded array of JSON-encoded message
// objects (yes, double-encoded). The naive `new Error(String(err))` then
// renders as the literal string "[object Object]" in the error bubble.
// _toError normalises any of these shapes into an Error with a human-readable
// message — see BUG-012 regression test.
function _toError(err: unknown): Error {
  if (err instanceof Error) return err;
  if (typeof err === "string") return new Error(err);
  if (err && typeof err === "object") {
    const e = err as Record<string, unknown>;
    // Frappe surfaces validation errors via _server_messages: JSON array of
    // JSON-encoded {message, indicator} objects. Try the first one.
    const sm = e._server_messages;
    if (typeof sm === "string") {
      try {
        const arr = JSON.parse(sm);
        if (Array.isArray(arr) && arr.length > 0) {
          const first = typeof arr[0] === "string" ? JSON.parse(arr[0]) : arr[0];
          if (first && typeof first === "object" && typeof (first as { message?: unknown }).message === "string") {
            return new Error((first as { message: string }).message);
          }
        }
      } catch {
        // fall through to other extraction paths
      }
    }
    if (typeof e.message === "string" && e.message) return new Error(e.message);
    if (typeof e.exception === "string" && e.exception) {
      // "frappe.exceptions.ValidationError: actual message" — strip the prefix.
      return new Error(e.exception.replace(/^[\w.]+Error:\s*/, ""));
    }
    if (typeof e.exc_type === "string" && e.exc_type) return new Error(e.exc_type);
  }
  return new Error("Failed to get response");
}

export function useChat() {
  const messages = ref<Message[]>([]);
  const isLoading = ref(false);
  const canCancel = ref(false);
  const lastError = ref<string | null>(null);

  // Holds a resolve callback so cancelMessage() can cleanly settle the stream promise.
  let _resolveStream: (() => void) | null = null;
  let _activeEventName: string | null = null;

  // Conversation-scoped session id. Reused across sendMessage() calls so
  // Frappe groups all turns under the same AI Chat Session row, and so
  // the agent's FrappeHistoryClient can pull prior messages back into
  // the LLM context for that session. Cleared by clearMessages()
  // ("New conversation") to start a fresh row.
  let _conversationId: string | null = null;

  async function sendMessage(content: string): Promise<void> {
    if (!content.trim() || isLoading.value) return;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content,
      timestamp: new Date(),
    };
    messages.value.push(userMessage);
    await _runTurn({ message: content });
  }

  /**
   * The user allowed a write on its card. The card is settled at once, then a
   * turn starts that runs the call before the model speaks; the server mints
   * the token for it. With `forConversation` the server is first told to skip
   * the card for the rest of this conversation (creates and updates only).
   */
  async function allowWrite(
    confirmId: string,
    options: { forConversation?: boolean } = {},
  ): Promise<void> {
    if (isLoading.value || !_conversationId) return;
    const card = _findConfirm(confirmId);
    if (!card || card.toolConfirm.status !== "pending") return;
    card.toolConfirm.status = "allowed";
    if (options.forConversation) {
      try {
        await frappe.call({
          method: "frappe_ai.api.confirm.set_session_auto_approve",
          args: { session_id: _conversationId, enabled: true },
        });
      } catch {
        // the site may have turned this off since the card was drawn; the
        // single Allow below still stands
      }
    }
    await _runTurn({
      confirmation: { tool: card.toolConfirm.name, arguments: card.toolConfirm.arguments },
    });
  }

  /** The user refused a write. Nothing is sent: a token is never minted, so nothing can run. */
  function denyWrite(confirmId: string): void {
    const card = _findConfirm(confirmId);
    if (!card || card.toolConfirm.status !== "pending") return;
    card.toolConfirm.status = "denied";
  }

  function _findConfirm(confirmId: string): ToolConfirmMessage | undefined {
    const found = messages.value.find(
      (m) => m.role === "tool_confirm" && m.toolConfirm.id === confirmId,
    );
    return found && found.role === "tool_confirm" ? found : undefined;
  }

  async function _runTurn(turn: Turn): Promise<void> {
    isLoading.value = true;
    lastError.value = null;

    const assistantId = crypto.randomUUID();
    const assistantMessage: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      // Initialise the ordered fragment list so chunk handlers can append
      // text/block fragments without first checking presence.
      parts: [],
      pending: true,
      timestamp: null,
    };
    messages.value.push(assistantMessage);

    // Per-call settlement guard. Every path that finalises the stream
    // (done chunk, error chunk, frappe.call error, client timeout, cancel)
    // sets this true and short-circuits any later attempts. Two error
    // bubbles can otherwise appear when, for example, an "error" chunk
    // arrives just before the client timeout fires and both rejection
    // paths run their side effects before Promise.race could disambiguate.
    let settled = false;
    let timerId: ReturnType<typeof setTimeout> | undefined;

    try {
      // Reuse the conversation's session id if we already have one so the
      // agent loads prior turns for the same AI Chat Session row. First
      // message in a conversation mints a fresh id; subsequent ones reuse
      // it. The agent's "session" chunk (below) may override with the
      // canonical id it persisted.
      const sessionId = _conversationId ?? crypto.randomUUID();
      _conversationId = sessionId;
      const eventName = `frappe_ai:chunk:${sessionId}`;
      _activeEventName = eventName;

      await new Promise<void>((resolve, reject) => {
        // Centralised teardown so every settlement path looks identical:
        // unsubscribe, clear the safety-net timer, drop the cancel
        // affordance, mark settled. Subsequent calls (from a late chunk,
        // a stale timer, a duplicate frappe.call error) become no-ops.
        const settle = (kind: "resolve" | "reject", payload?: unknown) => {
          if (settled) return;
          settled = true;
          if (timerId !== undefined) {
            clearTimeout(timerId);
            timerId = undefined;
          }
          frappe.realtime.off(eventName);
          canCancel.value = false;
          _resolveStream = null;
          _activeEventName = null;
          if (kind === "resolve") {
            resolve();
          } else {
            reject(payload as Error);
          }
        };

        // Expose cancel capability before the realtime listener is registered
        // so the stop button can appear as soon as the request is in-flight.
        _resolveStream = () => {
          _updateMessage(assistantId, (m) => {
            m.pending = false;
            if (!m.timestamp) m.timestamp = new Date();
          });
          settle("resolve");
        };
        canCancel.value = true;

        const armTimer = () => {
          if (timerId !== undefined) clearTimeout(timerId);
          timerId = setTimeout(
            () => settle("reject", new Error("Response timed out. Please try again.")),
            CLIENT_TIMEOUT_MS,
          );
        };

        frappe.realtime.on(eventName, (chunk: Chunk) => {
          if (settled) return;
          armTimer();
          if (chunk.type === "session" && chunk.id) {
            // The agent persisted the conversation under this canonical id
            // (which may differ from the optimistic UUID we minted). Adopt
            // it so subsequent turns continue saving against the same row.
            _conversationId = chunk.id;
          } else if (chunk.type === "content" && chunk.text) {
            _updateMessage(assistantId, (m) => {
              m.content += chunk.text;
              // Merge consecutive text chunks into the same fragment so a
              // streaming reply still renders as one markdown block. Only
              // start a new text part when the last fragment was a block —
              // that's what preserves the arrival order in the renderer.
              if (!m.parts) m.parts = [];
              const last = m.parts[m.parts.length - 1];
              if (last && last.kind === "text") {
                last.text += chunk.text;
              } else {
                m.parts.push({ kind: "text", text: chunk.text as string });
              }
              m.pending = false;
            });
          } else if (chunk.type === "content_block" && chunk.block) {
            // Structured blocks (table/chart/kpi/status) — append to the
            // message so MessageBubble.vue renders them via getBlockComponent.
            // The fragment is also pushed onto `parts` at its arrival
            // position so subsequent text chunks render BELOW it instead
            // of being silently inserted above (the historical bug where
            // late text "jumped over" an already-rendered table).
            const block = chunk.block as unknown as import("../types").ContentBlock;
            _updateMessage(assistantId, (m) => {
              if (!m.blocks) m.blocks = [];
              m.blocks.push(block);
              if (!m.parts) m.parts = [];
              m.parts.push({ kind: "block", block });
              m.pending = false;
            });
          } else if (chunk.type === "tool_call" && chunk.name) {
            // Surface tool invocations as their own bubble so the user can
            // see what the agent looked up. The relay only emits the call
            // (no separate result event today); render in "done" state so
            // the card isn't stuck in a perpetual "running" spinner.
            //
            // Insert just before the assistant placeholder so the visible
            // order matches the agent's actual sequence. If the placeholder
            // isn't found (e.g. an error path removed it before a late tool
            // chunk arrived), drop the card rather than appending out of
            // order — the `settled` short-circuit above should normally
            // prevent reaching this branch in that state.
            const assistantIdx = messages.value.findIndex((m) => m.id === assistantId);
            if (assistantIdx < 0) return;
            const toolCallMessage: Message = {
              id: crypto.randomUUID(),
              role: "tool_call",
              content: "",
              toolCall: {
                call_id: crypto.randomUUID(),
                name: chunk.name,
                arguments: chunk.arguments ?? {},
                status: "done",
                timestamp: new Date(),
              },
              timestamp: new Date(),
            };
            messages.value.splice(assistantIdx, 0, toolCallMessage);
          } else if (chunk.type === "tool_confirm" && chunk.name && chunk.id) {
            // A write the agent wants to make and has not made. The card goes
            // before the assistant bubble, like a tool call; the turn's `done`
            // follows at once, and the card's buttons start the next turn.
            const assistantIdx = messages.value.findIndex((m) => m.id === assistantId);
            if (assistantIdx < 0) return;
            const confirmMessage: Message = {
              id: crypto.randomUUID(),
              role: "tool_confirm",
              content: "",
              toolConfirm: {
                id: chunk.id,
                name: chunk.name,
                arguments: chunk.arguments ?? {},
                status: "pending",
                timestamp: new Date(),
              },
              timestamp: new Date(),
            };
            messages.value.splice(assistantIdx, 0, confirmMessage);
          } else if (chunk.type === "done") {
            _updateMessage(assistantId, (m) => {
              m.pending = false;
              m.timestamp = new Date();
            });
            settle("resolve");
          } else if (chunk.type === "error") {
            settle("reject", new Error(chunk.message ?? "Agent error"));
          }
        });

        frappe.call<StreamResult>({
          method: "frappe_ai.api.chat.start_stream",
          args: {
            ...turn,
            session_id: sessionId,
            // Inject route/doctype/docname/currency so the agent prompt
            // can ground answers in the user's current page. The relay
            // forwards this dict into the agent's `context` payload.
            page_context: getPageContext(),
          },
          error: (err: unknown) => settle("reject", _toError(err)),
        });

        // Safety net: if the RQ worker dies without emitting "done" or
        // "error", the promise above never settles. Arm the silence
        // timeout, which flows through `settle()` like every other
        // settlement path, so a late chunk arriving after the timer
        // can't add a second error bubble.
        armTimer();
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to get response";
      lastError.value = msg;
      // Remove the empty assistant placeholder and add a typed error message.
      messages.value = messages.value.filter((m) => m.id !== assistantId);
      _addErrorMessage(msg);
    } finally {
      isLoading.value = false;
      canCancel.value = false;
      _resolveStream = null;
    }
  }

  function _serverCancelInFlight(): void {
    // Fire-and-forget signal to the worker so it stops relaying chunks and
    // stops burning Ollama cycles for a turn the user no longer wants.
    // See BUG-003 + BUG-008. Client-side settle handles the bubble cleanup;
    // this is the server-side counterpart.
    if (!_conversationId) return;
    frappe.call({
      method: "frappe_ai.api.chat.cancel_stream",
      args: { session_id: _conversationId },
    });
  }

  function cancelMessage(): void {
    _serverCancelInFlight();
    if (_resolveStream) _resolveStream();
  }

  function clearMessages(): void {
    // If a stream is in flight, settle it cleanly first. settle() calls
    // frappe.realtime.off(eventName), clears the safety timer, drops
    // _resolveStream / _activeEventName, and resolves the awaited promise.
    // Without this, the listener leaked (OBS-006) and a follow-up
    // sendMessage could race the orphan listener (BUG-009: stray chunks
    // routed at the previous message's assistantId that no longer exists,
    // leaving the new assistant bubble empty).
    if (isLoading.value) _serverCancelInFlight();
    if (_resolveStream) _resolveStream();
    messages.value = [];
    isLoading.value = false;
    lastError.value = null;
    // "New conversation" — drop the session id so the next message opens
    // a fresh AI Chat Session row.
    _conversationId = null;
  }

  // Stop the worker if the tab is closing or navigating away mid-stream.
  // Uses sendBeacon when available so the request survives unload. See
  // BUG-003: without this, the worker kept relaying SSE chunks to a defunct
  // channel and the agent kept generating tokens for no consumer.
  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", () => {
      if (!isLoading.value || !_conversationId) return;
      try {
        const blob = new Blob(
          [JSON.stringify({ session_id: _conversationId })],
          { type: "application/json" },
        );
        navigator.sendBeacon(
          "/api/method/frappe_ai.api.chat.cancel_stream",
          blob,
        );
      } catch {
        // best-effort
      }
    });
  }

  interface RecentMessagesResponse {
    session_id: string | null;
    messages: Array<{
      id: string;
      role: string;
      content: string;
      timestamp: string | null;
      // the cards an assistant reply carried (KPI, chart, table), as the agent saved them
      blocks?: import("../types").ContentBlock[];
    }>;
  }

  async function loadRecentConversation(sessionId?: string): Promise<void> {
    // Hydrate the sidebar on mount so a page reload doesn't throw away the
    // user's last chat, or open one picked from the history list. Best-effort:
    // any failure leaves the bubble list as it was.
    if (sessionId && isLoading.value) return; // never swap chats under a reply still arriving
    try {
      const result = await new Promise<RecentMessagesResponse>((resolve, reject) => {
        frappe.call<RecentMessagesResponse>({
          method: "frappe_ai.api.chat.get_recent_messages",
          args: sessionId ? { limit: 50, session_id: sessionId } : { limit: 50 },
          callback: (r) => resolve(r?.message ?? { session_id: null, messages: [] }),
          error: reject,
        });
      });

      // on mount an empty answer keeps the empty state; a picked chat always replaces the view
      if (!result || !result.session_id || (!sessionId && result.messages.length === 0)) return;

      _conversationId = result.session_id;
      // Skip rows whose role we can't faithfully render (e.g. persisted
      // "tool" messages — we don't have the toolCall metadata in the
      // hydrated row). Better to omit than to mislabel them as user input.
      messages.value = result.messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m): Message => {
          const timestamp = m.timestamp ? new Date(m.timestamp) : null;
          if (m.role === "user") return { id: m.id, role: "user", content: m.content, timestamp };
          // no `parts` on a restored reply: the bubble renders its text, then its cards
          const blocks = Array.isArray(m.blocks) ? m.blocks : [];
          return { id: m.id, role: "assistant", content: m.content, timestamp, ...(blocks.length ? { blocks } : {}) };
        })
        // a reply with neither text nor a card has nothing to show
        .filter((m) => m.role !== "assistant" || !!m.content || !!m.blocks?.length);
    } catch (err) {
      // Swallow — restoring history is a nice-to-have, not a blocker.
      console.warn("[AI Sidebar] loadRecentConversation failed", err);
    }
  }

  /** Update an assistant message in-place by id.
   *
   * Restricted to assistant messages because that's the only role that
   * receives streamed chunks. Tightening the callback parameter type
   * means callers can write `m.blocks` / `m.pending` without manual
   * narrowing.
   */
  function _updateMessage(id: string, updater: (m: AssistantMessage) => void): void {
    const idx = messages.value.findIndex((m) => m.id === id);
    if (idx < 0) return;
    const target = messages.value[idx];
    if (target.role !== "assistant") return;
    // Mutate in place. Vue 3's reactive proxy detects nested property
    // writes, so the splice-clone pattern used previously was wasteful:
    // for a long streamed response it copied the message object on every
    // chunk and triggered a deep watch in ChatMessages.vue (O(N²) over
    // message length). In-place mutation drops both costs.
    updater(target);
  }

  // Cross-tab sync: server publishes `frappe_ai:msg_added` whenever an AI
  // Chat Message row is inserted (see the `doc_events` hook in
  // frappe_ai.api.realtime). The handler appends the new message if it
  // belongs to the conversation this tab is currently showing AND we're not
  // mid-stream (the streaming tab already renders these via chunk events;
  // appending again would duplicate the bubble).
  interface MsgAddedPayload {
    session_id: string;
    id: string;
    role: "user" | "assistant" | "tool";
    content: string;
    timestamp: string | null;
  }
  frappe.realtime.on("frappe_ai:msg_added", (payload: MsgAddedPayload) => {
    if (!payload || payload.session_id !== _conversationId) return;
    if (isLoading.value) return; // active tab handles its own turn via chunks
    if (payload.role !== "user" && payload.role !== "assistant") return;
    if (messages.value.some((m) => m.id === payload.id)) return;
    messages.value.push({
      id: payload.id,
      role: payload.role,
      content: payload.content,
      timestamp: payload.timestamp ? new Date(payload.timestamp) : null,
    });
  });

  function _addErrorMessage(message: string): void {
    lastError.value = message;
    messages.value.push({
      id: crypto.randomUUID(),
      role: "error",
      content: message,
      error: { code: "REQUEST_FAILED", message },
      timestamp: new Date(),
    });
  }

  return {
    messages,
    isLoading: readonly(isLoading),
    canCancel: readonly(canCancel),
    lastError: readonly(lastError),
    sendMessage,
    allowWrite,
    denyWrite,
    cancelMessage,
    clearMessages,
    loadRecentConversation,
    /** The conversation now on screen, for the history list to mark. */
    currentConversation: () => _conversationId,
  };
}
