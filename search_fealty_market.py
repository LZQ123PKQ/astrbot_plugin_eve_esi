import asyncio
import aiohttp

async def search_fealty_market():
    """使用市场中心API搜索富豪级的物品ID"""
    url = "https://www.ceve-market.org/api/searchname"
    data = {"name": "富豪级"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    print("市场中心搜索结果:")
                    for item in result:
                        print(f"物品ID: {item['typeid']}, 物品名称: {item['typename']}")
                else:
                    print(f"搜索失败: {response.status}")
    except Exception as e:
        print(f"搜索异常: {e}")

if __name__ == "__main__":
    asyncio.run(search_fealty_market())
