import json
import os
import uuid
from urllib.parse import urlparse

import frappe
import requests
from frappe import _

from frappe_ai.api import availability, confirm

# Upper bound on the user's single-message payload. Mirrors the agent's
# own 32 000-char Pydantic cap with a stricter ceiling because the relay
# also has to serialise this to RQ. Settable per-site via
# `frappe_ai_message_max_chars` in site_config.json.
_DEFAULT_MESSAGE_MAX_CHARS = 10_000


def _agent_ssl_verify():
	"""Return SSL verification parameter for outbound requests to the AI agent.

	Resolution order:
	1. Per-site in site_config.json: `frappe_ai_agent_ssl_verify`
	2. Environment variable: `FRAPPE_AI_AGENT_SSL_VERIFY` (a whole bench, e.g. its pods)
	3. Default: False (disable SSL verification by default for internal cluster services)

	The value is read the same either way:
	- False / "false" / "0" / "no": disable SSL verification (private internal certificates)
	- True / "true" / "1" / "yes": force standard CA verification
	- Path string: path to a custom CA bundle file
	"""
	val = None
	conf = getattr(frappe, "local", None) and getattr(frappe.local, "conf", None)
	if conf:
		val = conf.get("frappe_ai_agent_ssl_verify")
	if val is None:
		val = os.environ.get("FRAPPE_AI_AGENT_SSL_VERIFY")
	if val is False:
		return False
	if val is True:
		return True
	if isinstance(val, str):
		text = val.strip()
		if text.lower() in ("false", "0", "no"):
			return False
		if text.lower() in ("true", "1", "yes"):
			return True
		if text:
			return text
	return False


def _agent_url() -> str:
	return frappe.local.conf.get("frappe_ai_agent_url", "").rstrip("/")


def _bench_url() -> str:
	"""The URL where the agent and MCP server can reach this bench."""
	configured = frappe.local.conf.get("frappe_ai_bench_url")
	if configured:
		return str(configured).rstrip("/")
	try:
		url = frappe.utils.get_url()
		if url:
			return url.rstrip("/")
	except Exception:
		pass
	return ""


def _message_max_chars() -> int:
	raw = frappe.local.conf.get("frappe_ai_message_max_chars")
	if raw is None:
		return _DEFAULT_MESSAGE_MAX_CHARS
	try:
		v = int(raw)
		return v if v > 0 else _DEFAULT_MESSAGE_MAX_CHARS
	except (TypeError, ValueError):
		return _DEFAULT_MESSAGE_MAX_CHARS


def _validate_agent_url(url: str) -> None:
	"""Validate agent URL structure.

	Egress network security and IP routing are platform concerns managed
	via network policies and infrastructure firewalls.
	"""
	parsed = urlparse(url)
	if parsed.scheme not in ("http", "https"):
		frappe.throw(_("AI agent URL must use http or https (got '{0}').").format(parsed.scheme or "(none)"))
	if not parsed.hostname:
		frappe.throw(_("AI agent URL must include a hostname."))


_CANCEL_KEY_PREFIX = "frappe_ai:cancel:"
# Short TTL: cancellation should propagate within a few seconds. If the worker
# never sees the flag (already done), the key just expires.
_CANCEL_KEY_TTL_SECONDS = 300


def _cancel_key(session_id: str) -> str:
	return _CANCEL_KEY_PREFIX + session_id


@frappe.whitelist()
def cancel_stream(session_id: str) -> dict:
	"""Signal the running worker for ``session_id`` to stop relaying chunks.

	Used by the sidebar's ``cancelMessage`` (e.g. user clicked Stop) and by
	the ``beforeunload`` hook (BUG-003). The worker checks the cache flag
	between agent SSE reads and exits the loop when the flag appears.
	"""
	if not session_id or not session_id.strip():
		return {"ok": False}
	# Use site cache so all worker processes for this site see the flag.
	frappe.cache().set_value(
		_cancel_key(session_id),
		"1",
		expires_in_sec=_CANCEL_KEY_TTL_SECONDS,
	)
	return {"ok": True}


