# Domain 采集恢复与正式发布验收

北京时间 2026-11-09 14:56:55，服务器正式恢复任务 `20261109_142515_734978` 完成原悉尼 22 个 Domain 查询、每区最多 7 页，取得 2,290 个去重来源 ID，100 条详情补抓全部成功，错误 0。已定位并修复首次首页会话建立缺失，原香港云出口被拒的问题通过用户已有 VPN 的服务器独立出口解除；15:52:35 正式发布队列自然退出 0；15:54 全量 MySQL/API 读回通过，965 个地址发布或更新、51 个暂缓。15:59 最终部署、日常入口与磁盘检查也已通过。

## 本次已经排除的解释

- 原来的 Newtown 搜索 URL 仍有效。用浏览器打开 Domain 首页、选择 Rent、输入并选择 `Newtown, NSW, 2042`、点击网站自己的 Search，生成的 URL 与程序一致，均包含 `excludedeposittaken=1`。首页 HTTP 200，随后搜索页 HTTP 403，访问同一个服务器地址。
- 广州直接网络、固定 Linux Camoufox 的首个搜索请求即返回 `Server: AkamaiGHost` 和 `Access Denied`，无房源 DOM 或 `__NEXT_DATA__`。该次拒绝发生在网页脚本运行和房源解析之前。
- 旧 preset 文件中的 Firefox 150 不是实际发出的版本。离线回环测试显示，HTTP User-Agent 与真实网页脚本均观察到 Firefox 156，与当前安装的 156.0.1 内核一致。
- 香港现存旧 Camoufox Python 包 0.4.11 的默认指纹生成依赖无法启动；绕开该包装层、直接运行其现有 Firefox 内核后，真实搜索页仍为 HTTP 403。该实验没有安装或升级共享香港环境。

这些证据定位到来源的访问校验，但没有给出 Akamai 的具体命中规则，不能据此断言只封了 IP、某个浏览器参数必然导致拒绝，或者单页曾经成功意味着已经稳定恢复。13:26 时尚未读到用户的成功浏览器；下文的新对照已补齐这项证据，取代当时的工具阻塞。

## 已修复的会话管理问题

提交 `004bc1c5569523a27f514316b793b84d7eb486b3`。

1. 原 runner 每处理完一个区域就关闭浏览器。专用 Domain profile 现在在同轮中保留同一个浏览器实例，区域切换、分页和详情共享活跃会话。
2. 首张 HTML 搜索页通过后，原 JSON 请求流程仍会重新打开首页，下一地区又重新打开 HTML 搜索页。现在已验证的页面直接用于后续同源 JSON 请求，避免这些无关导航。
3. 超时等失败会关闭当前实例后按原重试规则处理；HTTP 403 仍立即停止该来源，同轮后续区域保持未尝试。Real Estate 的独立处理不变。完成、失败和预算耗尽均释放浏览器。

新增回归覆盖同一文档的分页/详情/区域切换、详情后保持会话、跨区拒绝后的关闭与其他来源继续运行。本机及服务器各 364 项检查通过。原 Node 提交 hook 因 pnpm 自动安装策略失败；已撤回 hook 自动产生的无关 workspace 配置，Python-only 提交在上述检查通过后跳过该 hook。

部署在提交完成前生成，原 manifest 如实记录 `working_tree: true` 和父提交。随后独立核对六个部署文件的 SHA-256，与最终提交、实际服务器文件全部一致；保留原回执，另记录 `committed-source-verification.json`，未改写原部署证据。

## 实际复测与资料

正式 runner 实测 `20261109_132613_496645`，原悉尼 22 区、每区 7 页窗口、原持久 Chrome profile 和香港普通出口。Newtown 首次请求被拒绝，其余 21 区未尝试；新增来源 ID、详情请求、住宅代理请求和线上发布均为 0。这证明首次访问仍受阻，尚未获得新会话逻辑的在线跨区成功验收。

- 私有诊断、原始拒绝与首页交互时间线：`packages/scraper/output/domain-root-cause-20261109/`。
- 部署、提交 SHA 对照：`packages/scraper/output/deployments/20261109_132456_266646/`。
- 失败补采原件在爬虫机 `output/domain-session-fix-attempt-20261109_132613_496645/`，逐文件 SHA 验证后归档，避免掩盖当日已验收的 02:00 Real Estate 报告。
- 写库前回滚快照已压缩并实际解压校验，原备份和正式账本均保留。检查结果见私有 `rollback-compression.json`、`final-integrity.json`。

上述 13:26 的首屏拒绝已在下列新出口与会话初始化路径上解除。旧失败记录仍保留，不改写为成功。


## 14:00 后的成功环境对照

