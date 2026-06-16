"""Applicant screening: score a resume, then set the Job Applicant's status/result.

Wired from hooks.py on Job Applicant after_insert (and resume changes). Scoring
itself lives in upande_ats.engine.score_applicant; this module wraps it and
applies the HR-configured pass mark + fail behaviour.
"""

import frappe
from frappe.utils import now_datetime

from upande_ats.engine import score_applicant
from upande_ats.engine.text_extract import extract_text

# Statuses set by a human (or downstream of one) that automation must never overwrite.
PROTECTED_STATUSES = {"Replied", "Shortlisted", "Hold", "Accepted"}


def enqueue_screening(doc, method=None):
	"""after_insert hook: run screening in the background so web-form submit returns fast."""
	try:
		frappe.enqueue(
			"upande_ats.screening.run_screening",
			queue="short",
			job_name=f"ats-screen-{doc.name}",
			enqueue_after_commit=True,
			applicant=doc.name,
		)
	except Exception:
		# Redis unavailable (dev/console). Log and skip; HR can use the Re-run Screening button.
		frappe.log_error(frappe.get_traceback(), "ATS screening enqueue failed")


def _missing_required_keywords(required_keywords: str, resume_text: str) -> list:
	if not required_keywords:
		return []
	text = resume_text or ""
	missing = []
	for raw in required_keywords.split(","):
		kw = raw.strip()
		if kw and kw.lower() not in text:
			missing.append(kw)
	return missing


def _set_applicant(applicant: str, *, status=None, result=None, reason=None):
	"""Write screening outcome via db.set_value to avoid re-triggering doc hooks."""
	values = {
		"ats_result": result,
		"ats_reason": reason,
		"ats_screened_on": now_datetime(),
	}
	if status is not None:
		values["status"] = status
	frappe.db.set_value("Job Applicant", applicant, values, update_modified=False)
	frappe.db.commit()


@frappe.whitelist()
def run_screening(applicant: str, method=None) -> dict:
	"""Score the applicant's resume and apply the configured pass-mark verdict.

	- score >= min_score            -> result Pass; un-reject (Rejected -> Open) but never
	                                    touch a human status (Shortlisted/Hold/Accepted/Replied).
	- score <  min_score, Reject    -> result Fail; status -> Rejected.
	- score <  min_score, Flag      -> result Fail; status untouched, reason flags for review.
	- no opening/keywords/resume    -> result Not Scored; status untouched.
	"""
	doc = frappe.get_doc("Job Applicant", applicant)
	opening = doc.get("job_title")
	designation = doc.get("designation") or (
		frappe.db.get_value("Job Opening", opening, "designation") if opening else None
	)

	settings = frappe.get_single("ATS Settings")
	rule = settings.resolve_passmark(designation, opening)
	min_score = rule["min_score"]
	role_label = designation or opening or "this role"

	# Ensure a score exists (re-scores so a freshly edited resume/threshold is honoured).
	result = score_applicant(applicant, opening)
	if not result:
		reason = "Not scored: no linked Job Opening, no ATS keywords on the opening, or no resume."
		_set_applicant(applicant, result="Not Scored", reason=reason)
		return {"result": "Not Scored", "reason": reason}

	score = float(result.get("score_pct") or 0)

	# Optional hard filter: required keywords must be present in the resume text.
	missing_required = []
	if rule["required_keywords"]:
		resume_text = extract_text(doc.get("resume_attachment"))
		missing_required = _missing_required_keywords(rule["required_keywords"], resume_text)

	passes = score >= min_score and not missing_required

	if passes:
		reason = f"Scored {score:.0f}%, meets the {min_score:.0f}% pass mark for {role_label}."
		# Un-reject a previously auto-rejected applicant, but never override a human decision.
		new_status = "Open" if doc.status == "Rejected" else None
		_set_applicant(applicant, status=new_status, result="Pass", reason=reason)
		return {"result": "Pass", "score": score, "min_score": min_score, "reason": reason}

	# Failed.
	if missing_required:
		reason = (
			f"Missing required keyword(s) for {role_label}: {', '.join(missing_required)}."
		)
	else:
		reason = f"Scored {score:.0f}%, below the {min_score:.0f}% pass mark for {role_label}."

	if rule["action_on_fail"] == "Reject" and doc.status not in PROTECTED_STATUSES:
		_set_applicant(applicant, status="Rejected", result="Fail", reason=reason)
		return {"result": "Fail", "action": "Rejected", "score": score, "reason": reason}

	# Flag for Review (default): leave status, just record the verdict.
	flag_reason = f"{reason} Flagged for review."
	_set_applicant(applicant, result="Fail", reason=flag_reason)
	return {"result": "Fail", "action": "Flag for Review", "score": score, "reason": flag_reason}
