#!/usr/bin/env python

import argparse
import ConfigParser
import boto3
import datetime
import json
import time

import gdax


def get_timestamp():
    ts = time.time()
    return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


"""
    Basic GDAX buying bot that pulls the current market price, subtracts a small
        spread to generate a valid buy price (see note below), then submits the
        buy as a limit order which avoids all GDAX fees.

    Note: GDAX API limits buy size to a minimum of 0.01 of the target crypto.

    This is meant to be run as a crontab to make regular purchases on a set
        schedule. Example job that runs every third hour at the 38th minute. 
        i.e. 00:38, 03:38, 06:38, 09:38, 12:38, 15:38, 18:38, 21:38

        38 */3 * * * /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto ETH -fiat_amount 8.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log

    Your Coinbase/GDAX account must obviously have enough USD in it to cover the
        buy order/series of buy orders.
"""
parser = argparse.ArgumentParser(description='This is a basic GDAX zero-fee buying bot')

parser.add_argument('-crypto', default="ETH", dest="crypto", help="Target cryptocurrency")
parser.add_argument('-fiat', default="USD", dest="fiat_type", help="Fiat currency type to fund buy order (e.g. USD)")
parser.add_argument('-fiat_amount', required=True, action="store", type=float, dest="fiat_amount", help="Buy order size in fiat")
parser.add_argument('-price_spread', default=0.01, action="store", type=float, dest="price_spread", help="Amount below current market rate to set buy price")
parser.add_argument('-sandbox', action="store_true", default=False, dest="sandbox_mode", help="Run against GDAX sandbox")
parser.add_argument('-warn_after', default=3600, action="store", type=int, dest="warn_after", help="Seconds to wait before sending an alert that an order isn't done")
parser.add_argument('-j', '--job', action="store_true", default=False, dest="job_mode", help="Suppresses user confirmation prompt")
parser.add_argument('-c', '--config', default="../settings.conf", dest="config_file", help="Override default config file location")

args = parser.parse_args()
print "%s: STARTED: %s" % (get_timestamp(), args)

crypto = args.crypto
fiat_type = args.fiat_type
purchase_pair = "%s-%s" % (crypto, fiat_type)
fiat_amount = args.fiat_amount
price_spread = args.price_spread
sandbox_mode = args.sandbox_mode
job_mode = args.job_mode
warn_after = args.warn_after


if not sandbox_mode and not job_mode:
    response = raw_input("Production purchase! Confirm [Y]: ")
    if response != 'Y':
        print "Exiting without submitting purchase."
        exit()


# Read config and set up auth_client
config = ConfigParser.SafeConfigParser()
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


if not args.sandbox_mode:
    auth_client = gdax.AuthenticatedClient(key, secret, passphrase)
else:
    # Use the sandbox API (requires a different set of API access credentials)
    auth_client = gdax.AuthenticatedClient(key, secret, passphrase, api_url="https://api-public.sandbox.gdax.com")

public_client = gdax.PublicClient()


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
result = {}
while cur_attempt <= max_attempts:
    # Get the current price...
    ticker = public_client.get_product_ticker(product_id=purchase_pair)
    current_price = float(ticker['price'])

    # Can't submit a buy order at or above current market price
    offer_price = round(current_price - price_spread, 2)
    print "offer_price: %0.2f" % offer_price

    # ...place a limit buy order to avoid taker fees
    crypto_amount = round(fiat_amount / current_price, 8)
    print "crypto_amount: %0.8f" % crypto_amount
    result = auth_client.buy(   type='limit',
                                post_only=True,      # Ensure that it's treated as a limit order
                                price=offer_price, # price in fiat
                                size=crypto_amount,  # cryptocoin quantity
                                product_id=purchase_pair)

    print json.dumps(result, sort_keys=True, indent=4)

    if "message" in result:
        # Something went wrong if there's a 'message' field in response
        sns.publish(
            TopicArn=sns_topic,
            Subject="Could not place order for $%0.2f of %s" % (fiat_amount, crypto),
            Message=json.dumps(result, sort_keys=True, indent=4)
        )
        exit()

    if result["status"] == "rejected":
        # Rejected - usually because price was above lowest sell offer
        print "%s: Order rejected @ $%0.2f" % (get_timestamp(), current_price)
        time.sleep(attempt_wait)
        cur_attempt += 1
    else:
        break


if cur_attempt == max_attempts:
    # Was never able to place an order
    sns.publish(
        TopicArn=sns_topic,
        Subject="Could not place order for $%0.2f of %s after %d attempts" % (fiat_amount, crypto, max_attempts),
        Message=json.dumps(result, sort_keys=True, indent=4)
    )
    exit()



order = result
order_id = order["id"]
print "order_id: " + order_id

sns = boto3.client(
    "sns",
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name="us-east-1"     # N. Virginia
)

sns.publish(
    TopicArn=sns_topic,
    Subject="$%0.2f buy submitted | %0.4f %s @ $%0.2f" % (fiat_amount, crypto_amount, crypto, current_price),
    Message=json.dumps(result, sort_keys=True, indent=4)
)


wait_time = 60
total_wait_time = 0
while "status" in order and (order["status"] == "pending" or order["status"] == "open"):
    if total_wait_time > warn_after:
        sns.publish(
            TopicArn=sns_topic,
            Subject="$%0.2f buy OPEN/UNFILLED | %0.4f %s @ $%0.2f" % (fiat_amount, crypto_amount, crypto, current_price),
            Message=json.dumps(order, sort_keys=True, indent=4)
        )
        exit()

    print "%s: Order %s still %s. Sleeping for %d (total %d)" % (get_timestamp(), order_id, order["status"], wait_time, total_wait_time)
    time.sleep(wait_time)      # Wait 60 seconds
    total_wait_time += wait_time
    order = auth_client.get_order(order_id)
    print json.dumps(order, sort_keys=True, indent=4)

    if "message" in order and order["message"] == "NotFound":
        # Most likely the order was manually deleted in the UI
        sns.publish(
            TopicArn=sns_topic,
            Subject="$%0.2f buy CANCELLED | %0.4f %s @ $%0.2f" % (fiat_amount, crypto_amount, crypto, current_price),
            Message=json.dumps(result, sort_keys=True, indent=4)
        )
        exit()


# Order status is no longer pending!
sns.publish(
    TopicArn=sns_topic,
    Subject="$%0.2f buy %s | %0.4f %s @ $%0.2f" % (fiat_amount, order["status"], crypto_amount, crypto, current_price),
    Message=json.dumps(order, sort_keys=True, indent=4)
)

print "%s: DONE: $%0.2f buy %s | %0.4f %s @ $%0.2f" % (get_timestamp(), fiat_amount, order["status"], crypto_amount, crypto, current_price)




