"""Real Docker UI upload acceptance; creates two contracts, no model calls."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work/browser-test-deps'))
os.environ['TEMP'] = str(ROOT / 'work/browser-temp')
os.environ['TMP'] = os.environ['TEMP']
from playwright.sync_api import sync_playwright, expect

BASE = 'http://localhost'
STATE = ROOT / 'work/party-acceptance.json'
assert not STATE.exists(), 'Keep existing acceptance records; do not duplicate uploads'
out = ROOT / 'work/party-ui'
out.mkdir(exist_ok=True)
private = json.loads((ROOT / 'work/initial-user-credentials.json').read_text(encoding='utf-8'))
state = {'base': BASE, 'contracts': {}, 'browser_errors': [], 'model_calls': 0}

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe', headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1000})
    page.on('pageerror', lambda error: state['browser_errors'].append(str(error)))
    page.goto(BASE + '/workspace/login')
    page.locator('#username').fill('reviewer01')
    page.locator('#password').fill(private['reviewer01']['password'])
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_url('**/workspace/contracts')

    def organization(value):
        page.locator('.user-menu summary').click()
        page.get_by_role('button', name='设置我的组织', exact=True).click()
        dialog = page.get_by_role('dialog', name='设置我的组织')
        expect(dialog.locator('#org-name')).to_be_enabled()
        dialog.locator('#org-name').fill(value)
        with page.expect_response(lambda r: r.url.endswith('/auth/me') and r.request.method == 'PATCH') as response:
            dialog.get_by_role('button', name='保存', exact=True).click()
        assert response.value.status == 200
        expect(dialog).to_have_count(0)
        page.locator('.user-menu summary').click()

    for side, org, suggestion in [('party_b', '星桥科技', 'party_a'), ('party_a', '澄川信息技术', 'party_b')]:
        organization(org)
        page.get_by_role('button', name='上传合同', exact=True).click()
        dialog = page.get_by_role('dialog', name='上传合同')
        dialog.locator('#file').set_input_files(str(ROOT / 'samples/contract_c_bad.txt'))
        dialog.locator('#title').fill('立场识别验收·' + ('乙方' if side == 'party_b' else '甲方'))
        with page.expect_response(lambda r: '/parse/preview' in r.url) as response:
            dialog.get_by_role('button', name='解析并识别当事方').click()
        result = response.value.json()
        assert result['suggested_stance'] == suggestion, result
        expect(dialog.get_by_text('甲方　示例星桥科技有限公司', exact=True)).to_be_visible()
        expect(dialog.get_by_text('乙方　示例澄川信息技术有限公司', exact=True)).to_be_visible()
        expect(dialog.locator('input[value="' + suggestion + '"]')).to_be_checked()
        dialog.locator('input[value="' + side + '"]').check()
        page.screenshot(path=str(out / ('01-preview-' + side + '.png')))
        with page.expect_response(lambda r: r.url.endswith('/contracts') and r.request.method == 'POST') as response:
            dialog.get_by_role('button', name='确认立场并上传').click()
        assert response.value.status == 201
        uploaded = response.value.json()
        state['contracts'][side] = {'contract_id': uploaded['contract_id'], 'preview': result,
                                   'upload': uploaded, 'confirmed_stance': side}
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        expect(dialog).to_have_count(0)
        expect(page.locator('.contract-row').filter(has_text='立场识别验收·' + ('乙方' if side == 'party_b' else '甲方'))).to_be_visible()

    organization('与双方无关的组织')
    page.get_by_role('button', name='上传合同', exact=True).click()
    dialog = page.get_by_role('dialog', name='上传合同')
    dialog.locator('#file').set_input_files(str(ROOT / 'samples/contract_c_bad.txt'))
    dialog.get_by_role('button', name='解析并识别当事方').click()
    expect(dialog.get_by_text('请选择我方立场', exact=True)).to_be_visible()
    expect(dialog.locator('input[type=radio]:checked')).to_have_count(0)
    expect(dialog.get_by_role('button', name='确认立场并上传')).to_be_disabled()
    page.set_viewport_size({'width':375,'height':812})
    page.screenshot(path=str(out / '03-no-match-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    dialog.get_by_role('button', name='关闭', exact=True).click()
    page.set_viewport_size({'width':1440,'height':1000})
    organization('示例澄川信息技术有限公司')
    page.screenshot(path=str(out / '04-contract-list.png'))
    browser.close()

assert not state['browser_errors'], state['browser_errors']
state['ui_passed'] = True
STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'ui_passed':True, 'contract_ids':{side:item['contract_id'] for side,item in state['contracts'].items()}, 'model_calls':0}))
