from html import escape
from glossary import GLOSSARY, DEFAULT

def _fmt_price(value):
    try:
        v = float(value)
        absolute = abs(v)
        if absolute >= 1_000_000_000:
            return f"{v / 1_000_000_000:,.2f}".rstrip("0").rstrip(".") + "B"
        if absolute >= 1_000_000:
            return f"{v / 1_000_000:,.2f}".rstrip("0").rstrip(".") + "M"
        if absolute >= 1000:
            return f"{v / 1_000:,.2f}".rstrip("0").rstrip(".") + "K"
        if v >= 1:
            return f"{v:,.4f}".rstrip("0").rstrip(".")
        return f"{v:.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value or "-")

def build_message(payload: dict) -> str:
    event = str(payload.get("event", "")).strip()
    info = GLOSSARY.get(event, DEFAULT)

    symbol = escape(str(payload.get("symbol", "UNKNOWN")))
    timeframe = escape(str(payload.get("timeframe", "-")))
    price = escape(_fmt_price(payload.get("price")))
    exchange = escape(str(payload.get("exchange", "")))
    source = f"{exchange}:{symbol}" if exchange else symbol

    trendline_price = payload.get("trendline_price")
    line_text = ""
    if trendline_price is not None:
        line_text = f"\n<b>추세선 가격</b>: {escape(_fmt_price(trendline_price))}"

    return (
        f"<b>{info['title']}</b>\n\n"
        f"<b>종목</b>: {source}\n"
        f"<b>시간봉</b>: {timeframe}\n"
        f"<b>현재가</b>: {price}"
        f"{line_text}\n\n"
        f"<b>현재 해석</b>\n{info['bias']}\n{escape(info['meaning'])}\n\n"
        f"<b>용어 설명 — {escape(info['term'])}이란?</b>\n"
        f"{escape(info['definition'])}\n\n"
        f"<b>다음 확인</b>\n{escape(info['check'])}\n\n"
        "⚠️ 이 알림은 기술적 분석 보조용이며 매수·매도 확정 신호가 아닙니다."
    )
