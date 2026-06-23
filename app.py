from flask import Flask, render_template, request, redirect, session, url_for, jsonify
import json
import requests
from datetime import datetime, timedelta
from random import randint
from decimal import Decimal, ROUND_HALF_UP
import numpy as np
from sklearn.linear_model import LinearRegression
from dotenv import load_dotenv
import os
app = Flask(__name__)
load_dotenv()

app.secret_key = os.getenv("FLASK_SECRET_KEY")

client_id = os.getenv("PAYPAL_CLIENT_ID")
client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
polygon_key = os.getenv("POLYGON_API_KEY")
gnews_api_key = os.getenv("GNEWS_API_KEY")
access_key =  os.getenv("ACCESS_KEY")

PAYPAL_API_BASE = os.getenv('PAYPAL_API_BASE', 'https://api-m.sandbox.paypal.com')
BASE_URL = os.getenv('BASE_URL', 'http://127.0.0.1:5000').rstrip('/')




def get_paypal_access_token():
    """Get an app-level PayPal access token using client credentials.

    This avoids the old Sign in with PayPal/OpenID login flow and uses
    PayPal Checkout directly for creating and capturing orders.
    """
    if not client_id or not client_secret:
        raise ValueError("Missing PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET")

    response = requests.post(
        f"{PAYPAL_API_BASE}/v1/oauth2/token",
        auth=(client_id, client_secret),
        headers={"Accept": "application/json", "Accept-Language": "en_US"},
        data={"grant_type": "client_credentials"},
        timeout=20,
    )

    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError(f"PayPal token error: {json.dumps(data, indent=2)}")

    return data["access_token"]

# 🔍 Utilities


@app.route("/")
def landing_page():
    try:
        if not access_key:
            raise ValueError("ACCESS_KEY is missing. Please add ACCESS_KEY in Render Environment Variables.")

        url = f"https://api.exchangerate.host/live?access_key={access_key}&currencies=SGD"
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get("success") is not True:
            raise ValueError(f"Exchange API error: {data}")

        rate = round(data["quotes"]["USDSGD"], 4)

    except Exception as e:
        print("❌ Failed to fetch USD/SGD rate:", e)
        rate = None

    return render_template("index.html", usd_sgd_rate=rate)


def get_polygon_fx_pairs():
    base_url = f"https://api.polygon.io/v3/reference/tickers?market=fx&active=true&limit=1000&apiKey={polygon_key}"
    fx_pairs = []
    try:
        url = base_url
        while url:
            res = requests.get(url)
            data = res.json()

            # ✅ Check if response contains results
            if "results" not in data:
                print("⚠️ Polygon API error or no results:", data)
                break

            for t in data.get("results", []):
                if t["ticker"].startswith("C:"):
                    fx_pairs.append(t["ticker"].replace("C:", ""))

            url = data.get("next_url")
            if url:
                url += f"&apiKey={polygon_key}"

        # ✅ Fallback if no pairs fetched
        if not fx_pairs:
            fx_pairs = ["EURSGD", "USDSGD", "AUDSGD", "JPYSGD"]
            print("⚠️ Using fallback FX pairs:", fx_pairs)

        return sorted(fx_pairs)

    except Exception as e:
        print("❌ Polygon pair error:", e)
        return ["EURSGD", "USDSGD", "AUDSGD", "JPYSGD"]



def get_fx_rate(from_ccy, to_ccy):
    url = f"https://api.polygon.io/v2/aggs/ticker/C:{from_ccy}{to_ccy}/prev?adjusted=true&apiKey={polygon_key}"
    try:
        res = requests.get(url).json()
        return res["results"][0]["c"]
    except Exception as e:
        print("FX rate error:", e)
        return None

# 📍 ROUTES
@app.route('/login')
def login():
    # Old PayPal OAuth login removed. Go directly to the dashboard.
    return redirect(url_for('services'))


@app.route("/callback")
def callback():
    # Kept only so old PayPal redirect URLs do not crash.
    return redirect(url_for('services'))


@app.route("/services")
def services():
    try:
        demo_user = {
            "name": "Demo User",
            "email": "demo@merlionfx.com",
            "payer_id": "PAYPAL_CHECKOUT_ONLY"
        }
        session['paypal_customer'] = demo_user

        return render_template(
            "services.html",
            fName=demo_user["name"],
            fEmail=demo_user["email"],
            fPayerID=demo_user["payer_id"],
            fCCY="SGD",
            fBalance="0.00",
            fxPairs=get_polygon_fx_pairs()
        )

    except Exception as e:
        return str(e)


