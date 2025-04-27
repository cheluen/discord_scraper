#!/usr/bin/env python3
"""
Discord 频道消息爬虫

此脚本从指定的 Discord 频道获取历史消息并导出为 Markdown、JSON或HTML文件。
使用用户令牌而不是机器人令牌，不需要服务器管理员权限。
支持获取用户角色信息，并在导出的文件中标注。

警告：使用用户令牌可能违反 Discord 的服务条款，请谨慎使用。
"""

import os
import re
import sys
import json
import logging
import asyncio
import aiohttp
import requests
import argparse
import time
from datetime import datetime
from typing import List, Dict, Optional, Any, Set, Tuple, Union
from dotenv import load_dotenv
from tqdm import tqdm
from tqdm.asyncio import tqdm_asyncio
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('discord_scraper')

# 加载环境变量
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Discord API 端点
API_BASE = 'https://discord.com/api/v10'

# 缓存
GUILD_ROLES_CACHE = {}  # 服务器角色缓存
GUILD_MEMBERS_CACHE = {}  # 服务器成员缓存

# 导出格式
EXPORT_FORMAT_MARKDOWN = "markdown"
EXPORT_FORMAT_JSON = "json"
EXPORT_FORMAT_HTML = "html"
VALID_EXPORT_FORMATS = [EXPORT_FORMAT_MARKDOWN, EXPORT_FORMAT_JSON, EXPORT_FORMAT_HTML]

# 默认并发请求数
DEFAULT_CONCURRENT_REQUESTS = 5

# 默认重试次数和重试间隔
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2  # 秒

def parse_arguments():
    """
    解析命令行参数。

    返回:
        解析后的参数命名空间
    """
    parser = argparse.ArgumentParser(
        description="Discord 频道消息爬虫 - 获取Discord频道的历史消息并导出为文件",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "channel",
        help="Discord频道ID或服务器ID/频道ID组合 (例如: 66666666/66666666)"
    )

    # 支持旧的命令行格式（第二个位置参数为limit）
    parser.add_argument(
        "legacy_limit",
        nargs="?",
        type=int,
        help=argparse.SUPPRESS,  # 隐藏此参数的帮助信息
        default=None
    )

    parser.add_argument(
        "-l", "--limit",
        type=int,
        help="要获取的最大消息数量 (默认: 无限制)",
        default=None
    )

    parser.add_argument(
        "-f", "--format",
        choices=VALID_EXPORT_FORMATS,
        help="导出格式 (markdown, json, html)",
        default=EXPORT_FORMAT_MARKDOWN
    )

    parser.add_argument(
        "-o", "--output",
        help="输出文件路径 (默认: 自动生成)",
        default=None
    )

    parser.add_argument(
        "-c", "--concurrent",
        type=int,
        help=f"并发请求数量 (默认: {DEFAULT_CONCURRENT_REQUESTS})",
        default=DEFAULT_CONCURRENT_REQUESTS
    )

    parser.add_argument(
        "-r", "--retries",
        type=int,
        help=f"请求失败时的最大重试次数 (默认: {DEFAULT_MAX_RETRIES})",
        default=DEFAULT_MAX_RETRIES
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细日志信息"
    )

    args = parser.parse_args()

    # 如果使用了旧的命令行格式（第二个位置参数为limit）
    if args.legacy_limit is not None and args.limit is None:
        args.limit = args.legacy_limit
        logger.info(f"使用旧的命令行格式，将获取最多 {args.limit} 条消息")

    return args

def parse_channel_id(channel_input: str) -> tuple:
    """
    从用户输入解析服务器 ID 和频道 ID。

    参数:
        channel_input: 格式为 'server_id/channel_id' 或仅 'channel_id' 的字符串

    返回:
        (server_id, channel_id) 或 (None, channel_id) 的元组
    """
    if '/' in channel_input:
        parts = channel_input.strip().split('/')
        if len(parts) == 2:
            return parts[0], parts[1]

    # 如果只提供了频道 ID
    return None, channel_input.strip()

