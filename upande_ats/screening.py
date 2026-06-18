"""Applicant screening: score a resume, then set the Job Applicant's status/result.

Wired from hooks.py on Job Applicant after_insert (and resume changes). Scoring
itself lives in upande_ats.engine.score_applicant; this module wraps it and
applies the HR-configured pass mark + fail behaviour.
"""

import json

import frappe
from frappe.utils import now_datetime

from upande_ats.engine import _keywords_for_opening, score_applicant
from upande_ats.engine.text_extract import extract_text
from upande_ats.experience import evaluate_experience

# Statuses set by a human (or downstream of one) that automation must never overwrite.
PROTECTED_STATUSES = {"Replied", "Shortlisted", "Hold", "Accepted", "Interview"}

# Map the experience gate's internal state to the ATS Score snapshot's Select value.
_EXP_STATE_TO_STATUS = {
	"off": "Off",
	"pass": "Pass",
	"fail": "Fail",
	"uncertain": "Needs Review",
}


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


def _set_applicant(
	applicant: str,
	*,
	status=None,
	result=None,
	reason=None,
	experience=None,
	auto_rejected=None,
):
	"""Write screening outcome via db.set_value to avoid re-triggering doc hooks.

	`experience`, when given, is the dict returned by the experience gate; its
	detected figures + breakdown are denormalized onto the applicant for HR.
	`auto_rejected`, when not None, sets the ats_auto_rejected flag (1 when ATS
	rejected the applicant, 0 when a clean re-run un-rejected them).
	"""
	values = {
		"ats_result": result,
		"ats_reason": reason,
		"ats_screened_on": now_datetime(),
	}
	if status is not None:
		values["status"] = status
	if auto_rejected is not None:
		values["ats_auto_rejected"] = auto_rejected
	if experience and experience.get("state") != "off":
		values["ats_total_experience"] = experience.get("total_years")
		values["ats_relevant_experience"] = experience.get("relevant_years")
		values["ats_experience_breakdown"] = json.dumps(
			experience.get("breakdown") or [], default=str
		)
	frappe.db.set_value("Job Applicant", applicant, values, update_modified=False)
	frappe.db.commit()


def _persist_experience_snapshot(score_name: str | None, exp: dict):
	"""Freeze the resolved experience figures onto the ATS Score record being written,
	so historical breakdowns stay accurate and the dialog can read them back (Change 2)."""
	if not score_name:
		return
	frappe.db.set_value(
		"ATS Score",
		score_name,
		{
			"relevant_experience": exp.get("relevant_years") or 0,
			"total_experience": exp.get("total_years") or 0,
			"required_experience": exp.get("required") or 0,
			"experience_status": _EXP_STATE_TO_STATUS.get(exp.get("state"), "Off"),
			"experience_breakdown": json.dumps(exp.get("breakdown") or [], default=str),
		},
		update_modified=False,
	)


def _evaluate_experience_gate(opening, designation, settings, resume_text) -> dict:
	"""Resolve the required relevant-years and judge the CV against it.

	Returns {state, required, total_years, relevant_years, confident, breakdown}.
	`state` is one of:
	  "off"       — no requirement configured anywhere; gate skipped.
	  "pass"      — confident the relevant experience meets the requirement.
	  "fail"      — confident the relevant experience is below the requirement.
	  "uncertain" — couldn't parse dates / confirm relevance; never auto-reject.
	"""
	req = settings.resolve_required_experience(designation, opening)
	required = req["required_years"]
	if not required:
		return {"state": "off", "required": None}

	keywords = _keywords_for_opening(opening)
	synonym_map = settings.get_synonym_map()
	ev = evaluate_experience(resume_text, keywords, synonym_map)

	if not ev["confident"]:
		state = "uncertain"
	elif ev["relevant_years"] >= required:
		state = "pass"
	else:
		state = "fail"

	return {
		"state": state,
		"required": required,
		"total_years": ev["total_years"],
		"relevant_years": ev["relevant_years"],
		"confident": ev["confident"],
		"breakdown": ev["breakdown"],
	}


def _experience_phrase(exp: dict, role_label: str) -> str:
	"""Human-readable description of the experience finding (state != off)."""
	required = exp["required"]
	relevant = exp.get("relevant_years") or 0
	total = exp.get("total_years") or 0
	if exp["state"] == "uncertain":
		if total:
			return (
				f"{total:g} yrs total experience detected but couldn't confirm relevant "
				f"experience for {role_label} (required {required:g})"
			)
		return (
			f"couldn't parse the work history to confirm relevant experience for "
			f"{role_label} (required {required:g})"
		)
	return f"{relevant:g} relevant years detected (required {required:g})"


