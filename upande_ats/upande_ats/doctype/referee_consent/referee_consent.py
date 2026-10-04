# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Referee / background-check consent captured from a shortlisted applicant via a
# tokenised guest link. The controller keeps the Job Applicant `consent_*` custom
# fields in sync so HR sees the state without opening this record.

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class RefereeConsent(Document):
	def validate(self):
		# A Completed consent must carry an explicit tick, and must be stamped.
		if self.status == "Completed":
			if not self.consent_given:
				frappe.throw(_("Consent must be given before this form can be marked Completed."))
			if not self.signed_on:
				self.signed_on = now_datetime()

	def on_update(self):
		"""Mirror the consent state onto the linked Job Applicant.

		This is the single source of truth for the JA `consent_*` fields — the send
		flow and the guest submit both flow through here via a save. Writes bypass
		permissions (the guest submit runs unauthenticated) and skip `modified` so
		they don't churn the applicant's timeline.
		"""
		if not self.job_applicant:
			return

		if self.status == "Completed":
			values = {
				"consent_status": "Received",
				"consent_received": 1,
				"consent_date": self.signed_on,
				"referee_consent": self.name,
			}
		elif self.status == "Withdrawn":
			values = {
				"consent_status": "Withdrawn",
				"consent_received": 0,
			}
		else:  # Sent
			values = {"consent_status": "Sent"}

		frappe.db.set_value("Job Applicant", self.job_applicant, values, update_modified=False)