@app.route("/create_order")
def create_order():
    try:
        customer = session.get('paypal_customer', {
            "name": "Demo User",
            "email": "demo@merlionfx.com",
            "payer_id": "PAYPAL_CHECKOUT_ONLY"
        })
        all_pairs = get_polygon_fx_pairs()
        default = "EURSGD" if "EURSGD" in all_pairs else all_pairs[0]
        fx_rate = get_fx_rate(default[:3], default[3:])
        invoice = datetime.now().strftime("%Y%m%d%H%M%S") + str(randint(100, 999))

        return render_template(
            "create_order.html",
            fName=customer["name"],
            fEmail=customer["email"],
            fPayerID=customer["payer_id"],
            fCCY="SGD",
            fBalance="0.00",
            finvoiceID=invoice,
            fxPairs=all_pairs,
            defaultPair=default,
            defaultRate=fx_rate
        )
    except Exception as e:
        return str(e)


@app.route("/get_fx_rate")
def get_fx_rate_route():
    pair = request.args.get("pair", "")
    try:
        rate = get_fx_rate(pair[:3], pair[3:])
        return {"rate": rate}
    except:
        return {"error": "Invalid pair"}, 400


@app.route("/process_order", methods=["POST"])
def process_order():
    try:
        access_token = get_paypal_access_token()
        email = request.form['customerEmailAdd']
        itemName = request.form['itemName']
        invoiceID = request.form['invoiceID']
        unit = Decimal(request.form['itemUnitAmount']).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        qty = int(request.form['itemQuantity'])
        desc = request.form['itemDescription']
        total = (unit * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        selectedPair = request.form['itemName']
        base_currency = selectedPair.split('/')[0] if '/' in selectedPair else selectedPair[:3]




        session["fx_unit_price"] = str(unit)
        session["fx_quantity"] = qty
        session["fx_total"] = str(total)
        session["fx_baseCurrency"] = base_currency

        payload = {
            "intent": "CAPTURE",
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": f"{BASE_URL}/capture_payment",
                        "cancel_url": f"{BASE_URL}/"
                    }
                }
            },
            "purchase_units": [{
                "invoice_id": invoiceID,
                "amount": {
                    "currency_code": "SGD",
                    "value": str(total),
                    "breakdown": {
                        "item_total": {
                            "currency_code": "SGD",
                            "value": str(total)
                        }
                    }
                },
                "items": [{
                    "name": itemName,
                    "description": desc,
                    "unit_amount": {
                        "currency_code": "SGD",
                        "value": str(unit)
                    },
                    "quantity": str(qty),
                    "category": "DIGITAL_GOODS",
                    "base_currency": base_currency
                }]
            }]
        }

        headers = {'Authorization': 'Bearer ' + access_token, 'Content-Type': 'application/json'}
        response = requests.post("https://api-m.sandbox.paypal.com/v2/checkout/orders", headers=headers, json=payload).json()
        for link in response["links"]:
            if link["rel"] in ["approve", "payer-action"]:
                return redirect(link["href"])
        return str(response)
    except Exception as e:
        return str(e)

@app.route("/capture_payment")
def capture_payment():
    try:
        access_token = get_paypal_access_token()
        orderID = request.args.get("token")
        payerID = request.args.get("PayerID")
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + access_token
        }
        url = f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{orderID}/capture"
        response = requests.post(url, headers=headers)
        order = response.json()

        # ✅ Add debug: print full PayPal response
        print("🔍 PayPal capture response:", json.dumps(order, indent=2))

        # ✅ Check for success before accessing keys
        if "id" not in order or "status" not in order:
            return f"<h3>❌ Capture failed</h3><pre>{json.dumps(order, indent=2)}</pre>"

        info = order.get("payer", {}).get("name", {})
        return render_template("order_result.html",
                               fOrderID=order["id"],
                               fStatus=order["status"],
                               fName=info.get("given_name", ""),
                               fSurname=info.get("surname", ""),
                               fEmail=order.get("payer", {}).get("email_address", ""),
                               fAmount=session.get("fx_total"),
                               fPayPalID=payerID,
                               fUnitPrice=session.get("fx_unit_price"),
                               fQuantity=session.get("fx_quantity"),
                               fBaseCurrency=session.get("fx_baseCurrency")
                            )
    except Exception as e:
        return f"<h3>⚠️ Error during capture</h3><pre>{str(e)}</pre>"

@app.route("/chart")
def chart():
    fx_pairs = get_polygon_fx_pairs()
    return render_template("chart.html", fxPairs=fx_pairs)

@app.route("/api/chart_data")
def chart_data():
    pair = request.args.get("pair", "EURSGD").upper()
    days = int(request.args.get("range", 30))

    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/C:{pair}/range/1/day/"
        f"{start}/{end}?adjusted=true&sort=asc&limit={days}&apiKey={polygon_key}"
    )

    try:
        res = requests.get(url).json()

        # ✅ Safety check
        results = res.get("results", [])
        if not results:
            return {"dates": [], "prices": []}

        dates = [datetime.fromtimestamp(d["t"] / 1000).strftime('%Y-%m-%d') for d in results]
        prices = [d["c"] for d in results]

        return {"dates": dates, "prices": prices}
    except Exception as e:
        print("Error loading chart data:", e)
        return {"dates": [], "prices": []}
    
# ✅ Get all CBM-supported currency codes
def get_cbm_supported_currencies():
    try:
        data = requests.get("https://forex.cbm.gov.mm/api/latest").json()
        return sorted(data["rates"].keys())
    except:
        return []

