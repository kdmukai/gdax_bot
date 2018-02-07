# gdax_bot
A basic GDAX buying bot that completes trades from fiat (government-backed currency: USD, EUR, GBP) to a target crypto asset (BTC, ETH, LTC, BCH).

Relies on [gdax-python](https://github.com/danpaquin/gdax-python). Props to [danpaquin](https://github.com/danpaquin) and thanks!

## Trading Philosophy
### GDAX overview; Trading with no fees
GDAX is a more professional cryptocurrency exchange that underlies Coinbase. If you have a Coinbase account, you have a GDAX account. All trades on Coinbase include a commission fee. But some trades on GDAX are free--specifically if you set your buy or sell price as a limit order. You are the "maker" of an offer and you await a "taker" to accept. The "takers" pay the fees, the "maker" pays none. The tradeoff is that limit orders may or may not be fulfilled; if you're selling X crypto at $Y value and no one likes your price, your sell order won't go anywhere.

### Basic investing strategy: Dollar Cost Averaging
You have to be extremely lucky or extremely good to time the market perfectly. Rather than trying to achieve the perfect timing for when to execute a purchase just set up your investment on a regular schedule. Buy X amount every Y days. Sometimes the market will be up, sometimes down. But over time your cache will more closely reflect the average market price with volatile peaks and valleys averaged out.

This approach is common for retirement accounts; you invest a fixed amount into your 401(k) every month and trust that the market trend will be overall up over time.

### Micro Dollar Cost Averaging for cryptos
While I believe strongly in dollar cost averaging, the crypto world is so volatile that making a single, regular buy once a month is still leaving too much to chance. The market can swing 30%, 50%, even 100%+ in a single day. I'd rather invest $20 every day for a month than agonize over deciding on just the right time to do a single $600 buy.

And because we can do buy orders on GDAX with no fees (so long as they're submitted as limit orders), there's no penalty for splitting an order down to smaller intervals.

### How far can you push micro dollar cost averaging?
The current minimum limit order through GDAX's API is 0.01 of the target crypto. This combined with the price gives us the minimum fiat transaction amount.

For example, BTC is currently at $6,600 USD. So the smallest possible buy order is 0.01*$6,600 = $66. So if you were looking to invest $300 each month, you're constrained to at most four equal-sized buy orders ($75x4); you couldn't do five equal-sized orders because $300/5 = $60, which would only be 0.009090901 BTC. An order that small would be rejected by the API.

More interestingly, ETH is currently at $770 USD. So the smallest buy order is $7.70. Now your $300 investment can be split up into 38 equal parts. For simplicity let's just make it one buy per day--30 equal orders of $10.

It gets even more fun if you're either looking to invest more money or buy a cheaper crypto. If you have $900 to invest each month, you can split it into 90 equal parts--that's three equal buy orders per day: $10 of ETH every eight hours. If you had $500 to invest in LTC each month (currently at $137), you could go crazy-micro and do $1.39 every other hour! At this point your total average cost basis for your crypto should be just about identical to its average cost for the month.

I'm a big believer in this strategy for smoothing out crypto's short-term volatility while continuing to place your bets on its long-term value.

### Adjust as prices change
If the crypto price keeps increasing, eventually your schedule will run up against the minimum purchase order size; you can't buy $1.39 of LTC if the price is greater than $139 (remember there's a 0.01 minimum crypto purchase size). In that case you'll have to increase how much you buy in each order, but decrease the frequency of the orders.


## Technical Details
### Basic approach
gdax_bot pulls the current market price, subtracts a small spread to generate a valid buy price, then submits the buy as a limit order.

### Making a valid limit buy
Buy orders will be rejected if they are at or above the lowest sell order (think: too far right on the order book) (see: https://stackoverflow.com/a/47447663). When the price is plummeting this is likely to happen. In this case gdax_bot will pause for a minute and then grab the latest price and re-place the order. It will currently attempt this 100 times before it gives up.

_*Longer pauses are probably advantageous--if the price is crashing, you don't want to be rushing in._

### Setup
#### Create a virtualenv
There's plenty of info elsewhere for the hows and whys.

#### Install requirements
```
pip install -r requirements.txt
```

#### Create GDAX API key
Try this out on GDAX's sandbox first. The sandbox is a test environment that is not connected to your actual fiat or crypto balances.

Log into your Coinbase/GDAX account in their test sandbox:
https://public.sandbox.gdax.com

Find and follow existing guides for creating an API key. Only grant the "Trade" permission. Note the passphrase, the new API key, and API key's secret.

While you're in the sandbox UI, fund your fiat account by transferring from the absurd fake balance that sits in the linked Coinbase account (remember, this is all just fake test data; no real money or crypto goes through the sandbox).


#### (Optional) Create an AWS Simple Notification System topic
This is out of scope for this document, but generate a set of AWS access keys and a new SNS topic to enable the bot to send email reports.

_TODO: Make this optional_


#### Customize settings
Update ```settings.conf``` with your API key info in the "sandbox" section. I recommend saving your version as ```settings__local.conf``` as that is already in the ```.gitignore``` so you don't have to worry about committing your sensitive info to your forked repo.

If you have an AWS SNS topic, enter the access keys and SNS topic.

_TODO: Add support to read these values from environment vars_


#### Try a basic test run
Run against the GDAX sandbox by including the ```-sandbox``` flag. Remember that the sandbox is just test data. The sandbox only supports BTC trading.

Activate your virtualenv and try a basic $100 USD BTC buy:
```
python gdax_bot.py -crypto BTC -fiat_amount 100.00 -sandbox -c ../settings__local.conf
```

Check the sandbox UI and you'll see your limit order listed. Unfortunately your order probably won't fill unless there's other activity in the sandbox.


### Usage
Run ```python gdax_bot.py -h``` for usage information:

```
usage: gdax_bot.py [-h] [-crypto CRYPTO] [-fiat FIAT_TYPE] -fiat_amount
                   FIAT_AMOUNT [-price_spread PRICE_SPREAD] [-sandbox]
                   [-warn_after WARN_AFTER] [-j] [-c CONFIG_FILE]

This is a basic GDAX zero-fee buying bot

optional arguments:
  -h, --help            show this help message and exit
  -crypto CRYPTO        Target cryptocurrency
  -fiat FIAT_TYPE       Fiat currency type to fund buy order (e.g. USD)
  -fiat_amount FIAT_AMOUNT
                        Buy order size in fiat
  -price_spread PRICE_SPREAD
                        Amount below current market rate to set buy price
  -sandbox              Run against GDAX sandbox
  -warn_after WARN_AFTER
                        Seconds to wait before sending an alert that an order
                        isn't done
  -j, --job             Suppresses user confirmation prompt
  -c CONFIG_FILE, --config CONFIG_FILE
                        Override default config file location
```


### Scheduling your recurring buys
This is meant to be run as a crontab to make regular purchases on a set schedule. Here are some example cron jobs:

$50 USD of ETH every Monday at 17:23:
```
23 17 * * 1 /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto ETH -fiat_amount 50.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log
```

€75 EUR of BTC every other day at 14:00:
```
00 14 */2 * * /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto BTC -fiat EUR -fiat_amount 75.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log
```

£5 GBP of LTC every day on every third hour at the 38th minute (i.e. 00:38, 03:38, 06:38, 09:38, 12:38, 15:38, 18:38, 21:38):
```
38 */3 * * * /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto LTC -fiat GBP -fiat_amount 5.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log
```

Your Coinbase/GDAX account must obviously have enough USD in it to cover the buy order/series of buy orders.


#### Mac notes
Edit the crontab:
```
env EDITOR=nano crontab -e
```

View the current crontab:
```
crontab -l
```


## Disclaimer
_I built this to execute my own micro dollar cost-averaging crypto buys. Use and modify it at your own risk. This is also not investment advice. I am not an investment advisor. You should do your own research and invest in the way that best suits your needs and risk profile._