def _is_stream_cancelled(session_id: str) -> bool:
	"""Return True if a cancel was requested for ``session_id``.

	Consumes the flag on read so subsequent turns on the same session start
	fresh — preventing a stale cancel from killing a brand new turn.
	"""
	if not session_id:
		return False
	key = _cancel_key(session_id)
	cache = frappe.cache()
	val = cache.get_value(key)
	if val:
		cache.delete_value(key)
		return True
	return False


@frappe.whitelist()
def list_sessions(limit: int = 30) -> dict:
	"""The caller's conversations, newest first, for the sidebar's history list."""
	user = frappe.session.user
	if user == "Guest":
		return {"sessions": []}
	rows = frappe.get_all(
		"AI Chat Session",
		filters={"user": user},
		fields=["name", "title", "modified"],
		order_by="modified desc",
		limit=max(1, min(int(limit), 100)),
	)
	return {
		"sessions": [
			{
				"id": r["name"],
				"title": r.get("title") or _("Untitled chat"),
				"updated": _to_iso_utc(r["modified"]),
			}
			for r in rows
		]
	}


@frappe.whitelist()
def get_recent_messages(limit: int = 50, session_id: str | None = None) -> dict:
	"""Return the caller's most recent AI Chat Session and its messages.

	Powers the sidebar's restore-on-mount: the user reopens the desk, the
	sidebar hydrates from this endpoint so the conversation history isn't
	thrown away each page-load.

	Returns {"session_id": str | None, "messages": [{role, content, timestamp}]}.
	An empty session_id with empty messages means the user has no prior
	conversation — the sidebar renders the empty state.
	"""
	user = frappe.session.user
	if user == "Guest":
		return {"session_id": None, "messages": []}

	if session_id:
		# a chat from the history list: only ever the caller's own
		if frappe.db.get_value("AI Chat Session", session_id, "user") != user:
			frappe.throw(_("That conversation was not found."), frappe.DoesNotExistError)
	else:
		sessions = frappe.get_all(
			"AI Chat Session",
			filters={"user": user},
			fields=["name"],
			order_by="modified desc",
			limit=1,
		)
		if not sessions:
			return {"session_id": None, "messages": []}
		session_id = sessions[0]["name"]
	# Frappe v16's @whitelist wrapper coerces query-string args to the
	# declared type before the endpoint runs (pydantic-backed), so `limit`
	# arrives as a real int. Unparseable input is rejected upstream with
	# FrappeTypeError — see test_unparseable_limit_raises_frappe_type_error.
	safe_limit = max(1, min(limit, 200))

	# newest first, then put back in order: oldest-first with a limit kept the start of a long
	# conversation and dropped the part the user had just been reading
	rows = frappe.get_all(
		"AI Chat Message",
		filters={"session": session_id},
		fields=["name", "role", "content", "creation", "tool_result_json"],
		order_by="creation desc",
		limit=safe_limit,
	)
	rows.reverse()
	messages = []
	for r in rows:
		message = {
			"id": r["name"],
			"role": r["role"],
			"content": r["content"] or "",
			"timestamp": _to_iso_utc(r.get("creation")),
		}
		blocks = _saved_blocks(r.get("tool_result_json")) if r["role"] == "assistant" else []
		if blocks:
			message["blocks"] = blocks
		messages.append(message)
	return {"session_id": session_id, "messages": messages}


# A reply's cards (KPI, chart, table, status list) as the agent saved them, so a reopened chat
# shows what the live one did; an answer that was only a card was an empty bubble before.
_MAX_SAVED_BLOCKS = 20


def _saved_blocks(raw) -> list[dict]:
	"""The `blocks` the agent stored with a message; [] for anything unreadable."""
	if not raw:
		return []
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
	except (json.JSONDecodeError, ValueError, TypeError):
		return []
	blocks = data.get("blocks") if isinstance(data, dict) else None
	if not isinstance(blocks, list):
		return []
	return [b for b in blocks if isinstance(b, dict) and isinstance(b.get("type"), str)][:_MAX_SAVED_BLOCKS]


