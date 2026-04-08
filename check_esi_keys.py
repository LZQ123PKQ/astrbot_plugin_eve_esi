import asyncio
import aiohttp
import json
import re

async def get_all_attributes():
    """获取所有属性"""
    base_url = "https://ali-esi.evepc.163.com"
    attributes_url = f"{base_url}/latest/dogma/attributes/"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(attributes_url) as response:
            if response.status == 200:
                attribute_ids = await response.json()
                print(f"获取到 {len(attribute_ids)} 个属性")
                
                attributes = []
                for attr_id in attribute_ids:  # 获取所有属性
                    attr_url = f"{base_url}/latest/dogma/attributes/{attr_id}/"
                    async with session.get(attr_url) as attr_response:
                        if attr_response.status == 200:
                            attr_data = await attr_response.json()
                            attributes.append(attr_data)
                            if len(attributes) % 100 == 0:
                                print(f"已获取 {len(attributes)} 个属性")
                
                # 保存属性数据
                with open('attributes.json', 'w', encoding='utf-8') as f:
                    json.dump(attributes, f, ensure_ascii=False, indent=2)
                
                print("属性数据已保存到 attributes.json")
                return attributes
            else:
                print(f"获取属性列表失败: {response.status}")
                return []

async def get_all_effects():
    """获取所有效果"""
    base_url = "https://ali-esi.evepc.163.com"
    effects_url = f"{base_url}/latest/dogma/effects/"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(effects_url) as response:
            if response.status == 200:
                effect_ids = await response.json()
                print(f"获取到 {len(effect_ids)} 个效果")
                
                effects = []
                for effect_id in effect_ids:  # 获取所有效果
                    effect_url = f"{base_url}/latest/dogma/effects/{effect_id}/"
                    async with session.get(effect_url) as effect_response:
                        if effect_response.status == 200:
                            effect_data = await effect_response.json()
                            effects.append(effect_data)
                            if len(effects) % 100 == 0:
                                print(f"已获取 {len(effects)} 个效果")
                
                # 保存效果数据
                with open('effects.json', 'w', encoding='utf-8') as f:
                    json.dump(effects, f, ensure_ascii=False, indent=2)
                
                print("效果数据已保存到 effects.json")
                return effects
            else:
                print(f"获取效果列表失败: {response.status}")
                return []

async def check_bonus_handlers():
    """检查bonus_handlers字典中的键是否都在ESI API的返回值中"""
    # 从main.py中提取bonus_handlers字典的键
    with open('main.py', 'r', encoding='utf-8') as f:
        main_content = f.read()
    
    # 提取bonus_handlers字典
    bonus_handlers_match = re.search(r'self\.bonus_handlers = \{(.*?)\n        \}', main_content, re.DOTALL)
    if not bonus_handlers_match:
        print("无法找到bonus_handlers字典")
        return
    
    bonus_handlers_content = bonus_handlers_match.group(1)
    # 提取所有键
    keys = re.findall(r"'([^']+)':", bonus_handlers_content)
    print(f"bonus_handlers字典中有 {len(keys)} 个键")
    
    # 获取所有属性和效果
    attributes = await get_all_attributes()
    effects = await get_all_effects()
    
    # 提取属性名称和效果名称
    attribute_names = [attr.get('name', '') for attr in attributes]
    effect_names = [effect.get('name', '') for effect in effects]
    
    # 检查每个键是否在属性或效果中
    found_keys = []
    not_found_keys = []
    
    for key in keys:
        if key in attribute_names or key in effect_names:
            found_keys.append(key)
        else:
            not_found_keys.append(key)
    
    print(f"找到 {len(found_keys)} 个键在ESI API返回值中")
    print(f"未找到 {len(not_found_keys)} 个键在ESI API返回值中")
    
    if not_found_keys:
        print("未找到的键:")
        for key in not_found_keys:
            print(f"  - {key}")
    else:
        print("所有键都在ESI API返回值中找到")

if __name__ == "__main__":
    asyncio.run(check_bonus_handlers())
