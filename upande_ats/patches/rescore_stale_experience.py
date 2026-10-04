# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Engine 1.1.0 changed how work history is parsed (role blocks instead of the
# date's own line, education excluded from relevant experience) and added the
# OCR fallback for scanned PDFs. Every applicant scored by an older engine
# therefore carries stale experience figures — .docx CVs worst of all, where
# every role came back "not relevant". Re-screen them through the normal entry
# point so the verdict, the denormalized fields and the ATS Score snapshot are
# all rebuilt the same way a manual "Re-run Screening" would.
#
# Idempotent: once re-screened, an applicant's live score carries the current
# engine version and is skipped on the next migrate.

import frappe

from upande_ats.engine import ENGINE_VERSION


def execute():
	applicants = _stale_applicants()
	if not applicants:
		return

	queued, ran = 0, 0
	for applicant in applicants:
		try:
			frappe.enqueue(
				"upande_ats.screening.run_screening",
				queue="long",
				job_name=f"ats-rescore-{applicant}",
				applicant=applicant,
			)
			queued += 1
		except Exception:
			# No worker/redis (local bench, some CI images): do it inline rather
			# than leaving the figures stale.
			try:
				frappe.get_attr("upande_ats.screening.run_screening")(applicant)
				ran += 1
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"ATS re-score failed: {applicant}")

	frappe.db.commit()
	print(f"upande_ats: re-scoring {len(applicants)} applicant(s) — {queued} queued, {ran} inline")


def _stale_applicants() -> list:
	"""Applicants whose live ATS Score predates the current engine, plus any
	.docx resume that was never scored at all."""
	names = set()

	stale_scores = frappe.get_all(
		"ATS Score",
		filters={"engine_version": ("!=", ENGINE_VERSION)},
		pluck="applicant",
	)
	names.update(n for n in stale_scores if n)

	for field in ("resume_attachment", "resume_link"):
		names.update(
			frappe.get_all(
				"Job Applicant",
				filters={field: ("like", "%.docx")},
				pluck="name",
			)
		)

	# Screening needs a linked opening; without one it would only stamp
	# "Not Scored" over records this patch has no business touching.
	if not names:
		return []
	return frappe.get_all(
		"Job Applicant",
		filters={"name": ("in", list(names)), "job_title": ("is", "set")},
		pluck="name",
		order_by="name asc",
	)
