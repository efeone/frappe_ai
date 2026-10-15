# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""Tests for frappe_ai.api.direct_write — writes from MCP clients other than the sidebar's agent."""

import unittest
from unittest.mock import patch

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from frappe_ai.api import direct_write

LOG = "AI External Write Log"
NO_ROLES_USER = "external-writer@example.test"
FORGE_USER = "direct-write-test@vyogoforge.invalid"


def _set_mode(mode: str):
	settings = frappe.get_single("AI Assistant Settings")
	settings.external_mcp_writes = mode
	settings.save(ignore_version=True)


def _request(headers: dict | None = None, cookies: dict | None = None) -> Request:
	builder = EnvironBuilder(method="POST", headers=headers or {})
	for name, value in (cookies or {}).items():
		builder.headers.add("Cookie", f"{name}={value}")
	return Request(builder.get_environ())


class _DirectCase(unittest.TestCase):
	def setUp(self):
		self._original = frappe.db.get_single_value("AI Assistant Settings", "external_mcp_writes")
		_set_mode(direct_write.MODE_READ_ONLY)
		self.user = frappe.session.user

	def tearDown(self):
		_set_mode(self._original or direct_write.MODE_READ_ONLY)

	def decide(self, tool, doctype, name="", caller=direct_write.CALLER_OAUTH, user=None):
		return direct_write.decide(user or self.user, caller, tool, doctype, name)


# ──────────────────────────────────────────────────────────────────────────
# decide — what an outside client may change
# ──────────────────────────────────────────────────────────────────────────


class TestDecide(_DirectCase):
	def test_read_only_is_the_default_and_refuses_every_write(self):
		meta = frappe.get_meta("AI Assistant Settings").get_field("external_mcp_writes")
		self.assertEqual(meta.default, direct_write.MODE_READ_ONLY)
		for tool in direct_write.WRITE_TOOLS:
			ok, reason = self.decide(tool, "ToDo", "TODO-1")
			self.assertFalse(ok, tool)
			self.assertIn("read only", reason)

	def test_creates_and_updates_but_not_deletes(self):
		_set_mode(direct_write.MODE_EDITS)
		self.assertTrue(self.decide("create_document", "ToDo")[0])
		self.assertTrue(self.decide("create_documents", "ToDo", "2 rows")[0])
		self.assertTrue(self.decide("update_document", "ToDo", "TODO-1")[0])
		ok, reason = self.decide("delete_document", "ToDo", "TODO-1")
		self.assertFalse(ok)
		self.assertIn("delete", reason)

	def test_allow_all_lets_deletes_through(self):
		_set_mode(direct_write.MODE_ALL)
		self.assertTrue(self.decide("delete_document", "ToDo", "TODO-1")[0])

	def test_code_privilege_and_assistant_records_stay_in_desk(self):
		_set_mode(direct_write.MODE_ALL)
		for doctype in (
			"User",
			"Role",
			"Server Script",
			"Client Script",
			"Custom DocPerm",
			"AI Assistant Settings",
			"AI Skill",
			"OAuth Client",
			LOG,
		):
			ok, reason = self.decide("update_document", doctype, "x")
			self.assertFalse(ok, doctype)
			self.assertIn("Desk", reason)

	def test_the_sidebar_agents_session_never_writes_without_its_card(self):
		_set_mode(direct_write.MODE_ALL)
		ok, reason = self.decide("create_document", "ToDo", caller=direct_write.CALLER_SESSION)
		self.assertFalse(ok)
		self.assertIn("approval", reason)

	def test_a_tool_that_is_not_a_write_is_refused(self):
		_set_mode(direct_write.MODE_ALL)
		self.assertFalse(self.decide("run_sql", "ToDo")[0])
		self.assertFalse(self.decide("", "ToDo")[0])
		self.assertFalse(self.decide("create_document", "")[0])

	def test_the_users_own_permissions_still_apply(self):
		_set_mode(direct_write.MODE_ALL)
		if not frappe.db.exists("User", NO_ROLES_USER):
			frappe.get_doc(
				{"doctype": "User", "email": NO_ROLES_USER, "first_name": "Examplia", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		ok, reason = self.decide("create_document", "Lead", user=NO_ROLES_USER)
		self.assertFalse(ok)
		self.assertIn("permission", reason)
		self.assertTrue(self.decide("create_document", "Lead")[0])  # Administrator

	def test_every_answer_is_logged(self):
		_set_mode(direct_write.MODE_EDITS)
		before = frappe.db.count(LOG)
		self.decide("update_document", "ToDo", "TODO-1")
		self.decide("delete_document", "ToDo", "TODO-1")
		rows = frappe.get_all(
			LOG,
			fields=["user", "client", "tool", "target_doctype", "target_name", "outcome", "reason"],
			order_by="creation desc",
			limit=2,
		)
		self.assertEqual(frappe.db.count(LOG), before + 2)
		refused, allowed = rows
		self.assertEqual(
			(allowed.user, allowed.tool, allowed.target_doctype, allowed.target_name, allowed.outcome),
			(self.user, "update_document", "ToDo", "TODO-1", "Allowed"),
		)
		self.assertEqual((refused.tool, refused.outcome), ("delete_document", "Refused"))
		self.assertTrue(refused.reason)


# ──────────────────────────────────────────────────────────────────────────
# caller — how the request was signed in
# ──────────────────────────────────────────────────────────────────────────


class TestCaller(unittest.TestCase):
	def setUp(self):
		self._request = getattr(frappe.local, "request", None)

	def tearDown(self):
		frappe.local.request = self._request

	def test_a_browser_session_is_the_session(self):
		frappe.local.request = _request(cookies={"sid": "a-session"})
		self.assertEqual(direct_write.caller(), (direct_write.CALLER_SESSION, ""))

	def test_no_request_is_the_session(self):
		frappe.local.request = None
		self.assertEqual(direct_write.caller()[0], direct_write.CALLER_SESSION)

	def test_a_session_cookie_wins_over_a_header(self):
		frappe.local.request = _request({"Authorization": "Bearer abc"}, {"sid": "a-session"})
		self.assertEqual(direct_write.caller()[0], direct_write.CALLER_SESSION)

	def test_a_guest_cookie_is_no_session(self):
		frappe.local.request = _request({"Authorization": "token k:s"}, {"sid": "Guest"})
		self.assertEqual(direct_write.caller(), (direct_write.CALLER_API_KEY, "API key"))

	def test_an_api_key(self):
		frappe.local.request = _request({"Authorization": "token k:s"})
		self.assertEqual(direct_write.caller(), (direct_write.CALLER_API_KEY, "API key"))

	def test_a_bearer_token_names_its_oauth_client(self):
		token = f"examplia-{frappe.generate_hash(length=16)}"
		client = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Examplia Desktop",
				"scopes": "all",
				"redirect_uris": "http://localhost",
				"default_redirect_uri": "http://localhost",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": client.name,
				"user": "Administrator",
				"access_token": token,
				"scopes": "all",
				"expires_in": 3600,
			}
		).insert(ignore_permissions=True)
		frappe.local.request = _request({"Authorization": f"Bearer {token}"})
		self.assertEqual(direct_write.caller(), (direct_write.CALLER_OAUTH, "Examplia Desktop"))

	def test_an_unknown_bearer_token_is_still_oauth(self):
		frappe.local.request = _request({"Authorization": "Bearer nobody-issued-this"})
		self.assertEqual(direct_write.caller(), (direct_write.CALLER_OAUTH, "OAuth"))