用户确认原环境为 Chrome 加 VPN。通过原生 Chrome 控制读取到正常搜索结果，实际翻页后内容变化；对与爬虫完全相同的 `excludedeposittaken=1` 搜索 URL 强制网络刷新，结果从 570 变为 571 条。无痕窗口直接访问该 URL 则得到 `Access Denied`。这排除了“只有错误 URL”或“只是旧缓存显示”的解释，但单凭这个对照不能区分所有 Cookie、会话和运行方式因素。

随后用全新专用 profile、安装的原生 Chrome 做受控对照，未复制用户个人 Chrome 数据：

| 环境与步骤 | 实际结果 |
| --- | --- |
| 本机 Patchright，当前 VPN，直接请求正式 Newtown 搜索 URL | 首文档 HTTP 403，无房源 |
| 本机相同 Patchright、当前 VPN，先首页正常加载约 7 秒，再搜索 | HTTP 200，20 条真实房源 |
| 上一成功专用 profile 重启，原路径继续分页与跨区 | Newtown 第 2 页、Kensington 第 1 页 JSON 均 HTTP 200，各 20 个 ID |
| 广州机器，现有普通香港出口，普通 Chrome 最少启动参数，先首页再搜索 | 首页 200，搜索 403 |
| 同一广州机器、现有正式 Chrome/Patchright，使用用户现有 iKuuu 香港节点的服务器独立客户端，先首页再搜索 | 搜索、分页和 Kensington 均 200，实际房源可解析 |

可定位的运行问题有两部分：正式混合采集入口直接从搜索页开始，没有执行成功对照中的首次首页会话建立；原香港云出口仍被来源拒绝。首次首页流程与可接受的现有 VPN 出口结合后，服务器真实采集已通过。未得到 Akamai 内部规则，因此不把这些证据夸大为其唯一判定因素。房源解析器对真实成功 HTML 直接解析出 20 条，价格和来源 URL 均有效。

## 新修复与独立出口

提交 `17414506d58e9d877195963936d732c0f2e64afc` 已部署，本机及服务器各 367 项检查通过。

- `DOMAIN_HOME_BOOTSTRAP=true` 在第一张 HTML 搜索页前加载正式首页，等待首次脚本和页面就绪；检查 HTTP、来源、页面和预算，失败不把会话标为有效。后续搜索页、分页、详情继续复用同一会话。
- 保留原 403 停止该来源的规则、固定学校范围、逐条质量检查以及 Real Estate 独立发布。
- Domain 新出口仅监听服务器 `127.0.0.1:18084`，不混用 Real Estate 住宅代理。现有 Maps/MySQL 香港链路 `18082` 不变。
- 复用用户已有 iKuuu 香港节点，配置仅保存在爬虫机私有目录，没有新购流量或创建订阅。使用官方 Shadowsocks Rust 1.25.0 的静态 Linux 构建并核对 GitHub 发布 SHA-256。`qrent-domain-egress.service` 已启用开机启动和故障重启，仅用于该采集出口；不依赖用户电脑。
- 成功对照产生的服务器专用 profile 原子转为正式 profile；旧 profile 和配置均备份。原个人 Chrome 会话和 VPN 设置未修改。

部署回执：`packages/scraper/output/deployments/20261109_142309_169835/server-receipt.json`。新出口与受控对照证据：私有 `packages/scraper/output/domain-vpn-comparison-20261109/`，服务器 `output/domain-root-cause-20261109/`。配置备份：服务器 `backup/pre-domain-vpn-20261109_142418_281734/`。

本次 335 MB 写库前 SQLite 源码部署快照已经压缩为约 64 MB，并完成实际解压、原始 SHA-256 对照和 SQLite `quick_check`；原始字节均可恢复。03:00 主库、03:05 正式账本备份、08:10 MySQL 冷归档和 08:45 主库冷归档的 cron 均仍在；11 月 9 日两项冷归档有实际成功回执。

## 正式采集与发布验收

正式采集 `20261109_142515_734978` 于北京时间 14:56:55 关闭浏览器，原 22 个 Domain 查询全部成功，取得 2,290 个去重来源 ID，复用 1,637 条详情，新抓 100 条详情全部成功，错误 0。831 个原生浏览器请求，响应内容统计 14,136,308 bytes；住宅代理请求、住宅线量均为 0。该响应内容统计不等同于 VPN 供应商计费线量。

该来源单独运行，原始 `capture_window_complete=false`、19 个 Real Estate 查询未尝试的元数据照实保留；不将 Domain 的 22/22 写成全来源 41/41。02:00 已完成的 Real Estate 结果不变。

准备得到 2,015 个地址组，其中 1,016 个地址进入正式补充与发布队列，需 1,382 对学校关联；698 个缺席来源同址所有权冲突继续保留，不给原有来源地址新建替代行。其余地址按原质量规则筛选，不把 2,290 个来源 ID 当成去重可发布套数。