def _to_iso_utc(value) -> str | None:
	"""Serialise a Frappe datetime as ISO 8601 with an explicit UTC suffix.

	Frappe writes ``creation`` naively in ``System Settings.time_zone`` (NOT
	the container's OS TZ). ``str(dt)`` therefore returns e.g.
	``"2026-05-16 16:17:20.101924"`` with no tz suffix even when the container
	is UTC. JavaScript's ``new Date(str)`` parses that as LOCAL time, which
	produced BUG-002: fresh-sent bubbles (rendered with ``new Date()``) showed
	a different time than restored bubbles after reload.

	Convert from the configured system tz to UTC, then emit ISO 8601 with
	``Z`` so the FE's ``new Date(ts)`` resolves to the same instant on every
	device and renders in the user's local timezone consistently.
	"""
	import datetime as _dt

	from frappe.utils import get_datetime, get_system_timezone

	if value is None:
		return None

	dt = get_datetime(value) if not isinstance(value, _dt.datetime) else value
	if dt is None:
		return None

	if dt.tzinfo is None:
		# Naive Frappe datetime: localise to the system timezone first.
		try:
			system_tz = get_system_timezone()
			# get_system_timezone returns a string like "Asia/Kolkata".
			# Use zoneinfo for the actual conversion (Python 3.9+).
			from zoneinfo import ZoneInfo

			dt = dt.replace(tzinfo=ZoneInfo(system_tz))
		except Exception:
			# Fallback: assume UTC if anything goes wrong getting the tz.
			dt = dt.replace(tzinfo=_dt.timezone.utc)

	utc_dt = dt.astimezone(_dt.timezone.utc)
	# Replace "+00:00" with "Z" for the canonical UTC suffix the FE expects.
	return utc_dt.isoformat().replace("+00:00", "Z")


_ALLOWED_PAGE_CONTEXT_KEYS = ("route", "doctype", "docname", "currency")


def _sanitize_page_context(raw) -> dict:
	"""Accept only a flat dict of expected page-context fields with string values.

	frappe.whitelist serialises JSON args, so `page_context` may arrive as a
	dict or as a JSON string. Anything else (lists, nested dicts, non-strings)
	gets dropped so a malformed frontend can't bloat the agent payload or
	smuggle non-grounding data into the system prompt.
	"""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except (json.JSONDecodeError, ValueError):
			return {}
	if not isinstance(raw, dict):
		return {}
	out: dict = {}
	for key in _ALLOWED_PAGE_CONTEXT_KEYS:
		val = raw.get(key)
		if isinstance(val, str) and val:
			# Cap at 200 chars so a giant route can't blow the prompt.
			out[key] = val[:200]
	return out


# ──────────────────────────────────────────────────────────────────────────
# Models: the agent decides which may be used, each user picks one of them
# ──────────────────────────────────────────────────────────────────────────

_MODEL_KEY = "frappe_ai_model"
_MODELS_CACHE_KEY = "frappe_ai:models"
_MODELS_CACHE_SECONDS = 300


def _agent_get_json(path: str) -> dict:
	"""GET a JSON route of the agent as the caller, with the same site and certificate handling as the relay."""
	agent_url = _agent_url()
	if not agent_url:
		frappe.throw(_("AI agent URL is not configured. Set frappe_ai_agent_url in site_config."))
	_validate_agent_url(agent_url)
	headers = {"Accept": "application/json"}
	site_name = getattr(getattr(frappe, "local", None), "site", None)
	if site_name:
		headers["X-Frappe-Site-Name"] = site_name
	bench_url = _bench_url()
	if bench_url:
		headers["X-Frappe-Bench-URL"] = bench_url
	kwargs: dict = {"cookies": {"sid": frappe.session.sid}, "headers": headers, "timeout": 8}
	verify_ssl = _agent_ssl_verify()
	if verify_ssl is not None:
		kwargs["verify"] = verify_ssl
	if verify_ssl is False:
		import urllib3

		urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
	response = requests.get(f"{agent_url}{path}", **kwargs)
	response.raise_for_status()
	data = response.json()
	return data if isinstance(data, dict) else {}


