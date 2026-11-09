import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from app.db import Base, create_db_engine
from app.models import KnowledgeChunk
from app.services.chunker import SourceInfo, chunk_law
from app.services.knowledge import KnowledgeError, enable_source, import_law
from app.services.search import BM25Index, build_search_index, tokenize
from tests.parse_helpers import ROOT, PreviewServer


DATASETS = [
    ("civil_code_liability.txt", "civil-code-liability", "民法典合同编：违约责任节选", 15),
    ("civil_code_termination.txt", "civil-code-termination", "民法典合同编：解除与争议条款节选", 8),
]
SOURCE_URL = "https://www.court.gov.cn/zixun/xiangqing/233181.html"


class ChunkerTests(unittest.TestCase):
    def test_numbered_articles_preserve_subclauses_and_exclude_header(self) -> None:
        text = "测试用，条文需人工核对\n来源：example\n\n第577条 正文\n（一）第一款\n\n第二款\n第577条之一 补充\n正文"
        chunks = chunk_law(text, "test.txt", SourceInfo("s", "test"))
        self.assertEqual([c.article_no for c in chunks], ["第577条", "第577条之一"])
        self.assertEqual([c.chunk_index for c in chunks], [0, 1])
        self.assertNotIn("测试用", chunks[0].content)
        self.assertIn("第二款", chunks[0].content)
        for chunk in chunks:
            self.assertEqual(chunk.content_hash, hashlib.sha256(chunk.content.encode("utf-8")).hexdigest())

    def test_fallback_cap_is_unicode_characters_not_bytes(self) -> None:
        chunks = chunk_law("字" * 1201 + "\n\n" + "段落二", "a.md", SourceInfo("s", "test"))
        self.assertEqual([len(c.content) for c in chunks], [500, 500, 201, 3])
        self.assertTrue(all(c.article_no is None for c in chunks))
        numbered = chunk_law("第一条 " + "字" * 600, "a.txt", SourceInfo("s", "test"))
        self.assertEqual(len(numbered), 1)
        self.assertGreater(len(numbered[0].content), 500)

    def test_samples_have_expected_articles_and_warning_headers(self) -> None:
        for filename, source_id, name, count in DATASETS:
            text = (ROOT / "samples/laws" / filename).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("测试用，条文需人工核对"))
            chunks = chunk_law(text, filename, SourceInfo(source_id, name))
            self.assertEqual(len(chunks), count)
            self.assertTrue(all(c.article_no is not None for c in chunks))
            self.assertTrue(all("原文链接" not in c.content for c in chunks))


class KnowledgeDatabaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name).resolve() / "knowledge.db"
        self.engine = create_db_engine("sqlite:///" + self.path.as_posix())
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def import_datasets(self) -> None:
        for filename, source_id, name, count in DATASETS:
            source = SourceInfo(source_id, name, source_locator=SOURCE_URL)
            with self.Session() as session:
                result = import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, source)
                self.assertEqual(result["imported"], count)

    def enable_datasets(self) -> None:
        for _, source_id, _, _ in DATASETS:
            with self.Session() as session:
                enable_source(session, source_id)


