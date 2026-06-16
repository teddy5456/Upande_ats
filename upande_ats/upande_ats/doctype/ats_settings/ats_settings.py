import frappe
from frappe import _
from frappe.model.document import Document


class ATSSettings(Document):
	def validate(self):
		self._validate_engine_weights()
		self._validate_thresholds()
		self._validate_designation_thresholds()

	def _validate_engine_weights(self):
		enabled_total = sum(int(w.weight_pct or 0) for w in self.engine_weights if w.is_enabled)
		if not self.engine_weights:
			return
		if enabled_total == 0:
			frappe.throw(_("At least one scoring engine must be enabled with a non-zero weight."))
		if enabled_total != 100:
			frappe.throw(
				_("Enabled engine weights must sum to 100. Current total: {0}").format(enabled_total)
			)

	def _validate_thresholds(self):
		if self.fuzzy_match_threshold is not None and not (0 <= int(self.fuzzy_match_threshold) <= 100):
			frappe.throw(_("Fuzzy Match Threshold must be between 0 and 100."))
		if self.default_passmark is not None and not (0 <= float(self.default_passmark) <= 100):
			frappe.throw(_("Default Passmark must be between 0 and 100."))
		if self.mandatory_cap is not None and not (0 <= float(self.mandatory_cap) <= 100):
			frappe.throw(_("Mandatory Cap must be between 0 and 100."))

	def _validate_designation_thresholds(self):
		seen = set()
		for row in self.designation_thresholds:
			if row.min_score is not None and not (0 <= int(row.min_score) <= 100):
				frappe.throw(
					_("Min Score for {0} must be between 0 and 100.").format(row.designation or "?")
				)
			if row.designation in seen:
				frappe.throw(
					_("Duplicate designation threshold for {0}. Keep one row per designation.").format(
						row.designation
					)
				)
			seen.add(row.designation)

	def _designation_row(self, designation: str | None):
		"""Return the ATS Designation Threshold row for a designation, or None."""
		if not designation:
			return None
		for row in self.designation_thresholds:
			if row.designation == designation:
				return row
		return None

	def resolve_passmark(self, designation: str | None, opening: str | None = None) -> dict:
		"""Resolve the effective pass mark and fail behaviour for an applicant.

		Resolution order (highest priority first):
		  1. Job Opening 'ats_passmark_override' (per-opening) — overrides only the pass
		     mark number; the fail action is inherited from the designation's threshold
		     row so override openings auto-reject too (Flag for Review if no row exists).
		  2. Matching 'ATS Designation Threshold' row (per-designation)
		  3. 'default_passmark' (global) — never auto-rejects (Flag for Review).

		Returns {min_score, action_on_fail, required_keywords, source}.
		"""
		row = self._designation_row(designation)

		# 1. Per-opening override (pass mark number only; action inherited from designation).
		if opening:
			override = frappe.db.get_value("Job Opening", opening, "ats_passmark_override")
			if override:
				try:
					min_score = float(override)
				except (TypeError, ValueError):
					min_score = None
				if min_score is not None:
					return {
						"min_score": min_score,
						"action_on_fail": (row.action_on_fail if row else None) or "Flag for Review",
						"required_keywords": "",
						"source": "Job Opening override"
						+ (f" (action from {designation})" if row else ""),
					}

		# 2. Per-designation threshold
		if row:
			return {
				"min_score": float(row.min_score or 0),
				"action_on_fail": row.action_on_fail or "Flag for Review",
				"required_keywords": row.required_keywords or "",
				"source": f"Designation threshold ({designation})",
			}

		# 3. Global default
		return {
			"min_score": float(self.default_passmark or 0),
			"action_on_fail": "Flag for Review",
			"required_keywords": "",
			"source": "Default passmark",
		}

	def get_enabled_engines(self):
		return [
			{"engine": w.engine_name, "weight": int(w.weight_pct or 0)}
			for w in self.engine_weights
			if w.is_enabled and (w.weight_pct or 0) > 0
		]

	def get_synonym_map(self):
		mapping = {}
		for grp in self.synonym_groups:
			if not grp.is_active or not grp.canonical_term or not grp.synonyms:
				continue
			syns = [s.strip().lower() for s in grp.synonyms.split(",") if s.strip()]
			mapping[grp.canonical_term.strip().lower()] = syns
		return mapping

	def get_accepted_file_types(self):
		if not self.accepted_file_types:
			return ["pdf", "docx", "doc", "txt"]
		return [t.strip().lower().lstrip(".") for t in self.accepted_file_types.split(",") if t.strip()]
