"""Vue production build + real API on a private DB copy; never starts worker."""
import json,os,sys,sqlite3,subprocess,time,tempfile,re
from contextlib import nullcontext
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'work/browser-test-deps'))
os.environ['TEMP']=str(root/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
import httpx
from playwright.sync_api import sync_playwright,expect
from tests.review_helpers import stop_process_tree
base='http://127.0.0.1:8001';tid='f57e3e2c88ec4f8fb034f3147a76050d';cid='c0220fbfcb4449f1ae7ffc802de04a75'
credentials=json.loads((root/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
out=root/'work/vue-screenshots';out.mkdir(exist_ok=True)
errors=[];checks=[]
with nullcontext(tempfile.mkdtemp(prefix='vue-acceptance-',dir=str(root/'work'))) as directory:
    database=Path(directory)/'acceptance.db'
    with sqlite3.connect(str(root/'law.db')) as source,sqlite3.connect(str(database)) as target:source.backup(target)
    source.close();target.close()
    environment=os.environ.copy();environment['DATABASE_URL']='sqlite:///'+database.as_posix();environment['CONTRACT_UPLOAD_DIR']=str(Path(directory)/'uploads')
    with (out/'api.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--port','8001'],cwd=str(root),env=environment,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with httpx.Client(base_url=base,trust_env=False) as http:
                for _ in range(100):
                    try:
                        if http.get('/health/ready').status_code==200:break
                    except httpx.TransportError:pass
                    time.sleep(.1)
                else:raise AssertionError('API startup failed')
                assert http.get('/contracts').status_code==401
                assert http.get('/workspace/results/'+tid).status_code==200
                assert http.get('/assets/missing.js').status_code==404
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
                target='/workspace/results/'+tid
                page.goto(base+target);page.wait_for_url(re.compile(r'/workspace/login\?redirect='))
                page.screenshot(path=str(out/'login.png'))
                page.locator('#username').fill('reviewer01');page.locator('#password').fill(credentials['reviewer01']['password']);page.get_by_role('button',name='登录',exact=True).click()
                page.wait_for_url(base+target);expect(page.locator('.risk-card')).to_have_count(4)
                checks.append('登录守卫及原地址回跳')
                page.reload();expect(page.locator('.risk-card')).to_have_count(4);checks.append('history刷新与Pinia会话恢复')
                page.locator('.risk-card details').first.locator('summary').click();expect(page.locator('.risk-card details').first).to_have_attribute('open','')
                page.screenshot(path=str(out/'result.png'))
                page.get_by_role('link',name='查看完整报告').click();expect(page.locator('.markdown h1')).to_have_count(1)
                with page.expect_download() as download:page.get_by_role('button',name='下载 .md').click()
                assert Path(download.value.path()).read_text(encoding='utf-8').startswith('# 合同')
                page.screenshot(path=str(out/'report.png'));checks.append('安全Markdown及报告下载')
                page.goto(base+'/workspace/contracts/'+cid);expect(page.get_by_role('link',name='查看结果',exact=True)).to_be_visible()
                expect(page.locator('.clause')).to_have_count(10);page.get_by_role('button',name='下一页').click();expect(page.locator('.clause')).to_have_count(2)
                page.screenshot(path=str(out/'detail.png'));checks.append('详情与真实分页')
                page.goto(base+'/workspace/contracts');expect(page.locator('.contract-row')).to_have_count(4)
                page.screenshot(path=str(out/'list.png'))
                page.get_by_role('button',name='上传合同',exact=True).click();page.locator('#file').set_input_files(str(root/'samples/contract_a.txt'));page.locator('#title').fill('Vue验收合同')
                page.get_by_role('button',name='上传',exact=True).click();expect(page.locator('.contract-row')).to_have_count(5)
                row=page.locator('.contract-row').filter(has_text='Vue验收合同');row.get_by_role('link',name='查看',exact=True).click()
                expect(page.get_by_role('button',name='发起审查',exact=True)).to_be_enabled()
                page.get_by_role('button',name='发起审查',exact=True).click();expect(page.get_by_text('排队中',exact=True).first).to_be_visible()
                expect(page.get_by_role('button',name='发起审查',exact=True)).to_be_disabled();checks.append('真实上传/建任务（隔离库，无worker）')
                # Poll states are UI fixtures only, not claimed as model execution.
                calls=[]
                def task_fixture(route):
                    response=route.fetch();data=response.json();calls.append(1);data.update(status='running' if len(calls)==1 else 'completed',progress=.5 if len(calls)==1 else 1,executed_items=5,planned_model_items=10)
                    route.fulfill(response=response,json=data)
                page.route('**/tasks/*',task_fixture);page.get_by_role('button',name='重新获取进度').click()
                expect(page.get_by_text('正在审查（已完成 5/10）')).to_be_visible();expect(page.get_by_role('link',name='查看结果',exact=True)).to_be_visible(timeout=10000)
                count=len(calls);page.wait_for_timeout(2300);assert len(calls)==count;page.unroute('**/tasks/*');checks.append('轮询终态停止（响应夹具）')
                for path in ['/workspace/login','/workspace/contracts','/workspace/contracts/'+cid,target,'/workspace/reports/'+tid]:
                    page.set_viewport_size({'width':375,'height':812});page.goto(base+path);page.wait_for_timeout(300);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),path
                page.goto(base+target);expect(page.locator('.risk-card')).to_have_count(4);page.screenshot(path=str(out/'mobile.png'))
                # Untrusted strings are text, never v-html.
                def hostile(route):
                    response=route.fetch();data=response.json();data['items'][0]['description']='<img src=x onerror="window.__xss=1">';route.fulfill(response=response,json=data)
                page.route('**/reviews/*/items',hostile);page.reload();expect(page.locator('.risk-card')).to_have_count(4);assert page.locator('.risk-card img').count()==0;assert page.evaluate('window.__xss') is None
                page.unroute('**/reviews/*/items');checks.append('HTML转义')
                page.evaluate('sessionStorage.setItem("access_token","invalid")');page.reload();page.wait_for_url(re.compile(r'/workspace/login\?redirect='));checks.append('统一401清理与跳转')
                page.locator('#username').fill('reviewer02');page.locator('#password').fill(credentials['reviewer02']['password']);page.get_by_role('button',name='登录',exact=True).click()
                expect(page.locator('.notice.error').first).to_be_visible();checks.append('越权404可见')
                browser.close()
            assert not errors,errors
            with sqlite3.connect(str(database)) as connection:
                assert connection.execute('SELECT count(*) FROM llm_logs').fetchone()[0]==175
            connection.close()
        finally:
            stop_process_tree(process)
(out/'acceptance.json').write_text(json.dumps({'passed':True,'checks':checks,'browser_errors':errors,'model_calls':0},ensure_ascii=False,indent=2),encoding='utf-8')
print('Vue production browser acceptance passed')
