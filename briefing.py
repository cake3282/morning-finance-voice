import asyncio
import os
import re
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
    "https://news.google.com/rss/search?q=%E5%8F%B0%E8%82%A1+%E7%BE%8E%E8%82%A1+%E5%8D%8A%E5%B0%8E%E9%AB%94+when:2d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
]


def fetch_headlines(limit: int = 12) -> str:
    items = []
    seen_titles = set()
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for entry in feed.entries[:8]:
            title = entry.get("title", "").strip()
            pub_date = entry.get("published", "").strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                if pub_date:
                    items.append(f"- [{pub_date}] {title}")
                else:
                    items.append(f"- {title}")
    return "\n".join(items[:limit])


def clean_script_for_weekend(text: str, is_weekend: bool) -> str:
    if not is_weekend:
        return text
    # 避免週末語音稿出現「今日開盤／盤前」等誤導詞彙
    cleaned = re.sub(r"(今日|今天)(台股|美股|股市)?(即將|準備)?(開盤|盤前)", "本週市場", text)
    return cleaned


def generate_script(headlines: str, now_tw: datetime) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    weekday_map = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    today_str = now_tw.strftime("%Y年%m月%d日")
    weekday_str = weekday_map[now_tw.weekday()]
    is_weekend = now_tw.weekday() >= 5

    if is_weekend:
        mode_instruction = (
            f"今天是 {today_str}（{weekday_str}），屬於週末休市日，台股與美股今天不開盤。"
            "請以「本週市場焦點回顧」與「下週觀察重點」為主。"
            "嚴格禁止說「今天開盤」、「盤前觀察」或把過去日期當成今天。"
        )
    else:
        mode_instruction = (
            f"今天是 {today_str}（{weekday_str}），屬於交易日早晨。"
            "請聚焦隔夜美股與科技/半導體焦點、台股盤前觀察與重要政經或匯率訊號。"
        )

    prompt = f"""
你是每天早上七點播報的財經主播。現在時間是台灣時間 {today_str}（{weekday_str}）。
{mode_instruction}

請根據以下附帶發布時間的財經新聞列表，寫一份約 550 到 750 字（朗讀約 2.5 到 3.5 分鐘）的繁體中文晨報口語稿。
要求：
1. 專為「語音朗讀」撰寫：使用自然口語、完整句子，不要用 Markdown 星號、條列符號或難唸的縮寫堆砌；股票代號或英文專有名詞請附上好唸的中文稱呼（例如特斯拉、輝達、標普五百、那斯達克）。
2. 優先參考日期較新的新聞；若標題有舊日期或與今天日期不同，請自然改用「本週市場焦點」或「近期觀察」敘述，不要照抄舊日期。
3. 開頭問候早安並清楚說出今天是 {today_str} {weekday_str}，結尾提醒投資人留意風險與市場波動。

近期財經新聞（含發布時間）：
{headlines}
"""
    resp = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
    )
    raw_text = resp.text.strip()
    return clean_script_for_weekend(raw_text, is_weekend)


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
