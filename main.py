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
# 基本処理
# ==========================
def normalize_text(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def extract_brand(name):
    return name.split(" ")[0] if name else ""

# ==========================
# 🎯 利益決定ロジック
# ==========================
def get_profit_by_category(category, purchase_price):

    # バッグ
    if category == "bag":
        if purchase_price < 15000:
            return 7500
        elif purchase_price < 35000:
            return 10000
        elif purchase_price < 70000:
            return 13000
        elif purchase_price < 100000:
            return 15000
        elif purchase_price < 150000:
            return 18000
        elif purchase_price < 180000:
            return 20000
        elif purchase_price < 200000:
            return 22000
        else:
            return 25000

    # 財布・アパレル・シューズ・その他
    else:
        if purchase_price < 10000:
            return 7000
        elif purchase_price < 30000:
            return 9000
        elif purchase_price < 50000:
            return 11000
        elif purchase_price < 80000:
            return 13000
        elif purchase_price < 120000:
            return 15000
        elif purchase_price < 150000:
            return 18000
        elif purchase_price < 200000:
            return 22000
        else:
            return 25000

# ==========================
# 🔥 仕入計算（完全改良版）
# ==========================
def calculate_purchase(price, category="wallet"):

    if price <= 0:
        return 0, 0, 0

    total_fee = price * 0.26   # 11% + 15%
    available = price - total_fee

    # 仮仕入価格（最初は単純に引く）
    tentative_purchase = available * 0.7

    # その仮仕入価格から適切な利益を決定
    target_profit = get_profit_by_category(category, tentative_purchase)

    # 最終仕入価格
    purchase = int(max(price - total_fee - target_profit, 0))

    rate = round((purchase / price) * 100, 1)
    profit = int(price - total_fee - purchase)

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
            "avg": float(np.mean(scores)),
            "growth": scores[-1] - scores[0]
        }
    except:
        return None

# ==========================
# AIスコア
# ==========================
def calculate_ai_score(item, stats):
    if stats["count"] == 0:
        return 0
    std = stats["std"] if stats["std"] > 0 else 1
    deviation = (stats["avg"] - item["price"]) / std
    raw = deviation * 0.5 + (item["purchase_rate"]/100)*0.5
    return max(0, min(100, int((raw+1)*50)))

# ==========================
# 共通検索処理
# ==========================
def perform_search(keyword, sort_order, min_price, max_price):

    yahoo_items, yahoo_total = search_yahoo(keyword)
    rakuten_items, rakuten_total = search_rakuten(keyword)

    items = yahoo_items + rakuten_items
    items = [i for i in items if min_price <= i["price"] <= max_price]

    stats = analyze_prices(items)

    for item in items:

        # 🔥 カテゴリ自動判定
        name = item["name"].lower()
        if "bag" in name or "バッグ" in name:
            category = "bag"
        elif "wallet" in name or "財布" in name:
            category = "wallet"
        elif "shoe" in name or "シューズ" in name:
            category = "wallet"
        elif "apparel" in name or "服" in name:
            category = "wallet"
        else:
            category = "wallet"

        purchase, rate, profit = calculate_purchase(item["price"], category)

        item["purchase_price"] = purchase
        item["purchase_rate"] = rate
        item["expected_profit"] = profit
        item["sell_score"] = calculate_ai_score(item, stats)

    items = sorted(items, key=lambda x: x["price"], reverse=(sort_order=="desc"))

    return (
        items[:200],
        stats,
        yahoo_total + rakuten_total,
        None,
        [],
        []
    )