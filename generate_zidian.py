#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 effects.json 和 attributes.json 生成新的 zidian1.txt
格式: 描述: effect_name|modified_attr|modifying_attr
"""

import json
import os
import re

# 加载数据
effects_path = os.path.join(os.path.dirname(__file__), 'effects.json')
attributes_path = os.path.join(os.path.dirname(__file__), 'attributes.json')

with open(effects_path, 'r', encoding='utf-8') as f:
    effects_data = json.load(f)

with open(attributes_path, 'r', encoding='utf-8') as f:
    attributes_data = json.load(f)

# 构建 attribute_id -> 信息 映射
attr_info = {}
# 同时构建 attribute_name -> display_name 映射
attr_display_names = {}
for attr in attributes_data:
    attr_id = attr.get('attribute_id')
    attr_name = attr.get('name', '')
    display_name = attr.get('display_name', '')
    
    if attr_id:
        attr_info[attr_id] = {
            'name': attr_name,
            'display_name': display_name,
            'description': attr.get('description', '')
        }
    
    if attr_name:
        attr_display_names[attr_name] = display_name

# effect_name 前缀映射表
effect_prefix_map = {
    'interceptorNullification': '拦截失效装置',
    'roleBonus': '角色',
    'shipBonus': '舰船',
    'eliteBonus': '精英',
    'mediumWeapon': '中型武器',
    'largeWeapon': '大型武器',
    'smallWeapon': '小型武器',
    'capitalWeapon': '旗舰武器',
    'drone': '无人机',
    'shield': '护盾',
    'armor': '装甲',
    'hull': '结构',
    'ewar': '电子战',
    'ecm': 'ECM',
    'sensor': '感应',
    'targeting': '锁定',
    'navigation': '导航',
    'propulsion': '推进',
    'mining': '采矿',
    'industry': '工业',
    'command': '指挥',
    'logistics': '后勤',
    'recon': '侦察',
    'covert': '隐形',
    'bomb': '炸弹',
    'missile': '导弹',
    'turret': '炮台',
    'launcher': '发射器',
    'hybrid': '混合',
    'energy': '能量',
    'projectile': '射弹',
    'precursor': '先驱',
    'disintegrator': '分解',
    'vorton': '涡流',
    'upwell': '昇威',
    'citadel': '堡垒',
    'structure': '建筑',
    'fighter': '战机',
    'fighterBomber': '轰炸战机',
    'support': '支援',
    'burst': '脉冲',
    'link': '链接',
    'gang': '团队',
    'fleet': '舰队',
    'wing': '联队',
    'squad': '小队',
    'booster': '增效',
    'implant': '植入体',
    'rig': '改装件',
    'subsystem': '子系统',
    'module': '装备',
    'charge': '弹药',
    'droneControl': '无人机控制',
    'bandwidth': '带宽',
    'bay': '舱',
    'cargo': '货舱',
    'hangar': '机库',
    'fuel': '燃料',
    'capacitor': '电容',
    'power': '能量栅格',
    'cpu': 'CPU',
    'speed': '速度',
    'agility': '敏捷',
    'mass': '质量',
    'volume': '体积',
    'radius': '半径',
    'signature': '信号半径',
    'scan': '扫描',
    'probe': '探针',
    'warp': '跃迁',
    'jump': '跳跃',
    'micro': '微型',
    'afterburner': '加力',
    'microwarpdrive': '微曲',
    'mwde': '微曲',
    'mwd': '微曲',
    'ab': '加力',
    'damage': '伤害',
    'range': '射程',
    'optimal': '最佳',
    'falloff': '失准',
    'tracking': '跟踪',
    'accuracy': '精度',
    'rof': '射速',
    'rate': '速率',
    'cycle': '周期',
    'duration': '持续时间',
    'activation': '激活',
    'capacitorNeed': '电容需求',
    'capNeed': '电容需求',
    'powerNeed': '能量栅格需求',
    'cpuNeed': 'CPU需求',
    'fitting': '装配',
    'slot': '槽位',
    'hardpoint': ' hardpoint',
    'turretHardpoint': '炮台 hardpoint',
    'launcherHardpoint': '发射器 hardpoint',
    'highSlot': '高槽',
    'mediumSlot': '中槽',
    'lowSlot': '低槽',
    'rigSlot': '改装件槽',
    'subsystemSlot': '子系统槽',
    'serviceSlot': '服务槽',
    'cargoHold': '货舱',
    'oreHold': '矿石舱',
    'gasHold': '气体舱',
    'mineralHold': '矿物舱',
    'salvageHold': '打捞舱',
    'shipHold': '舰船舱',
    'itemHold': '物品舱',
    'quafeHold': '酷菲舱',
    'commandCenterHold': '指挥中心舱',
    'planetaryCommoditiesHold': '行星商品舱',
    'materialBay': '材料舱',
    'mobileDepotHold': '移动仓库舱',
    'colonyHold': '殖民地舱',
    'boosterHold': '增效剂舱',
    'subsystemHold': '子系统舱',
    'frigateEscapeBay': '护卫舰逃生舱',
    'fighterBay': '战机舱',
    'fighterTube': '战机发射管',
    'droneBay': '无人机舱',
    'capacitorBooster': '电容注电器',
    'shieldBooster': '护盾回充器',
    'armorRepairer': '装甲维修器',
    'hullRepairer': '结构维修器',
    'remoteShield': '远程护盾',
    'remoteArmor': '远程装甲',
    'remoteCapacitor': '远程电容',
    'remoteHull': '远程结构',
    'energyTransfer': '能量传输',
    'energyVampire': '能量吸取',
    'energyNeutralizer': '能量中和器',
    'smartBomb': '智能炸弹',
    'bombLauncher': '炸弹发射器',
    'torpedo': '鱼雷',
    'cruiseMissile': '巡航导弹',
    'heavyMissile': '重型导弹',
    'lightMissile': '轻型导弹',
    'rocket': '火箭',
    'defender': '防御导弹',
    'fof': 'FOF',
    'autoTargeting': '自动锁定',
    'webifier': '网子',
    'warpScrambler': '扰频',
    'warpDisruptor': '扰断',
    'ecm': 'ECM',
    'eccm': 'ECCM',
    'sensorDampener': '感应抑阻',
    'trackingDisruptor': '跟踪扰断',
    'targetPainter': '目标标记',
    'guidanceDisruptor': '制导干扰',
    'energyWarfare': '能量战',
    'electronicWarfare': '电子战',
    'targetingWarfare': '锁定战',
    'navigationWarfare': '导航战',
    'skirmishWarfare': '游击战',
    'siegeWarfare': '攻城战',
    'informationWarfare': '信息战',
    'miningForeman': '采矿 foreman',
    'miningLaser': '采矿激光',
    'stripMiner': '露天采矿',
    'iceHarvester': '冰矿采集',
    'gasHarvester': '气体采集',
    'salvager': '打捞',
    'tractorBeam': '牵引光束',
    'cloaking': '隐形',
    'cynosural': '诱导',
    'jumpPortal': '跳跃通道',
    'microJump': '微型跳跃',
    'microJumpDrive': '微型跳跃驱动',
    'mjd': 'MJD',
    'mjfg': 'MJFG',
    'bubbble': '泡泡',
    'interdiction': '拦截',
    'interdictionNullifier': '拦截失效',
    'nullification': '失效',
    'pointDefense': '点防御',
    'doomsday': '末日武器',
    'superweapon': '超级武器',
    'titan': '泰坦',
    'supercarrier': '超级航母',
    'carrier': '航母',
    'dreadnought': '无畏舰',
    'forceAuxiliary': '力场辅助舰',
    'fax': 'FAX',
    'commandShip': '指挥舰',
    'strategicCruiser': '战略巡洋舰',
    'tacticalDestroyer': '战术驱逐舰',
    'commandDestroyer': '指挥驱逐舰',
    'logisticsFrigate': '后勤护卫舰',
    'logisticsCruiser': '后勤巡洋舰',
    'electronicAttackShip': '电子攻击舰',
    'heavyAssaultCruiser': '重型突击巡洋舰',
    'heavyInterceptor': '重型拦截舰',
    'interdictor': '拦截舰',
    'interceptor': '截击舰',
    'assaultFrigate': '突击护卫舰',
    'covertOps': '隐形特勤舰',
    'stealthBomber': '隐形轰炸舰',
    'reconShip': '侦察舰',
    'blackOps': '黑隐特勤舰',
    'marauder': '掠夺舰',
    'blockadeRunner': '封锁线运输舰',
    'deepSpaceTransport': '深空运输舰',
    'industrialCommandShip': '工业指挥舰',
    'rorqual': '长须鲸',
    'orca': '逆戟鲸',
    'porpoise': '鼠海豚',
    'bowhead': '弓头鲸',
    'freighter': '货舰',
    'jumpFreighter': '跳跃货舰',
    'miningBarge': '采矿驳船',
    'exhumer': '采掘者',
    'venture': '冲锋者',
    'prospect': '勘探者',
    'endurance': '耐久者',
    'expeditionFrigate': '探险护卫舰',
    'shuttle': '穿梭机',
    'corvette': '护卫舰',
    'frigate': '护卫舰',
    'destroyer': '驱逐舰',
    'cruiser': '巡洋舰',
    'battlecruiser': '战列巡洋舰',
    'battleship': '战列舰',
    'capital': '旗舰',
    'subcapital': '亚旗舰',
    'industrial': '工业舰',
    'mining': '采矿',
    'transport': '运输',
    'specialEdition': '特别版',
    'pirate': '海盗',
    'navy': '海军',
    'faction': '势力',
    'techII': 'T2',
    'techIII': 'T3',
    'storyline': '故事线',
    'officer': '官员',
    'deadspace': '死亡空间',
    'abyssal': '深渊',
    'mutated': '变异',
    'corrupted': '腐化',
    'encrypted': '加密',
    'decrypted': '解密',
    'blueprint': '蓝图',
    'reaction': '反应',
    'planetary': '行星',
    'pi': '行星',
    'industry': '工业',
    'manufacturing': '制造',
    'research': '研究',
    'invention': '发明',
    'copying': '复制',
    'reverseEngineering': '逆向工程',
    'reprocessing': '再处理',
    'refining': '精炼',
    'salvaging': '打捞',
    'archeology': '考古',
    'hacking': '黑客',
    'data': '数据',
    'relic': '遗迹',
    'gas': '气体',
    'ice': '冰矿',
    'ore': '矿石',
    'mineral': '矿物',
    'moon': '月球',
    'asteroid': '小行星',
    'belt': '带',
    'anomaly': '异常',
    'signature': '信号',
    'site': '地点',
    'complex': '复合体',
    'ded': 'DED',
    ' unrated': '未评级',
    'escalation': '升级',
    'expedition': '探险',
    'mission': '任务',
    'agent': '代理人',
    'lp': 'LP',
    'standings': '声望',
    'security': '安全等级',
    'bounty': '赏金',
    'reward': '奖励',
    'payment': '支付',
    'tax': '税收',
    'broker': '经纪人',
    'fee': '费用',
    'transaction': '交易',
    'contract': '合同',
    'trade': '贸易',
    'market': '市场',
    'industry': '工业',
    'science': '科学',
    'trade': '贸易',
    'corporation': '公司',
    'alliance': '联盟',
    'faction': '势力',
    'empire': '帝国',
    'pirate': '海盗',
    'mercenary': '雇佣兵',
    'police': '警察',
    'navy': '海军',
    'army': '军队',
    'guard': '卫队',
    'militia': '民兵',
    'fw': '势力战争',
    'factionWarfare': '势力战争',
    'sovereignty': '主权',
    'nullsec': '00',
    'lowsec': '低安',
    'highsec': '高安',
    'wspace': '虫洞',
    'abyssal': '深渊',
    'pochven': '波赫文',
    'triglavian': '三神裔',
    'edencom': 'EDENCOM',
    'concord': '统合部',
    'sisters': '姐妹会',
    'mordu': '莫德团',
    'ore': 'ORE',
    'outerRing': '外环',
    'syndicate': '辛迪加',
    'intaki': '印塔基',
    'serpentis': '天蛇',
    'guristas': '古斯塔斯',
    'angel': '天使',
    'blood': '血袭者',
    'sansha': '萨沙',
    'serpentis': '天蛇',
    'guristas': '古斯塔斯',
    'angel': '天使',
    'blood': '血袭者',
    'sansha': '萨沙',
    'rogueDrone': '流浪无人机',
    'sleepers': '冬眠者',
    'talocan': '塔洛迦',
    'yanJung': '严君',
    'vedmak': 'vedmak',
    'damavik': 'damavik',
    'kikimora': 'kikimora',
    'drekavac': 'drekavac',
    'leshak': 'leshak',
    'zirnitra': 'zirnitra',
    'hydra': 'hydra',
    'tiamat': 'tiamat',
    'chemosh': 'chemosh',
    'molok': 'molok',
    'vehement': 'vehement',
    'vendetta': 'vendetta',
    'revenant': 'revenant',
    'vanquisher': 'vanquisher',
    'komodo': 'komodo',
    'caiman': 'caiman',
    'minokawa': 'minokawa',
    'ninazu': 'ninazu',
    'lif': 'lif',
    'aphoros': 'aphoros',
    'dagon': 'dagon',
    'nyx': 'nyx',
    'wyvern': 'wyvern',
    'aeon': 'aeon',
    'hel': 'hel',
    'avatar': 'avatar',
    'erebus': 'erebus',
    'ragnarok': 'ragnarok',
    'leviathan': 'leviathan',
    'avatar': 'avatar',
    'erebus': 'erebus',
    'ragnarok': 'ragnarok',
    'leviathan': 'leviathan',
}

def get_effect_prefix(effect_name):
    """从 effect_name 提取中文前缀"""
    # 按长度排序，优先匹配最长的前缀
    sorted_prefixes = sorted(effect_prefix_map.items(), key=lambda x: len(x[0]), reverse=True)
    
    for prefix, cn_name in sorted_prefixes:
        if effect_name.startswith(prefix):
            return cn_name
    
    # 如果没有匹配，尝试提取 camelCase 中的大写字母部分
    words = re.findall(r'[A-Z][a-z]+', effect_name)
    if words:
        return words[0]
    
    # 最后返回 effect_name 本身（去掉后缀数字）
    base_name = re.sub(r'\d+$', '', effect_name)
    base_name = re.sub(r'(Role|Ship|Elite)?Bonus.*$', '', base_name, flags=re.IGNORECASE)
    return base_name

def generate_description(effect_name, modified_attr_name):
    """生成描述"""
    prefix = get_effect_prefix(effect_name)
    
    # 获取 display_name（使用属性名查找）
    attr_display = attr_display_names.get(modified_attr_name, '')
    
    # 如果 display_name 为空，使用 attr_name 本身
    if not attr_display:
        # 将 camelCase 转换为可读格式
        attr_display = re.sub(r'([A-Z])', r' \1', modified_attr_name).strip()
    
    return f"{prefix}{attr_display}"

def main():
    # 收集所有 effect|modified_attr|modifying_attr 组合
    entries = []
    
    for effect in effects_data:
        effect_name = effect.get('name', '')
        modifiers = effect.get('modifiers', [])
        
        if not modifiers:
            continue
        
        for mod in modifiers:
            modifying_attr_id = mod.get('modifying_attribute_id')
            modified_attr_id = mod.get('modified_attribute_id')
            
            if not modifying_attr_id or not modified_attr_id:
                continue
            
            # 获取属性名称
            modifying_attr_info = attr_info.get(modifying_attr_id, {})
            modified_attr_info = attr_info.get(modified_attr_id, {})
            
            modifying_attr_name = modifying_attr_info.get('name', str(modifying_attr_id))
            modified_attr_name = modified_attr_info.get('name', str(modified_attr_id))
            
            # 生成描述
            description = generate_description(effect_name, modified_attr_name)
            
            entries.append({
                'description': description,
                'effect_name': effect_name,
                'modified_attr': modified_attr_name,
                'modifying_attr': modifying_attr_name
            })
    
    # 去重（按 effect_name|modified_attr|modifying_attr）
    seen = set()
    unique_entries = []
    for entry in entries:
        key = f"{entry['effect_name']}|{entry['modified_attr']}|{entry['modifying_attr']}"
        if key not in seen:
            seen.add(key)
            unique_entries.append(entry)
    
    # 排序
    unique_entries.sort(key=lambda x: (x['effect_name'], x['modified_attr']))
    
    # 生成输出
    lines = []
    
    for entry in unique_entries:
        line = f"{entry['description']}: {entry['effect_name']}|{entry['modified_attr']}|{entry['modifying_attr']}"
        lines.append(line)
    
    # 写入文件
    output_path = os.path.join(os.path.dirname(__file__), 'zidian1_new.txt')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"已生成 {len(unique_entries)} 条记录")
    print(f"输出文件: {output_path}")

if __name__ == '__main__':
    main()
