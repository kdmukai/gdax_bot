# gdax_bot

Basic GDAX buying bot that pulls the current market price, subtracts a small spread to generate a valid buy price (see note below), then submits the buy as a limit order which avoids all GDAX fees.

Note: GDAX API limits buy size to a minimum of 0.01 of the target crypto.

This is meant to be run as a crontab to make regular purchases on a set schedule. Example job that runs every third hour at the 38th minute.  i.e. 00:38, 03:38, 06:38, 09:38, 12:38, 15:38, 18:38, 21:38

```38 */3 * * * /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto ETH -fiat_amount 8.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log```

Your Coinbase/GDAX account must obviously have enough USD in it to cover the buy order/series of buy orders.
