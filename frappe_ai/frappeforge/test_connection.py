# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""Agent user for Vyogo Forge AI connection: its own key, only the agent role, an expiry,
and a revocation that takes effect on the next request."""

import unittest
from datetime import timedelta

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils.password import get_decrypted_password

from frappe_ai.frappeforge import connection
from frappe_ai.frappeforge.connection import AGENT_ROLE
from frappe_ai.frappeforge.testing import allow_many_users


class ConnectionTestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		allow_many_users()
		connection.ensure_agent_role()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self.created: list[str] = []

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.cache.delete_keys("frappe_ai:vyogo_forge:")

	def make(self, **kwargs):
		kwargs.setdefault("label", "Test workspace")
		result = connection.create_connection(**kwargs)
		self.created.append(result["connection"])
		return result


class TestCreateConnection(ConnectionTestCase):
	def test_creates_an_agent_user_with_only_the_agent_role(self):
		result = self.make()
		user = frappe.get_doc("User", result["user"])
		self.assertTrue(user.enabled)
		self.assertEqual(user.user_type, "System User")
		self.assertEqual({r.role for r in user.roles}, {AGENT_ROLE})
		self.assertTrue(result["user"].endswith("@vyogoforge.invalid"))

	def test_returns_a_working_key_pair_once(self):
		result = self.make()
		self.assertEqual(frappe.db.get_value("User", result["user"], "api_key"), result["api_key"])
		self.assertEqual(get_decrypted_password("User", result["user"], "api_secret"), result["api_secret"])
		self.assertEqual(result["site_url"], frappe.utils.get_url())

	def test_expiry_defaults_to_30_days_and_is_bounded(self):
		self.make()
		expires = frappe.db.get_single_value("AI Assistant Settings", "forge_expires_on")
		self.assertAlmostEqual((expires - now_datetime()).days, 29, delta=1)
		for bad in (0, 366):
			with self.assertRaises(frappe.ValidationError):
				self.make(expires_in_days=bad)

	def test_allowed_ips_restrict_the_agent_user(self):
		result = self.make(allowed_ips="203.0.113.7, 198.51.100.0")
		self.assertEqual(
			frappe.db.get_value("User", result["user"], "restrict_ip"), "203.0.113.7,198.51.100.0"
		)

	def test_rejects_malformed_ip_entries(self):
		with self.assertRaises(frappe.ValidationError):
			self.make(allowed_ips="not-an-ip")

	def test_read_roles_are_added_but_privileged_roles_are_refused(self):
		result = self.make(read_roles="Sales User")
		roles = {r.role for r in frappe.get_doc("User", result["user"]).roles}
		self.assertEqual(roles, {AGENT_ROLE, "Sales User"})
		for privileged in ("System Manager", "Administrator"):
			with self.assertRaises(frappe.ValidationError):
				self.make(read_roles=privileged)

	def test_unknown_read_role_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.make(read_roles="No Such Role")


class TestLifecycle(ConnectionTestCase):
	def test_rotate_issues_a_new_secret(self):
		result = self.make()
		rotated = connection.rotate_key(result["connection"])
		self.assertEqual(rotated["api_key"], result["api_key"])
		self.assertNotEqual(rotated["api_secret"], result["api_secret"])
		self.assertEqual(get_decrypted_password("User", result["user"], "api_secret"), rotated["api_secret"])

	def test_revoke_disables_the_user_and_clears_its_key(self):
		result = self.make()
		connection.revoke(result["connection"])
		self.assertEqual(frappe.db.get_single_value("AI Assistant Settings", "forge_status"), "Revoked")
		self.assertEqual(frappe.db.get_value("User", result["user"], "enabled"), 0)
		self.assertFalse(frappe.db.get_value("User", result["user"], "api_key"))

	def test_rotate_refuses_inactive_connections(self):
		result = self.make()
		connection.revoke(result["connection"])
		with self.assertRaises(frappe.ValidationError):
			connection.rotate_key(result["connection"])

	def test_expire_job_disables_lapsed_connection(self):
		result = self.make(label="old")
		frappe.db.set_single_value(
			"AI Assistant Settings", "forge_expires_on", add_to_date(now_datetime(), days=-1)
		)
		connection.expire_connections()
		self.assertEqual(frappe.db.get_single_value("AI Assistant Settings", "forge_status"), "Expired")
		self.assertEqual(frappe.db.get_value("User", result["user"], "enabled"), 0)


class TestAgentLookup(ConnectionTestCase):
	def test_agent_connection_for_agent_and_non_agent_users(self):
		result = self.make()
		found = connection.agent_connection(result["user"])
		assert found is not None
		self.assertEqual(found["name"], result["connection"])
		self.assertIsNone(connection.agent_connection("Administrator"))

	def test_revocation_is_seen_immediately_despite_the_cache(self):
		result = self.make()
		self.assertEqual((connection.agent_connection(result["user"]) or {}).get("status"), "Active")
		connection.revoke(result["connection"])
		self.assertEqual((connection.agent_connection(result["user"]) or {}).get("status"), "Revoked")

	def test_is_usable_checks_status_and_expiry(self):
		now = now_datetime()
		self.assertTrue(
			connection.is_usable({"status": "Active", "expires_on": now + timedelta(hours=1)}, now)
		)
		self.assertFalse(
			connection.is_usable({"status": "Active", "expires_on": now - timedelta(seconds=1)}, now)
		)
		self.assertFalse(
			connection.is_usable({"status": "Revoked", "expires_on": now + timedelta(days=1)}, now)
		)
		self.assertFalse(connection.is_usable(None, now))


class TestEditingInDesk(ConnectionTestCase):
	def test_editing_allowed_ips_and_roles_updates_the_agent_user(self):
		result = self.make()
		doc = frappe.get_doc("AI Assistant Settings")
		doc.forge_allowed_ips = "192.0.2.10"
		doc.forge_read_roles = "Sales User"
		doc.save()
		user = frappe.get_doc("User", result["user"])
		self.assertEqual(user.restrict_ip, "192.0.2.10")
		self.assertEqual({r.role for r in user.roles}, {AGENT_ROLE, "Sales User"})


class TestBearerToken(ConnectionTestCase):
	def _token(self, access_token):
		return frappe.db.get_value(
			"OAuth Bearer Token",
			access_token,
			["user", "status", "expiration_time", "scopes", "client"],
			as_dict=True,
		)

	def test_create_issues_a_one_day_token_for_the_agent_user(self):
		result = self.make(expires_in_days=30)
		token = self._token(result["access_token"])
		self.assertEqual((token.user, token.status), (result["user"], "Active"))
		self.assertIn("openid", token.scopes.split())
		days = (token.expiration_time - now_datetime()).total_seconds() / 86400
		self.assertAlmostEqual(days, 1, delta=0.01)
		self.assertEqual(
			frappe.db.get_single_value("AI Assistant Settings", "forge_oauth_client"), token.client
		)
		self.assertEqual(str(token.expiration_time), result["token_expires_on"])

	def test_token_never_outlives_its_connection(self):
		result = self.make()
		ends = add_to_date(now_datetime(), hours=6)
		frappe.db.set_single_value("AI Assistant Settings", "forge_expires_on", ends)
		rotated = connection.rotate_key(result["connection"])
		self.assertEqual(self._token(rotated["access_token"]).expiration_time, ends)

	def test_rotate_revokes_the_old_token(self):
		result = self.make()
		rotated = connection.rotate_key(result["connection"])
		self.assertNotEqual(rotated["access_token"], result["access_token"])
		self.assertEqual(self._token(result["access_token"]).status, "Revoked")
		self.assertEqual(self._token(rotated["access_token"]).status, "Active")

	def test_revoke_and_expiry_revoke_the_token(self):
		result = self.make(label="r")
		connection.revoke(result["connection"])
		self.assertEqual(self._token(result["access_token"]).status, "Revoked")
