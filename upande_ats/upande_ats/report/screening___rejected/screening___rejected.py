"""Screening - Rejected.

Keyword failures only (status = 'Rejected' AND ats_passes_passmark = 0) — no overlap
with Screening - For Review, which holds keyword-qualified rejects. The Job Opening
filter is optional: its condition is only added to the WHERE when a value is supplied,
so a blank filter shows every opening (no fragile optional bind).
"""

import frappe

# Static base conditions (literals only — never interpolated with user input).
BASE_CONDITIONS = [
	"ja.status = 'Rejected'",
	"ja.ats_passes_passmark = 0",
]


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": "Applicant", "fieldname": "applicant", "fieldtype": "Link", "options": "Job Applicant", "width": 150},
		{"label": "Name", "fieldname": "applicant_name", "fieldtype": "Data", "width": 150},
		{"label": "Opening", "fieldname": "job_title", "fieldtype": "Link", "options": "Job Opening", "width": 170},
		{"label": "Designation", "fieldname": "designation", "fieldtype": "Link", "options": "Designation", "width": 140},
		{"label": "Score %", "fieldname": "ats_score", "fieldtype": "Percent", "width": 90},
		{"label": "Relevant", "fieldname": "relevant", "fieldtype": "Float", "precision": 1, "width": 90},
		{"label": "Required", "fieldname": "required", "fieldtype": "Float", "precision": 1, "width": 90},
		{"label": "Result", "fieldname": "ats_result", "fieldtype": "Data", "width": 110},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": "Reason", "fieldname": "ats_reason", "fieldtype": "Data", "width": 340},
		{"label": "Screened On", "fieldname": "ats_screened_on", "fieldtype": "Datetime", "width": 160},
	]


def get_data(filters):
	conditions = list(BASE_CONDITIONS)
	values = {}
	if filters.get("job_opening"):
		conditions.append("ja.job_title = %(job_opening)s")
		values["job_opening"] = filters.get("job_opening")

	where = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT
			ja.name                    AS applicant,
			ja.applicant_name          AS applicant_name,
			ja.job_title               AS job_title,
			ja.designation             AS designation,
			ja.ats_score               AS ats_score,
			ja.ats_relevant_experience AS relevant,
			sc.required_experience     AS required,
			ja.ats_result              AS ats_result,
			ja.status                  AS status,
			ja.ats_reason              AS ats_reason,
			ja.ats_screened_on         AS ats_screened_on
		FROM `tabJob Applicant` ja
		LEFT JOIN `tabATS Score` sc ON sc.name = ja.ats_score_link
		WHERE {where}
		ORDER BY ja.ats_screened_on DESC
		""",
		values,
		as_dict=True,
	)
