import { clamp, cleanText, normalizeMode, normalizeTime, slugify, toInt } from "@/lib/utils"
import type {
  AssociatedDevice,
  Board,
  BoardChannel,
  BoardKind,
  DeviceType,
  SwitchProfile,
  SwitchProfileEntry,
  ThermostatProfile,
  ThermostatProfileEntry,
} from "@/lib/types"

export const DOW = [
  { id: 1, label: "LUN" },
  { id: 2, label: "MAR" },
  { id: 3, label: "MER" },
  { id: 4, label: "GIO" },
  { id: 5, label: "VEN" },
  { id: 6, label: "SAB" },
  { id: 7, label: "DOM" },
] as const

export const DOW_ALL = DOW.map((item) => item.id)

export const KIND_META: Record<
  BoardKind,
  { label: string; maxChannels: number; prefix: string }
> = {
  light: { label: "Luci", maxChannels: 8, prefix: "Luce" },
  shutter: { label: "Tapparelle", maxChannels: 4, prefix: "Tapparella" },
  dimmer: { label: "Dimmer", maxChannels: 1, prefix: "Dimmer" },
  thermostat: { label: "Termostati", maxChannels: 1, prefix: "Termostato" },
}

export const DEFAULT_DEVICE_TYPE: DeviceType = "sheltr_4g"

export const DEVICE_TYPE_META: Record<
  DeviceType,
  {
    label: string
    description: string
    module: string
    transport: string
    defaultPayloadFormat: string
    defaultBoard: {
      id: string
      name: string
      kind: BoardKind
      address: number
      channelStart: number
      channelEnd: number
    }
  }
> = {
  sheltr_mini: {
    label: "Sheltr Mini",
    description: "Profilo Sheltr Cloud standard del firmware Sheltr Mini.",
    module: "SHELTR_MINI",
    transport: "mqtt_json",
    defaultPayloadFormat: "frame_hex_space_crlf",
    defaultBoard: {
      id: "board-1",
      name: "Sheltr Mini",
      kind: "light",
      address: 1,
      channelStart: 1,
      channelEnd: 8,
    },
  },
  sheltr_4g: {
    label: "Sheltr 4G / DR154",
    description: "Modulo DR154 con la configurazione attuale del portale.",
    module: "DR154",
    transport: "dr154_protocol_v1_6",
    defaultPayloadFormat: "frame_hex_space_crlf",
    defaultBoard: {
      id: "board-1",
      name: "Scheda Luci",
      kind: "light",
      address: 1,
      channelStart: 1,
      channelEnd: 8,
    },
  },
}

export function normalizeDeviceType(value: unknown, fallback: DeviceType = DEFAULT_DEVICE_TYPE): DeviceType {
  const raw = cleanText(value, fallback).toLowerCase().replace(/[\s-]+/g, "_")
  const aliases: Record<string, DeviceType> = {
    mini: "sheltr_mini",
    sheltrmini: "sheltr_mini",
    sheltr_mini: "sheltr_mini",
    "4g": "sheltr_4g",
    dr154: "sheltr_4g",
    sheltr4g: "sheltr_4g",
    sheltr_4g: "sheltr_4g",
  }
  const normalized = aliases[raw] ?? raw
  return normalized in DEVICE_TYPE_META ? (normalized as DeviceType) : fallback
}

export function deviceTypeMeta(deviceType: unknown) {
  return DEVICE_TYPE_META[normalizeDeviceType(deviceType)]
}

export function deviceUsesManualBoards(deviceType: unknown) {
  return normalizeDeviceType(deviceType) !== "sheltr_mini"
}

export function defaultChannelName(kind: BoardKind, channel: number) {
  return `${KIND_META[kind].prefix} ${channel}`
}

export function cleanTopicPath(value: unknown, fallback = "") {
  const text = cleanText(value, fallback)
  return text ? text.replace(/^\/+|\/+$/g, "") : fallback
}

