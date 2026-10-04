# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Employee doc event handlers.

import frappe
from frappe import _
from frappe.utils import flt


def validate_next_of_kin_allocation(doc, method=None):
	"""Employee validate hook: keep Next of Kin benefit allocation sane.

	The rule is a ceiling, not an exact requirement — a partially entered record
	totalling less than 100% must save without complaint so HR can come back to it.
	"""
	rows = doc.get("custom_next_of_kin") or []
	if not rows:
		return

	total = 0
	for row in rows:
		allocation = flt(row.allocation_percentage)
		if allocation <= 0:
			# allocation_percentage is deliberately not `reqd` on the child DocType:
			# Frappe reads 0 as missing on numeric fields, which would throw a
			# mandatory error on every freshly added row. Enforced here instead.
			frappe.throw(
				_("Next of Kin row {0} ({1}): Allocation (%) must be greater than 0.").format(
					row.idx, row.full_name or _("unnamed")
				)
			)
		total += allocation

	total = flt(total, 2)
	if total > 100:
		frappe.throw(
			_(
				"Next of Kin total allocation is {0}%. It cannot exceed 100%. "
				"Please adjust the rows so they add up to 100% or less."
			).format(total)
		)
