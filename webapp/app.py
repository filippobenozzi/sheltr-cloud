#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from paho.mqtt import client as mqtt

APP_PORT = int(os.environ.get("APP_PORT", "8080"))
CONFIG_FILE = Path(os.environ.get("CONFIG_FILE", "/data/config.json"))
MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "filippo")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "filippo1994")
MQTT_BASE_TOPIC = os.environ.get("MQTT_BASE_TOPIC", "dr154").strip("/") or "dr154"
MQTT_PUBLISH_QOS = max(0, min(2, int(os.environ.get("MQTT_PUBLISH_QOS", "1"))))

KIND_META = {
    "light": {"label": "Luci", "maxChannels": 8, "channelPrefix": "Luce"},
    "shutter": {"label": "Tapparelle", "maxChannels": 4, "channelPrefix": "Tapparella"},
    "dimmer": {"label": "Dimmer", "maxChannels": 1, "channelPrefix": "Dimmer"},
    "thermostat": {"label": "Termostati", "maxChannels": 8, "channelPrefix": "Termostato"},
}

STORE_LOCK = threading.Lock()

app = Flask(__name__, static_folder="static", static_url_path="/static")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_int(value: Any, fallback: int) -> int:
    try:
        if isinstance(value, bool):
            return fallback
        return int(value)
    except (TypeError, ValueError):
        return fallback


def clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def clean_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def slugify(value: Any, fallback: str) -> str:
    raw = clean_text(value, fallback).lower()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw or fallback


def default_channel_name(kind: str, channel: int) -> str:
    prefix = KIND_META.get(kind, KIND_META["light"])["channelPrefix"]
    return f"{prefix} {channel}"


def normalize_board(raw: Any, index: int) -> dict[str, Any]:
    board = raw if isinstance(raw, dict) else {}
    kind = clean_text(board.get("kind"), "light").lower()
    if kind not in KIND_META:
        kind = "light"
    max_channels = KIND_META[kind]["maxChannels"]

    board_id = slugify(board.get("id") or board.get("name"), f"board-{index + 1}")
    name = clean_text(board.get("name"), board_id)
    address = clamp(to_int(board.get("address"), index + 1), 0, 254)
    channel_start = clamp(to_int(board.get("channelStart"), 1), 1, max_channels)
    channel_end = clamp(to_int(board.get("channelEnd"), max_channels), 1, max_channels)
    if channel_end < channel_start:
        channel_end = channel_start

    channel_map: dict[int, dict[str, Any]] = {}
    for entry in board.get("channels", []):
        if not isinstance(entry, dict):
            continue
        channel = clamp(to_int(entry.get("channel"), -1), 1, max_channels)
        if channel < channel_start or channel > channel_end:
            continue
        channel_map[channel] = entry

    channels: list[dict[str, Any]] = []
    for channel in range(channel_start, channel_end + 1):
        saved = channel_map.get(channel, {})
        channels.append(
            {
                "channel": channel,
                "name": clean_text(saved.get("name"), default_channel_name(kind, channel)),
                "room": clean_text(saved.get("room"), "Senza stanza"),
            }
        )

    return {
        "id": board_id,
        "name": name,
        "address": address,
        "kind": kind,
        "channelStart": channel_start,
        "channelEnd": channel_end,
        "channels": channels,
    }


def normalize_instance(raw: Any, fallback_id: str) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    instance_id = slugify(payload.get("id"), slugify(fallback_id, "dr154-1"))
    instance_name = clean_text(payload.get("name"), instance_id)
    boards_raw = payload.get("boards")
    boards_input = boards_raw if isinstance(boards_raw, list) else []

    boards = [normalize_board(item, idx) for idx, item in enumerate(boards_input[:64])]
    if not boards:
        boards = [normalize_board({"id": "board-1", "name": "Scheda Luci", "kind": "light"}, 0)]

    return {
        "id": instance_id,
        "name": instance_name,
        "protocolVersion": "1.6",
        "boards": boards,
        "updatedAt": now_iso(),
    }


def ensure_store_file() -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text('{"instances": []}\n', encoding="utf-8")


def load_store() -> dict[str, Any]:
    ensure_store_file()
    try:
        content = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        content = {"instances": []}
    instances = content.get("instances", [])
    if not isinstance(instances, list):
        instances = []
    return {"instances": instances}


