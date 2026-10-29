# 原学校区域与免费 NSW 通勤

2026-10-29。用户要求按原程序的学校周边区域恢复，尤其悉尼；不再扩大整座都会区。每月新增 API/代理现金上限改为 USD 4，本次通勤新增现金为 0。用户随后选择**先处理悉尼、墨尔本暂缓**，采集和补充都先执行悉尼。Distancematrix.ai 已由用户开通 Growth 并获得 USD 50 赠金，最多用 USD 40；当前保持禁用，不消耗赠金。

## 原程序如何决定房源与学校

原 `src/scrapers/base.py:scrape_by_university` 读取 `TARGET_AREAS[university]`；`src/pipeline.py` 将这些查询的结果交给同一个 `university` 的通勤和写库步骤。每区默认最多 7 页，并没有“全城每个房源都算全部学校”的规则，也没有每所学校固定 2,000 或 3,000 套的数量规则。

| 学校 | 原查询区域 |
| --- | --- |
| UNSW | Newtown 2042、Eastgardens 2036、Pagewood 2035、Maroubra 2035、Kensington 2033、Kingsford 2032、Randwick 2031、Mascot 2020、Rosebery 2018、Zetland 2017 |
| USYD | Sydney City、Wolli Creek 2205、Hurstville 2220、Burwood 2134、Newtown 2042、Glebe 2037、Waterloo 2017、Chippendale 2008、Ultimo 2007、Haymarket 2000 |
| UTS | Sydney City、Ultimo 2007、Haymarket 2000、Pyrmont 2009、Chippendale 2008、Surry Hills 2010、Redfern 2016、Waterloo 2017、Glebe 2037、Newtown 2042 |

保留原 `sydney-city-nsw` 区域配置。Domain 查询 suburb slug，Real Estate 从原 slug 提取邮编查询；因此 Real Estate 的 Waterloo/Zetland 同为 2017，同一个查询可对应三所学校。原 Sydney City 别名没有邮编，旧 REA 适配器将它直接拼进 URL；此路径实测未通过。候选已用 **2000 邮编路径真实取到 25 条**，仍归 USYD/UTS，修复仅规范化这个查询，不扩大区域。规范化并去重后悉尼为 Domain 22 个查询、Real Estate 19 个查询；重复 URL 只采一次，在 ID 去重前保存全部学校角色。平台返回的相邻 suburb 仍属于发起该查询的学校，不通过地址另猜一个学校。

悉尼已有 `target_areas.py` 与 `src/config/settings.py` 配置一致。旧代码没有墨尔本学校区域配置；从四条恢复样本发布前的原库 `properties → property_school → schools` 恢复四个校区区域，保存为 `src/config/historical_victoria_regions.json`。MONASH 10 项、MONASH_CAULFIELD 8 项、UNIMELB 9 项、UNIMELB_SOUTHBANK 6 项；Domain 共 33 个 suburb 查询、Real Estate 27 个邮编查询。旧 `_RegionToSchool` 为空，不拿它推定范围。Carlton 被旧数据误连 NSW 2218，只在新配置按 VIC 3053 消歧，线上旧行保持。

代码用 `school_cohorts` 保存查询归属，`enrichment_runner.py --cohorts` 只计算对应校区，跨平台来源 ID 和 URL 保留。不能精确重建原查询归属的旧都会区库存只作地址筛选估计，不能声称与未来逐区查询结果逐条相同。

## 为什么会出现六万条

旧都会区活动在 08:15 部分结束，`complete_inventory=false`：Domain 25,474 / Real Estate 33,541，共 59,015 个平台来源 ID，包含大悉尼和大墨尔本广泛地区。它不是 59,015 个已去重的房屋，也不是某个学校的库存。

13:42 对固定都会区库存的只读筛选结果如下。此次审计本身没有抓网页、调用 API 或写生产库。下表包含两城历史范围供对账，当前仅执行悉尼：**2,681 个有效地址、4,253 对必要学校关联**。

| 指标 | 数量 |
| --- | ---: |
| 原区域内来源 ID | 11,281 |
| Domain / Real Estate | 4,636 / 6,645 |
| 范围外来源 ID | 47,734 |
| 规范化完整地址组 | 6,399 |
| 同源多 ID、价格或特征冲突，待复核地址组 | 591 |
| 隐藏、缺号或营销名称等不能分组的来源记录 | 926 |
| 无冲突地址组 | 5,808 |
| 已满足真实价格、描述、图片等基础输入的地址组 | 5,278 |
| 这些有效地址需要的学校关联 | 6,850 |
| NSW / VIC 有效地址—校区对 | 4,253 / 2,597 |

