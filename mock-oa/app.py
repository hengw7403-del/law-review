"""Disposable OA: REST and official MCP SDK share one in-memory repository."""
from contextlib import asynccontextmanager
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

ROOT = Path(os.environ.get('OA_SAMPLES_DIR', str(Path(__file__).resolve().parents[1] / 'samples')))
MAX_CONTENT_BYTES = int(os.environ.get('OA_MAX_CONTENT_BYTES', '131072'))
lock = threading.RLock()
contracts = {}


def reset(count=3,clear_stance=None):
    if isinstance(count,bool) or not isinstance(count,int) or not 0<=count<=100:
        raise ValueError('OA测试合同数必须在0至100之间')
    samples=['contract_a.txt','contract_c_bad.txt','contract_c_clean.txt']
    with lock:
        contracts.clear()
        for number in range(count):
            cid='C{:03d}'.format(number+1);filename=samples[number%len(samples)]
            content = (ROOT / filename).read_text(encoding='utf-8-sig')
            contracts[cid] = {'id':cid,'name':filename,'content':content,
                              'stance':None if cid==clear_stance else ['party_a','party_b','party_a'][number%3],
                              'size':len(content.encode('utf-8')),'result':None,
                              'due_date':datetime.now(timezone(timedelta(hours=8))).date().isoformat()}
    return {'ok':True,'count':count}


def pending_contracts():
    with lock:
        return [{'id':c['id'],'name':c['name'],'size':c['size']} for c in contracts.values() if c['result'] is None]


def contract_detail(contract_id):
    with lock:
        if contract_id not in contracts:raise ValueError('OA_NOT_FOUND: 合同不存在')
        result=copy.deepcopy(contracts[contract_id])
    if len(result['content'].encode('utf-8')) > MAX_CONTENT_BYTES:
        raise ValueError('OA_CONTENT_TOO_LARGE: 正文超过外部接口限制')
    return result


def save_result(contract_id, result):
    if len(json.dumps(result,ensure_ascii=False).encode('utf-8')) > 65536:
        raise ValueError('OA_RESULT_TOO_LARGE: 结果超过64KB')
    with lock:
        if contract_id not in contracts:raise ValueError('OA_NOT_FOUND: 合同不存在')
        contracts[contract_id]['result']=copy.deepcopy(result)
    return {'ok':True}


reset()
mcp = MCPServer('Mock OA')


@mcp.tool()
def list_contracts() -> list[dict]:
    """列出尚未写回审查结果的OA合同，返回id、name和size。"""
    return pending_contracts()


@mcp.tool()
def get_contract(contract_id: str) -> dict:
    """获取OA合同正文和已写回的result；未知ID或正文超限会报错。"""
    try:return contract_detail(contract_id)
    except ValueError as exc:raise ToolError(str(exc)) from exc


@mcp.tool()
def write_result(contract_id: str, result: dict) -> dict:
    """将真实审查结果写回OA。调用后可通过get_contract或REST读取。"""
    try:return save_result(contract_id,result)
    except ValueError as exc:raise ToolError(str(exc)) from exc


mcp_app=mcp.streamable_http_app(json_response=True,stateless_http=True,transport_security=TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=['localhost:*','127.0.0.1:*','mock-oa:*','host.docker.internal:*','testserver'],
    allowed_origins=['http://localhost:*','http://127.0.0.1:*']))


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app=FastAPI(title='Mock OA (test only; memory resets on restart)',lifespan=lifespan)


@app.get('/api/contracts')
def listing():return pending_contracts()


@app.get('/api/contracts/{contract_id}')
def detail(contract_id: str):
    try:return contract_detail(contract_id)
    except ValueError as exc:raise HTTPException(404 if 'OA_NOT_FOUND' in str(exc) else 413,str(exc))


class ResultBody(BaseModel):
    result: dict


@app.post('/api/contracts/{contract_id}/result')
def result(contract_id: str, body: ResultBody):
    try:return save_result(contract_id,body.result)
    except ValueError as exc:raise HTTPException(404 if 'OA_NOT_FOUND' in str(exc) else 413,str(exc))


@app.post('/api/reset')
def reset_api(count: int=Query(default=3,ge=0,le=100),clear_stance: str=Query(default=None,pattern=r'^C\d{3}$')):
    """Test-only override; default sample stances remain unchanged."""
    return reset(count,clear_stance)


# Keep REST routes first, and start the mounted SDK lifespan above.
app.mount('/',mcp_app)
