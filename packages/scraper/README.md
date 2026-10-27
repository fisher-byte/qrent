# QRent Property Scraper

A modular collector for Australian rental listings from Domain and Real Estate. Current metro coverage, deployment evidence, and limits are recorded in the recovery report below.

## Fork 与都会区采集

当前恢复工作维护在 `fisher-byte/qrent`。后端仍在 `packages/backend`；两站爬虫分别在 `src/scrapers/realestate.py` 和 `src/scrapers/domain.py`。
这个 fork 不依赖原仓库的 CI/CD；GitHub Actions 已禁用。本机通过已有 SSH 别名 `qrent-prod` 部署、检查进程和下载结果，密钥不进入仓库。

从仓库根目录执行：

```bash
# 本地检查、按哈希打包、SSH 上传、服务器加锁备份、测试失败回滚。
.venv/bin/python packages/scraper/ops/deploy_recovery.py

# 两城、两站、多大区各最多 3 页；包括 Domain 的直连详情验收。
.venv/bin/python packages/scraper/ops/remote_capture.py start \
  --campaign metro-pilot-20261027 --mode pilot --proxy-bytes 10000000 --detail-limit 4
.venv/bin/python packages/scraper/ops/remote_capture.py status --campaign metro-pilot-20261027

# 试跑验收后启动全量。指定的 300 MB 是本次传输记录上限，不是账单承诺。
.venv/bin/python packages/scraper/ops/remote_capture.py start \
  --campaign metro-full-20261028 --mode full --proxy-bytes 300000000 \
  --detail-limit 0 --timeout 64800
```

- `metro_runner.py` 用 Domain 的 Sydney Region / Melbourne Region，以及 `src/config/metro_regions.json` 中的 Real Estate 区域组覆盖都会区。Real Estate 的部分区域组包含相邻边缘市场；每条结果保留原站点 ID、州、邮编与链接。
- 旧 `sydney - greater region` 搜索会被 Real Estate 解析成 CBD。新流程校验官方位置 ID，拒绝悄悄缩小范围。
- Domain 的结果窗口最多 50 页、Real Estate 最多 80 页。超过窗口时递归分价段并按来源 ID 去重；检查每页结构化搜索 ID、叶查询与根查询的唯一总量，缺失时保留断点并返回非零。
- Real Estate 列表内嵌数据提供描述、可入住日期、图片链接和位置，通常不需要再打开详情页，其图片文件不下载。Domain 使用原生 Firefox 普通网络，保留浏览器自身缓存和正常资源加载；可复用同址、同价、同卧室的完整信息，隐藏地址不参与跨站匹配。
- `state.json` 是私有续跑断点，`result.json` 是范围、质量、错误、流量报告；同时输出两站按城市分开的 `.csv.gz`。后台进程状态另写 `process-status.json`，不能仅凭结果文件推断进程结束。
- `remote_capture.py start --resume` 只在旧进程确已终止后续跑。断点绑定区域配置、规划版本和根查询集合，范围改变时拒绝沿用。
- `ops/run_metro.sh` 供现有每日 02:00 任务调用；`auto` 在缺少有效全量基线或基线超过 7 天时全量刷新，其他日期做增量采集。未完成全量不建立基线；未扫描和失败的数据不自动下架。
- 两站的最新优先排序从页面元数据校验，缺失或不匹配时拒绝增量早停；Real Estate 旧房源的全部字段变化由每周全量覆盖。
- 本轮代理累计购买上限为 USD 1.50，包含已购买的 USD 0.30 一次性额度，无自动续费。所有传输统计必须同时对照供应商实际扣量；最终数字见恢复报告。

## 2026-10-27 恢复运行入口

腾讯云现有独立部署位于 `/root/qrent-scraper`，使用 SQLite；它与根目录 Docker Compose 的 MySQL 后端是两条数据路径。
新的 `recovery_runner.py` 可写入原 SQLite 表，按来源区分 ID，失败或部分扫描不会下架存量房源。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-fingerprint.txt
.venv/bin/python -m camoufox fetch
.venv/bin/python -m playwright install firefox
# 从 .env.example 配置私有 .env；代理地区必须是 AU。
chmod 600 .env
BROWSER_ENGINE=camoufox .venv/bin/python recovery_runner.py \
  --areas newtown-nsw-2042 melbourne-vic-3000 \
  --scrapers domain realestate --pages 1 --detail-limit 4 --details-per-area 1 --retries 1
