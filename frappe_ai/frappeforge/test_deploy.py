# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""Propose, approve, apply, roll back: the whole live-site write path, with a real Frappe
Package Release built on this site."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_ai.frappeforge import connection, deploy
from frappe_ai.frappeforge.testing import allow_many_users

PACKAGE = "ff-test-pkg"
MODULE = "FF Test Module"
FIELD = "ToDo-ff_test_field"


def _build_release() -> bytes:
	"""A one-record release (a Custom Field on ToDo) made the way Vyogo Forge AI's sandbox makes it."""
	if not frappe.db.exists("Package", PACKAGE):
		frappe.get_doc({"doctype": "Package", "name": PACKAGE, "package_name": PACKAGE}).insert()
	if not frappe.db.exists("Module Def", MODULE):
		frappe.get_doc(
			{
				"doctype": "Module Def",
				"module_name": MODULE,
				"custom": 1,
				"app_name": "frappe",
				"package": PACKAGE,
			}
		).insert()
	if not frappe.db.exists("Custom Field", FIELD):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "ToDo",
				"fieldname": "ff_test_field",
				"label": "FF Test",
				"fieldtype": "Data",
				"module": MODULE,
				"insert_after": "description",
			}
		).insert()
	# Frappe 16.33's Package Release.set_version replaces a 0 minor or patch with the column name once
	# a release exists, so the version is set explicitly with every part >= 1 (as Vyogo Forge AI does).
	patch = (
		frappe.db.sql("select max(patch) from `tabPackage Release` where package=%s", PACKAGE)[0][0] or 0
	) + 1
	rel = frappe.get_doc(
		{
			"doctype": "Package Release",
			"package": PACKAGE,
			"major": 1,
			"minor": 1,
			"patch": patch,
			"publish": 1,
		}
	).insert()
	return Path(frappe.get_site_path("public", "files", f"{rel.name}.tar.gz")).read_bytes()


class DeployTestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		allow_many_users()
		frappe.set_user("Administrator")
		connection.ensure_agent_role()
		cls.tarball = _build_release()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self.conn = connection.create_connection(label="deploy test")
		frappe.db.delete("Vyogo Forge AI Change Log")
		frappe.db.delete("Vyogo Forge AI Proposed Release")
		frappe.delete_doc("Custom Field", FIELD, ignore_permissions=True, force=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.cache.delete_keys("frappe_ai:frappeforge:")

	def propose(self, **kwargs):
		frappe.set_user(self.conn["user"])
		try:
			return deploy.propose(self.tarball, f"{PACKAGE}-1.1.1.tar.gz", **kwargs)
		finally:
			frappe.set_user("Administrator")


class TestPropose(DeployTestCase):
	def test_creates_a_pending_proposal_from_the_tarball(self):
		out = self.propose(summary="Adds a field", conversation_id="conv-1")
		doc = frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"])
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(doc.package, PACKAGE)
		self.assertEqual(doc.connection, self.conn["connection"])
		self.assertEqual(doc.proposed_by, self.conn["user"])
		# Frappe 16 gives a new Module Def a Workspace of the same name, which the release carries too
		records = {(r["doctype"], r["name"]): r for r in json.loads(doc.records_json)}
		self.assertIn(("Custom Field", FIELD), records)
		self.assertEqual(records[("Custom Field", FIELD)]["action"], "Create")
		self.assertIn("/vyogo-forge-ai-proposed-release/" + out["name"], out["approve_url"])

	def test_release_file_is_private(self):
		out = self.propose()
		url = frappe.db.get_value("Vyogo Forge AI Proposed Release", out["name"], "release_file")
		self.assertTrue(url.startswith("/private/files/"), url)

	def test_same_idempotency_key_returns_the_same_proposal(self):
		first = self.propose(idempotency_key="k-1")
		second = self.propose(idempotency_key="k-1")
		self.assertEqual(first["name"], second["name"])
		self.assertEqual(
			frappe.db.count("Vyogo Forge AI Proposed Release", {"connection": self.conn["connection"]}), 1
		)

	def test_a_retry_racing_the_first_request_gets_the_same_proposal(self):
		"""Both requests miss the key lookup; the database's unique key lets one insert win and the
		other must return that proposal instead of failing."""
		first = self.propose(idempotency_key="race-1")
		real_get_value = frappe.db.get_value
		key_lookups = {"count": 0}

		def miss_first_lookup(doctype, filters=None, *args, **kwargs):
			if (
				doctype == "Vyogo Forge AI Proposed Release"
				and isinstance(filters, dict)
				and "idempotency_key" in filters
			):
				key_lookups["count"] += 1
				if key_lookups["count"] == 1:
					return None
			return real_get_value(doctype, filters, *args, **kwargs)

		with patch.object(frappe.db, "get_value", side_effect=miss_first_lookup):
			second = self.propose(idempotency_key="race-1")
		self.assertEqual(second["name"], first["name"])
		self.assertEqual(
			frappe.db.count("Vyogo Forge AI Proposed Release", {"connection": self.conn["connection"]}), 1
		)

	def test_different_idempotency_keys_create_different_proposals(self):
		first = self.propose(idempotency_key="key-a")
		second = self.propose(idempotency_key="key-b")
		self.assertNotEqual(first["name"], second["name"])

	def test_invalid_tarball_is_refused(self):
		frappe.set_user(self.conn["user"])
		with self.assertRaises(frappe.ValidationError):
			deploy.propose(b"not a tarball", "x-0.0.1.tar.gz")

	def test_only_agent_users_propose(self):
		with self.assertRaises(frappe.PermissionError):
			deploy.propose(self.tarball, f"{PACKAGE}-1.1.1.tar.gz")

	def test_reports_travel_as_extra_records(self):
		report = {
			"doctype": "Report",
			"report_name": "FF Open ToDos",
			"ref_doctype": "ToDo",
			"report_type": "Report Builder",
			"is_standard": "No",
			"module": MODULE,
		}
		out = self.propose(extra_records=[report])
		extra = json.loads(
			frappe.db.get_value("Vyogo Forge AI Proposed Release", out["name"], "extra_records_json")
		)
		self.assertEqual(extra[0]["report_name"], "FF Open ToDos")

	def test_extra_records_are_limited_to_workflow_doctypes(self):
		with self.assertRaises(frappe.ValidationError):
			self.propose(extra_records=[{"doctype": "User", "name": "x@example.com"}])
		out = self.propose(extra_records=[{"doctype": "Workflow State", "workflow_state_name": "FF Review"}])
		extra = json.loads(
			frappe.db.get_value("Vyogo Forge AI Proposed Release", out["name"], "extra_records_json")
		)
		self.assertEqual(extra[0]["doctype"], "Workflow State")


class TestApprove(DeployTestCase):
	def test_only_system_managers_approve(self):
		out = self.propose()
		frappe.set_user(self.conn["user"])
		with self.assertRaises(frappe.PermissionError):
			deploy.approve(out["name"])

	def test_apply_creates_the_record_and_logs_it(self):
		out = self.propose()
		deploy.approve(out["name"])
		doc = frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"])
		self.assertEqual(doc.status, "Applied", doc.error)
		self.assertEqual(doc.approved_by, "Administrator")
		self.assertTrue(frappe.db.exists("Custom Field", FIELD))
		log = frappe.get_all(
			"Vyogo Forge AI Change Log",
			filters={"proposed_release": out["name"], "target_name": FIELD},
			fields=["action", "target_doctype", "target_name", "before_json", "after_json", "approved_by"],
		)
		self.assertEqual(len(log), 1)
		self.assertEqual(
			(log[0].action, log[0].target_doctype, log[0].target_name), ("Create", "Custom Field", FIELD)
		)
		self.assertFalse(log[0].before_json)
		self.assertEqual(json.loads(log[0].after_json)["label"], "FF Test")
		self.assertEqual(log[0].approved_by, "Administrator")

	def test_the_change_log_does_not_stop_the_owner_deleting_the_record(self):
		out = self.propose()
		deploy.approve(out["name"])
		frappe.delete_doc("Custom Field", FIELD)  # no force: link checks run
		self.assertFalse(frappe.db.exists("Custom Field", FIELD))
		self.assertTrue(frappe.db.exists("Vyogo Forge AI Change Log", {"proposed_release": out["name"]}))

	def test_apply_overwrites_and_records_the_previous_version(self):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "ToDo",
				"fieldname": "ff_test_field",
				"label": "Changed",
				"fieldtype": "Data",
				"module": MODULE,
			}
		).insert()
		out = self.propose()
		records = json.loads(
			frappe.db.get_value("Vyogo Forge AI Proposed Release", out["name"], "records_json")
		)
		self.assertEqual(next(r for r in records if r["name"] == FIELD)["action"], "Update")
		deploy.approve(out["name"])
		self.assertEqual(frappe.db.get_value("Custom Field", FIELD, "label"), "FF Test")
		before = frappe.db.get_value(
			"Vyogo Forge AI Change Log",
			{"proposed_release": out["name"], "target_name": FIELD},
			"before_json",
		)
		self.assertEqual(json.loads(before)["label"], "Changed")

	def test_extra_records_are_applied_and_logged(self):
		out = self.propose(extra_records=[{"doctype": "Workflow State", "workflow_state_name": "FF Review"}])
		deploy.approve(out["name"])
		self.assertTrue(frappe.db.exists("Workflow State", "FF Review"))
		self.assertTrue(
			frappe.db.exists(
				"Vyogo Forge AI Change Log",
				{"proposed_release": out["name"], "target_doctype": "Workflow State"},
			)
		)

	def test_failed_import_leaves_no_change_and_records_the_error(self):
		out = self.propose()
		with patch(
			"frappe.core.doctype.package_import.package_import.PackageImport.import_package",
			side_effect=RuntimeError("disk full"),
		):
			deploy.approve(out["name"])
		doc = frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"])
		self.assertEqual(doc.status, "Failed")
		self.assertIn("disk full", doc.error)
		self.assertFalse(frappe.db.exists("Custom Field", FIELD))
		self.assertEqual(frappe.db.count("Vyogo Forge AI Change Log", {"proposed_release": out["name"]}), 0)

	def test_a_proposal_is_applied_once(self):
		out = self.propose()
		deploy.approve(out["name"])
		with self.assertRaises(frappe.ValidationError):
			deploy.approve(out["name"])

	def test_reject_records_the_reason_and_blocks_approval(self):
		out = self.propose()
		deploy.reject(out["name"], "not now")
		doc = frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"])
		self.assertEqual((doc.status, doc.rejected_reason), ("Rejected", "not now"))
		with self.assertRaises(frappe.ValidationError):
			deploy.approve(out["name"])

	def test_revoked_connection_cannot_be_approved(self):
		out = self.propose()
		connection.revoke(self.conn["connection"])
		with self.assertRaises(frappe.ValidationError):
			deploy.approve(out["name"])


