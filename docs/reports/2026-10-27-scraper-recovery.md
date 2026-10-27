# Qrent 采集恢复与都会区验收（2026-10-27—28）

## 当前结论

服务器代码、专用 SSH、本机部署和两站采集已恢复。两城多区域试跑真实退出 0，取得 570 条，供应商实际扣量约 3.69 MB。首次整个都会区扫描尚待完成，不能把这个试跑称为全量验收；下面分别列出已验证结果与后续全量状态。

2026-10-28 00:23 启动 `metro-full-20261028`，独立 worker PID 4050859，300 MB 传输上限、18 小时超时。Real Estate 正在翻页；Domain 新会话首屏返回 403，两个查询保留失败状态，继续排查普通直连路径。全量结束前不会建立有效基线，也不会报告全量成功。开始前供应商余量为 849.32 MB（排序核验另消耗约 0.21 MB）。

## 仓库与部署

- 当前维护 fork：[fisher-byte/qrent](https://github.com/fisher-byte/qrent)。后续只推送此 fork；本机 upstream 推送已禁用，fork 的 GitHub Actions 已禁用。
- 原仓库 [wiperi/qrent](https://github.com/wiperi/qrent) 是 monorepo，后端在 `packages/backend`，未发现另一个对应的可访问后端仓库。
- [Real Estate](https://github.com/fisher-byte/qrent/blob/main/packages/scraper/src/scrapers/realestate.py)、[Domain](https://github.com/fisher-byte/qrent/blob/main/packages/scraper/src/scrapers/domain.py)；都会区入口 `packages/scraper/metro_runner.py`。
- 腾讯云 `139.199.73.159`（`VM-0-10-tencentos`）：原目录 `/root/qrent-scraper`，release `/root/qrent-scraper/releases/recovery-20261027`，原库 `/root/qrent-scraper/properties.db`。
- 此部署写 SQLite；网站主后端使用 MySQL。不能把本次 SQLite 入库称为网页数据库已经同步。

原说明提到的 `packages/tt.scraper-v2` 没有提交，且根目录 `tt*` 规则会忽略它。实际停运的独立代码已从服务器取回，未丢失。

## 修复内容

旧 cron 和代码仍在，旧 HK gost/SSH 代理返回 `ERR_TUNNEL_CONNECTION_FAILED`。旧 shell 经 `tee` 丢失退出码，0 条也会显示完成。新流程保留原库，按来源和房源 ID upsert；失败、空页、预算耗尽和未完成范围会返回非零，保留断点，不下架未扫描的历史记录。

- Domain 直连；Real Estate 关键页面与验证请求走 AU 住宅代理。每个付费查询验证澳洲出口，并使用同一个已验证的黏性会话。
- 所有脚本和样式走无认证普通网络；图片、字体、视频不下载，保留图片链接。普通请求失败不会转付费补拉。
- loopback HTTP/CONNECT 出口也统计浏览器后台连接，仅房源目标和小型 IP 校验走付费。过滤规则、更新、视频组件走普通网络。`proxy_wire_bytes` 与页面计数分别记录，预算同时约束两项，供应商读数另行核对。
- Domain 详情复用同一次列表浏览器，修复切换浏览器指纹后出现的 403。Real Estate 的 `Vic` 等州名统一转大写后校验。
- Real Estate 列表内嵌数据已有完整描述、可入住日期、图片链接与位置，通常无需额外详情请求。两站保留各自 ID 和链接，同地址含房号、价格、卧室相同的近期完整信息可复用，隐藏地址不跨站匹配。
- 千问从失效的 `qwen-plus-1220` 改为环境可配置的 `qwen-plus`，真实调用和单条评分成功。全量评分需显式启用，当前不批量调用模型。
- 使用 Python 3.11、Camoufox 0.5.8、Playwright 1.62.0、DashScope 2.0.2。代理会话设计参考恢复的 Qrent LycheeIP/gost 代码；千问配置私下复用本机 `pdftomd` 的 DashScope 配置，均未提交凭据。

## 两城范围与增量

Domain 使用官方 Sydney Region NSW / Melbourne Region VIC 搜索。Real Estate 使用官方联想接口校验的悉尼 19 个、墨尔本 19 个区域组，清单在 `src/config/metro_regions.json`。部分 Real Estate 区域组包含相邻边缘市场；这是平台搜索范围，不能称为精确 ABS 行政边界。每条保留实际城区、州、邮编。

旧 Real Estate `sydney - greater region` 搜索会解析为 CBD，不能覆盖悉尼都会区。新流程校验官方位置 ID，Domain 50 页或 Real Estate 80 页的结果窗口不足时递归分价段；按来源 ID 去重，检查结构化页面 ID、每个叶查询及根查询的唯一总量，缺口未解决就不建立全量基线。

实际页面确认 Domain `DateUpdated/Descending`、Real Estate `new-desc` 排序，代码拒绝站点忽略排序的页面。每日入口 `auto` 在没有有效基线或基线超过 7 天时做全量；其他日期遇到连续 3 页已知且基础字段未变才早停。Real Estate 按发布时间排序，旧房源的全部字段变动由每周全量刷新覆盖；不能宣称每日增量覆盖所有旧房源修改。

## 已完成的真实验收

第一轮恢复曾向原库累计新增 112 条，总量 1,962 → 2,074，活跃 407 → 519，数据库与在线备份完整性检查通过。第一次合并应用报告为成功，但原 SSH 最终超时退出 255，不能把应用报告当作远程命令退出码。后续独立正式入口均实际观察到退出 0。

| 服务器验收 | 两站/两城数量 | 实际进程结果 | 付费中继传输 |
| --- | --- | --- | ---: |
| `egress-acceptance` | Domain 40、Real Estate 50 | 退出 1；2 个 Domain 详情 403 | 773,877 bytes |
| `domain-session-acceptance` | Domain 40、实际详情 2 | 退出 0，错误 0 | 0 bytes |
| `metro-pilot-20261027` | 见下表 | 修复州名后续跑，退出 0，错误 0 | 3,690,740 bytes |

试跑于 2026-10-28 00:15:40 完成，24 个列表页，Domain 实际详情 2 个成功。初次运行因 `Vic` 州名格式失败，修复后只续跑失败区域，成功页没有重抓；一次 429 在有界重试后恢复。供应商和传输总量包含这些失败尝试。

| 来源 | 悉尼 | 墨尔本 | 字段质量 |
| --- | ---: | ---: | --- |
| Domain 直连 | 60 | 60 | 邮编缺失 0，图片链接 119/120；描述 27/120（含跨站补全） |
| Real Estate AU | 225 | 225 | 描述、可入住日期、图片链接均 450/450，邮编缺失 0 |

付费出口仅 `www.realestate.com.au`、`ipinfo.io`、`api.ipify.org`；普通页面下载 182,823,912 bytes，普通后台中继 33,096,979 bytes。页面与中继是不同层次的计数，不能直接相加后称为准确账单传输。

本机和服务器通过 54 项采集专项测试，包括真实本地 TCP 出口、认证隔离、配额、资源关闭、地区/分页/总量校验、续跑、字段保留、排序和增量早停。保存的真实两站排序页面也通过新元数据校验。

## 费用与流量

只购买了一次性 Residential Lite 1GB，实付 **USD 0.30**、无自动续费；账户余额 **USD 2.70**。本轮累计购买上限 **USD 1.50**，包含这 USD 0.30。现有额度足够先跑全量，没有为了用完预算继续购买。

| 阶段 | 供应商剩余量 | 该阶段实际扣量 |
| --- | ---: | ---: |
| 多区域试跑开始前 | 853.22 MB | — |
| 初次州名失败后 | 851.07 MB | 2.15 MB |
| 修复续跑完成后 | 849.53 MB | 试跑累计 3.69 MB |

按 USD 0.30/GB 折算，试跑消耗的额度价值约 **USD 0.0011**；这不是新增付款。不要把余额 USD 2.70 当作代理剩余 GB，也不要把传输记录代替供应商最终扣量。

以下为试跑推算，尚非全量/整日实测：

| 模式 | AU 流量估计 | 额度价值估计 |
| --- | ---: | ---: |
| 首次两城全量 | 150–300 MB | USD 0.045–0.09 |
| 普通一天增量 | 20–60 MB | USD 0.006–0.018 |

估算使用每页约 0.17–0.21 MB（含启动、出口校验和试跑失败开销）、两站分页数及每区至少 3 页已知记录。市场变化、区域重叠、验证码或重试会改变实际消耗。全量命令记录上限 300,000,000 bytes；未完成时明确失败，保留断点。最终全量实际数字在完成后补记。

## SSH、部署、定时任务与回滚

用户已单独确认长期公钥。专用别名 `qrent-prod` 使用严格主机校验、BatchMode 和独立密钥；服务器授权行带 `restrict`，禁用端口和代理转发。私钥仅留本机 `~/.ssh/qrent_ops_ed25519`，不进入 Git。多次 SSH/SCP 已成功，部署运行不依赖腾讯云网页会话或本机网络转发。此前临时恢复公钥已删除；此次专用公钥按用户授权保留。

从仓库根目录运行 `.venv/bin/python packages/scraper/ops/deploy_recovery.py`：本机检查、源码哈希打包、SSH 上传、服务器独占锁、在线数据库备份、原文件备份、测试失败回滚。工具拒绝未记录的远程源码漂移，并写入独立部署回执和 `DEPLOYMENT.json`。

2026-10-28 00:18:48 已把 `/root/qrent-scraper/run_scraper.sh` 切换为 `ops/run_metro.sh`，保留每日 **02:00 Asia/Beijing** cron。入口语法通过，实际持锁运行返回 **75**，证明另一个采集在跑时不会重叠。每日最长 18 小时，付费传输记录上限 300 MB；首次全量运行时若碰到 02:00 任务，后者会因锁跳过。

**03:00** 在线备份使用 SQLite backup API 包含 WAL 已提交数据，**03:10** 只清理备份根目录 30 天前普通 `.db`，回滚子目录不受影响。crond 已确认 active。

- 首次恢复备份：`/root/qrent-scraper/backup/pre-recovery-20261027`。
- 出口修复备份：`/root/qrent-scraper/backup/pre-egress-20261027_220341`。
- 正式入口备份：`/root/qrent-scraper/backup/pre-metro-entrypoint-20261028_001844`。
- 每次源码部署另有 `backup/pre-source-*`，测试失败自动回滚源码，保留数据库和私有 `.env`。

`remote_capture.py status --campaign NAME` 同时读取 PID+启动时钟、`process-status.json` 和应用报告。`start --resume` 只有旧进程终止后显式使用，断点绑定范围和规划指纹，避免漏采或重复启动。

原始结果、供应商读数、逐次退出码与日志保留在服务器 output 及本机被忽略的 `packages/scraper/output/recovery-20261027`，不公开 Cookie、代理认证或密钥。
