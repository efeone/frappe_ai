"""Whether the assistant is on (availability.py): the site's own switch, and a
host's site_config switch that wins over it."""

from types import SimpleNamespace

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.api import availability


class TestAvailability(IntegrationTestCase):
	def setUp(self):
		original = frappe.local.conf.get(availability.HOST_KEY)
		self.addCleanup(self._restore, original)
		frappe.local.conf.pop(availability.HOST_KEY, None)

	@staticmethod
	def _restore(original):
		if original is None:
			frappe.local.conf.pop(availability.HOST_KEY, None)
		else:
			frappe.local.conf[availability.HOST_KEY] = original

	def test_the_sites_own_switch_applies_when_the_host_says_nothing(self):
		self.assertTrue(availability.is_enabled(SimpleNamespace(enabled=1)))
		self.assertFalse(availability.is_enabled(SimpleNamespace(enabled=0)))
		self.assertFalse(availability.switched_off_by_host())

	def test_a_host_can_turn_it_off_whatever_the_form_says(self):
		for off in (0, "0", False, "false", "False"):
			frappe.local.conf[availability.HOST_KEY] = off
			self.assertTrue(availability.switched_off_by_host(), off)
			self.assertFalse(availability.is_enabled(SimpleNamespace(enabled=1)), off)

	def test_a_host_saying_on_does_not_override_the_sites_own_off(self):
		frappe.local.conf[availability.HOST_KEY] = 1
		self.assertFalse(availability.switched_off_by_host())
		self.assertFalse(availability.is_enabled(SimpleNamespace(enabled=0)))
		self.assertTrue(availability.is_enabled(SimpleNamespace(enabled=1)))

	def test_settings_report_a_host_switch_so_admins_get_no_enable_hint(self):
		from frappe_ai.api import get_settings

		frappe.local.conf[availability.HOST_KEY] = 0
		settings = get_settings()
		self.assertFalse(settings["enabled"])
		self.assertTrue(settings["enabled_managed"])

	def test_chat_is_refused_while_the_host_has_it_off(self):
		from frappe_ai.api import chat

		frappe.local.conf[availability.HOST_KEY] = 0
		with self.assertRaises(frappe.ValidationError) as caught:
			chat.start_stream(message="hello")
		self.assertIn("turned off", str(caught.exception))
