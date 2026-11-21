import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import edge_tts
import feedparser
import requests
from google import genai

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^IXIC,NVDA,TSLA&region=US&lang=en-US",
    "https://news.google.com/rss/search?q=%E5%8F%B0%E8%82%A1+%E7%BE%8E%E8%82%A1+%E5%8D%8A%E5%B0%8E%E9%AB%94&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
]


def fetch_headlines(limit: int = 12) -> str:
    items = []
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for entry in feed.entries[:6]:
            title = entry.get("title", "").strip()
            if title and title not in items:
                items.append(title)
    return "\n".join(f"- {t}" for t in items[:limit])


def generate_script(headlines: str, now_tw: datetime) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    weekday_map = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    today_str = now_tw.strftime("%Y年%m月%d日")
    weekday_str = weekday_map[now_tw.weekday()]
    is_weekend = now_tw.weekday() >= 5

    if is_weekend:
        mode_instruction = (
            "今天是週末（休市日），台股與美股今日不開盤。"
            "請改以「本週市場重點回顧」與「下週觀察方向」為主，"
            "不要說今天盤前或即將開盤，也不要把舊聞誤當成今天剛發生的突發消息。"
        )
    else:
        mode_instruction = (
            "今天是交易日早晨，請聚焦隔夜美股與科技/半導體焦點、"
            "台股盤前觀察與重要政經或匯率訊號。"
        )

    prompt = f"""
你是每天早上七點播報的財經主播。現在時間是台灣時間 {today_str}（{weekday_str}）。
{mode_instruction}

請根據以下最新財經標題，寫一份約 550 到 750 字（朗讀約 2.5 到 3.5 分鐘）的繁體中文晨報口語稿。
要求：
1. 專為「語音朗讀」撰寫：使用自然口語、完整句子，不要用 Markdown 星號、條列符號或難唸的縮寫堆砌；股票代號或英文專有名詞請附上好唸的中文稱呼（例如特斯拉、輝達、標普五百、那斯達克）。
2. 若標題內容偏舊或與日期有落差，請用「近期市場焦點」或「本週觀察」帶過，避免時間錯亂。
3. 開頭簡短問候早安並點出今天日期與星期幾，結尾提醒投資人留意風險與市場波動。

近期財經標題：
{headlines}
"""
    resp = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
    )
    return resp.text.strip()


async def text_to_speech(text: str, mp3_path: str = "briefing.mp3") -> None:
    communicate = edge_tts.Communicate(text=text, voice="zh-TW-HsiaoChenNeural")
    await communicate.save(mp3_path)


def send_telegram(text: str, mp3_path: str = "briefing.mp3") -> None:
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    requests.post(
        f"{base}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text[:4000]},
        timeout=30,
    ).raise_for_status()

    with open(mp3_path, "rb") as audio:
        requests.post(
            f"{base}/sendVoice",
            data={"chat_id": CHAT_ID},
            files={"voice": ("briefing.mp3", audio, "audio/mpeg")},
            timeout=60,
        ).raise_for_status()


def main() -> None:
    now_tw = datetime.now(ZoneInfo("Asia/Taipei"))
    headlines = fetch_headlines()
    script = generate_script(headlines, now_tw)
    asyncio.run(text_to_speech(script))
    send_telegram(script)


if __name__ == "__main__":
    main()
