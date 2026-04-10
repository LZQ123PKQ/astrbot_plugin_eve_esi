"""
Effect 到描述的映射字典
根据 zidian1.txt 和 jianchuan.txt 生成
"""

# 从 zidian1.txt 解析的 effect 描述字典
# 格式: effect_name: (描述模板, modified_attr_names)
EFFECT_DESCRIPTIONS = {}

def load_effect_descriptions():
    """从 zidian1.txt 加载 effect 描述"""
    import os
    
    zidian_path = os.path.join(os.path.dirname(__file__), 'zidian1.txt')
    if not os.path.exists(zidian_path):
        return {}
    
    descriptions = {}
    current_category = None
    
    with open(zidian_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # 分类标题
            if line.startswith('## '):
                current_category = line[3:].strip()
                continue
            
            # 解析描述行
            # 格式: xx% 描述: effect_name|modified_attr_name
            if ': ' in line and '|' in line:
                parts = line.split(': ', 1)
                if len(parts) == 2:
                    desc_template = parts[0].strip()  # xx% 描述
                    effect_part = parts[1].strip()
                    
                    if '|' in effect_part:
                        effect_parts = effect_part.rsplit('|', 1)
                        effect_name = effect_parts[0].strip()
                        modified_attrs = effect_parts[1].strip() if len(effect_parts) > 1 else ""
                        
                        descriptions[effect_name] = {
                            'template': desc_template,
                            'modified_attrs': modified_attrs,
                            'category': current_category
                        }
    
    return descriptions

# 技能类型识别规则（基于 jianchuan.txt）
# 严格按照 jianchuan.txt 中的定义，一个字符一个数字都不改
SKILL_TYPE_RULES = {
    # 一、精英加成（eliteBonus）- 特殊舰船类型
    '突击护卫舰操作': [
        'eliteBonusGunship1', 'eliteBonusGunship2', 'eliteBonusGunships', 'eliteBonusAssaultShips1'
    ],
    '截击舰操作': [
        'eliteBonusInterceptor', 'eliteBonusInterceptor2'
    ],
    '隐形特勤舰操作': [
        'eliteBonusCovertOps1', 'eliteBonusCovertOps2', 'eliteBonusCovertOps3', 'eliteBonusCovertOps4'
    ],
    '后勤护卫舰操作': [
        'eliteBonusLogiFrig1', 'eliteBonusLogiFrig2'
    ],
    '电子攻击舰操作': [
        'eliteBonusElectronicAttackShip1', 'eliteBonusElectronicAttackShip2', 'eliteBonusElectronicAttackShip3'
    ],
    '重型突击巡洋舰操作': [
        'eliteBonusHeavyGunship1', 'eliteBonusHeavyGunship2'
    ],
    '侦察舰操作': [
        'eliteBonusReconShip1', 'eliteBonusReconShip2', 'eliteBonusReconShip3'
    ],
    '隐形轰炸舰操作': [
        'eliteBonusBombers'
    ],
    '后勤舰操作': [
        'eliteBonusLogistics1', 'eliteBonusLogistics2', 'eliteBonusLogistics3', 'eliteBonusLogistics4'
    ],
    '指挥舰操作': [
        'eliteBonusCommandShips1', 'eliteBonusCommandShips2', 'eliteBonusCommandShips3', 'eliteBonusCommandShips4'
    ],
    '指挥驱逐舰操作': [
        'eliteBonusCommandDestroyer1', 'eliteBonusCommandDestroyer2', 'eliteBonusCommandDestroyer3'
    ],
    '重型拦截舰操作': [
        'eliteBonusHeavyInterdictors1', 'eliteBonusHeavyInterdictors2', 'eliteBonusHeavyInterdictors3'
    ],
    '拦截舰操作': [
        'eliteBonusInterdictors1', 'eliteBonusInterdictors2'
    ],
    '探险舰操作': [
        'eliteBonusExpedition1', 'eliteBonusExpedition2'
    ],
    '护卫型工业舰操作': [
        'eliteBonusEscorts'
    ],
    '工业舰操作': [
        'eliteBonusIndustrial1', 'eliteBonusIndustrial2'
    ],
    '战列巡洋舰操作': [
        'eliteBonusBattlecruiser'
    ],
    '驱逐舰操作': [
        'eliteBonusdestroyers'
    ],
    '黑隐特勤舰操作': [
        'eliteBonusBlackOps1', 'eliteBonusBlackOps2', 'eliteBonusBlackOps3', 'eliteBonusBlackOps4'
    ],
    '跳跃货舰操作': [
        'eliteBonusJumpFreighter1', 'eliteBonusJumpFreighter2'
    ],
    '采矿驳船操作': [
        'eliteBonusBarge1', 'eliteBonusBarge2'
    ],
    '暴乱者操作': [
        'eliteBonusViolators1', 'eliteBonusViolators2', 'eliteBonusViolators3', 'eliteBonusViolators4', 'eliteBonusViolators5',
        'eliteBonusViolatorsRole1', 'eliteBonusViolatorsRole2', 'eliteBonusViolatorsRole3'
    ],
    '旗舰巡洋舰操作': [
        'eliteBonusFlagCruisers1'
    ],
    '掠夺舰操作': [
        'eliteBonusMarauder'
    ],
    
    # 二、舰船基础加成（shipBonus）- 各阵营完整格式
    # 艾玛（Amarr）
    '艾玛护卫舰操作': [
        'shipBonusAF', 'shipBonus1AF', 'shipBonus2AF', 'shipBonus3AF'
    ],
    '艾玛驱逐舰操作': [
        'shipBonusAD1', 'shipBonusAD2', 'NavyDestroyerAmarr'
    ],
    '艾玛巡洋舰操作': [
        'shipBonusAC', 'shipBonusAC2', 'shipBonusAC3'
    ],
    '艾玛战列巡洋舰操作': [
        'shipBonusABC1', 'shipBonusABC2', 'shipBonusABC3'
    ],
    '艾玛战列舰操作': [
        'shipBonusAB', 'shipBonusAB2', 'shipBonusAB3'
    ],
    
    # 加达里（Caldari）
    '加达里护卫舰操作': [
        'shipBonusCF', 'shipBonusCF2', 'shipBonus3CF'
    ],
    '加达里驱逐舰操作': [
        'shipBonusCD1', 'shipBonusCD2', 'NavyDestroyerCaldari'
    ],
    '加达里巡洋舰操作': [
        'shipBonusCC', 'shipBonusCC2', 'shipBonusCC3'
    ],
    '加达里战列巡洋舰操作': [
        'shipBonusCBC1', 'shipBonusCBC2', 'shipBonusCBC3', 'shipBonusCBC4'
    ],
    '加达里战列舰操作': [
        'shipBonusCB', 'shipBonusCB3', 'shipBonusCB2'
    ],
    
    # 盖伦特（Gallente）
    '盖伦特护卫舰操作': [
        'shipBonusGF', 'shipBonusGF2', 'shipBonus3GF'
    ],
    '盖伦特驱逐舰操作': [
        'shipBonusGD1', 'shipBonusGD2', 'NavyDestroyerGallente'
    ],
    '盖伦特巡洋舰操作': [
        'shipBonusGC', 'shipBonusGC2', 'shipBonusGC3'
    ],
    '盖伦特战列巡洋舰操作': [
        'shipBonusGBC1', 'shipBonusGBC2', 'shipBonusGBC3'
    ],
    '盖伦特战列舰操作': [
        'shipBonusGB', 'shipBonusGB2', 'shipBonusGB3'
    ],
    
    # 米玛塔尔（Minmatar）
    '米玛塔尔护卫舰操作': [
        'shipBonusMF', 'shipBonusMF2', 'shipBonus3MF'
    ],
    '米玛塔尔驱逐舰操作': [
        'shipBonusMD1', 'shipBonusMD2', 'NavyDestroyerMinmatar'
    ],
    '米玛塔尔巡洋舰操作': [
        'shipBonusMC', 'shipBonusMC2'
    ],
    '米玛塔尔战列巡洋舰操作': [
        'shipBonusMBC1', 'shipBonusMBC2', 'shipBonusMBC3'
    ],
    '米玛塔尔战列舰操作': [
        'shipBonusMB', 'shipBonusMB2'
    ],
    
    # 三、战术/战略舰船加成
    '艾玛战术驱逐舰操作': [
        'TacticalDestroyerAmarr'
    ],
    '加达里战术驱逐舰操作': [
        'TacticalDestroyerCaldari'
    ],
    '盖伦特战术驱逐舰操作': [
        'TacticalDestroyerGallente'
    ],
    '米玛塔尔战术驱逐舰操作': [
        'TacticalDestroyerMinmatar'
    ],
    '艾玛战略巡洋舰操作': [
        'StrategicCruiserAmarr'
    ],
    '加达里战略巡洋舰操作': [
        'StrategicCruiserCaldari'
    ],
    '盖伦特战略巡洋舰操作': [
        'StrategicCruiserGallente'
    ],
    '米玛塔尔战略巡洋舰操作': [
        'StrategicCruiserMinmatar'
    ],
}

# 角色加成（特有加成）的精确匹配规则
# 严格按照 jianchuan.txt: roleBonus / roleBonusOverheatDST / roleBonusCBC 等 → 特有加成
ROLE_BONUS_PATTERNS = [
    'roleBonus',
    'roleBonusOverheatDST',
    'roleBonusCBC',
]

def identify_skill_type(modifying_attr_name):
    """
    根据 modifying_attribute 名称识别技能类型
    严格按照 jianchuan.txt 中的定义进行精确匹配
    返回技能类型名称或 None
    """
    for skill_type, patterns in SKILL_TYPE_RULES.items():
        for pattern in patterns:
            # 精确匹配，不是前缀匹配
            if modifying_attr_name == pattern:
                return skill_type
    return None

def is_role_bonus(effect_name):
    """
    判断是否是角色加成（特有加成）
    严格按照 jianchuan.txt 中的定义：
    - roleBonus
    - roleBonusOverheatDST
    - roleBonusCBC
    等 → 特有加成
    """
    # 首先检查是否是技能加成（eliteBonus 或 shipBonus）
    # 这些不是角色加成
    if modifying_attr_is_skill_bonus(effect_name):
        return False
    
    # 检查是否匹配角色加成模式
    for pattern in ROLE_BONUS_PATTERNS:
        if effect_name == pattern:
            return True
        # 对于 roleBonus，检查是否是 roleBonus 开头但不是技能加成
        if pattern == 'roleBonus' and effect_name.startswith('roleBonus'):
            return True
    
    return False

def modifying_attr_is_skill_bonus(modifying_attr_name):
    """
    判断 modifying_attribute 是否是技能加成
    用于区分技能加成和角色加成
    """
    # 检查是否是已知的技能加成模式
    for skill_type, patterns in SKILL_TYPE_RULES.items():
        for pattern in patterns:
            if modifying_attr_name == pattern:
                return True
    return False

def get_effect_description(effect_name, bonus_value, effect_descriptions=None):
    """
    获取 effect 的描述文本
    
    Args:
        effect_name: effect 名称
        bonus_value: 加成数值
        effect_descriptions: effect 描述字典（可选，默认从文件加载）
    
    Returns:
        描述文本或 None
    """
    if effect_descriptions is None:
        effect_descriptions = load_effect_descriptions()
    
    if effect_name not in effect_descriptions:
        return None
    
    desc_info = effect_descriptions[effect_name]
    template = desc_info['template']
    
    # 替换 xx% 为实际数值
    if 'xx%' in template:
        # 格式化数值
        if isinstance(bonus_value, (int, float)):
            formatted_value = f"{abs(bonus_value):.2f}%"
        else:
            formatted_value = str(bonus_value)
        return template.replace('xx%', formatted_value)
    elif 'xx＋' in template:
        if isinstance(bonus_value, (int, float)):
            formatted_value = f"{abs(bonus_value):.0f}＋"
        else:
            formatted_value = str(bonus_value)
        return template.replace('xx＋', formatted_value)
    else:
        return template

# 不显示的 effect 列表
HIDDEN_EFFECTS = [
    'entosisCPUPenalty',
    'covertOpsWarpResistance',
]

def should_hide_effect(effect_name):
    """判断是否应该隐藏该 effect"""
    for hidden in HIDDEN_EFFECTS:
        if hidden in effect_name:
            return True
    return False

# 初始化时加载描述
EFFECT_DESCRIPTIONS = load_effect_descriptions()
