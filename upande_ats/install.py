import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
	"Job Opening": [
		{
			"fieldname": "ats_section",
			"fieldtype": "Section Break",
			"label": "ATS Configuration",
			"insert_after": "description",
			"collapsible": 1,
		},
		{
			"fieldname": "ats_keywords",
			"fieldtype": "Table",
			"label": "ATS Keywords",
			"options": "ATS Keyword",
			"insert_after": "ats_section",
		},
		{
			"fieldname": "ats_passmark_override",
			"fieldtype": "Percent",
			"label": "Passmark Override %",
			"description": "Overrides default passmark from ATS Settings for this opening only.",
			"insert_after": "ats_keywords",
		},
	],
	"Job Applicant": [
		{
			"fieldname": "ats_section",
			"fieldtype": "Section Break",
			"label": "ATS",
			"insert_after": "resume_attachment",
		},
		{
			"fieldname": "ats_score",
			"fieldtype": "Percent",
			"label": "ATS Score %",
			"read_only": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "ats_section",
		},
		{
			"fieldname": "ats_passes_passmark",
			"fieldtype": "Check",
			"label": "Passes Passmark",
			"read_only": 1,
			"in_standard_filter": 1,
			"insert_after": "ats_score",
		},
		{
			"fieldname": "ats_score_link",
			"fieldtype": "Link",
			"label": "Latest Score Record",
			"options": "ATS Score",
			"read_only": 1,
			"insert_after": "ats_passes_passmark",
		},
		{
			"fieldname": "ats_result",
			"fieldtype": "Select",
			"label": "ATS Result",
			"options": "\nNot Scored\nPass\nFail",
			"read_only": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "ats_score_link",
		},
		{
			"fieldname": "ats_reason",
			"fieldtype": "Small Text",
			"label": "ATS Reason",
			"read_only": 1,
			"insert_after": "ats_result",
		},
		{
			"fieldname": "ats_screened_on",
			"fieldtype": "Datetime",
			"label": "ATS Screened On",
			"read_only": 1,
			"insert_after": "ats_reason",
		},
		{
			"fieldname": "ats_breakdown_html",
			"fieldtype": "HTML",
			"label": "ATS Breakdown",
			"insert_after": "ats_screened_on",
		},
	],
}


REJECTED_REPORT_NAME = "Screening - Rejected"

REJECTED_REPORT_QUERY = """SELECT
\tja.name            AS "Applicant:Link/Job Applicant:160",
\tja.applicant_name  AS "Name:Data:160",
\tja.job_title       AS "Opening:Link/Job Opening:180",
\tja.designation     AS "Designation:Link/Designation:140",
\tja.ats_score       AS "Score %%:Percent:90",
\tja.ats_reason      AS "Reason:Data:340",
\tja.ats_screened_on AS "Screened On:Datetime:160"
FROM `tabJob Applicant` ja
WHERE ja.status = 'Rejected' AND ja.ats_result = 'Fail'
ORDER BY ja.ats_screened_on DESC"""


def after_install():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	_seed_settings()
	setup_hr_views()
	frappe.db.commit()


def after_migrate():
	"""Re-assert app-owned custom fields, HR list view, shortcut and report on every migrate.

	Standard (hrms) workspaces can be re-synced on migrate, so we idempotently
	re-add our shortcut and report here rather than relying on a one-time install.
	"""
	create_custom_fields(CUSTOM_FIELDS, update=True)
	setup_hr_views()
	frappe.db.commit()


def setup_hr_views():
	_ensure_rejected_report()
	_ensure_to_review_shortcut()


def _ensure_rejected_report():
	"""Create/update the 'Screening - Rejected' Query Report (idempotent)."""
	values = {
		"report_type": "Query Report",
		"ref_doctype": "Job Applicant",
		"module": "Upande ATS",
		"is_standard": "No",
		"disabled": 0,
		"query": REJECTED_REPORT_QUERY,
	}
	if frappe.db.exists("Report", REJECTED_REPORT_NAME):
		report = frappe.get_doc("Report", REJECTED_REPORT_NAME)
		report.update(values)
	else:
		report = frappe.new_doc("Report")
		report.report_name = REJECTED_REPORT_NAME
		report.update(values)
	report.flags.ignore_permissions = True
	report.save()


def _ensure_to_review_shortcut():
	"""Add a 'To Review' shortcut (Job Applicant list filtered to status=Open) to the
	Recruitment workspace, so HR's working queue hides auto-rejected applicants."""
	if not frappe.db.exists("Workspace", "Recruitment"):
		return
	ws = frappe.get_doc("Workspace", "Recruitment")
	for sc in ws.shortcuts:
		if sc.label == "To Review":
			sc.type = "DocType"
			sc.link_to = "Job Applicant"
			sc.doc_view = "List"
			sc.stats_filter = '{"status":"Open"}'
			sc.color = "Green"
			break
	else:
		ws.append(
			"shortcuts",
			{
				"type": "DocType",
				"link_to": "Job Applicant",
				"label": "To Review",
				"doc_view": "List",
				"stats_filter": '{"status":"Open"}',
				"color": "Green",
			},
		)
	ws.flags.ignore_permissions = True
	ws.save()


def _seed_settings():
	settings = frappe.get_single("ATS Settings")

	if not settings.engine_weights:
		settings.append("engine_weights", {"engine_name": "Keyword Frequency", "weight_pct": 60, "is_enabled": 1})
		settings.append("engine_weights", {"engine_name": "TF-IDF Cosine", "weight_pct": 25, "is_enabled": 1})
		settings.append("engine_weights", {"engine_name": "Fuzzy Match", "weight_pct": 15, "is_enabled": 1})

	if settings.default_passmark is None:
		settings.default_passmark = 60
	if settings.fuzzy_match_threshold is None:
		settings.fuzzy_match_threshold = 85
	if not settings.mandatory_keyword_rule:
		settings.mandatory_keyword_rule = "Cap Score"
	if settings.mandatory_cap is None:
		settings.mandatory_cap = 40
	if settings.min_resume_chars is None:
		settings.min_resume_chars = 200
	if not settings.accepted_file_types:
		settings.accepted_file_types = "pdf,docx,doc,txt"
	if settings.auto_score_on_resume_upload is None:
		settings.auto_score_on_resume_upload = 1
	if settings.keep_score_history is None:
		settings.keep_score_history = 1

	if not settings.synonym_groups:
		seed = [
			("JavaScript", "js, ecmascript, node.js, nodejs"),
			("Python", "py, python3"),
			("Communication", "communicator, communicating, communications"),
			("Management", "managing, managed, manager"),
			("Leadership", "leader, leading, led"),
		]
		for canonical, syns in seed:
			settings.append(
				"synonym_groups",
				{"canonical_term": canonical, "synonyms": syns, "is_active": 1},
			)

	settings.flags.ignore_permissions = True
	settings.save()