```

- `--cities sydney melbourne` 展开为配置的大学周边区域，不代表整个都市区全量覆盖。
- Domain 默认直连，`PROXY_SOURCES=realestate` 只让 Real Estate 使用 AU 代理；仅采 Domain 时不做收费代理检查。
- AU 代理故障或额度耗尽时保留 Domain 的直连结果，Real Estate 单独记为失败，整个任务返回部分完成。
- 同时选择 `--scrapers domain realestate` 时先运行 Domain，再按完整地址（含房号、州、邮编）、价格、卧室数复用最近 7 天内完整详情；可用 `--full-details` 强制重新读取。
- 详情总量由 `--detail-limit` 限制，单区由 `--details-per-area` 限制；超出明确限额的详情留待后续采集。列表仍保留两个站点各自的 ID 和链接。
- 此恢复入口默认最多 1000 个网络请求和约 30 MB 已记录代理传输；Real Estate 图片、视频、字体不下载，公共 CDN 资源走无认证直连。Domain 原生网络资源全部使用普通流量。供应商用量与应用计数可能因协议开销不同。
- 站内 JavaScript（含应用包及验证 SDK）也走无认证普通网络，AU 会话只用于页面和验证/数据交互；最新节流规则的服务器部署状态见恢复报告。
- 浏览器的 loopback HTTP/CONNECT 出口把后台更新、过滤规则和视频组件连接也分到普通网络；`proxy_wire_bytes`、`direct_wire_bytes`、`wire_hosts` 包含这些连接的传输记录。页面拦截统计不等于供应商扣量，配额同时检查页面与中继两项代理计数。
- 按运行生成 CSV 和 `*_result.json`；`success` 退出 0，空结果、详情失败或部分扫描退出非零。临时浏览器配置运行后自动清理；诊断时可用 `--keep-profiles` 保留。
- 千问模型通过 `QWEN_MODEL` 配置，默认 `qwen-plus`；支持 `DASHSCOPE_API_KEY`、`PROPERTY_RATING_API_KEY`、`QWEN_API_KEY`。评分通过 `--score --score-limit 5` 显式启用。
- 服务器的列表、详情、原库写入和模型评分验收分别留有记录；真实退出码、版本与完成范围见 [恢复报告](../../docs/reports/2026-10-27-scraper-recovery.md)。

服务器启动模板是 `ops/run_recovery.sh`：带独占锁、2 小时超时、每次约 30 MB 代理传输上限及正确退出码。原目录的每日 02:00 cron 可继续调用 `/root/qrent-scraper/run_scraper.sh`。`ops/backup_sqlite.py` 使用 SQLite 在线备份接口，能包含 WAL 中已经提交的记录。

验证采集模块：`python -m unittest discover -s tests -v`。

Domain 的可选开源驱动是 [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)（Apache-2.0）。安装 `requirements-fingerprint.txt` 后设置 `DOMAIN_BROWSER_ENGINE=patchright`，使用已安装 Chrome 或 `CHROMIUM_EXECUTABLE_PATH`。遵循项目推荐的持久化、headful、`no_viewport` 配置，保留真实浏览器的 UA、语言和窗口；生产是否通过另看恢复报告。Real Estate 继续使用 [Camoufox](https://github.com/daijro/camoufox)（MPL-2.0）。

`DOMAIN_NATIVE_NETWORK=true` 且 Domain 没有付费代理时，可设置 `DOMAIN_SEARCH_TRANSPORT=json`：先打开公开首页，在同一个浏览器会话里 GET 原搜索 URL，使用网站前端自身的 `Accept: application/json` 格式。直接校验真实返回的 `props` 中的地区、筛选、排序、分页和房源 ID，再解析 `listingsMap`；不合成 HTML 或搜索元数据。403、空响应和覆盖不足仍失败，详情页继续正常导航。默认 `navigation` 保留页面方式。都会区列表采集会跨价段复用 Domain 浏览器，失败续跑保留已完成叶查询，避免重新抓取整个分区树。

Camoufox 的普通网络仍默认带有 uBlock Origin。对照实验可设置 `DOMAIN_CAMOUFOX_NO_UBO=true`；`DOMAIN_CAMOUFOX_PRESET_FILE` 指向私有的、从当前浏览器对应内置样本中选出的 JSON 字典，固定同一身份并使用主机 OS，不再覆盖其语言。两项只影响未代理的 Domain Camoufox，缺失或损坏的指定文件会失败；它们不保证目标站点不会限流。不要复制个人浏览器 Cookie 或账号 profile。

异机补采只有在 Domain-only 全量和实际进程均成功、付费计数为零时，才能用 `ops/import_domain_capture.py` 校验导入。默认 dry-run，`--apply` 取得主任务锁后备份、upsert，并保持目标 partial；主 runner 仍须显式续跑核验，才能建立完整基线。
代理会话设计参考取回的 Qrent 服务器 LycheeIP/gost 代码，当前连接来自 FlashProxy Residential Lite；千问配置私下复用本机 `pdftomd` 项目的 DashScope 密钥。不在仓库中保存凭据。

## 🌟 Features

### Core Capabilities
- **Multi-Platform Scraping**: Supports RealEstate.com.au and Domain.com.au
- **Browser Sessions**: Persistent profiles within a capture, with explicit reset configuration
- **Historical Data Reuse**: Intelligent caching system to avoid redundant API calls
- **AI-Powered Scoring**: Property quality assessment using DashScope (Qwen) API
- **Commute Time Calculation**: Google Maps API integration for transit time estimates
- **Database Integration**: MySQL storage with intelligent update/delete logic
- **Modular Architecture**: Clean separation of scrapers, services, models, and utilities

### Advanced Features
- **Smart Region Matching**: Fuzzy address parsing for flexible location handling
- **Session Management**: Browser restart after 30 detail pages, with cookies preserved by default
- **Concurrent Processing**: Multi-threaded scoring and commute calculation
- **Real-time Logging**: Live progress monitoring with automatic flush
- **CSV Export**: Structured data output with date-stamped filenames
- **Error Recovery**: Comprehensive retry mechanisms and error handling

## � Project Structure

```
src/
├── config/
│   ├── __init__.py
│   └── settings.py          # Centralized configuration management
├── models/
│   ├── __init__.py
│   └── property.py          # Property data model & enums
├── scrapers/
│   ├── __init__.py
│   ├── base.py              # Base scraper abstract class
│   ├── domain.py            # Domain.com.au scraper
│   └── realestate.py        # RealEstate.com.au scraper (Playwright)
├── services/
│   ├── __init__.py
│   ├── commute.py           # Google Maps commute time service
│   ├── scoring.py           # AI property scoring service
│   ├── database.py          # Database connection service
│   └── database_importer.py # CSV to MySQL import logic
├── utils/
│   ├── __init__.py
│   ├── browser.py           # Browser manager (Selenium/Playwright)
│   ├── helpers.py           # Utility functions
│   └── logger.py            # Logging configuration
└── pipeline.py              # Main orchestration pipeline
```

## � Quick Start

### Prerequisites

- Python 3.8+
- MySQL 5.7+ or 8.0+
- Google Maps API key (for commute calculation)
- DashScope API key (for property scoring)

### Installation

```bash
# Clone the repository
cd packages/scraper

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Configuration

