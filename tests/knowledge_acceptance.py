"""CLI + real HTTP acceptance; never resets existing knowledge data."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, create_db_engine
from app.models import KnowledgeChunk
from app.services.search import build_search_index, tokenize
from tests.parse_helpers import ROOT, PreviewServer


URL = "https://www.court.gov.cn/zixun/xiangqing/233181.html"
DATASETS = [
    ("civil_code_liability.txt", "civil-code-liability", "民法典合同编：违约责任节选", 15),
    ("civil_code_termination.txt", "civil-code-termination", "民法典合同编：解除与争议条款节选", 8),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "work/knowledge-acceptance.db")
    args = parser.parse_args()
    database = args.database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine("sqlite:///" + database.as_posix())
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        with Session() as session:
            if session.scalar(select(func.count()).select_from(KnowledgeChunk)):
                raise RuntimeError("验收要求空知识库；不会重置已有记录，请指定新测试数据库")
        environment = os.environ.copy()
        environment["DATABASE_URL"] = "sqlite:///" + database.as_posix()
        environment["PYTHONIOENCODING"] = "utf-8"
        lines = ["# 知识库入库与 BM25 检索 · 验收记录", "", "环境：Windows + Python 3.9.6；未接模型，未设置相关性分数阈值。", "",
                 "数据库：`{}`。".format(database), "", "## 1. CLI 草稿导入与幂等", ""]

        def command(*arguments):
            result = subprocess.run([sys.executable, "-m", "app.cli"] + list(arguments), cwd=str(ROOT),
                                    env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr)
            payload = json.loads(result.stdout)
            lines.append("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n")
            return payload

        for filename, source_id, name, count in DATASETS:
            args_import = ("import-law", "--file", str(ROOT / "samples/laws" / filename), "--source-id", source_id,
                           "--name", name, "--source-url", URL)
            assert command(*args_import)["imported"] == count
            assert command(*args_import)["skipped"] == count
        with Session() as session:
            rows = session.execute(select(KnowledgeChunk)).scalars().all()
            assert len(rows) == 23 and all(r.enabled == 0 and r.verified == 0 for r in rows)
        lines += ["导入 15 + 8 = 23 个块；全部 verified=0、enabled=0；再次导入跳过，未产生重复记录。", "",
                  "## 2. 未启用与启动快照", ""]
        server = PreviewServer()
        server.start(database)
        try:
            status, result = server.post_json("/search", {"query": "违约金"})
            assert status == 200 and result["results"] == []
            lines += ["启用前：`POST /search {query: 违约金}` → HTTP 200，results=[]。", ""]
            for _, source_id, _, count in DATASETS:
                assert command("enable-source", "--source-id", source_id)["affected_chunks"] == count
            assert server.post_json("/search", {"query": "违约金"})[1]["results"] == []
            lines += ["启用后、重启前：仍为空，证明没有请求时自动增量构建；以下结果为重启后的全量索引。", ""]
        finally:
            server.stop()
        server.start(database)
        try:
            with Session() as session:
                index = build_search_index(session)
            comparisons = []
            snapshots = {}
            for query in ("违约金过高怎么办", "合同怎么解除", "今天天气怎么样"):
                status, result = server.post_json("/search", {"query": query})
                assert status == 200
                results = result["results"]
                if query.startswith("违约金"):
                    assert results[0]["article_no"] == "第五百八十五条"
                elif query.startswith("合同"):
                    assert any(r["article_no"] == "第五百六十三条" for r in results)
                else:
                    assert results == []
                raw_scores = index.bm25.get_scores(list(dict.fromkeys(tokenize(query))))
                raw_max = float(max(raw_scores))
                comparisons.append((query, raw_max, len(results)))
                snapshots[query] = result
                lines += ["## 查询：{}".format(query), "", "分词：`{}`".format(json.dumps(tokenize(query), ensure_ascii=False)), "",
                          "| 排名 | 条号 | 原始分数 | matched_terms |", "|---|---|---|---|"]
                for rank, item in enumerate(results, 1):
                    lines.append("| {} | {} | {:.9f} | {} |".format(rank, item["article_no"], item["score"], " / ".join(item["matched_terms"])))
                if not results:
                    lines.append("| — | 无词项交集，results=[] | 原始分数向量全部为 0 | [] |")
                lines.append("")
            assert comparisons[2][1] == 0 and comparisons[0][1] > 0 and comparisons[1][1] > 0
            lines += ["## 原始分数对比", "", "| 查询 | 全语料原始分数最大值 | API 返回数量 |", "|---|---|---|"]
            lines += ["| {} | {:.9f} | {} |".format(*row) for row in comparisons]
            lines += ["", "天气查询的 0 是实际 BM25 分数向量的最大值，并非虚构的返回条目。无命中词项的文档不返回；有命中但原始分数为 0 或负数的文档仍保留。", "",
                      "这些分数仅为当前固定样本的验收观察，不是概率、跨查询置信度或未来风险判定阈值。", "",
                      "条文来源：[最高人民法院公布的《中华人民共和国民法典》](" + URL + ")；两份文件为同一法典的不同节选，均注明测试用且需人工核对。", "",
                      "验收中的 enable-source 仅验证显式启用流程，不代表完成实际业务的人工法规核验。验收后知识库保留 23 个已启用测试块，服务已停止。", ""]
            output = ROOT / "work/knowledge-search-results.json"
            output.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            server.stop()
        report = "\n".join(lines)
        destination = ROOT / "work/knowledge-search-acceptance.md"
        destination.write_text(report, encoding="utf-8")
        print(report)
        print("验收记录：", destination)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
