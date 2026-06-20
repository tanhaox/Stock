针对你的需求，我整理了一份 **Tushare Pro 接口调用参数速查手册**，重点覆盖 8000 积分 + A股历史分钟权限下可用的所有主流接口的 **输入参数** 和 **调用示例**。  
（返回值字段建议配合官方文档查阅，这里以输入参数为主。）

---

## 接口调用参数手册 （8000积分 + 历史分钟权限）

### 基本调用规则
- 所有接口通过 `pro = ts.pro_api('your_token')` 初始化
- 绝大多数接口都支持 `ts_code`、`trade_date`（或 `start_date`/`end_date`） 进行条件筛选
- 不传入日期/代码时，通常返回全量最新数据（注意积分和频次限制）
- 分钟接口需显式传入 `freq` 参数

---

## 一、沪深股票

### 1.1 基础信息
#### `stock_basic` – 股票列表
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| is_hs | str | N | 沪深港通标识：H沪股通/S深股通/N否，默认全部 |
| list_status | str | N | 上市状态：L上市/D退市/P暂停，默认L |
| exchange | str | N | 交易所 SSE上交所/SZSE深交所/BSE北交所 |
| market | str | N | 市场类别：主板/创业板/科创板/北交所 |
| ts_code | str | N | 股票代码（如 000001.SZ） |
| limit | int | N | 单次返回条数 |
| offset | int | N | 偏移量 |

**调用示例**
```python
pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
```

#### `trade_cal` – 交易日历
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| exchange | str | Y | 交易所 SSE上交所/SZSE深交所/CFFEX中金所等 |
| start_date | str | N | 开始日期 YYYYMMDD |
| end_date | str | N | 结束日期 |
| is_open | str | N | 是否交易 1交易/0休市 |

#### `namechange` – 股票曾用名
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 股票代码 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `hs_const` – 沪深股通成份股
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| hs_type | str | Y | SH沪股通/SZ深股通 |
| is_new | str | N | 是否最新 Y是/N否 |

---

### 1.2 行情数据
#### `daily` – 日线行情
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 股票代码 |
| trade_date | str | N | 交易日期 YYYYMMDD |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `weekly` / `monthly` – 周/月线
同上，无额外参数。

#### `adj_factor` – 复权因子
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 股票代码 |
| trade_date | str | N | 交易日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

**调用示例**
```python
pro.adj_factor(ts_code='000001.SZ', start_date='20230101', end_date='20231231')
```

#### `daily_basic` – 每日指标（市值、换手率等）
参数同上 `daily`，无额外参数。

#### `suspend_d` – 停复牌信息
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 股票代码 |
| trade_date | str | N | 交易日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |
| suspend_type | str | N | 停牌类型：S停牌/R复牌 |

---

### 1.3 资金与情绪
#### `moneyflow` – 个股资金流向
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 股票代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `stk_limit` – 涨跌停价格
参数同 `daily`。

#### `limit_list` – 涨跌停板列表
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | str | N | 交易日期 |
| ts_code | str | N | 股票代码 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |
| limit_type | str | N | U涨停/D跌停/Z炸板 |

#### `margin` – 融资融券（标的汇总）
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | str | N | 交易日期 |
| ts_code | str | N | 股票代码 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `margin_detail` – 融资融券明细（逐日明细）
参数同上，增加 `state`（Y当日/N历史），通常不传即可。

#### `top_list` – 龙虎榜明细
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | str | N | 日期 |
| ts_code | str | N | 股票代码 |

#### `top_inst` – 龙虎榜机构席位
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | str | N | 日期 |
| ts_code | str | N | 代码 |

---

### 1.4 股东与质押
#### `stk_holdernumber` – 股东人数
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| end_date | str | N | 截止日期 YYYYMMDD |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `stk_holderstrade` – 股东增减持
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| ann_date | str | N | 公告日期 |
| start_date | str | N | 公告开始日期 |
| end_date | str | N | 公告结束日期 |
| holder_type | str | N | 股东类型 C公司/P个人/G高管 |

#### `pledge_stat` – 股权质押统计
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| end_date | str | N | 截止日期 |

#### `pledge_detail` – 股权质押明细
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | Y | 代码 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `share_float` – 限售解禁
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| ann_date | str | N | 公告日期 |
| float_date | str | N | 解禁日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

---

### 1.5 特色数据
#### `stk_factor_pro` – 技术因子（专业版）
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `block_trade` – 大宗交易
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

---

