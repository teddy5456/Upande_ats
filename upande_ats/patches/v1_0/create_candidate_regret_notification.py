import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

NOTIFICATION_NAME = "Candidate Regret - Post Screening Stage"

CUSTOM_FIELDS = {
	"Job Applicant": [
		{
			"fieldname": "custom_rejection_stage",
			"label": "Rejection Stage",
			"fieldtype": "Select",
			"options": "\nCV Screening\nAssessment\nInterview\nReference Check\nOffer Stage",
			"insert_after": "custom_rejection_reason",
			"depends_on": 'eval:doc.status=="Rejected"',
			"no_copy": 1,
			"description": (
				"Controls the regret email. Leave blank or set 'CV Screening' to send nothing. "
				"Assessment / Interview / Reference Check / Offer Stage will email the candidate."
			),
		}
	]
}

CONDITION = (
	'doc.status == "Rejected" and doc.custom_rejection_stage '
	'and doc.custom_rejection_stage != "CV Screening"'
)

SUBJECT = "Update on your application - {{ doc.designation }}, Kentrout Farm Limited"


def execute():
	# update=False so a field edited in the UI is left untouched on re-run
	create_custom_fields(CUSTOM_FIELDS, update=False)

	if frappe.db.exists("Notification", NOTIFICATION_NAME):
		return

	doc = frappe.new_doc("Notification")
	doc.name = NOTIFICATION_NAME
	doc.subject = SUBJECT
	doc.document_type = "Job Applicant"
	doc.channel = "Email"
	doc.event = "Value Change"
	doc.value_changed = "status"
	doc.condition = CONDITION
	doc.message_type = "HTML"
	doc.message = _message()
	doc.enabled = 1
	doc.is_standard = 0
	doc.send_system_notification = 0
	doc.attach_print = 0
	doc.append("recipients", {"receiver_by_document_field": "email_id"})
	doc.insert(ignore_permissions=True)


def _message():
	# Plain string, not an f-string: the body is full of Jinja {{ }} and {% %}
	return """<div style="margin:0;padding:0;background:#f4f3ef;font-family:'Poppins',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f3ef;padding:28px 0;">
<tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:20px;overflow:hidden;border:1px solid #eae8e2;">

<!-- HEADER -->
<tr><td bgcolor="#0a0a0a" style="background:#0a0a0a;background:linear-gradient(135deg,#0a0a0a 0%,#3a3a34 100%);padding:34px 34px 30px 34px;">
  <div style="display:inline-block;background:#5a5a52;color:#ffffff;font-size:10px;letter-spacing:2.2px;font-weight:bold;padding:6px 14px;border-radius:999px;text-transform:uppercase;">Application Update</div>
  <h1 style="color:#ffffff;margin:16px 0 0 0;font-size:30px;line-height:1.1;font-weight:600;letter-spacing:-0.9px;">Update on your application</h1>
  <p style="color:#b8b6ae;margin:8px 0 0 0;font-size:13px;">Kentrout Farm Limited &middot; Human Resources</p>
</td></tr>

<!-- BODY -->
<tr><td style="padding:34px;color:#3a3a34;font-size:15px;line-height:1.6;">

  <p style="margin:0 0 16px 0;">Dear {{ doc.applicant_name }},</p>

  <p style="margin:0 0 16px 0;">Thank you for the time and effort you invested in your application for the <b>{{ doc.designation }}</b> position at Kentrout Farm Limited.</p>

  {% if doc.custom_rejection_stage == "Interview" %}
  <p style="margin:0 0 16px 0;">We appreciated the opportunity to meet you and to learn more about your experience.</p>
  {% elif doc.custom_rejection_stage == "Assessment" %}
  <p style="margin:0 0 16px 0;">We appreciated the effort you put into completing our assessment.</p>
  {% elif doc.custom_rejection_stage == "Reference Check" %}
  <p style="margin:0 0 16px 0;">We appreciated the opportunity to take your application through our full selection process.</p>
  {% elif doc.custom_rejection_stage == "Offer Stage" %}
  <p style="margin:0 0 16px 0;">We appreciated the opportunity to take your application through to the final stage of our process.</p>
  {% endif %}

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fafaf6;border-radius:16px;border-left:4px solid #b8b6ae;margin:8px 0 24px 0;">
  <tr><td style="padding:22px 26px;">
    <div style="font-size:10px;color:#8a8780;letter-spacing:2px;text-transform:uppercase;font-weight:bold;margin-bottom:10px;">Outcome</div>
    <p style="margin:0;font-size:15px;line-height:1.6;color:#3a3a34;">After careful consideration of all the candidates who reached this stage, we regret to inform you that we will not be proceeding with your application on this occasion. The standard of applicants was high and the decision was not an easy one.</p>
  </td></tr>
  </table>

  {% if doc.custom_talent_pool %}
  <div style="background:#fafaf6;border-radius:12px;border-left:3px solid #228883;padding:16px 20px;margin:0 0 24px 0;">
    <p style="margin:0;font-size:13px;color:#5a5a52;line-height:1.6;">We were impressed by what you brought to the process and would like to keep your details on file. Should a suitable opening arise, we will be in touch.</p>
  </div>
  {% endif %}

  <p style="margin:0;">We thank you sincerely for your interest in Kentrout Farm Limited and wish you every success in your career.</p>

  <p style="margin:22px 0 0 0;">Kind regards,<br><b>Human Resources</b><br>Kentrout Farm Limited</p>

</td></tr>

<!-- FOOTER -->
<tr><td bgcolor="#0a0a0a" style="background:#0a0a0a;background:linear-gradient(135deg,#0a0a0a 0%,#1a1a18 100%);padding:28px 34px;text-align:center;">
  <div style="color:#fafaf6;font-size:15px;font-weight:600;letter-spacing:3.4px;">KENTROUT FARM LIMITED</div>
  <p style="color:#8a8780;margin:6px 0 0 0;font-size:10px;letter-spacing:1.6px;text-transform:uppercase;">Human Resources</p>
  <div style="height:1px;background:#3a3a34;margin:18px auto;width:56%;"></div>
  <p style="color:#8a8780;margin:0;font-size:10px;line-height:1.7;">This is an automatically generated message sent from an unmonitored mailbox. Please do not reply to this email.</p>
</td></tr>

</table>
</td></tr>
</table>
</div>"""
