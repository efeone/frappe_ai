# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

import unittest

import frappe

from frappe_ai.ai_assistant.doctype.ai_skill.ai_skill import AISkill


class TestAISkill(unittest.TestCase):
	def test_valid_slug(self):
		doc = AISkill.__new__(AISkill)
		doc.skill_name = "custom-report-analysis"
		doc.description = "Analyzes monthly financial reports"
		doc.instructions = "# Instructions\nDo this."
		# Valid document does not raise
		doc.validate()
		self.assertEqual(doc.skill_name, "custom-report-analysis")

	def test_invalid_slug_throws(self):
		doc = AISkill.__new__(AISkill)
		doc.skill_name = "Custom Report Analysis"
		doc.description = "Desc"
		doc.instructions = "Instructions"
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_long_description_throws(self):
		doc = AISkill.__new__(AISkill)
		doc.skill_name = "custom-skill"
		doc.description = "x" * 250
		doc.instructions = "Instructions"
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_missing_instructions_throws(self):
		doc = AISkill.__new__(AISkill)
		doc.skill_name = "custom-skill"
		doc.description = "Valid description"
		doc.instructions = ""
		with self.assertRaises(frappe.ValidationError):
			doc.validate()
