# Enhanced Backtest — Bias & Parameter Research

Symbol **BTC** · 60 days · LTF **5m** · close invalidation · 2R target
Baseline executed trades: **622**

## 1. LTF FVG age (candles between 4H touch and LTF FVG c3 close)
Freshest gaps, formed immediately after the zone is touched, should carry the strongest imbalance.
```
    25+ (stale)      trades= 569  WR2R= 33.6%  net2R=  +4.0  PF= 1.01
    7-12             trades=  18  WR2R= 38.9%  net2R=  +3.0  PF= 1.27
    0-3 (freshest)   trades=   6  WR2R= 33.3%  net2R=  +0.0  PF=  1.0
    4-6              trades=   3  WR2R= 33.3%  net2R=  +0.0  PF=  1.0
    13-24            trades=  26  WR2R= 30.8%  net2R=  -2.0  PF= 0.89
```

## 2. Touch-to-entry age (LTF candles from 4H touch until entry fill)
```
    13+              trades= 606  WR2R= 33.7%  net2R=  +6.0  PF= 1.01
    7-12             trades=  12  WR2R= 33.3%  net2R=  +0.0  PF=  1.0
    4-6              trades=   3  WR2R= 33.3%  net2R=  +0.0  PF=  1.0
    0-3              trades=   1  WR2R=  0.0%  net2R=  -1.0  PF=  0.0
```

## 3. Distance of LTF FVG from 4H anchor zone
Closer-to-zone LTF FVGs coincide with the HTF imbalance and should retrace better.
```
    1-2%             trades= 196  WR2R= 36.2%  net2R= +17.0  PF= 1.14
    0.5-1%           trades=  86  WR2R= 36.0%  net2R=  +7.0  PF= 1.13
    <=0.5%           trades= 134  WR2R= 33.6%  net2R=  +1.0  PF= 1.01
    5%+              trades=   7  WR2R= 14.3%  net2R=  -4.0  PF= 0.33
    2-5%             trades= 199  WR2R= 30.7%  net2R= -16.0  PF= 0.88
```

## 4. LTF FVG gap size band
```
    0.05-0.1%        trades= 353  WR2R= 35.4%  net2R= +22.0  PF=  1.1
    0.6%+            trades=   2  WR2R=  0.0%  net2R=  -2.0  PF=  0.0
    0.3-0.6%         trades=  11  WR2R= 18.2%  net2R=  -5.0  PF= 0.44
    0.1-0.3%         trades= 256  WR2R= 32.0%  net2R= -10.0  PF= 0.94
```

## 5. 4H (HTF) anchor FVG age at time of LTF FVG formation
```
    11-20            trades= 158  WR2R= 36.7%  net2R= +16.0  PF= 1.16
    6-10             trades=  92  WR2R= 32.6%  net2R=  -2.0  PF= 0.97
    21+              trades= 157  WR2R= 32.5%  net2R=  -4.0  PF= 0.96
    0-5 4H candles   trades= 215  WR2R= 32.6%  net2R=  -5.0  PF= 0.97
```

## 6. Momentum impulse candle (c2) filter
```
    momentum         trades= 588  WR2R= 34.2%  net2R= +15.0  PF= 1.04
    no-momentum      trades=  34  WR2R= 23.5%  net2R= -10.0  PF= 0.62
```

## 7. Session filter (NY 13-22 UTC)
```
    NY-session       trades= 247  WR2R= 38.9%  net2R= +41.0  PF= 1.27
    non-NY           trades= 375  WR2R= 30.1%  net2R= -36.0  PF= 0.86
```

## 8. Risk per trade (SL distance as % of price)
```
    0.3-0.5%         trades= 123  WR2R= 36.6%  net2R= +12.0  PF= 1.15
    <0.3%            trades= 466  WR2R= 33.5%  net2R=  +2.0  PF= 1.01
    1%+              trades=   3  WR2R=  0.0%  net2R=  -3.0  PF=  0.0
    0.5-1%           trades=  30  WR2R= 26.7%  net2R=  -6.0  PF= 0.73
```