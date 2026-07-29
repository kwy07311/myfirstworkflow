import logging
import os


os.makedirs(
    "log",
    exist_ok=True
)


logging.basicConfig(
    filename="log/update.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    encoding="utf-8"
)


def log(msg):

    print(msg)

    logging.info(msg)