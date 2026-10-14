# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""Tests for frappe_ai.api.confirm — the policy over writes and the one-time tokens."""

import unittest

import frappe

from frappe_ai.api import confirm


def _set_settings(**values):
	settings = frappe.get_single("AI Assistant Settings")
	for key, value in values.items():
		setattr(settings, key, value)
	settings.save(ignore_version=True)


class _SettingsCase(unittest.TestCase):
	def setUp(self):
		settings = frappe.get_single("AI Assistant Settings")
		self._original = {
			"write_approval": settings.write_approval,
			"allow_session_auto_approve": settings.allow_session_auto_approve,
		}
		_set_settings(write_approval=confirm.MODE_ASK, allow_session_auto_approve=0)

	def tearDown(self):
		_set_settings(**self._original)


# ──────────────────────────────────────────────────────────────────────────
# parse_confirmation — what the sidebar may send
# ──────────────────────────────────────────────────────────────────────────


class TestParseConfirmation(unittest.TestCase):
	def test_nothing_sent_is_none(self):
		self.assertIsNone(confirm.parse_confirmation(None))
		self.assertIsNone(confirm.parse_confirmation(""))

	def test_dict_passes_through(self):
		out = confirm.parse_confirmation({"tool": "create_document", "arguments": {"doctype": "Customer"}})
		self.assertEqual(out, {"tool": "create_document", "arguments": {"doctype": "Customer"}})

	def test_json_string_is_parsed(self):
		# frappe.call hands nested args over as JSON strings.
		out = confirm.parse_confirmation('{"tool": "update_document", "arguments": {"name": "X"}}')
		self.assertEqual(out, {"tool": "update_document", "arguments": {"name": "X"}})

	def test_missing_arguments_default_to_empty(self):
		out = confirm.parse_confirmation({"tool": "delete_document"})
		self.assertEqual(out, {"tool": "delete_document", "arguments": {}})

	def test_malformed_json_throws(self):
		with self.assertRaises(frappe.ValidationError):
			confirm.parse_confirmation("{not json")

	def test_non_dict_throws(self):
		with self.assertRaises(frappe.ValidationError):
			confirm.parse_confirmation(["create_document"])

	def test_missing_tool_throws(self):
		with self.assertRaises(frappe.ValidationError):
			confirm.parse_confirmation({"arguments": {}})

	def test_non_dict_arguments_throw(self):
		with self.assertRaises(frappe.ValidationError):
			confirm.parse_confirmation({"tool": "create_document", "arguments": "doctype=Customer"})

	def test_oversized_arguments_throw(self):
		with self.assertRaises(frappe.ValidationError):
			confirm.parse_confirmation({"tool": "create_document", "arguments": {"data": "x" * 70_000}})


# ──────────────────────────────────────────────────────────────────────────
# decide — the policy
# ──────────────────────────────────────────────────────────────────────────


class TestDecide(_SettingsCase):
	def test_default_asks(self):
		self.assertEqual(confirm.decide("create_document", "a@x.io", "s1"), "ask")
		self.assertEqual(confirm.decide("delete_document", "a@x.io", "s1"), "ask")

	def test_read_only_denies_everything(self):
		_set_settings(write_approval=confirm.MODE_READ_ONLY)
		self.assertEqual(confirm.decide("create_document", "a@x.io", "s1"), "deny")
		self.assertEqual(confirm.decide("delete_document", "a@x.io", "s1"), "deny")

	def test_auto_edits_allows_creates_and_updates_but_asks_for_deletes(self):
		_set_settings(write_approval=confirm.MODE_AUTO_EDITS)
		self.assertEqual(confirm.decide("create_document", "a@x.io", "s1"), "allow")
		self.assertEqual(confirm.decide("update_document", "a@x.io", "s1"), "allow")
		self.assertEqual(confirm.decide("delete_document", "a@x.io", "s1"), "ask")

	def test_auto_all_allows_deletes_too(self):
		_set_settings(write_approval=confirm.MODE_AUTO_ALL)
		self.assertEqual(confirm.decide("delete_document", "a@x.io", "s1"), "allow")

	def test_session_flag_is_ignored_unless_the_site_allows_it(self):
		user = frappe.session.user
		frappe.cache().set_value(confirm._session_key(user, "s-flag"), 1, expires_in_sec=60)
		try:
			self.assertEqual(confirm.decide("create_document", user, "s-flag"), "ask")
			_set_settings(allow_session_auto_approve=1)
			self.assertEqual(confirm.decide("create_document", user, "s-flag"), "allow")
			# never a delete on a conversation-wide yes
			self.assertEqual(confirm.decide("delete_document", user, "s-flag"), "ask")
			# and only that conversation
			self.assertEqual(confirm.decide("create_document", user, "s-other"), "ask")
		finally:
			frappe.cache().delete_value(confirm._session_key(user, "s-flag"))

	def test_always_ask_is_never_auto_approved(self):
		user = frappe.session.user
		_set_settings(write_approval=confirm.MODE_AUTO_ALL, allow_session_auto_approve=1)
		frappe.cache().set_value(confirm._session_key(user, "s-flag"), 1, expires_in_sec=60)
		try:
			# Flagged with risk="always-ask"
			self.assertEqual(
				confirm.decide("create_document", user, "s-flag", risk="always-ask"),
				"ask",
			)
			# Sensitive doctype (Client Script, User)
			self.assertEqual(
				confirm.decide("create_document", user, "s-flag", doctype="Client Script"),
				"ask",
			)
			self.assertEqual(
				confirm.decide("update_document", user, "s-flag", doctype="User"),
				"ask",
			)
		finally:
			frappe.cache().delete_value(confirm._session_key(user, "s-flag"))


