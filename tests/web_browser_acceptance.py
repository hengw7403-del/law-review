"""Real Chrome UI acceptance on an isolated DB, using SDK mock worker only.

Testing tool is installed under work/browser-test-deps, not app dependencies.
Run with: python -m tests.web_browser_acceptance
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work/browser-test-deps"))
from playwright.sync_api import sync_playwright, expect
from jose import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import db
from app.models import Contract, LLMLog, ReviewTask
from app.services.auth import create_user, signing_key
from app.services.chunker import SourceInfo
from app.services.knowledge import enable_source, import_law
from tests.parse_helpers import PreviewServer
from tests.review_helpers import start_worker, stop_process_tree

PASSWORD = "browser-tests-only-password"


def main():
    # This machine denies the Node driver's default Temp location. Isolate only
    # this test process; do not change Windows environment/system settings.
    local_temp = ROOT / "work/browser-temp"
    local_temp.mkdir(exist_ok=True)
    os.environ["TEMP"] = str(local_temp)
    os.environ["TMP"] = str(local_temp)
    suffix = uuid.uuid4().hex[:10]
    database = ROOT / "work" / ("web-browser-" + suffix + ".db")
    engine = db.create_db_engine("sqlite:///" + database.as_posix())
    db.Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    ids = {}
    for name in ("reviewer01", "reviewer02"):
        ids[name] = create_user(sf, name, "reviewer", PASSWORD)["user_id"]
    for filename, source_id in (("civil_code_liability.txt", "law1"), ("civil_code_termination.txt", "law2")):
        with sf() as session:
            import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, SourceInfo(source_id, source_id))
        with sf() as session:
            enable_source(session, source_id)
    server, worker = PreviewServer(), None
    checks, errors, outside, task_requests, posts = [], [], [], [], []
    screenshots = ROOT / "work/web-screenshots"
    screenshots.mkdir(exist_ok=True)
    state = {"database": str(database), "real_model_calls": 0, "checks": checks}
    try:
        server.start(database, {"DEEPSEEK_API_KEY": "", "LLM_TEST_CALL_LIMIT": "0",
                                "CONTRACT_UPLOAD_DIR": str(ROOT / "work" / ("web-uploads-" + suffix))}, authenticated=False)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe", headless=True,
                                          args=["--disable-background-networking"])
            context = browser.new_context(viewport={"width": 1360, "height": 960})
            def traffic(route):
                address = route.request.url
                if address.startswith(("data:", "blob:")) or urlparse(address).hostname == "127.0.0.1":
                    route.continue_()
                else:
                    outside.append(address)
                    route.abort()
            context.route("**/*", traffic)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            def record(request):
                if "/tasks/" in request.url: task_requests.append((request.url, time.monotonic()))
                if request.method == "POST": posts.append(request.url)
            page.on("request", record)
            def login(name):
                page.get_by_label("用户名", exact=True).fill(name)
                page.get_by_label("密码", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="登录", exact=True).click()
                page.wait_for_url("**/contract-list.html")
            page.goto(server.url + "/")
            page.wait_for_url("**/login.html")
            page.screenshot(path=str(screenshots / "01-login.png"), full_page=True)
            checks.append("根路径自动跳到登录页")
            page.get_by_label("用户名", exact=True).fill("does-not-exist")
            page.get_by_label("密码", exact=True).fill("wrong-password")
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("alert")).to_have_text("账号或密码错误")
            login("reviewer01")
            expect(page.get_by_role("heading", name="还没有合同")).to_be_visible()
            checks.append("登录及空列表引导")
            page.get_by_role("button", name="上传合同", exact=True).click()
            before = len(posts)
            page.get_by_label("合同文件").set_input_files(str(ROOT / "samples/contract_a.txt"))
            expect(page.locator("#file-summary")).to_contain_text("contract_a.txt")
            expect(page.locator("#file-summary")).to_contain_text("字节")
            assert len(posts) == before, "选文件不得自动提交"
            page.get_by_label("合同名称（可选）").fill("合同 A · 浏览器验收")
            page.get_by_role("button", name="上传", exact=True).click()
            expect(page.locator("#list-message")).to_contain_text("上传成功", timeout=15000)
            row = page.locator("#contract-rows tr").first
            expect(row).to_contain_text("合同 A · 浏览器验收")
            expect(row).to_contain_text("解析成功")
            cid = row.get_by_role("link", name="查看", exact=True).get_attribute("href").split("id=")[1]
            state["contract_id"] = cid
            checks.append("选择文件展示名称大小，手动上传后显示新合同和解析状态")
            # A second upload proves server-side newest-first ordering.
            page.get_by_role("button", name="上传合同", exact=True).click()
            page.get_by_label("合同文件").set_input_files(str(ROOT / "samples/contract_b.txt"))
            page.get_by_label("合同名称（可选）").fill("新上传的合同 B")
            page.get_by_role("button", name="上传", exact=True).click()
            expect(page.locator("#contract-rows tr").first).to_contain_text("新上传的合同 B", timeout=15000)
            page.screenshot(path=str(screenshots / "02-contract-list.png"), full_page=True)
            checks.append("新上传合同位于列表首行")
            with page.expect_response(lambda response: response.url.endswith("/contracts") and response.request.method == "GET"):
                page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
            expect(page.locator("#contract-rows tr").first).to_contain_text("新上传的合同 B")
            checks.append("恢复缓存页面时重新校验并刷新列表")
            page.goto(server.url + "/contract-detail.html?id=" + cid)
            expect(page.locator("#info-count")).to_have_text("12 条")
            expect(page.locator("#info-parse")).to_have_text("解析成功")
            expect(page.locator("#clauses article")).to_have_count(10)
            page.get_by_role("button", name="下一页").click()
            expect(page.locator("#clauses article")).to_have_count(2)
            expect(page.locator("#page-label")).to_contain_text("第 2 / 2 页")
            checks.append("详情信息与条款分页")
            before = len([p for p in posts if p.endswith("/review")])
            page.get_by_role("button", name="发起审查", exact=True).click()
            expect(page.locator("#start-review")).to_be_disabled()
            expect(page.locator("#task-status")).to_have_text("排队中")
            assert len([p for p in posts if p.endswith("/review")]) == before + 1
            with sf() as session:
                task_id = session.scalar(select(ReviewTask.id).where(ReviewTask.contract_id == cid))
            state["task_id"] = task_id
            page.reload()
            expect(page.locator("#task-status")).to_have_text("排队中")
            checks.append("提交禁用防重复，刷新后恢复排队任务")
            worker = start_worker(database, ROOT / "work/web-browser-mock-worker.log", mock=True, delay=3)
            expect(page.locator("#task-status")).to_contain_text("正在审查", timeout=15000)
            page.screenshot(path=str(screenshots / "03-review-running.png"), full_page=True)
            expect(page.locator("#task-status")).to_have_text("审查完成", timeout=40000)
            assert worker.wait(timeout=30) == 0
            expect(page.get_by_role("button", name="查看报告", exact=True)).to_be_visible()
            page.screenshot(path=str(screenshots / "04-review-completed.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "05-mobile-detail.png"), full_page=True)
            page.set_viewport_size({"width": 1360, "height": 960})
            checks.append("窄屏详情无横向溢出")
            count = len(task_requests)
            page.wait_for_timeout(4500)
            assert len(task_requests) == count, "终态后仍在轮询"
            state["task_poll_requests"] = count
            checks.append("排队→正在审查→完成；终态停止2秒轮询")
            page.get_by_role("button", name="查看报告", exact=True).click()
            page.wait_for_url("**/report.html?id=*")
            expect(page.locator("#report")).to_contain_text("合同辅助初审报告", timeout=10000)
            page.go_back()
            expect(page.locator("#task-status")).to_have_text("审查完成")
            checks.append("查看报告跳转安全Markdown报告页，返回可恢复详情")
            # Test terminal error wording with local response fixtures, no worker.
            with sf() as session:
                from app.services.review_tasks import get_task
                completed = get_task(sf, task_id)
            for status, expected_text in (("partial_failed", "部分完成（2 项失败）"), ("failed", "审查失败")):
                fixture = {**completed, "status": status, "failed_items": 2, "error_message": "测试任务失败原因"}
                def terminal_response(route, request, fixture=fixture):
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(fixture))
                page.route("**/tasks/" + task_id, terminal_response)
                page.reload()
                expect(page.locator("#task-status")).to_have_text(expected_text)
                if status == "failed": expect(page.locator("#task-error")).to_contain_text("测试任务失败原因")
                count = len(task_requests)
                page.wait_for_timeout(2400)
                assert len(task_requests) == count
                page.unroute("**/tasks/" + task_id, terminal_response)
            checks.append("部分失败、失败原因及终态停止轮询（本地响应样本）")
            # Explicit parse-failed fixture verifies the re-upload path.
            failed_id = uuid.uuid4().hex
            with sf.begin() as session:
                session.add(Contract(id=failed_id, series_id=failed_id, contract_version=1, title="解析失败测试记录",
                                     filename="bad.pdf", stored_path="test-only", file_hash="test-only", party_stance="party_a",
                                     parse_status="parse_failed", parse_error="文件无法提取文本", owner_id=ids["reviewer01"]))
            page.goto(server.url + "/contract-detail.html?id=" + failed_id)
            expect(page.locator("#parse-error")).to_contain_text("文件无法提取文本")
            page.get_by_role("link", name="重新上传", exact=True).click()
            expect(page.locator("#upload-dialog")).to_be_visible()
            page.get_by_role("button", name="关闭上传窗口").click()
            checks.append("解析失败红色提示及重新上传入口")
            # Network failure must be visible and manually recoverable.
            page.route("**/contracts", lambda route: route.abort())
            page.goto(server.url + "/contract-list.html")
            expect(page.locator("#list-message")).to_contain_text("无法连接服务器")
            page.unroute("**/contracts")
            page.get_by_role("button", name="重新加载", exact=True).click()
            expect(page.locator("#table-wrap")).to_be_visible()
            checks.append("请求失败可见提示和重试")
            page.get_by_role("button", name="退出", exact=True).click()
            page.wait_for_url("**/login.html")
            login("reviewer02")
            expect(page.get_by_role("heading", name="还没有合同")).to_be_visible()
            page.goto(server.url + "/contract-detail.html?id=" + cid)
            expect(page.locator("#detail-error")).to_have_text("资源不存在")
            checks.append("reviewer02看不到reviewer01合同，猜ID返回可见404提示")
            expired = jwt.encode({"user_id": ids["reviewer02"], "auth_version": 1,
                                  "exp": datetime.now(timezone.utc) - timedelta(seconds=10)}, signing_key(), algorithm="HS256")
            page.evaluate("token => sessionStorage.setItem('access_token', token)", expired)
            page.goto(server.url + "/contract-list.html")
            page.wait_for_url("**/login.html")
            assert page.evaluate("sessionStorage.getItem('access_token')") is None
            checks.append("过期token的401统一清理并跳回登录")
            page.goto(server.url + "/docs")
            expect(page.get_by_role("heading", name="离线接口文档")).to_be_visible()
            checks.append("未登录可打开离线/docs，无CDN请求")
            # Actual browser close/reopen with a new tab context.
            page.goto(server.url + "/login.html")
            login("reviewer01")
            context.close()
            browser.close()
            browser = pw.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe", headless=True,
                                          args=["--disable-background-networking"])
            page = browser.new_page()
            page.goto(server.url + "/")
            page.wait_for_url("**/login.html")
            assert page.evaluate("sessionStorage.getItem('access_token')") is None
            checks.append("关闭浏览器重开不保留sessionStorage，回登录页")
            browser.close()
        assert not errors, errors
        assert not outside, outside
        with sf() as session:
            state["mock_sdk_calls"] = session.scalar(select(func.count()).select_from(LLMLog))
        state.update({"browser_errors": errors, "external_resource_requests": outside, "passed": True})
        (ROOT / "work/web-browser-acceptance.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False, indent=2))
    finally:
        if worker: stop_process_tree(worker)
        if hasattr(server, "process"): server.stop()
        engine.dispose()


if __name__ == "__main__":
    main()
