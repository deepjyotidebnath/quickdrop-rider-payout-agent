import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, send_file

from . import config
from .agent import Agent
from .data import DataStore, norm_rider
from .db import DB, loads
from .payswift import PaySwiftClient


def create_app(db=None, data=None, payswift=None, start_reconciler=True):
    app = Flask(__name__)
    app.json.ensure_ascii = False
    db = db or DB(config.DATABASE_URL)
    data = data or DataStore(config.DATA_DIR)
    agent = Agent(db, data, payswift or PaySwiftClient())
    app.agent = agent

    if start_reconciler:
        def loop():
            while True:
                time.sleep(config.RECONCILE_INTERVAL_S)
                try:
                    agent.reconcile_unknown()
                except Exception:
                    pass
        threading.Thread(target=loop, daemon=True, name="payout-reconciler").start()

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/messages")
    def messages():
        body = request.get_json(silent=True) or {}
        mid, rider, text = body.get("message_id"), body.get("rider_id"), body.get("text")
        if not (isinstance(mid, str) and mid and isinstance(rider, str) and rider and isinstance(text, str)):
            return jsonify(error="message_id, rider_id and text are required"), 400
        received = body.get("received_at") or datetime.now().astimezone().isoformat()
        reply = agent.handle(mid, rider, text, received)
        return jsonify(reply=reply)

    @app.get("/trace/<rider_id>")
    def trace(rider_id):
        rows = db.query("SELECT ts,type,name,input,output FROM trace WHERE rider_id=? ORDER BY n", (norm_rider(rider_id),))
        return jsonify([{"at": r["ts"], "type": r["type"], "name": r["name"],
                         "input": loads(r["input"]), "output": loads(r["output"])} for r in rows])

    @app.get("/ops/pending")
    def ops_pending():
        return jsonify(agent.pending_items())

    @app.get("/ops/items")
    def ops_items():
        return jsonify(agent.all_items())

    @app.post("/ops/<item_id>/approve")
    def ops_approve(item_id):
        res, code = agent.decide(item_id, True)
        return jsonify(res), code

    @app.post("/ops/<item_id>/reject")
    def ops_reject(item_id):
        res, code = agent.decide(item_id, False)
        return jsonify(res), code

    @app.get("/ops/riders")
    def ops_riders():
        rows = db.query("SELECT rider_id, COUNT(*) AS messages, MAX(created_n) AS last_n FROM messages GROUP BY rider_id ORDER BY last_n DESC")
        return jsonify([{"rider_id": r["rider_id"], "messages": r["messages"]} for r in rows])

    @app.get("/ops/conversation/<rider_id>")
    def ops_conversation(rider_id):
        rows = db.query("SELECT message_id,text,reply,received_at,status FROM messages WHERE rider_id=? ORDER BY created_n",
                        (norm_rider(rider_id),))
        return jsonify(rows)

    @app.get("/ops")
    def ops_page():
        return send_file(os.path.join(os.path.dirname(__file__), "ops.html"))

    @app.post("/admin/reset")
    def admin_reset():
        if not config.ALLOW_ADMIN_RESET:
            return jsonify(error="disabled"), 404
        db.reset()
        return jsonify(ok=True)

    return app


app = None


def get_app():
    global app
    if app is None:
        app = create_app()
    return app
