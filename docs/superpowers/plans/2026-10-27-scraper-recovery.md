# Qrent 爬虫恢复与低流量采集计划

> **For agentic workers:** Use native execution in this session. Steps are checked against local tests and server evidence.

**Goal:** 恢复腾讯云采集任务，验证悉尼、墨尔本 Domain 直连及 Real Estate 澳洲住宅代理采集，并限制费用。

**Architecture:** 继续使用仓库现有解析器与 Playwright；为其补充代理会话、资源分流、失败状态和增量补缺。保留服务器现有 SQLite 数据库，通过新的运行入口写入，旧源码和启动文件保留可回滚副本。

**Tech Stack:** Python 3.11, Playwright, requests, SQLite, existing DashScope scoring.

**Spec:** 用户 2026-10-27 的任务说明及晚间补充；累计代理支出 <= USD 1.50，固定澳洲出口，非商业找房。

## 已核实的现状

- 仓库 `wiperi/qrent` 是 monorepo：backend、frontend、shared、agent、scraper；没有检索到另一个可访问的 Qrent 后端仓库。
- `packages/tt.scraper-v2` 仅出现在旧说明中，目录没有提交，根目录 `tt*` 被忽略。
- 腾讯云 `ins-5a985eaw` 的真实采集目录是 `/root/qrent-scraper`，不是 Docker 部署；代码已恢复到本地私有 output 目录。
- 每日 02:00 的 cron 仍执行，2026-10-27 因 gost 代理隧道失败得到 0 条，仍返回成功。旧脚本未使用 pipefail。
- 该目录使用 SQLite；其数据不能被称为已同步到 `api.qrent.rent` 的 MySQL 后端。
- 代理方案可复用来源：服务器现有 Qrent 独立部署中的 LycheeIP/gost 设计。新代理由用户指定为 FlashProxy Residential Lite。
- 千问配置来源：本机 `pdftomd/.env`（仅私有复用密钥）。当前账号模型列表 HTTP 200，包含 `qwen-plus`，不含 `qwen-plus-1220`。

## Global Constraints

- 累计代理支出 <= USD 1.50（包含已购买的 USD 0.30/1GB），无自动续费。
- 用户补充：Domain 保持直连，住宅代理只用于 Real Estate。
- 每个小样本只取一页；确认结果前不恢复无限量或无预算采集。
- 图片、视频和字体默认不下载；需图片时另走无认证直连，代理仅用于站点必需资源。
- 代理认证和千问密钥只存忽略的私有环境文件，文件权限 0600。
- 页面受阻、代理异常或范围不完整，不能视为成功或据此下架旧房源。

## Review Focus

- 代理故障、HTTP 403/429、验证页、空解析：非零状态，保留旧数据。
- 房号、州和邮编：避免把同楼不同单元或 NSW/VIC 房源合并。
- 两个平台相同数字 ID：按来源区分，数据库不能覆盖另一平台。
- 静态资源直连：不转发 Cookie、Authorization 或代理凭据；直连失败不回退到收费代理。
- 预算和重试上限：在创建新请求前检查，失败后不无限重试。

## Task 1: 恢复可诊断的运行入口

**Files:** `packages/scraper/recovery_runner.py`, `src/services/sqlite_store.py`, `src/config/settings.py`, `run_scraper.py`, `tests/test_recovery.py`.

- [x] 写失败测试：VIC 地址正确解析；同源 upsert、跨源 ID 不覆盖；采集失败退出非零且不下架存量。
- [x] 实现显式地区、页数、房源数限制和 SQLite sink；移除旧快照模型硬编码，改环境可配置。
- [x] 运行采集相关测试、CLI 导入与帮助；不运行无关前端测试。

## Task 2: 低流量代理与增量补缺

**Files:** `src/utils/network.py`, `src/utils/browser.py`, `src/services/incremental.py`, `src/scrapers/domain.py`, `src/scrapers/realestate.py`, `.env.example`.

