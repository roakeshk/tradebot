from __future__ import annotations

import os
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .analytics import InstrumentAnalyzer, PriceBuffer
from .config import DB_PATH, DEFAULT_SIGNAL_THRESHOLD, MARKET, MAX_DD, RISK_CAPITAL, default_market_meta
from .database import Database
from .services import DashboardService
from .templates import DASHBOARD_HTML, SETUP_HTML


def create_app() -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    database = Database(DB_PATH, starting_capital=RISK_CAPITAL, max_drawdown=MAX_DD)
    price_buffer = PriceBuffer()
    analyzer = InstrumentAnalyzer(price_buffer)
    service = DashboardService(database, price_buffer, analyzer)
    cur, locale, js_tz, tz_label = default_market_meta(MARKET)

    def check_key() -> bool:
        return service.auth_ok(request.headers.get("X-API-Key", ""), os.environ.get("TRADEBOT_KEY", ""))

    @app.route("/")
    def index() -> str:
        return render_template_string(
            DASHBOARD_HTML,
            CUR=cur,
            LOCALE=locale,
            JS_TZ=js_tz,
            TZ_LABEL=tz_label,
            MARKET=MARKET,
        )

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "time": datetime.now().isoformat()})

    @app.route("/callback")
    def callback() -> str:
        auth_token = request.args.get("auth_token", "") or request.args.get("token", "")
        source = request.args.get("source", "broker")
        if auth_token:
            database.kv_set("broker_auth_token", auth_token)
            database.kv_set("broker_token_time", datetime.now().isoformat())
            database.kv_set("broker_token_source", source)
            return "<html><body style='background:#08111d;color:#9bd2be;font-family:Segoe UI;padding:40px'><h2>Connected</h2><p>Token saved. Close this tab.</p></body></html>"
        base_url = request.host_url.rstrip("/")
        return f"<html><body style='background:#08111d;color:#dce6f2;font-family:Segoe UI;padding:40px'><h2>TradeBot Callback</h2><p>{base_url}/callback</p></body></html>"

    @app.route("/setup")
    def setup() -> str:
        base_url = request.host_url.rstrip("/")
        return render_template_string(SETUP_HTML, callback_url=base_url + "/callback", base_url=base_url, market=MARKET)

    @app.route("/api/push/trade", methods=["POST"])
    def push_trade():
        if not check_key():
            return jsonify({"error": "unauthorized"}), 401
        service.push_trade(request.get_json() or {})
        return jsonify({"status": "ok"})

    @app.route("/api/push/status", methods=["POST"])
    def push_status():
        if not check_key():
            return jsonify({"error": "unauthorized"}), 401
        service.push_status(request.get_json() or {})
        return jsonify({"status": "ok"})

    @app.route("/api/push/log", methods=["POST"])
    def push_log():
        if not check_key():
            return jsonify({"error": "unauthorized"}), 401
        payload = request.get_json() or {}
        service.push_log(payload.get("level", "INFO"), payload.get("message", ""))
        return jsonify({"status": "ok"})

    @app.route("/api/status")
    def api_status():
        return jsonify(service.get_status())

    @app.route("/api/metrics")
    def api_metrics():
        return jsonify(service.get_metrics())

    @app.route("/api/trades")
    def api_trades():
        limit = int(request.args.get("limit", 50))
        return jsonify(service.database.recent_trades(limit))

    @app.route("/api/equity")
    def api_equity():
        capital = float(request.args.get("capital", RISK_CAPITAL))
        return jsonify(service.database.equity_curve(capital))

    @app.route("/api/logs")
    def api_logs():
        return jsonify({"lines": service.database.recent_logs()})

    @app.route("/api/markets")
    def api_markets():
        return jsonify(service.get_markets())

    @app.route("/api/analysis/<symbol>")
    def api_analysis(symbol: str):
        return jsonify(service.ensure_signal_book(symbol))

    @app.route("/api/technicals/<symbol>")
    def api_technicals(symbol: str):
        return jsonify(service.analyze_symbol(symbol)["technicals"])

    @app.route("/api/fundamentals/<symbol>")
    def api_fundamentals(symbol: str):
        return jsonify(service.analyze_symbol(symbol)["fundamentals"])

    @app.route("/api/sentiment/<symbol>")
    def api_sentiment(symbol: str):
        return jsonify(service.analyze_symbol(symbol)["sentiment"])

    @app.route("/api/signals")
    def api_signals():
        return jsonify(service.get_signals())

    @app.route("/api/price-history/<symbol>")
    def api_price_history(symbol: str):
        bars = int(request.args.get("bars", 160))
        return jsonify(service.get_price_history(symbol, bars))

    @app.route("/api/news")
    def api_news():
        symbol = request.args.get("symbol")
        return jsonify(service.get_news(symbol))

    @app.route("/api/reports")
    def api_reports():
        return jsonify(service.get_reports())

    @app.route("/api/sim/start", methods=["POST"])
    def api_sim_start():
        payload = request.get_json(silent=True) or {}
        market_id = payload.get("market", MARKET)
        speed = int(payload.get("speed", 5))
        auto_trade = bool(payload.get("auto_trade", True))
        threshold = float(payload.get("threshold", DEFAULT_SIGNAL_THRESHOLD))
        direction_filter = payload.get("direction_filter", "both")
        max_trades = int(payload.get("max_trades", 0))
        capital = float(payload.get("capital", RISK_CAPITAL))
        return jsonify(service.simulation_start(market_id, speed, auto_trade, threshold, direction_filter, max_trades, capital))

    @app.route("/api/sim/stop", methods=["POST"])
    def api_sim_stop():
        return jsonify(service.simulation_stop())

    @app.route("/api/sim/status")
    def api_sim_status():
        return jsonify(service.simulation_status())

    @app.route("/api/sim/reset", methods=["POST"])
    def api_sim_reset():
        return jsonify(service.simulation_reset())

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  TradeBot Relay [{MARKET} market]")
    print(f"  Dashboard:   http://localhost:{port}/")
    print(f"  Setup guide: http://localhost:{port}/setup")
    print(f"  Callback:    http://localhost:{port}/callback\n")
    app.run(host="0.0.0.0", port=port, debug=False)