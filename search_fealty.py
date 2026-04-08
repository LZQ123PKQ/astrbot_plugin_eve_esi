import asyncio
import aiohttp

async def search_fealty():
    """搜索富豪级的物品ID"""
    base_url = "https://ali-esi.evepc.163.com"
    search_url = f"{base_url}/latest/universe/ids/"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(search_url, json=["Fealty"]) as response:
                if response.status == 200:
                    result = await response.json()
                    print("搜索结果:")
                    print(result)
                else:
                    print(f"搜索失败: {response.status}")
    except Exception as e:
        print(f"搜索异常: {e}")

if __name__ == "__main__":
    asyncio.run(search_fealty())
