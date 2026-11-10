# Merge 性能修复与真实任务验收

## 验收结论

实现、合并语义、每次 UPDATE 上限、回归及正式任务可用性验证通过；**“总耗时降低10倍以上”未通过**。不能将本轮记录为全部验收通过。

## 修改

- `merge_clause_items(session, task_id, clause_id)` 同时按任务、条款过滤，只处理 done 的 supported/merged。小于2条直接返回，合同级单元不进入该路径。
- 在内存计算目标后，仅当状态或 JSON 内容不同才赋值。比较 JSON 值，保留未变化结果原有序列化格式；重复运行无 dirty、无 UPDATE。
- signature、风险等级和 termination 优先级不变。支持后来高风险替换 winner，保留原始模型输出、merged_into、merge_reason、merged_from。
- worker 每单元只合并当前条款。全任务包装只在所有单元完成后校正一次；显式 flush 后再统计高风险、回写工单。
- unit_committed 增加 committed_at、merge_ms、checkpoint_ms，用于区分模型延迟和数据库检查点耗时。

## 免费性能对照

执行：`python scripts/merge_perf_check.py`。默认用临时 SQLite 文件，不接触正式库，不调用模型；冻结修复前函数作为基线。

120项、12条款×10规则，每10项发布结果后调用一次merge。单项 JSON 5,983字节；5轮交替执行旧/新版本，比较中位数。计时含 merge 查询、计算及 flush，不含两边相同的结果插入；新版总耗时还包含结束时一次全任务校正。

|指标|改前|改后|
|---|---:|---:|
|总耗时中位数|78.0611ms|35.1535ms|
|加速比|—|2.2206倍|
|最后一轮 dirty 行数|120|10|
|最后一轮实际 UPDATE 行数|10|10|
|每次最大实际 UPDATE|10|10|
|最终校正 UPDATE|不单列|0|

两版最终逐项 JSON、状态与等级一致。实测发现，旧版 dirty 行数增加，但 SQLAlchemy 跳过了最终值相同的 UPDATE；因此本样本不支持“旧版每次全量 UPDATE”的说法。旧版确实反复扫描、反序列化、序列化整个任务。没有通过扩大载荷、给旧版添加 sleep 或排除新版最终校正来凑10倍指标。脚本将 `passed=false` 写入报告，并返回退出码1，表示性能目标未达到。

## 回归

148项测试全部通过，耗时161.236秒（原有144项＋新增4项）。新增测试覆盖条款隔离、单条/不同风险点不赋值、重复运行零dirty/零UPDATE、后来高风险晋升、termination同级优先以及原始输出保留。

## 正式库真实任务

- 环境：本机 Compose `http://localhost`，正式 `data/law.db`。没有 mock，没有隔离库，没有清理结果。
- 合同：原样上传 `samples/contract_c_bad.txt`，乙方立场，owner=reviewer01。
- task_id：`b7541f2ab23744349bcccca46d32340e`
- ticket_id：`754a28d538514b42900d63712d351ac9`
- 125/125，completed，failed_items=0；high=1，工单 needs_human。
- 状态统计：high=1、medium=4、insufficient=1、no_issue=24、not_applicable=93、merged=2、failed=0。
- 调用50次：建单对话1、条款审查44、缺失检查5；129,334 tokens。无调用错误或未知用量，未超过55次。
- 运行前已有业务数据通过 SQLite backup 接口备份到 `/data/before-merge-perf-20261110.db`，未删除数据库或WAL/SHM。

监测在 worker 启动前开始，从0/125覆盖至完成，共140.24秒。每500ms通过 nginx 检查 readiness 和已认证 `/tickets`，两者各281次，全部200；503、其他失败、超时、新 OperationalError均为0。就绪接口最大响应93.25ms。三个服务最终均healthy，数据库 integrity_check=ok、foreign_key_check=0。

76项关键词不适用在计划初始化时保存，49个实际执行单元各有一条 unit_committed，因此间隔样本为48个，而不是125个模型调用。

|计时指标|前10个|后10个|
|---|---:|---:|
|提交间隔均值|2,391.637ms|2,844.830ms|
|提交间隔中位数|2,329.174ms|2,909.014ms|
|对应模型调用均值|2,345.000ms|2,809.700ms|
|检查点均值（结果及独立进度事务）|25.904ms|18.355ms|

后期提交间隔约增加19%，不能表述为“完全没有增长”。同期模型耗时约增加20%，检查点均值下降；本次数据没有显示 merge/数据库检查点随任务后期退化。末尾包含5次合同级缺失检查，与条款检查的提示长度不同。全部检查点最大43.020ms，全部单次merge最大8.116ms。这些数据是一次运行的观察，不是控制模型延迟的对照实验，也不能证明历史503的唯一根因。

## 保留证据

- `work/merge-perf-report.json`：5轮逐次耗时、dirty与UPDATE计数。
- `work/merge-real-acceptance.json`：合同、会话、任务、工单、完整结果。
- `data/merge-concurrency.json`：每次接口状态和耗时。
- `work/merge-unit-timings.json`：49次提交与48个间隔、分组统计。
- `work/merge-real-containers.log`：监测周期内容器日志。
- `work/merge-cost-audit.json`：正式库调用增量与用量。

临时监测token已删除。没有追加模型试跑来追求指定风险等级。
