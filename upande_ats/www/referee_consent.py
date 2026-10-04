# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Guest-facing portal page for signing a referee-consent form via a tokenised link.
# (File is referee_consent.py; the template referee-consent.html serves /referee-consent —
# Frappe maps the hyphenated page to this underscored controller.)

import frappe
import frappe.sessions
from upande_ats.consent import get_referee_consent

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False

	token = frappe.form_dict.get("token")
	context.token = token

	if not token:
		context.state = "invalid"
		context.message = "This consent link is missing its token."
		return context

	try:
		data = get_referee_consent(token)
	except frappe.ValidationError:
		# get_referee_consent raises a clean message for bad/invalid tokens.
		context.state = "invalid"
		context.message = frappe.utils.strip_html(
			frappe.message_log[-1].get("message") if frappe.message_log else ""
		) or "This consent link is not valid."
		frappe.clear_messages()
		frappe.local.response.http_status_code = 200
		return context

	context.state = data.get("state")

	if context.state == "open":
		# The submit is a guest POST that needs a CSRF token. get_csrf_token()
		# reads/persists session.data.csrf_token; frappe.session.csrf_token is None.
		context.csrf_token = frappe.sessions.get_csrf_token()
		context.applicant_name = data.get("applicant_name")
		context.job_opening = data.get("job_opening")
		context.consent_statement = data.get("consent_statement")
		context.referees = data.get("referees")

	return context