def save_store(store: dict[str, Any]) -> None:
    ensure_store_file()
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def find_instance(store: dict[str, Any], instance_id: str) -> dict[str, Any] | None:
    for instance in store["instances"]:
        if instance.get("id") == instance_id:
            return instance
    return None


def mqtt_publish(topic: str, payload: dict[str, Any]) -> None:
    raw_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    client_id = f"iotsheltr-{os.getpid()}-{int(datetime.now().timestamp())}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    result = client.publish(topic, raw_payload.encode("utf-8"), qos=MQTT_PUBLISH_QOS, retain=True)
    result.wait_for_publish(timeout=5)
    if not result.is_published():
        raise RuntimeError("Timeout pubblicazione MQTT")
    client.disconnect()


def list_view(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": instance.get("id"),
        "name": instance.get("name"),
        "protocolVersion": instance.get("protocolVersion", "1.6"),
        "boardsCount": len(instance.get("boards", [])),
        "updatedAt": instance.get("updatedAt"),
    }


def err(message: str, status: int = 400):
    return jsonify({"error": message}), status


@app.get("/")
def root():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/instance/<instance_id>")
def instance_page(instance_id: str):
    _ = instance_id
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/api/meta")
def api_meta():
    return jsonify({"kindMeta": KIND_META, "mqttBaseTopic": MQTT_BASE_TOPIC})


@app.get("/api/instances")
def api_list_instances():
    with STORE_LOCK:
        store = load_store()
    data = [list_view(instance) for instance in store["instances"]]
    data.sort(key=lambda x: str(x.get("name", "")).lower())
    return jsonify({"instances": data})


@app.post("/api/instances")
def api_create_instance():
    body = request.get_json(silent=True) or {}
    fallback_id = clean_text(body.get("id"), "dr154-1")
    instance = normalize_instance(body, fallback_id=fallback_id)

    with STORE_LOCK:
        store = load_store()
        if find_instance(store, instance["id"]) is not None:
            return err(f"Istanza '{instance['id']}' già esistente", 409)
        store["instances"].append(instance)
        save_store(store)

    return jsonify({"instance": instance}), 201


@app.get("/api/instances/<instance_id>")
def api_get_instance(instance_id: str):
    normalized_id = slugify(instance_id, "dr154-1")
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, normalized_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    return jsonify({"instance": instance})


@app.put("/api/instances/<instance_id>")
def api_update_instance(instance_id: str):
    body = request.get_json(silent=True) or {}
    normalized_id = slugify(instance_id, "dr154-1")
    body["id"] = normalized_id
    instance = normalize_instance(body, fallback_id=normalized_id)

    with STORE_LOCK:
        store = load_store()
        current = find_instance(store, normalized_id)
        if current is None:
            return err("Istanza non trovata", 404)
        for idx, item in enumerate(store["instances"]):
            if item.get("id") == normalized_id:
                store["instances"][idx] = instance
                break
        save_store(store)

    return jsonify({"instance": instance})


@app.delete("/api/instances/<instance_id>")
def api_delete_instance(instance_id: str):
    normalized_id = slugify(instance_id, "dr154-1")
    with STORE_LOCK:
        store = load_store()
        before = len(store["instances"])
        store["instances"] = [item for item in store["instances"] if item.get("id") != normalized_id]
        if len(store["instances"]) == before:
            return err("Istanza non trovata", 404)
        save_store(store)
    return jsonify({"ok": True})


@app.post("/api/instances/<instance_id>/publish")
def api_publish_instance(instance_id: str):
    normalized_id = slugify(instance_id, "dr154-1")
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, normalized_id)
    if instance is None:
        return err("Istanza non trovata", 404)

    body = request.get_json(silent=True) or {}
    topic = clean_text(body.get("topic"), f"{MQTT_BASE_TOPIC}/{normalized_id}/config")
    if not topic:
        return err("Topic MQTT non valido", 400)

    try:
        mqtt_publish(topic, instance)
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore pubblicazione MQTT: {exc}", 502)

    return jsonify({"ok": True, "topic": topic, "qos": MQTT_PUBLISH_QOS, "retain": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
