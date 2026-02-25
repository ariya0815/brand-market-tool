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

# ==========================
# APIキー
# ==========================
YAHOO_APP_ID = "dmVyPTIwMjUwNyZpZD1BNTR6TmhTSUNWJmhhc2g9WVRsa056STVZVEpoWmpZd05UUTJZZw"
RAKUTEN_APP_ID = "1088675020626270178"

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
# ブランド抽出（簡易）
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
# Yahoo検索（200件）
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
            "start": start
        }
        try:
            res = requests.get(url, params=params).json()
            if start == 1:
                total_count = res.get("totalResultsAvailable", 0)

            for i in res.get("hits", []):
                image_url = i.get("image", {}).get("medium", "")
                all_items.append({
                    "name": i.get("name", ""),
                    "price": i.get("price", 0),
                    "image": image_url,
                    "url": i.get("url", ""),
                    "source": "Yahoo"
                })
        except:
            pass

    return all_items, total_count

# ==========================
# 楽天検索（200件）
# ==========================
def search_rakuten(keyword):
    url = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706"
    all_items = []
    total_count = 0

    for page in [1, 2, 3, 4]:
        params = {
            "applicationId": RAKUTEN_APP_ID,
            "keyword": keyword,
            "hits": 30,   # ← 50ではなく30に変更
            "page": page,
            "sort": "+itemPrice"  # 安い順
        }

        try:
            response = requests.get(url, params=params)
            print("楽天ステータス:", response.status_code)

            res = response.json()
            print("楽天レスポンスキー:", res.keys())

            if page == 1:
                total_count = res.get("count", 0)
                print("楽天総件数:", total_count)

            for i in res.get("Items", []):
                item = i["Item"]

                image_url = ""
                if item.get("mediumImageUrls"):
                    image_url = item["mediumImageUrls"][0]["imageUrl"]
                    image_url = image_url.replace("?_ex=128x128","")

                all_items.append({
                    "name": item.get("itemName", ""),
                    "price": item.get("itemPrice", 0),
                    "image": image_url,
                    "url": item.get("itemUrl", ""),
                    "source": "Rakuten"
                })

        except Exception as e:
            print("楽天エラー:", e)

    print("Rakuten取得件数:", len(all_items))
    return all_items, total_count

# ==========================
# 共通検索処理
# ==========================
def perform_search(keyword, sort_order):
    yahoo_items, yahoo_total = search_yahoo(keyword)
    rakuten_items, rakuten_total = search_rakuten(keyword)

    items = yahoo_items + rakuten_items

    for item in items:
        purchase, rate, profit = calculate_purchase(item["price"])
        item["purchase_price"] = purchase
        item["purchase_rate"] = rate
        item["expected_profit"] = profit
        item["brand"] = extract_brand(item["name"])

    items = sorted(items, key=lambda x: x["price"], reverse=(sort_order=="desc"))

    stats = analyze_prices(items)
    trend = get_google_trend(keyword)

    brand_counts = Counter([i["brand"] for i in items])
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
        "想定利益","販売元","URL"
    ])

    for item in items:
        writer.writerow([
            item["name"],
            item["price"],
            item["purchase_price"],
            item["purchase_rate"],
            item["expected_profit"],
            item["source"],
            item["url"]
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=search_result.csv"}
    )