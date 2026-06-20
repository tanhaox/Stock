"""Transform scoring functions from 0-10 to -10~+10."""
import re

with open('app/services/deep_scorer.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = []

# === score_kline_game ===
old = ('    if bull_ratio >= 0.70: bull_score = 3.0\n    elif bull_ratio >= 0.55: bull_score = 2.0\n'
       '    elif bull_ratio >= 0.40: bull_score = 1.2\n    else: bull_score = 0.5')
new = ('    if bull_ratio >= 0.70: bull_score = 3.0\n    elif bull_ratio >= 0.55: bull_score = 2.0\n'
       '    elif bull_ratio >= 0.40: bull_score = 1.0\n    elif bull_ratio >= 0.25: bull_score = -1.0\n'
       '    else: bull_score = -3.0')
if old in content:
    content = content.replace(old, new); changes.append('kline: bull_ratio')

old = ('    if avg_body_ratio > 0.70: body_score = 2.5\n    elif avg_body_ratio > 0.50: body_score = 2.0\n'
       '    elif avg_body_ratio > 0.30: body_score = 1.2\n    else: body_score = 0.5')
new = ('    if avg_body_ratio > 0.70: body_score = 2.5\n    elif avg_body_ratio > 0.50: body_score = 2.0\n'
       '    elif avg_body_ratio > 0.30: body_score = 1.0\n    elif avg_body_ratio > 0.15: body_score = -1.5\n'
       '    else: body_score = -3.0')
if old in content:
    content = content.replace(old, new); changes.append('kline: body_ratio')

old = ('    if pct_above_20d_low is not None and pct_above_20d_low <= 0.05: brk_score = 0.5  # extreme low\n'
       '    elif pct_of_20d_high is not None and pct_of_20d_high >= 0.98: brk_score = 2.5\n'
       '    elif pct_of_20d_high is not None and pct_of_20d_high >= 0.93: brk_score = 2.0\n'
       '    elif pct_of_20d_high is not None and pct_of_20d_high >= 0.85: brk_score = 1.0\n'
       '    else: brk_score = 1.5')
new = ('    if pct_above_20d_low is not None and pct_above_20d_low <= 0.05: brk_score = -3.5\n'
       '    elif pct_of_20d_high is not None and pct_of_20d_high >= 0.98: brk_score = 2.5\n'
       '    elif pct_of_20d_high is not None and pct_of_20d_high >= 0.93: brk_score = 1.5\n'
       '    elif pct_of_20d_high is not None and pct_of_20d_high >= 0.85: brk_score = 0.5\n'
       '    else: brk_score = 0.0')
if old in content:
    content = content.replace(old, new); changes.append('kline: breakout')

# Fix null check pattern
old = ('    if pct_above_20d_low is not None and pct_above_20d_low <= 0.05: brk_score = 0.5'
       '  # extreme low')
new_alt = ('    if pct_above_20d_low is not None and pct_above_20d_low <= 0.05: brk_score = -3.5')
if old in content:
    content = content.replace(old, new_alt); changes.append('kline: brk alt1')

old2 = ('    if pct_above_20d_low is not None and pct_above_20d_low <= 0.05: brk_score = 0.5')
if old2 in content:
    content = content.replace(old2, new_alt); changes.append('kline: brk alt2')

# Fix return clamp
old_ret = 'return {"score": round(min(10, max(0, bull_score + body_score + brk_score + streak_bonus)), 1)\n}'
new_ret = 'return {"score": round(float(np.clip(bull_score + body_score + brk_score + streak_bonus, -10, 10)), 1)\n}'
if old_ret in content:
    content = content.replace(old_ret, new_ret); changes.append('kline: return')

# === score_vol_ratio ===
old = ('    if 1.5 < vr <= 2.5: return {"score": 9.0, "detail": f"healthy_volume vr={vr:.2f}"}\n'
       '    if 0.8 <= vr <= 1.5: return {"score": 7.0, "detail": f"normal_active vr={vr:.2f}"}\n'
       '    if 2.5 < vr <= 4.0: return {"score": 6.0, "detail": f"excessive_volume vr={vr:.2f}"}\n'
       '    if 0.5 <= vr < 0.8: return {"score": 5.0, "detail": f"sluggish vr={vr:.2f}"}\n'
       '    if vr > 4.0: return {"score": 3.0, "detail": f"extreme_volume vr={vr:.2f}"}\n'
       '    return {"score": 2.0, "detail": f"extremely_sluggish vr={vr:.2f}"}')
new = ('    if 1.5 < vr <= 2.5: return {"score": 7.0, "detail": f"healthy_volume vr={vr:.2f}"}\n'
       '    if 0.8 <= vr <= 1.5: return {"score": 2.0, "detail": f"normal_active vr={vr:.2f}"}\n'
       '    if 2.5 < vr <= 4.0: return {"score": -1.0, "detail": f"excessive_volume vr={vr:.2f}"}\n'
       '    if 0.5 <= vr < 0.8: return {"score": -2.0, "detail": f"sluggish vr={vr:.2f}"}\n'
       '    if vr > 4.0: return {"score": -5.0, "detail": f"extreme_volume vr={vr:.2f}"}\n'
       '    return {"score": -6.0, "detail": f"extremely_sluggish vr={vr:.2f}"}')
if old in content:
    content = content.replace(old, new); changes.append('vol_ratio: scoring')

# === score_arbr ===
old = ('    if br > ar and 80 <= br <= 200: score = 8.0\n    elif br > ar and br > 200: score = 6.0\n'
       '    elif ar > br and ar > 200: score = 3.0\n'
       '    elif ar < 50 and br < 60: score = 6.5  # oversold contrarian\n'
       '    else: score = 5.0')
new = ('    if br > ar and 80 <= br <= 200: score = 6.0\n    elif br > ar and br > 200: score = 2.0\n'
       '    elif ar > br and ar > 200: score = -5.0\n'
       '    elif ar < 50 and br < 60: score = 3.0\n'
       '    else: score = 0.0')
if old in content:
    content = content.replace(old, new); changes.append('arbr: base')

old = ('    if ar_cross_up and ar < 120: score += 1.5\n    if ar_cross_up and ar < 70: score += 1.0\n'
       '    if ar_cross_down and br > 200: score -= 2.0\n    if ar_cross_down and br > 300: score -= 2.5')
new = ('    if ar_cross_up and ar < 120: score += 2.0\n    if ar_cross_up and ar < 70: score += 1.5\n'
       '    if ar_cross_down and br > 200: score -= 3.0\n    if ar_cross_down and br > 300: score -= 3.0')
if old in content:
    content = content.replace(old, new); changes.append('arbr: crosses')

old = '    return {"score": round(min(10, max(0, score)), 1)}'
new = '    return {"score": round(float(np.clip(score, -10, 10)), 1)}'
if old in content:
    content = content.replace(old, new); changes.append('arbr: clamp')

# === score_sector_alpha ===
old = ('    if alpha > 5: return {"score": 9.0, "detail": f"strong_outperform alpha={alpha:.1f}%"}\n'
       '    if alpha > 2: return {"score": 7.5, "detail": f"outperform alpha={alpha:.1f}%"}\n'
       '    if alpha > 0: return {"score": 6.0, "detail": f"slight_outperform alpha={alpha:.1f}%"}\n'
       '    if alpha > -2: return {"score": 4.5, "detail": f"slight_underperform alpha={alpha:.1f}%"}\n'
       '    if alpha > -5: return {"score": 2.5, "detail": f"underperform alpha={alpha:.1f}%"}\n'
       '    return {"score": 1.0, "detail": f"severe_underperform alpha={alpha:.1f}%"}')
new = ('    if alpha > 5: return {"score": 8.0, "detail": f"strong_outperform alpha={alpha:.1f}%"}\n'
       '    if alpha > 2: return {"score": 5.0, "detail": f"outperform alpha={alpha:.1f}%"}\n'
       '    if alpha > 0: return {"score": 2.0, "detail": f"slight_outperform alpha={alpha:.1f}%"}\n'
       '    if alpha > -2: return {"score": -2.0, "detail": f"slight_underperform alpha={alpha:.1f}%"}\n'
       '    if alpha > -5: return {"score": -5.0, "detail": f"underperform alpha={alpha:.1f}%"}\n'
       '    return {"score": -8.0, "detail": f"severe_underperform alpha={alpha:.1f}%"}')
if old in content:
    content = content.replace(old, new); changes.append('sector_alpha: scoring')

# === score_fund_flow ===
# Up/down vol ratio
old = ('    vol_ratio_updown = up_vol_sum / max(down_vol_sum, 1) if down_vol_sum > 0 else 2.0\n'
       '    flow_score = 2.5 + np.log(np.clip(vol_ratio_updown, 0.3, 3.0)) * 3.0')
new = ('    vol_ratio_updown = up_vol_sum / max(down_vol_sum, 1) if down_vol_sum > 0 else 2.0\n'
       '    flow_score = np.log(np.clip(vol_ratio_updown, 0.2, 5.0)) * 4.0  # 0 at ratio=1, +/- range')
if old in content:
    content = content.replace(old, new); changes.append('fund_flow: ratio')

# Vol-price health
old = ('    if health > 1.8: health_score = 3.0\n    elif health > 1.3: health_score = 2.2\n'
       '    elif health > 1.0: health_score = 1.5\n    elif health > 0.7: health_score = 0.8\n'
       '    else: health_score = 0.3')
new = ('    if health > 1.8: health_score = 3.0\n    elif health > 1.3: health_score = 2.0\n'
       '    elif health > 1.0: health_score = 1.0\n    elif health > 0.7: health_score = -1.0\n'
       '    else: health_score = -3.0')
if old in content:
    content = content.replace(old, new); changes.append('fund_flow: health')

# Turnover
old = ('    if 0.02 < turnover < 0.15: turn_score = 2.0\n    elif 0.01 < turnover <= 0.02: turn_score = 1.5\n'
       '    elif 0.005 < turnover <= 0.01: turn_score = 1.0\n    elif turnover <= 0.005: turn_score = 0.3\n'
       '    else: turn_score = 0.5')
new = ('    if 0.02 < turnover < 0.15: turn_score = 2.0\n    elif 0.01 < turnover <= 0.02: turn_score = 1.0\n'
       '    elif 0.005 < turnover <= 0.01: turn_score = 0.0\n    elif turnover <= 0.005: turn_score = -2.0\n'
       '    else: turn_score = -1.0')
if old in content:
    content = content.replace(old, new); changes.append('fund_flow: turnover')

# Clamp
old = '    return {"score": round(min(10, max(0, flow_score + health_score + turn_score)), 1)}'
new = '    return {"score": round(float(np.clip(flow_score + health_score + turn_score, -10, 10)), 1)}'
if old in content:
    content = content.replace(old, new); changes.append('fund_flow: clamp')

print(f'Script 2: Applied {len(changes)} changes: {changes}')

with open('app/services/deep_scorer.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