def _available_models() -> dict:
	"""The agent's list of models, cached briefly: {"models": [{"id", "label"}], "default": id}."""
	cached = frappe.cache().get_value(_MODELS_CACHE_KEY)
	if isinstance(cached, dict) and cached.get("models"):
		return cached
	try:
		data = _agent_get_json("/api/v1/models")
	except (requests.exceptions.RequestException, ValueError):
		# an agent that cannot list its models still answers with its default one
		return {"models": [], "default": None}
	models = [
		{"id": str(m["id"]), "label": str(m.get("label") or m["id"])}
		for m in data.get("models") or []
		if isinstance(m, dict) and m.get("id")
	]
	result = {"models": models, "default": data.get("default")}
	if models:
		frappe.cache().set_value(_MODELS_CACHE_KEY, result, expires_in_sec=_MODELS_CACHE_SECONDS)
	return result


def user_model(user: str) -> str | None:
	"""The model the user picked, while the agent still offers it; None means the agent's default."""
	chosen = frappe.defaults.get_user_default(_MODEL_KEY, user) if user else None
	if not chosen:
		return None
	offered = {m["id"] for m in _available_models()["models"]}
	return chosen if chosen in offered else None


@frappe.whitelist()
def get_models() -> dict:
	"""What the sidebar's model menu shows: the agent's models and the caller's pick."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	available = _available_models()
	return {**available, "current": user_model(user) or available.get("default")}


@frappe.whitelist(methods=["POST"])
def set_model(model: str) -> dict:
	"""Save the caller's model; refused unless the agent offers it."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	model = (model or "").strip()
	if model not in {m["id"] for m in _available_models()["models"]}:
		frappe.throw(_("That model is not available."))
	frappe.defaults.set_user_default(_MODEL_KEY, model, user)
	return get_models()


# One user message may chain this many writes the policy allows without a click. It bounds a
# model that keeps writing, since every hop is a whole agent turn. Settable per site via
# `frappe_ai_max_auto_approved_writes` in site_config.json; past the cap the next write shows
# a card, and Allow starts a fresh chain.
_MAX_AUTO_APPROVED_WRITES = 20


def _max_auto_approved_writes() -> int:
	raw = frappe.local.conf.get("frappe_ai_max_auto_approved_writes")
	try:
		value = int(raw) if raw is not None else _MAX_AUTO_APPROVED_WRITES
	except (TypeError, ValueError):
		return _MAX_AUTO_APPROVED_WRITES
	return value if value >= 0 else _MAX_AUTO_APPROVED_WRITES


