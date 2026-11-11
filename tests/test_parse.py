from io import BytesIO
import sqlite3
import unittest
import zipfile

from docx import Document

from app.services.clause_splitter import split_clauses
from app.services.parse_quality import assess_quality
from app.services.parser import MAX_FILE_SIZE, ParseError, parse_document
from tests.parse_helpers import ROOT, PreviewServer, image_pdf, text_pdf


class ParserTests(unittest.TestCase):
    def test_txt_bom_newlines_unicode_and_offsets(self) -> None:
        text, mapping = parse_document("合同.TXT", b"\xef\xbb\xbf" + "第一条 范围\r\n正文\r\n\r\n第二条 价款\r\n付款".encode("utf-8"))
        self.assertNotIn("\r", text)
        self.assertFalse(text.startswith("\ufeff"))
        clauses = split_clauses(text, mapping)
        self.assertEqual([c["original_no"] for c in clauses], ["第一条", "第二条"])
        self.assertEqual(clauses[1]["location"], "para2")
        for clause in clauses:
            self.assertEqual(clause["content"], text[clause["char_start"]:clause["char_end"]])

    def test_invalid_utf8_is_caught(self) -> None:
        with self.assertRaises(ParseError) as error:
            parse_document("bad.txt", b"\xff\xfe\x80")
        self.assertEqual(error.exception.code, "GARBLED")

    def test_pdf_page_map_and_cross_page_clause(self) -> None:
        text, mapping = parse_document("two.pdf", text_pdf([
            "1. Scope\nProvide software development and testing services.",
            "Continued obligations across the page boundary.\n2. Payment\nPay after delivery.",
        ]))
        clauses = split_clauses(text, mapping)
        self.assertEqual(len(clauses), 2)
        self.assertEqual(clauses[0]["location"], "p1-p2")
        self.assertEqual(clauses[1]["location"], "p2")
        self.assertEqual(clauses[0]["content"], text[clauses[0]["char_start"]:clauses[0]["char_end"]])

    def test_docx_paragraph_map(self) -> None:
        document = Document()
        document.add_paragraph("第一条 服务范围")
        document.add_paragraph("甲方委托乙方提供软件开发服务。" * 5)
        document.add_paragraph("")
        document.add_paragraph("第二条 项目价款")
        document.add_paragraph("价款和付款阶段以附件为准。")
        output = BytesIO()
        document.save(output)
        text, mapping = parse_document("contract.docx", output.getvalue())
        clauses = split_clauses(text, mapping)
        self.assertEqual(len(clauses), 2)
        self.assertEqual(clauses[0]["location"], "para1-para2")
        self.assertEqual(clauses[1]["location"], "para4-para5")

    def test_generic_zip_is_not_docx(self) -> None:
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("readme.txt", "not a Word document")
        with self.assertRaises(ParseError) as error:
            parse_document("fake.docx", output.getvalue())
        self.assertEqual(error.exception.code, "UNSUPPORTED_FORMAT")


class SplitterTests(unittest.TestCase):
    def test_primary_supplementary_articles_and_nested_markers(self) -> None:
        text = "前言\n第一条 范围\n（一）内部事项\n1. 内部列表\n第七条之一 新增约定\n正文\n第12条 其他\n正文"
        clauses = split_clauses(text)
        self.assertEqual([c["original_no"] for c in clauses], ["第一条", "第七条之一", "第12条"])
        self.assertNotIn("前言", clauses[0]["content"])
        self.assertIn("（一）", clauses[0]["content"])

    def test_mixed_outer_numbers_preserve_child_lists_and_long_paragraphs(self) -> None:
        text = "一、范围\n正文\n\n（一）嵌套\n\n1.1 子项\n\n后续长段\n\n1.价款\n付款\n\n三、交付\n交付"
        clauses = split_clauses(text)
        self.assertEqual([c["original_no"] for c in clauses], ["一、", "1.", "三、"])
        self.assertIn("后续长段", clauses[0]["content"])

    def test_hierarchical_numbers_when_no_outer_markers(self) -> None:
        clauses = split_clauses("1.1 范围\n正文\n1.1.1 子项\n正文\n1.2 费用\n正文")
        self.assertEqual([c["original_no"] for c in clauses], ["1.1", "1.2"])
        self.assertIn("1.1.1", clauses[0]["content"])

    def test_parenthesized_numbers(self) -> None:
        clauses = split_clauses("（一）范围\n正文\n（二）费用\n正文\n（三）交付\n正文")
        self.assertEqual([c["original_no"] for c in clauses], ["（一）", "（二）", "（三）"])

    def test_blank_line_fallback_and_exact_spans(self) -> None:
        text = "  服务范围及正文\n连续行\n \n  项目价款及付款  \n\n交付和验收\n"
        clauses = split_clauses(text)
        self.assertEqual(len(clauses), 3)
        self.assertTrue(all(c["original_no"] is None for c in clauses))
        for clause in clauses:
            self.assertEqual(clause["content"], text[clause["char_start"]:clause["char_end"]])

    def test_quality_thresholds(self) -> None:
        self.assertEqual(assess_quality("", 0), (False, "EMPTY_TEXT", []))
        self.assertEqual(assess_quality(" " * 100, 0), (False, "EMPTY_TEXT", []))
        self.assertEqual(assess_quality("字" * 49, 3), (False, "EMPTY_TEXT", []))
        self.assertEqual(assess_quality("字" * 50, 2), (True, None, ["TOO_FEW_CLAUSES"]))
        self.assertEqual(assess_quality("字" * 95 + "\ufffd" * 5, 3), (True, None, []))
        self.assertEqual(assess_quality("字" * 94 + "\ufffd" * 6, 3), (False, "GARBLED", []))


class PreviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = PreviewServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def test_sample_a_has_twelve_correct_numbers(self) -> None:
        status, result = self.server.upload("contract_a.txt", (ROOT / "samples/contract_a.txt").read_bytes())
        self.assertEqual(status, 200)
        self.assertEqual(result["clause_count"], 12)
        self.assertEqual(result["warnings"], [])
        numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]
        self.assertEqual([c["original_no"] for c in result["clauses"]], ["第" + n + "条" for n in numerals])

    def test_sample_b_has_ten_outer_clauses(self) -> None:
        status, result = self.server.upload("contract_b.txt", (ROOT / "samples/contract_b.txt").read_bytes())
        self.assertEqual(status, 200)
        self.assertEqual(result["clause_count"], 10)
        self.assertEqual([c["original_no"] for c in result["clauses"]],
                         ["一、", "1.", "三、", "2.", "五、", "6.", "七、", "4.", "九、", "10."])
        self.assertIn("1.1 对接", result["clauses"][0]["content"])
        self.assertIn("跨部门流程", result["clauses"][0]["content"])

    def test_clear_error_codes_for_empty_image_fake_and_encoding(self) -> None:
        cases = [
            ("empty.txt", b"", 422, "EMPTY_TEXT"),
            ("scan.pdf", image_pdf(), 422, "EMPTY_TEXT"),
            ("fake.pdf", b"MZ" + b"\x00" * 100, 415, "UNSUPPORTED_FORMAT"),
            ("broken.pdf", b"%PDF-1.4\nbroken", 415, "UNSUPPORTED_FORMAT"),
            ("bad.txt", b"\xff\xfe", 422, "GARBLED"),
            ("bad.docx", b"not ZIP", 415, "UNSUPPORTED_FORMAT"),
            ("program.exe", b"MZ", 415, "UNSUPPORTED_FORMAT"),
            ("garbled.txt", ("正文" * 25 + "\ufffd" * 10).encode("utf-8"), 422, "GARBLED"),
        ]
        for filename, data, expected_status, code in cases:
            with self.subTest(filename=filename):
                status, result = self.server.upload(filename, data)
                self.assertEqual(status, expected_status)
                self.assertEqual(result["error"]["code"], code)

    def test_over_twenty_mb_returns_413(self) -> None:
        status, result = self.server.upload("huge.txt", b"a" * (MAX_FILE_SIZE + 1))
        self.assertEqual(status, 413)
        self.assertEqual(result["error"]["code"], "FILE_TOO_LARGE")

    def test_warning_for_successful_single_clause(self) -> None:
        status, result = self.server.upload("one.txt", ("第一条 范围\n" + "技术开发交付事项。" * 10).encode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(result["warnings"], ["TOO_FEW_CLAUSES"])

    def test_docx_and_text_pdf_preview(self) -> None:
        document = Document()
        for paragraph in (ROOT / "samples/contract_a.txt").read_text(encoding="utf-8").split("\n\n"):
            document.add_paragraph(paragraph)
        output = BytesIO()
        document.save(output)
        status, result = self.server.upload("合同.docx", output.getvalue())
        self.assertEqual(status, 200)
        self.assertEqual(result["filename"], "合同.docx")
        self.assertEqual(result["clause_count"], 12)
        status, result = self.server.upload("text.pdf", text_pdf([
            "1. Scope\nDevelop and deliver a working internal service platform.",
            "2. Payment\nPayment follows acceptance.\n3. Delivery\nDeliver the source code and user guide.",
        ]))
        self.assertEqual(status, 200)
        self.assertEqual(result["clause_count"], 3)
        self.assertEqual(result["clauses"][0]["location"], "p1")

    def test_no_database_rows_written_by_previews(self) -> None:
        for _ in range(2):
            status, _ = self.server.upload("a.txt", (ROOT / "samples/contract_a.txt").read_bytes())
            self.assertEqual(status, 200)
        with sqlite3.connect(str(self.server.database)) as connection:
            tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            self.assertEqual(len(tables), 12)
            for table in tables:
                self.assertEqual(connection.execute("SELECT count(*) FROM " + table).fetchone()[0], 1 if table == "users" else 0)


if __name__ == "__main__":
    unittest.main()
