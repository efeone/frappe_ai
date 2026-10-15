# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""The guard is the line between an untrusted Vyogo Forge AI sandbox and this site: an agent user
may read and propose, nothing else, whatever roles it holds."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from frappe_ai.frappeforge import connection, guard
from frappe_ai.frappeforge.testing import allow_many_users


class TestDecide(unittest.TestCase):
	ALLOWED = (
		("GET", "/api/resource/Custom Field", None),
		("GET", "/api/resource/Sales Order/SO-0001", None),
		("HEAD", "/api/resource/Sales Order", None),
		("GET", "/api/v2/document/Sales Order", None),
		("GET", "/api/v2/document/Sales Order/SO-0001", None),
		("GET", "/api/v2/doctype/Sales Order/meta", None),
		("GET", "/api/method/ping", None),
		("POST", "/api/method/frappe.client.get_list", None),
		("POST", "/api/method/frappe.client.get_count", None),
		("POST", "/api/method/frappe.desk.query_report.run", None),
		("POST", "/api/method/frappe.utils.global_search.search", None),
		("GET", "/api/v2/method/frappe.client.get", None),
		("POST", "/api/method/frappe_ai.api.frappeforge.propose_release", None),
		("GET", "/api/method/frappe_ai.api.frappeforge.status", None),
		("POST", "/api/method/frappe_ai.api.frappeforge.request_rollback", None),
		# through frappe-mcp-server: a write becomes a proposal
		("POST", "/api/method/frappe_ai.api.direct_write.authorize", None),
		("POST", "/api/method/frappe_ai.api.frappeforge.propose_change", None),
		("POST", "/", "frappe.client.get"),
		("GET", "/api/method/frappe.integrations.oauth2.openid_profile", None),
	)
	DENIED = (
		("POST", "/api/resource/Custom Field", None),
		("PUT", "/api/resource/Custom Field/Sales Order-x", None),
		("DELETE", "/api/resource/Custom Field/Sales Order-x", None),
		("POST", "/api/v2/document/Sales Order", None),
		("PATCH", "/api/v2/document/Sales Order/SO-0001", None),
		("GET", "/api/v2/document/Sales Order/SO-0001/method/submit", None),
		("POST", "/api/method/frappe.client.insert", None),
		("GET", "/api/method/frappe.client.set_value", None),
		("POST", "/api/method/frappe.client.delete", None),
		("POST", "/api/method/upload_file", None),
		("POST", "/api/method/frappe_ai.api.frappeforge.approve", None),
		("POST", "/api/method/frappe_ai.api.frappeforge.create_connection", None),
		("POST", "/api/method/run_doc_method", None),
		("POST", "/", "frappe.client.insert"),
		("GET", "/app/sales-order", None),
		("GET", "/private/files/secret.pdf", None),
		("GET", "/api/resource/", None),
	)

	def test_reads_and_proposals_are_allowed(self):
		for method, path, cmd in self.ALLOWED:
			with self.subTest(method=method, path=path, cmd=cmd):
				self.assertTrue(guard.decide(method, path, cmd)[0], guard.decide(method, path, cmd)[1])

	def test_everything_else_is_denied(self):
		for method, path, cmd in self.DENIED:
			with self.subTest(method=method, path=path, cmd=cmd):
				allowed, reason = guard.decide(method, path, cmd)
				self.assertFalse(allowed)
				self.assertTrue(reason)

	def test_site_owner_can_allow_extra_read_methods(self):
		self.assertFalse(guard.decide("POST", "/api/method/rag.search.search", None)[0])
		self.assertTrue(
			guard.decide("POST", "/api/method/rag.search.search", None, extra=["rag.search.search"])[0]
		)


