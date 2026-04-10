import json
import re

with open('attributes.json', 'r', encoding='utf-8') as f:
    attrs = json.load(f)

attr_id_to_name = {a['attribute_id']: a['name'] for a in attrs}

SHIP_BONUS_RULES = {
    'Amarr': {
        'Frigate': ['AF', '1AF', '2AF', '3AF'],
        'Destroyer': ['AD1', 'AD2'],
        'Cruiser': ['AC', 'AC2', 'AC3'],
        'Battlecruiser': ['ABC1', 'ABC2', 'ABC3'],
        'Battleship': ['AB', 'AB2', 'AB3'],
    },
    'Caldari': {
        'Frigate': ['CF', 'CF2', '3CF'],
        'Destroyer': ['CD1', 'CD2'],
        'Cruiser': ['CC', 'CC2', 'CC3'],
        'Battlecruiser': ['CBC1', 'CBC2', 'CBC3', 'CBC4'],
        'Battleship': ['CB', 'CB2', 'CB3'],
    },
    'Gallente': {
        'Frigate': ['GF', 'GF2', '3GF'],
        'Destroyer': ['GD1', 'GD2'],
        'Cruiser': ['GC', 'GC2', 'GC3'],
        'Battlecruiser': ['GBC1', 'GBC2', 'GBC3'],
        'Battleship': ['GB', 'GB2', 'GB3'],
    },
    'Minmatar': {
        'Frigate': ['MF', 'MF2', '3MF'],
        'Destroyer': ['MD1', 'MD2'],
        'Cruiser': ['MC', 'MC2'],
        'Battlecruiser': ['MBC1', 'MBC2', 'MBC3'],
        'Battleship': ['MB', 'MB2'],
    },
}

FACTION_MAP = {
    'A': 'Amarr',
    'C': 'Caldari',
    'G': 'Gallente',
    'M': 'Minmatar',
}

SHIP_TYPE_CN = {
    'Frigate': '护卫舰',
    'Destroyer': '驱逐舰',
    'Cruiser': '巡洋舰',
    'Battlecruiser': '战列巡洋舰',
    'Battleship': '战列舰',
    'NavyDestroyer': '海军驱逐舰',
    'TacticalDestroyer': '战术驱逐舰',
    'StrategicCruiser': '战略巡洋舰',
}

ELITE_BONUS_MAP = {
    'eliteBonusGunship': '突击护卫舰操作',
    'eliteBonusInterceptor': '截击舰操作',
    'eliteBonusCovertOps': '隐形特勤舰操作',
    'eliteBonusLogiFrig': '后勤护卫舰操作',
    'eliteBonusElectronicAttack': '电子攻击舰操作',
}

SKIP_ATTRIBUTES = ['entosisCPUPenalty', 'covertOpsWarpResistance']

def classify_bonus_by_name(modifying_attr_name):
    if not modifying_attr_name:
        return None

    if modifying_attr_name in SKIP_ATTRIBUTES:
        return 'SKIP'

    if modifying_attr_name in ELITE_BONUS_MAP:
        return ELITE_BONUS_MAP[modifying_attr_name]

    if modifying_attr_name.startswith('roleBonus'):
        return '特有加成'

    if 'NavyDestroyer' in modifying_attr_name:
        for fc, fn in FACTION_MAP.items():
            if fn in modifying_attr_name:
                return f'{fn}海军驱逐舰操作'
        return '特有加成'

    if 'TacticalDestroyer' in modifying_attr_name:
        for fc, fn in FACTION_MAP.items():
            if fn in modifying_attr_name:
                return f'{fn}战术驱逐舰操作'
        return '特有加成'

    if 'StrategicCruiser' in modifying_attr_name:
        for fc, fn in FACTION_MAP.items():
            if fn in modifying_attr_name:
                return f'{fn}战略巡洋舰操作'
        return '特有加成'

    if not modifying_attr_name.startswith('shipBonus'):
        return None

    suffix = modifying_attr_name[len('shipBonus'):]

    for faction, types in SHIP_BONUS_RULES.items():
        for ship_type, suffixes in types.items():
            if suffix in suffixes:
                return f'{faction}{SHIP_TYPE_CN[ship_type]}操作'

    return '特有加成'

def get_bonus_suffix(modifying_attr_name):
    if modifying_attr_name.startswith('shipBonus'):
        return modifying_attr_name[len('shipBonus'):]
    return modifying_attr_name

def get_display_text(modified_attr_name, operator, value):
    abs_value = abs(value)

    if operator == 0:
        if modified_attr_name in ['power', 'cpu', 'capacitor']:
            return f"{(1 - value) * 100}% 需求降低"
        return f"{value}"

    if operator == 1:
        return f"{value}"

    if operator == 2 or operator == 4 or operator == 6:
        if modified_attr_name in ['duration', 'capacitorNeed', 'cpu', 'power']:
            return f"{abs_value}% 消耗减少"
        if modified_attr_name == 'virusStrength':
            return f"{abs_value}＋ 病毒强度加成"
        if 'Resonance' in modified_attr_name or 'Resistance' in modified_attr_name:
            return f"{abs_value}% 抗性加成"
        return f"{abs_value}% 加成"

    return f"{abs_value}% 加成"

print("=== 分类测试 ===")
test_cases = [
    ('shipBonusAC', 'damageMultiplier', 6, 5.0),
    ('shipBonusAC2', 'armorDamageAmount', 6, 12.5),
    ('shipBonusABC1', 'capacitorNeed', 6, -10.0),
    ('shipBonusAF', 'capacitorNeed', 6, -10.0),
    ('eliteBonusGunship', 'maxRange', 6, 10.0),
    ('roleBonus', 'warpSpeedMultiplier', 6, 60.0),
    ('entosisCPUPenalty', 'entosisCPUAdd', 2, 10000.0),
]

for bonus, attr, op, val in test_cases:
    result = classify_bonus_by_name(bonus)
    display = get_display_text(attr, op, val)
    print(f"{bonus} -> {result} | {display}")