@frappe.whitelist()
def run_screening(applicant: str, method=None) -> dict:
	"""Score the resume, apply the pass-mark verdict, then the experience gate.

	Keyword verdict (unchanged): score >= min_score AND no missing required keywords.
	Experience gate (additional, only when a requirement is configured): the CV's
	*relevant* experience must meet the minimum, judged only when we're confident.

	Final outcome (product decision: anything that is not a clean Pass rejects):
	  - Pass                          -> result Pass; un-reject an auto-rejected applicant
	                                     (Rejected -> Open, clear flag) but never touch a human
	                                     status (Shortlisted/Hold/Accepted/Replied/Interview) and
	                                     never un-reject a human rejection (ats_auto_rejected == 0).
	  - keyword gate fails, or
	    experience confidently below  -> result Fail; status -> Rejected, ats_auto_rejected = 1.
	  - keyword passed but experience
	    couldn't be confirmed          -> result Needs Review; status -> Rejected, ats_auto_rejected = 1.
	  - protected (human) status       -> result recorded, status left as-is (no auto-reject).
	  - no opening/keywords/resume     -> result Not Scored; status untouched.
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
	score_name = result.get("name")

	# Extract resume text once; reused by the required-keyword filter and the experience gate.
	resume_text = extract_text(doc.get("resume_attachment"))

	# --- Keyword gate (unchanged semantics) ---
	missing_required = []
	if rule["required_keywords"]:
		missing_required = _missing_required_keywords(rule["required_keywords"], resume_text)
	keyword_passes = score >= min_score and not missing_required

	if missing_required:
		keyword_msg = f"Missing required keyword(s) for {role_label}: {', '.join(missing_required)}"
	elif keyword_passes:
		keyword_msg = f"scored {score:.0f}% (pass mark {min_score:.0f}%)"
	else:
		keyword_msg = f"scored {score:.0f}%, below the {min_score:.0f}% pass mark for {role_label}"

	# --- Experience gate (only when a requirement is configured for this role) ---
	exp = _evaluate_experience_gate(opening, designation, settings, resume_text)
	exp_pass = exp["state"] in ("off", "pass")

	# --- Combine verdicts ---
	if keyword_passes and exp_pass:
		parts = [f"Scored {score:.0f}%, meets the {min_score:.0f}% pass mark for {role_label}"]
		if exp["state"] == "pass":
			parts.append(_experience_phrase(exp, role_label))
		reason = ". ".join(parts) + "."
		# Un-reject ONLY an applicant ATS itself rejected (Change 4); never override a human
		# decision (a manual Rejected with ats_auto_rejected == 0, or a protected status).
		new_status = None
		auto_rejected = None
		if doc.status == "Rejected" and doc.get("ats_auto_rejected"):
			new_status = "Open"
			auto_rejected = 0
		_set_applicant(
			applicant,
			status=new_status,
			result="Pass",
			reason=reason,
			experience=exp,
			auto_rejected=auto_rejected,
		)
		_persist_experience_snapshot(score_name, exp)
		return {"result": "Pass", "score": score, "min_score": min_score, "reason": reason}

	# Build the cause fragments shared by the reject paths.
	causes = []
	if not keyword_passes:
		causes.append(keyword_msg)
	if exp["state"] == "fail":
		causes.append(_experience_phrase(exp, role_label))
	uncertain = exp["state"] == "uncertain"
	if uncertain and keyword_passes:
		# Keyword side is fine; the only reason we're not passing is unconfirmed experience.
		causes.append(_experience_phrase(exp, role_label))

	# Result classification splits the two reports: keyword-qualified candidates whose ONLY
	# problem is unconfirmed experience are "Needs Review"; every other non-pass is "Fail".
	result_label = "Needs Review" if (keyword_passes and uncertain) else "Fail"

	# Product decision: any non-pass outcome rejects, unless a human already set a protected
	# status — then we only record the result/reason and leave their decision alone.
	if doc.status in PROTECTED_STATUSES:
		reason = "; ".join(causes) + " — flagged (status set by HR, left unchanged)."
		_set_applicant(applicant, result=result_label, reason=reason, experience=exp)
		_persist_experience_snapshot(score_name, exp)
		return {"result": result_label, "action": "Flagged", "score": score, "reason": reason}

	tail = " — needs review; rejected by default." if result_label == "Needs Review" else " — rejected."
	reason = "; ".join(causes) + tail
	_set_applicant(
		applicant,
		status="Rejected",
		result=result_label,
		reason=reason,
		experience=exp,
		auto_rejected=1,
	)
	_persist_experience_snapshot(score_name, exp)
	return {"result": result_label, "action": "Rejected", "score": score, "reason": reason}
