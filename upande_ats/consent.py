# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Referee / background-check consent flow.
#
# Trust boundary: get_referee_consent / submit_referee_consent are guest endpoints.
# They validate the token FIRST, then perform writes with ignore_permissions=True.
# The ONLY email sent by this module is the delivery of the tokenised consent link
# (the same transport exception as the assessment invite) — every other HR-facing
# alert is a Notification document, never code.

import json
import re

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import escape_html, get_url, now_datetime

CONSENT_PAGE = "referee-consent"

# States that count as an "active" consent — blocks a duplicate send.
_ACTIVE_CONSENT_STATES = ["Sent", "Completed"]


# ---------------------------------------------------------------------------
# HR-triggered: create + dispatch a consent form
# ---------------------------------------------------------------------------
@frappe.whitelist()
def send_consent_form(job_applicant):
	"""Create a Referee Consent (status Sent) and email the tokenised link.

	Guarded to shortlisted (ATS Pass) applicants with no active consent already
	out. The consent wording is copied from ATS Settings at send time so the
	record is a faithful snapshot even if HR later edits the template.
	"""
	# Only users who can edit the applicant (HR roles) may dispatch consent forms.
	if not frappe.has_permission("Job Applicant", "write", doc=job_applicant):
		frappe.throw(
			_("Not permitted to send a consent form for this applicant."),
			frappe.PermissionError,
		)

	applicant_doc = frappe.get_doc("Job Applicant", job_applicant)

	# Gate 1: shortlisted only.
	if applicant_doc.get("ats_result") != "Pass":
		frappe.throw(_("A consent form can only be sent to an applicant who has passed ATS screening."))

	# Gate 2: no active (Sent / Completed) consent already exists.
	existing = frappe.db.get_value(
		"Referee Consent",
		{"job_applicant": job_applicant, "status": ["in", _ACTIVE_CONSENT_STATES]},
		["name", "status"],
		as_dict=True,
	)
	if existing:
		frappe.throw(
			_("A consent form is already {0} for this applicant ({1}).").format(
				existing.status.lower(), existing.name
			)
		)

	consent = frappe.get_doc(
		{
			"doctype": "Referee Consent",
			"job_applicant": job_applicant,
			"status": "Sent",
			"token": frappe.generate_hash(length=32),
			"consent_statement": _get_consent_template(),
		}
	)
	# The Sent shell has no referees yet (the applicant adds them on the portal),
	# so skip the mandatory check here; it's enforced at Completed.
	consent.insert(ignore_permissions=True, ignore_mandatory=True)

	# on_update already mirrors this, but set it explicitly for clarity/robustness.
	frappe.db.set_value("Job Applicant", job_applicant, "consent_status", "Sent", update_modified=False)

	link = _consent_link(consent.token)
	emailed = _email_consent_invite(applicant_doc, consent, link)

	frappe.db.commit()

	return {"consent": consent.name, "link": link, "emailed": emailed}


def _get_consent_template():
	"""Current HR-editable consent wording from ATS Settings (never hardcoded)."""
	return frappe.db.get_single_value("ATS Settings", "consent_statement") or ""


# Seeded once into ATS Settings; fully editable in the UI thereafter. The company
# name is resolved from the site's default Company at seed time so a fresh site gets
# its own legal entity instead of inheriting a hardcoded one; with no default Company
# set the wording stays company-neutral. Each site sets its real legal name in the UI.
def _default_consent_statement():
	company = frappe.db.get_default("Company")
	who = escape_html(company) if company else "the hiring organisation"
	return (
		"<p>I confirm that the referee details I have provided are accurate and that I "
		"have informed each referee that they may be contacted.</p>"
		f"<p>I consent to {who} contacting these referees and to a reference and "
		"background check being carried out as part of my application. I understand this "
		"information will be handled confidentially and used only for recruitment purposes.</p>"
	)


def ensure_default_statement():
	"""Seed the consent wording if ATS Settings has none yet (idempotent)."""
	if not frappe.db.get_single_value("ATS Settings", "consent_statement"):
		frappe.db.set_single_value(
			"ATS Settings", "consent_statement", _default_consent_statement()
		)


def _consent_link(token):
	return get_url(f"/{CONSENT_PAGE}?token={token}")


