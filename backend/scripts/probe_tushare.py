#!/usr/bin/env python3
"""Full Tushare capability probe — all layers, no legacy constraints."""
import asyncio, sys, json
sys.path.insert(0, '.')
from app.services.tushare_common import call_tushare

async def probe(api, params=None, fields=''):
    try:
        rows = await call_tushare(api, params or {}, fields)
        if rows and len(rows) > 0:
            keys = sorted(rows[0].keys())
            return {'status': 'ok', 'count': len(rows), 'keys': keys[:20], 'sample_row': str(rows[0])[:150]}
        return {'status': 'empty'}
    except Exception as e:
        msg = str(e)[:100]
        if any(w in msg for w in ['权限','不支持','permission']):
            return {'status': 'no_permission', 'msg': msg[:50]}
        return {'status': 'error', 'msg': msg[:50]}

async def main():
    probes = [
        # Layer 1: Macro
        ("MACRO", "cn_m", {'m':'202604'}),
        ("MACRO", "cn_cpi", {'m':'202604'}),
        ("MACRO", "cn_ppi", {'m':'202604'}),
        ("MACRO", "cn_gdp", {'quarter':'2025Q4'}),
        ("MACRO", "cn_pmi", {'m':'202605'}),
        ("MACRO", "shibor", {'date':'20260605'}),
        ("MACRO", "shibor_lpr", {}),
        ("MACRO", "fx_daily", {'trade_date':'20260605'}),
        ("MACRO", "fx_obasic", {}),
        ("MACRO", "sf_monthly", {}),  # social financing
        ("MACRO", "gz_index", {}),     # government bonds
        # Layer 2: Commodity
        ("COMMODITY", "fut_basic", {'exchange':'DCE'}),
        ("COMMODITY", "fut_basic", {'exchange':'SHFE'}),
        ("COMMODITY", "fut_basic", {'exchange':'CZCE'}),
        ("COMMODITY", "fut_basic", {'exchange':'CFFEX'}),
        ("COMMODITY", "fut_basic", {'exchange':'INE'}),
        ("COMMODITY", "fut_daily", {'trade_date':'20260605','exchange':'DCE'}),
        ("COMMODITY", "fut_daily", {'trade_date':'20260605','exchange':'SHFE'}),
        ("COMMODITY", "fut_holding", {'trade_date':'20260605','symbol':'CU'}),
        ("COMMODITY", "fut_wsr", {}),
        ("COMMODITY", "fut_weekly", {}),
        # Layer 3: Flow/Money
        ("FLOW", "moneyflow", {'ts_code':'000001.SZ'}),
        ("FLOW", "moneyflow_hsgt", {}),
        ("FLOW", "hsgt_top10", {'trade_date':'20260605'}),
        ("FLOW", "ggt_top10", {'trade_date':'20260605'}),
        ("FLOW", "ggt_daily", {}),
        ("FLOW", "margin", {'trade_date':'20260605'}),
        ("FLOW", "margin_detail", {'trade_date':'20260605'}),
        ("FLOW", "hk_hold", {}),
        ("FLOW", "hk_daily", {}),
        ("FLOW", "stk_holdernumber", {}),
        ("FLOW", "stk_holderstrade", {}),
        ("FLOW", "block_trade", {}),
        ("FLOW", "pledge_stat", {}),
        ("FLOW", "pledge_detail", {}),
        ("FLOW", "share_float", {}),
        ("FLOW", "repurchase", {}),
        # Layer 4: Sector/Concept
        ("SECTOR", "ths_daily", {'ts_code':'883941.TI'}),
        ("SECTOR", "ths_member", {}),
        ("SECTOR", "ths_index", {}),
        ("SECTOR", "index_classify", {'level':'L1'}),
        ("SECTOR", "index_member", {'index_code':'000300.SH'}),
        ("SECTOR", "index_dailybasic", {}),
        ("SECTOR", "sw_daily", {}),
        ('SECTOR', 'concept', {}),
        ('SECTOR', 'concept_detail', {}),
        # Layer 5: Financial
        ("FIN", "fina_indicator_vip", {'ts_code':'000001.SZ'}),
        ("FIN", "income_vip", {'ts_code':'000001.SZ'}),
        ("FIN", "balancesheet_vip", {'ts_code':'000001.SZ'}),
        ("FIN", "cashflow_vip", {'ts_code':'000001.SZ'}),
        ("FIN", "forecast_vip", {}),
        ("FIN", "express_vip", {}),
        ("FIN", "stk_factor_pro", {}),
        ("FIN", "daily_basic", {'ts_code':'000001.SZ'}),
        ("FIN", "bak_basic", {}),
        ("FIN", "disclosure_date", {}),
        # Layer 6: News/Events
        ("NEWS", "major_news", {'src':''}),
        ("NEWS", "cctv_news", {}),
        ("NEWS", "news", {}),
        # Layer 7: Derivatives
        ("DERIV", "opt_basic", {}),
        ("DERIV", "opt_daily", {}),
        ("DERIV", "cb_basic", {}),
        ("DERIV", "cb_daily", {}),
    ]

    results = {}
    for layer, name, params in probes:
        r = await probe(name, params)
        results.setdefault(layer, []).append((name, r))

    for layer in ["MACRO","COMMODITY","FLOW","SECTOR","FIN","NEWS","DERIV"]:
        items = results.get(layer, [])
        ok = sum(1 for _, r in items if r['status'] == 'ok')
        denied = sum(1 for _, r in items if r['status'] == 'no_permission')
        print(f"\n=== {layer} ({ok} ok, {denied} denied, {len(items)} total) ===")
        for name, r in items:
            if r['status'] == 'ok':
                print(f"  + {name:<25} {r['count']:>6} rows  keys={r['keys'][:8]}")
            elif r['status'] == 'no_permission':
                print(f"  x {name:<25} NO PERMISSION")
            else:
                print(f"  - {name:<25} {r['status']}")

asyncio.run(main())
