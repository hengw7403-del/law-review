import json
import unittest
import httpx
from app.services.mcp_client import MCPClient,MCPError


TOOLS=[{'name':n,'description':n,'inputSchema':{'type':'object','properties':{}}}
       for n in ['list_contracts','get_contract','write_result']]


class MCPClientTests(unittest.TestCase):
    def client(self,reply):
        def handle(request):
            body=json.loads(request.content)
            self.assertIn('application/json',request.headers['Accept'])
            self.assertIn('text/event-stream',request.headers['Accept'])
            if body['method']=='initialize':result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}
            elif body['method']=='notifications/initialized':return httpx.Response(202)
            else:
                self.assertEqual(request.headers['MCP-Protocol-Version'],'2025-11-25')
                result=reply(body)
                if isinstance(result,httpx.Response):return result
            return httpx.Response(200,json={'jsonrpc':'2.0','id':body['id'],'result':result})
        return MCPClient('http://oa/mcp',transport=httpx.MockTransport(handle))

    def test_discovery_function_definitions_and_structured_wrapper(self):
        with self.client(lambda b:{'tools':TOOLS} if b['method']=='tools/list' else {'structuredContent':{'result':[{'id':'C001'}]}}) as c:
            tools=c.list_tools();definitions=c.function_definitions(tools)
            self.assertEqual([x['function']['name'] for x in definitions],[x['name'] for x in TOOLS])
            self.assertEqual(c.call_tool('list_contracts',{}),[{'id':'C001'}])

    def test_sse_response_and_error_tool(self):
        def reply(body):
            return httpx.Response(200,headers={'Content-Type':'text/event-stream'},
                text='event: message\ndata: '+json.dumps({'jsonrpc':'2.0','id':body['id'],'result':{'content':[{'type':'text','text':'{"ok":true}'}]}})+'\n\n')
        with self.client(reply) as c:self.assertEqual(c.call_tool('x',{}),{'ok':True})
        with self.client(lambda b:{'isError':True,'content':[{'type':'text','text':'OA_NOT_FOUND'}]}) as c:
            with self.assertRaises(MCPError) as error:c.call_tool('get_contract',{'contract_id':'missing'})
            self.assertEqual(error.exception.code,'MCP_TOOL_ERROR')
            self.assertIn('OA_NOT_FOUND',str(error.exception))

    def test_connection_timeout_and_invalid_response_are_explicit(self):
        for exception,code in [(httpx.ConnectError('offline'),'MCP_CONNECTION_FAILED'),(httpx.ReadTimeout('slow'),'MCP_TIMEOUT')]:
            def fail(request,exc=exception):raise exc
            with MCPClient('http://oa/mcp',transport=httpx.MockTransport(fail)) as c:
                with self.assertRaises(MCPError) as result:c.list_tools()
                self.assertEqual(result.exception.code,code)
        with self.client(lambda b:httpx.Response(200,text='not-json')) as c:
            with self.assertRaises(MCPError) as result:c.list_tools()
            self.assertEqual(result.exception.code,'MCP_INVALID_RESPONSE')

    def test_bounds_and_invalid_definitions(self):
        with self.client(lambda b:httpx.Response(200,content=b'x'*(2*1024*1024+1))) as c:
            with self.assertRaises(MCPError) as error:c.list_tools()
            self.assertEqual(error.exception.code,'MCP_RESPONSE_TOO_LARGE')
        with self.client(lambda b:httpx.Response(200,headers={'Content-Type':'text/event-stream'},content=b'x'*(2*1024*1024+1))) as c:
            with self.assertRaises(MCPError) as error:c.list_tools()
            self.assertEqual(error.exception.code,'MCP_RESPONSE_TOO_LARGE')
        for tools in [[TOOLS[0],TOOLS[0]],[{'name':'bad name','inputSchema':{}}]]:
            with self.assertRaises(MCPError):MCPClient.function_definitions(tools)
        with self.client(lambda b:{'tools':[],'nextCursor':'again'}) as c:
            with self.assertRaises(MCPError) as error:c.list_tools()
            self.assertEqual(error.exception.code,'MCP_TOO_MANY_TOOLS')
