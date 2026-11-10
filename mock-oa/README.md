# Mock OA（独立 Python 3.13 服务）

本轮选择**本地单独运行**，不是 Compose 第四服务。官方稳定 MCP SDK 2.3.0 要求 Python ≥3.10，且依赖较新的 Pydantic/httpx2；将其放在独立环境，保持主系统 Python 3.9 与原依赖不变。本机已有 Python 3.13，不需要另装解释器。Docker Python 3.13 基础镜像拉取曾遇网络超时，本轮没有将未验证的镜像作为交付。

在项目根目录执行：

```powershell
py -3.13 -m venv mock-oa/.venv
mock-oa/.venv/Scripts/python.exe -m pip install --index-url https://pypi.org/simple -r mock-oa/requirements.txt
mock-oa/.venv/Scripts/python.exe -m pip check
mock-oa/.venv/Scripts/python.exe -m uvicorn app:app --app-dir mock-oa --host 127.0.0.1 --port 9000
```

保持此终端运行。当前验收服务已经在9000端口启动，不要重复启动。数据在内存中，重启或调用POST `/api/reset`会清除写回结果。

## 接口

|接口|用途|
|---|---|
|GET /api/contracts|待审合同（result为空）|
|GET /api/contracts/C001|正文与result|
|POST /api/contracts/C001/result|JSON `{ "result": {...} }`写回|
|POST /api/reset|默认恢复三个样本；`?count=1/3/15`设置测试规模（0–100）|
|/mcp|Streamable HTTP MCP，官方SDK实现|

C001=contract_a、C002=contract_c_bad、C003=contract_c_clean。三个MCP工具为list_contracts、get_contract、write_result，和REST共用同一个内存仓库。正文限制默认128KiB，可用OA_MAX_CONTENT_BYTES配置；写回结果限64KiB。REST未知ID返回404，正文超限413；MCP按协议返回isError及OA_NOT_FOUND/OA_CONTENT_TOO_LARGE，不以HTTP404代替工具错误。

当count大于3时，按三个原样本轮换生成唯一ID（C004…），只用于规模/上限测试。reset会清空当前OA内存中的写回结果，不会删除主系统law.db或Agent记录。

本机主系统原生运行时MCP_OA_URL默认为 `http://localhost:9000/mcp`。主系统在Docker里时使用 `http://host.docker.internal:9000/mcp`，已写入Compose及.env.docker.example。该地址已实测可从API容器访问绑定本机回环的OA服务。

本服务用于本地模拟，没有客户级认证或持久化。SDK保留Host/Origin校验，允许本机与Docker宿主地址；不要把测试接口当生产OA发布。

## 测试

```powershell
mock-oa/.venv/Scripts/python.exe -m unittest discover -s mock-oa -p test_api.py -v
```

三项测试覆盖REST写回/读取/重置、404、413，以及MCP三工具发现和错误结果。另已用官方SDK Client分别验证2025兼容模式和2026-07-28自动协商；本系统Python 3.9 Client使用2025-11-25协议。

核对来源：[官方SDK稳定版与Python要求](https://pypi.org/project/mcp/2.3.0/)、[ASGI挂载与lifespan](https://py.sdk.modelcontextprotocol.io/run/asgi/)、[Streamable HTTP规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。
