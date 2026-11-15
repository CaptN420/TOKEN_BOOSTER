"""Compression modules."""
from ..adaptive import adaptive_compress
from .json import compress_json, compress_json_landscape
from .markdown import compress_markdown
from .code import compress_code, extract_important_code
from .logs import compress_logs
from .terminal import compress_terminal
from .structured import compress_csv, compress_sql, compress_diff, compress_git_log

__all__ = [
    "adaptive_compress",
    "compress_json",
    "compress_json_landscape",
    "compress_markdown",
    "compress_code",
    "extract_important_code",
    "compress_logs",
    "compress_terminal",
    "compress_csv",
    "compress_sql",
    "compress_diff",
    "compress_git_log",
]