export function defaultDeviceBaseTopic(instanceId: string, deviceType: DeviceType, mqttBaseTopic = "dr154") {
  void mqttBaseTopic
  return deviceType === "sheltr_mini"
    ? cleanTopicPath(slugify(instanceId, "sheltr-mini"), "sheltr-mini")
    : `/${slugify(instanceId, "dr154-1")}`
}

export function topicsFromBaseTopic(baseTopic: string, deviceType: DeviceType) {
  if (deviceType === "sheltr_mini") {
    const base = cleanTopicPath(baseTopic, "")
    if (!base) {
      return { baseTopic: "", configTopic: "", lightCommandTopic: "", lightResponseTopic: "" }
    }
    return {
      baseTopic: base,
      configTopic: `${base}/config`,
      lightCommandTopic: `${base}/cmd`,
      lightResponseTopic: `${base}/pub`,
    }
  }

  const baseSlug = slugify(cleanText(baseTopic, "").replace(/^\/+|\/+$/g, ""), "")
  if (!baseSlug) {
    return { baseTopic: "", configTopic: "", lightCommandTopic: "", lightResponseTopic: "" }
  }
  const base = `/${baseSlug}`
  return {
    baseTopic: base,
    configTopic: `${base}/config`,
    lightCommandTopic: `${base}/cmd`,
    lightResponseTopic: `${base}/status`,
  }
}

export function normalizeDay(value: unknown) {
  const num = toInt(value, 0)
  if (num >= 1 && num <= 7) return num
  const key = cleanText(value, "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[\s_.-]/g, "")
  const aliases: Record<string, number> = {
    mon: 1,
    monday: 1,
    lun: 1,
    lunedi: 1,
    tue: 2,
    tuesday: 2,
    mar: 2,
    martedi: 2,
    wed: 3,
    wednesday: 3,
    mer: 3,
    mercoledi: 3,
    thu: 4,
    thursday: 4,
    gio: 4,
    giovedi: 4,
    fri: 5,
    friday: 5,
    ven: 5,
    venerdi: 5,
    sat: 6,
    saturday: 6,
    sab: 6,
    sabato: 6,
    sun: 7,
    sunday: 7,
    dom: 7,
    domenica: 7,
  }
  return aliases[key] ?? 0
}

export function normalizeDays(value: unknown) {
  const out = new Set<number>()
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, enabled] of Object.entries(value)) {
      if (!enabled) continue
      const day = normalizeDay(key)
      if (day) out.add(day)
    }
  } else {
    const list = Array.isArray(value) ? value : [value]
    for (const item of list) {
      const day = normalizeDay(item)
      if (day) out.add(day)
    }
  }
  return out.size ? [...out].sort((a, b) => a - b) : [...DOW_ALL]
}

export function patchDays(days: number[], day: number, enabled: boolean) {
  const set = new Set(normalizeDays(days))
  if (enabled) set.add(day)
  else set.delete(day)
  return set.size ? [...set].sort((a, b) => a - b) : [...DOW_ALL]
}

export function normalizeSwitchEntry(kind: "light" | "shutter", raw: unknown): SwitchProfileEntry {
  const input = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const defaultAction = kind === "shutter" ? "down" : "off"
  const actionRaw = cleanText(input.action, defaultAction).toLowerCase()
  const action =
    kind === "shutter"
      ? (actionRaw === "up" ? "up" : "down")
      : (actionRaw === "on" ? "on" : "off")
  return {
    time: normalizeTime(input.time, "00:00"),
    action,
    days: normalizeDays(input.days),
  }
}

export function normalizeSwitchProfile(kind: "light" | "shutter", raw: unknown): SwitchProfile {
  const input = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const entries = Array.isArray(input.entries)
    ? input.entries.map((entry) => normalizeSwitchEntry(kind, entry)).slice(0, 48)
    : []
  if (entries.length) return { enabled: Boolean(input.enabled), entries }
  if ("time" in input || "action" in input || "days" in input) {
    return { enabled: Boolean(input.enabled), entries: [normalizeSwitchEntry(kind, input)] }
  }
  return { enabled: Boolean(input.enabled), entries: [] }
}

