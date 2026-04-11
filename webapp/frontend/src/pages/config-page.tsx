import { type FormEvent, useEffect, useState } from "react"
import { Link, useLocation, useParams } from "react-router-dom"
import { Copy, ExternalLink, Plus, RefreshCw, Save, Trash2 } from "lucide-react"

import { AppShell } from "@/components/app-shell"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { apiJson, ApiError, configTokenConfig } from "@/lib/api"
import {
  associatedDevicesFromBoards,
  DEFAULT_DEVICE_TYPE,
  defaultBoardForDevice,
  defaultDeviceBaseTopic,
  deviceTypeMeta,
  DEVICE_TYPE_META,
  deviceUsesManualBoards,
  KIND_META,
  normalizeBoard,
  normalizeDeviceType,
  topicsFromBaseTopic,
} from "@/lib/device"
import { cleanText, clamp, slugify, toInt } from "@/lib/utils"
import type {
  Board,
  ConfigAuthLoginResponse,
  ConfigAuthResponse,
  ConfigInstanceListItem,
  ConfigMetaResponse,
  DeviceType,
  DeviceTypePublic,
  InstanceMqtt,
  InstancePublic,
} from "@/lib/types"

type EditorInstance = {
  id: string
  name: string
  deviceType: DeviceType
  protocolVersion: string
  mqtt: InstanceMqtt
  boards: Board[]
  auth: {
    username: string
    password: string
    clearPassword: boolean
  }
}

type NoteState = {
  text: string
  error: boolean
}

type ConfigInstancesResponse = {
  instances?: ConfigInstanceListItem[]
}

type ConfigInstanceResponse = {
  instance: InstancePublic
}

type ConfigPublishResponse = {
  topic?: string
  instance?: InstancePublic
  autoconfig?: {
    ok?: boolean
    topic?: string
    devicesCount?: number
    error?: string
  } | null
}

function configTokenKey() {
  return "sheltr-config-token"
}

function cloneValue<T>(value: T): T {
  if (typeof structuredClone === "function") {
    return structuredClone(value)
  }
  return JSON.parse(JSON.stringify(value)) as T
}

function forcedInstanceFromLocation(pathname: string, search: string) {
  const parts = pathname.split("/").filter(Boolean)
  if (parts[0] === "instance" && parts[1]) {
    return decodeURIComponent(parts[1])
  }
  const params = new URLSearchParams(search)
  return params.get("instance") ?? ""
}

function controlUrl(instanceId: string) {
  return `/control/${encodeURIComponent(instanceId)}`
}

function normalizeDeviceTypes(meta?: Record<string, DeviceTypePublic>) {
  const merged: Record<string, DeviceTypePublic> = { ...DEVICE_TYPE_META }
  for (const [key, value] of Object.entries(meta ?? {})) {
    merged[key] = { ...(merged[key] ?? {}), ...(value ?? {}) }
  }
  return merged
}

function editorFromInstance(instance: InstancePublic, mqttBaseTopic: string): EditorInstance {
  const deviceType = normalizeDeviceType(instance.deviceType)
  const defaultBaseTopic = defaultDeviceBaseTopic(instance.id, deviceType, mqttBaseTopic)
  const derived = topicsFromBaseTopic(defaultBaseTopic, deviceType)
  const boards = deviceUsesManualBoards(deviceType)
    ? (Array.isArray(instance.boards) ? instance.boards : []).map((board, index) => normalizeBoard(board, index))
    : cloneValue(Array.isArray(instance.boards) ? instance.boards : [])

  return {
    id: cleanText(instance.id, "dr154-1"),
    name: cleanText(instance.name, instance.id),
    deviceType,
    protocolVersion: cleanText(instance.protocolVersion, "1.6"),
    mqtt: {
      baseTopic: derived.baseTopic,
      configTopic: derived.configTopic,
      lightCommandTopic: derived.lightCommandTopic,
      lightResponseTopic: derived.lightResponseTopic,
      lightPayloadFormat: cleanText(deviceTypeMeta(deviceType).defaultPayloadFormat, "frame_hex_space_crlf"),
    },
    boards,
    auth: {
      username: cleanText(instance.auth?.username, ""),
      password: "",
      clearPassword: false,
    },
  }
}

