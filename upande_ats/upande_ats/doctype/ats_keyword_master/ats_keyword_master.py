from frappe.model.document import Document


class ATSKeywordMaster(Document):
	def validate(self):
		if self.default_weight is None or self.default_weight < 0:
			self.default_weight = 1
		if self.default_weight > 5:
			self.default_weight = 5
