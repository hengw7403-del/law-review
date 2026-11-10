"""Role UI acceptance on an isolated real API/DB; confirmations affect fixtures only."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'work/browser-test-deps'))
os.environ['TEMP']=str(ROOT/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
from playwright.sync_api import sync_playwright,expect
from tests.test_rbac_matrix import RBACMatrixTests
from tests.parse_helpers import PreviewServer


def main():
    out=ROOT/'work/rbac-ui';out.mkdir(exist_ok=True)
    RBACMatrixTests.setUpClass();fixture=RBACMatrixTests();fixture.setUp()
    server=PreviewServer();errors=[];checks=[]
    try:
        server.start(Path(fixture.engine.url.database),env_overrides={'DEEPSEEK_API_KEY':'','LLM_TEST_CALL_LIMIT':'0'},authenticated=False)
        base=server.url
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
            def login(uid):
                context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page()
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/workspace/login');page.locator('#username').fill(uid);page.locator('#password').fill('private-rbac-test-password')
                page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url(base+'/workspace')
                expect(page.locator('.loading')).to_have_count(0)
                return context,page
            context,page=login('requester01')
            expect(page.locator('.sidebar nav a')).to_have_text(['概览','我的提交','合同'])
            expect(page.locator('main h1')).to_have_text('我的提交')
            expect(page.locator('main .ticket-card')).to_have_count(1)
            page.screenshot(path=str(out/'01-requester-home.png'))
            token=page.evaluate("sessionStorage.getItem('access_token')");headers={'Authorization':'Bearer '+token}
            assert page.request.get(base+'/tickets/ticket2',headers=headers).status==404
            assert page.request.post(base+'/tickets/ticket1/confirm',headers=headers,data={}).status==403
            page.goto(base+'/workspace/tickets/ticket1');expect(page.locator('.human-warning')).to_be_visible()
            expect(page.get_by_role('button',name='确认通过',exact=True)).to_have_count(0)
            expect(page.locator('#confirm-note')).to_have_count(0)
            page.goto(base+'/workspace/admin/users');page.wait_for_url(base+'/workspace');expect(page.locator('main h1')).to_have_text('我的提交')
            page.goto(base+'/workspace/knowledge');page.wait_for_url(base+'/workspace')
            page.set_viewport_size({'width':375,'height':812});page.reload();expect(page.locator('main .ticket-card')).to_have_count(1)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(out/'02-requester-mobile.png'),full_page=True);context.close();checks.append('requester菜单、主页、隔离、403、路由守卫、375px')
            for uid,number in [('reviewer01',1),('reviewer02',2)]:
                context,page=login(uid)
                expect(page.locator('.sidebar nav a')).to_have_text(['概览','工单','合同','AI 助手'])
                expect(page.get_by_role('heading',name='待你处理',exact=False)).to_be_visible()
                if number==1:page.screenshot(path=str(out/'03-reviewer-home.png'))
                page.goto(base+'/workspace/tickets');expect(page.locator('main .ticket-card')).to_have_count(2)
                page.goto(base+'/workspace/tickets/ticket'+str(number));expect(page.get_by_role('button',name='确认通过',exact=True)).to_be_visible()
                page.get_by_role('button',name='确认通过',exact=True).click();expect(page.locator('.human-warning')).to_have_count(0)
                page.goto(base+'/workspace/admin/users');page.wait_for_url(base+'/workspace');context.close()
            checks.append('两名reviewer均可看全部工单并确认非本人needs_human；拒绝管理员页面')
            context,page=login('admin')
            expect(page.locator('.sidebar nav a')).to_have_text(['概览','工单','合同','AI 助手','知识库','用户'])
            expect(page.locator('.dashboard-stats strong')).to_have_text(['2','2','0','3'])
            expect(page.get_by_role('heading',name='最近活动',exact=True)).to_be_visible()
            page.screenshot(path=str(out/'04-admin-home.png'))
            page.get_by_role('link',name='知识库',exact=True).click();expect(page.locator('main h1')).to_have_text('知识库')
            expect(page.get_by_role('link',name='导入法规说明',exact=True)).to_be_visible();page.screenshot(path=str(out/'05-admin-knowledge.png'))
            page.get_by_role('link',name='用户',exact=True).click();expect(page.locator('main h1')).to_have_text('用户管理')
            expect(page.get_by_role('heading',name='requester01',exact=True)).to_be_visible();page.screenshot(path=str(out/'06-admin-users.png'));context.close()
            checks.append('admin全部菜单、全量统计、活动、知识库和用户页面')
            browser.close()
        assert not errors,errors
        (out/'acceptance.json').write_text(json.dumps({'passed':True,'checks':checks,'browser_errors':errors,'model_calls':0,
            'environment':'isolated API + real JWT + seeded test DB; no production ticket confirmations'},ensure_ascii=False,indent=2),encoding='utf-8')
        print('RBAC browser acceptance passed; no model calls')
    finally:
        if hasattr(server,'process'):server.stop()
        fixture.tearDown()


if __name__=='__main__':main()
