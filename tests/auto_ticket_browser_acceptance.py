"""Isolated UI regression with a mock conversation SDK, no paid calls."""
import json,os,sys,sqlite3,subprocess,tempfile,time,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'work/browser-test-deps'))
os.environ['TEMP']=str(ROOT/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
import httpx
from playwright.sync_api import sync_playwright,expect
from sqlalchemy.orm import sessionmaker
from app.db import create_db_engine
from app.models import Ticket,ReviewTask,ReviewItem
from app.services.ticket_completion import complete_tickets
from tests.review_helpers import stop_process_tree

def main():
    out=ROOT/'work/auto-ticket-ui';out.mkdir(exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix='auto-ticket-ui-',dir=str(ROOT/'work')));database=directory/'law.db'
    source=sqlite3.connect(str(ROOT/'law.db'));target=sqlite3.connect(str(database));source.backup(target);before=source.execute('SELECT count(*) FROM llm_logs').fetchone()[0];source.close();target.close()
    env=os.environ.copy();env['DATABASE_URL']='sqlite:///'+database.as_posix();env['CONTRACT_UPLOAD_DIR']=str(directory/'uploads')
    credentials=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'));base='http://127.0.0.1:8004';errors=[]
    with (out/'api.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-m','tests.ticket_browser_server'],cwd=str(ROOT),env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with httpx.Client(base_url=base,trust_env=False) as c:
                for _ in range(100):
                    try:
                        if c.get('/health/ready').status_code==200:break
                    except httpx.TransportError:pass
                    time.sleep(.1)
                else:raise AssertionError('API not ready')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/workspace/new-ticket');page.wait_for_url(re.compile('/workspace/login'))
                page.locator('#username').fill('reviewer01');page.locator('#password').fill(credentials['reviewer01']['password']);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url(base+'/workspace/new-ticket')
                expect(page.locator('#ticket-input')).to_be_visible();key=page.evaluate('sessionStorage.getItem("conversation_id")');page.reload();expect(page.locator('#ticket-input')).to_be_visible();assert page.evaluate('sessionStorage.getItem("conversation_id")')==key
                page.locator('#ticket-input').fill('你是谁');page.get_by_role('button',name='发送',exact=True).click();expect(page.locator('.chat-message').last).to_contain_text('工单助手')
                page.get_by_role('button',name=re.compile('contract_c_bad.txt')).click();page.get_by_role('button',name='发送',exact=True).click();expect(page.locator('.chat-message').last).to_contain_text('确认')
                page.locator('#ticket-input').fill('确认创建工单');page.locator('#ticket-input').press('Enter');page.wait_for_url(re.compile('/workspace/tickets/[a-f0-9]+$'))
                ticket_id=page.url.rsplit('/',1)[-1];expect(page.get_by_text('审查中',exact=True)).to_be_visible()
                expect(page.get_by_role('button',name='接单',exact=True)).to_have_count(0);expect(page.get_by_role('button',name='发起审查',exact=True)).to_have_count(0)
                page.screenshot(path=str(out/'reviewing.png'))
                engine=create_db_engine('sqlite:///'+database.as_posix());sf=sessionmaker(bind=engine,autoflush=False)
                with sf.begin() as s:
                    ticket=s.get(Ticket,ticket_id);task=s.get(ReviewTask,ticket.review_task_id);snapshot=json.loads(task.input_snapshot)
                    for n,unit in enumerate(snapshot['plan']+snapshot['not_applicable']):
                        result={'description':'UI fixture','reason':'UI fixture','suggestion':'UI fixture','evidence':[]}
                        s.add(ReviewItem(id='fixture-'+str(n),review_id=task.id,item_key=unit['item_key'],clause_id=unit['clause_id'],rule_id=unit['rule_id'],execution_status='done',check_status='supported' if n==0 else 'no_issue_detected',risk_level='high' if n==0 else None,machine_result=json.dumps(result)))
                    task.status='completed';task.done_items=task.total_items;s.flush();complete_tickets(s,task)
                expect(page.get_by_role('button',name='确认通过',exact=True)).to_be_visible(timeout=10000);expect(page.get_by_role('button',name='关闭工单',exact=True)).to_have_count(0)
                page.screenshot(path=str(out/'needs-human.png'))
                page.get_by_role('link',name='我的工单',exact=True).click();page.get_by_role('button',name='待人工确认',exact=True).click();expect(page.locator('.ticket-card.needs-human').first).to_be_visible();page.screenshot(path=str(out/'filter.png'))
                page.locator(f'.ticket-card[href$="{ticket_id}"]').click();page.locator('#confirm-note').fill('UI fixture confirmation');page.get_by_role('button',name='确认通过',exact=True).click();expect(page.get_by_role('button',name='关闭工单',exact=True)).to_be_visible();expect(page.locator('.human-warning')).to_have_count(0)
                page.screenshot(path=str(out/'confirmed.png'))
                with sf() as s:
                    saved=s.get(Ticket,ticket_id);assert saved.status=='done' and saved.confirmed_by and saved.confirmation_note=='UI fixture confirmation'
                page.set_viewport_size({'width':375,'height':812});page.reload();expect(page.get_by_role('button',name='关闭工单',exact=True)).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'mobile.png'))
                browser.close();engine.dispose()
            assert not errors,errors
        finally:stop_process_tree(process)
    c=sqlite3.connect(str(ROOT/'law.db'));assert c.execute('SELECT count(*) FROM llm_logs').fetchone()[0]==before;c.close()
    (out/'acceptance.json').write_text(json.dumps({'passed':True,'real_model_calls':0,'database':'isolated copy','checks':['自动跳详情','无需接单/发起审查','轮询完成','高风险提示和筛选','确认后done及隐藏提示','确认人/备注落库','375px','恢复会话'],'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Automatic ticket browser acceptance passed')

if __name__=='__main__':main()
