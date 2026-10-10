# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""The live site reads a FrappeForge release itself: what an approver sees comes from the
tarball, never from the agent's description of it."""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from frappe_ai.frappeforge import release


def _tarball(entries: dict[str, bytes | None], *, symlink: str | None = None) -> Path:
	"""entries: archive path -> file bytes (None makes a directory)."""
	path = Path(tempfile.mkdtemp()) / "sales_rules-0.0.1.tar.gz"
	with tarfile.open(path, "w:gz") as tar:
		for name, data in entries.items():
			info = tarfile.TarInfo(name)
			if data is None:
				info.type = tarfile.DIRTYPE
				tar.addfile(info)
			else:
				info.size = len(data)
				tar.addfile(info, io.BytesIO(data))
		if symlink:
			info = tarfile.TarInfo(symlink)
			info.type = tarfile.SYMTYPE
			info.linkname = "/etc/passwd"
			tar.addfile(info)
	return path


def _doc(**fields) -> bytes:
	return json.dumps(fields).encode()


PACKAGE = {
	"sales_rules/sales_rules.json": _doc(doctype="Package", name="sales_rules", package_name="sales_rules"),
	"sales_rules/sales_rules_module/custom_field/sales_order_po_required/sales_order_po_required.json": _doc(
		doctype="Custom Field",
		name="Sales Order-po_required",
		dt="Sales Order",
		fieldname="po_required",
		module="Sales Rules Module",
	),
	"sales_rules/sales_rules_module/server_script/so_po_check/so_po_check.json": _doc(
		doctype="Server Script",
		name="SO PO Check",
		script="if doc.grand_total > 50000 and not doc.po_no:\n    frappe.throw('PO required')",
		module="Sales Rules Module",
	),
}


class TestInspectRelease(unittest.TestCase):
	def test_lists_package_and_records(self):
		info = release.inspect_release(_tarball(PACKAGE))
		self.assertEqual(info["package"], "sales_rules")
		self.assertEqual(
			sorted((r["doctype"], r["name"]) for r in info["records"]),
			[("Custom Field", "Sales Order-po_required"), ("Server Script", "SO PO Check")],
		)
		self.assertEqual(info["modules"], ["Sales Rules Module"])

	def test_marks_executable_records(self):
		info = release.inspect_release(_tarball(PACKAGE))
		risky = {r["name"] for r in info["records"] if r["executable"]}
		self.assertEqual(risky, {"SO PO Check"})
		self.assertTrue(info["has_executable"])

	def test_record_content_is_kept_for_the_diff(self):
		info = release.inspect_release(_tarball(PACKAGE))
		script = next(r for r in info["records"] if r["doctype"] == "Server Script")
		self.assertIn("PO required", script["data"]["script"])

	def test_rejects_symlinks(self):
		with self.assertRaises(release.InvalidRelease):
			release.inspect_release(_tarball(PACKAGE, symlink="sales_rules/evil"))

	def test_rejects_path_traversal(self):
		bad = {**PACKAGE, "../outside.json": _doc(doctype="Custom Field", name="x")}
		with self.assertRaises(release.InvalidRelease):
			release.inspect_release(_tarball(bad))

	def test_rejects_doctypes_outside_the_allowlist(self):
		bad = {
			**PACKAGE,
			"sales_rules/sales_rules_module/user/admin/admin.json": _doc(doctype="User", name="x"),
		}
		with self.assertRaisesRegex(release.InvalidRelease, "User"):
			release.inspect_release(_tarball(bad))

	def test_rejects_missing_package_file(self):
		bad = {k: v for k, v in PACKAGE.items() if k != "sales_rules/sales_rules.json"}
		with self.assertRaisesRegex(release.InvalidRelease, "Package"):
			release.inspect_release(_tarball(bad))

	def test_rejects_oversized_archives(self):
		with self.assertRaisesRegex(release.InvalidRelease, "too large"):
			release.inspect_release(_tarball(PACKAGE), max_bytes=10)

	def test_rejects_archives_with_too_many_members(self):
		with self.assertRaisesRegex(release.InvalidRelease, "files"):
			release.inspect_release(_tarball(PACKAGE), max_members=2)

	def test_rejects_non_gzip_input(self):
		path = Path(tempfile.mkdtemp()) / "x.tar.gz"
		path.write_bytes(b"not a tarball")
		with self.assertRaises(release.InvalidRelease):
			release.inspect_release(path)


class TestDiff(unittest.TestCase):
	def test_new_record_has_no_before(self):
		self.assertEqual(
			release.diff(None, {"label": "PO"}), [{"field": "label", "before": None, "after": "PO"}]
		)

	def test_changed_fields_only_and_metadata_ignored(self):
		before = {"label": "PO", "reqd": 0, "modified": "2026-01-01", "owner": "a", "idx": 1}
		after = {"label": "PO", "reqd": 1, "modified": "2026-10-10", "owner": "b", "idx": 2}
		self.assertEqual(release.diff(before, after), [{"field": "reqd", "before": 0, "after": 1}])

	def test_new_record_skips_empty_defaults_and_identity(self):
		after = {
			"doctype": "Custom Field",
			"name": "X-y",
			"label": "Y",
			"reqd": 0,
			"hidden": None,
			"options": "",
		}
		self.assertEqual(release.diff(None, after), [{"field": "label", "before": None, "after": "Y"}])


class TestWhatAPackageExports(unittest.TestCase):
	"""Package Release exports every DocType linked from Module Def (frappe/core/doctype/module_def)."""

	def _with(self, **doc):
		return {
			**PACKAGE,
			"sales_rules/sales_rules_module/x/x/x.json": _doc(module="Sales Rules Module", **doc),
		}

	def test_custom_doctypes_web_templates_and_themes_are_accepted(self):
		for doc in (
			{"doctype": "DocType", "name": "Delivery Slot", "custom": 1},
			{"doctype": "Web Template", "name": "Hero"},
			{"doctype": "Website Theme", "name": "Brand"},
		):
			with self.subTest(doc=doc["doctype"]):
				info = release.inspect_release(_tarball(self._with(**doc)))
				self.assertIn(doc["name"], [r["name"] for r in info["records"]])

	def test_standard_doctypes_are_refused(self):
		with self.assertRaisesRegex(release.InvalidRelease, "standard DocType"):
			release.inspect_release(_tarball(self._with(doctype="DocType", name="Sales Order", custom=0)))
