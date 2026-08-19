import configparser
import requests

config = configparser.ConfigParser()
config.read("Webhook.ini")

WEBHOOK_URL = config.get("Discord", "url", fallback="").strip()


def valid_webhook(url):
    return (
        url.startswith("https://discord.com/api/webhooks/")
        or url.startswith("https://discordapp.com/api/webhooks/")
    )


WEBHOOK_ENABLED = valid_webhook(WEBHOOK_URL)


def send_embed(title, description, color=0x5865F2):
    if not WEBHOOK_ENABLED:
        return

    data = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color
            }
        ]
    }

    try:
        response = requests.post(
            WEBHOOK_URL,
            json=data,
            timeout=10
        )
        response.raise_for_status()

    except requests.RequestException as e:
        print(f"Webhook error: {e}")
