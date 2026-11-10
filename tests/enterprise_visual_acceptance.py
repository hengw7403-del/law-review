"""Real local browser, existing records only, no model calls or write APIs."""
import json,os,sys,sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'work/browser-test-deps'))
os.environ['TEMP']=str(root/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
from playwright.sync_api import sync_playwright,expect
out=root/'work/enterprise-screenshots';out.mkdir(exist_ok=True)
base='http://127.0.0.1:8000'
private=json.loads((root/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
tid='f57e3e2c88ec4f8fb034f3147a76050d'
errors=[];writes=[]
with sqlite3.connect(str(root/'law.db')) as db:before=db.execute('SELECT count(*) FROM llm_logs').fetchone()[0]
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000},device_scale_factor=1)
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:writes.append(r.url) if r.method=='POST' and not r.url.endswith('/auth/login') else None)
    page.goto(base+'/login.html');expect(page.locator('#username')).to_be_visible()
    page.screenshot(path=str(out/'01-login.png'))
    page.locator('#username').fill('reviewer01');page.locator('#password').fill(private['reviewer01']['password'])
    page.locator('#login-submit').click();page.wait_for_url('**/contract-list.html')
    expect(page.locator('#table-wrap')).to_be_visible()
    count=page.locator('#contract-rows tr').count()
    expect(page.locator('#contract-total')).to_have_text(str(count))
    assert page.locator('#reviewed-total').inner_text().isdigit()
    page.screenshot(path=str(out/'02-contract-list.png'))
    page.goto(base+'/result.html?id='+tid);expect(page.locator('.risk-card')).to_have_count(4)
    expect(page.locator('[data-stat=high] .stat-value')).to_have_text('1')
    expect(page.locator('[data-stat=medium] .stat-value')).to_have_text('3')
    assert page.locator('.risk-distribution span').count()==4
    assert not page.locator('.risk-card details').first.evaluate('(e)=>e.open')
    page.locator('.risk-card details').last.locator('summary').click()
    page.evaluate('scrollTo(0,0)')
    page.screenshot(path=str(out/'03-result.png'))
    page.goto(base+'/report.html?id='+tid);expect(page.locator('#report h1')).to_have_count(1)
    assert page.locator('#report').bounding_box()['width']<=720
    page.screenshot(path=str(out/'04-report.png'))
    page.goto(base+'/contract-detail.html?id=c0220fbfcb4449f1ae7ffc802de04a75')
    expect(page.locator('#contract-panel')).to_be_visible()
    page.screenshot(path=str(out/'06-detail.png'))
    page.set_viewport_size({'width':375,'height':812})
    for name,url in [('login','/login.html'),('list','/contract-list.html'),('result','/result.html?id='+tid),('report','/report.html?id='+tid)]:
        page.goto(base+url)
        page.wait_for_timeout(500)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),name
        if name=='result':page.screenshot(path=str(out/'05-mobile-375.png'))
    page.goto(base+'/login.html')
    page.evaluate('Api.message(document.getElementById("login-error"),"测试错误提示")')
    expect(page.locator('#login-error')).to_be_visible();page.wait_for_timeout(3200)
    expect(page.locator('#login-error')).to_be_hidden()
    browser.close()
assert not errors,errors
assert not writes,writes
with sqlite3.connect(str(root/'law.db')) as db:assert db.execute('SELECT count(*) FROM llm_logs').fetchone()[0]==before
(out/'acceptance.json').write_text(json.dumps({'passed':True,'browser_errors':errors,'business_write_requests':writes,'model_calls':0,'contract_count':count},indent=2),encoding='utf-8')
print('Browser acceptance passed; screenshots: '+str(out))
