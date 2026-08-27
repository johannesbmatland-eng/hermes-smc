"""SMC Dashboard - Real-time trading dashboard with chart + bot thinking."""

import asyncio
import json
import logging
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from threading import Thread

from ..engine.paper_trading import PaperTradingEngine, SMCConfig
from ..engine.analytics import build_analytics, enrich_trade_meta

logger = logging.getLogger(__name__)

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).parent.parent / "state"))
CONFIG_PATH = Path(__file__).parent.parent / "config" / "strategy.yaml"


class DashboardServer:
    """HTTP dashboard server for SMC trading bot."""

    def __init__(self, port: int = 8080):
        self.port = port
        self.engine: PaperTradingEngine | None = None
        self._server: HTTPServer | None = None

    def load_engine(self):
        """Load or create the trading engine."""
        if self.engine is None:
            config = SMCConfig(CONFIG_PATH)
            self.engine = PaperTradingEngine(config)
        return self.engine

    async def start_engine(self):
        """Start the trading engine."""
        self.load_engine()
        self.engine._running = True
        asyncio.create_task(self._engine_loop())
        asyncio.create_task(self._price_loop())

    async def _engine_loop(self):
        """Background loop for the trading engine (candles + signals)."""
        while self.engine and self.engine._running and not self.engine._stopped:
            try:
                await self.engine.run_tick()
            except Exception as e:
                logger.error(f"Engine tick failed: {e}")
            await asyncio.sleep(10)

    async def _price_loop(self):
        """Fast live price + open-trade PnL / SL-TP checks (~2s)."""
        while self.engine and self.engine._running and not self.engine._stopped:
            try:
                await self._refresh_live_price()
            except Exception as e:
                logger.error(f"Live price update failed: {e}")
            await asyncio.sleep(2)

    async def _refresh_live_price(self):
        engine = self.engine
        if not engine:
            return
        market = engine.config.get("market", "BTC/USD")
        price = await engine.market_data.get_latest_price(market)
        engine.last_price = price

        for trade_id, position in list(engine.position_manager.open_positions.items()):
            engine.position_manager.update_position_price(trade_id, price)
            candles = engine.last_candles_5m or []
            if not candles:
                continue
            exit_reason = engine.check_exit_conditions(position, candles, price)
            if exit_reason:
                logger.info(f"Live exit {trade_id}: {exit_reason} @ {price:.2f}")
                engine.position_manager.close_position(trade_id, price, exit_reason)
                engine.trades.append({
                    "id": trade_id,
                    "type": "close",
                    "side": position.get("side"),
                    "reason": exit_reason,
                    "price": price,
                    "timestamp": time.time(),
                })

    def get_stats(self) -> dict[str, Any]:
        """Get current trading statistics."""
        engine = self.load_engine()
        pm = engine.position_manager

        open_positions = list(pm.open_positions.values())
        closed_positions = pm.closed_positions

        # Fresh mark-to-market for open trades (account % + price %)
        price = engine.last_price
        if price is not None:
            for p in open_positions:
                pm.update_position_price(p["id"], price)

        unrealized = sum(p.get("pnl", 0) or 0 for p in open_positions)
        total_pnl = sum(p.get("pnl", 0) for p in closed_positions)
        # Account return % vs initial capital (0.5% risk → ~1% at 1:2 RR)
        total_account_pct = (
            (total_pnl / pm.initial_capital) * 100 if pm.initial_capital else 0.0
        )
        unrealized_account_pct = (
            (unrealized / pm.initial_capital) * 100 if pm.initial_capital else 0.0
        )
        win_count = sum(1 for p in closed_positions if p.get("pnl", 0) > 0)
        win_rate = win_count / len(closed_positions) if closed_positions else 0
        market = engine.config.get("market", "BTC/USD")

        live_trade = None
        if open_positions:
            p = open_positions[0]
            risk = abs(p["entry_price"] - p["stop_loss"])
            reward = abs(p["take_profit"] - p["entry_price"])
            info = p.get("strategy_info") or {}
            live_trade = {
                "id": p["id"],
                "asset": p.get("asset", market),
                "side": p.get("side", "long"),
                "entry_price": p["entry_price"],
                "stop_loss": p["stop_loss"],
                "take_profit": p["take_profit"],
                "position_size": p["position_size"],
                "current_price": p.get("current_price", price),
                "pnl": p.get("pnl", 0),
                "pnl_pct": p.get("pnl_pct", 0),  # price move
                "pnl_account_pct": p.get("pnl_account_pct", 0),
                "r_multiple": p.get("r_multiple", 0),
                "open_time": p.get("open_time"),
                "rr": (reward / risk) if risk > 0 else None,
                "confirmation": info.get("confirmation"),
                "session": info.get("session"),
                "trend": info.get("trend"),
            }

        return {
            "market": market,
            "capital": pm.capital,
            "equity": getattr(pm, "equity", pm.capital),
            "initial_capital": pm.initial_capital,
            "open_positions": open_positions,
            "closed_positions": closed_positions[-10:],
            "total_pnl": total_pnl,
            "total_pnl_pct": total_account_pct,  # account % (was wrongly price %)
            "total_account_pct": total_account_pct,
            "unrealized_pnl": unrealized,
            "unrealized_account_pct": unrealized_account_pct,
            "live_trade": live_trade,
            "win_rate": win_rate,
            "trade_count": len(closed_positions),
            "engine_status": "running" if (self.engine and self.engine._running) else "stopped",
            "last_update": time.time(),
            "price": engine.last_price,
        }

    def get_analytics(self) -> dict[str, Any]:
        engine = self.load_engine()
        pm = engine.position_manager
        return build_analytics(
            pm.closed_positions,
            initial_capital=pm.initial_capital,
            open_positions=list(pm.open_positions.values()),
        )

    def get_positions(self) -> list[dict]:
        engine = self.load_engine()
        return list(engine.position_manager.open_positions.values())

    def get_trades(self, limit: int = 20) -> list[dict]:
        engine = self.load_engine()
        pm = engine.position_manager
        history = [
            enrich_trade_meta(t, pm.initial_capital)
            for t in pm.trade_history
        ]
        opens = [
            enrich_trade_meta({**p, "type": "open", "status": "open"}, pm.initial_capital)
            for p in pm.open_positions.values()
        ]
        combined = history + opens
        if combined:
            return combined[-limit:]
        return engine.trades[-limit:]

    def get_analysis(self) -> dict[str, Any]:
        engine = self.load_engine()
        return engine.last_analysis or {
            "phase": "Waiting for first engine tick…",
            "waiting_for": ["Engine starting"],
            "checklist": [],
            "market": engine.config.get("market", "BTC/USD"),
            "updated_at": time.time(),
        }

    def get_chart(self) -> dict[str, Any]:
        engine = self.load_engine()
        candles = [
            {
                "time": c["timestamp"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
            }
            for c in engine.last_candles_5m
        ]
        analysis = engine.last_analysis or {}
        nearest = (analysis.get("fvgs") or {}).get("nearest")
        levels = None
        opens = list(engine.position_manager.open_positions.values())
        if opens:
            p = opens[0]
            levels = {
                "entry": p["entry_price"],
                "stop_loss": p["stop_loss"],
                "take_profit": p["take_profit"],
                "side": p.get("side", "long"),
            }
        return {
            "market": engine.config.get("market", "BTC/USD"),
            "timeframe": "5m",
            "candles": candles,
            "ema": engine.last_ema_5m,
            "fvg": nearest,
            "fvg_boxes": getattr(engine, "last_fvg_boxes", []) or [],
            "levels": levels,
            "price": engine.last_price,
            "bias": analysis.get("bias"),
        }

    async def start(self):
        """Start the dashboard server."""
        self._server = HTTPServer(("0.0.0.0", self.port), DashboardHandler)
        DashboardHandler.engine_server = self
        logger.info(f"Dashboard server starting on port {self.port}")
        if self.engine is None:
            await self.start_engine()
        # serve_forever blocks — run in a thread so asyncio engine loop keeps ticking
        Thread(target=self._server.serve_forever, daemon=True).start()
        while True:
            await asyncio.sleep(3600)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the dashboard."""

    engine_server: DashboardServer | None = None

    def log_message(self, format, *args):
        logger.info(f"HTTP: {format % args}")

    def send_json(self, data: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def send_html(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            self.send_html(self._get_dashboard_html())
        elif path == "/api/stats":
            self.send_json(self.engine_server.get_stats() if self.engine_server else {})
        elif path == "/api/positions":
            positions = self.engine_server.get_positions() if self.engine_server else []
            self.send_json({"positions": positions})
        elif path == "/api/trades":
            trades = self.engine_server.get_trades() if self.engine_server else []
            self.send_json({"trades": trades})
        elif path == "/api/analytics":
            self.send_json(self.engine_server.get_analytics() if self.engine_server else {})
        elif path == "/api/analysis":
            self.send_json(self.engine_server.get_analysis() if self.engine_server else {})
        elif path == "/api/chart":
            self.send_json(self.engine_server.get_chart() if self.engine_server else {})
        elif path == "/api/config":
            if CONFIG_PATH.exists():
                import yaml
                with open(CONFIG_PATH) as f:
                    config = yaml.safe_load(f)
                self.send_json({"config": config})
            else:
                self.send_json({"error": "Config not found"})
        else:
            self.send_response(404)
            self.end_headers()

    def _get_dashboard_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hermes SMC · BTC/USD</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&family=IBM+Plex+Serif:wght@600&display=swap" rel="stylesheet">
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        :root {
            --bg: #0b0f14;
            --panel: #121821;
            --panel-2: #0f141c;
            --line: #1e2733;
            --text: #e7eef7;
            --muted: #8b97a8;
            --green: #3ecf8e;
            --red: #f07178;
            --blue: #5b9cff;
            --amber: #e6b450;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            background:
                radial-gradient(1200px 500px at 10% -10%, #152033 0%, transparent 55%),
                radial-gradient(900px 400px at 90% 0%, #1a1520 0%, transparent 50%),
                var(--bg);
            color: var(--text);
            padding: 20px;
            min-height: 100vh;
        }
        .wrap { max-width: 1440px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: end; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
        h1 { font-family: "IBM Plex Serif", Georgia, serif; font-size: 1.65rem; font-weight: 600; letter-spacing: -0.02em; }
        .subtitle { color: var(--muted); font-size: 0.85rem; margin-top: 4px; }
        .price-pill {
            font-variant-numeric: tabular-nums;
            font-size: 1.35rem;
            font-weight: 600;
            color: var(--blue);
        }
        .layout {
            display: grid;
            grid-template-columns: 1.6fr 1fr;
            gap: 16px;
            margin-bottom: 16px;
        }
        @media (max-width: 980px) { .layout { grid-template-columns: 1fr; } }
        .card {
            background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 16px;
        }
        .card-title {
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--muted);
            margin-bottom: 12px;
            font-weight: 700;
        }
        #chart { width: 100%; height: 420px; }
        #chart-wrap { position: relative; width: 100%; height: 420px; }
        #fvg-overlay {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 2;
        }
        .phase {
            font-size: 1.05rem;
            font-weight: 600;
            margin-bottom: 10px;
            line-height: 1.35;
        }
        .phase.long { color: var(--green); }
        .phase.short { color: var(--red); }
        .phase.neutral { color: var(--amber); }
        .waiting { color: var(--muted); font-size: 0.85rem; margin-bottom: 14px; }
        .checklist { display: flex; flex-direction: column; gap: 8px; }
        .check {
            display: grid;
            grid-template-columns: 10px 1fr;
            gap: 10px;
            padding: 10px 12px;
            border-radius: 10px;
            background: rgba(255,255,255,0.02);
            border: 1px solid var(--line);
        }
        .dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 4px; }
        .dot.pass { background: var(--green); box-shadow: 0 0 0 3px rgba(62,207,142,0.15); }
        .dot.fail { background: var(--red); box-shadow: 0 0 0 3px rgba(240,113,120,0.12); }
        .dot.wait { background: var(--amber); box-shadow: 0 0 0 3px rgba(230,180,80,0.12); }
        .check-label { font-size: 0.82rem; font-weight: 600; }
        .check-detail { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 16px; }
        .stat {
            background: rgba(0,0,0,0.18);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 12px;
        }
        .stat-label { font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
        .stat-value { font-size: 1.15rem; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }
        .stat-value.positive { color: var(--green); }
        .stat-value.negative { color: var(--red); }
        .stat-value.neutral { color: var(--blue); }
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th { text-align: left; padding: 8px 12px; background: #181f2b; color: var(--muted); font-weight: 600; }
        td { padding: 8px 12px; border-top: 1px solid #1c2330; }
        tr:hover { background: rgba(255,255,255,0.03); }
        .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
        .status-dot.running { background: var(--green); }
        .status-dot.stopped { background: var(--red); }
        .meta { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
        .chip {
            font-size: 0.72rem;
            padding: 4px 8px;
            border-radius: 999px;
            border: 1px solid var(--line);
            color: var(--muted);
        }
        .chip.on { color: var(--green); border-color: rgba(62,207,142,0.35); }
        .chip.off { color: var(--red); border-color: rgba(240,113,120,0.35); }
        #refresh { font-size: 0.75rem; color: var(--muted); margin-top: 8px; }
        .side-long { color: var(--green); text-transform: uppercase; font-weight: 600; }
        .side-short { color: var(--red); text-transform: uppercase; font-weight: 600; }
        .live-trade {
            display: none;
            margin-bottom: 16px;
            padding: 18px 20px;
            border-radius: 16px;
            border: 1px solid var(--line);
            background:
                linear-gradient(120deg, rgba(91,156,255,0.12) 0%, transparent 42%),
                linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
        }
        .live-trade.active { display: block; }
        .live-trade.profit {
            border-color: rgba(62,207,142,0.45);
            background:
                linear-gradient(120deg, rgba(62,207,142,0.14) 0%, transparent 50%),
                linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
        }
        .live-trade.loss {
            border-color: rgba(240,113,120,0.45);
            background:
                linear-gradient(120deg, rgba(240,113,120,0.14) 0%, transparent 50%),
                linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
        }
        .live-trade-top {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            flex-wrap: wrap;
        }
        .live-trade-title {
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--muted);
            font-weight: 700;
            margin-bottom: 6px;
        }
        .live-trade-side {
            font-size: 1.15rem;
            font-weight: 700;
            letter-spacing: 0.04em;
        }
        .live-pnl {
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
        .live-pnl-usd {
            font-size: 2.4rem;
            font-weight: 700;
            line-height: 1.05;
            letter-spacing: -0.03em;
        }
        .live-pnl-pct {
            font-size: 1.25rem;
            font-weight: 600;
            margin-top: 4px;
        }
        .live-pnl.positive .live-pnl-usd,
        .live-pnl.positive .live-pnl-pct { color: var(--green); }
        .live-pnl.negative .live-pnl-usd,
        .live-pnl.negative .live-pnl-pct { color: var(--red); }
        .live-metrics {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
            margin-top: 16px;
        }
        .live-metric {
            background: rgba(0,0,0,0.22);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 10px 12px;
        }
        .live-metric .k {
            font-size: 0.62rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--muted);
        }
        .live-metric .v {
            margin-top: 4px;
            font-size: 1.05rem;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }
        .live-metric .v.entry { color: var(--blue); }
        .live-metric .v.sl { color: var(--red); }
        .live-metric .v.tp { color: var(--green); }
        .chart-legend {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin-top: 10px;
            font-size: 0.75rem;
            color: var(--muted);
        }
        .chart-legend span::before {
            content: '';
            display: inline-block;
            width: 14px;
            height: 2px;
            margin-right: 6px;
            vertical-align: middle;
        }
        .chart-legend .lg-entry::before { background: var(--blue); }
        .chart-legend .lg-sl::before { background: var(--red); border-top: 1px dashed var(--red); height: 0; }
        .chart-legend .lg-tp::before { background: var(--green); }
        .chart-legend.hidden { display: none; }
    </style>
</head>
<body>
    <div class="wrap">
        <header>
            <div>
                <h1>Hermes SMC</h1>
                <p class="subtitle">ICT/SMC · <span id="market_label">BTC/USD</span> · Paper Trading · 5m chart</p>
            </div>
            <div class="price-pill" id="live_price">—</div>
        </header>

        <div class="live-trade" id="live_trade">
            <div class="live-trade-top">
                <div>
                    <div class="live-trade-title">Live trade</div>
                    <div class="live-trade-side" id="live_side">—</div>
                    <div style="color:var(--muted);font-size:0.82rem;margin-top:4px" id="live_meta">—</div>
                </div>
                <div class="live-pnl" id="live_pnl_wrap">
                    <div class="live-pnl-usd" id="live_pnl_usd">$0.00</div>
                    <div class="live-pnl-pct" id="live_pnl_pct">+0.00%</div>
                </div>
            </div>
            <div class="live-metrics">
                <div class="live-metric"><div class="k">Entry</div><div class="v entry" id="live_entry">—</div></div>
                <div class="live-metric"><div class="k">Stop loss</div><div class="v sl" id="live_sl">—</div></div>
                <div class="live-metric"><div class="k">Take profit</div><div class="v tp" id="live_tp">—</div></div>
                <div class="live-metric"><div class="k">Mark</div><div class="v" id="live_mark">—</div></div>
                <div class="live-metric"><div class="k">Size</div><div class="v" id="live_size">—</div></div>
                <div class="live-metric"><div class="k">Equity</div><div class="v" id="live_equity">—</div></div>
            </div>
        </div>

        <div class="layout">
            <div class="card">
                <div class="card-title">BTC/USD · 5m · FVG + trade levels</div>
                <div id="chart-wrap">
                    <div id="chart"></div>
                    <canvas id="fvg-overlay"></canvas>
                </div>
                <div class="chart-legend hidden" id="chart_legend">
                    <span class="lg-entry">Entry</span>
                    <span class="lg-sl">Stop loss</span>
                    <span class="lg-tp">Take profit</span>
                </div>
                <div class="meta">
                    <span class="chip" id="chip_ema">EMA 50</span>
                    <span class="chip" id="chip_fvg">FVG —</span>
                    <span class="chip" id="chip_bias">Bias —</span>
                </div>
            </div>

            <div class="card">
                <div class="card-title">Bot thinking</div>
                <div class="phase neutral" id="phase">Starting…</div>
                <div class="waiting" id="waiting">—</div>
                <div class="checklist" id="checklist"></div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-title">Account</div>
                <div class="stat"><div class="stat-label">Capital</div><div class="stat-value neutral" id="capital">--</div></div>
                <div class="stat" style="margin-top:8px"><div class="stat-label">Account PnL $</div><div class="stat-value" id="pnl">--</div></div>
                <div class="stat" style="margin-top:8px"><div class="stat-label">Account PnL %</div><div class="stat-value" id="pnl_pct">--</div></div>
            </div>
            <div class="card">
                <div class="card-title">Performance</div>
                <div class="stat"><div class="stat-label">Win rate</div><div class="stat-value" id="winrate">--</div></div>
                <div class="stat" style="margin-top:8px"><div class="stat-label">Trades</div><div class="stat-value" id="trades">--</div></div>
                <div class="stat" style="margin-top:8px"><div class="stat-label">Open</div><div class="stat-value" id="open_pos">--</div></div>
            </div>
            <div class="card">
                <div class="card-title">Engine</div>
                <div class="stat">
                    <div class="stat-label">Status</div>
                    <div class="stat-value"><span class="status-dot running" id="status_dot"></span><span id="status_text">--</span></div>
                </div>
                <div class="stat" style="margin-top:8px"><div class="stat-label">Last update</div><div class="stat-value" id="last_update">--</div></div>
                <div class="stat" style="margin-top:8px"><div class="stat-label">Cooldown</div><div class="stat-value" style="font-size:0.9rem">Kraken · paper</div></div>
            </div>
        </div>

        <div class="card" style="margin-bottom:16px">
            <div class="card-title">Strategy analytics · sessions & conditions</div>
            <p id="analytics_note" style="color:var(--muted);font-size:0.82rem;margin-bottom:12px">Loading…</p>
            <div class="grid" id="analytics_highlights" style="margin-bottom:12px"></div>
            <div class="grid">
                <div>
                    <div class="card-title">By session (UTC)</div>
                    <div id="analytics_sessions"><p style="color:var(--muted);font-size:0.85rem">No closed trades yet</p></div>
                </div>
                <div>
                    <div class="card-title">By market condition</div>
                    <div id="analytics_conditions"><p style="color:var(--muted);font-size:0.85rem">No closed trades yet</p></div>
                </div>
                <div>
                    <div class="card-title">By weekday</div>
                    <div id="analytics_weekdays"><p style="color:var(--muted);font-size:0.85rem">No closed trades yet</p></div>
                </div>
            </div>
        </div>

        <div class="card" style="margin-bottom:16px">
            <div class="card-title">Open positions</div>
            <div id="open_positions_container"><p style="color:var(--muted);font-size:0.85rem">No open positions</p></div>
        </div>

        <div class="card">
            <div class="card-title">Recent trades</div>
            <div id="trades_container"><p style="color:var(--muted);font-size:0.85rem">No trades yet</p></div>
        </div>

        <p id="refresh">Live price every 2s · full scan every 10s</p>
    </div>

    <script>
        let chart, candleSeries, emaSeries, fvgBoxes = [], chartFitted = false;
        let fvgOverlay, fvgCtx;
        let tradePriceLines = [];
        let lastLevelsKey = '';

        function initChart() {
            const wrap = document.getElementById('chart-wrap');
            const el = document.getElementById('chart');
            fvgOverlay = document.getElementById('fvg-overlay');
            fvgCtx = fvgOverlay.getContext('2d');
            chart = LightweightCharts.createChart(el, {
                layout: {
                    background: { color: 'transparent' },
                    textColor: '#8b97a8',
                },
                grid: {
                    vertLines: { color: 'rgba(30,39,51,0.7)' },
                    horzLines: { color: 'rgba(30,39,51,0.7)' },
                },
                rightPriceScale: { borderColor: '#1e2733' },
                timeScale: { borderColor: '#1e2733', timeVisible: true, secondsVisible: false },
                crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
                width: wrap.clientWidth,
                height: 420,
            });
            candleSeries = chart.addCandlestickSeries({
                upColor: '#3ecf8e',
                downColor: '#f07178',
                borderUpColor: '#3ecf8e',
                borderDownColor: '#f07178',
                wickUpColor: '#3ecf8e',
                wickDownColor: '#f07178',
            });
            emaSeries = chart.addLineSeries({
                color: '#5b9cff',
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: true,
                title: 'EMA 50',
            });
            const resize = () => {
                const w = wrap.clientWidth;
                chart.applyOptions({ width: w });
                fvgOverlay.width = w;
                fvgOverlay.height = 420;
                drawFvgBoxes();
            };
            window.addEventListener('resize', resize);
            resize();
            chart.timeScale().subscribeVisibleLogicalRangeChange(() => drawFvgBoxes());
            chart.subscribeCrosshairMove(() => drawFvgBoxes());
        }

        function clearTradeLevels() {
            for (const line of tradePriceLines) {
                try { candleSeries.removePriceLine(line); } catch (e) {}
            }
            tradePriceLines = [];
            lastLevelsKey = '';
            document.getElementById('chart_legend').classList.add('hidden');
        }

        function updateTradeLevels(levels) {
            if (!levels || levels.entry == null) {
                clearTradeLevels();
                return;
            }
            const key = [levels.entry, levels.stop_loss, levels.take_profit, levels.side].join('|');
            if (key === lastLevelsKey && tradePriceLines.length) return;
            clearTradeLevels();
            lastLevelsKey = key;
            const LineStyle = LightweightCharts.LineStyle;
            const defs = [
                { price: levels.entry, color: '#5b9cff', title: 'ENTRY', style: LineStyle.Solid, width: 2 },
                { price: levels.stop_loss, color: '#f07178', title: 'SL', style: LineStyle.Dashed, width: 2 },
                { price: levels.take_profit, color: '#3ecf8e', title: 'TP', style: LineStyle.Dashed, width: 2 },
            ];
            for (const d of defs) {
                if (d.price == null || isNaN(d.price)) continue;
                tradePriceLines.push(candleSeries.createPriceLine({
                    price: Number(d.price),
                    color: d.color,
                    lineWidth: d.width,
                    lineStyle: d.style,
                    axisLabelVisible: true,
                    title: d.title,
                }));
            }
            document.getElementById('chart_legend').classList.remove('hidden');
        }

        function drawFvgBoxes() {
            if (!fvgCtx || !candleSeries) return;
            const w = fvgOverlay.width;
            const h = fvgOverlay.height;
            fvgCtx.clearRect(0, 0, w, h);
            if (!fvgBoxes || !fvgBoxes.length) return;

            for (const box of fvgBoxes) {
                const x1 = chart.timeScale().timeToCoordinate(box.time_start);
                const x2 = chart.timeScale().timeToCoordinate(box.time_end);
                const yTop = candleSeries.priceToCoordinate(box.top);
                const yBot = candleSeries.priceToCoordinate(box.bottom);
                if (x1 == null || x2 == null || yTop == null || yBot == null) continue;

                const left = Math.min(x1, x2);
                const right = Math.max(x1, x2);
                const top = Math.min(yTop, yBot);
                const height = Math.abs(yBot - yTop);
                const width = Math.max(right - left, 2);
                const active = box.unmitigated !== false;
                const bull = box.type === 'bullish';

                fvgCtx.fillStyle = bull
                    ? (active ? 'rgba(62, 207, 142, 0.22)' : 'rgba(62, 207, 142, 0.08)')
                    : (active ? 'rgba(240, 113, 120, 0.22)' : 'rgba(240, 113, 120, 0.08)');
                fvgCtx.strokeStyle = bull
                    ? (active ? 'rgba(62, 207, 142, 0.85)' : 'rgba(62, 207, 142, 0.35)')
                    : (active ? 'rgba(240, 113, 120, 0.85)' : 'rgba(240, 113, 120, 0.35)');
                fvgCtx.lineWidth = 1;
                fvgCtx.setLineDash(active ? [] : [4, 3]);
                fvgCtx.fillRect(left, top, width, Math.max(height, 1));
                fvgCtx.strokeRect(left, top, width, Math.max(height, 1));

                const yMid = candleSeries.priceToCoordinate(box.mid);
                if (yMid != null) {
                    fvgCtx.beginPath();
                    fvgCtx.setLineDash([3, 3]);
                    fvgCtx.strokeStyle = bull
                        ? 'rgba(62, 207, 142, 0.55)'
                        : 'rgba(240, 113, 120, 0.55)';
                    fvgCtx.moveTo(left, yMid);
                    fvgCtx.lineTo(left + width, yMid);
                    fvgCtx.stroke();
                }

                fvgCtx.setLineDash([]);
                fvgCtx.fillStyle = bull ? '#3ecf8e' : '#f07178';
                fvgCtx.font = '10px IBM Plex Sans, sans-serif';
                fvgCtx.fillText(active ? 'FVG' : 'filled', left + 4, top + 12);
            }
        }

        function formatCurrency(value) {
            if (value == null || isNaN(value)) return '--';
            return '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        function formatSignedCurrency(value) {
            if (value == null || isNaN(value)) return '--';
            const n = Number(value);
            const sign = n > 0 ? '+' : (n < 0 ? '' : '');
            return sign + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        function renderLiveTrade(stats) {
            const panel = document.getElementById('live_trade');
            const t = stats.live_trade;
            if (!t) {
                panel.className = 'live-trade';
                return;
            }
            const pnl = Number(t.pnl || 0);
            const accountPct = Number(
                t.pnl_account_pct != null ? t.pnl_account_pct : t.pnl_pct || 0
            );
            const pricePct = Number(t.pnl_pct || 0);
            const positive = pnl >= 0;
            panel.className = 'live-trade active ' + (positive ? 'profit' : 'loss');
            document.getElementById('live_side').textContent =
                (t.side || '').toUpperCase() + ' · ' + (t.asset || stats.market || 'BTC/USD');
            document.getElementById('live_side').className =
                'live-trade-side side-' + (t.side || 'long');
            const conf = t.confirmation ? (' · ' + t.confirmation) : '';
            const rr = t.rr != null ? (' · RR 1:' + Number(t.rr).toFixed(1)) : '';
            const sess = t.session ? (' · ' + t.session) : '';
            const rMult = t.r_multiple != null ? (' · R ' + Number(t.r_multiple).toFixed(2)) : '';
            document.getElementById('live_meta').textContent =
                (t.id ? t.id.substring(0, 8) + '…' : '') + conf + rr + sess + rMult;

            const wrap = document.getElementById('live_pnl_wrap');
            wrap.className = 'live-pnl ' + (positive ? 'positive' : 'negative');
            document.getElementById('live_pnl_usd').textContent = formatSignedCurrency(pnl);
            document.getElementById('live_pnl_pct').textContent =
                (accountPct >= 0 ? '+' : '') + accountPct.toFixed(2) + '% account'
                + ' · price ' + (pricePct >= 0 ? '+' : '') + pricePct.toFixed(2) + '%';

            document.getElementById('live_entry').textContent = Number(t.entry_price).toFixed(2);
            document.getElementById('live_sl').textContent = Number(t.stop_loss).toFixed(2);
            document.getElementById('live_tp').textContent = Number(t.take_profit).toFixed(2);
            document.getElementById('live_mark').textContent =
                t.current_price != null ? Number(t.current_price).toFixed(2) : '—';
            document.getElementById('live_size').textContent =
                Number(t.position_size).toFixed(6) + ' BTC';
            document.getElementById('live_equity').textContent =
                formatCurrency(stats.equity != null ? stats.equity : (stats.capital + pnl));
        }

        function renderChecklist(items) {
            const root = document.getElementById('checklist');
            if (!items || !items.length) {
                root.innerHTML = '<p style="color:var(--muted);font-size:0.85rem">No checklist yet — waiting for market data</p>';
                return;
            }
            root.innerHTML = items.map(i => `
                <div class="check">
                    <div class="dot ${i.status || 'wait'}"></div>
                    <div>
                        <div class="check-label">${i.label}</div>
                        <div class="check-detail">${i.detail || ''}</div>
                    </div>
                </div>
            `).join('');
        }

        function renderAnalyticsTable(rows) {
            if (!rows || !rows.length) {
                return '<p style="color:var(--muted);font-size:0.85rem">No data yet</p>';
            }
            return '<table><thead><tr><th>Bucket</th><th>n</th><th>Win%</th><th>PnL $</th><th>Acct %</th><th>Avg R</th></tr></thead><tbody>' +
                rows.map(r => `
                    <tr>
                        <td>${r.name}</td>
                        <td>${r.trades}</td>
                        <td>${(r.win_rate * 100).toFixed(0)}%</td>
                        <td class="${r.total_pnl >= 0 ? 'positive' : 'negative'}">${formatSignedCurrency(r.total_pnl)}</td>
                        <td class="${r.total_account_pct >= 0 ? 'positive' : 'negative'}">${(r.total_account_pct >= 0 ? '+' : '') + r.total_account_pct.toFixed(2)}%</td>
                        <td>${Number(r.avg_r).toFixed(2)}</td>
                    </tr>
                `).join('') + '</tbody></table>';
        }

        function renderAnalytics(a) {
            if (!a) return;
            document.getElementById('analytics_note').textContent = a.note || '';
            const hi = document.getElementById('analytics_highlights');
            const bits = [];
            if (a.best_session) {
                bits.push(`<div class="stat"><div class="stat-label">Best session</div><div class="stat-value" style="font-size:1rem">${a.best_session.name}<div style="font-size:0.8rem;color:var(--muted);font-weight:400;margin-top:4px">${a.best_session.trades} trades · ${(a.best_session.total_account_pct>=0?'+':'') + a.best_session.total_account_pct.toFixed(2)}% acct</div></div></div>`);
            }
            if (a.best_weekday) {
                bits.push(`<div class="stat"><div class="stat-label">Best weekday</div><div class="stat-value" style="font-size:1rem">${a.best_weekday.name}<div style="font-size:0.8rem;color:var(--muted);font-weight:400;margin-top:4px">${a.best_weekday.trades} trades · ${(a.best_weekday.total_account_pct>=0?'+':'') + a.best_weekday.total_account_pct.toFixed(2)}% acct</div></div></div>`);
            }
            if (a.best_condition) {
                bits.push(`<div class="stat"><div class="stat-label">Best condition</div><div class="stat-value" style="font-size:1rem">${a.best_condition.name}<div style="font-size:0.8rem;color:var(--muted);font-weight:400;margin-top:4px">${a.best_condition.trades} trades · ${(a.best_condition.total_account_pct>=0?'+':'') + a.best_condition.total_account_pct.toFixed(2)}% acct</div></div></div>`);
            }
            bits.push(`<div class="stat"><div class="stat-label">Closed / win rate</div><div class="stat-value" style="font-size:1rem">${a.trade_count} · ${((a.win_rate||0)*100).toFixed(0)}%<div style="font-size:0.8rem;color:var(--muted);font-weight:400;margin-top:4px">Avg R ${Number(a.avg_r||0).toFixed(2)} · acct ${(a.total_account_pct>=0?'+':'') + Number(a.total_account_pct||0).toFixed(2)}%</div></div></div>`);
            hi.innerHTML = bits.join('');
            document.getElementById('analytics_sessions').innerHTML = renderAnalyticsTable(a.by_session);
            const conditions = [].concat(a.by_trend || [], a.by_confirmation || [], a.by_side || []);
            document.getElementById('analytics_conditions').innerHTML = renderAnalyticsTable(conditions);
            document.getElementById('analytics_weekdays').innerHTML = renderAnalyticsTable(a.by_weekday);
        }

        async function refresh() {
            try {
                const [stats, positions, trades, analysis, chartData, analytics] = await Promise.all([
                    fetch('/api/stats').then(r => r.json()),
                    fetch('/api/positions').then(r => r.json()),
                    fetch('/api/trades').then(r => r.json()),
                    fetch('/api/analysis').then(r => r.json()),
                    fetch('/api/chart').then(r => r.json()),
                    fetch('/api/analytics').then(r => r.json()),
                ]);

                document.getElementById('market_label').textContent = stats.market || analysis.market || 'BTC/USD';
                if (stats.price != null) {
                    document.getElementById('live_price').textContent = formatCurrency(stats.price);
                }

                renderLiveTrade(stats);
                renderAnalytics(analytics);

                document.getElementById('capital').textContent = formatCurrency(stats.capital);
                const unrealized = stats.unrealized_pnl || 0;
                const realized = stats.total_pnl || 0;
                document.getElementById('pnl').textContent = formatSignedCurrency(realized + unrealized);
                document.getElementById('pnl').className = 'stat-value ' + ((realized + unrealized) >= 0 ? 'positive' : 'negative');
                // Account % vs initial capital (NOT price move %)
                const accountPct = stats.total_account_pct != null
                    ? Number(stats.total_account_pct) + Number(stats.unrealized_account_pct || 0)
                    : Number(stats.total_pnl_pct || 0);
                document.getElementById('pnl_pct').textContent = (accountPct >= 0 ? '+' : '') + accountPct.toFixed(2) + '%';
                document.getElementById('pnl_pct').className = 'stat-value ' + (accountPct >= 0 ? 'positive' : 'negative');
                document.getElementById('winrate').textContent = ((stats.win_rate || 0) * 100).toFixed(1) + '%';
                document.getElementById('trades').textContent = stats.trade_count;
                document.getElementById('open_pos').textContent = (stats.open_positions || []).length;
                document.getElementById('status_text').textContent = stats.engine_status;
                document.getElementById('status_dot').className = 'status-dot ' + (stats.engine_status === 'running' ? 'running' : 'stopped');
                document.getElementById('last_update').textContent = new Date((stats.last_update || Date.now()/1000) * 1000).toLocaleTimeString();

                const phaseEl = document.getElementById('phase');
                phaseEl.textContent = analysis.phase || 'Scanning…';
                phaseEl.className = 'phase ' + (analysis.bias || 'neutral');
                const waiting = (analysis.waiting_for && analysis.waiting_for.length)
                    ? analysis.waiting_for.join(' · ')
                    : 'No blockers';
                document.getElementById('waiting').textContent = waiting;
                renderChecklist(analysis.checklist || []);

                if (chartData.candles && chartData.candles.length) {
                    candleSeries.setData(chartData.candles);
                    if (chartData.ema && chartData.ema.length) {
                        emaSeries.setData(chartData.ema);
                    }
                    fvgBoxes = chartData.fvg_boxes || [];
                    updateTradeLevels(chartData.levels || (stats.live_trade ? {
                        entry: stats.live_trade.entry_price,
                        stop_loss: stats.live_trade.stop_loss,
                        take_profit: stats.live_trade.take_profit,
                        side: stats.live_trade.side,
                    } : null));
                    if (!chartFitted) {
                        chart.timeScale().fitContent();
                        chartFitted = true;
                    }
                    requestAnimationFrame(drawFvgBoxes);
                } else if (stats.live_trade) {
                    updateTradeLevels({
                        entry: stats.live_trade.entry_price,
                        stop_loss: stats.live_trade.stop_loss,
                        take_profit: stats.live_trade.take_profit,
                        side: stats.live_trade.side,
                    });
                } else {
                    clearTradeLevels();
                }

                const ema = analysis.ema || {};
                const emaChip = document.getElementById('chip_ema');
                emaChip.textContent = `EMA50 · 5m ${ema.trend_5m || '—'} / 15m ${ema.trend_15m || '—'} / 1h ${ema.trend_1h || '—'}`;
                emaChip.className = 'chip ' + ((ema.pass_long || ema.pass_short) ? 'on' : 'off');

                const fvg = (analysis.fvgs || {});
                const fvgChip = document.getElementById('chip_fvg');
                fvgChip.textContent = `FVG · ↑${fvg.bullish_unmitigated || 0} ↓${fvg.bearish_unmitigated || 0}` + (fvg.price_in_fvg ? ' · inside' : '');
                fvgChip.className = 'chip ' + (fvg.price_in_fvg ? 'on' : '');

                const biasChip = document.getElementById('chip_bias');
                biasChip.textContent = 'Bias ' + (analysis.bias || 'neutral');
                biasChip.className = 'chip ' + (analysis.bias === 'long' || analysis.bias === 'short' ? 'on' : '');

                const posContainer = document.getElementById('open_positions_container');
                if (positions.positions && positions.positions.length > 0) {
                    posContainer.innerHTML = '<table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Entry</th><th>Size</th><th>SL</th><th>TP</th><th>PnL $</th><th>Acct %</th></tr></thead><tbody>' +
                        positions.positions.map(p => {
                            const acct = p.pnl_account_pct != null ? p.pnl_account_pct : p.pnl_pct;
                            return `
                            <tr>
                                <td>${p.id.substring(0, 8)}...</td>
                                <td>${p.asset}</td>
                                <td class="side-${p.side}">${p.side}</td>
                                <td>${p.entry_price.toFixed(2)}</td>
                                <td>${p.position_size.toFixed(6)}</td>
                                <td>${p.stop_loss.toFixed(2)}</td>
                                <td>${p.take_profit.toFixed(2)}</td>
                                <td class="${(p.pnl || 0) >= 0 ? 'positive' : 'negative'}">${formatSignedCurrency(p.pnl || 0)}</td>
                                <td class="${acct >= 0 ? 'positive' : 'negative'}">${(acct >= 0 ? '+' : '') + Number(acct).toFixed(2)}%</td>
                            </tr>`;
                        }).join('') + '</tbody></table>';
                } else {
                    posContainer.innerHTML = '<p style="color:var(--muted);font-size:0.85rem">No open positions</p>';
                }

                const tradesContainer = document.getElementById('trades_container');
                if (trades.trades && trades.trades.length > 0) {
                    tradesContainer.innerHTML = '<table><thead><tr><th>ID</th><th>Type</th><th>Side</th><th>Session</th><th>Entry</th><th>PnL $</th><th>Acct %</th><th>R</th><th>Reason</th><th>Time</th></tr></thead><tbody>' +
                        trades.trades.map(t => {
                            const ts = t.timestamp || t.open_time || t.exit_time;
                            const timeLabel = ts ? new Date(ts * 1000).toLocaleTimeString() : '--';
                            const reason = t.exit_reason || t.reason || t.confirmation || (t.strategy_info && t.strategy_info.confirmation) || '--';
                            const acct = t.pnl_account_pct != null ? t.pnl_account_pct : null;
                            const r = t.r_multiple != null ? Number(t.r_multiple).toFixed(2) : '--';
                            const sess = t.session || (t.strategy_info && t.strategy_info.session) || '--';
                            return `
                            <tr>
                                <td>${t.id.substring(0, 8)}...</td>
                                <td>${t.type || t.status || '--'}</td>
                                <td class="side-${t.side || ''}">${t.side || '--'}</td>
                                <td>${sess}</td>
                                <td>${t.entry_price ? t.entry_price.toFixed(2) : '--'}</td>
                                <td class="${(t.pnl || 0) >= 0 ? 'positive' : 'negative'}">${t.pnl != null ? formatSignedCurrency(t.pnl) : '--'}</td>
                                <td class="${(acct || 0) >= 0 ? 'positive' : 'negative'}">${acct != null ? ((acct >= 0 ? '+' : '') + Number(acct).toFixed(2) + '%') : '--'}</td>
                                <td>${r}</td>
                                <td>${reason}</td>
                                <td>${timeLabel}</td>
                            </tr>`;
                        }).join('') + '</tbody></table>';
                } else {
                    tradesContainer.innerHTML = '<p style="color:var(--muted);font-size:0.85rem">No trades yet</p>';
                }
            } catch (e) {
                console.error('Fetch error:', e);
            }
        }

        initChart();
        refresh();
        setInterval(refresh, 2000);
    </script>
</body>
</html>
"""


def main():
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="SMC Trading Dashboard")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)), help="Port to listen on")
    parser.add_argument("--config", type=str, default=None, help="Path to config file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    dashboard = DashboardServer(port=args.port)
    asyncio.run(dashboard.start())


if __name__ == "__main__":
    main()
