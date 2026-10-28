"""
通用工具函数
"""
import re
import logging
from datetime import datetime
from typing import Optional, Any, Union
import pandas as pd

logger = logging.getLogger(__name__)


def safe_int(val: Any, default: int = 0) -> int:
    """安全转换为整数"""
    if val is None or (isinstance(val, float) and pd.isna(val)) or val == '':
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def safe_float(val: Any, default: float = 0.0) -> float:
    """安全转换为浮点数"""
    if val is None or (isinstance(val, float) and pd.isna(val)) or val == '':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_str(val: Any, default: str = '') -> str:
    """安全转换为字符串"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip()


def safe_datetime(val: Any, default: Optional[datetime] = None) -> Optional[datetime]:
    """Parse known dates without inventing a timestamp for missing data."""
    try:
        if val is None or pd.isna(val):
            return default
        if isinstance(val, datetime):
            return val
        val_str = str(val).strip()
        if val_str:
            try:
                return datetime.fromisoformat(val_str[:-1] + '+00:00' if val_str.endswith('Z') else val_str)
            except ValueError:
                pass
            formats = [
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d',
                '%d/%m/%Y',
                '%m/%d/%Y',
                '%Y/%m/%d',
                '%Y-%m-%d %H:%M:%S.%f',
                '%d-%m-%Y',
                '%d-%m-%Y %H:%M:%S'
            ]
            
            for fmt in formats:
                try:
                    return datetime.strptime(val_str, fmt)
                except ValueError:
                    continue
    except Exception:
        pass
    
    return default


def extract_price(price_str: str) -> int:
    """Return an unambiguous whole weekly amount; 0 means unknown."""
    if price_str is None:
        return 0
    text = str(price_str).strip().lower()
    amounts = list(re.finditer(
        r'(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.,])', text))
    if not 1 <= len(amounts) <= 2 or re.search(r'(?<!\w)[-−]\s*\$?\s*\d', text):
        return 0
    periods = {
        'week': r'(?:per|a)\s*(?:week|wk)|weekly|p\.?\s*w\.?|p/w|/\s*(?:week|wk|w)',
        'month': r'(?:per|a)\s*(?:calendar\s+)?month|monthly|p\.?\s*c\.?\s*m\.?|p\.?\s*m\.?|p/m|/\s*(?:month|mth|mo|m)',
        'day': r'(?:per|a)\s*(?:day|night)|daily|nightly|p\.?\s*d\.?|p/d|/\s*(?:day|night|d)',
        'year': r'(?:per|a)\s*(?:calendar\s+)?(?:year|annum)|yearly|annual(?:ly)?|p\.?\s*a\.?|p/a|/\s*(?:year|yr|y)',
        'other': r'fortnight(?:ly)?|(?:per|an?)\s*hour|hourly|p\.?\s*f\.?',
    }

    def units(segment):
        return {name for name, pattern in periods.items()
                if re.search(r'(?<![a-z])(?:' + pattern + r')(?![a-z])', segment)}

    # Reject partial numeric tokens such as scientific notation or typos, while
    # still supporting the common compact forms "$530pw" and "$2,303pcm".
    for amount in amounts:
        suffix = text[amount.end():]
        if suffix[:1].isalpha() and not any(
                re.match(r'(?:' + pattern + r')(?![a-z])', suffix) for pattern in periods.values()):
            return 0
    if len(amounts) == 1:
        if units(text) - {'week'}:
            return 0
        amount = amounts[0]
    else:
        between = text[amounts[0].end():amounts[1].start()]
        if (re.search(r'[-–—+]|\b(?:to|plus)\b', between)
                or not re.search(r'[/(),=]|\bor\b', between)):
            return 0
        # Only the explicit weekly/monthly rendering of the same quote is
        # supported. Ranges, multiple weekly prices and unlabelled fees remain
        # unknown rather than becoming an invented scalar price.
        first_units = units(between)
        second_units = units(text[amounts[1].end():])
        if ({frozenset(first_units), frozenset(second_units)}
                != {frozenset({'week'}), frozenset({'month'})}):
            return 0
        amount = amounts[0] if first_units == {'week'} else amounts[1]
    whole, _, fraction = amount.group().replace(',', '').partition('.')
    return int(whole) if not fraction.strip('0') else 0


def extract_number(text: str) -> int:
    """从字符串中提取数字"""
    if not text:
        return 0
    
    try:
        match = re.search(r'(\d+)', str(text))
        if match:
            return int(match.group(1))
    except (ValueError, TypeError):
        pass
    
    return 0


def clean_address(address: str) -> str:
    """
    清理地址字符串
    - 移除逗号
    - 转小写
    - 将斜杠和空格替换为连字符
    """
    if not address:
        return ""
    
    return (
        str(address)
        .replace(',', '')
        .lower()
        .replace('/', '-')
        .replace(' ', '-')
    )


def parse_available_date(date_text: str) -> Optional[datetime]:
    """
    解析可用日期
    支持格式如 "Available Now", "Available from Monday, 15 January 2024"
    """
    if not date_text:
        return None
    
    date_text = str(date_text).strip()
    
    if re.fullmatch(
            r'(?:(?:available(?:\s+from)?(?:\s+|\s*[:-]\s*))?(?:now|immediately)'
            r'|(?:now|immediately)\s+available)[.!]?', date_text, re.I):
        return datetime.now()
    
    date_text = re.sub(r'^available(?:\s+from)?\s*', '', date_text, flags=re.I).strip()
    
    try:
        # 移除序数后缀 (1st, 2nd, 3rd, 4th, etc.)
        cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_text)
        
        # 尝试多种日期格式
        formats = [
            "%A, %d %B %Y",  # Monday, 15 January 2024
            "%d %B %Y",     # 15 January 2024
            "%d %b %Y",     # 18 Nov 2026
            "%d/%m/%Y",     # 15/01/2024
            "%Y-%m-%d",     # 2024-01-15
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
    except Exception as e:
        logger.debug(f"解析日期失败: {date_text}, 错误: {e}")
    
    return None


def is_valid_image_url(img_url: str) -> bool:
    """
    检查图片 URL 是否有效
    排除嵌套 URL 等异常情况
    """
    if not img_url or not img_url.startswith('http'):
        return False
    
    # 排除嵌套 URL
    http_count = img_url.count('http://') + img_url.count('https://')
    return http_count == 1


def generate_house_id(address: str, postcode: str) -> str:
    """生成房源唯一标识"""
    combined = f"{address}{postcode}".lower().replace(' ', '')
    return str(abs(hash(combined)) % (10**9))


def truncate_string(text: str, max_length: int) -> str:
    """截断字符串到指定长度"""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length]

