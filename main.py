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

def normalize_text(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def extract_brand(name):
    return name.split(" ")[0] if name else ""

def calculate_purchase(price):
    fee = price * 0.26
    target_profit = 10000
    purchase = max(int(price - fee - target_profit), 0)
    rate = round((purchase / price) * 100, 1) if price else 0
    profit = int(price - fee - purchase)
    return purchase, rate, profit

def analyze_prices(items):
    prices = [i["price"] for i in items if i["price"] > 0]
    if not prices:
        return {"count":0,"avg":0,"median":0,"min":0,"max":0,"std":0}
    return {
        "count": len(prices),
        "avg": int(np.mean(prices)),
        "median": int(np.median(prices)),
        "min": min(prices),
        "max": max(prices),
        "std": float(np.std(prices))
    }

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
            "avg": float(np.mean(scores)),
            "growth": scores[-1] - scores[0]
        }
    except:
        return None

# ==========================
# Yahoo（ショップ名追加）
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
            "availability": 1
        }
        try:
            res = requests.get(url, params=params).json()
            if start == 1:
                total_count = res.get("totalResultsAvailable", 0)

            for i in res.get("hits", []):
                if not i.get("inStock", True):
                    continue

                all_items.append({
                    "name": i.get("name", ""),
                    "price": i.get("price", 0),
                    "image": i.get("image", {}).get("medium", ""),
                    "url": i.get("url", ""),
                    "shop_name": i.get("store", {}).get("name", ""),
                    "source": "Yahoo"
                })
        except:
            pass

    return all_items, total_count

# ==========================
# 楽天（ショップ名追加）
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
            "availability": 1
        }

        try:
            res = requests.get(url, params=params).json()
            if page == 1:
                total_count = res.get("count", 0)

            for i in res.get("Items", []):
                item = i["Item"]
                if item.get("availability") != 1:
                    continue

                image_url = ""
                if item.get("mediumImageUrls"):
                    image_url = item["mediumImageUrls"][0]["imageUrl"].replace("?_ex=128x128","")

                all_items.append({
                    "name": item.get("itemName", ""),
                    "price": item.get("itemPrice", 0),
                    "image": image_url,
                    "url": item.get("itemUrl", ""),
                    "shop_name": item.get("shopName", ""),
                    "source": "Rakuten"
                })
        except:
            pass

    return all_items, total_count

# ==========================
# AIスコア（既存維持）
# ==========================
def calculate_ai_score(item, stats):
    if stats["count"] == 0:
        return 0
    std = stats["std"] if stats["std"] > 0 else 1
    deviation = (stats["avg"] - item["price"]) / std
    raw = deviation * 0.5 + (item["purchase_rate"]/100)*0.5
    return max(0, min(100, int((raw+1)*50)))

# ==========================
# 共通処理
# ==========================
def perform_search(keyword, sort_order, min_price, max_price):

    yahoo_items, yahoo_total = search_yahoo(keyword)
    rakuten_items, rakuten_total = search_rakuten(keyword)

    items = yahoo_items + rakuten_items
    items = [i for i in items if min_price <= i["price"] <= max_price]

    stats = analyze_prices(items)

    for item in items:
        purchase, rate, profit = calculate_purchase(item["price"])
        item["purchase_price"] = purchase
        item["purchase_rate"] = rate
        item["expected_profit"] = profit
        item["sell_score"] = calculate_ai_score(item, stats)

    items = sorted(items, key=lambda x: x["price"], reverse=(sort_order=="desc"))

    return items[:200], stats, yahoo_total + rakuten_total

# ==========================
# 画面
# ==========================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/search", response_class=HTMLResponse)
def search(request: Request,
           keyword: str = Form(""),
           sort_order: str = Form("asc"),
           min_price: int = Form(1),
           max_price: int = Form(999999999)):

    keyword = normalize_text(keyword)
    items, stats, total = perform_search(keyword, sort_order, min_price, max_price)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "items": items,
        "stats": stats,
        "keyword": keyword,
        "mall_total": total,
        "sort_order": sort_order,
        "min_price": min_price,
        "max_price": max_price
    })

# ==========================
# CSV（ショップ名追加）
# ==========================
@app.post("/download_csv")
def download_csv(keyword: str = Form(""),
                 sort_order: str = Form("asc"),
                 min_price: int = Form(1),
                 max_price: int = Form(999999999)):

    keyword = normalize_text(keyword)
    items, stats, total = perform_search(keyword, sort_order, min_price, max_price)

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "商品名","ショップ名","価格",
        "仕入目安","仕入率(%)",
        "想定利益","売れやすさスコア",
        "販売元","URL"
    ])

    for item in items:
        writer.writerow([
            item["name"],
            item["shop_name"],
            item["price"],
            item["purchase_price"],
            item["purchase_rate"],
            item["expected_profit"],
            item["sell_score"],
            item["source"],
            item["url"]
        ])

    csv_text = '\ufeff' + output.getvalue()

    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=search_result.csv"}
    )