class TestRollback(DeployTestCase):
	def _applied(self, **kwargs):
		out = self.propose(**kwargs)
		deploy.approve(out["name"])
		return out["name"], frappe.db.get_value(
			"Vyogo Forge AI Change Log", {"proposed_release": out["name"], "target_name": FIELD}, "name"
		)

	def test_rollback_of_a_create_deletes_the_record(self):
		_, change = self._applied()
		deploy.rollback(change)
		self.assertFalse(frappe.db.exists("Custom Field", FIELD))
		self.assertEqual(frappe.db.get_value("Vyogo Forge AI Change Log", change, "status"), "Rolled Back")
		undo = frappe.db.get_value(
			"Vyogo Forge AI Change Log", {"rollback_of": change}, ["action", "approved_by"], as_dict=True
		)
		self.assertEqual((undo.action, undo.approved_by), ("Rollback", "Administrator"))

	def test_rollback_of_an_update_restores_the_previous_version(self):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "ToDo",
				"fieldname": "ff_test_field",
				"label": "Changed",
				"fieldtype": "Data",
				"module": MODULE,
			}
		).insert()
		_, change = self._applied()
		deploy.rollback(change)
		self.assertEqual(frappe.db.get_value("Custom Field", FIELD, "label"), "Changed")

	def test_rollback_twice_is_refused(self):
		_, change = self._applied()
		deploy.rollback(change)
		with self.assertRaises(frappe.ValidationError):
			deploy.rollback(change)

	def test_agent_cannot_roll_back_but_can_ask(self):
		_, change = self._applied()
		frappe.set_user(self.conn["user"])
		with self.assertRaises(frappe.PermissionError):
			deploy.rollback(change)
		deploy.request_rollback(change)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Vyogo Forge AI Change Log", change, "rollback_requested"), 1)

	def test_unauthorized_user_cannot_flag_rollback(self):
		_, change = self._applied()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			deploy.request_rollback(change)