class TestSetSessionAutoApprove(_SettingsCase):
	def tearDown(self):
		frappe.cache().delete_value(confirm._session_key(frappe.session.user, "s-set"))
		super().tearDown()

	def test_refused_when_the_site_does_not_allow_it(self):
		with self.assertRaises(frappe.ValidationError):
			confirm.set_session_auto_approve("s-set", True)

	def test_sets_and_clears_the_flag(self):
		_set_settings(allow_session_auto_approve=1)
		out = confirm.set_session_auto_approve("s-set", True)
		self.assertEqual(out, {"ok": True, "enabled": True})
		self.assertTrue(confirm.session_auto_approved(frappe.session.user, "s-set"))
		out = confirm.set_session_auto_approve("s-set", "false")
		self.assertEqual(out["enabled"], False)
		self.assertFalse(confirm.session_auto_approved(frappe.session.user, "s-set"))

	def test_empty_session_throws(self):
		_set_settings(allow_session_auto_approve=1)
		with self.assertRaises(frappe.ValidationError):
			confirm.set_session_auto_approve("  ", True)


# ──────────────────────────────────────────────────────────────────────────
# issue + redeem — the token round trip frappe-mcp-server relies on
# ──────────────────────────────────────────────────────────────────────────


class TestTokens(unittest.TestCase):
	def setUp(self):
		self.user = frappe.session.user

	def test_redeem_spends_a_token_once(self):
		token = confirm.issue(self.user, "create_document", {"doctype": "Customer", "data": {"x": 1}})
		self.assertGreaterEqual(len(token), 32)
		self.assertEqual(confirm.redeem(token, "create_document", "Customer", ""), {"ok": True})
		# a second redeem of the same token finds nothing
		self.assertEqual(confirm.redeem(token, "create_document", "Customer", ""), {"ok": False})

	def test_redeem_needs_the_same_call(self):
		token = confirm.issue(self.user, "update_document", {"doctype": "Customer", "name": "CUST-1"})
		self.assertEqual(confirm.redeem(token, "update_document", "Customer", "CUST-2"), {"ok": False})
		# refused and spent: the right call cannot follow a wrong one
		self.assertEqual(confirm.redeem(token, "update_document", "Customer", "CUST-1"), {"ok": False})

	def test_redeem_needs_the_same_tool(self):
		token = confirm.issue(self.user, "update_document", {"doctype": "Customer", "name": "CUST-1"})
		self.assertEqual(confirm.redeem(token, "delete_document", "Customer", "CUST-1"), {"ok": False})

	def test_redeem_needs_the_same_user(self):
		token = confirm.issue("someone.else@example.com", "create_document", {"doctype": "Customer"})
		self.assertEqual(confirm.redeem(token, "create_document", "Customer", ""), {"ok": False})

	def test_unknown_token_is_refused(self):
		self.assertEqual(confirm.redeem("no-such-token", "create_document", "Customer", ""), {"ok": False})
		self.assertEqual(confirm.redeem("", "create_document", "Customer", ""), {"ok": False})

	def test_issue_refuses_guest(self):
		with self.assertRaises(frappe.AuthenticationError):
			confirm.issue("Guest", "create_document", {})

	def test_redeem_refuses_guest(self):
		original = frappe.session.user
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.AuthenticationError):
				confirm.redeem("t", "create_document", "Customer", "")
		finally:
			frappe.set_user(original)

	def test_token_record_carries_only_what_redeem_compares(self):
		# the arguments themselves (the document's data) never go into the cache
		token = confirm.issue(self.user, "create_document", {"doctype": "Customer", "data": {"secret": 1}})
		record = frappe.cache().get_value(confirm._TOKEN_KEY_PREFIX + token)
		self.assertEqual(set(record), {"user", "tool", "doctype", "name"})
		confirm.redeem(token, "create_document", "Customer", "")


