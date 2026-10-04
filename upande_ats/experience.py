"""Work-history parsing + relevance classification for the ATS experience gate.

Given the resume text (already lowercased + whitespace-normalized by
engine.text_extract.normalize, with newlines preserved), this module:

  1. parse_work_history()  -> detect date ranges, turn each into a dated entry
     whose `context` is the whole role *block* (title/company heading above the
     date, plus the duty lines below it) and a computed duration in months.
  2. classify_relevance()  -> using the opening's keywords (+ ATS synonym groups),
     mark each entry relevant or not, merging overlapping periods so concurrent
     roles aren't double-counted.
  3. evaluate_experience() -> the single entry point the screening gate calls:
     returns total/relevant years, a confidence flag, and a display breakdown.

It never raises on messy input — unparseable resumes simply yield low confidence,
which the gate turns into "Flag for Review" rather than a reject.
"""

import re
from datetime import date, datetime

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
	r"" + _MONTH + r"\.?\s*,?\s*\d{4}"           # mon yyyy / month, yyyy
	r"|\d{4}\s*[,\-/ ]?\s*" + _MONTH +            # yyyy mon (year-first, e.g. "2023 mar")
	r"|\d{1,2}\s*[/\-]\s*\d{4}"                   # mm/yyyy or mm-yyyy
	r"|\d{4}"                                      # bare year
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

	# year-first: yyyy <month>  (e.g. "2023 mar", "2005, june", "2021-may")
	m = re.match(r"(\d{4})\s*[,\-/ ]?\s*([a-z]+)", token, flags=re.IGNORECASE)
	if m:
		year = int(m.group(1))
		mon = _MONTH_NUM.get(m.group(2).lower()[:4]) or _MONTH_NUM.get(m.group(2).lower()[:3])
		if mon and _plausible_year(year):
			return year, mon

	# month-name year
	m = re.match(r"([a-z]+)\.?\s*,?\s*(\d{4})", token, flags=re.IGNORECASE)
	if m:
		mon = _MONTH_NUM.get(m.group(1).lower()[:4]) or _MONTH_NUM.get(m.group(1).lower()[:3])
		year = int(m.group(2))
		if mon and _plausible_year(year):
			return year, mon

	# Fall back to a stdlib parse for anything the fast paths missed.
	dt = _parse_date(token)
	if dt and _plausible_year(dt.year):
		return dt.year, dt.month
	return None


# Open-ended end points that resolve to "today" when parsed standalone.
_OPEN_ENDED_TOKENS = {"present", "now", "current", "to date", "since"}


def _parse_date(token: str):
	"""Convert a single matched token into a date, using only the stdlib.

	Tries, in order: "%B %Y"/"%b %Y" (February 2020 / Feb 2020), "%m/%Y"
	(02/2020), a bare 4-digit year (-> Jan 1 of that year), and the open-ended
	words (present/now/current/to date/since -> today). Returns None if nothing
	matches. Day is normalised to the 1st for clean, consistent durations.
	"""
	if not token:
		return None
	token = token.strip().rstrip(".,")
	if not token:
		return None

	if token.lower() in _OPEN_ENDED_TOKENS:
		d = getdate()
		return date(d.year, d.month, d.day)

	for fmt in ("%B %Y", "%b %Y"):
		try:
			return datetime.strptime(token, fmt).date()
		except ValueError:
			pass

	try:
		return datetime.strptime(token, "%m/%Y").date()
	except ValueError:
		pass

	if re.fullmatch(r"\d{4}", token):
		return date(int(token), 1, 1)

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


# --- Layout: sections, bullets, role blocks ----------------------------------
#
# A role's title, its dates and its duties do NOT reliably share a physical line.
# pdfplumber often glues them into one line; python-docx/docx2txt emit each
# paragraph separately, so the date frequently sits alone. Judging relevance off
# the date's own line therefore worked for PDFs and silently failed for .docx.
# Everything below normalises both layouts into the same thing: a role *block*
# made of the heading above the date, the date line, and the duty lines below.