async def get_headers() -> Dict:
    """
    获取请求头。

    返回:
        包含认证信息的请求头字典
    """
    return {
        'Authorization': TOKEN,
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

async def make_request(session, url, method="GET", headers=None, params=None, json_data=None, max_retries=DEFAULT_MAX_RETRIES):
    """
    发送HTTP请求，带重试机制。

    参数:
        session: aiohttp会话
        url: 请求URL
        method: HTTP方法 (GET, POST等)
        headers: 请求头
        params: URL参数
        json_data: JSON数据 (用于POST请求)
        max_retries: 最大重试次数

    返回:
        响应对象和响应内容
    """
    if headers is None:
        headers = await get_headers()

    retries = 0
    last_error = None

    # 增加超时设置
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=10)

    while retries <= max_retries:
        try:
            if method.upper() == "GET":
                async with session.get(url, headers=headers, params=params, timeout=timeout) as response:
                    if response.status == 429:  # 速率限制
                        retry_after = int(response.headers.get('Retry-After', DEFAULT_RETRY_DELAY))
                        logger.warning(f"速率限制，等待 {retry_after} 秒后重试...")
                        await asyncio.sleep(retry_after)
                        retries += 1
                        continue
                    elif response.status >= 500:  # 服务器错误
                        logger.warning(f"服务器错误 ({response.status})，重试中...")
                        await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))  # 指数退避
                        retries += 1
                        continue
                    elif response.status == 401:  # 未授权
                        logger.error(f"未授权错误 (401)，请检查您的Discord令牌是否有效")
                        raise Exception("Discord令牌无效或已过期")
                    elif response.status == 403:  # 禁止访问
                        logger.error(f"禁止访问错误 (403)，您可能没有权限访问此资源")
                        raise Exception("没有权限访问此资源")

                    # 预先读取响应内容，避免连接关闭问题
                    try:
                        content = await response.read()
                        return response, content
                    except Exception as e:
                        logger.warning(f"读取响应内容时出错: {str(e)}，重试中...")
                        await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))
                        retries += 1
                        last_error = e
                        continue

            elif method.upper() == "POST":
                async with session.post(url, headers=headers, params=params, json=json_data, timeout=timeout) as response:
                    if response.status == 429:  # 速率限制
                        retry_after = int(response.headers.get('Retry-After', DEFAULT_RETRY_DELAY))
                        logger.warning(f"速率限制，等待 {retry_after} 秒后重试...")
                        await asyncio.sleep(retry_after)
                        retries += 1
                        continue
                    elif response.status >= 500:  # 服务器错误
                        logger.warning(f"服务器错误 ({response.status})，重试中...")
                        await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))  # 指数退避
                        retries += 1
                        continue
                    elif response.status == 401:  # 未授权
                        logger.error(f"未授权错误 (401)，请检查您的Discord令牌是否有效")
                        raise Exception("Discord令牌无效或已过期")
                    elif response.status == 403:  # 禁止访问
                        logger.error(f"禁止访问错误 (403)，您可能没有权限访问此资源")
                        raise Exception("没有权限访问此资源")

                    # 预先读取响应内容，避免连接关闭问题
                    try:
                        content = await response.read()
                        return response, content
                    except Exception as e:
                        logger.warning(f"读取响应内容时出错: {str(e)}，重试中...")
                        await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))
                        retries += 1
                        last_error = e
                        continue

        except aiohttp.ClientConnectorError as e:
            logger.warning(f"连接错误: {str(e)}，重试中...")
            await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))
            retries += 1
            last_error = e
            continue
        except aiohttp.ClientOSError as e:
            logger.warning(f"操作系统错误: {str(e)}，重试中...")
            await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))
            retries += 1
            last_error = e
            continue
        except aiohttp.ServerDisconnectedError as e:
            logger.warning(f"服务器断开连接: {str(e)}，重试中...")
            await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))
            retries += 1
            last_error = e
            continue
        except asyncio.TimeoutError as e:
            logger.warning(f"请求超时: {str(e)}，重试中...")
            await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))
            retries += 1
            last_error = e
            continue
        except Exception as e:
            logger.warning(f"未知错误: {str(e)}，重试中...")
            await asyncio.sleep(DEFAULT_RETRY_DELAY * (retries + 1))
            retries += 1
            last_error = e
            if retries > max_retries:
                raise

    # 如果所有重试都失败
    if last_error:
        raise Exception(f"请求失败，已达到最大重试次数 ({max_retries}): {str(last_error)}")
    else:
        raise Exception(f"请求失败，已达到最大重试次数 ({max_retries})")