Create a `.env` file in the project root:

```env
# Database Configuration
DB_HOST=localhost
DB_USER=your_username
DB_PASSWORD=your_password
DB_DATABASE=qrent
DB_PORT=3306

# API Keys
GOOGLE_MAPS_API_KEY=your_google_maps_api_key
DASHSCOPE_API_KEY=your_dashscope_api_key
PROPERTY_RATING_API_KEY=your_dashscope_api_key  # Alternative key name

# Output Directory
OUTPUT_DIR=./output
```

### Basic Usage

```python
from src.pipeline import ScraperPipeline

# Initialize pipeline with all features enabled
pipeline = ScraperPipeline(
    scraper_types=['realestate'],  # or ['domain'] or both
    enable_scoring=True,
    enable_commute=True,
    enable_database=True,
    output_dir='./output'
)

# Run complete pipeline for UNSW
properties = pipeline.run(
    university='UNSW',
    scrape_details=True,
    skip_existing=True
)

print(f"Scraped {len(properties)} properties")
```

### Command Line Usage

```bash
# Run full pipeline for UNSW
python -m src.pipeline UNSW

# Generate UTS data from USYD (with commute time reuse)
python generate_uts_csv.py

# Process USYD details only (list already exists)
python process_usyd_only.py

# Clean CSV (remove properties without description)
python clean_csv.py output/UNSW_rentdata_251222.csv

# Import to database
python csv_cleaner_and_importer.py process output/UNSW_rentdata_251222.csv
```

