"""Proposals, approval, application and rollback of FrappeForge releases on this site.

The FrappeForge agent can only call ``propose`` (and ``status`` / ``request_rollback``). A System
Manager approves in Desk; the release is then applied here by a background job running with the
approver's authority: the Package Import, the Workflow records that packages do not carry, and one
``FrappeForge Change Log`` row per record with the version before and after. ``rollback`` restores
a row's previous version (or deletes what the release created).
"""

import json
import re
import tempfile
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import add_to_date, get_url_to_form, now_datetime

from frappe_ai.frappeforge import connection, release

PROPOSAL = "Vyogo Forge AI Proposed Release"
CHANGE_LOG = "Vyogo Forge AI Change Log"
#: Records the agent may send alongside a package because packages cannot carry them
#: (Package Release exports only the DocTypes linked from Module Def).
EXTRA_DOCTYPES = {
	"Workflow": "workflow_name",
	"Workflow State": "workflow_state_name",
	"Workflow Action Master": "workflow_action_name",
	"Report": "report_name",
}
MAX_PENDING_PER_CONNECTION = 50
DEFAULT_PROPOSAL_TTL_DAYS = 14
_VERSION = re.compile(r"-(\d+\.\d+\.\d+)\.tar\.gz$")
_SAVE_METADATA = {"name", "doctype", "creation", "modified", "modified_by", "owner", "idx", "docstatus"}


# ── agent side ────────────────────────────────────────────────────────────────


def propose(
	content: bytes,
	filename: str,
	summary: str = "",
	conversation_id: str = "",
	idempotency_key: str = "",
	extra_records: list | None = None,
) -> dict:
	"""Store a release for a System Manager to review; nothing on the site changes yet."""
	conn = _caller_connection()
	key = f"{conn['name']}:{idempotency_key}" if idempotency_key else None
	if key and (existing := frappe.db.get_value(PROPOSAL, {"idempotency_key": key}, "name")):
		return _proposal_summary(existing)
	pending = frappe.db.count(PROPOSAL, {"connection": conn["name"], "status": "Pending"})
	if pending >= MAX_PENDING_PER_CONNECTION:
		frappe.throw(_("This connection already has {0} releases waiting for approval.").format(pending))

	max_bytes = int(frappe.local.conf.get("frappeforge_max_release_mb") or 20) * 1024 * 1024
	info = _inspect_bytes(content, max_bytes)
	extras = _validate_extra_records(extra_records)
	version = (m.group(1) if (m := _VERSION.search(filename or "")) else "") or "unversioned"

	doc = frappe.get_doc(
		{
			"doctype": PROPOSAL,
			"package": info["package"],
			"version": version,
			"status": "Pending",
			"has_executable": int(info["has_executable"]),
			"connection": conn["name"],
			"proposed_by": frappe.session.user,
			"conversation_id": (conversation_id or "")[:140],
			"idempotency_key": key,
			"summary": (summary or "")[:5000],
			"records_json": json.dumps(_with_actions(info["records"]), indent=1, default=str),
			"extra_records_json": json.dumps(extras, indent=1) if extras else None,
		}
	)
	# The lookup above and this insert are not atomic: a retry racing the first request can get here
	# too. The unique idempotency key lets exactly one insert win; the other returns that proposal.
	frappe.db.savepoint("frappeforge_propose")
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		if not key:
			raise
		frappe.db.rollback(save_point="frappeforge_propose")
		# a locking read sees the winner's committed row; this transaction's snapshot (REPEATABLE READ) does not
		return _proposal_summary(
			frappe.db.get_value(PROPOSAL, {"idempotency_key": key}, "name", for_update=True), fresh=True
		)
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{info['package']}-{version}.tar.gz",
			"attached_to_doctype": PROPOSAL,
			"attached_to_name": doc.name,
			"is_private": 1,
			"content": content,
		}
	).insert(ignore_permissions=True)
	doc.db_set("release_file", file.file_url)
	return _proposal_summary(doc.name)


def status() -> dict:
	"""What the calling agent's connection proposed and what became of it."""
	conn = _caller_connection(require_usable=False)
	proposals = frappe.get_all(
		PROPOSAL,
		filters={"connection": conn["name"]},
		fields=[
			"name",
			"package",
			"version",
			"status",
			"approved_at",
			"rejected_reason",
			"error",
			"creation",
		],
		order_by="creation desc",
		limit=20,
	)
	changes = frappe.get_all(
		CHANGE_LOG,
		filters={"proposed_release": ("in", [p.name for p in proposals] or [""])},
		fields=[
			"name",
			"proposed_release",
			"action",
			"status",
			"target_doctype",
			"target_name",
			"rollback_requested",
		],
	)
	for p in proposals:
		p["changes"] = [c for c in changes if c.proposed_release == p.name]
	return {
		"site_url": frappe.utils.get_url(),
		"frappe_ai_version": frappe.get_attr("frappe_ai.__version__"),
		"connection": {"name": conn["name"], "status": conn["status"], "expires_on": str(conn["expires_on"])},
		"proposals": proposals,
	}


