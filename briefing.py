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

# 拆分為美股與台股獨立 RSS 來源
US_RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^IXIC,NVDA,TSLA,AAPL,MSFT,AVGO,MU,MRVL,AMD&region=US&lang=en-US",
]

TW_RSS_FEEDS = [
    "https://news.google.com/rss/search?q=%E5%8F%B0%E8%82%A1+%E5%8D%8A%E5%B0%8E%E9%AB%94+%E7%B6%93%E6%BF%9F+when:2d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def log(msg: str) -> None:
    now = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def clean_html_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_category_headlines(feeds: list, limit: int = 5, summary_max_len: int = 150) -> list:
    items = []
    seen_titles = set()

    for url in feeds:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            feed = feedparser.parse(resp.content)
        except Exception as e:
            log(f"抓取 RSS 失敗 ({url}): {e}")
            continue

        for entry in feed.entries:
            title = clean_html_text(entry.get("title", ""))
            pub_date = clean_html_text(entry.get("published", ""))
            raw_summary = entry.get("summary") or entry.get("description") or ""
            summary = clean_html_text(raw_summary)

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
    return items


def fetch_balanced_headlines() -> str:
    log("開始抓取台股與美股 RSS 新聞（各抓 5 則）...")
    us_items = fetch_category_headlines(US_RSS_FEEDS, limit=5)
    tw_items = fetch_category_headlines(TW_RSS_FEEDS, limit=5)

    digest = "【美股重點新聞】\n" + "\n".join(us_items) + "\n\n【台股重點新聞】\n" + "\n".join(tw_items)
    log(f"RSS 抓取完成：美股 {len(us_items)} 則，台股 {len(tw_items)} 則。")
    return digest


def clean_script_for_weekend(text: str, is_weekend: bool) -> str:
    if not is_weekend:
        return text
    cleaned = re.sub(r"(今日|今天)(台股|美股|股市)?(即將|準備)?(開盤|盤前)", "本週市場", text)
    return cleaned


def generate_script(news_digest: str, now_tw: datetime) -> str:
    log("開始呼叫 Gemini 生成晨報稿...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    weekday_map = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    today_str = now_tw.strftime("%Y年%m月%d日")
    weekday_str = weekday_map[now_tw.weekday()]
    is_weekend = now_tw.weekday() >= 5

    if is_weekend:
        mode_instruction = (
            f"今天是 {today_str}（{weekday_str}），屬於週末休市日，台股與美股今天不開盤。"
            "請以「本週台美股市場回顧」與「下週觀察重點」為主，台股與美股內容各占一半。"
            "嚴格禁止說「今天開盤」、「盤前觀察」或把過去日期當成今天。"
        )
    else:
        mode_instruction = (
            f"今天是 {today_str}（{weekday_str}），屬於交易日早晨。"
            "請將報導比重嚴格分為兩大部分（各占 50%）：第一部分為隔夜美股與科技半導體動態，第二部分為台股盤前觀察與重點產業評析。"
        )

    prompt = f"""
你是每天早上七點播報的財經主播。現在時間是台灣時間 {today_str}（{weekday_str}）。
{mode_instruction}

請根據以下財經新聞標題與摘要，寫一份約 550 到 750 字（朗讀約 2.5 到 3.5 分鐘）的繁體中文晨報口語稿。
要求：
1. **內容比重**：美股與台股的篇幅與分析比重必須各占約 50%，兼顧兩地市場焦點。
2. 依據標題與摘要的事實脈絡撰寫，不要自行腦補未提到的數字或事件。
3. 專為「語音朗讀」撰寫：使用自然親切的口語、完整句子，不要用 Markdown 星號、條列符號或難唸的縮寫堆砌；股票代號或英文專有名詞請附上好唸的中文稱呼（例如特斯拉、輝達、台積電、聯發科）。
4. 開頭親切問候早安並清楚說出今天是 {today_str} {weekday_str}，結尾提醒投資人留意風險與市場波動。

近期財經新聞與摘要：
{news_digest}
"""
    models_to_try = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash"]

    for model_name in models_to_try:
        for attempt in range(2):
            try:
                log(f"嘗試模型 {model_name}（第 {attempt + 1} 次）...")
                resp = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                raw_text = resp.text.strip()
                log(f"模型 {model_name} 成功產稿！")
                return clean_script_for_weekend(raw_text, is_weekend)
            except Exception as e:
                log(f"模型 {model_name} 失敗: {e}")
                time.sleep(5)

    raise RuntimeError("無法從 Gemini API 取得回覆，請稍後重試。")


async def _save_tts(text: str, mp3_path: str) -> None:
    # 切換為年輕女聲：zh-TW-HsiaoYuNeural
    communicate = edge_tts.Communicate(text=text, voice="zh-TW-HsiaoYuNeural")
    await communicate.save(mp3_path)


async def text_to_speech(text: str, mp3_path: str = "briefing.mp3") -> None:
    log("開始合成語音 (edge-tts zh-TW-HsiaoYuNeural)...")
    for attempt in range(2):
        try:
            await asyncio.wait_for(_save_tts(text, mp3_path), timeout=45)
            log("語音合成完成！")
            return
        except Exception as e:
            log(f"edge-tts 第 {attempt + 1} 次失敗或逾時: {e}")
            await asyncio.sleep(3)
    raise RuntimeError("edge-tts 語音合成逾時或失敗")


def send_telegram(text: str, mp3_path: str = "briefing.mp3") -> None:
    log("開始推播至 Telegram...")
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    requests.post(
        f"{base}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text[:4000]},
        timeout=30,
    ).raise_for_status()
    log("文字訊息已送出。")

    with open(mp3_path, "rb") as audio:
        requests.post(
            f"{base}/sendVoice",
            data={"chat_id": CHAT_ID},
            files={"voice": ("briefing.mp3", audio, "audio/mpeg")},
            timeout=60,
        ).raise_for_status()
    log("語音訊息已送出！")


def main() -> None:
    now_tw = datetime.now(ZoneInfo("Asia/Taipei"))
    news_digest = fetch_balanced_headlines()
    print("=== FETCHED NEWS DIGEST ===", flush=True)
    print(news_digest, flush=True)
    script = generate_script(news_digest, now_tw)
    asyncio.run(text_to_speech(script))
    send_telegram(script)
    log("全部流程順利完成！")


if __name__ == "__main__":
    main()
