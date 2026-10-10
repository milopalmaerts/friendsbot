# friendsbot — second-entry goudstrategie

Bot en onderzoek voor de "second entry"-strategie van een vriend: XAUUSD op 5 minuten, EMA 21,
key levels, entry op de tweede poging na een pullback (0 → 1 → P → 2).

**Status:** onderzoek en papiersignalen. Er is geen brokerkoppeling en de bot plaatst geen echte orders.

## Snel starten
```bash
pip install -r requirements.txt
python -m friendsbot fetch      # openbare 5m-XAUUSD-data verversen (data/xauusd_5m.csv)
python -m friendsbot fetch --all   # + 1m goud, US100 en BTC (data/cache/, niet in git)
python -m friendsbot timeframes # 1m…4h op goud, US100 en BTCUSDT → reports/timeframes.json
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
| `friendsbot/backtest.py` | uitvoering: stop-order entry, 1R/2R/partial+runner, kosten, sessies |
| `friendsbot/research.py` | vaste "vriend-regels" per sessie + varianten gekozen op feb–jun, getest op jul–sep |
| `friendsbot/journal.py` | inlezen van zijn journal (TIME/CHINA → UTC) |
| `website/` | statische site (`index.html` is publiek, zonder journalcijfers) |

Resultaten en beperkingen: zie [FINDINGS.md](FINDINGS.md). Databron: [THIRD_PARTY_DATA.md](THIRD_PARTY_DATA.md).

## Website
`website/index.html` is één zelfstandig bestand. Met de journal lokaal aanwezig schrijft
`python -m friendsbot site` een privéversie met journalcijfers naar `dist/` (niet in git).
Deployen op Vercel: importeer de repo; `vercel.json` serveert de map `website/`.
