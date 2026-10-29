# Qrent 执行约束与事实入口

沿用用户的全局 Agent 规则。本文件是本项目的执行入口；动态状态以部署回执、探针和下列报告为准，不把旧聊天或旧 `CLAUDE.md` 说明当作当前生产结论。

## 已确定的边界

- **2026-10-30 财务排查优先：新增模型请求已停止。** 活跃服务器环境中的模型 Key 为空，`qrent-school-enrichment.service` 另受缺省不存在的 `enrichment/model-investigation-cleared` 标记阻止。正式账本保留提供商暂停标记、全部失败与费用预留；未完成 Key 归属和预算复核前，不恢复凭据或启动模型任务。
- 只推送 `fisher-byte/qrent` 的恢复分支；不推送原 `wiperi/qrent`。fork 的 GitHub Actions 已停用，部署使用本机检查和 SSH。
- 用户最新授权：模型费用独立计算，每月最多 **CNY 50**，按人民币原生计价预留/结算；非模型新增 API/代理费用继续最多 **USD 4/月**，通勤接入新增现金 **USD 0**。代理实验累计采购最多 **USD 1.50**；10 月 27、28 日两张已付发票各 USD 0.30，合计 **USD 0.60 / 2GB**。用户已允许悉尼剩余 142 对通勤在既有 Distancematrix.ai 赠金内补齐，不必等待次日 Google 窗口；赠金累计最多 USD 40、现金 0，目前尚未接入或发请求。墨尔本继续暂缓，不新增采购。
- Google Cloud 的预算是 **AUD 5 提醒**，不是 Maps 自动停费。已设 ComputeRoutes 每日 300 次、Matrix 每日 0；受控程序每个真实结算组每月最多 9,500 次。同账号多 Key 不增加免费量，不提高调用闸值去启动未经预算覆盖的全量。
- 新增 API 调用必须使用带正式账本和调用上限的受控服务，`enrichment_runner.py` 为正式批处理入口。不要通过旧的评分/通勤批处理脚本绕过账本，也不要给采集 cron 增加未受控 `--score`。

## 持久状态与访问

- 现有 `ssh qrent-prod` 连接广州爬虫机；`ssh hk-relay` 连接现有香港出口。不要重新要求用户登录腾讯云，除非当前 SSH 有实际故障。
- **唯一正式补充账本：** `qrent-prod:/root/qrent-scraper/enrichment/state.db`。必须复用其缓存、失败/不确定调用记录和费用预留；禁止清空、换空文件或用旧快照重启收费任务。正式任务在该主机串行执行。
- 本地 `packages/scraper/output/enrichment-validation-20261029/enrichment-cache.db` 是历史验证快照。本地提供商 Key 已置空，避免两个主机各用一份账本发请求。服务器私有 `.env` 保留 Maps/NSW 配置；模型凭据在财务排查期间为空，原私有备份不可直接回填或进入 Git。
- Maps 普通香港出口为 `socks5h://127.0.0.1:18082`，由 `qrent-maps-egress.service` 复用既有服务器间 SSH 访问；不依赖本机在线。**18080 是旧 LycheeIP 住宅代理链路，不用于 Maps。**
- 成功通勤按完整地址（保留房号、门牌区间）与校区跨来源复用，不因普通重跑、进程重启或跨月自动重算。模型按完整描述、提示词和配置复用。金额和请求在 HTTP 前预留，失败/不确定请求不抹账。
- 悉尼线上 MySQL root 连接只在私有根 `.env`；不要复制到爬虫机。自动写入现用私有 `enrichment/qrent-writer.env` 的受限身份，只能写房源相关 4 表、只读 schools，无用户/收藏/删除/改表权限；仅允许现有香港出口。`qrent-mysql-egress.service` 经现有 SSH 监听 127.0.0.1:13306，保留 MySQL TLS 与压缩，不依赖本机或住宅代理。

## 数据与部署

