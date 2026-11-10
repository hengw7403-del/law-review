import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx

from app.db import create_db_engine
from app.migrations import add_user_org_name
from app.services.party_parser import parse_parties, suggest_stance
from app.services.ticket_title import contract_title
from tests.parse_helpers import ROOT, PreviewServer


class PartyParserTests(unittest.TestCase):
    def test_label_variants_and_sales_labels(self):
        for label in ['甲方', '甲方（委托方）', '甲方(委托方)', '甲　方', '甲 方', '委托方（甲方）', '卖方']:
            for colon in [':', '：']:
                with self.subTest(label=label, colon=colon):
                    text = label + colon + ' 示例星桥科技有限公司；受托方（乙方）：示例澄川信息技术有限公司。'
                    self.assertEqual(parse_parties(text), {'party_a_name': '示例星桥科技有限公司', 'party_b_name': '示例澄川信息技术有限公司'})
        self.assertEqual(parse_parties('买方：示例买家有限公司')['party_b_name'], '示例买家有限公司')

    def test_sample_and_matching_both_directions_unicode(self):
        parties = parse_parties((ROOT / 'samples/contract_c_bad.txt').read_text(encoding='utf-8'))
        self.assertEqual(parties, {'party_a_name': '示例星桥科技有限公司', 'party_b_name': '示例澄川信息技术有限公司'})
        for org, stance in [('示例星桥科技有限公司', 'party_a'), ('星桥科技', 'party_a'), ('示例澄川信息技术有限公司', 'party_b'), ('示例星桥科技有限公司总部', 'party_a'), ('无关公司', None), ('示例', None), ('', None), (None, None)]:
            self.assertEqual(suggest_stance(org, parties), stance)
        self.assertEqual(suggest_stance('ＡＢＣ 科技，有限公司', {'party_a_name': 'ABC科技有限公司', 'party_b_name': None}), 'party_a')

    def test_first_thirty_lines_length_and_punctuation(self):
        for text in ['', '技术服务合同\n第一条 服务范围', '\n' * 30 + '甲方：示例星桥科技有限公司', '甲方：甲', '甲方：' + '甲' * 51]:
            self.assertEqual(parse_parties(text), {'party_a_name': None, 'party_b_name': None})
        self.assertEqual(parse_parties('\n' * 29 + '甲方：甲乙')['party_a_name'], '甲乙')
        self.assertEqual(parse_parties('甲方： 甲乙 ，地址：北京')['party_a_name'], '甲乙')
        self.assertEqual(parse_parties('甲方：' + '甲' * 50)['party_a_name'], '甲' * 50)
        self.assertIsNone(parse_parties('甲方：甲乙\n甲方：丙丁')['party_a_name'])

    def test_org_migration_is_idempotent_preserves_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_db_engine('sqlite:///' + (Path(directory) / 'old.db').as_posix())
            try:
                with engine.begin() as c:
                    c.exec_driver_sql('CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT)')
                    c.exec_driver_sql("INSERT INTO users VALUES ('u','existing')")
                add_user_org_name(engine)
                add_user_org_name(engine)
                with engine.connect() as c:
                    self.assertEqual(tuple(c.exec_driver_sql('SELECT * FROM users').one()), ('u', 'existing', None))
                    self.assertEqual(sum(row[1] == 'org_name' for row in c.exec_driver_sql('PRAGMA table_info(users)')), 1)
            finally:
                engine.dispose()

    def test_display_title_uses_contract_title_without_extension(self):
        self.assertEqual(contract_title('自定义合同', 'file.txt'), '自定义合同')
        self.assertEqual(contract_title('不同于文件名的标题.PDF', 'file.txt'), '不同于文件名的标题')
        self.assertEqual(contract_title('', 'file.txt'), 'file')


class PartyPreviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = PreviewServer()
        cls.server.start()
        cls.http = httpx.Client(base_url=cls.server.url, headers=dict(cls.server.opener.addheaders), trust_env=False)

    @classmethod
    def tearDownClass(cls):
        cls.http.close()
        cls.server.stop()

    def test_org_preview_confirm_manual_override_and_no_preview_writes(self):
        data = (ROOT / 'samples/contract_c_bad.txt').read_bytes()
        files = {'file': ('contract_c_bad.txt', data, 'text/plain')}
        before = len(self.http.get('/contracts').json()['contracts'])
        for org, expected in [('星桥科技', 'party_a'), ('示例澄川信息技术有限公司', 'party_b'), ('无关组织', None)]:
            updated = self.http.patch('/auth/me', json={'org_name': org})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(self.http.get('/auth/me').json()['org_name'], org)
            response = self.http.post('/parse/preview', files=files)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['suggested_stance'], expected)
            self.assertEqual(response.json()['detected_parties']['party_b_name'], '示例澄川信息技术有限公司')
        self.assertEqual(len(self.http.get('/contracts').json()['contracts']), before)
        self.http.patch('/auth/me', json={'org_name': '星桥科技'})
        uploaded = self.http.post('/contracts', files=files, data={'party_stance': 'party_b', 'title': '手动确认乙方'})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertEqual(uploaded.json()['suggested_stance'], 'party_a')
        saved = self.http.get('/contracts/' + uploaded.json()['contract_id']).json()
        self.assertEqual(saved['party_stance'], 'party_b')
        self.assertEqual(saved['title'], '手动确认乙方')
        self.assertEqual(len(self.http.get('/contracts').json()['contracts']), before + 1)
        self.assertEqual(self.http.post('/contracts', files=files, data={'party_stance': 'party_a', 'stance': 'party_b'}).status_code, 422)

    def test_org_validation_clear_auth_and_unrecognized_parties(self):
        for value in [{'org_name': '甲' * 201}, {'org_name': '测试', 'role': 'admin'}, {}]:
            self.assertEqual(self.http.patch('/auth/me', json=value).status_code, 422)
        for value in [None, '   ']:
            self.assertIsNone(self.http.patch('/auth/me', json={'org_name': value}).json()['org_name'])
        response = self.http.post('/parse/preview', files={'file': ('no-parties.txt', ('第一条 服务范围\n' + '双方按约定完成技术服务。' * 10).encode('utf-8'), 'text/plain')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['detected_parties'], {'party_a_name': None, 'party_b_name': None})
        self.assertIsNone(response.json()['suggested_stance'])
        self.assertEqual(httpx.patch(self.server.url + '/auth/me', json={'org_name': '测试'}, trust_env=False).status_code, 401)
