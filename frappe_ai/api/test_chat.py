# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""Tests for frappe_ai.api.chat — both the pure-Python sanitiser and the
whitelisted endpoints (with the agent + RQ enqueue mocked out)."""

import json
import os
import unittest
import unittest.mock
from unittest.mock import patch

import frappe

from frappe_ai.api import chat

# ──────────────────────────────────────────────────────────────────────────
# _sanitize_page_context — pure-function tests, no DB / no Frappe context
# ──────────────────────────────────────────────────────────────────────────


class TestSanitizePageContext(unittest.TestCase):
	def test_empty_dict_returns_empty(self):
		self.assertEqual(chat._sanitize_page_context({}), {})

	def test_full_valid_dict_passes_through(self):
		raw = {
			"route": "/app/sales-invoice",
			"doctype": "Sales Invoice",
			"docname": "SINV-001",
			"currency": "INR",
		}
		self.assertEqual(chat._sanitize_page_context(raw), raw)

	def test_json_string_input_is_parsed(self):
		raw = json.dumps({"route": "/app", "doctype": "User"})
		self.assertEqual(
			chat._sanitize_page_context(raw),
			{"route": "/app", "doctype": "User"},
		)

	def test_malformed_json_string_returns_empty(self):
		self.assertEqual(chat._sanitize_page_context("{not json"), {})

	def test_non_dict_non_string_returns_empty(self):
		self.assertEqual(chat._sanitize_page_context(None), {})
		self.assertEqual(chat._sanitize_page_context(42), {})
		self.assertEqual(chat._sanitize_page_context([1, 2, 3]), {})
		self.assertEqual(chat._sanitize_page_context(True), {})

	def test_extra_keys_are_dropped(self):
		raw = {"route": "/app", "evil_key": "value", "another": 123}
		self.assertEqual(chat._sanitize_page_context(raw), {"route": "/app"})

	def test_non_string_values_are_dropped(self):
		raw = {"route": "/app", "doctype": 42, "docname": ["list"], "currency": None}
		self.assertEqual(chat._sanitize_page_context(raw), {"route": "/app"})

	def test_empty_string_values_are_dropped(self):
		# Falsy strings get filtered by `if val:` in the impl — keeps the
		# agent payload tidy when the FE forwards a blank context field.
		self.assertEqual(
			chat._sanitize_page_context({"route": "", "doctype": "User"}),
			{"doctype": "User"},
		)

	def test_strings_over_200_chars_are_truncated(self):
		long = "a" * 500
		out = chat._sanitize_page_context({"route": long})
		self.assertEqual(len(out["route"]), 200)
		self.assertTrue(out["route"].startswith("aaaa"))

	def test_nested_dict_value_dropped(self):
		# Whitelist requires string values; a dict for a key is treated
		# the same as any non-string and dropped.
		self.assertEqual(chat._sanitize_page_context({"route": {"nested": "x"}}), {})

	def test_returned_dict_is_independent_of_input(self):
		# Defensive: mutating the output must not mutate the input.
		raw = {"route": "/app", "doctype": "User"}
		out = chat._sanitize_page_context(raw)
		out["route"] = "MUTATED"
		self.assertEqual(raw["route"], "/app")


# ──────────────────────────────────────────────────────────────────────────
# _agent_url — reads site_config; needs frappe.local.conf
# ──────────────────────────────────────────────────────────────────────────


class TestValidateAgentUrl(unittest.TestCase):
	def test_accepts_https_url(self):
		chat._validate_agent_url("https://agent.example.com")

	def test_accepts_http_url(self):
		chat._validate_agent_url("http://agent.example.com:8484")

	def test_accepts_localhost(self):
		chat._validate_agent_url("http://localhost:8484")
		chat._validate_agent_url("http://127.0.0.1:8484")

	def test_accepts_ip_literal(self):
		chat._validate_agent_url("http://1.1.1.1:8484")
		chat._validate_agent_url("http://10.0.0.5:8484")

	def test_rejects_non_http_scheme(self):
		with self.assertRaises(frappe.ValidationError):
			chat._validate_agent_url("ftp://agent.example.com")
		with self.assertRaises(frappe.ValidationError):
			chat._validate_agent_url("file:///etc/passwd")

	def test_rejects_missing_scheme(self):
		with self.assertRaises(frappe.ValidationError):
			chat._validate_agent_url("agent.example.com:8484")

	def test_rejects_missing_host(self):
		with self.assertRaises(frappe.ValidationError):
			chat._validate_agent_url("http://")


class TestAgentSslVerify(unittest.TestCase):
	def setUp(self):
		self._original = frappe.local.conf.get("frappe_ai_agent_ssl_verify")
		# the host's own environment must not leak into these cases
		env = patch.dict("os.environ")
		env.start()
		self.addCleanup(env.stop)
		os.environ.pop("FRAPPE_AI_AGENT_SSL_VERIFY", None)

	def tearDown(self):
		if self._original is None:
			frappe.local.conf.pop("frappe_ai_agent_ssl_verify", None)
		else:
			frappe.local.conf["frappe_ai_agent_ssl_verify"] = self._original

	def test_false_when_unset(self):
		frappe.local.conf.pop("frappe_ai_agent_ssl_verify", None)
		self.assertIs(chat._agent_ssl_verify(), False)

	def test_false_boolean(self):
		frappe.local.conf["frappe_ai_agent_ssl_verify"] = False
		self.assertIs(chat._agent_ssl_verify(), False)

	def test_false_strings(self):
		for s in ("false", "0", "no"):
			frappe.local.conf["frappe_ai_agent_ssl_verify"] = s
			self.assertIs(chat._agent_ssl_verify(), False)

	def test_true_boolean(self):
		frappe.local.conf["frappe_ai_agent_ssl_verify"] = True
		self.assertIs(chat._agent_ssl_verify(), True)

	def test_custom_ca_path(self):
		frappe.local.conf["frappe_ai_agent_ssl_verify"] = "/etc/ssl/custom.pem"
		self.assertEqual(chat._agent_ssl_verify(), "/etc/ssl/custom.pem")

	def test_env_var_applies_when_site_config_is_unset(self):
		frappe.local.conf.pop("frappe_ai_agent_ssl_verify", None)
		os.environ["FRAPPE_AI_AGENT_SSL_VERIFY"] = "false"
		self.assertIs(chat._agent_ssl_verify(), False)
		os.environ["FRAPPE_AI_AGENT_SSL_VERIFY"] = "true"
		self.assertIs(chat._agent_ssl_verify(), True)
		os.environ["FRAPPE_AI_AGENT_SSL_VERIFY"] = " /etc/ssl/ca.pem "
		self.assertEqual(chat._agent_ssl_verify(), "/etc/ssl/ca.pem")

	def test_blank_env_var_falls_back_to_false(self):
		frappe.local.conf.pop("frappe_ai_agent_ssl_verify", None)
		os.environ["FRAPPE_AI_AGENT_SSL_VERIFY"] = "  "
		self.assertIs(chat._agent_ssl_verify(), False)

	def test_site_config_wins_over_the_env_var(self):
		frappe.local.conf["frappe_ai_agent_ssl_verify"] = True
		os.environ["FRAPPE_AI_AGENT_SSL_VERIFY"] = "false"
		self.assertIs(chat._agent_ssl_verify(), True)

	def test_get_json_silences_the_insecure_warning_when_verification_is_off(self):
		os.environ["FRAPPE_AI_AGENT_SSL_VERIFY"] = "false"
		frappe.local.conf.pop("frappe_ai_agent_ssl_verify", None)
		frappe.local.conf["frappe_ai_agent_url"] = "https://agent.example.test"
		self.addCleanup(frappe.local.conf.pop, "frappe_ai_agent_url", None)
		response = unittest.mock.MagicMock()
		response.json.return_value = {"models": []}
		with (
			patch.object(chat.requests, "get", return_value=response) as get,
			patch("urllib3.disable_warnings") as silence,
		):
			chat._agent_get_json("/api/v1/models")
		self.assertIs(get.call_args.kwargs["verify"], False)
		silence.assert_called_once()


class TestAgentUrl(unittest.TestCase):
	def setUp(self):
		self._original = frappe.local.conf.get("frappe_ai_agent_url")

	def tearDown(self):
		if self._original is None:
			frappe.local.conf.pop("frappe_ai_agent_url", None)
		else:
			frappe.local.conf["frappe_ai_agent_url"] = self._original

	def test_empty_when_unset(self):
		frappe.local.conf.pop("frappe_ai_agent_url", None)
		self.assertEqual(chat._agent_url(), "")

	def test_trailing_slash_stripped(self):
		frappe.local.conf["frappe_ai_agent_url"] = "http://localhost:8484/"
		self.assertEqual(chat._agent_url(), "http://localhost:8484")

	def test_no_trailing_slash_unchanged(self):
		frappe.local.conf["frappe_ai_agent_url"] = "http://localhost:8484"
		self.assertEqual(chat._agent_url(), "http://localhost:8484")

	def test_multiple_trailing_slashes_all_stripped(self):
		frappe.local.conf["frappe_ai_agent_url"] = "http://localhost:8484///"
		self.assertEqual(chat._agent_url(), "http://localhost:8484")


class TestBenchUrl(unittest.TestCase):
	def setUp(self):
		self._original = frappe.local.conf.get("frappe_ai_bench_url")

	def tearDown(self):
		if self._original is None:
			frappe.local.conf.pop("frappe_ai_bench_url", None)
		else:
			frappe.local.conf["frappe_ai_bench_url"] = self._original

	def test_configured_bench_url(self):
		frappe.local.conf["frappe_ai_bench_url"] = "http://erpnext:8000/"
		self.assertEqual(chat._bench_url(), "http://erpnext:8000")

	def test_fallback_to_get_url(self):
		frappe.local.conf.pop("frappe_ai_bench_url", None)
		with patch("frappe.utils.get_url", return_value="http://site.local:8000/"):
			self.assertEqual(chat._bench_url(), "http://site.local:8000")


# ──────────────────────────────────────────────────────────────────────────
# start_stream — whitelisted endpoint. Mocks frappe.enqueue.
# ──────────────────────────────────────────────────────────────────────────


class TestStartStream(unittest.TestCase):
	def setUp(self):
		# Establish a known-good baseline for the singleton + site_config.
		self._original_url = frappe.local.conf.get("frappe_ai_agent_url")
		frappe.local.conf["frappe_ai_agent_url"] = "http://localhost:8484"

		self.settings = frappe.get_single("AI Assistant Settings")
		self._original_enabled = self.settings.enabled
		self.settings.enabled = 1
		self.settings.save(ignore_version=True)

	def tearDown(self):
		settings = frappe.get_single("AI Assistant Settings")
		settings.enabled = self._original_enabled
		settings.save(ignore_version=True)
		if self._original_url is None:
			frappe.local.conf.pop("frappe_ai_agent_url", None)
		else:
			frappe.local.conf["frappe_ai_agent_url"] = self._original_url

	def test_empty_message_throws(self):
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(message="")

	def test_whitespace_only_message_throws(self):
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(message="   \n\t  ")

	def test_message_over_10k_throws(self):
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(message="x" * 10_001)

	def test_disabled_assistant_throws(self):
		self.settings.enabled = 0
		self.settings.save(ignore_version=True)
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(message="hello")

	def test_missing_agent_url_throws(self):
		frappe.local.conf.pop("frappe_ai_agent_url", None)
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(message="hello")

	@patch("frappe.enqueue")
	def test_happy_path_returns_session_id_and_enqueues_worker(self, mock_enqueue):
		result = chat.start_stream(message="hello", page_context={"route": "/app"})
		self.assertIn("session_id", result)
		self.assertEqual(len(result["session_id"]), 36)  # UUID4 string length
		mock_enqueue.assert_called_once()
		# Forwarded message + sanitised page_context land in the enqueue kwargs.
		_, kwargs = mock_enqueue.call_args
		self.assertEqual(kwargs["message"], "hello")
		self.assertEqual(kwargs["page_context"], {"route": "/app"})
		self.assertEqual(kwargs["agent_url"], "http://localhost:8484")
		self.assertEqual(kwargs["queue"], "long")
		self.assertTrue(kwargs["enqueue_after_commit"])

	@patch("frappe.enqueue")
	def test_supplied_session_id_is_reused(self, mock_enqueue):
		result = chat.start_stream(message="hello", session_id="caller-supplied-id")
		self.assertEqual(result["session_id"], "caller-supplied-id")

	@patch("frappe.enqueue")
	def test_malformed_page_context_is_filtered_not_rejected(self, mock_enqueue):
		# Endpoint accepts anything; _sanitize_page_context filters internally.
		chat.start_stream(message="hello", page_context={"junk": "x", "route": "/app"})
		_, kwargs = mock_enqueue.call_args
		self.assertEqual(kwargs["page_context"], {"route": "/app"})


# ──────────────────────────────────────────────────────────────────────────
# cancel_stream + _is_stream_cancelled — server-side stream cancellation
# ──────────────────────────────────────────────────────────────────────────


class TestCancelStream(unittest.TestCase):
	"""BUG-003 + BUG-008: client must be able to signal cancel to the worker."""

	def setUp(self):
		self._sid = "test-cancel-session-id"
		# Ensure clean state.
		frappe.cache().delete_value(f"frappe_ai:cancel:{self._sid}")

	def tearDown(self):
		frappe.cache().delete_value(f"frappe_ai:cancel:{self._sid}")

	def test_cancel_sets_redis_flag(self):
		chat.cancel_stream(session_id=self._sid)
		self.assertTrue(chat._is_stream_cancelled(self._sid))

	def test_flag_false_without_cancel(self):
		self.assertFalse(chat._is_stream_cancelled(self._sid))

	def test_empty_session_id_is_noop(self):
		# Must not raise, must not poison any cache key.
		chat.cancel_stream(session_id="")
		# Sanity: cancelling "" shouldn't affect a real session.
		self.assertFalse(chat._is_stream_cancelled(self._sid))

	def test_consume_clears_flag(self):
		# Worker calls _is_stream_cancelled, which must clear so a follow-up
		# turn on the same session isn't pre-cancelled.
		chat.cancel_stream(session_id=self._sid)
		self.assertTrue(chat._is_stream_cancelled(self._sid))
		# Second call returns False (flag consumed).
		self.assertFalse(chat._is_stream_cancelled(self._sid))


# ──────────────────────────────────────────────────────────────────────────
# get_recent_messages — hydrates the sidebar
# ──────────────────────────────────────────────────────────────────────────


# The history tests run as a user of their own. Cleaning up as the real session user deleted every
# chat on the site the tests ran on, a developer's real conversations included.
_HISTORY_TEST_USER = "ai-history-test@example.com"


class TestGetRecentMessages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls._user = frappe.session.user
		if not frappe.db.exists("User", _HISTORY_TEST_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": _HISTORY_TEST_USER,
					"first_name": "History Test",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user(_HISTORY_TEST_USER)

	def tearDown(self):
		# only this test user's rows: never another user's conversations
		for s in frappe.get_all(
			"AI Chat Session", filters={"user": _HISTORY_TEST_USER}, pluck="name", ignore_permissions=True
		):
			for m in frappe.get_all(
				"AI Chat Message", filters={"session": s}, pluck="name", ignore_permissions=True
			):
				frappe.delete_doc("AI Chat Message", m, ignore_permissions=True, force=True)
			frappe.delete_doc("AI Chat Session", s, ignore_permissions=True, force=True)
		frappe.set_user(self._user)

	def test_history_lists_own_chats_and_opens_only_them(self):
		mine = frappe.get_doc(
			{
				"doctype": "AI Chat Session",
				"name": "test-session-mine",
				"user": _HISTORY_TEST_USER,
				"title": "Mine",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "AI Chat Message", "session": mine.name, "role": "user", "content": "hello"}
		).insert(ignore_permissions=True)
		theirs = frappe.get_doc(
			{
				"doctype": "AI Chat Session",
				"name": "test-session-theirs",
				"user": "Administrator",
				"title": "Theirs",
			}
		).insert(ignore_permissions=True)
		try:
			listed = [s["id"] for s in chat.list_sessions()["sessions"]]
			self.assertIn(mine.name, listed)
			self.assertNotIn(theirs.name, listed)
			opened = chat.get_recent_messages(session_id=mine.name)
			self.assertEqual(opened["session_id"], mine.name)
			self.assertEqual([m["content"] for m in opened["messages"]], ["hello"])
			with self.assertRaises(frappe.DoesNotExistError):
				chat.get_recent_messages(session_id=theirs.name)
		finally:
			frappe.delete_doc("AI Chat Session", theirs.name, ignore_permissions=True, force=True)

	def test_empty_when_no_session(self):
		out = chat.get_recent_messages()
		self.assertEqual(out, {"session_id": None, "messages": []})

	def test_returns_messages_for_most_recent_session(self):
		session = frappe.get_doc(
			{
				"doctype": "AI Chat Session",
				"name": "test-session-1",
				"user": frappe.session.user,
			}
		).insert(ignore_permissions=True)
		for i, role in enumerate(["user", "assistant"]):
			frappe.get_doc(
				{
					"doctype": "AI Chat Message",
					"session": session.name,
					"role": role,
					"content": f"msg-{i}",
				}
			).insert(ignore_permissions=True)
		out = chat.get_recent_messages()
		self.assertEqual(out["session_id"], session.name)
		self.assertEqual(len(out["messages"]), 2)
		self.assertEqual(out["messages"][0]["role"], "user")
		self.assertEqual(out["messages"][0]["content"], "msg-0")

	def test_saved_cards_come_back_with_their_message(self):
		session = frappe.get_doc(
			{"doctype": "AI Chat Session", "name": "test-session-blocks", "user": frappe.session.user}
		).insert(ignore_permissions=True)
		kpi = {"type": "kpi", "metrics": [{"label": "Net Profit", "value": 1, "format": "currency"}]}
		for role, content, extra in (
			("user", "profit?", None),
			("assistant", "", json.dumps({"blocks": [kpi], "sources": []})),
			("assistant", "plain", "{not json"),
		):
			frappe.get_doc(
				{
					"doctype": "AI Chat Message",
					"session": session.name,
					"role": role,
					"content": content,
					"tool_result_json": extra,
				}
			).insert(ignore_permissions=True)
		out = chat.get_recent_messages()
		by_content = {m["content"]: m for m in out["messages"]}
		self.assertEqual(by_content[""]["blocks"], [kpi])
		self.assertNotIn("blocks", by_content["plain"])  # unreadable json: no cards, no error
		self.assertNotIn("blocks", by_content["profit?"])

	def test_a_long_conversation_returns_its_newest_messages(self):
		session = frappe.get_doc(
			{"doctype": "AI Chat Session", "name": "test-session-newest", "user": frappe.session.user}
		).insert(ignore_permissions=True)
		for i in range(5):
			doc = frappe.get_doc(
				{"doctype": "AI Chat Message", "session": session.name, "role": "user", "content": f"m{i}"}
			).insert(ignore_permissions=True)
			# distinct creation times so the order is the order of insertion
			frappe.db.set_value("AI Chat Message", doc.name, "creation", f"2026-01-01 00:00:0{i}")
		out = chat.get_recent_messages(limit=2)
		self.assertEqual([m["content"] for m in out["messages"]], ["m3", "m4"])

	def test_timestamps_are_iso8601_with_explicit_utc_suffix(self):
		# BUG-002 regression: pre-fix the API returned Frappe's naive datetime
		# str (e.g. "2026-05-16 17:12:35.041386") which JS parses as LOCAL,
		# producing a different display time after reload vs. fresh send.
		# Fix: emit ISO 8601 with explicit "Z" so `new Date(ts)` is unambiguous.
		session = frappe.get_doc(
			{
				"doctype": "AI Chat Session",
				"name": "test-session-ts",
				"user": frappe.session.user,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "AI Chat Message",
				"session": session.name,
				"role": "user",
				"content": "ts probe",
			}
		).insert(ignore_permissions=True)
		out = chat.get_recent_messages()
		ts = out["messages"][0]["timestamp"]
		self.assertIsNotNone(ts)
		# ISO 8601 with "T" separator and trailing "Z" (UTC):
		# "YYYY-MM-DDTHH:MM:SS[.ffffff]Z"
		self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

	def test_limit_clamped_to_safe_range(self):
		session = frappe.get_doc(
			{
				"doctype": "AI Chat Session",
				"name": "test-session-limit",
				"user": frappe.session.user,
			}
		).insert(ignore_permissions=True)
		for i in range(5):
			frappe.get_doc(
				{
					"doctype": "AI Chat Message",
					"session": session.name,
					"role": "user",
					"content": f"msg-{i}",
				}
			).insert(ignore_permissions=True)
		# limit=0 → clamped to 1
		out = chat.get_recent_messages(limit=0)
		self.assertEqual(len(out["messages"]), 1)
		# limit=10000 → clamped to 200
		out = chat.get_recent_messages(limit=10000)
		self.assertLessEqual(len(out["messages"]), 200)

	def test_string_limit_coerced_by_frappe_typing(self):
		# Frappe v16 wraps whitelisted endpoints in pydantic-based type
		# validation. A numeric string from the HTTP query layer is
		# coerced to int before the function runs, so a caller-supplied
		# "100" works identically to an integer 100.
		session = frappe.get_doc(
			{
				"doctype": "AI Chat Session",
				"name": "test-session-str-limit",
				"user": frappe.session.user,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "AI Chat Message",
				"session": session.name,
				"role": "user",
				"content": "ok",
			}
		).insert(ignore_permissions=True)
		out = chat.get_recent_messages(limit="100")  # type: ignore[arg-type]
		self.assertEqual(len(out["messages"]), 1)

	def test_unparseable_limit_raises_frappe_type_error(self):
		# Frappe's type validator rejects non-numeric strings upstream of
		# the endpoint body. This documents that contract — callers who
		# want defensive fallback must coerce themselves.
		with self.assertRaises(frappe.exceptions.FrappeTypeError):
			chat.get_recent_messages(limit="not-a-number")  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# start_stream with a confirmation — the turn an Allow click starts
# ──────────────────────────────────────────────────────────────────────────


class TestStartStreamConfirmation(unittest.TestCase):
	def setUp(self):
		from frappe_ai.api import confirm

		self.confirm = confirm
		self._original_url = frappe.local.conf.get("frappe_ai_agent_url")
		frappe.local.conf["frappe_ai_agent_url"] = "http://localhost:8484"
		settings = frappe.get_single("AI Assistant Settings")
		self._original = {"enabled": settings.enabled, "write_approval": settings.write_approval}
		settings.enabled = 1
		settings.write_approval = confirm.MODE_ASK
		settings.save(ignore_version=True)

	def tearDown(self):
		settings = frappe.get_single("AI Assistant Settings")
		for k, v in self._original.items():
			setattr(settings, k, v)
		settings.save(ignore_version=True)
		if self._original_url is None:
			frappe.local.conf.pop("frappe_ai_agent_url", None)
		else:
			frappe.local.conf["frappe_ai_agent_url"] = self._original_url

	@patch("frappe.enqueue")
	def test_confirmation_mints_a_token_and_enqueues_a_confirmed_turn(self, mock_enqueue):
		result = chat.start_stream(
			session_id="conv-1",
			confirmation={"tool": "create_document", "arguments": {"doctype": "Customer", "data": {"x": 1}}},
		)
		self.assertEqual(result["session_id"], "conv-1")
		_, kwargs = mock_enqueue.call_args
		self.assertIsNone(kwargs["message"])
		sent = kwargs["confirmation"]
		self.assertEqual(sent["tool"], "create_document")
		self.assertEqual(sent["arguments"], {"doctype": "Customer", "data": {"x": 1}})
		# the token is real: the MCP server can spend it for exactly this call
		self.assertEqual(self.confirm.redeem(sent["token"], "create_document", "Customer", ""), {"ok": True})

	@patch("frappe.enqueue")
	def test_confirmation_arrives_as_json_string(self, mock_enqueue):
		chat.start_stream(
			session_id="conv-1", confirmation=json.dumps({"tool": "delete_document", "arguments": {}})
		)
		_, kwargs = mock_enqueue.call_args
		self.assertEqual(kwargs["confirmation"]["tool"], "delete_document")

	def test_confirmation_needs_a_session(self):
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(confirmation={"tool": "create_document", "arguments": {}})

	def test_message_and_confirmation_together_throw(self):
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(message="hi", session_id="conv-1", confirmation={"tool": "create_document"})

	def test_malformed_confirmation_throws(self):
		with self.assertRaises(frappe.ValidationError):
			chat.start_stream(session_id="conv-1", confirmation="{nope")

	def test_read_only_site_refuses_to_mint(self):
		settings = frappe.get_single("AI Assistant Settings")
		settings.write_approval = self.confirm.MODE_READ_ONLY
		# outside the mock: saving re-syncs an active Forge connection's user, which enqueues its own job
		settings.save(ignore_version=True)
		with patch("frappe.enqueue") as mock_enqueue:
			with self.assertRaises(frappe.ValidationError):
				chat.start_stream(
					session_id="conv-1", confirmation={"tool": "create_document", "arguments": {}}
				)
		mock_enqueue.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────
# _stream_to_agent — the relay, with the agent faked at the HTTP boundary
# ──────────────────────────────────────────────────────────────────────────


class _FakeResponse:
	"""What requests.post(stream=True) hands the relay: SSE lines, one turn per response."""

	def __init__(self, chunks):
		self._chunks = chunks

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False

	def raise_for_status(self):
		pass

	def iter_lines(self, decode_unicode=True):
		for chunk in self._chunks:
			yield f"data: {json.dumps(chunk)}"
			yield ""


def _paused_turn(tool="create_document", arguments=None):
	return [
		{"type": "session", "id": "conv-1"},
		{"type": "content", "text": "Create a new Customer record."},
		{"type": "tool_confirm", "id": "c1", "name": tool, "arguments": arguments or {"doctype": "Customer"}},
		{"type": "done", "tools_called": []},
	]


def _answered_turn():
	return [
		{"type": "session", "id": "conv-1"},
		{"type": "tool_call", "name": "create_document", "arguments": {"doctype": "Customer"}},
		{"type": "content", "text": "Customer created."},
		{"type": "done", "tools_called": ["create_document"]},
	]


class TestStreamToAgentRelay(unittest.TestCase):
	def setUp(self):
		from frappe_ai.api import confirm

		self.confirm = confirm
		settings = frappe.get_single("AI Assistant Settings")
		self._original = {
			"write_approval": settings.write_approval,
			"allow_session_auto_approve": settings.allow_session_auto_approve,
		}
		self._set(write_approval=confirm.MODE_ASK, allow_session_auto_approve=0)

	def tearDown(self):
		self._set(**self._original)

	def _set(self, **values):
		settings = frappe.get_single("AI Assistant Settings")
		for k, v in values.items():
			setattr(settings, k, v)
		settings.save(ignore_version=True)

	def _run(self, turns, **kwargs):
		"""Relay `turns` (one list of chunks per agent response); return (published, posted bodies)."""
		responses = [_FakeResponse(t) for t in turns]
		posted = []

		def fake_post(url, **kw):
			posted.append(kw["json"])
			return responses.pop(0)

		published = []
		with (
			patch("frappe_ai.api.chat.requests.post", side_effect=fake_post),
			patch("frappe.publish_realtime", side_effect=lambda ev, chunk, **k: published.append(chunk)),
		):
			chat._stream_to_agent(
				message=kwargs.get("message", "make a customer"),
				session_id="conv-1",
				user=frappe.session.user,
				sid="sid-1",
				agent_url="http://agent:8484",
				timeout_seconds=30,
				confirmation=kwargs.get("confirmation"),
			)
		return published, posted

	def test_the_sites_date_goes_with_every_turn(self):
		_, posted = self._run([_answered_turn()])
		self.assertEqual(posted[0]["context"]["today"], frappe.utils.nowdate())

	def test_ask_mode_forwards_the_card_and_ends_the_turn(self):
		published, posted = self._run([_paused_turn()])
		self.assertEqual(len(posted), 1)
		self.assertEqual(posted[0]["message"], "make a customer")
		self.assertNotIn("confirmation", posted[0])
		types = [c["type"] for c in published]
		self.assertEqual(types, ["session", "content", "tool_confirm", "done"])

	def test_auto_mode_runs_the_write_on_the_same_stream(self):
		self._set(write_approval=self.confirm.MODE_AUTO_EDITS)
		published, posted = self._run([_paused_turn(), _answered_turn()])
		# two agent turns, the second carrying a token for the paused call
		self.assertEqual(len(posted), 2)
		second = posted[1]["confirmation"]
		self.assertEqual(second["tool"], "create_document")
		self.assertEqual(second["arguments"], {"doctype": "Customer"})
		self.assertEqual(
			self.confirm.redeem(second["token"], "create_document", "Customer", ""), {"ok": True}
		)
		self.assertNotIn("message", posted[1])
		# the browser never saw the card nor the paused turn's done: one continuous answer
		types = [c["type"] for c in published]
		self.assertNotIn("tool_confirm", types)
		self.assertEqual(types.count("done"), 1)
		self.assertEqual(types[-1], "done")
		self.assertIn("tool_call", types)

	def test_auto_mode_still_asks_for_a_delete(self):
		self._set(write_approval=self.confirm.MODE_AUTO_EDITS)
		published, posted = self._run(
			[_paused_turn(tool="delete_document", arguments={"doctype": "Customer", "name": "C-1"})]
		)
		self.assertEqual(len(posted), 1)
		self.assertIn("tool_confirm", [c["type"] for c in published])

	def test_read_only_replaces_the_card_with_a_note(self):
		self._set(write_approval=self.confirm.MODE_READ_ONLY)
		published, posted = self._run([_paused_turn()])
		self.assertEqual(len(posted), 1)
		types = [c["type"] for c in published]
		self.assertNotIn("tool_confirm", types)
		self.assertTrue(
			any(chat.READ_ONLY_NOTE in c.get("text", "") for c in published if c["type"] == "content")
		)
		self.assertEqual(types[-1], "done")

	def test_auto_mode_stops_chaining_at_the_cap(self):
		self._set(write_approval=self.confirm.MODE_AUTO_ALL)
		original = frappe.local.conf.get("frappe_ai_max_auto_approved_writes")
		frappe.local.conf["frappe_ai_max_auto_approved_writes"] = 3
		try:
			turns = [_paused_turn() for _ in range(4)]
			published, posted = self._run(turns)
		finally:
			if original is None:
				frappe.local.conf.pop("frappe_ai_max_auto_approved_writes", None)
			else:
				frappe.local.conf["frappe_ai_max_auto_approved_writes"] = original
		self.assertEqual(len(posted), 4)
		# the write past the cap goes to the user as a card
		types = [c["type"] for c in published]
		self.assertEqual(types.count("tool_confirm"), 1)
		self.assertEqual(types.count("done"), 1)

	def test_confirmed_turn_posts_the_confirmation_not_a_message(self):
		published, posted = self._run(
			[_answered_turn()],
			message=None,
			confirmation={"tool": "create_document", "arguments": {"doctype": "Customer"}, "token": "t-1"},
		)
		self.assertEqual(len(posted), 1)
		self.assertNotIn("message", posted[0])
		self.assertEqual(posted[0]["confirmation"]["token"], "t-1")
		self.assertEqual([c["type"] for c in published][-1], "done")


# ──────────────────────────────────────────────────────────────────────────
# Models: the agent's list, the user's pick
# ──────────────────────────────────────────────────────────────────────────

_AGENT_MODELS = {
	"models": [
		{"id": "provider/model-a", "label": "Model A"},
		{"id": "provider/model-b", "label": "Model B"},
	],
	"default": "provider/model-a",
}


class TestModels(unittest.TestCase):
	def setUp(self):
		frappe.cache().delete_value(chat._MODELS_CACHE_KEY)

	def tearDown(self):
		frappe.defaults.clear_user_default(chat._MODEL_KEY, frappe.session.user)
		frappe.cache().delete_value(chat._MODELS_CACHE_KEY)

	@patch("frappe_ai.api.chat._agent_get_json", return_value=_AGENT_MODELS)
	def test_the_menu_lists_the_agents_models_and_the_default(self, _agent):
		out = chat.get_models()
		self.assertEqual([m["id"] for m in out["models"]], ["provider/model-a", "provider/model-b"])
		self.assertEqual(out["current"], "provider/model-a")

	@patch("frappe_ai.api.chat._agent_get_json", return_value=_AGENT_MODELS)
	def test_a_user_picks_one_of_them(self, _agent):
		self.assertEqual(chat.set_model("provider/model-b")["current"], "provider/model-b")
		with self.assertRaises(frappe.ValidationError):
			chat.set_model("someone/else-entirely")

	@patch("frappe_ai.api.chat._agent_get_json", side_effect=chat.requests.exceptions.ConnectionError("down"))
	def test_an_agent_that_cannot_list_leaves_the_menu_empty(self, _agent):
		self.assertEqual(chat.get_models()["models"], [])

	def test_the_relay_sends_the_users_pick(self):
		frappe.defaults.set_user_default(chat._MODEL_KEY, "provider/model-b", frappe.session.user)
		posted = []

		def fake_post(url, **kw):
			posted.append(kw["json"])
			return _FakeResponse(_answered_turn())

		with (
			patch("frappe_ai.api.chat.requests.post", side_effect=fake_post),
			patch("frappe.publish_realtime"),
		):
			chat._stream_to_agent(
				message="hi",
				session_id="conv-m",
				user=frappe.session.user,
				sid="sid-1",
				agent_url="http://agent:8484",
			)
		self.assertEqual(posted[0]["context"]["model"], "provider/model-b")
