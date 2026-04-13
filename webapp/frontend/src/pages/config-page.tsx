import { type FormEvent, useEffect, useState } from "react"
import { Link, useLocation, useNavigate, useParams } from "react-router-dom"
import { Copy, ExternalLink, LayoutList, LogOut, Plus, RefreshCw, Save, Settings2, Trash2 } from "lucide-react"

import { AppShell } from "@/components/app-shell"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarSeparator,
  SidebarTrigger,
} from "@/components/ui/sidebar"
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

function instanceConfigUrl(instanceId: string) {
  return `/instance/${encodeURIComponent(instanceId)}/config`
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

function transportFromEditor(editor: EditorInstance, mqttBaseTopic: string) {
  return topicsFromBaseTopic(defaultDeviceBaseTopic(editor.id, editor.deviceType, mqttBaseTopic), editor.deviceType)
}

function formatUpdatedAt(value?: string) {
  if (!value) {
    return ""
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("it-IT")
}

function instanceInventoryLabel(deviceType: DeviceType, count: number) {
  return deviceType === "sheltr_mini" ? "AUTO" : `${count} schede`
}

function fullControlUrl(instanceId: string) {
  if (typeof window === "undefined") {
    return controlUrl(instanceId)
  }
  return `${window.location.origin}${controlUrl(instanceId)}`
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
                  onChange(
                    normalizeBoard(
                      { ...board, channelStart: clamp(toInt(event.target.value, board.channelStart), 1, maxChannels) },
                      boardIndex
                    )
                  )
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
                  onChange(
                    normalizeBoard(
                      { ...board, channelEnd: clamp(toInt(event.target.value, board.channelEnd), 1, maxChannels) },
                      boardIndex
                    )
                  )
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

function CreateInstanceDialog({
  open,
  onOpenChange,
  newId,
  setNewId,
  newName,
  setNewName,
  newDeviceType,
  setNewDeviceType,
  deviceTypes,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  newId: string
  setNewId: (value: string) => void
  newName: string
  setNewName: (value: string) => void
  newDeviceType: DeviceType
  setNewDeviceType: (value: DeviceType) => void
  deviceTypes: Record<string, DeviceTypePublic>
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Aggiungi istanza</DialogTitle>
          <DialogDescription>
            Crea una nuova istanza Sheltr e apri subito la sua configurazione dedicata.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-5" onSubmit={onSubmit}>
          <div className="space-y-2">
            <Label>ID istanza</Label>
            <Input value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="es. casa-demo" autoFocus />
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
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Annulla
            </Button>
            <Button type="submit">
              <Plus className="size-4" />
              Crea istanza
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function InstancesTable({
  instances,
  currentId,
  deviceTypes,
  onCopy,
}: {
  instances: ConfigInstanceListItem[]
  currentId: string
  deviceTypes: Record<string, DeviceTypePublic>
  onCopy: (value: string) => void
}) {
  if (!instances.length) {
    return (
      <Alert>
        <AlertTitle>Nessuna istanza</AlertTitle>
        <AlertDescription>
          Usa “Aggiungi istanza” dalla sidebar o dalla top navbar per creare la prima configurazione.
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <div className="min-w-0 overflow-x-auto border-y bg-background">
      <table className="min-w-full text-sm">
        <thead className="bg-muted/40 text-left text-muted-foreground">
          <tr className="border-b">
            <th className="px-4 py-3 font-medium">Istanza</th>
            <th className="px-4 py-3 font-medium">Tipo</th>
            <th className="px-4 py-3 font-medium">Inventario</th>
            <th className="px-4 py-3 font-medium">Accesso</th>
            <th className="px-4 py-3 font-medium">Aggiornata</th>
            <th className="px-4 py-3 text-right font-medium">Azioni</th>
          </tr>
        </thead>
        <tbody>
          {instances.map((instance) => {
            const active = instance.id === currentId
            const typeLabel = cleanText(instance.deviceLabel, deviceTypes[instance.deviceType]?.label || instance.deviceType)
            return (
              <tr key={instance.id} className={`border-b last:border-b-0 ${active ? "bg-muted/20" : "bg-background"}`}>
                <td className="px-4 py-4">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium text-foreground">{instance.name}</p>
                      {active ? <Badge variant="secondary">Aperta</Badge> : null}
                    </div>
                    <p className="font-mono text-xs text-muted-foreground">{instance.id}</p>
                  </div>
                </td>
                <td className="px-4 py-4">
                  <Badge variant="outline">{typeLabel}</Badge>
                </td>
                <td className="px-4 py-4 text-muted-foreground">
                  {instanceInventoryLabel(normalizeDeviceType(instance.deviceType), instance.boardsCount)}
                </td>
                <td className="px-4 py-4">
                  {instance.authRequired ? <Badge variant="secondary">Protetta</Badge> : <Badge variant="outline">Diretta</Badge>}
                </td>
                <td className="px-4 py-4 text-muted-foreground">{formatUpdatedAt(instance.updatedAt) || "-"}</td>
                <td className="px-4 py-4">
                  <div className="flex justify-end gap-2">
                    <Button asChild size="sm" variant="outline">
                      <Link to={instanceConfigUrl(instance.id)}>
                        <Settings2 className="size-4" />
                        Config
                      </Link>
                    </Button>
                    <Button asChild size="sm" variant="ghost">
                      <a href={instance.controlUrl || controlUrl(instance.id)} target="_blank" rel="noreferrer">
                        <ExternalLink className="size-4" />
                        Apri
                      </a>
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => onCopy(fullControlUrl(instance.id))}>
                      <Copy className="size-4" />
                      Copia
                    </Button>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function ConfigPage() {
  const params = useParams()
  const location = useLocation()
  const navigate = useNavigate()

  const routeInstanceId = slugify(params.instanceId || forcedInstanceFromLocation(location.pathname, location.search), "")
  const listView = !routeInstanceId

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
  const [createOpen, setCreateOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [note, setNote] = useState<NoteState>({ text: "", error: false })

  function showNote(text: string, error = false) {
    setNote({ text, error })
  }

  function handleConfigAuthError(error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      setConfigToken("")
      localStorage.removeItem(configTokenKey())
      setInstances([])
      setEditor(null)
      setCurrentId("")
      navigate("/config", { replace: true })
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
    const resolvedDefaultDeviceType = normalizeDeviceType(meta.defaultDeviceType || DEFAULT_DEVICE_TYPE)
    setMqttBaseTopic(resolvedMqttBaseTopic)
    setDeviceTypes(normalizeDeviceTypes(meta.deviceTypes))
    setDefaultDeviceType(resolvedDefaultDeviceType)
    setNewDeviceType((current) => normalizeDeviceType(current, resolvedDefaultDeviceType))
    return resolvedMqttBaseTopic
  }

  async function loadInstancesList(tokenValue = configToken) {
    const response = await apiJson<ConfigInstancesResponse>("/api/config/instances", {
      tokenConfig: configTokenConfig(tokenValue),
    })
    const items = Array.isArray(response.instances) ? response.instances : []
    setInstances(items)
    return items
  }

  async function loadInstance(instanceId: string, tokenValue = configToken, mqttBaseTopicValue = mqttBaseTopic) {
    const id = slugify(instanceId, "dr154-1")
    const response = await apiJson<ConfigInstanceResponse>(`/api/config/instances/${encodeURIComponent(id)}`, {
      tokenConfig: configTokenConfig(tokenValue),
    })
    setCurrentId(response.instance.id)
    setEditor(editorFromInstance(response.instance, mqttBaseTopicValue))
    return response.instance
  }

  useEffect(() => {
    let cancelled = false

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
          setInstances([])
          setEditor(null)
          setCurrentId("")
          setLoading(false)
          showNote("Login configurazione richiesto.")
          return
        }

        const tokenValue = authRequired ? storedToken : ""
        const baseTopicValue = await loadConfigMeta(tokenValue)
        if (cancelled) return

        await loadInstancesList(tokenValue)
        if (cancelled) return

        if (routeInstanceId) {
          await loadInstance(routeInstanceId, tokenValue, baseTopicValue)
        } else {
          setEditor(null)
          setCurrentId("")
        }
      } catch (caught) {
        if (cancelled) return
        if (!handleConfigAuthError(caught)) {
          setInstances([])
          setEditor(null)
          setCurrentId("")
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
  }, [routeInstanceId, navigate])

  async function copyText(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      showNote(`Copiato: ${value}`)
    } catch {
      showNote("Copia non riuscita", true)
    }
  }

  async function createInstance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      const id = slugify(newId, "dr154-1")
      const name = cleanText(newName, id)
      const deviceType = normalizeDeviceType(newDeviceType)
      const response = await apiJson<ConfigInstanceResponse>("/api/config/instances", {
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
      setCreateOpen(false)
      setNewId("")
      setNewName("")
      setNewDeviceType(defaultDeviceType)
      await loadInstancesList()
      showNote(`Istanza '${response.instance.id}' creata.`)
      navigate(instanceConfigUrl(response.instance.id))
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
    const saved = response.instance
    setCurrentId(saved.id)
    setEditor(editorFromInstance(saved, mqttBaseTopic))
    await loadInstancesList()
    if (routeInstanceId !== saved.id) {
      navigate(instanceConfigUrl(saved.id), { replace: true })
    }
    if (!silent) {
      showNote("Configurazione salvata.")
    }
    return saved
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
        await loadInstancesList()
      }
      const autoconfig = response.autoconfig && typeof response.autoconfig === "object" ? response.autoconfig : null
      const isMiniDevice = normalizeDeviceType(saved.deviceType) === "sheltr_mini"
      if (autoconfig?.ok) {
        showNote(
          isMiniDevice
            ? `Sheltr Mini sincronizzato da '${autoconfig.topic || response.topic}'. Rilevati ${autoconfig.devicesCount || 0} dispositivi.`
            : `Configurazione pubblicata su '${response.topic}'.`
        )
        return
      }
      if (isMiniDevice) {
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
      await loadInstancesList()
      setEditor(null)
      setCurrentId("")
      showNote(`Istanza '${currentId}' eliminata.`)
      navigate("/config")
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
      await loadInstancesList(token)
      if (routeInstanceId) {
        await loadInstance(routeInstanceId, token, baseTopicValue)
      }
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
    navigate("/config", { replace: true })
    showNote("Logout configurazione eseguito.")
  }

  function updateEditor(next: EditorInstance | null) {
    setEditor(next)
  }

  const configAllowed = !configAuthRequired || Boolean(configToken)
  const isMini = editor?.deviceType === "sheltr_mini"
  const deviceHint = editor ? transportHint(editor, mqttBaseTopic, deviceTypes) : ""
  const transport = editor ? transportFromEditor(editor, mqttBaseTopic) : null
  const associatedDevices = editor ? associatedDevicesFromBoards(editor.boards) : []
  const pageTitle = listView ? "Istanze" : editor ? editor.name : "Configurazione istanza"
  const pageDescription = listView
    ? "Elenco completo delle istanze Sheltr Cloud. Apri Config per entrare nell’editor della singola istanza."
    : editor
      ? "Modifica i dati principali dell’istanza, salva e poi pubblica o sincronizza il dispositivo."
      : "Caricamento configurazione istanza in corso."

  return (
    <AppShell
      title="Configurazione"
      description="Console amministrativa per creare istanze, aprire l’editor dedicato e pubblicare la configurazione dei moduli Sheltr."
      variant="full"
      showHeader={false}
      showFooter={false}
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
        <>
          <SidebarProvider defaultOpen>
            <Sidebar collapsible="offcanvas">
              <SidebarHeader className="border-b border-sidebar-border p-3">
                <div className="flex items-center gap-3 overflow-hidden rounded-xl px-2 py-1">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-2xl border border-sidebar-border bg-sidebar-accent">
                    <img src="/static/logo.svg" alt="Sheltr" className="size-5" />
                  </div>
                  <div className="min-w-0 group-data-[collapsible=icon]:hidden">
                    <p className="text-xs font-medium uppercase tracking-[0.24em] text-sidebar-foreground/60">Sheltr Cloud</p>
                    <p className="truncate text-sm font-semibold text-sidebar-foreground">Config Console</p>
                  </div>
                </div>
              </SidebarHeader>

              <SidebarContent>
                <SidebarGroup>
                  <SidebarGroupLabel>Navigazione</SidebarGroupLabel>
                  <SidebarGroupContent>
                    <SidebarMenu>
                      <SidebarMenuItem>
                        <SidebarMenuButton type="button" isActive={createOpen} onClick={() => setCreateOpen(true)}>
                          <Plus />
                          <span>Aggiungi istanza</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                      <SidebarMenuItem>
                        <SidebarMenuButton type="button" isActive={listView} onClick={() => navigate("/config")}>
                          <LayoutList />
                          <span>Istanze</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    </SidebarMenu>
                  </SidebarGroupContent>
                </SidebarGroup>

                <SidebarSeparator />

                <SidebarGroup>
                  <SidebarGroupLabel>Contesto</SidebarGroupLabel>
                  <SidebarGroupContent>
                    <div className="space-y-3 px-2 group-data-[collapsible=icon]:hidden">
                      <div className="space-y-1">
                        <p className="text-sm font-medium text-sidebar-foreground">Vista corrente</p>
                        <p className="text-sm leading-6 text-sidebar-foreground/70">{pageDescription}</p>
                      </div>

                      {editor ? (
                        <div className="space-y-3 rounded-2xl border border-sidebar-border bg-sidebar-accent/50 p-3">
                          <div className="space-y-1">
                            <p className="text-sm font-medium text-sidebar-foreground">{editor.name}</p>
                            <p className="font-mono text-xs text-sidebar-foreground/60">{editor.id}</p>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Badge variant="outline">{cleanText(deviceTypes[editor.deviceType]?.label, editor.deviceType)}</Badge>
                            <Badge variant="outline">{isMini ? `AUTO (${associatedDevices.length})` : `${editor.boards.length} schede`}</Badge>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </SidebarGroupContent>
                </SidebarGroup>
              </SidebarContent>

              <SidebarFooter className="border-t border-sidebar-border p-3">
                <SidebarMenu>
                  {configAuthRequired ? (
                    <SidebarMenuItem>
                      <SidebarMenuButton type="button" onClick={configLogout}>
                        <LogOut />
                        <span>Logout config</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ) : null}
                  {editor ? (
                    <SidebarMenuItem>
                      <SidebarMenuButton asChild>
                        <Link to={controlUrl(editor.id)}>
                          <ExternalLink />
                          <span>Apri controllo</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ) : null}
                </SidebarMenu>
              </SidebarFooter>
              <SidebarRail />
            </Sidebar>

            <SidebarInset className="min-w-0">
              <header className="sticky top-0 z-20 flex h-16 shrink-0 items-center justify-between gap-3 border-b bg-background/95 px-4 backdrop-blur md:px-6">
                <div className="flex min-w-0 items-center gap-3">
                  <SidebarTrigger className="rounded-full" />
                  <SidebarSeparator orientation="vertical" className="h-4" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{pageTitle}</p>
                    <p className="hidden text-xs text-muted-foreground md:block">Configurazione istanze Sheltr Cloud</p>
                  </div>
                </div>

                <nav className="flex flex-wrap items-center gap-2">
                  <Button type="button" variant="outline" className="rounded-full" onClick={() => setCreateOpen(true)}>
                    <Plus className="size-4" />
                    <span className="hidden sm:inline">Aggiungi istanza</span>
                  </Button>
                  <Button
                    type="button"
                    variant={listView ? "secondary" : "outline"}
                    className="rounded-full"
                    onClick={() => navigate("/config")}
                  >
                    <LayoutList className="size-4" />
                    <span className="hidden sm:inline">Istanze</span>
                  </Button>
                </nav>
              </header>

              <div className="flex min-w-0 flex-1 flex-col gap-6 px-4 py-6 md:px-6">
                <div className="w-full min-w-0 space-y-6">
                  {note.text ? (
                    <Alert variant={note.error ? "destructive" : "default"}>
                      <AlertTitle>{note.error ? "Attenzione" : "Stato"}</AlertTitle>
                      <AlertDescription>{note.text}</AlertDescription>
                    </Alert>
                  ) : null}

                  {loading ? <p className="text-sm text-muted-foreground">Caricamento configurazione in corso...</p> : null}

                  {!loading && listView ? (
                    <section className="w-full min-w-0 space-y-4">
                      <div className="space-y-1">
                        <h2 className="text-2xl font-semibold tracking-tight">Istanze</h2>
                        <p className="text-sm text-muted-foreground">
                          Il pulsante “Config” apre la configurazione dedicata della singola istanza.
                        </p>
                      </div>
                      <InstancesTable
                        instances={instances}
                        currentId={currentId}
                        deviceTypes={deviceTypes}
                        onCopy={(value) => {
                          void copyText(value)
                        }}
                      />
                    </section>
                  ) : null}

                  {!loading && !listView && !editor ? (
                    <Card className="border-border/80 bg-background/90 shadow-none">
                      <CardContent className="p-6 text-sm text-muted-foreground">
                        Impossibile caricare la configurazione dell’istanza richiesta.
                      </CardContent>
                    </Card>
                  ) : null}

                  {!loading && editor ? (
                    <>
                      <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1.15fr)_360px]">
                        <Card className="border-border/80 bg-background/90">
                          <CardHeader>
                            <CardTitle>Dati istanza</CardTitle>
                            <CardDescription>
                              Modifica le informazioni principali. I topic MQTT restano sempre derivati dall’ID istanza.
                            </CardDescription>
                          </CardHeader>
                          <CardContent className="space-y-6">
                            <div className="grid gap-4 md:grid-cols-2">
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
                                <Label>{isMini ? "Dispositivi rilevati" : "Schede configurate"}</Label>
                                <Input
                                  readOnly
                                  value={
                                    isMini
                                      ? associatedDevices.length
                                        ? `AUTO (${associatedDevices.length})`
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
                          </CardContent>
                        </Card>

                        <div className="space-y-6">
                          <Card className="border-border/80 bg-background/90">
                            <CardHeader>
                              <CardTitle>Trasporto</CardTitle>
                              <CardDescription>Riepilogo read-only dei topic MQTT derivati e del formato payload.</CardDescription>
                            </CardHeader>
                            <CardContent className="grid gap-3">
                              <div className="rounded-2xl border p-4">
                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Config</p>
                                <p className="mt-2 break-all font-mono text-sm">{transport?.configTopic || "-"}</p>
                              </div>
                              <div className="rounded-2xl border p-4">
                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Comandi</p>
                                <p className="mt-2 break-all font-mono text-sm">{transport?.lightCommandTopic || "-"}</p>
                              </div>
                              <div className="rounded-2xl border p-4">
                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Risposta</p>
                                <p className="mt-2 break-all font-mono text-sm">{transport?.lightResponseTopic || "-"}</p>
                              </div>
                              <div className="rounded-2xl border p-4">
                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Payload</p>
                                <p className="mt-2 font-mono text-sm">
                                  {cleanText(deviceTypes[editor.deviceType]?.defaultPayloadFormat, "frame_hex_space_crlf")}
                                </p>
                              </div>
                            </CardContent>
                          </Card>

                          <Card className="border-border/80 bg-background/90">
                            <CardHeader>
                              <CardTitle>Accesso controllo</CardTitle>
                              <CardDescription>Credenziali usate dalla pagina di controllo della singola istanza.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
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
                              <div className="flex items-center justify-between rounded-2xl border p-4">
                                <div className="space-y-1">
                                  <Label>Rimuovi password</Label>
                                  <p className="text-sm text-muted-foreground">Cancella la password salvata per questa istanza.</p>
                                </div>
                                <Switch
                                  checked={editor.auth.clearPassword}
                                  onCheckedChange={(checked) =>
                                    updateEditor({ ...editor, auth: { ...editor.auth, clearPassword: checked } })
                                  }
                                />
                              </div>
                            </CardContent>
                          </Card>

                          <Card className="border-border/80 bg-background/90">
                            <CardHeader>
                              <CardTitle>Azioni</CardTitle>
                              <CardDescription>Salva prima la configurazione, poi pubblica o sincronizza il dispositivo.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-3">
                              <Button
                                type="button"
                                className="w-full"
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
                              <Button type="button" variant="outline" className="w-full" onClick={publishCurrent}>
                                <RefreshCw className="size-4" />
                                {isMini ? "Sincronizza Sheltr Mini" : "Pubblica su MQTT"}
                              </Button>
                              <Button type="button" variant="destructive" className="w-full" onClick={deleteCurrent}>
                                <Trash2 className="size-4" />
                                Elimina istanza
                              </Button>
                              <Separator />
                              <Button asChild variant="outline" className="w-full">
                                <Link to={controlUrl(editor.id)}>Apri controllo</Link>
                              </Button>
                              <Button type="button" variant="ghost" className="w-full" onClick={() => void copyText(fullControlUrl(editor.id))}>
                                <Copy className="size-4" />
                                Copia link controllo
                              </Button>
                            </CardContent>
                          </Card>
                        </div>
                      </div>

                      {isMini ? (
                        <Card className="border-border/80 bg-background/90">
                          <CardHeader>
                            <CardTitle>Dispositivi autoconfigurati</CardTitle>
                            <CardDescription>
                              Sheltr Mini mostra qui solo i dispositivi rilevati dal modulo tramite sincronizzazione cloud.
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
                        <section className="space-y-4">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                              <h3 className="text-lg font-semibold">Schede</h3>
                              <p className="text-sm text-muted-foreground">
                                Configura indirizzo, tipo, canali e stanze di ogni scheda associata all’istanza.
                              </p>
                            </div>
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
                          </div>

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
                        </section>
                      )}
                    </>
                  ) : null}
                </div>
              </div>
            </SidebarInset>
          </SidebarProvider>

          <CreateInstanceDialog
            open={createOpen}
            onOpenChange={setCreateOpen}
            newId={newId}
            setNewId={setNewId}
            newName={newName}
            setNewName={setNewName}
            newDeviceType={newDeviceType}
            setNewDeviceType={setNewDeviceType}
            deviceTypes={deviceTypes}
            onSubmit={createInstance}
          />
        </>
      )}
    </AppShell>
  )
}
