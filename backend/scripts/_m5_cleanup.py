"""M-5 cleanup: remove old tags & unreachable Stage2 prompt branches."""
import re

with open('app/services/event_detector.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace STAGE1_PROMPT tag list (15->5)
old_s1 = 'STAGE1_PROMPT = """分析以下财经新闻, 为每条新闻打1个最匹配的分类标签.'
end_marker = '输出JSON数组: [{"idx":序号,"tag":"标签名","codes":["600660.SH"]}]'
s1_start = content.find(old_s1)
s1_end = content.find(end_marker, s1_start)
if s1_start > 0 and s1_end > 0:
    new_s1 = '''STAGE1_PROMPT = """分析以下财经新闻, 为每条新闻打1个最匹配的分类标签.

标签(5类):
company_announcement(公司公告:合同/财报/增减持/重组)
stock_market(股票市场:股指涨跌/资金流向)
tech_innovation(科技创新:AI/芯片/5G/航天)
leaderboard(龙虎榜:席位/资金)
garbage(垃圾:无关)

'''
    content = content[:s1_start] + new_s1 + content[s1_end:]
    print('STAGE1_PROMPT: 15->5 tags')

# 2. Remove STAGE2 policy/commodity/macro prompts
# Find the block between 'STAGE2_PROMPTS = {' and the closing '}'
s2_start = content.find('STAGE2_PROMPTS = {')
s2_end_block = s2_start
brace_count = 0
in_block = False
for i in range(s2_start, len(content)):
    if content[i] == '{':
        brace_count += 1
        in_block = True
    elif content[i] == '}':
        brace_count -= 1
        if brace_count == 0 and in_block:
            s2_end_block = i
            break

if s2_start > 0:
    # Build new STAGE2_PROMPTS with only "stock"
    new_s2 = '''STAGE2_PROMPTS = {
    # M-5: 仅保留 stock 深度分析 (policy/commodity/macro 已由 macro_data 接管)
    "stock": """分析个股新闻, 提取结构化事件。每只股票最多一条, 同股冲突以最新消息为准, 只从含代码的新闻提取。
格式: {{"events":[{{"ts_code":"600660.SH","direction":"bullish|bearish|neutral","scores":{{"materiality":0-5,"immediacy":0-5,"certainty":0-5,"scope":0-5}},"composite_impact":0.0-5.0,"title":"标题","summary":"影响","related_sectors":["板块"]}}]}}
板块限选: 传媒,公用事业,基础化工,家用电器,建筑材料,建筑装饰,房地产,有色金属,机械设备,汽车,煤炭,电子,石油石化,社会服务,综合,计算机,通信,钢铁,银行,食品饮料
只输出JSON。新闻:""",
}'''
    content = content[:s2_start] + new_s2 + content[s2_end_block + 1:]
    print('STAGE2_PROMPTS: 3->1 (stock only)')

# 3. Clean up the _analyze_all_categories to only process "stock"
# Replace the category loop — just keep stock
old_analyze = '''    # 按变更后的 TAG_TO_SYSTEM 分类items: 每个item只分配到一个主类别
    categorized: dict[str, list[dict]] = defaultdict(list)'''
new_analyze = '''    # M-5: 所有新闻归入 stock 处理 (policy/commodity/macro 已退役)
    categorized: dict[str, list[dict]] = {"stock": []}'''
content = content.replace(old_analyze, new_analyze)

# Also simplify the categorization loop
old_loop = '''        sys_cat = TAG_TO_SYSTEM.get(tag, "garbage")
        if sys_cat in ("commodity", "policy", "macro"):
            sys_cat = "stock"  # M-5: 重定向到 stock
        categorized[sys_cat].append(item)'''
new_loop = '''        sys_cat = TAG_TO_SYSTEM.get(tag, "garbage")
        if sys_cat == "stock":
            categorized["stock"].append(item)'''
content = content.replace(old_loop, new_loop)

# Fallback: if the old loop wasn't found, try another common pattern
old_loop2 = '''        sys_cat = TAG_TO_SYSTEM.get(tag, "macro")
        categorized[sys_cat].append(item)'''
new_loop2 = '''        sys_cat = TAG_TO_SYSTEM.get(tag, "garbage")
        if sys_cat == "stock":
            categorized["stock"].append(item)'''
content = content.replace(old_loop2, new_loop2)

with open('app/services/event_detector.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('M-5 cleanup complete')
