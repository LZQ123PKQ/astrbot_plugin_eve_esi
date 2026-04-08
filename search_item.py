import asyncio
import aiohttp

async def search_item(name):
    """搜索物品"""
    url = 'https://ali-esi.evepc.163.com/latest/universe/ids/'
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=[name]) as response:
            if response.status == 200:
                data = await response.json()
                print(data)
            else:
                print(f'请求失败: {response.status}')

if __name__ == "__main__":
    # 搜索富豪级（英文名称）
    asyncio.run(search_item('Fealty'))