# friendsbot — second-entry goudstrategie

Bot en onderzoek voor de "second entry"-strategie van een vriend: XAUUSD op 5 minuten, EMA 21,
key levels, entry op de tweede poging na een pullback (0 → 1 → P → 2).

**Status:** papier-trading sinds 10 oktober 2026. Er is geen brokerkoppeling en de bot plaatst geen echte orders.

## Papier-trading
```bash
python -m friendsbot fetch --all   # nieuwste candles (goud/US100 wekelijks op zaterdag, BTC dagelijks)
python -m friendsbot paper         # journal bijwerken → reports/paper/journal.json
python -m friendsbot site          # journal-website → website/index.html
```
De regels liggen vast sinds 10 oktober 2026 (`friendsbot/paper.py`). Elke maand kiest de bot per markt en sessie
de beste timeframe over de vorige 120 dagen; het venster 04:00–06:30 UTC concurreert met de hele Asia-sessie.
Exit: helft op 2R, stop naar breakeven, runner tot de EMA 21.
Risico: de 2 best gerangschikte combinaties 0,5% per trade, de rest 0,25%; een topper die 3 keer na elkaar verliest
zakt naar 0,25% en de volgende neemt over. Na 2 verliezen op een markt stopt die markt voor de dag. Max. 3 posities open.
Trades vóór 10 oktober zijn een walk-forward-simulatie, trades daarna zijn papiertrades.

## Snel starten
```bash
pip install -r requirements.txt
python -m friendsbot fetch      # openbare 5m-XAUUSD-data verversen (data/xauusd_5m.csv)
python -m friendsbot fetch --all   # + 1m goud, US100 en BTC (data/cache/, niet in git)
python -m friendsbot timeframes # 1m…4h op goud, US100 en BTCUSDT → reports/timeframes.json
python -m friendsbot portfolio  # één bot over goud, US100, BTC → reports/portfolio.json
python -m friendsbot research   # sessievergelijking, dev/test-split, 48 varianten → reports/
python -m friendsbot scan       # setup die zich op de laatste candle vormt (papiersignaal)
python -m friendsbot journal    # samenvatting van data/journal/data_final.xlsx (lokaal, privé)
python -m friendsbot site       # website bouwen
python -m pytest
```

## Opbouw
| Bestand | Wat |
|---|---|
| `friendsbot/strategy.py` | patroonherkenning 0-1-P-2, EMA 21-trendfilter, stop onder de pullback |
| `friendsbot/levels.py` | key levels zonder look-ahead (high/low gisteren, Asia-range, swing-punten 1u) |
| `friendsbot/execution.py` | uitvoering van signalen van elke timeframe op 1-minutencandles |
| `friendsbot/timeframes.py` | timeframe- en marktvergelijking |
| `friendsbot/portfolio.py` | één bot over meerdere markten, sessies gekozen op feb–jun |
| `friendsbot/backtest.py` | uitvoering: stop-order entry, 1R/2R/partial+runner, kosten, sessies |
| `friendsbot/research.py` | vaste "vriend-regels" per sessie + varianten gekozen op feb–jun, getest op jul–sep |
| `friendsbot/journal.py` | inlezen van zijn journal (TIME/CHINA → UTC) |
| `website/` | statische site (`index.html` is publiek, zonder journalcijfers) |

Resultaten en beperkingen: zie [FINDINGS.md](FINDINGS.md). Databron: [THIRD_PARTY_DATA.md](THIRD_PARTY_DATA.md).

## Website
`website/index.html` is de journal van de bot: 7/30/90 dagen of alles, equity-curve, resultaat per dag in € en %,
kalender, per markt en sessie, en alle trades. Eén zelfstandig bestand; Vercel serveert de map `website/`.
