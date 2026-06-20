"""Phase 1 数据库迁移 + 股票→原型分配."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.database import async_session_factory, engine
from sqlalchemy import text

MIGRATIONS = [
    # 1. 扩展 archetype_profiles
    "ALTER TABLE archetype_profiles ADD COLUMN IF NOT EXISTS effective_date date DEFAULT CURRENT_DATE",
    "ALTER TABLE archetype_profiles ADD COLUMN IF NOT EXISTS parent_archetype varchar",
    "ALTER TABLE archetype_profiles ADD COLUMN IF NOT EXISTS sample_count int DEFAULT 0",
    "ALTER TABLE archetype_profiles ADD COLUMN IF NOT EXISTS is_trainable boolean DEFAULT true",
    # 2. 扩展 bayesian_beliefs — strategy 维度
    "ALTER TABLE bayesian_beliefs ADD COLUMN IF NOT EXISTS strategy varchar DEFAULT '__global__'",
    # 重建唯一约束
    """
    DO $$ BEGIN
        ALTER TABLE bayesian_beliefs DROP CONSTRAINT IF EXISTS bayesian_beliefs_archetype_param_name_key;
    EXCEPTION WHEN undefined_object THEN NULL;
    END $$
    """,
    """
    DO $$ BEGIN
        ALTER TABLE bayesian_beliefs ADD CONSTRAINT bayesian_beliefs_arch_strat_param_uniq
        UNIQUE (archetype, strategy, param_name);
    EXCEPTION WHEN duplicate_table THEN NULL;
    END $$
    """,
    # 3. 扩展 experience_replay
    "ALTER TABLE experience_replay ADD COLUMN IF NOT EXISTS strategy varchar DEFAULT 'S2'",
    # 4. 扩展 param_library（为 Phase 2 准备）
    "ALTER TABLE param_library ADD COLUMN IF NOT EXISTS parent_version varchar",
    "ALTER TABLE param_library ADD COLUMN IF NOT EXISTS strategy varchar DEFAULT 'S2'",
    "ALTER TABLE param_library ADD COLUMN IF NOT EXISTS converge_status varchar DEFAULT 'training'",
    "ALTER TABLE param_library ADD COLUMN IF NOT EXISTS last_trained_at timestamptz",
    "ALTER TABLE param_library ADD COLUMN IF NOT EXISTS discrimination float8",
    "ALTER TABLE param_library ADD COLUMN IF NOT EXISTS consecutive_days int DEFAULT 0",
    # 唯一约束: 每个 (archetype, strategy, version) 只能有一条记录
    "CREATE UNIQUE INDEX IF NOT EXISTS param_library_arch_strat_ver_uniq ON param_library (archetype, strategy, version)",
]

async def migrate():
    for i, sql in enumerate(MIGRATIONS):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
            print(f"  [{i+1}/{len(MIGRATIONS)}] OK")
        except Exception as e:
            print(f"  [{i+1}/{len(MIGRATIONS)}] SKIP: {str(e)[:80]}")

# ── 股票 → 8 原型分配 ──────────────────────

ARCHETYPE_RULES = {
    "大金融":   ["银行", "保险", "证券", "多元金融"],
    "大消费":   ["白酒", "食品", "饮料", "家电", "家居", "纺织服饰", "旅游", "酒店餐饮", "农林牧渔"],
    "硬科技":   ["半导体", "元器件", "通信设备", "电脑设备", "机械基件"],
    "软科技":   ["软件服务", "互联网", "IT设备", "电信运营", "传媒娱乐"],
    "医药健康": ["化学制药", "生物制药", "医疗保健", "医药商业", "中药"],
    "能源材料": ["石油", "煤炭", "有色", "钢铁", "化工", "化纤", "矿物制品", "造纸"],
    "基建公用": ["电力", "水务", "供气供热", "路桥", "港口", "空运", "房产", "建筑", "建材", "装修装饰", "环境保护"],
}

def classify_archetype(industry: str) -> str:
    """Tushare industry → archetype."""
    if not industry: return "中小成长"
    for arch, keywords in ARCHETYPE_RULES.items():
        for kw in keywords:
            if kw in industry:
                return arch
    return "中小成长"

async def assign_archetypes():
    from app.services.tushare_common import call_tushare

    # 获取全市场股票
    stocks = await call_tushare("stock_basic", {"list_status": "L"}, "ts_code,name,industry")
    if not stocks:
        print("ERROR: 无法获取 stock_basic")
        return

    # 分配原型
    results = {}
    for s in stocks:
        arch = classify_archetype(s.get("industry", "") or "")
        results.setdefault(arch, []).append(s["ts_code"])

    print(f"\n原型分配结果 ({len(stocks)} 只股票):")
    print(f"{'原型':<12} {'股数':>6} {'可训练?':>8}")
    print("-" * 30)

    # 更新 archetype_profiles
    async with async_session_factory() as s:
        # 先清空旧的原型定义
        await s.execute(text("DELETE FROM archetype_profiles"))

        for arch, syms in sorted(results.items(), key=lambda x: -len(x[1])):
            count = len(syms)
            trainable = count >= 100
            flag = "YES" if trainable else "NO (合并)"
            print(f"{arch:<12} {count:>6} {flag:>8}")

            await s.execute(text("""
                INSERT INTO archetype_profiles (id, archetype, label, description, sample_count, is_trainable, effective_date, created_at, updated_at)
                VALUES (gen_random_uuid(), :a, :l, :d, :c, :t, CURRENT_DATE, NOW(), NOW())
            """), {
                "a": arch, "l": arch, "d": f"{arch}原型 (Phase 1 自动分配)",
                "c": count, "t": trainable,
            })

        await s.commit()

    # 统计
    trainable_archs = sum(1 for syms in results.values() if len(syms) >= 100)
    print(f"\n可独立训练原型: {trainable_archs}/{len(results)}")
    print("原型分配完成。请重启后端以加载新的 archetype_profiles。")

async def main():
    print("=== Phase 1: 数据库迁移 ===")
    await migrate()

    print("\n=== Phase 1: 股票→原型分配 ===")
    await assign_archetypes()

if __name__ == "__main__":
    asyncio.run(main())
