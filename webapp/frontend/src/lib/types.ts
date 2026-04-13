export type DeviceType = "sheltr_mini" | "sheltr_4g"
export type BoardKind = "light" | "shutter" | "dimmer" | "thermostat"

export type DeviceTypePublic = {
  type?: DeviceType | string
  label?: string
  description?: string
  module?: string
  transport?: string
  defaultPayloadFormat?: string
  supportsFramePolling?: boolean
  defaultBoard?: {
    id: string
    name: string
    kind: BoardKind
    address: number
    channelStart: number
    channelEnd: number
  }
}

export type SwitchProfileEntry = {
  time: string
  action: "on" | "off" | "up" | "down"
  days: number[]
}

export type SwitchProfile = {
  enabled: boolean
  entries: SwitchProfileEntry[]
}

export type ThermostatProfileEntry = {
  from: string
  to: string
  setpoint: number
  mode: "winter" | "summer"
  days: number[]
}

export type ThermostatProfile = {
  enabled: boolean
  entries: ThermostatProfileEntry[]
}

export type BoardChannel = {
  channel: number
  name: string
  room: string
  sourceId?: string
  meta?: Record<string, unknown>
  profile?: SwitchProfile | ThermostatProfile | Record<string, unknown>
}

export type Board = {
  id: string
  name: string
  address: number
  kind: BoardKind
  channelStart: number
  channelEnd: number
  channels: BoardChannel[]
}

export type InstanceAuthMeta = {
  username: string
  passwordConfigured: boolean
}

export type InstanceMqtt = {
  baseTopic?: string
  configTopic?: string
  lightCommandTopic?: string
  lightResponseTopic?: string
  lightPayloadFormat?: string
}

export type InstancePublic = {
  id: string
  name: string
  deviceType: DeviceType
  device?: DeviceTypePublic
  protocolVersion: string
  boards: Board[]
  mqtt: InstanceMqtt
  auth?: InstanceAuthMeta
  updatedAt?: string
  controlUrl?: string
}

export type ConfigInstanceListItem = {
  id: string
  name: string
  deviceType: DeviceType
  deviceLabel?: string
  boardsCount: number
  authRequired: boolean
  assignedUsersCount?: number
  controlUrl?: string
  updatedAt?: string
}

export type ConfigUserRole = "admin" | "user"

export type ConfigUser = {
  id: string
  username: string
  email: string
  role: ConfigUserRole
  instanceIds: string[]
  instancesCount?: number
  isDefaultAdmin?: boolean
  updatedAt?: string
}

export type StatusLight = {
  id: string
  name: string
  isOn?: boolean | null
}

export type StatusDimmer = {
  id: string
  name: string
  level?: number
  isOn?: boolean | null
}

export type StatusShutter = {
  id: string
  name: string
  action?: string
}

export type StatusThermostat = {
  id: string
  name: string
  temperature?: number | null
  setpoint?: number
  mode?: string
  isOn?: boolean | null
  isActive?: boolean | null
}

export type StatusRoom = {
  name: string
  lights: StatusLight[]
  dimmers: StatusDimmer[]
  shutters: StatusShutter[]
  thermostats: StatusThermostat[]
}

export type AutoconfigResult = {
  ok?: boolean
  topic?: string
  devicesCount?: number
  error?: string
}

export type InstanceStatus = {
  instanceId: string
  updatedAt?: string
  refreshErrors?: Array<{ address: number; error: string }>
  commandTopic?: string
  responseTopic?: string
  payloadFormat?: string
  rooms: StatusRoom[]
  boards: Board[]
  autoconfig?: AutoconfigResult | null
}

export type AssociatedDevice = {
  id: string
  kind: BoardKind
  boardId: string
  boardName: string
  address: number
  channel: number
  name: string
  room: string
  sourceId?: string
  meta?: Record<string, unknown>
}

export type ConfigMetaResponse = {
  mqttBaseTopic?: string
  defaultDeviceType?: DeviceType | string
  deviceTypes?: Record<string, DeviceTypePublic>
}

export type ConfigAuthResponse = {
  required?: boolean
  username?: string
}

export type InstanceAuthLoginResponse = {
  ok?: boolean
  token?: string
  expiresAt?: string | null
}

export type ConfigAuthLoginResponse = InstanceAuthLoginResponse & {
  required?: boolean
}

export type ConfigUserResponse = {
  user: ConfigUser
}

export type ConfigUsersResponse = {
  users?: ConfigUser[]
}

export type CommandSentItem = {
  id?: string
  verified?: boolean
  verifyReason?: string
  isOn?: boolean
  isActive?: boolean
  level?: number
  action?: string
  setpoint?: number
  mode?: string
  temperature?: number
}

export type CommandResponse = {
  ok?: boolean
  topic?: string
  responseTopic?: string
  payloadFormat?: string
  sent?: CommandSentItem[]
}
