# Bevindingen

Data: 46,419 candles van 5 minuten XAUUSD, 2026-02-02 → 2026-09-25 (UTC).
Ontwikkelperiode: tot 2026-07-01, testperiode: daarna.

## Vaste regels (vooraf vastgelegd, niet afgesteld)
Prijs aan de trendkant van een hellende EMA 21, entry met een stop-order op de break van de
vorige high/low (candle 2), stop 10 cent voorbij de pullback-extreme, de helft op 2R, stop naar
breakeven, runner tot een slotkoers aan de andere kant van de EMA. $0,30 kosten per trade.
Raken stop en doel dezelfde candle, dan telt de stop (pessimistisch).

| Sessie | Dev trades | Dev R | Test trades | Test R | Test R (optimistisch) |
|---|---:|---:|---:|---:|---:|
| Asia 00:00–07:00 UTC | 255 | -29.0R | 126 | +11.5R | +13.5R |
| London 07:00–12:00 UTC | 202 | -68.1R | 105 | -19.2R | -0.5R |
| New York 12:00–17:00 UTC | 179 | -32.9R | 111 | -25.7R | -2.7R |
| Late US 17:00–22:00 UTC | 166 | -49.4R | 78 | -3.7R | +3.7R |
| Journalvenster 04:00–06:30 UTC (12:00–14:30 China) | 88 | +21.5R | 56 | +14.3R | +18.3R |

## Conclusies
1. **Heel Asia is niet de beste sessie.** Alle volledige sessies verliezen in de ontwikkelperiode.
2. **Het venster uit zijn journal (04:00–06:30 UTC = 12:00–14:30 China) is het enige dat in beide
   periodes wint**: +21.5R en +14.3R.
   Het 95%-interval van de gemiddelde R in de test is [-0.184, 0.747], dus nul ligt er nog in.
3. **De winst is geconcentreerd.** Eén runner van +31R in maart is een groot deel van het totaal. Zonder de 5 beste trades
   is het venster negatief. Per uur zit vrijwel alle winst in 04:00–05:00 UTC.
4. **Zijn winrate (41 van 42 TP) haalt de bot nergens**; de bot wint ~37%.
   Zijn keuze welke setups hij neemt (en zijn exits, soms 1:1) doet dus veel van het werk.
5. Filters op key levels en EMA-touch, gekozen op de ontwikkelperiode, verbeteren de testperiode niet betrouwbaar.

## Vergelijking met zijn screenshots
- 31 juli: de bot shortte om 06:50 UTC, hij rond 06:50 UTC. Zijn andere zes trades die dag nam de bot niet.
- 4 augustus: de bot ging om 11:25 UTC long, hij rond 11:05 UTC.

## Journal
42 trades, 41 TP, alle entries 04:15–06:25 UTC; vooral 05:00–06:00 UTC. Geen datums, dus niet
te koppelen aan candles. De kolom PIPS (mediaan TP ~4.800, enige SL −200) is niet om te rekenen
naar R. Betekenis van "F" in F2EL/F2ES onbekend.

## Wat de bot verder zou helpen
- Zijn trades **met datum**, entry, stop en exit, plus setups die hij **bewust oversloeg**.
- Zijn eigen key levels per dag (screenshot voor de sessie start).
- Data van zijn broker (FXCM / Capital.com); onze feed ligt ~$8–16 hoger, met dezelfde vorm.

# Timeframes en markten

`python -m friendsbot timeframes` → `reports/timeframes.json`. Zelfde vaste regels, 2026-02-02 → 2026-09-25,
ontwikkeling tot 2026-07-01, test daarna. Elke timeframe is opgebouwd uit dezelfde 1-minutencandles en
elke trade wordt op 1-minutencandles uitgevoerd (`friendsbot/execution.py`), dus ook binnen een 4h-candle
telt wat eerst geraakt werd. Kosten: goud $0,30, US100 1,5 punt, BTCUSDT 0,08% per trade.

Resultaat na kosten, hele dag, exit "½ op 2R + runner" (ontwikkeling / test):

| Markt | 1m | 5m | 15m | 30m | 1h | 4h |
|---|---|---|---|---|---|---|
| Goud | −553 / −369 | −101 / −8 | +33 / −17 | −19 / −4 | **+4 / +6** | −8 / −2 |
| US100 | −558 / −570 | +19 / −36 | **+22 / +24** | −30 / +10 | **+10 / +2** | **+1 / +1** |
| BTCUSDT | −9470 / −6651 | −749 / −519 | −137 / −19 | −3 / −25 | **+0 / +4** | +2 / −8 |

## Conclusies
1. **1 minuut werkt op geen enkele markt.** Vóór kosten ongeveer nul; de kosten zijn 0,14–0,17R per trade
   (goud, US100) en 1,9R per trade op bitcoin.
