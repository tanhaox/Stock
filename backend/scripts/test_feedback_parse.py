import re, json

# Test JSON regex
raw = '```json\n{"stock_code": "600660.SH", "positive_signals": [{"type": "financial", "description": "test", "confidence": 0.9}], "negative_signals": [{"type": "technical_risk", "description": "test2", "confidence": 0.85}]}\n```'

# Regex from feedback.py
m = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', raw, re.DOTALL)
if m:
    parsed = json.loads(m.group(1))
    print('EXTRACT OK')
else:
    print('Regex failed, trying direct...')
    try:
        parsed = json.loads(raw.strip())
        print('DIRECT OK')
    except:
        print('ALL FAILED')
        exit(1)

pos = parsed.get('positive_signals', [])
neg = parsed.get('negative_signals', [])
print(f'Pos: {len(pos)}, Neg: {len(neg)}')

TYPE_DIM_MAP = {
    'technical_risk': ['tech', 'kline', 'ma_trend'],
    'fund_flow': ['fund', 'vol_ratio'],
    'financial': ['valuation', 'fundamental'],
    'financial_risk': ['valuation', 'fundamental'],
    'opportunity': ['sector_alpha', 'arbr'],
    'valuation': ['valuation'],
}
for sig in neg:
    dims = TYPE_DIM_MAP.get(sig['type'], [])
    print(f'  NEG {sig["type"]}: {dims} reward={-sig["confidence"]}')
for sig in pos:
    dims = TYPE_DIM_MAP.get(sig['type'], [])
    print(f'  POS {sig["type"]}: {dims} reward={sig["confidence"]}')
print('ALL TESTS PASSED')
