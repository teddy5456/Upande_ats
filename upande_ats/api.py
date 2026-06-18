import frappe


@frappe.whitelist()
def get_designation_keywords(designation):
	"""Return the standard ATS keywords configured on a Designation.

	Reads the Designation's `ats_keywords` Table MultiSelect (custom field), resolves
	each linked ATS Keyword Master, and returns rows shaped for the Job Opening
	`ats_keywords` (ATS Keyword) child table. `is_mandatory` is always 0 — HR decides
	mandatory status per opening.
	"""
	if not designation:
		return []

	# Read only the multiselect child rows; tolerant if the custom field is missing.
	rows = frappe.get_all(
		"ATS Designation Keyword",
		filters={"parent": designation, "parenttype": "Designation", "parentfield": "ats_keywords"},
		order_by="idx asc",
		pluck="keyword",
	)
	if not rows:
		return []

	# Preserve order, drop blanks/dupes from the designation table.
	keyword_names = list(dict.fromkeys(k for k in rows if k))
	if not keyword_names:
		return []

	masters = frappe.get_all(
		"ATS Keyword Master",
		filters={"name": ["in", keyword_names]},
		fields=["name", "keyword", "default_weight", "default_category"],
	)
	by_name = {m["name"]: m for m in masters}

	result = []
	for name in keyword_names:
		master = by_name.get(name)
		if not master:
			continue
		result.append(
			{
				"keyword": master.get("keyword") or name,
				"weight": master.get("default_weight") or 1,
				"category": master.get("default_category") or "Skill",
				"is_mandatory": 0,
			}
		)
	return result
