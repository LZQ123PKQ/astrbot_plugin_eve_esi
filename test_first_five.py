import asyncio
import aiohttp
import sys
import os

# 导入test_111.py中的类
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_111 import StandaloneEveESI

async def test_multiple_ships(ship_names):
    """测试多个舰船"""
    async with aiohttp.ClientSession() as session:
        for ship_name in ship_names:
            print(f"\n{'='*60}")
            print(f"测试舰船: {ship_name}")
            print(f"{'='*60}")
            
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
                            # 获取第一个结果的typeid
                            item_id = search_result[0]['typeid']
                            item_name = search_result[0]['typename']
                            print(f"找到物品: {item_name} (ID: {item_id})")
                        else:
                            print(f"未找到 {ship_name}")
                            continue
                    except Exception as e:
                        print(f"解析市场中心API响应失败: {e}")
                        continue
                else:
                    print(f"市场中心API搜索失败: {response.status}")
                    continue
            
            # 2. 使用ESI API获取物品信息
            eve_esi = StandaloneEveESI()
            item_info = await eve_esi.esi_request(session, f"/v4/universe/types/{item_id}/")
            
            if item_info:
                print(f"获取到物品信息: {item_info.get('name', '未知')}")
                
                # 3. 提取属性
                attr_dict = eve_esi._extract_attributes(item_info)
                
                # 4. 获取物品的dogma effects
                dogma_effects = item_info.get('dogma_effects', [])
                
                if dogma_effects:
                    print(f"找到 {len(dogma_effects)} 个效果")
                    
                    # 5. 处理加成
                    skill_bonuses_dict, unique_bonuses = await eve_esi._process_bonuses(dogma_effects, attr_dict, session, item_info.get('name', ''))
                    
                    # 6. 构建结果
                    result = await eve_esi._build_result(item_info, skill_bonuses_dict, unique_bonuses, attr_dict, item_name, dogma_effects, session)
                    
                    # 7. 输出结果
                    print("\n===== 舰船属性 =====")
                    print(result)
                else:
                    print("未找到物品的效果信息")
            else:
                print("未获取到物品信息")

if __name__ == "__main__":
    # 前五条船
    ship_names = ["富豪级", "巨神兵级", "惩罚者级", "检察官级", "磨难级"]
    
    print("开始测试前五条船...")
    asyncio.run(test_multiple_ships(ship_names))
