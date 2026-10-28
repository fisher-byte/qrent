# Qrent 采集恢复与都会区验收（2026-10-27—28）

## 当前结论

服务器代码、专用 SSH 和本机部署已恢复。两城多区域试跑真实退出 0，取得 570 条，供应商实际扣量约 3.69 MB。放大后 Domain 出现持续 403，首次都会区扫描于 2026-10-28 05:22 实际退出 1，保存 32,987 条；仍属部分完成，不能把试跑称为全量验收。

2026-10-28 00:23 启动 `metro-full-20261028`，独立 worker PID 4050859，300 MB 传输上限、18 小时超时。初期 Domain 首屏 403；后续使用原生 Firefox 网络完成独立验收并恢复直连采集。全量结束前不会建立有效基线，也不会报告全量成功。01:43 主动中断旧进程（真实退出 130）切换版本，保留 3,787 条 Real Estate / 163 个已存列表页；01:48 用 worker PID 4078332 从断点续跑，优先处理 Domain。开始前供应商余量为 849.32 MB（排序核验另消耗约 0.21 MB）。

该次结果：Real Estate 悉尼 17,296 / 墨尔本 13,841，Domain 悉尼 1,850 / 墨尔本 0，累计 1,575 列表页、付费中继 225,812,855 bytes。Real Estate 描述、可入住日期、图片链接均完整，邮编缺失 0；仍有墨尔本失败价段与根覆盖缺口。Domain 在本机 Camoufox 和服务器原生 Firefox 放大后都失败，Lite AU/US 有界诊断也未解决。结果没有建立完整基线。

## 开源指纹浏览器后续实验

