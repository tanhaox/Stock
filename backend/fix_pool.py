import asyncio, sys
sys.path.insert(0, '.')

with open('app/services/alphaflow_pool_service.py', 'r', encoding='utf-8') as f:
    src = f.read()

# Fix the broken backslash-quote escapes
src = src.replace('\\"SELECT level, buy_strength FROM scan_results WHERE symbol=:sym AND scan_date=(SELECT MAX(scan_date) FROM scan_results)\\"',
                  '\"SELECT level, buy_strength FROM scan_results WHERE symbol=:sym AND scan_date=(SELECT MAX(scan_date) FROM scan_results)\"')
src = src.replace('\\"sym\\"', '\"sym\"')
src = src.replace('\\"L1\\"', '\"L1\"')
src = src.replace('\\"signal\\"', '\"signal\"')
src = src.replace('\\"watch\\"', '\"watch\"')
src = src.replace('\\"label\\"', '\"label\"')
src = src.replace('\\"tg_no_confirm\\"', '\"tg_no_confirm\"')
src = src.replace('\\"sxqs\\"', '\"sxqs\"')

# Verify fix
if '\\\"' in src:
    print('STILL HAS BROKEN ESCAPES')
else:
    with open('app/services/alphaflow_pool_service.py', 'w', encoding='utf-8') as f:
        f.write(src)
    print('FIXED')
