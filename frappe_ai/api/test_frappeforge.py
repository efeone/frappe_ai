# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""The HTTP surface FrappeForge and the Desk use: who may call what, and the multipart
proposal upload exactly as the FrappeForge runtime sends it."""

import json
import unittest
from pathlib import Path

import frappe
import requests

from frappe_ai.api import frappeforge as api
from frappe_ai.frappeforge import connection
from frappe_ai.frappeforge.test_deploy import PACKAGE, _build_release
from frappe_ai.frappeforge.testing import allow_many_users

PLAIN_USER = "ff-plain-user@example.com"


def _plain_user() -> str:
	if not frappe.db.exists("User", PLAIN_USER):
		frappe.get_doc(
			{"doctype": "User", "email": PLAIN_USER, "first_name": "Plain", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	return PLAIN_USER


class TestPermissions(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		allow_many_users()
		connection.ensure_agent_role()
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.cache.delete_keys("frappe_ai:frappeforge:")

	def test_only_system_managers_manage_connections(self):
		frappe.set_user(_plain_user())
		for call in (
			lambda: api.create_connection(label="x"),
			lambda: api.rotate_key("x"),
			lambda: api.revoke("x"),
			lambda: api.approve("x"),
			lambda: api.reject("x"),
			lambda: api.rollback("x"),
		):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_system_manager_gets_a_connection_snippet(self):
		out = api.create_connection(label="Desk button", expires_in_days=7)
		self.assertEqual(set(out) >= {"site_url", "api_key", "api_secret", "connection", "expires_on"}, True)
		snippet = json.loads(out["snippet"])
		self.assertEqual(
			snippet,
			{
				"site_url": out["site_url"],
				"api_key": out["api_key"],
				"api_secret": out["api_secret"],
				"access_token": out["access_token"],
			},
		)

	def _set_mcp_url(self, url):
		frappe.db.set_single_value("AI Assistant Settings", "mcp_url", url)

	def test_snippet_includes_the_mcp_server_url_from_settings(self):
		self._set_mcp_url("https://mcp.example.com/mcp")
		out = api.create_connection(label="with mcp")
		self.assertEqual(json.loads(out["snippet"])["mcp_url"], "https://mcp.example.com/mcp")

	def test_snippet_has_no_mcp_url_when_the_setting_is_empty(self):
		self._set_mcp_url("")
		out = api.create_connection(label="without mcp")
		self.assertNotIn("mcp_url", json.loads(out["snippet"]))

	def test_rotated_snippet_keeps_the_mcp_url(self):
		self._set_mcp_url("https://mcp.example.com/mcp")
		out = api.create_connection(label="rotate mcp")
		rotated = api.rotate_key(out["connection"])
		self.assertEqual(json.loads(rotated["snippet"])["mcp_url"], "https://mcp.example.com/mcp")

	def test_status_is_for_agents(self):
		with self.assertRaises(frappe.PermissionError):
			api.status()

	def test_proposals_are_rate_limited_per_connection(self):
		conn = connection.create_connection(label="limits")
		frappe.set_user(conn["user"])
		for _ in range(api.PROPOSALS_PER_HOUR):
			api._limit("propose", api.PROPOSALS_PER_HOUR)
		with self.assertRaises(frappe.TooManyRequestsError):
			api._limit("propose", api.PROPOSALS_PER_HOUR)
		other = connection.create_connection(label="limits other")
		frappe.set_user(other["user"])
		api._limit("propose", api.PROPOSALS_PER_HOUR)  # another tenant is not affected


class TestOverHttp(unittest.TestCase):
	"""The runtime's calls: multipart propose with an agent key, then status."""

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
		cls.tarball = _build_release()
		cls.conn = connection.create_connection(label="http api")
		frappe.db.commit()
		cls.headers = {
			"Host": frappe.local.site,
			"Authorization": f"token {cls.conn['api_key']}:{cls.conn['api_secret']}",
		}

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		connection.revoke(cls.conn["connection"])
		frappe.db.commit()

	def test_propose_then_status(self):
		r = requests.post(
			self.BASE + "/api/method/frappe_ai.api.frappeforge.propose_release",
			headers=self.headers,
			files={"release": (f"{PACKAGE}-0.0.9.tar.gz", self.tarball, "application/gzip")},
			data={
				"summary": "Adds a field to ToDo",
				"conversation_id": "conv-http",
				"idempotency_key": frappe.generate_hash(length=8),
				"extra_records": json.dumps(
					[{"doctype": "Workflow State", "workflow_state_name": "FF HTTP"}]
				),
			},
			timeout=30,
		)
		self.assertEqual(r.status_code, 200, r.text[:500])
		proposal = r.json()["message"]
		self.assertEqual(
			(proposal["package"], proposal["version"], proposal["status"]), (PACKAGE, "0.0.9", "Pending")
		)

		s = requests.get(
			self.BASE + "/api/method/frappe_ai.api.frappeforge.status", headers=self.headers, timeout=10
		)
		self.assertEqual(s.status_code, 200, s.text[:300])
		self.assertIn(proposal["name"], [p["name"] for p in s.json()["message"]["proposals"]])

	def test_agent_cannot_approve_over_http(self):
		r = requests.post(
			self.BASE + "/api/method/frappe_ai.api.frappeforge.approve",
			headers=self.headers,
			data={"release": "FFR-00001"},
			timeout=10,
		)
		self.assertEqual(r.status_code, 403, r.text[:300])
