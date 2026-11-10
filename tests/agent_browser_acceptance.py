"""Read-only UI verification of real paid runs; never presses Start."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'work/browser-test-deps'))
os.environ['TEMP']=str(ROOT/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
from playwright.sync_api import sync_playwright,expect


def main():
    state=json.loads((ROOT/'work/agent-mcp-acceptance.json').read_text(encoding='utf-8'))
    private=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
    out=ROOT/'work/agent-ui';out.mkdir(exist_ok=True);errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        def login(username):
            page.goto('http://localhost/workspace/login');page.locator('#username').fill(username);page.locator('#password').fill(private[username]['password'])
            page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('http://localhost/workspace')
        login('reviewer01');expect(page.locator('.sidebar').get_by_role('link',name='AI 助手',exact=True)).to_be_visible()
        page.goto('http://localhost/workspace/agent?run='+state['list']['run_id'])
        expect(page.locator('.agent-timeline li')).to_have_count(1)
        expect(page.locator('.agent-summary')).to_contain_text('找到3份')
        page.get_by_role('button',name='看看 OA 里有哪些合同',exact=True).click()
        expect(page.locator('#agent-request')).to_have_value('看看 OA 里有哪些合同')
        page.screenshot(path=str(out/'01-list-run.png'))
        page.goto('http://localhost/workspace/agent?run='+state['review']['run_id'])
        expect(page.locator('.agent-timeline li.completed')).to_have_count(3)
        expect(page.locator('.agent-summary')).to_contain_text('已写回OA 1份')
        expect(page.locator('.agent-cost')).to_contain_text('54/200')
        page.screenshot(path=str(out/'02-review-timeline.png'),full_page=True)
        details=page.get_by_role('link',name='查看 C001 详情',exact=True);expect(details).to_be_visible()
        details.click();page.wait_for_url('**/workspace/results/'+state['review']['details'][0]['task_id'])
        expect(page.locator('.contract-results')).to_be_visible()
        page.goto('http://localhost/workspace/agent');expect(page.locator('.agent-timeline li.completed')).to_have_count(3)
        page.set_viewport_size({'width':375,'height':812});page.reload();expect(page.locator('.agent-timeline li.completed')).to_have_count(3)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'03-mobile.png'),full_page=True)
        page.locator('.user-menu summary').click();page.get_by_role('button',name='退出',exact=True).click()
        login('requester01');expect(page.locator('.sidebar').get_by_role('link',name='AI 助手',exact=True)).to_have_count(0)
        token=page.evaluate("sessionStorage.getItem('access_token')");headers={'Authorization':'Bearer '+token}
        self_post=page.request.post('http://localhost/agent/run',headers=headers,data={'request':'list OA'})
        assert self_post.status==403
        assert page.request.get('http://localhost/agent/runs/'+state['list']['run_id'],headers=headers).status==403
        page.goto('http://localhost/workspace/agent');page.wait_for_url('http://localhost/workspace')
        browser.close()
    assert not errors,errors
    (out/'acceptance.json').write_text(json.dumps({'passed':True,'extra_model_calls':0,'browser_errors':errors,
        'checks':['真实list时间线','真实取/审/写三步骤','分项成本','详情链接','刷新恢复','375px','requester菜单/路由/后端403']},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Real Agent browser acceptance passed; zero additional model calls')


if __name__=='__main__':main()