# ──────────────────────────────────────────────────────────────────────────
# Per-user approval modes and batch tokens
# ──────────────────────────────────────────────────────────────────────────


class TestUserApprovalModes(_SettingsCase):
	def tearDown(self):
		frappe.defaults.clear_user_default(confirm._USER_MODE_KEY, frappe.session.user)
		super().tearDown()

	def test_users_choose_within_what_the_site_allows(self):
		self.assertEqual(confirm.allowed_user_modes(), [confirm.MODE_ASK, confirm.MODE_READ_ONLY])
		_set_settings(allow_session_auto_approve=1)
		self.assertEqual(
			confirm.allowed_user_modes(), [confirm.MODE_ASK, confirm.MODE_AUTO_EDITS, confirm.MODE_READ_ONLY]
		)
		_set_settings(write_approval=confirm.MODE_AUTO_ALL)
		self.assertIn(confirm.MODE_AUTO_ALL, confirm.allowed_user_modes())
		_set_settings(write_approval=confirm.MODE_READ_ONLY)
		self.assertEqual(confirm.allowed_user_modes(), [confirm.MODE_READ_ONLY])

	def test_a_users_choice_drives_their_writes(self):
		_set_settings(allow_session_auto_approve=1)
		user = frappe.session.user
		confirm.set_approval_mode(confirm.MODE_AUTO_EDITS)
		self.assertEqual(confirm.decide("create_document", user, "s1"), "allow")
		self.assertEqual(confirm.decide("delete_document", user, "s1"), "ask")
		self.assertEqual(confirm.decide("create_document", user, "s1", doctype="Client Script"), "ask")
		confirm.set_approval_mode(confirm.MODE_READ_ONLY)
		self.assertEqual(confirm.decide("create_document", user, "s1"), "deny")

	def test_a_stricter_choice_holds_on_a_permissive_site(self):
		_set_settings(write_approval=confirm.MODE_AUTO_ALL)
		confirm.set_approval_mode(confirm.MODE_ASK)
		self.assertEqual(confirm.decide("create_document", frappe.session.user, "s1"), "ask")

	def test_a_looser_choice_is_refused_and_a_withdrawn_one_falls_back(self):
		with self.assertRaises(frappe.ValidationError):
			confirm.set_approval_mode(confirm.MODE_AUTO_ALL)
		_set_settings(allow_session_auto_approve=1)
		confirm.set_approval_mode(confirm.MODE_AUTO_EDITS)
		_set_settings(allow_session_auto_approve=0)  # the admin takes it back
		self.assertEqual(confirm.user_mode(frappe.session.user), confirm.MODE_ASK)

	def test_the_menu_payload(self):
		out = confirm.get_approval_modes()
		self.assertEqual(out["current"], confirm.MODE_ASK)
		self.assertEqual(out["site"], confirm.MODE_ASK)
		self.assertIn(confirm.MODE_READ_ONLY, out["allowed"])


class TestBatchTokens(unittest.TestCase):
	def test_a_batch_token_is_for_its_size(self):
		user = frappe.session.user
		rows = [{"subject": "Examplia one"}, {"subject": "Examplia two"}]
		token = confirm.issue(user, "create_documents", {"doctype": "Task", "rows": rows})
		self.assertEqual(confirm.redeem(token, "create_documents", "Task", "3 rows"), {"ok": False})
		token = confirm.issue(user, "create_documents", {"doctype": "Task", "rows": rows})
		self.assertEqual(confirm.redeem(token, "create_documents", "Task", "2 rows"), {"ok": True})

	def test_batch_name(self):
		self.assertEqual(confirm.batch_name({"rows": [{}, {}, {}]}), "3 rows")
		self.assertEqual(confirm.batch_name({}), "0 rows")
