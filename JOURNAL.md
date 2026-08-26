# Journal — Hermes SMC Bot

Logg over alt som blir gjort med boten. Nyaste overst.
(Alle kodeendringar ligg ogsa i git-historikken med detaljar.)

## 2026-08-26 — Go-live pa Railway + persistens + gamal bot stoppa

- **Ny bot live**: hermes-smc deploya til Railway med eige domene:
  https://hermes-smc-production.up.railway.app
- **Fiksa feil fra forste deploy-forsok**:
  - Repoet mangla pakkestruktur (`hermes_smc/`) — bygget feila.
  - Dashboard-serveren blokkerte trading-loopen (`serve_forever` i asyncio) —
    engine fekk aldri ticke. HTTP-server flytta til eigen trad.
  - `combined`-modus starta to engine-loops — duplikat fjerna.
  - `$PORT` fra Railway vart ignorert — nå plukka opp.
- **Persistent saldo**: `PositionManager` lagrar nå kapital, posisjonar og
  handelshistorikk til `/app/state/state.json` (atomisk skriving) ved kvar
  opna/lukka trade, og les det tilbake ved oppstart. Railway-volum
  `hermes-smc-volume` montert pa `/app/state` — saldoen nullstiller seg
  ikkje lenger ved deploy/restart.
- **Gamal bot (hermes-trading v10, live-handel ~€100) stoppa**:
  - Kraken-kontoen heldt 0 BTC (siste posisjon alt selt/stoppa ut pa bors),
    sa ingen tvangssal var nodvendig. EUR-saldo: €97.67.
  - To "open"-markerte trades i loggen markert `closed` (manual_shutdown).
  - Deployment fjerna med `railway down`. Volumet med all historikk er bevart.
- **Dokumentasjon**: README omskriven med strategi-forklaring, denne journalen
  oppretta. Alt synleg pa GitHub fra mobil.

## 2026-08-26 — Prosjektstart

- Ny SMC/ICT-strategi designa: FVG + pullback + engulfing/iFVG-bekreftelse,
  trendfilter pa 1t/15m, 0.5 % risk per trade, RR 1:2–1:3, mal 10 %/mnd.
- Kodebase bygd: engine (FVG/BOS/struktur-deteksjon), paper trading mot
  Kraken-data, dashboard.
- GitHub-repo oppretta: johannesbmatland-eng/hermes-smc, kopla til Railway
  for auto-deploy ved push.
