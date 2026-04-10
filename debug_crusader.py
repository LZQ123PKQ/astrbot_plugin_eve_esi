"""
调试脚本：查看科洛斯级的实际 effect 名称
"""

import aiohttp
import asyncio
import sys

sys.path.insert(0, '.')
from effect_dict import identify_skill_type, EFFECT_DESCRIPTIONS, get_effect_description

async def debug_crusader():
    base_url = "https://ali-esi.evepc.163.com"
    
    async with aiohttp.ClientSession() as session:
        # 获取科洛斯级信息 (ID: 11184)
        item_id = 11184
        
        # 获取物品类型信息
        async with session.get(f"{base_url}/v3/universe/types/{item_id}/") as response:
            if response.status == 200:
                item_info = await response.json()
                dogma_effects = item_info.get('dogma_effects', [])
                
                print(f"物品: {item_info.get('name')}")
                print(f"Effect 数量: {len(dogma_effects)}")
                print("\nEffect 列表:")
                
                for effect in dogma_effects:
                    effect_id = effect.get('effect_id')
                    
                    # 获取 effect 详细信息
                    async with session.get(f"{base_url}/v1/dogma/effects/{effect_id}/") as effect_response:
                        if effect_response.status == 200:
                            effect_info = await effect_response.json()
                            effect_name = effect_info.get('name', '')
                            
                            # 识别技能类型
                            skill_type = identify_skill_type(effect_name)
                            
                            # 获取描述
                            desc = get_effect_description(effect_name, 5.0, EFFECT_DESCRIPTIONS)
                            
                            print(f"\n  Effect ID: {effect_id}")
                            print(f"  Effect Name: {effect_name}")
                            print(f"  技能类型: {skill_type}")
                            print(f"  描述: {desc}")

if __name__ == "__main__":
    asyncio.run(debug_crusader())
