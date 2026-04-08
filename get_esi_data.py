import asyncio
import aiohttp
import json

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
                for attr_id in attribute_ids[:500]:  # 获取前500个属性
                    attr_url = f"{base_url}/latest/dogma/attributes/{attr_id}/"
                    async with session.get(attr_url) as attr_response:
                        if attr_response.status == 200:
                            attr_data = await attr_response.json()
                            # 只保存与加成相关的属性
                            if any(keyword in attr_data.get('name', '').lower() for keyword in ['bonus', 'damage', 'range', 'speed', 'rate', 'resistance', 'capacitor', 'armor', 'shield', 'structure']):
                                attributes.append(attr_data)
                                print(f"获取属性: {attr_data.get('name')} (ID: {attr_id})")
                
                # 保存属性数据
                with open('attributes.json', 'w', encoding='utf-8') as f:
                    json.dump(attributes, f, ensure_ascii=False, indent=2)
                
                print("属性数据已保存到 attributes.json")
            else:
                print(f"获取属性列表失败: {response.status}")

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
                for effect_id in effect_ids[:500]:  # 获取前500个效果
                    effect_url = f"{base_url}/latest/dogma/effects/{effect_id}/"
                    async with session.get(effect_url) as effect_response:
                        if effect_response.status == 200:
                            effect_data = await effect_response.json()
                            # 只保存与加成相关的效果
                            if any(keyword in effect_data.get('name', '').lower() for keyword in ['bonus', 'damage', 'range', 'speed', 'rate', 'resistance', 'capacitor', 'armor', 'shield', 'structure']):
                                effects.append(effect_data)
                                print(f"获取效果: {effect_data.get('name')} (ID: {effect_id})")
                
                # 保存效果数据
                with open('effects.json', 'w', encoding='utf-8') as f:
                    json.dump(effects, f, ensure_ascii=False, indent=2)
                
                print("效果数据已保存到 effects.json")
            else:
                print(f"获取效果列表失败: {response.status}")

if __name__ == "__main__":
    asyncio.run(get_all_attributes())
    asyncio.run(get_all_effects())