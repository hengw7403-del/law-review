"""Read-only Docker UI validation; warning cases are browser-only response fixtures."""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'work/browser-test-deps'))
os.environ['TEMP']=str(ROOT/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
from playwright.sync_api import sync_playwright,expect
state=json.loads((ROOT/'work/docker-firstboot-20261110/acceptance.json').read_text(encoding='utf-8'))
credentials=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'));base=state['base'];tid=state['task_id'];ticket=state['ticket_id'];errors=[]
out=ROOT/'work/full-ticket-ui';out.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/workspace/login');page.locator('#username').fill('reviewer01');page.locator('#password').fill(credentials['reviewer01']['password']);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('**/workspace/contracts')
    page.goto(base+'/workspace/tickets/'+ticket);expect(page.locator('.human-warning')).to_be_visible();expect(page.locator('main h1')).to_have_text('技术服务合同 · 条款审查')
    text=page.locator('main').inner_text();assert '#'+ticket[:8] in text and '全部条款' in text;assert '+00:00' not in text
    expect(page.locator('progress')).to_have_attribute('value','1');page.screenshot(path=str(out/'01-ticket-125.png'))
    page.goto(base+'/workspace/results/'+tid);expect(page.locator('.contract-results')).to_be_visible();expect(page.locator('.contract-results .panel')).to_have_count(5)
    page.screenshot(path=str(out/'02-result-contract-section.png'))
    page.locator('.contract-results').scroll_into_view_if_needed();page.screenshot(path=str(out/'03-missing-five.png'))
    def warning_result(route):
        response=route.fetch();data=response.json();data['all_clauses_insufficient']=True
        for item in data['items']:
            if item['scope']=='clause' and item['check_status'] not in {'not_applicable','merged'}:
                item.update(check_status='insufficient',risk_level='unknown',description='界面测试：本条未取得有效依据。',suggestion='导入法规后重新审查。',evidence=[])
        data['stats']={key:0 for key in data['stats']}
        for item in data['items']:
            key=item['risk_level'] if item['check_status']=='supported' else 'no_issue' if item['check_status']=='no_issue_detected' else item['check_status']
            if key in data['stats']:data['stats'][key]+=1
        route.fulfill(response=response,json=data)
    page.route('**/reviews/'+tid+'/items',warning_result);page.reload();expect(page.locator('.knowledge-warning')).to_be_visible();page.evaluate('scrollTo(0,0)');page.screenshot(path=str(out/'04-warning.png'))
    page.get_by_role('link',name='去导入',exact=True).click();expect(page.get_by_role('heading',name='法规知识库导入说明')).to_be_visible()
    page.unroute('**/reviews/'+tid+'/items');page.goto(base+'/workspace/results/'+tid);expect(page.locator('.contract-results')).to_be_visible();expect(page.locator('.knowledge-warning')).to_have_count(0)
    def warning_task(route):
        response=route.fetch();data=response.json();data['all_clauses_insufficient']=True;route.fulfill(response=response,json=data)
    page.route('**/tasks/'+tid,warning_task);page.goto(base+'/workspace/tickets/'+ticket);expect(page.locator('.knowledge-warning')).to_be_visible();page.unroute('**/tasks/'+tid)
    page.set_viewport_size({'width':375,'height':812});page.reload();expect(page.locator('.human-warning')).to_be_visible();expect(page.locator('.knowledge-warning')).to_have_count(0);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'05-mobile.png'))
    browser.close()
assert not errors,errors
(out/'acceptance.json').write_text(json.dumps({'passed':True,'browser_errors':errors,'real_model_calls':0,'checks':['125项进度','合同级5项单独展示','统一标题/上海时间/短号/关注点','真实结果无错误黄色提示','两页黄色提示及去导入（响应夹具）','375px']},ensure_ascii=False,indent=2),encoding='utf-8')
print('Full-ticket Docker browser acceptance passed')
