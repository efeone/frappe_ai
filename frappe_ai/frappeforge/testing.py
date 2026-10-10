"""Shared test setup for FrappeForge tests."""

import frappe


def allow_many_users() -> None:
	"""Frappe refuses more than 60 new users an hour per site; tests create one per connection."""
	frappe.local.conf["throttle_user_limit"] = 100_000
