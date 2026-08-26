"""SMC Dashboard - Real-time trading dashboard."""

import asyncio
import json
import logging
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any

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

        # Calculate stats
        total_pnl = sum(p.get("pnl", 0) for p in closed_positions)
        total_pnl_pct = sum(p.get("pnl_pct", 0) for p in closed_positions)
        win_count = sum(1 for p in closed_positions if p.get("pnl", 0) > 0)
        win_rate = win_count / len(closed_positions) if closed_positions else 0

        return {
            "capital": pm.capital,
            "initial_capital": pm.initial_capital,
            "open_positions": open_positions,
            "closed_positions": closed_positions[-10:],  # Last 10
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "win_rate": win_rate,
            "trade_count": len(closed_positions),
            "engine_status": "running" if (self.engine and self.engine._running) else "stopped",
            "last_update": time.time(),
        }

    def get_positions(self) -> list[dict]:
        """Get current open positions."""
        engine = self.load_engine()
        return list(engine.position_manager.open_positions.values())

    def get_trades(self, limit: int = 20) -> list[dict]:
        """Get recent trades."""
        engine = self.load_engine()
        return engine.trades[-limit:]

    async def start(self):
        """Start the dashboard server."""
        self._server = HTTPServer(("0.0.0.0", self.port), DashboardHandler)
        DashboardHandler.engine_server = self
        logger.info(f"Dashboard server starting on port {self.port}")
        await self.start_engine()
        # serve_forever blocks — run it in a thread so the asyncio engine loop keeps ticking
        Thread(target=self._server.serve_forever, daemon=True).start()
        while True:
            await asyncio.sleep(3600)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the dashboard."""

    engine_server: DashboardServer | None = None

    def log_message(self, format, *args):
        logger.info(f"HTTP: {format % args}")

    def send_json(self, data: dict):
        """Send JSON response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def send_html(self, html: str):
        """Send HTML response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/" or self.path == "/index.html":
            self.send_html(self._get_dashboard_html())
        elif self.path == "/api/stats":
            stats = self.engine_server.get_stats() if self.engine_server else {}
            self.send_json(stats)
        elif self.path == "/api/positions":
            positions = self.engine_server.get_positions() if self.engine_server else []
            self.send_json({"positions": positions})
        elif self.path == "/api/trades":
            trades = self.engine_server.get_trades() if self.engine_server else []
            self.send_json({"trades": trades})
        elif self.path == "/api/config":
            config_path = CONFIG_PATH
            if config_path.exists():
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                self.send_json({"config": config})
            else:
                self.send_json({"error": "Config not found"})
        else:
            self.send_response(404)
            self.end_headers()

    def _get_dashboard_html(self) -> str:
        """Generate dashboard HTML."""
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hermes SMC Trading Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0d13;
            color: #e8edf4;
            padding: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }
        h1 { font-size: 1.5rem; margin-bottom: 10px; }
        .subtitle { color: #8a94a6; font-size: 0.85rem; margin-bottom: 20px; }
        .card {
            background: linear-gradient(180deg, #161c28 0%, #12161f 100%);
            border: 1px solid #232b3a;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 16px;
        }
        .card-title {
            font-size: 0.7rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #8a94a6;
            margin-bottom: 12px;
            font-weight: 700;
        }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; }
        .stat {
            background: #12161f;
            border: 1px solid #232b3a;
            border-radius: 8px;
            padding: 12px;
        }
        .stat-label { font-size: 0.65rem; color: #8a94a6; text-transform: uppercase; letter-spacing: 0.05em; }
        .stat-value { font-size: 1.2rem; font-weight: 600; margin-top: 4px; }
        .stat-value.positive { color: #3fb950; }
        .stat-value.negative { color: #f16a61; }
        .stat-value.neutral { color: #5aa2ff; }
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th { text-align: left; padding: 8px 12px; background: #181f2b; color: #8a94a6; font-weight: 600; }
        td { padding: 8px 12px; border-top: 1px solid #1c2330; }
        tr:hover { background: rgba(255,255,255,0.03); }
        .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
        .status-dot.running { background: #3fb950; }
        .status-dot.stopped { background: #f16a61; }
        #refresh { font-size: 0.75rem; color: #8a94a6; }
    </style>
</head>
<body>
    <h1>📊 Hermes SMC Trading Dashboard</h1>
    <p class="subtitle">ICT/SMC Trading Bot · BTC/EUR · Paper Trading</p>

    <div class="grid">
        <div class="card">
            <div class="card-title">Account Overview</div>
            <div class="stat">
                <div class="stat-label">Capital (Paper)</div>
                <div class="stat-value neutral" id="capital">--</div>
            </div>
            <div class="stat" style="margin-top: 8px;">
                <div class="stat-label">Initial Capital</div>
                <div class="stat-value" id="initial">--</div>
            </div>
            <div class="stat" style="margin-top: 8px;">
                <div class="stat-label">Total PnL</div>
                <div class="stat-value" id="pnl">--</div>
            </div>
            <div class="stat" style="margin-top: 8px;">
                <div class="stat-label">Total PnL %</div>
                <div class="stat-value" id="pnl_pct">--</div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">Performance</div>
            <div class="stat">
                <div class="stat-label">Win Rate</div>
                <div class="stat-value" id="winrate">--</div>
            </div>
            <div class="stat" style="margin-top: 8px;">
                <div class="stat-label">Total Trades</div>
                <div class="stat-value" id="trades">--</div>
            </div>
            <div class="stat" style="margin-top: 8px;">
                <div class="stat-label">Open Positions</div>
                <div class="stat-value" id="open_pos">--</div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">Engine Status</div>
            <div class="stat">
                <div class="stat-label">Status</div>
                <div class="stat-value">
                    <span class="status-dot running" id="status_dot"></span>
                    <span id="status_text">--</span>
                </div>
            </div>
            <div class="stat" style="margin-top: 8px;">
                <div class="stat-label">Last Update</div>
                <div class="stat-value" id="last_update">--</div>
            </div>
            <div class="stat" style="margin-top: 8px;">
                <div class="stat-label">Strategy</div>
                <div class="stat-value" style="font-size: 0.9rem;">SMC / ICT</div>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="card-title">Open Positions</div>
        <div id="open_positions_container">
            <p style="color: #8a94a6; font-size: 0.85rem;">No open positions</p>
        </div>
    </div>

    <div class="card">
        <div class="card-title">Recent Trades</div>
        <div id="trades_container">
            <p style="color: #8a94a6; font-size: 0.85rem;">No trades yet</p>
        </div>
    </div>

    <p id="refresh">Auto-refreshing every 10 seconds...</p>

    <script>
        async function fetchStats() {
            try {
                const [stats, positions, trades] = await Promise.all([
                    fetch('/api/stats').then(r => r.json()),
                    fetch('/api/positions').then(r => r.json()),
                    fetch('/api/trades').then(r => r.json())
                ]);

                // Update stats
                document.getElementById('capital').textContent = formatCurrency(stats.capital);
                document.getElementById('initial').textContent = formatCurrency(stats.initial_capital);
                document.getElementById('pnl').textContent = formatCurrency(stats.total_pnl);
                document.getElementById('pnl_pct').textContent = (stats.total_pnl_pct >= 0 ? '+' : '') + stats.total_pnl_pct.toFixed(2) + '%';
                document.getElementById('pnl_pct').className = 'stat-value ' + (stats.total_pnl_pct >= 0 ? 'positive' : 'negative');
                document.getElementById('winrate').textContent = (stats.win_rate * 100).toFixed(1) + '%';
                document.getElementById('trades').textContent = stats.trade_count;
                document.getElementById('open_pos').textContent = stats.open_positions.length;
                document.getElementById('status_text').textContent = stats.engine_status;
                document.getElementById('last_update').textContent = new Date(stats.last_update * 1000).toLocaleTimeString();

                // Update positions
                const posContainer = document.getElementById('open_positions_container');
                if (positions.positions && positions.positions.length > 0) {
                    posContainer.innerHTML = '<table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Entry</th><th>Size</th><th>SL</th><th>TP</th><th>PnL %</th></tr></thead><tbody>' +
                        positions.positions.map(p => `
                            <tr>
                                <td>${p.id.substring(0, 8)}...</td>
                                <td>${p.asset}</td>
                                <td>${p.side}</td>
                                <td>${p.entry_price.toFixed(2)}</td>
                                <td>${p.position_size.toFixed(6)}</td>
                                <td>${p.stop_loss.toFixed(2)}</td>
                                <td>${p.take_profit.toFixed(2)}</td>
                                <td class="${p.pnl_pct >= 0 ? 'positive' : 'negative'}">${(p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2)}%</td>
                            </tr>
                        `).join('') +
                    '</tbody></table>';
                } else {
                    posContainer.innerHTML = '<p style="color: #8a94a6; font-size: 0.85rem;">No open positions</p>';
                }

                // Update trades
                const tradesContainer = document.getElementById('trades_container');
                if (trades.trades && trades.trades.length > 0) {
                    tradesContainer.innerHTML = '<table><thead><tr><th>ID</th><th>Type</th><th>Entry</th><th>Size</th><th>SL</th><th>TP</th><th>Reason</th><th>Time</th></tr></thead><tbody>' +
                        trades.trades.map(t => `
                            <tr>
                                <td>${t.id.substring(0, 8)}...</td>
                                <td>${t.type}</td>
                                <td>${t.entry_price ? t.entry_price.toFixed(2) : '--'}</td>
                                <td>${t.position_size ? t.position_size.toFixed(6) : '--'}</td>
                                <td>${t.sl_price ? t.sl_price.toFixed(2) : '--'}</td>
                                <td>${t.tp_price ? t.tp_price.toFixed(2) : '--'}</td>
                                <td>${t.reason || t.confirmation || '--'}</td>
                                <td>${new Date(t.timestamp * 1000).toLocaleTimeString()}</td>
                            </tr>
                        `).join('') +
                    '</tbody></table>';
                } else {
                    tradesContainer.innerHTML = '<p style="color: #8a94a6; font-size: 0.85rem;">No trades yet</p>';
                }
            } catch (e) {
                console.error('Fetch error:', e);
            }
        }

        function formatCurrency(value) {
            return '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        // Initial fetch
        fetchStats();

        // Auto-refresh every 10 seconds
        setInterval(fetchStats, 10000);
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
