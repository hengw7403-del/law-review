# 全规则工单、首次法规导入与结果提示：本轮验收

## 规则与进度

工单触发现在显式从review_rules.CLAUSE_RULES和MISSING_RULES取全部定义，不使用手写规则ID列表。输入快照rules为10项、missing_rules为5项（非空），条款120单元加合同5单元，总计125。规则适用过滤产生76条not_applicable审计记录，实际计划为44条款检查+5合同缺失检查=49次。

工单关联任务的done_items改为已处理记录数，包括不适用与合并；successful_items保留有效执行结果的单独数量，failed_items另列。get_task另报clause_items和contract_items。processed_items直接取落库执行完成记录数，避免新done口径与不适用数重复相加；最终125/125、progress=1。普通非工单审查的done_items原口径不变，普通/contracts/{id}/review默认规则与行为不变。

结果页把全部5项scope=contract放进“合同级缺失”独立区域，包括已有约定的no_issue结果，不混入条款级卡片和折叠列表。全局统计仍覆盖125项。

## 首次导入

容器initialize先建表及检查可读写，再执行bootstrap_laws，最后启动API及其索引。使用BEGIN IMMEDIATE原子判断knowledge_chunks为空，遍历samples/laws/*.txt，复用正常import_law和enable_source，source_id=文件stem。每文件savepoint，坏文件记录WARNING并回滚该文件，其他文件继续；整体导入失败也不阻断API启动。非空则不导入、不覆盖、不重新启用已停用条文。worker依赖API健康，启动索引能读到已导入资料。

实际空库日志：`已自动导入 26 条法规（4 个来源文件）；已启用`。

|source_id|法条块数|enabled/verified|
|---|---:|---|
|civil_code_liability|15|1/1|
|civil_code_termination|8|1/1|
|civil_procedure_agreed_jurisdiction|1|1/1|
|civil_procedure_jurisdiction_interpretation|2|1/1|

重启后26条ID集合的哈希保持ad5cfd0b62569d2c8dc9e6de1a7d93c9d57e8c52c6e1f91b30e5b0d8e98c5267不变。数据库中草稿等非空情况不会被自动覆盖。启动日志、前后数量及哈希位于work/docker-firstboot-knowledge.json、docker-firstboot-after-restart.json和docker-firstboot-api.log。

## 黄色提示及界面

结果/任务API提供all_clauses_insufficient：只在终态且确有条款级有效检查、全部此类检查为insufficient时为真；不适用、合并不当作已检查风险，合同级缺失不混入该条件。部分不足、有未完成项或没有适用检查时不提示。结果页和工单详情复用KnowledgeWarning，黄色提示并链接/workspace/knowledge-help说明导入和重启索引。

统一formatTime显式使用Asia/Shanghai及h23，输出YYYY-MM-DD HH:mm，旧数据库无时区时间按UTC解析；工单号#前8位，完整ID继续用于路由。空focus_points显示全部条款，非空列出具体项。工单名称优先contracts.title；若仍是默认文件名则去扩展名，生成“合同标题 · 条款审查”，没有凭空把文件内容当成用户标题。列表、详情、审查时间、人工确认时间均已统一，不改合同原文或报告内的原始数据。

## Docker真实验收位置

为了不销毁已有用户数据，首次空库验证使用独立Compose项目law-review-firstboot-20261110，端口18080，新建空目录work/docker-firstboot-20261110/data，等价于新数据卷的首次启动。它使用正常Dockerfile、API和worker，没有SDK mock。三容器healthy；首次导入、重启幂等、真实search结果、真实全量模型调用均在这个目录中可复核。

本轮真实审查库是**work/docker-firstboot-20261110/data/law.db**，不是宿主项目根law.db，也不是原data/law.db。已生成冻结备份law-after-full-ticket.db，便于离线检查。复核本轮50条调用请查看该验收卷；宿主旧库保留原记录。

原80端口部署的数据保留；其knowledge_chunks原为0，更新镜像后同样自动导入26条。更新前已从宿主SQLite backup保存work/backups/docker-data-before-fullrules-20261110-123151.db；原worker unhealthy及挂载打开问题在重建容器后恢复，三个服务均healthy。宿主8000 API也已重载新代码。没有删除原数据或重写旧审查快照；历史36项任务保留历史范围，需新建工单才能使用125项规则。

## 全量真实审查结果

- 合同：57c8d04ccc754d42a94bee808c7991d4，技术服务合同，party_b。
- 工单：a8e78f16fc324fc38c85745f9b48adca。
- 任务：095f999695b04519873e6b3f1d7062a8，completed。
- total_items=125，clause_items=120，contract_items=5，数据库存储done_items=125，processed_items=125，progress=1。
- needs_human，1处高风险。全局：high 1、medium 4、insufficient 1、no_issue 25、not_applicable 93、merged 1、failed 0。

5项缺失检查都各有1条purpose=missing_judge的实际调用日志，含真实usage及内容，不是仅增加计划：

|规则|校验后状态|
|---|---|
|payment_default|no_issue_detected|
|delivery_default|supported|
|dispute_resolution_gap|supported|
|acceptance_gap|no_issue_detected|
|confidentiality_gap|no_issue_detected|

其中gap_description、impact及usage的完整原始输出在work/docker-firstboot-20261110/missing-audit.jsonl，全部结果及会话响应在acceptance.json，10+5的实际快照规则在work/full-ticket-snapshot.json。

成本：建单1次、514 token；条款44次、120044 token；缺失5次、8337 token。总计**50次真实调用、128895 token**。worker启动前先暂停并设置该任务49次持久化预算，再启动正常worker；没有更换模型客户端或注入mock。所有50条llm_logs都在上述Docker数据目录。达到本轮上限后未再调用模型。

## 验证

- npm run build成功。
- 完整135项单元/回归通过，含原132项及全规则、bootstrap幂等/失败不阻断、全部不足/部分不足边界测试。
- node tests/frontend_format.mjs通过上海时间、跨日、短号及关注点格式断言。
- tests.full_ticket_ui_acceptance通过真实125项进度、5项单独显示、标题/时间/编号/关注点、375px；黄色提示的全不足/部分不足与两页链接用浏览器响应夹具验证，没有新增模型调用。
- 原80端口及独立18080验收项目均三个healthy；空库导入、重启及查询依据使用真实容器。原数据未删除。

截图：work/full-ticket-ui/01-ticket-125.png、02-result-contract-section.png、03-missing-five.png、04-warning.png（提示夹具）、05-mobile.png。