@frappe.whitelist()
def start_stream(
	message: str | None = None,
	session_id: str | None = None,
	page_context=None,
	confirmation=None,
) -> dict:
	"""Enqueue an agent SSE relay in the background and return the session_id immediately.

	The browser subscribes to frappe_ai:chunk:<session_id> via frappe.realtime.on
	before calling this endpoint. The background worker (queue=long) consumes the
	agent's SSE stream and publishes each chunk via frappe.publish_realtime.

	`page_context` (optional) is a dict {route, doctype, docname, currency}
	captured from the browser; forwarded into the agent's context so the
	system prompt can ground answers in the user's current page.

	`confirmation` (instead of `message`) is the `{tool, arguments}` of a write
	the user just allowed on a confirmation card. The token for it is minted
	here, as the user, and the agent runs that one call before the model speaks.
	"""
	confirmation = confirm.parse_confirmation(confirmation)
	if confirmation is None:
		text = message or ""
		if not text.strip():
			frappe.throw(_("Message is required"))
		max_chars = _message_max_chars()
		if len(text) > max_chars:
			frappe.throw(_("Message too long (max {0} characters).").format(max_chars))
	else:
		if message:
			frappe.throw(_("Send a message or a confirmation, not both."))
		if not session_id or not session_id.strip():
			frappe.throw(_("A confirmation needs the conversation it belongs to."))
		message = None

	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)

	settings = frappe.get_single("AI Assistant Settings")
	if availability.switched_off_by_host():
		frappe.throw(_("The AI assistant is turned off for this site."))
	if not settings.enabled:
		frappe.throw(_("AI Assistant is not enabled"))

	if not session_id:
		session_id = str(uuid.uuid4())

	agent_url = _agent_url()
	if not agent_url:
		frappe.throw(_("AI agent URL is not configured. Set frappe_ai_agent_url in site_config."))
	_validate_agent_url(agent_url)

	if confirmation is not None:
		if confirm.decide(confirmation["tool"], user, session_id) == "deny":
			frappe.throw(_("The Vyogo AI is not allowed to change data on this site."))
		confirmation["token"] = confirm.issue(user, confirmation["tool"], confirmation["arguments"])

	timeout_seconds = int(settings.timeout or 30)

	frappe.enqueue(
		"frappe_ai.api.chat._stream_to_agent",
		queue="long",
		# Worker timeout covers the longest chain of auto-approved writes, each a
		# full agent turn bounded by the read timeout, plus room to emit the error event.
		timeout=timeout_seconds * (1 + _max_auto_approved_writes()) + 30,
		# Enqueue after the HTTP transaction commits so the worker sees all side effects.
		enqueue_after_commit=True,
		message=message,
		session_id=session_id,
		user=user,
		sid=frappe.session.sid,
		agent_url=agent_url,
		timeout_seconds=timeout_seconds,
		page_context=_sanitize_page_context(page_context),
		confirmation=confirmation,
	)

	return {"session_id": session_id}


# Connecting to the agent is quick or it is not happening: fail in seconds,
# not after the full read timeout. Overridable per site.
DEFAULT_AGENT_CONNECT_TIMEOUT = 5
AGENT_UNREACHABLE_MESSAGE = (
	"The AI agent is not available right now. Please try again later; "
	"if it keeps happening, contact your administrator."
)


def _agent_connect_timeout() -> float:
	raw = frappe.local.conf.get("frappe_ai_agent_connect_timeout")
	try:
		value = float(raw) if raw not in (None, "") else DEFAULT_AGENT_CONNECT_TIMEOUT
	except (TypeError, ValueError):
		return DEFAULT_AGENT_CONNECT_TIMEOUT
	return value if value > 0 else DEFAULT_AGENT_CONNECT_TIMEOUT


# Shown in place of the confirmation card when the site is read-only for the assistant.
READ_ONLY_NOTE = "This change was not made: the Vyogo AI is not allowed to change data on this site."