# A heading is a short line; anything longer is prose (a duty sentence).
_MAX_HEAD_LINES = 2
_MAX_HEAD_LEN = 120
# Duty lists are rarely longer than this; the cap stops one missing date from
# swallowing the rest of the CV.
_MAX_BODY_LINES = 10

_HEADER_MAX_LEN = 60
_HEADER_MAX_WORDS = 5

# Section headers are matched word-by-word rather than against fixed phrases:
# real CVs write "CERTIFICATION & EDUCATION", "Academic and Professional
# Qualifications", "Additional Information". Every word must be known, which is
# what keeps a job title ("Training Officer") from being read as a header.
_HEADER_NEUTRAL = {
	"a", "and", "of", "in", "my", "the", "to", "with", "other", "others",
	"additional", "further", "more", "professional", "personal", "core", "key",
	"main", "relevant", "detailed", "brief", "background", "details", "detail",
	"section", "area", "areas",
}
_HEADER_EXPERIENCE = {
	"experience", "experiences", "employment", "employments", "work", "working",
	"history", "career", "record", "records", "attachment", "attachments",
	"internship", "internships", "job", "jobs", "engagements", "engagement",
	"exposure", "positions", "roles",
}
_HEADER_EDUCATION = {
	"education", "educational", "academic", "academics", "qualification",
	"qualifications", "certification", "certifications", "certificate",
	"certificates", "schooling", "school", "training", "trainings", "course",
	"courses", "studies", "development",
}
_HEADER_OTHER = {
	"skills", "skill", "competencies", "competency", "competence", "strengths",
	"referees", "referee", "references", "reference", "hobbies", "interests",
	"information", "profile", "objective", "objectives", "summary", "languages",
	"language", "achievements", "achievement", "awards", "award", "membership",
	"memberships", "affiliations", "publications", "declaration", "contact",
	"contacts", "technical", "computer", "attributes", "abilities", "activities",
}

_BULLET_RE = re.compile(
	r"^\s*(?:[•▪●○◦‣➢❯❖♦⁃·"
	r"–—\-\*\+>o]\s+|\d{1,2}[.)]\s+)"
)

# Schooling markers — a block carrying these and no employer/duty signal is a
# qualification row, not a job, however many keywords it happens to contain.
_EDU_SIGNALS_RE = re.compile(
	r"(?<![a-z])(?:university|universities|college|polytechnic|institute|"
	r"school|academy|campus|bachelor|bachelors|b\.?sc|b\.?com|b\.?a\.?|"
	r"m\.?sc|mba|master|masters|phd|diploma|certificate|degree|"
	r"cpa|cpa-?k|acca|cifa|kasneb|kcse|kcpe|"
	r"undergraduate|postgraduate|graduated|form four|"
	r"part\s+(?:i{1,3}|iv|[1-4])|section\s+[1-6])(?![a-z])"
)

# Employer / duty markers — presence means the block describes a job. Job
# *titles* are deliberately absent: "certified public accountant, part 1" is a
# qualification, not an employer, and would otherwise rescue every CPA row.
_WORK_SIGNALS_RE = re.compile(
	r"(?<![a-z])(?:ltd|limited|plc|inc|llc|company|co|corporation|enterprises|"
	r"holdings|group|sacco|bank|hotel|hospital|agency|firm|farm|farms|flowers|"
	r"duties|responsibilities|responsible|reporting|report|reports|managed|manage|"
	r"managing|prepared|preparing|reconciliation|reconciliations|reconciled|"
	r"reconciling|invoicing|invoices|invoice|payroll|payments|supervised|"
	r"supervising|assisted|assisting|maintained|maintaining|processed|processing|"
	r"handled|handling|liaised|liaise|internship|attachment|intern|"
	r"employer|employed|position|role)(?![a-z])"
)

