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
- 当前私有根 `.env` 的三个 `DEPLOY_SERVER_*` 均为空；现有爬虫 SSH 密钥及已提供数据库密码未通过悉尼 root SSH。已请求补充有效 SSH 用户名/密码、密钥路径或现有部署配置位置；不继续猜测密码，不新增服务器凭据。
- 因此本次后端排序/启动改动**尚未部署原官网 API**。数据补齐和原表写入继续正常，不能用数据发布回执声称后端代码已部署。

取得 SSH 后先只读确认实际工作目录、容器、镜像和环境，再备份代码/部署定义及原库，替换 backend 服务并核对 `/echo`、原搜索 API 和实际页面。保留数据库/Redis 容器、卷、JWT 和其他原环境配置；不执行原 CD 的 `compose down`、全机 prune 或 schema push。fork Actions 仍停用，继续使用本机检查和 SSH。
