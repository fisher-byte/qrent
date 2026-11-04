# Independent Publication Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 有效采集记录独立发布，并补发 11 月 4 日最新 Real Estate 数据。

**Architecture:** 在原采集报告和受控发布队列之间增加部分结果验证入口。准备阶段保护缺席来源的已有主键，原报告继续记录失败区域，正式账本及发布质量校验保持。

**Tech Stack:** Python 3.11, unittest, SQLite, MySQL, Bash, systemd.

**Spec:** `docs/superpowers/specs/2026-11-04-independent-publication-design.md`

## Global Constraints

- 来源和区域独立验收，不要求全部 41 查询成功；逐套房字段和全部必要公交通勤必须完整。
- 唯一正式账本 `/root/qrent-scraper/enrichment/state.db`；原主键、学校、用户和收藏保留。
- 模型 CNY 50/月、其他 API/代理 USD 4/月、通勤现金 USD 0；不自动购买。
- 原报告、失败、不确定调用和费用预留保留；不覆盖运行源码；原部署工具加锁备份。

## Review Focus

- 来源代理检查失败可能没有区域条目：有证据的其他来源仍可更新，未知身份不得进入。
- 局部 success 不能声称完整 41/41；原始报告不被改写。
- 旧 Domain 主键不在本次采集中：同址的新 Real Estate 身份不能创建替代行。
- 重启必须复用冻结选择；不能因已发布身份变化重选。
- 403 后不继续同来源自动重试；其他来源继续采集。

### Task 1: 原始部分结果与独立发布

**Files:** `ops/publishable_capture.py`, `ops/daily_enrich.py`, `ops/prepare_school_enrichment.py`, `ops/run_school_capture.sh`, `tests/test_partial_publication.py`, `tests/test_school_capture_entry.py`.

**Interfaces:** `validate_publishable_capture(path) -> dict`, `select_publishable_capture(folder) -> Path`, `capture_summary(metadata) -> dict`; `run_daily(root, now=None, capture_path=None)` supports explicit original backfill evidence.

- [ ] 写先失败的反例：单区失败、整源缺席仍接纳真实身份；篡改学校/身份、无记录、外来查询拒绝；最新 partial 不回退旧 success；真实 shell 触发发布。
- [ ] 运行相应用例，确认因缺少新行为失败。
- [ ] 实现原始报告验证、来源/查询缺口摘要、daily/prepare 接入和定时 shell 新触发。
- [ ] 用例通过，并保持旧完整恢复接口行为。

### Task 2: 缺席主来源保护与拒绝后停止请求

**Files:** `ops/prepare_school_enrichment.py`, `recovery_runner.py`, `src/scrapers/domain.py`, `tests/test_partial_publication.py`, existing capture tests.

**Interfaces:** 原地址键及线上 `(source, house_id)` 用于准备阶段保护；Domain 访问拒绝传递为明确错误并停止该来源剩余请求。

- [ ] 写先失败的反例：缺席 Domain 拥有同址原主键时不创建 REA 替代，已有 REA 主键仍更新，单元房号保持分离；403 后其余来源正常运行且 Domain 不重试。
- [ ] 实现最小改动，运行目标用例及完整离线爬虫检查。
- [ ] 独立审查本次差异，解决可复现问题；原部署工具加锁、备份、离线验证后部署。

### Task 3: 补发及生产验收

**Files:** 私有 `output/daily-refresh-20261104/`、当前报告及 `AGENTS.md` 的事实约束。

- [ ] 对 2–4 日来源身份去重，冻结最新真实输入；先零 API 检查覆盖、来源保护、预算和原记录。
- [ ] 串行运行原正式队列，保留每批备份、断点、费用及发布回执。
- [ ] 对实际发布身份逐项核对原 MySQL 和公开 API，再检查实际页面日期。
- [ ] 保存部署/补发/差异回执和当前限制；清理无用临时文件，提交本次源码及公开报告。