## 🏗️ Architecture

### Pipeline Workflow

```
1. List Scraping
   ↓
2. Historical Data Loading (7-day cache)
   ↓
3. Detail Scraping (browser restart after 30 properties; explicit profile reset only)
   ↓
4. AI Scoring (DashScope API, 0-20 scale)
   ↓
5. Commute Time Calculation (Google Maps Transit API)
   ↓
6. Database Import (with outdated RealEstate property deletion)
   ↓
7. CSV Export
```

### Key Components

#### 1. ScraperPipeline (`pipeline.py`)
- **Purpose**: Orchestrates the complete scraping workflow
- **Key Methods**:
  - `_load_history_csv()`: Loads last 7 days' CSV for data reuse
  - `_apply_history_data()`: Applies cached details/scores/commute times
  - `run()`: Main execution flow for a university
  - **UTS Generation** (lines 641-710): Copies USYD data with UTS-specific commute

#### 2. RealEstateScraper (`scrapers/realestate.py`)
- **Purpose**: RealEstate.com.au scraping with Kasada bypass
- **Anti-Bot Strategy**:
  - Playwright persistent browser (`rea_profile/`)
  - Profile reset every 30 properties via `_reset_profile()`
  - Random delays (1-2 seconds) between requests
  - Page scrolling simulation
- **Key Methods**:
  - `scrape_listings()`: Scrapes property list pages
  - `scrape_property_details()`: Scrapes individual property details

#### 3. ScoringService (`services/scoring.py`)
- **Purpose**: AI-powered property quality assessment
- **Scoring Dimensions**:
  1. House Quality (0-10)
  2. Living Experience (0-10)
  3. Internal Facilities (0-10)
  - **Total Score** = (Sum of 3 dimensions) / 30 × 20
- **API**: DashScope model selected by `QWEN_MODEL` (default `qwen-plus`)
- **Strategy**: Multiple calls for consistency, average results

#### 4. CommuteService (`services/commute.py`)
- **Purpose**: Calculate public transit commute times
- **API**: Google Maps Directions API
- **Parameters**: 
  - Mode: Transit
  - Departure: Next day 8:30 AM
  - Alternatives: False (fastest route only)
- **Smart Reuse**: Checks `commute_times` dict before API call

#### 5. DatabaseImporter (`services/database_importer.py`)


## 🔧 Configuration

### ScraperConfig

```python
max_pages: int = 7          # Pages to scrape per area
page_delay: float = 5.0     # Delay between pages
request_delay: float = 3.0  # Delay between requests
retry_count: int = 3
retry_delay: float = 10.0
```

### Target Areas

Configurable target suburbs for each university in `config/settings.py`:

```python
TARGET_AREAS = {
    'UNSW': [
        "kensington-nsw-2033",
        "kingsford-nsw-2032",
        "randwick-nsw-2031",
        # ... 7 more areas
    ],
    'USYD': [...],
    'UTS': [...]
}
```

## 🛡️ Anti-Bot Strategies

### 1. Browser Profile Management
- **Persistent Profile**: Saves cookies, localStorage, session data
- **Automatic Reset**: Deletes profile every 30 properties
- **Directory**: `rea_profile/` (can be customized)

### 2. Request Patterns
- **Random Delays**: 1-2 seconds between requests
- **Page Scrolling**: Simulates human behavior
- **Wait Times**: Explicit waits for elements

