# Sydney daily recovery implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在原悉尼三校范围内补齐可验证数据，接通每日采集、补充和完整记录发布。

**Architecture:** 保留原 runner、正式账本及受限写库。NSW 免费优先，显式赠金补缺；新 Flash 小样本通过后仅补 pending 输入。每日模型暂停时仍允许免费通勤及缓存发布。

**Tech Stack:** Python、requests、SQLite、MySQL、systemd、SSH。

**Spec:** `docs/superpowers/specs/2026-10-30-sydney-daily.md`

## Global Constraints

- 模型 CNY 50/月；非模型 USD 4/月；通勤现金 0；代理累计采购 USD 1.50，已采购 USD 0.60。
- 赠金累计最多 USD 40，1×1 元素按 USD 0.002 保守预留，未知计价拒绝。
- 正式账本 `/root/qrent-scraper/enrichment/state.db`，不清空失败、缓存或旧预留。
- 原悉尼三校区域和学校归属；不采墨尔本、不改线上表、不推上游。

## Review Focus

- 不确定赠金请求跨重启、日期或 Key 轮换不能重发；Task 2 测试持久失败保护。
- 多 Key / 跨月不能重置 USD 40 赠金总额；Task 2 使用同一结算组累计表测试。
- 上游 JSON 无成功元素、错误门牌或错误州不能被存成真实通勤；Task 2 边界响应测试。
- skip-model 结合 NSW / 赠金只能调用通勤，cache-only 仍禁止一切新 API；Task 3 运行真实队列边界测试。
- 旧 held 与未处理输入必须继续，已发布记录及旧回执必须保留；Task 3 冻结恢复输入测试。

### Task 1: 新模型与正式状态验收

**Files:** 私有 `output/sydney-daily-recovery-20261030/`，不改变源码。
**Interfaces:** 消费现有 `enrichment_runner.py --score-only`；产出真实小样本、缓存复验与预算证据。

- [x] 检查余额、原账本、任务及夜间入口；只读列出 NSW 缺口。
- [x] 三条未处理输入最多 15 次模型、地图 0；Expected: 8 个评分与双语关键词全部有效。
- [x] 同输入请求上限均为 0 重验；Expected: 3 个缓存命中、新请求 0。
- [x] 旧备份无损压缩并验证；Expected: 解压 SHA256 与原文件相同，正式库不变。

### Task 2: 赠金公交补缺

**Files:** `src/services/distance_transit.py`（新增）、`enrichment_state.py`、`commute_pool.py`、`tests/test_distance_transit.py`（新增）。
**Interfaces:** `EnrichmentState.reserve_credit(service, group, run_limit, usd_ceiling, lifetime_limit=40)`；`DistanceTransit.calculate(prop, destination, departure, pair_key)` 返回真实分钟或 None；显式 `COMMUTE_PROVIDER=distancematrix`。

- [x] 先写并运行持久赠金额闸、1×1 TRANSIT、成功复用、超时不重发及无效响应测试。Expected: FAIL（缺少接口）。
- [x] 实现独立累计赠金表、HTTP 前预留、严格响应与永久 pair 缓存；不记为现金支出。
- [x] 运行 `../../.venv/bin/python -m unittest discover -s tests`。Expected: 全绿。
- [x] 提交，然后通过原 SSH 部署入口部署。

### Task 3: 恢复 pending 与每日阶段

**Files:** `ops/enrichment_queue.py`、`ops/daily_enrich.py`（新增）、`ops/recover_enrichment_job.py`（新增）、`tests/test_enrichment_queue.py`、`tests/test_daily_enrich.py`（新增）。
**Interfaces:** Task 2 的显式通勤 provider；`--skip-model` 保持模型 0；`--bonus-fallback` 在 NSW 之后补缺；新冻结恢复 job 排除旧回执的 published_ids。

- [x] 先写并运行 skip-model / cache-only、恢复 held 与未处理、每日停止模型后的缓存路径测试。Expected: FAIL（接口未实现）。
- [x] 实现最小阶段逻辑、可检查的每日执行回执；模型遇到提供商错误仍退出 3，后续显式无模型路径不再评分。
- [x] 运行全套检查并提交；Expected: 全绿，旧回执及输入未覆盖。
- [x] 原部署入口备份、测试、部署；保留并停用已被赠金补缺替代的 Google 等待作业；正式账本与原日/月限额保持。

### Task 4: 真实补齐、发布与夜间验收

**Files:** 私有回执、`AGENTS.md`、最终报告；不修改原 MySQL 结构。
**Interfaces:** Task 1 新模型验收；Task 2 赠金额闸；Task 3 pending / daily 入口。

- [x] 核对账户真实赠金与现金账单，安装现有 Key，先最多 10 条验证；Expected: 真实 TRANSIT、现金 0。
- [ ] 只补缺失路线、只跑 pending 模型，保存每批结果与异常；不假称全部成功。
- [x] 对完整记录备份并发布；逐层核对 MySQL、API 和实际页面。
- [ ] 接回原 02:00 采集后每日流程，取得当轮实际调度回执，核对费用和缓存没有重复调用。
- [ ] 最终独立代码审查、必要修复、fork 推送；报告实际覆盖、剩余缺口与每日开关。


## Runtime checkpoint — 2026-10-30

- Task 1 三条 Flash 样本、中文缺失修复、零请求复验及旧备份无损压缩已完成。
- Task 2 已部署并完成 142 个真实元素，新增 71 对，正式通勤为 4,116/4,187；余下 71 对保留为 held。赠金预留 USD 0.284，供应商显示 USD 0.28，现金 0。
- Task 3 已部署每日准备、冻结恢复、模型暂停后的免费/缓存分支。首轮按发布回执排除 568 个已发布地址，V2 继续 2,069 个；尚未结束，不能勾选全体补齐。
- Task 4 原 02:00 采集已成功，5,075 来源 ID、41 查询、住宅线量 20,286,366 bytes；MySQL、公开 API 和两站真实页面卡片验证通过。Domain 200 候选回填已处理，199 条有真实描述、1 超时；每日服务已实际启动，先等真实首次补充标记；尚未取得整轮 daily-receipt.json。
- 审查发现的退出码、0 模型上限、错误校区、资产下载总时限及部分发布后恢复问题已修复；原始回执保留。主目录 14ec688/280 项服务器检查，运行中的 worker 2196667/275 项检查。
- 最终完成范围及 fork 推送记录见 docs/reports/2026-10-30-sydney-daily.md。

### Runtime correction — repeated one-group scoring replies

- [x] 只读核对 V2 的 8 条 held 原始响应，证明旧补缺仍仅返回一组；不重发旧请求。
- [x] 原评分组和补缺组均保留，剩余每组使用独立缓存槽请求；模型、提示词主键与原八组规则保持。
- [x] 预算中断恢复、零 HTTP 重验、非法单组及有效＋越界/负数/NaN 多组响应回归；RED→GREEN，284 项全绿。
- [x] 独立只读审查发现一项 raw 评分数量校验问题，已在一次修复中处理。
- [x] 主目录备份/测试/部署并恢复等待中的每日服务；运行中 worker 不改源码。
- [ ] 当日实际队列结束后核对原 held 的处理结果；不以离线测试代替真实批次回执。

07:37 checkpoint：V2 1,050/2,069 处理、1,042 发布、8 隔离，累计 1,610 个地址有发布回执；首轮未结束。Domain 原 200 候选已全部有描述，首页三校及同址三种通勤已实测。正式账本新增只读一致备份，夜间任务仍等待真实 first-pass 标记。
