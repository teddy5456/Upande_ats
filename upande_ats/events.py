import frappe


def maybe_screen_on_update(doc, method=None):
	"""on_update hook: re-screen a Job Applicant when its resume changes.

	The initial insert is handled by screening.enqueue_screening (after_insert),
	so this skips brand-new docs and only fires on a genuine resume change.
	"""
	previous = doc.get_doc_before_save()
	if previous is None:
		# Brand-new doc — after_insert already enqueues screening. Don't double-run.
		return

	try:
		settings = frappe.get_single("ATS Settings")
	except Exception:
		return

	if not bool(settings.auto_score_on_resume_upload):
		return
	if not doc.get("resume_attachment") or not doc.get("job_title"):
		return
	if previous.get("resume_attachment") == doc.get("resume_attachment"):
		# Resume unchanged — nothing to re-score.
		return

	try:
		frappe.enqueue(
			"upande_ats.screening.run_screening",
			queue="short",
			job_name=f"ats-screen-{doc.name}",
			enqueue_after_commit=True,
			applicant=doc.name,
		)
	except Exception:
		# Redis unavailable (dev/console). Log and skip — use the Re-run Screening button.
		frappe.log_error(frappe.get_traceback(), "ATS re-screen enqueue failed")
