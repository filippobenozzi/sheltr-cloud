#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory
from paho.mqtt import client as mqtt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

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
MQTT_PUBLISH_TIMEOUT_SEC = max(1, min(30, int(os.environ.get("MQTT_PUBLISH_TIMEOUT_SEC", "6"))))
MQTT_COMMAND_RETRIES = max(0, min(5, int(os.environ.get("MQTT_COMMAND_RETRIES", "2"))))
MQTT_COMMAND_RETRY_DELAY_MS = max(0, int(os.environ.get("MQTT_COMMAND_RETRY_DELAY_MS", "180")))
MQTT_COMMAND_REPEAT_ONOFF = max(1, min(5, int(os.environ.get("MQTT_COMMAND_REPEAT_ONOFF", "2"))))
MQTT_COMMAND_REPEAT_GAP_MS = max(0, int(os.environ.get("MQTT_COMMAND_REPEAT_GAP_MS", "120")))
MQTT_RESPONSE_TIMEOUT_MS = max(200, int(os.environ.get("MQTT_RESPONSE_TIMEOUT_MS", "2600")))
MQTT_RESPONSE_RETRIES = max(0, min(5, int(os.environ.get("MQTT_RESPONSE_RETRIES", "2"))))
MQTT_RESPONSE_RETRY_DELAY_MS = max(0, int(os.environ.get("MQTT_RESPONSE_RETRY_DELAY_MS", "220")))
MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS = max(0, int(os.environ.get("MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS", "320")))
THERMOSTAT_RESPONSE_TIMEOUT_MS = max(200, int(os.environ.get("THERMOSTAT_RESPONSE_TIMEOUT_MS", "4500")))
THERMOSTAT_RESPONSE_RETRIES = max(0, min(5, int(os.environ.get("THERMOSTAT_RESPONSE_RETRIES", "3"))))
THERMOSTAT_RESPONSE_RETRY_DELAY_MS = max(0, int(os.environ.get("THERMOSTAT_RESPONSE_RETRY_DELAY_MS", "400")))
THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS = max(0, int(os.environ.get("THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS", "700")))
THERMOSTAT_COMMAND_FRAME_GAP_MS = max(0, int(os.environ.get("THERMOSTAT_COMMAND_FRAME_GAP_MS", "220")))
MQTT_REQUIRE_RESPONSE = os.environ.get("MQTT_REQUIRE_RESPONSE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
INSTANCE_AUTH_TTL_SEC = max(300, int(os.environ.get("INSTANCE_AUTH_TTL_SEC", "43200")))
INSTANCE_AUTH_SECRET = (os.environ.get("INSTANCE_AUTH_SECRET") or "").strip() or f"{MQTT_USERNAME}:{MQTT_PASSWORD}:instance-auth"
CONFIG_AUTH_USERNAME = (os.environ.get("CONFIG_AUTH_USERNAME") or "").strip()
CONFIG_AUTH_PASSWORD = (os.environ.get("CONFIG_AUTH_PASSWORD") or "").strip()
CONFIG_AUTH_TTL_SEC = max(300, int(os.environ.get("CONFIG_AUTH_TTL_SEC", "43200")))
LIGHT_PROFILE_LOOP_INTERVAL_SEC = max(5, int(os.environ.get("LIGHT_PROFILE_LOOP_INTERVAL_SEC", "20")))
LIGHT_COMMAND_ACTIONS = {"on", "off"}
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
LIGHT_ACTION_CODES = {"on": 0x41, "off": 0x53}
SHUTTER_COMMAND = 0x5C
SHUTTER_ACTION_CODES = {"up": 0x55, "down": 0x44, "stop": 0x53}
DIMMER_COMMAND = 0x5B
DIMMER_SET_KEY = 0x53
DIMMER_ACTIONS = {"on", "off", "toggle", "set"}
DIMMER_MIN_LEVEL = 0
DIMMER_MAX_LEVEL = 9
THERMOSTAT_MODE_COMMAND = 0x6B
THERMOSTAT_SETPOINT_COMMAND = 0x5A
THERMOSTAT_MODE_CODES = {"winter": 0x00, "summer": 0x01}
THERMOSTAT_MODE_NAMES = {code: name for name, code in THERMOSTAT_MODE_CODES.items()}
FRAME_START = 0x49
FRAME_END = 0x46
FRAME_LEN = 14

KIND_META = {
    "light": {"label": "Luci", "maxChannels": 8, "channelPrefix": "Luce"},
    "shutter": {"label": "Tapparelle", "maxChannels": 4, "channelPrefix": "Tapparella"},
    "dimmer": {"label": "Dimmer", "maxChannels": 1, "channelPrefix": "Dimmer"},
    "thermostat": {"label": "Termostati", "maxChannels": 1, "channelPrefix": "Termostato"},
}
DEFAULT_DEVICE_TYPE = "sheltr_4g"
DEVICE_TYPE_META = {
    "sheltr_mini": {
        "label": "Sheltr Mini",
        "description": "Profilo Sheltr Cloud con payload MQTT JSON.",
        "module": "SHELTR_MINI",
        "transport": "mqtt_json",
        "defaultPayloadFormat": "json",
        "supportsFramePolling": False,
        "defaultBoard": {
            "id": "board-1",
            "name": "Sheltr Mini",
            "kind": "light",
            "address": 1,
            "channelStart": 1,
            "channelEnd": 8,
        },
    },
    "sheltr_4g": {
        "label": "Sheltr 4G / DR154",
        "description": "Modulo DR154 con la configurazione attuale a frame protocollo 1.6.",
        "module": "DR154",
        "transport": "dr154_protocol_v1_6",
        "defaultPayloadFormat": "frame_hex_space_crlf",
        "supportsFramePolling": True,
        "defaultBoard": {
            "id": "board-1",
            "name": "Scheda Luci",
            "kind": "light",
            "address": 1,
            "channelStart": 1,
            "channelEnd": 8,
        },
    },
}

STORE_LOCK = threading.Lock()
LIGHT_STATE_LOCK = threading.Lock()
PROFILE_LOCK = threading.Lock()
LIGHT_PROFILE_LAST_RUN: dict[str, str] = {}
PROFILE_LOOP_STARTED = False
AUTH_TOKEN_SERIALIZER = URLSafeTimedSerializer(INSTANCE_AUTH_SECRET, salt="iotsheltr-instance-auth-v1")
CONFIG_TOKEN_SERIALIZER = URLSafeTimedSerializer(INSTANCE_AUTH_SECRET, salt="iotsheltr-config-auth-v1")

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


def to_float(value: Any, fallback: float) -> float:
    try:
        if isinstance(value, bool):
            return fallback
        num = float(value)
        if num != num:  # NaN
            return fallback
        return num
    except (TypeError, ValueError):
        return fallback


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value not in {float("inf"), float("-inf")}


def parse_bool_text(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
    text = clean_text(value, "").lower()
    if text in {"1", "true", "yes", "on", "acceso", "attivo"}:
        return True
    if text in {"0", "false", "no", "off", "spento", "disattivo"}:
        return False
    return None


def clean_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def slugify(value: Any, fallback: str) -> str:
    raw = clean_text(value, fallback).lower().replace("_", "-")
    raw = re.sub(r"[^a-z0-9-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw or fallback


def control_instance_from_path(path: str) -> str:
    parts = [segment for segment in clean_text(path, "").split("/") if segment]
    if len(parts) >= 2 and parts[0] in {"control", "instance"}:
        return slugify(parts[1], "")
    return ""


def default_channel_name(kind: str, channel: int) -> str:
    prefix = KIND_META.get(kind, KIND_META["light"])["channelPrefix"]
    return f"{prefix} {channel}"


def normalize_device_type(value: Any, fallback: str = DEFAULT_DEVICE_TYPE) -> str:
    fallback_type = fallback if fallback in DEVICE_TYPE_META else DEFAULT_DEVICE_TYPE
    raw = clean_text(value, fallback_type).lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "mini": "sheltr_mini",
        "sheltrmini": "sheltr_mini",
        "sheltr_mini": "sheltr_mini",
        "4g": "sheltr_4g",
        "dr154": "sheltr_4g",
        "sheltr4g": "sheltr_4g",
        "sheltr_4g": "sheltr_4g",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in DEVICE_TYPE_META else fallback_type


def device_type_definition(device_type: Any) -> dict[str, Any]:
    return DEVICE_TYPE_META[normalize_device_type(device_type)]


def device_type_public(device_type: Any) -> dict[str, Any]:
    normalized = normalize_device_type(device_type)
    meta = device_type_definition(normalized)
    return {
        "type": normalized,
        "label": clean_text(meta.get("label"), normalized),
        "description": clean_text(meta.get("description"), ""),
        "module": clean_text(meta.get("module"), ""),
        "transport": clean_text(meta.get("transport"), ""),
        "defaultPayloadFormat": clean_text(meta.get("defaultPayloadFormat"), "frame_hex_space_crlf"),
        "supportsFramePolling": bool(meta.get("supportsFramePolling")),
    }


def device_types_public_meta() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(DEVICE_TYPE_META):
        item = device_type_public(key)
        meta = device_type_definition(key)
        default_board = meta.get("defaultBoard") if isinstance(meta.get("defaultBoard"), dict) else {}
        item["defaultBoard"] = {
            "id": clean_text(default_board.get("id"), "board-1"),
            "name": clean_text(default_board.get("name"), "Scheda"),
            "kind": clean_text(default_board.get("kind"), "light"),
            "address": clamp(to_int(default_board.get("address"), 1), 0, 254),
            "channelStart": clamp(to_int(default_board.get("channelStart"), 1), 1, 8),
            "channelEnd": clamp(to_int(default_board.get("channelEnd"), 8), 1, 8),
        }
        out[key] = item
    return out


def build_device_default_mqtt(device_type: Any, instance_id: str) -> dict[str, Any]:
    normalized = normalize_device_type(device_type)
    meta = device_type_definition(normalized)
    response_topic = "" if normalized == "sheltr_mini" else f"{MQTT_BASE_TOPIC}/{instance_id}/pub/light"
    return {
        "lightCommandTopic": f"{MQTT_BASE_TOPIC}/{instance_id}/cmd/light",
        "lightResponseTopic": response_topic,
        "lightPayloadFormat": clean_text(meta.get("defaultPayloadFormat"), "frame_hex_space_crlf"),
    }


def build_device_default_boards(device_type: Any) -> list[dict[str, Any]]:
    meta = device_type_definition(device_type)
    default_board = meta.get("defaultBoard") if isinstance(meta.get("defaultBoard"), dict) else {}
    return [normalize_board(default_board, 0)]


def split_temperature(value: float) -> tuple[int, int]:
    rounded = round(abs(value), 1)
    integer = int(rounded)
    decimal = int(round((rounded - integer) * 10))
    return clamp(integer, 0, 99), clamp(decimal, 0, 9)


def normalize_time_hhmm(value: Any, fallback: str = "00:00") -> str:
    text = clean_text(value, fallback)
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        return fallback
    hh = to_int(text[:2], -1)
    mm = to_int(text[3:], -1)
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return fallback
    return f"{hh:02d}:{mm:02d}"


def normalize_day(value: Any) -> int:
    num = to_int(value, 0)
    if 1 <= num <= 7:
        return num
    key = clean_text(value, "").lower()
    aliases = {
        "mon": 1,
        "monday": 1,
        "lun": 1,
        "lunedi": 1,
        "tue": 2,
        "tuesday": 2,
        "mar": 2,
        "martedi": 2,
        "wed": 3,
        "wednesday": 3,
        "mer": 3,
        "mercoledi": 3,
        "thu": 4,
        "thursday": 4,
        "gio": 4,
        "giovedi": 4,
        "fri": 5,
        "friday": 5,
        "ven": 5,
        "venerdi": 5,
        "sat": 6,
        "saturday": 6,
        "sab": 6,
        "sabato": 6,
        "sun": 7,
        "sunday": 7,
        "dom": 7,
        "domenica": 7,
    }
    return aliases.get(key, 0)


def normalize_days(value: Any) -> list[int]:
    out: set[int] = set()
    if isinstance(value, dict):
        for key, enabled in value.items():
            if not enabled:
                continue
            day = normalize_day(key)
            if day:
                out.add(day)
    elif isinstance(value, list):
        for item in value:
            day = normalize_day(item)
            if day:
                out.add(day)
    else:
        day = normalize_day(value)
        if day:
            out.add(day)
    if not out:
        return [1, 2, 3, 4, 5, 6, 7]
    return sorted(out)


def normalize_switch_profile(value: Any, kind: str = "light") -> dict[str, Any]:
    profile_in = value if isinstance(value, dict) else {}
    entries_in = profile_in.get("entries")
    entries_list = entries_in if isinstance(entries_in, list) else []
    default_action = "off" if kind != "shutter" else "down"
    entries: list[dict[str, Any]] = []
    for entry in entries_list[:64]:
        if not isinstance(entry, dict):
            continue
        action = clean_text(entry.get("action"), default_action).lower()
        if kind == "shutter":
            action = "up" if action == "up" else "down"
        else:
            action = "on" if action == "on" else "off"
        entries.append(
            {
                "time": normalize_time_hhmm(entry.get("time"), "00:00"),
                "action": action,
                "days": normalize_days(entry.get("days")),
            }
        )
    if not entries and isinstance(profile_in, dict) and any(k in profile_in for k in ("time", "action", "days")):
        one = {
            "time": normalize_time_hhmm(profile_in.get("time"), "00:00"),
            "action": clean_text(profile_in.get("action"), default_action).lower(),
            "days": normalize_days(profile_in.get("days")),
        }
        if kind == "shutter":
            one["action"] = "up" if one["action"] == "up" else "down"
        else:
            one["action"] = "on" if one["action"] == "on" else "off"
        entries = [one]
    return {"enabled": bool(profile_in.get("enabled")), "entries": entries}


def normalize_thermostat_profile(value: Any) -> dict[str, Any]:
    profile_in = value if isinstance(value, dict) else {}
    entries_in = profile_in.get("entries")
    entries_list = entries_in if isinstance(entries_in, list) else []
    entries: list[dict[str, Any]] = []
    for entry in entries_list[:64]:
        if not isinstance(entry, dict):
            continue
        setpoint_raw = entry.get("setpoint")
        try:
            setpoint = float(setpoint_raw)
        except (TypeError, ValueError):
            setpoint = 21.0
        setpoint = max(5.0, min(30.0, round(setpoint * 2) / 2))
        mode = clean_text(entry.get("mode"), "winter").lower()
        if mode not in {"winter", "summer"}:
            mode = "winter"
        entries.append(
            {
                "from": normalize_time_hhmm(entry.get("from"), "00:00"),
                "to": normalize_time_hhmm(entry.get("to"), "23:59"),
                "setpoint": setpoint,
                "mode": mode,
                "days": normalize_days(entry.get("days")),
            }
        )
    return {"enabled": bool(profile_in.get("enabled")), "entries": entries}


def profile_kind_for_board(kind: str) -> str | None:
    if kind in {"light", "shutter", "thermostat"}:
        return kind
    return None


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
        data = {
            "channel": channel,
            "name": clean_text(saved.get("name"), default_channel_name(kind, channel)),
            "room": clean_text(saved.get("room"), "Senza stanza"),
        }
        profile_kind = profile_kind_for_board(kind)
        if profile_kind == "thermostat":
            data["profile"] = normalize_thermostat_profile(saved.get("profile"))
        elif profile_kind in {"light", "shutter"}:
            data["profile"] = normalize_switch_profile(saved.get("profile"), profile_kind)
        channels.append(data)

    return {
        "id": board_id,
        "name": name,
        "address": address,
        "kind": kind,
        "channelStart": channel_start,
        "channelEnd": channel_end,
        "channels": channels,
    }


def normalize_instance(raw: Any, fallback_id: str, current_instance: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    current = current_instance if isinstance(current_instance, dict) else {}
    current_id = clean_text(current.get("id"), "dr154-1")
    instance_id = slugify(payload.get("id"), slugify(fallback_id, slugify(current_id, "dr154-1")))
    instance_name = clean_text(payload.get("name"), clean_text(current.get("name"), instance_id))
    device_type = normalize_device_type(payload.get("deviceType"), normalize_device_type(current.get("deviceType"), DEFAULT_DEVICE_TYPE))
    apply_device_defaults = bool(payload.get("applyDeviceDefaults"))
    boards_raw = payload.get("boards")
    boards_input = boards_raw if isinstance(boards_raw, list) else []
    mqtt_in = payload.get("mqtt") if isinstance(payload.get("mqtt"), dict) else {}
    mqtt_current = current.get("mqtt") if isinstance(current.get("mqtt"), dict) else {}
    mqtt_defaults = build_device_default_mqtt(device_type, instance_id)
    mqtt_fallback = mqtt_defaults if apply_device_defaults else mqtt_current
    light_command_topic = clean_text(
        mqtt_in.get("lightCommandTopic"),
        clean_text(mqtt_fallback.get("lightCommandTopic"), mqtt_defaults["lightCommandTopic"]),
    )
    response_raw = mqtt_in.get("lightResponseTopic")
    if isinstance(response_raw, str):
        light_response_topic = response_raw.strip()
    else:
        light_response_topic = clean_text(
            mqtt_fallback.get("lightResponseTopic"),
            clean_text(mqtt_defaults.get("lightResponseTopic"), ""),
        )
    light_payload_format = clean_text(
        mqtt_in.get("lightPayloadFormat"),
        clean_text(mqtt_fallback.get("lightPayloadFormat"), mqtt_defaults["lightPayloadFormat"]),
    ).lower()
    if light_payload_format not in LIGHT_PAYLOAD_FORMATS:
        light_payload_format = "frame_hex_space_crlf"

    auth_in = payload.get("auth") if isinstance(payload.get("auth"), dict) else {}
    auth_current = current.get("auth") if isinstance(current.get("auth"), dict) else {}
    auth_username = clean_text(auth_in.get("username"), clean_text(auth_current.get("username"), ""))
    auth_hash = clean_text(auth_current.get("passwordHash"), "")
    clear_password = bool(auth_in.get("clearPassword"))
    password_raw = auth_in.get("password")
    if clear_password:
        auth_hash = ""
    elif isinstance(password_raw, str):
        password_raw = password_raw.strip()
        if password_raw:
            auth_hash = generate_password_hash(password_raw)
    if not auth_username:
        auth_hash = ""

    boards = [normalize_board(item, idx) for idx, item in enumerate(boards_input[:64])]
    if not boards:
        boards = build_device_default_boards(device_type)

    return {
        "id": instance_id,
        "name": instance_name,
        "deviceType": device_type,
        "protocolVersion": "1.6",
        "boards": boards,
        "mqtt": {
            "lightCommandTopic": light_command_topic,
            "lightResponseTopic": light_response_topic,
            "lightPayloadFormat": light_payload_format,
        },
        "auth": {
            "username": auth_username,
            "passwordHash": auth_hash,
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
    target = slugify(instance_id, "dr154-1")
    for instance in store["instances"]:
        raw_id = clean_text(instance.get("id"), "")
        if not raw_id:
            continue
        if raw_id == instance_id or slugify(raw_id, raw_id) == target:
            return instance
    return None


def instance_auth_meta(instance: dict[str, Any]) -> dict[str, Any]:
    auth = instance.get("auth") if isinstance(instance.get("auth"), dict) else {}
    username = clean_text(auth.get("username"), "")
    password_hash = clean_text(auth.get("passwordHash"), "")
    return {
        "username": username,
        "passwordConfigured": bool(username and password_hash),
    }


def instance_has_auth(instance: dict[str, Any]) -> bool:
    meta = instance_auth_meta(instance)
    return bool(meta["passwordConfigured"])


def instance_control_url(instance_id: str) -> str:
    return f"/control/{slugify(instance_id, 'dr154-1')}"


def instance_device_type(instance: dict[str, Any]) -> str:
    return normalize_device_type(instance.get("deviceType"), DEFAULT_DEVICE_TYPE)


def instance_public(instance: dict[str, Any]) -> dict[str, Any]:
    device_type = instance_device_type(instance)
    return {
        "id": clean_text(instance.get("id"), "dr154-1"),
        "name": clean_text(instance.get("name"), "dr154-1"),
        "deviceType": device_type,
        "device": device_type_public(device_type),
        "protocolVersion": clean_text(instance.get("protocolVersion"), "1.6"),
        "boards": instance.get("boards", []),
        "mqtt": instance.get("mqtt", {}),
        "auth": instance_auth_meta(instance),
        "updatedAt": instance.get("updatedAt"),
        "controlUrl": instance_control_url(clean_text(instance.get("id"), "dr154-1")),
    }


def instance_associated_devices(instance: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for board in instance.get("boards", []):
        if not isinstance(board, dict):
            continue
        kind = clean_text(board.get("kind"), "light").lower()
        if kind not in KIND_META:
            kind = "light"
        board_id = clean_text(board.get("id"), "board-1")
        board_name = clean_text(board.get("name"), board_id)
        address = clamp(to_int(board.get("address"), 0), 0, 254)
        max_channels = KIND_META.get(kind, KIND_META["light"])["maxChannels"]
        for channel_data in board.get("channels", []):
            if not isinstance(channel_data, dict):
                continue
            channel = clamp(to_int(channel_data.get("channel"), 1), 1, max_channels)
            item = {
                "id": f"{board_id}-c{channel}",
                "kind": kind,
                "boardId": board_id,
                "boardName": board_name,
                "address": address,
                "channel": channel,
                "name": clean_text(channel_data.get("name"), default_channel_name(kind, channel)),
                "room": clean_text(channel_data.get("room"), "Senza stanza"),
            }
            profile_kind = profile_kind_for_board(kind)
            if profile_kind == "thermostat":
                item["profile"] = normalize_thermostat_profile(channel_data.get("profile"))
            elif profile_kind in {"light", "shutter"}:
                item["profile"] = normalize_switch_profile(channel_data.get("profile"), profile_kind)
            out.append(item)
    out.sort(key=lambda item: (str(item.get("boardId", "")).lower(), int(item.get("channel", 0))))
    return out


def instance_publish_payload(instance: dict[str, Any]) -> dict[str, Any]:
    device_type = instance_device_type(instance)
    return {
        "id": clean_text(instance.get("id"), "dr154-1"),
        "name": clean_text(instance.get("name"), "dr154-1"),
        "deviceType": device_type,
        "device": device_type_public(device_type),
        "protocolVersion": clean_text(instance.get("protocolVersion"), "1.6"),
        "boards": instance.get("boards", []),
        "devices": instance_associated_devices(instance),
        "mqtt": instance.get("mqtt", {}),
        "updatedAt": instance.get("updatedAt"),
    }


def config_auth_enabled() -> bool:
    return bool(CONFIG_AUTH_USERNAME and CONFIG_AUTH_PASSWORD)


def config_token_cookie_name() -> str:
    return "sheltr_config_token"


def issue_config_token() -> tuple[str, str]:
    now_ts = int(time.time())
    token = CONFIG_TOKEN_SERIALIZER.dumps({"scope": "config", "iat": now_ts})
    expires_ts = now_ts + CONFIG_AUTH_TTL_SEC
    return token, datetime.fromtimestamp(expires_ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def extract_config_token(body: dict[str, Any] | None = None) -> str:
    from_header = clean_text(request.headers.get("X-Config-Token"), "")
    if from_header:
        return from_header
    from_query = clean_text(request.args.get("configToken"), "")
    if from_query:
        return from_query
    if isinstance(body, dict):
        raw = body.get("configToken")
        if isinstance(raw, str):
            return raw.strip()
    from_cookie = clean_text(request.cookies.get(config_token_cookie_name()), "")
    if from_cookie:
        return from_cookie
    return ""


def require_config_auth(body: dict[str, Any] | None = None):
    if not config_auth_enabled():
        return None
    token = extract_config_token(body)
    if not token:
        return err("Login configurazione richiesto", 401)
    try:
        payload = CONFIG_TOKEN_SERIALIZER.loads(token, max_age=CONFIG_AUTH_TTL_SEC)
    except SignatureExpired:
        return err("Sessione configurazione scaduta", 401)
    except BadSignature:
        return err("Sessione configurazione non valida", 401)
    if not isinstance(payload, dict) or clean_text(payload.get("scope"), "") != "config":
        return err("Sessione configurazione non valida", 401)
    return None


def instance_token_cookie_name(instance_id: str) -> str:
    return f"sheltr_token_{slugify(instance_id, 'dr154-1').replace('-', '_')}"


def issue_instance_token(instance_id: str) -> tuple[str, str]:
    now_ts = int(time.time())
    token = AUTH_TOKEN_SERIALIZER.dumps({"instanceId": instance_id, "iat": now_ts})
    expires_ts = now_ts + INSTANCE_AUTH_TTL_SEC
    return token, datetime.fromtimestamp(expires_ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def extract_instance_token(body: dict[str, Any] | None = None, instance_id: str = "") -> str:
    from_header = clean_text(request.headers.get("X-Instance-Token"), "")
    if from_header:
        return from_header
    from_query = clean_text(request.args.get("token"), "")
    if from_query:
        return from_query
    if isinstance(body, dict):
        raw = body.get("token")
        if isinstance(raw, str):
            return raw.strip()
    if instance_id:
        from_cookie = clean_text(request.cookies.get(instance_token_cookie_name(instance_id)), "")
        if from_cookie:
            return from_cookie
    from_legacy_cookie = clean_text(request.cookies.get("instance_token"), "")
    if from_legacy_cookie:
        return from_legacy_cookie
    return ""


def require_instance_auth(instance: dict[str, Any], instance_id: str, body: dict[str, Any] | None = None):
    if not instance_has_auth(instance):
        return None

    token = extract_instance_token(body, instance_id=instance_id)
    if not token:
        return err("Autenticazione richiesta", 401)

    try:
        payload = AUTH_TOKEN_SERIALIZER.loads(token, max_age=INSTANCE_AUTH_TTL_SEC)
    except SignatureExpired:
        return err("Sessione scaduta", 401)
    except BadSignature:
        return err("Sessione non valida", 401)

    if not isinstance(payload, dict):
        return err("Sessione non valida", 401)
    if clean_text(payload.get("instanceId"), "") != instance_id:
        return err("Sessione non valida per questa istanza", 401)

    return None


def revoke_instance_token(token: str) -> None:
    _ = token


def migrate_instance_tokens(old_id: str, new_id: str) -> None:
    _ = (old_id, new_id)


def mqtt_payload_bytes(payload: Any) -> bytes:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return str(payload).encode("utf-8")


def mqtt_publish(
    topic: str,
    payload: Any,
    *,
    qos: int,
    retain: bool,
    retries: int = 0,
    retry_delay_ms: int = 0,
) -> None:
    raw_payload = mqtt_payload_bytes(payload)
    attempts = max(1, retries + 1)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        client_id = f"iotsheltr-{os.getpid()}-{int(time.time() * 1000)}-{attempt}"
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
                result.wait_for_publish(timeout=MQTT_PUBLISH_TIMEOUT_SEC)
                if not result.is_published():
                    raise RuntimeError("Timeout pubblicazione MQTT")
            else:
                rc = getattr(result, "rc", None)
                if rc not in (0, None):
                    raise RuntimeError(f"Publish MQTT fallita rc={rc}")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            if retry_delay_ms > 0:
                time.sleep(retry_delay_ms / 1000.0)
        finally:
            try:
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    raise RuntimeError(f"Publish MQTT fallita dopo {attempts} tentativi: {last_error}")


def get_light_command_topic(instance: dict[str, Any]) -> str:
    instance_id = clean_text(instance.get("id"), "dr154-1")
    mqtt_cfg = instance.get("mqtt") if isinstance(instance.get("mqtt"), dict) else {}
    return clean_text(mqtt_cfg.get("lightCommandTopic"), f"{MQTT_BASE_TOPIC}/{instance_id}/cmd/light")


def get_light_response_topic(instance: dict[str, Any]) -> str:
    instance_id = clean_text(instance.get("id"), "dr154-1")
    mqtt_cfg = instance.get("mqtt") if isinstance(instance.get("mqtt"), dict) else {}
    raw = mqtt_cfg.get("lightResponseTopic")
    if isinstance(raw, str):
        return raw.strip()
    return f"{MQTT_BASE_TOPIC}/{instance_id}/pub/light"


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


def parse_protocol_frame(frame: bytes) -> dict[str, Any]:
    return {
        "start": frame[0],
        "address": frame[1],
        "command": frame[2],
        "g": [frame[3 + idx] for idx in range(10)],
        "end": frame[13],
        "hex": frame_to_hex(frame),
    }


def extract_binary_protocol_frame(payload: bytes) -> bytes | None:
    if len(payload) < FRAME_LEN:
        return None
    for idx in range(0, len(payload) - FRAME_LEN + 1):
        if payload[idx] != FRAME_START:
            continue
        if payload[idx + FRAME_LEN - 1] == FRAME_END:
            return payload[idx : idx + FRAME_LEN]
    return None


def extract_hex_protocol_frame(payload: bytes) -> bytes | None:
    text = payload.decode("utf-8", errors="ignore")
    tokens = re.findall(r"[0-9A-Fa-f]{2}", text)
    if len(tokens) < FRAME_LEN:
        return None
    values = [int(token, 16) for token in tokens]
    for idx in range(0, len(values) - FRAME_LEN + 1):
        chunk = values[idx : idx + FRAME_LEN]
        if chunk[0] == FRAME_START and chunk[FRAME_LEN - 1] == FRAME_END:
            return bytes(chunk)
    return None


def parse_frame_from_mqtt_payload(payload: bytes) -> dict[str, Any] | None:
    frame = extract_binary_protocol_frame(payload)
    if frame is None:
        frame = extract_hex_protocol_frame(payload)
    if frame is None:
        return None
    return parse_protocol_frame(frame)


def frame_payload_for_format(frame: bytes, payload_format: str) -> Any:
    frame_hex_spaced = frame_to_hex(frame, compact=False)
    if payload_format == "frame_bytes":
        return frame
    if payload_format == "frame_hex_compact":
        return frame_to_hex(frame, compact=True)
    if payload_format == "frame_hex_space_crlf":
        return frame_hex_spaced + "\r\n"
    if payload_format == "frame_hex_compact_crlf":
        return frame_to_hex(frame, compact=True) + "\r\n"
    return frame_hex_spaced


def decode_polling_frame(frame: dict[str, Any]) -> dict[str, Any] | None:
    if to_int(frame.get("command"), -1) != 0x40:
        return None
    g_raw = frame.get("g")
    if not isinstance(g_raw, list) or len(g_raw) < 9:
        return None
    g = [clamp(to_int(g_raw[idx], 0), 0, 255) if idx < len(g_raw) else 0 for idx in range(10)]
    type_and_release = g[0]
    sign = -1 if g[6] == 0x2D else 1
    return {
        "boardType": type_and_release & 0x0F,
        "release": (type_and_release >> 4) & 0x0F,
        "outputMask": g[1],
        "inputMask": g[2],
        "dimmerLevel": clamp(g[3], DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL),
        "temperature": sign * (g[4] + g[5] / 10.0),
        "powerKw": g[7] / 10.0,
        "setpoint": g[8],
    }


def decode_poll_output_mask(frame: dict[str, Any]) -> int | None:
    poll = decode_polling_frame(frame)
    if not isinstance(poll, dict):
        return None
    return clamp(to_int(poll.get("outputMask"), 0), 0, 255)


def mqtt_publish_and_wait_frame(
    *,
    publish_topic: str,
    publish_payload: Any,
    response_topic: str,
    expected_address: int,
    expected_command: int,
    timeout_ms: int,
    qos: int,
) -> dict[str, Any] | None:
    if not response_topic:
        return None

    seen_frames: list[dict[str, Any]] = []
    matched: dict[str, Any] | None = None
    subscribed = threading.Event()
    result_ready = threading.Event()
    error_message: list[str] = []
    raw_payload = mqtt_payload_bytes(publish_payload)

    def on_subscribe(
        client: mqtt.Client,
        _userdata: Any,
        _mid: int,
        _granted_qos: list[int],
        _properties: Any = None,
    ) -> None:
        _ = client
        subscribed.set()

    def on_message(client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        _ = client
        nonlocal matched
        frame = parse_frame_from_mqtt_payload(bytes(msg.payload or b""))
        if frame is None:
            return
        seen_frames.append(frame)
        if (
            to_int(frame.get("address"), -1) == expected_address
            and to_int(frame.get("command"), -1) == expected_command
        ):
            matched = frame
            result_ready.set()

    client_id = f"iotsheltr-rx-{os.getpid()}-{int(time.time() * 1000)}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_message = on_message
    client.on_subscribe = on_subscribe

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        client.loop_start()
        sub_rc, _ = client.subscribe(response_topic, qos=min(1, max(0, qos)))
        if sub_rc not in (0, mqtt.MQTT_ERR_SUCCESS):
            raise RuntimeError(f"Subscribe MQTT fallita rc={sub_rc}")
        if not subscribed.wait(timeout=2):
            raise RuntimeError("Timeout subscribe MQTT su topic risposta")
        pub_info = client.publish(publish_topic, raw_payload, qos=qos, retain=False)
        if qos > 0:
            pub_info.wait_for_publish(timeout=MQTT_PUBLISH_TIMEOUT_SEC)
            if not pub_info.is_published():
                raise RuntimeError("Timeout publish MQTT richiesta stato")
        elif getattr(pub_info, "rc", None) not in (0, None):
            raise RuntimeError(f"Publish MQTT richiesta stato fallita rc={getattr(pub_info, 'rc', None)}")
        result_ready.wait(timeout=max(0.2, timeout_ms / 1000.0))
    except Exception as exc:  # noqa: BLE001
        error_message.append(str(exc))
    finally:
        try:
            client.loop_stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    if matched is not None:
        return {
            "matched": matched,
            "framesSeen": len(seen_frames),
        }
    if error_message:
        return {
            "matched": None,
            "framesSeen": len(seen_frames),
            "error": "; ".join(error_message),
        }
    return {
        "matched": None,
        "framesSeen": len(seen_frames),
        "error": "Timeout risposta dispositivo",
    }


def poll_light_state_via_mqtt(
    *,
    command_topic: str,
    response_topic: str,
    payload_format: str,
    address: int,
    channel: int,
) -> dict[str, Any]:
    if not response_topic:
        return {"verified": False, "reason": "response_topic_non_configurato"}
    if response_topic.strip() == command_topic.strip():
        return {"verified": False, "reason": "response_topic_uguale_al_topic_comandi"}
    if not payload_format.startswith("frame_"):
        return {"verified": False, "reason": "payload_non_frame"}

    poll_frame = build_protocol_frame(address, 0x40, [])
    poll_payload = frame_payload_for_format(poll_frame, payload_format)
    attempts = max(1, MQTT_RESPONSE_RETRIES + 1)

    for attempt in range(1, attempts + 1):
        outcome = mqtt_publish_and_wait_frame(
            publish_topic=command_topic,
            publish_payload=poll_payload,
            response_topic=response_topic,
            expected_address=address,
            expected_command=0x40,
            timeout_ms=MQTT_RESPONSE_TIMEOUT_MS,
            qos=MQTT_COMMAND_QOS,
        ) or {}
        matched = outcome.get("matched")
        if isinstance(matched, dict):
            output_mask = decode_poll_output_mask(matched)
            if output_mask is not None:
                bit = 1 << (clamp(channel, 1, 8) - 1)
                return {
                    "verified": True,
                    "isOn": bool(output_mask & bit),
                    "outputMask": output_mask,
                    "frameHex": matched.get("hex"),
                    "framesSeen": to_int(outcome.get("framesSeen"), 0),
                }

        if attempt < attempts and MQTT_RESPONSE_RETRY_DELAY_MS > 0:
            time.sleep(MQTT_RESPONSE_RETRY_DELAY_MS / 1000.0)

    reason = ""
    if isinstance(outcome, dict):
        reason = clean_text(outcome.get("error"), "")
    return {"verified": False, "reason": reason or "nessuna_risposta_poll"}


def poll_board_output_mask_via_mqtt(
    *,
    command_topic: str,
    response_topic: str,
    payload_format: str,
    address: int,
    timeout_ms: int | None = None,
    retries: int | None = None,
    retry_delay_ms: int | None = None,
) -> dict[str, Any]:
    if not response_topic:
        return {"ok": False, "error": "response_topic_non_configurato"}
    if response_topic.strip() == command_topic.strip():
        return {"ok": False, "error": "response_topic_uguale_al_topic_comandi"}
    if not payload_format.startswith("frame_"):
        return {"ok": False, "error": "payload_non_frame"}

    poll_frame = build_protocol_frame(address, 0x40, [])
    poll_payload = frame_payload_for_format(poll_frame, payload_format)
    effective_timeout_ms = MQTT_RESPONSE_TIMEOUT_MS if timeout_ms is None else max(200, int(timeout_ms))
    effective_retries = MQTT_RESPONSE_RETRIES if retries is None else max(0, min(5, int(retries)))
    effective_retry_delay_ms = MQTT_RESPONSE_RETRY_DELAY_MS if retry_delay_ms is None else max(0, int(retry_delay_ms))
    attempts = max(1, effective_retries + 1)
    outcome: dict[str, Any] = {}

    for attempt in range(1, attempts + 1):
        outcome = mqtt_publish_and_wait_frame(
            publish_topic=command_topic,
            publish_payload=poll_payload,
            response_topic=response_topic,
            expected_address=address,
            expected_command=0x40,
            timeout_ms=effective_timeout_ms,
            qos=MQTT_COMMAND_QOS,
        ) or {}
        matched = outcome.get("matched")
        if isinstance(matched, dict):
            poll = decode_polling_frame(matched)
            if isinstance(poll, dict):
                return {
                    "ok": True,
                    "address": address,
                    "poll": poll,
                    "outputMask": clamp(to_int(poll.get("outputMask"), 0), 0, 255),
                    "frameHex": matched.get("hex"),
                    "framesSeen": to_int(outcome.get("framesSeen"), 0),
                }
        if attempt < attempts and effective_retry_delay_ms > 0:
            time.sleep(effective_retry_delay_ms / 1000.0)

    reason = clean_text(outcome.get("error"), "nessuna_risposta_poll")
    return {"ok": False, "address": address, "error": reason}


def ensure_instance_state_shape(instance_state_any: Any) -> tuple[dict[str, Any], bool]:
    changed = False
    legacy_lights: dict[str, Any] = {}
    if isinstance(instance_state_any, dict):
        has_any_known = any(
            key in instance_state_any
            for key in ("lights", "dimmers", "shutters", "thermostats", "boards", "updatedAt")
        )
        if not has_any_known:
            for key, value in instance_state_any.items():
                if isinstance(value, dict):
                    legacy_lights[key] = value
            changed = bool(legacy_lights) or bool(instance_state_any)
            instance_state: dict[str, Any] = {
                "lights": legacy_lights,
                "dimmers": {},
                "shutters": {},
                "thermostats": {},
                "boards": {},
            }
            return instance_state, changed
        instance_state = dict(instance_state_any)
    else:
        changed = True
        instance_state = {}

    def ensure_dict(key: str) -> None:
        nonlocal changed
        value = instance_state.get(key)
        if isinstance(value, dict):
            return
        instance_state[key] = {}
        changed = True

    ensure_dict("lights")
    ensure_dict("dimmers")
    ensure_dict("shutters")
    ensure_dict("thermostats")
    ensure_dict("boards")
    return instance_state, changed


def _load_instance_state(instance_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    with LIGHT_STATE_LOCK:
        light_state = load_light_state()
        instances_map = light_state.setdefault("instances", {})
        if not isinstance(instances_map, dict):
            instances_map = {}
            light_state["instances"] = instances_map
        instance_state_raw = instances_map.get(instance_id)
        instance_state, changed = ensure_instance_state_shape(instance_state_raw)
        if changed or instance_state_raw is None:
            instances_map[instance_id] = instance_state
            save_light_state(light_state)
    return light_state, instances_map, instance_state


def _save_instance_state(light_state: dict[str, Any]) -> None:
    with LIGHT_STATE_LOCK:
        save_light_state(light_state)


def execute_light_targets(
    *,
    instance_id: str,
    instance: dict[str, Any],
    targets: list[dict[str, Any]],
    action: str,
    topic: str | None = None,
    response_topic: str | None = None,
    payload_format: str | None = None,
    require_response: bool | None = None,
) -> dict[str, Any]:
    if action not in LIGHT_COMMAND_ACTIONS:
        allowed = ",".join(sorted(LIGHT_COMMAND_ACTIONS))
        raise ValueError(f"Azione luce non valida. Valori ammessi: {allowed}")

    effective_topic = clean_text(topic, get_light_command_topic(instance))
    if not effective_topic:
        raise ValueError("Topic MQTT non valido")
    effective_response_topic = response_topic.strip() if isinstance(response_topic, str) else get_light_response_topic(instance)
    effective_payload_format = clean_text(payload_format, get_light_payload_format(instance)).lower()
    if effective_payload_format not in LIGHT_PAYLOAD_FORMATS:
        raise ValueError("Formato payload non valido")
    must_verify = MQTT_REQUIRE_RESPONSE if require_response is None else bool(require_response)

    light_state, _, instance_state = _load_instance_state(instance_id)
    lights_state = instance_state["lights"] if isinstance(instance_state.get("lights"), dict) else {}
    instance_state["lights"] = lights_state
    sent: list[dict[str, Any]] = []

    for entity in targets:
        payload, frame_hex = light_payload_for_target(
            instance_id=instance_id,
            target=entity,
            action=action,
            payload_format=effective_payload_format,
        )
        send_count = 1
        if effective_payload_format.startswith("frame_") and action in {"on", "off"}:
            send_count = MQTT_COMMAND_REPEAT_ONOFF
        for idx in range(send_count):
            mqtt_publish(
                effective_topic,
                payload,
                qos=MQTT_COMMAND_QOS,
                retain=False,
                retries=MQTT_COMMAND_RETRIES,
                retry_delay_ms=MQTT_COMMAND_RETRY_DELAY_MS,
            )
            if idx < (send_count - 1) and MQTT_COMMAND_REPEAT_GAP_MS > 0:
                time.sleep(MQTT_COMMAND_REPEAT_GAP_MS / 1000.0)

        previous_state = lights_state.get(entity["id"]) if isinstance(lights_state.get(entity["id"]), dict) else {}
        prev_on = previous_state.get("isOn") if isinstance(previous_state, dict) else None
        next_on = desired_light_state(action, prev_on if isinstance(prev_on, bool) else None)
        verification: dict[str, Any] = {"verified": True}
        if must_verify:
            if MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS > 0:
                time.sleep(MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS / 1000.0)
            verification = poll_light_state_via_mqtt(
                command_topic=effective_topic,
                response_topic=effective_response_topic,
                payload_format=effective_payload_format,
                address=clamp(to_int(entity.get("address"), 0), 0, 254),
                channel=clamp(to_int(entity.get("channel"), 1), 1, 8),
            )
            if isinstance(verification.get("isOn"), bool):
                next_on = bool(verification["isOn"])
        if must_verify and not bool(verification.get("verified")):
            reason = clean_text(verification.get("reason"), "nessuna risposta")
            raise RuntimeError(f"Nessuna conferma dal dispositivo per {entity['id']}: {reason}")

        state_updated_at = now_iso()
        lights_state[entity["id"]] = {
            "isOn": bool(next_on) if next_on is not None else None,
            "updatedAt": state_updated_at,
            "source": "poll" if must_verify and bool(verification.get("verified")) else "command",
            "action": action,
        }
        item = {"id": entity["id"], "action": action}
        if frame_hex:
            item["frameHex"] = frame_hex
        if isinstance(payload, (bytes, bytearray)):
            item["payloadBytesHex"] = payload.hex().upper()
        elif isinstance(payload, str):
            item["payload"] = payload
        item["sendCount"] = send_count
        item["publishRetries"] = MQTT_COMMAND_RETRIES
        item["verified"] = bool(verification.get("verified"))
        if verification.get("frameHex"):
            item["verifyFrameHex"] = verification.get("frameHex")
        if verification.get("outputMask") is not None:
            item["verifyOutputMask"] = verification.get("outputMask")
        if verification.get("reason"):
            item["verifyReason"] = verification.get("reason")
        item["isOn"] = lights_state[entity["id"]]["isOn"]
        sent.append(item)

    _save_instance_state(light_state)
    return {
        "topic": effective_topic,
        "responseTopic": effective_response_topic,
        "payloadFormat": effective_payload_format,
        "sent": sent,
    }


def hhmm_to_minute(value: str) -> int:
    text = normalize_time_hhmm(value, "00:00")
    return to_int(text[:2], 0) * 60 + to_int(text[3:], 0)


def apply_light_profiles_once() -> None:
    now_local = time.localtime()
    weekday = now_local.tm_wday + 1
    now_minute = now_local.tm_hour * 60 + now_local.tm_min
    minute_stamp = f"{now_local.tm_year}-{now_local.tm_yday}-{now_minute}"

    with STORE_LOCK:
        store = load_store()
    valid_keys: set[str] = set()

    for instance in store.get("instances", []):
        if not isinstance(instance, dict):
            continue
        instance_id = clean_text(instance.get("id"), "")
        if not instance_id:
            continue

        light_map = {item["id"]: item for item in light_entities(instance)}
        shutter_map = {item["id"]: item for item in shutter_entities(instance)}
        thermostat_map = {item["id"]: item for item in thermostat_entities(instance)}

        _, _, instance_state = _load_instance_state(instance_id)
        thermostats_state = get_state_map(instance_state, "thermostats")

        for board in instance.get("boards", []):
            if not isinstance(board, dict):
                continue
            kind = clean_text(board.get("kind"), "").lower()
            if kind not in {"light", "shutter", "thermostat"}:
                continue
            board_id = clean_text(board.get("id"), "")
            if not board_id:
                continue
            max_channels = KIND_META.get(kind, KIND_META["light"])["maxChannels"]
            for channel_data in board.get("channels", []):
                if not isinstance(channel_data, dict):
                    continue
                channel = clamp(to_int(channel_data.get("channel"), -1), 1, max_channels)
                if channel < 1:
                    continue
                entity_id = f"{board_id}-c{channel}"

                if kind in {"light", "shutter"}:
                    profile = normalize_switch_profile(channel_data.get("profile"), kind)
                    if not profile.get("enabled"):
                        continue
                    entries = profile.get("entries", [])
                    if not isinstance(entries, list):
                        continue
                    for idx, entry in enumerate(entries):
                        if not isinstance(entry, dict):
                            continue
                        cache_key = f"{kind}:{instance_id}:{entity_id}:{idx}"
                        valid_keys.add(cache_key)
                        if weekday not in normalize_days(entry.get("days")):
                            continue
                        if now_minute != hhmm_to_minute(clean_text(entry.get("time"), "00:00")):
                            continue
                        with PROFILE_LOCK:
                            if LIGHT_PROFILE_LAST_RUN.get(cache_key) == minute_stamp:
                                continue
                        action = clean_text(entry.get("action"), "off").lower()
                        try:
                            if kind == "light":
                                if action not in LIGHT_COMMAND_ACTIONS:
                                    continue
                                target = light_map.get(entity_id)
                                if not isinstance(target, dict):
                                    continue
                                execute_light_targets(
                                    instance_id=instance_id,
                                    instance=instance,
                                    targets=[target],
                                    action=action,
                                    require_response=False,
                                )
                            else:
                                if action not in {"up", "down"}:
                                    action = "down"
                                target = shutter_map.get(entity_id)
                                if not isinstance(target, dict):
                                    continue
                                execute_shutter_targets(
                                    instance_id=instance_id,
                                    instance=instance,
                                    targets=[target],
                                    action=action,
                                    require_response=False,
                                )
                            with PROFILE_LOCK:
                                LIGHT_PROFILE_LAST_RUN[cache_key] = minute_stamp
                        except Exception as exc:  # noqa: BLE001
                            print(f"[warn] profilo {kind} {instance_id}:{entity_id} fallito: {exc}")
                else:
                    profile = normalize_thermostat_profile(channel_data.get("profile"))
                    if not profile.get("enabled"):
                        continue
                    target = thermostat_map.get(entity_id)
                    if not isinstance(target, dict):
                        continue
                    target_setpoint, target_mode = thermostat_profile_target(profile, now_minute, weekday)
                    previous = thermostats_state.get(entity_id) if isinstance(thermostats_state.get(entity_id), dict) else {}
                    prev_setpoint = previous.get("setpoint")
                    prev_mode = clean_text(previous.get("mode"), "").lower()
                    needs_setpoint = (
                        not isinstance(prev_setpoint, (int, float))
                        or abs(float(prev_setpoint) - target_setpoint) > 0.24
                    )
                    needs_mode = prev_mode not in {"winter", "summer"} or prev_mode != target_mode
                    if not needs_setpoint and not needs_mode:
                        continue
                    try:
                        execute_thermostat_targets(
                            instance_id=instance_id,
                            instance=instance,
                            targets=[target],
                            setpoint=target_setpoint,
                            mode=target_mode,
                            require_response=False,
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[warn] profilo termostato {instance_id}:{entity_id} fallito: {exc}")

    with PROFILE_LOCK:
        stale = [key for key in LIGHT_PROFILE_LAST_RUN if key not in valid_keys]
        for key in stale:
            LIGHT_PROFILE_LAST_RUN.pop(key, None)


def light_profile_loop() -> None:
    while True:
        try:
            apply_light_profiles_once()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] loop profili luce: {exc}")
        time.sleep(LIGHT_PROFILE_LOOP_INTERVAL_SEC)


def ensure_profile_loop() -> None:
    global PROFILE_LOOP_STARTED
    with PROFILE_LOCK:
        if PROFILE_LOOP_STARTED:
            return
        PROFILE_LOOP_STARTED = True
    thread = threading.Thread(target=light_profile_loop, daemon=True, name="light-profile-loop")
    thread.start()


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


def entities_by_kind(instance: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    max_channels = KIND_META.get(kind, KIND_META["light"])["maxChannels"]
    entities: list[dict[str, Any]] = []
    for board in instance.get("boards", []):
        if not isinstance(board, dict) or board.get("kind") != kind:
            continue
        board_id = clean_text(board.get("id"), "board-1")
        board_name = clean_text(board.get("name"), board_id)
        address = clamp(to_int(board.get("address"), 0), 0, 254)
        for channel_data in board.get("channels", []):
            if not isinstance(channel_data, dict):
                continue
            channel = clamp(to_int(channel_data.get("channel"), -1), 1, max_channels)
            entity_id = f"{board_id}-c{channel}"
            entities.append(
                {
                    "id": entity_id,
                    "boardId": board_id,
                    "boardName": board_name,
                    "address": address,
                    "channel": channel,
                    "kind": kind,
                    "name": clean_text(channel_data.get("name"), default_channel_name(kind, channel)),
                    "room": clean_text(channel_data.get("room"), "Senza stanza"),
                }
            )
    entities.sort(key=lambda item: (str(item.get("room", "")).lower(), str(item.get("name", "")).lower()))
    return entities


def light_entities(instance: dict[str, Any]) -> list[dict[str, Any]]:
    return entities_by_kind(instance, "light")


def shutter_entities(instance: dict[str, Any]) -> list[dict[str, Any]]:
    return entities_by_kind(instance, "shutter")


def dimmer_entities(instance: dict[str, Any]) -> list[dict[str, Any]]:
    return entities_by_kind(instance, "dimmer")


def thermostat_entities(instance: dict[str, Any]) -> list[dict[str, Any]]:
    return entities_by_kind(instance, "thermostat")


def desired_light_state(action: str, previous: bool | None) -> bool | None:
    if action == "on":
        return True
    if action == "off":
        return False
    return previous


def payload_from_frame(
    *,
    frame: bytes,
    payload_format: str,
    json_payload: dict[str, Any],
) -> tuple[Any, str | None]:
    if payload_format == "json":
        return json_payload, None
    frame_hex = frame_to_hex(frame, compact=False)
    return frame_payload_for_format(frame, payload_format), frame_hex


def add_payload_debug(item: dict[str, Any], payload: Any) -> None:
    if isinstance(payload, (bytes, bytearray)):
        item["payloadBytesHex"] = bytes(payload).hex().upper()
    elif isinstance(payload, str):
        item["payload"] = payload
    elif isinstance(payload, dict):
        item["payload"] = payload


def get_state_map(instance_state: dict[str, Any], key: str) -> dict[str, Any]:
    current = instance_state.get(key)
    if isinstance(current, dict):
        return current
    instance_state[key] = {}
    return instance_state[key]


def update_board_poll_state(instance_state: dict[str, Any], address: int, poll_outcome: dict[str, Any]) -> None:
    if not bool(poll_outcome.get("ok")):
        return
    poll = poll_outcome.get("poll")
    if not isinstance(poll, dict):
        return
    boards_state = get_state_map(instance_state, "boards")
    boards_state[str(address)] = {
        "address": address,
        "poll": poll,
        "frameHex": poll_outcome.get("frameHex"),
        "updatedAt": now_iso(),
    }


def resolve_dimmer_level(action: str, requested_level: int | None, previous_level: int, last_on_level: int) -> int:
    if action == "set":
        if requested_level is None:
            raise ValueError("Per action='set' devi specificare 'level' (0..9)")
        return clamp(requested_level, DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL)
    if action == "off":
        return DIMMER_MIN_LEVEL
    if action == "on":
        return previous_level if previous_level > 0 else last_on_level
    if action == "toggle":
        return DIMMER_MIN_LEVEL if previous_level > 0 else last_on_level
    raise ValueError("Azione dimmer non valida")


def execute_dimmer_targets(
    *,
    instance_id: str,
    instance: dict[str, Any],
    targets: list[dict[str, Any]],
    action: str,
    level: int | None = None,
    topic: str | None = None,
    response_topic: str | None = None,
    payload_format: str | None = None,
    require_response: bool | None = None,
) -> dict[str, Any]:
    if action not in DIMMER_ACTIONS:
        allowed = ",".join(sorted(DIMMER_ACTIONS))
        raise ValueError(f"Azione dimmer non valida. Valori ammessi: {allowed}")

    effective_topic = clean_text(topic, get_light_command_topic(instance))
    if not effective_topic:
        raise ValueError("Topic MQTT non valido")
    effective_response_topic = response_topic.strip() if isinstance(response_topic, str) else get_light_response_topic(instance)
    effective_payload_format = clean_text(payload_format, get_light_payload_format(instance)).lower()
    if effective_payload_format not in LIGHT_PAYLOAD_FORMATS:
        raise ValueError("Formato payload non valido")
    must_verify = MQTT_REQUIRE_RESPONSE if require_response is None else bool(require_response)

    light_state, _, instance_state = _load_instance_state(instance_id)
    dimmers_state = get_state_map(instance_state, "dimmers")
    sent: list[dict[str, Any]] = []

    requested_level = clamp(to_int(level, 0), DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL) if isinstance(level, int) else None

    for entity in targets:
        previous = dimmers_state.get(entity["id"]) if isinstance(dimmers_state.get(entity["id"]), dict) else {}
        prev_level = clamp(to_int(previous.get("level"), 0), DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL)
        last_on_level = clamp(to_int(previous.get("lastOnLevel"), DIMMER_MAX_LEVEL), 1, DIMMER_MAX_LEVEL)
        target_level = resolve_dimmer_level(action, requested_level, prev_level, last_on_level)

        frame = build_protocol_frame(
            clamp(to_int(entity.get("address"), 1), 0, 254),
            DIMMER_COMMAND,
            [DIMMER_SET_KEY, target_level],
        )
        payload, frame_hex = payload_from_frame(
            frame=frame,
            payload_format=effective_payload_format,
            json_payload={
                "type": "dimmer_command",
                "instanceId": instance_id,
                "dimmerId": entity["id"],
                "boardId": entity["boardId"],
                "address": entity["address"],
                "channel": entity["channel"],
                "action": action,
                "level": target_level,
                "sentAt": now_iso(),
            },
        )
        mqtt_publish(
            effective_topic,
            payload,
            qos=MQTT_COMMAND_QOS,
            retain=False,
            retries=MQTT_COMMAND_RETRIES,
            retry_delay_ms=MQTT_COMMAND_RETRY_DELAY_MS,
        )

        verification: dict[str, Any] = {"ok": True}
        if must_verify:
            if MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS > 0:
                time.sleep(MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS / 1000.0)
            verification = poll_board_output_mask_via_mqtt(
                command_topic=effective_topic,
                response_topic=effective_response_topic,
                payload_format=effective_payload_format,
                address=clamp(to_int(entity.get("address"), 0), 0, 254),
            )
        verified = bool(verification.get("ok"))
        if must_verify and not verified:
            reason = clean_text(verification.get("error"), "nessuna risposta")
            raise RuntimeError(f"Nessuna conferma dal dispositivo per {entity['id']}: {reason}")
        if must_verify:
            update_board_poll_state(instance_state, clamp(to_int(entity.get("address"), 0), 0, 254), verification)
        poll_data = verification.get("poll") if must_verify and isinstance(verification.get("poll"), dict) else {}
        final_level = target_level
        if isinstance(poll_data, dict):
            final_level = clamp(to_int(poll_data.get("dimmerLevel"), target_level), DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL)
        final_is_on = final_level > 0
        final_last_on = final_level if final_level > 0 else last_on_level
        dimmers_state[entity["id"]] = {
            "level": final_level,
            "isOn": final_is_on,
            "lastOnLevel": final_last_on,
            "updatedAt": now_iso(),
            "action": action,
        }

        item = {
            "id": entity["id"],
            "action": action,
            "requestedLevel": target_level,
            "level": final_level,
            "isOn": final_is_on,
            "verified": verified,
            "publishRetries": MQTT_COMMAND_RETRIES,
        }
        if frame_hex:
            item["frameHex"] = frame_hex
        if verification.get("frameHex"):
            item["verifyFrameHex"] = verification.get("frameHex")
        if verification.get("outputMask") is not None:
            item["verifyOutputMask"] = verification.get("outputMask")
        if verification.get("error"):
            item["verifyReason"] = verification.get("error")
        add_payload_debug(item, payload)
        sent.append(item)

    _save_instance_state(light_state)
    return {
        "topic": effective_topic,
        "responseTopic": effective_response_topic,
        "payloadFormat": effective_payload_format,
        "sent": sent,
    }


def execute_shutter_targets(
    *,
    instance_id: str,
    instance: dict[str, Any],
    targets: list[dict[str, Any]],
    action: str,
    topic: str | None = None,
    response_topic: str | None = None,
    payload_format: str | None = None,
    require_response: bool | None = None,
) -> dict[str, Any]:
    action_code = SHUTTER_ACTION_CODES.get(action)
    if action_code is None:
        allowed = ",".join(sorted(SHUTTER_ACTION_CODES))
        raise ValueError(f"Azione tapparella non valida. Valori ammessi: {allowed}")

    effective_topic = clean_text(topic, get_light_command_topic(instance))
    if not effective_topic:
        raise ValueError("Topic MQTT non valido")
    effective_response_topic = response_topic.strip() if isinstance(response_topic, str) else get_light_response_topic(instance)
    effective_payload_format = clean_text(payload_format, get_light_payload_format(instance)).lower()
    if effective_payload_format not in LIGHT_PAYLOAD_FORMATS:
        raise ValueError("Formato payload non valido")
    must_verify = MQTT_REQUIRE_RESPONSE if require_response is None else bool(require_response)

    light_state, _, instance_state = _load_instance_state(instance_id)
    shutters_state = get_state_map(instance_state, "shutters")
    sent: list[dict[str, Any]] = []

    for entity in targets:
        frame = build_protocol_frame(
            clamp(to_int(entity.get("address"), 1), 0, 254),
            SHUTTER_COMMAND,
            [clamp(to_int(entity.get("channel"), 1), 1, 4), action_code],
        )
        payload, frame_hex = payload_from_frame(
            frame=frame,
            payload_format=effective_payload_format,
            json_payload={
                "type": "shutter_command",
                "instanceId": instance_id,
                "shutterId": entity["id"],
                "boardId": entity["boardId"],
                "address": entity["address"],
                "channel": entity["channel"],
                "action": action,
                "sentAt": now_iso(),
            },
        )
        mqtt_publish(
            effective_topic,
            payload,
            qos=MQTT_COMMAND_QOS,
            retain=False,
            retries=MQTT_COMMAND_RETRIES,
            retry_delay_ms=MQTT_COMMAND_RETRY_DELAY_MS,
        )

        verification: dict[str, Any] = {"ok": True}
        if must_verify:
            if MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS > 0:
                time.sleep(MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS / 1000.0)
            verification = poll_board_output_mask_via_mqtt(
                command_topic=effective_topic,
                response_topic=effective_response_topic,
                payload_format=effective_payload_format,
                address=clamp(to_int(entity.get("address"), 0), 0, 254),
            )
        verified = bool(verification.get("ok"))
        if must_verify and not verified:
            reason = clean_text(verification.get("error"), "nessuna risposta")
            raise RuntimeError(f"Nessuna conferma dal dispositivo per {entity['id']}: {reason}")
        if must_verify:
            update_board_poll_state(instance_state, clamp(to_int(entity.get("address"), 0), 0, 254), verification)

        output_mask = clamp(to_int(verification.get("outputMask"), 0), 0, 255)
        bit = 1 << (clamp(to_int(entity.get("channel"), 1), 1, 8) - 1)
        shutters_state[entity["id"]] = {
            "action": action,
            "isActive": bool(output_mask & bit) if verified else None,
            "updatedAt": now_iso(),
        }

        item = {
            "id": entity["id"],
            "action": action,
            "verified": verified,
            "publishRetries": MQTT_COMMAND_RETRIES,
        }
        if frame_hex:
            item["frameHex"] = frame_hex
        if verification.get("frameHex"):
            item["verifyFrameHex"] = verification.get("frameHex")
        if verification.get("outputMask") is not None:
            item["verifyOutputMask"] = verification.get("outputMask")
        if verification.get("error"):
            item["verifyReason"] = verification.get("error")
        add_payload_debug(item, payload)
        sent.append(item)

    _save_instance_state(light_state)
    return {
        "topic": effective_topic,
        "responseTopic": effective_response_topic,
        "payloadFormat": effective_payload_format,
        "sent": sent,
    }


def normalize_thermostat_mode(value: Any) -> str:
    mode_raw = clean_text(value, "winter").lower()
    if mode_raw in {"summer", "estate", "cool"}:
        return "summer"
    return "winter"


def normalize_thermostat_setpoint(value: Any, fallback: float = 21.0) -> float:
    raw = to_float(value, fallback)
    if not is_finite_number(raw):
        raw = fallback
    return max(5.0, min(30.0, round(raw * 2.0) / 2.0))


def thermostat_profile_target(profile: dict[str, Any], now_minute: int, now_weekday: int) -> tuple[float, str]:
    prev_weekday = 7 if now_weekday <= 1 else now_weekday - 1
    for entry in profile.get("entries", []):
        if not isinstance(entry, dict):
            continue
        days = normalize_days(entry.get("days"))
        start_at = hhmm_to_minute(normalize_time_hhmm(entry.get("from"), "00:00"))
        end_at = hhmm_to_minute(normalize_time_hhmm(entry.get("to"), "23:59"))
        if start_at == end_at:
            matches = now_weekday in days
        elif start_at < end_at:
            matches = now_weekday in days and start_at <= now_minute < end_at
        else:
            matches = now_weekday in days if now_minute >= start_at else prev_weekday in days
        if not matches:
            continue
        return normalize_thermostat_setpoint(entry.get("setpoint"), 21.0), normalize_thermostat_mode(entry.get("mode"))
    return 5.0, "winter"


def execute_thermostat_targets(
    *,
    instance_id: str,
    instance: dict[str, Any],
    targets: list[dict[str, Any]],
    setpoint: float | None = None,
    mode: str | None = None,
    power: bool | None = None,
    topic: str | None = None,
    response_topic: str | None = None,
    payload_format: str | None = None,
    require_response: bool | None = None,
) -> dict[str, Any]:
    requested_setpoint = normalize_thermostat_setpoint(setpoint, 21.0) if isinstance(setpoint, (int, float)) else None
    requested_mode = normalize_thermostat_mode(mode) if isinstance(mode, str) and mode.strip() else None
    requested_power = power if isinstance(power, bool) else None
    if requested_setpoint is None and requested_mode is None and requested_power is None:
        raise ValueError("Specifica almeno uno tra setpoint, mode, power")

    effective_topic = clean_text(topic, get_light_command_topic(instance))
    if not effective_topic:
        raise ValueError("Topic MQTT non valido")
    effective_response_topic = response_topic.strip() if isinstance(response_topic, str) else get_light_response_topic(instance)
    effective_payload_format = clean_text(payload_format, get_light_payload_format(instance)).lower()
    if effective_payload_format not in LIGHT_PAYLOAD_FORMATS:
        raise ValueError("Formato payload non valido")
    must_verify = MQTT_REQUIRE_RESPONSE if require_response is None else bool(require_response)

    light_state, _, instance_state = _load_instance_state(instance_id)
    thermostats_state = get_state_map(instance_state, "thermostats")
    sent: list[dict[str, Any]] = []

    for entity in targets:
        previous = thermostats_state.get(entity["id"]) if isinstance(thermostats_state.get(entity["id"]), dict) else {}
        next_setpoint = normalize_thermostat_setpoint(previous.get("setpoint"), 21.0)
        next_mode = normalize_thermostat_mode(previous.get("mode"))
        next_power = previous.get("isOn")
        if not isinstance(next_power, bool):
            next_power = True

        frames_to_send: list[dict[str, Any]] = []
        if requested_mode is not None:
            mode_code = THERMOSTAT_MODE_CODES.get(requested_mode, 0)
            mode_frame = build_protocol_frame(clamp(to_int(entity.get("address"), 1), 0, 254), THERMOSTAT_MODE_COMMAND, [mode_code])
            mode_payload, mode_hex = payload_from_frame(
                frame=mode_frame,
                payload_format=effective_payload_format,
                json_payload={
                    "type": "thermostat_mode_command",
                    "instanceId": instance_id,
                    "thermostatId": entity["id"],
                    "mode": requested_mode,
                    "address": entity["address"],
                    "channel": entity["channel"],
                    "sentAt": now_iso(),
                },
            )
            frames_to_send.append({"type": "mode", "payload": mode_payload, "frameHex": mode_hex})
            next_mode = requested_mode

        if requested_setpoint is not None:
            next_setpoint = requested_setpoint

        if requested_power is False:
            power_off_frame = build_protocol_frame(
                clamp(to_int(entity.get("address"), 1), 0, 254),
                THERMOSTAT_SETPOINT_COMMAND,
                [0, 0],
            )
            off_payload, off_hex = payload_from_frame(
                frame=power_off_frame,
                payload_format=effective_payload_format,
                json_payload={
                    "type": "thermostat_power_command",
                    "instanceId": instance_id,
                    "thermostatId": entity["id"],
                    "power": "off",
                    "address": entity["address"],
                    "channel": entity["channel"],
                    "sentAt": now_iso(),
                },
            )
            frames_to_send.append({"type": "power_off", "payload": off_payload, "frameHex": off_hex})
            next_power = False
        elif requested_setpoint is not None or requested_power is True:
            set_i, set_d = split_temperature(next_setpoint)
            power_on_frame = build_protocol_frame(
                clamp(to_int(entity.get("address"), 1), 0, 254),
                THERMOSTAT_SETPOINT_COMMAND,
                [set_i, set_d],
            )
            on_payload, on_hex = payload_from_frame(
                frame=power_on_frame,
                payload_format=effective_payload_format,
                json_payload={
                    "type": "thermostat_setpoint_command",
                    "instanceId": instance_id,
                    "thermostatId": entity["id"],
                    "setpoint": next_setpoint,
                    "power": "on",
                    "address": entity["address"],
                    "channel": entity["channel"],
                    "sentAt": now_iso(),
                },
            )
            frames_to_send.append({"type": "setpoint", "payload": on_payload, "frameHex": on_hex})
            next_power = True

        if not frames_to_send:
            raise ValueError("Nessun comando termostato generato")

        for frame_idx, frame_item in enumerate(frames_to_send):
            mqtt_publish(
                effective_topic,
                frame_item["payload"],
                qos=MQTT_COMMAND_QOS,
                retain=False,
                retries=MQTT_COMMAND_RETRIES,
                retry_delay_ms=MQTT_COMMAND_RETRY_DELAY_MS,
            )
            if frame_idx < (len(frames_to_send) - 1) and THERMOSTAT_COMMAND_FRAME_GAP_MS > 0:
                time.sleep(THERMOSTAT_COMMAND_FRAME_GAP_MS / 1000.0)

        verification: dict[str, Any] = {"ok": True}
        if must_verify:
            if THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS > 0:
                time.sleep(THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS / 1000.0)
            verification = poll_board_output_mask_via_mqtt(
                command_topic=effective_topic,
                response_topic=effective_response_topic,
                payload_format=effective_payload_format,
                address=clamp(to_int(entity.get("address"), 0), 0, 254),
                timeout_ms=THERMOSTAT_RESPONSE_TIMEOUT_MS,
                retries=THERMOSTAT_RESPONSE_RETRIES,
                retry_delay_ms=THERMOSTAT_RESPONSE_RETRY_DELAY_MS,
            )
        verified = bool(verification.get("ok"))
        if must_verify and not verified:
            reason = clean_text(verification.get("error"), "nessuna risposta")
            raise RuntimeError(f"Nessuna conferma dal dispositivo per {entity['id']}: {reason}")
        if must_verify:
            update_board_poll_state(instance_state, clamp(to_int(entity.get("address"), 0), 0, 254), verification)

        poll_data = verification.get("poll") if must_verify and isinstance(verification.get("poll"), dict) else {}
        poll_setpoint = None
        if isinstance(poll_data, dict) and "setpoint" in poll_data:
            raw_poll_setpoint = to_int(poll_data.get("setpoint"), -1)
            if raw_poll_setpoint >= 0:
                poll_setpoint = clamp(raw_poll_setpoint, 0, 99)
        final_setpoint = float(poll_setpoint) if isinstance(poll_setpoint, int) and poll_setpoint >= 0 else float(next_setpoint)
        final_is_on = final_setpoint > 0 if isinstance(poll_setpoint, int) and poll_setpoint >= 0 else bool(next_power)
        output_mask = clamp(to_int(verification.get("outputMask"), 0), 0, 255)
        bit = 1 << (clamp(to_int(entity.get("channel"), 1), 1, 8) - 1)
        final_is_active = bool(output_mask & bit) if verified else bool(previous.get("isActive")) if isinstance(previous.get("isActive"), bool) else final_is_on

        temperature = None
        if isinstance(poll_data, dict):
            raw_temp = poll_data.get("temperature")
            if is_finite_number(raw_temp):
                temperature = float(raw_temp)

        thermostats_state[entity["id"]] = {
            "setpoint": final_setpoint,
            "mode": next_mode,
            "isOn": final_is_on,
            "isActive": final_is_active,
            "temperature": temperature,
            "updatedAt": now_iso(),
        }

        item = {
            "id": entity["id"],
            "mode": next_mode,
            "setpoint": final_setpoint,
            "isOn": final_is_on,
            "isActive": final_is_active,
            "verified": verified,
            "publishRetries": MQTT_COMMAND_RETRIES,
            "frames": [],
        }
        for frame_item in frames_to_send:
            frame_out = {"type": frame_item["type"]}
            if frame_item.get("frameHex"):
                frame_out["frameHex"] = frame_item["frameHex"]
            add_payload_debug(frame_out, frame_item["payload"])
            item["frames"].append(frame_out)
        if verification.get("frameHex"):
            item["verifyFrameHex"] = verification.get("frameHex")
        if verification.get("outputMask") is not None:
            item["verifyOutputMask"] = verification.get("outputMask")
        if verification.get("error"):
            item["verifyReason"] = verification.get("error")
        if temperature is not None:
            item["temperature"] = temperature
        sent.append(item)

    _save_instance_state(light_state)
    return {
        "topic": effective_topic,
        "responseTopic": effective_response_topic,
        "payloadFormat": effective_payload_format,
        "sent": sent,
    }


def list_view(instance: dict[str, Any]) -> dict[str, Any]:
    instance_id = clean_text(instance.get("id"), "dr154-1")
    device_type = instance_device_type(instance)
    return {
        "id": instance_id,
        "name": clean_text(instance.get("name"), instance_id),
        "deviceType": device_type,
        "deviceLabel": device_type_public(device_type)["label"],
        "protocolVersion": clean_text(instance.get("protocolVersion"), "1.6"),
        "boardsCount": len(instance.get("boards", [])),
        "authRequired": instance_has_auth(instance),
        "controlUrl": instance_control_url(instance_id),
        "updatedAt": instance.get("updatedAt"),
    }


def collect_instance_addresses(instance: dict[str, Any]) -> list[int]:
    values: set[int] = set()
    for board in instance.get("boards", []):
        if not isinstance(board, dict):
            continue
        values.add(clamp(to_int(board.get("address"), -1), 0, 254))
    return sorted(values)


def infer_thermostat_active(channel: int, output_mask: int | None, fallback: Any) -> bool:
    if isinstance(output_mask, int):
        bit = 1 << (clamp(channel, 1, 8) - 1)
        return bool(output_mask & bit)
    return bool(fallback) if isinstance(fallback, bool) else True


def build_instance_status(instance_id: str, instance: dict[str, Any], refresh: bool = False) -> dict[str, Any]:
    light_state, _, instance_state = _load_instance_state(instance_id)
    lights_state = get_state_map(instance_state, "lights")
    dimmers_state = get_state_map(instance_state, "dimmers")
    shutters_state = get_state_map(instance_state, "shutters")
    thermostats_state = get_state_map(instance_state, "thermostats")
    boards_state = get_state_map(instance_state, "boards")

    command_topic = get_light_command_topic(instance)
    response_topic = get_light_response_topic(instance)
    payload_format = get_light_payload_format(instance)

    refresh_errors: list[dict[str, Any]] = []
    polls_by_address: dict[int, dict[str, Any]] = {}
    state_dirty = False

    if refresh:
        for address in collect_instance_addresses(instance):
            outcome = poll_board_output_mask_via_mqtt(
                command_topic=command_topic,
                response_topic=response_topic,
                payload_format=payload_format,
                address=address,
            )
            if outcome.get("ok"):
                polls_by_address[address] = outcome
                update_board_poll_state(instance_state, address, outcome)
                state_dirty = True
            else:
                refresh_errors.append({"address": address, "error": clean_text(outcome.get("error"), "poll fallito")})

    now_ts = now_iso()

    lights = light_entities(instance)
    dimmers = dimmer_entities(instance)
    shutters = shutter_entities(instance)
    thermostats = thermostat_entities(instance)

    for entity in lights:
        address = clamp(to_int(entity.get("address"), 0), 0, 254)
        poll = polls_by_address.get(address, {})
        poll_data = poll.get("poll") if isinstance(poll.get("poll"), dict) else None
        if isinstance(poll_data, dict):
            output_mask = clamp(to_int(poll_data.get("outputMask"), 0), 0, 255)
            bit = 1 << (clamp(to_int(entity.get("channel"), 1), 1, 8) - 1)
            lights_state[entity["id"]] = {
                "isOn": bool(output_mask & bit),
                "updatedAt": now_ts,
                "source": "poll-refresh",
            }
            state_dirty = True

    for entity in dimmers:
        address = clamp(to_int(entity.get("address"), 0), 0, 254)
        poll = polls_by_address.get(address, {})
        poll_data = poll.get("poll") if isinstance(poll.get("poll"), dict) else None
        if isinstance(poll_data, dict):
            level = clamp(to_int(poll_data.get("dimmerLevel"), 0), DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL)
            prev = dimmers_state.get(entity["id"]) if isinstance(dimmers_state.get(entity["id"]), dict) else {}
            last_on_level = clamp(to_int(prev.get("lastOnLevel"), DIMMER_MAX_LEVEL), 1, DIMMER_MAX_LEVEL)
            if level > 0:
                last_on_level = level
            dimmers_state[entity["id"]] = {
                "level": level,
                "isOn": level > 0,
                "lastOnLevel": last_on_level,
                "updatedAt": now_ts,
                "source": "poll-refresh",
            }
            state_dirty = True

    for entity in thermostats:
        address = clamp(to_int(entity.get("address"), 0), 0, 254)
        poll = polls_by_address.get(address, {})
        poll_data = poll.get("poll") if isinstance(poll.get("poll"), dict) else None
        if isinstance(poll_data, dict):
            prev = thermostats_state.get(entity["id"]) if isinstance(thermostats_state.get(entity["id"]), dict) else {}
            setpoint_raw = clamp(to_int(poll_data.get("setpoint"), 0), 0, 99)
            output_mask = clamp(to_int(poll_data.get("outputMask"), 0), 0, 255)
            mode = normalize_thermostat_mode(prev.get("mode"))
            temperature = poll_data.get("temperature")
            thermostats_state[entity["id"]] = {
                "setpoint": float(setpoint_raw),
                "mode": mode,
                "isOn": setpoint_raw > 0,
                "isActive": infer_thermostat_active(
                    clamp(to_int(entity.get("channel"), 1), 1, 8),
                    output_mask,
                    prev.get("isActive"),
                ),
                "temperature": float(temperature) if is_finite_number(temperature) else prev.get("temperature"),
                "updatedAt": now_ts,
                "source": "poll-refresh",
            }
            state_dirty = True

    rooms_map: dict[str, dict[str, Any]] = {}

    def room_bucket(name: str) -> dict[str, Any]:
        room_name = clean_text(name, "Senza stanza")
        if room_name not in rooms_map:
            rooms_map[room_name] = {
                "name": room_name,
                "lights": [],
                "dimmers": [],
                "shutters": [],
                "thermostats": [],
            }
        return rooms_map[room_name]

    for entity in lights:
        current = lights_state.get(entity["id"]) if isinstance(lights_state.get(entity["id"]), dict) else {}
        item = dict(entity)
        state_value = current.get("isOn")
        item["isOn"] = bool(state_value) if isinstance(state_value, bool) else None
        if current.get("updatedAt"):
            item["stateUpdatedAt"] = current.get("updatedAt")
        room_bucket(entity.get("room", "Senza stanza"))["lights"].append(item)

    for entity in dimmers:
        current = dimmers_state.get(entity["id"]) if isinstance(dimmers_state.get(entity["id"]), dict) else {}
        level = clamp(to_int(current.get("level"), 0), DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL)
        item = dict(entity)
        item["level"] = level
        item["isOn"] = bool(current.get("isOn")) if isinstance(current.get("isOn"), bool) else (level > 0)
        if current.get("updatedAt"):
            item["stateUpdatedAt"] = current.get("updatedAt")
        room_bucket(entity.get("room", "Senza stanza"))["dimmers"].append(item)

    for entity in shutters:
        current = shutters_state.get(entity["id"]) if isinstance(shutters_state.get(entity["id"]), dict) else {}
        item = dict(entity)
        item["action"] = clean_text(current.get("action"), "unknown")
        if isinstance(current.get("isActive"), bool):
            item["isActive"] = bool(current.get("isActive"))
        if current.get("updatedAt"):
            item["stateUpdatedAt"] = current.get("updatedAt")
        room_bucket(entity.get("room", "Senza stanza"))["shutters"].append(item)

    for entity in thermostats:
        current = thermostats_state.get(entity["id"]) if isinstance(thermostats_state.get(entity["id"]), dict) else {}
        poll_entry = boards_state.get(str(clamp(to_int(entity.get("address"), 0), 0, 254)))
        poll_data = poll_entry.get("poll") if isinstance(poll_entry, dict) and isinstance(poll_entry.get("poll"), dict) else {}
        temperature = current.get("temperature")
        poll_temperature = poll_data.get("temperature") if isinstance(poll_data, dict) else None
        if is_finite_number(poll_temperature):
            temperature = float(poll_temperature)
        setpoint_value = current.get("setpoint")
        if not is_finite_number(setpoint_value):
            setpoint_value = poll_data.get("setpoint") if isinstance(poll_data, dict) else None
        if not is_finite_number(setpoint_value):
            setpoint_value = 21.0
        mode = normalize_thermostat_mode(current.get("mode"))
        is_on = current.get("isOn")
        if not isinstance(is_on, bool):
            is_on = float(setpoint_value) > 0
        output_mask = None
        if isinstance(poll_data, dict) and "outputMask" in poll_data:
            output_mask = clamp(to_int(poll_data.get("outputMask"), 0), 0, 255)
        is_active = infer_thermostat_active(
            clamp(to_int(entity.get("channel"), 1), 1, 8),
            output_mask,
            current.get("isActive"),
        )
        item = dict(entity)
        item["temperature"] = float(temperature) if is_finite_number(temperature) else None
        item["setpoint"] = float(setpoint_value)
        item["mode"] = mode
        item["isOn"] = is_on
        item["isActive"] = is_active
        if current.get("updatedAt"):
            item["stateUpdatedAt"] = current.get("updatedAt")
        room_bucket(entity.get("room", "Senza stanza"))["thermostats"].append(item)

    boards_out: list[dict[str, Any]] = []
    for board in instance.get("boards", []):
        if not isinstance(board, dict):
            continue
        kind = clean_text(board.get("kind"), "light")
        board_out = {
            "id": clean_text(board.get("id"), "board-1"),
            "name": clean_text(board.get("name"), clean_text(board.get("id"), "board-1")),
            "address": clamp(to_int(board.get("address"), 0), 0, 254),
            "kind": kind,
            "channels": [],
        }
        for channel_data in board.get("channels", []):
            if not isinstance(channel_data, dict):
                continue
            channel = clamp(to_int(channel_data.get("channel"), 1), 1, KIND_META.get(kind, KIND_META["light"])["maxChannels"])
            item_id = f"{board_out['id']}-c{channel}"
            base = {
                "id": item_id,
                "channel": channel,
                "name": clean_text(channel_data.get("name"), default_channel_name(kind, channel)),
                "room": clean_text(channel_data.get("room"), "Senza stanza"),
            }
            if kind == "light":
                current = lights_state.get(item_id) if isinstance(lights_state.get(item_id), dict) else {}
                base["isOn"] = current.get("isOn") if isinstance(current.get("isOn"), bool) else None
            elif kind == "dimmer":
                current = dimmers_state.get(item_id) if isinstance(dimmers_state.get(item_id), dict) else {}
                base["level"] = clamp(to_int(current.get("level"), 0), DIMMER_MIN_LEVEL, DIMMER_MAX_LEVEL)
                base["isOn"] = bool(current.get("isOn")) if isinstance(current.get("isOn"), bool) else (base["level"] > 0)
            elif kind == "shutter":
                current = shutters_state.get(item_id) if isinstance(shutters_state.get(item_id), dict) else {}
                base["action"] = clean_text(current.get("action"), "unknown")
            else:
                current = thermostats_state.get(item_id) if isinstance(thermostats_state.get(item_id), dict) else {}
                base["temperature"] = current.get("temperature")
                base["setpoint"] = current.get("setpoint")
                base["mode"] = normalize_thermostat_mode(current.get("mode"))
                base["isOn"] = current.get("isOn") if isinstance(current.get("isOn"), bool) else None
                base["isActive"] = current.get("isActive") if isinstance(current.get("isActive"), bool) else None
            board_out["channels"].append(base)
        boards_out.append(board_out)

    rooms_out = sorted(rooms_map.values(), key=lambda item: str(item.get("name", "")).lower())
    if state_dirty:
        instance_state["updatedAt"] = now_ts
        _save_instance_state(light_state)

    return {
        "instanceId": instance_id,
        "updatedAt": now_ts,
        "refreshErrors": refresh_errors,
        "commandTopic": command_topic,
        "responseTopic": response_topic,
        "payloadFormat": payload_format,
        "rooms": rooms_out,
        "boards": boards_out,
    }


def parse_bool_flag(value: Any, default: bool = False) -> bool:
    parsed = parse_bool_text(value)
    if isinstance(parsed, bool):
        return parsed
    return default


def extract_command_transport(instance: dict[str, Any], body: dict[str, Any]) -> tuple[str, str, str]:
    topic = clean_text(body.get("topic"), get_light_command_topic(instance))
    if not topic:
        raise ValueError("Topic MQTT non valido")
    response_topic_raw = body.get("responseTopic")
    response_topic = response_topic_raw.strip() if isinstance(response_topic_raw, str) else get_light_response_topic(instance)
    payload_format = clean_text(body.get("payloadFormat"), get_light_payload_format(instance)).lower()
    if payload_format not in LIGHT_PAYLOAD_FORMATS:
        raise ValueError("Formato payload non valido")
    return topic, response_topic, payload_format


def select_targets_from_body(
    *,
    kind: str,
    entities: list[dict[str, Any]],
    body: dict[str, Any],
    id_key: str,
) -> list[dict[str, Any]]:
    all_targets = parse_bool_flag(body.get("all"), False)
    entity_id = clean_text(body.get(id_key), "")
    max_channels = KIND_META.get(kind, KIND_META["light"])["maxChannels"]
    label = KIND_META.get(kind, KIND_META["light"])["label"].lower()

    if all_targets:
        if not entities:
            raise ValueError(f"Nessuna entita {label} configurata")
        return entities

    if entity_id:
        matches = [item for item in entities if item.get("id") == entity_id]
        if matches:
            return matches
        target_in = body.get("target") if isinstance(body.get("target"), dict) else {}
        raw_channel = to_int(target_in.get("channel"), -1)
        if raw_channel < 1 or raw_channel > max_channels:
            raise LookupError("Entita non trovata")
        board_id = clean_text(target_in.get("boardId"), entity_id.split("-c", 1)[0] or "board-1")
        address = clamp(to_int(target_in.get("address"), 0), 0, 254)
        return [
            {
                "id": entity_id,
                "boardId": board_id,
                "boardName": clean_text(target_in.get("boardName"), board_id),
                "address": address,
                "channel": raw_channel,
                "kind": kind,
                "name": clean_text(target_in.get("name"), default_channel_name(kind, raw_channel)),
                "room": clean_text(target_in.get("room"), "Senza stanza"),
            }
        ]

    raise ValueError(f"Specifica '{id_key}' oppure imposta 'all=true'")


def err(message: str, status: int = 400):
    return jsonify({"error": message}), status


def resolve_manifest_default_instance() -> tuple[str, str]:
    hinted = slugify(request.args.get("instance"), "")
    if not hinted:
        referer = clean_text(request.headers.get("Referer"), "")
        if referer:
            hinted = control_instance_from_path(urlparse(referer).path)
    if not hinted:
        return "dr154-1", "Sheltr Cloud"
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, hinted)
    if isinstance(instance, dict):
        instance_id = clean_text(instance.get("id"), hinted)
        instance_name = clean_text(instance.get("name"), instance_id)
        return instance_id, instance_name
    return hinted, hinted


def pwa_instance_label(instance_name: str, fallback: str) -> str:
    raw = clean_text(instance_name, fallback)
    primary = re.split(r"\s*//\s*", raw, maxsplit=1)[0].strip()
    return primary or raw


def control_manifest_payload(instance_id: str, instance_name: str) -> dict[str, Any]:
    clean_id = slugify(instance_id, "dr154-1")
    clean_name = pwa_instance_label(instance_name, clean_id)
    short_name = clean_name[:24] if len(clean_name) > 24 else clean_name
    control_url = f"/control/{clean_id}"
    return {
        "id": control_url,
        "name": clean_name,
        "short_name": short_name,
        "description": "Sheltr Cloud control panel for DR154 devices",
        "start_url": control_url,
        "scope": "/control/",
        "display": "standalone",
        "background_color": "#191919",
        "theme_color": "#191919",
        "icons": [
            {
                "src": "/static/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any",
            },
            {
                "src": "/static/logo.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any",
            },
        ],
    }


@app.get("/")
def root():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/manifest.webmanifest")
def manifest_default():
    instance_id, instance_name = resolve_manifest_default_instance()
    payload = control_manifest_payload(instance_id, instance_name)
    response = app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        status=200,
        mimetype="application/manifest+json",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/manifest/<instance_id>.webmanifest")
def manifest_instance(instance_id: str):
    clean_id = slugify(instance_id, "dr154-1")
    clean_name = clean_id
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, clean_id)
    if isinstance(instance, dict):
        clean_id = clean_text(instance.get("id"), clean_id)
        clean_name = clean_text(instance.get("name"), clean_id)
    payload = control_manifest_payload(clean_id, clean_name)
    response = app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        status=200,
        mimetype="application/manifest+json",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/control")
def control_page():
    return send_from_directory(app.static_folder, "control.html")


@app.get("/control/<instance_id>")
def control_instance_page(instance_id: str):
    _ = instance_id
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


@app.get("/api/config/auth")
def api_config_auth():
    return jsonify(
        {
            "required": config_auth_enabled(),
            "username": CONFIG_AUTH_USERNAME if config_auth_enabled() else "",
        }
    )


@app.post("/api/config/auth/login")
def api_config_auth_login():
    if not config_auth_enabled():
        return jsonify({"ok": True, "required": False, "token": "", "expiresAt": None})
    body = request.get_json(silent=True) or {}
    username_in = clean_text(body.get("username"), "")
    password_in = str(body.get("password") or "")
    if username_in != CONFIG_AUTH_USERNAME or password_in != CONFIG_AUTH_PASSWORD:
        return err("Credenziali configurazione non valide", 401)
    token, expires_at = issue_config_token()
    response = jsonify({"ok": True, "required": True, "token": token, "expiresAt": expires_at})
    response.set_cookie(
        config_token_cookie_name(),
        token,
        max_age=CONFIG_AUTH_TTL_SEC,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


@app.post("/api/config/auth/logout")
def api_config_auth_logout():
    response = jsonify({"ok": True})
    response.delete_cookie(config_token_cookie_name(), path="/")
    return response


@app.get("/api/config/meta")
def api_config_meta():
    auth_error = require_config_auth()
    if auth_error is not None:
        return auth_error
    return api_meta()


@app.get("/api/config/instances")
def api_config_list_instances():
    auth_error = require_config_auth()
    if auth_error is not None:
        return auth_error
    return api_list_instances()


@app.post("/api/config/instances")
def api_config_create_instance():
    body = request.get_json(silent=True) or {}
    auth_error = require_config_auth(body)
    if auth_error is not None:
        return auth_error
    return api_create_instance()


@app.get("/api/config/instances/<instance_id>")
def api_config_get_instance(instance_id: str):
    auth_error = require_config_auth()
    if auth_error is not None:
        return auth_error
    return api_get_instance(instance_id)


@app.put("/api/config/instances/<instance_id>")
def api_config_update_instance(instance_id: str):
    body = request.get_json(silent=True) or {}
    auth_error = require_config_auth(body)
    if auth_error is not None:
        return auth_error
    return api_update_instance(instance_id)


@app.delete("/api/config/instances/<instance_id>")
def api_config_delete_instance(instance_id: str):
    auth_error = require_config_auth()
    if auth_error is not None:
        return auth_error
    return api_delete_instance(instance_id)


@app.post("/api/config/instances/<instance_id>/publish")
def api_config_publish_instance(instance_id: str):
    body = request.get_json(silent=True) or {}
    auth_error = require_config_auth(body)
    if auth_error is not None:
        return auth_error
    return api_publish_instance(instance_id)


@app.get("/api/meta")
def api_meta():
    return jsonify(
        {
            "kindMeta": KIND_META,
            "mqttBaseTopic": MQTT_BASE_TOPIC,
            "mqttConfigQos": MQTT_CONFIG_QOS,
            "mqttCommandQos": MQTT_COMMAND_QOS,
            "mqttPublishTimeoutSec": MQTT_PUBLISH_TIMEOUT_SEC,
            "mqttCommandRetries": MQTT_COMMAND_RETRIES,
            "mqttCommandRetryDelayMs": MQTT_COMMAND_RETRY_DELAY_MS,
            "mqttCommandRepeatOnOff": MQTT_COMMAND_REPEAT_ONOFF,
            "mqttCommandRepeatGapMs": MQTT_COMMAND_REPEAT_GAP_MS,
            "mqttResponseTimeoutMs": MQTT_RESPONSE_TIMEOUT_MS,
            "mqttResponseRetries": MQTT_RESPONSE_RETRIES,
            "mqttResponseRetryDelayMs": MQTT_RESPONSE_RETRY_DELAY_MS,
            "mqttResponseAfterCommandDelayMs": MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS,
            "thermostatResponseTimeoutMs": THERMOSTAT_RESPONSE_TIMEOUT_MS,
            "thermostatResponseRetries": THERMOSTAT_RESPONSE_RETRIES,
            "thermostatResponseRetryDelayMs": THERMOSTAT_RESPONSE_RETRY_DELAY_MS,
            "thermostatResponseAfterCommandDelayMs": THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS,
            "thermostatCommandFrameGapMs": THERMOSTAT_COMMAND_FRAME_GAP_MS,
            "mqttRequireResponse": MQTT_REQUIRE_RESPONSE,
            "lightPayloadFormats": sorted(LIGHT_PAYLOAD_FORMATS),
            "defaultDeviceType": DEFAULT_DEVICE_TYPE,
            "deviceTypes": device_types_public_meta(),
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
    fallback_id = slugify(body.get("id"), "dr154-1")
    instance = normalize_instance(body, fallback_id=fallback_id, current_instance=None)

    with STORE_LOCK:
        store = load_store()
        if find_instance(store, instance["id"]) is not None:
            return err(f"Istanza '{instance['id']}' già esistente", 409)
        store["instances"].append(instance)
        save_store(store)

    return jsonify({"instance": instance_public(instance)}), 201


@app.get("/api/instances/<instance_id>")
def api_get_instance(instance_id: str):
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    return jsonify({"instance": instance_public(instance)})


@app.put("/api/instances/<instance_id>")
def api_update_instance(instance_id: str):
    body = request.get_json(silent=True) or {}

    with STORE_LOCK:
        store = load_store()
        current = find_instance(store, instance_id)
        if current is None:
            return err("Istanza non trovata", 404)
        current_id = clean_text(current.get("id"), slugify(instance_id, "dr154-1"))
        requested_id = slugify(body.get("id"), current_id)
        if requested_id != current_id and find_instance(store, requested_id) is not None:
            return err(f"Istanza '{requested_id}' già esistente", 409)
        body["id"] = requested_id
        instance = normalize_instance(body, fallback_id=requested_id, current_instance=current)
        for idx, item in enumerate(store["instances"]):
            if clean_text(item.get("id"), "") == current_id:
                store["instances"][idx] = instance
                break
        save_store(store)

    if current_id != instance["id"]:
        with LIGHT_STATE_LOCK:
            light_state = load_light_state()
            instances_map = light_state.get("instances")
            if isinstance(instances_map, dict):
                old_state = instances_map.pop(current_id, None)
                if isinstance(old_state, dict):
                    if isinstance(instances_map.get(instance["id"]), dict):
                        instances_map[instance["id"]].update(old_state)
                    else:
                        instances_map[instance["id"]] = old_state
                save_light_state(light_state)
        migrate_instance_tokens(current_id, instance["id"])

    return jsonify({"instance": instance_public(instance)})


@app.delete("/api/instances/<instance_id>")
def api_delete_instance(instance_id: str):
    with STORE_LOCK:
        store = load_store()
        current = find_instance(store, instance_id)
        if current is None:
            return err("Istanza non trovata", 404)
        current_id = clean_text(current.get("id"), slugify(instance_id, "dr154-1"))
        store["instances"] = [item for item in store["instances"] if clean_text(item.get("id"), "") != current_id]
        save_store(store)

    with LIGHT_STATE_LOCK:
        light_state = load_light_state()
        instances_map = light_state.get("instances")
        if isinstance(instances_map, dict):
            instances_map.pop(current_id, None)
            save_light_state(light_state)

    return jsonify({"ok": True})


@app.get("/api/instances/<instance_id>/auth")
def api_instance_auth(instance_id: str):
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    return jsonify({"auth": instance_auth_meta(instance)})


@app.post("/api/instances/<instance_id>/auth/login")
def api_instance_auth_login(instance_id: str):
    body = request.get_json(silent=True) or {}
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)

    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))
    auth = instance_auth_meta(instance)
    if not auth.get("passwordConfigured"):
        return err("Login non configurato per questa istanza", 400)

    username_in = clean_text(body.get("username"), "")
    password_in = str(body.get("password") or "")
    stored_auth = instance.get("auth") if isinstance(instance.get("auth"), dict) else {}
    expected_user = clean_text(stored_auth.get("username"), "")
    expected_hash = clean_text(stored_auth.get("passwordHash"), "")
    if username_in != expected_user or not check_password_hash(expected_hash, password_in):
        return err("Credenziali non valide", 401)

    token, expires_at = issue_instance_token(instance_id_real)
    response = jsonify({"ok": True, "token": token, "expiresAt": expires_at})
    response.set_cookie(
        instance_token_cookie_name(instance_id_real),
        token,
        max_age=INSTANCE_AUTH_TTL_SEC,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


@app.post("/api/instances/<instance_id>/auth/logout")
def api_instance_auth_logout(instance_id: str):
    instance_id_real = slugify(instance_id, "dr154-1")
    body = request.get_json(silent=True) or {}
    token = extract_instance_token(body, instance_id=instance_id_real)
    revoke_instance_token(token)
    response = jsonify({"ok": True})
    response.delete_cookie(instance_token_cookie_name(instance_id_real), path="/")
    response.delete_cookie("instance_token", path="/")
    return response


@app.post("/api/instances/<instance_id>/publish")
def api_publish_instance(instance_id: str):
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))

    body = request.get_json(silent=True) or {}
    topic = clean_text(body.get("topic"), f"{MQTT_BASE_TOPIC}/{instance_id_real}/config")
    if not topic:
        return err("Topic MQTT non valido", 400)

    try:
        mqtt_publish(topic, instance_publish_payload(instance), qos=MQTT_CONFIG_QOS, retain=True, retries=1, retry_delay_ms=200)
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore pubblicazione MQTT: {exc}", 502)

    return jsonify({"ok": True, "topic": topic, "qos": MQTT_CONFIG_QOS, "retain": True})


@app.get("/api/instances/<instance_id>/lights")
def api_list_lights(instance_id: str):
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))
    auth_error = require_instance_auth(instance, instance_id_real)
    if auth_error is not None:
        return auth_error

    refresh = clean_text(request.args.get("refresh"), "0") in {"1", "true", "yes", "on"}
    status = build_instance_status(instance_id_real, instance, refresh=refresh)
    lights: list[dict[str, Any]] = []
    for room in status.get("rooms", []):
        if not isinstance(room, dict):
            continue
        for item in room.get("lights", []):
            if isinstance(item, dict):
                lights.append(item)

    return jsonify(
        {
            "instanceId": instance_id_real,
            "commandTopic": status.get("commandTopic"),
            "responseTopic": status.get("responseTopic"),
            "payloadFormat": status.get("payloadFormat"),
            "refreshErrors": status.get("refreshErrors", []),
            "lights": lights,
        }
    )


@app.get("/api/instances/<instance_id>/status")
def api_instance_status(instance_id: str):
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))
    auth_error = require_instance_auth(instance, instance_id_real)
    if auth_error is not None:
        return auth_error

    refresh = clean_text(request.args.get("refresh"), "0") in {"1", "true", "yes", "on"}
    try:
        return jsonify(build_instance_status(instance_id_real, instance, refresh=refresh))
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore recupero stato: {exc}", 502)


@app.post("/api/instances/<instance_id>/lights/command")
def api_light_command(instance_id: str):
    body = request.get_json(silent=True) or {}
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))
    auth_error = require_instance_auth(instance, instance_id_real, body)
    if auth_error is not None:
        return auth_error

    action = clean_text(body.get("action"), "").lower()
    if action not in LIGHT_COMMAND_ACTIONS:
        allowed = ",".join(sorted(LIGHT_COMMAND_ACTIONS))
        return err(f"Azione luce non valida. Valori ammessi: {allowed}", 400)

    try:
        topic, response_topic, payload_format = extract_command_transport(instance, body)
    except ValueError as exc:
        return err(str(exc), 400)

    entities = light_entities(instance)
    try:
        targets = select_targets_from_body(kind="light", entities=entities, body=body, id_key="lightId")
    except LookupError as exc:
        return err(str(exc), 404)
    except ValueError as exc:
        return err(str(exc), 400)

    try:
        result = execute_light_targets(
            instance_id=instance_id_real,
            instance=instance,
            targets=targets,
            action=action,
            topic=topic,
            response_topic=response_topic,
            payload_format=payload_format,
            require_response=MQTT_REQUIRE_RESPONSE,
        )
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore pubblicazione comando luce: {exc}", 502)

    return jsonify(
        {
            "ok": True,
            "topic": result["topic"],
            "responseTopic": result["responseTopic"],
            "payloadFormat": result["payloadFormat"],
            "qos": MQTT_COMMAND_QOS,
            "retain": False,
            "reliability": {
                "retries": MQTT_COMMAND_RETRIES,
                "retryDelayMs": MQTT_COMMAND_RETRY_DELAY_MS,
                "repeatOnOff": MQTT_COMMAND_REPEAT_ONOFF,
                "repeatGapMs": MQTT_COMMAND_REPEAT_GAP_MS,
                "publishTimeoutSec": MQTT_PUBLISH_TIMEOUT_SEC,
                "responseTimeoutMs": MQTT_RESPONSE_TIMEOUT_MS,
                "responseRetries": MQTT_RESPONSE_RETRIES,
                "responseRetryDelayMs": MQTT_RESPONSE_RETRY_DELAY_MS,
                "responseAfterCommandDelayMs": MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS,
                "thermostatResponseTimeoutMs": THERMOSTAT_RESPONSE_TIMEOUT_MS,
                "thermostatResponseRetries": THERMOSTAT_RESPONSE_RETRIES,
                "thermostatResponseRetryDelayMs": THERMOSTAT_RESPONSE_RETRY_DELAY_MS,
                "thermostatResponseAfterCommandDelayMs": THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS,
                "thermostatCommandFrameGapMs": THERMOSTAT_COMMAND_FRAME_GAP_MS,
                "requireResponse": MQTT_REQUIRE_RESPONSE,
            },
            "sent": result["sent"],
        }
    )


@app.post("/api/instances/<instance_id>/dimmers/command")
def api_dimmer_command(instance_id: str):
    body = request.get_json(silent=True) or {}
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))
    auth_error = require_instance_auth(instance, instance_id_real, body)
    if auth_error is not None:
        return auth_error

    action = clean_text(body.get("action"), "").lower()
    if not action:
        action = "set" if body.get("level") is not None else ""
    if action not in DIMMER_ACTIONS:
        allowed = ",".join(sorted(DIMMER_ACTIONS))
        return err(f"Azione dimmer non valida. Valori ammessi: {allowed}", 400)

    level: int | None = None
    if body.get("level") is not None:
        raw_level = to_int(body.get("level"), -1)
        if raw_level < DIMMER_MIN_LEVEL or raw_level > DIMMER_MAX_LEVEL:
            return err("Level dimmer non valido (0..9)", 400)
        level = raw_level
    if action == "set" and level is None:
        return err("Per action='set' devi specificare 'level' (0..9)", 400)

    try:
        topic, response_topic, payload_format = extract_command_transport(instance, body)
    except ValueError as exc:
        return err(str(exc), 400)

    entities = dimmer_entities(instance)
    try:
        targets = select_targets_from_body(kind="dimmer", entities=entities, body=body, id_key="dimmerId")
    except LookupError as exc:
        return err(str(exc), 404)
    except ValueError as exc:
        return err(str(exc), 400)

    try:
        result = execute_dimmer_targets(
            instance_id=instance_id_real,
            instance=instance,
            targets=targets,
            action=action,
            level=level,
            topic=topic,
            response_topic=response_topic,
            payload_format=payload_format,
            require_response=MQTT_REQUIRE_RESPONSE,
        )
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore pubblicazione comando dimmer: {exc}", 502)

    return jsonify(
        {
            "ok": True,
            "topic": result["topic"],
            "responseTopic": result["responseTopic"],
            "payloadFormat": result["payloadFormat"],
            "qos": MQTT_COMMAND_QOS,
            "retain": False,
            "reliability": {
                "retries": MQTT_COMMAND_RETRIES,
                "retryDelayMs": MQTT_COMMAND_RETRY_DELAY_MS,
                "publishTimeoutSec": MQTT_PUBLISH_TIMEOUT_SEC,
                "responseTimeoutMs": MQTT_RESPONSE_TIMEOUT_MS,
                "responseRetries": MQTT_RESPONSE_RETRIES,
                "responseRetryDelayMs": MQTT_RESPONSE_RETRY_DELAY_MS,
                "responseAfterCommandDelayMs": MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS,
                "requireResponse": MQTT_REQUIRE_RESPONSE,
            },
            "sent": result["sent"],
        }
    )


@app.post("/api/instances/<instance_id>/shutters/command")
def api_shutter_command(instance_id: str):
    body = request.get_json(silent=True) or {}
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))
    auth_error = require_instance_auth(instance, instance_id_real, body)
    if auth_error is not None:
        return auth_error

    action = clean_text(body.get("action"), "").lower()
    if action not in SHUTTER_ACTION_CODES:
        allowed = ",".join(sorted(SHUTTER_ACTION_CODES))
        return err(f"Azione tapparella non valida. Valori ammessi: {allowed}", 400)

    try:
        topic, response_topic, payload_format = extract_command_transport(instance, body)
    except ValueError as exc:
        return err(str(exc), 400)

    entities = shutter_entities(instance)
    try:
        targets = select_targets_from_body(kind="shutter", entities=entities, body=body, id_key="shutterId")
    except LookupError as exc:
        return err(str(exc), 404)
    except ValueError as exc:
        return err(str(exc), 400)

    try:
        result = execute_shutter_targets(
            instance_id=instance_id_real,
            instance=instance,
            targets=targets,
            action=action,
            topic=topic,
            response_topic=response_topic,
            payload_format=payload_format,
            require_response=MQTT_REQUIRE_RESPONSE,
        )
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore pubblicazione comando tapparella: {exc}", 502)

    return jsonify(
        {
            "ok": True,
            "topic": result["topic"],
            "responseTopic": result["responseTopic"],
            "payloadFormat": result["payloadFormat"],
            "qos": MQTT_COMMAND_QOS,
            "retain": False,
            "reliability": {
                "retries": MQTT_COMMAND_RETRIES,
                "retryDelayMs": MQTT_COMMAND_RETRY_DELAY_MS,
                "publishTimeoutSec": MQTT_PUBLISH_TIMEOUT_SEC,
                "responseTimeoutMs": MQTT_RESPONSE_TIMEOUT_MS,
                "responseRetries": MQTT_RESPONSE_RETRIES,
                "responseRetryDelayMs": MQTT_RESPONSE_RETRY_DELAY_MS,
                "responseAfterCommandDelayMs": MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS,
                "requireResponse": MQTT_REQUIRE_RESPONSE,
            },
            "sent": result["sent"],
        }
    )


