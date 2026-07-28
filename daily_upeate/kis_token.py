import requests
import json
import config


def get_access_token():

    url = f"{config.BASE_URL}/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": config.APP_KEY,
        "appsecret": config.APP_SECRET
    }


    res = requests.post(
        url,
        headers={
            "content-type":"application/json"
        },
        data=json.dumps(body)
    )


    res.raise_for_status()


    return res.json()["access_token"]