import os
import requests
import unicodedata
import re
import numpy as np
import csv
from io import StringIO
from collections import Counter
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pytrends.request import TrendReq

YAHOO_APP_ID = os.getenv("YAHOO_APP_ID")
RAKUTEN_APP_ID = os.getenv("RAKUTEN_APP_ID")

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ==========================
# 正規化
# ==========================
def normalize_text(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# ==========================
# ブランド抽出
# ==========================
def extract_brand(name):
    return name.split(" ")[0]

# ==========================
# 仕入計算
# ==========================
def calculate_purchase(price):
    fee = price * 0.26
    target_profit = 10000
    purchase = int(price - fee - target_profit)
    purchase = purchase if purchase > 0 else 0
    rate = round((purchase / price) * 100, 1) if price else 0
    profit = int(price - fee - purchase)
    return purchase, rate, profit

# ==========================
# 統計
# ==========================
def analyze_prices(items):
    prices = [i["price"] for i in items if i["price"] > 0]
    if not prices:
        return {"count":0,"avg":0,"median":0,"min":0,"max":0}
    return {
        "count": len(prices),
        "avg": int(np.mean(prices)),
        "median": int(np.median(prices)),
        "min": min(prices),
        "max": max(prices)
    }

# ==========================
# Google Trends
# ==========================
def get_google_trend(keyword):
    try:
        pytrends = TrendReq(hl='ja-JP', tz=540)
        pytrends.build_payload([keyword], timeframe='today 3-m', geo='JP')
        df = pytrends.interest_over_time()
        if df.empty:
            return None
        scores = df[keyword].tolist()
        return {
            "scores": scores,
            "current": scores[-1],
            "avg": int(np.mean(scores))
        }
    except:
        return None

# ==========================
# Yahoo検索（販売中のみ）
# ==========================
def search_yahoo(keyword):
    url = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    all_items = []
    total_count = 0

    for start in [1, 51, 101, 151]:
        params = {
            "appid": YAHOO_APP_ID,
            "query": f"{keyword} 中古",
            "results": 50,
            "start": start,
            "availability": 1  # 在庫ありのみ
        }
        try:
            res = requests.get(url, params=params).json()
            if start == 1:
                total_count = res.get("totalResultsAvailable", 0)

            for i in res.get("hits", []):
                all_items.append({
                    "name": i.get("name", ""),
                    "price": i.get("price", 0),
                    "image": i.get("image", {}).get("medium", ""),
                    "url": i.get("url", ""),
                    "source": "Yahoo"
                })
        except:
            pass

    return all_items, total_count

# ==========================
# 楽天検索（販売中のみ）
# ==========================
def search_rakuten(keyword):
    url = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706"
    all_items = []
    total_count = 0

    for page in [1, 2, 3, 4]:
        params = {
            "applicationId": RAKUTEN_APP_ID,
            "keyword": keyword,
            "hits": 30,
            "page": page,
            "sort": "+itemPrice",
            "availability": 1
        }

        try:
            res = requests.get(url, params=params).json()

            if page == 1:
                total_count = res.get("count", 0)

            for i in res.get("Items", []):
                item = i["Item"]
                if item.get("itemPrice", 0) <= 0:
                    continue

                image_url = ""
                if item.get("mediumImageUrls"):
                    image_url = item["mediumImageUrls"][0]["imageUrl"].replace("?_ex=128x128","")

                all_items.append({
                    "name": item.get("itemName", ""),
                    "price": item.get("itemPrice", 0),
                    "image": image_url,
                    "url": item.get("itemUrl", ""),
                    "source": "Rakuten"
                })
        except:
            pass

    return all_items, total_count

# ==========================
# 売れやすさスコア
# ==========================
def calculate_sell_score(item, stats, total_count, trend, brand_counts):

    score = 0

    # ① 価格優位性（40点）
    median = stats["median"]
    if median > 0 and item["price"] < median:
        diff_rate = (median - item["price"]) / median
        score += min(diff_rate * 100, 40)

    # ② 供給不足（30点）
    if total_count < 50:
        score += 30
    elif total_count < 100:
        score += 20
    elif total_count < 200:
        score += 10

    # ③ トレンド（20点）
    if trend:
        score += (trend["avg"] / 100) * 20

    # ④ ブランド出現頻度（10点）
    brand_freq = brand_counts.get(item["brand"], 0)
    if brand_freq > 0:
        score += min((brand_freq / 50) * 10, 10)

    return int(min(score, 100))

# ==========================
# 共通検索処理
# ==========================
def perform_search(keyword, sort_order):
    yahoo_items, yahoo_total = search_yahoo(keyword)
    rakuten_items, rakuten_total = search_rakuten(keyword)

    items = yahoo_items + rakuten_items

    stats = analyze_prices(items)
    trend = get_google_trend(keyword)
    brand_counts = Counter([extract_brand(i["name"]) for i in items])

    for item in items:
        purchase, rate, profit = calculate_purchase(item["price"])
        item["purchase_price"] = purchase
        item["purchase_rate"] = rate
        item["expected_profit"] = profit
        item["brand"] = extract_brand(item["name"])
        item["sell_score"] = calculate_sell_score(
            item, stats,
            yahoo_total + rakuten_total,
            trend,
            brand_counts
        )

    items = sorted(items, key=lambda x: x["price"], reverse=(sort_order=="desc"))

    brand_labels = list(brand_counts.keys())[:10]
    brand_values = list(brand_counts.values())[:10]

    return items[:200], stats, yahoo_total + rakuten_total, trend, brand_labels, brand_values

# ==========================
# 画面
# ==========================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/search", response_class=HTMLResponse)
def search(request: Request,
           keyword: str = Form(""),
           sort_order: str = Form("asc")):

    keyword = normalize_text(keyword)
    items, stats, total, trend, labels, values = perform_search(keyword, sort_order)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "items": items,
        "stats": stats,
        "keyword": keyword,
        "mall_total": total,
        "trend": trend,
        "brand_labels": labels,
        "brand_values": values,
        "sort_order": sort_order
    })

# ==========================
# CSVダウンロード
# ==========================
@app.post("/download_csv")
def download_csv(keyword: str = Form(""),
                 sort_order: str = Form("asc")):

    keyword = normalize_text(keyword)
    items, stats, total, trend, labels, values = perform_search(keyword, sort_order)

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "商品名","価格","仕入目安","仕入率(%)",
        "想定利益","売れやすさスコア",
        "販売元","URL"
    ])

    for item in items:
        writer.writerow([
            item["name"],
            item["price"],
            item["purchase_price"],
            item["purchase_rate"],
            item["expected_profit"],
            item["sell_score"],
            item["source"],
            item["url"]
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=search_result.csv"}
    )