# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""One agent user per FrappeForge connection: its own key, only the agent role, an expiry,
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
		# install-time setup (after_migrate does the same); committed so per-test rollbacks keep it
		connection.ensure_agent_role()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self.created: list[str] = []

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.cache.delete_keys("frappe_ai:frappeforge:")

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
		self.assertTrue(result["user"].endswith("@frappeforge.invalid"))

	def test_returns_a_working_key_pair_once(self):
		result = self.make()
		self.assertEqual(frappe.db.get_value("User", result["user"], "api_key"), result["api_key"])
		self.assertEqual(get_decrypted_password("User", result["user"], "api_secret"), result["api_secret"])
		self.assertEqual(result["site_url"], frappe.utils.get_url())

	def test_each_connection_has_its_own_user(self):
		a, b = self.make(label="A"), self.make(label="B")
		self.assertNotEqual(a["user"], b["user"])

	def test_expiry_defaults_to_30_days_and_is_bounded(self):
		result = self.make()
		expires = frappe.db.get_value("FrappeForge Connection", result["connection"], "expires_on")
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
		self.assertEqual(
			frappe.db.get_value("FrappeForge Connection", result["connection"], "status"), "Revoked"
		)
		self.assertEqual(frappe.db.get_value("User", result["user"], "enabled"), 0)
		self.assertFalse(frappe.db.get_value("User", result["user"], "api_key"))

	def test_rotate_refuses_inactive_connections(self):
		result = self.make()
		connection.revoke(result["connection"])
		with self.assertRaises(frappe.ValidationError):
			connection.rotate_key(result["connection"])

	def test_expire_job_disables_lapsed_connections_only(self):
		lapsed, live = self.make(label="old"), self.make(label="new")
		frappe.db.set_value(
			"FrappeForge Connection", lapsed["connection"], "expires_on", add_to_date(now_datetime(), days=-1)
		)
		connection.expire_connections()
		self.assertEqual(
			frappe.db.get_value("FrappeForge Connection", lapsed["connection"], "status"), "Expired"
		)
		self.assertEqual(frappe.db.get_value("User", lapsed["user"], "enabled"), 0)
		self.assertEqual(
			frappe.db.get_value("FrappeForge Connection", live["connection"], "status"), "Active"
		)


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
		doc = frappe.get_doc("FrappeForge Connection", result["connection"])
		doc.allowed_ips = "192.0.2.10"
		doc.read_roles = "Sales User"
		doc.save()
		user = frappe.get_doc("User", result["user"])
		self.assertEqual(user.restrict_ip, "192.0.2.10")
		self.assertEqual({r.role for r in user.roles}, {AGENT_ROLE, "Sales User"})

	def test_renewal_is_limited_to_a_year_ahead(self):
		result = self.make()
		doc = frappe.get_doc("FrappeForge Connection", result["connection"])
		doc.expires_on = add_to_date(now_datetime(), days=400)
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_a_revoked_connection_cannot_be_reactivated_by_editing(self):
		result = self.make()
		connection.revoke(result["connection"])
		doc = frappe.get_doc("FrappeForge Connection", result["connection"])
		doc.expires_on = add_to_date(now_datetime(), days=10)
		doc.save()
		self.assertEqual(
			frappe.db.get_value("FrappeForge Connection", result["connection"], "status"), "Revoked"
		)
		self.assertEqual(frappe.db.get_value("User", result["user"], "enabled"), 0)


class TestBearerToken(ConnectionTestCase):
	"""Each connection also gets an OAuth Bearer token: the hosted frappe-mcp-server for this site
	authenticates FrappeForge with it and forwards it on every call."""

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
		self.assertAlmostEqual(days, 1, delta=0.01)  # one day: a leaked token is useless by tomorrow
		self.assertEqual(
			frappe.db.get_value("FrappeForge Connection", result["connection"], "oauth_client"), token.client
		)
		self.assertEqual(str(token.expiration_time), result["token_expires_on"])

	def test_token_never_outlives_its_connection(self):
		result = self.make()
		ends = add_to_date(now_datetime(), hours=6)
		frappe.db.set_value("FrappeForge Connection", result["connection"], "expires_on", ends)
		rotated = connection.rotate_key(result["connection"])
		self.assertEqual(self._token(rotated["access_token"]).expiration_time, ends)

	def test_rotate_revokes_the_old_token(self):
		result = self.make()
		rotated = connection.rotate_key(result["connection"])
		self.assertNotEqual(rotated["access_token"], result["access_token"])
		self.assertEqual(self._token(result["access_token"]).status, "Revoked")
		self.assertEqual(self._token(rotated["access_token"]).status, "Active")

	def test_revoke_and_expiry_revoke_the_token(self):
		revoked, lapsed = self.make(label="r"), self.make(label="l")
		connection.revoke(revoked["connection"])
		frappe.db.set_value(
			"FrappeForge Connection", lapsed["connection"], "expires_on", add_to_date(now_datetime(), days=-1)
		)
		connection.expire_connections()
		self.assertEqual(self._token(revoked["access_token"]).status, "Revoked")
		self.assertEqual(self._token(lapsed["access_token"]).status, "Revoked")

	def test_renewing_the_connection_does_not_extend_the_token(self):
		result = self.make(expires_in_days=30)
		before = self._token(result["access_token"]).expiration_time
		doc = frappe.get_doc("FrappeForge Connection", result["connection"])
		doc.expires_on = add_to_date(now_datetime(), days=200)
		doc.save()
		self.assertEqual(self._token(result["access_token"]).expiration_time, before)

	def test_shortening_the_connection_shortens_the_token(self):
		result = self.make(expires_in_days=30)
		doc = frappe.get_doc("FrappeForge Connection", result["connection"])
		doc.expires_on = add_to_date(now_datetime(), hours=6)
		doc.save()
		self.assertEqual(self._token(result["access_token"]).expiration_time, doc.expires_on)
