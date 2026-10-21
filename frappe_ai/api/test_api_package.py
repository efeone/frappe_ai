"""The api package must load without importing its own modules."""

import subprocess
import sys

from frappe.tests.utils import FrappeTestCase


class TestApiPackage(FrappeTestCase):
	def test_importing_the_package_loads_none_of_its_modules(self):
		# When it did, a threaded worker's first parallel requests (chat and confirm
		# imported at once) failed with "deadlock detected by _ModuleLock".
		code = "import sys, frappe_ai.api; print(sorted(m for m in sys.modules if m.startswith('frappe_ai.api.')))"
		loaded = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
		self.assertEqual(loaded, "[]")