export function normalizeThermostatEntry(raw: unknown): ThermostatProfileEntry {
  const input = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const setpointRaw = Number(String(input.setpoint ?? "").replace(",", "."))
  return {
    from: normalizeTime(input.from, "00:00"),
    to: normalizeTime(input.to, "23:59"),
    setpoint: Number.isFinite(setpointRaw) ? clamp(Math.round(setpointRaw * 2) / 2, 5, 30) : 21,
    mode: normalizeMode(input.mode),
    days: normalizeDays(input.days),
  }
}

export function normalizeThermostatProfile(raw: unknown): ThermostatProfile {
  const input = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const entries = Array.isArray(input.entries)
    ? input.entries.map((entry) => normalizeThermostatEntry(entry)).slice(0, 48)
    : []
  return { enabled: Boolean(input.enabled), entries }
}

export function normalizeBoard(raw: unknown, index: number): Board {
  const board = raw && typeof raw === "object" ? (raw as Partial<Board>) : {}
  const kind = cleanText(board.kind, "light") as BoardKind
  const resolvedKind = kind in KIND_META ? kind : "light"
  const max = KIND_META[resolvedKind].maxChannels
  const boardId = slugify(board.id ?? board.name ?? `board-${index + 1}`, `board-${index + 1}`)
  const name = cleanText(board.name, boardId)
  const address = clamp(toInt(board.address, index + 1), 0, 254)
  const channelStart = clamp(toInt(board.channelStart, 1), 1, max)
  const channelEnd = clamp(toInt(board.channelEnd, max), channelStart, max)
  const channelMap = new Map<number, BoardChannel>()
  for (const item of Array.isArray(board.channels) ? board.channels : []) {
    const channel = clamp(toInt(item.channel, -1), 1, max)
    if (channel >= channelStart && channel <= channelEnd) {
      channelMap.set(channel, item)
    }
  }
  const channels: BoardChannel[] = []
  for (let channel = channelStart; channel <= channelEnd; channel += 1) {
    const saved = channelMap.get(channel)
    const entry: BoardChannel = {
      channel,
      name: cleanText(saved?.name, defaultChannelName(resolvedKind, channel)),
      room: cleanText(saved?.room, "Senza stanza"),
    }
    if (resolvedKind === "light" || resolvedKind === "shutter") {
      entry.profile = normalizeSwitchProfile(resolvedKind, saved?.profile)
    } else if (resolvedKind === "thermostat") {
      entry.profile = normalizeThermostatProfile(saved?.profile)
    }
    channels.push(entry)
  }
  return {
    id: boardId,
    name,
    address,
    kind: resolvedKind,
    channelStart,
    channelEnd,
    channels,
  }
}

export function defaultBoardForDevice(deviceType: DeviceType, index = 0) {
  return normalizeBoard(deviceTypeMeta(deviceType).defaultBoard, index)
}

export function channelProfileId(boardId: string, channel: number) {
  return `${cleanText(boardId, "board-1")}-c${Math.trunc(Number(channel) || 0)}`
}

export function associatedDevicesFromBoards(boards: Board[] | undefined | null) {
  const out: AssociatedDevice[] = []
  for (const board of boards ?? []) {
    const kind = board.kind in KIND_META ? board.kind : "light"
    for (const channel of board.channels ?? []) {
      const item: AssociatedDevice = {
        id: channelProfileId(board.id, channel.channel),
        kind,
        boardId: board.id,
        boardName: board.name,
        address: board.address,
        channel: channel.channel,
        name: cleanText(channel.name, defaultChannelName(kind, channel.channel)),
        room: cleanText(channel.room, "Senza stanza"),
      }
      if (channel.sourceId) item.sourceId = channel.sourceId
      if (channel.meta) item.meta = channel.meta
      out.push(item)
    }
  }
  return out.sort((left, right) => {
    const boardCompare = left.boardId.localeCompare(right.boardId, "it", { sensitivity: "base" })
    if (boardCompare !== 0) return boardCompare
    return left.channel - right.channel
  })
}

export function associatedDevicesCount(boards: Board[] | undefined | null) {
  return associatedDevicesFromBoards(boards).length
}
