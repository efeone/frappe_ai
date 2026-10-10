"""FrappeForge connections: one agent user per connection, scoped to reads and proposals.

The agent user exists only to authenticate FrappeForge's API calls. It gets the
``FrappeForge Agent`` role (read access to customization DocTypes) plus any read roles the
site owner adds; the request guard (``guard.py``) blocks every write whatever those roles allow.
Keys are minted here, shown once, rotated or revoked here, and lapse at ``expires_on``.

Besides the API key, each connection gets an OAuth Bearer token for the same user (an OAuth
Client per connection, scope ``openid all``). The site's frappe-mcp-server authenticates FrappeForge
with it (``openid_profile``) and forwards it on every call, so the guard applies there too. The token
lives ``TOKEN_DAYS`` days at most and never outlives the connection; Rotate key issues a fresh one,
and revoking or expiring the connection revokes it.
"""

import ipaddress

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

AGENT_ROLE = "FrappeForge Agent"
AGENT_EMAIL_DOMAIN = "frappeforge.invalid"
#: Roles that would let a leaked key read secrets or administer the site.
PRIVILEGED_ROLES = frozenset({"Administrator", "System Manager"})
#: What the agent role may read: the records a FrappeForge customization touches.
READ_DOCTYPES = (
	"Custom Field",
	"Property Setter",
	"Client Script",
	"Server Script",
	"Print Format",
	"Notification",
	"Web Form",
	"Workspace",
	"Web Page",
	"Report",
	"Workflow",
	"Workflow State",
	"Workflow Action Master",
	"Package",
	"Module Def",
)
MAX_EXPIRY_DAYS = 365
DEFAULT_EXPIRY_DAYS = 30
_CACHE_PREFIX = "frappe_ai:frappeforge:agent:"
_CACHE_TTL = 60
_LAST_SEEN_EVERY = 300
TOKEN_SCOPES = "openid all"
TOKEN_DAYS = 1


def is_agent_user(user: str | None) -> bool:
	"""Cheap test the guard runs on every request: agent users live under their own domain."""
	return bool(user) and user.endswith("@" + AGENT_EMAIL_DOMAIN)


def ensure_agent_role() -> None:
	"""Create the agent role and its read-only permissions; cheap and idempotent (runs on migrate)."""
	changed = False
	if not frappe.db.exists("Role", AGENT_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": AGENT_ROLE, "desk_access": 1, "is_custom": 1}).insert(
			ignore_permissions=True
		)
		changed = True
	from frappe.permissions import add_permission

	read_only = {"read": 1, "write": 0, "create": 0, "delete": 0, "submit": 0, "cancel": 0, "amend": 0}
	for doctype in READ_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		key = {"parent": doctype, "role": AGENT_ROLE, "permlevel": 0}
		current = frappe.db.get_value("Custom DocPerm", key, list(read_only), as_dict=True)
		if current is None:
			add_permission(doctype, AGENT_ROLE, 0)
			current = {}
		if any(current.get(k) != v for k, v in read_only.items()):
			frappe.db.set_value("Custom DocPerm", key, read_only)
			frappe.clear_cache(doctype=doctype)
			changed = True
	if changed:
		frappe.clear_cache()


def create_connection(
	label: str,
	expires_in_days: int = DEFAULT_EXPIRY_DAYS,
	allowed_ips: str = "",
	read_roles: str = "",
) -> dict:
	"""Create a connection and its agent user; return the key pair (shown once)."""
	expires_in_days = int(expires_in_days)
	if not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
		frappe.throw(_("A connection lasts between 1 and {0} days.").format(MAX_EXPIRY_DAYS))
	ensure_agent_role()

	conn = frappe.get_doc(
		{
			"doctype": "FrappeForge Connection",
			"label": label,
			"status": "Active",
			"expires_on": add_to_date(now_datetime(), days=expires_in_days),
			"allowed_ips": allowed_ips or "",
			"read_roles": read_roles or "",
			"created_by_user": frappe.session.user,
		}
	)
	conn.insert(ignore_permissions=True)

	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"ff-{conn.name.lower()}@{AGENT_EMAIL_DOMAIN}",
			"first_name": "FrappeForge",
			"last_name": label[:100],
			"user_type": "System User",
			"send_welcome_email": 0,
			"enabled": 1,
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	conn.db_set("user", user.name)
	conn.sync_user()
	keys = _issue_keys(user.name)
	keys["access_token"], keys["token_expires_on"] = _issue_token(conn)
	return {
		"connection": conn.name,
		"user": user.name,
		"site_url": frappe.utils.get_url(),
		"expires_on": str(conn.expires_on),
		**keys,
	}


def rotate_key(name: str) -> dict:
	conn = frappe.get_doc("FrappeForge Connection", name)
	if not is_usable(conn.as_dict(), now_datetime()):
		frappe.throw(_("Only an active connection can get a new key."))
	keys = _issue_keys(conn.user)
	keys["access_token"], keys["token_expires_on"] = _issue_token(conn)
	return keys


def revoke(name: str) -> None:
	conn = frappe.get_doc("FrappeForge Connection", name)
	conn.db_set({"status": "Revoked", "revoked_by": frappe.session.user})
	_disable_user(conn.user)
	_forget(conn.user)


def expire_connections() -> None:
	"""Scheduled hourly: lapsed connections stop working even if nobody revokes them."""
	for row in frappe.get_all(
		"FrappeForge Connection",
		filters={"status": "Active", "expires_on": ("<", now_datetime())},
		fields=["name", "user"],
	):
		frappe.db.set_value("FrappeForge Connection", row.name, "status", "Expired")
		_disable_user(row.user)
		_forget(row.user)


