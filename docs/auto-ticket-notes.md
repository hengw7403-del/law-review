# 建单即审查、高风险转人工

## 当前流程

确认建单 → 自动调用既有create_review → reviewing → worker完成回写：有高风险needs_human，无高风险done。needs_human必须人工确认后done，再允许closed归档。assigned状态和接单接口已移除，前端不再显示接单或正常路径的手动发起审查按钮。

如果触发失败，工单保留pending，review_error记录错误；详情可重试自动审查。合同已有活动任务时，新工单不冒充已绑定任务，保留pending及错误；原/contracts/{id}/review的活动任务去重行为保持原样。

## 原子性与worker边界

会话确认先提交工单及对话，再调用tickets.review。复用现有create_review服务，不走HTTP自调用。create_review新增仅供内部使用的_on_created回调，缺省不执行，旧接口行为不变；新任务插入后先flush，再在同一事务中写工单的review_task_id和reviewing状态。这避免任务先被worker跑完、工单却还没绑定的竞态。任何回调异常回滚任务创建，不返回假成功。

worker的检查计划、检索、模型判定、单项提交、进度和恢复逻辑不变。仅在终态事务中调用complete_tickets，查询review_task_id匹配且status=reviewing的工单。没有关联工单直接跳过；失败任务将工单恢复pending并写错误。完成/部分完成任务按有效supported/high条目数分流。merged重复项不重复计入高风险；已有confirmed/done/closed不被重复回调覆盖。任务状态和工单回写在同一事务提交。

工单读取保留同样的同步兜底，兼容历史已完成任务，但真实验收在调用GET详情之前就直接查库确认了worker回写成功。不能把读取时回填当成worker已写入的证据。

## 迁移与审计

新增high_risk_count INTEGER NOT NULL DEFAULT 0、needs_human_reason TEXT、confirmed_by TEXT（users FK RESTRICT）、confirmed_at DATETIME；额外保留confirmation_note与review_error记录确认备注及触发失败原因。

ticket_auto_review重建tickets表，保留原字段、主键、唯一约束、索引及外键，assigned按reviewing复制，新CHECK只允许pending/reviewing/needs_human/done/closed，high_risk_count非负。完成后做foreign_key_check；再次执行按DDL识别为已迁移，无重复创建。原assignee_id字段与历史关系保留，权限规则没有扩大。旧assigned但未关联任务的记录保留为reviewing，页面明确提示旧关联待恢复，不伪造审查任务或结果。

正式备份：work/backups/law-before-auto-ticket-20261110-114843.db。定向迁移测试覆盖旧assigned记录、内容保留、重复执行及拒绝再次写入assigned。

## 人工确认

POST /tickets/{id}/confirm，body={"note":"可选，最多2000字"}。必须为reviewer/admin且通过原工单资源权限，越权404；状态必须needs_human，否则409。确认在BEGIN IMMEDIATE事务内检查并记录confirmed_by、confirmed_at、confirmation_note及done，重复提交不会覆盖首次确认。needs_human和reviewing不能关闭。

列表支持GET /tickets?status=needs_human。前端筛选为全部/审查中/待人工确认/已完成，待人工卡片橙色边框；详情显示高风险数量和备注框，确认通过后提示条消失。建单成功自动跳转详情并轮询；发送响应丢失、重新恢复已完成会话时也直接进入对应工单。失败pending有可见错误。

## 真实验收记录（正式law.db）

|样本|工单ID|审查任务ID|worker写入状态|高风险数|
|---|---|---|---|---:|
|坑合同|d772a4109fe5467c8ea5cea427971cda|5d940d3149a1486db1a23edb54177713|needs_human|1|
|干净合同|c2d85a645d934d89be02ded366ea4e4e|99526185d1c44a0c8612e0cb891fdae6|done|0|

两份均party_b、reviewer01。用户明确提供候选合同ID并确认时，一次意图调用即可建单；信息不明确仍多轮确认。本轮两个会话各1次真实调用，分别625、631 token。worker真实执行两份合同各11次调用，分别30564、30281 token，未使用SDK mock。

另通过原上传接口新增普通合同fd52b83b947643959b6fe69b93506b61，不建工单，使用前4条+intellectual_property单规则作限成本烟测；任务0867459dde0c4c98b17c30b06c2c0e3c正常completed，1次真实调用、2574 token，没有关联工单，也没有回写异常。这不是普通合同全量效果验收，完整普通worker流程另由原有回归覆盖。

本轮总计25次真实调用（意图2+两合同22+普通1），64675 token，llm_logs从189增至214。全局持久化计数硬限制25，未通过重跑挑结果。

坑合同之后已用真实浏览器点击确认通过，当前为done；确认前needs_human状态、高风险数和筛选结果均已截图，确认后confirmed_by=reviewer01用户ID，备注为“验收测试：确认通过流程验证”。这是一条功能验收确认记录，原机器风险意见没有被改写。对该工单重复confirm，以及对干净合同confirm，均实测409。

证据文件：work/auto-ticket-acceptance.json（含会话响应、worker读取前的DB状态、确认审计）、work/ordinary-after-hook.json、work/auto-ticket-regression.log、work/auto-ticket-confirm.log。截图在work/auto-ticket-formal/。

## 测试范围

- npm run build成功。
- unittest完整132项通过，原129项中旧assigned操作的断言按新状态机更新，并新增迁移、高风险确认及活动任务失败测试。
- tests.ticket_browser_acceptance通过，隔离数据库+SDK mock，只验证UI，不记作上述真实调用；覆盖自动跳转、无需接单、轮询完成、提示/筛选/确认审计、375px。真实工单的前后状态另外通过scripts/confirm_auto_ticket_acceptance.py验证，没有追加模型调用。
- 现有合同接口的去重语义、原审查任务/报告权限和worker恢复测试保留并通过。

scripts/auto_ticket_acceptance.py、ordinary_after_hook_acceptance.py会创建正式任务并计费，保留已有结果，不应作为日常无成本单元测试反复运行。
