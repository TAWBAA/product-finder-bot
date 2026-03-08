import os
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_KEY)

LAST_UPDATE_ID = None


def send_telegram(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message
        }
    )


def get_updates():

    global LAST_UPDATE_ID

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

    if LAST_UPDATE_ID:
        url += f"?offset={LAST_UPDATE_ID + 1}"

    response = requests.get(url).json()

    return response["result"]


def generate_products(niche):

    prompt = f"""
أنت خبير ecommerce.

اعطني 10 منتجات قوية داخل هذا النيش:

{niche}

كل منتج يجب أن يحتوي فقط على:

product
problem
audience
marketing_angle

اعطني النتيجة فقط بهذا الشكل:

Product 1
product:
problem:
audience:
marketing_angle:
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an ecommerce expert."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )

    return response.choices[0].message.content


def main():

    global LAST_UPDATE_ID

    print("Product Finder Bot Started")

    while True:

        updates = get_updates()

        for update in updates:

            LAST_UPDATE_ID = update["update_id"]

            if "message" not in update:
                continue

            text = update["message"]["text"]

            print("NICHE RECEIVED:", text)

            send_telegram("🔎 جاري البحث عن أفضل المنتجات داخل النيش...")

            result = generate_products(text)

            send_telegram(result)


if __name__ == "__main__":
    main()