class TestHousekeeping(DeployTestCase):
	def test_stale_pending_proposals_expire(self):
		out = self.propose()
		frappe.db.set_value(
			"Vyogo Forge AI Proposed Release",
			out["name"],
			"creation",
			add_to_date(now_datetime(), days=-30),
			update_modified=False,
		)
		deploy.expire_proposals()
		self.assertEqual(
			frappe.db.get_value("Vyogo Forge AI Proposed Release", out["name"], "status"), "Expired"
		)

	def test_status_requires_agent_and_lists_proposals(self):
		mine = self.propose()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			deploy.status()
		frappe.set_user(self.conn["user"])
		self.assertIn(mine["name"], [p["name"] for p in deploy.status()["proposals"]])

	def test_preview_shows_fresh_diffs(self):
		out = self.propose()
		changes = deploy.preview(frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"]))
		field = next(c for c in changes if c["name"] == FIELD)
		self.assertEqual(field["action"], "Create")
		self.assertIn("label", [c["field"] for c in field["changes"]])


class TestApprovalPage(DeployTestCase):
	def test_html_lists_each_record_with_its_action_and_fields(self):
		out = self.propose(summary="Adds a field")
		html = deploy.render_changes_html(frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"]))
		self.assertIn("Custom Field", html)
		self.assertIn(FIELD, html)
		self.assertIn("Create", html)
		self.assertIn("FF Test", html)

	def test_code_is_shown_in_full_and_escaped(self):
		doc = frappe.get_doc("Vyogo Forge AI Proposed Release", self.propose()["name"])
		records = json.loads(doc.records_json)
		records.append(
			{
				"doctype": "Server Script",
				"name": "Evil",
				"executable": True,
				"data": {
					"doctype": "Server Script",
					"name": "Evil",
					"script": "frappe.throw('<script>x</script>')",
				},
			}
		)
		doc.records_json = json.dumps(records)
		html = deploy.render_changes_html(doc)
		normalised = html.replace("&#39;", "'").replace("&#x27;", "'").replace("&apos;", "'")
		self.assertIn("frappe.throw('&lt;script&gt;x&lt;/script&gt;')", normalised)
		self.assertNotIn("<script>x</script>", html)
		self.assertIn("runs code", html)

	def test_workflow_records_are_listed(self):
		out = self.propose(extra_records=[{"doctype": "Workflow State", "workflow_state_name": "FF Review"}])
		html = deploy.render_changes_html(frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"]))
		self.assertIn("Workflow State", html)
		self.assertIn("FF Review", html)
