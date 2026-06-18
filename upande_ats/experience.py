"""Work-history parsing + relevance classification for the ATS experience gate.

Given the resume text (already lowercased + whitespace-normalized by
engine.text_extract.normalize, with newlines preserved), this module:

  1. parse_work_history()  -> detect date ranges, turn each into a dated entry
     with the surrounding text as `context` and a computed duration in months.
  2. classify_relevance()  -> using the opening's keywords (+ ATS synonym groups),
     mark each entry relevant or not, merging overlapping periods so concurrent
     roles aren't double-counted.
  3. evaluate_experience() -> the single entry point the screening gate calls:
     returns total/relevant years, a confidence flag, and a display breakdown.

It never raises on messy input — unparseable resumes simply yield low confidence,
which the gate turns into "Flag for Review" rather than a reject.
"""

import re

import frappe
from frappe.utils import getdate

# Reuse the SAME term expansion + matching the keyword scorer uses, so relevance
# is judged consistently with the score.
from upande_ats.engine.keyword_frequency import _count_term, _expand_terms

_MONTH = (
	r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
	r"january|february|march|april|june|july|august|september|october|november|december)"
)

# A single point in time the parser understands.
_DATE_POINT = (
	r"(?:"
	r"" + _MONTH + r"\.?\s*,?\s*\d{4}"   # mon yyyy / month, yyyy
	r"|\d{1,2}\s*[/\-]\s*\d{4}"           # mm/yyyy or mm-yyyy
	r"|\d{4}"                              # bare year
	r")"
)

# An end point may also be "present"/open-ended.
_OPEN_ENDED = r"(?:present|current|now|ongoing|to\s*date|till\s*date|date|todate)"
_DATE_END = r"(?:" + _OPEN_ENDED + r"|" + _DATE_POINT + r")"

_SEP = r"\s*(?:–|—|-|to|until|till|through|thru)\s*"

# Full "start <sep> end" range.
_RANGE_RE = re.compile(
	r"(?P<start>" + _DATE_POINT + r")" + _SEP + r"(?P<end>" + _DATE_END + r")",
	flags=re.IGNORECASE,
)

# "since 2019" / "since jan 2019" -> open-ended from that point.
_SINCE_RE = re.compile(
	r"since\s+(?P<start>" + _DATE_POINT + r")",
	flags=re.IGNORECASE,
)

