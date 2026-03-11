#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from paho.mqtt import client as mqtt
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
MQTT_RESPONSE_TIMEOUT_MS = max(200, int(os.environ.get("MQTT_RESPONSE_TIMEOUT_MS", "1600")))
MQTT_RESPONSE_RETRIES = max(0, min(5, int(os.environ.get("MQTT_RESPONSE_RETRIES", "1"))))
MQTT_RESPONSE_RETRY_DELAY_MS = max(0, int(os.environ.get("MQTT_RESPONSE_RETRY_DELAY_MS", "140")))
MQTT_REQUIRE_RESPONSE = os.environ.get("MQTT_REQUIRE_RESPONSE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
INSTANCE_AUTH_TTL_SEC = max(300, int(os.environ.get("INSTANCE_AUTH_TTL_SEC", "43200")))
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
AUTH_LOCK = threading.Lock()
AUTH_SESSIONS: dict[str, dict[str, Any]] = {}

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
    raw = clean_text(value, fallback).lower().replace("_", "-")
    raw = re.sub(r"[^a-z0-9-]+", "-", raw)
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


def normalize_instance(raw: Any, fallback_id: str, current_instance: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    current = current_instance if isinstance(current_instance, dict) else {}
    current_id = clean_text(current.get("id"), "dr154-1")
    instance_id = slugify(payload.get("id"), slugify(fallback_id, slugify(current_id, "dr154-1")))
    instance_name = clean_text(payload.get("name"), clean_text(current.get("name"), instance_id))
    boards_raw = payload.get("boards")
    boards_input = boards_raw if isinstance(boards_raw, list) else []
    mqtt_in = payload.get("mqtt") if isinstance(payload.get("mqtt"), dict) else {}
    mqtt_current = current.get("mqtt") if isinstance(current.get("mqtt"), dict) else {}
    light_command_topic = clean_text(
        mqtt_in.get("lightCommandTopic"),
        clean_text(mqtt_current.get("lightCommandTopic"), f"{MQTT_BASE_TOPIC}/{instance_id}/cmd/light"),
    )
    response_raw = mqtt_in.get("lightResponseTopic")
    if isinstance(response_raw, str):
        light_response_topic = response_raw.strip()
    else:
        light_response_topic = clean_text(
            mqtt_current.get("lightResponseTopic"),
            f"{MQTT_BASE_TOPIC}/{instance_id}/pub/light",
        )
    light_payload_format = clean_text(
        mqtt_in.get("lightPayloadFormat"),
        clean_text(mqtt_current.get("lightPayloadFormat"), "frame_hex_space_crlf"),
    ).lower()
    if light_payload_format not in LIGHT_PAYLOAD_FORMATS:
        light_payload_format = "frame_hex_space_crlf"

    auth_in = payload.get("auth") if isinstance(payload.get("auth"), dict) else {}
    auth_current = current.get("auth") if isinstance(current.get("auth"), dict) else {}
    auth_username = clean_text(auth_in.get("username"), clean_text(auth_current.get("username"), ""))
    auth_hash = clean_text(auth_current.get("passwordHash"), "")
    password_raw = auth_in.get("password")
    if isinstance(password_raw, str):
        password_raw = password_raw.strip()
        if password_raw:
            auth_hash = generate_password_hash(password_raw)
    if not auth_username:
        auth_hash = ""

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


def instance_public(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": clean_text(instance.get("id"), "dr154-1"),
        "name": clean_text(instance.get("name"), "dr154-1"),
        "protocolVersion": clean_text(instance.get("protocolVersion"), "1.6"),
        "boards": instance.get("boards", []),
        "mqtt": instance.get("mqtt", {}),
        "auth": instance_auth_meta(instance),
        "updatedAt": instance.get("updatedAt"),
        "controlUrl": instance_control_url(clean_text(instance.get("id"), "dr154-1")),
    }


def instance_publish_payload(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": clean_text(instance.get("id"), "dr154-1"),
        "name": clean_text(instance.get("name"), "dr154-1"),
        "protocolVersion": clean_text(instance.get("protocolVersion"), "1.6"),
        "boards": instance.get("boards", []),
        "mqtt": instance.get("mqtt", {}),
        "updatedAt": instance.get("updatedAt"),
    }


def prune_auth_sessions_locked() -> None:
    now_ts = time.time()
    stale = [token for token, item in AUTH_SESSIONS.items() if float(item.get("expiresAt", 0)) <= now_ts]
    for token in stale:
        AUTH_SESSIONS.pop(token, None)


def issue_instance_token(instance_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    expires_ts = time.time() + INSTANCE_AUTH_TTL_SEC
    with AUTH_LOCK:
        prune_auth_sessions_locked()
        AUTH_SESSIONS[token] = {"instanceId": instance_id, "expiresAt": expires_ts}
    return token, datetime.fromtimestamp(expires_ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def extract_instance_token(body: dict[str, Any] | None = None) -> str:
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
    return ""


def require_instance_auth(instance: dict[str, Any], instance_id: str, body: dict[str, Any] | None = None):
    if not instance_has_auth(instance):
        return None

    token = extract_instance_token(body)
    if not token:
        return err("Autenticazione richiesta", 401)

    with AUTH_LOCK:
        prune_auth_sessions_locked()
        session = AUTH_SESSIONS.get(token)
        if not isinstance(session, dict):
            return err("Sessione non valida", 401)
        if clean_text(session.get("instanceId"), "") != instance_id:
            return err("Sessione non valida per questa istanza", 401)
        expires_at = float(session.get("expiresAt", 0))
        if expires_at <= time.time():
            AUTH_SESSIONS.pop(token, None)
            return err("Sessione scaduta", 401)

    return None


def revoke_instance_token(token: str) -> None:
    if not token:
        return
    with AUTH_LOCK:
        AUTH_SESSIONS.pop(token, None)


def migrate_instance_tokens(old_id: str, new_id: str) -> None:
    if old_id == new_id:
        return
    with AUTH_LOCK:
        prune_auth_sessions_locked()
        for item in AUTH_SESSIONS.values():
            if clean_text(item.get("instanceId"), "") == old_id:
                item["instanceId"] = new_id


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


def decode_poll_output_mask(frame: dict[str, Any]) -> int | None:
    if to_int(frame.get("command"), -1) != 0x40:
        return None
    g = frame.get("g")
    if not isinstance(g, list) or len(g) < 2:
        return None
    return clamp(to_int(g[1], 0), 0, 255)


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
    return previous


def list_view(instance: dict[str, Any]) -> dict[str, Any]:
    instance_id = clean_text(instance.get("id"), "dr154-1")
    return {
        "id": instance_id,
        "name": clean_text(instance.get("name"), instance_id),
        "protocolVersion": clean_text(instance.get("protocolVersion"), "1.6"),
        "boardsCount": len(instance.get("boards", [])),
        "authRequired": instance_has_auth(instance),
        "controlUrl": instance_control_url(instance_id),
        "updatedAt": instance.get("updatedAt"),
    }


def err(message: str, status: int = 400):
    return jsonify({"error": message}), status


@app.get("/")
def root():
    return send_from_directory(app.static_folder, "config.html")


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
            "mqttRequireResponse": MQTT_REQUIRE_RESPONSE,
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

    with AUTH_LOCK:
        prune_auth_sessions_locked()
        stale = [token for token, sess in AUTH_SESSIONS.items() if clean_text(sess.get("instanceId"), "") == current_id]
        for token in stale:
            AUTH_SESSIONS.pop(token, None)

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
    return jsonify({"ok": True, "token": token, "expiresAt": expires_at})


@app.post("/api/instances/<instance_id>/auth/logout")
def api_instance_auth_logout(instance_id: str):
    _ = instance_id
    body = request.get_json(silent=True) or {}
    token = extract_instance_token(body)
    revoke_instance_token(token)
    return jsonify({"ok": True})


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

    lights = light_entities(instance)
    with LIGHT_STATE_LOCK:
        light_state = load_light_state()
    by_instance = light_state.get("instances", {}).get(instance_id_real, {})
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
            "instanceId": instance_id_real,
            "commandTopic": get_light_command_topic(instance),
            "responseTopic": get_light_response_topic(instance),
            "payloadFormat": get_light_payload_format(instance),
            "lights": lights,
        }
    )


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

    topic = clean_text(body.get("topic"), get_light_command_topic(instance))
    if not topic:
        return err("Topic MQTT non valido", 400)
    response_topic_raw = body.get("responseTopic")
    if isinstance(response_topic_raw, str):
        response_topic = response_topic_raw.strip()
    else:
        response_topic = get_light_response_topic(instance)
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
    instance_state = instances_map.setdefault(instance_id_real, {})
    if not isinstance(instance_state, dict):
        instance_state = {}
        instances_map[instance_id_real] = instance_state

    sent: list[dict[str, Any]] = []
    for entity in targets:
        try:
            payload, frame_hex = light_payload_for_target(
                instance_id=instance_id_real,
                target=entity,
                action=action,
                payload_format=payload_format,
            )
        except ValueError as exc:
            return err(str(exc), 400)
        send_count = 1
        if payload_format.startswith("frame_") and action in {"on", "off"}:
            send_count = MQTT_COMMAND_REPEAT_ONOFF
        try:
            for idx in range(send_count):
                mqtt_publish(
                    topic,
                    payload,
                    qos=MQTT_COMMAND_QOS,
                    retain=False,
                    retries=MQTT_COMMAND_RETRIES,
                    retry_delay_ms=MQTT_COMMAND_RETRY_DELAY_MS,
                )
                if idx < (send_count - 1) and MQTT_COMMAND_REPEAT_GAP_MS > 0:
                    time.sleep(MQTT_COMMAND_REPEAT_GAP_MS / 1000.0)
        except Exception as exc:  # noqa: BLE001
            return err(f"Errore pubblicazione comando luce: {exc}", 502)
        previous_state = instance_state.get(entity["id"]) if isinstance(instance_state.get(entity["id"]), dict) else {}
        prev_on = previous_state.get("isOn") if isinstance(previous_state, dict) else None
        next_on = desired_light_state(action, prev_on if isinstance(prev_on, bool) else None)
        verification = poll_light_state_via_mqtt(
            command_topic=topic,
            response_topic=response_topic,
            payload_format=payload_format,
            address=clamp(to_int(entity.get("address"), 0), 0, 254),
            channel=clamp(to_int(entity.get("channel"), 1), 1, 8),
        )
        if isinstance(verification.get("isOn"), bool):
            next_on = bool(verification["isOn"])
        if MQTT_REQUIRE_RESPONSE and not bool(verification.get("verified")):
            reason = clean_text(verification.get("reason"), "nessuna risposta")
            return err(f"Nessuna conferma dal dispositivo per {entity['id']}: {reason}", 502)
        state_updated_at = now_iso()
        instance_state[entity["id"]] = {
            "isOn": bool(next_on) if next_on is not None else None,
            "updatedAt": state_updated_at,
            "source": "poll" if bool(verification.get("verified")) else "command",
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
        item["isOn"] = instance_state[entity["id"]]["isOn"]
        sent.append(item)

    with LIGHT_STATE_LOCK:
        save_light_state(light_state)

    return jsonify(
        {
            "ok": True,
            "topic": topic,
            "responseTopic": response_topic,
            "payloadFormat": payload_format,
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
                "requireResponse": MQTT_REQUIRE_RESPONSE,
            },
            "sent": sent,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