def _stream_to_agent(
	message: str | None,
	session_id: str,
	user: str,
	sid: str,
	agent_url: str,
	timeout_seconds: int = 30,
	page_context: dict | None = None,
	confirmation: dict | None = None,
) -> None:
	"""Background worker: relay agent SSE chunks to the browser via frappe.realtime.

	Not a whitelisted endpoint — only called via frappe.enqueue.

	A turn the agent pauses on a write ends with a ``tool_confirm`` chunk. When the
	policy (``confirm.decide``) allows that write without a click, the chunk and the
	turn's ``done`` stay here: the token is minted as the user and the next turn
	runs on the same browser stream, so the sidebar sees one continuous answer.
	"""
	import time

	logger = frappe.logger("frappe_ai", allow_site=True)
	# The site's own date, in its time zone: a model has no other way to know what "this month"
	# or "overdue" means, and the agent's server can be a day away.
	context: dict = {"user_id": user, "today": frappe.utils.nowdate()}
	# the user's saved pick, never the browser's say-so. Not checked against the agent's list here: a
	# background job has no session of the user's to ask with, and the agent checks it itself.
	model = frappe.defaults.get_user_default(_MODEL_KEY, user)
	if isinstance(model, str) and model.strip():
		context["model"] = model.strip()
	if page_context:
		# Merge the sanitised page context (route/doctype/docname/currency) into
		# the agent's request context. build_system_prompt() reads these keys.
		context.update(page_context)

	event_name = f"frappe_ai:chunk:{session_id}"
	stream_start = time.monotonic()
	turn: dict = {"confirmation": confirmation} if confirmation else {"message": message}

	logger.info(
		"stream.start session=%s user=%s agent=%s timeout=%ds context_keys=%s msg_len=%d confirmed=%s",
		session_id,
		user,
		agent_url,
		timeout_seconds,
		sorted(page_context or {}),
		len(message or ""),
		bool(confirmation),
	)

	relay = _Relay(
		session_id=session_id,
		user=user,
		sid=sid,
		agent_url=agent_url,
		timeout_seconds=timeout_seconds,
		context=context,
		event_name=event_name,
		logger=logger,
		stream_start=stream_start,
	)

	hops = 0
	max_hops = _max_auto_approved_writes()
	while True:
		pending = relay.run(turn, may_hold=hops < max_hops)
		if pending is None:
			break
		hops += 1
		logger.info(
			"stream.auto_approved session=%s user=%s tool=%s hop=%d",
			session_id,
			user,
			pending["name"],
			hops,
		)
		token = confirm.issue(user, pending["name"], pending["arguments"])
		turn = {
			"confirmation": {
				"tool": pending["name"],
				"arguments": pending["arguments"],
				"token": token,
			}
		}

	if not relay.done_received:
		frappe.publish_realtime(
			event_name,
			{"type": "done", "tools_called": []},
			user=user,
			after_commit=False,
		)

	logger.info(
		"stream.done session=%s user=%s duration_ms=%d chunks=%d done_source=%s hops=%d",
		session_id,
		user,
		int((time.monotonic() - stream_start) * 1000),
		relay.chunk_count,
		relay.done_source,
		hops,
	)


