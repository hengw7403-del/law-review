# SQLite 并发修复与断点恢复验收

本轮使用本机 Compose 正式库 `./data/law.db`，没有 mock，没有隔离库，没有清理业务结果。按用户确认恢复既有任务，不另外新跑 125 单元。

## 诊断与修改

根因判断：锁等待超时缺失 + 事务持锁时间过长。这一判断基于故障现象及修复后结果推断，未做对照实验，不是已隔离验证的因果结论。“锁等待超时缺失”指缺少适合当前并发负载的等待配置；原连接已有 busy_timeout=5000，并非完全没有超时设置。

正式库原本已是 WAL（且存在 `law.db-wal` / `law.db-shm`），WAL 不是本次修复点。真正生效的修复是 busy_timeout 调整与事务边界收紧：等待上限从 5 秒增至 30 秒，结果写入与进度更新拆为独立短事务。worker 原本已在模型调用前关闭查询会话，领取任务也已短事务提交，本轮保留这两项行为。

每个新连接确认 WAL 设置，并配置 busy_timeout=30000、synchronous=NORMAL；WAL 属于已有模式的确认，不能计为本次故障修复。结果与执行状态先提交，再独立短事务更新进度。重启按已提交单元跳过，进度可重算。异常日志保留堆栈，所有事务上下文自动回滚关闭。API 镜像与 worker 镜像重建，API 先启动到健康，随后在监测已经开始的情况下恢复 worker。没有修改挂载方式或删除 WAL/SHM；已有库用 SQLite backup 接口备份为 `/data/before-concurrency-fix-20261110.db`。

## 并发与恢复结果

- 任务：`d5cf0c64e7e945a49c9a07341dfad21d`
- 工单：`3c62f99a4a0d46d4ba5c4c51dead0f73`
- 断点：103/125 → completed，125/125，failed_items=0。
- 工单最终 done，high_risk_count=0；已有风险统计保留，未通过改判风险等级影响本轮验收。
- 恢复 70.77 秒，500ms 一轮；健康接口 142 次，已认证 `/tickets` 142 次，均为 200，503/超时/其他失败均为 0。
- 142 次检测全 200 是修复后该验收区间可用的有效证据；本轮没有分别回退 busy_timeout 或事务边界做对照，不能据此量化各项修改的贡献，也不能单凭结果证明根因。
- 就绪接口最大延时 17.87ms。监测通过容器内 `http://nginx`，经过相同 nginx→API 链路。
- 103 个已完成项没有新增调用日志；新增 22 条真实调用日志，无调用错误，共 55,641 tokens。
- 新 OperationalError=0，无超过 180 秒无进展。
- integrity_check=ok，foreign_key_check=0；WAL 与 SHM 均存在。
- 独立进程逐秒短写 30 秒模拟也通过，非 200/超时=0，最大就绪延时 14.01ms。

## 重现方式

先等待 API 健康，再执行（不调用模型）：

```powershell
docker compose --env-file .env.docker exec -T api python scripts/db_concurrency_check.py --database /data/law.db --base-url http://nginx --seconds 30 --output /data/concurrency-simulation.json
```

任务恢复模式将 `--seconds 30` 换为 `--task-id <任务ID>`，并在启动 worker **之前**启动监测；如需测 `/tickets`，通过 `--token-file <容器内私有文件>` 提供有效 JWT，避免把 token 写在命令行。验收工具自身不调用模型、不启动 worker；调用预算仍由 worker 的任务快照控制。

`concurrency-resume.json` 包含断点项 ID、基线日志 ID、每次请求状态和延时，供重复调用核对。PowerShell 外层监视同时检查 API/worker 新日志，任何 OperationalError 或脚本违规信号立即停止 worker。本轮没有触发停止条件。临时 token 已删除。

API 重建后首次尝试模拟启动过早，捕获到连接尚未监听的 URLError，被如实记为失败；等待 API 健康后重新运行，以上正式模拟及恢复报告均为后者。启动等待不属于 worker 恢复监测区间。

## 限制

这是从 103/125 开始的剩余全程并发验收，覆盖 22 次真实调用，并非从 0/125 新跑全量；这是用户为保持 55 次调用预算选择的验收方式。SQLite 仍然只有一个写者，多 worker 应迁移 PostgreSQL。WAL 目录必须可写，不适用于跨主机网络共享文件系统。仅靠 WAL 无法解决所有 CANTOPEN；若再次出现，应保留日志与挂载现场。

参考：[SQLite WAL](https://www.sqlite.org/wal.html)、[SQLite 错误码](https://www.sqlite.org/rescode.html)。

最终回归：137 项测试全部通过（原有 135 项 + 新增 2 项并发测试），耗时 125.756 秒。三个 Compose 服务均 healthy。