async def create_client_session():
    """
    创建一个配置了连接池和超时的aiohttp会话。

    返回:
        配置好的aiohttp.ClientSession对象
    """
    # 配置连接池
    conn = aiohttp.TCPConnector(
        limit=10,  # 最大连接数
        ttl_dns_cache=300,  # DNS缓存时间（秒）
        ssl=False,  # 禁用SSL验证以提高性能
        force_close=False  # 允许连接重用
    )

    # 配置超时
    timeout = aiohttp.ClientTimeout(
        total=60,  # 总超时时间
        connect=10,  # 连接超时
        sock_connect=10,  # 套接字连接超时
        sock_read=30  # 套接字读取超时
    )

    # 创建会话
    return aiohttp.ClientSession(
        connector=conn,
        timeout=timeout,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    )

async def get_channel_info(channel_id: str, max_retries: int = DEFAULT_MAX_RETRIES) -> Dict:
    """
    获取频道信息。

    参数:
        channel_id: Discord 频道 ID
        max_retries: 最大重试次数

    返回:
        包含频道信息的字典
    """
    session = await create_client_session()
    async with session:
        try:
            response, content = await make_request(
                session,
                f"{API_BASE}/channels/{channel_id}",
                max_retries=max_retries
            )

            if response.status == 200:
                return json.loads(content)
            else:
                error_text = content.decode('utf-8', errors='replace')
                logger.error(f"获取频道信息失败: {response.status} - {error_text}")
                raise Exception(f"获取频道信息失败: {response.status}")
        except Exception as e:
            logger.error(f"获取频道信息时发生错误: {str(e)}")
            raise

async def get_guild_info(guild_id: str, max_retries: int = DEFAULT_MAX_RETRIES) -> Dict:
    """
    获取服务器信息。

    参数:
        guild_id: Discord 服务器 ID
        max_retries: 最大重试次数

    返回:
        包含服务器信息的字典
    """
    session = await create_client_session()
    async with session:
        try:
            response, content = await make_request(
                session,
                f"{API_BASE}/guilds/{guild_id}",
                max_retries=max_retries
            )

            if response.status == 200:
                return json.loads(content)
            else:
                error_text = content.decode('utf-8', errors='replace')
                logger.error(f"获取服务器信息失败: {response.status} - {error_text}")
                raise Exception(f"获取服务器信息失败: {response.status}")
        except Exception as e:
            logger.error(f"获取服务器信息时发生错误: {str(e)}")
            raise

async def get_guild_roles(guild_id: str, max_retries: int = DEFAULT_MAX_RETRIES) -> List[Dict]:
    """
    获取服务器角色列表。

    参数:
        guild_id: Discord 服务器 ID
        max_retries: 最大重试次数

    返回:
        包含角色信息的列表
    """
    # 检查缓存
    if guild_id in GUILD_ROLES_CACHE:
        return GUILD_ROLES_CACHE[guild_id]

    session = await create_client_session()
    async with session:
        try:
            response, content = await make_request(
                session,
                f"{API_BASE}/guilds/{guild_id}/roles",
                max_retries=max_retries
            )

            if response.status == 200:
                roles = json.loads(content)
                # 缓存结果
                GUILD_ROLES_CACHE[guild_id] = roles
                return roles
            else:
                error_text = content.decode('utf-8', errors='replace')
                logger.error(f"获取服务器角色失败: {response.status} - {error_text}")
                return []
        except Exception as e:
            logger.warning(f"获取服务器角色时发生错误: {str(e)}")
            return []

