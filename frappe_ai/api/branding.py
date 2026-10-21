"""How the assistant presents itself: name, byline, logo and accent colour.

Two sources, in order:

1. site_config, for a host that manages the site (a hosting provider setting
   its own brand on every site it runs). These win and cannot be changed from
   the site's own settings form:

	frappe_ai_assistant_name      e.g. "Acme Assistant"
	frappe_ai_assistant_byline    a short line under the name, e.g. "from Example"
	frappe_ai_assistant_logo_url  an http(s) or site-relative image URL
	frappe_ai_assistant_accent    a hex colour, e.g. "#14b8a6"

2. AI Assistant Settings → Branding, for a site that brands itself.

With neither, the sidebar keeps its neutral defaults. Values that are not a
safe URL or a hex colour are ignored rather than rendered.
"""

import re

import frappe

DEFAULT_NAME = "AI Sidebar"

CONF_KEYS = {
	"name": "frappe_ai_assistant_name",
	"byline": "frappe_ai_assistant_byline",
	"logo_url": "frappe_ai_assistant_logo_url",
	"accent": "frappe_ai_assistant_accent",
}
FIELDS = {
	"name": "assistant_name",
	"byline": "assistant_byline",
	"logo_url": "assistant_logo",
	"accent": "accent_color",
}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_MAX_TEXT = 60


def _text(value) -> str:
	return str(value or "").strip()[:_MAX_TEXT]


def safe_logo_url(value) -> str:
	"""An image URL fit for an <img src>: http(s) or a path on this site."""
	url = str(value or "").strip()
	if not url:
		return ""
	if url.startswith("/") and not url.startswith("//"):
		return url
	if re.match(r"^https?://[^\s\"'<>]+$", url, re.IGNORECASE):
		return url
	return ""


def safe_accent(value) -> str:
	colour = str(value or "").strip()
	return colour if _HEX.match(colour) else ""


def resolve(settings=None) -> dict:
	"""The effective branding, plus whether a host manages it."""
	conf = frappe.local.conf
	managed = any(conf.get(key) for key in CONF_KEYS.values())
	if settings is None:
		settings = frappe.get_single("AI Assistant Settings")

	def pick(kind):
		from_conf = conf.get(CONF_KEYS[kind])
		if from_conf not in (None, ""):
			return from_conf
		return getattr(settings, FIELDS[kind], None)

	return {
		"assistant_name": _text(pick("name")) or DEFAULT_NAME,
		"assistant_byline": _text(pick("byline")),
		"assistant_logo_url": safe_logo_url(pick("logo_url")),
		"accent_color": safe_accent(pick("accent")),
		"branding_managed": managed,
	}