- 保留线上原表结构、已有主键、用户与收藏。只发布有真实报价、描述、8 个有效评分、中英文关键词、图片与实际公交通勤的完整记录；未知值不伪造成固定 13 分、0 分钟或驾驶乘数。
- 来源用原 URL 和 `(source, house_id)` 区分，跨源数字 ID 碰撞隔离。原库既有 7 个学校代码，不创建重复全名学校。
- 用户已撤回整座都会区的采集范围。悉尼严格复用 `TARGET_AREAS`：UNSW / USYD / UTS 各 10 项；Domain 按原 suburb slug，Real Estate 按原邮编 URL；每个区域保留原来的 7 页窗口。相同 URL 只采一次，但在来源 ID 去重前保存 `school_cohorts`，计算查询对应学校的通勤，不将全城房源关联同城全部学校。平台返回相邻区域时也保留查询的学校归属。
- 用户最新选择先处理悉尼、墨尔本暂缓。当前采集和通勤只执行 NSW；VIC 历史配置及现有数据保留，不启用其 API、赠金或采集。每日住宅流量预算按实际页面试跑调整，仍不自动充值或新购代理。
- 旧程序没有墨尔本 `TARGET_AREAS`。墨尔本 4 个校区的区域从发布前的原库 `property_school` 关系恢复，公开配置在 `packages/scraper/src/config/historical_victoria_regions.json`。Carlton 跨州旧关联只在范围配置中消歧，不改线上旧表。
- 跨源按完整地址、房号、街道号区间、州、邮编分组；同源多 ID、价格/特征冲突、隐藏地址或营销名称先隔离，不为可疑合并发 API。已有都会区库存的地址筛选只是估计，不能代替新的原查询学校归属或声称全量覆盖。
- 生产 DML 前备份；以 MySQL 行、公开 API 与实际页面逐层核对。4 条小样本通过不等于几万条库存已经同步。原站发布时间与原库采集时间的语义见恢复报告。
- 采集使用 `/root/qrent-scraper/.capture.lock`；用 `packages/scraper/ops/deploy_recovery.py` 加锁、备份、测试并部署。运行中不覆盖源码，不同时启动两个采集。
- 旧都会区活动 `metro-full-integrity-20261028` 已部分结束，保留断点及原始记录，不再自动恢复整城任务。每日采集入口改用 `ops/run_school_capture.sh`；部署后核对根 `run_scraper.sh` 和 cron 的实际指向。旧 `exit-code` 文件可能属于前一次启动，进程以 `process-status.json` 与 launcher 的 PID/start ticks 一并判断。
- NSW 通勤优先使用 Transport for NSW 免费 API，仅覆盖 NSW；所有端点共用账本每日 59,000 次和 0.30 秒间隔。`--commute-only` 禁止模型调用及生产发布；错误、不确定调用保留，成功同址学校通勤长期复用。墨尔本不会自动回退 Google 或 Distancematrix.ai。
- 悉尼原区域修复 `20fd6f3` 已于 10 月 29 日 14:25 经 206 项服务器检查部署；根每日入口仅悉尼、每区 7 页、住宅流量最多 150MB，不自动充值。Sydney City 原配置保持，REA 查询规范化为邮编 2000；同邮编 URL 合并后为 Domain 22 / REA 19 个查询。
- 本次悉尼剩余免费队列在 `/root/qrent-scraper/output/free-transit-batch-20261029/remaining-free-queue`。两次一次性服务器任务分别为 10 月 29、30 日北京时间 15:05，受 `ops/guarded_free_batch.py` 检查当前源文件、已有 Google 日/月占用和上一批结果，每批最多 280 新请求、模型 0、禁止发布，复用同一正式账本。前一批发生提供商错误时停止复跑；不把 timer active 或 exit 2 当作全部成功。读取实际 `batch-a/b/result.json` 与最新缓存探针确认进度。
- 模型已获单独每月人民币 50 元授权，但财务事件排查期间停止新增请求。恢复后也仅通过正式账本 `--score-only` 分批执行；完整描述、提示词、配置和评分槽位的成功结果可复用，失败/不确定调用保留待复核，不自动重复收费。更新源文件后必须同步检查现有免费队列的源码校验清单，保持通勤模型调用上限 0。
- 当前源码 `c9865d4` 已经 230 项本机/服务器检查部署。正式模型请求首次遇到拒绝或不确定响应即暂停整批，以退出码 3 阻止队列进入下一批；同一 Key 的暂停标记跨进程、提示词和模型生效，成功缓存仍能读取。人工复核前不得删除暂停标记或失败记录。`ops/enrichment_queue.py` 每批最多 50 条，审查/发布两类 API 上限均为 0。
- 首次悉尼队列为 `qrent-sydney-model-full-20261029.service`，输出 `output/model-enrichment-20261029/full-queue`；2,681 个原输入剔除 44 条后为 2,637 个有效地址。运行状态必须读实际 batch receipt、结果和账本，不凭启动状态宣称完成。
- 每日 02:00 原采集入口已接 `qrent-school-enrichment.service`：`ops/prepare_school_enrichment.py` 只按当前采集真实 school_cohorts 准备去重输入，`ops/enrichment_queue.py --nsw-free` 仅允许免费 NSW 补缺，再做受控模型和完整记录发布。服务先等待首次补充的 `enrichment/bootstrap-first-pass.json`；当前格式的首次整条每日运行还未验收。不要用历史兼容测试副本冒充新全量。
- 10 月 30 日 15:05 免费队列结束后有缓存发布后置服务，必须先确认 `batch-b/result.json` 为 commute_complete，再发布首次队列 held checkpoint；不新增模型/地图请求。模型/每日队列在当天 14:30–16:00 让出正式账本，不能挤掉免费任务。

## 可核查记录

- [补充、预算、生产小样本与部署](docs/reports/2026-10-29-enrichment-recovery.md)
- [免费额度、便宜 API 与采购候选](docs/reports/2026-10-29-commute-api-options.md)
- [当前执行计划及被替代的历史提案](docs/superpowers/plans/2026-10-28-website-sync.md)
- [原区域规则、库存筛选与 NSW 验证](docs/reports/2026-10-29-school-scope-and-nsw.md)
- [模型独立预算、分批发布与每日连接](docs/reports/2026-10-29-model-budget-and-publication.md)
- [模型提供商错误停止机制与当前暂停边界](docs/reports/2026-10-30-model-provider-stop.md)
- 私有输出中的 `deployments/*/server-receipt.json`、`enrichment-server-stage-20261029` 和 `enrichment-validation-20261029`。不要将私有备份、账本、页面原文或 `.env` 推送。
