app_name = "upande_ats"
app_title = "Upande ATS"
app_publisher = "Upande"
app_description = "ATS scoring on top of Frappe HRMS"
app_email = "teddy@upande.com"
app_license = "mit"

required_apps = ["hrms"]

after_install = "upande_ats.install.after_install"
after_migrate = "upande_ats.install.after_migrate"

doc_events = {
	"Job Applicant": {
		# New application -> score + apply pass-mark verdict (status/result/reason).
		"after_insert": "upande_ats.screening.enqueue_screening",
		# Resume changed on an existing applicant -> re-score and re-evaluate the verdict.
		"on_update": "upande_ats.events.maybe_screen_on_update",
	},
	"Employee": {
		# Next of Kin benefit allocation must total 100% or less.
		"validate": "upande_ats.employee.validate_next_of_kin_allocation",
	},
}

doctype_js = {
	"Job Applicant": "public/js/job_applicant.js",
	"Job Opening": "public/js/job_opening.js",
}

doctype_list_js = {
	"Job Applicant": "public/js/job_applicant_list.js",
}

fixtures = [
	# One Custom Field entry only: every entry for the same doctype writes to the
	# same fixtures/custom_field.json, so a second block would overwrite this one
	# on export. custom_notice_period is the public job-application form's only
	# non-standard field, so it rides along via or_filters.
	{
		"dt": "Custom Field",
		"filters": [["dt", "in", ["Job Opening", "Job Applicant", "Designation"]]],
		"or_filters": [["fieldname", "like", "ats_%"], ["fieldname", "=", "custom_notice_period"]],
	},
	# The public application form. Fixtured, so the commit is the source of truth:
	# every deploy re-imports it and overwrites edits made in Desk. Re-export
	# (bench export-fixtures) and commit after any UI change to this form.
	{"dt": "Web Form", "filters": [["name", "=", "kentrout-job-application"]]},
]
