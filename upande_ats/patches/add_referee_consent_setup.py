# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Adds the Job Applicant consent_* custom fields (beside the assessment tracks),
# and creates the HR "consent completed" Notification and the Referee Consent
# print format. All steps are idempotent and shared with install.after_migrate.

import frappe
from upande_ats.consent import (
	ensure_default_statement,
	ensure_job_applicant_fields,
	ensure_notification,
	ensure_print_format,
)


def execute():
	ensure_job_applicant_fields()
	ensure_notification()
	ensure_print_format()
	ensure_default_statement()
	frappe.db.commit()