# A clause that opens with a negation asserts the absence of something; matching
# keywords inside it would count "no floriculture exposure in this role" as
# floriculture experience.
_NEGATED_CLAUSE_RE = re.compile(r"^(?:no|not|none|never|without|nil)(?![a-z])")


def _header_kind(line: str) -> str:
	"""Return 'experience' / 'education' / 'other' if the line is a section header."""
	text = line.strip()
	if len(text) > _HEADER_MAX_LEN or _RANGE_RE.search(text):
		return ""

	words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in _HEADER_NEUTRAL]
	if not words or len(words) > _HEADER_MAX_WORDS:
		return ""

	kinds = set()
	for word in words:
		if word in _HEADER_EXPERIENCE:
			kinds.add("experience")
		elif word in _HEADER_EDUCATION:
			kinds.add("education")
		elif word in _HEADER_OTHER:
			kinds.add("other")
		else:
			# An unknown word means this is content, not a section header.
			return ""

	# A combined header ("Education and Work Experience") must not hide the jobs
	# under it, so experience wins.
	if "experience" in kinds:
		return "experience"
	if "education" in kinds:
		return "education"
	return "other"


def _clean_lines(resume_text: str) -> list:
	"""Split into stripped, non-empty lines — the shared shape for every format."""
	out = []
	for raw in (resume_text or "").split("\n"):
		line = re.sub(r"\s{2,}", " ", raw.strip())
		if line:
			out.append(line)
	return out


def _scan_lines(lines: list) -> list:
	"""Annotate each line with its section, header-ness and bullet-ness."""
	meta = []
	section = ""
	for text in lines:
		kind = _header_kind(text)
		if kind:
			section = kind
		meta.append(
			{
				"text": text,
				"is_header": bool(kind),
				"section": section,
				"is_bullet": bool(_BULLET_RE.match(text)),
			}
		)
	return meta


def _date_ranges_in(line: str) -> list:
	"""Every (start, end, open_ended) range on this line. Months logic unchanged."""
	matches = []

	for m in _RANGE_RE.finditer(line):
		start = _parse_point(m.group("start"))
		end_tok = m.group("end")
		end = _parse_point(end_tok)
		# A role is unparseable only if BOTH ends fail. If just the end
		# didn't parse, treat the range as running to today; without a
		# start there's no anchor to measure from, so drop it.
		if not start and not end:
			continue
		open_ended = bool(re.fullmatch(_OPEN_ENDED, end_tok.strip(), flags=re.IGNORECASE))
		if not end:
			end = _today_ym()
			open_ended = True
		if not start:
			continue
		# Discard reversed/implausible ranges.
		if _months_between(start, end) <= 0 and not open_ended:
			continue
		matches.append((start, end, open_ended))

	for m in _SINCE_RE.finditer(line):
		start = _parse_point(m.group("start"))
		if start:
			matches.append((start, _today_ym(), True))

	return matches


def _looks_like_prose(text: str) -> bool:
	"""A finished sentence — i.e. a duty line, not a title/company heading."""
	return text.endswith((".", ";", "!")) and len(text.split()) >= 6


def _heading_start(meta: list, idx: int, prev_idx: int, date_lines: set) -> int:
	"""Index of the first heading line belonging to the date line at `idx`.

	Walks up from the date line while lines still look like a title/company
	heading — short, not prose, not a bullet, not a section header, not another
	date line. Returns `idx` itself when the date line carries its own heading
	(the glued single-line layout pdfplumber produces).
	"""
	start = idx
	j = idx - 1
	while j > prev_idx and (idx - j) <= _MAX_HEAD_LINES:
		m = meta[j]
		if m["is_header"] or m["is_bullet"] or j in date_lines:
			break
		if len(m["text"]) > _MAX_HEAD_LEN or _looks_like_prose(m["text"]):
			break
		start = j
		j -= 1
	return start