def _email_consent_invite(applicant_doc, consent, link):
	"""Email the applicant the tokenised consent link.

	This is the sole email send in the consent flow — the link-delivery transport
	exception, mirroring the assessment invite. Prefers an HR-editable Email
	Template; falls back to a built-in message so the flow works out of the box.
	"""
	recipient = applicant_doc.get("email_id")
	if not recipient:
		return False

	args = {
		"applicant_name": applicant_doc.get("applicant_name") or "Candidate",
		"link": link,
	}

	template = frappe.db.exists("Email Template", "Referee Consent Invitation")
	if template:
		et = frappe.get_doc("Email Template", "Referee Consent Invitation")
		subject = frappe.render_template(et.subject, args)
		message = frappe.render_template(et.response_html or et.response or "", args)
	else:
		subject = _("Referee & background-check consent form")
		message = _DEFAULT_INVITE_HTML.format(**args)

	frappe.sendmail(
		recipients=[recipient],
		subject=subject,
		message=message,
		reference_doctype="Referee Consent",
		reference_name=consent.name,
	)
	return True


_DEFAULT_INVITE_HTML = """
<p>Dear {applicant_name},</p>
<p>As part of your application you have been asked to provide referee contact
details and to consent to a background / reference check.</p>
<p>Please use the link below to review the consent statement, list your referees,
and sign. It is personal to you and can be submitted only once.</p>
<p><a href="{link}"
   style="display:inline-block;padding:10px 18px;background:#2490ef;color:#fff;
   border-radius:6px;text-decoration:none;">Open Consent Form</a></p>
<p>If the button does not work, copy this link into your browser:<br>{link}</p>
<p>Thank you.</p>
"""


# ---------------------------------------------------------------------------
# Guest endpoint: fetch consent state for rendering
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def get_referee_consent(token):
	"""Return the state needed to render the guest consent page."""
	consent = _get_consent_by_token(token)

	if consent.status == "Completed":
		return {"state": "completed"}

	if consent.status == "Withdrawn":
		return {"state": "withdrawn"}

	return {
		"state": "open",
		"applicant_name": consent.applicant_name,
		"job_opening": consent.job_opening,
		"consent_statement": consent.consent_statement,
		"referees": [
			{
				"referee_name": r.referee_name,
				"relationship": r.relationship,
				"organisation": r.organisation,
				"position": r.position,
				"phone": r.phone,
				"email": r.email,
			}
			for r in consent.referees
		],
	}


# ---------------------------------------------------------------------------
# Guest endpoint: submit the signed consent
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def submit_referee_consent(token, referees, signed_by_name, signature, consent_given):
	"""Write referees + signature and mark the consent Completed. Idempotent-safe.

	Strictly gated on the token; the write itself bypasses permissions because the
	applicant is unauthenticated. Resubmission after Completed is blocked.
	"""
	consent = _get_consent_by_token(token)

	if consent.status == "Completed":
		frappe.throw(_("This consent form has already been submitted."), title=_("Already Submitted"))
	if consent.status == "Withdrawn":
		frappe.throw(_("This consent form is no longer active."), title=_("Withdrawn"))

	if isinstance(referees, str):
		referees = json.loads(referees)

	# Require the explicit tick, a typed name, and a signature.
	if not int(consent_given or 0):
		frappe.throw(_("You must tick the consent box to submit."))
	signed_by_name = (signed_by_name or "").strip()
	if not signed_by_name:
		frappe.throw(_("Please type your full name."))
	if not signature:
		frappe.throw(_("Please sign in the signature box."))

	# Every referee row must be complete, and at least two referees are required.
	# Mirrors the client-side checks so a hand-crafted POST can't slip a partial
	# or single referee through.
	referees = referees or []
	referee_fields = [
		("referee_name", _("Name")),
		("relationship", _("Relationship")),
		("organisation", _("Organisation")),
		("position", _("Position")),
		("phone", _("Phone")),
		("email", _("Email")),
	]
	if len(referees) < 2:
		frappe.throw(_("Please provide at least two referees."))

	clean = []
	for idx, r in enumerate(referees, start=1):
		row = {field: (r.get(field) or "").strip() for field, _label in referee_fields}
		for field, label in referee_fields:
			if not row[field]:
				frappe.throw(_("Referee {0}: please fill in {1}.").format(idx, label))
		# Contact details are the whole point of the form, so the format is
		# checked here too — the browser prefix/pattern can be bypassed.
		row["phone"] = _normalize_phone(row["phone"])
		if not row["phone"] or not frappe.utils.validate_email_address(row["email"], throw=False):
			frappe.throw(
				_("Referee {0}: enter a valid email and a phone in the form +254 7XXXXXXXX").format(idx)
			)
		clean.append(row)

	consent.set("referees", [])
	for r in clean:
		consent.append("referees", r)

	consent.signed_by_name = signed_by_name
	consent.signature = signature
	consent.consent_given = 1
	consent.signed_on = now_datetime()
	consent.source_ip = frappe.local.request_ip
	consent.status = "Completed"
	consent.save(ignore_permissions=True)  # triggers the JA write-back in on_update

	frappe.db.commit()
	return {"state": "submitted"}


