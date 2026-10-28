# Qrent 执行约束与事实入口

沿用用户的全局 Agent 规则。本文件是本项目的执行入口；动态状态以部署回执、探针和下列报告为准，不把旧聊天或旧 `CLAUDE.md` 说明当作当前生产结论。

## 已确定的边界

- 只推送 `fisher-byte/qrent` 的恢复分支；不推送原 `wiperi/qrent`。fork 的 GitHub Actions 已停用，部署使用本机检查和 SSH。
- 每月新增 API/代理支出最多 **USD 5**。代理实验累计采购最多 **USD 1.50**；10 月 27、28 日两张已付发票各 USD 0.30，合计 **USD 0.60 / 2GB**。本轮新增 API 服务只调研，由用户看完清单后自行购买。
- Google Cloud 的预算是 **AUD 5 提醒**，不是 Maps 自动停费。已设 ComputeRoutes 每日 300 次、Matrix 每日 0；受控程序每个真实结算组每月最多 9,500 次。同账号多 Key 不增加免费量，不提高调用闸值去启动未经预算覆盖的全量。
- 新增 API 调用必须使用带正式账本和调用上限的受控服务，`enrichment_runner.py` 为正式批处理入口。不要通过旧的评分/通勤批处理脚本绕过账本，也不要给采集 cron 增加未受控 `--score`。

## 持久状态与访问

- 现有 `ssh qrent-prod` 连接广州爬虫机；`ssh hk-relay` 连接现有香港出口。不要重新要求用户登录腾讯云，除非当前 SSH 有实际故障。
- **唯一正式补充账本：** `qrent-prod:/root/qrent-scraper/enrichment/state.db`。必须复用其缓存、失败/不确定调用记录和费用预留；禁止清空、换空文件或用旧快照重启收费任务。正式任务在该主机串行执行。
- 本地 `packages/scraper/output/enrichment-validation-20261029/enrichment-cache.db` 是历史验证快照。本地提供商 Key 已置空，避免两个主机各用一份账本发请求。服务器私有 `.env` 保存已验证的 Maps/Qwen 配置；不进入 Git。
- Maps 普通香港出口为 `socks5h://127.0.0.1:18082`，由 `qrent-maps-egress.service` 复用既有服务器间 SSH 访问；不依赖本机在线。**18080 是旧 LycheeIP 住宅代理链路，不用于 Maps。**
- 成功通勤按完整地址（保留房号、门牌区间）与校区跨来源复用，不因普通重跑、进程重启或跨月自动重算。模型按完整描述、提示词和配置复用。金额和请求在 HTTP 前预留，失败/不确定请求不抹账。
- 悉尼线上 MySQL 连接只在私有根 `.env`；不要把 root 数据库凭据复制到爬虫机。自动生产写入需要单独配置受限写入身份，当前尚未接到每日发布。

## 数据与部署

- 保留线上原表结构、已有主键、用户与收藏。只发布有真实报价、描述、8 个有效评分、中英文关键词、图片与实际公交通勤的完整记录；未知值不伪造成固定 13 分、0 分钟或驾驶乘数。
- 来源用原 URL 和 `(source, house_id)` 区分，跨源数字 ID 碰撞隔离。原库既有 7 个学校代码，不创建重复全名学校。
- 生产 DML 前备份；以 MySQL 行、公开 API 与实际页面逐层核对。4 条小样本通过不等于几万条库存已经同步。原站发布时间与原库采集时间的语义见恢复报告。
- 采集使用 `/root/qrent-scraper/.capture.lock`；用 `packages/scraper/ops/deploy_recovery.py` 加锁、备份、测试并部署。运行中不覆盖源码，不同时启动两个采集。
- 当前都会区活动为 `metro-full-integrity-20261028`。先通过 `ops/remote_capture.py status --campaign metro-full-integrity-20261028` 检查真实进程，再决定是否显式 `--resume`；保留成功页面，仅补失败查询。旧 `exit-code` 文件可能属于前一次手工启动，新的 `process-status.json` 与 launcher 的 PID/start ticks 一并判断。

## 可核查记录

- [补充、预算、生产小样本与部署](docs/reports/2026-10-29-enrichment-recovery.md)
- [免费额度、便宜 API 与采购候选](docs/reports/2026-10-29-commute-api-options.md)
- [当前执行计划及被替代的历史提案](docs/superpowers/plans/2026-10-28-website-sync.md)
- 私有输出中的 `deployments/*/server-receipt.json`、`enrichment-server-stage-20261029` 和 `enrichment-validation-20261029`。不要将私有备份、账本、页面原文或 `.env` 推送。
