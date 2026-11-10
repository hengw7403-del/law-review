# 自然语言发起工单：实现与验收

本页保留第一版验收历史。当前已取消接单和正常路径手动发起审查，状态机及新验收以[auto-ticket-notes.md](auto-ticket-notes.md)为准。

这是现有合同初审之外的一层入口，不是Agent或新审查引擎。模型只提取contract_id、focus_points、priority并生成回复；服务端控制权限、确认、状态转移和是否建单。工单确认后为pending，接单后才可通过已有create_review服务触发审查，worker及原有审查接口行为保持不变。关注点是人工复核备注，本轮不会据此自动修改规则清单。

## 步骤1：数据与迁移

在原7张表之上增加conversations、conversation_messages、tickets，id均TEXT。完整表及索引DDL在work/ticket-step1-schema.sql。add_ticket_tables是加法迁移，重复执行不会重建或清理原表；启动时执行。正式库变更前已用SQLite backup备份至work/backups/law-before-tickets-20261110-110713.db。

- conversations：user_id非空、用户删除RESTRICT；status有CHECK；collected需合法JSON对象；部分唯一索引保证每用户最多1个active会话。
- conversation_messages：conversation_id非空且CASCADE，role仅user/assistant；按会话及时间索引，服务端保证同轮两条消息时间严格递增。
- tickets：contract_id必须存在且删除RESTRICT；requester、assignee删除RESTRICT，保留审计关系；conversation_id可空且SET NULL，非空时UNIQUE，保证一次会话只建一单；review_task_id可空，关联任务删除RESTRICT；status、priority及focus_points的JSON数组类型均有CHECK，assigned/reviewing/done必须有assignee。

旧的精确“7张表”断言更新为10，原7张表对需求v6的字段核对仍保留；解析预览不写任何业务表的断言仍保留。不能要求新增表后数据库表数仍是7。

## 步骤2：会话

新增POST /conversations、GET /conversations/{id}、POST /conversations/{id}/messages，全部复用JWT。会话仅本人可读写，其他人（含其他用户的admin会话）404；admin通过获授权工单可查看关联历史。POST入口在短事务内读取或创建active会话，数据库唯一索引兜底防重复。

每轮读取完整历史和最近10份已解析且有权限的候选合同，提示词独立放app/prompts/ticket_intent.py。候选只有ID/标题，不传全文。多份同名还需用户提交候选ID；模型伪造、越权或失效ID不能建单。服务端要求contract_id有效、ready为真且本轮有明确确认词，无关话不建单。可选槽位采用默认值；输入及数组长度受限。输出格式错误记INVALID_MODEL_OUTPUT，保留已有槽位，不伪造成功。

模型调用在事务外执行。collected内有短期inflight标识，防止并发发送重复收费或覆盖历史；120秒后可重试，历史不回滚成空。有效槽位和双方消息、工单、completed状态在同一事务提交。每个会话最多5次HTTP尝试，无自动网络/格式重试；以已知实际usage加本轮UTF-8保守上界及输出预算检查8000 token上限，用量未知时停止继续计费，允许明确结束后重开。额外POST /conversations/{id}/abandon用于用户主动“重新开始”，历史保留；不会因为重复打开页面就弃掉会话。

LLMClient默认行为保持不变，仅允许会话传max_output_tokens=512及max_retries=0；每次调用前预记llm_logs，purpose=conversation，request_id以会话ID为前缀。GET会话返回cost；工单详情额外汇总conversation、review和合计成本，未知用量为null，不按0算。

### 真实curl输出摘要

完整请求/响应：work/ticket-step2-curl.json（授权信息未写入）。正式会话647a7e1d94ea4c3a9cc26b1a24dabced，reviewer01：

