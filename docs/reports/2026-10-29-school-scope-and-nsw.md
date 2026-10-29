# 原学校区域与免费 NSW 通勤

2026-10-29。用户要求按原程序的学校周边区域恢复，尤其悉尼；不再扩大整座都会区。每月新增 API/代理现金上限改为 USD 4，本次通勤新增现金为 0。Distancematrix.ai 已由用户开通 Growth 并获得 USD 50 赠金，最多用 USD 40；当前保持禁用，先展示 NSW 缺口再决定。

## 原程序如何决定房源与学校

原 `src/scrapers/base.py:scrape_by_university` 读取 `TARGET_AREAS[university]`；`src/pipeline.py` 将这些查询的结果交给同一个 `university` 的通勤和写库步骤。每区默认最多 7 页，并没有“全城每个房源都算全部学校”的规则，也没有每所学校固定 2,000 或 3,000 套的数量规则。

| 学校 | 原查询区域 |
| --- | --- |
| UNSW | Newtown 2042、Eastgardens 2036、Pagewood 2035、Maroubra 2035、Kensington 2033、Kingsford 2032、Randwick 2031、Mascot 2020、Rosebery 2018、Zetland 2017 |
| USYD | Sydney City、Wolli Creek 2205、Hurstville 2220、Burwood 2134、Newtown 2042、Glebe 2037、Waterloo 2017、Chippendale 2008、Ultimo 2007、Haymarket 2000 |
| UTS | Sydney City、Ultimo 2007、Haymarket 2000、Pyrmont 2009、Chippendale 2008、Surry Hills 2010、Redfern 2016、Waterloo 2017、Glebe 2037、Newtown 2042 |

保留原 `sydney-city-nsw` 查询别名。Domain 查询 suburb slug，Real Estate 从原 slug 提取邮编查询；因此 Real Estate 的 Waterloo/Zetland 同为 2017，同一个查询可对应三所学校。去重后悉尼为 Domain 22 个查询、Real Estate 20 个查询；重复 URL 只采一次，在 ID 去重前保存全部学校角色。平台返回的相邻 suburb 仍属于发起该查询的学校，不通过地址另猜一个学校。

悉尼已有 `target_areas.py` 与 `src/config/settings.py` 配置一致。旧代码没有墨尔本学校区域配置；从四条恢复样本发布前的原库 `properties → property_school → schools` 恢复四个校区区域，保存为 `src/config/historical_victoria_regions.json`。MONASH 10 项、MONASH_CAULFIELD 8 项、UNIMELB 9 项、UNIMELB_SOUTHBANK 6 项；Domain 共 33 个 suburb 查询、Real Estate 27 个邮编查询。旧 `_RegionToSchool` 为空，不拿它推定范围。Carlton 被旧数据误连 NSW 2218，只在新配置按 VIC 3053 消歧，线上旧行保持。

代码用 `school_cohorts` 保存查询归属，`enrichment_runner.py --cohorts` 只计算对应校区，跨平台来源 ID 和 URL 保留。不能精确重建原查询归属的旧都会区库存只作地址筛选估计，不能声称与未来逐区查询结果逐条相同。

## 为什么会出现六万条

旧都会区活动在 08:15 部分结束，`complete_inventory=false`：Domain 25,474 / Real Estate 33,541，共 59,015 个平台来源 ID，包含大悉尼和大墨尔本广泛地区。它不是 59,015 个已去重的房屋，也不是某个学校的库存。

13:13 只读筛选结果如下。没有新抓网页，没有调用 API，没有写生产库：

| 指标 | 数量 |
| --- | ---: |
| 原区域内来源 ID | 11,281 |
| Domain / Real Estate | 4,636 / 6,645 |
| 范围外来源 ID | 47,734 |
| 规范化完整地址组 | 6,427 |
| 同源多 ID、价格或特征冲突，待复核地址组 | 594 |
| 隐藏、缺号或营销名称等不能分组的来源记录 | 880 |
| 无冲突地址组 | 5,833 |
| 已满足真实价格、描述、图片等基础输入的地址组 | 5,302 |
| 这些有效地址需要的学校关联 | 6,879 |
| NSW / VIC 有效地址—校区对 | 4,264 / 2,615 |

逐校来源 ID：UNSW 2,624、USYD 3,346、UTS 3,112、UNIMELB 1,306、UNIMELB_SOUTHBANK 2,360、MONASH 1,182、MONASH_CAULFIELD 563。合计 14,493 是带学校重叠的来源计数，不能当成独立房源。5 所大学原库有 7 个校区代码。

跨源按完整地址、房号、门牌区间、州和邮编规范化；不同房号不合并，不凭营销名称或相近地名合并。同源多个 ID、价格或床浴等特征冲突先隔离，保留全部来源证据。无冲突组中有 3,173 组跨源匹配。一个主记录优先有完整描述，再优先 Domain；不同描述不共享 AI 评分输入。

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

区域与免费通勤代码正在验收部署，部署回执和扩大样本结果在完成后追加。本轮尚未写官网 MySQL，未补齐全批评分/关键词，也未声称整库同步。旧四条完整生产样本及原 3,111 条记录的验收见 [恢复报告](2026-10-29-enrichment-recovery.md)。

墨尔本的 2,615 对有效关联不在 NSW 覆盖范围；按 Growth Accurate 当前 USD 2/1,000 elements，约消耗 USD 5.23 赠金、现金 0。启用前需用户在“赠金试 10 条 / Google 免费分天 / 暂缓”中选择，当前不会发 Distancematrix.ai 请求或自动付费。[供应商价格](https://distancematrix.ai/pricing)