### 3. Detection Avoidance
- Non-headless mode by default (uses Xvfb on servers)
- User-agent rotation (optional)
- Request header customization

## 📈 Performance Optimization

### Historical Data Reuse

The pipeline caches data from the last 7 days to minimize redundant operations:

```python
# Reuse statistics from a typical USYD run:
Details:  823 properties (no need to re-scrape)
Scores:   823 properties (no AI API calls)
Commute:  varies by university (no Google Maps API calls)
```

### Concurrent Processing

```python
# Scoring: 2 workers (configurable)
scoring_service = ScoringService(
    max_workers=2
)

# Commute: 5 workers (configurable)
commute_service = CommuteService(
    max_workers=5,
    request_delay=1.1  # Rate limiting
)
```

## 📦 CSV Output Format

Generated files: `{UNIVERSITY}_rentdata_YYMMDD.csv`

```csv
houseId,bedrooms,bathrooms,parking,propertyType,pricePerWeek,address_line1,address_line2,suburb,state,postcode,url,thumbnail_url,description_en,keywords,average_score,commuteTime_UNSW,commuteTime_USYD,commuteTime_UTS,available_date,source
```

## 🗄️ Database Schema

### Key Tables

- `property`: Main property data
- `property_school`: Many-to-many relationship (property ↔ school)
- `property_images`: Property image gallery
- `regions`: Suburb/postcode lookup table
- `schools`: University information

## 🔍 Troubleshooting

### IP Blocking Issues

If you encounter "Access Denied" or Kasada challenges:

```python
# Increase profile reset frequency
scraper._reset_profile()  # Manual reset
```

### Historical Data Not Loading

Check the output directory for CSV files:

```bash
ls -lt output/UNSW_rentdata_*.csv
```

Ensure files are within 7 days and don't contain `_list_` in filename.

### Database Import Errors

**Region Mismatch**: The fuzzy matching should handle most cases, but if you see warnings about unmatched regions, new records will be created with `state=NSW` and `postcode=0`. Review and update manually if needed.

## 🚀 Deployment

### Docker Deployment

```bash
# Build image
docker build -t qrent-scraper -f Dockerfile .

# Run scraper
docker run --env-file .env qrent-scraper python src/pipeline.py UNSW
```

### Scheduled Runs (Cron)

```cron
# Daily scraping at 2 AM
0 2 * * * cd /path/to/scraper && python src/pipeline.py UNSW >> logs/unsw.log 2>&1
0 3 * * * cd /path/to/scraper && python src/pipeline.py USYD >> logs/usyd.log 2>&1
0 4 * * * cd /path/to/scraper && python src/pipeline.py UTS >> logs/uts.log 2>&1
```

## 📝 Development

### Adding a New Scraper

```python
from src.scrapers.base import BaseScraper
from src.models import PropertyData, PropertySource

class NewSiteScraper(BaseScraper):
    SOURCE = PropertySource.NEW_SITE
    BASE_URL = "https://newsite.com.au"
    
    def scrape_listings(self, area: str) -> List[PropertyData]:
        # Implement listing scraping
        pass
    
    def scrape_property_details(self, properties: List[PropertyData]) -> None:
        # Implement detail scraping
        pass

# Register in pipeline.py
SCRAPERS = {
    'domain': DomainScraper,
    'realestate': RealEstateScraper,
    'newsite': NewSiteScraper,  # Add here
}
```

### Running Tests

```bash
# Run unit tests
pytest tests/

# Run with coverage
pytest --cov=src tests/
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is proprietary software for QRent internal use only.

## 📧 Contact

For questions or issues, please contact the development team.

## 🙏 Acknowledgments

---

**Last Updated**: December 2025  
**Version**: 2.0.0  
**Status**: Production Ready ✅
### UTS Surrounding Areas (10 postcodes)
sydney-city, ultimo, haymarket, pyrmont, chippendale, surry-hills, redfern, waterloo, glebe, newtown

## 📊 Monitoring and Logging

### Check Service Status
```bash
./deploy.sh status
```

### Real-time Log Monitoring
```bash
./deploy.sh logs
```

### Manual Task Execution
```bash
# Complete scraping process
./deploy.sh run

