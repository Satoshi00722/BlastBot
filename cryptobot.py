# cryptobot.py
import requests

API_URL = "https://pay.crypt.bot/api"

def create_invoice(token, amount, description, payload):
    headers = {
        "Crypto-Pay-API-Token": token
    }
    data = {
        "asset": "USDT",
        "amount": amount,
        "description": description,
        "payload": payload
    }
    r = requests.post(f"{API_URL}/createInvoice", json=data, headers=headers)
    return r.json()

def get_invoice(token, invoice_id):
    headers = {
        "Crypto-Pay-API-Token": token
    }
    r = requests.get(
        f"{API_URL}/getInvoices?invoice_ids={invoice_id}",
        headers=headers
    )
    return r.json()