- [x] 写失败测试：黏性会话、预算截止、敏感头不直传、同址补缺且保留来源、房号差异不合并。
- [x] 实现澳洲参数代理配置、运行及失败重试时轮换、受限重试、资源分流和逐请求流量统计。
- [x] Domain 先抓列表/缺失详情；Real Estate 保留列表来源，仅对缺失详情补抓。缓存限定时效。
- [x] 用真实浏览器对悉尼/墨尔本各一页做小样本，记录解析结果、出口地区、错误与供应商用量。

## Task 3: 服务器部署与验收

**Files:** 新运行脚本、部署说明、`docs/reports/2026-10-27-scraper-recovery.md`.

- [x] 取回原始源码及配置键名，备份原启动文件和数据库；不复制含密钥的历史计划到 Git。
- [x] 上传通过验证的版本到独立 release 目录，复用服务器原环境和新代理私有配置。
- [x] 手动执行受限任务，核对退出码、日志、CSV、SQLite 新记录；确认失败不会误报成功。
- [x] 把有预算上限、加锁、正确退出码的命令接回现有 cron，并检查下一次执行设置。
- [x] 留存真实完成范围、费用、剩余障碍和回滚入口，清理无用临时文件。

## Task 4: 把站内脚本也移出澳洲代理

- [x] 逐请求记录一次真实 Real Estate 页面加载，核对付费请求的主机、类型和传输量，不记录 Cookie、认证头或 URL 查询参数。
- [x] 验证站内应用脚本和验证 SDK 都能普通网络 HTTP 200 下载；一页 25 条房源的脚本直连实验成功。
- [x] 写失败测试后实现同源 script / stylesheet 直连，文档和站点验证请求仍用 AU；静态资源失败不回退付费代理。
- [x] 用仓库实际代码验证悉尼、墨尔本列表及各一个详情，记录普通/代理传输占比。
- [x] 完成 MFA 后部署并复测；39 项服务器测试通过。两站两地得到 90 条；Domain 的两次详情 403 经复用列表浏览器修复，另一次服务器验收 40 条、2 详情、0 代理流量、真实退出 0。

## Task 5: 覆盖页面外的浏览器后台流量

- [x] 对供应商扣量与页面统计的差异做真实 TCP 中继记录，定位后台过滤规则、更新和视频组件下载。
- [x] 实现 loopback HTTP/CONNECT 出口，只让房源站点与出口校验走 AU，其余后台连接走普通网络，保持 TLS 验证。
- [x] 把出口校验也纳入中继计量；为全局预算增加传输层字节和主机记录。
- [x] 29 项本地测试通过；真实合并两地验收 90 条、4 个详情、2 条跨站复用，中继付费主机仅有房源站点及 IP 校验，进程退出 0。
- [x] 旧采集进程已查明不存在；出口修复已部署。服务器新出口试跑付费中继 773877 字节，付费主机仅 Real Estate 及 IP 校验；供应商余量下降约 1.38 MB。

## 用户更新的执行范围（2026-10-27 晚间）

- 两站均覆盖整个 Greater Sydney 与 Greater Melbourne；先做多个区域试跑并核对指标，再全量。
- 本轮累计代理购买上限 USD 1.50，包含已购买 USD 0.30；不启用自动续费。
- 后续只推送到 fork `fisher-byte/qrent`；本地 upstream push URL 已禁用。fork 的 GitHub Actions 已禁用，检查、打包、部署从本机执行。
- 用户已确认长期专用公钥；`qrent-prod` 通过严格主机校验直连原服务器，关闭转发，私钥仅在本机。公钥安装脚本已从服务器移除。
- 首次全量记录实际范围、分页结束或截断原因、两站各自数量、付费出口字节和供应商扣量；报告首跑与日常增量成本。
- 节流模块 0405db0 已于服务器 22:03 部署，29 项服务器测试通过；备份 `/root/qrent-scraper/backup/pre-egress-20261027_220341`。
- 两站两地验收进程 PID 4014764，独立后台运行，真实退出码写入 `output/egress-acceptance/exit-code`；未观察到终止前不重启。

## Task 6: 全都会区与本地部署

