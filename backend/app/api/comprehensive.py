"""综合分析报告 API — 一键获取多维度分析."""
from fastapi import APIRouter, Query
from app.services.comprehensive_analyzer import analyze_comprehensive

router = APIRouter(prefix="/analysis", tags=["comprehensive"])


@router.get("/comprehensive")
async def get_comprehensive_report(symbol: str = Query(..., description="股票代码, 如 002911.SZ")):
    """对单只股票生成完整的多维度综合分析报告.

    返回四大部分:
      - individual: 个股结构分析 (锁死+老兵+筹码+波段+TG+深度评分+K线趋势)
      - sector: 板块横向对比 (同行涨跌/排名/板块生命周期/共振度)
      - macro: 大盘天时 (市场体制/涨跌比/风格/成交额/天时信号)
      - verdict: 综合裁决 (地利分/天时分/整体判定/操作建议/关注点)
    """
    code = symbol.strip().upper()
    # 自动补全后缀
    if code.isdigit() and len(code) == 6:
        if code.startswith('6'):
            code += '.SH'
        elif code.startswith(('0', '3')):
            code += '.SZ'
        elif code.startswith(('8', '4')):
            code += '.BJ'

    if not code or not (code.endswith('.SH') or code.endswith('.SZ') or code.endswith('.BJ')):
        return {"status": "error", "detail": "格式错误，如: 600660.SH 或 002911.SZ"}

    report = await analyze_comprehensive(code)
    if "error" in report:
        return {"status": "error", "detail": report["error"]}
    return {"status": "success", "data": report}
