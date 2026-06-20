# 残留问题清单（Phase 8+ 统一修复）

> 主线 Phase 1-7 验证过程中发现的所有小问题，按优先级排列。

---

## RESIDUAL-001: `_ambush_cache` 声明两次

- **来源**: Phase 1 验证
- **文件**: `app/services/data_preloader.py`
- **行号**: 9 和 29
- **现象**: `_ambush_cache: dict[str, float] = {}` 在 `preload_ambush` 函数前后各声明一次
- **修复**: 删除行 29 的重复声明

## RESIDUAL-002: `_pattern_cache` 声明在函数之后

- **来源**: Phase 1 验证
- **文件**: `app/services/data_preloader.py`
- **行号**: 50（声明）vs 33（函数引用 global）
- **现象**: 声明在函数定义之后，与 `_ambush_cache`（行 9）风格不一致
- **修复**: 将 `_pattern_cache` 声明移到 `preload_patterns` 函数之前

## RESIDUAL-003: `alphaflow_pool_service.py` 是空心 stub ✅ RESOLVED v4.7

- **来源**: Phase 5 验证
- **文件**: `app/services/alphaflow_pool_service.py`
- **状态**: ✅ 已于 v4.7 解决 — 信号计算/Big Fairy/两期扫描全部在该文件中实现

## RESIDUAL-004: `holdings.py` auto_holding_strategy 未提取

- **来源**: Phase 5 验证
- **文件**: `app/api/holdings.py` 行 526-624（99 行）
- **现象**: 板块集中度 + 逐股策略生成已在 `holding_strategy.py` 但 API 层仍有大量内联业务逻辑
- **修复**: 将行 543-624 的集中度检测 + 逐股策略移入 `holding_strategy.py` 的 `generate_holding_strategies()` 函数

## RESIDUAL-005: `scan.py` get_today_events 未提取

- **来源**: Phase 5 验证
- **文件**: `app/api/scan.py` 行 511-614（104 行）
- **现象**: HK→A 匹配 + 事件衰减 + 行业去重逻辑仍在 API 内，`event_aggregator.py` 有函数但未被调用
- **修复**: 将行 516-608 的业务逻辑移入 `event_aggregator.py`，API handler 改为调用该服务

## RESIDUAL-006: `capital_account` raw SQL CREATE TABLE 未删除

- **来源**: Phase 6 验证
- **文件**: `app/api/holdings.py` 行 48-56
- **现象**: `capital_account` 表已有 ORM 模型 `CapitalAccount`，raw SQL DD L应删除
- **修复**: 删除行 48-56 的 CREATE TABLE，删除 `_ensure_tables()` 的所有调用（约 8 处），改用 `Base.metadata.create_all`

## RESIDUAL-007: `alphaflow_pool` raw SQL CREATE TABLE 未删除

- **来源**: Phase 6 验证
- **文件**: `app/services/alphaflow_pool.py` 行 32-46
- **现象**: `alphaflow_pool` 表已有 ORM 模型 `AlphaflowPool`，raw SQL DDL 应删除
- **修复**: 删除行 32-46 的局部 CREATE TABLE，删除 `create_pool_tables()` 的调用，改用 ORM

## RESIDUAL-008: `goose_archive` CREATE TABLE 仍有两处定义

- **来源**: Phase 6 验证
- **文件**: `app/api/alphaflow.py:107` 和 `app/services/alphaflow_pool.py:690`
- **现象**: 两处 raw SQL CREATE TABLE，且没有对应的 ORM 模型
- **修复**: 在 `data_models.py` 中新增 `GooseArchive(Base)` ORM 模型，删除两处 raw SQL

---

*积累状态: **7 条待处理 + 1 条已解决** — 标记为 Phase 8*
