"""A synthetic, offline treasury workflow prototype."""

from .models import Account, ParseResult, Statement, Transaction
from .parsers import parse_bai2, parse_camt053, parse_mt940

__all__ = ["Account", "ParseResult", "Statement", "Transaction",
           "parse_bai2", "parse_camt053", "parse_mt940"]
__version__ = "0.1.0"
