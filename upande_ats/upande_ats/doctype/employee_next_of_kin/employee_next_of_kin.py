# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

from frappe.model.document import Document


# Class name is intentionally "EmployeeNextofKin" with a lower-case "of", not the
# PEP8-natural "EmployeeNextOfKin". Frappe resolves a controller with
# doctype.replace(" ", "").replace("-", ""), so "Employee Next of Kin" only ever
# looks for this spelling. Renaming it to EmployeeNextOfKin makes the import fail
# and migrate drops the DocType as an orphan.
class EmployeeNextofKin(Document):
	pass
