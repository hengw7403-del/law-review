"""Run the requested uploads over HTTP and print the first three full clauses."""

import argparse
import json
from pathlib import Path
import sys

from app.services.parser import MAX_FILE_SIZE
from tests.parse_helpers import ROOT, PreviewServer, image_pdf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(ROOT / "work/parse-preview-acceptance.md"))
    args = parser.parse_args()
    server = PreviewServer()
    server.start()
    lines = ["# 文档解析与条款切分 · HTTP 验收记录", "", "环境：Windows + Python 3.9.6；无模型调用、无预览数据入库。", ""]
    snapshots = ROOT / "work/parse-preview"
    snapshots.mkdir(parents=True, exist_ok=True)
    try:
        for name, label, expected in (("contract_a.txt", "合同 A", 12), ("contract_b.txt", "合同 B", 10)):
            status, result = server.upload(name, (ROOT / "samples" / name).read_bytes())
            assert status == 200 and result["clause_count"] == expected
            (snapshots / name.replace(".txt", ".json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            lines += ["## {}：{} 条，HTTP {}".format(label, result["clause_count"], status), "",
                      "原编号：" + " / ".join(c["original_no"] or "无编号" for c in result["clauses"]), "",
                      "warnings：`{}`。以下是前 3 条完整内容：".format(result["warnings"]), ""]
            for clause in result["clauses"][:3]:
                lines += ["### 第 {} 项｜{}｜{}｜字符 [{}:{})".format(
                    clause["seq"], clause["original_no"], clause["location"], clause["char_start"], clause["char_end"]),
                    "", clause["content"], ""]
        lines += ["## 异常与边界上传", "", "| 场景 | HTTP | 错误码 |", "|---|---|---|"]
        for filename, data, expected_status, expected_code in [
            ("empty.txt", b"", 422, "EMPTY_TEXT"),
            ("image-only.pdf", image_pdf(), 422, "EMPTY_TEXT"),
            ("exe-renamed.pdf", b"MZ" + b"\x00" * 200, 415, "UNSUPPORTED_FORMAT"),
            ("invalid-utf8.txt", b"\xff\xfe", 422, "GARBLED"),
            ("broken.pdf", b"%PDF-1.4\nbroken", 415, "UNSUPPORTED_FORMAT"),
            ("over-20mb.txt", b"x" * (MAX_FILE_SIZE + 1), 413, "FILE_TOO_LARGE"),
        ]:
            status, result = server.upload(filename, data)
            assert (status, result["error"]["code"]) == (expected_status, expected_code)
            lines.append("| {} | {} | {} |".format(filename, status, result["error"]["code"]))
        lines += ["", "合同 B 命中次级混合编号规则，内部 1.1 / （一）保留在上级条款；无编号文本的空行回退另有自动化测试。", "",
                  "位置采用 p1/p1-p2 或 para1/para1-para3；偏移为解析后文本的 Unicode 字符索引，右端不包含。", "",
                  "本记录由真实 Uvicorn HTTP 上传生成。使用隔离的临时数据库，服务验收后停止；完整 JSON 位于项目 work/parse-preview/。", ""]
    finally:
        server.stop()
    report = "\n".join(lines)
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(report)
    print("验收记录：", path)


if __name__ == "__main__":
    main()
