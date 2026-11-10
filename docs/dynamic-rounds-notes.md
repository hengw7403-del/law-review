# Agent 动态步数

## 规则

- `BASE_MAX_ROUNDS=10`：还没有成功列出合同时的工具调用上限。
- `PER_CONTRACT_ROUNDS=3`：每份约取、审、写三步。
- 首次list_contracts得到N份后，上限=`min(2 + 3*N, 40)`；绝对上限`MAX_ROUNDS_CEILING=40`。
- 空列表上限2；1份上限5，3份上限11，15份封顶40。
- 工具失败也占一步。循环及同一模型响应中的每次工具调用前都会重查上限，模型不能通过一次返回很多tool_calls绕过限制。

首次成功的合同列表作为任务范围冻结。写回后OA待审数减少、再次列出更多合同，都不会缩小或扩大初始上限，也不清零已用步数。首次列表之外的合同不能混入该run的审查。

## 输出与日志

最终输出包括：rounds_used、rounds_limit、contracts_processed、contracts_total、stopped_reason。

contracts_processed只统计审查完成且OA确认写回成功的合同；仅获取正文或仅完成审查不算全流程完成。合同总数未知时为null，不伪装成0。未先列合同的单ID旧入口仍可使用基础上限；结束时可从明确ID推导总数。

每次工具执行完打印一条agent_round JSON，包含run_id、round、max_rounds、decision、tool、args、result_ok。max_rounds是该步开始时的上限，额外的max_rounds_after说明执行后是否因首次列表而重算。例如第一步列出3份时为10→11，之后各步11。成功/失败也保存在steps中。

触及上限后stopped_reason=MAX_ROUNDS_REACHED，status=failed、partial=true，保留已有任务、写回结果和详情链接。摘要明确完成N/M以及哪些合同未处理完，例如“完成了2/3份；第3份 C003 未处理完”。旧固定8步历史run不重写，不为历史执行伪造动态上限。

## 保留成本与审查边界

规划仍限50,000 tokens，单run规划＋审查仍限200次模型调用。子任务调用额度根据动态剩余步数预留规划余量。正文、原始审查意见和依据始终保留在run/数据库/真实OA写回结果中；规划上下文只携带调度必要的ID、名称、task_id、状态和统计，避免多份合同重复正文及长依据先耗尽规划预算。没有修改审查规则、审查提示词、worker或工单逻辑。

用户要求“审一遍”且没给ID时，先列合同并按首次列表处理；我方立场仍由用户明确或组织匹配，模型不能猜。前端显示已用/上限和已完成/总数，轮询与终态停止行为不变。

## 免费验证

- 原170项＋新增8项完整回归178项通过，耗时136.202秒；额外定向8项复验通过。OA原3项全部通过并覆盖reset规模参数。
- 1份模拟：4步完成、上限5、1/1；3份模拟：10步完成、上限11、3/3。
- 15份模拟：40步封顶、完成13/15，MAX_ROUNDS_REACHED，摘要列出C014、C015。模拟审查标为SIMULATED，模型调用0，没有在正式库写入假审查结果。
- 覆盖未知规模10步、失败调用计数、空列表、重复列表不改变范围、2/3部分完成说明、完整数据保留与规划上下文压缩。
- 真实OA REST已验证count=1/3/15，实际列表数量一致。

## Mock OA 测试规模

POST `/api/reset?count=1`、`?count=3`、`?count=15`。默认仍为3，允许0–100，大于3时轮换原三个样本并生成唯一ID。reset仅清空OA内存，不删除law.db或Agent历史。

上轮OA内存记录先归档至work/dynamic-rounds-oa-before.json；单份验收结束后的OA写回也先归档到验收JSON，再重置三份。正式库的历史与本轮run均保留。

## 真实验收结果

验收脚本：scripts/dynamic_rounds_acceptance.py。完整单份及三份任务各只启动一次，使用正式Compose数据库data/law.db、真实模型、真实MCP与既有worker；没有以模拟审查代替这两次验收。run详情和OA写回保存至work/dynamic-rounds-acceptance.json。

|OA规模|run_id|rounds_used|rounds_limit|完成合同|stopped_reason|状态|
|---|---|---:|---:|---|---|---|
|1|0808b78bbf0f46a58852e3d341a80941|4|5|1/1|null|completed|
|3|6b46f633a33d4fa28f5305ea44b3e9a3|10|11|3/3|null|completed|

三份任务中，模型先获取三份正文，再逐份审查和写回；仍是10次工具调用，没有重复检查。四次被触发的正式审查都完成125项，其中一个在单份阶段，三个在三份阶段。

|阶段|OA ID|正式task_id|
|---|---|---|
|单份|C001|427e891d726540fcb1fd10be096c5487|
|三份|C001|728e0c3887824a218acee244a1cd586d|
|三份|C002|9d33ce10b455430fa784faaa526c1902|
|三份|C003|22b46e4347c445d6a6de99a3d137d22b|

真实API日志已逐条核对14条：单份第一步max_rounds=10、执行后为5，后续三步均5；三份第一步10→11，之后九步均11。每条有decision/tool/args/result_ok，均成功。结构化摘录在work/dynamic-rounds-round-logs.json，完整步骤也在run记录中。

## 成本与保留数据

|阶段|规划调用/tokens|审查调用/tokens|合计调用|
|---|---|---|---:|
|1份|5 / 6,691|50 / 131,193|55|
|3份|11 / 19,432|149 / 390,739|160|
|总计|16 / 26,123|199 / 521,932|215|

正式库llm_logs新增215条（agent=16、risk_judge=179、missing_judge=20），低于本轮260；三份run的160也低于单run200。未知用量0、调用错误0，没有追加收费重跑。每个run的规划均低于50,000 tokens。

验收前使用SQLite backup保留/data/before-dynamic-rounds-20261110.db；两个run、四个任务和全部调用日志保留在正式库及/data/agent-runs。最终OA保留三份的真实写回；单份阶段写回在重置前已归档。integrity_check=ok，foreign_key_check=0。

只读浏览器验证通过：真实1份4/5和1/1、真实3份10/11和3/3、10条时间线、375px布局，无额外模型调用。前端构建成功。

15份40步、13/15的结果仅是明确标记的免费模拟边界测试，不是真实模型审查13份；生产运行还受规划token和调用预算约束，可能先触及其他上限。
