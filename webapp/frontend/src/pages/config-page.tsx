import { type FormEvent, useEffect, useState } from "react"
import { Link, useLocation, useNavigate, useParams } from "react-router-dom"
import { Copy, ExternalLink, LayoutList, LogOut, Plus, Save, Settings2, Trash2 } from "lucide-react"

import { AppShell } from "@/components/app-shell"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator as BreadcrumbDivider,
} from "@/components/ui/breadcrumb"
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
import { notify } from "@/lib/notifications"
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

  function showNote(text: string, error = false, tone: "success" | "info" | "warning" = "success") {
    if (error) {
      notify({ title: "Attenzione", description: text, tone: "destructive" })
      return
    }
    if (tone === "info") {
      notify({ title: "Info", description: text, tone: "info" })
      return
    }
    if (tone === "warning") {
      notify({ title: "Avviso", description: text, tone: "warning" })
      return
    }
    notify({ title: "Operazione completata", description: text, tone: "success" })
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
          showNote("Login configurazione richiesto.", false, "info")
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

  function requestSaveCurrent() {
    void saveCurrent(false).catch((caught) => {
      if (!handleConfigAuthError(caught)) {
        showNote(caught instanceof Error ? caught.message : "Salvataggio non riuscito", true)
      }
    })
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
    showNote("Logout configurazione eseguito.", false, "info")
  }

  function updateEditor(next: EditorInstance | null) {
    setEditor(next)
  }

  const configAllowed = !configAuthRequired || Boolean(configToken)
  const isMini = editor?.deviceType === "sheltr_mini"
  const deviceHint = editor ? transportHint(editor, mqttBaseTopic, deviceTypes) : ""
  const associatedDevices = editor ? associatedDevicesFromBoards(editor.boards) : []
  const pageTitle = listView ? "Istanze" : editor ? editor.name : "Configurazione istanza"
  const currentCrumb = listView ? "Istanze" : editor?.name || "Configurazione istanza"
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
            <Sidebar collapsible="icon">
              <SidebarHeader className="h-16 justify-center border-b border-sidebar-border px-4 md:h-20 md:px-6">
                <div className="space-y-1 overflow-hidden px-2 py-1 group-data-[collapsible=icon]:hidden">
                  <p className="text-xs font-medium uppercase tracking-[0.24em] text-sidebar-foreground/60">Sheltr Cloud</p>
                  <p className="truncate text-sm font-semibold text-sidebar-foreground">Configuration</p>
                </div>
              </SidebarHeader>

              <SidebarContent>
                <SidebarGroup>
                  <SidebarGroupLabel>Navigazione</SidebarGroupLabel>
                  <SidebarGroupContent>
                    <SidebarMenu>
                      <SidebarMenuItem>
                        <SidebarMenuButton
                          type="button"
                          isActive={createOpen}
                          tooltip="Aggiungi istanza"
                          onClick={() => setCreateOpen(true)}
                        >
                          <Plus />
                          <span className="group-data-[collapsible=icon]:hidden">Aggiungi istanza</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                      <SidebarMenuItem>
                        <SidebarMenuButton
                          type="button"
                          isActive={listView}
                          tooltip="Istanze"
                          onClick={() => navigate("/config")}
                        >
                          <LayoutList />
                          <span className="group-data-[collapsible=icon]:hidden">Istanze</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    </SidebarMenu>
                  </SidebarGroupContent>
                </SidebarGroup>
              </SidebarContent>

              <SidebarFooter className="border-t border-sidebar-border p-3">
                <SidebarMenu>
                  {configAuthRequired ? (
                    <SidebarMenuItem>
                      <SidebarMenuButton type="button" tooltip="Logout" onClick={configLogout}>
                        <LogOut />
                        <span className="group-data-[collapsible=icon]:hidden">Logout</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ) : null}
                </SidebarMenu>
              </SidebarFooter>
              <SidebarRail />
            </Sidebar>

            <SidebarInset className="min-w-0 w-full">
              <header className="sticky top-0 z-30 flex h-16 shrink-0 items-stretch justify-between gap-2 border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/90 md:h-20 md:px-6">
                <div className="flex min-w-0 items-stretch gap-2 self-stretch">
                  <div className="flex items-center">
                    <SidebarTrigger className="size-8 rounded-full" />
                  </div>
                  <SidebarSeparator orientation="vertical" className="mx-0.5" />
                  <div className="flex min-w-0 flex-col justify-center gap-1 py-2">
                    <Breadcrumb>
                      <BreadcrumbList className="text-xs">
                        <BreadcrumbItem>
                          <BreadcrumbLink asChild>
                            <Link to="/">Home</Link>
                          </BreadcrumbLink>
                        </BreadcrumbItem>
                        <BreadcrumbDivider />
                        <BreadcrumbItem>
                          {listView ? (
                            <BreadcrumbPage>Istanze</BreadcrumbPage>
                          ) : (
                            <BreadcrumbLink asChild>
                              <Link to="/config">Istanze</Link>
                            </BreadcrumbLink>
                          )}
                        </BreadcrumbItem>
                        {!listView ? (
                          <>
                            <BreadcrumbDivider />
                            <BreadcrumbItem>
                              <BreadcrumbPage>{currentCrumb}</BreadcrumbPage>
                            </BreadcrumbItem>
                          </>
                        ) : null}
                      </BreadcrumbList>
                    </Breadcrumb>
                    <p className="truncate text-lg font-semibold tracking-tight md:text-xl">{pageTitle}</p>
                  </div>
                </div>

                <nav className="flex flex-wrap items-center gap-2">
                  {editor ? (
                    <>
                      <Button type="button" size="sm" variant="outline" className="rounded-full" onClick={() => void copyText(fullControlUrl(editor.id))}>
                        <Copy className="size-4" />
                        <span className="hidden sm:inline">Copia link</span>
                      </Button>
                      <Button asChild size="sm" variant="outline" className="rounded-full">
                        <a href={controlUrl(editor.id)} target="_blank" rel="noreferrer">
                          <ExternalLink className="size-4" />
                          <span className="hidden sm:inline">Apri controllo</span>
                        </a>
                      </Button>
                      <Button type="button" size="sm" className="rounded-full" onClick={requestSaveCurrent}>
                        <Save className="size-4" />
                        <span className="hidden sm:inline">Salva</span>
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button type="button" size="sm" variant="outline" className="rounded-full" onClick={() => setCreateOpen(true)}>
                        <Plus className="size-4" />
                        <span className="hidden sm:inline">Aggiungi istanza</span>
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant={listView ? "secondary" : "outline"}
                        className="rounded-full"
                        onClick={() => navigate("/config")}
                      >
                        <LayoutList className="size-4" />
                        <span className="hidden sm:inline">Istanze</span>
                      </Button>
                    </>
                  )}
                </nav>
              </header>

              <div className="flex min-w-0 w-full flex-1 flex-col gap-6 px-4 py-6 md:px-6">
                <div className="w-full min-w-0 max-w-none space-y-6">
                  {loading ? <p className="text-sm text-muted-foreground">Caricamento configurazione in corso...</p> : null}

                  {!loading && listView ? (
                    <section className="w-full min-w-0 space-y-4">
                      <h2 className="text-2xl font-semibold tracking-tight">Istanze</h2>
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
                    <div className="space-y-8">
                      <section className="space-y-4">
                        <h2 className="text-xl font-semibold tracking-tight">Dati istanza</h2>
                        <div className="space-y-4">
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
                        </div>
                        {deviceHint ? <p className="text-sm text-muted-foreground">{deviceHint}</p> : null}
                      </section>

                      <Separator />

                      <section className="space-y-4">
                        <h2 className="text-xl font-semibold tracking-tight">Accesso</h2>
                        <div className="space-y-4">
                          <div className="space-y-2">
                            <Label>Username accesso</Label>
                            <Input
                              value={editor.auth.username}
                              onChange={(event) =>
                                updateEditor({ ...editor, auth: { ...editor.auth, username: event.target.value } })
                              }
                              placeholder="es. filippo"
                            />
                          </div>
                          <div className="space-y-2">
                            <Label>Password accesso</Label>
                            <Input
                              value={editor.auth.password}
                              onChange={(event) =>
                                updateEditor({ ...editor, auth: { ...editor.auth, password: event.target.value } })
                              }
                              placeholder="Visibile: nuova password o vuoto"
                            />
                          </div>
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
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
                        </div>
                      </section>

                      <Separator />

                      {isMini ? (
                        <section className="space-y-4">
                          <h2 className="text-xl font-semibold tracking-tight">Dispositivi autoconfigurati</h2>
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
                        </section>
                      ) : (
                        <section className="space-y-4">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <h2 className="text-xl font-semibold tracking-tight">Schede</h2>
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
                            <p className="text-sm text-muted-foreground">Nessuna scheda configurata.</p>
                          )}
                        </section>
                      )}

                      <Separator />

                      <section className="space-y-4">
                        <h2 className="text-xl font-semibold tracking-tight">Azioni</h2>
                        <div className="flex flex-wrap gap-3">
                          <Button type="button" variant="destructive" onClick={deleteCurrent}>
                            <Trash2 className="size-4" />
                            Elimina istanza
                          </Button>
                        </div>
                      </section>
                    </div>
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
