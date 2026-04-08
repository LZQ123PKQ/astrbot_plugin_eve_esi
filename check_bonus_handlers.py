# 从main.py文件中提取bonus_handlers字典的键
with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 找到bonus_handlers字典的开始和结束行
start_line = -1
end_line = -1
for i, line in enumerate(lines):
    if 'self.bonus_handlers = {' in line:
        start_line = i
    elif start_line != -1 and '}' in line and end_line == -1:
        end_line = i
        break

# 提取字典内容
if start_line != -1 and end_line != -1:
    dict_lines = lines[start_line:end_line+1]
    dict_content = ''.join(dict_lines)
    
    # 提取所有键
    import re
    keys = re.findall(r"'([^']+)':", dict_content)
    
    print('Total keys in bonus_handlers:', len(keys))
    print('Unique keys:', len(set(keys)))
    
    # 检查重复键
    duplicate_keys = [key for key in keys if keys.count(key) > 1]
    print('Duplicate keys:', duplicate_keys)
    
    # 打印重复的详细信息
    if duplicate_keys:
        print('\nDetailed duplicates:')
        for key in set(duplicate_keys):
            count = keys.count(key)
            print(f'{key}: {count} occurrences')
    else:
        print('\nNo duplicate keys found in bonus_handlers!')
else:
    print('Could not find bonus_handlers dictionary in main.py')
