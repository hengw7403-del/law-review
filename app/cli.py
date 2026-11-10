"""Local knowledge commands, bounded review tasks and the independent worker."""

import argparse
from getpass import getpass
import json
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.services.chunker import SourceInfo
from app.services.knowledge import KnowledgeError, enable_source, import_law
from app.config import load_environment
from app.services.review_tasks import ReviewError, create_review
from app.services.review_worker import ReviewWorker, WorkerAlreadyRunning
from app.services.contract_reparse import reparse_contract
from app.services.auth import create_user
from app.models import User
from sqlalchemy import select


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库本地管理；修改后重启服务以全量重建 BM25 索引")
    commands = parser.add_subparsers(dest="command", required=True)
    creator = commands.add_parser("create-user", help="交互输入密码创建账号，不接受命令行密码参数")
    creator.add_argument("--username", required=True)
    creator.add_argument("--role", required=True, choices=["admin", "reviewer", "requester"])
    commands.add_parser("seed-users", help="交互初始化admin、reviewer01、reviewer02；已有账号跳过，不重置密码")
    importer = commands.add_parser("import-law", help="导入 UTF-8 TXT/Markdown，默认为未核对且未启用的草稿")
    importer.add_argument("--file", required=True, type=Path)
    importer.add_argument("--source-id", required=True)
    importer.add_argument("--name", required=True)
    importer.add_argument("--source-version", type=int, default=1)
    importer.add_argument("--source-url")
    importer.add_argument("--publisher")
    importer.add_argument("--effective-date")
    enable = commands.add_parser("enable-source", help="显式声明该来源已核对并启用；随后重启服务")
    enable.add_argument("--source-id", required=True)
    enable.add_argument("--source-version", type=int)
    review = commands.add_parser("review", help="创建受限检查任务，不在 CLI 中直接调用模型")
    review.add_argument("--contract", required=True)
    review.add_argument("--max-clauses", type=int, default=None, help="默认全部父条款；传入正整数可限制条数")
    review.add_argument("--rules", default="liability,payment,dispute_resolution")
    review.add_argument("--missing-rules", default="")
    reparse = commands.add_parser("reparse-contract", help="从存储正文重新切分，创建新合同版本并保留历史")
    reparse.add_argument("--contract", required=True)
    worker = commands.add_parser("run-worker", help="启动单 worker，重启后从检查点恢复")
    worker.add_argument("--once", action="store_true", help="最多处理一个任务后退出")
    worker.add_argument("--poll-interval", type=float, default=1)
    worker.add_argument("--unit-delay", type=float, default=0, help="单元提交后的间隔秒数，可用于限速")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        load_environment()
        db.init_db()
        if args.command in {"create-user", "seed-users"}:
            accounts = [(args.username, args.role)] if args.command == "create-user" else [
                ("admin", "admin"), ("reviewer01", "reviewer"), ("reviewer02", "reviewer")]
            results = []
            for username, role in accounts:
                with db.SessionLocal() as session:
                    exists = session.scalar(select(User.id).where(User.username == username))
                if exists and args.command == "seed-users":
                    results.append({"username": username, "skipped": True})
                    continue
                password = getpass("{} 密码（10至72字节）：".format(username))
                if password != getpass("再次输入密码："):
                    raise ValueError("两次密码不一致")
                results.append(create_user(db.SessionLocal, username, role, password))
            print(json.dumps(results, ensure_ascii=False))
            return 0
        if args.command == "reparse-contract":
            print(json.dumps(reparse_contract(db.SessionLocal, args.contract), ensure_ascii=False))
            return 0
        if args.command == "review":
            result = create_review(db.SessionLocal, args.contract, args.max_clauses,
                                   args.rules.split(",") if args.rules else [],
                                   args.missing_rules.split(",") if args.missing_rules else [])
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "run-worker":
            if args.poll_interval <= 0 or args.unit_delay < 0:
                raise ValueError("轮询间隔必须大于零，单元间隔不能为负")
            database_path = Path(db.engine.url.database).resolve()
            ReviewWorker(db.SessionLocal, database_path.with_suffix(".worker.lock"),
                         poll_interval=args.poll_interval, unit_delay=args.unit_delay).run(once=args.once)
            return 0
        with db.SessionLocal() as session:
            if args.command == "import-law":
                if args.file.suffix.lower() not in {".txt", ".md"}:
                    raise KnowledgeError("UNSUPPORTED_FORMAT", "导入仅支持 UTF-8 TXT/Markdown 文件")
                source = SourceInfo(
                    source_id=args.source_id, name=args.name, source_version=args.source_version,
                    source_locator=args.source_url or args.file.resolve().as_uri(),
                    publisher=args.publisher, effective_date=args.effective_date,
                )
                result = import_law(session, args.file.read_text(encoding="utf-8-sig"), args.file.name, source)
                result["restart_required"] = True
            else:
                result = enable_source(session, args.source_id, args.source_version)
        result["message"] = "操作完成；重启服务以全量重建 BM25 索引。enable-source 是人工核对后的显式操作，不是自动核验。"
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (KnowledgeError, ReviewError) as exc:
        code, message = exc.code, exc.message
    except UnicodeError:
        code, message = "GARBLED", "文件不是有效的 UTF-8 文本"
    except (OSError, ValueError) as exc:
        code, message = "INVALID_INPUT", str(exc)
    except SQLAlchemyError:
        code, message = "DATABASE_ERROR", "数据库操作失败，请检查数据库状态和日志"
    except WorkerAlreadyRunning as exc:
        code, message = "WORKER_ALREADY_RUNNING", str(exc)
    except KeyboardInterrupt:
        return 0
    finally:
        db.engine.dispose()
    print(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
