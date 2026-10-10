"""Parser registry。各家 parser 在此註冊。"""
from __future__ import annotations

from mailquill.gmail_message import EmailMessage
from mailquill.parsers.base import Parser

_PARSERS: list[Parser] = []


def register(parser: Parser) -> None:
    _PARSERS.append(parser)


def get_parser(msg: EmailMessage) -> Parser | None:
    for p in _PARSERS:
        if p.matches(msg):
            return p
    return None


def all_parsers() -> list[Parser]:
    return list(_PARSERS)


# 註冊內建 parser（真實銀行 parser 在取得樣本後逐一加入）
from mailquill.config import load_config  # noqa: E402
from mailquill.parsers.example_bank import ExampleBankParser  # noqa: E402
from mailquill.parsers.cathay import CathayParser  # noqa: E402
from mailquill.parsers.ubot import UnionBankParser  # noqa: E402
from mailquill.parsers.taishin import TaishinParser  # noqa: E402
from mailquill.parsers.fubon import FubonParser  # noqa: E402
from mailquill.parsers.esun import EsunParser  # noqa: E402
from mailquill.parsers.sinopac import SinoPacParser  # noqa: E402
from mailquill.parsers.hsbc import HSBCParser  # noqa: E402
from mailquill.parsers.ctbc import CtbcParser  # noqa: E402


def _fubon_rent_signature() -> tuple[str, ...]:
    """從 config.yaml 讀 fubon_rent_dest_signature；沒有設定/沒有 config.yaml 就停用。"""
    try:
        return tuple(load_config().fubon_rent_dest_signature)
    except Exception:
        return ()


register(CathayParser())
register(UnionBankParser())
register(TaishinParser())
register(FubonParser(rent_dest_signature=_fubon_rent_signature()))
register(EsunParser())
register(SinoPacParser())
register(HSBCParser())
register(CtbcParser())
register(ExampleBankParser())
