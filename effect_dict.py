"""
Effect 到描述的映射字典
根据 zidian1.txt 和 jianchuan.txt 生成
"""

# 从 zidian1.txt 解析的 effect 描述字典
# 格式: effect_name: (描述模板, modified_attr_names)
EFFECT_DESCRIPTIONS = {}

def load_effect_descriptions():
    """从 zidian1.txt 加载 effect 描述
    
    新格式: [描述:] effect_name|modified_attr|modifying_attr
    - 描述可选，如果没有则自动生成
    - effect_name: effect 名称
    - modified_attr: 被修改的属性名
    - modifying_attr: 用于修改的属性名（加成值来源）
    """
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
            # 格式: [描述:] effect_name|modified_attr|modifying_attr
            if ':' in line and '|' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    desc_template = parts[0].strip()  # 描述（可能为空）
                    effect_part = parts[1].strip()
                    
                    # 解析 effect_name|modified_attr|modifying_attr
                    effect_parts = effect_part.split('|')
                    if len(effect_parts) >= 3:
                        effect_name = effect_parts[0].strip()
                        modified_attr = effect_parts[1].strip()
                        modifying_attr = effect_parts[2].strip()
                        
                        # 使用 effect_name|modified_attr 作为 key
                        key = f"{effect_name}|{modified_attr}"
                        
                        descriptions[key] = {
                            'template': desc_template,  # 可能为空，表示需要自动生成
                            'effect_name': effect_name,
                            'modified_attr': modified_attr,
                            'modifying_attr': modifying_attr,
                            'category': current_category
                        }
    
    return descriptions

