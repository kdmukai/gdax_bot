#!/usr/bin/env python

import argparse
import boto3
import configparser
import datetime
import decimal
import json
import math
import sys
import time

import gdax

from decimal import Decimal


def get_timestamp():
    ts = time.time()
    return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


"""
    Basic Coinbase Pro DCA buy/sell bot that pulls the current market price, subtracts a
        small spread to generate a valid price (see note below), then submits the trade as
        a limit order.

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
    formatter_class=argparse.RawTextHelpFormatter
)

# Required positional arguments
parser.add_argument('market_name', help="(e.g. BTC-USD, ETH-BTC, etc)")

parser.add_argument('order_side',
                    type=str,
                    choices=["BUY", "SELL"])

parser.add_argument('amount',
                    type=Decimal,
                    help="The quantity to buy or sell in the amount_currency")

parser.add_argument('amount_currency',
                    help="The currency the amount is denominated in")


# Additional options
parser.add_argument('-sandbox',
                    action="store_true",
                    default=False,
                    dest="sandbox_mode",
                    help="Run against sandbox, skips user confirmation prompt")

parser.add_argument('-warn_after',
                    default=3600,
                    action="store",
                    type=int,
                    dest="warn_after",
                    help="secs to wait before sending an alert that an order isn't done")

parser.add_argument('-j', '--job',
                    action="store_true",
                    default=False,
                    dest="job_mode",
                    help="Suppresses user confirmation prompt")

parser.add_argument('-c', '--config',
                    default="settings.conf",
                    dest="config_file",
                    help="Override default config file location")



if __name__ == "__main__":
    args = parser.parse_args()
    print("%s: STARTED: %s" % (get_timestamp(), args))

    market_name = args.market_name
    order_side = args.order_side.lower()
    amount = args.amount
    amount_currency = args.amount_currency

    sandbox_mode = args.sandbox_mode
    job_mode = args.job_mode
    warn_after = args.warn_after

    if not sandbox_mode and not job_mode:
        if sys.version_info[0] < 3:
            # python2.x compatibility
            response = raw_input("Production purchase! Confirm [Y]: ")  # noqa: F821
        else:
            response = input("Production purchase! Confirm [Y]: ")
        if response != 'Y':
            print("Exiting without submitting purchase.")
            exit()

    # Read settings
    config = configparser.ConfigParser()
    config.read(args.config_file)

    config_section = 'production'
    if sandbox_mode:
        config_section = 'sandbox'
    key = config.get(config_section, 'API_KEY')
    passphrase = config.get(config_section, 'PASSPHRASE')
    secret = config.get(config_section, 'SECRET_KEY')
    aws_access_key_id = config.get(config_section, 'AWS_ACCESS_KEY_ID')
    aws_secret_access_key = config.get(config_section, 'AWS_SECRET_ACCESS_KEY')
    sns_topic = config.get(config_section, 'SNS_TOPIC')

    # Instantiate public and auth API clients
    if not args.sandbox_mode:
        auth_client = gdax.AuthenticatedClient(key, secret, passphrase)
    else:
        # Use the sandbox API (requires a different set of API access credentials)
        auth_client = gdax.AuthenticatedClient(
            key,
            secret,
            passphrase,
            api_url="https://api-public.sandbox.pro.coinbase.com")

    public_client = gdax.PublicClient()

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
                raise Exception("amount_currency %s not in market %s" % (amount_currency,
                                                                         market_name))
            print(product)

    print("base_min_size: %s" % base_min_size)
    print("quote_increment: %s" % quote_increment)

    # Prep boto SNS client for email notifications
    sns = boto3.client(
        "sns",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name="us-east-1"     # N. Virginia
    )


    """
        Buy orders will be rejected if they are at or above the lowest sell order
          (think: too far right on the order book). When the price is plummeting
          this is likely to happen. In this case pause for Y amount of time and
          then grab the latest price and re-place the order. Attempt X times before
          giving up.

        *Longer pauses are probably advantageous--if the price is crashing, you
          don't want to be rushing in.

        see: https://stackoverflow.com/a/47447663
    """
    max_attempts = 100
    attempt_wait = 60
    cur_attempt = 1
    result = None
    while cur_attempt <= max_attempts:
        # Get the current price...
        ticker = public_client.get_product_ticker(product_id=market_name)
        if 'price' not in ticker:
            # Cannot proceed with order. Coinbase Pro is likely under maintenance.
            if 'message' in ticker:
                sns.publish(
                    TopicArn=sns_topic,
                    Subject="%s order aborted" % (market_name),
                    Message=ticker.get('message')
                )
                print(ticker.get('message'))
            print("%s order aborted" % (market_name))
            exit()

        current_price = Decimal(ticker['price'])
        bid = Decimal(ticker['bid'])
        ask = Decimal(ticker['ask'])
        if order_side == "buy":
            rounding = decimal.ROUND_DOWN
        else:
            rounding = decimal.ROUND_UP
        midmarket_price = ((ask + bid) / Decimal('2.0')).quantize(quote_increment,
                                                                  rounding)
        print("bid: %s %s" % (bid, quote_currency))
        print("ask: %s %s" % (ask, quote_currency))
        print("midmarket_price: %s %s" % (midmarket_price, quote_currency))

        offer_price = midmarket_price
        print("offer_price: %s %s" % (offer_price, quote_currency))

        # Quantize by the base_increment limitation (in some cases this is as large as 1)
        if amount_currency_is_quote_currency:
            # Convert 'amount' of the quote_currency to equivalent in base_currency
            base_currency_amount = (amount / current_price).quantize(base_increment)
            amount_quantization = quote_increment
        else:
            # Already in base_currency
            base_currency_amount = amount.quantize(base_increment)
            amount_quantization = base_increment

        print("base_currency_amount: %s %s" % (base_currency_amount, base_currency))

        if order_side == "buy":
            result = auth_client.buy(type='limit',
                                     post_only=True,             # Ensure that it's treated as a limit order
                                     price=float(offer_price),   # price in quote_currency
                                     size=float(base_currency_amount),  # quantity of base_currency to buy
                                     product_id=market_name)

        elif order_side == "sell":
            result = auth_client.sell(type='limit',
                                      post_only=True,             # Ensure that it's treated as a limit order
                                      price=float(offer_price),   # price in quote_currency
                                      size=float(base_currency_amount),  # quantity of base_currency to sell
                                      product_id=market_name)

        print(json.dumps(result, sort_keys=True, indent=4))

        if "message" in result and "Post only mode" in result.get("message"):
            # Price moved away from valid order
            print("Post only mode at %f %s" % (offer_price, quote_currency))

        elif "message" in result:
            # Something went wrong if there's a 'message' field in response
            sns.publish(
                TopicArn=sns_topic,
                Subject="Could not place %s %s order for %f %s" % (market_name,
                                                                   order_side,
                                                                   amount.quantize(amount_quantization),
                                                                   amount_currency),
                Message=json.dumps(result, sort_keys=True, indent=4)
            )
            exit()

        if result and "status" in result and result["status"] != "rejected":
            break

        if result and "status" in result and result["status"] == "rejected":
            # Rejected - usually because price was above lowest sell offer. Try
            #   again in the next loop.
            print("%s: %s Order rejected @ %f %s" % (get_timestamp(),
                                                     market_name,
                                                     current_price,
                                                     quote_currency))

        time.sleep(attempt_wait)
        cur_attempt += 1


    if cur_attempt > max_attempts:
        # Was never able to place an order
        sns.publish(
            TopicArn=sns_topic,
            Subject="Could not place %s %s order for %f %s after %d attempts" % (
                market_name, order_side, amount.quantize(amount_quantization), amount_currency, max_attempts
            ),
            Message=json.dumps(result, sort_keys=True, indent=4)
        )
        exit()


    order = result
    order_id = order["id"]
    print("order_id: " + order_id)


    '''
        Wait to see if the limit order was fulfilled.
    '''
    wait_time = 60
    total_wait_time = 0
    while "status" in order and \
            (order["status"] == "pending" or order["status"] == "open"):
        if total_wait_time > warn_after:
            sns.publish(
                TopicArn=sns_topic,
                Subject="%s %s order of %f %s OPEN/UNFILLED @ %s %s" % (
                    market_name,
                    order_side,
                    amount.quantize(amount_quantization),
                    amount_currency,
                    offer_price,
                    quote_currency
                ),
                Message=json.dumps(order, sort_keys=True, indent=4)
            )
            exit()

        print("%s: Order %s still %s. Sleeping for %d (total %d)" % (
            get_timestamp(),
            order_id,
            order["status"],
            wait_time,
            total_wait_time))
        time.sleep(wait_time)
        total_wait_time += wait_time
        order = auth_client.get_order(order_id)
        # print(json.dumps(order, sort_keys=True, indent=4))

        if "message" in order and order["message"] == "NotFound":
            # Most likely the order was manually cancelled in the UI
            sns.publish(
                TopicArn=sns_topic,
                Subject="%s %s order of %f %s CANCELED @ %s %s" % (
                    market_name,
                    order_side,
                    amount.quantize(amount_quantization),
                    amount_currency,
                    offer_price,
                    quote_currency
                ),
                Message=json.dumps(result, sort_keys=True, indent=4)
            )
            exit()


    # Order status is no longer pending!
    sns.publish(
        TopicArn=sns_topic,
        Subject="%s %s order of %f %s %s @ %s %s" % (
            market_name,
            order_side,
            amount.quantize(amount_quantization),
            amount_currency,
            order["status"],
            offer_price,
            quote_currency
        ),
        Message=json.dumps(order, sort_keys=True, indent=4)
    )

    print("%s: DONE: %s %s order of %f %s %s @ %s %s" % (
        get_timestamp(),
        market_name,
        order_side,
        amount.quantize(amount_quantization),
        amount_currency,
        order["status"],
        offer_price,
        quote_currency))
