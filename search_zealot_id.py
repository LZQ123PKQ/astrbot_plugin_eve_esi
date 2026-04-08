import asyncio
import aiohttp

async def search_zealot_id():
    # 市场中心API地址
    search_url = "https://www.ceve-market.org/api/searchname"
    
    # 搜索关键词
    keyword = "审判者级"
    
    async with aiohttp.ClientSession() as session:
        try:
            # 发送搜索请求（使用POST）
            async with session.post(search_url, data={"name": keyword}) as response:
                if response.status == 200:
                    search_results = await response.json()
                    print(f"搜索结果（{len(search_results)}个物品）:")
                    for item in search_results:
                        item_id = item.get('typeid')
                        item_name = item.get('typename')
                        print(f"{item_name} (ID: {item_id})")
                else:
                    print(f"搜索失败，状态码: {response.status}")
                    print(await response.text())
        except Exception as e:
            print(f"搜索失败: {e}")

if __name__ == "__main__":
    asyncio.run(search_zealot_id())