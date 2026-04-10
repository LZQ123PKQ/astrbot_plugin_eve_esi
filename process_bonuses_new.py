"""
新的加成处理方法，使用 effect_dict.py 中的字典
用于替换 main.py 中的 _process_bonuses 方法
"""

import re
from typing import Dict, List, Tuple, Optional
from effect_dict import (
    load_effect_descriptions, 
    identify_skill_type, 
    is_role_bonus,
    should_hide_effect,
    get_effect_description
)

async def _process_bonuses_new(self, dogma_effects, attr_dict, session=None, item_name=''):
    """
    处理技能加成和特有加成（新版，使用 effect_dict）
    
    Args:
        dogma_effects: 物品的 dogma effects 列表
        attr_dict: 属性字典 {attribute_id: value}
        session: aiohttp session（可选）
        item_name: 物品名称（用于特殊处理）
    
    Returns:
        (skill_bonuses_dict, unique_bonuses)
    """
    # 加载 effect 描述字典
    effect_descriptions = load_effect_descriptions()
    
    # 初始化技能加成容器
    skill_bonuses_dict: Dict[str, List[str]] = {}
    unique_bonuses: List[str] = []
    
    for effect in dogma_effects:
        effect_id = effect.get('effect_id')
        
        # 获取 effect 详细信息
        effect_info = await self.esi_request(f"/v1/dogma/effects/{effect_id}/")
        if not effect_info:
            continue
        
        effect_name = effect_info.get('name', '')
        
        # 检查是否应该隐藏
        if should_hide_effect(effect_name):
            continue
        
        # 获取加成值
        modifiers = effect_info.get('modifiers', [])
        if not modifiers:
            continue
        
        # 获取第一个 modifier 的加成值
        bonus_value = None
        for mod in modifiers:
            modifying_attr_id = mod.get('modifying_attribute_id')
            if modifying_attr_id and modifying_attr_id in attr_dict:
                bonus_value = attr_dict[modifying_attr_id]
                break
        
        if bonus_value is None:
            continue
        
        # 获取描述
        desc = get_effect_description(effect_name, bonus_value, effect_descriptions)
        
        if not desc:
            # 如果没有找到描述，使用通用格式
            desc = f"{self._format_bonus_value(abs(bonus_value))}% {effect_name}"
        
        # 识别技能类型
        skill_type = identify_skill_type(effect_name)
        
        if skill_type:
            # 技能加成
            if skill_type not in skill_bonuses_dict:
                skill_bonuses_dict[skill_type] = []
            
            # 去重
            if desc not in skill_bonuses_dict[skill_type]:
                skill_bonuses_dict[skill_type].append(desc)
        else:
            # 特有加成（roleBonus 或其他未分类）
            if desc not in unique_bonuses:
                unique_bonuses.append(desc)
    
    # 后处理：合并同类加成
    skill_bonuses_dict, unique_bonuses = _merge_similar_bonuses(
        skill_bonuses_dict, unique_bonuses, item_name
    )
    
    return skill_bonuses_dict, unique_bonuses


def _merge_similar_bonuses(
    skill_bonuses_dict: Dict[str, List[str]], 
    unique_bonuses: List[str],
    item_name: str
) -> Tuple[Dict[str, List[str]], List[str]]:
    """
    合并相似的加成
    
    例如：四种装甲抗性合并为"装甲抗性加成"
    """
    
    # 合并装甲抗性
    for skill_type, bonuses in skill_bonuses_dict.items():
        armor_resistances = [
            b for b in bonuses 
            if '装甲电磁伤害抗性' in b or '装甲热能伤害抗性' in b 
            or '装甲动能伤害抗性' in b or '装甲爆炸伤害抗性' in b
        ]
        
        if len(armor_resistances) == 4:
            # 提取数值
            match = re.search(r'(\d+\.?\d*)%', armor_resistances[0])
            if match:
                value = match.group(1)
                # 移除单独的抗性加成，添加合并后的
                skill_bonuses_dict[skill_type] = [
                    b for b in bonuses 
                    if b not in armor_resistances
                ]
                skill_bonuses_dict[skill_type].append(f"{value}% 装甲抗性加成")
    
    # 合并护盾抗性
    for skill_type, bonuses in skill_bonuses_dict.items():
        shield_resistances = [
            b for b in bonuses 
            if '护盾电磁伤害抗性' in b or '护盾热能伤害抗性' in b 
            or '护盾动能伤害抗性' in b or '护盾爆炸伤害抗性' in b
        ]
        
        if len(shield_resistances) == 4:
            match = re.search(r'(\d+\.?\d*)%', shield_resistances[0])
            if match:
                value = match.group(1)
                skill_bonuses_dict[skill_type] = [
                    b for b in bonuses 
                    if b not in shield_resistances
                ]
                skill_bonuses_dict[skill_type].append(f"{value}% 护盾抗性加成")
    
    # 合并导弹伤害
    for skill_type, bonuses in skill_bonuses_dict.items():
        missile_damages = [
            b for b in bonuses 
            if '电磁伤害加成' in b or '热能伤害加成' in b 
            or '动能伤害加成' in b or '爆炸伤害加成' in b
        ]
        
        if len(missile_damages) >= 4:
            match = re.search(r'(\d+\.?\d*)%', missile_damages[0])
            if match:
                value = match.group(1)
                skill_bonuses_dict[skill_type] = [
                    b for b in bonuses 
                    if b not in missile_damages
                ]
                skill_bonuses_dict[skill_type].append(f"{value}% 导弹伤害加成")
    
    return skill_bonuses_dict, unique_bonuses


# 使用说明：
# 1. 将 effect_dict.py 放在插件目录中
# 2. 在 main.py 中导入：
#    from .effect_dict import (
#        load_effect_descriptions, 
#        identify_skill_type, 
#        is_role_bonus,
#        should_hide_effect,
#        get_effect_description
#    )
# 3. 将 _process_bonuses 方法替换为上面的 _process_bonuses_new 方法
# 4. 删除旧的 bonus_handlers 字典和相关处理代码
