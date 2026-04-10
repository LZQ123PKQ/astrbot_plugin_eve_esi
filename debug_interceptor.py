"""
调试脚本：查看科洛斯级的所有 modifying_attribute
"""

import aiohttp
import asyncio
import sys

sys.path.insert(0, '.')
from effect_dict import identify_skill_type

async def debug_interceptor():
    base_url = "https://ali-esi.evepc.163.com"
    
    async with aiohttp.ClientSession() as session:
        item_id = 11184
        
        async with session.get(f"{base_url}/v3/universe/types/{item_id}/") as response:
            if response.status == 200:
                item_info = await response.json()
                dogma_effects = item_info.get('dogma_effects', [])
                
                print(f"物品: {item_info.get('name')}\n")
                
                for effect in dogma_effects:
                    effect_id = effect.get('effect_id')
                    
                    async with session.get(f"{base_url}/v1/dogma/effects/{effect_id}/") as effect_response:
                        if effect_response.status == 200:
                            effect_info = await effect_response.json()
                            effect_name = effect_info.get('name', '')
                            modifiers = effect_info.get('modifiers', [])
                            
                            for mod in modifiers:
                                modifying_attr_id = mod.get('modifying_attribute_id')
                                if modifying_attr_id:
                                    async with session.get(f"{base_url}/v1/dogma/attributes/{modifying_attr_id}/") as attr_response:
                                        if attr_response.status == 200:
                                            attr_info = await attr_response.json()
                                            attr_name = attr_info.get('name', '')
                                            skill_type = identify_skill_type(attr_name)
                                            
                                            print(f"Effect: {effect_name}")
                                            print(f"  modifying_attribute: {attr_name}")
                                            print(f"  识别为: {skill_type}")
                                            print()

if __name__ == "__main__":
    asyncio.run(debug_interceptor())