- [x] 创建 fork，禁用 fork Actions 和本机 upstream 推送；建立 SSH 本地部署、加锁备份、失败回滚及独立后台进程状态工具。
- [x] 校验站点官方区域 ID 与搜索数据；按结果窗口分段，绑定断点规划指纹，校验结构化搜索 ID 和总量；独立审查后的关键回归通过。
- [x] 完成两城两站多区域 pilot：570 条、实际退出 0、错误 0，供应商扣量 3.69 MB；Real Estate 450 条描述/日期/图片链接齐全，邮编缺失 0；Domain 实际详情 2 个成功。
- [ ] pilot 通过后执行全量，验证实际终止状态、分段覆盖、两站数据和原库写入；更新正式每日任务。
- 2026-10-28 00:23 已启动 `metro-full-20261028`，PID 4050859，付费传输上限 300 MB、超时 18h。Domain 新会话首屏 403 待修复，Real Estate 继续运行；不得把启动和部分结果称为全量完成。
- 正式入口已于 00:18:48 切换至 `ops/run_metro.sh`，原 cron 保留，实际持锁测试退出 75。新增州名/排序和增量回归后，本机与服务器 54 项测试通过，独立复核允许全量试验。
- 01:45 已部署 8255ed8，73 项本机/服务器测试通过；Domain 原生网络独立候选取得两城各 60 条、2 实际详情、错误 0、退出 0、付费字节 0。新增有界公开静态缓存和原生网络被动计数。
- 01:43 为切换版本主动 SIGINT，实际退出 130，保留 3,787 条 REA / 163 页断点；01:48 以 PID 4078332 续跑，优先 Domain。同期供应商余量 823.69 MB，无新增购买。

## Task 7: 大规模 Domain 的普通网络补采

- [x] 实际放大后发现：Domain 原生小样本成功，服务器连续约 1,850 条后多个价段和墨尔本返回 403；Lite AU/US 有界验证也未解决，不追加购买。正常 Residential 公开最小套餐超过本轮预算。
- [x] 02:27 启动本机普通网络 Domain-only 全量，PID 25099 / child 25100；独立 SQLite 和断点，付费来源显式只保留 Real Estate，Domain 代理计数为 0。服务器继续采集 Real Estate。
- [ ] 验证本机两城完整查询树、真实进程退出码和字段后，安全导入服务器；来源和目标均须通过范围指纹、覆盖、真实结束状态和锁检查，在线备份后 upsert。
- [ ] 导入仅准备 Domain 检查点，仍由主 runner 显式续跑复核、导出并建立基线；不得凭导入或局部样本标记全量成功。

## Task 8: 开源指纹浏览器与公开搜索 JSON

- [x] 用户明确要求沿开源指纹浏览器方向继续；比较 Camoufox、Patchright、CloakBrowser，选 Apache-2.0 Patchright 作独立对照，不购买浏览器服务。
- [x] 本机 Patchright + 已安装 Chrome、普通网络、首页预热，两城各 2 页均 200，80 条真实结构化 ID 与解析一致。服务器官方 Chrome for Testing 156.0.8078.73 + Patchright：首个列表导航 403，但同会话公开 JSON GET 返回 200；其余 3 列表导航为 200。
- [x] JSON 实际含 props.listingsMap / listingSearchResultIds / totalListings / pageViewMetadata.searchRequest；服务端样本 20 条全部解析且 ID 一致。新增可选真实 JSON 输入、原有元数据校验、单会话复用与旧失败父节点修复，不以页面 200 代替完整覆盖。
- [x] Real Estate 复用刚验证的 AU 出口 IP 给 Camoufox，避免第二次 geoip 自动查 IP 超时；原始 IP 不写结果报告。
- [x] 62e0436 已部署，91 项本机/服务器测试通过。服务器独立 Domain JSON 两城试验 399 条/20 页/实际退出 0/错误 0/付费请求连接与字节 0；本机同入口也 399 条且退出 0。
- [x] 复核补上根查询意外价格/卧室/额外地区/未知限制拒绝，以及旧 Domain 入口 JSON 分页兼容；不以过滤后总量冒充完整都会区。
- [ ] 显式续跑主全量并复核剩余 Real Estate 失败价段，完整后验证每日增量。
