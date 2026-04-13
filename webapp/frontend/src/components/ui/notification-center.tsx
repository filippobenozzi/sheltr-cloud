import { useEffect, useState } from "react"
import { CheckCircle2, Info, TriangleAlert, X, XCircle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { dismissNotification, subscribeNotifications, type NotificationItem } from "@/lib/notifications"

function notificationIcon(tone: NotificationItem["tone"]) {
  switch (tone) {
    case "success":
      return <CheckCircle2 className="size-4 text-emerald-600" />
    case "warning":
      return <TriangleAlert className="size-4 text-amber-600" />
    case "destructive":
      return <XCircle className="size-4 text-destructive" />
    default:
      return <Info className="size-4 text-sky-600" />
  }
}

export function NotificationCenter() {
  const [items, setItems] = useState<NotificationItem[]>([])

  useEffect(() => subscribeNotifications(setItems), [])

  if (!items.length) {
    return null
  }

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-[120] flex justify-center px-4">
      <div className="flex w-full max-w-xl flex-col gap-3">
        {items.map((item) => (
          <Alert
            key={item.id}
            variant={item.tone}
            className="pointer-events-auto border shadow-lg supports-[backdrop-filter]:bg-background/95"
          >
            <div className="flex items-start gap-3 pr-8">
              <div className="mt-0.5 shrink-0">{notificationIcon(item.tone)}</div>
              <div className="min-w-0">
                <AlertTitle>{item.title}</AlertTitle>
                <AlertDescription>{item.description}</AlertDescription>
              </div>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="absolute top-2 right-2"
              onClick={() => dismissNotification(item.id)}
            >
              <X className="size-3.5" />
            </Button>
          </Alert>
        ))}
      </div>
    </div>
  )
}
