"""PDF extraction tests, including the OCR fallback for scanned CVs.

Scanned/image-only CVs (Protas Fwamba's `scan420 (2).pdf`) have no text layer, so
pdfplumber returns nothing and the applicant scored 0. The fallback OCRs the
pages instead; when OCR is unavailable it must degrade quietly to "no text" so
the caller still raises the "Needs Review" flag rather than silently rejecting.
"""

import os
import shutil
import tempfile
import unittest

from upande_ats.engine import text_extract

TEXT_LINE = "Golden Tulip Farm Limited (October 2020 to date) Accountant, bank reconciliation"


def _write_text_pdf(path, lines):
	"""A minimal one-page PDF with a real text layer (no reportlab needed)."""
	content = "BT /F1 12 Tf 40 750 Td 14 TL\n" + "\n".join(f"({l}) Tj T*" for l in lines) + "\nET"
	objs = [
		"<< /Type /Catalog /Pages 2 0 R >>",
		"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
		"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
		f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
		"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	]
	out, offsets = "%PDF-1.4\n", []
	for i, obj in enumerate(objs, 1):
		offsets.append(len(out))
		out += f"{i} 0 obj\n{obj}\nendobj\n"
	xref = len(out)
	out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
	out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
	out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
	with open(path, "wb") as f:
		f.write(out.encode("latin-1"))


def _write_image_pdf(path):
	"""A scanned CV: one page that is nothing but a bitmap — no text layer."""
	from PIL import Image, ImageDraw

	img = Image.new("1", (1240, 1754), 1)
	draw = ImageDraw.Draw(img)
	for i, line in enumerate(["PROTAS FWAMBA", "ACCOUNTS ASSISTANT", "KISIMA FARM 2019 - PRESENT"]):
		draw.text((80, 80 + i * 40), line, 0)
	img.save(path)


class TestPdfExtraction(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.mkdtemp()
		self.text_pdf = os.path.join(self.tmp, "text.pdf")
		self.scan_pdf = os.path.join(self.tmp, "scan.pdf")
		_write_text_pdf(self.text_pdf, [TEXT_LINE] * 6)
		_write_image_pdf(self.scan_pdf)
		self.ocr_calls = []

	def tearDown(self):
		shutil.rmtree(self.tmp, ignore_errors=True)
		text_extract._ocr_pdf = self._real_ocr

	_real_ocr = staticmethod(text_extract._ocr_pdf)

	def _stub_ocr(self, result):
		def fake(path):
			self.ocr_calls.append(path)
			return result

		text_extract._ocr_pdf = fake

	def test_text_layer_pdf_is_not_ocred(self):
		self._stub_ocr("SHOULD NOT BE USED")
		text = text_extract._extract_pdf(self.text_pdf)
		self.assertIn("Golden Tulip Farm Limited", text)
		self.assertEqual(self.ocr_calls, [], "OCR ran on a PDF that already had text")

	def test_image_only_pdf_falls_back_to_ocr(self):
		ocr_text = (
			"PROTAS FWAMBA\nACCOUNTS ASSISTANT\nKISIMA FARM 2019 - PRESENT\n"
			+ "bank reconciliation, VAT and KRA returns. " * 6
		)
		self._stub_ocr(ocr_text)
		text = text_extract._extract_pdf(self.scan_pdf)
		self.assertEqual(self.ocr_calls, [self.scan_pdf], "OCR was never attempted")
		self.assertIn("ACCOUNTS ASSISTANT", text)

	def test_image_only_pdf_without_ocr_is_left_for_review(self):
		# OCR unavailable (no tesseract) -> no text, so the caller keeps its
		# "Unparseable ... Needs Review" path. It must not raise.
		self._stub_ocr("")
		text = text_extract._extract_pdf(self.scan_pdf)
		self.assertEqual(self.ocr_calls, [self.scan_pdf])
		self.assertEqual(text.strip(), "")

	def test_ocr_never_raises_when_unavailable(self):
		# Real _ocr_pdf against the real environment: with or without the
		# tesseract binary installed, it returns a string and never blows up.
		self.assertIsInstance(self._real_ocr(self.scan_pdf), str)


class TestOcrIntegration(unittest.TestCase):
	"""Only meaningful where tesseract + poppler are actually installed."""

	def setUp(self):
		try:
			import pytesseract
			from pdf2image import convert_from_path  # noqa: F401

			pytesseract.get_tesseract_version()
		except Exception as e:
			raise unittest.SkipTest(f"tesseract/poppler unavailable: {e}")
		self.tmp = tempfile.mkdtemp()
		self.scan_pdf = os.path.join(self.tmp, "scan.pdf")
		_write_image_pdf(self.scan_pdf)

	def tearDown(self):
		shutil.rmtree(getattr(self, "tmp", ""), ignore_errors=True)

	def test_scanned_pdf_yields_text(self):
		text = text_extract._extract_pdf(self.scan_pdf)
		self.assertIn("protas", text.lower())
