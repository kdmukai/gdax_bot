#!/usr/bin/env python

import argparse
import configparser
import datetime
import json
import sys
import time
import boto3
from decimal import Decimal

from config import set_api_credentials
from coinbase_client import getProduct, createOrder, generate_client_order_id, getOrder, Side


def get_timestamp():
    ts = time.time()
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


"""
    Basic Coinbase Advance Trade buy/sell bot that executes a market order.

    This is meant to be run as a event trigger job to make regular buys/sells on a set schedule.
"""

parser = argparse.ArgumentParser(
    description="""
        This is a basic Coinbase Advance Trade DCA buying/selling bot.

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
    "-product_id", default="BTC-USD", help="(e.g. BTC-USD, ETH-BTC, etc)"
)

parser.add_argument("-side", default="BUY", type=str, choices=["BUY", "SELL"])

parser.add_argument(
    "-amount",
    type=Decimal,
    default="4.00",
    help="The quantity to buy or sell in the amount_currency",
)

parser.add_argument(
    "-amount_currency", default="USD", help="The currency the amount is denominated in"
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
    default="./secrets.conf",
    dest="config_file",
    help="Override default config file location",
)


def lambda_handler(event, context):
    args = parser.parse_args()
    attributes = event.get("attributes", {})

    product_id = attributes.get("product_id", args.product_id)
    side = attributes.get("side", args.side).lower()
    amount = Decimal(attributes.get("amount", args.amount))
    amount_currency = attributes.get("amount_currency", args.amount_currency)
    config_file = attributes.get("config_file", args.config_file)

    job_mode = True if "job" in attributes else args.job_mode

    args.product_id = product_id
    args.side = side
    args.amount = amount
    args.amount_currency = amount_currency
    args.config_file = config_file
    args.job_mode = job_mode

    print(f"{get_timestamp()}: STARTED: {args}")

    job_mode = args.job_mode
    warn_after = args.warn_after

  
    # Read settings
    _config = configparser.ConfigParser()
    _config.read(config_file)

    config_section = "production"
   
    key = _config.get(config_section, "API_KEY")
    secret = _config.get(config_section, "SECRET_KEY")
    sns_topic = _config.get(config_section, "SNS_TOPIC")

    
    # Set credentials for CBAuth
    set_api_credentials(api_key=key, api_secret=secret)
    

    # Retrieve dict list of all trading pairs
    product_details = getProduct(product_id)
    base_min_size = None
    base_increment = None
    quote_increment = None
    
    if product_details.get("product_id") == product_id:
        base_currency = product_details.get("base_currency_id")
        quote_currency = product_details.get("quote_currency_id")
        base_min_size = Decimal(product_details.get("base_min_size")).normalize()
        base_increment = Decimal(product_details.get("base_increment")).normalize()
        quote_increment = Decimal(product_details.get("quote_increment")).normalize()
        if amount_currency == product_details.get("quote_currency_id"):
            amount_currency_is_quote_currency = True
        elif amount_currency == product_details.get("base_currency_id"):
            amount_currency_is_quote_currency = False
        else:
            raise Exception(
                f"amount_currency {amount_currency} not in market {product_id}"
            )
        print(json.dumps(product_details, indent=2))

    print(f"base_min_size: {base_min_size}")
    print(f"quote_increment: {quote_increment}")

    # Prep boto SNS client for email notifications
    sns = boto3.client('sns')

    if amount_currency_is_quote_currency:
        result = createOrder(
            client_order_id=generate_client_order_id(),
            product_id=product_id,
            side=Side.BUY.name,
            order_type='market_market_ioc',
            order_configuration={"quote_size": str(amount.quantize(quote_increment))} 
        )
    else:
         result = createOrder(
            client_order_id=generate_client_order_id(),
            product_id=product_id,
            side=Side.BUY.name,
            order_type='market_market_ioc',
            order_configuration={"quote_size": str(amount.quantize(base_increment))} 
        )

    print(json.dumps(result, sort_keys=True, indent=4))

    if "message" in result:
        # Something went wrong if there's a 'message' field in response
        sns.publish(
            TopicArn=sns_topic,
            Subject=f"Could not place {product_id} {side} order",
            Message=json.dumps(result, sort_keys=True, indent=4),
        )
        exit()

    if result and "error_response" in result:
        print(f"{get_timestamp()}: {product_id} Order Error")

    
    order_id = result["success_response"]["order_id"]
    client_order_id = result["success_response"]["client_order_id"]
    print(f"order_id: {order_id}")
    print(f"client_order_id: {client_order_id}")

    """
        Wait to see if the order was fulfilled.
    """
    wait_time = 5
    total_wait_time = 0

    #get order status
    order = getOrder(order_id=order_id).get('order')

    while "status" in order and (
        order["status"] == "UNKNOWN_ORDER_STATUS" or order["status"] == "OPEN"
    ):

        if total_wait_time > warn_after:
            sns.publish(
                TopicArn=sns_topic,
                Subject=f"{product_id} {side} order of {amount} {amount_currency} OPEN/UNFILLED",
                Message=json.dumps(order, sort_keys=True, indent=4),
            )
            exit()

        print(
            f"{get_timestamp()}: Order {order_id} still {order['status']}. Sleeping for {wait_time} (total {total_wait_time})"
        )
        time.sleep(wait_time)
        total_wait_time += wait_time
        order = getOrder(order_id=order_id)
        
        if "cancel_message" in order or "reject_message" in order and \
                order["status"] not in ['OPEN', 'FILLED', 'UNKNOWN_ORDER_STATU']:
            # Most likely the order was manually cancelled in the UI
            sns.publish(
                TopicArn=sns_topic,
                Subject=f"{product_id} {side} order of {amount} {amount_currency} CANCELLED/REJECTED",
                Message=json.dumps(result, sort_keys=True, indent=4),
            )
            exit()

    # Order status is no longer pending!
    print(json.dumps(order, indent=2))

    market_price = Decimal(order["average_filled_price"]).quantize(quote_increment)

    subject = f"{product_id} {side} order of {amount} {amount_currency} {order['status']} @ {market_price} {quote_currency}"
    print(subject)
    sns.publish(
        TopicArn=sns_topic,
        Subject=subject,
        Message=json.dumps(order, sort_keys=True, indent=4),
    )

    return {
        'statusCode': 200,
        'body': json.dumps("BTCBOT Job Ended!")
    }


if __name__ == "__main__":
    context = {}
    event = {"attributes": {}}
    # event = {
    #     "attributes": {
    #         "product_id": "BTC-USD",
    #         "side": "BUY",
    #         "amount": "1.00",
    #         "amount_currency": "USD",
    #         "config_file": "./secrets.conf",
    #     }
    # }

    lambda_handler(event, context)
    