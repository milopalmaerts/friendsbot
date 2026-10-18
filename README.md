# friendsbot — second-entry goudstrategie

Bot en onderzoek voor de "second entry"-strategie van een vriend: XAUUSD op 5 minuten, EMA 21,
key levels, entry op de tweede poging na een pullback (0 → 1 → P → 2).

**Status:** papier-trading sinds 10 oktober 2026, plus een MyFundedPerps-adapter voor accountuitvoering.
Die adapter verstuurt alleen orders als een uitvoeringsproces met accounttoegang draait; `--dry-run` verstuurt niets.

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

## MyFundedPerps (sandbox)
De bot kan dezelfde regels uitvoeren op een MyFundedPerps-account via hun REST-API
(`friendsbot/mfp.py`, `friendsbot/live.py`). Markten: goud `binance|XAUUSDT`, US100
`hyperliquid|xyz:XYZ100`, BTC `binance|BTCUSDT`.

```bash
python -m friendsbot live --dry-run   # zonder sleutel: berekent de orders op live candles, verstuurt niets
FP_API_KEY=fp_test_… python -m friendsbot live   # één run tegen de sandbox
```
Elke run is los: entries zijn stop-orders op het niveau van candle 2, na de fill volgen een stop loss en een
TP op 2R voor de helft (OCO), daarna stop naar breakeven en runner dicht bij een slot door de EMA 21.
De runner gebruikt altijd de **gesloten 15-minuutcandle en de 15-minuut EMA 21**, onafhankelijk van de
entry-timeframe uit de maandselectie. Short: slot boven de EMA; long: slot eronder. Een lopende candle of
een wick alleen telt niet. De EMA-exit heeft voorrang op het wijzigen van een beschermende stop.
Risico per trade standaard 0,10% (toppers 0,20%) omdat prop-accounts maar 3–6% drawdown toelaten
(`FB_BASE_RISK_PCT`, `FB_HOT_RISK_PCT`). Geen nieuwe entries als de daglimiet of de maximale drawdown dichtbij is.
Een `fp_live_`-sleutel wordt geweigerd tenzij `FB_ALLOW_LIVE=1`.
De bot handelt alleen op account `FP-16539224` (aan te passen met `FB_ACCOUNT`); andere accounts op dezelfde sleutel blijven onaangeroerd, en ontbreekt dit account dan plaatst hij niets.

### Doorlopend draaien (aanbevolen)
`python -m friendsbot run` luistert naar de marktstream en de accountstream van MyFundedPerps en reageert binnen
enkele seconden: bij het sluiten van een candle zet of verplaatst hij de stop-entry, bij een fill plaatst hij meteen
de stop loss en TP. Elke 60 seconden volgt een extra controle. Goud en US100 volgen de CME-uren (zondag 18:00 tot
vrijdag 17:00 New York, dagelijkse pauze 17:00–18:00): daarbuiten geen entries, en open posities sluiten 5 minuten
voor de pauze. BTC handelt de klok rond.

Dit moet op een machine die altijd aan staat:
* Linux-server/VPS: `deploy/friendsbot.service` (systemd, herstart zichzelf, haalt dagelijks de nieuwe selectie op).
* Windows-pc die aan blijft: `deploy/run_windows.bat`.

Fallback: `.github/workflows/live.yml` draait elke 5 minuten zodra de repository-secret `FP_API_KEY`
bestaat (GitHub → Settings → Secrets and variables → Actions). `.github/workflows/paper.yml` werkt
wekelijks en op de 1e van de maand de data, de journal en de maandselectie bij.

### Runner sluit niet op de EMA

Een wijziging in deze repository wordt pas actief nadat de draaiende bot de nieuwe code gebruikt.
Op Railway: deploy de versie met de fix naar de bestaande service en controleer de nieuwe deploymentlogs.
Op een VPS: haal de nieuwe versie op en herstart `friendsbot`; bekijk `journalctl -u friendsbot -f`.
Start geen tweede uitvoeringsproces naast de bestaande bot om dezelfde positie te beheren.

Met de sleutel veilig in de runtime-omgeving kun je eerst uitsluitend lezen/plannen:

```bash
python -m friendsbot live --dry-run
```

`checks` toont per positie de entry-timeframe, runner-timeframe, laatst gesloten candle, close, EMA,
huidige en oorspronkelijke grootte en de beslissing:

* `ema_exit`: de exit is verschuldigd; in dry-run wordt geen sluitorder verstuurd.
* `awaiting_partial`: de afbouw naar de runner is nog niet vastgesteld.
* `holding_runner`: de laatst gesloten 15m-candle geeft geen exit.
* `missing_runner_candles`: geen voldoende gesloten 15m-historie; de entry-EMA wordt niet als vervanging gebruikt.
* `unmatched_entry`: de positie kan niet aan een eigen entry gekoppeld worden en wordt niet automatisch overgenomen.

De live-loop leest zo nodig oudere orderpagina's en bewaart eerder herkende entries en hun oorspronkelijke
grootte in `logs/live-entry-links.json` (niet in Git). Dit voorkomt dat een runner na veel vervangende
orders of een herstart buiten de laatste 100 orders valt. Een oude entry wordt alleen hersteld met een
expliciete positie-link of een overeenkomende, eerder vastgelegde bottrade uit het accountjournal.
Een willekeurige oude botfill op dezelfde markt is onvoldoende om een handmatige positie over te nemen.
Bewaar deze runtime-map bij een herstart; op een nieuwe machine kan het accountjournal als herstelbewijs dienen.

De always-on runner logt deze controles ook tijdens safety-ticks zonder orders. Een geplande
`close_runner` bewijst nog geen fill: controleer daarna de accountpositie en eventuele API-fout in de logs.

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
