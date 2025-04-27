# Discord 频道消息爬虫

一个高性能 Python 工具，用于从 Discord 频道抓取历史消息并导出为 Markdown、JSON 或 HTML 文件。使用用户令牌而不是机器人令牌，不需要服务器管理员权限。支持获取用户角色信息，并使用异步并发请求提高性能。

## 功能特点

- 使用频道 ID 从任何 Discord 频道获取消息
- **多种导出格式**：支持 Markdown、JSON 和 HTML 格式
- 包含消息内容、附件、嵌入内容和反应
- **显示用户角色信息**，便于识别管理员和重要用户
- 支持分页获取大量消息历史
- 可配置的消息获取数量限制
- 进度条显示，清晰了解爬取进度
- **使用异步请求提高性能**，大幅加快爬取速度
- **并发请求控制**，避免触发 Discord 的速率限制
- **自动重试机制**，提高爬取稳定性

## 要求

- Python 3.8 或更高版本
- Discord 用户令牌（不是机器人令牌）
- 依赖库：requests, aiohttp, asyncio, python-dotenv, tqdm, argparse

## 安装

1. 克隆此仓库：
   ```
   git clone <repository-url>
   cd discord-channel-scraper
   ```

2. 安装所需的依赖项：
   ```
   pip install -r requirements.txt
   ```

3. 在项目目录中创建一个 `.env` 文件，并添加您的 Discord 用户令牌：
   ```
   DISCORD_TOKEN=your_discord_user_token_here
   ```

## 如何获取 Discord 用户令牌

警告：使用用户令牌可能违反 Discord 的服务条款，请谨慎使用。

获取用户令牌的方法：

1. 在 Discord 网页版中登录您的账户
2. 按 F12 打开开发者工具
3. 切换到“Application”标签
4. 刷新页面
5. 进入Local Storage
6. 在Local Storage中找到 `https://discord.com` ，找到key值为tokens，Value值冒号后的其值就是您的用户令牌(无需引号)
7. 将该令牌复制到您的 `.env` 文件中

## 使用方法

使用频道 ID 作为参数运行脚本：

```
python discord_scraper.py <server_id/channel_id> [选项]
```

### 命令行参数

```
位置参数:
  channel                Discord频道ID或服务器ID/频道ID组合 (例如: 66666666/66666666)

可选参数:
  -h, --help            显示帮助信息并退出
  -l LIMIT, --limit LIMIT
                        要获取的最大消息数量 (默认: 无限制)
  -f {markdown,json,html}, --format {markdown,json,html}
                        导出格式 (默认: markdown)
  -o OUTPUT, --output OUTPUT
                        输出文件路径 (默认: 自动生成)
  -c CONCURRENT, --concurrent CONCURRENT
                        并发请求数量 (默认: 5)
  -r RETRIES, --retries RETRIES
                        请求失败时的最大重试次数 (默认: 3)
  -v, --verbose         显示详细日志信息
```

### 示例

```bash
# 从频道获取所有消息，保存为Markdown
python discord_scraper.py 66666666/66666666

# 从频道获取最新1000条消息
python discord_scraper.py 66666666/66666666 -l 1000

# 导出为JSON格式
python discord_scraper.py 66666666/66666666 -f json

# 导出为HTML格式，并指定输出文件
python discord_scraper.py 66666666/66666666 -f html -o discord_export.html

# 设置并发请求数为10，提高性能
python discord_scraper.py 66666666/66666666 -c 10

# 增加重试次数，提高稳定性
python discord_scraper.py 66666666/66666666 -r 5

# 显示详细日志
python discord_scraper.py 66666666/66666666 -v
```

## 输出结果

脚本将创建一个使用以下命名约定的文件：
```
discord_messages_<channel_name>_<timestamp>.<format>
```

其中 `<format>` 可以是 `md`、`json` 或 `html`，取决于选择的导出格式。

### Markdown 格式

Markdown 文件包括：
- 频道名称
- 导出时间戳
- 总消息数
- 服务器角色列表（按重要性排序）
- 所有消息包含：
  - 作者名称
  - **作者角色**（显示最高级别角色）
  - 时间戳
  - 消息内容
  - 附件（带链接）
  - 嵌入内容
  - 反应（表情反应）

### JSON 格式

JSON 文件包含结构化数据，便于程序处理：
- 频道信息
- 服务器信息（如果可用）
- 服务器角色列表
- 完整的消息数据，包括：
  - 作者信息（包括角色）
  - 时间戳
  - 消息内容
  - 附件
  - 嵌入内容
  - 反应

### HTML 格式

HTML 文件提供美观的网页视图：
- 响应式设计，适合在浏览器中查看
- 彩色角色标签
- 格式化的消息内容
- 可点击的附件链接
- 嵌入内容和反应的清晰展示

## 性能优化

本工具使用异步请求和并发处理来提高性能：

- 使用 `aiohttp` 替代 `requests` 进行异步HTTP请求
- 并发获取用户角色信息，可通过 `-c` 参数调整并发数
- 使用缓存减少重复请求
- 自动重试机制，可通过 `-r` 参数调整重试次数
- 优化数据处理流程
- 速率限制自动处理，避免被Discord封禁

## 限制

- Discord API 有速率限制，可能会减慢大型爬取（但本工具会自动处理速率限制）
- 高流量频道中的非常旧的消息可能需要很长时间才能获取
- 使用用户令牌可能违反 Discord 的服务条款，请谨慎使用
- 获取用户角色需要服务器ID，如果未提供，将尝试从频道信息中获取
- HTML和Markdown格式的角色颜色显示可能在某些Markdown查看器中不支持

## 故障排除

- 如果遇到频繁的速率限制，尝试减少并发请求数（使用 `-c` 参数）
- 如果遇到网络问题，尝试增加重试次数（使用 `-r` 参数）
- 使用 `-v` 参数启用详细日志，以便更好地诊断问题
- 如果无法获取用户角色，请确保提供了正确的服务器ID（格式为 `server_id/channel_id`）
- 如果导出的HTML文件中角色颜色不显示，请尝试使用现代浏览器打开

## 许可证

[MIT 许可证](LICENSE)
