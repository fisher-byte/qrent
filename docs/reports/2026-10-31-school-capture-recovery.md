# 悉尼每日采集失败区域恢复候选

本次是本地实现与离线验收，尚未上传服务器、补抓网页或触发每日补全。

## 已确认的故障

2026-10-31 02:00 原 cron 实际启动了 `20261031_020005_264602`。03:19 只读核对时原进程仍运行；Domain 的 Maroubra、Kensington、Haymarket 各两次导航失败，其他区域继续采集。原 `capture()` 正确保留 partial/1，但根入口只在 0/2 时启动 enrichment，因此本轮有缺口时不会自动发布。昨日三个区域的 297 个来源 ID 仍全部存在且 active；当前受控写库没有按“本轮未见”删除记录的逻辑。

另有原入口边界：daily 旧实现按 mtime 选择最后一个报告，prepare 的完整窗口标记仅看 success，不能把一次局部补抓成功当成原 41 查询完成。

## 候选行为

- 原完整报告、成功区域条目、来源身份与学校归属均保留，不改原 partial/1。
- `ops/recover_school_capture.py` 从完整原报告提取失败 Domain 区域，最多两轮；第二轮只处理仍失败的区域。普通香港出口固定为已验证的 `127.0.0.1:18082`，住宅配置及模型/地图凭据在该子进程环境置空；不抓详情、不评分、不买服务。
- 原 `.capture.lock` 覆盖恢复和实际子进程。请求先冻结，原报告、代码、请求及结果收据发生漂移均停止；启动但没有可信结果的轮次视为不确定，不自动重发。
- 聚合报告沿用原 run，重放原报告和逐轮证据，核对原 Domain 22/REA 19 查询、原成功结果和来源/学校并集。仅完整 41/41 生成单独的 `*_recovered_result.json`；不宣称整个悉尼市场完整。
- daily 与 prepare 均验证完整证据；最新一轮 partial 不会回退旧成功，也不会接纳局部 success。正式 ledger、模型授权与发布质量检查保持原入口。
- 原 root 仍负责触发 `qrent-school-enrichment.service`。子包装层只有完整验证返回 0，错误 2 会转为失败，防止原 root 把工具/参数错误误当可发布。

## Review 后的恢复入口

等待原采集自然结束，再通过原 `ops/deploy_recovery.py` 在采集锁内备份、离线测试及部署。不要覆盖运行中的源码。

部署与人工复核均完成后，原入口支持：

```bash
/root/qrent-scraper/run_scraper.sh --recover-only /root/qrent-scraper/output/school-daily-20261031/20261031_020005_264602_result.json
```

这是待 review 的操作说明，本次没有执行。恢复入口只接受当天窗口，完整验证后由原 root 触发原 daily；若有 REA、详情、其他阶段错误，或者两轮仍未恢复，则保持未完成，不强制发布。

## 离线证据

新增 19 项测试包含：原证据与成功结果保留、身份/学校并集、两轮耗尽、重启复用、未知请求不重发、缺前轮不能重置次数、篡改拒绝、原采集锁、真实子进程继承锁、固定 run/禁止覆盖、局部报告拒绝及实际 shell 的服务触发边界。

先观察到缺少恢复能力、固定 run、daily guard 和 shell exit-2 误触发的失败；修复后以 `SCRAPER_ENV_FILE=/dev/null PYTHONPATH=. ../../.venv/bin/python -B -m unittest discover -s tests` 验收：**314 项通过**。Bash 语法和 `git diff --check` 通过。测试中的网页、模型拒绝日志来自离线 doubles；未运行正式浏览器、模型、地图或生产写入。

同一套 314 项另在外网 DNS/TCP 连接被阻断、仅允许本机 loopback/Unix socket 的环境中全部通过，确认没有供应商外网请求依赖。
