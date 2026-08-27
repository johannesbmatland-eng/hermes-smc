# Hermes SMC Trading Bot

ICT/SMC (Smart Money Concepts) trading-bot for **BTC/USD** pa Kraken.
Koyrer 24/7 pa Railway med paper trading (100 000 USD demo-kapital).

## Live dashboard (funkar pa mobil)

**https://hermes-smc-production.up.railway.app**

| Endpoint | Kva du far |
|---|---|
| `/` | Dashboard med **BTC/USD chart**, EMA, FVG, bot-thinking, saldo, trades |
| `/api/analysis` | Kva boten tenker / ventar pa (JSON) |
| `/api/chart` | 5m candles + EMA for chart (JSON) |
| `/api/stats` | Rå tal (JSON) |
| `/api/trades` | Siste trades (JSON) |
| `/api/config` | Gjeldande strategi-config (JSON) |

## Strategi (kort forklart)

Boten handlar **long og short** pa BTC/USD nar SMC-monsteret stemmer:

1. **Trend-filter**: 1t og 15m ma vise klar trend (EMA50 + HH/HL for long, LH/LL for short).
2. **FVG (Fair Value Gap)**: 3 candles der wick på fyrste og siste ikkje overlappar
   (bullish: siste low over fyrste high; bearish: siste high under fyrste low).
   Dashbordet markerer gap som grøne/raude bokser (nephew_sam_-stil), og fjernar/toner
   dei når wick fyller gapet.
3. **Pullback**: ventar pa at prisen trekkjer seg tilbake inn i gapet.
4. **Bekreftelse**: engulfing-candle eller IFVG.
5. **Risk**: 0.5 % per trade, SL/TP spegla for begge retningar.
6. Maks 1 open posisjon, 5 min cooldown.

Detaljar/parametre: [`hermes_smc/config/strategy.yaml`](hermes_smc/config/strategy.yaml)

## Status og historikk

- **Saldoen overlever redeploys**: all state (kapital, posisjonar, historikk)
  vert lagra fortlopande til eit Railway-volum (`/app/state/state.json`).
- Endringslogg for prosjektet: [JOURNAL.md](JOURNAL.md)
- All kode-historikk: [commits](../../commits/master)

## Drift

- **Hosting**: Railway, prosjekt `hermes-trading`, service `hermes-smc`.
  Push til `master` -> automatisk deploy.
- **Arkitektur**: ein prosess koyrer bade trading-engine (tick kvart 10. sekund
  mot Kraken sine offentlege data) og dashboard-webserver.
- Den gamle live-boten (hermes-trading, v10 RSI-strategi) er **stoppa** 2026-08-26.
  Data ligg bevart pa sitt eige volum.

## Lokal koyring

```bash
uv sync
uv run python -m hermes_smc --mode combined --port 8080
# opne http://localhost:8080
```

`STATE_DIR=<mappe>` aktiverer persistent lagring (utan den er state kun i minnet).
