"""Experience-gate parsing tests.

The regression these guard against: role_context used to be the physical line the
date sat on. In a PDF that line also holds the title and duties, so relevance
worked; in a .docx each paragraph is its own line, so the date sat alone and
every role came back `relevant: false` (Fridah Mukami, HR-OPN-2026-0003).

Inputs are lowercased the way engine.text_extract.normalize leaves them.
"""

import os
import shutil
import tempfile
import unittest
import zipfile

from upande_ats.engine.text_extract import _extract_docx, normalize
from upande_ats.experience import evaluate_experience, parse_work_history

# Shaped like an accounting opening's ATS Keyword rows.
ACCOUNTING_KEYWORDS = [
	{"keyword": "accountant"},
	{"keyword": "accounting"},
	{"keyword": "bank reconciliation"},
	{"keyword": "invoicing"},
	{"keyword": "payroll"},
	{"keyword": "audit"},
]

# Fridah's shape: title, date and duties are each their own paragraph.
DOCX_CV = """fridah mukami
curriculum vitae

work experience

farm accountant | kevian kenya ltd
apr 2021–present
manage financial records for the farm and prepare monthly management accounts.
handle bank reconciliation and supplier payments.

internal auditor | nila pharmaceuticals
may 2020–mar 2021
reviewed internal controls and carried out an audit of accounts payable.

assistant accountant | tropical heat ltd
may 2019–apr 2020
prepared invoicing schedules, ran payroll and maintained the general ledger.

education

kenyatta university
2011–2015
bachelor of commerce, accounting option. cpa part ii.
"""

# Ruth's shape: pdfplumber glues the whole role onto one line.
PDF_CV = """curriculum vitae ruth thige
working experience ➢ golden tulip farm limited (october 2020 to date) ❖ accountant
❖ prepared monthly accounts, bank reconciliation and payroll for 120 staff
➢ bloom valley ltd (jan 2016 - sep 2020) ❖ assistant accountant
❖ invoicing, debtor follow-up and stock reconciliations
"""


def _evaluate(text):
	return evaluate_experience(text, ACCOUNTING_KEYWORDS, {})


def _relevant_contexts(text):
	return [
		e["context"] for e in parse_work_history(text) if not e["is_education"]
	]


class TestDocxRoleBlocks(unittest.TestCase):
	"""The actual bug: paragraph-per-line CVs must still classify as relevant."""

	def test_role_context_includes_title_and_duties(self):
		entries = parse_work_history(DOCX_CV)
		first = entries[0]
		self.assertIn("farm accountant", first["context"])
		self.assertIn("kevian kenya ltd", first["context"])
		self.assertIn("bank reconciliation", first["context"])
		# The display label stays short: heading + date line, no duty text.
		self.assertIn("apr 2021–present", first["label"])
		self.assertNotIn("supplier payments", first["label"])

	def test_accounting_roles_are_relevant(self):
		ev = _evaluate(DOCX_CV)
		jobs = [r for r in ev["breakdown"] if r.get("kind") != "education"]
		self.assertTrue(jobs, "no work roles parsed")
		self.assertTrue(
			all(r["relevant"] for r in jobs),
			f"expected every accounting role to count: {ev['breakdown']}",
		)

	def test_relevant_years_reflect_real_tenure(self):
		ev = _evaluate(DOCX_CV)
		self.assertGreater(ev["relevant_years"], 3.0)
		self.assertTrue(ev["confident"])

	def test_each_role_keeps_its_own_months(self):
		months = {r["months"] for r in parse_work_history(DOCX_CV)}
		# may 2020–mar 2021 = 10, may 2019–apr 2020 = 11, 2011–2015 = 48.
		self.assertTrue({10, 11, 48}.issubset(months))


class TestPdfNoRegression(unittest.TestCase):
	"""Single-glued-line roles must keep working exactly as before."""

	def test_glued_line_role_is_relevant(self):
		ev = _evaluate(PDF_CV)
		jobs = [r for r in ev["breakdown"] if r.get("kind") != "education"]
		self.assertEqual(len(jobs), 2)
		self.assertTrue(all(r["relevant"] for r in jobs), ev["breakdown"])

	def test_open_ended_and_closed_ranges_both_count(self):
		ev = _evaluate(PDF_CV)
		# jan 2016 - sep 2020 is 56 months on its own; the open-ended role adds more.
		self.assertGreater(ev["relevant_years"], 4.6)
		self.assertEqual(ev["relevant_years"], ev["total_years"])


