# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_DESC_LEN = 200


def _throw(msg: str) -> None:
	frappe.throw(msg, frappe.ValidationError)


class AISkill(Document):
	def validate(self):
		if not self.skill_name or not _SLUG_PATTERN.match(self.skill_name.strip()):
			_throw(
				_("Skill Name must use lowercase letters, numbers, and hyphens (e.g. 'custom-export-flow').")
			)
		self.skill_name = self.skill_name.strip()
		if self.description and len(self.description.strip()) > _MAX_DESC_LEN:
			_throw(
				_("Catalog Description cannot exceed {0} characters (currently {1}).").format(
					_MAX_DESC_LEN, len(self.description.strip())
				)
			)
		if not self.instructions or not self.instructions.strip():
			_throw(_("Markdown Instructions are required."))
