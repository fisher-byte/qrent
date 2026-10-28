# Qrent 通勤 API 免费量与采购候选

核查时间：2026-10-29 凌晨至早晨（北京时间）。本次没有购买新的 API 服务。月度新增 API/代理支出上限仍为 USD 5；代理实验累计采购上限仍为 USD 1.50。两张供应商已付发票确认代理累计已买 **USD 0.60 / 2GB**，本月连同模型预留共 USD 0.65，月预算余量 USD 4.35。

先继续使用已经跑通的 Google 免费量。悉尼优先试 Transport for NSW 官方 Trip Planner；付费备选优先用 Distancematrix.ai 的免费样本验证澳洲公交，再决定是否开通。现有证据不能承诺只买几美元就能立即补齐两城全部房源、全部校区。

## 已核实的 Google 账户与小样本

- Q-rent 项目已关联用户刚启用的有效结算账号，原有密钥已经私下配置，限制为 Routes API。旧 My First Project 的结算失效与新账号可用是两回事。
- 赠金页面没有额外赠金记录；预算页面当前显示 AUD 0 / 5，但账单有延迟，不把这个显示当作实时用量回执。
- Compute Routes Essentials 每月免费 10,000 次；超额首档 USD 5/千次，100,001–500,000 档为 USD 4/千次。特别大批量的更低价格不能直接套在 Qrent 当前规模上。[Google 价格表](https://developers.google.com/maps/billing-and-pricing/pricing)
- 已保存云端 ComputeRoutes 每日 300 次、未用的 ComputeRouteMatrix 每日 0 元素。预算提醒为 **AUD 5/月**，50%/90%/100%，这是提醒；应用调用前的 USD 5 账本才是本流程金额闸。每个真实结算组最多 9,500 次/月，配置不能调高为付费批量。
- 两站两城各一条，14 条真实 TRANSIT 路线全部成功。包含悉尼 20–28 分钟、墨尔本 10–148 分钟的不同校区路线。重跑命中 4 条 AI 与 14 条路线缓存，新增 API 调用 0。

Compute Route Matrix Essentials 有单独免费 SKU，但矩阵按“起点数 × 终点数”的**元素**计费，批量请求不会把几千条路线变成一次费用。当前没有启用这条备用路径。[计费规则](https://developers.google.com/maps/documentation/routes/usage-and-billing)

## 候选清单

“支持公交”要求返回澳洲公共交通加步行的真实行程时长；驾车 ETA、地图瓦片和地址转坐标不能替代。

| 服务 | 免费量与当前价格 | 购买门槛 | 澳洲公交证据与当前判断 |
| --- | --- | --- | --- |
| Google Routes Essentials | 10,000 次/月；随后 USD 5/千次，过 10 万档 USD 4/千次 | 已有账号与有效结算，无新订阅 | **两城 14 条已真实跑通**；继续免费量，达到闸值暂停 |
| Transport for NSW Trip Planner | 默认 Bronze：60,000 调用/日、5/秒；开放数据路径，无本次采购 | 需注册、激活账号、创建应用 Key；尚未取得 Key | **NSW 官方公交规划**，含步行接驳；是悉尼优先候选，尚未在本账户实测 |
| Distancematrix.ai Accurate | 免费 1,000 元素/月；Growth 前 10 万 USD 2/千，之后至 30 万 USD 1.60/千，30–50 万 USD 1.25/千，之后 USD 1/千 | 官网写明按量、无订阅/预付最低消费；升级 Growth 宣传 USD 50 赠金（约 25,000 元素），**尚非我方已获赠金** | 官方文档有 `mode=transit`，需指定时间与 `transit_mode`；**悉尼/墨尔本覆盖及准确度待免费对照试验** |
| TravelTime | Basic 免费供研发/评估，120 hits/min；生产价格询价 | 正式生产最低 12 个月，未公布美元价格 | 有公共交通与大矩阵；暂不作为 USD 5 生产方案 |
| SearchAPI Google Maps Directions | 一次性 100 次试用；USD 40/月含 10,000，250/月含 100,000；百万档 USD 1,500/月 | 最低付费套餐 USD 40/月，超预算 | 文档有 transit、出发/到达时间；未在澳洲实测。批量单价虽低，套餐总额不适合目前 |
| HERE Public Transit | 现行 Base Plan 价格需账号/询价核实 | 网上仍可见 Limited 1,000/日旧页面，但该套餐已退役 | 有公交接口和部分悉尼覆盖记录；**不把退役套餐当成可领取免费量** |

对应官方来源：[NSW API 配额与申请](https://opendata.transport.nsw.gov.au/developers/api-basics)、[NSW Trip Planner](https://opendata.transport.nsw.gov.au/dataset/trip-planner-apis)；[Distancematrix.ai 价格及赠金](https://distancematrix.ai/pricing)、[公交参数](https://distancematrix.ai/distance-matrix-api)；[TravelTime 定价与年约](https://traveltime.com/pricing)；[SearchAPI 价格](https://www.searchapi.io/pricing)、[Directions 参数](https://www.searchapi.io/docs/google-maps-directions-api)；[HERE 套餐退役说明](https://knowledge.here.com/csm_kb?id=public_kb_csm_details&number=KB0028268)、[公交接口](https://docs.here.com/transit/reference/public-transit-api-v8-getroutes)。

## 首次与日常的费用尺度

现有成功全量加增量共 59,364 个来源 ID，并不等于独立房屋数。一次只读清点发现悉尼 18,442、墨尔本 18,025 个完整地址，另有 966 条隐藏或不完整地址；街道缩写标准化还能继续合并。

把每个悉尼地址都算 3 校、每个墨尔本地址都算 4 校，会得到 **127,426 对的上界**。原线上 3,111 个房源只有 3,635 条学校关联，原采集也按大学周边区域分配，不能把这个上界说成必需调用量。必须先定出符合原逻辑的学校关联、排除隐藏地址，再扣除成功缓存。当前四条小样本的全校计算不自动推广到全库。

| 数量情景（只计路线） | Google 一个 Essentials SKU | Distancematrix.ai Growth |
| --- | ---: | ---: |
| 1,000 对 | 免费量内 USD 0 | Free 计划可先试 1,000/月 |
| 10,000 对 | 免费量内 USD 0 | 基础价 USD 20，赠金资格另核 |
| 127,426 对上界 | 90,000 × 0.005 + 27,426 × 0.004 = **USD 559.70** | 100,000 × 0.002 + 27,426 × 0.0016 = **USD 243.88**；若确认获得 USD 50 赠金，则约 **193.88** |

这些是计算情景，**不是已花的钱，也不是待批准的大批量任务**。只换便宜付费供应商仍不能在 USD 5 内覆盖这个上界。NSW 官方路径、正确学校范围与增量去重才可能显著改变账本。

日常量按“此前未成功计算过的地址—学校对”计，价格或图片变化不重算路线。例如每天净新增 100 对，30 天约 3,000 对，Google 常规免费量能覆盖；每天净新增 1,000 对则约 30,000 对，单 SKU 约 USD 100，不能启动。示例不代表已测到的每日新增率。

代理的已测历史全量传输为 247.09 MB、增量验证为 41.66 MB；以已购 USD 0.30/GB 折算约 USD 0.0741/次全量、0.0125/次增量。尚需观察正常多日波动，不能把一次增量简单当成长期均值。图片不下载，Domain 不走付费澳洲代理，Maps 使用独立普通出口。

若一个 30 天月按 4 次周全量加 26 次日增量、每次都恰好等于上述样本，约 2.07 GB，折算 USD 0.62/月；这是单次样本的情景外推，不含补采波动，也不是长期实测账单。首次从零的费用与成功地址只算一次的日常更新费用应分别列出。

AI 也纳入同一 USD 5。四条原算法 qwen-plus 共 8,936 输入、1,281 输出 tokens，约 CNY 0.009711；完整描述与模型配置相同即复用。便宜模型和 Batch 仅列为下一步评估，不为已评分样本重新付费试模型，不假定它们质量相同或赠送额度可用。

## 明早可执行的采购顺序

1. **先不买 Google 额外额度**。现有 Key 已可用，先按原学校范围排队，持久化缓存成功结果；应用及 Cloud 配额阻止无界调用。
2. 申请 NSW 官方 Trip Planner 应用 Key，先比对 20–50 条悉尼真实路线。地址识别请求也会消耗配额，校区坐标固定复用。
3. 如需付费备用，先注册 Distancematrix.ai Free，用墨尔本和悉尼各一组比对 Google；验证公交模式、时区、门牌/房号和零结果行为，再核实 Growth 的实际赠金、付款门槛、费用停机与数据保存权。现阶段没有理由先买数十美元套餐。
4. 原计划“同地址成功后不再自动请求”已技术实现。Google 标准条款对路线内容缓存有限制；长期生产数据保存应采用明确允许的合同或开放数据，不能把免费量视为无限保存许可。[Google Routes 缓存政策](https://developers.google.com/maps/documentation/routes/policies)

## 已排除或未证实的便宜选项

- Mapbox / OSRM / Simple Routing 的低价道路路线不等于公共交通。Simple Routing 当前还只覆盖北美/欧洲，明确没有 transit。[说明](https://www.simplerouting.io/google-maps-directions-alternative/)
- Transitland 的数据目录有澳洲 GTFS，但当前路由文档写明公交行程覆盖美国；全球覆盖的是道路路线，不能拿来替换两城公交。[当前能力](https://www.transit.land/documentation)
- GoMaps.pro 有旧的兼容 Directions 文档，但本次官网返回 502，未核实当前价格、可售状态和澳洲公交成功率，暂不列为购买推荐。
- 维州官方开放平台提供免费数据和部分 API，不等于已找到可直接用的地址到地址旅程接口；自建 OpenTripPlanner/R5 还需测算内存、GTFS 与维护成本，今晚未部署。[维州平台变更](https://opendata.transport.vic.gov.au/news-and-updates/consolidating-access-to-transport-apis)

过程证据与线上小样本见 [流水线恢复记录](2026-10-29-enrichment-recovery.md)。明早报告必须重新读取进程/账本/API，不能沿用凌晨数字声称全量已经同步。