|轮次|用户|回复及状态|
|---|---|---|
|1|审不存在的蓝鲸采购合同|没找到，列出4个候选，contract_id=null，ready=false|
|2|选择contract_c_bad.txt并提供ID，关注付款账期、单方解除|复述信息，priority=normal，ready=false|
|3|确认创建工单|ready=true，创建pending工单a46652569ab34182a2fffefbffb17fa3|

3次真实模型调用，累计1900 token。SDK mock测试不计入这个数字。

## 步骤3：工单与既有权限

GET /tickets支持pending、assigned、reviewing、done、closed及processing（assigned+reviewing）过滤；reviewer只看pending及自己发起/负责的，admin全量。详情、接单、审查、关闭均先做资源级检查。pending工单可供reviewer接单，但无关接单前用户不获得对话历史，也不因此获得合同全文权限。

POST assign：pending→assigned，绑定当前接单人；普通reviewer写入合同现有reviewer_id，继续走既有合同权限，不修改access.py。若已有其他复核人则409，防止接单抢占；admin本来能看所有合同，无需修改合同复核人。重复本人接单返回已有结果。

POST review：仅接单人/admin可触发。先短事务保留reviewing状态，再调用既有create_review（不持有写事务），写入review_task_id。重复触发已有reviewing/done返回同task_id。关闭reviewing被拒绝，不能让入口关闭与在跑worker脱节。失败任务可重新发起；工单接口读取时同步completed/partial_failed为done、failed为assigned，worker完全不知道工单表。

POST close：本人发起/负责或admin可关闭非执行中工单，其他404。会话不一定可删除，合同/任务关联不可随意删除，保留审计链。

### 真实链路

work/ticket-step3-states.json记录pending→assigned→reviewing→done。合同为正式库坑合同，party_b；任务db0abf7bcf56480480471585f40ffa61由正常worker真实执行并completed。默认原有3规则、12条全量、11个适用单元，4 supported（high 1、medium 3）、0 insufficient、32 not_applicable。11次审查调用，30618 token；会话和审查合计14次、32518 token。会话8000限制不包括用户另行发起的合同审查费用。

## 步骤4：Vue入口

- /workspace/new-ticket：气泡、候选按钮、输入法组合事件识别、Enter发送/Shift+Enter换行、发送中锁定、建单卡片；sessionStorage只保存conversation_id，恢复时从服务器取历史；切换账号清该ID，后端仍是最终隔离。
- /workspace/tickets：全部/待接单/处理中/已完成过滤，卡片及空状态，异步筛选有序列号防过期响应覆盖。
- /workspace/tickets/:id：信息、原始需求、授权对话、接单/审查/关闭、任务进度及结果/报告入口；终态和离页停止轮询。成本显示分开列出。

继续复用StatusBadge、Toast、Pinia用户状态和统一fetch，新增三个视图，没有新增npm依赖或CDN。全局导航新增发起工单、我的工单，登录回跳白名单包含新路由。

截图位于work/ticket-screenshots/01-conversation.png、02-tickets.png、03-ticket-detail.png、04-mobile.png。展示截图使用此前正式记录的副本；额外UI交互使用隔离库及tests/ticket_browser_server.py内的SDK MockTransport，不向正式worker注入mock，也不把mock结果写入law.db。验收包含无关话、错合同、未确认不建单、刷新恢复、重复入口、候选点击、接单、触发queued任务、既有真实结果跳转、筛选与375px。正式curl和worker验证单独保留，不能互相替代。

## 步骤5：复验命令

```powershell
npm run build
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m tests.ticket_browser_acceptance
```

真实验收脚本scripts/ticket_conversation_acceptance.py会调用收费模型，scripts/ticket_review_acceptance.py会创建正式审查任务，不能当成普通单元测试反复执行。正常UI可查看上述已完成工单；重新发起真实审查仍按现有计费及日志规则。

最终完整回归：129项全部通过。新增页面浏览器验收通过，本机8000已加载最终版本。work/ticket-final-acceptance.json记录最终工单、任务及分项成本；正式库llm_logs最终189条（新增会话3条、审查11条）。