# 技能类型识别规则（基于 jianchuan.txt）
# 严格按照 jianchuan.txt 中的定义，一个字符一个数字都不改
SKILL_TYPE_RULES = {
    # 一、精英加成（eliteBonus）- 特殊舰船类型
        '艾玛防御子系统': [
        'subsystemBonusAmarrDefensive'
    ],
        '艾玛无畏舰操作': [
        'shipBonusDreadnoughtA1',
        'shipBonusDreadnoughtA2',
        'shipBonusDreadnoughtA3',
        'shipBonusDreadnoughtA4'
    ],
        '长枪无畏舰操作': [
        'shipBonusAdvancedDreadnought1',
        'shipBonusAdvancedDreadnought2'
    ],
        '艾玛航空母舰操作': [
        'shipBonusForceAuxiliaryA1',
        'shipBonusForceAuxiliaryA2',
        'shipBonusForceAuxiliaryA4'
    ],
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
    '掠夺舰操作': [
        'eliteBonusViolators1', 'eliteBonusViolators2', 'eliteBonusViolators3', 'eliteBonusViolators4', 'eliteBonusViolators5'
    ],
    '旗舰巡洋舰操作': [
        'eliteBonusFlagCruisers1'
    ],
    '暴乱者操作': [
        'eliteBonusMarauder'
    ],
    
    # 二、舰船基础加成（shipBonus）- 各阵营完整格式
    # 艾玛（Amarr）
    '艾玛护卫舰操作': [
        'shipBonusAF', 'shipBonus1AF', 'shipBonus2AF', 'shipBonus3AF'
    ],
    '艾玛驱逐舰操作': [
        'shipBonusAD1', 'shipBonusAD2', 'NavyDestroyerAmarr',
        'shipBonusNavyDestroyerAmarr1',
        'shipBonusNavyDestroyerAmarr2',
        'shipBonusNavyDestroyerAmarr3',
        'shipBonusNavyDestroyerAmarr4',
        'shipBonusNavyDestroyerAmarr5'
    ],
    '艾玛巡洋舰操作': [
        'shipBonusAC', 'shipBonusAC2', 'shipBonusAC3',
        'droneArmorDamageAmountBonus',
        'droneShieldBonusBonus'
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
        'shipBonusCD1', 'shipBonusCD2', 'NavyDestroyerCaldari',
        'shipBonusNavyDestroyerCaldari1',
        'shipBonusNavyDestroyerCaldari2',
        'shipBonusNavyDestroyerCaldari3',
        'shipBonusNavyDestroyerCaldari4',
        'shipBonusNavyDestroyerCaldari5'
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
        'shipBonusGD1', 'shipBonusGD2', 'NavyDestroyerGallente',
        'shipBonusNavyDestroyerGallente1',
        'shipBonusNavyDestroyerGallente2',
        'shipBonusNavyDestroyerGallente3',
        'shipBonusNavyDestroyerGallente4',
        'shipBonusNavyDestroyerGallente5'
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
        'shipBonusMD1', 'shipBonusMD2', 'NavyDestroyerMinmatar',
        'shipBonusNavyDestroyerMinmatar1',
        'shipBonusNavyDestroyerMinmatar2',
        'shipBonusNavyDestroyerMinmatar3',
        'shipBonusNavyDestroyerMinmatar4',
        'shipBonusNavyDestroyerMinmatar5'
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
        'TacticalDestroyerAmarr',
        'shipBonusTacticalDestroyerAmarr1',
        'shipBonusTacticalDestroyerAmarr2',
        'shipBonusTacticalDestroyerAmarr3'
    ],
    '加达里战术驱逐舰操作': [
        'TacticalDestroyerCaldari',
        'shipBonusTacticalDestroyerCaldari1',
        'shipBonusTacticalDestroyerCaldari2',
        'shipBonusTacticalDestroyerCaldari3'
    ],
    '盖伦特战术驱逐舰操作': [
        'TacticalDestroyerGallente',
        'shipBonusTacticalDestroyerGallente1',
        'shipBonusTacticalDestroyerGallente2',
        'shipBonusTacticalDestroyerGallente3'
    ],
    '米玛塔尔战术驱逐舰操作': [
        'TacticalDestroyerMinmatar',
        'shipBonusTacticalDestroyerMinmatar1',
        'shipBonusTacticalDestroyerMinmatar2',
        'shipBonusTacticalDestroyerMinmatar3'
    ],
    '艾玛战略巡洋舰操作': [
        'StrategicCruiserAmarr',
        'shipBonusStrategicCruiserAmarr1',
        'shipBonusStrategicCruiserAmarr2'
    ],
    '加达里战略巡洋舰操作': [
        'StrategicCruiserCaldari',
        'shipBonusStrategicCruiserCaldari1',
        'shipBonusStrategicCruiserCaldari2'
    ],
    '盖伦特战略巡洋舰操作': [
        'StrategicCruiserGallente',
        'shipBonusStrategicCruiserGallente1',
        'shipBonusStrategicCruiserGallente2'
    ],
    '米玛塔尔战略巡洋舰操作': [
        'StrategicCruiserMinmatar',
        'shipBonusStrategicCruiserMinmatar1',
        'shipBonusStrategicCruiserMinmatar2'
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

def get_effect_description(effect_name, modified_attr, bonus_value, operator=6, effect_descriptions=None):
    """
    获取 effect 的描述文本，根据 operator 格式化输出
    
    新格式: 描述: effect_name|modified_attr|modifying_attr
    
    Args:
        effect_name: effect 名称
        modified_attr: 被修改的属性名
        bonus_value: 加成数值
        operator: operator 类型（0, 2, 4, 6, 7 等），默认 6 (PostPercent)
        effect_descriptions: effect 描述字典（可选，默认从文件加载）
    
    Returns:
        描述文本或 None
    
    operator 规则：
    0: PreAssignment - 0.0035→1-0.0035=99.65% 放在描述前边
    2: PreDiv - 10.0→10+ 放在描述前边
    4: Add - 0.5→50% 放在描述前边
    6: PostPercent - 5.0→5% 放在描述前边
    7: PostMul - 15000→15秒 放在描述后边，并且描述前边加一个·
    0.0: 不写数值，并且描述前边加一个·
    """
    if effect_descriptions is None:
        effect_descriptions = load_effect_descriptions()
    
    # 使用 effect_name|modified_attr 作为 key
    key = f"{effect_name}|{modified_attr}"
    
    if key not in effect_descriptions:
        return None
    
    desc_info = effect_descriptions[key]
    description = desc_info['template']  # 描述文本（如"拦截失效装置最大锁定范围加成"）
    
    # 根据 operator 格式化数值
    value = abs(bonus_value)
    
    # 特殊处理：强化舱隔壁结构值加成
    # 将小数转换为百分比显示，例如 0.05 → 5.00%
    if '强化舱隔壁结构值加成' in description:
        percent = value * 100
        return f"{percent:.2f}% {description}"
    
    # 特殊处理：无人机护盾容量加成
    # 虽然 operator 是 2 (PreDiv)，但应该显示为百分比
    if '无人机护盾容量加成' in description:
        return f"{value:.2f}% {description}"
    
    if operator == 0:
        # PreAssignment: 0.0035→1-0.0035=99.65% 放在描述前边
        percent = (1 - value) * 100
        return f"{percent:.2f}% {description}"
    elif operator == 2:
        # PreDiv: 10.0→10+ 放在描述前边
        return f"{value:.2f}+ {description}"
    elif operator == 4:
        # Add: 0.5→50% 放在描述前边
        percent = value * 100
        return f"{percent:.2f}% {description}"
    elif operator == 7:
        # PostMul: 15000→15秒 放在描述后边，描述前边加·
        seconds = value / 1000
        return f"·{description} {seconds:.2f}秒"
    else:
        # 默认 PostPercent (6): 5.0→5% 放在描述前边
        return f"{value:.2f}% {description}"


def get_effect_description_count(effect_name, effect_descriptions=None):
    """
    获取 effect 的描述行数（用于支持同一 effect 多行描述）
    
    Args:
        effect_name: effect 名称
        effect_descriptions: effect 描述字典（可选，默认从文件加载）
    
    Returns:
        描述行数（int）
    """
    if effect_descriptions is None:
        effect_descriptions = load_effect_descriptions()
    
    if effect_name not in effect_descriptions:
        return 0
    
    desc_info = effect_descriptions[effect_name]
    template = desc_info['template']
    
    # 按 / 拆分多行描述
    desc_templates = template.split('/')
    return len(desc_templates)

# 不显示的 effect 列表
HIDDEN_EFFECTS = [
    'entosisCPUPenalty',
]

def should_hide_effect(effect_name):
    """判断是否应该隐藏该 effect"""
    for hidden in HIDDEN_EFFECTS:
        if hidden in effect_name:
            return True
    return False

# 初始化时加载描述
EFFECT_DESCRIPTIONS = load_effect_descriptions()