### 🔥 1.6 A股历史分钟K线 （核心权限接口）
#### `stk_mins` – 沪深股票分钟K线
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | Y* | 股票代码，可留空表示全市场（谨慎） |
| freq | str | Y | K线周期：1min/5min/15min/30min/60min |
| start_date | str | N | 开始日期 格式YYYYMMDD |
| end_date | str | N | 结束日期 |
| offset | int | N | 偏移量 |
| limit | int | N | 返回条数 |

**调用示例（获取平安银行2023年全年1分钟线）**
```python
df = pro.stk_mins(ts_code='000001.SZ', freq='1min', 
                  start_date='20230101', end_date='20231231')
```

> ⚠️ **注意**：该接口单次返回有数据量限制，如需拉取全量历史分钟，建议按日期或代码循环、控制频率。8000积分下无时间范围锁定。

---

## 二、指数

#### `index_basic` – 指数基本信息
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| market | str | N | 市场：SSE/SZSE/MSCI等 |
| publisher | str | N | 发布商 |
| category | str | N | 指数类别 |

#### `index_daily` / `index_weekly` / `index_monthly` – 指数行情
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 指数代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `index_weight` – 指数权重
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| index_code | str | N | 指数代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `index_classify` – 申万行业分类
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| level | str | N | 行业级别 L1/L2/L3 |
| src | str | N | 来源 SW申万（默认） |

#### `index_member` – 指数成份
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| index_code | str | Y | 指数代码 |
| trade_date | str | N | 日期 |
| ts_code | str | N | 成份代码 |

---

## 三、基金

#### `fund_basic` – 基金列表
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| market | str | N | 市场 E场内/O场外 |
| status | str | N | 状态 L上市/D退市 |

#### `fund_daily` – 基金日线
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 基金代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始 |
| end_date | str | N | 结束 |

#### `fund_nav` – 基金净值
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| nav_date | str | N | 净值日期 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

#### `fund_portfolio` – 基金持仓
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | Y | 基金代码 |
| end_date | str | N | 报告期 |

其他基金接口类似，基本都围绕 `ts_code` 和日期。

---

## 四、期货

#### `fut_basic` – 合约列表
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| exchange | str | N | DCE大商所/CZCE郑商所等 |
| fut_type | str | N | 1普通/2主力 |
| ts_code | str | N | 合约代码 |

#### `fut_daily` – 期货日线
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 合约代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始 |
| end_date | str | N | 结束 |
| exchange | str | N | 交易所 |

#### `fut_holding` – 持仓排名
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | str | N | 日期 |
| symbol | str | N | 品种 |
| broker | str | N | 期货公司 |

#### `fut_mapping` – 主力合约映射
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 合约代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始 |
| end_date | str | N | 结束 |

---

## 五、期权

#### `opt_basic` – 期权合约信息
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| exchange | str | N | SSE上交所 |
| ts_code | str | N | 合约代码 |
| call_put | str | N | C认购/P认沽 |

#### `opt_daily` – 期权日线
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 合约代码 |
| trade_date | str | N | 日期 |
| start_date | str | N | 开始 |
| end_date | str | N | 结束 |

> 期权分钟线 `opt_mins` 需要更高积分，8000分无法调用。

---

## 六、可转债/债券

#### `cb_basic` – 可转债列表
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | N | 代码 |
| list_status | str | N | L上市 |

#### `cb_daily` – 可转债日线
参数同 `daily`。

#### `bond_daily` – 债券日线
参数同 `daily`。

---

## 七、宏观经济

#### `cn_cpi` / `cn_ppi` / `cn_m` 等
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| m | str | N | 月份 YYYYMM |
| start_m | str | N | 开始月份 |
| end_m | str | N | 结束月份 |

#### `shibor` – 拆借利率
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| date | str | N | 日期 |
| start_date | str | N | 开始 |
| end_date | str | N | 结束 |

---

## 八、通用行情接口

#### `pro_bar` – 万能行情（整合日/周/月/分钟）
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | Y | 证券代码 |
| api | str | N | 指定接口（如 daily），不指定自动判断 |
| freq | str | N | 周期：D日/W周/M月/1min等分钟 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |
| asset | str | N | 资产类型：E股票/I指数/FT期货等 |
| adj | str | N | 复权：None不复权/qfq前复权/hfq后复权 |

**示例（获取后复权日线）**
```python
pro.pro_bar(ts_code='000001.SZ', start_date='20200101', 
            end_date='20231231', freq='D', adj='hfq', asset='E')
```

**示例（获取分钟线，需分钟权限）**
```python
pro.pro_bar(ts_code='000001.SZ', freq='1min', 
            start_date='20230601', end_date='20230602', asset='E')
```

---