@app.post("/api/instances/<instance_id>/thermostats/command")
def api_thermostat_command(instance_id: str):
    body = request.get_json(silent=True) or {}
    with STORE_LOCK:
        store = load_store()
        instance = find_instance(store, instance_id)
    if instance is None:
        return err("Istanza non trovata", 404)
    instance_id_real = clean_text(instance.get("id"), slugify(instance_id, "dr154-1"))
    auth_error = require_instance_auth(instance, instance_id_real, body)
    if auth_error is not None:
        return auth_error

    setpoint_raw = body.get("setpoint", body.get("set"))
    setpoint: float | None = None
    if setpoint_raw is not None and clean_text(setpoint_raw, ""):
        setpoint_value = to_float(setpoint_raw, float("nan"))
        if not is_finite_number(setpoint_value):
            return err("Setpoint non valido", 400)
        setpoint = normalize_thermostat_setpoint(setpoint_value, 21.0)

    mode_raw = body.get("mode")
    mode = normalize_thermostat_mode(mode_raw) if isinstance(mode_raw, str) and mode_raw.strip() else None
    power = parse_bool_text(body.get("power"))

    if setpoint is None and mode is None and power is None:
        return err("Specifica almeno uno tra setpoint, mode, power", 400)

    try:
        topic, response_topic, payload_format = extract_command_transport(instance, body)
    except ValueError as exc:
        return err(str(exc), 400)

    entities = thermostat_entities(instance)
    try:
        targets = select_targets_from_body(kind="thermostat", entities=entities, body=body, id_key="thermostatId")
    except LookupError as exc:
        return err(str(exc), 404)
    except ValueError as exc:
        return err(str(exc), 400)

    try:
        result = execute_thermostat_targets(
            instance_id=instance_id_real,
            instance=instance,
            targets=targets,
            setpoint=setpoint,
            mode=mode,
            power=power if isinstance(power, bool) else None,
            topic=topic,
            response_topic=response_topic,
            payload_format=payload_format,
            require_response=MQTT_REQUIRE_RESPONSE,
        )
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return err(f"Errore pubblicazione comando termostato: {exc}", 502)

    return jsonify(
        {
            "ok": True,
            "topic": result["topic"],
            "responseTopic": result["responseTopic"],
            "payloadFormat": result["payloadFormat"],
            "qos": MQTT_COMMAND_QOS,
            "retain": False,
            "reliability": {
                "retries": MQTT_COMMAND_RETRIES,
                "retryDelayMs": MQTT_COMMAND_RETRY_DELAY_MS,
                "publishTimeoutSec": MQTT_PUBLISH_TIMEOUT_SEC,
                "responseTimeoutMs": THERMOSTAT_RESPONSE_TIMEOUT_MS,
                "responseRetries": THERMOSTAT_RESPONSE_RETRIES,
                "responseRetryDelayMs": THERMOSTAT_RESPONSE_RETRY_DELAY_MS,
                "responseAfterCommandDelayMs": THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS,
                "commandFrameGapMs": THERMOSTAT_COMMAND_FRAME_GAP_MS,
                "requireResponse": MQTT_REQUIRE_RESPONSE,
            },
            "sent": result["sent"],
        }
    )


ensure_profile_loop()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
