"""临时脚本: 同步宏观数据到 macro_cache"""
import asyncio, sys
sys.path.insert(0, '.')

async def main():
    from app.services.macro_data import sync_macro_cache
    results = await sync_macro_cache()
    print('\n=== 同步结果 ===')
    print(f'status: {results.get("status")}')
    print(f'total rows: {results.get("total_rows")}')
    failures = {k: v for k, v in results.get("indicators", {}).items() if isinstance(v, int) and v < 0}
    if failures:
        print(f'\n失败 ({len(failures)} 项):')
        for k, v in list(failures.items())[:15]:
            print(f'  ❌ {k}')
    successes = sum(v for v in results.get("indicators", {}).values() if isinstance(v, (int, float)) and v > 0)
    print(f'\n成功: {successes} rows across {len([v for v in results.get("indicators",{}).values() if isinstance(v,int) and v>0])} indicators')

    # 验证
    from sqlalchemy import text
    from app.core.database import async_session_factory
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT COUNT(*) FROM macro_cache"))
        print(f'\nmacro_cache total rows: {r.scalar()}')

asyncio.run(main())
