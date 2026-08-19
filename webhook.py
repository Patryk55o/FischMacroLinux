import configparser
import requests

config = configparser.ConfigParser()
config.read("Webhook.ini")

WEBHOOK_URL = config["Discord"]["url"]


def send_embed(title, description, color=0x5865F2):
    data = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color
            }
        ]
    }

    response = requests.post(WEBHOOK_URL, json=data)
    response.raise_for_status()