function applyDerivedTransport(editor: EditorInstance, mqttBaseTopic: string) {
  const instanceId = slugify(editor.id, "dr154-1")
  const baseTopic = defaultDeviceBaseTopic(instanceId, editor.deviceType, mqttBaseTopic)
  const derived = topicsFromBaseTopic(baseTopic, editor.deviceType)
  return {
    ...editor,
    id: instanceId,
    mqtt: {
      ...editor.mqtt,
      baseTopic: derived.baseTopic,
      configTopic: derived.configTopic,
      lightCommandTopic: derived.lightCommandTopic,
      lightResponseTopic: derived.lightResponseTopic,
      lightPayloadFormat: cleanText(deviceTypeMeta(editor.deviceType).defaultPayloadFormat, "frame_hex_space_crlf"),
    },
  }
}

function applyDevicePreset(editor: EditorInstance, nextType: DeviceType, mqttBaseTopic: string) {
  const normalized = normalizeDeviceType(nextType)
  const instanceId = slugify(editor.id, "dr154-1")
  const baseTopic = defaultDeviceBaseTopic(instanceId, normalized, mqttBaseTopic)
  const derived = topicsFromBaseTopic(baseTopic, normalized)
  return {
    ...editor,
    id: instanceId,
    deviceType: normalized,
    mqtt: {
      ...editor.mqtt,
      baseTopic: derived.baseTopic,
      configTopic: derived.configTopic,
      lightCommandTopic: derived.lightCommandTopic,
      lightResponseTopic: derived.lightResponseTopic,
      lightPayloadFormat: cleanText(deviceTypeMeta(normalized).defaultPayloadFormat, "frame_hex_space_crlf"),
    },
    boards: deviceUsesManualBoards(normalized) ? [defaultBoardForDevice(normalized, 0)] : [],
  }
}

function payloadFromEditor(editor: EditorInstance, mqttBaseTopic: string) {
  const currentId = slugify(editor.id, "dr154-1")
  const deviceType = normalizeDeviceType(editor.deviceType)
  const derived = topicsFromBaseTopic(defaultDeviceBaseTopic(currentId, deviceType, mqttBaseTopic), deviceType)
  const boards = deviceUsesManualBoards(deviceType)
    ? editor.boards.map((board, index) => normalizeBoard(board, index))
    : cloneValue(editor.boards)

  return {
    id: currentId,
    name: cleanText(editor.name, currentId),
    deviceType,
    protocolVersion: "1.6",
    mqtt: {
      baseTopic: derived.baseTopic,
      configTopic: derived.configTopic,
      lightCommandTopic: derived.lightCommandTopic,
      lightResponseTopic: derived.lightResponseTopic,
      lightPayloadFormat: cleanText(deviceTypeMeta(deviceType).defaultPayloadFormat, "frame_hex_space_crlf"),
    },
    auth: {
      username: cleanText(editor.auth.username, ""),
      password: cleanText(editor.auth.password, ""),
      clearPassword: Boolean(editor.auth.clearPassword),
    },
    boards,
  }
}

function transportHint(editor: EditorInstance, mqttBaseTopic: string, deviceTypes: Record<string, DeviceTypePublic>) {
  const deviceType = normalizeDeviceType(editor.deviceType)
  const meta = deviceTypes[deviceType] ?? deviceTypeMeta(deviceType)
  const derived = topicsFromBaseTopic(defaultDeviceBaseTopic(editor.id, deviceType, mqttBaseTopic), deviceType)

  const parts = [
    cleanText(meta.description, ""),
    `Modulo: ${cleanText(meta.module, "-")}`,
    deviceType === "sheltr_mini"
      ? `Cloud standard: ${derived.configTopic || "-"} / ${derived.lightCommandTopic || "-"} / ${derived.lightResponseTopic || "-"}`
      : `Topic DR154 standard: subscribe ${derived.lightCommandTopic || "-"} • publish ${derived.lightResponseTopic || "-"} • config ${derived.configTopic || "-"}`,
  ]

  return parts.filter(Boolean).join(" • ")
}

