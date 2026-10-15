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
		# Forge Autonomy is on by default; most of these tests are about the approval path
		frappe.db.set_single_value("AI Assistant Settings", "forge_autonomy", deploy.AUTONOMY_OFF)
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


# ── record-level proposals: a write through frappe-mcp-server ─────────────────


class TestProposeRecords(DeployTestCase):
	def setUp(self):
		super().setUp()
		self.kept = frappe.get_doc({"doctype": "ToDo", "description": "ff kept"}).insert()
		self.gone = frappe.get_doc({"doctype": "ToDo", "description": "ff gone"}).insert()

	def propose_records(self, records, **kwargs):
		frappe.set_user(self.conn["user"])
		try:
			return deploy.propose_records(records, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def three_changes(self):
		return [
			{
				"doctype": "ToDo",
				"name": self.kept.name,
				"action": "Update",
				"data": {"description": "ff updated"},
			},
			{"doctype": "ToDo", "name": "", "action": "Create", "data": {"description": "ff created"}},
			{"doctype": "ToDo", "name": self.gone.name, "action": "Delete", "data": {}},
		]

	def test_a_record_change_is_a_pending_proposal_without_a_package(self):
		out = self.propose_records(self.three_changes(), summary="Tidy ToDos")
		doc = frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"])
		self.assertEqual((doc.status, doc.release_file, doc.has_executable), ("Pending", None, 0))
		self.assertEqual(doc.proposed_by, self.conn["user"])
		self.assertEqual([r["action"] for r in json.loads(doc.records_json)], ["Update", "Create", "Delete"])
		# nothing on the site changed yet
		self.assertEqual(frappe.db.get_value("ToDo", self.kept.name, "description"), "ff kept")
		self.assertTrue(frappe.db.exists("ToDo", self.gone.name))
		self.assertIn("/vyogo-forge-ai-proposed-release/", out["approve_url"])

	def test_approval_applies_each_change_and_logs_it(self):
		out = self.propose_records(self.three_changes())
		deploy.approve(out["name"])
		self.assertEqual(
			frappe.db.get_value("Vyogo Forge AI Proposed Release", out["name"], "status"), "Applied"
		)
		self.assertEqual(frappe.db.get_value("ToDo", self.kept.name, "description"), "ff updated")
		self.assertFalse(frappe.db.exists("ToDo", self.gone.name))
		created = frappe.db.get_value("ToDo", {"description": "ff created"})
		self.assertTrue(created)
		log = {
			r.action: r
			for r in frappe.get_all(
				"Vyogo Forge AI Change Log",
				filters={"proposed_release": out["name"]},
				fields=["name", "action", "target_name", "before_json", "after_json"],
			)
		}
		self.assertEqual(set(log), {"Update", "Create", "Delete"})
		self.assertEqual(log["Create"].target_name, created)
		self.assertIsNone(log["Delete"].after_json)
		self.assertIn("ff gone", log["Delete"].before_json)

	def test_every_change_rolls_back(self):
		out = self.propose_records(self.three_changes())
		deploy.approve(out["name"])
		created = frappe.db.get_value("ToDo", {"description": "ff created"})
		for change in frappe.get_all(
			"Vyogo Forge AI Change Log", filters={"proposed_release": out["name"]}, pluck="name"
		):
			deploy.rollback(change)
		self.assertEqual(frappe.db.get_value("ToDo", self.kept.name, "description"), "ff kept")
		self.assertFalse(frappe.db.exists("ToDo", created))
		self.assertEqual(frappe.db.get_value("ToDo", self.gone.name, "description"), "ff gone")

	def test_a_change_that_runs_code_is_flagged(self):
		out = self.propose_records(
			[
				{
					"doctype": "Client Script",
					"name": "",
					"action": "Create",
					"data": {"dt": "ToDo", "script": "1"},
				}
			]
		)
		self.assertEqual(
			frappe.db.get_value("Vyogo Forge AI Proposed Release", out["name"], "has_executable"), 1
		)

	def test_privilege_records_are_never_proposed(self):
		for doctype in (
			"User",
			"Role",
			"Custom DocPerm",
			"AI Assistant Settings",
			"OAuth Client",
			"Vyogo Forge AI Change Log",
		):
			with self.assertRaises(frappe.ValidationError, msg=doctype):
				self.propose_records(
					[{"doctype": doctype, "name": "x", "action": "Update", "data": {"a": 1}}]
				)

	def test_malformed_changes_are_refused(self):
		for bad in (
			[],
			[{"doctype": "ToDo", "name": "", "action": "Rename", "data": {}}],
			[{"doctype": "ToDo", "name": "", "action": "Update", "data": {"description": "x"}}],
			[{"doctype": "ToDo", "name": "", "action": "Create", "data": "not a dict"}],
			[{"doctype": "No Such DocType", "name": "", "action": "Create", "data": {}}],
			[{"doctype": "ToDo", "name": "", "action": "Create", "data": {}}] * 201,
		):
			with self.assertRaises(frappe.ValidationError):
				self.propose_records(bad)

	def test_only_a_forge_connection_proposes(self):
		with self.assertRaises(frappe.PermissionError):
			deploy.propose_records(self.three_changes())

	def test_a_retry_returns_the_same_proposal(self):
		first = self.propose_records(self.three_changes(), idempotency_key="rec-1")
		second = self.propose_records(self.three_changes(), idempotency_key="rec-1")
		self.assertEqual(first["name"], second["name"])

	def test_the_approver_sees_each_change(self):
		out = self.propose_records(self.three_changes())
		html = deploy.render_changes_html(frappe.get_doc("Vyogo Forge AI Proposed Release", out["name"]))
		for text in ("Update", "Create", "Delete", "ff updated", "ff created", self.gone.name):
			self.assertIn(text, html)

	def test_tool_calls_map_to_changes(self):
		self.assertEqual(
			deploy.records_from_tool("update_document", "ToDo", "T-1", {"description": "x"}),
			[{"doctype": "ToDo", "name": "T-1", "action": "Update", "data": {"description": "x"}}],
		)
		self.assertEqual(
			deploy.records_from_tool("create_document", "ToDo", "", {"description": "x"}),
			[{"doctype": "ToDo", "name": "", "action": "Create", "data": {"description": "x"}}],
		)
		self.assertEqual(
			[
				r["data"]
				for r in deploy.records_from_tool("create_documents", "ToDo", "2 rows", [{"a": 1}, {"a": 2}])
			],
			[{"a": 1}, {"a": 2}],
		)
		self.assertEqual(
			deploy.records_from_tool("delete_document", "ToDo", "T-1", None),
			[{"doctype": "ToDo", "name": "T-1", "action": "Delete", "data": {}}],
		)
		with self.assertRaises(frappe.ValidationError):
			deploy.records_from_tool("run_sql", "ToDo", "", {})


def _set_autonomy(
	mode: str, approver: str = "Administrator", doctypes: str = "Custom Field\nWorkspace\nToDo"
):
	settings = frappe.get_single("AI Assistant Settings")
	settings.forge_autonomy = mode
	settings.forge_autonomy_approver = approver if mode == deploy.AUTONOMY_SKILLS else None
	settings.forge_autonomous_doctypes = doctypes
	settings.save(ignore_permissions=True)


_AUTO_FIELD = {
	"dt": "ToDo",
	"fieldname": "ff_auto_field",
	"label": "FF Auto",
	"fieldtype": "Data",
	"insert_after": "description",
}


class TestAutonomy(DeployTestCase):
	"""With the site's yes, a change the skills cover applies itself; everything else still waits."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.tarball = _build_release()
		frappe.delete_doc("Custom Field", FIELD, ignore_permissions=True, force=True)
		frappe.db.commit()

	def setUp(self):
		super().setUp()
		self.todo = frappe.get_doc({"doctype": "ToDo", "description": "ff autonomous"}).insert()

	def tearDown(self):
		super().tearDown()
		# applying commits, so the setting and what was applied are undone for the tests that follow
		_set_autonomy(deploy.AUTONOMY_OFF)
		frappe.delete_doc("Custom Field", "ToDo-ff_auto_field", ignore_permissions=True, force=True)
		frappe.db.commit()

	def propose_records(self, records):
		frappe.set_user(self.conn["user"])
		try:
			return deploy.propose_records(records)
		finally:
			frappe.set_user("Administrator")

	def update_todo(self, description="ff by forge"):
		return [
			{
				"doctype": "ToDo",
				"name": self.todo.name,
				"action": "Update",
				"data": {"description": description},
			}
		]

	def proposal(self, name):
		return frappe.get_doc(deploy.PROPOSAL, name)

	def test_it_is_on_by_default_as_administrator(self):
		meta = frappe.get_meta("AI Assistant Settings")
		self.assertEqual(meta.get_field("forge_autonomy").default, deploy.AUTONOMY_SKILLS)
		self.assertEqual(meta.get_field("forge_autonomy_approver").default, "Administrator")
		# a site that never saved the new settings
		for field in ("forge_autonomy", "forge_autonomy_approver", "forge_autonomous_doctypes"):
			frappe.db.set_single_value("AI Assistant Settings", field, None)
		lead_like = [{"doctype": "Custom Field", "name": "", "action": "Create", "data": _AUTO_FIELD}]
		out = self.propose_records(lead_like)
		doc = self.proposal(out["name"])
		self.assertEqual((doc.status, doc.approved_by, doc.auto_approved), ("Applied", "Administrator", 1))

	def test_a_site_can_turn_it_off(self):
		_set_autonomy(deploy.AUTONOMY_OFF)
		out = self.propose_records(self.update_todo())
		self.assertEqual(self.proposal(out["name"]).status, "Pending")
		self.assertEqual(frappe.db.get_value("ToDo", self.todo.name, "description"), "ff autonomous")
		self.assertEqual(frappe.db.get_value("ToDo", self.todo.name, "description"), "ff autonomous")

	def test_a_change_the_skills_cover_applies_itself_as_the_approver(self):
		_set_autonomy(deploy.AUTONOMY_SKILLS)
		out = self.propose_records(self.update_todo())
		self.assertEqual(out["auto_approved"], 1)  # Forge is told
		doc = self.proposal(out["name"])
		self.assertEqual((doc.status, doc.approved_by, doc.auto_approved), ("Applied", "Administrator", 1))
		self.assertEqual(frappe.db.get_value("ToDo", self.todo.name, "description"), "ff by forge")
		change = frappe.get_value(deploy.CHANGE_LOG, {"proposed_release": doc.name}, "name")
		deploy.rollback(change)
		self.assertEqual(frappe.db.get_value("ToDo", self.todo.name, "description"), "ff autonomous")

	def test_a_release_the_skills_cover_applies_itself(self):
		_set_autonomy(deploy.AUTONOMY_SKILLS)
		out = self.propose()
		doc = self.proposal(out["name"])
		self.assertEqual(doc.status, "Applied", (doc.autonomy_note, doc.error))
		self.assertTrue(frappe.db.exists("Custom Field", FIELD))

	def test_a_record_type_outside_the_skills_waits_and_says_why(self):
		_set_autonomy(deploy.AUTONOMY_SKILLS, doctypes="Custom Field")
		out = self.propose_records(self.update_todo())
		doc = self.proposal(out["name"])
		self.assertEqual((doc.status, doc.auto_approved), ("Pending", 0))
		self.assertIn("ToDo", doc.autonomy_note)
		self.assertEqual(frappe.db.get_value("ToDo", self.todo.name, "description"), "ff autonomous")

	def test_a_change_that_runs_code_always_waits(self):
		_set_autonomy(deploy.AUTONOMY_SKILLS)
		with patch.object(deploy, "_autonomous_doctypes", return_value={"Client Script", "ToDo"}):
			out = self.propose_records(
				[
					{
						"doctype": "Client Script",
						"name": "",
						"action": "Create",
						"data": {"dt": "ToDo", "script": "1"},
					}
				]
			)
		doc = self.proposal(out["name"])
		self.assertEqual(doc.status, "Pending")
		self.assertIn("runs code", doc.autonomy_note)

	def test_an_approver_who_lost_system_manager_stops_autonomy(self):
		_set_autonomy(deploy.AUTONOMY_SKILLS)
		with patch.object(deploy.frappe, "get_roles", return_value=["Desk User"]):
			out = self.propose_records(self.update_todo())
		doc = self.proposal(out["name"])
		self.assertEqual(doc.status, "Pending")
		self.assertIn("System Manager", doc.autonomy_note)

	def test_the_setting_refuses_unsafe_choices(self):
		for doctypes in ("Client Script", "Server Script", "User", "Custom DocPerm", "No Such DocType"):
			with self.assertRaises(frappe.ValidationError, msg=doctypes):
				_set_autonomy(deploy.AUTONOMY_SKILLS, doctypes=doctypes)
		_set_autonomy(deploy.AUTONOMY_SKILLS, approver="")
		self.assertEqual(
			frappe.db.get_single_value("AI Assistant Settings", "forge_autonomy_approver"), "Administrator"
		)
		with self.assertRaises(frappe.ValidationError):
			_set_autonomy(deploy.AUTONOMY_SKILLS, approver=self.conn["user"])

	def test_the_default_scope_is_what_the_writes_skills_govern(self):
		self.assertEqual(
			set(deploy.DEFAULT_AUTONOMOUS_DOCTYPES),
			{
				"Custom Field",
				"Property Setter",
				"Print Format",
				"Letter Head",
				"Workspace",
				"Lead",
				"CRM Lead",
				"FCRM Note",
			},
		)

	def test_a_blank_list_means_the_shipped_default(self):
		for blank in (None, "", "  \n "):
			self.assertEqual(
				deploy.autonomous_doctypes_from(blank), list(deploy.DEFAULT_AUTONOMOUS_DOCTYPES), repr(blank)
			)
		self.assertEqual(deploy.autonomous_doctypes_from(" Lead \n\nCustom Field "), ["Lead", "Custom Field"])