async def get_guild_member(guild_id: str, user_id: str, max_retries: int = DEFAULT_MAX_RETRIES) -> Optional[Dict]:
    """
    获取服务器成员信息。

    参数:
        guild_id: Discord 服务器 ID
        user_id: Discord 用户 ID
        max_retries: 最大重试次数

    返回:
        包含成员信息的字典，如果未找到则返回 None
    """
    # 检查缓存
    cache_key = f"{guild_id}_{user_id}"
    if cache_key in GUILD_MEMBERS_CACHE:
        return GUILD_MEMBERS_CACHE[cache_key]

    session = await create_client_session()
    async with session:
        try:
            response, content = await make_request(
                session,
                f"{API_BASE}/guilds/{guild_id}/members/{user_id}",
                max_retries=max_retries
            )

            if response.status == 200:
                member = json.loads(content)
                # 缓存结果
                GUILD_MEMBERS_CACHE[cache_key] = member
                return member
            else:
                return None
        except Exception as e:
            logger.debug(f"获取用户 {user_id} 的成员信息失败: {str(e)}")
            return None

async def fetch_messages(
    channel_id: str,
    guild_id: Optional[str] = None,
    limit: Optional[int] = None,
    concurrent_requests: int = DEFAULT_CONCURRENT_REQUESTS,
    max_retries: int = DEFAULT_MAX_RETRIES
) -> List[Dict]:
    """
    从频道获取消息，带分页。

    参数:
        channel_id: Discord 频道 ID
        guild_id: Discord 服务器 ID（用于获取用户角色）
        limit: 要获取的最大消息数（None 表示全部）
        concurrent_requests: 并发请求数量
        max_retries: 最大重试次数

    返回:
        Discord 消息列表
    """
    all_messages = []
    last_message_id = None
    batch_size = 100  # Discord API 每次请求的最大消息数

    # 创建进度条
    progress_bar = None
    if limit:
        progress_bar = tqdm(total=limit, desc="获取消息")

    # 获取服务器角色（如果提供了服务器ID）
    guild_roles = []
    if guild_id:
        try:
            guild_roles = await get_guild_roles(guild_id, max_retries)
            logger.info(f"已获取 {len(guild_roles)} 个服务器角色")
        except Exception as e:
            logger.warning(f"获取服务器角色失败: {str(e)}")

    session = await create_client_session()
    async with session:
        while True:
            # 构建 URL 参数
            params = {'limit': min(batch_size, limit - len(all_messages) if limit else batch_size)}
            if last_message_id:
                params['before'] = last_message_id

            # 发送请求
            try:
                response, content = await make_request(
                    session,
                    f"{API_BASE}/channels/{channel_id}/messages",
                    params=params,
                    max_retries=max_retries
                )

                # 检查响应
                if response.status != 200:
                    error_text = content.decode('utf-8', errors='replace')
                    logger.error(f"获取消息失败: {response.status} - {error_text}")
                    break

                # 解析消息
                messages = json.loads(content)
                if not messages:
                    break  # 没有更多消息

                # 如果提供了服务器ID，获取每个消息作者的角色
                if guild_id and guild_roles:
                    # 创建一个信号量来限制并发请求数量
                    semaphore = asyncio.Semaphore(concurrent_requests)

                    async def get_member_with_semaphore(message):
                        async with semaphore:
                            user_id = message['author']['id']
                            try:
                                member = await get_guild_member(guild_id, user_id, max_retries)
                                if member and 'roles' in member:
                                    # 将用户角色ID列表添加到消息中
                                    message['author']['roles'] = member['roles']
                            except Exception as e:
                                logger.debug(f"获取用户 {message['author']['username']} 的角色失败: {str(e)}")

                    # 创建任务列表
                    tasks = [get_member_with_semaphore(message) for message in messages]

                    # 等待所有任务完成
                    await asyncio.gather(*tasks)

                all_messages.extend(messages)

                # 更新进度条
                if progress_bar:
                    progress_bar.update(min(len(messages), limit - progress_bar.n))
                else:
                    logger.info(f"已获取 {len(all_messages)} 条消息...")

                # 检查是否达到限制
                if limit and len(all_messages) >= limit:
                    all_messages = all_messages[:limit]  # 确保不超过限制
                    break

                # 获取最后一条消息的 ID 用于下一页
                last_message_id = messages[-1]['id']

                # 如果获取的消息少于请求的数量，说明已经到达最后一页
                if len(messages) < batch_size:
                    break

            except Exception as e:
                logger.error(f"获取消息时发生错误: {str(e)}")
                break

    if progress_bar:
        progress_bar.close()

    logger.info(f"共获取 {len(all_messages)} 条消息")
    return all_messages

