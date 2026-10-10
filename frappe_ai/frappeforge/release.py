"""Read a Frappe Package release tarball without trusting whoever sent it.

A Package Release (``frappe.core.doctype.package_release``) is a ``.tar.gz`` holding
``<package>/<package>.json`` and one exported JSON file per record
(``<package>/<module>/<doctype>/<name>/<name>.json``). The approver's view, the before/after
change log and the import itself are all derived from these files.
"""

import json
import tarfile
from pathlib import PurePosixPath

#: Records a FrappeForge release may carry: what a Package Release exports (the DocTypes linked from
#: Module Def), minus anything that grants access. DocType only when custom (``custom: 1``).
ALLOWED_DOCTYPES = frozenset(
	{
		"DocType",
		"Custom Field",
		"Property Setter",
		"Client Script",
		"Server Script",
		"Print Format",
		"Notification",
		"Web Form",
		"Web Page",
		"Web Template",
		"Website Theme",
		"Workspace",
	}
)
#: Records whose content runs code on this site or in users' browsers: shown in full to the approver.
EXECUTABLE_DOCTYPES = frozenset(
	{"Server Script", "Client Script", "Web Form", "Notification", "Website Theme", "Web Template"}
)
#: Fields that change on every save and say nothing about the customization.
_METADATA = frozenset(
	{
		"modified",
		"creation",
		"owner",
		"modified_by",
		"idx",
		"docstatus",
		"_user_tags",
		"_comments",
		"_assign",
		"_liked_by",
	}
)

DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_MEMBERS = 2000
#: A single record file larger than this is not a customization.
_MAX_MEMBER_BYTES = 5 * 1024 * 1024


class InvalidRelease(ValueError):
	"""The tarball is not a release this site will consider."""


def inspect_release(
	path, *, max_bytes: int = DEFAULT_MAX_BYTES, max_members: int = DEFAULT_MAX_MEMBERS
) -> dict:
	"""Return ``{package, modules, records, has_executable}``; raise ``InvalidRelease`` otherwise.

	Each record is ``{doctype, name, module, executable, data}`` with ``data`` the exported document.
	"""
	try:
		size = path.stat().st_size
	except OSError as exc:
		raise InvalidRelease(f"cannot read the release: {exc}") from exc
	if size > max_bytes:
		raise InvalidRelease(f"release is too large ({size} bytes, limit {max_bytes})")

	try:
		tar = tarfile.open(path, "r:gz")
	except (tarfile.TarError, OSError, EOFError) as exc:
		raise InvalidRelease("not a gzip tar archive") from exc

	package_doc = None
	records: list[dict] = []
	with tar:
		members = tar.getmembers()
		if len(members) > max_members:
			raise InvalidRelease(f"release has {len(members)} files, limit {max_members}")
		for member in members:
			name = PurePosixPath(member.name)
			if member.issym() or member.islnk() or member.isdev() or member.isfifo():
				raise InvalidRelease(f"release contains a link or special file: {member.name}")
			if name.is_absolute() or ".." in name.parts:
				raise InvalidRelease(f"release contains a path outside the package: {member.name}")
			if member.isdir():
				continue
			if not member.isfile() or name.suffix != ".json":
				continue
			if member.size > _MAX_MEMBER_BYTES:
				raise InvalidRelease(f"{member.name} is too large")
			doc = _read_json(tar, member)
			if len(name.parts) == 2 and doc.get("doctype") == "Package":
				package_doc = doc
				continue
			doctype = doc.get("doctype")
			if doctype not in ALLOWED_DOCTYPES:
				raise InvalidRelease(
					f"release contains a {doctype or 'document without doctype'} record, which is not allowed"
				)
			if not doc.get("name"):
				raise InvalidRelease(f"{member.name} has no name")
			if doctype == "DocType" and not doc.get("custom"):
				raise InvalidRelease(
					f"{doc['name']} is a standard DocType; only custom DocTypes can be released"
				)
			records.append(
				{
					"doctype": doctype,
					"name": doc["name"],
					"module": doc.get("module"),
					"executable": doctype in EXECUTABLE_DOCTYPES,
					"data": doc,
				}
			)

	if not package_doc:
		raise InvalidRelease("release has no Package file (<package>/<package>.json)")
	records.sort(key=lambda r: (r["doctype"], r["name"]))
	return {
		"package": package_doc.get("package_name") or package_doc.get("name"),
		"modules": sorted({r["module"] for r in records if r["module"]}),
		"records": records,
		"has_executable": any(r["executable"] for r in records),
	}


def _read_json(tar: tarfile.TarFile, member: tarfile.TarInfo) -> dict:
	handle = tar.extractfile(member)
	if handle is None:
		raise InvalidRelease(f"cannot read {member.name}")
	try:
		doc = json.loads(handle.read())
	except ValueError as exc:
		raise InvalidRelease(f"{member.name} is not valid JSON") from exc
	if not isinstance(doc, dict):
		raise InvalidRelease(f"{member.name} is not a document")
	return doc


def diff(before: dict | None, after: dict) -> list[dict]:
	"""Fields whose value the release changes, ignoring save metadata and child-row bookkeeping."""
	is_new = before is None
	before = before or {}
	changes = []
	for field in sorted(set(before) | set(after)):
		if field in _METADATA or field.startswith("_") or field not in after:
			continue
		old, new = before.get(field), after.get(field)
		if is_new and (field in ("doctype", "name") or new in (None, "", 0, [])):
			continue  # a new record's identity and empty defaults say nothing to the approver
		if old != new:
			changes.append({"field": field, "before": old, "after": new})
	return changes
