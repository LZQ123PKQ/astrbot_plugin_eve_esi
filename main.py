from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from datetime import datetime, date, timezone, timedelta
import aiohttp
import asyncio
import json
import os
import time
import re
import sys

# 导入 effect 字典（动态导入以兼容不同环境）
def _import_effect_dict():
    """动态导入 effect_dict 模块"""
    try:
        # 尝试相对导入（AstrBot 插件标准方式）
        from . import effect_dict
        return effect_dict
    except ImportError:
        try:
            # 尝试绝对导入
            import effect_dict
            return effect_dict
        except ImportError:
            # 尝试通过路径导入
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
            import effect_dict
            return effect_dict

# 获取 effect_dict 模块
_effect_dict = _import_effect_dict()
load_effect_descriptions = _effect_dict.load_effect_descriptions
identify_skill_type = _effect_dict.identify_skill_type
is_role_bonus = _effect_dict.is_role_bonus
should_hide_effect = _effect_dict.should_hide_effect
get_effect_description = _effect_dict.get_effect_description

@register("eve_esi", "LZQ123PKQ", "EVE市场助手", "2.1.3")
class EveESIPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 使用 AstrBot 的 data 目录存储配置，防止更新时被覆盖
        # 按照 AstrBot 官方文档推荐的方式获取数据目录
        from pathlib import Path
        self.data_dir = str(Path(get_astrbot_data_path()) / "plugin_data" / "eve_esi")
        os.makedirs(self.data_dir, exist_ok=True)
        # 简称字典文件
        self.alias_file = os.path.join(self.data_dir, "aliases.json")
        # 加载简称字典
        self.aliases = self._load_aliases()
        # 服务器状态监控配置
        self.monitor_config_file = os.path.join(self.data_dir, "monitor_config.json")
        self.monitor_config = self._load_monitor_config()
        # 服务器状态监控任务
        self.monitor_task = None
        # 服务器状态记录（按群聊分别记录）
        self.group_server_status = {}  # {group_id: last_status}
        # 记录今天是否已经检测到开服（每天重置）
        self.today_online_notified = set()  # {group_id}
        self.last_check_date = None
        # 初始化aiohttp ClientSession
        self.session = None
        
    async def initialize(self):
        """插件初始化方法"""
        logger.info("EVE ESI 插件初始化")
        # 创建aiohttp ClientSession
        self.session = aiohttp.ClientSession()
        # 如果有任何群聊启用了监控，启动监控任务
        enabled_groups = [gid for gid, config in self.monitor_config.items() if config.get('enabled', False)]
        if enabled_groups:
            logger.info(f"发现已启用的监控群聊: {enabled_groups}")
            self._start_monitor_task()
        else:
            logger.info("没有启用的监控群聊")

    async def shutdown(self):
        """插件关闭方法"""
        logger.info("EVE ESI 插件关闭")
        # 停止监控任务
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None
        # 关闭aiohttp ClientSession
        if self.session:
            await self.session.close()
    
    def _load_monitor_config(self):
        """加载监控配置"""
        try:
            if os.path.exists(self.monitor_config_file):
                with open(self.monitor_config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载监控配置失败: {e}")
        # 配置格式: {group_id: {'enabled': True/False}}
        return {}
    
    def _save_monitor_config(self):
        """保存监控配置"""
        try:
            with open(self.monitor_config_file, 'w', encoding='utf-8') as f:
                json.dump(self.monitor_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存监控配置失败: {e}")
    
    def _is_group_monitor_enabled(self, group_id):
        """检查指定群聊是否启用了监控"""
        if not group_id:
            return False
        return self.monitor_config.get(group_id, {}).get('enabled', False)
    
    def _set_group_monitor_enabled(self, group_id, enabled, umo=None):
        """设置指定群聊的监控状态"""
        if not group_id:
            return
        if group_id not in self.monitor_config:
            self.monitor_config[group_id] = {}
        self.monitor_config[group_id]['enabled'] = enabled
        # 保存 unified_msg_origin 用于后续发送消息
        if umo:
            self.monitor_config[group_id]['umo'] = umo
        self._save_monitor_config()
    
    def _start_monitor_task(self):
        """启动监控任务"""
        if self.monitor_task is None:
            self.monitor_task = asyncio.create_task(self._monitor_server_status())
            logger.info("服务器状态监控任务已启动")
        elif self.monitor_task.done():
            # 如果任务已完成，重置并创建新任务
            try:
                self.monitor_task.result()  # 获取结果以清理异常
            except:
                pass
            self.monitor_task = asyncio.create_task(self._monitor_server_status())
            logger.info("服务器状态监控任务已重新启动")
    
    def _stop_monitor_task(self):
        """停止监控任务"""
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            self.monitor_task = None
            logger.info("服务器状态监控任务已停止")
    
    async def _monitor_server_status(self):
        """监控服务器状态的后台任务
        
        监控逻辑：
        1. 用户只需开启一次，永久有效（直到手动关闭）
        2. 每天11:00开始监控
        3. 检测到服务器维护时，发送通知
        4. 检测到服务器开服后，发送通知，当天停止监控该群聊
        5. 第二天11:00自动重新开始监控
        """
        logger.info("服务器状态监控任务开始运行")
        try:
            while True:
                # 获取当前时间（使用局部导入确保兼容性）
                from datetime import datetime
                now = datetime.now()
                today = now.date()
                
                # 检查是否是新的一天，如果是则重置开服通知记录
                if self.last_check_date != today:
                    self.today_online_notified = set()
                    self.last_check_date = today
                    logger.info(f"新的一天 {today}，重置开服通知记录")
                
                # 检查是否在监控时间段（11:00开始）
                if now.hour >= 11:
                    logger.debug(f"当前时间 {now.hour}:{now.minute}，开始检测服务器状态")
                    # 查询服务器状态
                    is_online = await self._check_server_online()
                    logger.debug(f"服务器状态: {'在线' if is_online else '维护中'}")
                    
                    # 获取所有启用的监控群组
                    enabled_groups = [gid for gid, config in self.monitor_config.items() if config.get('enabled', False)]
                    logger.debug(f"启用的监控群聊: {enabled_groups}")
                    
                    for group_id in enabled_groups:
                        # 如果今天已经通知过开服了，跳过该群聊
                        if group_id in self.today_online_notified:
                            continue
                        
                        # 获取该群聊上次的状态
                        last_status = self.group_server_status.get(group_id)
                        
                        # 状态变化检测
                        if last_status is None:
                            # 首次检测，记录状态
                            self.group_server_status[group_id] = is_online
                            logger.info(f"群聊 {group_id} 首次检测，服务器状态: {'在线' if is_online else '维护中'}")
                            # 如果首次检测就是维护状态，发送维护通知
                            if not is_online:
                                await self._send_server_offline_notification(group_id)
                        elif is_online != last_status:
                            # 状态发生变化
                            if is_online:
                                # 服务器从维护变为正常（开服了）
                                await self._send_server_online_notification(group_id)
                                # 记录今天已经通知过开服
                                self.today_online_notified.add(group_id)
                                logger.info(f"群聊 {group_id} 服务器已开服，今天不再监控该群聊")
                            else:
                                # 服务器从正常变为维护
                                await self._send_server_offline_notification(group_id)
                            
                            # 更新该群聊的状态记录
                            self.group_server_status[group_id] = is_online
                
                # 等待1分钟
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("服务器状态监控任务被取消")
            raise
        except Exception as e:
            logger.error(f"服务器状态监控任务出错: {e}")
    
    async def _check_server_online(self):
        """检查服务器是否在线"""
        try:
            status_url = "https://ali-esi.evepc.163.com/v1/status/"
            async with self.session.get(status_url, timeout=10) as response:
                is_online = response.status == 200
                logger.debug(f"检查服务器状态: HTTP {response.status}, 在线: {is_online}")
                return is_online
        except Exception as e:
            logger.debug(f"检查服务器状态失败: {e}")
            return False
    
    async def _send_server_offline_notification(self, group_id):
        """发送服务器维护通知到指定群聊"""
        try:
            # 获取群聊的 unified_msg_origin
            umo = self.monitor_config.get(group_id, {}).get('umo')
            if not umo:
                logger.error(f"群聊 {group_id} 没有 unified_msg_origin，无法发送通知")
                return
            
            # 使用 LLM 生成消息
            message = await self._generate_llm_message("服务器维护通知", "EVE服务器刚刚进入维护状态")
            await self._send_message_to_group(umo, message)
        except Exception as e:
            logger.error(f"发送维护通知到群组 {group_id} 失败: {e}")
    
    async def _send_server_online_notification(self, group_id):
        """发送服务器开服通知到指定群聊"""
        try:
            # 获取群聊的 unified_msg_origin
            umo = self.monitor_config.get(group_id, {}).get('umo')
            if not umo:
                logger.error(f"群聊 {group_id} 没有 unified_msg_origin，无法发送通知")
                return
            
            # 使用 LLM 生成消息
            message = await self._generate_llm_message("服务器开服通知", "EVE服务器已经开服了")
            await self._send_message_to_group(umo, message)
        except Exception as e:
            logger.error(f"发送开服通知到群组 {group_id} 失败: {e}")
    
    async def _generate_llm_message(self, context_str, default_message):
        """使用 LLM 生成消息"""
        try:
            # 构建提示词
            prompt = f"""你是一位 EVE Online 游戏助手，现在需要向玩家群发送一条消息。

场景：{context_str}
默认消息：{default_message}

请生成一条友好、有趣、符合 EVE 游戏氛围的消息。可以包含一些 EVE 相关的梗或幽默元素。
要求：
1. 消息简洁，不超过100字
2. 语气友好活泼
3. 可以适当使用 emoji

请直接输出消息内容，不要添加任何解释。"""
            
            # 调用 LLM 生成消息 - 使用正确的 API
            from astrbot.api.provider import ProviderRequest
            provider_request = ProviderRequest(prompt=prompt, system_prompt="")
            llm_response = await self.context.llm_chat(provider_request)
            if llm_response and llm_response.completion_text and llm_response.completion_text.strip():
                return llm_response.completion_text.strip()
        except Exception as e:
            logger.error(f"LLM 生成消息失败: {e}")
        
        # 如果 LLM 失败，返回默认消息
        return default_message
    
    async def _send_message_to_group(self, umo, message):
        """发送消息到指定群聊"""
        try:
            # 使用 AstrBot 的消息发送接口 - 使用正确的 API
            from astrbot.api.message_components import Plain
            from astrbot.api.event import MessageChain
            chain = MessageChain([Plain(message)])
            await self.context.send_message(umo, chain)
        except Exception as e:
            logger.error(f"发送消息到群组 {umo} 失败: {e}")

    def _load_aliases(self):
        """加载简称字典"""
        try:
            if os.path.exists(self.alias_file):
                with open(self.alias_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载简称字典失败: {e}")
        return {}

    def _save_aliases(self):
        """保存简称字典"""
        try:
            with open(self.alias_file, 'w', encoding='utf-8') as f:
                json.dump(self.aliases, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存简称字典失败: {e}")

    @filter.command("简称")
    async def add_alias(self, event: AstrMessageEvent):
        """添加简称
        
        支持两种格式:
        /简称 全称=简称
        /简称 简称=全称
        
        系统会自动识别哪个是全称哪个是简称
        """
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /简称 <全称>=<简称> 或 /简称 <简称>=<全称>")
            return
        
        alias_part = " ".join(parts[1:])
        if "=" not in alias_part:
            yield event.plain_result("使用方法: /简称 <全称>=<简称> 或 /简称 <简称>=<全称>")
            return
        
        left_part, right_part = alias_part.split("=", 1)
        left_part = left_part.strip()
        right_part = right_part.strip()
        
        if not left_part or not right_part:
            yield event.plain_result("全称和简称不能为空")
            return
        
        # 智能识别哪个是全称哪个是简称
        full_name = None
        alias = None
        
        # 情况1: 左边在 aliases 的 key 中 -> 左边是全称
        if left_part in self.aliases:
            full_name = left_part
            alias = right_part
        # 情况2: 右边在 aliases 的 key 中 -> 右边是全称
        elif right_part in self.aliases:
            full_name = right_part
            alias = left_part
        # 情况3: 左边在任何一个简称列表中 -> 左边是简称，右边是全称
        else:
            left_is_alias = False
            for fn, aliases in self.aliases.items():
                if left_part in aliases:
                    left_is_alias = True
                    break
            
            if left_is_alias:
                # 左边是简称，右边是全称
                full_name = right_part
                alias = left_part
            else:
                # 默认: 长的是全称，短的是简称
                if len(left_part) >= len(right_part):
                    full_name = left_part
                    alias = right_part
                else:
                    full_name = right_part
                    alias = left_part
        
        # 添加简称
        if full_name not in self.aliases:
            self.aliases[full_name] = []
        
        if alias not in self.aliases[full_name]:
            self.aliases[full_name].append(alias)
            self._save_aliases()
            yield event.plain_result(f"已添加简称: {alias} -> {full_name}")
        else:
            yield event.plain_result(f"简称 {alias} 已存在")

    @filter.command("简称列表")
    async def list_alias(self, event: AstrMessageEvent):
        """查看简称列表"""
        message_str = event.message_str
        parts = message_str.split(" ")
        
        if len(parts) < 2:
            # 显示所有简称
            if not self.aliases:
                yield event.plain_result("暂无简称")
                return
            
            result = "简称列表:\n"
            for full_name, aliases in self.aliases.items():
                result += f"{full_name}: {', '.join(aliases)}\n"
            yield event.plain_result(result)
        else:
            # 查询指定全称或简称
            query = " ".join(parts[1:])
            
            # 检查是否是简称
            for full_name, aliases in self.aliases.items():
                if query in aliases:
                    yield event.plain_result(f"{full_name}: {', '.join(aliases)}")
                    return
            
            # 检查是否是全称
            if query in self.aliases:
                yield event.plain_result(f"{query}: {', '.join(self.aliases[query])}")
            else:
                yield event.plain_result(f"{query} 还没有简称")

    @filter.command("简称删除")
    async def delete_alias(self, event: AstrMessageEvent):
        """删除简称"""
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /简称删除 <简称>")
            return
        
        alias = " ".join(parts[1:])
        
        # 查找并删除简称
        found = False
        for full_name, aliases in list(self.aliases.items()):
            if alias in aliases:
                aliases.remove(alias)
                if not aliases:
                    del self.aliases[full_name]
                found = True
                break
        
        if found:
            self._save_aliases()
            yield event.plain_result(f"已删除简称: {alias}")
        else:
            yield event.plain_result(f"简称 {alias} 不存在")

    @filter.command("jt")
    async def get_jita_price_short(self, event: AstrMessageEvent):
        """查询吉他市场价格（短命令）"""
        async for result in self.get_jita_price(event):
            yield result

    @filter.command("吉他")
    async def get_jita_price(self, event: AstrMessageEvent):
        """查询吉他市场价格"""
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /吉他 <物品名称或ID>")
            return
        
        query = " ".join(parts[1:])
        # 检查是否有简称（遍历所有全名，查找匹配的简称）
        full_name_from_alias = None
        for full_name, aliases in self.aliases.items():
            if query in aliases:
                full_name_from_alias = full_name
                break
        
        if full_name_from_alias:
            logger.info(f"使用简称: {query} -> {full_name_from_alias}")
            query = full_name_from_alias
        
        # 尝试将查询转换为数字（物品ID）
        if query.isdigit():
            item_id = query
            async for result in self._get_jita_price(item_id, event, ''):
                yield result
        else:
            # 先使用市场中心API搜索，将名称转换为ID
            logger.info(f"使用市场中心API搜索物品: {query}")
            market_result = await self.search_item_by_name(query)
            
            if market_result and len(market_result) > 0:
                # 判断用户是否明确查询涂装或蓝图
                is_query_skin = self._is_skin(query)
                is_query_blueprint = self._is_blueprint(query)
                
                if is_query_skin or is_query_blueprint:
                    # 用户明确查询涂装或蓝图，不过滤，显示所有结果
                    result_text = f"找到'{query}'的搜索结果:\n"
                    for i, item in enumerate(market_result[:10], 1):  # 显示前10个结果
                        result_text += f"{i}. {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})\n"
                    result_text += "\n请使用 /吉他 <物品ID> 查看具体物品价格"
                    yield event.plain_result(result_text)
                else:
                    # 用户查询的是缩写，过滤掉涂装和蓝图
                    filtered_result = [
                        item for item in market_result 
                        if not self._is_skin(item.get('typename', '')) 
                        and not self._is_blueprint(item.get('typename', ''))
                    ]
                    skin_count = len(market_result) - len(filtered_result)
                    
                    if filtered_result:
                        # 构建结果文本
                        result_text = f"找到'{query}'的{len(market_result)}个结果\n"
                        if skin_count > 0:
                            result_text += f"其中{skin_count}个为涂装或蓝图暂时过滤\n"
                        result_text += "\n"
                        
                        # 显示前5个的具体价格（卖价在前，买价在后）
                        for i, item in enumerate(filtered_result[:5], 1):
                            item_id = str(item.get('typeid', ''))
                            item_name = item.get('typename', '未知')
                            buy_price, sell_price = await self._get_item_price_info(item_id)
                            result_text += f"{i}. {item_name}\n"
                            result_text += f"   {sell_price}\n"
                            result_text += f"   {buy_price}\n\n"
                        
                        # 如果超过5个，列出剩下的全名
                        if len(filtered_result) > 5:
                            remaining_count = len(filtered_result) - 5
                            result_text += f"其他结果（还有{remaining_count}个）:\n"
                            for i, item in enumerate(filtered_result[5:10], 6):
                                result_text += f"{i}. {item.get('typename', '未知')}\n"
                            if remaining_count > 5:
                                result_text += f"... 还有 {remaining_count - 5} 个结果\n"
                            result_text += "\n"
                        
                        result_text += "如果你的搜索物品不在本列表内，请再详细一点搜索"
                        yield event.plain_result(result_text)
                    else:
                        # 如果过滤后没有结果，提示用户
                        yield event.plain_result("搜索结果前十个都是涂装或蓝图。请再详细一点搜索。")
            else:
                # 如果市场中心没找到，提示用户使用更详细的名称或物品ID
                yield event.plain_result(f"未找到物品'{query}'。提示：请使用更详细的中文名称或物品ID（如34）进行查询。")

    @filter.command("加成")
    async def get_item_info_short(self, event: AstrMessageEvent):
        """查看物品加成信息"""
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /加成 <物品名称或ID>")
            return
        
        query = " ".join(parts[1:])
        # 检查是否有简称（遍历所有全名，查找匹配的简称）
        full_name_from_alias = None
        for full_name, aliases in self.aliases.items():
            if query in aliases:
                full_name_from_alias = full_name
                break
        
        if full_name_from_alias:
            logger.info(f"使用简称: {query} -> {full_name_from_alias}")
            query = full_name_from_alias
        
        # 尝试将查询转换为数字（物品ID）
        if query.isdigit():
            item_id = query
            async for result in self._get_item_info(item_id, event, ''):
                yield result
        else:
            # 先使用市场中心API搜索，将名称转换为ID
            logger.info(f"使用市场中心API搜索物品: {query}")
            market_result = await self.search_item_by_name(query)
            
            if market_result and len(market_result) > 0:
                # 判断用户是否明确查询涂装或蓝图
                is_query_skin = self._is_skin(query)
                is_query_blueprint = self._is_blueprint(query)
                
                if is_query_skin or is_query_blueprint:
                    # 用户明确查询涂装或蓝图，不过滤，显示所有结果
                    result_text = f"找到'{query}'的搜索结果:\n"
                    for i, item in enumerate(market_result[:10], 1):  # 显示前10个结果
                        result_text += f"{i}. {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})\n"
                    result_text += "\n请使用 /加成 <物品ID> 查看具体物品加成信息"
                    yield event.plain_result(result_text)
                else:
                    # 用户查询的是缩写，过滤掉涂装和蓝图
                    filtered_result = [
                        item for item in market_result 
                        if not self._is_skin(item.get('typename', '')) 
                        and not self._is_blueprint(item.get('typename', ''))
                    ]
                    
                    if filtered_result:
                        if len(filtered_result) == 1:
                            # 只有一个结果，直接显示物品信息
                            item = filtered_result[0]
                            item_id = str(item.get('typeid', ''))
                            item_name = item.get('typename', '')
                            async for result in self._get_item_info(item_id, event, item_name):
                                yield result
                        else:
                            # 多个结果，列出所有非涂装非蓝图结果，引导用户再用命令查看
                            result_text = f"找到'{query}'的{len(filtered_result)}个结果:\n"
                            for i, item in enumerate(filtered_result[:15], 1):  # 显示前15个结果
                                result_text += f"{i}. {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})\n"
                            if len(filtered_result) > 15:
                                result_text += f"... 还有 {len(filtered_result) - 15} 个结果\n"
                            result_text += "\n请使用 /加成 <物品ID> 查看具体物品加成信息"
                            yield event.plain_result(result_text)
                    else:
                        # 如果过滤后没有结果，提示用户
                        yield event.plain_result("搜索结果前十个都是涂装或蓝图。请再详细一点搜索。")
            else:
                # 如果市场中心没找到，提示用户使用更详细的名称或物品ID
                yield event.plain_result(f"未找到物品'{query}'。提示：请使用更详细的中文名称或物品ID（如34）进行查询。")

    @filter.command("加成修改")
    async def modify_effect_description(self, event: AstrMessageEvent):
        """修改加成描述字典或技能类型映射

        使用方式1 - 修改加成描述(zidian1.txt):
        /加成修改 原描述(effect_name|attr1/attr2/...)=新描述
        示例: /加成修改 能量炮台最佳射程加成(shipETOptimalRange2AF|maxRange)=小型能量炮台最佳射程加成

        使用方式2 - 修改技能类型名称(effect_dict.py):
        /加成修改 原技能名=新技能名
        示例: /加成修改 旗舰巡洋舰操作=航空母舰操作

        使用方式3 - 添加effect到技能类型映射:
        /加成修改 描述(effect_name|attr1/attr2/...)+技能类型名
        示例: /加成修改 武器扰断器效果加成(shipBonusEwWeaponDisruptionStrengthAF2|trackingSpeedBonus)+艾玛航空母舰操作
        """
        message_str = event.message_str
        parts = message_str.split(" ", 1)
        if len(parts) < 2:
            yield event.plain_result("使用方法1: /加成修改 原描述(effect_name|modified_attr|modifying_attr)=新描述\n使用方法2: /加成修改 原技能名=新技能名\n使用方法3: /加成修改 描述(effect_name|modified_attr|modifying_attr)+技能类型名\n示例: /加成修改 拦截失效装置最大锁定范围加成(interceptorNullificationRoleBonus|maxTargetRangeBonus|shipBonusRole1)=拦截失效装置最大锁定距离加成\n示例: /加成修改 旗舰巡洋舰操作=航空母舰操作\n示例: /加成修改 拦截失效装置最大锁定范围加成(interceptorNullificationRoleBonus|maxTargetRangeBonus|shipBonusRole1)+艾玛护卫舰操作")
            return
        
        modify_part = parts[1]
        
        # 判断是哪种格式
        # 格式1: 描述(effect_name|modified_attr|modifying_attr)=新描述 - 修改 zidian1.txt
        # 格式2: 技能名=新技能名 - 修改 effect_dict.py 技能名称
        # 格式3: 描述(effect_name|modified_attr|modifying_attr)+技能类型名 - 添加 effect 到技能映射
        
        if "+" in modify_part:
            # 格式3: 添加 effect 到技能类型映射
            effect_part, skill_type = modify_part.split("+", 1)
            effect_part = effect_part.strip()
            skill_type = skill_type.strip()
            
            # 解析 effect 部分
            # 格式: 描述(effect_name|modified_attr|modifying_attr)
            effect_match = re.match(r'(.+)\(([^|)]+)\|([^|)]+)\|([^)]+)\)', effect_part)
            if not effect_match:
                yield event.plain_result(f"格式错误: {effect_part}\n需要使用格式: 描述(effect_name|modified_attr|modifying_attr)+技能类型名\n示例: /加成修改 拦截失效装置最大锁定范围加成(interceptorNullificationRoleBonus|maxTargetRangeBonus|shipBonusRole1)+艾玛护卫舰操作")
                return
            
            async for result in self._add_effect_to_skill_type(effect_match, skill_type, event):
                yield result
        elif "=" in modify_part:
            # 格式1 或 格式2
            old_part, new_desc = modify_part.split("=", 1)
            old_part = old_part.strip()
            new_desc = new_desc.strip()
            
            if not new_desc:
                yield event.plain_result("新描述不能为空")
                return
            
            # 新格式: 描述(effect_name|modified_attr|modifying_attr)
            old_match = re.match(r'(.+)\(([^|]+)\|([^|)]+)\|([^)]+)\)', old_part)
            
            if old_match:
                # 格式1: 修改 zidian1.txt
                async for result in self._modify_zidian1(old_match, new_desc, event):
                    yield result
            else:
                # 格式2: 修改 effect_dict.py 技能名称
                async for result in self._modify_effect_dict(old_part, new_desc, event):
                    yield result
        else:
            yield event.plain_result("格式错误，需要使用 = 或 + 分隔\n示例: /加成修改 拦截失效装置最大锁定范围加成(interceptorNullificationRoleBonus|maxTargetRangeBonus|shipBonusRole1)=拦截失效装置最大锁定距离加成\n示例: /加成修改 旗舰巡洋舰操作=航空母舰操作\n示例: /加成修改 拦截失效装置最大锁定范围加成(interceptorNullificationRoleBonus|maxTargetRangeBonus|shipBonusRole1)+艾玛护卫舰操作")
    
    async def _modify_zidian1(self, old_match, new_desc, event):
        """修改 zidian1.txt 中的 effect 描述
        
        新格式: 描述: effect_name|modified_attr|modifying_attr
        """
        old_desc = old_match.group(1).strip()
        old_effect = old_match.group(2).strip()
        old_modified_attr = old_match.group(3).strip()
        
        # 读取 zidian1.txt
        zidian1_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zidian1.txt")
        
        try:
            with open(zidian1_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # 查找匹配的行
            found = False
            modified_line = None
            
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                
                # 解析行格式: 描述: effect_name|modified_attr|modifying_attr
                if ':' not in line or '|' not in line:
                    continue
                
                desc_part, effect_part = line.split(':', 1)
                desc_part = desc_part.strip()
                effect_part = effect_part.strip()
                
                # 解析 effect_name|modified_attr|modifying_attr
                effect_parts = effect_part.split('|')
                if len(effect_parts) >= 3:
                    file_effect = effect_parts[0].strip()
                    file_modified_attr = effect_parts[1].strip()
                    
                    # 检查 effect_name 和 modified_attr 是否匹配
                    if file_effect == old_effect and file_modified_attr == old_modified_attr:
                        # 找到了匹配的行
                        found = True
                        
                        # 构建新行（只修改描述部分）
                        new_line = f"{new_desc}: {effect_part}\n"
                        modified_line = i + 1  # 行号从1开始
                        lines[i] = new_line
                        break
            
            if not found:
                yield event.plain_result(f"未找到匹配的映射: {old_desc}({old_effect}|{old_modified_attr})\n请检查 effect_name 和 modified_attr 是否正确。")
                return
            
            # 写回文件
            with open(zidian1_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            # 重新加载 effect_dict
            logger.info("zidian1.txt 已修改，下次查询时会自动加载新配置")
            
            yield event.plain_result(f"修改成功！\n第 {modified_line} 行:\n{old_desc}({old_effect}|{old_modified_attr})\n↓\n{new_desc}")
            
        except Exception as e:
            logger.error(f"修改 zidian1.txt 失败: {e}")
            yield event.plain_result(f"修改失败: {e}")
    
    async def _modify_effect_dict(self, old_skill_name, new_skill_name, event):
        """修改 effect_dict.py 中的技能名称"""
        effect_dict_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "effect_dict.py")
        
        try:
            with open(effect_dict_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 查找并替换技能名称
            # 格式: '旧技能名': [
            pattern = f"'{old_skill_name}': ["
            replacement = f"'{new_skill_name}': ["
            
            if pattern not in content:
                yield event.plain_result(f"未找到技能名称: {old_skill_name}\n请检查技能名称是否正确。")
                return
            
            # 替换
            new_content = content.replace(pattern, replacement)
            
            # 写回文件
            with open(effect_dict_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # 重新加载 effect_dict 模块
            try:
                import importlib
                global _effect_dict, identify_skill_type, is_role_bonus, should_hide_effect, get_effect_description
                _effect_dict = importlib.reload(_effect_dict)
                identify_skill_type = _effect_dict.identify_skill_type
                is_role_bonus = _effect_dict.is_role_bonus
                should_hide_effect = _effect_dict.should_hide_effect
                get_effect_description = _effect_dict.get_effect_description
                logger.info("已重新加载 effect_dict 模块")
            except Exception as e:
                logger.error(f"重新加载 effect_dict 失败: {e}")

            # 计算行号
            lines_before = content[:content.find(pattern)].count('\n') + 1

            yield event.plain_result(f"修改成功！\n第 {lines_before} 行:\n{old_skill_name}\n↓\n{new_skill_name}")
            
        except Exception as e:
            logger.error(f"修改 effect_dict.py 失败: {e}")
            yield event.plain_result(f"修改失败: {e}")
    
    async def _add_effect_to_skill_type(self, effect_match, skill_type, event):
        """添加 modifying_attribute 到技能类型映射
        
        流程:
        1. 从输入提取 effect_name
        2. 查询 effects.json 获取 modifying_attribute_id
        3. 查询 attributes.json 获取 modifying_attribute 的 name
        4. 将 name 添加到技能类型映射
        """
        effect_desc = effect_match.group(1).strip()
        effect_name = effect_match.group(2).strip()
        # modified_attrs_str 是用户输入的 modified_attr 列表（用于显示，不用于查询）
        modified_attrs_str = effect_match.group(3).strip()
        
        # 加载 effects.json 和 attributes.json
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        effects_path = os.path.join(plugin_dir, "effects.json")
        attributes_path = os.path.join(plugin_dir, "attributes.json")
        
        try:
            # 加载 effects.json
            with open(effects_path, 'r', encoding='utf-8') as f:
                effects_data = json.load(f)
            
            # 查找 effect
            target_effect = None
            for effect in effects_data:
                if effect.get('name') == effect_name:
                    target_effect = effect
                    break
            
            if not target_effect:
                yield event.plain_result(f"未找到 effect: {effect_name}")
                return
            
            # 获取 modifying_attribute_id
            modifiers = target_effect.get('modifiers', [])
            if not modifiers:
                yield event.plain_result(f"effect {effect_name} 没有 modifiers")
                return
            
            modifying_attr_id = modifiers[0].get('modifying_attribute_id')
            if not modifying_attr_id:
                yield event.plain_result(f"无法获取 modifying_attribute_id")
                return
            
            # 加载 attributes.json
            with open(attributes_path, 'r', encoding='utf-8') as f:
                attributes_data = json.load(f)
            
            # 查找 attribute
            modifying_attr_name = None
            for attr in attributes_data:
                if attr.get('attribute_id') == modifying_attr_id:
                    modifying_attr_name = attr.get('name')
                    break
            
            if not modifying_attr_name:
                yield event.plain_result(f"未找到 attribute_id: {modifying_attr_id}")
                return
            
            # 现在将 modifying_attr_name 添加到技能类型映射
            effect_dict_path = os.path.join(plugin_dir, "effect_dict.py")
            
            with open(effect_dict_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 查找 SKILL_TYPE_RULES 字典
            if 'SKILL_TYPE_RULES = {' not in content:
                yield event.plain_result("未找到 SKILL_TYPE_RULES 字典")
                return
            
            # 检查技能类型是否已存在
            skill_pattern = f"'{skill_type}': ["
            
            if skill_pattern in content:
                # 技能类型已存在，添加 effect 到现有列表
                start_idx = content.find(skill_pattern)
                if start_idx == -1:
                    yield event.plain_result(f"未找到技能类型: {skill_type}")
                    return
                
                list_start = start_idx + len(skill_pattern)
                
                # 查找该列表的闭合括号
                bracket_count = 1
                list_end = list_start
                while bracket_count > 0 and list_end < len(content):
                    if content[list_end] == '[':
                        bracket_count += 1
                    elif content[list_end] == ']':
                        bracket_count -= 1
                    list_end += 1
                
                if bracket_count != 0:
                    yield event.plain_result(f"解析 SKILL_TYPE_RULES 失败: 括号不匹配")
                    return
                
                list_content = content[list_start:list_end-1]
                
                # 检查是否已存在
                if f"'{modifying_attr_name}'" in list_content:
                    yield event.plain_result(f"'{modifying_attr_name}' 已存在于 '{skill_type}' 中")
                    return
                
                # 在列表最后添加
                last_quote_idx = list_content.rfind("'")
                if last_quote_idx == -1:
                    new_list_content = f"\n        '{modifying_attr_name}'\n    "
                else:
                    new_list_content = list_content.rstrip() + f",\n        '{modifying_attr_name}'\n    "
                
                new_content = content[:list_start] + new_list_content + content[list_end-1:]
                
                with open(effect_dict_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                
                action = "添加"
            else:
                # 技能类型不存在，创建新的技能类型
                dict_start = content.find('SKILL_TYPE_RULES = {')
                if dict_start == -1:
                    yield event.plain_result("未找到 SKILL_TYPE_RULES 字典")
                    return
                
                first_skill_pattern = "'突击护卫舰操作': ["
                first_skill_idx = content.find(first_skill_pattern, dict_start)
                
                if first_skill_idx == -1:
                    insert_pos = content.find('{', dict_start) + 1
                else:
                    insert_pos = first_skill_idx
                
                new_skill_entry = f"    '{skill_type}': [\n        '{modifying_attr_name}'\n    ],\n    "
                
                new_content = content[:insert_pos] + new_skill_entry + content[insert_pos:]
                
                with open(effect_dict_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                
                action = "创建"
            
            # 重新加载 effect_dict 模块
            try:
                import importlib
                global _effect_dict, identify_skill_type, is_role_bonus, should_hide_effect, get_effect_description
                _effect_dict = importlib.reload(_effect_dict)
                identify_skill_type = _effect_dict.identify_skill_type
                is_role_bonus = _effect_dict.is_role_bonus
                should_hide_effect = _effect_dict.should_hide_effect
                get_effect_description = _effect_dict.get_effect_description
                logger.info("已重新加载 effect_dict 模块")
            except Exception as e:
                logger.error(f"重新加载 effect_dict 失败: {e}")

            yield event.plain_result(f"{action}成功！\n查询结果: {effect_name} -> modifying_attribute_id: {modifying_attr_id} -> name: {modifying_attr_name}\n已将 '{modifying_attr_name}' {action}到 '{skill_type}'")
            
        except Exception as e:
            logger.error(f"添加 effect 到技能类型失败: {e}")
            yield event.plain_result(f"添加失败: {e}")

    def _format_price(self, price):
        """格式化价格显示
        
        格式：数字（简化表示）
        例如：1,234,567,890.12 (12亿) 或 12,345,678.90 (1234万)
        """
        if price is None or price == 0:
            return "0"
        
        # 千分位格式化
        formatted = f"{price:,.2f}"
        
        # 计算简化表示，只显示亿和万
        if price >= 100000000:  # 1亿以上
            simplified = price / 100000000
            if simplified >= 100:
                return f"{formatted} ({int(simplified)}亿)"
            else:
                return f"{formatted} ({simplified:.1f}亿)"
        elif price >= 10000:  # 1万以上
            simplified = price / 10000
            return f"{formatted} ({int(simplified)}万)"
        else:
            return formatted

    async def _get_item_price_info(self, item_id):
        """获取物品简要价格信息（用于多结果列表显示）"""
        jita_region_id = 10000002
        
        try:
            # 获取买单和卖单
            buy_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=buy")
            sell_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=sell")
            
            # 计算最高买单和最低卖单
            highest_buy = 0
            if buy_orders and len(buy_orders) > 0:
                highest_buy = max(order['price'] for order in buy_orders)
            
            lowest_sell = 0
            if sell_orders and len(sell_orders) > 0:
                lowest_sell = min(order['price'] for order in sell_orders)
            
            # 格式化价格
            buy_text = f"买:{self._format_price(highest_buy)}" if highest_buy > 0 else "买:无数据"
            sell_text = f"卖:{self._format_price(lowest_sell)}" if lowest_sell > 0 else "卖:无数据"
            
            return buy_text, sell_text
        except Exception as e:
            logger.error(f"获取物品价格信息失败: {e}")
            return "买:错误", "卖:错误"

    async def _get_jita_price(self, item_id, event, item_name_cn=''):
        """获取吉他市场价格的内部方法"""
        # PLEX（伊甸币）的物品ID列表
        plex_ids = ['50001', '44992']  # 50001是国服PLEX ID，44992是国际服PLEX ID
        
        if item_id in plex_ids:
            # PLEX有专门的市场端点
            async for result in self._get_plex_price(item_id, event, item_name_cn):
                yield result
            return
        
        jita_region_id = 10000002  # 吉他所在的区域ID
        # 获取吉他市场价格
        buy_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=buy")
        sell_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=sell")
        
        # 获取物品信息
        item_info = await self.esi_request(f"/v3/universe/types/{item_id}/")
        item_name = item_name_cn if item_name_cn else (item_info.get('name', '未知物品') if item_info else '未知物品')
        
        # 处理买单数据
        highest_buy = 0
        buy_volume = 0
        if buy_orders and len(buy_orders) > 0:
            # 按价格排序，获取最高买单
            buy_orders.sort(key=lambda x: x['price'], reverse=True)
            highest_buy = buy_orders[0]['price']
            buy_volume = sum(order['volume_remain'] for order in buy_orders[:5])  # 前5个买单的数量
        
        # 处理卖单数据
        lowest_sell = 0
        sell_volume = 0
        if sell_orders and len(sell_orders) > 0:
            # 按价格排序，获取最低卖单
            sell_orders.sort(key=lambda x: x['price'])
            lowest_sell = sell_orders[0]['price']
            sell_volume = sum(order['volume_remain'] for order in sell_orders[:5])  # 前5个卖单的数量
        
        # 构建结果文本
        result = f"吉他市场价格信息:\n"
        result += f"物品名: {item_name}\n"
        result += f"物品ID: {item_id}\n"
        
        if highest_buy > 0:
            result += f"最高买单: {self._format_price(highest_buy)} (数量: {buy_volume})\n"
        else:
            result += "最高买单: 无数据\n"
        
        if lowest_sell > 0:
            result += f"最低卖单: {self._format_price(lowest_sell)} (数量: {sell_volume})\n"
        else:
            result += "最低卖单: 无数据\n"
        
        yield event.plain_result(result)

    async def _get_plex_price(self, item_id, event, item_name_cn=''):
        """获取伊甸币价格（国服特殊处理）"""
        yield event.plain_result("伊甸币价格查询暂不可用")
    
    def _extract_attributes(self, item_info):
        """提取物品属性"""
        attr_dict = {}
        for attr in item_info.get('dogma_attributes', []):
            attr_dict[attr['attribute_id']] = attr['value']
        return attr_dict
    
    async def _process_bonuses(self, dogma_effects, attr_dict, session=None, item_name=''):
        """处理技能加成和特有加成（同步自 test_111_v2.py）"""
        # 使用字典按 effect_name 去重，每个 effect 只保留一条记录
        skill_bonuses_dict = {}  # {skill_type: {effect_name: bonus_dict, ...}, ...}
        unique_bonuses_dict = {}  # {effect_name: bonus_dict, ...}

        for effect in dogma_effects:
            effect_id = effect.get('effect_id')
            effect_info = await self.esi_request(f"/v1/dogma/effects/{effect_id}/")
            if not effect_info:
                continue

            effect_name = effect_info.get('name', '')

            # 跳过应该隐藏的 effect
            if should_hide_effect(effect_name):
                continue

            modifiers = effect_info.get('modifiers', [])

            # 跳过没有 modifier 的 effect
            if not modifiers:
                continue

            # 每个 modifier 单独处理，不再分组
            for mod in modifiers:
                modifying_attr_id = mod.get('modifying_attribute_id')
                if not modifying_attr_id or modifying_attr_id not in attr_dict:
                    continue
                
                bonus_value = attr_dict[modifying_attr_id]
                operator = mod.get('operator', 6)
                modified_attr_id = mod.get('modified_attribute_id')
                
                if not modified_attr_id:
                    continue
                
                # 获取 modifying_attribute 名称
                modifying_attr_name = ''
                modifying_attr_info = await self.esi_request(f"/v1/dogma/attributes/{modifying_attr_id}/")
                if modifying_attr_info:
                    modifying_attr_name = modifying_attr_info.get('name', '')
                
                # 获取 modified_attribute 名称
                modified_attr_name = ''
                bonus_attribute = ''
                attr_info = await self.esi_request(f"/v1/dogma/attributes/{modified_attr_id}/")
                if attr_info:
                    modified_attr_name = attr_info.get('name', '')
                    bonus_attribute = attr_info.get('display_name', modified_attr_name)
                
                # 获取描述（传入 operator）
                bonus_text = await self._process_bonus(bonus_value, bonus_attribute, effect_name, 
                                                       modified_attr_name, 
                                                       operator, modified_attr_name)
                if not bonus_text:
                    continue
                
                # 使用 modifying_attribute 名称识别技能类型
                skill_type = self._identify_skill_type(modifying_attr_name)
                
                # 生成唯一的 effect_key（包含 modified_attr_id 以区分同一 effect 的不同 modifier）
                effect_key = f"{effect_name}_{modified_attr_id}"
                
                bonus_dict = {
                    'text': bonus_text,
                    'effect_name': effect_name,
                    'attr_name': modified_attr_name,
                    'modifying_attr_name': modifying_attr_name,
                    'value': bonus_value
                }
                
                if skill_type:
                    if skill_type not in skill_bonuses_dict:
                        skill_bonuses_dict[skill_type] = {}
                    # 按 effect_key 去重
                    if effect_key not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type][effect_key] = bonus_dict
                else:
                    # 按 effect_key 去重
                    if effect_key not in unique_bonuses_dict:
                        unique_bonuses_dict[effect_key] = bonus_dict

        # 将字典转换为列表格式
        skill_bonuses_list = {}
        for skill_type, bonuses in skill_bonuses_dict.items():
            skill_bonuses_list[skill_type] = list(bonuses.values())
        unique_bonuses_list = list(unique_bonuses_dict.values())

        return skill_bonuses_list, unique_bonuses_list
    
    async def _process_bonus(self, bonus_value, bonus_attribute, effect_name, modified_attr_name, operator=6, attr_names_str=''):
        """处理单个加成，根据 operator 格式化输出
        
        新格式: 描述: effect_name|modified_attr|modifying_attr
        
        operator 规则：
        0: PreAssignment - 0.0035→1-0.0035=99.65% 放在描述前边
        2: PreDiv - 10.0→10+ 放在描述前边
        4: Add - 0.5→50% 放在描述前边
        6: PostPercent - 5.0→5% 放在描述前边
        7: PostMul - 15000→15秒 放在描述后边，并且描述前边加一个·
        0.0: 不写数值，并且描述前边加一个·
        """
        # 首先尝试从 effect_dict 获取描述（基于 zidian1.txt）
        # 使用 effect_name|modified_attr 作为 key
        desc_from_dict = get_effect_description(effect_name, modified_attr_name, bonus_value, operator)
        if desc_from_dict:
            return desc_from_dict
        
        # 如果 effect_dict 中没有找到，根据 operator 格式化
        return self._format_by_operator(abs(bonus_value), bonus_attribute, operator)
    
    def _format_by_operator(self, value, bonus_attribute, operator):
        """根据 operator 格式化输出（备用，当 effect_dict 中没有描述时使用）"""
        # 0.0 的情况：不写数值，描述前边加·
        if value == 0.0:
            return f"·{bonus_attribute}加成"
        
        if operator == 0:
            # PreAssignment: 0.0035→1-0.0035=99.65% 放在描述前边
            percent = (1 - value) * 100
            return f"{percent:.2f}% {bonus_attribute}加成"
        elif operator == 2:
            # PreDiv: 10.0→10+ 放在描述前边
            return f"{value:.2f}+ {bonus_attribute}加成"
        elif operator == 4:
            # Add: 0.5→50% 放在描述前边
            percent = value * 100
            return f"{percent:.2f}% {bonus_attribute}加成"
        elif operator == 7:
            # PostMul: 15000→15秒 放在描述后边，描述前边加·
            # 将大数值转换为秒（除以1000）
            seconds = value / 1000
            return f"·{bonus_attribute}加成 {seconds:.2f}秒"
        else:
            # 默认 PostPercent (6): 5.0→5% 放在描述前边
            return f"{value:.2f}% {bonus_attribute}加成"
    
    def _identify_skill_type(self, modifying_attr_name):
        """识别技能类型（使用 effect_dict）"""
        # 使用 effect_dict 识别（基于 jianchuan.txt）
        # 使用全局导入的 identify_skill_type 函数
        return identify_skill_type(modifying_attr_name)
    
    def _merge_armor_resistance_bonuses(self, bonuses):
        """合并装甲抗性加成：当四种抗性加成同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_armor_bonus)
        merged_armor_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找四种装甲抗性加成
        em_bonus = None
        th_bonus = None
        kn_bonus = None
        ex_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '装甲电磁抗性加成' in bonus_text:
                em_bonus = bonus_dict
            elif '装甲热能抗性加成' in bonus_text:
                th_bonus = bonus_dict
            elif '装甲动能抗性加成' in bonus_text:
                kn_bonus = bonus_dict
            elif '装甲爆炸抗性加成' in bonus_text:
                ex_bonus = bonus_dict
        
        # 检查是否四种都存在
        if not (em_bonus and th_bonus and kn_bonus and ex_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        em_match = re.search(r'(\d+\.?\d*)%', em_bonus['text'])
        th_match = re.search(r'(\d+\.?\d*)%', th_bonus['text'])
        kn_match = re.search(r'(\d+\.?\d*)%', kn_bonus['text'])
        ex_match = re.search(r'(\d+\.?\d*)%', ex_bonus['text'])
        
        if not (em_match and th_match and kn_match and ex_match):
            return bonuses, None
        
        em_value = em_match.group(1)
        th_value = th_match.group(1)
        kn_value = kn_match.group(1)
        ex_value = ex_match.group(1)
        
        # 检查数值是否相等
        if not (em_value == th_value == kn_value == ex_value):
            return bonuses, None
        
        # 构建合并后的装甲抗性加成信息
        merged_armor_bonus = {
            'text': f"{em_value}% 装甲抗性加成",
            'value': em_value,
            'bonuses': [em_bonus, th_bonus, kn_bonus, ex_bonus]  # 保存四条原始加成信息
        }
        
        # 构建新的 bonuses 列表，移除四条单独的抗性加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('装甲电磁抗性加成' in bonus_text or 
                '装甲热能抗性加成' in bonus_text or 
                '装甲动能抗性加成' in bonus_text or 
                '装甲爆炸抗性加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_armor_bonus
    
    def _merge_weapon_disruption_bonuses(self, bonuses):
        """合并武器扰断器效果加成：当7种效果同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找7种武器扰断器效果加成
        tracking_bonus = None
        falloff_bonus = None
        max_range_bonus = None
        aoe_cloud_bonus = None
        aoe_velocity_bonus = None
        explosion_delay_bonus = None
        missile_velocity_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '武器扰断器跟踪速度效果加成' in bonus_text:
                tracking_bonus = bonus_dict
            elif '武器扰断器失准范围效果加成' in bonus_text:
                falloff_bonus = bonus_dict
            elif '武器扰断器最佳射程效果加成' in bonus_text:
                max_range_bonus = bonus_dict
            elif '武器扰断器爆炸半径效果加成' in bonus_text:
                aoe_cloud_bonus = bonus_dict
            elif '武器扰断器爆炸速度效果加成' in bonus_text:
                aoe_velocity_bonus = bonus_dict
            elif '武器扰断器飞行时间效果加成' in bonus_text:
                explosion_delay_bonus = bonus_dict
            elif '武器扰断器导弹速度效果加成' in bonus_text:
                missile_velocity_bonus = bonus_dict
        
        # 检查是否7种都存在
        if not (tracking_bonus and falloff_bonus and max_range_bonus and 
                aoe_cloud_bonus and aoe_velocity_bonus and explosion_delay_bonus and missile_velocity_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        tracking_match = re.search(r'(\d+\.?\d*)%', tracking_bonus['text'])
        falloff_match = re.search(r'(\d+\.?\d*)%', falloff_bonus['text'])
        max_range_match = re.search(r'(\d+\.?\d*)%', max_range_bonus['text'])
        aoe_cloud_match = re.search(r'(\d+\.?\d*)%', aoe_cloud_bonus['text'])
        aoe_velocity_match = re.search(r'(\d+\.?\d*)%', aoe_velocity_bonus['text'])
        explosion_delay_match = re.search(r'(\d+\.?\d*)%', explosion_delay_bonus['text'])
        missile_velocity_match = re.search(r'(\d+\.?\d*)%', missile_velocity_bonus['text'])
        
        if not (tracking_match and falloff_match and max_range_match and 
                aoe_cloud_match and aoe_velocity_match and explosion_delay_match and missile_velocity_match):
            return bonuses, None
        
        tracking_value = tracking_match.group(1)
        falloff_value = falloff_match.group(1)
        max_range_value = max_range_match.group(1)
        aoe_cloud_value = aoe_cloud_match.group(1)
        aoe_velocity_value = aoe_velocity_match.group(1)
        explosion_delay_value = explosion_delay_match.group(1)
        missile_velocity_value = missile_velocity_match.group(1)
        
        # 检查数值是否相等
        if not (tracking_value == falloff_value == max_range_value == 
                aoe_cloud_value == aoe_velocity_value == explosion_delay_value == missile_velocity_value):
            return bonuses, None
        
        # 构建合并后的武器扰断器效果加成信息
        merged_bonus = {
            'text': f"{tracking_value}% 武器扰断器效果加成",
            'value': tracking_value,
            'bonuses': [tracking_bonus, falloff_bonus, max_range_bonus, 
                       aoe_cloud_bonus, aoe_velocity_bonus, explosion_delay_bonus, missile_velocity_bonus]
        }
        
        # 构建新的 bonuses 列表，移除7条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('武器扰断器跟踪速度效果加成' in bonus_text or 
                '武器扰断器失准范围效果加成' in bonus_text or 
                '武器扰断器最佳射程效果加成' in bonus_text or 
                '武器扰断器爆炸半径效果加成' in bonus_text or 
                '武器扰断器爆炸速度效果加成' in bonus_text or 
                '武器扰断器飞行时间效果加成' in bonus_text or 
                '武器扰断器导弹速度效果加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_weapon_range_falloff_bonuses(self, bonuses):
        """合并武器扰断器最佳射程和失准范围：当两者同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找武器扰断器最佳射程和失准范围加成
        max_range_bonus = None
        falloff_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '武器扰断器最佳射程' in bonus_text and '失准范围' not in bonus_text:
                max_range_bonus = bonus_dict
            elif '武器扰断器失准范围' in bonus_text:
                falloff_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (max_range_bonus and falloff_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        max_range_match = re.search(r'(\d+\.?\d*)%', max_range_bonus['text'])
        falloff_match = re.search(r'(\d+\.?\d*)%', falloff_bonus['text'])
        
        if not (max_range_match and falloff_match):
            return bonuses, None
        
        max_range_value = max_range_match.group(1)
        falloff_value = falloff_match.group(1)
        
        # 检查数值是否相等
        if max_range_value != falloff_value:
            return bonuses, None
        
        # 构建合并后的武器扰断器最佳射程和失准范围惩罚信息（数值取负号）
        merged_bonus = {
            'text': f"-{max_range_value}% 武器扰断器最佳射程和失准范围惩罚",
            'value': f"-{max_range_value}",
            'bonuses': [max_range_bonus, falloff_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if (('武器扰断器最佳射程' in bonus_text and '失准范围' not in bonus_text) or 
                '武器扰断器失准范围' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_target_painter_cpu_activation_bonuses(self, bonuses):
        """合并索敌扰断器CPU需求和启动消耗：当两者同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找索敌扰断器CPU需求降低和启动消耗降低加成
        cpu_bonus = None
        activation_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '索敌扰断器CPU需求降低' in bonus_text:
                cpu_bonus = bonus_dict
            elif '索敌扰断器启动消耗降低' in bonus_text:
                activation_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (cpu_bonus and activation_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        cpu_match = re.search(r'(\d+\.?\d*)%', cpu_bonus['text'])
        activation_match = re.search(r'(\d+\.?\d*)%', activation_bonus['text'])
        
        if not (cpu_match and activation_match):
            return bonuses, None
        
        cpu_value = cpu_match.group(1)
        activation_value = activation_match.group(1)
        
        # 检查数值是否相等
        if cpu_value != activation_value:
            return bonuses, None
        
        # 构建合并后的索敌扰断器启动消耗和CPU需求降低信息（数值取负号）
        merged_bonus = {
            'text': f"-{cpu_value}% 索敌扰断器启动消耗和CPU需求降低",
            'value': f"-{cpu_value}",
            'bonuses': [cpu_bonus, activation_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('索敌扰断器CPU需求降低' in bonus_text or 
                '索敌扰断器启动消耗降低' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_propulsion_overload_bonuses(self, bonuses):
        """合并加力燃烧器和微型跃迁推进器过载效果加成：当两者同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找加力燃烧器和微型跃迁推进器过载效果加成
        ab_bonus = None
        mwd_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '加力燃烧器过载效果加成' in bonus_text:
                ab_bonus = bonus_dict
            elif '微型跃迁推进器过载效果加成' in bonus_text:
                mwd_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (ab_bonus and mwd_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        ab_match = re.search(r'(\d+\.?\d*)%', ab_bonus['text'])
        mwd_match = re.search(r'(\d+\.?\d*)%', mwd_bonus['text'])
        
        if not (ab_match and mwd_match):
            return bonuses, None
        
        ab_value = ab_match.group(1)
        mwd_value = mwd_match.group(1)
        
        # 检查数值是否相等
        if ab_value != mwd_value:
            return bonuses, None
        
        # 构建合并后的加力燃烧器和微型跃迁推进器过载效果加成信息
        merged_bonus = {
            'text': f"{ab_value}% 加力燃烧器和微型跃迁推进器过载效果加成",
            'value': ab_value,
            'bonuses': [ab_bonus, mwd_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('加力燃烧器过载效果加成' in bonus_text or 
                '微型跃迁推进器过载效果加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_interdiction_nullifier_bonuses(self, bonuses):
        """合并拦截失效装置三个加成：当重启延迟、最大锁定范围、扫描分辨率同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找三个拦截失效装置加成
        delay_bonus = None
        range_bonus = None
        scan_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '拦截失效装置重启延迟' in bonus_text:
                delay_bonus = bonus_dict
            elif '拦截失效装置最大锁定范围' in bonus_text:
                range_bonus = bonus_dict
            elif '拦截失效装置扫描分辨率' in bonus_text:
                scan_bonus = bonus_dict
        
        # 检查是否三者都存在
        if not (delay_bonus and range_bonus and scan_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        delay_match = re.search(r'(\d+\.?\d*)%', delay_bonus['text'])
        range_match = re.search(r'(\d+\.?\d*)%', range_bonus['text'])
        scan_match = re.search(r'(\d+\.?\d*)%', scan_bonus['text'])
        
        if not (delay_match and range_match and scan_match):
            return bonuses, None
        
        delay_value = delay_match.group(1)
        range_value = range_match.group(1)
        scan_value = scan_match.group(1)
        
        # 检查数值是否相等
        if not (delay_value == range_value == scan_value):
            return bonuses, None
        
        # 构建合并后的拦截失效装置加成信息
        merged_bonus = {
            'text': f"{delay_value}% 拦截失效装置重启延迟、最大锁定距离惩罚和扫描分辨率惩罚降低",
            'value': delay_value,
            'bonuses': [delay_bonus, range_bonus, scan_bonus]
        }
        
        # 构建新的 bonuses 列表，移除3条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('拦截失效装置重启延迟' in bonus_text or 
                '拦截失效装置最大锁定范围' in bonus_text or
                '拦截失效装置扫描分辨率' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_light_missile_damage_bonuses(self, bonuses):
        """合并轻型导弹和火箭四种伤害加成：当爆炸、动能、热能、电磁伤害加成同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找四种轻型导弹和火箭伤害加成
        explosive_bonus = None
        kinetic_bonus = None
        thermal_bonus = None
        em_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '轻型导弹和火箭爆炸伤害加成' in bonus_text:
                explosive_bonus = bonus_dict
            elif '轻型导弹和火箭动能伤害加成' in bonus_text:
                kinetic_bonus = bonus_dict
            elif '轻型导弹和火箭热能伤害加成' in bonus_text:
                thermal_bonus = bonus_dict
            elif '轻型导弹和火箭电磁伤害加成' in bonus_text:
                em_bonus = bonus_dict
        
        # 检查是否四者都存在
        if not (explosive_bonus and kinetic_bonus and thermal_bonus and em_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        explosive_match = re.search(r'(\d+\.?\d*)%', explosive_bonus['text'])
        kinetic_match = re.search(r'(\d+\.?\d*)%', kinetic_bonus['text'])
        thermal_match = re.search(r'(\d+\.?\d*)%', thermal_bonus['text'])
        em_match = re.search(r'(\d+\.?\d*)%', em_bonus['text'])
        
        if not (explosive_match and kinetic_match and thermal_match and em_match):
            return bonuses, None
        
        explosive_value = explosive_match.group(1)
        kinetic_value = kinetic_match.group(1)
        thermal_value = thermal_match.group(1)
        em_value = em_match.group(1)
        
        # 检查数值是否相等
        if not (explosive_value == kinetic_value == thermal_value == em_value):
            return bonuses, None
        
        # 构建合并后的轻型导弹和火箭伤害加成信息
        merged_bonus = {
            'text': f"{explosive_value}% 轻型导弹和火箭伤害加成",
            'value': explosive_value,
            'bonuses': [explosive_bonus, kinetic_bonus, thermal_bonus, em_bonus]
        }
        
        # 构建新的 bonuses 列表，移除4条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('轻型导弹和火箭爆炸伤害加成' in bonus_text or 
                '轻型导弹和火箭动能伤害加成' in bonus_text or
                '轻型导弹和火箭热能伤害加成' in bonus_text or
                '轻型导弹和火箭电磁伤害加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_neutralizer_range_bonuses(self, bonuses):
        """合并能量中和器和掠能器最佳射程加成：当两者同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找能量中和器和掠能器最佳射程加成
        neutralizer_bonus = None
        nosferatu_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '能量中和器最佳射程加成' in bonus_text:
                neutralizer_bonus = bonus_dict
            elif '掠能器最佳射程加成' in bonus_text:
                nosferatu_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (neutralizer_bonus and nosferatu_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        neutralizer_match = re.search(r'(\d+\.?\d*)%', neutralizer_bonus['text'])
        nosferatu_match = re.search(r'(\d+\.?\d*)%', nosferatu_bonus['text'])
        
        if not (neutralizer_match and nosferatu_match):
            return bonuses, None
        
        neutralizer_value = neutralizer_match.group(1)
        nosferatu_value = nosferatu_match.group(1)
        
        # 检查数值是否相等
        if neutralizer_value != nosferatu_value:
            return bonuses, None
        
        # 构建合并后的掠能器和能量中和器最佳射程加成信息
        merged_bonus = {
            'text': f"{neutralizer_value}% 掠能器和能量中和器最佳射程加成",
            'value': neutralizer_value,
            'bonuses': [neutralizer_bonus, nosferatu_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('能量中和器最佳射程加成' in bonus_text or 
                '掠能器最佳射程加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_neutralizer_falloff_bonuses(self, bonuses):
        """合并能量中和器和掠能器失准范围加成：当两者同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找能量中和器和掠能器失准范围加成
        neutralizer_bonus = None
        nosferatu_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '能量中和器失准范围加成' in bonus_text:
                neutralizer_bonus = bonus_dict
            elif '掠能器失准范围加成' in bonus_text:
                nosferatu_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (neutralizer_bonus and nosferatu_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        neutralizer_match = re.search(r'(\d+\.?\d*)%', neutralizer_bonus['text'])
        nosferatu_match = re.search(r'(\d+\.?\d*)%', nosferatu_bonus['text'])
        
        if not (neutralizer_match and nosferatu_match):
            return bonuses, None
        
        neutralizer_value = neutralizer_match.group(1)
        nosferatu_value = nosferatu_match.group(1)
        
        # 检查数值是否相等
        if neutralizer_value != nosferatu_value:
            return bonuses, None
        
        # 构建合并后的掠能器和能量中和器失准范围加成信息
        merged_bonus = {
            'text': f"{neutralizer_value}% 掠能器和能量中和器失准范围加成",
            'value': neutralizer_value,
            'bonuses': [neutralizer_bonus, nosferatu_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('能量中和器失准范围加成' in bonus_text or 
                '掠能器失准范围加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_neutralizer_amount_bonuses(self, bonuses):
        """合并掠能器和能量中和器吸取量加成：当两者同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找掠能器和能量中和器吸取量加成
        nosferatu_bonus = None
        neutralizer_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '掠能器吸取量加成' in bonus_text:
                nosferatu_bonus = bonus_dict
            elif '能量中和器吸取量加成' in bonus_text:
                neutralizer_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (nosferatu_bonus and neutralizer_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        nosferatu_match = re.search(r'(\d+\.?\d*)%', nosferatu_bonus['text'])
        neutralizer_match = re.search(r'(\d+\.?\d*)%', neutralizer_bonus['text'])
        
        if not (nosferatu_match and neutralizer_match):
            return bonuses, None
        
        nosferatu_value = nosferatu_match.group(1)
        neutralizer_value = neutralizer_match.group(1)
        
        # 检查数值是否相等
        if nosferatu_value != neutralizer_value:
            return bonuses, None
        
        # 构建合并后的掠能器和能量中和器吸取量加成信息
        merged_bonus = {
            'text': f"{nosferatu_value}% 掠能器和能量中和器吸取量加成",
            'value': nosferatu_value,
            'bonuses': [nosferatu_bonus, neutralizer_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('掠能器吸取量加成' in bonus_text or 
                '能量中和器吸取量加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_nosferatu_range_amount_bonuses(self, bonuses):
        """合并掠能器有效距离和吸取量加成：当两者同时存在且数值相等时，合并为一条
        
        二合一：有效距离 + 吸取量
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找掠能器有效距离加成和吸取量加成
        range_bonus = None
        amount_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '掠能器有效距离加成' in bonus_text:
                range_bonus = bonus_dict
            elif '掠能器吸取量加成' in bonus_text:
                amount_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (range_bonus and amount_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        range_match = re.search(r'(\d+\.?\d*)%', range_bonus['text'])
        amount_match = re.search(r'(\d+\.?\d*)%', amount_bonus['text'])
        
        if not (range_match and amount_match):
            return bonuses, None
        
        range_value = range_match.group(1)
        amount_value = amount_match.group(1)
        
        # 检查数值是否相等
        if range_value != amount_value:
            return bonuses, None
        
        # 构建合并后的掠能器吸取量和有效距离加成信息
        merged_bonus = {
            'text': f"{range_value}% 掠能器吸取量和有效距离加成",
            'value': range_value,
            'bonuses': [range_bonus, amount_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('掠能器有效距离加成' in bonus_text or 
                '掠能器吸取量加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_remote_armor_repair_bonuses(self, bonuses):
        """合并远程装甲维修器运转周期和启动消耗：当两者同时存在且数值相等时，合并为一条
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找远程装甲维修器运转周期和启动消耗加成
        duration_bonus = None
        cap_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '远程装甲维修器运转周期减少' in bonus_text:
                duration_bonus = bonus_dict
            elif '远程装甲维修器启动消耗减少' in bonus_text:
                cap_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (duration_bonus and cap_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        duration_match = re.search(r'(\d+\.?\d*)%', duration_bonus['text'])
        cap_match = re.search(r'(\d+\.?\d*)%', cap_bonus['text'])
        
        if not (duration_match and cap_match):
            return bonuses, None
        
        duration_value = duration_match.group(1)
        cap_value = cap_match.group(1)
        
        # 检查数值是否相等
        if duration_value != cap_value:
            return bonuses, None
        
        # 构建合并后的远程装甲维修器运转周期和启动消耗减少信息
        merged_bonus = {
            'text': f"{duration_value}% 远程装甲维修器运转周期和启动消耗减少",
            'value': duration_value,
            'bonuses': [duration_bonus, cap_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('远程装甲维修器运转周期减少' in bonus_text or 
                '远程装甲维修器启动消耗减少' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_drone_damage_hp_bonuses(self, bonuses):
        """合并无人机伤害和HP加成：当伤害加成、装甲值、结构值、护盾容量同时存在且数值相等时，合并为一条
        
        优先级：
        1. 五合一：伤害 + HP + 采矿量
        2. 四合一：伤害 + HP
        3. 四合一：跟踪速度 + HP
        4. 三合一：HP（装甲+结构+护盾）
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找六种无人机加成
        damage_bonus = None
        armor_bonus = None
        hull_bonus = None
        shield_bonus = None
        mining_bonus = None
        tracking_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '无人机伤害加成' in bonus_text:
                damage_bonus = bonus_dict
            elif '无人机装甲值加成' in bonus_text:
                armor_bonus = bonus_dict
            elif '无人机结构值加成' in bonus_text:
                hull_bonus = bonus_dict
            elif '无人机护盾容量加成' in bonus_text:
                shield_bonus = bonus_dict
            elif '无人机采矿量加成' in bonus_text:
                mining_bonus = bonus_dict
            elif '无人机跟踪速度加成' in bonus_text:
                tracking_bonus = bonus_dict
        
        # 检查HP三项（装甲+结构+护盾）是否都存在
        hp_bonuses_exist = armor_bonus and hull_bonus and shield_bonus
        if not hp_bonuses_exist:
            return bonuses, None
        
        # 提取数值
        import re
        armor_match = re.search(r'(\d+\.?\d*)%', armor_bonus['text'])
        hull_match = re.search(r'(\d+\.?\d*)%', hull_bonus['text'])
        shield_match = re.search(r'(\d+\.?\d*)%', shield_bonus['text'])
        
        if not (armor_match and hull_match and shield_match):
            return bonuses, None
        
        armor_value = armor_match.group(1)
        hull_value = hull_match.group(1)
        shield_value = shield_match.group(1)
        
        # 检查HP三项数值是否相等
        if not (armor_value == hull_value == shield_value):
            return bonuses, None
        
        # 判断合并类型
        damage_match = damage_bonus and re.search(r'(\d+\.?\d*)%', damage_bonus['text'])
        mining_match = mining_bonus and re.search(r'(\d+\.?\d*)%', mining_bonus['text'])
        tracking_match = tracking_bonus and re.search(r'(\d+\.?\d*)%', tracking_bonus['text'])
        
        damage_value = damage_match.group(1) if damage_match else None
        mining_value = mining_match.group(1) if mining_match else None
        tracking_value = tracking_match.group(1) if tracking_match else None
        
        # 五合一：伤害+HP+采矿量
        if damage_bonus and mining_bonus and damage_value == armor_value and mining_value == armor_value:
            merged_bonus = {
                'text': f"{armor_value}% 无人机伤害、HP和采矿量加成",
                'value': armor_value,
                'bonuses': [damage_bonus, armor_bonus, hull_bonus, shield_bonus, mining_bonus]
            }
            # 构建新的 bonuses 列表，移除5条单独的效果加成
            new_bonuses = []
            for bonus_dict in bonuses:
                bonus_text = bonus_dict.get('text', '')
                if ('无人机伤害加成' in bonus_text or 
                    '无人机装甲值加成' in bonus_text or
                    '无人机结构值加成' in bonus_text or
                    '无人机护盾容量加成' in bonus_text or
                    '无人机采矿量加成' in bonus_text):
                    continue
                new_bonuses.append(bonus_dict)
            return new_bonuses, merged_bonus
        
        # 四合一：伤害+HP
        if damage_bonus and damage_value == armor_value:
            merged_bonus = {
                'text': f"{armor_value}% 无人机伤害和HP加成",
                'value': armor_value,
                'bonuses': [damage_bonus, armor_bonus, hull_bonus, shield_bonus]
            }
            # 构建新的 bonuses 列表，移除4条单独的效果加成
            new_bonuses = []
            for bonus_dict in bonuses:
                bonus_text = bonus_dict.get('text', '')
                if ('无人机伤害加成' in bonus_text or 
                    '无人机装甲值加成' in bonus_text or
                    '无人机结构值加成' in bonus_text or
                    '无人机护盾容量加成' in bonus_text):
                    continue
                new_bonuses.append(bonus_dict)
            return new_bonuses, merged_bonus
        
        # 四合一：跟踪速度+HP
        if tracking_bonus and tracking_value == armor_value:
            merged_bonus = {
                'text': f"{armor_value}% 无人机HP和跟踪速度加成",
                'value': armor_value,
                'bonuses': [tracking_bonus, armor_bonus, hull_bonus, shield_bonus]
            }
            # 构建新的 bonuses 列表，移除4条单独的效果加成
            new_bonuses = []
            for bonus_dict in bonuses:
                bonus_text = bonus_dict.get('text', '')
                if ('无人机跟踪速度加成' in bonus_text or 
                    '无人机装甲值加成' in bonus_text or
                    '无人机结构值加成' in bonus_text or
                    '无人机护盾容量加成' in bonus_text):
                    continue
                new_bonuses.append(bonus_dict)
            return new_bonuses, merged_bonus
        
        # 三合一：HP（装甲+结构+护盾）
        merged_bonus = {
            'text': f"{armor_value}% 无人机HP加成",
            'value': armor_value,
            'bonuses': [armor_bonus, hull_bonus, shield_bonus]
        }
        
        # 构建新的 bonuses 列表，移除3条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('无人机装甲值加成' in bonus_text or
                '无人机结构值加成' in bonus_text or
                '无人机护盾容量加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_small_energy_turret_range_bonuses(self, bonuses):
        """合并小型能量炮台最佳射程和失准范围加成：当两者同时存在且数值相等时，合并为一条
        
        二合一：最佳射程 + 失准范围
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找小型能量炮台最佳射程和失准范围加成
        optimal_bonus = None
        falloff_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '小型能量炮台最佳射程加成' in bonus_text:
                optimal_bonus = bonus_dict
            elif '小型能量炮台失准范围加成' in bonus_text:
                falloff_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (optimal_bonus and falloff_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        optimal_match = re.search(r'(\d+\.?\d*)%', optimal_bonus['text'])
        falloff_match = re.search(r'(\d+\.?\d*)%', falloff_bonus['text'])
        
        if not (optimal_match and falloff_match):
            return bonuses, None
        
        optimal_value = optimal_match.group(1)
        falloff_value = falloff_match.group(1)
        
        # 检查数值是否相等
        if optimal_value != falloff_value:
            return bonuses, None
        
        # 构建合并后的小型能量炮台最佳射程和失准范围加成信息
        # 注意：数值和文字间有一个空格
        merged_bonus = {
            'text': f"{optimal_value}% 小型能量炮台最佳射程和失准范围加成",
            'value': optimal_value,
            'bonuses': [optimal_bonus, falloff_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('小型能量炮台最佳射程加成' in bonus_text or 
                '小型能量炮台失准范围加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_medium_energy_turret_range_bonuses(self, bonuses):
        """合并中型能量炮台最佳射程和失准范围加成：当两者同时存在且数值相等时，合并为一条
        
        二合一：最佳射程 + 失准范围
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找中型能量炮台最佳射程和失准范围加成
        optimal_bonus = None
        falloff_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '中型能量炮台最佳射程加成' in bonus_text:
                optimal_bonus = bonus_dict
            elif '中型能量炮台失准范围加成' in bonus_text:
                falloff_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (optimal_bonus and falloff_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        optimal_match = re.search(r'(\d+\.?\d*)%', optimal_bonus['text'])
        falloff_match = re.search(r'(\d+\.?\d*)%', falloff_bonus['text'])
        
        if not (optimal_match and falloff_match):
            return bonuses, None
        
        optimal_value = optimal_match.group(1)
        falloff_value = falloff_match.group(1)
        
        # 检查数值是否相等
        if optimal_value != falloff_value:
            return bonuses, None
        
        # 构建合并后的中型能量炮台最佳射程和失准范围加成信息
        # 注意：数值和文字间有一个空格
        merged_bonus = {
            'text': f"{optimal_value}% 中型能量炮台最佳射程和失准范围加成",
            'value': optimal_value,
            'bonuses': [optimal_bonus, falloff_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('中型能量炮台最佳射程加成' in bonus_text or 
                '中型能量炮台失准范围加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_weapon_damage_bonuses(self, bonuses):
        """合并武器伤害加成：鱼雷、巡航导弹、重型快速导弹、大型能量炮台、重型导弹、重型攻击导弹、火箭、超大型巡航导弹、超大型鱼雷
        
        优先级：
        1. 十三合一：鱼雷4种 + 巡航导弹4种 + 重型快速导弹4种 + 大型能量炮台
        2. 八合一：重型导弹4种 + 重型攻击导弹4种
        3. 十二合一：超大型巡航导弹4种 + 超大型鱼雷4种 + 鱼雷4种
        4. 鱼雷四合一：电磁 + 热能 + 动能 + 爆炸
        5. 巡航导弹四合一
        6. 重型快速导弹四合一
        7. 重型攻击导弹四合一
        8. 重型导弹四合一
        9. 火箭四合一：电磁 + 热能 + 动能 + 爆炸
        10. 火箭三合一：电磁 + 动能 + 热能
        
        返回: (new_bonuses, merged_bonus, merged_torpedo, merged_cruise, merged_rapid, 
               merged_heavy_assault_heavy, merged_heavy_assault, merged_heavy, merged_rocket, merged_rocket_partial,
               merged_xl_cruise_torpedo)
        """
        import re
        
        # 查找所有武器伤害加成
        torpedo_em = torpedo_therm = torpedo_kin = torpedo_exp = None
        cruise_em = cruise_therm = cruise_kin = cruise_exp = None
        rapid_em = rapid_therm = rapid_kin = rapid_exp = None
        large_energy = None
        # 重型导弹和重型攻击导弹
        heavy_em = heavy_therm = heavy_kin = heavy_exp = None
        heavy_assault_em = heavy_assault_therm = heavy_assault_kin = heavy_assault_exp = None
        # 火箭
        rocket_em = rocket_therm = rocket_kin = rocket_exp = None
        # 超大型巡航导弹和超大型鱼雷
        xl_cruise_em = xl_cruise_therm = xl_cruise_kin = xl_cruise_exp = None
        xl_torpedo_em = xl_torpedo_therm = xl_torpedo_kin = xl_torpedo_exp = None
        # 破坏型长枪
        lance_em = lance_therm = lance_kin = lance_exp = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            # 超大型巡航导弹、超大型鱼雷和鱼雷合并格式（如灾祸级）- 必须优先检测
            if '超大型巡航导弹、超大型鱼雷和鱼雷电磁伤害加成' in bonus_text:
                xl_cruise_em = xl_torpedo_em = torpedo_em = bonus_dict
            elif '超大型巡航导弹、超大型鱼雷和鱼雷热能伤害加成' in bonus_text:
                xl_cruise_therm = xl_torpedo_therm = torpedo_therm = bonus_dict
            elif '超大型巡航导弹、超大型鱼雷和鱼雷动能伤害加成' in bonus_text:
                xl_cruise_kin = xl_torpedo_kin = torpedo_kin = bonus_dict
            elif '超大型巡航导弹、超大型鱼雷和鱼雷爆炸伤害加成' in bonus_text:
                xl_cruise_exp = xl_torpedo_exp = torpedo_exp = bonus_dict
            # 鱼雷（单独格式，注意：不能匹配合并格式）
            elif '鱼雷电磁伤害加成' in bonus_text and '超大型' not in bonus_text:
                torpedo_em = bonus_dict
            elif '鱼雷热能伤害加成' in bonus_text and '超大型' not in bonus_text:
                torpedo_therm = bonus_dict
            elif '鱼雷动能伤害加成' in bonus_text and '超大型' not in bonus_text:
                torpedo_kin = bonus_dict
            elif '鱼雷爆炸伤害加成' in bonus_text and '超大型' not in bonus_text:
                torpedo_exp = bonus_dict
            # 巡航导弹
            elif '巡航导弹电磁伤害加成' in bonus_text:
                cruise_em = bonus_dict
            elif '巡航导弹热能伤害加成' in bonus_text:
                cruise_therm = bonus_dict
            elif '巡航导弹动能伤害加成' in bonus_text:
                cruise_kin = bonus_dict
            elif '巡航导弹爆炸伤害加成' in bonus_text:
                cruise_exp = bonus_dict
            # 重型快速导弹
            elif '重型快速导弹电磁伤害加成' in bonus_text:
                rapid_em = bonus_dict
            elif '重型快速导弹热能伤害加成' in bonus_text:
                rapid_therm = bonus_dict
            elif '重型快速导弹动能伤害加成' in bonus_text:
                rapid_kin = bonus_dict
            elif '重型快速导弹爆炸伤害加成' in bonus_text:
                rapid_exp = bonus_dict
            # 大型能量炮台
            elif '大型能量炮台伤害加成' in bonus_text:
                large_energy = bonus_dict
            # 重型导弹
            elif '重型导弹电磁伤害加成' in bonus_text:
                heavy_em = bonus_dict
            elif '重型导弹热能伤害加成' in bonus_text:
                heavy_therm = bonus_dict
            elif '重型导弹动能伤害加成' in bonus_text:
                heavy_kin = bonus_dict
            elif '重型导弹爆炸伤害加成' in bonus_text:
                heavy_exp = bonus_dict
            # 重型攻击导弹
            elif '重型攻击导弹电磁伤害加成' in bonus_text:
                heavy_assault_em = bonus_dict
            elif '重型攻击导弹热能伤害加成' in bonus_text:
                heavy_assault_therm = bonus_dict
            elif '重型攻击导弹动能伤害加成' in bonus_text:
                heavy_assault_kin = bonus_dict
            elif '重型攻击导弹爆炸伤害加成' in bonus_text:
                heavy_assault_exp = bonus_dict
            # 火箭
            elif '火箭电磁伤害加成' in bonus_text:
                rocket_em = bonus_dict
            elif '火箭热能伤害加成' in bonus_text:
                rocket_therm = bonus_dict
            elif '火箭动能伤害加成' in bonus_text:
                rocket_kin = bonus_dict
            elif '火箭爆炸伤害加成' in bonus_text:
                rocket_exp = bonus_dict
            # 超大型巡航导弹（单独格式）
            elif '超大型巡航导弹电磁伤害加成' in bonus_text and '超大型鱼雷' not in bonus_text:
                xl_cruise_em = bonus_dict
            elif '超大型巡航导弹热能伤害加成' in bonus_text and '超大型鱼雷' not in bonus_text:
                xl_cruise_therm = bonus_dict
            elif '超大型巡航导弹动能伤害加成' in bonus_text and '超大型鱼雷' not in bonus_text:
                xl_cruise_kin = bonus_dict
            elif '超大型巡航导弹爆炸伤害加成' in bonus_text and '超大型鱼雷' not in bonus_text:
                xl_cruise_exp = bonus_dict
            # 超大型鱼雷（单独格式）
            elif '超大型鱼雷电磁伤害加成' in bonus_text and '超大型巡航导弹' not in bonus_text:
                xl_torpedo_em = bonus_dict
            elif '超大型鱼雷热能伤害加成' in bonus_text and '超大型巡航导弹' not in bonus_text:
                xl_torpedo_therm = bonus_dict
            elif '超大型鱼雷动能伤害加成' in bonus_text and '超大型巡航导弹' not in bonus_text:
                xl_torpedo_kin = bonus_dict
            elif '超大型鱼雷爆炸伤害加成' in bonus_text and '超大型巡航导弹' not in bonus_text:
                xl_torpedo_exp = bonus_dict
            # 破坏型长枪
            elif '破坏型长枪电磁伤害加成' in bonus_text:
                lance_em = bonus_dict
            elif '破坏型长枪热能伤害加成' in bonus_text:
                lance_therm = bonus_dict
            elif '破坏型长枪动能伤害加成' in bonus_text:
                lance_kin = bonus_dict
            elif '破坏型长枪爆炸伤害加成' in bonus_text:
                lance_exp = bonus_dict
        
        # 检查各组是否完整
        torpedo_complete = all([torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp])
        cruise_complete = all([cruise_em, cruise_therm, cruise_kin, cruise_exp])
        rapid_complete = all([rapid_em, rapid_therm, rapid_kin, rapid_exp])
        heavy_complete = all([heavy_em, heavy_therm, heavy_kin, heavy_exp])
        heavy_assault_complete = all([heavy_assault_em, heavy_assault_therm, heavy_assault_kin, heavy_assault_exp])
        rocket_complete = all([rocket_em, rocket_therm, rocket_kin, rocket_exp])
        lance_complete = all([lance_em, lance_therm, lance_kin, lance_exp])
        rocket_partial_complete = all([rocket_em, rocket_kin, rocket_therm])  # 电磁+动能+热能
        xl_cruise_complete = all([xl_cruise_em, xl_cruise_therm, xl_cruise_kin, xl_cruise_exp])
        xl_torpedo_complete = all([xl_torpedo_em, xl_torpedo_therm, xl_torpedo_kin, xl_torpedo_exp])
        
        def extract_value(bonus_dict):
            if not bonus_dict:
                return None
            match = re.search(r'(\d+\.?\d*)%', bonus_dict['text'])
            return match.group(1) if match else None
        
        # 十三合一：所有武器类型
        if torpedo_complete and cruise_complete and rapid_complete and large_energy:
            values = [extract_value(b) for b in [torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp,
                                                  cruise_em, cruise_therm, cruise_kin, cruise_exp,
                                                  rapid_em, rapid_therm, rapid_kin, rapid_exp, large_energy]]
            if all(v == values[0] for v in values):
                merged_bonus = {
                    'text': f"{values[0]}% 大型能量炮台、重型快速导弹、巡航导弹和鱼雷伤害加成",
                    'value': values[0],
                    'bonuses': [large_energy, rapid_em, rapid_therm, rapid_kin, rapid_exp,
                               cruise_em, cruise_therm, cruise_kin, cruise_exp,
                               torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp]
                }
                new_bonuses = []
                for bonus_dict in bonuses:
                    bonus_text = bonus_dict.get('text', '')
                    if ('鱼雷电磁伤害加成' in bonus_text or '鱼雷热能伤害加成' in bonus_text or
                        '鱼雷动能伤害加成' in bonus_text or '鱼雷爆炸伤害加成' in bonus_text or
                        '巡航导弹电磁伤害加成' in bonus_text or '巡航导弹热能伤害加成' in bonus_text or
                        '巡航导弹动能伤害加成' in bonus_text or '巡航导弹爆炸伤害加成' in bonus_text or
                        '重型快速导弹电磁伤害加成' in bonus_text or '重型快速导弹热能伤害加成' in bonus_text or
                        '重型快速导弹动能伤害加成' in bonus_text or '重型快速导弹爆炸伤害加成' in bonus_text or
                        '大型能量炮台伤害加成' in bonus_text):
                        continue
                    new_bonuses.append(bonus_dict)
                return new_bonuses, merged_bonus, None, None, None, None, None, None, None, None, None, None
        
        # 八合一：重型导弹4种 + 重型攻击导弹4种
        merged_heavy_assault_heavy = None
        merged_heavy_assault = None
        merged_heavy = None
        if heavy_complete and heavy_assault_complete:
            values = [extract_value(b) for b in [heavy_em, heavy_therm, heavy_kin, heavy_exp,
                                                  heavy_assault_em, heavy_assault_therm, heavy_assault_kin, heavy_assault_exp]]
            if all(v == values[0] for v in values):
                merged_heavy_assault_heavy = {
                    'text': f"{values[0]}% 重型导弹和重型攻击导弹伤害加成",
                    'value': values[0],
                    'bonuses': [heavy_em, heavy_therm, heavy_kin, heavy_exp,
                               heavy_assault_em, heavy_assault_therm, heavy_assault_kin, heavy_assault_exp]
                }
        
        # 如果八合一失败，才检查各自的四合一
        if not merged_heavy_assault_heavy:
            # 重型攻击导弹四合一
            if heavy_assault_complete:
                values = [extract_value(b) for b in [heavy_assault_em, heavy_assault_therm, heavy_assault_kin, heavy_assault_exp]]
                if all(v == values[0] for v in values):
                    merged_heavy_assault = {
                        'text': f"{values[0]}% 重型攻击导弹伤害加成",
                        'value': values[0],
                        'bonuses': [heavy_assault_em, heavy_assault_therm, heavy_assault_kin, heavy_assault_exp]
                    }
            
            # 重型导弹四合一
            if heavy_complete:
                values = [extract_value(b) for b in [heavy_em, heavy_therm, heavy_kin, heavy_exp]]
                if all(v == values[0] for v in values):
                    merged_heavy = {
                        'text': f"{values[0]}% 重型导弹伤害加成",
                        'value': values[0],
                        'bonuses': [heavy_em, heavy_therm, heavy_kin, heavy_exp]
                    }
        
        # 十二合一：超大型巡航导弹4种 + 超大型鱼雷4种 + 鱼雷4种
        merged_xl_cruise_torpedo = None
        if xl_cruise_complete and xl_torpedo_complete and torpedo_complete:
            values = [extract_value(b) for b in [xl_cruise_em, xl_cruise_therm, xl_cruise_kin, xl_cruise_exp,
                                                  xl_torpedo_em, xl_torpedo_therm, xl_torpedo_kin, xl_torpedo_exp,
                                                  torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp]]
            if all(v == values[0] for v in values):
                merged_xl_cruise_torpedo = {
                    'text': f"{values[0]}% 超大型巡航导弹、超大型鱼雷和鱼雷伤害加成",
                    'value': values[0],
                    'bonuses': [xl_cruise_em, xl_cruise_therm, xl_cruise_kin, xl_cruise_exp,
                               xl_torpedo_em, xl_torpedo_therm, xl_torpedo_kin, xl_torpedo_exp,
                               torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp]
                }
        
        # 鱼雷四合一
        merged_torpedo = None
        if torpedo_complete and not merged_xl_cruise_torpedo:
            values = [extract_value(b) for b in [torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp]]
            if all(v == values[0] for v in values):
                merged_torpedo = {
                    'text': f"{values[0]}% 鱼雷伤害加成",
                    'value': values[0],
                    'bonuses': [torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp]
                }
        
        # 巡航导弹四合一
        merged_cruise = None
        if cruise_complete:
            values = [extract_value(b) for b in [cruise_em, cruise_therm, cruise_kin, cruise_exp]]
            if all(v == values[0] for v in values):
                merged_cruise = {
                    'text': f"{values[0]}% 巡航导弹伤害加成",
                    'value': values[0],
                    'bonuses': [cruise_em, cruise_therm, cruise_kin, cruise_exp]
                }
        
        # 重型快速导弹四合一
        merged_rapid = None
        if rapid_complete:
            values = [extract_value(b) for b in [rapid_em, rapid_therm, rapid_kin, rapid_exp]]
            if all(v == values[0] for v in values):
                merged_rapid = {
                    'text': f"{values[0]}% 重型快速导弹伤害加成",
                    'value': values[0],
                    'bonuses': [rapid_em, rapid_therm, rapid_kin, rapid_exp]
                }
        
        # 火箭四合一
        merged_rocket = None
        merged_rocket_partial = None
        if rocket_complete:
            values = [extract_value(b) for b in [rocket_em, rocket_therm, rocket_kin, rocket_exp]]
            if all(v == values[0] for v in values):
                merged_rocket = {
                    'text': f"{values[0]}% 火箭伤害加成",
                    'value': values[0],
                    'bonuses': [rocket_em, rocket_therm, rocket_kin, rocket_exp]
                }
        
        # 如果火箭四合一失败，检查火箭三合一（电磁+动能+热能）
        if not merged_rocket and rocket_partial_complete:
            values = [extract_value(b) for b in [rocket_em, rocket_kin, rocket_therm]]
            if all(v == values[0] for v in values):
                merged_rocket_partial = {
                    'text': f"{values[0]}% 火箭电磁、动能、热能伤害加成",
                    'value': values[0],
                    'bonuses': [rocket_em, rocket_kin, rocket_therm]
                }
        
        # 破坏型长枪四合一
        merged_lance = None
        if lance_complete:
            values = [extract_value(b) for b in [lance_em, lance_therm, lance_kin, lance_exp]]
            if all(v == values[0] for v in values):
                merged_lance = {
                    'text': f"{values[0]}% 破坏型长枪伤害加成",
                    'value': values[0],
                    'bonuses': [lance_em, lance_therm, lance_kin, lance_exp]
                }
        
        # 构建新的 bonuses 列表
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            should_remove = False
            # 检查是否在合并的列表中
            if merged_torpedo and any(b and bonus_dict == b for b in [torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp]):
                should_remove = True
            if merged_cruise and any(b and bonus_dict == b for b in [cruise_em, cruise_therm, cruise_kin, cruise_exp]):
                should_remove = True
            if merged_rapid and any(b and bonus_dict == b for b in [rapid_em, rapid_therm, rapid_kin, rapid_exp]):
                should_remove = True
            if merged_heavy_assault_heavy and any(b and bonus_dict == b for b in [heavy_em, heavy_therm, heavy_kin, heavy_exp, heavy_assault_em, heavy_assault_therm, heavy_assault_kin, heavy_assault_exp]):
                should_remove = True
            if merged_heavy_assault and any(b and bonus_dict == b for b in [heavy_assault_em, heavy_assault_therm, heavy_assault_kin, heavy_assault_exp]):
                should_remove = True
            if merged_heavy and any(b and bonus_dict == b for b in [heavy_em, heavy_therm, heavy_kin, heavy_exp]):
                should_remove = True
            if merged_rocket and any(b and bonus_dict == b for b in [rocket_em, rocket_therm, rocket_kin, rocket_exp]):
                should_remove = True
            if merged_rocket_partial and any(b and bonus_dict == b for b in [rocket_em, rocket_kin, rocket_therm]):
                should_remove = True
            if merged_xl_cruise_torpedo and any(b and bonus_dict == b for b in [xl_cruise_em, xl_cruise_therm, xl_cruise_kin, xl_cruise_exp, xl_torpedo_em, xl_torpedo_therm, xl_torpedo_kin, xl_torpedo_exp, torpedo_em, torpedo_therm, torpedo_kin, torpedo_exp]):
                should_remove = True
            if merged_lance and any(b and bonus_dict == b for b in [lance_em, lance_therm, lance_kin, lance_exp]):
                should_remove = True
            if should_remove:
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, None, merged_torpedo, merged_cruise, merged_rapid, merged_heavy_assault_heavy, merged_heavy_assault, merged_heavy, merged_rocket, merged_rocket_partial, merged_xl_cruise_torpedo, merged_lance
    
    def _merge_missile_velocity_bonuses(self, bonuses):
        """合并导弹最大速度加成：当重型导弹最大速度加成和重型攻击导弹最大速度加成同时存在且数值相等时，合并为一条
        
        二合一：重型导弹最大速度 + 重型攻击导弹最大速度
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找重型导弹最大速度加成和重型攻击导弹最大速度加成
        heavy_velocity_bonus = None
        heavy_assault_velocity_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '重型导弹最大速度加成' in bonus_text:
                heavy_velocity_bonus = bonus_dict
            elif '重型攻击导弹最大速度加成' in bonus_text:
                heavy_assault_velocity_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (heavy_velocity_bonus and heavy_assault_velocity_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        heavy_match = re.search(r'(\d+\.?\d*)%', heavy_velocity_bonus['text'])
        heavy_assault_match = re.search(r'(\d+\.?\d*)%', heavy_assault_velocity_bonus['text'])
        
        if not (heavy_match and heavy_assault_match):
            return bonuses, None
        
        heavy_value = heavy_match.group(1)
        heavy_assault_value = heavy_assault_match.group(1)
        
        # 检查数值是否相等
        if heavy_value != heavy_assault_value:
            return bonuses, None
        
        # 构建合并后的导弹最大速度加成信息
        merged_bonus = {
            'text': f"{heavy_value}% 重型导弹和重型攻击导弹最大速度加成",
            'value': heavy_value,
            'bonuses': [heavy_velocity_bonus, heavy_assault_velocity_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('重型导弹最大速度加成' in bonus_text or 
                '重型攻击导弹最大速度加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_remote_armor_repair_range_bonuses(self, bonuses):
        """合并远程装甲维修器最佳射程和失准范围加成：当两者同时存在且数值相等时，合并为一条
        
        二合一：最佳射程 + 失准范围
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找远程装甲维修器最佳射程和失准范围加成
        optimal_bonus = None
        falloff_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '远程装甲维修器最佳射程加成' in bonus_text:
                optimal_bonus = bonus_dict
            elif '远程装甲维修器失准范围加成' in bonus_text:
                falloff_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (optimal_bonus and falloff_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        optimal_match = re.search(r'(\d+\.?\d*)%', optimal_bonus['text'])
        falloff_match = re.search(r'(\d+\.?\d*)%', falloff_bonus['text'])
        
        if not (optimal_match and falloff_match):
            return bonuses, None
        
        optimal_value = optimal_match.group(1)
        falloff_value = falloff_match.group(1)
        
        # 检查数值是否相等
        if optimal_value != falloff_value:
            return bonuses, None
        
        # 构建合并后的远程装甲维修器最佳射程和失准范围加成信息
        # 注意：数值和文字间有一个空格
        merged_bonus = {
            'text': f"{optimal_value}% 远程装甲维修器最佳射程和失准范围加成",
            'value': optimal_value,
            'bonuses': [optimal_bonus, falloff_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('远程装甲维修器最佳射程加成' in bonus_text or 
                '远程装甲维修器失准范围加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_sensor_dampener_bonuses(self, bonuses):
        """合并远程感应抑阻器效果加成：当最大锁定范围效果加成和扫描分辨率效果加成同时存在且数值相等时，合并为一条
        
        二合一：最大锁定范围效果 + 扫描分辨率效果
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找两种远程感应抑阻器效果加成
        max_range_bonus = None
        scan_res_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '远程感应抑阻器最大锁定范围效果加成' in bonus_text:
                max_range_bonus = bonus_dict
            elif '远程感应抑阻器扫描分辨率效果加成' in bonus_text:
                scan_res_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (max_range_bonus and scan_res_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        max_range_match = re.search(r'(\d+\.?\d*)%', max_range_bonus['text'])
        scan_res_match = re.search(r'(\d+\.?\d*)%', scan_res_bonus['text'])
        
        if not (max_range_match and scan_res_match):
            return bonuses, None
        
        max_range_value = max_range_match.group(1)
        scan_res_value = scan_res_match.group(1)
        
        # 检查数值是否相等
        if max_range_value != scan_res_value:
            return bonuses, None
        
        # 构建合并后的远程感应抑阻器效果加成信息
        # 注意：数值和文字间有一个空格
        merged_bonus = {
            'text': f"{max_range_value}% 远程感应抑阻器效果加成",
            'value': max_range_value,
            'bonuses': [max_range_bonus, scan_res_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('远程感应抑阻器最大锁定范围效果加成' in bonus_text or 
                '远程感应抑阻器扫描分辨率效果加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_shield_resistance_bonuses(self, bonuses):
        """合并护盾抗性加成：当四种护盾抗性加成同时存在且数值相等时，合并为一条
        
        四合一：电磁 + 爆炸 + 动能 + 热能
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找四种护盾抗性加成
        em_bonus = None
        explosive_bonus = None
        kinetic_bonus = None
        thermal_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '护盾电磁抗性加成' in bonus_text:
                em_bonus = bonus_dict
            elif '护盾爆炸抗性加成' in bonus_text:
                explosive_bonus = bonus_dict
            elif '护盾动能抗性加成' in bonus_text:
                kinetic_bonus = bonus_dict
            elif '护盾热能抗性加成' in bonus_text:
                thermal_bonus = bonus_dict
        
        # 检查是否四者都存在
        if not (em_bonus and explosive_bonus and kinetic_bonus and thermal_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        em_match = re.search(r'(\d+\.?\d*)%', em_bonus['text'])
        explosive_match = re.search(r'(\d+\.?\d*)%', explosive_bonus['text'])
        kinetic_match = re.search(r'(\d+\.?\d*)%', kinetic_bonus['text'])
        thermal_match = re.search(r'(\d+\.?\d*)%', thermal_bonus['text'])
        
        if not (em_match and explosive_match and kinetic_match and thermal_match):
            return bonuses, None
        
        em_value = em_match.group(1)
        explosive_value = explosive_match.group(1)
        kinetic_value = kinetic_match.group(1)
        thermal_value = thermal_match.group(1)
        
        # 检查数值是否相等
        if not (em_value == explosive_value == kinetic_value == thermal_value):
            return bonuses, None
        
        # 构建合并后的护盾抗性加成信息
        merged_bonus = {
            'text': f"{em_value}% 护盾抗性加成",
            'value': em_value,
            'bonuses': [em_bonus, explosive_bonus, kinetic_bonus, thermal_bonus]
        }
        
        # 构建新的 bonuses 列表，移除4条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('护盾电磁抗性加成' in bonus_text or 
                '护盾爆炸抗性加成' in bonus_text or 
                '护盾动能抗性加成' in bonus_text or 
                '护盾热能抗性加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_ecm_strength_bonuses(self, bonuses):
        """合并ECM目标干扰器强度加成：当四种ECM强度加成同时存在且数值相等时，合并为一条
        
        四合一：引力 + 磁力 + 光雷达 + 雷达
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找四种ECM强度加成
        gravimetric_bonus = None
        magnetometric_bonus = None
        ladar_bonus = None
        radar_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if 'ECM目标干扰器引力强度加成' in bonus_text:
                gravimetric_bonus = bonus_dict
            elif 'ECM目标干扰器磁力强度加成' in bonus_text:
                magnetometric_bonus = bonus_dict
            elif 'ECM目标干扰器光雷达强度加成' in bonus_text:
                ladar_bonus = bonus_dict
            elif 'ECM目标干扰器雷达强度加成' in bonus_text:
                radar_bonus = bonus_dict
        
        # 检查是否四者都存在
        if not (gravimetric_bonus and magnetometric_bonus and ladar_bonus and radar_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        grav_match = re.search(r'(\d+\.?\d*)%', gravimetric_bonus['text'])
        mag_match = re.search(r'(\d+\.?\d*)%', magnetometric_bonus['text'])
        ladar_match = re.search(r'(\d+\.?\d*)%', ladar_bonus['text'])
        radar_match = re.search(r'(\d+\.?\d*)%', radar_bonus['text'])
        
        if not (grav_match and mag_match and ladar_match and radar_match):
            return bonuses, None
        
        grav_value = grav_match.group(1)
        mag_value = mag_match.group(1)
        ladar_value = ladar_match.group(1)
        radar_value = radar_match.group(1)
        
        # 检查数值是否相等
        if not (grav_value == mag_value == ladar_value == radar_value):
            return bonuses, None
        
        # 构建合并后的ECM强度加成信息
        merged_bonus = {
            'text': f"{grav_value}% ECM目标干扰器强度加成",
            'value': grav_value,
            'bonuses': [gravimetric_bonus, magnetometric_bonus, ladar_bonus, radar_bonus]
        }
        
        # 构建新的 bonuses 列表，移除4条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('ECM目标干扰器引力强度加成' in bonus_text or 
                'ECM目标干扰器磁力强度加成' in bonus_text or 
                'ECM目标干扰器光雷达强度加成' in bonus_text or 
                'ECM目标干扰器雷达强度加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_logistics_drone_bonuses(self, bonuses, skill_type=None):
        """合并后勤无人机传输量加成：当装甲、护盾、结构传输量加成同时存在且数值相等时，合并为一条
        
        三合一：装甲 + 护盾 + 结构
        合并后数值除以5（航空母舰操作除外）
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找三种后勤无人机传输量加成
        armor_bonus = None
        shield_bonus = None
        hull_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '后勤无人机装甲值传输量加成' in bonus_text:
                armor_bonus = bonus_dict
            elif '后勤无人机护盾传输量加成' in bonus_text:
                shield_bonus = bonus_dict
            elif '后勤无人机结构值传输量加成' in bonus_text:
                hull_bonus = bonus_dict
        
        # 检查是否三者都存在
        if not (armor_bonus and shield_bonus and hull_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        armor_match = re.search(r'(\d+\.?\d*)%', armor_bonus['text'])
        shield_match = re.search(r'(\d+\.?\d*)%', shield_bonus['text'])
        hull_match = re.search(r'(\d+\.?\d*)%', hull_bonus['text'])
        
        if not (armor_match and shield_match and hull_match):
            return bonuses, None
        
        armor_value = float(armor_match.group(1))
        shield_value = float(shield_match.group(1))
        hull_value = float(hull_match.group(1))
        
        # 检查数值是否相等
        if not (armor_value == shield_value == hull_value):
            return bonuses, None
        
        # 判断是否有航空母舰操作
        has_carrier_operation = skill_type and '航空母舰' in skill_type
        
        # 数值处理：有航空母舰操作不除以5（保持原值），否则除以5
        if has_carrier_operation:
            merged_value = armor_value
        else:
            merged_value = armor_value / 5
        
        # 格式化数值，保留合适的小数位数
        if merged_value == int(merged_value):
            merged_value_str = f"{int(merged_value)}.00"
        else:
            merged_value_str = f"{merged_value:.2f}"
        
        # 构建合并后的后勤无人机传输量加成信息
        # 注意：数值和文字之间有一个空格
        # 从第一个bonus复制必要的字段
        first_bonus = armor_bonus
        merged_bonus = {
            'text': f"{merged_value_str}% 后勤无人机传输量加成",
            'value': merged_value_str,
            'effect_name': first_bonus['effect_name'],
            'attr_name': first_bonus['attr_name'],
            'modifying_attr_name': first_bonus.get('modifying_attr_name', ''),
            'bonuses': [armor_bonus, shield_bonus, hull_bonus]
        }
        
        # 构建新的 bonuses 列表，移除3条单独的效果加成，添加合并后的加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('后勤无人机装甲值传输量加成' in bonus_text or 
                '后勤无人机护盾传输量加成' in bonus_text or 
                '后勤无人机结构值传输量加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        # 添加合并后的后勤无人机传输量加成
        new_bonuses.append(merged_bonus)
        
        return new_bonuses, merged_bonus
    
    def _merge_remote_shield_booster_bonuses(self, bonuses):
        """合并远程护盾回充增量器加成：当运转周期减少和启动消耗减少同时存在且数值相等时，合并为一条
        
        二合一：运转周期 + 启动消耗
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找两种远程护盾回充增量器加成
        duration_bonus = None
        cap_need_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '远程护盾回充增量器运转周期减少' in bonus_text:
                duration_bonus = bonus_dict
            elif '远程护盾回充增量器启动消耗减少' in bonus_text:
                cap_need_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (duration_bonus and cap_need_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        duration_match = re.search(r'(\d+\.?\d*)%', duration_bonus['text'])
        cap_need_match = re.search(r'(\d+\.?\d*)%', cap_need_bonus['text'])
        
        if not (duration_match and cap_need_match):
            return bonuses, None
        
        duration_value = duration_match.group(1)
        cap_need_value = cap_need_match.group(1)
        
        # 检查数值是否相等
        if duration_value != cap_need_value:
            return bonuses, None
        
        # 构建合并后的远程护盾回充增量器加成信息
        merged_bonus = {
            'text': f"{duration_value}% 远程护盾回充增量器运转周期和启动消耗减少",
            'value': duration_value,
            'bonuses': [duration_bonus, cap_need_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('远程护盾回充增量器运转周期减少' in bonus_text or 
                '远程护盾回充增量器启动消耗减少' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_remote_capital_armor_bonuses(self, bonuses):
        """合并远程电容传输装置传输量加成和旗舰级远程装甲维修器维修量加成：当两者同时存在且数值相等时，合并为一条
        
        二合一：远程电容传输装置传输量 + 旗舰级远程装甲维修器维修量
        
        返回: (new_bonuses, merged_bonus)
        merged_bonus 为 None 表示没有合并，否则包含合并后的信息
        """
        # 查找远程电容传输装置传输量加成和旗舰级远程装甲维修器维修量加成
        cap_transfer_bonus = None
        capital_armor_bonus = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if '远程电容传输装置传输量加成' in bonus_text:
                cap_transfer_bonus = bonus_dict
            elif '旗舰级远程装甲维修器维修量加成' in bonus_text:
                capital_armor_bonus = bonus_dict
        
        # 检查是否两者都存在
        if not (cap_transfer_bonus and capital_armor_bonus):
            return bonuses, None
        
        # 提取数值
        import re
        cap_match = re.search(r'(\d+\.?\d*)%', cap_transfer_bonus['text'])
        armor_match = re.search(r'(\d+\.?\d*)%', capital_armor_bonus['text'])
        
        if not (cap_match and armor_match):
            return bonuses, None
        
        cap_value = cap_match.group(1)
        armor_value = armor_match.group(1)
        
        # 检查数值是否相等
        if cap_value != armor_value:
            return bonuses, None
        
        # 构建合并后的加成信息
        merged_bonus = {
            'text': f"{cap_value}% 远程电容传输装置传输量加成和旗舰级远程装甲维修器维修量加成",
            'value': cap_value,
            'bonuses': [cap_transfer_bonus, capital_armor_bonus]
        }
        
        # 构建新的 bonuses 列表，移除2条单独的效果加成
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            if ('远程电容传输装置传输量加成' in bonus_text or 
                '旗舰级远程装甲维修器维修量加成' in bonus_text):
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, merged_bonus
    
    def _merge_command_burst_bonuses(self, bonuses):
        """合并指挥脉冲波加成：信息战和装甲指挥脉冲波的多种组合
        
        优先级：
        1. 十合一：信息战(4种Buff+1持续时间) + 装甲(4种Buff+1持续时间)
        2. 信息战五合一：信息战(4种Buff+1持续时间)
        3. 装甲五合一：装甲(4种Buff+1持续时间)
        4. 信息战四合一：信息战(4种Buff)
        5. 装甲四合一：装甲(4种Buff)
        
        返回: (new_bonuses, merged_info_armor, merged_info_full, merged_armor_full, 
               merged_info_with_duration, merged_armor_with_duration, merged_info_buffs, merged_armor_buffs)
        """
        import re
        
        # 查找所有指挥脉冲波加成
        info_duration = info_buff1 = info_buff2 = info_buff3 = info_buff4 = None
        armor_duration = armor_buff1 = armor_buff2 = armor_buff3 = armor_buff4 = None
        
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            # 装甲指挥和信息战指挥合并格式（十合一格式）- 必须优先检测
            if '装甲指挥和信息战指挥脉冲波持续时间加成' in bonus_text:
                armor_duration = info_duration = bonus_dict
            elif '装甲指挥和信息战指挥脉冲波Buff1强度加成' in bonus_text:
                armor_buff1 = info_buff1 = bonus_dict
            elif '装甲指挥和信息战指挥脉冲波Buff2强度加成' in bonus_text:
                armor_buff2 = info_buff2 = bonus_dict
            elif '装甲指挥和信息战指挥脉冲波Buff3强度加成' in bonus_text:
                armor_buff3 = info_buff3 = bonus_dict
            elif '装甲指挥和信息战指挥脉冲波Buff4强度加成' in bonus_text:
                armor_buff4 = info_buff4 = bonus_dict
            # 信息战指挥脉冲波（单独格式，注意：不能匹配合并格式）
            elif '信息战指挥脉冲波持续时间加成' in bonus_text and '装甲指挥' not in bonus_text:
                info_duration = bonus_dict
            elif '信息战指挥脉冲波Buff1强度加成' in bonus_text and '装甲指挥' not in bonus_text:
                info_buff1 = bonus_dict
            elif '信息战指挥脉冲波Buff2强度加成' in bonus_text and '装甲指挥' not in bonus_text:
                info_buff2 = bonus_dict
            elif '信息战指挥脉冲波Buff3强度加成' in bonus_text and '装甲指挥' not in bonus_text:
                info_buff3 = bonus_dict
            elif '信息战指挥脉冲波Buff4强度加成' in bonus_text and '装甲指挥' not in bonus_text:
                info_buff4 = bonus_dict
            # 装甲指挥脉冲波（单独格式，注意：不能匹配合并格式）
            elif '装甲指挥脉冲波持续时间加成' in bonus_text and '信息战指挥' not in bonus_text:
                armor_duration = bonus_dict
            elif '装甲指挥脉冲波Buff1强度加成' in bonus_text and '信息战指挥' not in bonus_text:
                armor_buff1 = bonus_dict
            elif '装甲指挥脉冲波Buff2强度加成' in bonus_text and '信息战指挥' not in bonus_text:
                armor_buff2 = bonus_dict
            elif '装甲指挥脉冲波Buff3强度加成' in bonus_text and '信息战指挥' not in bonus_text:
                armor_buff3 = bonus_dict
            elif '装甲指挥脉冲波Buff4强度加成' in bonus_text and '信息战指挥' not in bonus_text:
                armor_buff4 = bonus_dict
        
        # 检查各组是否完整
        info_buffs_complete = all([info_buff1, info_buff2, info_buff3, info_buff4])
        armor_buffs_complete = all([armor_buff1, armor_buff2, armor_buff3, armor_buff4])
        info_full_complete = info_buffs_complete and info_duration
        armor_full_complete = armor_buffs_complete and armor_duration
        all_complete = info_full_complete and armor_full_complete
        
        def extract_value(bonus_dict):
            if not bonus_dict:
                return None
            match = re.search(r'(\d+\.?\d*)%', bonus_dict['text'])
            return match.group(1) if match else None
        
        def should_remove_bonus(bonus_text):
            # 合并格式（十合一）
            if ('装甲指挥和信息战指挥脉冲波持续时间加成' in bonus_text or
                '装甲指挥和信息战指挥脉冲波Buff1强度加成' in bonus_text or
                '装甲指挥和信息战指挥脉冲波Buff2强度加成' in bonus_text or
                '装甲指挥和信息战指挥脉冲波Buff3强度加成' in bonus_text or
                '装甲指挥和信息战指挥脉冲波Buff4强度加成' in bonus_text):
                return True
            # 单独格式
            return ('信息战指挥脉冲波持续时间加成' in bonus_text or
                    '信息战指挥脉冲波Buff1强度加成' in bonus_text or
                    '信息战指挥脉冲波Buff2强度加成' in bonus_text or
                    '信息战指挥脉冲波Buff3强度加成' in bonus_text or
                    '信息战指挥脉冲波Buff4强度加成' in bonus_text or
                    '装甲指挥脉冲波持续时间加成' in bonus_text or
                    '装甲指挥脉冲波Buff1强度加成' in bonus_text or
                    '装甲指挥脉冲波Buff2强度加成' in bonus_text or
                    '装甲指挥脉冲波Buff3强度加成' in bonus_text or
                    '装甲指挥脉冲波Buff4强度加成' in bonus_text)
        
        # 十合一：信息战(4种Buff+1持续时间) + 装甲(4种Buff+1持续时间)
        if all_complete:
            values = [extract_value(b) for b in [info_duration, info_buff1, info_buff2, info_buff3, info_buff4,
                                                  armor_duration, armor_buff1, armor_buff2, armor_buff3, armor_buff4]]
            if all(v == values[0] for v in values):
                merged_bonus = {
                    'text': f"{values[0]}% 装甲指挥和信息战指挥脉冲波强度和持续时间加成",
                    'value': values[0],
                    'bonuses': [info_duration, info_buff1, info_buff2, info_buff3, info_buff4,
                               armor_duration, armor_buff1, armor_buff2, armor_buff3, armor_buff4]
                }
                new_bonuses = [b for b in bonuses if not should_remove_bonus(b.get('text', ''))]
                return new_bonuses, merged_bonus, None, None, None, None, None, None
        
        # 信息战五合一
        merged_info_full = None
        if info_full_complete:
            values = [extract_value(b) for b in [info_duration, info_buff1, info_buff2, info_buff3, info_buff4]]
            if all(v == values[0] for v in values):
                merged_info_full = {
                    'text': f"{values[0]}% 信息战指挥脉冲波强度和持续时间加成",
                    'value': values[0],
                    'bonuses': [info_duration, info_buff1, info_buff2, info_buff3, info_buff4]
                }
        
        # 装甲五合一
        merged_armor_full = None
        if armor_full_complete:
            values = [extract_value(b) for b in [armor_duration, armor_buff1, armor_buff2, armor_buff3, armor_buff4]]
            if all(v == values[0] for v in values):
                merged_armor_full = {
                    'text': f"{values[0]}% 装甲指挥脉冲波强度和持续时间加成",
                    'value': values[0],
                    'bonuses': [armor_duration, armor_buff1, armor_buff2, armor_buff3, armor_buff4]
                }
        
        # 信息战四合一（只有Buff）- 只有当五合一失败时才检查
        merged_info_buffs = None
        if not merged_info_full and info_buffs_complete:
            values = [extract_value(b) for b in [info_buff1, info_buff2, info_buff3, info_buff4]]
            if all(v == values[0] for v in values):
                merged_info_buffs = {
                    'text': f"{values[0]}% 信息战指挥脉冲波强度加成",
                    'value': values[0],
                    'bonuses': [info_buff1, info_buff2, info_buff3, info_buff4]
                }
        
        # 装甲四合一（只有Buff）- 只有当五合一失败时才检查
        merged_armor_buffs = None
        if not merged_armor_full and armor_buffs_complete:
            values = [extract_value(b) for b in [armor_buff1, armor_buff2, armor_buff3, armor_buff4]]
            if all(v == values[0] for v in values):
                merged_armor_buffs = {
                    'text': f"{values[0]}% 装甲指挥脉冲波强度加成",
                    'value': values[0],
                    'bonuses': [armor_buff1, armor_buff2, armor_buff3, armor_buff4]
                }
        
        # 构建新的 bonuses 列表
        new_bonuses = []
        for bonus_dict in bonuses:
            bonus_text = bonus_dict.get('text', '')
            should_remove = False
            if merged_info_full and any(b and bonus_dict == b for b in [info_duration, info_buff1, info_buff2, info_buff3, info_buff4]):
                should_remove = True
            if merged_armor_full and any(b and bonus_dict == b for b in [armor_duration, armor_buff1, armor_buff2, armor_buff3, armor_buff4]):
                should_remove = True
            if merged_info_buffs and any(b and bonus_dict == b for b in [info_buff1, info_buff2, info_buff3, info_buff4]):
                should_remove = True
            if merged_armor_buffs and any(b and bonus_dict == b for b in [armor_buff1, armor_buff2, armor_buff3, armor_buff4]):
                should_remove = True
            if should_remove:
                continue
            new_bonuses.append(bonus_dict)
        
        return new_bonuses, None, merged_info_full, merged_armor_full, None, None, merged_info_buffs, merged_armor_buffs
    
    def _build_result(self, item_info, skill_bonuses_dict, unique_bonuses_list, attr_dict, item_name_cn):
        """构建结果文本（同步自 test_111_v2.py）
        
        新格式: effect_name|modified_attr|modifying_attr
        """
        display_name = item_name_cn or item_info.get('name', '未知物品')
        
        result = f"{display_name}\n\n"
        
        # 从 effect_dict 获取技能类型顺序
        skill_order = list(_effect_dict.SKILL_TYPE_RULES.keys())
        
        # 按顺序输出技能加成
        result_parts = []
        for skill_type in skill_order:
            if skill_type in skill_bonuses_dict and skill_bonuses_dict[skill_type]:
                bonuses = skill_bonuses_dict[skill_type]
                # 处理装甲抗性加成合并
                bonuses, merged_armor_bonus = self._merge_armor_resistance_bonuses(bonuses)
                # 处理武器扰断器效果加成合并
                bonuses, merged_weapon_disruption_bonus = self._merge_weapon_disruption_bonuses(bonuses)
                # 处理武器扰断器最佳射程和失准范围合并
                bonuses, merged_range_falloff_bonus = self._merge_weapon_range_falloff_bonuses(bonuses)
                # 处理索敌扰断器CPU需求和启动消耗合并
                bonuses, merged_target_painter_bonus = self._merge_target_painter_cpu_activation_bonuses(bonuses)
                # 处理加力燃烧器和微型跃迁推进器过载效果加成合并
                bonuses, merged_propulsion_overload_bonus = self._merge_propulsion_overload_bonuses(bonuses)
                # 处理拦截失效装置三个加成合并
                bonuses, merged_interdiction_nullifier_bonus = self._merge_interdiction_nullifier_bonuses(bonuses)
                # 处理轻型导弹和火箭四种伤害加成合并
                bonuses, merged_light_missile_bonus = self._merge_light_missile_damage_bonuses(bonuses)
                # 处理能量中和器和掠能器最佳射程加成合并
                bonuses, merged_neutralizer_range_bonus = self._merge_neutralizer_range_bonuses(bonuses)
                # 处理能量中和器和掠能器失准范围加成合并
                bonuses, merged_neutralizer_falloff_bonus = self._merge_neutralizer_falloff_bonuses(bonuses)
                # 处理掠能器和能量中和器吸取量加成合并
                bonuses, merged_neutralizer_amount_bonus = self._merge_neutralizer_amount_bonuses(bonuses)
                # 处理掠能器有效距离和吸取量加成合并
                bonuses, merged_nosferatu_range_amount_bonus = self._merge_nosferatu_range_amount_bonuses(bonuses)
                # 处理远程装甲维修器运转周期和启动消耗合并
                bonuses, merged_remote_armor_bonus = self._merge_remote_armor_repair_bonuses(bonuses)
                # 处理无人机伤害和HP加成合并（四合一：伤害+装甲+结构+护盾）
                bonuses, merged_drone_damage_hp_bonus = self._merge_drone_damage_hp_bonuses(bonuses)
                # 处理小型能量炮台最佳射程和失准范围加成合并（二合一）
                bonuses, merged_small_energy_range_bonus = self._merge_small_energy_turret_range_bonuses(bonuses)
                # 处理远程装甲维修器最佳射程和失准范围加成合并（二合一）
                bonuses, merged_remote_armor_range_bonus = self._merge_remote_armor_repair_range_bonuses(bonuses)
                # 处理中型能量炮台最佳射程和失准范围加成合并（二合一）
                bonuses, merged_medium_energy_range_bonus = self._merge_medium_energy_turret_range_bonuses(bonuses)
                # 处理武器伤害加成合并（鱼雷、巡航导弹、重型快速导弹、大型能量炮台、重型导弹、重型攻击导弹、火箭、超大型巡航导弹、超大型鱼雷、破坏型长枪）
                bonuses, merged_weapon_bonus, merged_torpedo_bonus, merged_cruise_bonus, merged_rapid_bonus, merged_heavy_assault_heavy_bonus, merged_heavy_assault_bonus, merged_heavy_bonus, merged_rocket_bonus, merged_rocket_partial_bonus, merged_xl_cruise_torpedo_bonus, merged_lance_bonus = self._merge_weapon_damage_bonuses(bonuses)
                # 处理指挥脉冲波加成合并（信息战和装甲指挥脉冲波）
                bonuses, merged_command_burst, merged_info_full, merged_armor_full, merged_info_duration, merged_armor_duration, merged_info_buffs, merged_armor_buffs = self._merge_command_burst_bonuses(bonuses)
                # 处理导弹最大速度加成合并（二合一：重型导弹+重型攻击导弹）
                bonuses, merged_missile_velocity_bonus = self._merge_missile_velocity_bonuses(bonuses)
                # 处理后勤无人机传输量加成合并（三合一：装甲+护盾+结构）
                bonuses, merged_logistics_drone_bonus = self._merge_logistics_drone_bonuses(bonuses, skill_type)
                # 处理远程感应抑阻器效果加成合并（二合一：最大锁定范围+扫描分辨率）
                bonuses, merged_sensor_dampener_bonus = self._merge_sensor_dampener_bonuses(bonuses)
                # 处理护盾抗性加成合并（四合一：电磁+爆炸+动能+热能）
                bonuses, merged_shield_resistance_bonus = self._merge_shield_resistance_bonuses(bonuses)
                # 处理ECM目标干扰器强度加成合并（四合一：引力+磁力+光雷达+雷达）
                bonuses, merged_ecm_strength_bonus = self._merge_ecm_strength_bonuses(bonuses)
                # 处理远程护盾回充增量器加成合并（二合一：运转周期+启动消耗）
                bonuses, merged_remote_shield_bonus = self._merge_remote_shield_booster_bonuses(bonuses)
                # 处理远程电容传输装置传输量加成和旗舰级远程装甲维修器维修量加成合并（二合一）
                bonuses, merged_remote_capital_armor_bonus = self._merge_remote_capital_armor_bonuses(bonuses)
                
                # 如果舰船有战略巡洋舰操作技能加成，先添加子系统固定加成
                part = ""
                if skill_type == '艾玛战略巡洋舰操作':
                    part = "艾玛防御子系统每升一级:\n"
                    part += "· 艾玛防御子系统效果加成\n"
                    part += "艾玛攻击子系统每升一级:\n"
                    part += "· 艾玛攻击子系统效果加成\n"
                    part += "艾玛推进子系统每升一级:\n"
                    part += "· 艾玛推进子系统效果加成\n"
                    part += "艾玛核心子系统每升一级:\n"
                    part += "· 艾玛核心子系统效果加成\n"
                    part += "\n"
                elif skill_type == '盖伦特战略巡洋舰操作':
                    part = "盖伦特防御子系统每升一级:\n"
                    part += "· 盖伦特防御子系统效果加成\n"
                    part += "盖伦特攻击子系统每升一级:\n"
                    part += "· 盖伦特攻击子系统效果加成\n"
                    part += "盖伦特推进子系统每升一级:\n"
                    part += "· 盖伦特推进子系统效果加成\n"
                    part += "盖伦特核心子系统每升一级:\n"
                    part += "· 盖伦特核心子系统效果加成\n"
                    part += "\n"
                elif skill_type == '加达里战略巡洋舰操作':
                    part = "加达里防御子系统每升一级:\n"
                    part += "· 加达里防御子系统效果加成\n"
                    part += "加达里攻击子系统每升一级:\n"
                    part += "· 加达里攻击子系统效果加成\n"
                    part += "加达里推进子系统每升一级:\n"
                    part += "· 加达里推进子系统效果加成\n"
                    part += "加达里核心子系统每升一级:\n"
                    part += "· 加达里核心子系统效果加成\n"
                    part += "\n"
                elif skill_type == '米玛塔尔战略巡洋舰操作':
                    part = "米玛塔尔防御子系统每升一级:\n"
                    part += "· 米玛塔尔防御子系统效果加成\n"
                    part += "米玛塔尔攻击子系统每升一级:\n"
                    part += "· 米玛塔尔攻击子系统效果加成\n"
                    part += "米玛塔尔推进子系统每升一级:\n"
                    part += "· 米玛塔尔推进子系统效果加成\n"
                    part += "米玛塔尔核心子系统每升一级:\n"
                    part += "· 米玛塔尔核心子系统效果加成\n"
                    part += "\n"
                
                part += f"{skill_type}每升一级:\n"
                for bonus_dict in bonuses:
                    bonus_text = bonus_dict['text']
                    # 如果描述包含"不显示"，则跳过
                    if '不显示' in bonus_text:
                        continue
                    effect_name = bonus_dict['effect_name']
                    # 新格式: effect_name|modified_attr|modifying_attr
                    modified_attr = bonus_dict['attr_name']
                    modifying_attr = bonus_dict.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({effect_name}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的装甲抗性加成，特殊格式输出
                if merged_armor_bonus:
                    bonus_text = merged_armor_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_armor_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_armor_bonus['bonuses'])):
                        bonus_info = merged_armor_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的武器扰断器效果加成，特殊格式输出
                if merged_weapon_disruption_bonus:
                    bonus_text = merged_weapon_disruption_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_weapon_disruption_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_weapon_disruption_bonus['bonuses'])):
                        bonus_info = merged_weapon_disruption_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的武器扰断器最佳射程和失准范围惩罚，特殊格式输出
                if merged_range_falloff_bonus:
                    bonus_text = merged_range_falloff_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_range_falloff_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_range_falloff_bonus['bonuses'])):
                        bonus_info = merged_range_falloff_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的索敌扰断器启动消耗和CPU需求降低，特殊格式输出
                if merged_target_painter_bonus:
                    bonus_text = merged_target_painter_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_target_painter_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_target_painter_bonus['bonuses'])):
                        bonus_info = merged_target_painter_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的加力燃烧器和微型跃迁推进器过载效果加成，特殊格式输出
                if merged_propulsion_overload_bonus:
                    bonus_text = merged_propulsion_overload_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_propulsion_overload_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_propulsion_overload_bonus['bonuses'])):
                        bonus_info = merged_propulsion_overload_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的拦截失效装置三个加成，特殊格式输出
                if merged_interdiction_nullifier_bonus:
                    bonus_text = merged_interdiction_nullifier_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_interdiction_nullifier_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_interdiction_nullifier_bonus['bonuses'])):
                        bonus_info = merged_interdiction_nullifier_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的轻型导弹和火箭四种伤害加成，特殊格式输出
                if merged_light_missile_bonus:
                    bonus_text = merged_light_missile_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_light_missile_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_light_missile_bonus['bonuses'])):
                        bonus_info = merged_light_missile_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的能量中和器和掠能器最佳射程加成，特殊格式输出
                if merged_neutralizer_range_bonus:
                    bonus_text = merged_neutralizer_range_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_neutralizer_range_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_neutralizer_range_bonus['bonuses'])):
                        bonus_info = merged_neutralizer_range_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的能量中和器和掠能器失准范围加成，特殊格式输出
                if merged_neutralizer_falloff_bonus:
                    bonus_text = merged_neutralizer_falloff_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_neutralizer_falloff_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_neutralizer_falloff_bonus['bonuses'])):
                        bonus_info = merged_neutralizer_falloff_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的掠能器和能量中和器吸取量加成，特殊格式输出
                if merged_neutralizer_amount_bonus:
                    bonus_text = merged_neutralizer_amount_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_neutralizer_amount_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_neutralizer_amount_bonus['bonuses'])):
                        bonus_info = merged_neutralizer_amount_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的掠能器吸取量和有效距离加成，特殊格式输出
                if merged_nosferatu_range_amount_bonus:
                    bonus_text = merged_nosferatu_range_amount_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_nosferatu_range_amount_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_nosferatu_range_amount_bonus['bonuses'])):
                        bonus_info = merged_nosferatu_range_amount_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的远程装甲维修器运转周期和启动消耗，特殊格式输出
                if merged_remote_armor_bonus:
                    bonus_text = merged_remote_armor_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_remote_armor_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_remote_armor_bonus['bonuses'])):
                        bonus_info = merged_remote_armor_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的无人机伤害和HP加成，特殊格式输出
                if merged_drone_damage_hp_bonus:
                    bonus_text = merged_drone_damage_hp_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_drone_damage_hp_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_drone_damage_hp_bonus['bonuses'])):
                        bonus_info = merged_drone_damage_hp_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的小型能量炮台最佳射程和失准范围加成，特殊格式输出
                if merged_small_energy_range_bonus:
                    bonus_text = merged_small_energy_range_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_small_energy_range_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_small_energy_range_bonus['bonuses'])):
                        bonus_info = merged_small_energy_range_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的远程装甲维修器最佳射程和失准范围加成，特殊格式输出
                if merged_remote_armor_range_bonus:
                    bonus_text = merged_remote_armor_range_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_remote_armor_range_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_remote_armor_range_bonus['bonuses'])):
                        bonus_info = merged_remote_armor_range_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的中型能量炮台最佳射程和失准范围加成，特殊格式输出
                if merged_medium_energy_range_bonus:
                    bonus_text = merged_medium_energy_range_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_medium_energy_range_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_medium_energy_range_bonus['bonuses'])):
                        bonus_info = merged_medium_energy_range_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的武器伤害加成（十三合一），特殊格式输出
                if merged_weapon_bonus:
                    bonus_text = merged_weapon_bonus['text']
                    first_bonus = merged_weapon_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_weapon_bonus['bonuses'])):
                        bonus_info = merged_weapon_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的鱼雷伤害加成（四合一），特殊格式输出
                if merged_torpedo_bonus:
                    bonus_text = merged_torpedo_bonus['text']
                    first_bonus = merged_torpedo_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_torpedo_bonus['bonuses'])):
                        bonus_info = merged_torpedo_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的巡航导弹伤害加成（四合一），特殊格式输出
                if merged_cruise_bonus:
                    bonus_text = merged_cruise_bonus['text']
                    first_bonus = merged_cruise_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_cruise_bonus['bonuses'])):
                        bonus_info = merged_cruise_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的重型快速导弹伤害加成（四合一），特殊格式输出
                if merged_rapid_bonus:
                    bonus_text = merged_rapid_bonus['text']
                    first_bonus = merged_rapid_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_rapid_bonus['bonuses'])):
                        bonus_info = merged_rapid_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的火箭伤害加成（四合一），特殊格式输出
                if merged_rocket_bonus:
                    bonus_text = merged_rocket_bonus['text']
                    first_bonus = merged_rocket_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_rocket_bonus['bonuses'])):
                        bonus_info = merged_rocket_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的火箭电磁、动能、热能伤害加成（三合一），特殊格式输出
                if merged_rocket_partial_bonus:
                    bonus_text = merged_rocket_partial_bonus['text']
                    first_bonus = merged_rocket_partial_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_rocket_partial_bonus['bonuses'])):
                        bonus_info = merged_rocket_partial_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的破坏型长枪伤害加成（四合一），特殊格式输出
                if merged_lance_bonus:
                    bonus_text = merged_lance_bonus['text']
                    first_bonus = merged_lance_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_lance_bonus['bonuses'])):
                        bonus_info = merged_lance_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的重型导弹和重型攻击导弹伤害加成（八合一），特殊格式输出
                if merged_heavy_assault_heavy_bonus:
                    bonus_text = merged_heavy_assault_heavy_bonus['text']
                    first_bonus = merged_heavy_assault_heavy_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_heavy_assault_heavy_bonus['bonuses'])):
                        bonus_info = merged_heavy_assault_heavy_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的重型攻击导弹伤害加成（四合一），特殊格式输出
                if merged_heavy_assault_bonus:
                    bonus_text = merged_heavy_assault_bonus['text']
                    first_bonus = merged_heavy_assault_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_heavy_assault_bonus['bonuses'])):
                        bonus_info = merged_heavy_assault_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的重型导弹伤害加成（四合一），特殊格式输出
                if merged_heavy_bonus:
                    bonus_text = merged_heavy_bonus['text']
                    first_bonus = merged_heavy_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_heavy_bonus['bonuses'])):
                        bonus_info = merged_heavy_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的超大型巡航导弹、超大型鱼雷和鱼雷伤害加成（十二合一），特殊格式输出
                if merged_xl_cruise_torpedo_bonus:
                    bonus_text = merged_xl_cruise_torpedo_bonus['text']
                    first_bonus = merged_xl_cruise_torpedo_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_xl_cruise_torpedo_bonus['bonuses'])):
                        bonus_info = merged_xl_cruise_torpedo_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的指挥脉冲波加成（十合一），特殊格式输出
                if merged_command_burst:
                    bonus_text = merged_command_burst['text']
                    first_bonus = merged_command_burst['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_command_burst['bonuses'])):
                        bonus_info = merged_command_burst['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的信息战指挥脉冲波加成（五合一），特殊格式输出
                if merged_info_full:
                    bonus_text = merged_info_full['text']
                    first_bonus = merged_info_full['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_info_full['bonuses'])):
                        bonus_info = merged_info_full['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的装甲指挥脉冲波加成（五合一），特殊格式输出
                if merged_armor_full:
                    bonus_text = merged_armor_full['text']
                    first_bonus = merged_armor_full['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_armor_full['bonuses'])):
                        bonus_info = merged_armor_full['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的信息战指挥脉冲波强度加成（四合一），特殊格式输出
                if merged_info_buffs:
                    bonus_text = merged_info_buffs['text']
                    first_bonus = merged_info_buffs['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_info_buffs['bonuses'])):
                        bonus_info = merged_info_buffs['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的装甲指挥脉冲波强度加成（四合一），特殊格式输出
                if merged_armor_buffs:
                    bonus_text = merged_armor_buffs['text']
                    first_bonus = merged_armor_buffs['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_armor_buffs['bonuses'])):
                        bonus_info = merged_armor_buffs['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的导弹最大速度加成（二合一），特殊格式输出
                if merged_missile_velocity_bonus:
                    bonus_text = merged_missile_velocity_bonus['text']
                    first_bonus = merged_missile_velocity_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_missile_velocity_bonus['bonuses'])):
                        bonus_info = merged_missile_velocity_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的后勤无人机传输量加成（三合一），特殊格式输出
                if merged_logistics_drone_bonus:
                    bonus_text = merged_logistics_drone_bonus['text']
                    first_bonus = merged_logistics_drone_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_logistics_drone_bonus['bonuses'])):
                        bonus_info = merged_logistics_drone_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的远程感应抑阻器效果加成（二合一），特殊格式输出
                if merged_sensor_dampener_bonus:
                    bonus_text = merged_sensor_dampener_bonus['text']
                    first_bonus = merged_sensor_dampener_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_sensor_dampener_bonus['bonuses'])):
                        bonus_info = merged_sensor_dampener_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的护盾抗性加成（四合一），特殊格式输出
                if merged_shield_resistance_bonus:
                    bonus_text = merged_shield_resistance_bonus['text']
                    first_bonus = merged_shield_resistance_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_shield_resistance_bonus['bonuses'])):
                        bonus_info = merged_shield_resistance_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的ECM目标干扰器强度加成（四合一），特殊格式输出
                if merged_ecm_strength_bonus:
                    bonus_text = merged_ecm_strength_bonus['text']
                    first_bonus = merged_ecm_strength_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_ecm_strength_bonus['bonuses'])):
                        bonus_info = merged_ecm_strength_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的远程护盾回充增量器加成（二合一），特殊格式输出
                if merged_remote_shield_bonus:
                    bonus_text = merged_remote_shield_bonus['text']
                    first_bonus = merged_remote_shield_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_remote_shield_bonus['bonuses'])):
                        bonus_info = merged_remote_shield_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的远程电容传输装置传输量加成和旗舰级远程装甲维修器维修量加成（二合一），特殊格式输出
                if merged_remote_capital_armor_bonus:
                    bonus_text = merged_remote_capital_armor_bonus['text']
                    first_bonus = merged_remote_capital_armor_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    for i in range(1, len(merged_remote_capital_armor_bonus['bonuses'])):
                        bonus_info = merged_remote_capital_armor_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        indent = len(bonus_text) + 1
                        part += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
                part += "\n"
                result_parts.append(part)
        
        result += "".join(result_parts)
        
        # 输出特有加成
        if unique_bonuses_list:
            result += "特有加成\n"
            # 对特有加成也进行合并处理
            unique_bonuses_list, merged_armor_bonus = self._merge_armor_resistance_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_weapon_disruption_bonus = self._merge_weapon_disruption_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_range_falloff_bonus = self._merge_weapon_range_falloff_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_target_painter_bonus = self._merge_target_painter_cpu_activation_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_propulsion_overload_bonus = self._merge_propulsion_overload_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_interdiction_nullifier_bonus = self._merge_interdiction_nullifier_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_light_missile_bonus = self._merge_light_missile_damage_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_neutralizer_range_bonus = self._merge_neutralizer_range_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_neutralizer_falloff_bonus = self._merge_neutralizer_falloff_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_neutralizer_amount_bonus = self._merge_neutralizer_amount_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_nosferatu_range_amount_bonus = self._merge_nosferatu_range_amount_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_remote_armor_bonus = self._merge_remote_armor_repair_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_drone_damage_hp_bonus = self._merge_drone_damage_hp_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_small_energy_range_bonus = self._merge_small_energy_turret_range_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_remote_armor_range_bonus = self._merge_remote_armor_repair_range_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_medium_energy_range_bonus = self._merge_medium_energy_turret_range_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_weapon_bonus, merged_torpedo_bonus, merged_cruise_bonus, merged_rapid_bonus, merged_heavy_assault_heavy_bonus, merged_heavy_assault_bonus, merged_heavy_bonus, merged_rocket_bonus, merged_rocket_partial_bonus, merged_xl_cruise_torpedo_bonus, merged_lance_bonus = self._merge_weapon_damage_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_command_burst, merged_info_full, merged_armor_full, merged_info_duration, merged_armor_duration, merged_info_buffs, merged_armor_buffs = self._merge_command_burst_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_missile_velocity_bonus = self._merge_missile_velocity_bonuses(unique_bonuses_list)
            # 判断是否有航空母舰操作，用于后勤无人机传输量加成计算
            has_carrier_for_logistics_drone = any('航空母舰操作' in skill_key for skill_key in skill_bonuses_dict.keys())
            unique_bonuses_list, merged_logistics_drone_bonus = self._merge_logistics_drone_bonuses(unique_bonuses_list, '航空母舰操作' if has_carrier_for_logistics_drone else None)
            unique_bonuses_list, merged_sensor_dampener_bonus = self._merge_sensor_dampener_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_shield_resistance_bonus = self._merge_shield_resistance_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_ecm_strength_bonus = self._merge_ecm_strength_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_remote_shield_bonus = self._merge_remote_shield_booster_bonuses(unique_bonuses_list)
            unique_bonuses_list, merged_remote_capital_armor_bonus = self._merge_remote_capital_armor_bonuses(unique_bonuses_list)
            
            for bonus_dict in unique_bonuses_list:
                bonus_text = bonus_dict['text']
                # 如果描述包含"不显示"，则跳过
                if '不显示' in bonus_text:
                    continue
                effect_name = bonus_dict['effect_name']
                # 新格式: effect_name|modified_attr|modifying_attr
                modified_attr = bonus_dict['attr_name']
                modifying_attr = bonus_dict.get('modifying_attr_name', '')
                result += f"{bonus_text} ({effect_name}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的装甲抗性加成
            if merged_armor_bonus:
                bonus_text = merged_armor_bonus['text']
                first_bonus = merged_armor_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_armor_bonus['bonuses'])):
                    bonus_info = merged_armor_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的武器扰断器效果加成
            if merged_weapon_disruption_bonus:
                bonus_text = merged_weapon_disruption_bonus['text']
                first_bonus = merged_weapon_disruption_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_weapon_disruption_bonus['bonuses'])):
                    bonus_info = merged_weapon_disruption_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的武器扰断器最佳射程和失准范围惩罚
            if merged_range_falloff_bonus:
                bonus_text = merged_range_falloff_bonus['text']
                first_bonus = merged_range_falloff_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_range_falloff_bonus['bonuses'])):
                    bonus_info = merged_range_falloff_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的索敌扰断器启动消耗和CPU需求降低
            if merged_target_painter_bonus:
                bonus_text = merged_target_painter_bonus['text']
                first_bonus = merged_target_painter_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_target_painter_bonus['bonuses'])):
                    bonus_info = merged_target_painter_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的加力燃烧器和微型跃迁推进器过载效果加成
            if merged_propulsion_overload_bonus:
                bonus_text = merged_propulsion_overload_bonus['text']
                first_bonus = merged_propulsion_overload_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_propulsion_overload_bonus['bonuses'])):
                    bonus_info = merged_propulsion_overload_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的拦截失效装置三个加成
            if merged_interdiction_nullifier_bonus:
                bonus_text = merged_interdiction_nullifier_bonus['text']
                first_bonus = merged_interdiction_nullifier_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_interdiction_nullifier_bonus['bonuses'])):
                    bonus_info = merged_interdiction_nullifier_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的轻型导弹和火箭四种伤害加成
            if merged_light_missile_bonus:
                bonus_text = merged_light_missile_bonus['text']
                first_bonus = merged_light_missile_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_light_missile_bonus['bonuses'])):
                    bonus_info = merged_light_missile_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的能量中和器和掠能器最佳射程加成
            if merged_neutralizer_range_bonus:
                bonus_text = merged_neutralizer_range_bonus['text']
                first_bonus = merged_neutralizer_range_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_neutralizer_range_bonus['bonuses'])):
                    bonus_info = merged_neutralizer_range_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的能量中和器和掠能器失准范围加成
            if merged_neutralizer_falloff_bonus:
                bonus_text = merged_neutralizer_falloff_bonus['text']
                first_bonus = merged_neutralizer_falloff_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_neutralizer_falloff_bonus['bonuses'])):
                    bonus_info = merged_neutralizer_falloff_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的掠能器和能量中和器吸取量加成
            if merged_neutralizer_amount_bonus:
                bonus_text = merged_neutralizer_amount_bonus['text']
                first_bonus = merged_neutralizer_amount_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_neutralizer_amount_bonus['bonuses'])):
                    bonus_info = merged_neutralizer_amount_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的掠能器吸取量和有效距离加成
            if merged_nosferatu_range_amount_bonus:
                bonus_text = merged_nosferatu_range_amount_bonus['text']
                first_bonus = merged_nosferatu_range_amount_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_nosferatu_range_amount_bonus['bonuses'])):
                    bonus_info = merged_nosferatu_range_amount_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的远程装甲维修器运转周期和启动消耗
            if merged_remote_armor_bonus:
                bonus_text = merged_remote_armor_bonus['text']
                first_bonus = merged_remote_armor_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_remote_armor_bonus['bonuses'])):
                    bonus_info = merged_remote_armor_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的无人机伤害和HP加成
            if merged_drone_damage_hp_bonus:
                bonus_text = merged_drone_damage_hp_bonus['text']
                first_bonus = merged_drone_damage_hp_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_drone_damage_hp_bonus['bonuses'])):
                    bonus_info = merged_drone_damage_hp_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的小型能量炮台最佳射程和失准范围加成
            if merged_small_energy_range_bonus:
                bonus_text = merged_small_energy_range_bonus['text']
                first_bonus = merged_small_energy_range_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_small_energy_range_bonus['bonuses'])):
                    bonus_info = merged_small_energy_range_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的远程装甲维修器最佳射程和失准范围加成
            if merged_remote_armor_range_bonus:
                bonus_text = merged_remote_armor_range_bonus['text']
                first_bonus = merged_remote_armor_range_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_remote_armor_range_bonus['bonuses'])):
                    bonus_info = merged_remote_armor_range_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的中型能量炮台最佳射程和失准范围加成
            if merged_medium_energy_range_bonus:
                bonus_text = merged_medium_energy_range_bonus['text']
                first_bonus = merged_medium_energy_range_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_medium_energy_range_bonus['bonuses'])):
                    bonus_info = merged_medium_energy_range_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的武器伤害加成（十三合一）
            if merged_weapon_bonus:
                bonus_text = merged_weapon_bonus['text']
                first_bonus = merged_weapon_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_weapon_bonus['bonuses'])):
                    bonus_info = merged_weapon_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的鱼雷伤害加成（四合一）
            if merged_torpedo_bonus:
                bonus_text = merged_torpedo_bonus['text']
                first_bonus = merged_torpedo_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_torpedo_bonus['bonuses'])):
                    bonus_info = merged_torpedo_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的巡航导弹伤害加成（四合一）
            if merged_cruise_bonus:
                bonus_text = merged_cruise_bonus['text']
                first_bonus = merged_cruise_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_cruise_bonus['bonuses'])):
                    bonus_info = merged_cruise_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的重型快速导弹伤害加成（四合一）
            if merged_rapid_bonus:
                bonus_text = merged_rapid_bonus['text']
                first_bonus = merged_rapid_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_rapid_bonus['bonuses'])):
                    bonus_info = merged_rapid_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的火箭伤害加成（四合一）
            if merged_rocket_bonus:
                bonus_text = merged_rocket_bonus['text']
                first_bonus = merged_rocket_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_rocket_bonus['bonuses'])):
                    bonus_info = merged_rocket_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的火箭电磁、动能、热能伤害加成（三合一）
            if merged_rocket_partial_bonus:
                bonus_text = merged_rocket_partial_bonus['text']
                first_bonus = merged_rocket_partial_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_rocket_partial_bonus['bonuses'])):
                    bonus_info = merged_rocket_partial_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的破坏型长枪伤害加成（四合一）
            if merged_lance_bonus:
                bonus_text = merged_lance_bonus['text']
                first_bonus = merged_lance_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_lance_bonus['bonuses'])):
                    bonus_info = merged_lance_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的重型导弹和重型攻击导弹伤害加成（八合一）
            if merged_heavy_assault_heavy_bonus:
                bonus_text = merged_heavy_assault_heavy_bonus['text']
                first_bonus = merged_heavy_assault_heavy_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_heavy_assault_heavy_bonus['bonuses'])):
                    bonus_info = merged_heavy_assault_heavy_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的重型攻击导弹伤害加成（四合一）
            if merged_heavy_assault_bonus:
                bonus_text = merged_heavy_assault_bonus['text']
                first_bonus = merged_heavy_assault_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_heavy_assault_bonus['bonuses'])):
                    bonus_info = merged_heavy_assault_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的重型导弹伤害加成（四合一）
            if merged_heavy_bonus:
                bonus_text = merged_heavy_bonus['text']
                first_bonus = merged_heavy_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_heavy_bonus['bonuses'])):
                    bonus_info = merged_heavy_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的超大型巡航导弹、超大型鱼雷和鱼雷伤害加成（十二合一）
            if merged_xl_cruise_torpedo_bonus:
                bonus_text = merged_xl_cruise_torpedo_bonus['text']
                first_bonus = merged_xl_cruise_torpedo_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_xl_cruise_torpedo_bonus['bonuses'])):
                    bonus_info = merged_xl_cruise_torpedo_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的导弹最大速度加成（二合一）
            if merged_missile_velocity_bonus:
                bonus_text = merged_missile_velocity_bonus['text']
                first_bonus = merged_missile_velocity_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_missile_velocity_bonus['bonuses'])):
                    bonus_info = merged_missile_velocity_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的后勤无人机传输量加成（三合一）
            if merged_logistics_drone_bonus:
                bonus_text = merged_logistics_drone_bonus['text']
                first_bonus = merged_logistics_drone_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_logistics_drone_bonus['bonuses'])):
                    bonus_info = merged_logistics_drone_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的远程感应抑阻器效果加成（二合一）
            if merged_sensor_dampener_bonus:
                bonus_text = merged_sensor_dampener_bonus['text']
                first_bonus = merged_sensor_dampener_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_sensor_dampener_bonus['bonuses'])):
                    bonus_info = merged_sensor_dampener_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的护盾抗性加成（四合一）
            if merged_shield_resistance_bonus:
                bonus_text = merged_shield_resistance_bonus['text']
                first_bonus = merged_shield_resistance_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_shield_resistance_bonus['bonuses'])):
                    bonus_info = merged_shield_resistance_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的ECM目标干扰器强度加成（四合一）
            if merged_ecm_strength_bonus:
                bonus_text = merged_ecm_strength_bonus['text']
                first_bonus = merged_ecm_strength_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_ecm_strength_bonus['bonuses'])):
                    bonus_info = merged_ecm_strength_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的远程护盾回充增量器加成（二合一）
            if merged_remote_shield_bonus:
                bonus_text = merged_remote_shield_bonus['text']
                first_bonus = merged_remote_shield_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_remote_shield_bonus['bonuses'])):
                    bonus_info = merged_remote_shield_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的远程电容传输装置传输量加成和旗舰级远程装甲维修器维修量加成（二合一）
            if merged_remote_capital_armor_bonus:
                bonus_text = merged_remote_capital_armor_bonus['text']
                first_bonus = merged_remote_capital_armor_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_remote_capital_armor_bonus['bonuses'])):
                    bonus_info = merged_remote_capital_armor_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的指挥脉冲波加成（十合一）
            if merged_command_burst:
                bonus_text = merged_command_burst['text']
                first_bonus = merged_command_burst['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_command_burst['bonuses'])):
                    bonus_info = merged_command_burst['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的信息战指挥脉冲波加成（五合一）
            if merged_info_full:
                bonus_text = merged_info_full['text']
                first_bonus = merged_info_full['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_info_full['bonuses'])):
                    bonus_info = merged_info_full['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的装甲指挥脉冲波加成（五合一）
            if merged_armor_full:
                bonus_text = merged_armor_full['text']
                first_bonus = merged_armor_full['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_armor_full['bonuses'])):
                    bonus_info = merged_armor_full['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的信息战指挥脉冲波强度加成（四合一）
            if merged_info_buffs:
                bonus_text = merged_info_buffs['text']
                first_bonus = merged_info_buffs['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_info_buffs['bonuses'])):
                    bonus_info = merged_info_buffs['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的装甲指挥脉冲波强度加成（四合一）
            if merged_armor_buffs:
                bonus_text = merged_armor_buffs['text']
                first_bonus = merged_armor_buffs['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text} ({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_armor_buffs['bonuses'])):
                    bonus_info = merged_armor_buffs['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
            # 如果舰船有突击护卫舰操作技能加成，添加固定特有加成
            if '突击护卫舰操作' in skill_bonuses_dict:
                result += "· 可以装配突击损伤控制装备\n"
            
            # 如果舰船有重型突击巡洋舰操作技能加成，添加固定特有加成
            if '重型突击巡洋舰操作' in skill_bonuses_dict:
                result += "· 可以装配突击损伤控制装备\n"
            
            # 如果舰船有重型拦截舰操作技能加成，添加固定特有加成
            if '重型拦截舰操作' in skill_bonuses_dict:
                result += "· 可以装配跃迁扰断力场发生器和诱导力场发生器\n"
            
            # 如果舰船有战略巡洋舰操作技能加成，添加固定特有加成
            if any('战略巡洋舰操作' in skill_key for skill_key in skill_bonuses_dict.keys()):
                result += "· 改装件从舰船上移除不会销毁\n"
            
            # 如果舰船有隐形特勤舰操作技能加成，添加固定特有加成
            if '隐形特勤舰操作' in skill_bonuses_dict:
                # 检查是否有炸弹伤害加成
                has_bomb_bonus = False
                for bonus_dict in skill_bonuses_dict['隐形特勤舰操作']:
                    if '炸弹' in bonus_dict.get('text', ''):
                        has_bomb_bonus = True
                        break
                
                if has_bomb_bonus:
                    result += "· 可以装备隐秘行动隐形装置、隐秘诱导力场发生器和炸弹发射器\n"
                else:
                    result += "· 可以装备隐秘行动隐形装置和隐秘诱导力场发生器\n"
            
            # 如果特有加成有指挥脉冲波效果范围加成，添加固定特有加成
            has_command_burst_bonus = False
            for bonus_dict in unique_bonuses_list:
                if '指挥脉冲波效果范围加成' in bonus_dict.get('text', ''):
                    has_command_burst_bonus = True
                    break
            if has_command_burst_bonus:
                # 检查舰船类型，决定可以装配几个指挥脉冲波装备
                has_command_ship = '指挥舰操作' in skill_bonuses_dict
                has_carrier = any('航空母舰操作' in skill_key for skill_key in skill_bonuses_dict.keys())
                has_titan = '泰坦操作' in skill_bonuses_dict
                
                if has_titan:
                    result += "· 可以装配三个指挥脉冲波装备\n"
                elif has_command_ship or has_carrier:
                    result += "· 可以装配两个指挥脉冲波装备\n"
                else:
                    result += "· 可以装配一个指挥脉冲波装备\n"
            
            # 如果舰船有指挥驱逐舰操作技能加成，添加固定特有加成
            if '指挥驱逐舰操作' in skill_bonuses_dict:
                result += "· 可以装配微型跳跃力场发生器\n"
                result += "· 可以装配一个指挥脉冲波装备\n"
            
            # 如果舰船包含航空母舰操作，且特有加成中有后勤无人机传输量加成，添加固定特有加成
            has_carrier_for_logistics = any('航空母舰操作' in skill_key for skill_key in skill_bonuses_dict.keys())
            if has_carrier_for_logistics:
                has_logistics_drone_bonus = any('后勤无人机传输量加成' in bonus_dict.get('text', '') for bonus_dict in unique_bonuses_list)
                if has_logistics_drone_bonus:
                    result += "· 可以装配一个会战型紧急修复增强模块\n"
                    result += "· 只能启用一个电容注电器装备\n"
            
            # 如果舰船有拦截舰操作技能加成，添加固定特有加成
            if '拦截舰操作' in skill_bonuses_dict:
                result += "· 可以安装拦截泡发射器\n"
            
            # 如果舰船有黑隐特勤舰操作技能加成，添加固定特有加成
            if '黑隐特勤舰操作' in skill_bonuses_dict:
                result += "· 可以装备诱导力场发生器、隐秘诱导力场发生器和隐秘跳跃通道发生器\n"
                result += "75.00% 减少跳跃距离对产生跳跃疲劳的影响\n"
            
            # 如果舰船有掠夺舰操作技能加成，添加固定特有加成
            if '掠夺舰操作' in skill_bonuses_dict:
                result += "· 可以装配堡垒装备\n"
            
            # 如果舰船有无畏舰操作技能加成，添加固定特有加成
            if any('无畏舰操作' in skill_key for skill_key in skill_bonuses_dict.keys()):
                result += "· 可以装配一个会战装备\n"
            
            # 如果舰船有长枪无畏舰操作技能加成，添加固定特有加成
            if any('长枪无畏舰操作' in skill_key for skill_key in skill_bonuses_dict.keys()):
                result += "· 可以装配一个破坏型长枪\n"
            
            # 如果舰船是忏悔者级，添加战术驱逐舰模式加成
            if '忏悔者级' in display_name or 'Confessor' in display_name:
                result += "· 当战术驱逐舰启用三种模式中的任意一种会获得额外加成。每10秒钟只能切换一次模式。\n"
                result += "· 防御模式\n"
                result += "33.30% 启用防御模式后装甲抗性加成\n"
                result += "33.30% 启用防御模式后信号半径降低\n"
                result += "33.30% 启用防御模式时，远程装甲维修器的修复量提高，启动消耗降低。\n"
                result += "· 高速模式\n"
                result += "66.60% 启用高速模式后加力燃烧器和微型跃迁推进器的速度增量加成\n"
                result += "33.30% 启用高速模式后惯性系数加成\n"
                result += "· 狙击模式\n"
                result += "66.60% 启用狙击模式后小型能量炮台最佳射程加成\n"
                result += "33.30% 启用狙击模式后小型能量炮台伤害加成\n"
                result += "100.00% 启用狙击模式后感应强度和锁定距离加成\n"
                result += "66.60% 启用狙击模式后对敌方的感应抑阻器和武器扰断器的抗性提高\n"
            
            # 如果舰船有侦察舰操作技能加成，添加固定特有加成
            if '侦察舰操作' in skill_bonuses_dict:
                # 检查特有加成中是否有隐形装置重启延时降到
                has_cloak_reactivation = False
                for bonus_dict in unique_bonuses_list:
                    if '隐形装置重启延时降到' in bonus_dict.get('text', ''):
                        has_cloak_reactivation = True
                        break
                if has_cloak_reactivation:
                    result += "· 可以装备隐秘行动隐形装置和隐秘诱导力场发生器\n"
                else:
                    result += "· 不能被定向扫描器探测到\n"
            
            result += "\n"
        
        # 如果舰船有侦察舰操作技能加成且没有特有加成，添加特有加成标题和固定加成
        if '侦察舰操作' in skill_bonuses_dict and not unique_bonuses_list:
            result += "特有加成：\n"
            result += "· 不能被定向扫描器探测到\n"
            result += "\n"
        
        return result

    async def _get_item_info(self, item_id, event, item_name_cn=''):
        """获取物品信息的内部方法"""
        item_info = await self.esi_request(f"/v3/universe/types/{item_id}/")
        if item_info:
            # 提取属性
            attr_dict = self._extract_attributes(item_info)
            
            # 处理技能加成和特有加成
            dogma_effects = item_info.get('dogma_effects', [])
            skill_bonuses_dict, unique_bonuses = await self._process_bonuses(dogma_effects, attr_dict, self.session, item_info.get('name', ''))
            
            # 构建结果
            result = self._build_result(item_info, skill_bonuses_dict, unique_bonuses, attr_dict, item_name_cn)
            
            yield event.plain_result(result)
        else:
            yield event.plain_result(f"未找到物品ID {item_id} 的信息")

    async def esi_request(self, endpoint, method="GET", data=None):
        """发送ESI请求"""
        base_url = "https://ali-esi.evepc.163.com"
        url = f"{base_url}{endpoint}"
        
        try:
            # 使用类初始化时创建的session
            if not self.session:
                # 如果session未初始化，创建一个临时session
                async with aiohttp.ClientSession() as session:
                    if method == "GET":
                        async with session.get(url) as response:
                            if response.status == 200:
                                return await response.json()
                            else:
                                logger.error(f"ESI请求失败: {response.status} - {url}")
                                return None
                    elif method == "POST":
                        async with session.post(url, json=data) as response:
                            if response.status == 200:
                                return await response.json()
                            else:
                                logger.error(f"ESI请求失败: {response.status} - {url}")
                                return None
            else:
                # 使用已初始化的session
                if method == "GET":
                    async with self.session.get(url) as response:
                        if response.status == 200:
                            return await response.json()
                        else:
                            logger.error(f"ESI请求失败: {response.status} - {url}")
                            return None
                elif method == "POST":
                    async with self.session.post(url, json=data) as response:
                        if response.status == 200:
                            return await response.json()
                        else:
                            logger.error(f"ESI请求失败: {response.status} - {url}")
                            return None
        except Exception as e:
            logger.error(f"ESI请求异常: {e} - {url}")
            return None

    async def search_item_by_name(self, name):
        """使用市场中心API搜索物品，如果失败则使用ESI搜索API作为备选
        注意：市场中心API需要使用POST请求，不能使用GET请求
        """
        # 先尝试使用市场中心API
        try:
            url = "https://www.ceve-market.org/api/searchname"
            data = {"name": name}
            
            # 使用类初始化时创建的session
            if not self.session:
                # 如果session未初始化，创建一个临时session
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, data=data) as response:
                        if response.status == 200:
                            result = await response.json()
                            logger.info(f"市场中心搜索结果: {len(result)}个物品")
                            return result
                        else:
                            logger.error(f"市场中心搜索失败: {response.status}，尝试使用ESI搜索API")
                            # 市场中心API失败，尝试使用ESI搜索API
                            return await self._search_item_by_name_esi(name)
            else:
                # 使用已初始化的session
                async with self.session.post(url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"市场中心搜索结果: {len(result)}个物品")
                        return result
                    else:
                        logger.error(f"市场中心搜索失败: {response.status}，尝试使用ESI搜索API")
                        # 市场中心API失败，尝试使用ESI搜索API
                        return await self._search_item_by_name_esi(name)
        except Exception as e:
            logger.error(f"市场中心搜索异常: {e}，尝试使用ESI搜索API")
            # 市场中心API异常，尝试使用ESI搜索API
            return await self._search_item_by_name_esi(name)
    
    async def _search_item_by_name_esi(self, name):
        """使用ESI搜索API搜索物品"""
        try:
            base_url = "https://ali-esi.evepc.163.com/latest"
            url = f"{base_url}/universe/ids/"
            
            # 使用类初始化时创建的session
            if not self.session:
                # 如果session未初始化，创建一个临时session
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=[name]) as response:
                        if response.status == 200:
                            result = await response.json()
                            inventory_types = result.get('inventory_types', [])
                            # 转换为与市场中心API相同的格式
                            items = []
                            for item in inventory_types:
                                items.append({
                                    'typeid': item.get('id'),
                                    'typename': item.get('name')
                                })
                            logger.info(f"ESI搜索API结果: {len(items)}个物品")
                            return items
                        else:
                            logger.error(f"ESI搜索API失败: {response.status}")
                            return []
            else:
                # 使用已初始化的session
                async with self.session.post(url, json=[name]) as response:
                    if response.status == 200:
                        result = await response.json()
                        inventory_types = result.get('inventory_types', [])
                        # 转换为与市场中心API相同的格式
                        items = []
                        for item in inventory_types:
                            items.append({
                                'typeid': item.get('id'),
                                'typename': item.get('name')
                            })
                        logger.info(f"ESI搜索API结果: {len(items)}个物品")
                        return items
                    else:
                        logger.error(f"ESI搜索API失败: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"ESI搜索API异常: {e}")
            return []

    def _is_skin(self, item_name):
        """判断物品是否为涂装（SKIN）"""
        skin_keywords = ['涂装', 'Skin', 'SKIN', 'skin']
        return any(keyword in item_name for keyword in skin_keywords)
    
    def _is_blueprint(self, item_name):
        """判断物品是否为蓝图"""
        blueprint_keywords = ['蓝图', 'Blueprint', 'BLUEPRINT', 'blueprint']
        return any(keyword in item_name for keyword in blueprint_keywords)
    
    def _format_bonus_value(self, value):
        """格式化加成数值，当有小数时保留两位小数，没有小数时保留整数"""
        if isinstance(value, (int, float)):
            if value.is_integer():
                return f"{int(value)}"
            else:
                return f"{value:.2f}"
        return str(value)
    
    @filter.command("帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """🚀 EVE ESI 插件帮助

━━━━━━━━━━━━━━━━━━━━━━
📊 市场价格查询
━━━━━━━━━━━━━━━━━━━━━━
/吉他 <物品名称或ID> - 查询吉他市场价格
/jt <物品名称或ID> - 短命令
示例: /吉他 三钛合金 或 /jt 34

━━━━━━━━━━━━━━━━━━━━━━
⚔️ 舰船加成查询
━━━━━━━━━━━━━━━━━━━━━━
/加成 <物品名称或ID> - 查看舰船技能加成和特有加成
示例: /加成 乌鸦 或 /加成 670

输出格式说明:
• 技能加成按技能类型分组显示
• 特有加成单独列出
• 每条加成后显示(effect_name|attr_name)供参考

━━━━━━━━━━━━━━━━━━━━━━
🔧 加成字典管理
━━━━━━━━━━━━━━━━━━━━━━
/加成修改 原描述(effect|attr)=新描述
  → 修改加成描述文字
  示例: /加成修改 能量炮台最佳射程加成(shipETOptimalRange2AF|maxRange)=小型能量炮台最佳射程加成

/加成修改 原技能名=新技能名
  → 修改技能类型名称
  示例: /加成修改 旗舰巡洋舰操作=航空母舰操作

/加成修改 描述(effect|attr)+技能类型名
  → 添加effect到技能类型映射
  示例: /加成修改 武器扰断器效果加成(shipBonusEwWeaponDisruptionStrengthAF2|trackingSpeedBonus)+艾玛航空母舰操作

━━━━━━━━━━━━━━━━━━━━━━
🏷️ 简称管理
━━━━━━━━━━━━━━━━━━━━━━
/简称 <全称>=<简称> - 添加物品简称
  示例: /简称 鱼鹰级海军型=海鱼鹰

/简称列表 [全称或简称] - 查看简称列表
  示例: /简称列表 鱼鹰级海军型

/简称删除 <简称> - 删除简称
  示例: /简称删除 海鱼鹰

━━━━━━━━━━━━━━━━━━━━━━
💡 使用提示
━━━━━━━━━━━━━━━━━━━━━━
• 支持中文名称、英文名称或物品ID
• 伊甸币(PLEX)查询暂不支持
• 模糊搜索自动过滤涂装和蓝图
• 一个全称可设置多个简称
• 搜索任意简称会自动转换为全称

━━━━━━━━━━━━━━━━━━━━━━
🌐 服务器状态
━━━━━━━━━━━━━━━━━━━━━━
/状态 - 查询 EVE 服务器状态和监控状态
  → 显示在线人数、服务器版本、启动时间
  → 显示本群监控状态

/状态开 - 开启本群服务器状态监控（只需开启一次，永久有效）
/状态关 - 关闭本群服务器状态监控
  → 每天11:00开始自动检测
  → 开服后当天停止检测，第二天11:00自动恢复
  → 维护/开服时自动通知
  → 使用AI生成通知消息"""

        yield event.plain_result(help_text)

    @filter.command("状态")
    async def server_status_command(self, event: AstrMessageEvent):
        """查询 EVE 服务器状态和本群监控状态"""
        try:
            # 获取服务器状态
            server_status = await self._get_server_status_with_monitor()
            
            # 获取当前群聊监控状态（仅在群聊中显示）
            group_id = event.message_obj.group_id if event.message_obj else ""
            if group_id:
                is_enabled = self._is_group_monitor_enabled(group_id)
                
                monitor_info = f"\n━━━━━━━━━━━━━━━━━━━━━━\n📊 本群监控状态\n━━━━━━━━━━━━━━━━━━━━━━\n"
                monitor_info += f"状态: {'✅ 开启' if is_enabled else '❌ 关闭'}\n"
                
                if is_enabled:
                    monitor_info += f"监控时间: 每天11:00开始，直到服务器开服\n"
                    monitor_info += f"检测间隔: 1分钟\n"
                    monitor_info += f"\n💡 维护或开服时会自动发送通知\n"
                    monitor_info += f"💡 开服后当天停止，第二天11:00自动恢复"
                else:
                    monitor_info += f"\n💡 使用 /状态开 开启监控（只需开启一次）"
                
                server_status += monitor_info
            # 私聊时只显示服务器状态，不显示监控信息
            
            yield event.plain_result(server_status)
        except Exception as e:
            logger.error(f"查询服务器状态失败: {e}")
            yield event.plain_result(f"查询服务器状态失败: {e}")

    @filter.command("状态开")
    async def monitor_enable_command(self, event: AstrMessageEvent):
        """开启本群服务器状态监控"""
        try:
            group_id = event.message_obj.group_id if event.message_obj else ""
            if not group_id:
                yield event.plain_result("请在群组中使用此命令")
                return
            
            # 获取 unified_msg_origin
            umo = event.unified_msg_origin
            
            # 设置当前群聊的监控状态（保存 group_id 和 umo）
            self._set_group_monitor_enabled(group_id, True, umo)
            
            # 启动监控任务（如果还没启动）
            self._start_monitor_task()
            
            yield event.plain_result(f"✅ 本群服务器状态监控已开启\n\n监控时间: 每天11:00开始\n检测间隔: 1分钟\n\n维护或开服时会自动发送通知")
        except Exception as e:
            logger.error(f"开启监控失败: {e}")
            yield event.plain_result(f"开启监控失败: {e}")

    @filter.command("状态关")
    async def monitor_disable_command(self, event: AstrMessageEvent):
        """关闭本群服务器状态监控"""
        try:
            group_id = event.message_obj.group_id if event.message_obj else ""
            if not group_id:
                yield event.plain_result("请在群组中使用此命令")
                return
            
            # 设置当前群聊的监控状态
            self._set_group_monitor_enabled(group_id, False)
            
            # 如果没有群聊启用监控了，停止监控任务
            enabled_groups = [gid for gid, config in self.monitor_config.items() if config.get('enabled', False)]
            if not enabled_groups:
                self._stop_monitor_task()
            
            yield event.plain_result("✅ 本群服务器状态监控已关闭")
        except Exception as e:
            logger.error(f"关闭监控失败: {e}")
            yield event.plain_result(f"关闭监控失败: {e}")

    async def _get_server_status_with_monitor(self):
        """获取服务器状态信息（供/状态命令使用，返回基础状态）"""
        return await self._get_server_status()

    async def _get_server_status(self):
        """获取服务器状态信息"""
        # EVE 国服 ESI 服务器状态接口
        status_url = "https://ali-esi.evepc.163.com/v1/status/"
        
        try:
            async with self.session.get(status_url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # 解析状态数据
                    players = data.get('players', 0)
                    server_version = data.get('server_version', '未知')
                    start_time = data.get('start_time', '')
                    
                    # 格式化启动时间（UTC 转北京时间 UTC+8）
                    start_time_str = '未知'
                    if start_time:
                        try:
                            # ISO 格式时间转换（使用局部导入确保兼容性）
                            from datetime import datetime, timezone, timedelta
                            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                            # 转换为北京时间（UTC+8）
                            beijing_tz = timezone(timedelta(hours=8))
                            dt_beijing = dt.astimezone(beijing_tz)
                            start_time_str = dt_beijing.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            start_time_str = start_time
                    
                    # 构建状态信息
                    status_text = f"""🌐 EVE 服务器状态

━━━━━━━━━━━━━━━━━━━━━━
📊 在线人数: {players:,} 人
🔧 服务器版本: {server_version}
⏰ 启动时间: {start_time_str}
✅ 服务器状态: 正常运行
━━━━━━━━━━━━━━━━━━━━━━"""
                    
                    return status_text
                elif response.status == 503:
                    return """🌐 EVE 服务器状态

━━━━━━━━━━━━━━━━━━━━━━
❌ 服务器状态: 维护中
━━━━━━━━━━━━━━━━━━━━━━

服务器正在进行维护，请稍后再试。"""
                else:
                    return f"""🌐 EVE 服务器状态

━━━━━━━━━━━━━━━━━━━━━━
⚠️ 无法获取服务器状态
HTTP 状态码: {response.status}
━━━━━━━━━━━━━━━━━━━━━━"""
        except aiohttp.ClientError as e:
            return f"""🌐 EVE 服务器状态

━━━━━━━━━━━━━━━━━━━━━━
❌ 连接失败
错误信息: {str(e)}
━━━━━━━━━━━━━━━━━━━━━━

无法连接到 EVE 服务器，请检查网络连接。"""

    # ==================== LLM 工具 ====================

    @filter.llm_tool(name="query_jita_price")
    async def query_jita_price_tool(self, event: AstrMessageEvent, item_name: str) -> str:
        '''查询 EVE 吉他市场价格。适用于用户询问某个物品的价格、市场行情、买卖价等场景。

        Args:
            item_name(string): 物品名称，如"三钛合金"、"乌鸦"、"PLEX"等
        '''
        try:
                # 检查简称（遍历所有全名，查找匹配的简称）
                query = item_name
                full_name_from_alias = None
                for full_name, aliases in self.aliases.items():
                    if query in aliases:
                        full_name_from_alias = full_name
                        break
                
                if full_name_from_alias:
                    query = full_name_from_alias

                # 如果是数字ID，直接查询
                if query.isdigit():
                    buy_text, sell_text = await self._get_item_price_info(query)
                    return f"【{item_name}】吉他市场价格：\n{buy_text}\n{sell_text}"

                # 否则搜索物品名称
                market_result = await self.search_item_by_name(query)
                if not market_result:
                    return f"未找到物品'{item_name}'"

                # 过滤涂装和蓝图
                filtered_result = [
                    item for item in market_result
                    if not self._is_skin(item.get('typename', ''))
                    and not self._is_blueprint(item.get('typename', ''))
                ]

                if not filtered_result:
                    return f"未找到'{item_name}'的有效结果（已过滤涂装和蓝图）"

                # 查询第一个结果的价格
                item = filtered_result[0]
                item_id = str(item.get('typeid', ''))
                item_name_found = item.get('typename', item_name)
                buy_text, sell_text = await self._get_item_price_info(item_id)

                return f"【{item_name_found}】吉他市场价格：\n{buy_text}\n{sell_text}"

        except Exception as e:
            logger.error(f"LLM工具查询价格失败: {e}")
            return f"查询'{item_name}'价格时出错：{str(e)}"

    @filter.llm_tool(name="query_ship_bonus")
    async def query_ship_bonus_tool(self, event: AstrMessageEvent, ship_name: str) -> str:
        '''查询 EVE 舰船的技能加成和特有加成。适用于用户询问某艘船的加成信息、舰船特性、技能加成等场景。

        Args:
            ship_name(string): 舰船名称，如"乌鸦级"、"狂热级"、"十字军"等
        '''
        try:
            # 检查简称（遍历所有全名，查找匹配的简称）
            query = ship_name
            full_name_from_alias = None
            for full_name, aliases in self.aliases.items():
                if query in aliases:
                    full_name_from_alias = full_name
                    break
            
            if full_name_from_alias:
                query = full_name_from_alias

            # 搜索舰船
            market_result = await self.search_item_by_name(query)
            if not market_result:
                return f"未找到舰船'{ship_name}'"

            # 过滤涂装和蓝图
            filtered_result = [
                item for item in market_result
                if not self._is_skin(item.get('typename', ''))
                and not self._is_blueprint(item.get('typename', ''))
            ]

            if not filtered_result:
                return f"未找到'{ship_name}'的有效结果"

            # 获取第一个结果的详细信息
            item = filtered_result[0]
            item_id = str(item.get('typeid', ''))
            item_name_found = item.get('typename', ship_name)

            # 获取物品信息
            item_info = await self.esi_request(f"/v3/universe/types/{item_id}/")
            if not item_info:
                return f"无法获取'{item_name_found}'的详细信息"

            # 提取属性和处理加成
            attr_dict = self._extract_attributes(item_info)
            dogma_effects = item_info.get('dogma_effects', [])
            skill_bonuses_dict, unique_bonuses = await self._process_bonuses(dogma_effects, attr_dict, self.session, item_name_found)

            # 构建结果
            result = self._build_result(item_info, skill_bonuses_dict, unique_bonuses, attr_dict, item_name_found)
            return result

        except Exception as e:
            logger.error(f"LLM工具查询加成失败: {e}")
            return f"查询'{ship_name}'加成时出错：{str(e)}"

    @filter.llm_tool(name="add_alias")
    async def add_alias_tool(self, event: AstrMessageEvent, full_name: str, alias: str) -> str:
        '''添加物品简称。适用于用户想要为某个物品设置快捷名称的场景。

        Args:
            full_name(string): 物品全称，如"鱼鹰级海军型"
            alias(string): 简称，如"海鱼鹰"
        '''
        try:
            if not full_name or not alias:
                return "全称和简称不能为空"

            if full_name not in self.aliases:
                self.aliases[full_name] = []

            if alias not in self.aliases[full_name]:
                self.aliases[full_name].append(alias)
                self._save_aliases()
                return f"已添加简称: {alias} -> {full_name}"
            else:
                return f"简称 {alias} 已存在"
        except Exception as e:
            logger.error(f"LLM工具添加简称失败: {e}")
            return f"添加简称时出错：{str(e)}"

    @filter.llm_tool(name="list_aliases")
    async def list_aliases_tool(self, event: AstrMessageEvent, query: str = "") -> str:
        '''查看简称列表。适用于用户想要查看所有简称或查询某个物品的简称。

        Args:
            query(string): 查询内容，可以是全称或简称。为空时显示所有简称
        '''
        try:
            if not self.aliases:
                return "暂无简称"

            if not query:
                # 显示所有简称
                result = "简称列表:\n"
                for full_name, aliases in self.aliases.items():
                    result += f"{full_name}: {', '.join(aliases)}\n"
                return result
            else:
                # 查询指定内容
                # 检查是否是简称
                for full_name, aliases in self.aliases.items():
                    if query in aliases:
                        return f"{full_name}: {', '.join(aliases)}"

                # 检查是否是全称
                if query in self.aliases:
                    return f"{query}: {', '.join(self.aliases[query])}"

                return f"{query} 还没有简称"
        except Exception as e:
            logger.error(f"LLM工具查看简称失败: {e}")
            return f"查看简称时出错：{str(e)}"

    @filter.llm_tool(name="delete_alias")
    async def delete_alias_tool(self, event: AstrMessageEvent, alias: str) -> str:
        '''删除简称。适用于用户想要删除某个已添加的简称。

        Args:
            alias(string): 要删除的简称
        '''
        try:
            found = False
            for full_name, aliases in list(self.aliases.items()):
                if alias in aliases:
                    aliases.remove(alias)
                    if not aliases:
                        del self.aliases[full_name]
                    found = True
                    break

            if found:
                self._save_aliases()
                return f"已删除简称: {alias}"
            else:
                return f"简称 {alias} 不存在"
        except Exception as e:
            logger.error(f"LLM工具删除简称失败: {e}")
            return f"删除简称时出错：{str(e)}"

    @filter.llm_tool(name="modify_bonus_description")
    async def modify_bonus_description_tool(self, event: AstrMessageEvent, old_description: str, effect_name: str, attr_names: str, new_description: str) -> str:
        '''修改加成描述（zidian1.txt）。适用于用户想要修改某个加成效果的描述文字。

        Args:
            old_description(string): 原描述文字，如"能量炮台最佳射程加成"
            effect_name(string): effect名称，如"shipETOptimalRange2AF"
            attr_names(string): 属性名，多个用/分隔，如"maxRange"
            new_description(string): 新描述文字，如"小型能量炮台最佳射程加成"
        '''
        try:
            zidian1_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zidian1.txt")
            old_attrs = [a.strip() for a in attr_names.split('/')]

            with open(zidian1_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            found = False
            modified_line = None

            for i, line in enumerate(lines):
                line = line.strip()
                if not line or ':' not in line:
                    continue

                desc_part, effect_attr_part = line.split(':', 1)
                effect_attr_part = effect_attr_part.strip()

                if '|' in effect_attr_part:
                    file_effect, file_attrs = effect_attr_part.split('|', 1)
                    file_effect = file_effect.strip()
                    file_attr_list = [a.strip() for a in file_attrs.split('/')]

                    if file_effect == effect_name:
                        if all(attr in file_attr_list for attr in old_attrs):
                            found = True
                            percent_match = re.match(r'(xx%\s*)', desc_part)
                            percent_prefix = percent_match.group(1) if percent_match else "xx% "
                            new_line = f"{percent_prefix}{new_description}: {file_effect}|{file_attrs}\n"
                            modified_line = i + 1
                            lines[i] = new_line
                            break

            if not found:
                return f"未找到匹配的映射: {old_description}({effect_name}|{attr_names})"

            with open(zidian1_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            return f"修改成功！\n第 {modified_line} 行:\n{old_description}({effect_name}|{attr_names})\n↓\n{new_description}"

        except Exception as e:
            logger.error(f"LLM工具修改加成描述失败: {e}")
            return f"修改加成描述时出错：{str(e)}"

    @filter.llm_tool(name="modify_skill_type_name")
    async def modify_skill_type_name_tool(self, event: AstrMessageEvent, old_skill_name: str, new_skill_name: str) -> str:
        '''修改技能类型名称（effect_dict.py）。适用于用户想要修改技能类型的名称。

        Args:
            old_skill_name(string): 原技能名，如"旗舰巡洋舰操作"
            new_skill_name(string): 新技能名，如"航空母舰操作"
        '''
        try:
            effect_dict_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "effect_dict.py")

            with open(effect_dict_path, 'r', encoding='utf-8') as f:
                content = f.read()

            pattern = f"'{old_skill_name}': ["
            replacement = f"'{new_skill_name}': ["

            if pattern not in content:
                return f"未找到技能名称: {old_skill_name}"

            new_content = content.replace(pattern, replacement)

            with open(effect_dict_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            try:
                import importlib
                global _effect_dict, identify_skill_type, is_role_bonus, should_hide_effect, get_effect_description
                _effect_dict = importlib.reload(_effect_dict)
                identify_skill_type = _effect_dict.identify_skill_type
                is_role_bonus = _effect_dict.is_role_bonus
                should_hide_effect = _effect_dict.should_hide_effect
                get_effect_description = _effect_dict.get_effect_description
            except Exception as e:
                logger.error(f"重新加载 effect_dict 失败: {e}")

            lines_before = content[:content.find(pattern)].count('\n') + 1

            return f"修改成功！\n第 {lines_before} 行:\n{old_skill_name}\n↓\n{new_skill_name}"

        except Exception as e:
            logger.error(f"LLM工具修改技能名称失败: {e}")
            return f"修改技能名称时出错：{str(e)}"

    @filter.llm_tool(name="add_effect_to_skill_type")
    async def add_effect_to_skill_type_tool(self, event: AstrMessageEvent, effect_name: str, modified_attrs: str, skill_type: str) -> str:
        '''添加effect到技能类型映射（effect_dict.py）。适用于用户想要将某个加成效果关联到特定技能类型。

        Args:
            effect_name(string): effect名称，如"shipBonusEwWeaponDisruptionStrengthAF2"
            modified_attrs(string): 修改的属性名，多个用/分隔，如"trackingSpeedBonus/falloffBonus"
            skill_type(string): 技能类型名，如"艾玛航空母舰操作"
        '''
        try:
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            effects_path = os.path.join(plugin_dir, "effects.json")
            attributes_path = os.path.join(plugin_dir, "attributes.json")

            with open(effects_path, 'r', encoding='utf-8') as f:
                effects_data = json.load(f)

            target_effect = None
            for effect in effects_data:
                if effect.get('name') == effect_name:
                    target_effect = effect
                    break

            if not target_effect:
                return f"未找到 effect: {effect_name}"

            modifiers = target_effect.get('modifiers', [])
            if not modifiers:
                return f"effect {effect_name} 没有 modifiers"

            modifying_attr_id = modifiers[0].get('modifying_attribute_id')
            if not modifying_attr_id:
                return "无法获取 modifying_attribute_id"

            with open(attributes_path, 'r', encoding='utf-8') as f:
                attributes_data = json.load(f)

            modifying_attr_name = None
            for attr in attributes_data:
                if attr.get('attribute_id') == modifying_attr_id:
                    modifying_attr_name = attr.get('name')
                    break

            if not modifying_attr_name:
                return f"未找到 attribute_id: {modifying_attr_id}"

            effect_dict_path = os.path.join(plugin_dir, "effect_dict.py")

            with open(effect_dict_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if 'SKILL_TYPE_RULES = {' not in content:
                return "未找到 SKILL_TYPE_RULES 字典"

            skill_pattern = f"'{skill_type}': ["

            if skill_pattern in content:
                start_idx = content.find(skill_pattern)
                list_start = start_idx + len(skill_pattern)

                bracket_count = 1
                list_end = list_start
                while bracket_count > 0 and list_end < len(content):
                    if content[list_end] == '[':
                        bracket_count += 1
                    elif content[list_end] == ']':
                        bracket_count -= 1
                    list_end += 1

                if bracket_count != 0:
                    return "解析 SKILL_TYPE_RULES 失败: 括号不匹配"

                list_content = content[list_start:list_end-1]

                if f"'{modifying_attr_name}'" in list_content:
                    return f"'{modifying_attr_name}' 已存在于 '{skill_type}' 中"

                last_quote_idx = list_content.rfind("'")
                if last_quote_idx == -1:
                    new_list_content = f"\n        '{modifying_attr_name}'\n    "
                else:
                    new_list_content = list_content.rstrip() + f",\n        '{modifying_attr_name}'\n    "

                new_content = content[:list_start] + new_list_content + content[list_end-1:]

                with open(effect_dict_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)

                action = "添加"
            else:
                dict_start = content.find('SKILL_TYPE_RULES = {')
                first_skill_pattern = "'突击护卫舰操作': ["
                first_skill_idx = content.find(first_skill_pattern, dict_start)

                if first_skill_idx == -1:
                    insert_pos = content.find('{', dict_start) + 1
                else:
                    insert_pos = first_skill_idx

                new_skill_entry = f"    '{skill_type}': [\n        '{modifying_attr_name}'\n    ],\n    "

                new_content = content[:insert_pos] + new_skill_entry + content[insert_pos:]

                with open(effect_dict_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)

                action = "创建"

            try:
                import importlib
                global _effect_dict, identify_skill_type, is_role_bonus, should_hide_effect, get_effect_description
                _effect_dict = importlib.reload(_effect_dict)
                identify_skill_type = _effect_dict.identify_skill_type
                is_role_bonus = _effect_dict.is_role_bonus
                should_hide_effect = _effect_dict.should_hide_effect
                get_effect_description = _effect_dict.get_effect_description
            except Exception as e:
                logger.error(f"重新加载 effect_dict 失败: {e}")

            return f"{action}成功！\n已将 '{modifying_attr_name}' {action}到 '{skill_type}'"

        except Exception as e:
            logger.error(f"LLM工具添加effect到技能类型失败: {e}")
            return f"添加effect到技能类型时出错：{str(e)}"

    @filter.llm_tool(name="query_server_status")
    async def query_server_status_tool(self, event: AstrMessageEvent, query: str = "") -> str:
        '''查询 EVE 服务器状态。适用于用户询问服务器是否在线、是否在维护、当前在线人数等场景。
        
        Args:
            query(string): 查询内容，如"服务器状态"、"在线人数"等，可为空
        
        可以回答以下类型的问题：
        - EVE服务器状态怎么样？
        - 服务器在线吗？
        - 服务器是否在维护？
        - 现在有多少人在线？
        - 服务器开了吗？
        '''
        try:
            status_info = await self._get_server_status()
            return status_info
        except Exception as e:
            logger.error(f"LLM工具查询服务器状态失败: {e}")
            return f"查询服务器状态时出错：{str(e)}"
