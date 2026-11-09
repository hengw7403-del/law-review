import json,os,sys,sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'work/browser-test-deps'))
os.environ['TEMP']=str(root/'work/browser-temp');os.environ['TMP']=os.environ['TEMP']
import httpx
from playwright.sync_api import sync_playwright,expect
old=json.loads((root/'work/official-contrast-results.json').read_text(encoding='utf-8'))
private=json.loads((root/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
base='http://127.0.0.1:8000';results={};errors=[]
with sqlite3.connect(str(root/'law.db')) as db:before=db.execute('SELECT count(*) FROM llm_logs').fetchone()[0]
with httpx.Client(base_url=base,trust_env=False) as http:
    tokens={}
    for name in ['reviewer01','reviewer02','admin']:
        response=http.post('/auth/login',json={'username':name,'password':private[name]['password']});response.raise_for_status();tokens[name]=response.json()['access_token']
    for label in ['bad','clean']:
        tid=old['runs'][label]['task_id'];path='/reviews/'+tid+'/items'
        data=http.get(path,headers={'Authorization':'Bearer '+tokens['reviewer01']}).json();results[label]=data
        assert http.get(path).status_code==401
        assert http.get(path,headers={'Authorization':'Bearer '+tokens['reviewer02']}).status_code==404
        assert http.get(path,headers={'Authorization':'Bearer '+tokens['admin']}).status_code==200
        with sqlite3.connect(str(root/'law.db')) as db:
            rows=db.execute('SELECT check_status,risk_level FROM review_items WHERE review_id=?',(tid,)).fetchall()
            stats=dict.fromkeys(data['stats'],0)
            for status,risk in rows:
                key=risk if status=='supported' else 'no_issue' if status=='no_issue_detected' else status
                stats[key]+=1
            assert stats==data['stats']
    markdown=http.get('/reviews/'+old['runs']['bad']['task_id']+'/report',headers={'Authorization':'Bearer '+tokens['reviewer01']}).text
screens=root/'work/results-screenshots';screens.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
    context=browser.new_context(viewport={'width':1360,'height':960},accept_downloads=True)
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/login.html');page.evaluate('(t)=>sessionStorage.setItem("access_token",t)',tokens['reviewer01'])
    tid=old['runs']['bad']['task_id']
    page.goto(base+'/result.html?id='+tid)
    expect(page.locator('.risk-card.medium')).to_have_count(3)
    expect(page.locator('.risk-card.unknown')).to_have_count(results['bad']['stats']['insufficient'])
    expect(page.locator('[data-stat="medium"] .stat-value')).to_have_text('3')
    details=page.locator('.risk-card.medium details').first
    assert not details.evaluate('(e)=>e.open');details.locator('summary').click();assert details.evaluate('(e)=>e.open')
    expect(details.locator('.clause-content').first).not_to_be_empty()
    page.screenshot(path=str(screens/'result-bad.png'))
    page.get_by_role('link',name='查看完整报告').click();page.wait_for_url('**/report.html?id=*')
    expect(page.locator('#report h1')).to_have_count(1)
    assert page.locator('#report li').count()>0
    assert page.locator('#report pre').count()>0
    with page.expect_download() as download:page.get_by_role('button',name='下载 .md').click()
    file=Path(download.value.path());assert file.read_text(encoding='utf-8')==markdown
    page.screenshot(path=str(screens/'report.png'))
    malicious='<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>'
    page.evaluate('(s)=>renderMarkdown("# 测试\\n\\n- **粗体**\\n\\n"+s+"\\n\\n```\\n"+s+"\\n```",document.getElementById("report"))',malicious)
    assert page.locator('#report img,#report script').count()==0
    assert page.evaluate('window.__xss') is None
    # Synthetic hostile contract response only in browser memory; no DB edits.
    hostile=json.loads(json.dumps(results['bad']));hostile['items'][0]['clause_content']=malicious;hostile['items'][0]['description']=malicious
    page.route('**/reviews/'+tid+'/items',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(hostile)))
    page.goto(base+'/result.html?id='+tid);expect(page.locator('.risk-card.medium')).to_have_count(3)
    assert page.locator('#cards img,#cards script').count()==0
    assert page.evaluate('window.__xss') is None
    assert malicious in page.locator('#cards').inner_text()
    page.goto(base+'/result.html?id='+old['runs']['clean']['task_id'])
    expect(page.locator('#risk-total')).to_contain_text('0 风险')
    expect(page.locator('.risk-card.unknown')).to_have_count(results['clean']['stats']['insufficient'])
    page.screenshot(path=str(screens/'result-clean.png'))
    page.goto(base+'/contract-detail.html?id='+old['runs']['bad']['contract_id'])
    expect(page.locator('#view-result')).to_be_visible();assert 'result.html?id=' in page.locator('#view-result').get_attribute('href')
    page.locator('#view-report').click();page.wait_for_url('**/report.html?id=*')
    page.evaluate('sessionStorage.setItem("access_token","invalid")');page.reload();page.wait_for_url('**/login.html')
    browser.close()
assert not errors,errors
with sqlite3.connect(str(root/'law.db')) as db:log_count=db.execute('SELECT count(*) FROM llm_logs').fetchone()[0];assert log_count==before
state={'passed':True,'stats':{k:v['stats'] for k,v in results.items()},'checks':['数据库统计一致','401/404/admin权限','3张中风险卡','引用默认折叠并可展开','报告标题列表代码块','下载原始md一致','合同HTML不执行','Markdown HTML不执行','干净合同保留依据不足','详情跳结果/报告','过期401跳登录'],'model_calls':0,'llm_logs':log_count,'browser_errors':errors}
(root/'work/results-browser-acceptance.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(state,ensure_ascii=False))