class _Relay:
	"""One browser stream fed by one or more agent turns."""

	def __init__(
		self,
		*,
		session_id: str,
		user: str,
		sid: str,
		agent_url: str,
		timeout_seconds: int,
		context: dict,
		event_name: str,
		logger,
		stream_start: float,
	) -> None:
		self.session_id = session_id
		self.user = user
		self.sid = sid
		self.agent_url = agent_url
		self.timeout_seconds = timeout_seconds
		self.context = context
		self.event_name = event_name
		self.logger = logger
		self.stream_start = stream_start
		self.done_received = False
		self.done_source = "fallback"  # "agent" when the agent emitted the done chunk
		self.chunk_count = 0

	def _publish(self, chunk: dict) -> None:
		frappe.publish_realtime(self.event_name, chunk, user=self.user, after_commit=False)

	def _elapsed_ms(self) -> int:
		import time

		return int((time.monotonic() - self.stream_start) * 1000)

	def run(self, turn: dict, *, may_hold: bool) -> dict | None:
		"""Relay one agent turn. Returns the write to run next when the policy allowed it."""
		payload = {
			**turn,
			# Forward session_id so the agent groups all turns under the same
			# AI Chat Session row, and so its FrappeHistoryClient can pull
			# prior messages back into the LLM context for this session.
			"session_id": self.session_id,
			"context": self.context,
		}
		headers = {
			"Content-Type": "application/json",
			"Accept": "text/event-stream",
		}
		site_name = getattr(getattr(frappe, "local", None), "site", None)
		if site_name:
			headers["X-Frappe-Site-Name"] = site_name
		bench_url = _bench_url()
		if bench_url:
			headers["X-Frappe-Bench-URL"] = bench_url

		held: dict | None = None  # a tool_confirm the policy allowed; its turn's done is held too
		try:
			verify_ssl = _agent_ssl_verify()
			if verify_ssl is False:
				import urllib3

				urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
			post_kwargs: dict = {
				"json": payload,
				"cookies": {"sid": self.sid},
				"headers": headers,
				# (connect, read): an unreachable agent fails in seconds, while a
				# long answer still gets the full read timeout.
				"timeout": (_agent_connect_timeout(), self.timeout_seconds),
				"stream": True,
			}
			if verify_ssl is not None:
				post_kwargs["verify"] = verify_ssl
			with requests.post(f"{self.agent_url}/api/v1/chat", **post_kwargs) as response:
				response.raise_for_status()

				for line in response.iter_lines(decode_unicode=True):
					# Cooperative cancel: the client posts to cancel_stream when
					# the user clicks Stop, hits New conversation mid-flight,
					# or closes the tab (beforeunload). The flag is consumed on
					# read so a follow-up turn on the same session isn't
					# pre-cancelled. See BUG-003 + BUG-008.
					if _is_stream_cancelled(self.session_id):
						self.logger.info(
							"stream.cancelled session=%s after=%dms chunks=%d",
							self.session_id,
							self._elapsed_ms(),
							self.chunk_count,
						)
						self.done_received = True
						self.done_source = "cancel"
						self._publish({"type": "done", "tools_called": [], "cancelled": True})
						return None
					if not line or not line.startswith("data: "):
						continue
					try:
						chunk = json.loads(line[6:])
					except (json.JSONDecodeError, ValueError):
						continue

					self.chunk_count += 1
					kind = chunk.get("type")
					if kind == "tool_confirm":
						decision = self._judge(chunk)
						if decision == "deny":
							self._publish({"type": "content", "text": "\n\n" + READ_ONLY_NOTE})
							continue
						if decision == "allow" and may_hold:
							held = chunk
							continue
						# "ask": the sidebar shows the card and the user decides
					elif kind == "done":
						if held is not None:
							# the turn paused on a write the policy allows: the next
							# turn continues this stream, so its done is not the end
							return held
						self.done_received = True
						self.done_source = "agent"

					self._publish(chunk)

		except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
			# Checked before Timeout: ConnectTimeout is both. Nothing was sent.
			self.logger.error(
				"stream.unreachable session=%s after=%dms err=%s", self.session_id, self._elapsed_ms(), e
			)
			frappe.log_error(title="AI Agent Unreachable", message=f"{self.agent_url}: {e}")
			self._publish({"type": "error", "message": AGENT_UNREACHABLE_MESSAGE})
			self.done_received = True
			self.done_source = "error"
		except requests.exceptions.Timeout:
			self.logger.warning(
				"stream.timeout session=%s after=%dms chunks=%d",
				self.session_id,
				self._elapsed_ms(),
				self.chunk_count,
			)
			self._publish(
				{
					"type": "error",
					"message": _(
						"The AI assistant didn't finish answering within {0} seconds. Please try again."
					).format(self.timeout_seconds),
				}
			)
			self.done_received = True
			self.done_source = "timeout"
		except requests.exceptions.HTTPError as e:
			status = getattr(e.response, "status_code", None)
			self.logger.error("stream.http_error session=%s status=%s", self.session_id, status)
			frappe.log_error(title="AI Agent Stream Failed", message=str(e))
			self._publish(
				{
					"type": "error",
					"message": _("The AI assistant returned an error (HTTP {0}). Please try again.").format(
						status
					),
				}
			)
			self.done_received = True
			self.done_source = "error"
		except requests.exceptions.RequestException as e:
			self.logger.error(
				"stream.failed session=%s after=%dms chunks=%d err=%s",
				self.session_id,
				self._elapsed_ms(),
				self.chunk_count,
				e,
			)
			frappe.log_error(title="AI Agent Stream Failed", message=str(e))
			self._publish({"type": "error", "message": AGENT_UNREACHABLE_MESSAGE})
			self.done_received = True
			self.done_source = "error"

		if held is not None:
			# the stream ended without a done after the pause; run the write anyway
			return held
		return None

	def _judge(self, chunk: dict) -> str:
		"""The policy's answer for a paused write: ``allow``, ``ask`` or ``deny``."""
		name = chunk.get("name")
		arguments = chunk.get("arguments")
		if not isinstance(name, str) or not name or not isinstance(arguments, dict):
			return "ask"  # not a call the policy can judge; the sidebar shows it as is
		risk = chunk.get("risk")
		doctype = arguments.get("doctype") or arguments.get("dt")
		return confirm.decide(name, self.user, self.session_id, risk=risk, doctype=doctype)