def format_message_to_markdown(message: Dict, guild_roles: List[Dict] = None) -> str:
    """
    将 Discord 消息格式化为 Markdown。

    参数:
        message: Discord 消息对象
        guild_roles: 服务器角色列表（用于解析用户角色）

    返回:
        格式化的 Markdown 字符串
    """
    # 解析时间戳
    timestamp = datetime.fromisoformat(message['timestamp'].replace('Z', '+00:00'))
    formatted_time = timestamp.strftime('%Y-%m-%d %H:%M:%S')

    # 获取作者信息
    author_name = message['author']['username']

    # 处理用户角色信息
    user_roles = []
    if guild_roles and 'roles' in message['author']:
        user_role_ids = message['author']['roles']
        # 将角色ID转换为角色名称
        role_map = {role['id']: role for role in guild_roles}
        user_roles = [role_map[role_id] for role_id in user_role_ids if role_id in role_map]
        # 按照角色位置排序（位置高的角色更重要）
        user_roles.sort(key=lambda r: r.get('position', 0), reverse=True)

    # 格式化用户角色
    roles_str = ""
    if user_roles:
        # 获取最高级别的角色（通常是管理员或特殊角色）
        top_role = user_roles[0] if user_roles else None
        if top_role:
            # 获取角色颜色（十六进制格式）
            role_color = top_role.get('color', 0)
            role_color_hex = f"#{role_color:06x}" if role_color else None

            # 格式化角色名称
            role_name = top_role.get('name', '')
            if role_color_hex and role_color != 0:
                roles_str = f" <span style='color:{role_color_hex}'>**[{role_name}]**</span>"
            else:
                roles_str = f" **[{role_name}]**"

    # 格式化附件
    attachments = ""
    if message.get('attachments'):
        attachments = "\n\n**附件:**\n"
        for attachment in message['attachments']:
            attachments += f"- [{attachment['filename']}]({attachment['url']})\n"

    # 格式化嵌入内容
    embeds = ""
    if message.get('embeds'):
        embeds = "\n\n**嵌入内容:**\n"
        for embed in message['embeds']:
            if embed.get('title'):
                embeds += f"- **{embed['title']}**\n"
            if embed.get('description'):
                embeds += f"  {embed['description']}\n"

    # 格式化内容，处理空消息
    content = message.get('content', '') if message.get('content') else "*[无文本内容]*"

    # 格式化反应
    reactions = ""
    if message.get('reactions'):
        reactions = "\n\n**反应:**\n"
        for reaction in message['reactions']:
            emoji_name = reaction['emoji'].get('name', '')
            emoji_id = reaction['emoji'].get('id', '')

            # 处理自定义表情
            if emoji_id:
                emoji_str = f"<:{emoji_name}:{emoji_id}>"
            else:
                emoji_str = emoji_name

            count = reaction['count']
            reactions += f"- {emoji_str}: {count}\n"

    return f"### {author_name}{roles_str} - {formatted_time}\n\n{content}{attachments}{embeds}{reactions}\n\n---\n\n"

def save_to_json(messages: List[Dict], channel_name: str, guild_info: Dict = None, guild_roles: List[Dict] = None, output_path: Optional[str] = None) -> str:
    """
    将消息保存到 JSON 文件。

    参数:
        messages: Discord 消息列表
        channel_name: 用于文件名的频道名称
        guild_info: 服务器信息
        guild_roles: 服务器角色列表
        output_path: 输出文件路径（如果提供）

    返回:
        保存的文件路径
    """
    # 创建安全的文件名
    safe_channel_name = re.sub(r'[^\w\-_]', '_', channel_name)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    if output_path:
        filename = output_path
    else:
        filename = f"discord_messages_{safe_channel_name}_{timestamp}.json"

    # 创建导出数据
    export_data = {
        "channel_name": channel_name,
        "export_time": datetime.now().isoformat(),
        "message_count": len(messages),
        "messages": sorted(messages, key=lambda m: m['timestamp'])
    }

    # 添加服务器信息（如果有）
    if guild_info:
        export_data["guild_info"] = guild_info

    # 添加角色信息（如果有）
    if guild_roles:
        export_data["guild_roles"] = guild_roles

    # 保存到文件
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    return filename

