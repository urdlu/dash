CASES = {
"stock_full": """Subject: Your order has been filled
Date: Mon, 06 Oct 2026 14:03:22 -0700

Your order has been filled
Account: TFSA
Type: Market Buy
Symbol: HXS
Shares: 12
Average price: $78.10
Total value: $937.20
Time: October 6, 2026 2:03 pm""",
"stock_partial_frac": """Subject: Your order has been partially filled
Date: Tue, 7 Oct 2026 09:31:05 -0700
Your order has been partially filled
Account: RRSP
Type: Limit Sell
Symbol: VFV
Shares: 474 of 474.0385
Average price: US$150.25 USD
Total value: US$71,218.50 USD""",
"option_fill": """Subject: Your order has been filled
Date: Wed, 08 Oct 2026 10:15:00 -0700
**Your order has been filled**
Account: Margin
Type: Limit Sell to Open
Option: AAPL 230 call
Contracts: 2
Expiry: October 17, 2026
Average price: US$1.25
Total value: US$250.00
Time: October 8, 2026""",
"option_squashed": """Subject: Fwd: Your order has been filled
Date: Thu, 09 Oct 2026 11:00:00 -0700
Your order has been filled Account: TFSA Type: Market Buy to Close Option: SPY 500.5 put Contracts: 1 Expiry: Nov 21 Average price: $0.42 Total value: $42.00 Time: today""",
"multi_leg": """Subject: Your order has been filled
Date: Fri, 10 Oct 2026 12:00:00 -0700
Account: Margin
Type: Limit
Options strategy: Iron condor
Contracts: 1
Sell to open 1 SPY October 24 $560 Put
Buy to open 1 SPY October 24 $555 Put
Sell to open 1 SPY January 16 $600.5 Call
Buy to open 1 SPY October 24 $605 Call
Total premium: US$1,234.56 USD
Time: October 10, 2026 12:00 pm""",
"multi_leg_cost": """Subject: Your order has been filled
Date: Fri, 10 Oct 2026 12:05:00 -0700
Account: Margin
Options strategy: Calendar
Buy to open 2 QQQ November 21 $480 Call
Sell to open 2 QQQ October 31 $480 Call
Total cost: $310.00
Time: october 10, 2026""",
"cancel": """Subject: Your order has been cancelled
Date: Mon, 13 Oct 2026 08:00:00 -0700
Your order has been cancelled
Account: TFSA
Type: Limit Buy
Symbol: HXQ""",
"exercise": """Subject: Your options have been exercised or assigned
Date: Sat, 18 Oct 2026 06:00:00 -0700
Your options have been exercised or assigned
Account: Margin
Your options were exercised and assigned on October 17, 2026.
AAPL $230 Call
-2 contracts
Assigned
Sold 200 shares
Total Cost: $46,000.00 USD""",
"dividend": """Subject: You earned a dividend
Date: Wed, 15 Oct 2026 07:00:00 -0700
You earned a dividend
Account: TFSA
Symbol: ZWC
Amount: $12.34""",
"dividend_nosym": """Subject: You earned a dividend
Date: Wed, 15 Oct 2026 07:00:00 -0700
You earned a dividend
Account: TFSA
Amount: $0.5""",
"transfer": """Subject: Transfer
Date: Thu, 16 Oct 2026 07:00:00 -0700
You made a transfer
Amount: $1,000
From: Cash
To: TFSA""",
"fx": """Subject: Funds converted
Date: Thu, 16 Oct 2026 07:30:00 -0700
Your funds have been converted
Account: Margin
Amount submitted: $1,000.00 CAD
Amount received: US$720.15 USD""",
"deposit": """Subject: Direct deposit received
Date: Thu, 16 Oct 2026 08:30:00 -0700
Amount: $2,500.00
From: ACME PAYROLL""",
"wire": """Subject: Wire transfer received
Date: Thu, 16 Oct 2026 09:30:00 -0700
Wire transfer received
Amount: $10,000.00
From: RBC
To: Cash
Status: Completed""",
"ignored": """Subject: Your credit card statement is ready
Date: Thu, 16 Oct 2026 09:30:00 -0700
Hello""",
"unrecognized": """Subject: Hello
Date: Thu, 16 Oct 2026 09:30:00 -0700
Something else""",
"weird_date": """Subject: Your order has been filled
Date: Mon, 6 Oct 2026 14:03
Your order has been filled
Account: TFSA
Type: Market Buy
Symbol: HXS
Shares: 1
Average price: $78
Total value: $78""",
"unicode": """Subject: Your order has been filled
Date: Mon, 06 Oct 2026 14:03:22 -0700
Your order has been filled​
Account:͏ TFSA‌
Type: Market Buy
Symbol: XEQT
Shares: 3.5
Average price: $30.00
Total cost: $105.00""",
}
