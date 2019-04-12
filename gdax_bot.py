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
    Basic GDAX buying bot that pulls the current market price, subtracts a
        small spread to generate a valid buy price (see note below), then
        submits the buy as a limit order which avoids all GDAX fees. No one
        likes fees.

    Note: GDAX API limits buy size to a minimum of 0.01 of the target crypto.

    This is meant to be run as a crontab to make regular purchases on a set
        schedule.

    Your Coinbase/GDAX account must obviously have enough USD in it to cover
        the buy order/series of buy orders.
"""
parser = argparse.ArgumentParser(
    description='This is a basic Coinbase Pro buying bot')

parser.add_argument('-crypto',
                    choices=['BTC', 'ETH', 'XLM', 'LTC', 'BCH', 'ZRX', 'EOS'],
                    required=True,
                    dest="crypto",
                    help="Target cryptocurrency")
parser.add_argument('-fiat_amount',
                    required=True,
                    action="store",
                    type=Decimal,
                    dest="fiat_amount",
                    help="Buy order size in fiat")

# Additional options
parser.add_argument('-fiat',
                    choices=['USD', 'EUR', 'GBP'],
                    default="USD",
                    dest="fiat_type",
                    help="Fiat currency type to fund buy order (e.g. USD)")
parser.add_argument('-sandbox',
                    action="store_true",
                    default=False,
                    dest="sandbox_mode",
                    help="Run against GDAX sandbox, skips user confirmation prompt")
parser.add_argument('-warn_after',
                    default=3600,
                    action="store",
                    type=int,
                    dest="warn_after",
                    help="Seconds to wait before sending an alert that an order isn't done")
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

    crypto = args.crypto
    fiat_type = args.fiat_type
    purchase_pair = "%s-%s" % (crypto, fiat_type)
    fiat_amount = args.fiat_amount
    sandbox_mode = args.sandbox_mode
    job_mode = args.job_mode
    warn_after = args.warn_after

    if fiat_type == 'USD':
        fiat_symbol = '$'
    elif fiat_type == 'EUR':
        fiat_symbol = u"\u20ac"
    elif fiat_type == 'GBP':
        fiat_symbol = u"\xA3"

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

    smallest_unit = Decimal(config.get('smallest_units', crypto))

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
    quote_increment = None
    for product in products:
        if product.get("id") == purchase_pair:
            base_min_size = Decimal(product.get("base_min_size")).normalize()
            quote_increment = Decimal(product.get("quote_increment")).normalize()
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
        ticker = public_client.get_product_ticker(product_id=purchase_pair)
        if 'price' not in ticker:
            # Cannot proceed with order. GDAX is likely under maintenance.
            if 'message' in ticker:
                sns.publish(
                    TopicArn=sns_topic,
                    Subject="%s order aborted" % (purchase_pair),
                    Message=ticker.get('message')
                )
                print(ticker.get('message'))
            print("%s order aborted" % (purchase_pair))
            exit()

        current_price = Decimal(ticker['price'])
        bid = Decimal(ticker['bid'])
        ask = Decimal(ticker['ask'])
        midmarket_price = ((ask + bid) / Decimal('2.0')).quantize(quote_increment, decimal.ROUND_DOWN)
        print("bid: $%s" % bid)
        print("ask: $%s" % ask)
        print("midmarket_price: $%s" % midmarket_price)

        offer_price = midmarket_price
        print("offer_price: $%s" % offer_price)

        # ...place a limit buy order to avoid taker fees
        # Quantize by the smallest_unit limitation (in some cases this is as large as 1)
        crypto_amount = (fiat_amount / current_price).quantize(smallest_unit)
        print("crypto_amount: %s" % crypto_amount)

        if crypto_amount > Decimal('0.0'):
            # Buy amount is over the min threshold, attempt to submit order
            result = auth_client.buy(   type='limit',
                                        post_only=True,             # Ensure that it's treated as a limit order
                                        price=float(offer_price),   # price in fiat
                                        size=float(crypto_amount),  # cryptocoin quantity
                                        product_id=purchase_pair)

            print(json.dumps(result, sort_keys=True, indent=4))

            if "message" in result:
                # Something went wrong if there's a 'message' field in response
                sns.publish(
                    TopicArn=sns_topic,
                    Subject="Could not place order for %s%0.2f of %s" % (fiat_symbol, fiat_amount, crypto),
                    Message=json.dumps(result, sort_keys=True, indent=4)
                )
                exit()

        else:
            # Order was too small. Will have to try again in the next loop to see
            #   if the price drops low enough for us to buy at or above the
            #   minimum order size.
            print("crypto_amount %0.08f is below the minimum %s order size (%0.3f)" % (crypto_amount, crypto, min_crypto_amount))
            exit()

        if result and result["status"] != "rejected":
            break

        if result and result["status"] == "rejected":
            # Rejected - usually because price was above lowest sell offer. Try
            #   again in the next loop.
            print("%s: %s Order rejected @ %s%0.2f" % (get_timestamp(), crypto, fiat_symbol, current_price))

        time.sleep(attempt_wait)
        cur_attempt += 1


    if cur_attempt > max_attempts:
        # Was never able to place an order
        sns.publish(
            TopicArn=sns_topic,
            Subject="Could not place order for %s%0.2f of %s after %d attempts" % (fiat_symbol, fiat_amount, crypto, max_attempts),
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
    while ("status" in order
            and (order["status"] == "pending" or order["status"] == "open")):
        if total_wait_time > warn_after:
            sns.publish(
                TopicArn=sns_topic,
                Subject="%s%0.2f buy OPEN/UNFILLED | %s %s @ %s%s" % (fiat_symbol, fiat_amount, crypto_amount, crypto, fiat_symbol, offer_price),
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
                Subject="%s%0.2f buy CANCELED | %s %s @ %s%s" % (fiat_symbol, fiat_amount, crypto_amount, crypto, fiat_symbol, offer_price),
                Message=json.dumps(result, sort_keys=True, indent=4)
            )
            exit()


    # Order status is no longer pending!
    sns.publish(
        TopicArn=sns_topic,
        Subject="%s%s buy %s | %s %s @ %s%s" % (
            fiat_symbol,
            fiat_amount,
            order["status"],
            crypto_amount,
            crypto,
            fiat_symbol,
            offer_price),
        Message=json.dumps(order, sort_keys=True, indent=4)
    )

    print("%s: DONE: %s%0.2f buy %s | %s %s @ %s%s" % (
        get_timestamp(),
        fiat_symbol,
        fiat_amount,
        order["status"],
        crypto_amount,
        crypto,
        fiat_symbol,
        offer_price))