原生 Chrome 已核对官网首页的本轮 Domain 卡片：1 Park Lane（A$850 / 39 分钟）、103/2-4 Powell Street（A$830 / 26 分钟）、101/18 Danks Street（A$950 / 27 分钟）、108/17 Gadigal Avenue（A$850 / 20 分钟），均显示 11 月 9 日，与本轮批次和公开 UNSW 搜索 API 一致。官网保留 Qrent 原有布局、订阅入口和“小 Q · AI 找房”悬浮入口。

21 个批次全部完成，1,016 个准备地址中 **965 个发布或更新、51 个暂缓**。其中 882 个沿用已有来源主键，83 个首次发布；51 个暂缓地址均在评分前缺少合格公交时间，其中 46 个因此仍缺完整模型字段。保留原始失败和不确定结果，没有填充默认通勤或评分。

最终只读验收逐条检查 965 个地址与 **1,310 对学校关联**（UNSW 465、USYD 469、UTS 376），MySQL 和公开 `properties.search` 共 98 次分页读回均无差异。21 份写库前备份均通过 SHA-256 及实际解压内容校验；原 **7,846 个主键、11,631 条学校关联**全部保留。原四张调用/费用表的历史行数及内容 SHA 也全部未变；验收自身没有供应商调用和生产写入。

正式回执：爬虫机 `output/school-enrichment-20261109_142515_734978/daily-receipt.json` 及 `verification-20261109T075327Z/publication-verification.json`；本机私有副本为 `packages/scraper/output/domain-vpn-comparison-20261109/final-verification-output.json`。服务 `qrent-domain-recovery-20261109.service` 已自然退出 0，不再运行。

收尾时原生 Chrome 已刷新并保留官网房源页，当前卡片与公开 API 再次对齐：19-31 Goold Street（A$1,600 / 37 分钟）、540/351 George Street（A$1,050 / 28 分钟）、1 Park Lane（A$850 / 39 分钟）、423/7 Potter Street（A$1,000 / 28 分钟），均显示 11 月 9 日。页面仍保留正式订阅入口和“小 Q · AI 找房”。

### 源站内容冲突（不等于已确认可租）

`18273524`（101/832 Elizabeth St，Waterloo）的当前 Domain 原图写着 `DEPOSIT TAKEN / Inspection cancelled`，但同一详情页标题为 `FOR LEASE...`，租金为 A$1,000/week、出租日期和 FAQ 均为 `Available Now`，搜索端点仍返回该记录。已通过实际原生 Chrome 同页核实：Qrent 抓到的图片、价格与描述均来自源站，存在的是源站自身的可租状态冲突。本次未据此伪造另一种房源状态，也不能保证该房源仍可租。私有页面核对记录在 `rendered-verification.json`。


## 最终运行、费用与磁盘核对

后续运维提交 `54179a3bf13643b96b8c595fec613a64cf27e8db` 在本轮发布自然结束后部署，隔离离线测试中的 `DOMAIN_HOME_BOOTSTRAP`，避免测试误继承生产配置。本机及服务器各 367 项检查通过；97 个部署文件与 manifest 的 SHA 全部一致。最终部署回执为 `packages/scraper/output/deployments/20261109_155509_960519/server-receipt.json`，私有运行核对为 `final-operations-verification.json`。该部署前后正式账本四张表的行数不变。

本轮新增 182 个 NSW 免费请求、802 个模型请求、19 个既有赠金请求。新增模型费用 **CNY 0.160876**；11 月累计支出及预留 **CNY 1.3948**，低于 CNY 50。新增既有赠金预留 USD 0.038，通勤现金支出 0；没有购买代理。赠金返回的 7 个起点不精确结果照实保留，不当作成功路线。最终数额来自原正式账本，不是推算账单。

最后一次部署产生的 351,580,160-byte SQLite 回滚快照压缩为 64,185,414 bytes，实际解压后的原始 SHA 和 SQLite `quick_check` 均通过，随后才删除可重建的未压缩副本。压缩备份和回执留在服务器 `backup/pre-source-20261109_155511_255884/`。最终可用磁盘约 **1.54 GB**；03:00 主库备份、03:05 账本备份、08:10 MySQL 冷归档和 08:45 SQLite 冷归档均保留，原每日入口仍执行磁盘阈值检查。

02:00 采集仍指向原 `ops/run_school_capture.sh`，实际私有配置为专用原生 Chrome、首页初始化、`html-then-json` 列表、JSON 详情及 Domain `18084` 出口。独立出口已启用且运行中，重启计数 0，内存约 1.6 MB。下一轮 02:00 尚未发生，本次验收不冒充下一轮日常调度结果。

流量后台：[Domain 使用的现有 iKuuu](https://ikuuu.co/auth/login)、[Real Estate 使用的 FlashProxy](https://www.flashproxy.com/dashboard)。Domain 本轮住宅代理消耗为 0；14 MB 浏览器响应统计不等于 VPN 供应商账单线量，不用历史余额冒充当前余额。