def save_to_html(messages: List[Dict], channel_name: str, guild_roles: List[Dict] = None, output_path: Optional[str] = None) -> str:
    """
    将消息保存到 HTML 文件。

    参数:
        messages: Discord 消息列表
        channel_name: 用于文件名的频道名称
        guild_roles: 服务器角色列表（用于解析用户角色）
        output_path: 输出文件路径（如果提供）

    返回:
        保存的文件路径
    """
    # 创建安全的文件名
    safe_channel_name = re.sub(r'[^\w\-_]', '_', channel_name)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    if output_path:
        filename = output_path
    else:
        filename = f"discord_messages_{safe_channel_name}_{timestamp}.html"

    # 创建HTML头部
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Discord 消息 - #{channel_name}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            color: #333;
        }}
        h1, h2, h3 {{
            color: #5865F2;
        }}
        .message {{
            margin-bottom: 20px;
            border-bottom: 1px solid #eee;
            padding-bottom: 15px;
        }}
        .message-header {{
            display: flex;
            align-items: baseline;
        }}
        .author {{
            font-weight: bold;
            margin-right: 10px;
        }}
        .role {{
            margin-right: 10px;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.8em;
        }}
        .timestamp {{
            color: #888;
            font-size: 0.9em;
        }}
        .content {{
            margin-top: 5px;
            white-space: pre-wrap;
        }}
        .attachments, .embeds, .reactions {{
            margin-top: 10px;
            font-size: 0.9em;
        }}
        .attachments h4, .embeds h4, .reactions h4 {{
            margin-bottom: 5px;
            color: #555;
        }}
        .roles-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 20px;
        }}
        .role-item {{
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 0.9em;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <h1>Discord 消息 - #{channel_name}</h1>
    <p>导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p>总消息数: {len(messages)}</p>
"""

    # 添加角色信息（如果有）
    if guild_roles:
        html += """    <h2>服务器角色</h2>
    <div class="roles-list">
"""
        # 按照角色位置排序（位置高的角色更重要）
        sorted_roles = sorted(guild_roles, key=lambda r: r.get('position', 0), reverse=True)
        for role in sorted_roles:
            role_name = role.get('name', '')
            role_color = role.get('color', 0)
            role_color_hex = f"#{role_color:06x}" if role_color else "#333333"

            html += f'        <div class="role-item" style="background-color: {role_color_hex}; color: white;">{role_name}</div>\n'

        html += "    </div>\n"

    html += "    <hr>\n\n"

    # 按时间戳排序消息（最旧的在前）
    sorted_messages = sorted(messages, key=lambda m: m['timestamp'])

    # 使用进度条显示格式化进度
    for message in tqdm(sorted_messages, desc="格式化消息"):
        # 解析时间戳
        timestamp = datetime.fromisoformat(message['timestamp'].replace('Z', '+00:00'))
        formatted_time = timestamp.strftime('%Y-%m-%d %H:%M:%S')

        # 获取作者信息
        author_name = message['author']['username']

        # 处理用户角色信息
        role_html = ""
        if guild_roles and 'roles' in message['author']:
            user_role_ids = message['author']['roles']
            # 将角色ID转换为角色名称
            role_map = {role['id']: role for role in guild_roles}
            user_roles = [role_map[role_id] for role_id in user_role_ids if role_id in role_map]
            # 按照角色位置排序（位置高的角色更重要）
            user_roles.sort(key=lambda r: r.get('position', 0), reverse=True)

            if user_roles:
                # 获取最高级别的角色
                top_role = user_roles[0]
                role_name = top_role.get('name', '')
                role_color = top_role.get('color', 0)
                role_color_hex = f"#{role_color:06x}" if role_color else "#333333"

                role_html = f'<span class="role" style="background-color: {role_color_hex}; color: white;">{role_name}</span>'

        # 格式化内容，处理空消息
        content = message.get('content', '') if message.get('content') else "<em>[无文本内容]</em>"

        # 替换Discord的Markdown格式为HTML
        content = content.replace('**', '<strong>').replace('**', '</strong>')
        content = content.replace('*', '<em>').replace('*', '</em>')
        content = content.replace('~~', '<del>').replace('~~', '</del>')
        content = content.replace('`', '<code>').replace('`', '</code>')

        # 处理换行
        content = content.replace('\n', '<br>')

        # 格式化附件
        attachments_html = ""
        if message.get('attachments'):
            attachments_html = "<div class='attachments'>\n        <h4>附件:</h4>\n        <ul>\n"
            for attachment in message['attachments']:
                attachments_html += f"            <li><a href='{attachment['url']}' target='_blank'>{attachment['filename']}</a></li>\n"
            attachments_html += "        </ul>\n    </div>\n"

        # 格式化嵌入内容
        embeds_html = ""
        if message.get('embeds'):
            embeds_html = "<div class='embeds'>\n        <h4>嵌入内容:</h4>\n        <ul>\n"
            for embed in message['embeds']:
                if embed.get('title'):
                    embeds_html += f"            <li><strong>{embed['title']}</strong>"
                    if embed.get('description'):
                        embeds_html += f"<br>{embed['description']}"
                    embeds_html += "</li>\n"
                elif embed.get('description'):
                    embeds_html += f"            <li>{embed['description']}</li>\n"
            embeds_html += "        </ul>\n    </div>\n"

        # 格式化反应
        reactions_html = ""
        if message.get('reactions'):
            reactions_html = "<div class='reactions'>\n        <h4>反应:</h4>\n        <ul>\n"
            for reaction in message['reactions']:
                emoji_name = reaction['emoji'].get('name', '')
                count = reaction['count']
                reactions_html += f"            <li>{emoji_name}: {count}</li>\n"
            reactions_html += "        </ul>\n    </div>\n"

        # 组合消息HTML
        html += f"""    <div class="message">
        <div class="message-header">
            <span class="author">{author_name}</span>
            {role_html}
            <span class="timestamp">{formatted_time}</span>
        </div>
        <div class="content">{content}</div>
        {attachments_html}{embeds_html}{reactions_html}
    </div>
"""

    # 添加HTML尾部
    html += """</body>
</html>"""

    # 保存到文件
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html)

    return filename

def save_to_markdown(messages: List[Dict], channel_name: str, guild_roles: List[Dict] = None, output_path: Optional[str] = None) -> str:
    """
    将消息保存到 Markdown 文件。

    参数:
        messages: Discord 消息列表
        channel_name: 用于文件名的频道名称
        guild_roles: 服务器角色列表（用于解析用户角色）
        output_path: 输出文件路径（如果提供）

    返回:
        保存的文件路径
    """
    # 创建安全的文件名
    safe_channel_name = re.sub(r'[^\w\-_]', '_', channel_name)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    if output_path:
        filename = output_path
    else:
        filename = f"discord_messages_{safe_channel_name}_{timestamp}.md"

    # 创建标题
    header = f"# Discord 消息 - #{channel_name}\n\n"
    header += f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += f"总消息数: {len(messages)}\n\n"

    # 添加角色信息（如果有）
    if guild_roles:
        header += "## 服务器角色\n\n"
        # 按照角色位置排序（位置高的角色更重要）
        sorted_roles = sorted(guild_roles, key=lambda r: r.get('position', 0), reverse=True)
        for role in sorted_roles:
            role_name = role.get('name', '')
            role_color = role.get('color', 0)
            role_color_hex = f"#{role_color:06x}" if role_color else "#000000"

            if role_color != 0:
                header += f"- <span style='color:{role_color_hex}'>**{role_name}**</span>\n"
            else:
                header += f"- **{role_name}**\n"

    header += "\n---\n\n"

    # 按时间戳排序消息（最旧的在前）
    sorted_messages = sorted(messages, key=lambda m: m['timestamp'])

    # 格式化所有消息
    content = header

    # 使用进度条显示格式化进度
    for message in tqdm(sorted_messages, desc="格式化消息"):
        content += format_message_to_markdown(message, guild_roles)

    # 保存到文件
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

    return filename

def save_messages(messages: List[Dict], channel_name: str, export_format: str, guild_info: Dict = None, guild_roles: List[Dict] = None, output_path: Optional[str] = None) -> str:
    """
    将消息保存到指定格式的文件。

    参数:
        messages: Discord 消息列表
        channel_name: 用于文件名的频道名称
        export_format: 导出格式 (markdown, json, html)
        guild_info: 服务器信息
        guild_roles: 服务器角色列表
        output_path: 输出文件路径（如果提供）

    返回:
        保存的文件路径
    """
    if export_format == EXPORT_FORMAT_JSON:
        return save_to_json(messages, channel_name, guild_info, guild_roles, output_path)
    elif export_format == EXPORT_FORMAT_HTML:
        return save_to_html(messages, channel_name, guild_roles, output_path)
    else:  # 默认为 Markdown
        return save_to_markdown(messages, channel_name, guild_roles, output_path)

async def main_async():
    """异步主函数。"""
    if not TOKEN:
        logger.error("未找到 Discord 令牌。请在 .env 文件中设置 DISCORD_TOKEN 环境变量。")
        return

    try:
        # 解析命令行参数
        args = parse_arguments()

        # 设置日志级别
        if args.verbose:
            logger.setLevel(logging.DEBUG)
            logger.debug("已启用详细日志模式")

        # 解析频道ID和服务器ID
        guild_id, channel_id = parse_channel_id(args.channel)

        # 获取频道信息
        logger.info(f"获取频道 {channel_id} 的信息...")
        channel_info = await get_channel_info(channel_id, args.retries)
        channel_name = channel_info.get('name', f"channel_{channel_id}")

        # 如果没有提供服务器ID，尝试从频道信息中获取
        guild_info = None
        if not guild_id and 'guild_id' in channel_info:
            guild_id = channel_info['guild_id']
            logger.info(f"从频道信息中获取到服务器ID: {guild_id}")

            # 获取服务器信息
            try:
                guild_info = await get_guild_info(guild_id, args.retries)
                logger.info(f"已获取服务器信息: {guild_info.get('name', guild_id)}")
            except Exception as e:
                logger.warning(f"获取服务器信息失败: {str(e)}")

        # 获取服务器角色（如果有服务器ID）
        guild_roles = None
        if guild_id:
            logger.info(f"获取服务器 {guild_id} 的角色信息...")
            try:
                guild_roles = await get_guild_roles(guild_id, args.retries)
                logger.info(f"已获取 {len(guild_roles)} 个服务器角色")
            except Exception as e:
                logger.warning(f"获取服务器角色失败: {str(e)}")

        logger.info(f"开始从 #{channel_name} 获取消息")
        start_time = time.time()

        # 获取消息
        messages = await fetch_messages(
            channel_id,
            guild_id,
            args.limit,
            args.concurrent,
            args.retries
        )

        # 计算获取消息所用时间
        elapsed_time = time.time() - start_time
        messages_per_second = len(messages) / elapsed_time if elapsed_time > 0 else 0
        logger.info(f"获取消息完成，用时 {elapsed_time:.2f} 秒，平均速度 {messages_per_second:.2f} 条/秒")

        # 保存消息
        if messages:
            filename = save_messages(
                messages,
                channel_name,
                args.format,
                guild_info,
                guild_roles,
                args.output
            )
            logger.info(f"消息已保存到 {filename}")
        else:
            logger.warning("未获取到任何消息")

    except KeyboardInterrupt:
        logger.info("用户中断操作")
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")
        if logger.level <= logging.DEBUG:
            import traceback
            logger.debug(traceback.format_exc())

def main():
    """脚本的主入口点。"""
    # 运行异步主函数
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
