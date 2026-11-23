import asyncio
import html
import os
import re
import time
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


def clean_html_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    # 移除 HTML 標籤並還原跳脫字元
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    # 合併多餘空白與換行
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_headlines(limit: int = 10, summary_max_len: int = 150) -> str:
    items = []
    seen_titles = set()

    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for entry in feed.entries[:8]:
            title = clean_html_text(entry.get("title", ""))
            pub_date = clean_html_text(entry.get("published", ""))
            raw_summary = entry.get("summary") or entry.get("description") or ""
            summary = clean_html_text(raw_summary)

            # 若摘要跟標題幾乎一樣，就不再重複貼一次
            if summary and summary.lower() == title.lower():
                summary = ""

            if len(summary) > summary_max_len:
                summary = summary[:summary_max_len].rstrip() + "..."

            if title and title not in seen_titles:
                seen_titles.add(title)
                line = f"- 標題：{title}"
                if pub_date:
                    line += f"（時間：{pub_date}）"
                if summary:
                    line += f"\n  摘要：{summary}"
                items.append(line)

            if len(items) >= limit:
                break
        if len(items) >= limit:
            break

    return "\n".join(items)


def clean_script_for_weekend(text: str, is_weekend: bool) -> str:
    if not is_weekend:
        return text
    # 避免週末語音稿出現「今日開盤／盤前」等誤導詞彙
    cleaned = re.sub(r"(今日|今天)(台股|美股|股市)?(即將|準備)?(開盤|盤前)", "本週市場", text)
    return cleaned


def generate_script(news_digest: str, now_tw: datetime) -> str:
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

請根據以下財經新聞標題與摘要，寫一份約 550 到 750 字（朗讀約 2.5 到 3.5 分鐘）的繁體中文晨報口語稿。
要求：
1. 依據標題與摘要的事實脈絡撰寫，不要自行腦補未提到的數字或事件。
2. 專為「語音朗讀」撰寫：使用自然口語、完整句子，不要用 Markdown 星號、條列符號或難唸的縮寫堆砌；股票代號或英文專有名詞請附上好唸的中文稱呼（例如特斯拉、輝達、標普五百、那斯達克）。
3. 開頭問候早安並清楚說出今天是 {today_str} {weekday_str}，結尾提醒投資人留意風險與市場波動。

近期財經新聞與摘要：
{news_digest}
"""
    # 修正模型備用清單：使用現行支援的 gemini-3.8-flash, gemini-3.5-flash 與 gemini-3.5-lite
    models_to_try = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-lite"]

    for model_name in models_to_try:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                raw_text = resp.text.strip()
                return clean_script_for_weekend(raw_text, is_weekend)
            except Exception as e:
                print(f"嘗試使用 {model_name} (第 {attempt + 1} 次) 失敗: {e}")
                time.sleep(10)  # 遇到忙碌時等待 10 秒再重試

    raise RuntimeError("無法從 Gemini API 取得回覆，請稍後重試。")


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
    news_digest = fetch_headlines()
    print("=== FETCHED NEWS DIGEST ===")
    print(news_digest)
    script = generate_script(news_digest, now_tw)
    asyncio.run(text_to_speech(script))
    send_telegram(script)


if __name__ == "__main__":
    main()
