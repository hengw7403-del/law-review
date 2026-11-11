# 法务合同辅助初审系统

工单默认全10条款规则+5缺失规则，12条合同共125项（clause_items=120、contract_items=5），工单done_items按已处理数计；普通合同接口默认行为保留。容器空知识库会自动导入并启用samples/laws/*.txt，非空不覆盖。最新Docker实测、提示边界和统一格式见[docs/full-rules-firstboot-notes.md](docs/full-rules-firstboot-notes.md)。

工单现已支持“建单即审查、高风险转人工”：正常路径无需接单或再次发起审查，高风险工单needs_human人工确认后才能完成/归档。当前状态机、幂等迁移、确认审计及真实25次调用验收见[docs/auto-ticket-notes.md](docs/auto-ticket-notes.md)。

新增自然语言工单入口：`/workspace/new-ticket`、`/workspace/tickets`，全部使用现有JWT；确认建单、接单后调用既有审查任务服务，worker不变。新增三张表，启动时幂等建表；权限、状态、成本和分步验收见[docs/tickets-notes.md](docs/tickets-notes.md)。

## Vue工作台

新前端位于src/：Vite + Vue 3 Composition API + Router 4 history + Pinia，仅用户会话入store，fetch统一请求，零UI库。运行`npm ci`、`npm run build`生成dist，然后启动FastAPI，打开`http://127.0.0.1:8000/workspace/contracts`；开发可另开`npm run dev`（5173代理API到8000）。旧.html工作台仍保留兼容。history fallback只作用于/workspace页面，不覆盖业务API和缺失资源。

讲解见[docs/frontend-notes.md](docs/frontend-notes.md)，目录树见[docs/frontend-tree.md](docs/frontend-tree.md)，完整结果页代码见[docs/frontend-example.md](docs/frontend-example.md)。浏览器验收：`python -m tests.vue_browser_acceptance`，使用临时数据库副本、无模型调用。

条款级检查只评价本条已写出的安排，缺少约定交合同级检查。worker将同条款同风险的重复supported项标为merged，保留原始模型输出，报告与结果页不重复计数。启动时幂等迁移旧库状态约束；真实样本验收见work/written-terms-acceptance.md。

上传一份合同，自动切分条款、检索相关法规、标出风险点，**每条结论都附带可溯源的法规原文**。

面向企业法务的辅助工具：不替代人做判断，只把人从翻文档里解放出来。

> **关于本项目**：作者在企业法务工单平台上工作后，独立实现的教学 / 复现版本。
> 简化了部署形态（单机 SQLite 取代分布式部署），保留核心设计思路。
> 不包含任何企业真实数据或代码，`samples/` 中的合同与法规均为公开或虚构材料。

---

## 解决什么问题

法务审一份合同平均 2–3 小时，其中约 60% 的时间花在「翻法规、找历史案例」上，
真正用于判断的不到一半。

这个系统把「找依据」变成「查索引」：

```
上传合同 → 切分条款 → 逐条检索法规 → 判定风险 → 生成带出处的报告
```

- 合同自动切分成条款，位置精确到字符
- 每条条款用 BM25 检索相关法规
- 模型判定风险等级，**依据必须来自检索结果，不允许编造**
- 输出 Markdown 报告，每条意见可回溯到法条原文

---

## 核心设计

| 设计 | 为什么这么做 |
|---|---|
| **依据只能从候选集里选** | 防止模型编造法条。服务端校验 ID 是否在本次候选内，引文一律从数据库取，不接受模型写的引文 |
| **四种状态分开** | `supported` / `insufficient` / `no_issue_detected` / `not_applicable`。把「没发现问题」和「没敢下结论」分开，不误导用户 |
| **条款偏移按 code point 计** | UTF-16 下标遇到 emoji 会错位，报告里的「点击定位到原文」就废了 |
| **规则带适用条件** | 用「违约责任」规则去查「争议解决」条款是浪费，也制造噪音。关键词不命中就不调用模型 |
| **条款级只管本条款** | 条款级检查不评价"缺少某项约定"——那是合同级缺失检查的活。混在一起会产生大量假 `insufficient` |
| **同风险去重** | 同一条款被多个规则指向同一问题时标 `merged`，报告只展示一条，原始判定仍保留在库里 |

> 每条决策的完整推演（备选方案、代价、踩过的坑）见 [设计文档](docs/design.md)。
| **任务持久化 + 断点续跑** | worker 挂了重启能接着跑，已完成的单元不重复扣费 |

---

## 技术栈

Python 3.9 · FastAPI · SQLite · SQLAlchemy · rank-bm25 · jieba · DeepSeek API · Docker Compose

前端使用 Vue 3 Composition API + Vite，手写样式、无 UI 组件库或运行时 CDN；原生版本仍保留兼容。

---

## 快速开始

```powershell
cd law-review
Copy-Item .env.docker.example .env.docker

# 编辑 .env.docker，填两个值：
#   JWT_SECRET=<32 字节以上随机串>
#   DEEPSEEK_API_KEY=sk-xxx

docker compose --env-file .env.docker build
docker compose --env-file .env.docker up -d
docker compose --env-file .env.docker exec api python -m app.cli seed-users
```

打开 `http://localhost`，用 `reviewer01` 登录。

首次空知识库启动会自动导入并启用 samples/laws 下全部 TXT，日志显示自动导入条数。非空库不会覆盖，也不会重新启用已停用的条文。导入失败仅记录警告；可在工作台“法规知识库导入说明”中查看手工导入方法。

上传 `samples/contract_a.txt`，发起审查，看结果页。

---

## 实测效果

用 10 个条款规则 + 5 个缺失规则跑「坑合同」（`contract_c_bad.txt`，乙方立场）：

| 条款 | 命中规则 | 判定 | 问题 |
|---|---|---|---|
| 第二条 | payment | 中风险 | 验收后 180 天付款，回款时点完全由对方控制 |
| 第十条 | liability | 中风险 | 违约金按日累计**无上限**，长期逾期可能远超实际损失 |
| 第十一条 | termination | 高风险 | 甲方可随时单方解除且免责，乙方无对等权利 |
| 第十二条 | dispute_resolution | 中风险 | "按最终签署文本约定"——等于没约定管辖 |

同一份合同共 120 个检查单元：`supported` 4、`insufficient` 0、`no_issue_detected` 23、`not_applicable` 92、失败 0。

对照测试：

| 场景 | 结果 |
|---|---|
| 规范合同（contract_a） | **0 误报**，五个正常条款（服务范围/验收/变更/知识产权/保密）全部判无问题 |
| 坑合同（contract_c_bad） | **4/4 全部抓到**，每条都带法规原文 |
| 同一条款多个规则指向同一风险 | 标记 `merged`，报告只展示一次 |

规则从 3 个扩到 10 个的过程中出现过一次回退：`insufficient` 从 0 反弹到 19。
原因是条款级检查开始评价"缺少某项约定"，而那些约定其实在别的条款里。
划清职责后（条款级只看本条款写了什么，缺什么交给合同级检查）恢复为 0。

---

## 项目结构

```text
app/
  main.py            FastAPI 入口
  models.py          7 张表（SQLAlchemy）
  api/               路由
  services/
    parser.py        文档解析（PDF/DOCX/TXT）
    clause_splitter.py 条款切分，code point 对齐
    chunker.py       法规切块
    search.py        BM25 检索
    validator.py     依据四层校验
    risk_judge.py    单条款判定
    review_worker.py 任务执行器
    report.py        报告生成
  static/            原生 HTML/JS 前端
  prompts/           提示词模板（版本化）
samples/             测试合同与法规
tests/               自动化测试
docker-compose.yml   api + worker + nginx
```

---

<details>
<summary><b>详细实现与开发记录</b>（点开查看完整技术细节）</summary>

规则定义已扩展为10项条款规则、5项规范合同级缺失规则，本轮仅生成计划，尚未执行扩展规则的模型验收。默认审查仍使用原3项条款规则，不自动扩大调用范围。运行 `.\.venv\Scripts\python.exe -m scripts.preview_rule_plan` 可离线重建合同A计划：work/rule-plan-v4.md（含定义与关键词）及work/rule-plan-v4.json。旧缺失规则ID保留兼容，不在新计划中重复列入。

当前包含原生HTML/JS工作台（登录、合同列表、详情与进度）、FastAPI、SQLite 七张表、JWT 登录、合同上传入库与资源权限、解析预览、法规入库、BM25、DeepSeek 判定、独立 worker、检查点落库和 Markdown 报告。已支持结果/报告Web页面及全量条款审查；人工复核工作流尚未实现。

## 本地Docker Compose部署

仅在本机部署，不涉及服务器。要求Docker Desktop已安装并运行Linux容器，且包含Compose v2；宿主机不需要Python、npm、Nginx等其他软件。先运行 `docker version` 和 `docker compose version`，必须能看到Engine服务端，不能只有CLI。

当前交付配置尚待本机Docker就绪后真实执行build/up验收；代码测试不等于镜像构建或容器验收通过。

### 从零启动（PowerShell）

在项目目录执行：

```powershell
cd C:\Users\admin\Documents\deepseek-harness\default-workspace\law-review
Copy-Item .env.docker.example .env.docker
docker run --rm python:3.9-slim python -c "import secrets; print(secrets.token_urlsafe(48))"
```

把上一步输出填入.env.docker的JWT_SECRET，再填写DEEPSEEK_API_KEY。不要覆盖已有.env.docker或轮换JWT_SECRET，否则旧登录token失效。JWT_SECRET至少32字节，API和worker使用同一个值；容器配置不会复制本机.env。

```powershell
New-Item -ItemType Directory -Force data | Out-Null
docker compose --env-file .env.docker build
# 仅用一次辅助命令设置挂载目录的所有权；长期运行的API/worker仍为UID 10001。
docker compose --env-file .env.docker run --rm --no-deps --user 0 --entrypoint sh api -c "chown 10001:10001 /data"
docker compose --env-file .env.docker up -d --wait --wait-timeout 180
docker compose --env-file .env.docker ps
curl.exe -f http://localhost/health/ready
```

若是Linux宿主机，可先使用 `mkdir -p data`，再执行同样的Docker命令。bind mount的宿主目录权限必须允许UID 10001读写；镜像内的chown不会改变宿主挂载目录权限。不要为了权限问题把长期服务改成root。

三个服务应均为healthy。只有Nginx映射宿主80端口；API/worker不映射宿主端口。API与worker共用law-review:local镜像；Dockerfile只复制app、samples和依赖清单，排除了本机law.db、work、data及私密.env文件。数据卷初始是全新数据库，不会带入本机账户或历史合同。

### 初始化账户、知识库

```powershell
docker compose --env-file .env.docker exec api python -m app.cli seed-users
```

在终端分别隐藏输入并确认admin、reviewer01、reviewer02密码。已有账号跳过，不重置。也可单独执行：

```powershell
docker compose --env-file .env.docker exec api python -m app.cli create-user --username reviewer03 --role reviewer
```

打开 http://localhost/ 登录。容器空知识库首次自动导入并启用内置法条（目前26块/4个来源），非空时保持不变。以下是手工补充示例；已有来源若内容或元数据变更，需使用新版本，不要重复换ID导入：

```powershell
docker compose --env-file .env.docker exec api python -m app.cli import-law --file samples/laws/civil_code_liability.txt --source-id civil-code-liability --name "民法典违约责任节选"
docker compose --env-file .env.docker exec api python -m app.cli import-law --file samples/laws/civil_code_termination.txt --source-id civil-code-termination --name "民法典解除及争议条款节选"
# 人工核对原文及适用版本后再执行启用；不要把未核对草稿当成已验证依据。
docker compose --env-file .env.docker exec api python -m app.cli enable-source --source-id civil-code-liability
docker compose --env-file .env.docker exec api python -m app.cli enable-source --source-id civil-code-termination
docker compose --env-file .env.docker restart api worker
```

API/worker在进程启动时重建BM25索引，启用后要重启；索引语料保存在SQLite，jieba分词缓存保存在/data/index。上传一个合同，发起审查，核对queued→running→completed（或如实显示失败原因）、结果引用及llm_logs。该步骤会使用真实DeepSeek接口产生费用，不能以“空知识库快速完成”充当模型调用成功。

### 持久化及日志验收

```powershell
docker compose --env-file .env.docker logs --tail 200 api worker nginx
docker compose --env-file .env.docker logs --no-color | Select-String -Pattern 'Traceback'
docker compose --env-file .env.docker down
docker compose --env-file .env.docker up -d --wait --wait-timeout 180
curl.exe -f http://localhost/health/ready
```

再次登录，核对同一合同ID、任务ID、原始上传文件和报告仍存在，日志没有traceback。down不删除./data；不要删除该目录。主要持久文件为data/law.db（及SQLite WAL/SHM）、data/uploads、data/index。备份运行中的SQLite应使用SQLite backup接口，不能只复制law.db而遗漏未合并的WAL。本机原有law.db保持独立，不在本轮迁入容器。

### 启动与健康检查机制

API入口先建表、执行已有幂等迁移、检查数据库可读写，成功后才exec Uvicorn；启动失败不会对外提供接口。SQLite是文件库，因此没有额外数据库容器，也没有第四个init服务。API健康检查访问/health/ready，worker依赖API healthy，Nginx依赖API及worker healthy。

worker用独立线程每5秒写原子心跳（模型调用期间仍更新），健康探针要求30秒内心跳和数据库可读。restart: unless-stopped负责进程崩溃重启；Docker不会仅因unhealthy而自动重启仍存活的进程，故必须区分健康告警和进程退出。故障任务仍按现有检查点恢复，不承诺远端API恰好一次。

Nginx允许25m请求体，应用单文件仍限制20MiB；静态CSS/JS等缓存1小时，页面和业务响应no-store。TZ=Asia/Shanghai并安装容器内tzdata；数据库现有UTC时间存储约定保持不变，网页继续按浏览器时区显示。配置文件解析、容器build/up、真实模型流程与down/up持久化分别验收，不互相替代。

## 原生Python运行环境

- Windows、Python 3.9.6（64 位）
- 本项目 `.venv`，不依赖系统默认 Python 版本
- `requirements.txt` 固定直接依赖，`constraints-py39.txt` 固定间接依赖；完整已安装版本见 `requirements.lock.txt`

## 启动

在 PowerShell 中执行：

```powershell
cd C:\Users\admin\Documents\deepseek-harness\default-workspace\law-review
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app
```

新环境先执行 `py -3.9 -m venv .venv`。本机现有虚拟环境已经安装完成；所有命令明确使用项目解释器，无须修改执行策略或激活虚拟环境。

- `http://127.0.0.1:8000/health/live`：200，`{"status":"ok"}`。
- `http://127.0.0.1:8000/health/ready`：数据库可读写时 200；失败时 503，`{"status":"unavailable"}`。

浏览器入口为 `http://127.0.0.1:8000/`，未登录自动跳到login.html。已有账号reviewer01/02的初始密码保留在本机Git忽略文件 `work/initial-user-credentials.json`，不在页面或报告中公开。

## Web 界面（第一批）

### 第二批：结果页与报告页（已完成）

GET /reviews/{task_id}/items返回合同快照信息、审查时间、状态统计及结构化检查明细；与报告下载使用相同Bearer和资源权限，越权404。条款原文从该次任务快照读取，合同级缺失项clause_seq/clause_no为空，不伪造条号。排序为supported按高/中/低、insufficient、失败/待执行、不适用，最后no_issue_detected。

result.html?id=<task_id>按实际数据渲染风险、依据不足及失败卡片；法条默认折叠，可显示实际引用全文。未发现问题和不适用分开折叠。统计单位是检查项，不是唯一条款数；“0风险”只代表没有supported，依据不足仍显示，不能归到未发现问题。

report.html?id=<task_id>使用本地markdown.js的安全DOM渲染器，支持标题、列表、围栏代码块、粗体与空行分段。原始HTML及不支持的Markdown语法按文本展示，链接不执行；表格语法本轮保留为文本。下载按钮以Blob保存原始.md；返回链接回到同一任务结果页。所有错误可见，401统一跳登录。静态页面公开，业务数据仍需鉴权。

本轮108项回归通过；`python -m tests.results_browser_acceptance`在运行中的正式API上只读验证已有正式任务，无真实模型调用，包含统计与DB一致、三张风险卡、依据折叠、报告渲染、下载一致性、HTML注入不执行和资源权限。浏览器工具沿用work/browser-test-deps，应用不增加依赖。结果为work/results-browser-acceptance.json，截图为work/results-screenshots/。

- app/static/login.html：用户名、密码登录，错误账号和错误密码统一提示；JWT只写sessionStorage，不写localStorage或cookie。
- app/static/contract-list.html：展示可见合同的名称、立场、解析状态、创建时间；支持文件名/大小预览、点击上传、成功后刷新并将新合同置顶。选择文件本身不会发请求。
- app/static/contract-detail.html：展示条款数、解析状态、分页条款和最新任务；刷新后恢复任务进度，每两秒轮询，终态或离页停止。网络异常可见并提供手动重试。
- 所有页面有合同列表/退出导航，退出清理会话。业务请求通过api.js同源fetch附Bearer；401清理会话并跳登录。恢复浏览器缓存页面会重新校验数据，避免显示旧账号的列表。
- 解析失败的详情展示红色原因及重新上传入口。重新上传创建新合同，保留原记录；无效文件上传仍按已有后端规则直接返回错误，不生成新合同。
- 发起审查立即禁用按钮，服务端也保留活动任务去重。默认检查全部父条款、三项条款规则，并按关键词筛选；缺失规则仍需显式选择。可传max_clauses限制条款数。
- 任务完成后“查看结果”进入result.html，“查看报告”进入report.html；两页均通过同源Bearer fetch读取已有数据，不会触发模型调用。

静态文件通过StaticFiles挂到根路径，API路由优先。公开路径限这些静态页面/资源、登录、健康和文档；业务接口和资源权限不放开。新增GET /contracts/{id}/clauses?page=1&page_size=10，page>=1、page_size=1–100，返回分页元数据，越权依旧404。合同列表补充party_stance、created_at、clause_count、latest_task，并按创建时间倒序。

内网离线：没有React/Vue、npm、构建步骤、CDN或外部字体。为去除默认Swagger/ReDoc的CDN依赖，/docs与/redoc提供原生HTML离线接口参考；完整机器可读定义在公开/openapi.json，不依赖外网交互式文档资源。

API与worker仍是独立进程。仅启动Uvicorn时可以登录、上传、建任务，任务会排队。要执行真实模型审查，在项目目录的另一终端启动：

```powershell
.\.venv\Scripts\python.exe -m app.cli run-worker
```

浏览器验收使用独立测试库和SDK MockTransport worker，真实模型调用0次；不把模拟结果写入law.db，也不在生产worker中加入mock开关。自动验收脚本 `python -m tests.web_browser_acceptance` 使用本机Chrome。Playwright 1.48.0仅隔离安装在work/browser-test-deps，未加入应用requirements；无需npm或下载浏览器。该脚本给自身设置work/browser-temp，避免本机系统Temp的权限问题，不改Windows配置。

最新验证：98项后端回归通过；Chrome实际覆盖登录、上传、新记录置顶、详情分页、任务恢复、防重复、进度终态停止、部分失败/失败原因、解析失败入口、网络错误、账号隔离、过期token、关浏览器重开、离线/docs及窄屏布局。浏览器结果为work/web-browser-acceptance.json，截图为work/web-screenshots/。默认全审后，合同A的12条生成11个适用单元，隔离测试库中的11次SDK调用为合成mock数据，不是真实API消耗。

应用启动时生成项目根目录的 `law.db` 并创建缺失表；数据库路径基于项目位置，不随启动工作目录变化。可用环境变量 `DATABASE_URL` 覆盖，例如测试临时数据库。当前仅支持 SQLite。

就绪检查执行真实主数据库写事务及零行更新，最后回滚；不创建测试账号、不修改业务数据，也不增加第八张表。SQLite 每个连接启用外键、WAL、30 秒 busy_timeout 和 synchronous=NORMAL。

`create_all` 用于缺失表初始化。`app/migrations.py` 启动时幂等迁移旧库：允许预览日志的 review_id 为空，新增 contracts.preamble，扩展 review_items 状态约束以支持 not_applicable。迁移保留历史数据、外键和索引；本轮升级前备份见 `work/backups/law-before-applicability-*.db`。仍未引入完整 Alembic revision 目录，其他结构变更不能依靠 create_all 升级。

## 目录

```text
app/
  __init__.py
  main.py
  db.py
  models.py
  cli.py
  migrations.py
  api/__init__.py
  api/parse.py
  api/search.py
  api/judge.py
  api/reviews.py
  api/auth.py
  api/contracts.py
  api/admin.py
  static/
    index.html
    login.html
    contract-list.html
    contract-detail.html
    style.css
    api.js
    nav.js
    login.js
    contract-list.js
    contract-detail.js
  prompts/risk_judge.py
  prompts/missing_judge.py
  services/
    parser.py
    clause_splitter.py
    parse_quality.py
    preview.py
    chunker.py
    knowledge.py
    search.py
    llm.py
    validator.py
    risk_judge.py
    review_planner.py
    review_rules.py
    missing_judge.py
    contract_reparse.py
    review_tasks.py
    review_worker.py
    report.py
    report_evidence.py
    auth.py
    access.py
    contracts.py
tests/
  __init__.py
  schema_v6.sql
  test_schema.py
  test_health.py
  test_parse.py
  parse_helpers.py
  preview_acceptance.py
  test_knowledge_search.py
  knowledge_acceptance.py
  test_judge.py
  judge_acceptance.py
  test_review.py
  test_applicability.py
  applicability_acceptance.py
  review_worker_process.py
  review_acceptance.py
samples/
  contract_a.txt
  contract_b.txt
  judge/
    liability_uncapped.txt
    data_export.txt
  laws/
    civil_code_liability.txt
    civil_code_termination.txt
    README.md
requirements.txt
constraints-py39.txt
requirements.lock.txt
law.db
```

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试用临时数据库，自动启动/停止独立 Uvicorn 进程，不向 `law.db` 写入样例数据。

覆盖：v6 SQL 的字段、默认值、外键和索引；`UNIQUE(review_id,item_key)`；空 clause_id 下的幂等约束；同合同活动任务唯一索引；必要 CHECK；数据库只读及 HTTP 故障行为；启动生成恰好七张空表。

## 无状态文档预览

启动服务后，用浏览器 `/docs` 的 `POST /parse/preview` 上传，或执行：

```powershell
curl.exe -H "Authorization: Bearer <access_token>" -F "file=@samples/contract_a.txt" http://127.0.0.1:8000/parse/preview
```

仅支持 PDF、DOCX、UTF-8 TXT，文件上限为 20 MiB（20 × 1024 × 1024 字节）。预览需要 Bearer 认证，不保存原文件、不写业务表、不调用模型；multipart 临时上传文件随请求关闭。正式保存使用 POST /contracts。

格式检查：PDF 要有 `%PDF` 文件头且能够解析；DOCX 要是包含有效 Word 正文的 ZIP/OOXML，不接受普通 ZIP；TXT 严格 UTF-8，可含 BOM。DOCX 解压内容额外限制为 100 MiB。PDF 不做 OCR，DOCX 按正文段落读取，本轮未实现表格文本提取。

### 返回和错误

成功返回 filename、text_length、clause_count、warnings、preamble、clauses。每条有 seq、original_no、content、char_start、char_end、location。

- 偏移是解析后文本的 Unicode 字符索引，右端不包含；`content == text[char_start:char_end]`。
- 提取时将 CRLF/CR 统一成 LF，UTF-8 BOM 不计入正文。`page_map` 使用同一坐标系。
- PDF 位置为 `p1` 或跨页 `p1-p2`；DOCX/TXT 为 `para1` 或跨段落 `para1-para3`。TXT 以空行区分源段落，DOCX 保留实际段落序号。
- 切分先匹配“第 X 条 / 第 X 条之一”；不命中时使用次级编号；再不命中按空行回退。上级编号存在时，内部 1.1、（一）等不单独切条。
- 第一个编号之前的标题和主体信息单独返回 preamble，不进入 clauses；偏移仍对应包含前言的完整文本。无编号回退不推断前言，original_no 为 null。
- 正文少于 50 字符：422 / EMPTY_TEXT；替换字符占比大于 5%：422 / GARBLED；UTF-8 解码错误也返回 GARBLED。
- 不支持或损坏的格式：415 / UNSUPPORTED_FORMAT；超 20 MiB：413 / FILE_TOO_LARGE。
- 条款少于三条但正文质量合格：200，warnings 包含 TOO_FEW_CLAUSES。

错误体统一为 `{"error":{"code":"...","message":"..."}}`，不会将解析失败伪装成服务内部错误。

### 样本与人工核对

合同 A 使用十二个“第 X 条”编号，合同 B 使用十个混合编号并包含跨段落长条款及内部子项。均为虚构测试资料。

运行验收脚本会真实启动服务、上传两份样本及异常文件，打印条款数与前 3 条完整内容，再停止服务：

```powershell
.\.venv\Scripts\python.exe -m tests.preview_acceptance
```

默认记录写入 `work/parse-preview-acceptance.md`，完整 JSON 写入 `work/parse-preview/`。支持 `--report <文件路径>`。测试使用临时数据库，不改动项目 `law.db`。

## 法规入库与 BM25 检索

### 草稿导入与显式启用

在项目根目录执行；本轮验收已将这两份样本导入 `law.db` 并启用，按下列相同参数重复导入会跳过已存在的块，不清除其启用状态：

```powershell
.\.venv\Scripts\python.exe -m app.cli import-law --file samples/laws/civil_code_liability.txt --source-id civil-code-liability --name "民法典合同编：违约责任节选" --source-url "https://www.court.gov.cn/zixun/xiangqing/233181.html"
.\.venv\Scripts\python.exe -m app.cli import-law --file samples/laws/civil_code_termination.txt --source-id civil-code-termination --name "民法典合同编：解除与争议条款节选" --source-url "https://www.court.gov.cn/zixun/xiangqing/233181.html"
.\.venv\Scripts\python.exe -m app.cli enable-source --source-id civil-code-liability
.\.venv\Scripts\python.exe -m app.cli enable-source --source-id civil-code-termination
```

- 首次导入默认 `verified=0, enabled=0, superseded=0`；服务不会自动启用草稿。
- 导入支持 UTF-8 TXT/Markdown，`--source-version` 默认 1；可选 `--source-url`、`--publisher`、`--effective-date`。未给 URL 时，出处保存为本地文件 URI。
- 同一 source_id + source_version、内容与元数据一致时跳过，保持块 ID 和状态；不一致时返回 SOURCE_VERSION_CONFLICT，要求增加版本，不先删后插。
- import-law 整批事务提交，不会留下部分块。错误返回非零退出码及结构化错误。
- enable-source 是本地显式管理操作；默认将该来源所有块标为已核对/已启用，可用 `--source-version` 限定。不会清除 superseded，也不自动替代旧版本；旧版本替代流程不在本轮实现。
- 当前还没有登录用户，CLI 核对动作记录 verified_at，verified_by 保持空；不冒填不存在的用户 ID。测试启用不代表完成实际业务的人工核对。

### 切块与分词

- `chunk_law(text, filename, SourceInfo(...))` 优先按第 X 条/第 X 条之一切块，一整条保留为一块，内部款项不拆；法条前的标题和来源说明不混入正文。
- 无条号时按空行段落切，每块最多 500 个 Unicode 字符；有条号的完整条文不受该回退上限限制。
- chunk_index 从 0 开始；content_hash 为保存正文的 UTF-8 SHA-256。
- jieba 搜索模式、HMM=False，查询和文档共用规则；“解除合同”同时提供“解除”“合同”等检索词。
- 用正则预先保护整段法条编号，例如“第五百八十五条”和“第585条之一”各为一个完整词项。
- 词典保留“违约金”“过高”“过低”，去除少量助词和问句词，保留“不”“不得”等否定词；规则版本见 TOKENIZER_VERSION。

### 索引生命周期和接口

**每次服务启动全量构建索引。CLI 导入/启用后必须重启服务；不做增量、不在每个请求中重建。**

仅 `enabled=1 AND verified=1 AND superseded=0` 的块进入索引。请求时还检查当前允许返回的块 ID，停用或退役立即屏蔽；新增启用的块仍需重启才进入语料。运行期间 IDF 保持启动快照值。

`POST /search` 请求示例：

```json
{"query":"违约金过高怎么办","top_k":5}
```

成功返回 query 和 results；每项含 chunk_id、source_file、article_no、content、score、matched_terms。top_k 默认 5，允许 1–50；query 去除首尾空白后不可为空，最长 1000 字符。

score 为 `BM25Okapi.get_scores` 的原始分数，不归一化、不裁剪、不设相关性阈值。matched_terms 是查询分词与该块分词的交集。完全没有词项交集时返回空列表；有交集但分数为零或负数时仍保留。分数不能作为概率或跨查询置信度。

检索接口需要 Bearer 认证。Web第一批已将/docs、/redoc、/openapi.json和静态页面/资源加入公开白名单，业务接口仍全部校验Bearer。

### 知识库验收

38 项自动化测试包含旧功能回归、幂等入库、状态过滤、法条完整词、排名、零/负分保留，以及真实 HTTP 的启用后重启和即时停用过滤。

CLI + HTTP 演示脚本要求指定数据库的知识库为空，不会清空已有数据。默认使用 `work/knowledge-acceptance.db`；重复演示请指定新的测试库：

```powershell
.\.venv\Scripts\python.exe -m tests.knowledge_acceptance --database work/knowledge-demo-new.db
```

它会导入、重复导入、验证草稿不可检索、显式启用并重启服务，打印三个查询的排名、原始分数和 matched_terms。结果写入 `work/knowledge-search-acceptance.md` 与 `work/knowledge-search-results.json`；演示结束停止服务，保留已启用的测试记录。

## DeepSeek 单条款初审

### 配置与接口

客户端固定使用 `https://api.deepseek.com`、`deepseek-chat`、`temperature=0`，从当前进程环境读取 `DEEPSEEK_API_KEY`，没有默认 key，不自动切换提供方或模型。

可在本机设置进程环境变量后启动，或复制 `.env.example` 为不提交 Git 的 `.env`，填写 key。python-dotenv 只加载项目根目录 `.env`，已有进程环境变量优先：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app
```

`.gitignore` 已包含 `.env`。不要把真实 key 发到聊天或写入源码。未设置 key 时健康、解析和检索仍可用，有候选依据的判定返回 503 / MISSING_API_KEY，且不发出模型请求。

当前机器的系统代理导致 TLS 连接提前断开，未带 key 的 GET 对照确认直连正常。因此模型客户端默认 `trust_env=false`，仅此客户端直连，不改系统代理；确需工作正常的环境代理/自定义环境 CA 时可设置 `DEEPSEEK_TRUST_ENV=true`。

`POST /judge/preview`：

```json
{
  "clause_text":"甲方逾期付款，每日按合同价款的千分之一支付违约金，未约定累计上限。",
  "stance":"party_a",
  "rule_id":"liability"
}
```

流程固定为启动索引 Top-5 检索 → 独立提示词模板 → JSON 模式模型调用 → 结构校验 → DB 引用校验。liability 规则使用固定的“违约金、违约责任”查询补充词，不按目标法条加权。无候选时直接 insufficient，不浪费模型调用。

返回包括 check_status、risk_level、has_issue、description、reason、suggestion、support_note、evidence、usage、calls、request_id、candidate_ids、prompt_version。输入条款不超过 20000 字符；立场只接受 party_a/party_b。

- 模型只有候选 ID 可选；字段或严格 JSON 错误时，最多重新生成一次；再次失败返回 failed / unknown。
- 引用取候选 ID 与当前已核对、已启用、未退役 DB 块的交集；内容或条号发生变化也拒绝该引用。
- 引文、条号、来源版本、出处、内容哈希都由服务端填充，模型不能自写引文。
- 有问题但无有效引用降级 insufficient / unknown，并用待人工核查文案替换未经支持的确定结论。
- has_issue=false 且结构一致，归为 no_issue_detected / none，不因空引用强制报风险；它仅表示本次范围内未发现问题。
- supported 表示通过本轮结构和引用技术校验，不代表已经人工确认法律结论；support_note 仍是模型提供的支持关系说明。

提示词位于 `app/prompts/risk_judge.py`，当前版本为 risk-judge-v4-applicable-dispute-method。严格八字段输出包含applicable、has_issue、risk_level、description、reason、suggestion、evidence_ids、support_note。applicable为必填严格布尔值，旧输出不能自动默认适用。旧验收和任务保留原提示词版本；旧运行中任务不能悄悄换新提示词继续。没有 Function Calling，没有模型自主工具选择。Python入口为 `app.services.risk_judge.judge_clause(clause_text, stance, rule_id)`。

dispute_resolution统一只查争议解决方式：诉讼/仲裁、管辖法院/仲裁机构、适用法律及约定明确性。不检查争议条款存续效力。“发生争议由合同签订地人民法院管辖”为适用正例，“本合同终止后争议解决条款仍然有效”为不适用反例。一般的终止后存续表述不再与RULE_GUIDANCE矛盾。

对照样本：samples/contract_c_bad.txt仅修改A的第二条（验收后180日付全款）、第十条（删除10%上限并明确不设上限）、第十一条（新增甲方任意解除免责、乙方不得单方解除）；原有其他表述均保留，所以新增解约内容还与原双向解除约定并存。samples/contract_c_clean.txt只改标题和文件名，正文与A相同。两份统一用乙方立场，以免把有利于甲方的约定作为甲方风险。

一次性真实对照命令：`python -m tests.contrast_acceptance --live`。使用独立work/contrast-live.db及生产worker，复制现有已核对知识块，不向正式库混入测试结果。两份各执行一次、关键词适用项各11个，共72条审计记录。第一份预算14次，第二份预算为25减已用次数；含网络/JSON重试合计不超过25。已有状态或测试库拒绝自动重跑；不根据结果修改提示词再付费凑数。输出为work/contrast-live-results.json、work/contrast-comparison.md及两份报告。

先判断内容是否涉及规则，不能把“候选依据不足”等同于不适用。违约金、赔偿、严重违反义务后的催告解除都属于liability；仅讲协商/法院的争议条款不属于liability。applicable=false要求has_issue=false、risk_level=none，服务端返回not_applicable且不要求引用；applicable=true、has_issue=false返回no_issue_detected；适用且有问题时才按有效引用返回supported或insufficient。适用但没发现具体问题无需硬凑引用。没有候选或调用失败、尚未获得模型适用判断时applicable可为空，不能伪称模型判断不适用。

### 引用校验诊断（preview与worker共用）

两条路径均调用RiskJudge.judge_clause和同一个validate_evidence。区别是preview使用启动时的知识索引，worker从任务知识快照重建索引；引用均回查当前DB。相同条款、立场、rule_id及候选/模型输出在两条路径下应产生相同校验结果。

每次打印一行event=evidence_validation，包含path、request_id、review_id、item_id、input_digest、candidate_ids、model_evidence_ids、validation_trace及最终状态；不打印key或完整合同/提示词。input_digest是条款原文+立场+规则的SHA-256，便于确认是否确为同输入。worker输出在本机work/worker.log，API输出在work/web-server.log。

校验阶段：model_output（模型选择哪些ID）→candidate_membership（不在候选集则NOT_IN_CANDIDATES）→database_eligibility（CHUNK_NOT_FOUND/SOURCE_DISABLED/SOURCE_UNVERIFIED/SOURCE_SUPERSEDED）→snapshot_match（SNAPSHOT_MISMATCH，列出content/article_no/source_file的差异字段）→final。模型本就返回空ID时不伪造拒绝步骤，明确标记MODEL_RETURNED_NO_EVIDENCE_IDS；全部被拒才是ALL_EVIDENCE_REJECTED。有效ID不会为了得到风险等级而被自动填充，也不会放宽校验。

结果及machine_result增加model_evidence_ids、validation_trace、input_digest、model_output。model_output是未验证的原始模型结论，仅供排查；报告只用验证/降级后的结果，不将它作为已确认意见。JSON失败、未调用模型等情况不会伪造已解析的ID。

历史任务4388fa77baf1403b9f9f922ceca3e37f的三项实际是第二条/payment、第三条/liability、第三条/payment，均保存了5个候选但discarded_evidence_ids=[]、evidence=[]。按旧校验器“每个请求ID都接受或丢弃”的不变量，可确定模型当时选择的ID列表为空；不代表校验器拒绝第585条。历史原始模型原因未保存，不能还原完整原始输出；历史preview请求体也未保存，不能证明当时两次条款、立场、规则完全相同。旧结果不回填伪造日志。

离线诊断：`python -m tests.validation_diagnostics --task <任务ID>`，打印历史ID行为回放（明确标记推断来源）及SDK mock的同输入有效585/空ID/假ID对照，不调用真实模型。输出在work/validation-diagnostics.json和对应日志。新任务默认全审，旧5条任务仍显示实际5/12覆盖范围；重新发起才创建新的全审快照。

### 重试、日志与费用

- openai SDK 固定 1.109.1，SDK 自动重试关闭。每次尝试 HTTP 超时 60 秒。
- 网络错误/超时/5xx 最多重试两次，退避 1、2 秒；认证错误及所有 4xx（含 429）直接抛给上层，不重试。
- JSON 格式修复最多一次，和网络重试分开计数；最坏单条预览会有六次网络尝试。
- 每次网络尝试都写 llm_logs，并打印 token 与耗时元数据；不记录 key、完整提示词或合同正文。
- 请求固定 model=deepseek-chat；调用元数据同时记录 requested_model 和响应 model，数据库 model 保存响应名。实际验收服务端回报 deepseek-flash，客户端未自动替换请求模型。
- 预览没有正式合同/审查任务，review_id/item_id 为空，通过 request_id 和 attempt_no 关联；不伪造用户、合同或任务，不写 review_items。
- API 未返回 token 的失败尝试保存 NULL；usage_complete=false 时总计可能未知，known_* 仅为已知部分，不冒充零消耗。
- 模型调用成功但日志落库失败时返回 failed，不重发付费请求。
- 验收可设置 `LLM_TEST_CALL_LIMIT=20`，全进程共享预算，在每次外发前计数，包含网络与格式重试；常规运行不设置该变量则不受这个验收预算限制。

本轮对原 `law.db` 迁移前备份保存在 `work/backups/law-before-llm-*.db`，七张表和 23 个知识块保持不变。

### 验收脚本

合同 A 的原违约责任已经约定 10% 上限。本轮另建 `samples/judge/liability_uncapped.txt` 去上限变体，原 A 不修改。不能为满足验收而让模型声称原文不存在的缺陷。

默认脚本使用 ASGI 请求和 SDK MockTransport，无真实模型费用：

```powershell
.\.venv\Scripts\python.exe -m tests.judge_acceptance
```

环境变量就绪后显式运行真实验收：

```powershell
.\.venv\Scripts\python.exe -m tests.judge_acceptance --live
```

真实部分只请求无上限变体和数据出境两项，累计验收外发预算上限为 20；假引用用例使用本地 mock。脚本保留 work/judge-acceptance-results.json 中的真实调用历史，重跑时扣除已经发生的尝试，不清零失败次数。每项含网络和格式重试最多六次，两项计划最坏十二次。不会为了凑出预期结果反复调用模型。

脚本的 mock token 为合成数据，不能作为真实 API 消耗或模型效果证明。输出位于 `work/judge-acceptance.md` 与 `work/judge-acceptance-results.json`；实际验收结果以 live 状态和真实调用记录为准。

用 `python -m tests.judge_acceptance --render-only` 可仅从已有 JSON 重建报告，不重新调用模型。本轮真实验收已通过，累计五次尝试（含三次代理连接失败、两次成功）；无需再次付费复测。

## 受限合同任务与 worker

### 当前限制

- 默认全部父条款（max_clauses=null或省略），不受200条的默认截断；可显式选择前1–200条，子块不单独建检查项。
- 条款规则为 liability、payment、dispute_resolution，关键词定义位于 `app/services/review_rules.py`。按不同关键词命中数判断 min_hits，默认 1；未达到条件不进入可执行计划，另存 not_applicable 审计记录，不调用模型、不算失败。
- 条款 item_key 为 `clause:<clause_id>:<rule_id>`。显式选择 missing_rules 后，每个合同级规则只生成一个 `contract:<rule_id>` 单元，clause_id 为空。
- 新任务预算按计划计算：max(15, 条款级单元数×6 + 合同级单元数)，覆盖现有网络/格式重试上限，避免全审被旧测试用十五次上限截断；重启不清零。合同级检查禁用网络和JSON重试，每个单元只尝试一次。旧任务保留原快照预算。

合同级规则为 dispute_resolution（争议解决）、acceptance（验收标准）、confidentiality（保密）、payment_default（逾期付款责任）、delivery_default（交付违约责任）。使用全合同条号和每条前 80 字，不受 max_clauses 限制；不向模型发送全文。原三个规则输出区分 present、missing、incomplete、uncertain，并保留缺失事项及重要性。服务端从快照填充合同摘要依据，和法律法规引用分开标识；若完整条款已有实质约定，拦截摘要截断造成的确定缺失判断。提示词单独位于 `app/prompts/missing_judge.py`。

新增两个规则要求模型严格输出 `{has_gap: bool, gap_description: str, impact: str, suggestion: str}`；字段缺失、空泛影响或非严格布尔值按失败处理，不重试。has_gap=false 对应 no_issue_detected，说明中要写清跨条款覆盖依据；has_gap=true 保存具体缺口、影响及补充建议。报告独立“合同级缺失”一节展示这些字段，合同级单元不伪造条号。

摘要有盲区时不得强报缺失：若相关责任表述出现在80字之后，服务端仅用完整正文拦截误报，降为 insufficient / unknown，has_gap=null，保留 model_has_gap 供审计；此时不是确认无缺失，也不是确认有缺失。原合同 A 第十条的付款责任主要表述位于前80字末尾，模型已识别跨条款覆盖并返回 has_gap=false。验收原文不得为了预期 has_gap=true 删除已有责任约定。

仅检查新增两项：`python -m app.cli review --contract <合同ID> --rules "" --missing-rules payment_default,delivery_default`。HTTP 可传 rules=[]，missing_rules 最多五项；每个缺失规则仅一次外发尝试，无网络或JSON修复重试。显式真实验收命令为 `python -m tests.default_gap_acceptance --live`，本轮验收任务持久调用上限3；已有记录拒绝重复付费。原始结果和报告位于 `work/default-gap-live-results.json`、`work/default-gap-live-report.md`。

### 创建和运行

合同必须已经存在、解析成功且有父条款；有解析警告时须先确认。POST /contracts 可上传保存合同和切分条款，owner_id 由服务端取当前登录用户。历史验收的禁用测试所有者保留，不在应用启动时自动造账号。

以下命令会创建新任务，但不立即调用模型；重复提交 queued/running 合同返回已有 ID。完成后再次提交会生成新的审查版本：

```powershell
.\.venv\Scripts\python.exe -m app.cli review --contract <合同ID> --max-clauses 5 --rules liability,payment
.\.venv\Scripts\python.exe -m app.cli review --contract <合同ID> --max-clauses 12 --rules liability --missing-rules dispute_resolution,acceptance,confidentiality
.\.venv\Scripts\python.exe -m app.cli run-worker
```

worker 默认持续轮询；`--once` 最多处理一个任务后退出，`--unit-delay 2` 可设置单元提交后的限速间隔。只有 worker 执行任务时才可能产生模型费用。

接口：

```text
POST /contracts/{id}/review   JSON: {"max_clauses":12,"rules":["liability"],"missing_rules":["dispute_resolution","acceptance","confidentiality"]}，返回 202 {task_id,deduplicated}
GET  /tasks/{id}              状态、阶段、进度及模型用量
GET  /reviews/{id}/report     UTF-8 Markdown 草稿（review ID 即 task ID）
```

不传请求体时默认全部父条款、上述三个条款规则，missing_rules默认空；显式max_clauses=5仍只检查前五条。CLI不传--max-clauses也为全部。只做缺失检查可传rules=[]。合同、任务和报告接口均校验Bearer和合同可见范围。默认变更不重写已有任务，旧任务仍保留原覆盖范围；详情会显示实际已选/总条款数。

已有合同重新切分：`python -m app.cli reparse-contract --contract <合同ID>`。命令创建新合同版本，raw_text 保留完整前言，preamble 单独保存，clauses 从首个编号开始。旧合同、条款、审查输入快照不覆盖，后续 review 使用命令返回的新合同 ID。

### 一致性与恢复

- 创建任务使用 SQLite 短写事务与活动任务唯一索引去重，保存合同信息、选中条款、规则、知识全文及版本配置快照。
- worker 对数据库路径对应的 `.worker.lock` 获取 OS 排他锁；第二个 worker 拒绝启动，不重置任务。进程被 kill 后锁自动释放。
- 取得锁后才把遗留 running 重置为 queued，再按创建时间领取最早任务；领取完成即提交。
- 从计划幂等预建 pending 检查项，每项的模型调用都在数据库事务外进行。
- 每一项的 machine_result 和执行状态先用短事务提交，进度随后用独立短事务更新。重启跳过 done 项，继续 pending 项，旧结果不覆盖。
- 单元失败保存 failed 结果并继续；有单元失败则任务 partial_failed；全成功则 completed。快照或数据库等任务级故障标 failed 并保留已存检查点。
- 模型请求前写入 CALL_IN_PROGRESS 日志，得到返回后更新同一行；日志绑定真实 review_id 和 item_id，不再使用预览空关联。
- 重启将中断中的预记标为 INTERRUPTED_UNKNOWN_USAGE，仍占用预算。若远端已处理但本地尚未提交结果，pending 项可能重发；不承诺外部 API 恰好一次，但已提交的 done 项不会再调用。
- prompt/tokenizer/model 配置版本不符时停止该旧任务，不悄悄换新配置继续；需创建新审查版本。

### 进度与报告

- done_items：成功完成执行的项（包括 supported、insufficient、no_issue_detected）。
- failed_items：执行失败项；not_applicable_items包含关键词初筛和模型判断两种不适用，均不计入done_items或failed_items。新增keyword_not_applicable_items、model_not_applicable_items区分来源；executed_items=done_items+failed_items+model_not_applicable_items，页面用它显示已执行进度。processed_items=done_items+failed_items+全部不适用项，避免重复计数；pending_items单独返回。planned_model_items为计划执行单元数。报告统计“不适用”与“依据不足”分列，并保留模型不适用理由。
- progress 按已处理单元计算，1.0 表示计划执行结束，不代表合同通过审查；失败数和状态始终可见。
- metrics.model_calls 为该任务日志尝试数；token 未知保留 NULL，known_* 为已知部分。模型累计耗时与任务总耗时分开，后者包含排队、暂停和恢复。
- 报告从已保存的机器结果及输入快照生成，含条款原文、统计、意见、引用、独立缺失项节、不适用清单和未发现问题清单；明确标注实际选择条款范围，不宣称全合同已审。
- 报告是未人工确认的草稿。本轮不做接受/驳回、确认冻结等人工复核工作流。

### 前一轮 worker 验收（历史记录）

`tests/review_worker_process.py` 只在测试中使用 SDK MockTransport，生产 run-worker 无 mock 环境开关。自动化覆盖去重、跨进程 kill/restart、第二 worker 排斥、单项失败隔离、持久预算及快照。

一次性完整演示：

```powershell
.\.venv\Scripts\python.exe -m tests.review_acceptance          # 本地 mock
.\.venv\Scripts\python.exe -m tests.review_acceptance --live   # 显式真实任务，最多二十次尝试
```

已有对应验收记录时脚本拒绝自动重跑，不清空任务或重新付费。真实演示在第二项提交后、下一次调用前 kill worker，然后重启；比较两项已完成单元的日志次数。

本轮真实验收已完成：task_id 为 `114899a574ca4a4a8d66307a847dd4c7`，contract_id 为 `2e576107b4494c3e98d8a6f90b5e4863`。五项均有 machine_result；模型调用五次，输入 6500、输出 1054、总计 7554 token；已完成项调用数在重启前后均为二。查看现有任务/报告无需再 POST 创建任务。

原始验收状态及逐次用量在 `work/review-acceptance-live-results.json`；报告在 `work/review-live-report.md`。真实验收前数据备份为 `work/backups/law-before-worker.db`。验收结束后服务和 worker 均停止。

### 规则适用、缺失项与前言验收

运行 `python -m tests.applicability_acceptance` 使用 SDK MockTransport；加 `--live` 才产生真实模型费用。已有对应结果时拒绝自动重建或重复付费。结果为 `work/applicability-{mock,live}-results.json`，报告为同名前缀的 `-report.md`。常规 unittest 不外发模型请求。

最新 applicability-v2 扩展 liability 关键词，覆盖违反、不履行、未履行、未按期、未按时、延迟、迟延、逾期、超时、赔偿、损失，同时保留违约、责任、罚。合同 A 十二条中第三、十、十一条适用，九条不适用；条款级预计调用数从旧关键词的一次增为三次，相比未过滤的十二次仍减少九次。再加三个合同级检查，共六个可执行单元。旧验收文件保留 v1 的实际四次调用记录，不用新规则改写历史结果；重新审查需新建任务。

样本已有验收和保密约定，不能为了预期缺失结论修改原样本或编造缺陷；第十二条的争议约定较笼统，是否具体完整以实际输出和人工核查为准。

## 登录、账号与权限

新增 OA MCP 与 AI 助手入口 `/workspace/agent`（admin/reviewer）。模拟OA选择独立本地Python3.13运行在9000端口，主系统保持Python3.9；启动命令见 [mock-oa/README.md](mock-oa/README.md)，架构、限制、真实run与成本见 [docs/mcp-agent-notes.md](docs/mcp-agent-notes.md)。先启动OA再打开AI助手，容器通过MCP_OA_URL连接宿主OA。

Agent已改用动态步数：未知规模10步，首次列表得到N份后限`min(2+3*N,40)`；每轮记录实际上限，最终返回步数、完成合同数和停止原因。当前说明见 [docs/dynamic-rounds-notes.md](docs/dynamic-rounds-notes.md)。

初始化账号（密码在终端隐藏输入并确认，不接受 --password 参数，不使用用户名作默认密码）：

```powershell
.\.venv\Scripts\python.exe -m app.cli create-user --username X --role reviewer
.\.venv\Scripts\python.exe -m app.cli seed-users
```

seed-users 创建 admin/admin、reviewer01/reviewer、reviewer02/reviewer（格式为用户名/角色），已有账号跳过，不覆盖密码。密码按 bcrypt 保存，UTF-8 长度10–72字节。本机本轮已经初始化三个账号，随机初始密码仅保存在 Git 忽略的 `work/initial-user-credentials.json`，不写入报告或聊天；历史禁用测试账号不启用。不要运行脚本覆盖已有密码。

- POST /auth/login：JSON `{username,password}` → `{access_token,token_type,role,expires_in}`，expires_in=86400。账号不存在、密码错、账号禁用统一401“账号或密码错误”。
- GET /auth/me：Bearer token → 当前 user_id、username、role，不返回密码哈希。
- JWT 固定HS256，含 user_id、auth_version、iat、exp；每次请求从DB复核 active、auth_version，并以DB角色为准。停用账号或增加 auth_version 立即使旧token失效。
- JWT_SECRET_KEY 可在环境/.env配置（至少32字节）；未设置时持久保存随机密钥至 `work/jwt-secret.key`，进程重启后token仍可验证。该文件和初始密码文件均在被忽略的work目录，不可提交或公开。
- 业务接口除 /auth/login、/health/* 外均需 `Authorization: Bearer <access_token>`；未认证、无效或过期token返回401，含WWW-Authenticate头。静态页面与资源以及/docs、/redoc、/openapi.json为公开白名单，页面由sessionStorage认证状态和业务接口权限控制。未知路径不在白名单中。
- 三角色：admin / reviewer 可访问全部合同和工单；requester 仅可访问自己创建的合同和自己提交的工单。合同详情、建任务、任务状态、结果与报告采用同一合同可见性；越权ID和不存在ID统一404。
- 工单确认仅 admin / reviewer，requester 返回403；关闭工单还须满足既有状态机（审查中、待人工确认不可关闭）。会话仅创建者本人或 admin 可读写。
- GET /admin/users、GET /admin/knowledge 和 PATCH /admin/contracts/{id}/reviewer 仅管理员，其他角色403，不返回密码哈希。复核人字段保留，但不再限制法务的全局可见性。
- 默认登录后进入 `/workspace` 概览；侧边栏和路由按角色渲染，前端隐藏仅是体验，服务端逐接口授权。`GET /dashboard/summary` 的统计按角色过滤；高风险总数汇总可见工单的 high_risk_count。详见 `docs/rbac-notes.md`。
- 创建提交人：`python -m app.cli create-user --username requester01 --role requester`，密码由终端交互输入。已有账号不会自动改角色或重置密码。

合同接口：GET /contracts 返回可见合同列表；POST /contracts 使用 multipart file，可附 title、stance（party_a/party_b，默认party_a）；GET /contracts/{id} 返回合同及条款。上传限制同预览，完整raw_text、独立preamble和条款同时保存，原文件保存在work/uploads（可配置CONTRACT_UPLOAD_DIR）。客户端不能指定owner_id，不会因上传自动调用模型。

报告依据现在每项最多五条，每条一行“来源/条号 —— 本项覆盖关系说明”。合同级从快照挑选相关条款，不再贴出完整候选摘要；delivery_default选择验收、质保、统一违约条款。说明区分已存在的整改/响应义务和还需核对的赔偿责任，不把它们混同为完全未约定。条款级只使用校验后的实际evidence，候选列表不进入报告。该渲染也适用于旧草稿，不修改历史机器结果或已确认的冻结报告。

自动化：`python -m unittest discover -v`，均不调用真实模型。实际账号与HTTP验收为 `python -m tests.auth_acceptance`（本机已运行，已有记录拒绝重复初始化）；它禁用模型key并设置零调用预算，不启动worker，核对llm_logs数量不变。结果位于work/auth-acceptance-results.json，精简报告位于work/auth-compact-report.md。

## 本机安装说明

初始 pip 21.1.3 遇到系统代理错误，在线索引处理也较慢。最终在项目内升级为 pip 25.3，下载匹配 Python 3.9/Windows 的固定安装包，核对 PyPI 元数据中的 SHA-256 后，从 `work/wheelhouse` 安装。未修改系统代理或系统 Python。

本机默认 pip 缓存目录出现拒绝访问，初次安装改用项目 `work/pip-cache`。保留的安装包支持离线安装：

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links work/wheelhouse setuptools==75.8.2 wheel==0.45.1
.\.venv\Scripts\python.exe -m pip install --cache-dir work/pip-cache --no-index --find-links work/wheelhouse --no-build-isolation -r requirements.txt
```

安装后，普通的 `python -m pip install -r requirements.txt` 已再次执行成功。`work/` 和 `.venv/` 均不提交 Git。

</details>

### Agent 运行历史

管理员和法务可在「AI 助手 → 历史记录」按状态筛选并查看原时间线。接口 `GET /agent/runs` 支持 `limit`、`status` 和 `created_by`，详情接口为 `GET /agent/runs/{id}`。

升级后可在 API 容器内导入旧记录。迁移脚本支持重跑，原 JSON 保留在 `/data/agent-runs/`。

```powershell
docker compose --env-file .env.docker exec -T api python scripts/migrate_agent_runs.py
```

### Agent 法规问答与待明确立场

Agent 合并远端 OA 三个 MCP 工具和本地 review_contract/search_law，无需额外进程。问“违约金上限是多少”走法规检索；要求审合同时优先使用用户明确立场、OA stance、组织匹配。都不明确则创建 needs_stance 工单，法务在工单详情选甲方/乙方后自动审查，批量其他合同继续执行。未知立场不默认甲方，不写回虚构结果。

mock OA 默认三份样本立场为甲/乙/甲；`POST http://localhost:9000/api/reset?clear_stance=C002` 是测试待办的显式开关，默认数据不因此改变。完成的法务待办只有来源、原文、立场、规则和知识库均一致时才复用原任务，结果注明 reused=true 和原 task_id。

### SQLite 并发与数据目录

本次并发问题的根因判断为“锁等待超时缺失 + 事务持锁时间过长”（基于现象推断，未做对照实验）。正式库原本已是 WAL，WAL 不是本次修复点；真正生效的是 busy_timeout 从 5 秒增至 30 秒及事务边界收紧。“超时缺失”指缺少适合当前负载的等待配置，并非原先完全未设置。恢复期间健康接口和 `/tickets` 各 142 次检测全 200，是修复后可用性的有效证据，不代表已通过对照实验证明根因。详见 `docs/db-concurrency-notes.md`。

数据目录必须可写：WAL 模式需要在 law.db 同目录创建 law.db-wal 和 law.db-shm。不要只授予数据库文件写权限；不要在运行时删除 WAL/SHM 文件。API 和 worker 必须访问同一文件系统上的同一数据库。Windows Docker Desktop 的共享目录若出现 SQLITE_CANTOPEN，需同时检查挂载、文件权限和容器日志，不能将其直接归因于锁等待。

并发自验（不调用模型）：`docker compose --env-file .env.docker exec -T api python scripts/db_concurrency_check.py --database /data/law.db --base-url http://nginx --seconds 30 --output /data/concurrency-simulation.json`。应在数据库所属的容器内执行，使用容器路径 `/data/law.db`，避免宿主机进程与 Linux 容器同时操作 WAL。恢复任务时将 `--seconds 30` 换成 `--task-id <id>`；`--token-file` 可额外检测已认证的 `/tickets`。任一非 200、超时、断点重复调用或 180 秒无进展均视为失败。报告默认写入 work/db-concurrency-report.json。
