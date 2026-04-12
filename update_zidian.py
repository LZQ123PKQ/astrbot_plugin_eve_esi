#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量修改 zidian1.txt，按照规则生成描述
格式: 描述: effect_name|modified_attr|modifying_attr

描述生成规则:
- 前缀: 从 effect_name 提取（需要映射表）
- 后缀: 从 attributes.json 获取 modified_attr 的 display_name
"""

import json
import os
import re

# 加载 attributes.json
attributes_path = os.path.join(os.path.dirname(__file__), 'attributes.json')
with open(attributes_path, 'r', encoding='utf-8') as f:
    attributes_data = json.load(f)

# 构建 attribute_name -> display_name 映射
attr_display_names = {}
for attr in attributes_data:
    name = attr.get('name', '')
    display_name = attr.get('display_name', '')
    if name and display_name:
        attr_display_names[name] = display_name

# effect_name 前缀映射表（需要手动维护）
effect_prefix_map = {
    'interceptorNullification': '拦截失效装置',
    'roleBonus': '角色加成',
    'shipBonus': '舰船加成',
    'eliteBonus': '精英加成',
    # 添加更多映射...
}

def get_effect_prefix(effect_name):
    """从 effect_name 提取前缀"""
    for prefix, cn_name in effect_prefix_map.items():
        if effect_name.startswith(prefix):
            return cn_name
    # 如果没有匹配，返回 effect_name 本身（去掉后缀数字）
    base_name = re.sub(r'\d+$', '', effect_name)
    base_name = re.sub(r'RoleBonus$', '', base_name)
    return base_name

def generate_description(effect_name, modified_attr):
    """生成描述"""
    prefix = get_effect_prefix(effect_name)
    suffix = attr_display_names.get(modified_attr, modified_attr)
    return f"{prefix}{suffix}"

def parse_old_line(line):
    """解析旧格式的行"""
    # 格式: xx% 描述: effect_name|attr1/attr2/...
    if ':' not in line or '|' not in line:
        return None
    
    parts = line.split(':', 1)
    if len(parts) != 2:
        return None
    
    desc_part = parts[0].strip()
    effect_part = parts[1].strip()
    
    # 解析 effect_name|attrs
    if '|' not in effect_part:
        return None
    
    effect_parts = effect_part.split('|', 1)
    effect_name = effect_parts[0].strip()
    attrs_part = effect_parts[1].strip() if len(effect_parts) > 1 else ""
    
    # 分割多个 attr
    attrs = [a.strip() for a in attrs_part.split('/') if a.strip()]
    
    return {
        'desc_part': desc_part,
        'effect_name': effect_name,
        'attrs': attrs
    }

def convert_line(line):
    """转换一行为新格式"""
    parsed = parse_old_line(line)
    if not parsed:
        return line  # 无法解析，保持原样
    
    effect_name = parsed['effect_name']
    attrs = parsed['attrs']
    
    # 如果没有 attrs，保持原样
    if not attrs:
        return line
    
    # 为每个 attr 生成一行
    new_lines = []
    for attr in attrs:
        # 尝试从 attr 提取 modifying_attr（这里简化处理，实际可能需要更复杂的逻辑）
        # 暂时使用 effect_name 推断 modifying_attr
        modifying_attr = f"shipBonusRole1"  # 默认值，需要根据实际情况调整
        
        # 生成描述
        description = generate_description(effect_name, attr)
        
        new_line = f"{description}: {effect_name}|{attr}|{modifying_attr}"
        new_lines.append(new_line)
    
    return '\n'.join(new_lines)

def main():
    zidian_path = os.path.join(os.path.dirname(__file__), 'zidian1.txt')
    backup_path = os.path.join(os.path.dirname(__file__), 'zidian1.txt.bak')
    
    # 备份原文件
    if os.path.exists(zidian_path):
        with open(zidian_path, 'r', encoding='utf-8') as f:
            content = f.read()
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"已备份到 {backup_path}")
    
    # 读取并转换
    with open(zidian_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    new_lines = []
    for line in lines:
        line = line.rstrip('\n')
        if not line or line.startswith('#') or line.startswith('##'):
            new_lines.append(line)
            continue
        
        new_line = convert_line(line)
        new_lines.append(new_line)
    
    # 写回文件
    with open(zidian_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(new_lines))
    
    print("转换完成！")

if __name__ == '__main__':
    main()
