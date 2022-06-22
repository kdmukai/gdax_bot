#!/usr/bin/env python

import argparse
import base64
import configparser
import datetime
import decimal
import json
import math
import sys
import time
from decimal import Decimal

import boto3
import cbpro


def get_timestamp():
    ts = time.time()
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


"""
    Basic Coinbase Pro DCA buy/sell bot that executes a market order.
    * CB Pro does not incentivize maker vs taker trading unless you trade over $50k in
        a 30 day period (0.25% taker, 0.15% maker). Current fees are 0.50% if you make
        less than $10k worth of trades over the last 30 days. Drops to 0.35% if you're
        above $10k but below $50k in trades.
    * Market orders can be issued for as little as $5 of value versus limit orders which
        must be 0.001 BTC (e.g. $50 min if btc is at $50k). BTC-denominated market
        orders must be at least 0.0001 BTC.

    This is meant to be run as a crontab to make regular buys/sells on a set schedule.
"""

parser = argparse.ArgumentParser(
    description="""
        This is a basic Coinbase Pro DCA buying/selling bot.

        ex:
            BTC-USD BUY 14 USD          (buy $14 worth of BTC)
            BTC-USD BUY 0.00125 BTC     (buy 0.00125 BTC)
            ETH-BTC SELL 0.00125 BTC    (sell 0.00125 BTC worth of ETH)
            ETH-BTC SELL 0.1 ETH        (sell 0.1 ETH)
    """,
    formatter_class=argparse.RawTextHelpFormatter,
)

# Required positional arguments
parser.add_argument(
    "-market_name", default="BTC-USD", help="(e.g. BTC-USD, ETH-BTC, etc)"
)

parser.add_argument("-order_side", default="BUY", type=str, choices=["BUY", "SELL"])

parser.add_argument(
    "-amount",
    type=Decimal,
    default="4.00",
    help="The quantity to buy or sell in the amount_currency",
)

parser.add_argument(
    "-amount_currency", default="USD", help="The currency the amount is denominated in"
)


# Additional options
parser.add_argument(
    "-sandbox",
    action="store_true",
    default=False,
    dest="sandbox_mode",
    help="Run against sandbox, skips user confirmation prompt",
)

parser.add_argument(
    "-warn_after",
    default=300,
    action="store",
    type=int,
    dest="warn_after",
    help="secs to wait before sending an alert that an order isn't done",
)

parser.add_argument(
    "-j",
    "--job",
    action="store_true",
    default=False,
    dest="job_mode",
    help="Suppresses user confirmation prompt",
)

parser.add_argument(
    "-c",
    "--config",
    default="./settings-local.conf",
    dest="config_file",
    help="Override default config file location",
)


