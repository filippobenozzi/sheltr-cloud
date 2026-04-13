export type NotificationTone = "success" | "info" | "warning" | "destructive"

export type NotificationItem = {
  id: string
  title: string
  description: string
  tone: NotificationTone
  duration: number
}

type NotificationListener = (items: NotificationItem[]) => void

let notifications: NotificationItem[] = []
const listeners = new Set<NotificationListener>()
const timers = new Map<string, ReturnType<typeof setTimeout>>()

function emitNotifications() {
  for (const listener of listeners) {
    listener(notifications)
  }
}

function createNotificationId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID()
  }
  return `notice-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function subscribeNotifications(listener: NotificationListener) {
  listeners.add(listener)
  listener(notifications)
  return () => {
    listeners.delete(listener)
  }
}

export function dismissNotification(id: string) {
  const timer = timers.get(id)
  if (timer) {
    clearTimeout(timer)
    timers.delete(id)
  }
  notifications = notifications.filter((item) => item.id !== id)
  emitNotifications()
}

export function notify(input: {
  title: string
  description: string
  tone?: NotificationTone
  duration?: number
}) {
  const item: NotificationItem = {
    id: createNotificationId(),
    title: input.title,
    description: input.description,
    tone: input.tone ?? "info",
    duration: Math.max(1500, input.duration ?? 4200),
  }

  notifications = [...notifications, item]
  emitNotifications()

  const timer = setTimeout(() => {
    dismissNotification(item.id)
  }, item.duration)
  timers.set(item.id, timer)

  return item.id
}
