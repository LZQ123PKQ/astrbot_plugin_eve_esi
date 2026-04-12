"""
EVE ESI 属性查询测试脚本 V2（完整输出格式）
用法: python test_111_v2.py <物品名称或ID>
示例: python test_111_v2.py 科洛斯级
"""

import aiohttp
import json
import os
import sys
import asyncio
import re

# 导入 effect 字典
try:
    from effect_dict import (
        load_effect_descriptions,
        identify_skill_type,
        is_role_bonus,
        should_hide_effect,
        get_effect_description,
        get_effect_description_count,
        EFFECT_DESCRIPTIONS,
        SKILL_TYPE_RULES
    )
except ImportError:
    print("错误: 无法导入 effect_dict.py，请确保文件在同一目录")
    sys.exit(1)


class EVEESITester:
    def __init__(self):
        self.base_url = "https://ali-esi.evepc.163.com"
        self.session = None
        
    async def initialize(self):
        """初始化 aiohttp session"""
        self.session = aiohttp.ClientSession()
        
    async def shutdown(self):
        """关闭 session"""
        if self.session:
            await self.session.close()
            
    async def esi_request(self, endpoint, method="GET", data=None):
        """发送 ESI 请求"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == "GET":
                async with self.session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"ESI请求失败: {response.status} - {url}")
                        return None
            elif method == "POST":
                async with self.session.post(url, json=data) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"ESI请求失败: {response.status} - {url}")
                        return None
        except Exception as e:
            print(f"ESI请求异常: {e}")
            return None
    
    async def search_item_by_name(self, name):
        """使用市场中心API搜索物品"""
        try:
            url = "https://www.ceve-market.org/api/searchname"
            data = {"name": name}
            
            async with self.session.post(url, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    print(f"市场中心搜索结果: {len(result)}个物品")
                    return result
                else:
                    print(f"市场中心搜索失败: {response.status}，尝试使用ESI搜索API")
                    return await self._search_item_by_name_esi(name)
        except Exception as e:
            print(f"市场中心搜索异常: {e}，尝试使用ESI搜索API")
            return await self._search_item_by_name_esi(name)
    
    async def _search_item_by_name_esi(self, name):
        """使用ESI搜索API搜索物品"""
        try:
            url = f"{self.base_url}/latest/universe/ids/"
            
            async with self.session.post(url, json=[name]) as response:
                if response.status == 200:
                    result = await response.json()
                    inventory_types = result.get('inventory_types', [])
                    items = []
                    for item in inventory_types:
                        items.append({
                            'typeid': item.get('id'),
                            'typename': item.get('name')
                        })
                    print(f"ESI搜索API结果: {len(items)}个物品")
                    return items
                else:
                    print(f"ESI搜索API失败: {response.status}")
                    return []
        except Exception as e:
            print(f"ESI搜索API异常: {e}")
            return []
    
    def _is_skin(self, item_name):
        """判断物品是否为涂装"""
        skin_keywords = ['涂装', 'Skin', 'SKIN', 'skin']
        return any(keyword in item_name for keyword in skin_keywords)
    
    def _is_blueprint(self, item_name):
        """判断物品是否为蓝图"""
        blueprint_keywords = ['蓝图', 'Blueprint', 'BLUEPRINT', 'blueprint']
        return any(keyword in item_name for keyword in blueprint_keywords)
    
    def _format_bonus_value(self, value):
        """格式化加成数值"""
        if isinstance(value, (int, float)):
            if value == int(value):
                return f"{int(value)}"
            else:
                return f"{value:.2f}"
        return str(value)
    
    async def get_item_info(self, item_id, item_name=''):
        """获取物品信息"""
        print(f"\n正在查询物品ID: {item_id}")
        
        # 获取物品类型信息
        item_info = await self.esi_request(f"/v3/universe/types/{item_id}/")
        if not item_info:
            print(f"未找到物品信息: {item_id}")
            return
        
        # 优先使用传入的中文名字，如果没有则使用ESI返回的名字
        item_name_cn = item_name if item_name else item_info.get('name', '未知物品')
        print(f"物品名称: {item_name_cn}")
        
        # 获取 dogma 信息
        dogma_attributes = item_info.get('dogma_attributes', [])
        dogma_effects = item_info.get('dogma_effects', [])
        
        if not dogma_effects:
            print("该物品没有技能加成信息")
            return
        
        # 构建属性字典
        attr_dict = {}
        for attr in dogma_attributes:
            attr_id = attr.get('attribute_id')
            value = attr.get('value')
            if attr_id and value is not None:
                attr_dict[attr_id] = value
        
        # 处理加成
        skill_bonuses_dict, unique_bonuses = await self._process_bonuses(
            dogma_effects, attr_dict, item_name_cn
        )
        
        # 构建结果
        result = self._build_result(item_info, skill_bonuses_dict, unique_bonuses, attr_dict, item_name_cn)
        print("\n" + "="*80)
        print(result)
        print("="*80)
    
    async def _process_bonuses(self, dogma_effects, attr_dict, item_name=''):
        """处理技能加成和特有加成（同步自 main.py）
        
        每个 modifier 单独显示一行
        """
        # 使用字典按 effect_name 去重，每个 effect 只保留一条记录
        skill_bonuses_dict = {}  # {skill_type: {effect_key: bonus_dict, ...}, ...}
        unique_bonuses_dict = {}  # {effect_key: bonus_dict, ...}

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
        """识别技能类型"""
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
    
    def _build_result(self, item_info, skill_bonuses_dict, unique_bonuses_list, attr_dict, item_name_cn):
        """构建结果文本
        
        新格式: effect_name|modified_attr|modifying_attr
        """
        display_name = item_name_cn or item_info.get('name', '未知物品')
        
        result = f"{display_name}\n\n"
        
        # 从 effect_dict 获取技能类型顺序
        skill_order = list(SKILL_TYPE_RULES.keys())
        
        # 按顺序输出技能加成
        result_parts = []
        for skill_type in skill_order:
            if skill_type in skill_bonuses_dict and skill_bonuses_dict[skill_type]:
                bonuses = skill_bonuses_dict[skill_type]
                # 处理装甲抗性加成合并
                bonuses, merged_armor_bonus = self._merge_armor_resistance_bonuses(bonuses)
                # 处理武器扰断器效果加成合并
                bonuses, merged_weapon_disruption_bonus = self._merge_weapon_disruption_bonuses(bonuses)
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
                part += "\n"
                result_parts.append(part)
        
        result += "".join(result_parts)
        
        # 输出特有加成
        if unique_bonuses_list:
            result += "特有加成\n"
            for bonus_dict in unique_bonuses_list:
                bonus_text = bonus_dict['text']
                effect_name = bonus_dict['effect_name']
                # 新格式: effect_name|modified_attr|modifying_attr
                modified_attr = bonus_dict['attr_name']
                modifying_attr = bonus_dict.get('modifying_attr_name', '')
                result += f"{bonus_text}({effect_name}|{modified_attr}|{modifying_attr})\n"
            result += "\n"
        
        return result
    
    async def run(self, query):
        """运行查询"""
        await self.initialize()
        
        try:
            # 判断是否为数字ID
            if query.isdigit():
                await self.get_item_info(query)
            else:
                # 使用市场中心API搜索
                print(f"搜索物品: {query}")
                market_result = await self.search_item_by_name(query)
                
                if market_result and len(market_result) > 0:
                    # 过滤涂装和蓝图
                    filtered_result = [
                        item for item in market_result 
                        if not self._is_skin(item.get('typename', '')) 
                        and not self._is_blueprint(item.get('typename', ''))
                    ]
                    
                    if len(filtered_result) == 1:
                        item = filtered_result[0]
                        item_id = str(item.get('typeid', ''))
                        item_name = item.get('typename', '')
                        await self.get_item_info(item_id, item_name)
                    elif len(filtered_result) > 1:
                        print(f"\n找到 {len(filtered_result)} 个结果:")
                        for i, item in enumerate(filtered_result[:10], 1):
                            print(f"{i}. {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})")
                        print("\n请使用物品ID进行精确查询: python test_111_v2.py <ID>")
                    else:
                        print("未找到非涂装/蓝图物品")
                else:
                    print(f"未找到物品: {query}")
        finally:
            await self.shutdown()


async def main():
    if len(sys.argv) < 2:
        print("用法: python test_111_v2.py <物品名称或ID>")
        print("示例: python test_111_v2.py 科洛斯级")
        print("       python test_111_v2.py 11182")
        sys.exit(1)
    
    query = sys.argv[1]
    tester = EVEESITester()
    await tester.run(query)


if __name__ == "__main__":
    asyncio.run(main())