class TestEnforce(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		allow_many_users()
		connection.ensure_agent_role()
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.cache.delete_keys("frappe_ai:frappeforge:")

	def _request(self, method, path):
		return patch.object(frappe, "request", SimpleNamespace(method=method, path=path), create=True)

	def test_ordinary_users_are_untouched(self):
		frappe.set_user("Administrator")
		with self._request("POST", "/api/resource/Custom Field"):
			guard.enforce()

	def test_agent_write_is_refused(self):
		result = connection.create_connection(label="guard")
		frappe.set_user(result["user"])
		with self._request("POST", "/api/resource/Custom Field"), self.assertRaises(frappe.PermissionError):
			guard.enforce()

	def test_agent_read_passes(self):
		result = connection.create_connection(label="guard")
		frappe.set_user(result["user"])
		with self._request("GET", "/api/resource/Custom Field"):
			guard.enforce()

	def test_expired_connection_is_refused_before_the_job_runs(self):
		result = connection.create_connection(label="guard")
		frappe.db.set_single_value(
			"AI Assistant Settings",
			"forge_expires_on",
			add_to_date(now_datetime(), minutes=-1),
		)
		frappe.cache.delete_keys("frappe_ai:frappeforge:")
		frappe.set_user(result["user"])
		with self._request("GET", "/api/resource/Custom Field"), self.assertRaises(frappe.PermissionError):
			guard.enforce()


class TestOverHttp(unittest.TestCase):
	"""Real requests through the running web server with a real agent key."""

	BASE = "http://127.0.0.1:8000"

	@classmethod
	def setUpClass(cls):
		allow_many_users()
		try:
			requests.get(cls.BASE + "/api/method/ping", headers={"Host": frappe.local.site}, timeout=5)
		except requests.RequestException:
			raise unittest.SkipTest("no web server on :8000")
		frappe.set_user("Administrator")
		connection.ensure_agent_role()
		cls.result = connection.create_connection(label="http guard")
		frappe.db.commit()
		cls.headers = {
			"Host": frappe.local.site,
			"Authorization": f"token {cls.result['api_key']}:{cls.result['api_secret']}",
		}

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		connection.revoke(cls.result["connection"])
		frappe.db.commit()

	def test_agent_can_read(self):
		r = requests.get(self.BASE + "/api/resource/Custom Field", headers=self.headers, timeout=10)
		self.assertEqual(r.status_code, 200, r.text[:300])

	def test_agent_cannot_create_a_record_its_roles_allow(self):
		# every user may create a ToDo, so only the guard stands between this request and a write
		r = requests.post(
			self.BASE + "/api/resource/ToDo",
			headers=self.headers,
			json={"description": "written by a sandbox"},
			timeout=10,
		)
		self.assertEqual(r.status_code, 403, r.text[:300])
		self.assertIn("Vyogo Forge AI may only read", r.text)
		self.assertFalse(frappe.db.exists("ToDo", {"description": "written by a sandbox"}))

	def test_agent_cannot_call_a_write_method(self):
		r = requests.post(
			self.BASE + "/api/method/frappe.client.insert",
			headers=self.headers,
			json={"doc": {"doctype": "ToDo", "description": "via insert"}},
			timeout=10,
		)
		self.assertEqual(r.status_code, 403, r.text[:300])
		self.assertIn("Vyogo Forge AI may only read", r.text)

	def test_revoked_key_stops_working(self):
		extra = connection.create_connection(label="revoke me")
		frappe.db.commit()
		headers = {**self.headers, "Authorization": f"token {extra['api_key']}:{extra['api_secret']}"}
		self.assertEqual(
			requests.get(self.BASE + "/api/method/ping", headers=headers, timeout=10).status_code, 200
		)
		connection.revoke(extra["connection"])
		frappe.db.commit()
		self.assertIn(
			requests.get(self.BASE + "/api/method/ping", headers=headers, timeout=10).status_code, (401, 403)
		)


class TestBearerOverHttp(TestOverHttp):
	"""The hosted frappe-mcp-server's path: it checks the token at openid_profile, then forwards it."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.headers = {"Host": frappe.local.site, "Authorization": f"Bearer {cls.result['access_token']}"}

	def test_openid_profile_names_the_agent_user(self):
		r = requests.get(
			self.BASE + "/api/method/frappe.integrations.oauth2.openid_profile",
			headers=self.headers,
			timeout=10,
		)
		self.assertEqual(r.status_code, 200, r.text[:300])
		self.assertEqual(r.json().get("email"), self.result["user"])

	def test_revoked_key_stops_working(self):
		extra = connection.create_connection(label="revoke bearer")
		frappe.db.commit()
		headers = {**self.headers, "Authorization": f"Bearer {extra['access_token']}"}
		self.assertEqual(
			requests.get(self.BASE + "/api/method/ping", headers=headers, timeout=10).status_code, 200
		)
		connection.revoke(extra["connection"])
		frappe.db.commit()
		self.assertIn(
			requests.get(self.BASE + "/api/method/ping", headers=headers, timeout=10).status_code, (401, 403)
		)