def request_rollback(change: str) -> None:
	"""Ask the site owner to roll a change back; the agent cannot do it itself."""
	conn = _caller_connection()
	row = frappe.db.get_value(CHANGE_LOG, change, ["connection", "status"], as_dict=True)
	if not row or row.connection != conn["name"]:
		raise frappe.PermissionError(_("This change was not made through your connection."))
	if row.status != "Applied":
		frappe.throw(_("Change {0} is already rolled back.").format(change))
	frappe.db.set_value(CHANGE_LOG, change, "rollback_requested", 1)
	frappe.get_doc(CHANGE_LOG, change).add_comment(
		"Comment", _("Vyogo Forge AI asked for this change to be rolled back.")
	)


# ── site owner side ───────────────────────────────────────────────────────────


def approve(name: str) -> dict:
	frappe.only_for("System Manager")
	row = frappe.db.get_value(PROPOSAL, name, ["status", "connection"], as_dict=True, for_update=True)
	if not row:
		frappe.throw(_("Proposed release {0} not found.").format(name), frappe.DoesNotExistError)
	if row.status != "Pending":
		frappe.throw(_("Only a pending release can be approved; this one is {0}.").format(row.status))
	settings = frappe.get_single("AI Assistant Settings")
	conn = {"status": settings.forge_status, "expires_on": settings.forge_expires_on}
	if not connection.is_usable(conn, now_datetime()):
		frappe.throw(_("The connection that proposed this release is revoked or expired."))
	frappe.db.set_value(
		PROPOSAL,
		name,
		{"status": "Approved", "approved_by": frappe.session.user, "approved_at": now_datetime()},
	)
	frappe.enqueue(
		"frappe_ai.frappeforge.deploy.apply_release",
		queue="long",
		job_id=f"frappeforge-apply-{name}",
		deduplicate=True,
		enqueue_after_commit=True,
		now=bool(frappe.flags.in_test),
		name=name,
	)
	return _proposal_summary(name)


def reject(name: str, reason: str = "") -> None:
	frappe.only_for("System Manager")
	current = frappe.db.get_value(PROPOSAL, name, "status", for_update=True)
	if current != "Pending":
		frappe.throw(_("Only a pending release can be rejected; this one is {0}.").format(current))
	frappe.db.set_value(PROPOSAL, name, {"status": "Rejected", "rejected_reason": (reason or "")[:1000]})


def apply_release(name: str) -> None:
	"""Background job: import an approved release and log every record it touched."""
	doc = frappe.get_doc(PROPOSAL, name)
	if doc.status != "Approved":
		return
	frappe.set_user(doc.approved_by)
	doc.db_set("status", "Applying")
	frappe.db.savepoint("frappeforge_apply")
	try:
		info = release.inspect_release(_release_path(doc.release_file))
		extras = json.loads(doc.extra_records_json or "[]")
		targets = [(r["doctype"], r["name"]) for r in info["records"]]
		targets += [(e["doctype"], _extra_name(e)) for e in extras]
		before = {t: _snapshot(*t) for t in targets}

		log = _import_package(doc, info)
		for extra in extras:
			_upsert(extra)
		for target in targets:
			_log_change(doc, target, before[target], _snapshot(*target))
		doc.db_set({"status": "Applied", "import_log": log, "error": None})
	except Exception as exc:
		frappe.db.rollback(save_point="frappeforge_apply")
		frappe.log_error(title=f"Vyogo Forge AI release {name} failed")
		doc.db_set({"status": "Failed", "error": str(exc)[:1000]})