def main(event, context):
    args = parser.parse_args()
    attributes = event.get("attributes", {})

    market_name = attributes.get("market_name", args.market_name)
    order_side = attributes.get("order_side", args.order_side).lower()
    amount = Decimal(attributes.get("amount", args.amount))
    amount_currency = attributes.get("amount_currency", args.amount_currency)
    config_file = attributes.get("config_file", args.config_file)

    job_mode = True if "job" in attributes else args.job_mode

    args.market_name = market_name
    args.order_side = order_side
    args.amount = amount
    args.amount_currency = amount_currency
    args.config_file = config_file
    args.job_mode = job_mode

    print(f"{get_timestamp()}: STARTED: {args}")

    sandbox_mode = args.sandbox_mode
    job_mode = args.job_mode
    warn_after = args.warn_after

    if not sandbox_mode and not job_mode:
        if sys.version_info[0] < 3:
            # python2.x compatibility
            response = raw_input("Production purchase! Confirm [Y]: ")  # noqa: F821
        else:
            response = input("Production purchase! Confirm [Y]: ")
        if response != "Y":
            print("Exiting without submitting purchase.")
            exit()

    # Read settings
    config = configparser.ConfigParser()
    config.read(config_file)

    config_section = "production"
    if sandbox_mode:
        config_section = "sandbox"
    key = config.get(config_section, "API_KEY")
    passphrase = config.get(config_section, "PASSPHRASE")
    secret = config.get(config_section, "SECRET_KEY")
    aws_access_key_id = config.get(config_section, "AWS_ACCESS_KEY_ID")
    aws_secret_access_key = config.get(config_section, "AWS_SECRET_ACCESS_KEY")
    sns_topic = config.get(config_section, "SNS_TOPIC")
    aws_region = config.get(config_section, "AWS_REGION")

    # Instantiate public and auth API clients
    if not args.sandbox_mode:
        auth_client = cbpro.AuthenticatedClient(key, secret, passphrase)
    else:
        # Use the sandbox API (requires a different set of API access credentials)
        auth_client = cbpro.AuthenticatedClient(
            key,
            secret,
            passphrase,
            api_url="https://api-public.sandbox.pro.coinbase.com",
        )

    public_client = cbpro.PublicClient()

    # Retrieve dict list of all trading pairs
    products = public_client.get_products()
    base_min_size = None
    base_increment = None
    quote_increment = None
    for product in products:
        if product.get("id") == market_name:
            base_currency = product.get("base_currency")
            quote_currency = product.get("quote_currency")
            base_min_size = Decimal(product.get("base_min_size")).normalize()
            base_increment = Decimal(product.get("base_increment")).normalize()
            quote_increment = Decimal(product.get("quote_increment")).normalize()
            if amount_currency == product.get("quote_currency"):
                amount_currency_is_quote_currency = True
            elif amount_currency == product.get("base_currency"):
                amount_currency_is_quote_currency = False
            else:
                raise Exception(
                    f"amount_currency {amount_currency} not in market {market_name}"
                )
            print(json.dumps(product, indent=2))

    print(f"base_min_size: {base_min_size}")
    print(f"quote_increment: {quote_increment}")

    # Prep boto SNS client for email notifications
    sns = boto3.client(
        "sns",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=aws_region,
    )

    if amount_currency_is_quote_currency:
        result = auth_client.place_market_order(
            product_id=market_name,
            side=order_side,
            funds=float(amount.quantize(quote_increment)),
        )
    else:
        result = auth_client.place_market_order(
            product_id=market_name,
            side=order_side,
            size=float(amount.quantize(base_increment)),
        )

    print(json.dumps(result, sort_keys=True, indent=4))

    if "message" in result:
        # Something went wrong if there's a 'message' field in response
        sns.publish(
            TopicArn=sns_topic,
            Subject=f"Could not place {market_name} {order_side} order",
            Message=json.dumps(result, sort_keys=True, indent=4),
        )
        exit()

    if result and "status" in result and result["status"] == "rejected":
        print(f"{get_timestamp()}: {market_name} Order rejected")

    order = result
    order_id = order["id"]
    print(f"order_id: {order_id}")

    """
        Wait to see if the order was fulfilled.
    """
    wait_time = 5
    total_wait_time = 0
    while "status" in order and (
        order["status"] == "pending" or order["status"] == "open"
    ):
        if total_wait_time > warn_after:
            sns.publish(
                TopicArn=sns_topic,
                Subject=f"{market_name} {order_side} order of {amount} {amount_currency} OPEN/UNFILLED",
                Message=json.dumps(order, sort_keys=True, indent=4),
            )
            exit()

        print(
            f"{get_timestamp()}: Order {order_id} still {order['status']}. Sleeping for {wait_time} (total {total_wait_time})"
        )
        time.sleep(wait_time)
        total_wait_time += wait_time
        order = auth_client.get_order(order_id)
        # print(json.dumps(order, sort_keys=True, indent=4))

        if "message" in order and order["message"] == "NotFound":
            # Most likely the order was manually cancelled in the UI
            sns.publish(
                TopicArn=sns_topic,
                Subject=f"{market_name} {order_side} order of {amount} {amount_currency} CANCELLED",
                Message=json.dumps(result, sort_keys=True, indent=4),
            )
            exit()

    # Order status is no longer pending!
    print(json.dumps(order, indent=2))

    market_price = (
        Decimal(order["executed_value"]) / Decimal(order["filled_size"])
    ).quantize(quote_increment)

    subject = f"{market_name} {order_side} order of {amount} {amount_currency} {order['status']} @ {market_price} {quote_currency}"
    print(subject)
    sns.publish(
        TopicArn=sns_topic,
        Subject=subject,
        Message=json.dumps(order, sort_keys=True, indent=4),
    )


if __name__ == "__main__":
    context = {}
    event = {"attributes": {}}
    event = {
        "attributes": {
            "market_name": "BTC-USD",
            "order_side": "BUY",
            "amount": "0.50",
            "amount_currency": "USD",
            "config_file": "./settings-local.conf",
        }
    }
    main(event, context)
