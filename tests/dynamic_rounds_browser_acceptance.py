"""Read-only browser validation of the real 1/3-contract runs. No Start clicks."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'work/browser-test-deps'))
os.environ['TEMP']=str(ROOT/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
from playwright.sync_api import sync_playwright,expect


def main():
    state=json.loads((ROOT/'work/dynamic-rounds-acceptance.json').read_text(encoding='utf-8'))
    private=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
    out=ROOT/'work/dynamic-rounds-ui';out.mkdir(exist_ok=True);errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://localhost/workspace/login');page.locator('#username').fill('reviewer01');page.locator('#password').fill(private['reviewer01']['password'])
        page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('http://localhost/workspace')
        page.goto('http://localhost/workspace/agent?run='+state['1']['run_id'])
        expect(page.locator('.agent-timeline li.completed')).to_have_count(4)
        stats=page.locator('p').filter(has_text='已用')
        expect(stats).to_contain_text('4/5');expect(stats).to_contain_text('1/1')
        page.screenshot(path=str(out/'01-one-contract.png'),full_page=True)
        page.goto('http://localhost/workspace/agent?run='+state['3']['run_id'])
        expect(page.locator('p').filter(has_text='已用')).to_contain_text('/11')
        page.screenshot(path=str(out/'02-three-progress.png'),full_page=True)
        expect(page.locator('.agent-timeline li.completed')).to_have_count(10,timeout=600000)
        expect(page.locator('p').filter(has_text='已用')).to_contain_text('10/11')
        expect(page.locator('p').filter(has_text='已用')).to_contain_text('3/3')
        expect(page.locator('.agent-summary')).to_contain_text('已写回OA 3份')
        page.screenshot(path=str(out/'03-three-complete.png'),full_page=True)
        page.set_viewport_size({'width':375,'height':812});page.reload()
        expect(page.locator('.agent-timeline li.completed')).to_have_count(10)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(out/'04-mobile.png'),full_page=True)
        browser.close()
    assert not errors,errors
    (out/'acceptance.json').write_text(json.dumps({'passed':True,'model_calls_added_by_browser':0,'browser_errors':errors,
        'checks':['真实1份4/5步、1/1完成','真实3份10/11步、3/3完成','10条时间线','375px']},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Dynamic rounds real-run browser acceptance passed; zero extra model calls')


if __name__=='__main__':main()
