import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Preferred anchor: the last field of the Statutory Details section that The Social
# House adds to the Personal Details tab. Kentrout has no such section.
TSH_ANCHOR = "custom_sha_no"

# Fallback: HRMS ships this as a Custom Field on Employee (hrms/setup.py), inside the
# collapsible "Health Insurance" section that sits immediately before Passport
# Details. Present on both sites, so Next of Kin lands at the end of the tab either way.
HRMS_ANCHOR = "health_insurance_no"

# Last resort: standard ERPNext field in the same tab. Positions Next of Kin higher up
# than intended, but keeps the migrate green instead of failing on a bad insert_after.
FALLBACK_ANCHOR = "blood_group"


def execute():
	# One-time seed. Anything already on site — including HR's Desk edits — is left alone.
	if frappe.db.exists("Custom Field", "Employee-custom_next_of_kin"):
		return

	create_custom_fields(_custom_fields(_resolve_anchor()), update=False)


def _resolve_anchor():
	"""Pick the field the Next of Kin section is inserted after.

	The Personal Details tab differs per site, so this is resolved at runtime rather
	than hardcoded.
	"""
	if frappe.db.exists("Custom Field", f"Employee-{TSH_ANCHOR}"):
		return TSH_ANCHOR

	if frappe.db.exists("Custom Field", f"Employee-{HRMS_ANCHOR}"):
		return HRMS_ANCHOR

	# Neither anchor resolved. insert_after pointing at a missing field would silently
	# dump the section at the end of the form, so fall back to a standard field and
	# leave a breadcrumb instead of failing the migrate.
	frappe.log_error(
		f"Employee Next of Kin: neither '{TSH_ANCHOR}' nor '{HRMS_ANCHOR}' exists on "
		f"Employee. Falling back to '{FALLBACK_ANCHOR}' — check the section position.",
		"Next of Kin anchor fallback",
	)
	return FALLBACK_ANCHOR


def _custom_fields(anchor):
	return {
		"Employee": [
			{
				"fieldname": "custom_next_of_kin_section",
				"label": "Next of Kin",
				"fieldtype": "Section Break",
				"insert_after": anchor,
			},
			{
				"fieldname": "custom_next_of_kin",
				"label": "Next of Kin",
				"fieldtype": "Table",
				"options": "Employee Next of Kin",
				"insert_after": "custom_next_of_kin_section",
				"description": "Total Allocation (%) across all rows cannot exceed 100%.",
			},
		]
	}