def rollback(change: str) -> str:
	"""Restore the version a change replaced, or delete what it created; returns the rollback row."""
	frappe.only_for("System Manager")
	row = frappe.get_doc(CHANGE_LOG, change)
	if row.status != "Applied" or row.action == "Rollback":
		frappe.throw(_("Change {0} cannot be rolled back (it is {1}).").format(change, row.status))
	target = (row.target_doctype, row.target_name)
	current = _snapshot(*target)
	previous = json.loads(row.before_json) if row.before_json else None
	if previous is None:
		if current is not None:
			frappe.delete_doc(*target, ignore_permissions=True, force=True)
	else:
		_restore(target, previous)
	undo = frappe.get_doc(
		{
			"doctype": CHANGE_LOG,
			"proposed_release": row.proposed_release,
			"connection": row.connection,
			"action": "Rollback",
			"status": "Applied",
			"target_doctype": row.target_doctype,
			"target_name": row.target_name,
			"approved_by": frappe.session.user,
			"applied_at": now_datetime(),
			"rollback_of": row.name,
			"before_json": _dump(current),
			"after_json": _dump(_snapshot(*target)),
		}
	).insert(ignore_permissions=True, ignore_links=True)
	row.db_set({"status": "Rolled Back", "rolled_back_by": undo.name})
	return undo.name


def preview(doc) -> list[dict]:
	"""The proposal's records diffed against the site as it is now (shown on the approval form)."""
	out = []
	for record in json.loads(doc.records_json or "[]"):
		current = _snapshot(record["doctype"], record["name"])
		out.append(
			{
				"doctype": record["doctype"],
				"name": record["name"],
				"executable": record.get("executable"),
				"action": "Update" if current else "Create",
				"changes": release.diff(current, record["data"]),
			}
		)
	return out


def expire_proposals() -> None:
	"""Scheduled hourly: proposals nobody answered stop being approvable."""
	ttl = int(frappe.local.conf.get("frappeforge_proposal_ttl_days") or DEFAULT_PROPOSAL_TTL_DAYS)
	table = frappe.qb.DocType(PROPOSAL)
	(
		frappe.qb.update(table)
		.set(table.status, "Expired")
		.where((table.status == "Pending") & (table.creation < add_to_date(now_datetime(), days=-ttl)))
	).run()


# ── helpers ───────────────────────────────────────────────────────────────────


def _caller_connection(require_usable: bool = True) -> dict:
	user = frappe.session.user
	conn = connection.agent_connection(user) if connection.is_agent_user(user) else None
	if not conn:
		raise frappe.PermissionError(_("Only a Vyogo Forge AI connection can do this."))
	if require_usable and not connection.is_usable(conn, now_datetime()):
		raise frappe.PermissionError(_("This Vyogo Forge AI connection is revoked or expired."))
	return conn


def _proposal_summary(name: str, fresh: bool = False) -> dict:
	"""``fresh``: a locking read, for a row another transaction committed after this one began."""
	row = frappe.db.get_value(
		PROPOSAL, name, ["name", "package", "version", "status"], as_dict=True, for_update=fresh
	)
	return {**row, "approve_url": get_url_to_form(PROPOSAL, name)}


def _inspect_bytes(content: bytes, max_bytes: int) -> dict:
	if not content:
		frappe.throw(_("No release file was sent."))
	with tempfile.TemporaryDirectory() as tmp:
		path = Path(tmp) / "release.tar.gz"
		path.write_bytes(content)
		try:
			return release.inspect_release(path, max_bytes=max_bytes)
		except release.InvalidRelease as exc:
			frappe.throw(_("Invalid release: {0}").format(exc))
			raise  # for the type checker: frappe.throw never returns


def _with_actions(records: list[dict]) -> list[dict]:
	return [
		{**r, "action": "Update" if frappe.db.exists(r["doctype"], r["name"]) else "Create"} for r in records
	]


def _validate_extra_records(extra_records) -> list[dict]:
	if isinstance(extra_records, str):
		extra_records = json.loads(extra_records or "[]")
	records = list(extra_records or [])
	if len(records) > 200:
		frappe.throw(_("At most 200 records outside the package per release."))
	for record in records:
		if not isinstance(record, dict) or record.get("doctype") not in EXTRA_DOCTYPES:
			frappe.throw(
				_("Only {0} records can be sent outside the package.").format(", ".join(EXTRA_DOCTYPES))
			)
		if not _extra_name(record):
			frappe.throw(_("A {0} record needs a name.").format(record["doctype"]))
	return records


def _extra_name(record: dict) -> str:
	return record.get("name") or record.get(EXTRA_DOCTYPES[record["doctype"]]) or ""


def _release_path(file_url: str) -> Path:
	return Path(frappe.get_doc("File", {"file_url": file_url}).get_full_path())


