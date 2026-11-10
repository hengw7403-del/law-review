# 三角色权限与界面验收

## 角色口径

|能力|admin|reviewer|requester|
|---|---|---|---|
|上传合同|允许|允许|允许|
|合同、条款、审查任务、结果、报告|全部|全部|自己的合同|
|工单列表与详情|全部|全部|自己提交|
|发起合同审查|可见合同|可见合同|可见合同|
|确认needs_human|允许|允许|403|
|关闭工单|全部|全部|自己的工单|
|会话读写|全部|仅创建者|仅创建者|
|概览|全量|全量|按本人隔离|
|用户与知识库管理入口|允许|403|403|
|指定合同复核人|允许|403|403|

可见性不满足统一404，未认证401；确认接口先判断角色，requester对任何ID均403。关闭还要满足已有状态机：reviewing和needs_human返回409，不能通过requester归档绕过人工确认。

## 后端与迁移

- access.py集中定义contract_visibility和ticket_visibility，并注释“前端隐藏只是体验，后端必须再拦一次”。旧visibility名称保留兼容，执行的是新合同规则。
- 合同列表、详情、条款、任务创建、任务轮询、结果与报告使用统一合同可见性；工单各入口使用统一工单可见性。复核人字段不再赋予或限制requester权限。
- reviewer可看全部工单，但不会因此获得他人会话消息。admin代发他人会话时，候选合同和工单requester仍属于会话创建者。
- users.role CHECK扩展为admin/reviewer/requester；父表在显式事务内重建，保留主键、密码哈希、组织、auth_version及子表引用。迁移幂等，失败自动回滚并恢复外键。旧账号不改角色、不重置密码。
- create-user支持requester，密码继续交互输入；没有新增含密码的HTTP管理接口。

## 统计定义

`GET /dashboard/summary`对admin/reviewer统计全部，对requester统计自己的合同及自己提交的工单。high_risk_total汇总可见工单high_risk_count，包含已人工确认工单的历史high，不含没有工单的普通审查，也不将merged副本重复相加。

today_done按Asia/Shanghai自然日计算，统计当前done且完成更新时间在当天的工单，不把归档日期当完成日期。主页的待确认/提交/活动列表最多20项，总数计数不截断。前端不遍历全部审查结果计算风险。

## 前端

- AppLayout.vue为当前布局；AppShell保留薄兼容包装。默认登录进入/workspace。
- 侧边栏按roles过滤：admin为概览/工单/合同/知识库/用户，reviewer为概览/工单/合同，requester为概览/我的提交/合同。
- 每个路由具有meta.roles；无权限跳概览，未登录跳登录并保留原目标。后端数据权限独立检查。
- 三套Dashboard：管理员四统计与活动，法务待处理与今天完成，提交人自己的工单进度和结果入口。
- requester不渲染确认按钮或确认备注；reviewer可确认他人提交的工单。导入说明、用户入口与相关路由仅admin。
- 知识库页面通过admin接口展示来源，导入/核对/启用沿用CLI；用户页展示账号、角色、组织和启用状态，未额外实现账号增删改页面。

## 自动化与浏览器验证

最终完整回归156项全部通过（原有148项＋新增8项），耗时126.989秒。`npm run build`成功，未新增依赖。

`tests/test_rbac_matrix.py`使用临时数据库及真实JWT验证每角色×接口，包括创建、读写、404/403/401、待接单不泄露、统计口径、admin代处理会话归属、迁移幂等与迁移失败回滚。对话权限测试只使用本地mock，不发模型请求。

`tests/rbac_browser_acceptance.py`使用隔离真实API、真实登录、测试库和禁用模型配置，验证三角色菜单、首页、路由、375px布局、知识库/用户页面；两名reviewer实际确认他人needs_human，requester确认403。确认操作只影响明确的测试夹具，没有确认正式库中的高风险结论。

旧权限断言按新规范更新：reviewer02原本应404的跨合同访问现在应200，隔离断言转由requester覆盖。完整回归还发现旧断点测试依赖4秒sleep的竞态；改为测试进程在已提交检查点暂停，保留原有日志次数与不重复调用断言，生产worker未改变。

## 正式部署核对

- 原库备份：`/data/before-three-role-rbac-20261110.db`。迁移重复执行后，原2个用户的ID/字段/密码哈希摘要不变；原7合同、11工单、13任务、1162结果、418调用日志数量不变。
- integrity_check=ok，foreign_key_check=0。
- 补建reviewer02和requester01，原账号未重置。新requester密码位于本机Git忽略的work/initial-user-credentials.json，不在报告展示。
- 正式reviewer01、reviewer02均真实登录成功：可见7合同和11工单，待确认4件；管理接口403。正式requester01真实登录成功：自己的统计均为0，访问他人工单404、确认403；三人的正式界面亦只读验证通过。
- 私有历史凭据文件中的admin密码与正式账号不匹配，登录返回401；没有重置该账号。管理员登录、页面和授权在隔离测试库中验证，不能声称正式库admin密码登录已验收。
- 本轮不触发审查、不运行真实对话、不调用模型；正式库llm_logs从418条到418条，增量0。无活动任务后恢复worker空闲运行。未涉及MCP。

截图：work/rbac-ui/；接口审计：work/rbac-formal-api.json；迁移审计：work/rbac-migration-audit.json；完整回归：work/rbac-full-tests.log。
