"""宏观因子→板块暴露系数矩阵. 纯数据定义, 零 IO (M-2).

27 板块 × 30 因子 + 12 商品 × 84 链路.
"""
import logging

logger = logging.getLogger(__name__)

SECTOR_FACTOR_EXPOSURE = {
    # === 金融 (3个) ===
    "银行":       {"shibor_spread": 1.5, "lpr_1y": 1.0, "m2_yoy": 1.0, "bond_10y_yield": 0.5, "margin_balance": 0.5},
    "非银金融":   {"shibor_spread": 1.0, "margin_balance": 1.5, "bond_10y_yield": 0.5},
    "综合金融":   {"shibor_spread": 0.8, "margin_balance": 1.0, "m2_yoy": 0.5},

    # === 周期资源 (5个) ===
    "有色金属":   {"copper": 2.0, "aluminum": 1.5, "gold": 1.5, "lithium": 1.0, "ppi_producer": 1.5, "crude_oil": -0.5},
    "煤炭":       {"crude_oil": 1.0, "coke_coal": 2.0, "ppi_producer": 1.5},
    "钢铁":       {"iron_ore": 2.0, "rebar": 1.5, "coke_coal": 1.0, "m2_yoy": 1.0},
    "石油石化":   {"crude_oil": 2.5, "methanol": 1.0, "ppi_producer": 1.0, "cny_usd": -0.5},
    "基础化工":   {"crude_oil": 1.5, "methanol": 1.5, "pvc": 1.0, "ppi_producer": 1.0},

    # === 中游制造 (4个) ===
    "电力设备":   {"copper": 1.5, "lithium": 1.5, "silicon": 1.0, "aluminum": 1.0},
    "机械设备":   {"rebar": 1.0, "copper": 0.5, "pmi_new_order": 1.5, "m2_yoy": 0.5},
    "汽车":       {"lithium": 2.0, "aluminum": 1.0, "natural_rubber": 1.0, "rebar": 0.5, "shibor_spread": -1.0, "pmi_new_order": 1.0},
    "国防军工":   {"pmi_new_order": 0.5, "bond_10y_yield": -0.3},

    # === 消费 (8个) ===
    "食品饮料":   {"cpi_core": 1.5, "cny_usd": -0.5},
    "家用电器":   {"copper": 0.5, "aluminum": 0.3, "ppi_producer": -0.5, "shibor_spread": 0.5},
    "纺织服饰":   {"cny_usd": 1.0, "crude_oil": -0.5},
    "社会服务":   {"cpi_core": 0.5, "cny_usd": 0.3},
    "商贸零售":   {"cpi_core": 0.5, "m2_yoy": 0.3},
    "农林牧渔":   {"cpi_core": 1.0, "methanol": -0.3},
    "美容护理":   {"cpi_core": 0.5},
    "轻工制造":   {"cny_usd": 0.5, "crude_oil": -0.3, "pvc": 0.5},

    # === 科技 (4个) ===
    "电子":       {"copper": 1.0, "pmi_new_order": 1.5, "pmi_export_order": 1.0, "shibor_spread": -1.0, "cny_usd": 1.0},
    "计算机":     {"shibor_spread": -1.5, "pmi_new_order": 0.5, "cny_usd": 0.5},
    "通信":       {"pmi_new_order": 0.5, "shibor_spread": -0.5},
    "传媒":       {"shibor_spread": -1.0, "cny_usd": 0.3, "cpi_core": 0.3},

    # === 基建/地产 (3个) ===
    "房地产":     {"shibor_spread": -2.0, "lpr_5y": -1.5, "m2_yoy": 1.5, "rebar": 1.0, "bond_10y_yield": -1.0},
    "建筑装饰":   {"rebar": 1.5, "m2_yoy": 1.0, "shibor_spread": -0.5, "pvc": 0.5},
    "建筑材料":   {"rebar": 1.0, "pvc": 0.5, "m2_yoy": 1.0, "crude_oil": -0.5},

    # === 公用+其他 (5个) ===
    "公用事业":   {"shibor_spread": 0.5, "bond_10y_yield": -0.5, "crude_oil": -0.5},
    "交通运输":   {"crude_oil": -1.5, "cny_usd": 0.5},
    "综合":       {},
    "医药生物":   {"shibor_spread": 0.3},
    "环保":       {"m2_yoy": 0.5, "shibor_spread": -0.3},
}

COMMODITY_SECTOR_EXPOSURE = {
    "crude_oil": {"石油石化": 2.5, "基础化工": 1.5, "交通运输": -1.5, "煤炭": 1.0, "建筑材料": -0.5, "汽车": -0.5, "纺织服饰": -0.5, "公用事业": -0.5, "轻工制造": -0.3, "有色金属": -0.5},
    "copper": {"有色金属": 2.0, "电力设备": 1.5, "电子": 1.0, "家用电器": 0.5, "机械设备": 0.5, "汽车": 0.5},
    "aluminum": {"有色金属": 1.5, "汽车": 1.0, "电力设备": 1.0, "家用电器": 0.3, "建筑装饰": 0.5, "交通运输": 0.5},
    "rebar": {"钢铁": 1.5, "建筑装饰": 1.5, "建筑材料": 1.0, "机械设备": 1.0, "汽车": 0.5, "房地产": 1.0},
    "iron_ore": {"钢铁": 2.0, "建筑材料": 0.5},
    "coke_coal": {"钢铁": 1.0, "煤炭": 2.0, "公用事业": 0.3},
    "lithium": {"电力设备": 1.5, "汽车": 2.0, "有色金属": 1.0, "基础化工": 0.5},
    "silicon": {"电力设备": 1.0, "有色金属": 0.5, "建筑材料": 0.5},
    "gold": {"有色金属": 1.5, "商贸零售": 0.5},
    "natural_rubber": {"汽车": 1.0, "交通运输": 0.5, "煤炭": -0.3, "纺织服饰": -0.3},
    "methanol": {"基础化工": 1.5, "石油石化": 1.0, "农林牧渔": -0.3},
    "pvc": {"基础化工": 1.0, "建筑材料": 0.5, "建筑装饰": 0.5, "轻工制造": 0.5},
}

COMMODITY_NAMES = list(COMMODITY_SECTOR_EXPOSURE.keys())

DEFAULT_EXPOSURE = {"pmi_new_order": 0.3, "m2_yoy": 0.2, "shibor_spread": -0.2, "cpi_core": 0.2}


def get_sector_exposure(sector: str) -> dict:
    """获取板块对宏观因子的暴露系数."""
    return SECTOR_FACTOR_EXPOSURE.get(sector, DEFAULT_EXPOSURE)


def get_commodity_affected_sectors(commodity: str) -> list[tuple[str, float]]:
    """获取商品影响的板块及强度, 按绝对值降序."""
    mapping = COMMODITY_SECTOR_EXPOSURE.get(commodity, {})
    return sorted(mapping.items(), key=lambda x: abs(x[1]), reverse=True)


def compute_sector_score(sector: str, factor_values: dict[str, float]) -> float:
    """计算板块的综合宏观得分 = Σ(因子值 × 暴露系数)."""
    exposure = get_sector_exposure(sector)
    return sum(factor_values.get(name, 0) * weight for name, weight in exposure.items())