# ──────────────────────────────────────────────────────────────────────────
# authorize — the whitelisted method frappe-mcp-server calls
# ──────────────────────────────────────────────────────────────────────────


class TestAuthorize(_DirectCase):
	def setUp(self):
		super().setUp()
		self._request = getattr(frappe.local, "request", None)

	def tearDown(self):
		frappe.local.request = self._request
		super().tearDown()

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.AuthenticationError):
				direct_write.authorize("create_document", "ToDo")
		finally:
			frappe.set_user(self.user)

	def test_an_api_key_caller_gets_the_answer_and_the_reason(self):
		frappe.local.request = _request({"Authorization": "token k:s"})
		self.assertEqual(direct_write.authorize("create_document", "ToDo")["ok"], False)
		_set_mode(direct_write.MODE_EDITS)
		self.assertEqual(
			direct_write.authorize("create_document", "ToDo"), {"ok": True, "reason": "", "propose": False}
		)
		row = frappe.get_all(LOG, fields=["client"], order_by="creation desc", limit=1)[0]
		self.assertEqual(row.client, "API key")

	def test_the_log_holds_no_credential_and_no_document_data(self):
		_set_mode(direct_write.MODE_EDITS)
		secret = "examplia-secret-access-token"
		frappe.local.request = _request({"Authorization": f"Bearer {secret}"})
		direct_write.authorize("update_document", "ToDo", "TODO-1")
		frappe.local.request = _request({"Authorization": f"token examplia-key:{secret}"})
		direct_write.authorize("delete_document", "ToDo", "TODO-1")
		rows = frappe.get_all(LOG, fields=["*"], order_by="creation desc", limit=2)
		for row in rows:
			for field, value in row.items():
				self.assertNotIn(secret, str(value), field)
				self.assertNotIn("examplia-key", str(value), field)
		# only these columns exist: no place for the change's data
		fields = {f.fieldname for f in frappe.get_meta(LOG).fields if f.fieldtype != "Column Break"}
		self.assertEqual(
			fields, {"user", "client", "tool", "target_doctype", "target_name", "outcome", "reason"}
		)

	def test_a_session_caller_is_refused_however_open_the_site_is(self):
		_set_mode(direct_write.MODE_ALL)
		frappe.local.request = _request(cookies={"sid": "a-session"})
		self.assertFalse(direct_write.authorize("create_document", "ToDo")["ok"])

	def test_vyogo_forge_ai_is_told_to_propose(self):
		# the log links the user, so the agent user must exist, as it does on a site with a Forge connection
		if not frappe.db.exists("User", FORGE_USER):
			frappe.get_doc(
				{"doctype": "User", "email": FORGE_USER, "first_name": "Forge", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "User", FORGE_USER, ignore_permissions=True, force=True)
		self.addCleanup(frappe.db.delete, LOG, {"user": FORGE_USER})
		_set_mode(direct_write.MODE_ALL)
		ok, reason = self.decide("update_document", "ToDo", "TODO-1", user=FORGE_USER)
		self.assertFalse(ok)
		self.assertEqual(reason, direct_write.PROPOSE)
		row = frappe.get_all(LOG, fields=["outcome"], order_by="creation desc", limit=1)[0]
		self.assertEqual(row.outcome, "Proposed")
		frappe.local.request = _request({"Authorization": "token k:s"})
		frappe.set_user("Administrator")
		with patch.object(direct_write.connection, "is_agent_user", return_value=True):
			out = direct_write.authorize("update_document", "ToDo", "TODO-1")
		self.assertEqual(out["propose"], True)
		self.assertFalse(out["ok"])

	def test_it_is_whitelisted_for_post_only(self):
		methods = frappe.allowed_http_methods_for_whitelisted_func.get(direct_write.authorize)
		self.assertEqual(methods, ["POST"])
