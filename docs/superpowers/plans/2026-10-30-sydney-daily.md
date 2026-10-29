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

- [ ] 检查余额、原账本、任务及夜间入口；只读列出 NSW 缺口。
- [ ] 三条未处理输入最多 15 次模型、地图 0；Expected: 8 个评分与双语关键词全部有效。
- [ ] 同输入请求上限均为 0 重验；Expected: 3 个缓存命中、新请求 0。
- [ ] 旧备份无损压缩并验证；Expected: 解压 SHA256 与原文件相同，正式库不变。

### Task 2: 赠金公交补缺

**Files:** `src/services/distance_transit.py`（新增）、`enrichment_state.py`、`commute_pool.py`、`tests/test_distance_transit.py`（新增）。
**Interfaces:** `EnrichmentState.reserve_credit(service, group, run_limit, usd_ceiling, lifetime_limit=40)`；`DistanceTransit.calculate(prop, destination, departure, pair_key)` 返回真实分钟或 None；显式 `COMMUTE_PROVIDER=distancematrix`。

- [ ] 先写并运行持久赠金额闸、1×1 TRANSIT、成功复用、超时不重发及无效响应测试。Expected: FAIL（缺少接口）。
- [ ] 实现独立累计赠金表、HTTP 前预留、严格响应与永久 pair 缓存；不记为现金支出。
- [ ] 运行 `../../.venv/bin/python -m unittest discover -s tests`。Expected: 全绿。
- [ ] 提交，然后通过原 SSH 部署入口部署。

### Task 3: 恢复 pending 与每日阶段

**Files:** `ops/enrichment_queue.py`、`ops/daily_enrich.py`（新增）、`ops/recover_enrichment_job.py`（新增）、`tests/test_enrichment_queue.py`、`tests/test_daily_enrich.py`（新增）。
**Interfaces:** Task 2 的显式通勤 provider；`--skip-model` 保持模型 0；`--bonus-fallback` 在 NSW 之后补缺；新冻结恢复 job 排除旧回执的 published_ids。

- [ ] 先写并运行 skip-model / cache-only、恢复 held 与未处理、每日停止模型后的缓存路径测试。Expected: FAIL（接口未实现）。
- [ ] 实现最小阶段逻辑、可检查的每日执行回执；模型遇到提供商错误仍退出 3，后续显式无模型路径不再评分。
- [ ] 运行全套检查并提交；Expected: 全绿，旧回执及输入未覆盖。
- [ ] 原部署入口备份、测试、部署；更新现有免费作业源码清单，保持模型 0、正式账本与原日/月限额。

### Task 4: 真实补齐、发布与夜间验收

**Files:** 私有回执、`AGENTS.md`、最终报告；不修改原 MySQL 结构。
**Interfaces:** Task 1 新模型验收；Task 2 赠金额闸；Task 3 pending / daily 入口。

- [ ] 核对账户真实赠金与现金账单，安装现有 Key，先最多 10 条验证；Expected: 真实 TRANSIT、现金 0。
- [ ] 只补缺失路线、只跑 pending 模型，保存每批结果与异常；不假称全部成功。
- [ ] 对完整记录备份并发布；逐层核对 MySQL、API 和实际页面。
- [ ] 接回原 02:00 采集后每日流程，取得当轮实际调度回执，核对费用和缓存没有重复调用。
- [ ] 最终独立代码审查、必要修复、fork 推送；报告实际覆盖、剩余缺口与每日开关。