# Enter container for debugging
./deploy.sh shell
```

## 🛠️ API Configuration

### Google Maps API
1. Visit [Google Cloud Console](https://console.cloud.google.com/)
2. Enable "Maps JavaScript API" and "Distance Matrix API"
3. Create API key and set restrictions
4. Add key to `GOOGLE_MAPS_API_KEY` in `.env` file

### DashScope API (Alibaba Cloud)
1. Visit [Alibaba Cloud DashScope Console](https://dashscope.console.aliyun.com/)
2. Create API key
3. Add key to `PROPERTY_RATING_API_KEY` in `.env` file

## 🔍 Troubleshooting

### Common Issues

1. **Cannot connect to database**
   ```bash
   # Check database configuration
   ./deploy.sh shell
   env | grep DB_
   ```

2. **Chrome/ChromeDriver errors**
   ```bash
   # Rebuild image
   ./deploy.sh build
   ```

3. **API quota exceeded**
   - Check Google Maps API and DashScope API usage
   - Adjust concurrency or add delays

### Debug Mode
```bash
# Run test mode
./deploy.sh test

# Enter container for debugging
./deploy.sh shell

# View detailed logs
docker-compose logs scraper
```

## 🔒 Security Features

- ✅ Independent network isolation
- ✅ Run as non-root user
- ✅ Resource limit configuration
- ✅ Secure environment variable management
- ✅ Regular security updates

## 📈 Performance Configuration

### Resource Limits
- **Memory**: 1-2GB
- **CPU**: 0.5-1 core
- **Disk**: Automatic temporary file cleanup

### Optimization Configuration
- Intelligent data caching
- Incremental update strategy
- Concurrency control
- API call optimization

## 🔄 Updates and Maintenance

### Regular Maintenance
```bash
# Backup data
./deploy.sh backup

# Update image
./deploy.sh update

# Restart service
./deploy.sh restart
```

### Complete Cleanup
```bash
# Clean all Docker resources
./deploy.sh cleanup
```

## 🏗️ Docker Architecture

### Container Components
- **Base Image**: Python 3.11-slim-bullseye
- **Browser**: Google Chrome (stable)
- **WebDriver**: ChromeDriver (version-matched)
- **Process Manager**: Cron for scheduled tasks
- **User Security**: Non-root user execution

### Volume Management
- `scraper_logs`: Persistent log storage
- `scraper_data`: Temporary data storage
- `./backup`: Local backup directory

### Network Configuration
- **Isolated Network**: `qrent-scraper-network`
- **External Access**: Database connection only
- **Port Exposure**: None (security by design)

### Health Checks
- **Chrome Installation**: Verified on startup
- **ChromeDriver Version**: Automatic compatibility check
- **Python Dependencies**: Import validation
- **API Connectivity**: Runtime verification

## 🚀 Deployment Modes

### Production Mode (Recommended)
```bash
# Scheduled execution at 1:00 AM daily
./deploy.sh start
```

### Development Mode
```bash
# Immediate execution for testing
docker-compose run --rm scraper run
```

### Debug Mode
```bash
# Interactive shell access
./deploy.sh shell
```

## 📦 Data Management

### University-Specific Logic
- **UNSW & USYD**: Independent data scraping
- **UTS**: Copies USYD data, recalculates commute times only
- **Database Import**: Separate tables for each university

### Data Lifecycle
1. **Scraping**: Fresh data from Domain.com.au
2. **Processing**: AI enhancement and geo-calculation
3. **Storage**: Incremental database updates
4. **Cleanup**: Automatic temporary file removal
5. **Backup**: Optional data preservation

## 📞 Support

If you encounter issues:
1. Check logs: `./deploy.sh logs`
2. Run tests: `./deploy.sh test`
3. Check configuration: `./deploy.sh status`
4. Debug mode: `./deploy.sh shell`

Independent deployment ensures scraper service is completely isolated from the main application, providing better stability and security.

## 📄 License

This project is part of the QRent application suite. All rights reserved.

---

**Note**: This scraper is designed specifically for educational and research purposes related to Sydney university area rental market analysis.