def _is_education_block(section: str, text: str) -> bool:
	"""True when the block is a qualification row rather than a job.

	Anything under an Education/Qualifications header counts. Elsewhere (headers
	are often missing or unrecognised) a block is education only when it carries
	schooling markers and no employer or duty signal at all — so an accounting
	job that merely mentions a degree still counts as work.
	"""
	if section == "education":
		return True
	if not text:
		return False
	return bool(_EDU_SIGNALS_RE.search(text)) and not _WORK_SIGNALS_RE.search(text)


def parse_work_history(resume_text: str) -> list:
	"""Return a list of entries: {context, label, start, end, months, open_ended,
	is_education}.

	`start`/`end` are (year, month) tuples. `context` is the full role block
	(heading + date line + duty lines) — relevance is judged on all of it, since
	the accounting terms often live in the duties and not in the title. `label`
	is the heading + date line only, for display in the HR breakdown.
	"""
	if not resume_text:
		return []

	lines = _clean_lines(resume_text)
	if not lines:
		return []
	meta = _scan_lines(lines)

	dated = []
	for i, m in enumerate(meta):
		ranges = _date_ranges_in(m["text"])
		if ranges:
			dated.append((i, ranges))
	if not dated:
		return []

	idxs = [i for i, _ in dated]
	date_lines = set(idxs)

	# Headings first: a block's duties stop where the next block's heading starts,
	# so a title paragraph is never also counted as the previous role's duty.
	heads = {}
	for pos, i in enumerate(idxs):
		heads[i] = _heading_start(meta, i, idxs[pos - 1] if pos else -1, date_lines)

	entries = []
	for pos, (i, ranges) in enumerate(dated):
		next_i = idxs[pos + 1] if pos + 1 < len(idxs) else len(meta)
		body_end = min(heads.get(next_i, next_i), i + 1 + _MAX_BODY_LINES)
		body = []
		for j in range(i + 1, body_end):
			if meta[j]["is_header"]:
				break
			body.append(meta[j]["text"])

		head = [meta[j]["text"] for j in range(heads[i], i)]
		label = " ".join([*head, meta[i]["text"]]).strip()
		context = " ".join([*head, meta[i]["text"], *body]).strip()
		is_education = _is_education_block(meta[i]["section"], context)

		for start, end, open_ended in ranges:
			months = max(0, _months_between(start, end))
			entries.append(
				{
					"context": context,
					"label": label,
					"start": start,
					"end": end,
					"months": months,
					"open_ended": open_ended,
					"is_education": is_education,
				}
			)

	return entries


def _drop_negated_clauses(text: str) -> str:
	"""Remove sentences that assert an absence ("no floriculture exposure ...")."""
	kept = [s for s in re.split(r"(?<=[.;!?])\s+|\n", text) if not _NEGATED_CLAUSE_RE.match(s.strip())]
	return " ".join(kept)


def _entry_is_relevant(context: str, keywords: list, synonym_map: dict) -> bool:
	"""True if the entry's context mentions any opening keyword (or its synonyms)."""
	if not context:
		return False
	context = _drop_negated_clauses(context)
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
		is_education = bool(e.get("is_education"))
		# A qualification row never counts as work, however many role keywords it
		# carries ("... university 2015-2019 cpa part ii" is not four years of
		# accounting experience).
		relevant = (not is_education) and _entry_is_relevant(ctx, keywords, synonym_map)
		if relevant and ctx:
			has_relevant_with_context = True

		s_idx, e_idx = _ym_index(e["start"]), _ym_index(e["end"])
		all_intervals.append((s_idx, e_idx))
		if relevant:
			relevant_intervals.append((s_idx, e_idx))

		row = {
			"role_context": _short(e.get("label") or ctx),
			"months": e["months"],
			"relevant": relevant,
		}
		if is_education:
			row["kind"] = "education"
		breakdown.append(row)

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
