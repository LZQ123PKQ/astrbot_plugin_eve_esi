with open('jiacheng.txt', 'r', encoding='utf-8') as f:
    content = f.read()

# 按双换行符分割舰船信息
sections = content.split('\n\n')

ships = []
for section in sections:
    lines = section.strip().split('\n')
    if lines:
        first_line = lines[0].strip()
        # 检查是否是舰船名称（不包含%、+、操作每升一级、特有加成等关键词）
        if first_line and '%' not in first_line and '+' not in first_line and '操作每升一级' not in first_line and '特有加成' not in first_line and '后勤护卫舰' not in first_line:
            ships.append(first_line)

print('舰船列表:')
for i, ship in enumerate(ships, 1):
    print(f'{i}. {ship}')

print(f'\n\n舰船数量: {len(ships)}')

# 保存舰船列表到文件
with open('ships_list.txt', 'w', encoding='utf-8') as f:
    for ship in ships:
        f.write(ship + '\n')

print('\n舰船列表已保存到 ships_list.txt')