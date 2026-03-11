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
LIGHT_STATE_FILE = Path(os.environ.get("LIGHT_STATE_FILE", "/data/light_state.json"))
MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "filippo")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "filippo1994")
MQTT_BASE_TOPIC = os.environ.get("MQTT_BASE_TOPIC", "dr154").strip("/") or "dr154"
MQTT_CONFIG_QOS = max(0, min(2, int(os.environ.get("MQTT_CONFIG_QOS", "1"))))
MQTT_COMMAND_QOS = max(0, min(2, int(os.environ.get("MQTT_COMMAND_QOS", "1"))))
LIGHT_COMMAND_ACTIONS = {"on", "off", "toggle"}
LIGHT_PAYLOAD_FORMATS = {
    "json",
    "frame_hex_space",
    "frame_hex_compact",
    "frame_hex_space_crlf",
    "frame_hex_compact_crlf",
    "frame_bytes",
}
LIGHT_RELAY_COMMANDS = {
    1: 0x51,
    2: 0x52,
    3: 0x53,
    4: 0x54,
    5: 0x65,
    6: 0x66,
    7: 0x67,
    8: 0x68,
}
LIGHT_ACTION_CODES = {"on": 0x41, "off": 0x53, "toggle": 0x55}
FRAME_START = 0x49
FRAME_END = 0x46
FRAME_LEN = 14

KIND_META = {
    "light": {"label": "Luci", "maxChannels": 8, "channelPrefix": "Luce"},
    "shutter": {"label": "Tapparelle", "maxChannels": 4, "channelPrefix": "Tapparella"},
    "dimmer": {"label": "Dimmer", "maxChannels": 1, "channelPrefix": "Dimmer"},
    "thermostat": {"label": "Termostati", "maxChannels": 8, "channelPrefix": "Termostato"},
}

