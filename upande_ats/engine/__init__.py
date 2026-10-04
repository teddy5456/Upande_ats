import json

import frappe
from frappe.utils import now_datetime

from .fuzzy_match import score_fuzzy
from .keyword_frequency import score_keyword_frequency
from .text_extract import extract_text
from .tfidf_cosine import score_tfidf

# 1.1.0 — block-based role parsing (docx layouts), education excluded from
# relevant experience, OCR fallback for scanned PDFs. Scores written by an older
# version are re-run by patches.rescore_stale_experience.
ENGINE_VERSION = "1.1.0"


def _get_settings():
	return frappe.get_single("ATS Settings")


def _keywords_for_opening(opening_name: str) -> list:
	opening = frappe.get_doc("Job Opening", opening_name)
	out = []
	for row in (opening.get("ats_keywords") or []):
		out.append({
			"keyword": row.keyword,
			"weight": row.weight,
			"is_mandatory": row.is_mandatory,
			"category": row.category,
		})
	return out


def _resume_url(applicant_doc) -> str:
	# Prefer the uploaded file (Attach); fall back to a local Resume Link if set.
	return applicant_doc.get("resume_attachment") or applicant_doc.get("resume_link") or ""


def _passmark_for_opening(opening_name: str, settings) -> float:
	override = frappe.db.get_value("Job Opening", opening_name, "ats_passmark_override")
	if override:
		try:
			return float(override)
		except (TypeError, ValueError):
			pass
	return float(settings.default_passmark or 0)


def _run_engines(text: str, keywords: list, settings) -> dict:
	synonym_map = settings.get_synonym_map()
	enabled = settings.get_enabled_engines()
	results = {}

	kw_freq_data = None
	for cfg in enabled:
		name = cfg["engine"]
		if name == "Keyword Frequency":
			kw_freq_data = score_keyword_frequency(text, keywords, synonym_map)
			results[name] = kw_freq_data
		elif name == "TF-IDF Cosine":
			results[name] = score_tfidf(text, keywords, synonym_map)
		elif name == "Fuzzy Match":
			results[name] = score_fuzzy(
				text, keywords, synonym_map, threshold=int(settings.fuzzy_match_threshold or 85)
			)

	weighted_total = 0.0
	for cfg in enabled:
		sub = results.get(cfg["engine"], {}).get("sub_score", 0.0)
		weighted_total += sub * (cfg["weight"] / 100.0)

	mandatory_unmatched = []
	if kw_freq_data is not None:
		mandatory_unmatched = kw_freq_data.get("mandatory_unmatched", [])
	else:
		check = score_keyword_frequency(text, keywords, synonym_map)
		mandatory_unmatched = check.get("mandatory_unmatched", [])

	final_score = weighted_total
	rule = settings.mandatory_keyword_rule or "Cap Score"
	if mandatory_unmatched:
		if rule == "Cap Score":
			final_score = min(weighted_total, float(settings.mandatory_cap or 40))
		elif rule == "Zero Score":
			final_score = 0.0

	return {
		"final_score": round(final_score, 2),
		"weighted_total": round(weighted_total, 2),
		"engines": results,
		"mandatory_unmatched": mandatory_unmatched,
		"enabled_engines": enabled,
	}


def score_applicant(applicant: str, opening: str | None = None) -> dict | None:
	"""Score a Job Applicant against a Job Opening (defaults to applicant.job_title)."""
	applicant_doc = frappe.get_doc("Job Applicant", applicant)
	opening = opening or applicant_doc.get("job_title")
	if not opening or not frappe.db.exists("Job Opening", opening):
		return None

	settings = _get_settings()
	keywords = _keywords_for_opening(opening)
	if not keywords:
		return None

	resume_url = _resume_url(applicant_doc)
	text = extract_text(resume_url)

	min_chars = int(settings.min_resume_chars or 0)
	note = ""
	if not text or len(text) < min_chars:
		note = "Unparseable or too-short resume; score set to 0."
		result = {
			"final_score": 0.0,
			"weighted_total": 0.0,
			"engines": {},
			"mandatory_unmatched": [k["keyword"] for k in keywords if k.get("is_mandatory")],
			"enabled_engines": settings.get_enabled_engines(),
		}
	else:
		result = _run_engines(text, keywords, settings)

	passmark = _passmark_for_opening(opening, settings)
	passes = result["final_score"] >= passmark

	score_doc = _persist_score(
		applicant_doc=applicant_doc,
		opening=opening,
		result=result,
		passmark=passmark,
		passes=passes,
		resume_url=resume_url,
		settings=settings,
		note=note,
	)

	_update_applicant_denorm(applicant_doc, score_doc, passes)
	return score_doc.as_dict()


def _persist_score(*, applicant_doc, opening, result, passmark, passes, resume_url, settings, note):
	if not bool(settings.keep_score_history):
		frappe.db.delete(
			"ATS Score", {"applicant": applicant_doc.name, "job_opening": opening}
		)

	settings_snapshot = {
		"engines": result["enabled_engines"],
		"fuzzy_match_threshold": int(settings.fuzzy_match_threshold or 85),
		"mandatory_rule": settings.mandatory_keyword_rule,
		"mandatory_cap": float(settings.mandatory_cap or 0),
		"passmark": passmark,
	}

	doc = frappe.new_doc("ATS Score")
	doc.applicant = applicant_doc.name
	doc.job_opening = opening
	doc.score_pct = result["final_score"]
	doc.passes_passmark = 1 if passes else 0
	doc.passmark_used = passmark
	doc.score_breakdown = json.dumps(result["engines"], default=str, indent=2)
	doc.mandatory_unmatched = ", ".join(result["mandatory_unmatched"])
	doc.settings_snapshot = json.dumps(settings_snapshot, default=str, indent=2)
	doc.scored_on = now_datetime()
	doc.resume_file = resume_url
	doc.engine_version = ENGINE_VERSION
	doc.note = note
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc


def _update_applicant_denorm(applicant_doc, score_doc, passes):
	frappe.db.set_value(
		"Job Applicant",
		applicant_doc.name,
		{
			"ats_score": score_doc.score_pct,
			"ats_passes_passmark": 1 if passes else 0,
			"ats_score_link": score_doc.name,
		},
		update_modified=False,
	)


@frappe.whitelist()
def score_applicant_now(applicant: str, opening: str | None = None) -> dict | None:
	return score_applicant(applicant, opening)


@frappe.whitelist()
def rescore_opening(opening: str) -> dict:
	"""Enqueue scoring for every applicant linked to this opening."""
	applicants = frappe.get_all(
		"Job Applicant",
		filters={"job_title": opening},
		pluck="name",
	)
	for app in applicants:
		frappe.enqueue(
			"upande_ats.engine.score_applicant",
			queue="long",
			job_name=f"ats-score-{app}",
			applicant=app,
			opening=opening,
		)
	return {"queued": len(applicants)}
