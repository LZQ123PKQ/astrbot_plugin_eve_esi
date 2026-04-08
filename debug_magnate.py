import asyncio
import aiohttp
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_111 import StandaloneEveESI

async def debug_magnate():
    """调试富豪级"""
    async with aiohttp.ClientSession() as session:
        ship_name = "富豪级"
        
        # 1. 使用市场中心API搜索舰船
        search_url = "https://www.ceve-market.org/api/searchname"
        search_data = {
            "name": ship_name
        }
        
        async with session.post(search_url, data=search_data, headers={"Content-Type": "application/x-www-form-urlencoded"}) as response:
            if response.status == 200:
                try:
                    search_result = await response.json()
                    if search_result and len(search_result) > 0:
                        item_id = search_result[0]['typeid']
                        item_name = search_result[0]['typename']
                        print(f"找到物品: {item_name} (ID: {item_id})")
                    else:
                        print(f"未找到 {ship_name}")
                        return
                except Exception as e:
                    print(f"解析市场中心API响应失败: {e}")
                    return
        
        # 2. 使用ESI API获取物品信息
        eve_esi = StandaloneEveESI()
        item_info = await eve_esi.esi_request(session, f"/v4/universe/types/{item_id}/")
        
        if item_info:
            dogma_effects = item_info.get('dogma_effects', [])
            
            print("\n=== Dogma Effects ===")
            for effect in dogma_effects:
                effect_id = effect.get('effect_id')
                print(f"\nEffect ID: {effect_id}")
                
                # 获取effect信息
                effect_info = await eve_esi.esi_request(session, f"/v2/dogma/effects/{effect_id}/")
                if effect_info:
                    effect_name = effect_info.get('name', '')
                    print(f"Effect Name: {effect_name}")
                    
                    # 测试identify_skill_type
                    skill_type = eve_esi._identify_skill_type(effect_name)
                    print(f"Identified Skill Type: {skill_type}")

if __name__ == "__main__":
    asyncio.run(debug_magnate())