class KnowledgeTests(KnowledgeDatabaseCase):
    def test_import_is_draft_and_exact_retry_preserves_ids_and_enabled_state(self) -> None:
        self.import_datasets()
        with self.Session() as session:
            rows = session.execute(select(KnowledgeChunk)).scalars().all()
            self.assertEqual(len(rows), 23)
            self.assertTrue(all(r.enabled == 0 and r.verified == 0 for r in rows))
            before = {r.id for r in rows}
            self.assertEqual(build_search_index(session).search("违约金"), [])
        self.enable_datasets()
        filename, source_id, name, count = DATASETS[0]
        with self.Session() as session:
            result = import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"),
                                filename, SourceInfo(source_id, name, source_locator=SOURCE_URL))
            self.assertEqual(result["skipped"], count)
            self.assertEqual(result["imported"], 0)
        with self.Session() as session:
            rows = session.execute(select(KnowledgeChunk)).scalars().all()
            self.assertEqual({r.id for r in rows}, before)
            self.assertTrue(all(r.enabled == 1 and r.verified == 1 for r in rows))

    def test_same_version_different_content_is_rejected_without_partial_writes(self) -> None:
        source = SourceInfo("s", "test")
        with self.Session() as session:
            import_law(session, "第一条 原文\n第二条 原文", "test.txt", source)
        with self.Session() as session:
            with self.assertRaises(KnowledgeError) as error:
                import_law(session, "第一条 改文\n第二条 原文\n第三条 新增", "test.txt", source)
            self.assertEqual(error.exception.code, "SOURCE_VERSION_CONFLICT")
        with self.Session() as session:
            rows = session.execute(select(KnowledgeChunk).order_by(KnowledgeChunk.chunk_index)).scalars().all()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].content, "第一条 原文")

    def test_unknown_or_empty_sources_do_not_write(self) -> None:
        with self.Session() as session:
            with self.assertRaises(KnowledgeError):
                import_law(session, " \n", "empty.txt", SourceInfo("empty", "empty"))
        with self.Session() as session:
            with self.assertRaises(KnowledgeError) as error:
                enable_source(session, "missing")
            self.assertEqual(error.exception.code, "SOURCE_NOT_FOUND")
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(KnowledgeChunk)), 0)

    def test_only_three_flags_together_allow_search(self) -> None:
        for i in range(4):
            with self.Session() as session:
                import_law(session, "第一条 违约金调整", "a.txt", SourceInfo(str(i), str(i)))
        flags = [(1, 1, 0), (1, 0, 0), (0, 1, 0), (1, 1, 1)]
        with self.Session.begin() as session:
            for i, (enabled, verified, superseded) in enumerate(flags):
                session.execute(update(KnowledgeChunk).where(KnowledgeChunk.source_id == str(i)).values(
                    enabled=enabled, verified=verified, superseded=superseded))
        with self.Session() as session:
            index = build_search_index(session)
            self.assertEqual(len(index.documents), 1)
            self.assertEqual(len(index.search("违约金")), 1)

    def test_rankings_and_no_overlap_query(self) -> None:
        self.import_datasets()
        self.enable_datasets()
        with self.Session() as session:
            index = build_search_index(session)
        penalties = index.search("违约金过高怎么办")
        termination = index.search("合同怎么解除")
        self.assertEqual(penalties[0]["article_no"], "第五百八十五条")
        self.assertTrue(any(r["article_no"] == "第五百六十三条" for r in termination))
        self.assertIn("违约金", penalties[0]["matched_terms"])
        self.assertEqual(index.search("今天天气怎么样"), [])

    def test_cli_import_enable_and_idempotency(self) -> None:
        environment = os.environ.copy()
        environment["DATABASE_URL"] = "sqlite:///" + self.path.as_posix()
        environment["PYTHONIOENCODING"] = "utf-8"

        def command(*args):
            completed = subprocess.run([sys.executable, "-m", "app.cli"] + list(args), cwd=str(ROOT),
                                       env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

        arguments = ("import-law", "--file", "samples/laws/civil_code_liability.txt", "--source-id", "cli", "--name", "test")
        self.assertEqual(command(*arguments)["imported"], 15)
        self.assertEqual(command(*arguments)["skipped"], 15)
        self.assertEqual(command("enable-source", "--source-id", "cli")["affected_chunks"], 15)


class TokenizerAndScoreTests(unittest.TestCase):
    def test_article_numbers_are_atomic_and_negations_survive(self) -> None:
        self.assertEqual(tokenize("第五百八十五条"), ["第五百八十五条"])
        self.assertEqual(tokenize("第585条之一"), ["第585条之一"])
        terms = tokenize("不得 不 违约金过高")
        self.assertIn("不得", terms)
        self.assertIn("不", terms)
        self.assertIn("过高", terms)

    def test_raw_zero_negative_and_empty_indexes(self) -> None:
        self.assertEqual(BM25Index([]).search("违约金"), [])
        empty = KnowledgeChunk(id="empty", source_file="e.txt", content="。！？", article_no=None)
        self.assertEqual(BM25Index([empty]).search("违约金"), [])
        one = KnowledgeChunk(id="one", source_file="a.txt", content="违约金", article_no=None)
        negative = BM25Index([one]).search("违约金")
        self.assertEqual(len(negative), 1)
        self.assertLess(negative[0]["score"], 0)
        two = KnowledgeChunk(id="two", source_file="b.txt", content="合同 解除", article_no=None)
        zero = BM25Index([one, two]).search("违约金")
        self.assertEqual(len(zero), 1)
        self.assertEqual(zero[0]["score"], 0)


class SearchApiTests(KnowledgeDatabaseCase):
    def test_http_startup_snapshot_activation_restart_and_revocation(self) -> None:
        self.import_datasets()
        server = PreviewServer()
        server.start(self.path)
        try:
            self.assertEqual(server.post_json("/search", {"query": "违约金"}), (200, {"query": "违约金", "results": []}))
            self.enable_datasets()
            self.assertEqual(server.post_json("/search", {"query": "违约金"})[1]["results"], [])
        finally:
            server.stop()
        server.start(self.path)
        try:
            status, result = server.post_json("/search", {"query": "违约金过高怎么办"})
            self.assertEqual(status, 200)
            self.assertEqual(result["results"][0]["article_no"], "第五百八十五条")
            self.assertIn("违约金", result["results"][0]["matched_terms"])
            status, result = server.post_json("/search", {"query": "合同怎么解除", "top_k": 5})
            self.assertEqual(status, 200)
            self.assertTrue(any(r["article_no"] == "第五百六十三条" for r in result["results"]))
            self.assertEqual(server.post_json("/search", {"query": "今天天气怎么样"})[1]["results"], [])
            with self.Session.begin() as session:
                session.execute(update(KnowledgeChunk).where(KnowledgeChunk.article_no == "第五百八十五条").values(enabled=0))
            result = server.post_json("/search", {"query": "违约金过高怎么办"})[1]
            self.assertFalse(any(r["article_no"] == "第五百八十五条" for r in result["results"]))
            for payload in ({"query": " "}, {"query": "合同", "top_k": 0}, {"query": "合同", "top_k": 51}, {"query": "合同", "top_k": True}):
                self.assertEqual(server.post_json("/search", payload)[0], 422)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