## ⚠️ 关键提示
1. **所有接口的详细输入/输出字段** 以官网文档 https://tushare.pro/document/2?doc_id=14 （及具体接口页）为准，建议对照使用。
2. 若接口返回 `权限不足`，请先在“个人中心-我的权限”中确认对应接口是否点亮；8000积分默认已涵盖 A股分钟权限，但某些细分如期货分钟、期权分钟仍需额外购买。
3. 调用频率限制一般为 200 次/分钟，具体以你账户的权限页为准。
4. 分钟数据体量巨大，建议使用 `start_date` + `end_date` 分段拉取，并善用 `fields` 参数减少流量。

如需某个具体接口的更多参数细节或 Python 拉取模板，可以继续问我。

---

## ⭐ v7.0.34 Exclusion 集成接口（已实测）

| 接口 | 用途 | 字段 | 积分要求 |
|------|------|------|---------|
| `stock_st` | 全市场 ST/*ST/PT 名单 | `ts_code, name, trade_date, type, type_name` | 3000+ |
| `income_vip` | 全市场利润表 (批量) | `ts_code, period, end_date, revenue, n_income, ...` | 5000+ |
| `balancesheet_vip` | 全市场资产负债表 (批量) | `ts_code, period, end_date, total_assets, total_liab, ...` | 5000+ |

**集成位置**: `backend/scripts/refresh_exclusion_list.py` — 季度初跑, 写到 `exclusion_list` 表 5 reasons.

### 11.1 stock_st 接口用法

```python
import tushare as ts
pro = ts.pro_api(token)

# 一次返回全市场 ST/*ST/PT 列表 (含历史, ~1000 行)
df = pro.stock_st(limit=5000)
print(df.head())
#   ts_code   name      trade_date  type    type_name
# 688184.SH  STXX     2026-06-18  ST      风险警示
# 002667.SZ  *STYY    2026-06-18  ST      风险警示

# 注意: stock_st 返回**历史所有 ST 事件**, 需用 trade_date 取最新一天的 232 只作为当前名单
latest = df['trade_date'].max()
current_st = df[df['trade_date'] == latest]
```

**踩坑**: 不要直接用 `len(df)`, 那是历史总数. 必须按 `trade_date == max(trade_date)` 过滤取最新名单.

### 11.2 income_vip 接口用法

```python
# 一次批量拿全市场利润表 (~5000 行, 5000+ 积分)
df = pro.income_vip(period='20260331',
                     fields='ts_code,period,end_date,revenue,operate_profit,n_income',
                     limit=5000)

# 真实亏损股 = n_income < 0
loss_stocks = df[df['n_income'] < 0]['ts_code'].tolist()
# ~1168 只 (vs daily_basic.pe_ttm=0 的 2039, 剔除了 800+ 误判)
```

**踩坑**: 不要用 daily_basic.pe_ttm=0 判断亏损, 那是 Tushare 的简略估算 (PE=0 含微利股). 真实亏损必须用 income.n_income<0.

**季度切换容错**: 当前季度财报未出时 fallback 上季度:
```python
for period in ['20260630', '20260331']:  # 试当前 → fallback
    df = pro.income_vip(period=period, fields='ts_code,period,n_income', limit=5000)
    if len(df) > 100:  # 数据回来了
        break
```

### 11.3 balancesheet_vip 接口用法

```python
# 一次批量拿全市场资产负债表 (~5000 行, 5000+ 积分)
df = pro.balancesheet_vip(period='20260331',
                           fields='ts_code,period,end_date,total_assets,total_liab',
                           limit=5000)

# 资不抵债股 = total_liab > total_assets
insolvent = df[df['total_liab'] > df['total_assets']]
# ~22 只 (主板 / 北交所都有)
```

**用法**: 配合 income_vip 一起用, 更全面识别"问题股".

### 11.4 实测结果 (2026-06-20)

| 接口 | 测试日期 | 返回行数 | 真实亏损/资不抵债/ST |
|------|---------|---------|-------------------|
| `stock_st(limit=5000)` | 2026-06-20 | 1000 (历史) / **225 (当前)** | 225 只 ST/*ST |
| `income_vip(period='20260331')` | 2026-06-20 | 5000 | **1168 只 n_income<0** |
| `balancesheet_vip(period='20260331')` | 2026-06-20 | 5000 | **22 只 total_liab > total_assets** |

集成后 exclusion_list 状态 (5 reasons 跨 reason 去重):
- TECH_BOARD: 599 (688 开头)
- BJ_BOARD: 318 (920 开头)
- ST_NAME: 211 (Tushare stock_st)
- PE_LOSS: 733 (Tushare income_vip)
- INSOLVENT: 2 (Tushare balancesheet_vip, 其它 20 只跨 reason 重复)
- **总踢出**: 1771 只