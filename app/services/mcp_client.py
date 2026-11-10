"""Python 3.9 Streamable HTTP MCP client for request/response tools.

Official MCP SDK 2.3 requires Python 3.10+. OA uses it in an isolated runtime;
this client implements initialize, initialized, tools/list, tools/call with
httpx. JSON and SSE POST responses are supported, with bounded reads/timeouts.
It does not implement stdio, OAuth, sampling, or resumable server event streams.
"""
import json
import os
import re
import time
from typing import Optional
from urllib.parse import urlsplit

import httpx


class MCPError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code=code


class MCPClient:
    def __init__(self, endpoint: Optional[str] = None, transport=None, timeout=20):
        self.endpoint=endpoint or os.environ.get('MCP_OA_URL','http://localhost:9000/mcp')
        parsed=urlsplit(self.endpoint)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
            raise MCPError('MCP_INVALID_URL','MCP_OA_URL必须是无内嵌凭据的HTTP地址')
        self.timeout=timeout
        self.http=httpx.Client(timeout=httpx.Timeout(timeout,connect=min(timeout,5)),
                               trust_env=False,follow_redirects=False,transport=transport)
        self.protocol=None;self.session=None;self.request_id=0

    def close(self):self.http.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def _rpc(self,method,params=None,notification=False):
        self.request_id+=1
        rid=self.request_id
        body={'jsonrpc':'2.0','method':method}
        if not notification:body['id']=rid
        if params is not None:body['params']=params
        headers={'Accept':'application/json, text/event-stream'}
        if self.protocol:headers['MCP-Protocol-Version']=self.protocol
        if self.session:headers['Mcp-Session-Id']=self.session
        try:
            start=time.monotonic()
            with self.http.stream('POST',self.endpoint,json=body,headers=headers) as response:
                if response.status_code>=300:
                    raise MCPError('MCP_HTTP_ERROR','MCP接口返回HTTP {}'.format(response.status_code))
                if response.headers.get('mcp-session-id'):self.session=response.headers['mcp-session-id']
                if notification:return None
                if 'text/event-stream' in response.headers.get('content-type',''):
                    data=[];size=0;message=None;buffer=b''
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>2*1024*1024:raise MCPError('MCP_RESPONSE_TOO_LARGE','MCP响应超过2MB')
                        if time.monotonic()-start>self.timeout:raise MCPError('MCP_TIMEOUT','MCP响应超时')
                        buffer+=chunk
                        while b'\n' in buffer:
                            raw_line,buffer=buffer.split(b'\n',1)
                            line=raw_line.rstrip(b'\r').decode('utf-8')
                            if line.startswith('data:'):data.append(line[5:].lstrip())
                            elif not line and data:
                                raw='\n'.join(data);data=[]
                                if not raw.strip():continue
                                event=json.loads(raw)
                                if event.get('id')==rid:message=event;break
                        if message is not None:break
                    if message is None:raise MCPError('MCP_INVALID_RESPONSE','SSE流没有对应请求的结果')
                else:
                    chunks=[];size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>2*1024*1024:raise MCPError('MCP_RESPONSE_TOO_LARGE','MCP响应超过2MB')
                        if time.monotonic()-start>self.timeout:raise MCPError('MCP_TIMEOUT','MCP响应超时')
                        chunks.append(chunk)
                    message=json.loads(b''.join(chunks))
            if not isinstance(message,dict) or message.get('jsonrpc')!='2.0' or message.get('id')!=rid:
                raise MCPError('MCP_INVALID_RESPONSE','MCP响应ID或结构错误')
            if 'error' in message:
                raise MCPError('MCP_RPC_ERROR',str(message['error'].get('message','MCP协议错误'))[:1000])
            if 'result' not in message:raise MCPError('MCP_INVALID_RESPONSE','MCP响应缺少result')
            return message['result']
        except httpx.TimeoutException as exc:raise MCPError('MCP_TIMEOUT','连接或读取MCP服务超时') from exc
        except httpx.RequestError as exc:raise MCPError('MCP_CONNECTION_FAILED','无法连接MCP服务，请检查MCP_OA_URL及OA服务状态') from exc
        except (ValueError,AttributeError) as exc:raise MCPError('MCP_INVALID_RESPONSE','MCP接口返回了无效JSON') from exc

    def initialize(self):
        if self.protocol:return
        result=self._rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},
                                     'clientInfo':{'name':'law-review','version':'1.0'}})
        if result.get('protocolVersion') not in {'2025-03-26','2025-06-18','2025-11-25'}:
            raise MCPError('MCP_UNSUPPORTED_PROTOCOL','OA协商了不支持的协议版本')
        self.protocol=result['protocolVersion']
        self._rpc('notifications/initialized',notification=True)

    def list_tools(self):
        self.initialize();tools=[];cursor=None
        for _ in range(10):
            result=self._rpc('tools/list',{'cursor':cursor} if cursor else {})
            tools.extend(result.get('tools',[]));cursor=result.get('nextCursor')
            if not cursor:return tools
        raise MCPError('MCP_TOO_MANY_TOOLS','MCP工具分页超过限制')

    def call_tool(self,name,args):
        self.initialize()
        result=self._rpc('tools/call',{'name':name,'arguments':args})
        if result.get('isError'):
            text=' '.join(b.get('text','') for b in result.get('content',[]) if b.get('type')=='text')
            raise MCPError('MCP_TOOL_ERROR',text[:1500] or '外部工具执行失败')
        if result.get('structuredContent') is not None:
            content=result['structuredContent']
            return content['result'] if isinstance(content,dict) and set(content)=={'result'} else content
        text='\n'.join(b.get('text','') for b in result.get('content',[]) if b.get('type')=='text')
        try:return json.loads(text)
        except ValueError:return text

    @staticmethod
    def function_definitions(tools):
        definitions=[];names=set()
        for tool in tools:
            name=tool.get('name','');schema=tool.get('inputSchema',{})
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}',name) or name in names or schema.get('type')!='object':
                raise MCPError('MCP_INVALID_TOOL_SCHEMA','MCP工具名称或输入schema不合法')
            names.add(name)
            definitions.append({'type':'function','function':{'name':name,'description':tool.get('description',''),
                                                            'parameters':schema}})
        return definitions
