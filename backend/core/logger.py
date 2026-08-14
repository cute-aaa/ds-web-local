"""日志系统：loguru + 敏感信息脱敏 + 结构化文件日志。"""
import sys
import re
from loguru import logger

# 敏感字段脱敏规则（顺序匹配）
_SENSITIVE_PATTERNS = [
    (re.compile(r'(?i)(token|password|authorization|api_key|apikey|secret)\s*[=:]\s*([^\s,"\'}\]<]+)'), r'\1=***'),
    (re.compile(r'(?i)("command"\s*:\s*")[^"]*(")'), r'\1***\2'),
]


def _mask(record):
    msg = record["message"]
    for pat, repl in _SENSITIVE_PATTERNS:
        msg = pat.sub(repl, msg)
    record["message"] = msg


def setup_logger(level="INFO", log_file="logs/app.log", rotation="10 MB", retention="30 days"):
    logger.remove()
    fmt_console = ("<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                   "<level>{level: <8}</level> | "
                   "<cyan>{name}</cyan> - <level>{message}</level>")
    fmt_file = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name} | {message}"

    logger.add(sys.stdout, format=fmt_console, level=level, colorize=True, filter=_mask)
    logger.add(log_file, format=fmt_file, level=level, rotation=rotation,
               retention=retention, encoding="utf-8", filter=_mask)
    return logger


def get_logger(name=None):
    return logger.bind(name=name or "root")
