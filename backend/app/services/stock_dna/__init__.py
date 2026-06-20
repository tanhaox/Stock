"""Stock DNA 个性化模型包.

核心模块:
  features: ~150 维特征工程 (77日线 + 15表情 + 15市场 + 12转移 + 8周期 + 15历史 + 8交互)
  emotion: 日内表情聚类 + 马尔可夫转移矩阵
  cycle: 老兵周期检测 (锁死→爆发)
  market_context: 大盘分时联动特征
  model: Per-Stock XGBoost 训练 + 推理
  data_builder: 从 daily_kline + min_kline 生成训练样本
  inference: DNA 推理服务
  similarity: 跨股票 DNA 相似度
"""
