"""Vyogo Forge AI connections: one agent user per connection, scoped to reads and proposals.

The agent user exists only to authenticate Vyogo Forge AI's API calls. It gets the
``Vyogo Forge AI Agent`` role (read access to customization DocTypes) plus any read roles the
site owner adds; the request guard (``guard.py``) blocks every write whatever those roles allow.
Keys are minted here, shown once, rotated or revoked here, and lapse at ``forge_expires_on`` in
``AI Assistant Settings``.

Besides the API key, the connection gets an OAuth Bearer token for the same user (an OAuth
Client, scope ``openid all``). The site's frappe-mcp-server authenticates Vyogo Forge AI
with it (``openid_profile``) and forwards it on every call, so the guard applies there too. The token
lives ``TOKEN_DAYS`` days at most and never outlives the connection; Rotate key issues a fresh one,
and revoking or expiring the connection revokes it.
"""

import ipaddress

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

AGENT_ROLE = "Vyogo Forge AI Agent"
AGENT_EMAIL_DOMAIN = "vyogoforge.invalid"
#: Roles that would let a leaked key read secrets or administer the site.
PRIVILEGED_ROLES = frozenset({"Administrator", "System Manager"})
#: What the agent role may read: the records a Vyogo Forge AI customization touches.
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
_CACHE_PREFIX = "frappe_ai:vyogo_forge:agent:"
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
	"""Create/activate a connection in AI Assistant Settings and return the key pair (shown once)."""
	expires_in_days = int(expires_in_days)
	if not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
		frappe.throw(_("A connection lasts between 1 and {0} days.").format(MAX_EXPIRY_DAYS))
	ensure_agent_role()

	settings = frappe.get_single("AI Assistant Settings")
	expires_on = add_to_date(now_datetime(), days=expires_in_days)

	user_email = f"forge-agent@{AGENT_EMAIL_DOMAIN}"
	if not frappe.db.exists("User", user_email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": user_email,
				"first_name": "Vyogo Forge AI",
				"last_name": label[:100],
				"user_type": "System User",
				"send_welcome_email": 0,
				"enabled": 1,
			}
		)
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", user_email)
		user.enabled = 1
		user.first_name = "Vyogo Forge AI"
		user.last_name = label[:100]
		user.save(ignore_permissions=True)

	settings.forge_status = "Active"
	settings.forge_label = label
	settings.forge_expires_on = expires_on
	settings.forge_allowed_ips = allowed_ips or ""
	settings.forge_read_roles = read_roles or ""
	settings.forge_user = user.name
	settings.forge_created_by = frappe.session.user
	settings.forge_revoked_by = None
	settings.flags.ignore_permissions = True
	settings.save()
	settings.sync_forge_user()

	keys = _issue_keys(user.name)
	keys["access_token"], keys["token_expires_on"] = _issue_token(settings)
	return {
		"connection": "AI Assistant Settings",
		"user": user.name,
		"site_url": frappe.utils.get_url(),
		"expires_on": str(settings.forge_expires_on),
		**keys,
	}


def rotate_key(name: str = "") -> dict:
	settings = frappe.get_single("AI Assistant Settings")
	conn_dict = {
		"status": settings.forge_status,
		"expires_on": settings.forge_expires_on,
		"user": settings.forge_user,
	}
	if not is_usable(conn_dict, now_datetime()):
		frappe.throw(_("Only an active connection can get a new key."))
	keys = _issue_keys(settings.forge_user)
	keys["access_token"], keys["token_expires_on"] = _issue_token(settings)
	return keys


def revoke(name: str = "") -> None:
	settings = frappe.get_single("AI Assistant Settings")
	settings.forge_status = "Revoked"
	settings.forge_revoked_by = frappe.session.user
	settings.flags.ignore_permissions = True
	settings.save()
	_disable_user(settings.forge_user)
	_forget(settings.forge_user)


def expire_connections() -> None:
	"""Scheduled hourly: lapsed connections stop working even if nobody revokes them."""
	settings = frappe.get_single("AI Assistant Settings")
	if (
		settings.forge_status == "Active"
		and settings.forge_expires_on
		and get_datetime(settings.forge_expires_on) < now_datetime()
	):
		settings.forge_status = "Expired"
		settings.flags.ignore_permissions = True
		settings.save()
		_disable_user(settings.forge_user)
		_forget(settings.forge_user)


def agent_connection(user: str) -> dict | None:
	"""``{name, status, expires_on, last_seen}`` of the connection ``user`` belongs to, cached briefly."""
	if not is_agent_user(user):
		return None
	key = _CACHE_PREFIX + user
	cached = frappe.cache.get_value(key)
	if cached is None:
		settings = frappe.get_single("AI Assistant Settings")
		if settings.forge_user == user:
			cached = {
				"name": "AI Assistant Settings",
				"status": settings.forge_status or "Disconnected",
				"expires_on": str(settings.forge_expires_on or ""),
				"last_seen": str(settings.forge_last_seen or ""),
			}
		else:
			cached = {}
		frappe.cache.set_value(key, cached, expires_in_sec=_CACHE_TTL)
	if not cached:
		return None
	return {
		**cached,
		"expires_on": get_datetime(cached["expires_on"]) if cached.get("expires_on") else None,
	}


def is_usable(conn: dict | None, now) -> bool:
	return (
		bool(conn)
		and conn.get("status") == "Active"
		and conn.get("expires_on") is not None
		and get_datetime(conn.get("expires_on")) > now
	)


def touch(conn: dict, now) -> None:
	"""Record activity at most every five minutes: thousands of calls must not mean thousands of writes."""
	last = conn.get("last_seen")
	if last and (now - get_datetime(last)).total_seconds() < _LAST_SEEN_EVERY:
		return
	frappe.db.set_single_value("AI Assistant Settings", "forge_last_seen", now)
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
			frappe.throw(_("The {0} role cannot be given to a Vyogo Forge AI connection.").format(role))
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


def _issue_token(settings) -> tuple[str, str]:
	"""A new Bearer token for the connection's user, for TOKEN_DAYS or until the connection ends;
	earlier ones stop working. Returns (token, expiry)."""
	oauth_client = getattr(settings, "forge_oauth_client", None)
	if not oauth_client or not frappe.db.exists("OAuth Client", oauth_client):
		client = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": f"Vyogo Forge AI {settings.forge_label or ''}"[:140],
				"scopes": TOKEN_SCOPES,
				"skip_authorization": 1,
				"grant_type": "Authorization Code",
				"response_type": "Code",
				"redirect_uris": "https://vyogoforge.invalid/callback",
				"default_redirect_uri": "https://vyogoforge.invalid/callback",
			}
		).insert(ignore_permissions=True)
		settings.db_set("forge_oauth_client", client.name)
		oauth_client = client.name
	_revoke_tokens(settings.forge_user)
	expires = min(add_to_date(now_datetime(), days=TOKEN_DAYS), get_datetime(settings.forge_expires_on))
	token = frappe.generate_hash(length=40)
	frappe.get_doc(
		{
			"doctype": "OAuth Bearer Token",
			"access_token": token,
			"client": oauth_client,
			"user": settings.forge_user,
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
