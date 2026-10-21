"""The relay fails fast and says why: an unreachable agent errors within the
connect timeout, and each failure reaches the sidebar as a readable message."""

from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from frappe_ai.api import chat


def run_relay():
	"""One turn through the background relay; returns the chunks it published."""
	published = []
	with patch.object(
		frappe, "publish_realtime", side_effect=lambda event, chunk, **kw: published.append(chunk)
	):
		chat._stream_to_agent(
			message="Show open invoices over 30 days",
			session_id="failfast-session",
			user="Administrator",
			sid="sid",
			agent_url="http://agent.test:8484",
			timeout_seconds=120,
		)
	return published


def errors(published):
	return [c for c in published if c.get("type") == "error"]


class TestRelayFailsFast(FrappeTestCase):
	def setUp(self):
		self._conf = frappe.local.conf.get("frappe_ai_agent_connect_timeout")
		frappe.local.conf.pop("frappe_ai_agent_connect_timeout", None)

	def tearDown(self):
		if self._conf is None:
			frappe.local.conf.pop("frappe_ai_agent_connect_timeout", None)
		else:
			frappe.local.conf["frappe_ai_agent_connect_timeout"] = self._conf

	def test_connect_and_read_timeouts_are_separate(self):
		response = MagicMock()
		response.__enter__.return_value = response
		response.iter_lines.return_value = iter(['data: {"type": "done", "tools_called": []}'])
		with patch.object(chat.requests, "post", return_value=response) as post:
			run_relay()
		self.assertEqual(post.call_args.kwargs["timeout"], (chat.DEFAULT_AGENT_CONNECT_TIMEOUT, 120))

	def test_the_connect_timeout_is_configurable_per_site(self):
		frappe.local.conf["frappe_ai_agent_connect_timeout"] = 2
		self.assertEqual(chat._agent_connect_timeout(), 2.0)
		frappe.local.conf["frappe_ai_agent_connect_timeout"] = "nonsense"
		self.assertEqual(chat._agent_connect_timeout(), chat.DEFAULT_AGENT_CONNECT_TIMEOUT)
		frappe.local.conf["frappe_ai_agent_connect_timeout"] = 0
		self.assertEqual(chat._agent_connect_timeout(), chat.DEFAULT_AGENT_CONNECT_TIMEOUT)

	def test_an_unreachable_agent_says_so(self):
		for exc in (
			requests.exceptions.ConnectionError("refused"),
			requests.exceptions.ConnectTimeout("slow"),
		):
			with patch.object(chat.requests, "post", side_effect=exc):
				published = run_relay()
			self.assertEqual([e["message"] for e in errors(published)], [chat.AGENT_UNREACHABLE_MESSAGE], exc)

	def test_a_slow_answer_names_the_limit(self):
		with patch.object(chat.requests, "post", side_effect=requests.exceptions.ReadTimeout("slow")):
			published = run_relay()
		self.assertIn("within 120 seconds", errors(published)[0]["message"])

	def test_an_agent_error_status_is_reported(self):
		error = requests.exceptions.HTTPError("503 Server Error")
		error.response = MagicMock(status_code=503)
		with patch.object(chat.requests, "post", side_effect=error):
			published = run_relay()
		self.assertIn("HTTP 503", errors(published)[0]["message"])
