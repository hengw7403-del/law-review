"""Run using the isolated Python 3.13 OA environment, not main Python 3.9."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

spec=importlib.util.spec_from_file_location('oa_test_service',Path(__file__).with_name('app.py'))
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)


class OAApiTests(unittest.TestCase):
    def setUp(self):
        # The SDK session manager lifespan is single-use; each test gets a new app.
        spec.loader.exec_module(service)

    def test_rest_and_write_reset(self):
        service.reset()
        with TestClient(service.app) as c:
            self.assertEqual(len(c.get('/api/contracts').json()),3)
            self.assertEqual(c.get('/api/contracts/missing').status_code,404)
            self.assertEqual(c.post('/api/contracts/C001/result',json={'result':{'task_id':'test-only'}}).json(),{'ok':True})
            self.assertEqual(c.get('/api/contracts/C001').json()['result'],{'task_id':'test-only'})
            self.assertEqual(len(c.get('/api/contracts').json()),2)
            self.assertEqual(c.post('/api/reset').json()['count'],3)
            self.assertIsNone(c.get('/api/contracts/C001').json()['result'])
            self.assertEqual(c.post('/api/reset?count=1').json()['count'],1)
            self.assertEqual(len(c.get('/api/contracts').json()),1)
            self.assertEqual(c.post('/api/reset?count=15').json()['count'],15)
            self.assertEqual(len(c.get('/api/contracts').json()),15)
            self.assertEqual(c.post('/api/reset?count=101').status_code,422)

    def test_content_limit_is_413_not_500(self):
        with patch.object(service,'MAX_CONTENT_BYTES',1):
            with TestClient(service.app) as c:
                response=c.get('/api/contracts/C001');self.assertEqual(response.status_code,413)
                self.assertIn('OA_CONTENT_TOO_LARGE',response.text)

    def test_mcp_tools_and_error_results(self):
        headers={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'}
        with TestClient(service.app) as c:
            def rpc(rid,method,params):return c.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':rid,'method':method,'params':params}).json()
            rpc(1,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
            tools=rpc(2,'tools/list',{})['result']['tools']
            self.assertEqual({t['name'] for t in tools},{'list_contracts','get_contract','write_result'})
            result=rpc(3,'tools/call',{'name':'get_contract','arguments':{'contract_id':'missing'}})['result']
            self.assertTrue(result['isError'])
            with patch.object(service,'MAX_CONTENT_BYTES',1):
                result=rpc(4,'tools/call',{'name':'get_contract','arguments':{'contract_id':'C001'}})['result']
                self.assertTrue(result['isError']);self.assertIn('OA_CONTENT_TOO_LARGE',str(result))