class TestDateEdgeCases(unittest.TestCase):
	def test_year_only_range(self):
		text = "work experience\nfarm accountant, kevian ltd\n2011–2015\nran the ledger.\n"
		ev = _evaluate(text)
		self.assertEqual(ev["breakdown"][0]["months"], 48)
		self.assertTrue(ev["breakdown"][0]["relevant"])

	def test_hyphen_en_dash_present_and_to_date(self):
		variants = [
			"jan 2020 - present",
			"jan 2020 – present",
			"jan 2020–present",
			"jan 2020 to date",
			"since jan 2020",
		]
		for date_line in variants:
			with self.subTest(date_line=date_line):
				text = f"work experience\naccountant, kevian ltd\n{date_line}\nran the ledger.\n"
				ev = _evaluate(text)
				self.assertEqual(len(ev["breakdown"]), 1, date_line)
				self.assertTrue(ev["breakdown"][0]["relevant"], date_line)
				self.assertGreater(ev["breakdown"][0]["months"], 60, date_line)

	def test_duties_carry_the_keywords_not_the_title(self):
		# Elisha's shape — a non-accounting title over accounting duties.
		text = (
			"work experience\n"
			"sales & marketing officer\n"
			"heritage flowers ltd\n"
			"jan 2018 - dec 2020\n"
			"responsible for bank reconciliation, invoicing and customer statements.\n"
			"followed up on debtor balances and issued receipts.\n"
		)
		ev = _evaluate(text)
		self.assertEqual(len(ev["breakdown"]), 1)
		self.assertTrue(ev["breakdown"][0]["relevant"], ev["breakdown"])
		# jan 2018 -> dec 2020 is 35 months; month arithmetic is unchanged here.
		self.assertEqual(ev["relevant_years"], 2.9)

	def test_a_denied_mention_does_not_make_a_role_relevant(self):
		text = (
			"work experience\n"
			"retail sales representative - citymart kenya (january 2019 - december 2023)\n"
			"managed retail sales and customer accounts for a supermarket chain.\n"
			"no floriculture or agribusiness exposure in this role.\n"
		)
		ev = evaluate_experience(text, [{"keyword": "floriculture"}], {})
		self.assertFalse(ev["breakdown"][0]["relevant"], ev["breakdown"])
		self.assertEqual(ev["relevant_years"], 0.0)

	def test_previous_roles_duties_do_not_leak_into_the_next_heading(self):
		contexts = _relevant_contexts(DOCX_CV)
		self.assertNotIn("supplier payments", contexts[1])


class TestEducationIsNotWork(unittest.TestCase):
	def test_qualification_row_under_education_header_is_excluded(self):
		text = (
			"education\n"
			"kenyatta university\n"
			"2015–2019\n"
			"bachelor of commerce, accounting option. cpa part ii.\n"
		)
		ev = _evaluate(text)
		self.assertEqual(ev["relevant_years"], 0.0)
		self.assertEqual(ev["breakdown"][0].get("kind"), "education")
		self.assertFalse(ev["breakdown"][0]["relevant"])

	def test_qualification_row_without_a_header_is_excluded(self):
		# Elisha's 4.0 "relevant years" came entirely from this row.
		text = (
			"work experience\n"
			"sales & marketing officer\n"
			"heritage flowers ltd\n"
			"jan 2018 - dec 2020\n"
			"responsible for bank reconciliation, invoicing and customer statements.\n"
			"kenyatta university 2015–2019 cpa part ii\n"
		)
		ev = _evaluate(text)
		uni = [r for r in ev["breakdown"] if "kenyatta" in r["role_context"]]
		self.assertEqual(len(uni), 1)
		self.assertEqual(uni[0].get("kind"), "education")
		self.assertFalse(uni[0]["relevant"])
		# Only the real job counts (35 months), not the 4 university years.
		self.assertEqual(ev["relevant_years"], 2.9)

	def test_a_job_that_merely_mentions_a_degree_still_counts(self):
		text = (
			"work experience\n"
			"accountant, kevian kenya ltd\n"
			"jan 2018 - dec 2020\n"
			"used the bachelor of commerce training in daily bank reconciliation "
			"and monthly payroll runs.\n"
		)
		ev = _evaluate(text)
		self.assertTrue(ev["breakdown"][0]["relevant"], ev["breakdown"])
		self.assertIsNone(ev["breakdown"][0].get("kind"))

	def test_compound_section_header_is_recognised(self):
		# "CERTIFICATION & EDUCATION" is a header; "Training Officer" is a job title.
		from upande_ats.experience import _header_kind

		self.assertEqual(_header_kind("certification & education"), "education")
		self.assertEqual(_header_kind("academic and professional qualifications"), "education")
		self.assertEqual(_header_kind("work experience"), "experience")
		self.assertEqual(_header_kind("additional information"), "other")
		# A combined header must not bury the jobs listed under it.
		self.assertEqual(_header_kind("education and work experience"), "experience")
		self.assertEqual(_header_kind("training officer"), "")
		self.assertEqual(_header_kind("accountant, kevian kenya ltd"), "")

	def test_qualification_block_is_not_rescued_by_the_words_in_it(self):
		text = (
			"certification & education\n"
			"cpa 1 — certified public accountant, part 1 | kasneb, kenya\n"
			"bachelor of commerce (accounting option) | kenyatta university | 2015 – 2019\n"
		)
		ev = evaluate_experience(text, [{"keyword": "accountant"}], {})
		self.assertEqual(ev["relevant_years"], 0.0)
		self.assertEqual(ev["breakdown"][0].get("kind"), "education")

	def test_education_still_counts_towards_total_experience(self):
		# Total is a raw "dates found on the CV" figure and is left as-is; only
		# the gated relevant figure excludes schooling.
		ev = _evaluate(DOCX_CV)
		self.assertGreater(ev["total_years"], ev["relevant_years"])


