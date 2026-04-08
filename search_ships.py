import asyncio
import aiohttp

async def search_ships():
    # 使用CEVE市场中心API搜索舰船
    url = "https://www.ceve-market.org/api/searchname"
    
    async def search_ship(name):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data={"name": name}) as response:
                    if response.status == 200:
                        result = await response.json()
                        print(f"搜索 '{name}' 结果:")
                        for item in result:
                            print(f"  - {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})")
                    else:
                        print(f"搜索 '{name}' 失败: {response.status}")
        except Exception as e:
            print(f"搜索异常: {e}")
    
    # 搜索舰船
    await search_ship("帝国海军切割机级")
    await search_ship("富豪级海军型")

if __name__ == "__main__":
    asyncio.run(search_ships())