用户要求继续沿开源指纹浏览器方向。保留 [Camoufox](https://github.com/daijro/camoufox)（MPL-2.0），新增可选 [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)（Apache-2.0），不购买浏览器服务。Patchright 使用其官方推荐的持久化、headful Chrome、no_viewport 配置，不拼接 UA 或自定义浏览器语言。浏览器工具宣传的检测成绩不作为 Domain 验收证据。

本机 Patchright 1.64.3、已安装 Chrome、普通本地代理、首页预热后，悉尼和墨尔本各 2 页均为 200，80 条结构化 ID 与解析完全一致。服务器使用官网下载的 Chrome for Testing 156.0.8078.73：首页 200，首个悉尼列表导航 403，但同一浏览器内按公开前端的 `Accept: application/json` GET 原搜索 URL 返回 200；下一页及墨尔本两页导航均为 200。这些同时改变了驱动、Chrome 版本和预热，尚不能认定单个指纹字段是原因。

服务器 JSON 样本真实包含 `props.listingsMap`、`listingSearchResultIds`、`totalListings`、`totalPages`、`currentPage` 和 `pageViewMetadata.searchRequest`；20 条全部解析、ID 一致、价格和邮编缺失 0。代码直接校验真实 JSON 的范围、筛选、排序和分页，不合成页面或元数据；仅 Domain 普通原生网络允许启用，403 与损坏响应仍失败。

06:27 服务器主 runner 独立 JSON 试跑共 399 条（悉尼 200、墨尔本 199）、20 页，实际退出 0、错误 0、付费请求/连接/字节均 0；普通响应记录 4,151,043 bytes，描述和入住日期留待跨站补全。本机同一入口也实际退出 0，399 条 / 20 页 / 付费 0。源码 62e0436 已通过本机和服务器 91 项测试；随后复核补上根查询不能接受意外价段、卧室、额外地区或其他限制的校验，以及旧入口 JSON 分页兼容。全量仍需续跑验证。

02e3817 已部署并通过本机/服务器 93 项测试。06:42 用 worker 4170986 续跑原主任务，保留既有价段；悉尼 Domain 已连续推进，但墨尔本根查询仍有 403，不能宣称整体稳定。新会话不一定能复现一轮成功，浏览器配置、会话与出口需分别验证。

| 后续开源实验 | 实际结果 | 费用与边界 |
| --- | --- | --- |
| Camoufox 0.5.8 / Firefox 156，固定内置 Linux preset、排除默认 uBO、保留 preset 语言 | 隔离源码 dd26476 的主 runner 两城 20 页 / 397 条，真实退出 0、错误 0；独立全量继续运行 | AU 请求/连接/字节均 0；源码 94 项本机及隔离服务器测试通过 |
| [Apostate v0.5.1](https://github.com/heretic-tech/apostate) / Chromium 155，Patchright 驱动原始 binary、固定 seed / Linux persona | 两城各 2 页，80 条真实元数据及 ID 校验通过，真实退出 0 | 5,826,047 普通响应字节，付费 0；GPL-3.0；不使用 SDK/Widevine。观察到时区 Etc/Unknown，未证明长期稳定 |

Apostate 使用官方发布归档，SHA-256 `351955271b7ba1aed314bf14c2d64f6bd59bfdcb5c4a1541c9c921c570ff97bb` 已核验。其他候选的“全部检测通过”宣传不作本项目验收依据。

Camoufox 独立全量只写自己的 SQLite 与断点，源库和主库分开；只有真实完整结束、范围/覆盖/行身份通过、付费 0 时才允许导入，导入后仍须主 runner 复核建立基线。07:00 时该任务已过 100 页、1,891 条，错误 0、AU 字节 0；主任务 Domain 悉尼 8,873 条。两者均未在此时完成。

主任务第三阶段于 07:28 前后实际退出 1：Real Estate 悉尼 17,296 / 墨尔本 14,892，Domain 悉尼 13,150 / 墨尔本 0，累计 2,283 页、付费中继 237,993,257 bytes。剩一个 Real Estate 价段拦截；Northern 根覆盖 2,739/2,741；悉尼 Domain 旧根计数 13,150/13,180。仍未建立完整基线。新增父页刷新逻辑使用真实新响应：只有所有后代已完成、ID union 达到 min(first_total, fresh_total) 才保留成功子树，避免因市场总量下降重抓整个成功分区；真实缺口仍重试，102 项本机测试通过。

最新一次 AU 原生浏览器对照的 750 KB 硬上限在首页后耗尽，搜索未取得响应，因此属于预算中止，不能当作 AU 成功或 403 证据。另一次诊断被出口规则分到普通流量，已标记 invalid_routing_probe，仅 IP 检查走 AU。两次合计付费中继 757,504 bytes，来自已有额度，无新增购买。

## 仓库与部署

- 当前维护 fork：[fisher-byte/qrent](https://github.com/fisher-byte/qrent)。后续只推送此 fork；本机 upstream 推送已禁用，fork 的 GitHub Actions 已禁用。
- 原仓库 [wiperi/qrent](https://github.com/wiperi/qrent) 是 monorepo，后端在 `packages/backend`，未发现另一个对应的可访问后端仓库。
- [Real Estate](https://github.com/fisher-byte/qrent/blob/main/packages/scraper/src/scrapers/realestate.py)、[Domain](https://github.com/fisher-byte/qrent/blob/main/packages/scraper/src/scrapers/domain.py)；都会区入口 `packages/scraper/metro_runner.py`。
- 腾讯云生产实例（`VM-0-10-tencentos`）：原目录 `/root/qrent-scraper`，release `/root/qrent-scraper/releases/recovery-20261027`，原库 `/root/qrent-scraper/properties.db`。
- 此部署写 SQLite；网站主后端使用 MySQL。不能把本次 SQLite 入库称为网页数据库已经同步。

原说明提到的 `packages/tt.scraper-v2` 没有提交，且根目录 `tt*` 规则会忽略它。实际停运的独立代码已从服务器取回，未丢失。

## 修复内容

旧 cron 和代码仍在，旧 HK gost/SSH 代理返回 `ERR_TUNNEL_CONNECTION_FAILED`。旧 shell 经 `tee` 丢失退出码，0 条也会显示完成。新流程保留原库，按来源和房源 ID upsert；失败、空页、预算耗尽和未完成范围会返回非零，保留断点，不下架未扫描的历史记录。

- Domain 使用原生 Firefox 直连，保留正常 HTTP 缓存与资源加载；通过被动事件记录请求和字节，仅未代理模式允许启用。Real Estate 关键页面与验证请求走 AU 住宅代理。每个付费查询验证澳洲出口，并使用同一个已验证的黏性会话。
- Real Estate 脚本和样式走无认证普通网络，图片、字体、视频不下载，保留图片链接；Domain 原生模式的资源使用普通网络。普通请求失败不会转付费补拉。
- Real Estate 的公开脚本/样式增加单浏览器内存缓存（32 MB / 128 项 / 单项 4 MB / TTL 最多 300 秒），尊重缓存禁令、重取、Cookie 与 Vary；命中仍检查预算，另记 cache_hits / cache_saved_body_bytes。服务器两页 50 条对照记录付费中继 360,370 bytes、59 次命中，避免重复的解码响应体 7,684,588 bytes；第二页 9.93 秒，旧样本中位 36.79 秒。解码响应体不是准确账单字节。
- loopback HTTP/CONNECT 出口也统计浏览器后台连接，仅房源目标和小型 IP 校验走付费。过滤规则、更新、视频组件走普通网络。`proxy_wire_bytes` 与页面计数分别记录，预算同时约束两项，供应商读数另行核对。
- Domain 详情复用同一次列表浏览器，修复切换浏览器指纹后出现的 403。Real Estate 的 `Vic` 等州名统一转大写后校验。
- Real Estate 列表内嵌数据已有完整描述、可入住日期、图片链接与位置，通常无需额外详情请求。两站保留各自 ID 和链接，同地址含房号、价格、卧室相同的近期完整信息可复用，隐藏地址不跨站匹配。
- 旧库可能只有基础表详情而 enrichment 缺行或字段为空。读写现按“新非空值 → 旧 JSON → 旧 SQL”保留正文、中译、关键词、图片和入住日期，避免基础表有数据而导出读空；七日跨站规则与身份校验保持。修复已通过 109 项本机测试，待正在运行的采集结束后部署；不批量改写旧库。
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
| Domain 原生网络独立候选 | Domain 两城各 60、实际详情 2 | 退出 0，错误 0 | 0 bytes |
| `metro-pilot-20261027` | 见下表 | 修复州名后续跑，退出 0，错误 0 | 3,690,740 bytes |

试跑于 2026-10-28 00:15:40 完成，24 个列表页，Domain 实际详情 2 个成功。初次运行因 `Vic` 州名格式失败，修复后只续跑失败区域，成功页没有重抓；一次 429 在有界重试后恢复。供应商和传输总量包含这些失败尝试。

| 来源 | 悉尼 | 墨尔本 | 字段质量 |
| --- | ---: | ---: | --- |
| Domain 直连 | 60 | 60 | 邮编缺失 0，图片链接 119/120；描述 27/120（含跨站补全） |
| Real Estate AU | 225 | 225 | 描述、可入住日期、图片链接均 450/450，邮编缺失 0 |

付费出口仅 `www.realestate.com.au`、`ipinfo.io`、`api.ipify.org`；普通页面下载 182,823,912 bytes，普通后台中继 33,096,979 bytes。页面与中继是不同层次的计数，不能直接相加后称为准确账单传输。

本机和服务器于 01:45 通过 73 项采集专项测试（部署源码 8255ed8），包括真实本地 TCP 出口、认证隔离、配额、资源关闭、地区/分页/总量校验、续跑、字段保留、排序、增量早停、真实本地 HTTP 静态缓存及原生网络计数。保存的真实两站排序页面也通过新元数据校验。

## 费用与流量

只购买了一次性 Residential Lite 1GB，实付 **USD 0.30**、无自动续费。本轮累计购买上限 **USD 1.50**，包含这 USD 0.30。现有额度足够先跑全量，没有为了用完预算继续购买。

| 阶段 | 供应商剩余量 | 该阶段实际扣量 |
| --- | ---: | ---: |
| 多区域试跑开始前 | 853.22 MB | — |
| 初次州名失败后 | 851.07 MB | 2.15 MB |
| 修复续跑完成后 | 849.53 MB | 试跑累计 3.69 MB |

按 USD 0.30/GB 折算，试跑消耗的额度价值约 **USD 0.0011**；这不是新增付款。传输记录不能代替供应商最终扣量。

以下为试跑推算，尚非全量/整日实测：

| 模式 | AU 流量估计 | 额度价值估计 |
| --- | ---: | ---: |
| 首次两城全量 | 150–300 MB | USD 0.045–0.09 |
| 普通一天增量 | 20–60 MB | USD 0.006–0.018 |

估算使用每页约 0.17–0.21 MB（含启动、出口校验和试跑失败开销）、两站分页数及每区至少 3 页已知记录。市场变化、区域重叠、验证码或重试会改变实际消耗。全量命令记录上限 300,000,000 bytes；未完成时明确失败，保留断点。最终全量实际数字在完成后补记。

全量第一阶段和同期诊断的供应商余量从 849.32 MB 变为 **823.69 MB**，同期扣量 **25.63 MB**。主任务中继记录 25,315,703 bytes；另两次有界 Domain 诊断共记录 328,909 bytes，均未解决 403，因此正式版本仍保持 Domain 直连。01:48 之后 Domain 已继续分页，AU 计数保持不变。上述消耗来自已有额度，没有新增付款。

第二阶段结束后刷新供应商页面，余量 **622.34 MB**，相对全量开始累计扣量约 **226.98 MB**；主任务中继 225,812,855 bytes，独立有界诊断与缓存试验另 1,209,856 bytes。显示取整与计数层不同，差约 0.05 MB。按套餐价格折算该阶段总用量约 USD 0.068，累计实付款仍 USD 0.30。尚未取得完整扫描或整日增量实测。

## SSH、部署、定时任务与回滚

用户已单独确认长期公钥。专用别名 `qrent-prod` 使用严格主机校验、BatchMode 和独立密钥；服务器授权行带 `restrict`，禁用端口和代理转发。私钥仅留本机 `~/.ssh/qrent_ops_ed25519`，不进入 Git。多次 SSH/SCP 已成功，部署运行不依赖腾讯云网页会话或本机网络转发。此前临时恢复公钥已删除；此次专用公钥按用户授权保留。

从仓库根目录运行 `.venv/bin/python packages/scraper/ops/deploy_recovery.py`：本机检查、源码哈希打包、SSH 上传、服务器独占锁、在线数据库备份、原文件备份、测试失败回滚。工具拒绝未记录的远程源码漂移，并写入独立部署回执和 `DEPLOYMENT.json`。

2026-10-28 00:18:48 已把 `/root/qrent-scraper/run_scraper.sh` 切换为 `ops/run_metro.sh`，保留每日 **02:00 Asia/Beijing** cron。入口语法通过，实际持锁运行返回 **75**，证明另一个采集在跑时不会重叠。每日最长 18 小时，付费传输记录上限 300 MB；首次全量运行时若碰到 02:00 任务，后者会因锁跳过。

**03:00** 在线备份使用 SQLite backup API 包含 WAL 已提交数据，**03:10** 只清理备份根目录 30 天前普通 `.db`，回滚子目录不受影响。crond 已确认 active。

10 月 28 日真实 cron 已在主任务持锁时退出 75；03:00 备份已实际生成 66,482,176 bytes 的 `properties_20261028.db`，日志记录路径，未依赖手动触发。

数据增长后，主库约 229 MB、根盘余量约 3.9 GB，原先 30 份未压缩备份会超出空间。新增兼容的 `ops/backup_sqlite.py --gzip`：在线复制含 WAL 的一致快照，quick_check 后流式压缩、0600、原子替换，失败保留上个有效备份。一次真实只读试验约 229.4 MB → 37.0 MB（节省 83.87%），7.63 秒；解压恢复后 quick_check 和两表行数核验通过，临时副本已清理。100 项本机测试通过；此处记录的是候选实测，cron 的正式切换待主任务释放部署锁后执行。

07:33 已部署 550a94b，102 项本机及服务器测试通过。随后正式备份 cron 改为 `--gzip`，30 日清理同时覆盖 `.db` 与 `.db.gz`；各命令 `/bin/sh -n` 与只读清理 dry-run 通过。正式目录产物为 39,931,515 bytes，解压 246,202,368 bytes、quick_check=ok，properties 46,966 / enrichment 45,446，与静止原库一致。旧未压缩备份仍保留。

Domain 的固定 preset / no-uBO 已提升至主任务配置，preset 独立保存在私有 state 目录；原环境和 cron 已备份。07:37 worker 4188452 续跑，悉尼根最新实际总量 13,161、覆盖 13,167，通过真实父页刷新保留成功子树；墨尔本开始持续入库，仍使用普通网络。两城完整结束和最终流量继续核验。

08:12 左右第四阶段实际退出 1，保存 REA 17,296 / 14,913、Domain 13,167 / 12,058，累计 2,938 页、付费中继 238,245,752 bytes。Domain 已遍历两城，剩墨尔本总量校验差 16 条；REA 同一 Melbourne City 高价段仍被拦截，故未建立完整基线。新的有界重试在 REA 403 时创建新黏性会话，先重新验证 AU 并更新 GeoIP，再重试同一页；不使用未验证出口。111 项本机测试通过。

代理池另做 3 会话×2 次真实国别查询：获得 3 个不同出口，每个会话内 IP 相同，6 次均 AU。匿名指纹的盐不保存；共 40,461 付费中继字节，无新购买。它证明本次短时黏性及换会话轮换，不代表已经测试完整 30 分钟周期。

日志复核发现该 REA 价段早期为 403，最近两次为 429 后短重试空响应。重试补充 429 的 30 秒冷却，再换已验证 AU 会话，仍最多一次。112 项本机测试通过。独立原生浏览器探针只在首页遇到 429，实际目标请求 0，付费中继 457,974 bytes；该结果不能当作目标价段试验结果，后续直接目标对照仍计入同一 3 MB 上限。

- 首次恢复备份：`/root/qrent-scraper/backup/pre-recovery-20261027`。
- 出口修复备份：`/root/qrent-scraper/backup/pre-egress-20261027_220341`。
- 正式入口备份：`/root/qrent-scraper/backup/pre-metro-entrypoint-20261028_001844`。
- 每次源码部署另有 `backup/pre-source-*`，测试失败自动回滚源码，保留数据库和私有 `.env`。

`remote_capture.py status --campaign NAME` 同时读取 PID+启动时钟、`process-status.json` 和应用报告。`start --resume` 只有旧进程终止后显式使用，断点绑定范围和规划指纹，避免漏采或重复启动。

原始结果、供应商读数、逐次退出码与日志保留在服务器 output 及本机被忽略的 `packages/scraper/output/recovery-20261027`，不公开 Cookie、代理认证或密钥。
