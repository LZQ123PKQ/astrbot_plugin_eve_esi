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

@register("eve_esi", "LZQ123PKQ", "EVE市场助手", "2.1.2")
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
    
    def _set_group_monitor_enabled(self, group_id, enabled):
        """设置指定群聊的监控状态"""
        if not group_id:
            return
        if group_id not in self.monitor_config:
            self.monitor_config[group_id] = {}
        self.monitor_config[group_id]['enabled'] = enabled
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
        3. 检测到服务器开服后，当天停止监控该群聊
        4. 第二天11:00自动重新开始监控
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
                        
                        # 状态变化检测（首次检测时也发送通知）
                        if last_status is None:
                            # 首次检测，记录状态但不发送通知（避免启动时误报）
                            self.group_server_status[group_id] = is_online
                            logger.info(f"群聊 {group_id} 首次检测，服务器状态: {'在线' if is_online else '维护中'}")
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
            # 使用 LLM 生成消息
            message = await self._generate_llm_message("服务器维护通知", "EVE服务器刚刚进入维护状态")
            await self._send_message_to_group(group_id, message)
        except Exception as e:
            logger.error(f"发送维护通知到群组 {group_id} 失败: {e}")
    
    async def _send_server_online_notification(self, group_id):
        """发送服务器开服通知到指定群聊"""
        try:
            # 使用 LLM 生成消息
            message = await self._generate_llm_message("服务器开服通知", "EVE服务器已经开服了")
            await self._send_message_to_group(group_id, message)
        except Exception as e:
            logger.error(f"发送开服通知到群组 {group_id} 失败: {e}")
    
    async def _generate_llm_message(self, context, default_message):
        """使用 LLM 生成消息"""
        try:
            # 构建提示词
            prompt = f"""你是一位 EVE Online 游戏助手，现在需要向玩家群发送一条消息。

场景：{context}
默认消息：{default_message}

请生成一条友好、有趣、符合 EVE 游戏氛围的消息。可以包含一些 EVE 相关的梗或幽默元素。
要求：
1. 消息简洁，不超过100字
2. 语气友好活泼
3. 可以适当使用 emoji

请直接输出消息内容，不要添加任何解释。"""
            
            # 调用 LLM 生成消息
            llm_response = await self.context.get_llm_response(prompt)
            if llm_response and llm_response.strip():
                return llm_response.strip()
        except Exception as e:
            logger.error(f"LLM 生成消息失败: {e}")
        
        # 如果 LLM 失败，返回默认消息
        return default_message
    
    async def _send_message_to_group(self, group_id, message):
        """发送消息到指定群聊"""
        try:
            # 使用 AstrBot 的消息发送接口
            await self.context.send_message(group_id, message)
        except Exception as e:
            logger.error(f"发送消息到群组 {group_id} 失败: {e}")

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
            'text': f"-{max_range_value}%武器扰断器最佳射程和失准范围惩罚",
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
            'text': f"-{cpu_value}%索敌扰断器启动消耗和CPU需求降低",
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
                part = f"{skill_type}每升一级:\n"
                for bonus_dict in bonuses:
                    bonus_text = bonus_dict['text']
                    effect_name = bonus_dict['effect_name']
                    # 新格式: effect_name|modified_attr|modifying_attr
                    modified_attr = bonus_dict['attr_name']
                    modifying_attr = bonus_dict.get('modifying_attr_name', '')
                    part += f"{bonus_text}({effect_name}|{modified_attr}|{modifying_attr})\n"
                # 如果有合并的装甲抗性加成，特殊格式输出
                if merged_armor_bonus:
                    bonus_text = merged_armor_bonus['text']
                    # 第一行显示数值和描述，以及第一个 effect_name|modified_attr|modifying_attr
                    first_bonus = merged_armor_bonus['bonuses'][0]
                    first_modified_attr = first_bonus['attr_name']
                    first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                    part += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
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
                    part += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
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
                    part += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
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
                    part += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                    # 后续行只显示 effect_name|modified_attr|modifying_attr，前面加空格对齐
                    for i in range(1, len(merged_target_painter_bonus['bonuses'])):
                        bonus_info = merged_target_painter_bonus['bonuses'][i]
                        modified_attr = bonus_info['attr_name']
                        modifying_attr = bonus_info.get('modifying_attr_name', '')
                        # 计算缩进：数值部分的长度 + 1
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
            
            for bonus_dict in unique_bonuses_list:
                bonus_text = bonus_dict['text']
                effect_name = bonus_dict['effect_name']
                # 新格式: effect_name|modified_attr|modifying_attr
                modified_attr = bonus_dict['attr_name']
                modifying_attr = bonus_dict.get('modifying_attr_name', '')
                result += f"{bonus_text}({effect_name}|{modified_attr}|{modifying_attr})\n"
            
            # 输出合并的装甲抗性加成
            if merged_armor_bonus:
                bonus_text = merged_armor_bonus['text']
                first_bonus = merged_armor_bonus['bonuses'][0]
                first_modified_attr = first_bonus['attr_name']
                first_modifying_attr = first_bonus.get('modifying_attr_name', '')
                result += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
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
                result += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
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
                result += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
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
                result += f"{bonus_text}({first_bonus['effect_name']}|{first_modified_attr}|{first_modifying_attr})\n"
                for i in range(1, len(merged_target_painter_bonus['bonuses'])):
                    bonus_info = merged_target_painter_bonus['bonuses'][i]
                    modified_attr = bonus_info['attr_name']
                    modifying_attr = bonus_info.get('modifying_attr_name', '')
                    indent = len(bonus_text) + 1
                    result += f"{' ' * indent}({bonus_info['effect_name']}|{modified_attr}|{modifying_attr})\n"
            
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
            
            # 设置当前群聊的监控状态
            self._set_group_monitor_enabled(group_id, True)
            
            # 启动监控任务（如果还没启动）
            self._start_monitor_task()
            
            yield event.plain_result(f"✅ 本群服务器状态监控已开启\n\n监控时间: 每天11:00-23:59\n检测间隔: 1分钟\n\n维护或开服时会自动发送通知")
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
