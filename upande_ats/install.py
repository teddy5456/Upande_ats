import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
	"Designation": [
		{
			"fieldname": "ats_keywords",
			"fieldtype": "Table MultiSelect",
			"label": "ATS Keywords",
			"options": "ATS Designation Keyword",
			"insert_after": "description",
			"description": "Standard ATS keywords for this role; auto-populated into new Job Openings.",
		},
	],
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
		{
			"fieldname": "ats_min_relevant_experience",
			"fieldtype": "Float",
			"label": "Minimum Relevant Years",
			"precision": "1",
			"description": (
				"Minimum years of <i>relevant</i> experience required for this opening. "
				"Overrides the per-designation default. Leave blank/0 to use the designation "
				"default (and if neither is set, the experience gate is off for this opening)."
			),
			"insert_after": "ats_passmark_override",
		},
		{
			"fieldname": "ats_preferred_max_experience",
			"fieldtype": "Float",
			"label": "Preferred Max Years",
			"precision": "1",
			"description": (
				"Display/preference only — shown to HR but NEVER used to reject. "
				"Over-qualified applicants are not auto-rejected."
			),
			"insert_after": "ats_min_relevant_experience",
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
			"options": "\nNot Scored\nPass\nFail\nNeeds Review",
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
			"fieldname": "ats_auto_rejected",
			"fieldtype": "Check",
			"label": "Auto-Rejected by ATS",
			"default": "0",
			"read_only": 1,
			"in_standard_filter": 1,
			"description": (
				"1 when ATS set the status to Rejected. A later clean Pass re-run clears this "
				"and re-opens the applicant; a manual rejection (flag 0) is never auto-reopened."
			),
			"insert_after": "ats_reason",
		},
		{
			"fieldname": "ats_total_experience",
			"fieldtype": "Float",
			"label": "Total Experience (yrs)",
			"precision": "1",
			"read_only": 1,
			"description": "Total work experience detected from the CV (overlaps merged).",
			"insert_after": "ats_reason",
		},
		{
			"fieldname": "ats_relevant_experience",
			"fieldtype": "Float",
			"label": "Relevant Experience (yrs)",
			"precision": "1",
			"read_only": 1,
			"in_standard_filter": 1,
			"description": "Years of experience judged relevant to the designation's keywords.",
			"insert_after": "ats_total_experience",
		},
		{
			"fieldname": "ats_experience_breakdown",
			"fieldtype": "Small Text",
			"label": "Experience Breakdown",
			"read_only": 1,
			"description": "Per-role detected months and whether each was counted as relevant (JSON).",
			"insert_after": "ats_relevant_experience",
		},
		{
			"fieldname": "ats_screened_on",
			"fieldtype": "Datetime",
			"label": "ATS Screened On",
			"read_only": 1,
			"insert_after": "ats_experience_breakdown",
		},
		{
			"fieldname": "ats_breakdown_html",
			"fieldtype": "HTML",
			"label": "ATS Breakdown",
			"insert_after": "ats_screened_on",
		},
	],
}


# The two screening reports are now standard file-based Script Reports shipped in the app
# (upande_ats/upande_ats/report/screening_for_review and .../screening_rejected), so the
# WHERE is built conditionally in Python and an empty Job Opening filter is handled cleanly.
# These are the legacy DB-stored Query Report names to delete on migrate so they can't
# shadow or duplicate the standard ones.
LEGACY_QUERY_REPORT_NAMES = [
	"Screening - Action Needed",
	"Screening - For Review",
	"Screening - Rejected",
]


def after_install():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	_seed_settings()
	setup_hr_views()
	_setup_referee_consent()
	frappe.db.commit()


def after_migrate():
	"""Re-assert app-owned custom fields, HR list view, shortcut and report on every migrate.

	Standard (hrms) workspaces can be re-synced on migrate, so we idempotently
	re-add our shortcut and report here rather than relying on a one-time install.
	"""
	create_custom_fields(CUSTOM_FIELDS, update=True)
	setup_hr_views()
	_setup_referee_consent()
	frappe.db.commit()


def _setup_referee_consent():
	"""Idempotently re-assert the referee-consent custom fields, notification and
	print format (also run by the add_referee_consent_setup patch)."""
	from upande_ats.consent import (
		ensure_default_statement,
		ensure_job_applicant_fields,
		ensure_notification,
		ensure_print_format,
	)

	ensure_job_applicant_fields()
	ensure_notification()
	ensure_print_format()
	ensure_default_statement()


def setup_hr_views():
	_drop_legacy_query_reports()
	_ensure_to_review_shortcut()


def _drop_legacy_query_reports():
	"""Delete leftover DB-stored Query Report docs for the screening reports.

	These were previously created in code as Query Reports; they are now standard
	Script Reports shipped as files. Removing the Query Report rows prevents a
	duplicate/shadowing report with the same name. Standard (file-based) reports of
	the same name are left untouched — only report_type == 'Query Report' is dropped.
	"""
	for name in LEGACY_QUERY_REPORT_NAMES:
		if (
			frappe.db.exists("Report", name)
			and frappe.db.get_value("Report", name, "report_type") == "Query Report"
		):
			frappe.delete_doc("Report", name, ignore_permissions=True, force=True)


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
