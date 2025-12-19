import re

# Telegram MarkdownV2: these must be escaped in most places
_MD2_SPECIALS = r"_*\[\]()~`>#+\-=|{}\.!"
_MD2_RE = re.compile(rf"([\\{re.escape(_MD2_SPECIALS)}])")
def tg_md2_escape(text: str) -> str:
    """Escape text for Telegram parse_mode='MarkdownV2' (general text)."""
    return _MD2_RE.sub(r"\\\1", text)