# Kenyan mobile numbers in international form: +254 then a 9-digit national
# number starting 7 (Safaricom) or 1 (Airtel/Telkom).
PHONE_RE = re.compile(r"^\+254[17]\d{8}$")


def _normalize_phone(value: str) -> str:
	"""Return the number as +254XXXXXXXXX, or "" if it isn't a valid one.

	The form posts the 9 national digits behind a fixed +254 prefix, but a
	pasted or hand-crafted value may arrive as 0712…, 254712… or +254 712 …,
	so those are reduced to the same 9 digits before checking.
	"""
	digits = re.sub(r"[\s\-()]", "", value or "")
	digits = digits.removeprefix("+")
	if digits.startswith("254"):
		digits = digits[3:]
	elif digits.startswith("0"):
		digits = digits[1:]
	candidate = f"+254{digits}"
	return candidate if PHONE_RE.match(candidate) else ""


def _get_consent_by_token(token):
	if not token:
		frappe.throw(_("Missing consent token."), title=_("Invalid Link"))

	name = frappe.db.get_value("Referee Consent", {"token": token}, "name")
	if not name:
		frappe.throw(_("This consent link is not valid."), title=_("Invalid Link"))

	return frappe.get_doc("Referee Consent", name)


# ---------------------------------------------------------------------------
# Idempotent setup — called from the patch AND from install.after_migrate
# ---------------------------------------------------------------------------

# Consent fields sit beside the assessment tracks on Job Applicant. Anchored after
# `custom_technical_score` (the tail of the Technical Status track owned by
# upande_assessments) so the technical block stays intact.
JOB_APPLICANT_CONSENT_FIELDS = {
	"Job Applicant": [
		{
			"fieldname": "consent_section",
			"label": "Referee Consent",
			"fieldtype": "Section Break",
			"insert_after": "custom_technical_score",
			"collapsible": 1,
		},
		{
			"fieldname": "consent_status",
			"label": "Consent Status",
			"fieldtype": "Select",
			"options": "Not Sent\nSent\nReceived\nWithdrawn",
			"default": "Not Sent",
			"insert_after": "consent_section",
			"in_standard_filter": 1,
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "consent_received",
			"label": "Consent Received",
			"fieldtype": "Check",
			"insert_after": "consent_status",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "consent_date",
			"label": "Consent Date",
			"fieldtype": "Datetime",
			"insert_after": "consent_received",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "referee_consent",
			"label": "Referee Consent",
			"fieldtype": "Link",
			"options": "Referee Consent",
			"insert_after": "consent_date",
			"read_only": 1,
			"allow_on_submit": 1,
		},
	]
}


def ensure_job_applicant_fields():
	create_custom_fields(JOB_APPLICANT_CONSENT_FIELDS, ignore_validate=True)


NOTIFICATION_NAME = "Referee Consent Completed"
NOTIFY_ROLE = "Group HR Manager"


def _ensure_role(role_name):
	"""Guarantee the notification target role exists (HRMS may not have seeded it)."""
	if not frappe.db.exists("Role", role_name):
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.flags.ignore_permissions = True
		role.insert(ignore_permissions=True)


def ensure_notification():
	"""HR alert (a Notification document, not email code) when a consent completes."""
	_ensure_role(NOTIFY_ROLE)

	message = (
		"Referee consent for <b>{{ doc.applicant_name or doc.job_applicant }}</b> "
		"has been signed and submitted.<br>"
		'<a href="/app/referee-consent/{{ doc.name }}">Open {{ doc.name }}</a>'
	)
	values = {
		"subject": "Referee consent completed: {{ doc.applicant_name or doc.job_applicant }}",
		"document_type": "Referee Consent",
		"channel": "System Notification",
		"event": "Value Change",
		"value_changed": "status",
		"condition": "doc.status == 'Completed'",
		"is_standard": 0,
		"enabled": 1,
		"message": message,
	}

	is_new = not frappe.db.exists("Notification", NOTIFICATION_NAME)
	if is_new:
		notif = frappe.new_doc("Notification")
		notif.name = NOTIFICATION_NAME
	else:
		notif = frappe.get_doc("Notification", NOTIFICATION_NAME)

	notif.update(values)
	notif.set("recipients", [{"receiver_by_role": NOTIFY_ROLE}])
	notif.flags.ignore_permissions = True
	if is_new:
		notif.insert(ignore_permissions=True, set_name=NOTIFICATION_NAME)
	else:
		notif.save()