def agent_connection(user: str) -> dict | None:
	"""``{name, status, expires_on, last_seen}`` of the connection ``user`` belongs to, cached briefly."""
	if not is_agent_user(user):
		return None
	key = _CACHE_PREFIX + user
	cached = frappe.cache.get_value(key)
	if cached is None:
		row = frappe.db.get_value(
			"FrappeForge Connection",
			{"user": user},
			["name", "status", "expires_on", "last_seen"],
			as_dict=True,
		)
		cached = (
			{
				"name": row.name,
				"status": row.status,
				"expires_on": str(row.expires_on),
				"last_seen": str(row.last_seen or ""),
			}
			if row
			else {}
		)
		frappe.cache.set_value(key, cached, expires_in_sec=_CACHE_TTL)
	if not cached:
		return None
	return {**cached, "expires_on": get_datetime(cached["expires_on"])}


def is_usable(conn: dict | None, now) -> bool:
	return bool(conn) and conn.get("status") == "Active" and get_datetime(conn.get("expires_on")) > now


def touch(conn: dict, now) -> None:
	"""Record activity at most every five minutes: thousands of calls must not mean thousands of writes."""
	last = conn.get("last_seen")
	if last and (now - get_datetime(last)).total_seconds() < _LAST_SEEN_EVERY:
		return
	frappe.db.set_value("FrappeForge Connection", conn["name"], "last_seen", now, update_modified=False)
	frappe.cache.delete_value(_CACHE_PREFIX + frappe.session.user)


def parse_ips(raw: str) -> list[str]:
	"""Addresses or dotted IPv4 prefixes (``203.0.113.``), the forms Frappe's Restrict IP matches."""
	entries = [p.strip() for p in (raw or "").replace("\n", ",").split(",") if p.strip()]
	for entry in entries:
		candidate = entry.rstrip(".")
		try:
			if entry.endswith("."):
				parts = candidate.split(".")
				if not 1 <= len(parts) <= 3 or not all(p.isdigit() and int(p) < 256 for p in parts):
					raise ValueError
			else:
				ipaddress.ip_address(candidate)
		except ValueError:
			frappe.throw(_("'{0}' is not an IP address or prefix.").format(entry))
	return entries


def parse_roles(raw: str) -> list[str]:
	roles = [r.strip() for r in (raw or "").splitlines() if r.strip()]
	for role in roles:
		if role in PRIVILEGED_ROLES:
			frappe.throw(_("The {0} role cannot be given to a FrappeForge connection.").format(role))
		if not frappe.db.exists("Role", role):
			frappe.throw(_("Role {0} does not exist.").format(role))
	return roles


def _issue_keys(user: str) -> dict:
	doc = frappe.get_doc("User", user)
	if not doc.api_key:
		doc.api_key = frappe.generate_hash(length=15)
	secret = frappe.generate_hash(length=32)
	doc.api_secret = secret
	doc.save(ignore_permissions=True)
	return {"api_key": doc.api_key, "api_secret": secret}


def _issue_token(conn) -> tuple[str, str]:
	"""A new Bearer token for the connection's user, for TOKEN_DAYS or until the connection ends;
	earlier ones stop working. Returns (token, expiry)."""
	if not conn.oauth_client or not frappe.db.exists("OAuth Client", conn.oauth_client):
		client = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": f"FrappeForge {conn.label}"[:140],
				"scopes": TOKEN_SCOPES,
				"skip_authorization": 1,
				"grant_type": "Authorization Code",
				"response_type": "Code",
				"redirect_uris": "https://frappeforge.invalid/callback",
				"default_redirect_uri": "https://frappeforge.invalid/callback",
			}
		).insert(ignore_permissions=True)
		conn.db_set("oauth_client", client.name)
	_revoke_tokens(conn.user)
	expires = min(add_to_date(now_datetime(), days=TOKEN_DAYS), get_datetime(conn.expires_on))
	token = frappe.generate_hash(length=40)
	frappe.get_doc(
		{
			"doctype": "OAuth Bearer Token",
			"access_token": token,
			"client": conn.oauth_client,
			"user": conn.user,
			"scopes": TOKEN_SCOPES,
			"status": "Active",
			"expiration_time": expires,
			"expires_in": max(int((expires - now_datetime()).total_seconds()), 0),
		}
	).insert(ignore_permissions=True)
	return token, str(expires)


def sync_token_expiry(user: str, expires_on) -> None:
	"""A shortened connection shortens its token; a renewed one does not extend it (Rotate key does)."""
	table = frappe.qb.DocType("OAuth Bearer Token")
	limit = get_datetime(expires_on)
	(
		frappe.qb.update(table)
		.set(table.expiration_time, limit)
		.where((table.user == user) & (table.status == "Active") & (table.expiration_time > limit))
	).run()


def _revoke_tokens(user: str | None) -> None:
	if user:
		frappe.db.set_value("OAuth Bearer Token", {"user": user, "status": "Active"}, "status", "Revoked")


def _disable_user(user: str | None) -> None:
	if not user or not frappe.db.exists("User", user):
		return
	_revoke_tokens(user)
	frappe.db.set_value("User", user, {"enabled": 0, "api_key": None})
	frappe.db.delete("__Auth", {"doctype": "User", "name": user, "fieldname": "api_secret"})


def _forget(user: str | None) -> None:
	if user:
		frappe.cache.delete_value(_CACHE_PREFIX + user)
