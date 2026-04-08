import json

# 读取属性数据
with open('attributes.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('Total attributes:', len(data))

# 提取所有display_name
display_names = [item.get('display_name') for item in data if item.get('display_name')]
print('Total display names:', len(display_names))

# 检查唯一值
unique_names = set(display_names)
print('Unique display names:', len(unique_names))

# 检查重复值
duplicates = [name for name in display_names if display_names.count(name) > 1]
print('Duplicate display names:', duplicates)

# 打印重复的详细信息
if duplicates:
    print('\nDetailed duplicates:')
    for name in set(duplicates):
        count = display_names.count(name)
        print(f'{name}: {count} occurrences')
else:
    print('\nNo duplicate display names found!')