def _write_docx(path, paragraphs):
	"""A minimal Word file — one <w:p> per paragraph, which is exactly the
	layout that broke the old parser. Hand-rolled so tests need no python-docx."""
	content_types = (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
		'<Default Extension="xml" ContentType="application/xml"/>'
		'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
		'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
		'officedocument.wordprocessingml.document.main+xml"/></Types>'
	)
	rels = (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
		'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
		'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
	)
	body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
	document = (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
		f"<w:body>{body}</w:body></w:document>"
	)
	with zipfile.ZipFile(path, "w") as z:
		z.writestr("[Content_Types].xml", content_types)
		z.writestr("_rels/.rels", rels)
		z.writestr("word/document.xml", document)


class TestDocxEndToEnd(unittest.TestCase):
	"""File in, verdict out — Fridah Mukami's case at the .docx level.

	Keywords and synonyms mirror HR-OPN-2026-0003 (required experience 3.0 yrs).
	"""

	KEYWORDS = [
		{"keyword": "Accounts Officer", "weight": 5},
		{"keyword": "Bank Reconciliation", "weight": 4},
		{"keyword": "CPA Part II", "weight": 5},
		{"keyword": "Floriculture", "weight": 4},
	]
	SYNONYMS = {
		"accounts officer": ["accountant", "assistant accountant", "accounts assistant"],
		"bank reconciliation": ["bank reconciliations", "bank rec"],
		"cpa part ii": ["cpa part 2", "cpa (k)", "cpa-k"],
		"floriculture": ["flower farm", "cut flowers", "roses", "farm"],
	}

	def setUp(self):
		self.tmp = tempfile.mkdtemp()
		self.path = os.path.join(self.tmp, "cv.docx")
		_write_docx(
			self.path,
			[
				"Fridah Mukami",
				"WORK EXPERIENCE",
				"Farm Accountant | Kevian Kenya Ltd",
				"Apr 2021–Present",
				"Manage financial records for the farm and prepare management accounts.",
				"Handle bank reconciliations and supplier payments.",
				"Internal Auditor | Nila Pharmaceuticals",
				"May 2020–Mar 2021",
				"Reviewed internal controls for the accounts payable function.",
				"EDUCATION",
				"Kenyatta University",
				"2011–2015",
				"Bachelor of Commerce. CPA Part II.",
			],
		)

	def tearDown(self):
		shutil.rmtree(self.tmp, ignore_errors=True)

	def test_docx_cv_clears_the_three_year_gate(self):
		text = normalize(_extract_docx(self.path))
		ev = evaluate_experience(text, self.KEYWORDS, self.SYNONYMS)
		self.assertTrue(ev["confident"])
		self.assertGreaterEqual(ev["relevant_years"], 3.0, ev["breakdown"])
		# The degree row carries "cpa part ii" but is not four years of work.
		education = [r for r in ev["breakdown"] if r.get("kind") == "education"]
		self.assertEqual(len(education), 1)
		self.assertFalse(education[0]["relevant"])


class TestUnparseable(unittest.TestCase):
	def test_no_dates_is_not_confident(self):
		ev = _evaluate("a cv with no dates at all, just prose about accounting.")
		self.assertFalse(ev["confident"])
		self.assertEqual(ev["relevant_years"], 0.0)

	def test_empty_text(self):
		ev = _evaluate("")
		self.assertFalse(ev["confident"])
		self.assertEqual(ev["breakdown"], [])
