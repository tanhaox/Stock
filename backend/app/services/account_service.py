"""账户服务 - 从 holdings.py 提取 (v4.3)."""
import logging
from datetime import date
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("account_service")

async def get_full_account_summary(user_id, session):
    """资金+持仓+已清仓汇总."""
    # Capital
    r = await session.execute(text(
        "SELECT COALESCE(SUM(amount), 0) FROM capital_accounts WHERE user_id = :u"
    ), {"u": user_id})
    total_capital = float(r.scalar() or 0)

    # Holdings
    r = await session.execute(text(
        "SELECT COUNT(*), COALESCE(SUM(qty * cost), 0) FROM holdings WHERE user_id = :u AND qty > 0"
    ), {"u": user_id})
    row = r.fetchone()
    n_holdings = row[0]
    total_cost = float(row[1] or 0)

    # Closed PnL
    r = await session.execute(text(
        "SELECT COUNT(*), COALESCE(SUM(pnl), 0), AVG(pnl) FROM closed_positions WHERE user_id = :u"
    ), {"u": user_id})
    row = r.fetchone()
    n_closed = row[0]
    total_pnl = float(row[1] or 0)
    avg_pnl = float(row[2] or 0)

    return {
        "capital": total_capital, "n_holdings": n_holdings,
        "total_cost": total_cost, "n_closed": n_closed,
        "total_pnl": total_pnl, "avg_pnl": avg_pnl,
    }
