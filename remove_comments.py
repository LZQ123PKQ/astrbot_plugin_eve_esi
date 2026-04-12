#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去掉 zidian1.txt 中的注释行"""

import os

zidian_path = os.path.join(os.path.dirname(__file__), 'zidian1.txt')

with open(zidian_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 过滤掉注释行和空行
new_lines = []
for line in lines:
    line = line.rstrip('\n')
    if line and not line.startswith('#'):
        new_lines.append(line)

# 写回文件
with open(zidian_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(new_lines))

print(f"已移除注释行，剩余 {len(new_lines)} 行")
