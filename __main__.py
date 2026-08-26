"""Main entry point for hermes-smc trading bot."""

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

from .engine.paper_trading import PaperTradingEngine, SMCConfig
from .dashboard.server import DashboardServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    logger.info("Shutdown signal received, stopping...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


async def run_engine_only(config_path: Path | None = None, paper: bool = True):
    """Run the SMC engine without dashboard."""
    config = SMCConfig(config_path)
    engine = PaperTradingEngine(config) if paper else None

    if engine is None:
        logger.error("Live trading not implemented yet, use paper=True")
        return

    logger.info(f"Starting SMC engine (paper mode) with {engine.position_manager.capital:.2f} USD capital")
    engine._running = True

    try:
        while engine._running and not engine._stopped:
            await engine.run_tick()
            await asyncio.sleep(10)
    except KeyboardInterrupt:
        logger.info("Interrupted, stopping...")
    finally:
        engine.stop()


async def run_dashboard(config_path: Path | None = None, port: int = 8080):
    """Run the dashboard server."""
    dashboard = DashboardServer(port=port)
    await dashboard.start()


async def run_combined(config_path: Path | None = None, port: int = 8080):
    """Run both engine and dashboard."""
    config = SMCConfig(config_path)
    engine = PaperTradingEngine(config)

    logger.info(f"Starting SMC engine + dashboard with {engine.position_manager.capital:.2f} USD capital")

    # Start engine loop
    engine._running = True
    engine_task = asyncio.create_task(_engine_loop(engine))

    # Start dashboard
    dashboard = DashboardServer(port=port)
    dashboard.engine = engine

    try:
        await dashboard.start()
    except KeyboardInterrupt:
        logger.info("Interrupted, stopping...")
    finally:
        engine._running = False
        engine._stopped = True
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass


async def _engine_loop(engine: PaperTradingEngine):
    """Background engine loop."""
    while engine._running and not engine._stopped:
        try:
            await engine.run_tick()
        except Exception as e:
            logger.error(f"Engine tick failed: {e}")
        await asyncio.sleep(10)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Hermes SMC Trading Bot")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to strategy.yaml config file",
    )
    parser.add_argument(
        "--mode",
        choices=["engine", "dashboard", "combined"],
        default="combined",
        help="Run mode: engine, dashboard, or combined",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Dashboard port (default: 8080)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Paper trading mode (default: True)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Live trading mode (not implemented yet)",
    )
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None

    if args.live:
        logger.warning("Live trading not implemented yet, falling back to paper")
        args.paper = True

    if args.mode == "engine":
        asyncio.run(run_engine_only(config_path, paper=args.paper))
    elif args.mode == "dashboard":
        asyncio.run(run_dashboard(config_path, args.port))
    elif args.mode == "combined":
        asyncio.run(run_combined(config_path, args.port))


if __name__ == "__main__":
    main()
