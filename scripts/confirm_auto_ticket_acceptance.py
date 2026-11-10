"""Real UI confirmation, no model calls; preserves before/after screenshots."""
import json,os,sys,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'work/browser-test-deps'))
os.environ['TEMP']=str(ROOT/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
from playwright.sync_api import sync_playwright,expect
import httpx
state=json.loads((ROOT/'work/auto-ticket-acceptance.json').read_text(encoding='utf-8'))
credentials=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
bad=state['runs']['bad']['ticket_id'];clean=state['runs']['clean']['ticket_id'];out=ROOT/'work/auto-ticket-formal';out.mkdir(exist_ok=True);base='http://127.0.0.1:8000';errors=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/workspace/login');page.locator('#username').fill('reviewer01');page.locator('#password').fill(credentials['reviewer01']['password']);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('**/workspace/contracts')
    page.goto(base+'/workspace/tickets');page.get_by_role('button',name='待人工确认',exact=True).click();expect(page.locator('.ticket-card.needs-human')).to_have_count(1);page.screenshot(path=str(out/'01-needs-human-list.png'))
    page.locator(f'.ticket-card[href$="{bad}"]').click();expect(page.get_by_role('button',name='确认通过',exact=True)).to_be_visible();expect(page.locator('.human-warning')).to_contain_text('发现 1 处高风险');expect(page.get_by_role('button',name='关闭工单',exact=True)).to_have_count(0)
    page.screenshot(path=str(out/'02-needs-human-detail.png'))
    page.set_viewport_size({'width':375,'height':812});page.reload();expect(page.locator('.human-warning')).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'05-mobile.png'))
    page.set_viewport_size({'width':1440,'height':1000});page.locator('#confirm-note').fill('验收测试：确认通过流程验证');page.get_by_role('button',name='确认通过',exact=True).click();expect(page.locator('.human-warning')).to_have_count(0);expect(page.get_by_role('button',name='关闭工单',exact=True)).to_be_visible();page.screenshot(path=str(out/'03-confirmed-done.png'))
    page.goto(base+'/workspace/tickets/'+clean);expect(page.get_by_role('button',name='关闭工单',exact=True)).to_be_visible();expect(page.locator('.human-warning')).to_have_count(0);page.screenshot(path=str(out/'04-clean-done.png'))
    browser.close()
assert not errors,errors
with httpx.Client(base_url=base,trust_env=False) as c:
    r=c.post('/auth/login',json={'username':'reviewer01','password':credentials['reviewer01']['password']});r.raise_for_status();c.headers['Authorization']='Bearer '+r.json()['access_token']
    assert c.post('/tickets/'+bad+'/confirm',json={}).status_code==409
    assert c.post('/tickets/'+clean+'/confirm',json={}).status_code==409
    assert c.get('/tickets?status=needs_human').json()['tickets']==[]
    state['confirmation']={label:c.get('/tickets/'+key).json() for label,key in [('bad',bad),('clean',clean)]}
assert state['confirmation']['bad']['confirmed_by']=='5eceabfc593d4eae95f8f07bb544529b'
assert state['confirmation']['bad']['status']=='done' and state['confirmation']['clean']['status']=='done'
connection=sqlite3.connect(str(ROOT/'law.db'));assert connection.execute('SELECT count(*) FROM llm_logs').fetchone()[0]-state['baseline_logs']==25;assert connection.execute('PRAGMA foreign_key_check').fetchall()==[];connection.close()
state['final_new_calls']=25;state['browser_errors']=errors;(ROOT/'work/auto-ticket-acceptance.json').write_text(json.dumps(state,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print('Formal UI confirmation, non-needs-human 409, filters and audit verified; no extra model calls.')
