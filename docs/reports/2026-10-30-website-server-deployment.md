# 官网服务器更新路径

用户要求沿用服务器和私有环境配置更新，不通过个人 Vercel 账号重新部署官网。

## 已核对

- `CLAUDE.md` 是历史架构说明。仓库没有 `cloud.md`；原 `.github/workflows/cd.yml` 使用 `DEPLOY_SERVER_HOST`、`DEPLOY_SERVER_USERNAME`、`DEPLOY_SERVER_PASSWORD`，经 SSH/SCP 更新服务器 `~/qrent`，Docker Compose 管理 backend、MySQL、Redis。
- 2026-10-30 从本机及爬虫机读取的实际响应：`www.qrent.rent` 是 Next.js/Vercel，`api.qrent.rent` 是 Cloudflare 后的 Express。这不妨碍通过悉尼服务器更新 API，使现有网页获得正确排序，无需取得前端部署账号。
- 数据库连接 `139.180.164.78` 正常，受限写入已能自动更新原表。数据库登录成功不等于已经取得该机器的 SSH 登录。

## 本次改动

旧搜索网页每页请求 12 条、仅按评分降序，导致保留的旧高分房源占据前排。后端识别这一请求形状，改为先按原 `publishedAt` 降序、再按评分降序；过滤、分页、语言及其他显式排序保持。相同形状的其他客户端也会获得该默认排序。首页已有日期优先排序。

本次不把 `publishedAt` 当成最近核验时间，不改旧行日期、不删除旧库存。原表没有 `last_seen` / 活跃状态字段，因此日期优先也不等于所有旧库存已经重新核验。

原 backend 启动脚本每次执行 `prisma db push --accept-data-loss`。现已移除：启动只运行后端；Prisma Client 在镜像构建时生成，使用占位 URL、不连接数据库。pnpm 固定为已在本机使用的 10.19.0。

## 验证和部署边界

- 真实 tRPC caller 的 6 项排序、过滤和本地化回归通过，PropertyService 被替换，不访问数据库或邮件。
- 启动测试先在原脚本观察到 `db push` 而失败；修复后实际执行 shell，仅捕获后端启动命令。共 7 项回归及 backend TypeScript 构建通过；独立审查未发现阻塞问题。
- Docker 镜像验证停在 Docker Hub 认证端点网络超时，尚未完成镜像内验证。构建上下文只含公开源码和依赖清单，没有复制私有环境文件。
- 已将私有根 `.env` 的 `DEPLOY_SERVER_HOST` 配置为用户给定 IP；`DEPLOY_SERVER_USERNAME` / `DEPLOY_SERVER_PASSWORD` 仍为空；现有爬虫 SSH 密钥及已提供数据库密码未通过悉尼 root SSH。已请求补充有效 SSH 用户名/密码、密钥路径或现有部署配置位置；不继续猜测密码，不新增服务器凭据。
- 因此本次后端排序/启动改动**尚未部署原官网 API**。数据补齐和原表写入继续正常，不能用数据发布回执声称后端代码已部署。

取得 SSH 后先只读确认实际工作目录、容器、镜像和环境，再备份代码/部署定义及原库，替换 backend 服务并核对 `/echo`、原搜索 API 和实际页面。保留数据库/Redis 容器、卷、JWT 和其他原环境配置；不执行原 CD 的 `compose down`、全机 prune 或 schema push。fork Actions 仍停用，继续使用本机检查和 SSH。


## 20:06 用户确认后的部署路径更正

用户明确：长期沿用“GitHub 合并后 CI/CD 发布后端/依赖/Docker；数据库由 Agent 独立账号操作；爬虫在 yibin 的机器写库”。GitHub 已只读核实 `production` 环境有 18 项 Secrets，包括部署主机、用户名和密码，当前账号有原仓库写权限。后端发布不依赖本地 `.env` 的部署字段，也不需要导出 Secrets 或索取密码。

先前本地 SSH 阻塞结论只适用于手工 SSH，不适用于既有 CI/CD。新复核确认本地 `DB_ROOT_PASSWORD` 已被读取并提交，但服务器拒绝 root 认证；部署身份必须交由原环境中的三项 Secrets 使用。

新的小范围发布分支从当前原 `main` 的 `9850e68` 准备：后端排序/安全启动、CI 成功后调用 CD、只更新 backend、保留原 `.env`/MySQL/Redis/卷、校验失败回退旧镜像。用户已明确按这个流程继续；原广泛爬虫恢复分支不整体合入。实际部署仍须取得 PR 检查、生产 CD 和公开 API/页面验证，不能用准备成功替代。


## CI 验收与剩余审批

最小发布 PR 为 https://github.com/wiperi/qrent/pull/293 ，head `fd1237cb39b257a910440339dd137d83d1de1fca`。原仓库 CI https://github.com/wiperi/qrent/actions/runs/41054391656 已完成成功：代码格式、前后端构建/类型检查、7 项后端回归、8 项可执行发布回归、6 项健康验收，以及真实后端 Docker 构建和无网络 Prisma Client 检查全部通过。PR 事件的部署 job 按设计跳过，只有原 main/staging 的 push 且 CI 成功后才调用生产/预发布 CD。

独立审查发现的两处 Important 均已先复现后修复：旧镜像回退还需通过真实数据库房源查询；新页须与显式日期/评分查询的结果边界对齐，允许两种排序键完全相同的并列。不能只凭 /echo 或页内有序宣称恢复。

已按用户最新授权尝试正常 squash 合并，GitHub 返回 base branch policy prohibits the merge。PR 状态 REVIEW_REQUIRED，当前 fisher-byte 是作者、非管理员，仓库 auto-merge=false；需原维护者完成 GitHub 审批/合并。已向用户提交具体 PR，不再索取服务器密码、不绕过分支保护。Vercel 的外部 PR 状态不属于 required checks；必需项 ci-success 已通过，实际剩余合并阻塞是 Review。

尚未合并、尚未执行生产 CD；官网原排序行为仍需上线后验收。数据写入与爬虫原每日链路继续独立运行。
