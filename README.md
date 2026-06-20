# Stock Analyst — A股量化分析系统 v4.7

## 功能

- **TG 信号扫描**: V11.2 18步精准买卖指标，全市场 5,200+ 只股票扫描
- **AlphaFlow 主升浪**: 锁死检测 + XGBoost + 两期扫描 (池内秒级 + 全市场后台)
- **大神仙空 v2.0**: 7维度卖出指标 (KDJ+MACD+MA+RSI+量价+动量+超买), 13/13同花顺校准
- **11维深度评分**: 含大神仙空过滤 (BF≥3剔除, ≥2打折)
- **DeepSeek LLM 深度分析**: 富宏观上下文 (25+指标, 杜绝LLM虚构)
- **5 原型分类**: 大盘蓝筹/小盘题材/成长科技/价值防御/周期资源
- **Bayesian 自学习**: Normal-Normal 共轭更新 + Shadow Trainer 66维权重
- **持仓管理**: 组合分析、集中度警告、大神仙空退出信号
- **SSE 实时反馈**: AlphaFlow 扫描进度 / LLM 分析进度 / 新闻分析进度

## 启动

```bash
# 一键启动
StockAnalyst.bat

# 或手动分别启动
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npx vite
```

## 浏览器扩展安装

1. 打开 `edge://extensions/` 或 `chrome://extensions/`
2. 开启「开发人员模式」
3. 加载解压缩的扩展 → 选择 `browser-extension/` 目录

## 技术栈

- 后端: Python 3.13 + FastAPI + SQLAlchemy 2.0 + PostgreSQL + asyncpg
- 前端: React 19 + TypeScript + Vite 8 + Ant Design 6
- 数据源: Tushare Pro API
- AI: DeepSeek API + 百度千帆 AI Search