PRINT_FORMAT_NAME = "Referee Consent"


def ensure_print_format():
	"""Clean signed-consent PDF: applicant details, referees, statement, signature."""
	values = {
		"doc_type": "Referee Consent",
		"module": "Upande ATS",
		"print_format_type": "Jinja",
		"standard": "No",
		"custom_format": 1,
		"disabled": 0,
		"html": _PRINT_FORMAT_HTML,
	}

	is_new = not frappe.db.exists("Print Format", PRINT_FORMAT_NAME)
	if is_new:
		pf = frappe.new_doc("Print Format")
		pf.name = PRINT_FORMAT_NAME
	else:
		pf = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)

	pf.update(values)
	pf.flags.ignore_permissions = True
	if is_new:
		pf.insert(ignore_permissions=True, set_name=PRINT_FORMAT_NAME)
	else:
		pf.save()


_PRINT_FORMAT_HTML = """
<div style="font-family: Arial, sans-serif; color:#333;">
	<h2 style="margin-bottom:2px;">Referee &amp; Background-Check Consent</h2>
	<div style="color:#777; font-size:12px; margin-bottom:16px;">{{ doc.name }}</div>

	<table style="width:100%; font-size:13px; margin-bottom:16px;">
		<tr>
			<td style="padding:3px 8px;"><b>Applicant</b></td>
			<td style="padding:3px 8px;">{{ doc.applicant_name or doc.job_applicant }}</td>
			<td style="padding:3px 8px;"><b>Job Opening</b></td>
			<td style="padding:3px 8px;">{{ doc.job_opening or "" }}</td>
		</tr>
		<tr>
			<td style="padding:3px 8px;"><b>Designation</b></td>
			<td style="padding:3px 8px;">{{ doc.designation or "" }}</td>
			<td style="padding:3px 8px;"><b>Status</b></td>
			<td style="padding:3px 8px;">{{ doc.status }}</td>
		</tr>
	</table>

	<h4 style="margin-bottom:4px;">Consent Statement</h4>
	<div style="border:1px solid #eee; padding:10px; font-size:13px; line-height:1.5;">
		{{ doc.consent_statement or "" }}
	</div>
	<p style="font-size:13px; margin-top:8px;">
		Consent given:
		<b>{% if doc.consent_given %}Yes{% else %}No{% endif %}</b>
	</p>

	<h4 style="margin-bottom:4px;">Referees</h4>
	<table style="width:100%; border-collapse:collapse; font-size:12px;">
		<thead>
			<tr style="background:#f4f5f6;">
				<th style="border:1px solid #ddd; padding:6px; text-align:left;">Name</th>
				<th style="border:1px solid #ddd; padding:6px; text-align:left;">Relationship</th>
				<th style="border:1px solid #ddd; padding:6px; text-align:left;">Organisation</th>
				<th style="border:1px solid #ddd; padding:6px; text-align:left;">Position</th>
				<th style="border:1px solid #ddd; padding:6px; text-align:left;">Phone</th>
				<th style="border:1px solid #ddd; padding:6px; text-align:left;">Email</th>
			</tr>
		</thead>
		<tbody>
			{% for r in doc.referees %}
			<tr>
				<td style="border:1px solid #ddd; padding:6px;">{{ r.referee_name or "" }}</td>
				<td style="border:1px solid #ddd; padding:6px;">{{ r.relationship or "" }}</td>
				<td style="border:1px solid #ddd; padding:6px;">{{ r.organisation or "" }}</td>
				<td style="border:1px solid #ddd; padding:6px;">{{ r.position or "" }}</td>
				<td style="border:1px solid #ddd; padding:6px;">{{ r.phone or "" }}</td>
				<td style="border:1px solid #ddd; padding:6px;">{{ r.email or "" }}</td>
			</tr>
			{% endfor %}
		</tbody>
	</table>

	<div style="margin-top:24px; font-size:13px;">
		<div><b>Signed by:</b> {{ doc.signed_by_name or "" }}</div>
		<div><b>Signed on:</b> {{ frappe.utils.format_datetime(doc.signed_on) if doc.signed_on else "" }}</div>
		<div><b>Source IP:</b> {{ doc.source_ip or "" }}</div>
		{% if doc.signature %}
		<div style="margin-top:8px;">
			<b>Signature:</b><br>
			<img src="{{ doc.signature }}" style="max-width:320px; border:1px solid #eee; margin-top:4px;">
		</div>
		{% endif %}
	</div>
</div>
"""
