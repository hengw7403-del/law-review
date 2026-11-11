# Agent 立场交接、工具分流与真实复用验收

日期：2026-11-10（Asia/Shanghai）。正式 Compose 库为 `data/law.db`；未使用隔离库替代真实验收，任务、工单、模型日志均保留。

## 真实结果

| 场景 | run_id / task_id | 结果 |
| --- | --- | --- |
| admin 无组织，临时清空 C002.stance | run `0650d4cbc0e04289895ca6fbba4af8dc` | 取合同→review_contract→needs_stance；没有触发审查，规划 3 次 |
| reviewer01 指定 C002 乙方 | task `402a9d418e784600b7782e25994937dd` | completed，125/125，10 条款规则＋5 缺失规则，真实调用 49 次 |
| 法律问题“违约金上限是多少” | run `1f473f38f4d24696979e229ad348d1f7` | 只调用 search_law，检索 5 条依据，规划 2 次，未创建审查任务 |
| “OA 里有哪些合同” | run `5ea43c565d9e48528f995ca385e524da` | 只调用 list_contracts，返回 3 份，规划 2 次 |
| 全部审查，临时清空 C003.stance | run `5bc7f528f3da4dc7b64b72d7afc11c1e` | completed，9/11 步，2 份完成、1 份待明确；规划 10 次＋新审查 50 次 |

C002 工单 `38969ff9b50c4bbeb374fab017640d74`：needs_human，high_risk_count=1。实际审查结果为 supported/high 1、supported/medium 3、insufficient 1、no_issue 25、not_applicable 93、merged 2。merged 的原风险保留，统计不重复计为高风险；原高风险尚待法务确认。

批量中的 C001 真实 task 为 `613a84f7e30846d8af63e223d02301c3`，party_a，completed，已处理 125/125，high=0、medium=3、no_issue=31、not_applicable=91。调用 50 次。其普通任务 done_items 沿用现有成功检查数语义（34），完整进度使用 processed_items=125，未修改普通审查计数行为。

批量中的 C002 明确返回 reused=true，task_id 仍为 `402a9d418e784600b7782e25994937dd`。正式库该任务日志仍为 49 条，没有重复调用已完成单元；没有把复用成本再加到批量的新审查成本。OA C002.result 中保留同一 task_id 和 reused=true，C001.result 也已真实写回。

C003 工单 `b588500b4e374f32a4f14c10c11cdd8e`：needs_stance，review_task_id=NULL；OA result=NULL，不伪造完成结果。默认 OA 三份都有 stance；验收才使用 reset 的 clear_stance 开关。当前 OA 保留批量验收现场，未恢复默认覆盖结果。

## 成本与失败记录

正式库 llm_logs：697 → 824，新增 **127** 条，低于 150 次上限。

| 类型 | 实际调用 | tokens |
| --- | ---: | ---: |
| Agent 规划（含失败） | 28 | 54,758 |
| 条款审查 | 89 | 243,300 |
| 合同级缺失检查 | 10 | 16,455 |
| 总计 | **127** | **314,513** |

规划 50,000 token 限额按每个 run 单独检查；不是所有验收 run 的总和。本轮两份真实审查合计 99 次，259,755 tokens。

保留两次失败验收，计费共 11 次，未掩盖为成功：

- run `a1c7efb924a0416b983acc3b23ef591f`：1 次规划后数据库打开失败，未触发审查。验收脚本从 Windows 打开容器的 WAL 数据库；已移除该跨系统访问路径。重启恢复后该模型日志仍在正式库。
- run `91ae9c403f23419bb32ccaba1682dd1b`：10 次规划，已真实复用并写回 C002，但把批量请求中的 C002 说明误当成唯一目标，导致 C001/C003 被拒。已修复目标范围及历史立场引用的判断，并补回归测试。该轮没有新审查。

当前 admin 初始化密码文件与实际账号密码不匹配，因此 admin 验收使用应用本地签名器生成的短期 JWT；没有改密码、角色、组织或 auth_version。reviewer01 指定立场使用原账号真实登录获得的 JWT。

## 免费验证与边界

主项目 **189 项通过**，包括原 178 项及 11 项新增测试；OA **3 项通过**。新增覆盖旧表迁移＋重复迁移、外键保留、needs_stance 幂等、角色与资源权限、立场选择后原子建任务、失败回滚、全部规则、显式立场与活动任务冲突、来源立场优先、检索参数错误、同源同版本真实任务复用条件。

原测试中单份“审一下 C001”的路由用免费 stub 验证；本轮批量的 C001 则真正调用 review_contract 和 worker，按此前确认合并验收。没有把免费测试结果当作正式审查结果。

C001 后半程（已完成 91 项以后）在容器内同时检测健康及工单接口约 79 秒，503=0，最大健康请求耗时 20.46ms。它只证明该监测区间可用，不能声称覆盖整个任务，也不证明历史故障的唯一根因。

前端生产构建通过，未增加依赖。自动化浏览器工具初始化报“failed to write kernel assets / 系统找不到指定的路径”，所以没有取得页面截图，也未把前端点击流程标为浏览器实测通过。后端指定立场→reviewing→needs_human 已通过真实接口执行。

原始可核对数据在 `work/agent-handoff-acceptance.json`、`work/agent-handoff-audit.json`、`data/agent-handoff-health.json`；正式任务与日志保留在 `data/law.db`，Agent 原始过程在 `data/agent-runs/`。