逐校来源 ID：UNSW 2,624、USYD 3,346、UTS 3,112、UNIMELB 1,306、UNIMELB_SOUTHBANK 2,360、MONASH 1,182、MONASH_CAULFIELD 563。合计 14,493 是带学校重叠的来源计数，不能当成独立房源。5 所大学原库有 7 个校区代码。

跨源按完整地址、房号、门牌区间、州和邮编规范化；不同房号不合并，不凭营销名称或相近地名合并。同源多个 ID、价格或床浴等特征冲突先隔离，保留全部来源证据。无冲突组中有 3,158 组跨源匹配。一个主记录优先有完整描述，再优先 Domain；不同描述不共享 AI 评分输入。比 13:13 快照进一步排除了 `2x/Loftus Street` 等斜线后没有真正门牌的输入，原 5,302 地址 / 6,879 对已被本表替代。

私有证据：`output/school-scope-audit-20261029/{report,scoped-properties,groups,unknown-addresses}.json`。这是被部分采集覆盖的库存筛选，不是覆盖验收，更不代表官网已同步。

## NSW 真实验证与现金边界

已配置用户登录后创建的 NSW Token，密钥只在服务器私有 `.env`。Transport for NSW 的 Trip Planner API 免费，账号各端点共享每日 60,000 次、每秒 5 次；程序取每日 59,000 和至少 0.30 秒间隔，HTTP 前记正式账本，所有端点合并计数。[NSW API basics](https://opendata.transport.nsw.gov.au/developers/api-basics)

通勤只接受真实门牌、街道、邮编匹配的房屋坐标和具名大学 POI；不把 suburb 中心或附近车站当房屋。行程包含步行和换乘等待，拒绝汽车/出租车，不乘驾驶系数。NSW 对墨尔本不会自动回退其他服务。

已验证 194 Anzac Parade → UNSW 11 分钟；随后不同区域的 5 个地址、11 对学校路线全部成功：

| 地址 | 实际公共交通分钟 |
| --- | --- |
| 1 Park Lane, Chippendale | USYD 20 / UTS 7 |
| 1 Sonny Leonard Street, Zetland | UNSW 25 / USYD 42 / UTS 28 |
| 1/102 Darley Street, Newtown | UNSW 52 / USYD 29 / UTS 30 |
| 1/146 Anzac Parade, Kensington | UNSW 14 |
| 1/584 Harris Street, Ultimo | USYD 24 / UTS 8 |

一条 `1 bed/2-6 Willis Street` 营销地址在输入阶段隔离。初试中地理索引将 Australia 误作街名 token，去掉请求末尾国家词后通过；永久地址身份及房号未改变。此前失败解析仍保留账本，未删除或抹账。

截至扩大试跑前：NSW 29 次 HTTP（均 200，不代表每次都有可接受地址），12 条成功路线；原 Google 15 成功、1 不确定，模型 16 次保持。新增现金为 0，正式账本已花/预留 USD 0.65，硬上限 USD 4，剩余 USD 3.35。USD 0.60 来自两笔代理发票，USD 0.05 是历史模型保守预留。没有新采购或计费服务。

`--commute-only` 强制跳过模型并禁止发布；同一完整地址与校区的成功通勤永久缓存，跨来源、重启和跨月复用。失败和不确定请求不自动重复发 HTTP。旧 Google 免费调用闸保持 9,500/月和云端 300/日，Cloud AUD 5 仍只是提醒。

## 本轮完成边界

区域与 NSW 代码 `0c6ddb5` 已部署，本机及服务器 **201 项检查通过**。回执 `output/deployments/20261029_134000_867580/server-receipt.json`；此前 `817ffcd` 的区域主体部署为 199 项。现有根采集入口已经从都会区任务切回学校区域，凌晨 02:00 cron 指向该入口。用户刚选择仅悉尼后的入口收窄，以及实际试跑暴露的重复 IP 查询/浏览器缓存修复，正在独立候选目录验证，正式源码在已有任务运行期间保持。

正式部署后 11 条路线零请求复验全部命中。扩大到 100 个地址、147 对路线的初试得到 117 对；根据已缓存响应修复 `Tce/Cr/Qy`、门牌字母大小写和 Sydney Metro class 2 后，99 个有效地址的 146 对中 **127 对成功**，新增 8 个此前尚未发起的路线请求，原 117 对全部命中。另 1 个不完整门牌已隔离；19 对仍无法严格匹配地址，保留复核，后续 NSW 重跑不自动重复这些失败对。没有通过放宽门牌、邮编或改用街道中心提高成功率。

NSW 完整免费批次 `qrent-nsw-full-20261029.service` 已于 **13:44:35** 启动：2,681 地址、4,253 必要学校对，最多 10,000 次本轮 NSW HTTP，请求与失败均在原正式账本；模型上限 0、禁止发布、无自动提供商回退。13:59 快照为处理 1,092 地址、返回 1,423 对通勤，任务仍在运行；这不是完成结果。

该批次现已结束：2,681 地址全部处理，**3,549 / 4,253 对成功**，704 对未通过严格地址匹配（685 个新缺口、19 个此前隔离结果）。本轮新增 NSW HTTP 3,610 次；已有 132 对直接命中共享成功缓存。`commute_partial` / exit 2 表示这些真实缺口，不能当成全部补齐，也不是进程仍在运行。下一步仅对缺口使用已有 Google 免费额度补最多 280 次，留足原有调用占用；不切换赠金或付费调用。

批次启动前已对正式账本做一致性异机备份，含 NSW 233 次、Google 15 成功/1 不确定、模型 16 次及 USD 0.65 费用。解压 SHA256 `68123899b082a7831790e8aaad9850b16793f342623699263de7615bd3108c75`；原失败和不确定记录保持。本机仍不使用旧空账本发 API。

实际来源试跑已取到 Domain 的 Newtown / Kensington / Sydney City 各 20 条，以及 Real Estate 的 Newtown / Kensington 各 25 条，共 110 个来源 ID；查询对应学校和重叠归属已在真实结果中保存。第一次小试的全局 600 请求闸触发，因此整体标记 partial；Sydney City 的 Real Estate 查询另行复核，不把部分成功报告成完整覆盖。旧浏览器路径还暴露了第三方出口 IP 再探测超时，候选修复会复用既有 AU 验证 IP。

14:17 用这 110 个真实捕获 ID 的查询归属只读复核：UNSW 90、USYD 65、UTS 65 个带学校角色的来源 ID，学校重叠保持。新 `ops/select_school_inventory.py --capture-result` 与补充入口共用查询角色读取，不再依地址重猜；其中 64 个基础输入有效的地址组、128 对学校关联可进入补充。部分采集的整体窗口仍标记不完整。

出口 IP 修复候选复用已经验证的 AU 地址，避免 Camoufox 再去 ipecho.net 探测；会话轮换或 5 分钟后重新验证，不拿前一个会话的结果冒充新出口。区域之间保留同一轮的 cookies/资源缓存，结束时清理本轮临时 profile。候选目录 **206 项检查通过**，正式已有任务结束后再部署，未覆盖运行中的源码。

## 流量与每日成本估算

NSW API 走既有普通香港出口，Domain 仍不使用住宅流量。Real Estate 的两区试跑住宅 wire 为 1,641,672 bytes；复用已验证 IP 后 Sydney 2000 单页为 558,219 bytes、83 个受控请求，25 条房源成功。失败尝试和后台开销也计入流量，不能只看正文长度。

按悉尼 19 个 REA 查询、每区最多 7 页（至多 133 页），以上小样本粗估 **75–110MB/日**，按已购 USD 0.30/十亿 bytes 折算约 **USD 0.023–0.033/日、USD 0.68–0.99/30 日**。这是预付流量消耗价值，不是本轮新增采购，也不是已测得的连续多日账单。每日入口仅悉尼、最多 150MB 住宅流量（折算约 USD 0.045），仍无自动充值；不足时停止并报告，月度新增现金硬闸保持 USD 4。

本轮尚未写官网 MySQL，未补齐全批评分/关键词，也未声称整库同步。旧四条完整生产样本及原 3,111 条记录的验收见 [恢复报告](2026-10-29-enrichment-recovery.md)。

墨尔本最新筛选为 2,597 对，原问题中的 2,615 对是门牌进一步校验前的估计。用户已选暂缓，因此不执行该队列，不发 Distancematrix.ai 请求或自动付费。

## 15:14 最新验收与剩余任务

14:25 已正式部署 **`20fd6f3`**，本机和正式服务器均为 **206 项检查通过**。回执 `output/deployments/20261029_142505_643139/server-receipt.json`，备份 `/root/qrent-scraper/backup/pre-source-20261029_142506_680820`。14:27 根 `run_scraper.sh` 已换为仅悉尼的学校入口，凌晨 02:00 cron 指向不变；文件 SHA256 `7d33728f694398f0605b9b55c2f4e6208531256555119223a1af1ed53c795354`。

NSW 阶段后的 704 对缺口，先用已有 Google Routes 免费量补 **280 对，全部成功**；随后 3,829 对跨提供商通勤零请求复验全部命中。保留原失败记录，没有通过清账本重新申请额度。

Google Cloud 的每日额度按太平洋时间午夜重置，因此在北京时间 **10 月 29 日 15:05** 的一次性服务器任务再补了 **280 对，全部成功**。15:13 只读读取正式缓存的结果为：

| 当前悉尼冻结输入 | 数量 |
| --- | ---: |
| 有效地址 | 2,681 |
| 必要地址—学校对 | 4,253 |
| 成功通勤 | **4,109** |
| 仍待补通勤 | **144** |
| 仍有通勤缺口的地址 | 84 |
| 本轮新增现金 | **USD 0** |

正式账本保留 NSW 3,843 次 HTTP（包含不能精确解析的地址响应）、Google 575 成功和 1 历史不确定、模型 16 次；费用仍 USD 0.65 已花/预留、USD 3.35 余量。这里只统计缓存和真实请求，不把 HTTP 200 等同为地址/通勤成功。当前 read-only 探针新增 API 0、生产写入 0。

剩余输入保存在服务器 `remaining-free-queue/`；下一次一次性任务为 **10 月 30 日 15:05 北京时间**。它复用上一批成功结果，只补未完成对，单批最多 280 次，始终模型 0、禁止发布。守卫脚本见 `packages/scraper/ops/guarded_free_batch.py`；实际投放脚本 SHA256 `8a13bc45694d857c2b714b8a428e8e6a7939ccf946fae39d722e9fb3377238dc`。它检查 69 个正式源文件、真实账本中的日/月请求、Routes 配置及上一批错误；源文件变化、前一批提供商异常或免费量不足时停止。10 月 29 日 15:00 前的检查正确得到 Google 已占 296 次、当前允许 0，不继续发请求。未来结果以 `batch-b/result.json` 为准。

每日时区依据：[Cloud Quotas 的每日重置规则](https://docs.cloud.google.com/docs/quotas/overview)；免费月额度依据：[Google Maps 价格与免费量](https://developers.google.com/maps/billing-and-pricing/pricing)。未提高原 300/日和 9,500/月限额，未启用 Matrix、赠金或付费备用。

正式账本已在 15:11 再做一致性备份、gzip 完整性及解压 SHA256 校验：`state-free-sydney-combined-20261029_151140.db.gz`，解压 18,870,272 bytes，SHA256 `63ab2e8ddd008b4ceccfb39c957fcbb37de2e79a9961b839c5ea5d6c217f50d7`。私有回执为 `final-ledger-backup.json`，不覆盖旧备份或失败记录。

**官网没有因此批量发布。** 15:14 用原根 `.env` 对悉尼 MySQL 再做只读事务核对：房源 3,115、用户 54、学校代码 7，最新 `published_at` 仍是 2026-10-28 08:31:24；本轮生产写入 0。批量 AI 评分、中英文关键词和生产发布仍未完成；模型是否也必须完全免费，已向用户澄清，在收到回复前不新增付费模型请求。

私有证据：`latest-cache-readonly.json`、`google-free-280/result.json`、`remaining-free-queue/{schedule-receipt,batch-a/result}.json`、本机 `mysql-readonly-final.json`。部署、采集窗口、路线缓存、模型字段、数据库和网页分别验收，不将这批真实通勤当成官网整库已经更新。

收尾将本轮三份 `pre-source-20261029_{132932_293274,134002_009468,142506_680820}` 目录内的 **爬虫 SQLite 备份**由 `properties.db` 无损压缩为 `properties.db.gz`；每份通过 SQLite quick_check 和完整解压 SHA256 比对，内容可恢复，源码回滚文件保留。合计原始 1,003,819,008 bytes、压缩后 221,904,543 bytes；连接关闭后可用磁盘约 1.40GB。对应目录的 `compressed-backup-receipt.json` 保存原始哈希和大小。没有压缩、更换或清理正式补充账本，也没有删除失败/不确定调用。