_MONTH_NUM = {
	"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
	"jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MIN_YEAR = 1950


def _today_ym():
	d = getdate()
	return d.year, d.month


def _parse_point(token: str):
	"""Parse a single date token to (year, month), or None if implausible.

	Open-ended tokens (present/current/...) resolve to today. Bare years and
	month-less tokens default to month=1 for clean, consistent durations.
	"""
	if not token:
		return None
	token = token.strip().rstrip(".,")

	if re.fullmatch(_OPEN_ENDED, token, flags=re.IGNORECASE):
		return _today_ym()

	# mm/yyyy or mm-yyyy
	m = re.fullmatch(r"(\d{1,2})\s*[/\-]\s*(\d{4})", token)
	if m:
		month, year = int(m.group(1)), int(m.group(2))
		if 1 <= month <= 12 and _plausible_year(year):
			return year, month
		return None

	# bare year
	if re.fullmatch(r"\d{4}", token):
		year = int(token)
		return (year, 1) if _plausible_year(year) else None

	# month-name year
	m = re.match(r"([a-z]+)\.?\s*,?\s*(\d{4})", token, flags=re.IGNORECASE)
	if m:
		mon = _MONTH_NUM.get(m.group(1).lower()[:4]) or _MONTH_NUM.get(m.group(1).lower()[:3])
		year = int(m.group(2))
		if mon and _plausible_year(year):
			return year, mon

	# Fall back to dateparser for anything fuzzier.
	try:
		import dateparser

		dt = dateparser.parse(token, settings={"PREFER_DAY_OF_MONTH": "first"})
		if dt and _plausible_year(dt.year):
			return dt.year, dt.month
	except Exception:
		pass
	return None


def _plausible_year(year: int) -> bool:
	now_year, _ = _today_ym()
	return _MIN_YEAR <= year <= now_year + 1


def _ym_index(ym) -> int:
	"""Map (year, month) to an absolute month index for arithmetic/merging."""
	year, month = ym
	return year * 12 + (month - 1)


def _months_between(start_ym, end_ym) -> int:
	return _ym_index(end_ym) - _ym_index(start_ym)


def _merge_months(intervals: list) -> int:
	"""Sum month-spans of [start_idx, end_idx] intervals, merging overlaps."""
	spans = sorted((s, e) for s, e in intervals if e > s)
	if not spans:
		return 0
	total = 0
	cur_s, cur_e = spans[0]
	for s, e in spans[1:]:
		if s <= cur_e:  # overlapping or contiguous
			cur_e = max(cur_e, e)
		else:
			total += cur_e - cur_s
			cur_s, cur_e = s, e
	total += cur_e - cur_s
	return total


def parse_work_history(resume_text: str) -> list:
	"""Return a list of entries: {context, start, end, months, open_ended}.

	`start`/`end` are (year, month) tuples; `context` is the line containing the
	date range plus its neighbouring lines (role title above, duties below).
	"""
	if not resume_text:
		return []

	lines = resume_text.split("\n")
	entries = []

	for i, line in enumerate(lines):
		matches = []

		for m in _RANGE_RE.finditer(line):
			start = _parse_point(m.group("start"))
			end_tok = m.group("end")
			end = _parse_point(end_tok)
			if not start or not end:
				continue
			open_ended = bool(re.fullmatch(_OPEN_ENDED, end_tok.strip(), flags=re.IGNORECASE))
			# Discard reversed/implausible ranges.
			if _months_between(start, end) <= 0 and not open_ended:
				continue
			matches.append((start, end, open_ended))

		for m in _SINCE_RE.finditer(line):
			start = _parse_point(m.group("start"))
			if start:
				matches.append((start, _today_ym(), True))

		if not matches:
			continue

		context = _context_for(lines, i)
		for start, end, open_ended in matches:
			months = max(0, _months_between(start, end))
			entries.append(
				{
					"context": context,
					"start": start,
					"end": end,
					"months": months,
					"open_ended": open_ended,
				}
			)

	return entries


def _context_for(lines: list, idx: int) -> str:
	"""Role title is usually on/above the date line; duties below. Grab a window."""
	lo = max(0, idx - 1)
	hi = min(len(lines), idx + 2)
	window = " ".join(l.strip() for l in lines[lo:hi] if l.strip())
	return re.sub(r"\s{2,}", " ", window).strip()


def _entry_is_relevant(context: str, keywords: list, synonym_map: dict) -> bool:
	"""True if the entry's context mentions any opening keyword (or its synonyms)."""
	if not context:
		return False
	for kw in keywords:
		term = (kw.get("keyword") or "").strip()
		if not term:
			continue
		for t in _expand_terms(term, synonym_map):
			if _count_term(context, t) > 0:
				return True
	return False


def classify_relevance(entries: list, keywords: list, synonym_map: dict) -> dict:
	"""Mark each entry relevant, merge overlaps, and build a display breakdown.

	Returns {total_months, relevant_months, breakdown, has_dated_entry,
	has_context, has_relevant_with_context}.
	"""
	breakdown = []
	all_intervals = []
	relevant_intervals = []
	has_context = False
	has_relevant_with_context = False

	for e in entries:
		ctx = e.get("context") or ""
		if ctx:
			has_context = True
		relevant = _entry_is_relevant(ctx, keywords, synonym_map)
		if relevant and ctx:
			has_relevant_with_context = True

		s_idx, e_idx = _ym_index(e["start"]), _ym_index(e["end"])
		all_intervals.append((s_idx, e_idx))
		if relevant:
			relevant_intervals.append((s_idx, e_idx))

		breakdown.append(
			{
				"role_context": _short(ctx),
				"months": e["months"],
				"relevant": relevant,
			}
		)

	return {
		"total_months": _merge_months(all_intervals),
		"relevant_months": _merge_months(relevant_intervals),
		"breakdown": breakdown,
		"has_dated_entry": bool(entries),
		"has_context": has_context,
		"has_relevant_with_context": has_relevant_with_context,
	}


def _short(text: str, limit: int = 90) -> str:
	text = (text or "").strip()
	return text if len(text) <= limit else text[: limit - 1] + "…"


def evaluate_experience(resume_text: str, keywords: list, synonym_map: dict) -> dict:
	"""Single entry point for the screening gate.

	Returns:
	  {
	    total_years, relevant_years,   # floats, rounded to 1dp
	    confident,                     # bool — see Phase 3 confidence rules
	    breakdown,                     # [{role_context, months, relevant}]
	  }

	Confidence:
	  Confident  = at least one entry had a cleanly parsed date range AND
	               relevance could be judged (entries had usable context).
	  Not conf.  = no parseable date ranges, OR entries had no context to confirm
	               relevance, OR layout yielded nothing usable.
	"""
	entries = parse_work_history(resume_text)
	cls = classify_relevance(entries, keywords or [], synonym_map or {})

	confident = bool(cls["has_dated_entry"] and cls["has_context"])

	return {
		"total_years": round(cls["total_months"] / 12.0, 1),
		"relevant_years": round(cls["relevant_months"] / 12.0, 1),
		"confident": confident,
		"breakdown": cls["breakdown"],
	}