function formatUpdatedAt(value?: string) {
  if (!value) {
    return ""
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("it-IT")
}

function BoardEditor({
  board,
  boardIndex,
  onChange,
  onRemove,
}: {
  board: Board
  boardIndex: number
  onChange: (nextBoard: Board) => void
  onRemove: () => void
}) {
  const maxChannels = KIND_META[board.kind]?.maxChannels ?? 8

  function updateBoard(patch: Partial<Board>) {
    onChange({ ...board, ...patch })
  }

  function updateChannel(index: number, patch: Partial<Board["channels"][number]>) {
    const channels = board.channels.map((channel, channelIndex) =>
      channelIndex === index ? { ...channel, ...patch } : channel
    )
    onChange({ ...board, channels })
  }

  return (
    <Card className="border-border/70 shadow-none">
      <CardHeader className="pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Scheda {boardIndex + 1}</CardTitle>
            <CardDescription>
              {KIND_META[board.kind]?.label ?? board.kind} • indirizzo {board.address}
            </CardDescription>
          </div>
          <Button type="button" variant="destructive" size="sm" onClick={onRemove}>
            <Trash2 className="size-4" />
            Rimuovi
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <div className="space-y-2">
            <Label>ID scheda</Label>
            <Input
              value={board.id}
              onChange={(event) => updateBoard({ id: slugify(event.target.value, `board-${boardIndex + 1}`) })}
            />
          </div>
          <div className="space-y-2">
            <Label>Nome scheda</Label>
            <Input value={board.name} onChange={(event) => updateBoard({ name: cleanText(event.target.value, board.id) })} />
          </div>
          <div className="space-y-2">
            <Label>Tipo</Label>
            <Select
              value={board.kind}
              onValueChange={(value) => onChange(normalizeBoard({ ...board, kind: value }, boardIndex))}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(KIND_META).map(([value, meta]) => (
                  <SelectItem key={value} value={value}>
                    {meta.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Indirizzo</Label>
            <Input
              type="number"
              min={0}
              max={254}
              value={board.address}
              onChange={(event) => updateBoard({ address: clamp(toInt(event.target.value, board.address), 0, 254) })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>Canale da</Label>
              <Input
                type="number"
                min={1}
                max={maxChannels}
                value={board.channelStart}
                onChange={(event) =>
                  onChange(normalizeBoard({ ...board, channelStart: clamp(toInt(event.target.value, board.channelStart), 1, maxChannels) }, boardIndex))
                }
              />
            </div>
            <div className="space-y-2">
              <Label>Canale a</Label>
              <Input
                type="number"
                min={1}
                max={maxChannels}
                value={board.channelEnd}
                onChange={(event) =>
                  onChange(normalizeBoard({ ...board, channelEnd: clamp(toInt(event.target.value, board.channelEnd), 1, maxChannels) }, boardIndex))
                }
              />
            </div>
          </div>
        </div>

        <Separator />

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-sm font-semibold">Canali</h4>
              <p className="text-sm text-muted-foreground">Imposta nome e stanza per ogni canale attivo della scheda.</p>
            </div>
            <Badge variant="outline">{board.channels.length} canali</Badge>
          </div>
          <div className="space-y-3">
            {board.channels.map((channel, channelIndex) => (
              <div key={`${board.id}-${channel.channel}`} className="grid gap-3 rounded-2xl border p-4 md:grid-cols-[80px_1fr_1fr]">
                <div className="flex items-center">
                  <Badge variant="secondary">C{channel.channel}</Badge>
                </div>
                <div className="space-y-2">
                  <Label>Nome</Label>
                  <Input
                    value={channel.name}
                    onChange={(event) =>
                      updateChannel(channelIndex, {
                        name: cleanText(event.target.value, `${KIND_META[board.kind]?.prefix ?? "Canale"} ${channel.channel}`),
                      })
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label>Stanza</Label>
                  <Input
                    value={channel.room}
                    onChange={(event) => updateChannel(channelIndex, { room: cleanText(event.target.value, "Senza stanza") })}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export function ConfigPage() {
  const params = useParams()
  const location = useLocation()

  const [instances, setInstances] = useState<ConfigInstanceListItem[]>([])
  const [editor, setEditor] = useState<EditorInstance | null>(null)
  const [currentId, setCurrentId] = useState("")
  const [mqttBaseTopic, setMqttBaseTopic] = useState("dr154")
  const [deviceTypes, setDeviceTypes] = useState<Record<string, DeviceTypePublic>>({ ...DEVICE_TYPE_META })
  const [defaultDeviceType, setDefaultDeviceType] = useState<DeviceType>(DEFAULT_DEVICE_TYPE)
  const [configAuthRequired, setConfigAuthRequired] = useState(false)
  const [configToken, setConfigToken] = useState("")
  const [configUser, setConfigUser] = useState("")
  const [configPass, setConfigPass] = useState("")
  const [newId, setNewId] = useState("")
  const [newName, setNewName] = useState("")
  const [newDeviceType, setNewDeviceType] = useState<DeviceType>(DEFAULT_DEVICE_TYPE)
  const [loading, setLoading] = useState(true)
  const [note, setNote] = useState<NoteState>({ text: "", error: false })

  function showNote(text: string, error = false) {
    setNote({ text, error })
  }

  function handleConfigAuthError(error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      setConfigToken("")
      localStorage.removeItem(configTokenKey())
      setEditor(null)
      setCurrentId("")
      showNote("Sessione configurazione scaduta o non valida. Effettua il login config.", true)
      return true
    }
    return false
  }

  async function loadConfigMeta(tokenValue: string) {
    const meta = await apiJson<ConfigMetaResponse>("/api/config/meta", {
      tokenConfig: configTokenConfig(tokenValue),
    })
    const resolvedMqttBaseTopic = cleanText(meta.mqttBaseTopic, "dr154")
    setMqttBaseTopic(resolvedMqttBaseTopic)
    setDeviceTypes(normalizeDeviceTypes(meta.deviceTypes))
    const resolvedDefaultDeviceType = normalizeDeviceType(meta.defaultDeviceType || DEFAULT_DEVICE_TYPE)
    setDefaultDeviceType(resolvedDefaultDeviceType)
    setNewDeviceType(resolvedDefaultDeviceType)
    return resolvedMqttBaseTopic
  }

  async function loadInstance(instanceId: string, tokenValue = configToken, mqttBaseTopicValue = mqttBaseTopic) {
    const id = slugify(instanceId, "dr154-1")
    const response = await apiJson<ConfigInstanceResponse>(`/api/config/instances/${encodeURIComponent(id)}`, {
      tokenConfig: configTokenConfig(tokenValue),
    })
    setCurrentId(response.instance.id)
    setEditor(editorFromInstance(response.instance, mqttBaseTopicValue))
  }

  async function loadInstances(preferredId = "", tokenValue = configToken, mqttBaseTopicValue = mqttBaseTopic) {
    const response = await apiJson<ConfigInstancesResponse>("/api/config/instances", {
      tokenConfig: configTokenConfig(tokenValue),
    })
    const items = Array.isArray(response.instances) ? response.instances : []
    setInstances(items)

    const selected = preferredId || currentId
    if (selected && items.some((item) => item.id === selected)) {
      await loadInstance(selected, tokenValue, mqttBaseTopicValue)
      return
    }

    setCurrentId("")
    setEditor(null)
  }

  useEffect(() => {
    let cancelled = false
    const preferredId = slugify(params.instanceId || forcedInstanceFromLocation(location.pathname, location.search), "")

    async function bootstrap() {
      setLoading(true)
      try {
        const authMeta = await apiJson<ConfigAuthResponse>("/api/config/auth")
        if (cancelled) return

        const authRequired = Boolean(authMeta.required)
        const storedToken = cleanText(localStorage.getItem(configTokenKey()), "")
        setConfigAuthRequired(authRequired)
        setConfigUser(cleanText(authMeta.username, ""))
        setConfigToken(storedToken)

        if (authRequired && !storedToken) {
          setMqttBaseTopic("dr154")
          setDeviceTypes({ ...DEVICE_TYPE_META })
          setDefaultDeviceType(DEFAULT_DEVICE_TYPE)
          setNewDeviceType(DEFAULT_DEVICE_TYPE)
          setEditor(null)
          setCurrentId("")
          showNote("Login configurazione richiesto.")
          return
        }

        const baseTopicValue = await loadConfigMeta(authRequired ? storedToken : "")
        if (cancelled) return

        await loadInstances(preferredId, authRequired ? storedToken : "", baseTopicValue)
        if (!cancelled) {
          showNote("Pronto.")
        }
      } catch (caught) {
        if (cancelled) return
        if (!handleConfigAuthError(caught)) {
          showNote(caught instanceof Error ? caught.message : "Errore caricamento configurazione", true)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void bootstrap()

    return () => {
      cancelled = true
    }
  }, [location.pathname, location.search, params.instanceId])

  async function copyText(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      return false
    }
  }

  async function createInstance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      const id = slugify(newId, "dr154-1")
      const name = cleanText(newName, id)
      const deviceType = normalizeDeviceType(newDeviceType)
      await apiJson<ConfigInstanceResponse>("/api/config/instances", {
        method: "POST",
        body: {
          id,
          name,
          deviceType,
          applyDeviceDefaults: true,
          auth: { username: "", password: "" },
          boards: [],
        },
        tokenConfig: configTokenConfig(configToken),
      })
      setNewId("")
      setNewName("")
      setNewDeviceType(defaultDeviceType)
      showNote(`Istanza '${id}' creata.`)
      await loadInstances(id)
    } catch (caught) {
      if (!handleConfigAuthError(caught)) {
        showNote(caught instanceof Error ? caught.message : "Creazione istanza non riuscita", true)
      }
    }
  }

  async function saveCurrent(silent = false) {
    if (!editor || !currentId) {
      return null
    }
    const payload = payloadFromEditor(editor, mqttBaseTopic)
    const response = await apiJson<ConfigInstanceResponse>(`/api/config/instances/${encodeURIComponent(currentId)}`, {
      method: "PUT",
      body: payload,
      tokenConfig: configTokenConfig(configToken),
    })
    setCurrentId(response.instance.id)
    setEditor(editorFromInstance(response.instance, mqttBaseTopic))
    await loadInstances(response.instance.id)
    if (!silent) {
      showNote("Configurazione salvata.")
    }
    return response.instance
  }

  async function publishCurrent() {
    if (!editor) {
      return
    }
    try {
      const saved = await saveCurrent(true)
      if (!saved) {
        return
      }
      const response = await apiJson<ConfigPublishResponse>(
        `/api/config/instances/${encodeURIComponent(saved.id)}/publish`,
        {
          method: "POST",
          body: {},
          tokenConfig: configTokenConfig(configToken),
        }
      )
      if (response.instance) {
        setCurrentId(response.instance.id)
        setEditor(editorFromInstance(response.instance, mqttBaseTopic))
        await loadInstances(response.instance.id)
      }
      const autoconfig = response.autoconfig && typeof response.autoconfig === "object" ? response.autoconfig : null
      const isMini = normalizeDeviceType(saved.deviceType) === "sheltr_mini"
      if (autoconfig?.ok) {
        showNote(
          isMini
            ? `Sheltr Mini sincronizzato da '${autoconfig.topic || response.topic}'. Rilevati ${autoconfig.devicesCount || 0} dispositivi.`
            : `Configurazione pubblicata su '${response.topic}'. Autoconfigurazione completata: ${autoconfig.devicesCount || 0} dispositivi rilevati.`
        )
        return
      }
      if (isMini) {
        showNote(
          `Sheltr Mini non sincronizzato da '${autoconfig?.topic || response.topic}': ${cleanText(autoconfig?.error, "nessun payload dispositivi ricevuto")}.`,
          true
        )
        return
      }
      showNote(`Configurazione pubblicata su '${response.topic}'.`)
    } catch (caught) {
      if (!handleConfigAuthError(caught)) {
        showNote(caught instanceof Error ? caught.message : "Pubblicazione non riuscita", true)
      }
    }
  }

  async function deleteCurrent() {
    if (!editor || !currentId) {
      return
    }
    if (!window.confirm(`Eliminare istanza '${currentId}'?`)) {
      return
    }
    try {
      await apiJson<{ ok?: boolean }>(`/api/config/instances/${encodeURIComponent(currentId)}`, {
        method: "DELETE",
        tokenConfig: configTokenConfig(configToken),
      })
      showNote(`Istanza '${currentId}' eliminata.`)
      setEditor(null)
      setCurrentId("")
      await loadInstances("")
    } catch (caught) {
      if (!handleConfigAuthError(caught)) {
        showNote(caught instanceof Error ? caught.message : "Eliminazione non riuscita", true)
      }
    }
  }

  async function configLogin() {
    try {
      const response = await apiJson<ConfigAuthLoginResponse>("/api/config/auth/login", {
        method: "POST",
        body: {
          username: cleanText(configUser, ""),
          password: cleanText(configPass, ""),
        },
      })
      const token = cleanText(response.token, "")
      if (configAuthRequired && !token) {
        throw new Error("Token configurazione non valido")
      }
      setConfigToken(token)
      if (token) {
        localStorage.setItem(configTokenKey(), token)
      }
      setConfigPass("")
      const baseTopicValue = await loadConfigMeta(token)
      await loadInstances(
        slugify(params.instanceId || forcedInstanceFromLocation(location.pathname, location.search), ""),
        token,
        baseTopicValue
      )
      showNote("Login configurazione eseguito.")
    } catch (caught) {
      if (!handleConfigAuthError(caught)) {
        showNote(caught instanceof Error ? caught.message : "Login configurazione non riuscito", true)
      }
    }
  }

  async function configLogout() {
    try {
      await apiJson<{ ok?: boolean }>("/api/config/auth/logout", {
        method: "POST",
        tokenConfig: configTokenConfig(configToken),
      })
    } catch {
      // Ignore logout failures and clear client state anyway.
    }
    setConfigToken("")
    localStorage.removeItem(configTokenKey())
    setInstances([])
    setEditor(null)
    setCurrentId("")
    showNote("Logout configurazione eseguito.")
  }

  function updateEditor(next: EditorInstance | null) {
    setEditor(next)
  }

  const configAllowed = !configAuthRequired || Boolean(configToken)
  const isMini = editor?.deviceType === "sheltr_mini"
  const deviceHint = editor ? transportHint(editor, mqttBaseTopic, deviceTypes) : ""
  const associatedDevices = editor ? associatedDevicesFromBoards(editor.boards) : []

  return (
    <AppShell
      title="Configurazione"
      description="Crea e modifica le istanze Sheltr Cloud con preset dispositivo, autenticazione dedicata e publish/sync verso i moduli registrati."
      actions={
        configAuthRequired && configAllowed ? (
          <Button variant="outline" size="sm" className="rounded-full" onClick={configLogout}>
            Logout config
          </Button>
        ) : null
      }
    >
      {!configAllowed ? (
        <Card className="mx-auto w-full max-w-xl border-border/80 bg-background/90">
          <CardHeader>
            <CardTitle>Login configurazione</CardTitle>
            <CardDescription>
              Usa le credenziali impostate in `.env` tramite `CONFIG_AUTH_USERNAME` e `CONFIG_AUTH_PASSWORD`.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Username config</Label>
              <Input value={configUser} onChange={(event) => setConfigUser(event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Password config</Label>
              <Input
                type="password"
                value={configPass}
                onChange={(event) => setConfigPass(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void configLogin()
                  }
                }}
              />
            </div>
            <Button onClick={configLogin}>Accedi</Button>
          </CardContent>
        </Card>
      ) : (
        <section className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
          <div className="space-y-6">
            <Card className="border-border/80 bg-background/90">
              <CardHeader>
                <CardTitle>Istanze</CardTitle>
                <CardDescription>Crea una nuova istanza e apri subito il link controllo dedicato.</CardDescription>
              </CardHeader>
              <CardContent>
                <form className="space-y-4" onSubmit={createInstance}>
                  <div className="space-y-2">
                    <Label>ID istanza</Label>
                    <Input value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="es. casa-demo" />
                  </div>
                  <div className="space-y-2">
                    <Label>Nome istanza</Label>
                    <Input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="es. Casa Demo" />
                  </div>
                  <div className="space-y-2">
                    <Label>Tipo dispositivo</Label>
                    <Select value={newDeviceType} onValueChange={(value) => setNewDeviceType(normalizeDeviceType(value))}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(deviceTypes).map(([value, meta]) => (
                          <SelectItem key={value} value={value}>
                            {cleanText(meta.label, value)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button type="submit" className="w-full">
                    <Plus className="size-4" />
                    Crea istanza
                  </Button>
                </form>
              </CardContent>
            </Card>

            <Card className="border-border/80 bg-background/90">
              <CardHeader>
                <CardTitle>Istanze esistenti</CardTitle>
                <CardDescription>
                  {currentId ? `Istanza selezionata: ${currentId}` : "Seleziona una istanza per aprire l’editor."}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ScrollArea className="max-h-[40rem] pr-4">
                  <div className="space-y-3">
                    {!instances.length && !loading ? (
                      <p className="text-sm text-muted-foreground">Nessuna istanza.</p>
                    ) : null}
                    {instances.map((instance) => {
                      const fullUrl = `${window.location.origin}${instance.controlUrl || controlUrl(instance.id)}`
                      const active = instance.id === currentId
                      return (
                        <Card key={instance.id} className="border-border/70 shadow-none">
                          <CardContent className="space-y-4 p-4">
                            <div className="space-y-2">
                              <div className="flex items-center gap-2">
                                <h3 className="font-semibold">{instance.name}</h3>
                                {active ? <Badge>Selezionata</Badge> : null}
                              </div>
                              <p className="text-sm text-muted-foreground">
                                {instance.id} •{" "}
                                {cleanText(instance.deviceLabel, deviceTypes[instance.deviceType]?.label || instance.deviceType)} •{" "}
                                {instance.deviceType === "sheltr_mini" ? "autoconfigurazione" : `${instance.boardsCount} schede`}
                                {instance.authRequired ? " • login" : ""}
                              </p>
                              {instance.updatedAt ? (
                                <p className="text-xs text-muted-foreground">Aggiornata: {formatUpdatedAt(instance.updatedAt)}</p>
                              ) : null}
                            </div>
                            <div className="rounded-2xl border bg-muted/30 p-3 text-xs text-muted-foreground">
                              <p className="truncate">{fullUrl}</p>
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <Button
                                type="button"
                                variant={active ? "secondary" : "outline"}
                                size="sm"
                                onClick={() => {
                                  void loadInstance(instance.id).catch((caught) => {
                                    if (!handleConfigAuthError(caught)) {
                                      showNote(caught instanceof Error ? caught.message : "Caricamento istanza non riuscito", true)
                                    }
                                  })
                                }}
                                disabled={active}
                              >
                                {active ? "Selezionata" : "Seleziona"}
                              </Button>
                              <Button asChild variant="ghost" size="sm">
                                <a href={instance.controlUrl || controlUrl(instance.id)} target="_blank" rel="noreferrer">
                                  <ExternalLink className="size-4" />
                                  Apri
                                </a>
                              </Button>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                  void copyText(fullUrl).then((ok) => {
                                    showNote(ok ? `Link copiato: ${fullUrl}` : "Copia non riuscita", !ok)
                                  })
                                }}
                              >
                                <Copy className="size-4" />
                                Copia
                              </Button>
                            </div>
                          </CardContent>
                        </Card>
                      )
                    })}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
          </div>

          <div className="space-y-6">
            <Card className="border-border/80 bg-background/90">
              <CardHeader>
                <CardTitle>{editor ? `Istanza: ${editor.name}` : "Editor istanza"}</CardTitle>
                <CardDescription>
                  {editor ? "Modifica i parametri dell’istanza e salva prima di pubblicare o sincronizzare." : "Crea o seleziona una istanza."}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {!editor ? (
                  <p className="text-sm text-muted-foreground">Crea o seleziona una istanza per iniziare.</p>
                ) : (
                  <>
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                      <div className="space-y-2">
                        <Label>ID istanza</Label>
                        <Input
                          value={editor.id}
                          onChange={(event) => {
                            const next = { ...editor, id: slugify(event.target.value, editor.id || "dr154-1") }
                            updateEditor(applyDerivedTransport(next, mqttBaseTopic))
                          }}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label>Nome istanza</Label>
                        <Input value={editor.name} onChange={(event) => updateEditor({ ...editor, name: event.target.value })} />
                      </div>
                      <div className="space-y-2">
                        <Label>Tipo dispositivo</Label>
                        <Select
                          value={editor.deviceType}
                          onValueChange={(value) => {
                            const nextType = normalizeDeviceType(value)
                            if (nextType === editor.deviceType) {
                              return
                            }
                            const label = cleanText(deviceTypes[nextType]?.label, nextType)
                            const ok = window.confirm(
                              `Applicare il preset '${label}'? Verranno aggiornati topic MQTT, formato payload e schede dell'istanza corrente.`
                            )
                            if (!ok) {
                              return
                            }
                            updateEditor(applyDevicePreset(editor, nextType, mqttBaseTopic))
                            showNote(`Preset '${label}' applicato. Controlla nomi canali e stanze prima di salvare.`)
                          }}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(deviceTypes).map(([value, meta]) => (
                              <SelectItem key={value} value={value}>
                                {cleanText(meta.label, value)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-2">
                        <Label>Versione protocollo</Label>
                        <Input value="1.6" readOnly />
                      </div>
                      <div className="space-y-2">
                        <Label>{isMini ? "Dispositivi" : "Schede"}</Label>
                        <Input
                          readOnly
                          value={
                            isMini
                              ? associatedDevices.length
                                ? `AUTO (${associatedDevices.length} dispositivi)`
                                : "AUTO"
                              : String(editor.boards.length)
                          }
                        />
                      </div>
                    </div>

                    <Alert>
                      <AlertTitle>{cleanText(deviceTypes[editor.deviceType]?.label, editor.deviceType)}</AlertTitle>
                      <AlertDescription>{deviceHint}</AlertDescription>
                    </Alert>

                    <div className="grid gap-4 md:grid-cols-3">
                      <div className="space-y-2">
                        <Label>Username login controllo</Label>
                        <Input
                          value={editor.auth.username}
                          onChange={(event) =>
                            updateEditor({ ...editor, auth: { ...editor.auth, username: event.target.value } })
                          }
                          placeholder="es. filippo"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label>Password login controllo</Label>
                        <Input
                          value={editor.auth.password}
                          onChange={(event) =>
                            updateEditor({ ...editor, auth: { ...editor.auth, password: event.target.value } })
                          }
                          placeholder="Visibile: nuova password o vuoto"
                        />
                      </div>
                      <div className="flex items-end">
                        <div className="flex w-full items-center justify-between rounded-2xl border p-4">
                          <div className="space-y-1">
                            <Label>Rimuovi password controllo</Label>
                            <p className="text-sm text-muted-foreground">Cancella la password salvata per questa istanza.</p>
                          </div>
                          <Switch
                            checked={editor.auth.clearPassword}
                            onCheckedChange={(checked) =>
                              updateEditor({ ...editor, auth: { ...editor.auth, clearPassword: checked } })
                            }
                          />
                        </div>
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                      {deviceUsesManualBoards(editor.deviceType) ? (
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() =>
                            updateEditor({
                              ...editor,
                              boards: [...editor.boards, normalizeBoard({}, editor.boards.length)],
                            })
                          }
                        >
                          <Plus className="size-4" />
                          Aggiungi scheda
                        </Button>
                      ) : null}
                      <Button
                        type="button"
                        onClick={() => {
                          void saveCurrent(false).catch((caught) => {
                            if (!handleConfigAuthError(caught)) {
                              showNote(caught instanceof Error ? caught.message : "Salvataggio non riuscito", true)
                            }
                          })
                        }}
                      >
                        <Save className="size-4" />
                        Salva
                      </Button>
                      <Button type="button" variant="outline" onClick={publishCurrent}>
                        <RefreshCw className="size-4" />
                        {isMini ? "Sincronizza Sheltr Mini" : "Pubblica su MQTT"}
                      </Button>
                      <Button type="button" variant="destructive" onClick={deleteCurrent}>
                        <Trash2 className="size-4" />
                        Elimina istanza
                      </Button>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            {editor ? (
              isMini ? (
                <Card className="border-border/80 bg-background/90">
                  <CardHeader>
                    <CardTitle>Dispositivi autoconfigurati</CardTitle>
                    <CardDescription>
                      Sheltr Mini deriva topic e payload dall’ID istanza e mostra qui solo i dispositivi rilevati dal modulo.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {associatedDevices.length ? (
                      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                        {associatedDevices.map((device) => (
                          <Card key={device.id} className="border-border/70 shadow-none">
                            <CardContent className="space-y-3 p-4">
                              <div className="flex flex-wrap items-center gap-2">
                                <h3 className="font-semibold">{device.name}</h3>
                                <Badge variant="outline">{KIND_META[device.kind]?.label ?? device.kind}</Badge>
                              </div>
                              <p className="text-sm text-muted-foreground">
                                {device.room} • {device.boardName} • C{device.channel}
                              </p>
                              {device.sourceId ? <p className="text-xs text-muted-foreground">Source: {device.sourceId}</p> : null}
                            </CardContent>
                          </Card>
                        ))}
                      </div>
                    ) : (
                      <Alert>
                        <AlertTitle>Nessun dispositivo sincronizzato</AlertTitle>
                        <AlertDescription>
                          Salva l’istanza, poi usa “Sincronizza Sheltr Mini” per leggere il retained cloud su `{editor.id}/config`.
                        </AlertDescription>
                      </Alert>
                    )}
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-4">
                  {editor.boards.length ? (
                    editor.boards.map((board, boardIndex) => (
                      <BoardEditor
                        key={`${board.id}-${boardIndex}`}
                        board={board}
                        boardIndex={boardIndex}
                        onChange={(nextBoard) =>
                          updateEditor({
                            ...editor,
                            boards: editor.boards.map((boardItem, index) =>
                              index === boardIndex ? normalizeBoard(nextBoard, boardIndex) : boardItem
                            ),
                          })
                        }
                        onRemove={() =>
                          updateEditor({
                            ...editor,
                            boards: editor.boards.filter((_, index) => index !== boardIndex),
                          })
                        }
                      />
                    ))
                  ) : (
                    <Card className="border-border/80 bg-background/90">
                      <CardContent className="p-6 text-sm text-muted-foreground">Nessuna scheda configurata.</CardContent>
                    </Card>
                  )}
                </div>
              )
            ) : null}
          </div>
        </section>
      )}

      {note.text ? (
        <Alert variant={note.error ? "destructive" : "default"}>
          <AlertTitle>{note.error ? "Attenzione" : "Stato"}</AlertTitle>
          <AlertDescription>{note.text}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? <p className="text-sm text-muted-foreground">Caricamento configurazione in corso...</p> : null}

      {editor ? (
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to={controlUrl(editor.id)}>Apri pagina controllo</Link>
          </Button>
        </div>
      ) : null}
    </AppShell>
  )
}
