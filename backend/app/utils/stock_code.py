"""统一股票代码规范化 (P0 系统级).

消除 9 处分散的 startswith('6')->.SH / startswith('0','3')->.SZ 复制。

支持:
  - 6xxxxx → .SH (上海主板 + 科创板 688xxx)
  - 0xxxxx/3xxxxx/2xxxxx → .SZ (深圳主板/中小板/创业板/B股)
  - 8xxxxx/4xxxxx/920xxx → .BJ (北交所, 含新代码段)

核心函数:
  normalize_ts_code(code) → 'XXXXXX.SH|SZ|BJ'
  strip_suffix(ts_code)   → 'XXXXXX'
  classify_board(ts_code) → '科创板'/'创业板'/...
"""
import re

_CODE_PREFIX_MAP = [
    (('6',), '.SH'),
    (('0', '3', '2'), '.SZ'),
    (('8', '4', '9'), '.BJ'),
]

_TS_CODE_RE = re.compile(r'^\d{6}\.(SH|SZ|BJ)$')


def normalize_ts_code(code: str) -> str:
    """规范化为 ts_code 格式 (XXXXXX.SH|SZ|BJ).

    Args:
        code: 裸代码 ('600660') 或已有后缀 ('002594.SZ')

    Returns:
        规范化后的代码, 无法规范化时返回原值

    Examples:
        normalize_ts_code('600660')   → '600660.SH'
        normalize_ts_code('300750')   → '300750.SZ'
        normalize_ts_code('920123')   → '920123.BJ'
        normalize_ts_code('002594.SZ') → '002594.SZ'
        normalize_ts_code('')         → ''
    """
    if not code:
        return code

    code = code.strip().upper()

    # 已有后缀 → 校验
    if '.' in code:
        if _TS_CODE_RE.match(code):
            return code
        return code  # 非标准后缀, 不变

    # 6 位纯数字裸代码
    if code.isdigit() and len(code) == 6:
        first_digit = code[0]
        for prefixes, suffix in _CODE_PREFIX_MAP:
            if first_digit in prefixes:
                return code + suffix

    return code


def strip_suffix(ts_code: str) -> str:
    """去除交易所后缀 → 纯 6 位代码."""
    if '.' in ts_code:
        return ts_code.split('.')[0]
    return ts_code


def classify_board(ts_code: str) -> str:
    """从代码判断所属板块."""
    bare = strip_suffix(ts_code)
    if bare.startswith('688'):
        return '科创板'
    if bare.startswith(('300', '301')):
        return '创业板'
    if bare.startswith(('002', '003')):
        return '中小板'
    if bare.startswith('60'):
        return '上海主板'
    if bare.startswith(('00', '001')):
        return '深圳主板'
    if bare.startswith(('8', '4', '920')):
        return '北交所'
    return '未知'
