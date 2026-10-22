"""Branding resolution (branding.py): the host's site_config, then the form,
then neutral defaults; and nothing unsafe reaches the page."""

from types import SimpleNamespace

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.api import branding


def form(**values):
	return SimpleNamespace(**{field: values.get(field) for field in branding.FIELDS.values()})


class TestBranding(IntegrationTestCase):
	def setUp(self):
		for key in branding.CONF_KEYS.values():
			original = frappe.local.conf.get(key)
			self.addCleanup(self._restore, key, original)
			frappe.local.conf.pop(key, None)

	@staticmethod
	def _restore(key, original):
		if original is None:
			frappe.local.conf.pop(key, None)
		else:
			frappe.local.conf[key] = original

	def test_neutral_defaults(self):
		self.assertEqual(
			branding.resolve(form()),
			{
				"assistant_name": "AI Sidebar",
				"assistant_byline": "",
				"assistant_logo_url": "",
				"accent_color": "",
				"branding_managed": False,
			},
		)

	def test_the_sites_own_form_applies(self):
		got = branding.resolve(
			form(
				assistant_name="Wren",
				assistant_byline="from Example",
				assistant_logo="/files/mark.png",
				accent_color="#14b8a6",
			)
		)
		self.assertEqual(got["assistant_name"], "Wren")
		self.assertEqual(got["assistant_byline"], "from Example")
		self.assertEqual(got["assistant_logo_url"], "/files/mark.png")
		self.assertEqual(got["accent_color"], "#14b8a6")
		self.assertFalse(got["branding_managed"])

	def test_the_hosts_site_config_wins_and_marks_it_managed(self):
		frappe.local.conf["frappe_ai_assistant_name"] = "Hosted Name"
		frappe.local.conf["frappe_ai_assistant_accent"] = "#0f766e"
		got = branding.resolve(
			form(assistant_name="Form Name", accent_color="#ffffff", assistant_byline="kept")
		)
		self.assertEqual(got["assistant_name"], "Hosted Name")
		self.assertEqual(got["accent_color"], "#0f766e")
		self.assertEqual(got["assistant_byline"], "kept")
		self.assertTrue(got["branding_managed"])

	def test_unsafe_logo_urls_are_dropped(self):
		for bad in (
			"javascript:alert(1)",
			"//evil.example/x.png",
			"data:image/png;base64,AA",
			'https://x/"><script>',
		):
			self.assertEqual(branding.safe_logo_url(bad), "", bad)
		for good in ("https://cdn.example.com/mark.png", "http://h.local/m.svg", "/files/mark.png"):
			self.assertEqual(branding.safe_logo_url(good), good)

	def test_only_hex_colours_are_accepted(self):
		for bad in ("teal", "red; background:url(x)", "#12", "#gggggg"):
			self.assertEqual(branding.safe_accent(bad), "", bad)
		for good in ("#fff", "#14B8A6"):
			self.assertEqual(branding.safe_accent(good), good)

	def test_long_text_is_cut_to_fit_the_header(self):
		got = branding.resolve(form(assistant_name="x" * 200))
		self.assertEqual(len(got["assistant_name"]), 60)
