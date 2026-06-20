"""根据 recommendation_tracking 实际盈亏数据, 训练评分维度权重.

从分析中发现的根因:
  - composite_score 与 T+1 收益零相关 (赢家平均 49.3 vs 输家 47.8)
  - 最差收益组 平均分最高 (49.1)
  - Bayesian 优化器有 6280 条经验但所有参数组观测数 = 0
  - 核心问题: DEFAULT_WEIGHTS 是人工猜测, 从未基于实际数据训练

本脚本:
  1. 从 recommendation_tracking 获取历史推荐 (含 T+3/T+5 盈亏)
  2. 匹配 analysis_scores 的各维度分数
  3. 用 Logistic Regression 训练维度权重
  4. 生成新的 DEFAULT_WEIGHTS 和 ARCHETYPE_OFFSETS
  5. 回测验证新权重的预测能力
"""
import asyncio, json, numpy as np
from datetime import date, timedelta
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sqlalchemy import text
import sys; sys.path.insert(0, '/c/AI-Agent-Local/Stock/backend')
from app.core.database import async_session_factory

async def main():
    # ─── 1. 加载训练数据 ───
    async with async_session_factory() as s:
        # 只取 T+3 已验证的记录 (至少需要3个交易日后的数据)
        r = await s.execute(text("""
            SELECT rt.symbol, rt.scan_date, rt.composite_score, rt.close_price,
                   rt.was_profitable_3d, rt.was_profitable_5d,
                   a.dimension_scores, a.tech_score, a.kline_score, a.fund_score,
                   a.sector_bonus, a.archetype, a.level
            FROM recommendation_tracking rt
            LEFT JOIN analysis_scores a ON a.symbol=rt.symbol AND a.scan_date=rt.scan_date
            WHERE rt.was_profitable_3d IS NOT NULL
              AND a.dimension_scores IS NOT NULL
            ORDER BY rt.scan_date
        """))
        rows = r.fetchall()

    print(f'Loaded {len(rows)} verified samples')

    # ─── 2. 构建特征矩阵 ───
    dim_keys = [
        'tech_score', 'kline_score', 'fund_score', 'tg_momentum_score',
        'vol_ratio_score', 'arbr_score', 'sector_alpha_score',
        'market_relative_score', 'valuation_score', 'ma_trend_score',
        'pattern_score', 'trend_deviation_score', 'bbi_score', 'box_score',
        'ambush_score'
    ]
    # Also add enhanced dimensions if available
    extra_keys = ['real_fund_score', 'northbound_score', 'institutional_score', 'shareholder_score']

    X_list = []
    y_list_3d = []
    y_list_5d = []
    symbols_dates = []

    for row in rows:
        sym, sd, sc, px, p3, p5, dims_raw, tech, kl, fd, sb, arch, lv = row
        dims = dims_raw if isinstance(dims_raw, dict) else (json.loads(dims_raw) if dims_raw else {})

        features = []
        for k in dim_keys:
            features.append(float(dims.get(k, 0)))
        for k in extra_keys:
            features.append(float(dims.get(k, 0)))
        features.append(float(sc or 0))  # composite_score itself
        features.append(float(sb or 0))  # sector_bonus

        X_list.append(features)
        y_list_3d.append(1 if p3 else 0)
        y_list_5d.append(1 if p5 else 0)

    X = np.array(X_list)
    y3 = np.array(y_list_3d)
    y5 = np.array(y_list_5d)

    print(f'Feature matrix: {X.shape}')
    print(f'T+3 win rate: {y3.mean()*100:.1f}%')
    print(f'T+5 win rate: {y5.mean()*100:.1f}%')

    # ─── 3. 训练 Logistic Regression ───
    # Standardize features
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # T+3 model
    model3 = LogisticRegression(max_iter=1000, C=0.5, class_weight='balanced')
    model3.fit(X_scaled, y3)
    # Cross-validation
    cv3 = cross_val_score(model3, X_scaled, y3, cv=5, scoring='roc_auc')
    print(f'\nT+3 CV AUC: {cv3.mean():.3f} (+/-{cv3.std()*2:.3f})')

    # T+5 model
    model5 = LogisticRegression(max_iter=1000, C=0.5, class_weight='balanced')
    model5.fit(X_scaled, y5)
    cv5 = cross_val_score(model5, X_scaled, y5, cv=5, scoring='roc_auc')
    print(f'T+5 CV AUC: {cv5.mean():.3f} (+/-{cv5.std()*2:.3f})')

    # ─── 4. 输出维度重要性 (系数) ───
    all_keys = dim_keys + extra_keys + ['composite_score', 'sector_bonus']
    coefs = list(zip(all_keys, model3.coef_[0]))
    coefs.sort(key=lambda x: -abs(x[1]))

    print(f'\n{"="*70}')
    print(f'  维度重要性排序 (T+3 Logistic Regression 系数)')
    print(f'{"="*70}')
    for name, coef in coefs:
        direction = '→ 加分' if coef > 0 else '→ 减分'
        print(f'  {name:30s}  coef={coef:+.4f}  {direction}')

    # ─── 5. 生成新权重 ───
    # 将 Logistic 系数转换为 0-5 区间的权重
    max_abs = max(abs(c) for _, c in coefs)
    new_weights = {}
    for name, coef in coefs:
        # 负系数 → 该维度对选股是反向指标, 权重应降低或取反
        normalized = (coef / max_abs) * 3.0  # scale to ~0-3 range, centered
        new_weights[name] = round(max(0.5, min(4.0, abs(normalized))), 1)

    print(f'\n{"="*70}')
    print(f'  建议新权重 (基于 T+3 实际盈亏训练)')
    print(f'{"="*70}')
    for k, v in sorted(new_weights.items(), key=lambda x: -x[1]):
        print(f'  {k}: {v}')

    # ─── 6. 回测: 新权重 vs 旧权重 ───
    # Split by date: train on older data, test on recent (June)
    train_mask = np.array([sd < date(2026,5,25) for sd in [row[1] for row in rows]])
    test_mask = np.array([sd >= date(2026,5,25) for sd in [row[1] for row in rows]])

    if test_mask.sum() > 10:
        X_train = X_scaled[train_mask]; y_train = y3[train_mask]
        X_test = X_scaled[test_mask]; y_test = y3[test_mask]

        model_test = LogisticRegression(max_iter=1000, C=0.5, class_weight='balanced')
        model_test.fit(X_train, y_train)
        test_probs = model_test.predict_proba(X_test)[:, 1]

        # Compare: old composite_score vs new probability
        old_scores = X[test_mask, -2]  # composite_score column
        from sklearn.metrics import roc_auc_score

        # Binarize old scores at various thresholds
        for thresh in [40, 50, 60]:
            old_pred = (old_scores >= thresh).astype(int)
            if len(set(old_pred)) > 1:
                old_acc = (old_pred == y_test).mean()
                new_pred = (test_probs >= 0.5).astype(int)
                new_acc = (new_pred == y_test).mean()
                print(f'\n  阈值={thresh}: 旧权重准确率={old_acc:.2%}  新权重={new_acc:.2%}')

        try:
            old_auc = roc_auc_score(y_test, old_scores)
            new_auc = roc_auc_score(y_test, test_probs)
            print(f'\n  旧 composite_score AUC: {old_auc:.3f}')
            print(f'  新 Logistic 模型 AUC: {new_auc:.3f}')
            print(f'  提升: {(new_auc - old_auc)*100:+.1f}%')
        except:
            pass

    # ─── 7. 保存模型 ───
    import pickle
    model_data = {
        'coef_': model3.coef_.tolist(),
        'intercept_': model3.intercept_.tolist(),
        'dim_keys': all_keys,
        'scaler_mean': scaler.mean_.tolist(),
        'scaler_scale': scaler.scale_.tolist(),
        'new_weights': new_weights,
        'cv_auc': float(cv3.mean()),
    }
    with open('C:/AI-Agent-Local/Stock/backend/models/scoring_model_v1.json', 'w') as f:
        json.dump(model_data, f, indent=2)
    print(f'\nModel saved to models/scoring_model_v1.json')

asyncio.run(main())
