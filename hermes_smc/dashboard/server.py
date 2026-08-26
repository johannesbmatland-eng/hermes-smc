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

    async def _engine_loop(self):
        """Background loop for the trading engine."""
        while self.engine and self.engine._running and not self.engine._stopped:
            try:
                await self.engine.run_tick()
            except Exception as e:
                logger.error(f"Engine tick failed: {e}")
            await asyncio.sleep(10)

    def get_stats(self) -> dict[str, Any]:
        """Get current trading statistics."""
        engine = self.load_engine()
        pm = engine.position_manager

        open_positions = list(pm.open_positions.values())
        closed_positions = pm.closed_positions

        total_pnl = sum(p.get("pnl", 0) for p in closed_positions)
        total_pnl_pct = sum(p.get("pnl_pct", 0) for p in closed_positions)
        win_count = sum(1 for p in closed_positions if p.get("pnl", 0) > 0)
        win_rate = win_count / len(closed_positions) if closed_positions else 0
        market = engine.config.get("market", "BTC/USD")

        return {
            "market": market,
            "capital": pm.capital,
            "initial_capital": pm.initial_capital,
            "open_positions": open_positions,
            "closed_positions": closed_positions[-10:],
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "win_rate": win_rate,
            "trade_count": len(closed_positions),
            "engine_status": "running" if (self.engine and self.engine._running) else "stopped",
            "last_update": time.time(),
            "price": engine.last_price,
        }

    def get_positions(self) -> list[dict]:
        engine = self.load_engine()
        return list(engine.position_manager.open_positions.values())

    def get_trades(self, limit: int = 20) -> list[dict]:
        engine = self.load_engine()
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
        return {
            "market": engine.config.get("market", "BTC/USD"),
            "timeframe": "5m",
            "candles": candles,
            "ema": engine.last_ema_5m,
            "fvg": nearest,
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

        <div class="layout">
            <div class="card">
                <div class="card-title">BTC/USD · 5m</div>
                <div id="chart"></div>
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
                <div class="stat" style="margin-top:8px"><div class="stat-label">Total PnL</div><div class="stat-value" id="pnl">--</div></div>
                <div class="stat" style="margin-top:8px"><div class="stat-label">PnL %</div><div class="stat-value" id="pnl_pct">--</div></div>
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
            <div class="card-title">Open positions</div>
            <div id="open_positions_container"><p style="color:var(--muted);font-size:0.85rem">No open positions</p></div>
        </div>

        <div class="card">
            <div class="card-title">Recent trades</div>
            <div id="trades_container"><p style="color:var(--muted);font-size:0.85rem">No trades yet</p></div>
        </div>

        <p id="refresh">Auto-refresh every 10s</p>
    </div>

    <script>
        let chart, candleSeries, emaSeries, fvgTopLine, fvgBottomLine;

        function initChart() {
            const el = document.getElementById('chart');
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
                width: el.clientWidth,
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
            window.addEventListener('resize', () => {
                chart.applyOptions({ width: el.clientWidth });
            });
        }

        function setFvgLines(fvg) {
            if (fvgTopLine) { candleSeries.removePriceLine(fvgTopLine); fvgTopLine = null; }
            if (fvgBottomLine) { candleSeries.removePriceLine(fvgBottomLine); fvgBottomLine = null; }
            if (!fvg) return;
            const color = fvg.type === 'bullish' ? 'rgba(62,207,142,0.85)' : 'rgba(240,113,120,0.85)';
            fvgTopLine = candleSeries.createPriceLine({
                price: fvg.top, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true, title: 'FVG top',
            });
            fvgBottomLine = candleSeries.createPriceLine({
                price: fvg.bottom, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true, title: 'FVG bot',
            });
        }

        function formatCurrency(value) {
            if (value == null || isNaN(value)) return '--';
            return '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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

        async function refresh() {
            try {
                const [stats, positions, trades, analysis, chartData] = await Promise.all([
                    fetch('/api/stats').then(r => r.json()),
                    fetch('/api/positions').then(r => r.json()),
                    fetch('/api/trades').then(r => r.json()),
                    fetch('/api/analysis').then(r => r.json()),
                    fetch('/api/chart').then(r => r.json()),
                ]);

                document.getElementById('market_label').textContent = stats.market || analysis.market || 'BTC/USD';
                if (stats.price != null) {
                    document.getElementById('live_price').textContent = formatCurrency(stats.price);
                }

                document.getElementById('capital').textContent = formatCurrency(stats.capital);
                document.getElementById('pnl').textContent = formatCurrency(stats.total_pnl);
                const pnlPct = stats.total_pnl_pct || 0;
                document.getElementById('pnl_pct').textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%';
                document.getElementById('pnl_pct').className = 'stat-value ' + (pnlPct >= 0 ? 'positive' : 'negative');
                document.getElementById('winrate').textContent = ((stats.win_rate || 0) * 100).toFixed(1) + '%';
                document.getElementById('trades').textContent = stats.trade_count;
                document.getElementById('open_pos').textContent = (stats.open_positions || []).length;
                document.getElementById('status_text').textContent = stats.engine_status;
                document.getElementById('status_dot').className = 'status-dot ' + (stats.engine_status === 'running' ? 'running' : 'stopped');
                document.getElementById('last_update').textContent = new Date((stats.last_update || Date.now()/1000) * 1000).toLocaleTimeString();

                // Bot thinking
                const phaseEl = document.getElementById('phase');
                phaseEl.textContent = analysis.phase || 'Scanning…';
                phaseEl.className = 'phase ' + (analysis.bias || 'neutral');
                const waiting = (analysis.waiting_for && analysis.waiting_for.length)
                    ? analysis.waiting_for.join(' · ')
                    : 'No blockers';
                document.getElementById('waiting').textContent = waiting;
                renderChecklist(analysis.checklist || []);

                // Chart
                if (chartData.candles && chartData.candles.length) {
                    candleSeries.setData(chartData.candles);
                    if (chartData.ema && chartData.ema.length) {
                        emaSeries.setData(chartData.ema);
                    }
                    setFvgLines(chartData.fvg);
                    chart.timeScale().fitContent();
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

                // Positions
                const posContainer = document.getElementById('open_positions_container');
                if (positions.positions && positions.positions.length > 0) {
                    posContainer.innerHTML = '<table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Entry</th><th>Size</th><th>SL</th><th>TP</th><th>PnL %</th></tr></thead><tbody>' +
                        positions.positions.map(p => `
                            <tr>
                                <td>${p.id.substring(0, 8)}...</td>
                                <td>${p.asset}</td>
                                <td class="side-${p.side}">${p.side}</td>
                                <td>${p.entry_price.toFixed(2)}</td>
                                <td>${p.position_size.toFixed(6)}</td>
                                <td>${p.stop_loss.toFixed(2)}</td>
                                <td>${p.take_profit.toFixed(2)}</td>
                                <td class="${p.pnl_pct >= 0 ? 'positive' : 'negative'}">${(p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2)}%</td>
                            </tr>
                        `).join('') + '</tbody></table>';
                } else {
                    posContainer.innerHTML = '<p style="color:var(--muted);font-size:0.85rem">No open positions</p>';
                }

                const tradesContainer = document.getElementById('trades_container');
                if (trades.trades && trades.trades.length > 0) {
                    tradesContainer.innerHTML = '<table><thead><tr><th>ID</th><th>Type</th><th>Side</th><th>Entry</th><th>Size</th><th>SL</th><th>TP</th><th>Reason</th><th>Time</th></tr></thead><tbody>' +
                        trades.trades.map(t => `
                            <tr>
                                <td>${t.id.substring(0, 8)}...</td>
                                <td>${t.type}</td>
                                <td class="side-${t.side || ''}">${t.side || '--'}</td>
                                <td>${t.entry_price ? t.entry_price.toFixed(2) : '--'}</td>
                                <td>${t.position_size ? t.position_size.toFixed(6) : '--'}</td>
                                <td>${t.sl_price ? t.sl_price.toFixed(2) : '--'}</td>
                                <td>${t.tp_price ? t.tp_price.toFixed(2) : '--'}</td>
                                <td>${t.reason || t.confirmation || '--'}</td>
                                <td>${new Date(t.timestamp * 1000).toLocaleTimeString()}</td>
                            </tr>
                        `).join('') + '</tbody></table>';
                } else {
                    tradesContainer.innerHTML = '<p style="color:var(--muted);font-size:0.85rem">No trades yet</p>';
                }
            } catch (e) {
                console.error('Fetch error:', e);
            }
        }

        initChart();
        refresh();
        setInterval(refresh, 10000);
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
