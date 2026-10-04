import os
import re

import frappe


def normalize(text: str) -> str:
	text = text.lower()
	text = re.sub(r"[\t\r\f\v]+", " ", text)
	text = re.sub(r" {2,}", " ", text)
	return text.strip()


def _local_path(file_url: str) -> str:
	if not file_url:
		return ""
	site_path = frappe.get_site_path()
	if file_url.startswith("/private/files/"):
		return os.path.join(site_path, "private", "files", os.path.basename(file_url))
	if file_url.startswith("/files/"):
		return os.path.join(site_path, "public", "files", os.path.basename(file_url))
	return os.path.join(site_path, "public", file_url.lstrip("/"))


def extract_text(file_url: str) -> str:
	"""Return lowercased, whitespace-normalized text content of file at file_url. Empty string on failure."""
	if not file_url:
		return ""

	path = _local_path(file_url)
	if not os.path.exists(path):
		return ""

	ext = os.path.splitext(path)[1].lower().lstrip(".")
	try:
		if ext == "pdf":
			return normalize(_extract_pdf(path))
		if ext in ("docx",):
			return normalize(_extract_docx(path))
		if ext in ("txt", "md"):
			with open(path, encoding="utf-8", errors="ignore") as f:
				return normalize(f.read())
		if ext == "doc":
			return ""
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ATS text extraction failed")
		return ""
	return ""


# Below this many non-whitespace characters a PDF is assumed to have no usable
# text layer (a scan / photo of a printed CV) and is worth OCR-ing.
_PDF_TEXT_LAYER_MIN_CHARS = 200
# Rasterising is slow; CVs that matter are short.
_OCR_MAX_PAGES = 5
_OCR_DPI = 300


def _extract_pdf(path: str) -> str:
	import pdfplumber

	parts = []
	with pdfplumber.open(path) as pdf:
		for page in pdf.pages:
			parts.append(page.extract_text() or "")
	text = "\n".join(parts)

	# Scanned/image-only CVs come back empty (or near-empty) here. Rather than
	# scoring them 0, OCR the pages and use that if it read more than the text
	# layer did. If OCR is unavailable or reads nothing, we fall through with
	# whatever we had, so the caller still flags the applicant for review.
	if len(re.sub(r"\s+", "", text)) < _PDF_TEXT_LAYER_MIN_CHARS:
		ocr_text = _ocr_pdf(path)
		if len(re.sub(r"\s+", "", ocr_text)) > len(re.sub(r"\s+", "", text)):
			return ocr_text

	# TODO(upande_ats): some PDFs have a text layer whose spaces are missing
	# ("certifiedpublicaccountant"). That defeats word-boundary keyword matching
	# and needs a different pdfplumber layout mode (or a word-level rebuild).
	# TODO(upande_ats): two-column CVs come back with the columns interleaved, so
	# an education line can end up glued to a job line and suppress a genuinely
	# relevant role. Needs column detection (page.extract_text(layout=True) or
	# clustering words by x0) before the lines reach experience.py.
	return text


def _ocr_pdf(path: str) -> str:
	"""OCR a PDF's pages. Returns "" when OCR isn't available — never raises.

	Needs the `tesseract-ocr` and `poppler-utils` system packages (declared in
	pyproject's [deploy.dependencies.apt]) alongside the pytesseract/pdf2image
	Python wrappers.
	"""
	try:
		import pytesseract
		from pdf2image import convert_from_path
	except ImportError:
		frappe.log_error(
			"pytesseract/pdf2image not installed; scanned PDF left for manual review",
			"ATS OCR unavailable",
		)
		return ""

	try:
		images = convert_from_path(path, dpi=_OCR_DPI, first_page=1, last_page=_OCR_MAX_PAGES)
		return "\n".join(pytesseract.image_to_string(image) or "" for image in images)
	except Exception:
		# Missing tesseract binary, missing poppler, corrupt scan — all the same
		# outcome: no text, applicant stays flagged for review.
		frappe.log_error(frappe.get_traceback(), "ATS OCR failed")
		return ""


def _extract_docx(path: str) -> str:
	import docx2txt

	return docx2txt.process(path) or ""
