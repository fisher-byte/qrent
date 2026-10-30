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
- [x] 只补缺失路线、只跑 pending 模型，保存每批结果与异常；不假称全部成功。
- [x] 对完整记录备份并发布；逐层核对 MySQL、API 和实际页面。
- [x] 接回原 02:00 采集后每日流程，取得当轮实际回执；模型恢复另串行执行，不冒称下一轮已经完成。
- [x] 最终独立代码审查、必要修复、fork 推送；报告实际覆盖、剩余缺口与每日开关。


## Runtime checkpoint — 2026-10-30 15:19

- Task 1–3 的真实小样本、缓存复验、原账本、受控赠金、每日入口与独立审查已完成。首次 V2 于 10:11 完成，旧 2,637 地址范围有 2,585 个发布回执；旧范围不代表当前库存。
- 原 02:00 采集退出 0：41 查询、5,075 来源 ID、住宅线量 20,286,366 bytes。原 200 个 Domain 候选描述已全部补回，旧超时/403 回执保留。
- 实际每日回执 12:38 取得：2,772 地址，模型暂停时发布/更新 2,057、隔离 715。用户澄清账单后，另受控补齐 627 个已有全部路线的模型缺口，627 全部发布；19 对保存响应复验及 5 次首次 NSW 免费路线后再补齐 13 地址，13 全部发布。
- 当前窄地址分组复核：2,771 有效地址 / 4,130 必要学校关联。2,696 完整地址 / 4,022 关联已经全部 MySQL/API 核对，差异 0；75 地址缺 107 对公交、其中 42 个还缺模型，继续隔离。
- 正式月模型账本 CNY 12.602919 含 CNY 10.080080 历史保守预留；本轮独立增量 CNY 2.522839。赠金累计 USD 0.56、通勤现金 0、新购买 0。旧其他项目 Key 保留。
- `53b2187` 经 295 项本机/服务器检查于 15:17 部署；评分前零 API 预检保证全部原学校路线有效后才花模型预算。真实 75 held 的凭据置空验收：模型阶段 0、新调用 0、生产写入 0、四账表 SHA 不变。尚未观察下一次真实 02:00 周期。
- 全分支及后端/新预检独立审查均未发现待处理 Critical/Important/Minor。实际运行不由代码审查推断。恢复代码已推送 fork 至 `53b2187`，原仓库未推、Actions `enabled=false`；本报告最后更新亦随 fork 保存。

### Task 5: 按用户纠正沿用服务器部署官网兼容修复

- [x] 核对 `CLAUDE.md`、原 SSH/Docker CD、私有环境配置和实际前后端响应。
- [x] 将旧搜索默认排序改为后端兼容，保留其他请求；真实 tRPC 6 项回归通过。
- [x] 移除原启动的 `db push --accept-data-loss`，构建时生成 Prisma、固定 pnpm；启动回归和 backend TypeScript 构建通过，共 7 项测试。独立审查无阻塞问题。
- [ ] 悉尼 `139.180.164.78` 有效 SSH 认证到位后，先核对原部署再只更新 backend，保留原环境/卷/表。部署主机已配置，当前部署用户名/密码为空，现有认证未通过；已请求补充，不继续猜测，不要求 Vercel 账号。
- [ ] 取得真实镜像构建、原 API 默认排序和实际网页验收；本机 Docker Hub 拉取超时，尚未确认镜像。不能以数据发布冒充后端代码部署。
