"""
EVE ESI 属性查询测试脚本（独立版本，无需 AstrBot）
用法: python test_111.py <物品名称或ID>
示例: python test_111.py 科洛斯级
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
        EFFECT_DESCRIPTIONS
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
        
        item_name_cn = item_info.get('name', item_name)
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
        print("\n" + "="*50)
        print(result)
        print("="*50)
    
    async def _process_bonuses(self, dogma_effects, attr_dict, item_name=''):
        """处理技能加成和特有加成"""
        skill_bonuses_dict = {}
        unique_bonuses = []
        
        # 收集装甲抗性加成
        armor_resistance_bonuses = {}
        shield_resistance_bonuses = {}
        missile_damage_bonuses = {}
        weapon_disruption_bonuses = {}
        
        is_crusader = 'Crusader' in item_name
        is_malediction = 'Malediction' in item_name
        is_executioner = 'Executioner' in item_name
        
        for effect in dogma_effects:
            effect_id = effect.get('effect_id')
            effect_info = await self.esi_request(f"/v1/dogma/effects/{effect_id}/")
            if effect_info:
                effect_name = effect_info.get('name', '')
                modifiers = effect_info.get('modifiers', [])
                
                # 解析加成信息
                bonus_texts = await self._process_modifiers(modifiers, attr_dict, effect_name)
                
                # 处理每个加成
                for bonus_item in bonus_texts:
                    if isinstance(bonus_item, dict):
                        bonus_text = bonus_item.get('text', '')
                        modifying_attr_name = bonus_item.get('modifying_attr_name', '')
                    else:
                        bonus_text = bonus_item
                        modifying_attr_name = ''
                    
                    # 使用 modifying_attribute 名称识别技能类型
                    skill_type = self._identify_skill_type(modifying_attr_name)
                    
                    if skill_type:
                        if skill_type not in skill_bonuses_dict:
                            skill_bonuses_dict[skill_type] = []
                        
                        if '装甲电磁伤害抗性' in bonus_text or '装甲热能伤害抗性' in bonus_text or '装甲动能伤害抗性' in bonus_text or '装甲爆炸伤害抗性' in bonus_text:
                            if skill_type not in armor_resistance_bonuses:
                                armor_resistance_bonuses[skill_type] = []
                            armor_resistance_bonuses[skill_type].append(bonus_text)
                        elif '护盾电磁伤害抗性' in bonus_text or '护盾热能伤害抗性' in bonus_text or '护盾动能伤害抗性' in bonus_text or '护盾爆炸伤害抗性' in bonus_text:
                            if skill_type not in shield_resistance_bonuses:
                                shield_resistance_bonuses[skill_type] = []
                            shield_resistance_bonuses[skill_type].append(bonus_text)
                        elif '电磁伤害' in bonus_text or '爆炸伤害' in bonus_text or '动能伤害' in bonus_text or '热能伤害' in bonus_text:
                            if skill_type not in missile_damage_bonuses:
                                missile_damage_bonuses[skill_type] = []
                            missile_damage_bonuses[skill_type].append(bonus_text)
                        else:
                            if bonus_text not in skill_bonuses_dict[skill_type]:
                                skill_bonuses_dict[skill_type].append(bonus_text)
                    else:
                        # 特有加成
                        if bonus_text not in unique_bonuses:
                            unique_bonuses.append(bonus_text)
        
        # 处理装甲抗性加成
        for skill_type, bonuses in armor_resistance_bonuses.items():
            if len(bonuses) == 4:
                bonus_value = None
                for bonus in bonuses:
                    match = re.search(r'(\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    armor_bonus = f"{bonus_value}% 装甲抗性加成"
                    if armor_bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(armor_bonus)
            else:
                for bonus in bonuses:
                    if bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(bonus)
        
        # 处理护盾抗性加成
        for skill_type, bonuses in shield_resistance_bonuses.items():
            if len(bonuses) == 4:
                bonus_value = None
                for bonus in bonuses:
                    match = re.search(r'(\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    shield_bonus = f"{bonus_value}% 护盾抗性加成"
                    if shield_bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(shield_bonus)
            else:
                for bonus in bonuses:
                    if bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(bonus)
        
        # 处理导弹伤害加成
        for skill_type, bonuses in missile_damage_bonuses.items():
            if len(bonuses) >= 4:
                bonus_value = None
                for bonus in bonuses:
                    match = re.search(r'(\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    missile_bonus = f"{bonus_value}% 导弹伤害加成"
                    if missile_bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(missile_bonus)
            else:
                for bonus in bonuses:
                    if bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(bonus)
        
        return skill_bonuses_dict, unique_bonuses
    
    async def _process_modifiers(self, modifiers, attr_dict, effect_name):
        """处理 modifiers，生成加成文本"""
        bonus_texts = []
        
        for mod in modifiers:
            bonus_value = None
            bonus_attribute = None
            modified_attr_id = mod.get('modified_attribute_id')
            modifying_attr_id = mod.get('modifying_attribute_id')
            
            # 获取加成值和 modifying_attribute 名称（用于技能类型识别）
            modifying_attr_name = ''
            if modifying_attr_id and modifying_attr_id in attr_dict:
                bonus_value = attr_dict[modifying_attr_id]
                # 获取 modifying_attribute 的名称（技能属性名）
                modifying_attr_info = await self.esi_request(f"/v1/dogma/attributes/{modifying_attr_id}/")
                if modifying_attr_info:
                    modifying_attr_name = modifying_attr_info.get('name', '')
            
            # 获取 modified_attribute 的名称（用于显示）
            attr_name = ''
            if modified_attr_id:
                attr_info = await self.esi_request(f"/v1/dogma/attributes/{modified_attr_id}/")
                if attr_info:
                    bonus_attribute = attr_info.get('display_name', attr_info.get('name', ''))
                    attr_name = attr_info.get('name', '')
            
            if bonus_value and bonus_attribute:
                bonus_text = await self._process_bonus(bonus_value, bonus_attribute, effect_name, modified_attr_id)
                if bonus_text:
                    bonus_texts.append({
                        'text': bonus_text,
                        'effect_name': effect_name,
                        'attr_name': attr_name,
                        'modifying_attr_name': modifying_attr_name  # 用于技能类型识别
                    })
            elif bonus_value and modified_attr_id:
                bonus_text = await self._process_bonus_without_display_name(bonus_value, modified_attr_id, bonus_attribute)
                if bonus_text:
                    bonus_texts.append({
                        'text': bonus_text,
                        'effect_name': effect_name,
                        'attr_name': attr_name,
                        'modifying_attr_name': modifying_attr_name  # 用于技能类型识别
                    })
        
        return bonus_texts
    
    async def _process_bonus(self, bonus_value, bonus_attribute, effect_name, modified_attr_id):
        """处理单个加成"""
        desc_from_dict = get_effect_description(effect_name, bonus_value, EFFECT_DESCRIPTIONS)
        if desc_from_dict:
            if '未知加成' not in desc_from_dict:
                return desc_from_dict
        
        return f"{self._format_bonus_value(abs(bonus_value))}% {bonus_attribute}加成"
    
    async def _process_bonus_without_display_name(self, bonus_value, modified_attr_id, bonus_attribute):
        """处理没有中文显示名称的加成"""
        attr_info = await self.esi_request(f"/v1/dogma/attributes/{modified_attr_id}/")
        if attr_info:
            attr_name = attr_info.get('name', '')
            return f"{self._format_bonus_value(abs(bonus_value))}% {attr_name}加成"
        return None
    
    def _identify_skill_type(self, effect_name):
        """识别技能类型"""
        return identify_skill_type(effect_name)
    
    def _build_result(self, item_info, skill_bonuses_dict, unique_bonuses, attr_dict, item_name_cn):
        """构建结果文本"""
        display_name = item_name_cn or item_info.get('name', '未知物品')
        
        result = f"{display_name}\n\n"
        
        # 计算最长数值长度
        all_bonuses = []
        for bonuses in skill_bonuses_dict.values():
            all_bonuses.extend(bonuses)
        all_bonuses.extend(unique_bonuses)
        
        max_value_length = 0
        for bonus in all_bonuses:
            percent_pos = bonus.find('%')
            if percent_pos != -1:
                value_length = percent_pos + 1
                if value_length > max_value_length:
                    max_value_length = value_length
        
        # 输出技能加成
        for skill_type, bonuses in skill_bonuses_dict.items():
            if bonuses:
                result += f"{skill_type}每升一级:\n"
                for bonus in bonuses:
                    percent_pos = bonus.find('%')
                    if percent_pos != -1:
                        value_length = percent_pos + 1
                        num_indent = ' ' * (max_value_length - value_length)
                        result += f"  {num_indent}{bonus[:percent_pos + 1]} {bonus[percent_pos + 1:].strip()}\n"
                    else:
                        total_indent = 2 + max_value_length + 1
                        result += f"{' ' * total_indent}{bonus}\n"
                result += "\n"
        
        # 输出特有加成
        if unique_bonuses:
            result += "特有加成\n"
            for bonus in unique_bonuses:
                percent_pos = bonus.find('%')
                if percent_pos != -1:
                    value_length = percent_pos + 1
                    num_indent = ' ' * (max_value_length - value_length)
                    result += f"  {num_indent}{bonus[:percent_pos + 1]} {bonus[percent_pos + 1:].strip()}\n"
                else:
                    total_indent = 2 + max_value_length + 1
                    result += f"{' ' * total_indent}{bonus}\n"
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
                        print("\n请使用物品ID进行精确查询: python test_111.py <ID>")
                    else:
                        print("未找到非涂装/蓝图物品")
                else:
                    print(f"未找到物品: {query}")
        finally:
            await self.shutdown()


async def main():
    if len(sys.argv) < 2:
        print("用法: python test_111.py <物品名称或ID>")
        print("示例: python test_111.py 科洛斯级")
        print("       python test_111.py 11182")
        sys.exit(1)
    
    query = sys.argv[1]
    tester = EVEESITester()
    await tester.run(query)


if __name__ == "__main__":
    asyncio.run(main())
