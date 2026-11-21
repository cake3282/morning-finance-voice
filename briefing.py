import asyncio
import os
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


def generate_script(headlines: str) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
你是每天早上七點播報的財經主播。請根據以下最新財經標題，寫一份約 550 到 750 字（朗讀約 2.5 到 3.5 分鐘）的繁體中文晨報口語稿。
要求：
1. 重點涵蓋：隔夜美股與科技/半導體焦點、台股盤前觀察與重要政經或匯率訊號。
2. 專為「語音朗讀」撰寫：使用自然口語、完整句子，不要用 Markdown 星號、條列符號或難唸的縮寫堆砌；股票代號或英文專有名詞請附上好唸的中文稱呼（例如特斯拉、輝達、標普五百、那斯達克）。
3. 開頭簡短問候早安，結尾提醒投資人留意風險與市場波動。

今日財經標題：
{headlines}
"""
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
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
    headlines = fetch_headlines()
    script = generate_script(headlines)
    asyncio.run(text_to_speech(script))
    send_telegram(script)


if __name__ == "__main__":
    main()