def _import_package(doc, info: dict) -> str:
	if not frappe.db.exists("Package", info["package"]):
		frappe.get_doc(
			{"doctype": "Package", "name": info["package"], "package_name": info["package"]}
		).insert(ignore_permissions=True)
	for module in info["modules"]:
		if not frappe.db.exists("Module Def", module):
			frappe.get_doc(
				{
					"doctype": "Module Def",
					"module_name": module,
					"custom": 1,
					"app_name": "frappe",
					"package": info["package"],
				}
			).insert(ignore_permissions=True)
	package_import = frappe.get_doc({"doctype": "Package Import"}).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{info['package']}-{doc.version}.tar.gz",
			"attached_to_doctype": "Package Import",
			"attached_to_name": package_import.name,
			"is_private": 1,
			"content": _release_path(doc.release_file).read_bytes(),
		}
	).insert(ignore_permissions=True)
	package_import.activate = 1
	package_import.force = 1
	package_import.save(ignore_permissions=True)
	return package_import.log or ""


def _snapshot(doctype: str, name: str) -> dict | None:
	if not name or not frappe.db.exists(doctype, name):
		return None
	return json.loads(frappe.as_json(frappe.get_doc(doctype, name).as_dict()))


def _upsert(record: dict) -> None:
	name = _extra_name(record)
	data = {k: v for k, v in record.items() if k not in _SAVE_METADATA}
	if frappe.db.exists(record["doctype"], name):
		doc = frappe.get_doc(record["doctype"], name)
		doc.update(data)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({"doctype": record["doctype"], **data}).insert(ignore_permissions=True)


def _restore(target: tuple[str, str], previous: dict) -> None:
	data = {k: v for k, v in previous.items() if k not in _SAVE_METADATA and not k.startswith("_")}
	if frappe.db.exists(*target):
		doc = frappe.get_doc(*target)
		doc.update(data)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({"doctype": target[0], "name": target[1], **data}).insert(ignore_permissions=True)


def _log_change(doc, target: tuple[str, str], before: dict | None, after: dict | None) -> None:
	if before == after:
		return
	frappe.get_doc(
		{
			"doctype": CHANGE_LOG,
			"proposed_release": doc.name,
			"connection": doc.connection,
			"action": "Create" if before is None else "Update",
			"status": "Applied",
			"target_doctype": target[0],
			"target_name": target[1],
			"approved_by": doc.approved_by,
			"applied_at": now_datetime(),
			"before_json": _dump(before),
			"after_json": _dump(after),
		}
	).insert(ignore_permissions=True, ignore_links=True)


def _dump(value) -> str | None:
	return json.dumps(value, indent=1, default=str) if value is not None else None


#: Fields whose value is code or markup: always shown in full, never summarised.
_CODE_FIELDS = frozenset(
	{
		"script",
		"html",
		"query",
		"report_script",
		"javascript",
		"condition",
		"message",
		"response",
		"custom_css",
		"css",
	}
)


def render_changes_html(doc) -> str:
	"""The approver's view, built on the server from the stored tarball contents, all escaped."""
	from frappe.utils import escape_html as esc

	parts = []
	for change in preview(doc):
		badge = "orange" if change["action"] == "Update" else "green"
		head = (
			f'<div class="frappeforge-record" style="margin-bottom:12px">'
			f'<span class="indicator-pill {badge}">{esc(change["action"])}</span> '
			f"<b>{esc(change['doctype'])}</b> <code>{esc(change['name'])}</code>"
		)
		if change["executable"]:
			head += ' <span class="indicator-pill red">runs code</span>'
		rows = []
		for c in change["changes"]:
			if c["field"] in _CODE_FIELDS:
				value = f"<pre style='white-space:pre-wrap'>{esc(str(c['after'] or ''))}</pre>"
			else:
				before = "" if c["before"] is None else esc(_short(c["before"]))
				value = (f"<s>{before}</s> → " if before else "") + esc(_short(c["after"]))
			rows.append(f"<tr><td><code>{esc(c['field'])}</code></td><td>{value}</td></tr>")
		table = f"<table class='table table-bordered table-sm'>{''.join(rows)}</table>" if rows else ""
		parts.append(head + table + "</div>")
	for extra in json.loads(doc.extra_records_json or "[]"):
		exists = frappe.db.exists(extra["doctype"], _extra_name(extra))
		parts.append(
			f'<div><span class="indicator-pill {"orange" if exists else "green"}">'
			f"{'Update' if exists else 'Create'}</span> <b>{esc(extra['doctype'])}</b> "
			f"<code>{esc(_extra_name(extra))}</code> (applied after the package)"
			f"<pre style='white-space:pre-wrap'>{esc(json.dumps(extra, indent=1))}</pre></div>"
		)
	return "".join(parts) or "<p>This release changes nothing on this site.</p>"


def _short(value, limit: int = 300) -> str:
	text = value if isinstance(value, str) else json.dumps(value, default=str)
	return text if len(text) <= limit else text[:limit] + "…"