2. **US100 op 15 minuten** is de stabielste combinatie: positief in beide periodes met alle drie de exits.
   Met een vaste exit op 1R: 499 trades, 57% winrate, +43R, 95%-interval gemiddelde R [−0,00; +0,17],
   6 van de 8 maanden positief. Met de runner-exit hangt het aan uitschieters (zonder top 5: −4,5R).
3. **Goud**: op 5m wint alleen het journalvenster (04:00–06:30 UTC). Op de hele dag is 1h klein positief.
4. **Bitcoin**: fees maken 1m–30m onrendabel; 1h staat rond nul.
5. 18 combinaties getest: één goede combinatie kan toeval zijn. Volgende stap is US100 15m vooruit
   paper-traden voordat er geld op gezet wordt.


# Eén bot over goud, US100 en BTC (`python -m friendsbot portfolio`)

De bot kiest per markt en sessie (Asia, London, New York, Late US) de timeframe met de beste gemiddelde R
in februari–juni (minstens 20 trades, gemiddeld ≥ +0,05R), en wordt daarna één keer getest op juli–september.

| Portefeuille | Feb–jun trades | Feb–jun | Jul–sep trades | Jul–sep | Max. drawdown |
|---|---:|---:|---:|---:|---:|
| Bot-keuze (feb–jun), exit 1R/goede setup+runner | 409 | +69.5R | 245 | +4.5R | 41.4R |
| Jouw selectie*, exit 1R/goede setup+runner | 286 | +5.9R | 173 | +89.4R | 45.7R |
| Bot-keuze (feb–jun), exit 1R | 245 | +43.6R | 166 | +7.6R | 18.9R |
| Jouw selectie*, exit 1R | 299 | -12.1R | 179 | -10.8R | 45.3R |
| Bot-keuze (feb–jun), exit ½ op 2R + runner | 383 | +85.0R | 240 | +15.3R | 58.4R |
| Jouw selectie*, exit ½ op 2R + runner | 278 | +24.2R | 170 | +105.7R | 28.0R |

\* Jouw selectie (US100 15m NY, goud 15m + 1h Asia, US100 1h Asia, BTC 15m London) is gekozen na het zien
van de juli–septembercijfers, dus die kolom is geen eerlijke test.

## Conclusies
1. Kiezen op het verleden en handelen in de toekomst laat het grootste deel van de schijnbare winst verdwijnen:
   +69,5R in de selectieperiode wordt +4,5R in de test (exit van je vriend). Positief, maar binnen de ruis.
2. Jouw selectie haalde vóóraf (feb–jun) maar +5,9R; de +89R in de test komt vooral van BTC London 15m, die in
   feb–jun −18,9R verloor.
3. TP-regel van je vriend ("1R, goede setup 1R + runner"): de runner levert op 15m/1h meer op dan alles op 1R
   (+0,089R tegenover +0,034R per trade bij trendstart), maar een eenvoudige regel markeert 90% van de setups als
   "goed". Zijn eigen oordeel is strenger dan wat de bot nu kan nabootsen.
4. Een multi-market bot kan, maar dan met een vaste selectieregel en eerst maanden papier-trading.

# Correctie 10 oktober 2026: tijdseenheden in de 1-minuut-engine
De 1-minutendata heeft tijdstempels in microseconden; de engine vergeleek ze met grenzen in nanoseconden.
Daardoor werden de dagelijkse pauze en het weekend nooit herkend en werkte de maximale houdtijd niet: trades
bleven in de praktijk altijd doorlopen. Hersteld in `execution.py` (alles naar nanoseconden) met een test.
Alle rapporten (timeframes, portefeuille, papierjournal) zijn opnieuw berekend.

## Laten lopen of sluiten? (walk-forward papierbot, juni – 10 oktober)
| Open trades bij een pauze | Resultaat | Account | Max. drawdown |
|---|---:|---:|---:|
| Sluiten voor pauze en weekend | −39,4R | −8,3% | 14,5% |
| Door de pauze, sluiten voor het weekend | −47,6R | −9,2% | 13,1% |
| **Altijd laten lopen** | **−15,3R** | **−4,9%** | **11,6%** |
| Sluiten aan einde van de sessie | −47,0R | −12,2% | 16,9% |

Een stop die door een gap wordt overgeslagen vult tegen de openingsprijs (verlies > 1R); dat zit in de cijfers.
De bot laat trades nu lopen (`paper.GAP_POLICY = "hold"`). De volgorde is niet netjes oplopend, dus een deel van
het verschil is toeval. Swap-fees per uur van MyFundedPerps zitten niet in de backtest.