# ✅ Get the exchange rate for one currency
def fetch_cbm_rate(currency):
    try:
        data = requests.get("https://forex.cbm.gov.mm/api/latest").json()
        return data["rates"].get(currency)
    except:
        return None

# ✅ Show CBM rate form with dropdown and default SGD rate
@app.route("/cbm", methods=["GET", "POST"])
def cbm():
    cbm_data = requests.get("https://forex.cbm.gov.mm/api/latest").json()
    rates = cbm_data["rates"]
    default_currency = "SGD"
    default_rate = rates.get(default_currency)
    return render_template("cbm.html", all_rates=rates, default_currency=default_currency, default_rate=default_rate)

# ✅ Live CBM rate endpoint (for any currency)
@app.route("/get_cbm_rate")
def get_cbm_rate_api():
    currency = request.args.get("currency", "SGD")

    try:
        data = requests.get("https://forex.cbm.gov.mm/api/latest").json()
        rates = data["rates"]
        if currency not in rates:
            return {"error": "Invalid currency code."}, 400
        return {"currency": currency, "rate": rates[currency]}
    except Exception as e:
        return {"error": str(e)}, 500

# ✅ PayPal Purchase with MMK converted to SGD (using CBM rate)
@app.route("/process_cbm_order", methods=["POST"])
def process_cbm_order():
    try:
        access_token = get_paypal_access_token()
        currency = request.form["currency"]  # Always "SGD"
        mmk_amount = request.form["mmkAmount"]
        rate_str = request.form["rate"]  # CBM rate like "1500.55"
        

        # Convert MMK → SGD using CBM rate
        cbm_rate = Decimal(rate_str.replace(",", ""))
        mmk_value = Decimal(mmk_amount)
        sgd_total = (mmk_value / cbm_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        invoiceID = datetime.now().strftime("%Y%m%d%H%M%S") + str(randint(100, 999))

        session["fx_unit_price"] = str(sgd_total)
        session["fx_quantity"] = str(mmk_value)  # Optional, for consistency
        session["fx_total"] = str(sgd_total)
       
        
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{

                "invoice_id": invoiceID,
                "amount": {
                    "currency_code": "SGD",
                    "value": str(sgd_total),
                    "breakdown": {
                        "item_total": {
                            "currency_code": "SGD",
                            "value": str(sgd_total)
                        }
                    }
                },
                "items": [{
                    "name": f"CBM MMK Buy {currency}",
                    "description": f"{mmk_amount} MMK → {sgd_total} SGD",
                    "unit_amount": {
                        "currency_code": "SGD",
                        "value": str(sgd_total)
                    },
                    "quantity": "1",
                    "category": "DIGITAL_GOODS"
                }]
            }],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": f"{BASE_URL}/capture_payment",
                        "cancel_url": f"{BASE_URL}/"
                    }
                }
            }
        }

        headers = {
            'Authorization': 'Bearer ' + access_token,
            'Content-Type': 'application/json'
        }

        res = requests.post("https://api-m.sandbox.paypal.com/v2/checkout/orders",
                            headers=headers, data=json.dumps(payload))
        data = res.json()

        approval_url = next((link["href"] for link in data["links"] if link["rel"] in ["approve", "payer-action"]), None)
        if approval_url:
            return redirect(approval_url)
        else:
            return f"<h3>❌ Failed to create PayPal order</h3><pre>{json.dumps(data, indent=2)}</pre>"

    except Exception as e:
        return f"<h3>⚠️ Error processing CBM order</h3><pre>{e}</pre>"
    

    
@app.route("/news")
def news_page():
    return render_template("news.html")


@app.route("/api/news")
def get_news():
    url = f"https://gnews.io/api/v4/search?q=forex&lang=en&token={gnews_api_key}"
    try:
        return requests.get(url).json()
    except Exception as e:
        return {"error": str(e)}


@app.route("/forecast")
def forecast_page():
    return render_template("forecast.html", fxPairs=get_polygon_fx_pairs())

@app.route("/api/forecast")
def get_forecast():
    pair = request.args.get("pair", "USD/SGD").replace("/", "")
    days = 30
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/C:{pair}/range/1/day/"
        f"{start}/{end}?adjusted=true&sort=asc&limit={days}&apiKey={polygon_key}"
    )

    try:
        res = requests.get(url).json()

        # ✅ Defensive check
        results = res.get("results", [])
        if not results:
            return {"error": "No historical data available for this pair."}, 400

        close_prices = [p["c"] for p in results]
        X = np.arange(len(close_prices)).reshape(-1, 1)
        y = np.array(close_prices)
        model = LinearRegression().fit(X, y)
        tomorrow = len(close_prices)
        prediction = model.predict([[tomorrow]])
        return {
    "predicted_rate": round(prediction[0], 4),
    "last_known": round(close_prices[-1], 6)
}

    except Exception as e:
        return {"error": str(e)}, 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG') == '1')