STORE_LOCK = threading.Lock()
LIGHT_STATE_LOCK = threading.Lock()

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
    mqtt_in = payload.get("mqtt") if isinstance(payload.get("mqtt"), dict) else {}
    light_command_topic = clean_text(mqtt_in.get("lightCommandTopic"), f"{MQTT_BASE_TOPIC}/{instance_id}/cmd/light")
    light_payload_format = clean_text(mqtt_in.get("lightPayloadFormat"), "frame_hex_space_crlf").lower()
    if light_payload_format not in LIGHT_PAYLOAD_FORMATS:
        light_payload_format = "frame_hex_space_crlf"

    boards = [normalize_board(item, idx) for idx, item in enumerate(boards_input[:64])]
    if not boards:
        boards = [normalize_board({"id": "board-1", "name": "Scheda Luci", "kind": "light"}, 0)]

    return {
        "id": instance_id,
        "name": instance_name,
        "protocolVersion": "1.6",
        "boards": boards,
        "mqtt": {
            "lightCommandTopic": light_command_topic,
            "lightPayloadFormat": light_payload_format,
        },
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


def ensure_light_state_file() -> None:
    LIGHT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LIGHT_STATE_FILE.exists():
        LIGHT_STATE_FILE.write_text('{"instances": {}}\n', encoding="utf-8")


def load_light_state() -> dict[str, Any]:
    ensure_light_state_file()
    try:
        content = json.loads(LIGHT_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        content = {"instances": {}}
    instances = content.get("instances")
    if not isinstance(instances, dict):
        instances = {}
    return {"instances": instances}


def save_light_state(state: dict[str, Any]) -> None:
    ensure_light_state_file()
    tmp = LIGHT_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LIGHT_STATE_FILE)


def find_instance(store: dict[str, Any], instance_id: str) -> dict[str, Any] | None:
    for instance in store["instances"]:
        if instance.get("id") == instance_id:
            return instance
    return None


def mqtt_publish(topic: str, payload: Any, *, qos: int, retain: bool) -> None:
    if isinstance(payload, (bytes, bytearray)):
        raw_payload = bytes(payload)
    elif isinstance(payload, dict):
        raw_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    else:
        raw_payload = str(payload).encode("utf-8")
    client_id = f"iotsheltr-{os.getpid()}-{int(datetime.now().timestamp())}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        client.loop_start()
        result = client.publish(topic, raw_payload, qos=qos, retain=retain)
        if qos > 0:
            result.wait_for_publish(timeout=5)
            if not result.is_published():
                raise RuntimeError("Timeout pubblicazione MQTT")
        else:
            rc = getattr(result, "rc", None)
            if rc not in (0, None):
                raise RuntimeError(f"Publish MQTT fallita rc={rc}")
    finally:
        try:
            client.loop_stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def get_light_command_topic(instance: dict[str, Any]) -> str:
    instance_id = clean_text(instance.get("id"), "dr154-1")
    mqtt_cfg = instance.get("mqtt") if isinstance(instance.get("mqtt"), dict) else {}
    return clean_text(mqtt_cfg.get("lightCommandTopic"), f"{MQTT_BASE_TOPIC}/{instance_id}/cmd/light")


def get_light_payload_format(instance: dict[str, Any]) -> str:
    mqtt_cfg = instance.get("mqtt") if isinstance(instance.get("mqtt"), dict) else {}
    fmt = clean_text(mqtt_cfg.get("lightPayloadFormat"), "frame_hex_space_crlf").lower()
    return fmt if fmt in LIGHT_PAYLOAD_FORMATS else "frame_hex_space_crlf"


def build_protocol_frame(address: int, command: int, g_bytes: list[int]) -> bytes:
    packet = bytearray([0] * FRAME_LEN)
    packet[0] = FRAME_START
    packet[1] = clamp(to_int(address, 1), 0, 254)
    packet[2] = command & 0xFF
    for idx in range(10):
        packet[3 + idx] = (g_bytes[idx] if idx < len(g_bytes) else 0) & 0xFF
    packet[13] = FRAME_END
    return bytes(packet)


def frame_to_hex(frame: bytes, compact: bool = False) -> str:
    if compact:
        return "".join(f"{byte:02X}" for byte in frame)
    return " ".join(f"{byte:02X}" for byte in frame)


def light_payload_for_target(
    *,
    instance_id: str,
    target: dict[str, Any],
    action: str,
    payload_format: str,
) -> tuple[Any, str | None]:
    if payload_format == "json":
        return (
            {
                "type": "light_command",
                "instanceId": instance_id,
                "lightId": target["id"],
                "boardId": target["boardId"],
                "address": target["address"],
                "channel": target["channel"],
                "action": action,
                "sentAt": now_iso(),
            },
            None,
        )

    relay_command = LIGHT_RELAY_COMMANDS.get(clamp(to_int(target.get("channel"), 1), 1, 8))
    action_code = LIGHT_ACTION_CODES.get(action)
    if relay_command is None or action_code is None:
        raise ValueError("Canale o azione non validi per frame protocollo")

    frame = build_protocol_frame(clamp(to_int(target.get("address"), 1), 0, 254), relay_command, [action_code])
    frame_hex_spaced = frame_to_hex(frame, compact=False)

    if payload_format == "frame_bytes":
        return frame, frame_hex_spaced
    if payload_format == "frame_hex_compact":
        return frame_to_hex(frame, compact=True), frame_hex_spaced
    if payload_format == "frame_hex_space_crlf":
        return frame_hex_spaced + "\r\n", frame_hex_spaced
    if payload_format == "frame_hex_compact_crlf":
        return frame_to_hex(frame, compact=True) + "\r\n", frame_hex_spaced
    return frame_hex_spaced, frame_hex_spaced


def light_entities(instance: dict[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for board in instance.get("boards", []):
        if not isinstance(board, dict) or board.get("kind") != "light":
            continue
        board_id = clean_text(board.get("id"), "board-1")
        board_name = clean_text(board.get("name"), board_id)
        address = clamp(to_int(board.get("address"), 0), 0, 254)
        for channel_data in board.get("channels", []):
            if not isinstance(channel_data, dict):
                continue
            channel = clamp(to_int(channel_data.get("channel"), -1), 1, 8)
            light_id = f"{board_id}-c{channel}"
            entities.append(
                {
                    "id": light_id,
                    "boardId": board_id,
                    "boardName": board_name,
                    "address": address,
                    "channel": channel,
                    "name": clean_text(channel_data.get("name"), default_channel_name("light", channel)),
                    "room": clean_text(channel_data.get("room"), "Senza stanza"),
                }
            )
    entities.sort(key=lambda item: (str(item.get("room", "")).lower(), str(item.get("name", "")).lower()))
    return entities


def desired_light_state(action: str, previous: bool | None) -> bool | None:
    if action == "on":
        return True
    if action == "off":
        return False
    if action == "toggle":
        return not bool(previous)
    return previous


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
    return send_from_directory(app.static_folder, "control.html")


@app.get("/control")
def control_page():
    return send_from_directory(app.static_folder, "control.html")


@app.get("/config")
def config_page():
    return send_from_directory(app.static_folder, "config.html")


@app.get("/instance/<instance_id>")
def instance_page(instance_id: str):
    _ = instance_id
    return send_from_directory(app.static_folder, "control.html")


@app.get("/instance/<instance_id>/config")
def instance_config_page(instance_id: str):
    _ = instance_id
    return send_from_directory(app.static_folder, "config.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/api/meta")
def api_meta():
    return jsonify(
        {
            "kindMeta": KIND_META,
            "mqttBaseTopic": MQTT_BASE_TOPIC,
            "mqttConfigQos": MQTT_CONFIG_QOS,
            "mqttCommandQos": MQTT_COMMAND_QOS,
            "lightPayloadFormats": sorted(LIGHT_PAYLOAD_FORMATS),
        }
    )


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
        mqtt_publish(topic, instance, qos=MQTT_CONFIG_QOS, retain=True)
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore pubblicazione MQTT: {exc}", 502)

    return jsonify({"ok": True, "topic": topic, "qos": MQTT_CONFIG_QOS, "retain": True})


@app.get("/api/instances/<instance_id>/lights")
def api_list_lights(instance_id: str):
    normalized_id = slugify(instance_id, "dr154-1")
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, normalized_id)
    if instance is None:
        return err("Istanza non trovata", 404)

    lights = light_entities(instance)
    with LIGHT_STATE_LOCK:
        light_state = load_light_state()
    by_instance = light_state.get("instances", {}).get(normalized_id, {})
    if not isinstance(by_instance, dict):
        by_instance = {}

    for light in lights:
        stored = by_instance.get(light["id"])
        if isinstance(stored, dict):
            if "isOn" in stored:
                light["isOn"] = bool(stored.get("isOn"))
            if stored.get("updatedAt"):
                light["stateUpdatedAt"] = stored.get("updatedAt")
        else:
            light["isOn"] = None

    return jsonify(
        {
            "instanceId": normalized_id,
            "commandTopic": get_light_command_topic(instance),
            "payloadFormat": get_light_payload_format(instance),
            "lights": lights,
        }
    )


@app.post("/api/instances/<instance_id>/lights/command")
def api_light_command(instance_id: str):
    normalized_id = slugify(instance_id, "dr154-1")
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, normalized_id)
    if instance is None:
        return err("Istanza non trovata", 404)

    body = request.get_json(silent=True) or {}
    action = clean_text(body.get("action"), "").lower()
    if action not in LIGHT_COMMAND_ACTIONS:
        allowed = ",".join(sorted(LIGHT_COMMAND_ACTIONS))
        return err(f"Azione luce non valida. Valori ammessi: {allowed}", 400)

    topic = clean_text(body.get("topic"), get_light_command_topic(instance))
    if not topic:
        return err("Topic MQTT non valido", 400)
    payload_format = clean_text(body.get("payloadFormat"), get_light_payload_format(instance)).lower()
    if payload_format not in LIGHT_PAYLOAD_FORMATS:
        return err("Formato payload non valido", 400)

    entities = light_entities(instance)
    light_id = clean_text(body.get("lightId"), "")
    all_lights = bool(body.get("all"))
    targets: list[dict[str, Any]] = []
    if all_lights:
        if not entities:
            return err("Nessuna luce configurata", 400)
        targets = entities
    elif light_id:
        targets = [item for item in entities if item.get("id") == light_id]
        if not targets:
            target_in = body.get("target") if isinstance(body.get("target"), dict) else {}
            raw_channel = to_int(target_in.get("channel"), -1)
            if raw_channel < 1 or raw_channel > 8:
                return err("Luce non trovata", 404)
            board_id = clean_text(target_in.get("boardId"), light_id.split("-c", 1)[0] or "board-1")
            address = clamp(to_int(target_in.get("address"), 0), 0, 254)
            targets = [
                {
                    "id": light_id,
                    "boardId": board_id,
                    "boardName": clean_text(target_in.get("boardName"), board_id),
                    "address": address,
                    "channel": raw_channel,
                    "name": clean_text(target_in.get("name"), default_channel_name("light", raw_channel)),
                    "room": clean_text(target_in.get("room"), "Senza stanza"),
                }
            ]
    else:
        return err("Specifica 'lightId' oppure imposta 'all=true'", 400)

    with LIGHT_STATE_LOCK:
        light_state = load_light_state()
    instances_map = light_state.setdefault("instances", {})
    if not isinstance(instances_map, dict):
        instances_map = {}
        light_state["instances"] = instances_map
    instance_state = instances_map.setdefault(normalized_id, {})
    if not isinstance(instance_state, dict):
        instance_state = {}
        instances_map[normalized_id] = instance_state

    sent: list[dict[str, Any]] = []
    for entity in targets:
        try:
            payload, frame_hex = light_payload_for_target(
                instance_id=normalized_id,
                target=entity,
                action=action,
                payload_format=payload_format,
            )
        except ValueError as exc:
            return err(str(exc), 400)
        try:
            mqtt_publish(topic, payload, qos=MQTT_COMMAND_QOS, retain=False)
        except Exception as exc:  # noqa: BLE001
            return err(f"Errore pubblicazione comando luce: {exc}", 502)
        previous_state = instance_state.get(entity["id"]) if isinstance(instance_state.get(entity["id"]), dict) else {}
        prev_on = previous_state.get("isOn") if isinstance(previous_state, dict) else None
        next_on = desired_light_state(action, prev_on if isinstance(prev_on, bool) else None)
        state_updated_at = now_iso()
        instance_state[entity["id"]] = {
            "isOn": bool(next_on) if next_on is not None else None,
            "updatedAt": state_updated_at,
            "source": "command",
            "action": action,
        }
        item = {"id": entity["id"], "action": action}
        if frame_hex:
            item["frameHex"] = frame_hex
        if isinstance(payload, (bytes, bytearray)):
            item["payloadBytesHex"] = payload.hex().upper()
        elif isinstance(payload, str):
            item["payload"] = payload
        item["isOn"] = instance_state[entity["id"]]["isOn"]
        sent.append(item)

    with LIGHT_STATE_LOCK:
        save_light_state(light_state)

    return jsonify(
        {
            "ok": True,
            "topic": topic,
            "payloadFormat": payload_format,
            "qos": MQTT_COMMAND_QOS,
            "retain": False,
            "sent": sent,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
