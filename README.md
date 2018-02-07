# gdax_bot
A basic GDAX buying bot that completes trades from fiat (typical government-backed currency like USD, EUR, etc) to a target crypto asset (BTC, ETH, LTC, BCH).

## Trading Philosophy
### GDAX overview; Trading with no fees
GDAX is a more professional cryptocurrency trading market that underlies Coinbase. If you have a Coinbase account, you have a GDAX account. All trades on Coinbase include a commission fee. But some trades on GDAX are free--specifically if you set your buy or sell price as a limit order. You are the "maker" of an offer and you await a "taker" to accept. The "takers" pay the fees, the "maker" pays none. The tradeoff is that limit orders may or may not be fulfilled; if you're selling X crypto at $Y value and no one likes your price, your sell order won't go anywhere.

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
gdax_bot pulls the current market price, subtracts a small spread to generate a valid buy price (see note below), then submits the buy as a limit order.

### Setup
#### Create a virtualenv
There's plenty of info elsewhere for the hows and whys.

#### Install requirements
```
pip install -r requirements.txt
```

#### Create GDAX API key
Try this out on the sandbox first. Log into your Coinbase/GDAX account in their test sandbox:
https://public.sandbox.gdax.com

Find and follow existing guides for creating an API key. Only grant the "Trade" permission. Note the passphrase, the new API key, and API key's secret.

#### (Optional) Create an AWS Simple Notification System topic
This is out of scope for this document, but generate a set of AWS access keys and a new SNS topic to enable the bot to send email reports.

TODO: Make this optional

#### Customize ```settings.conf```
Update ```settings.conf``` with your API key info in the "sandbox" section. I recommend saving your version as ```settings_local.conf``` as that is already in the ```.gitignore``` so you don't have to worry about committing your sensitive info to your forked repo.

If you have an AWS SNS topic, enter the access keys and SNS topic.

TODO: Read these values from environment vars

### Scheduling your recurring buys
This is meant to be run as a crontab to make regular purchases on a set schedule. Here are some example cron jobs:

$50 of ETH every Monday at 17:23:
```
23 17 * * 1 /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto ETH -fiat_amount 50.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log
```

$75 of BTC every other day at 14:00:
```
00 14 */2 * * /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto BTC -fiat_amount 75.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log
```

$5 of LTC every day on every third hour at the 38th minute (i.e. 00:38, 03:38, 06:38, 09:38, 12:38, 15:38, 18:38, 21:38):
```
38 */3 * * * /your/virtualenv/path/bin/python /your/gdax_bot/path/src/gdax_bot.py -j -crypto LTC -fiat_amount 5.00 -c /your/settings/path/your_settings_file.conf >> /your/cron/log/path/cron.log
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
I built this to execute my own micro dollar cost-averaging crypto buys. Use and modify it at your own risk. This is also not investment advice. I am not an investment advisor. You should do your own research and invest in the way that best suits your needs and risk profile.
