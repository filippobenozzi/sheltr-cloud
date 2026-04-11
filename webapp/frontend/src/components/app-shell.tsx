import type { ReactNode } from "react"
import { NavLink } from "react-router-dom"

import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type AppShellProps = {
  title: string
  description: string
  actions?: ReactNode
  children: ReactNode
  showHeader?: boolean
  showFooter?: boolean
  variant?: "default" | "full"
}

function NavButton({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          buttonVariants({ variant: "ghost", size: "sm" }),
          "rounded-full text-muted-foreground",
          isActive && "bg-secondary text-foreground hover:bg-secondary"
        )
      }
    >
      {label}
    </NavLink>
  )
}

export function AppShell({
  title,
  description,
  actions,
  children,
  showHeader = true,
  showFooter = true,
  variant = "default",
}: AppShellProps) {
  return (
    <div className="page-shell relative overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(15,23,42,0.08),transparent_34%),radial-gradient(circle_at_bottom_right,rgba(148,163,184,0.14),transparent_28%)]" />
      <div
        className={cn(
          "page-container relative",
          variant === "full" && "max-w-none gap-0 px-0 py-0 md:px-0 lg:px-0"
        )}
      >
        {showHeader ? (
          <header className="rounded-[1.75rem] border bg-background/95 p-5 shadow-sm backdrop-blur md:p-6">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex size-12 items-center justify-center rounded-2xl border bg-muted/60">
                    <img src="/static/logo.svg" alt="Sheltr" className="size-7" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-medium uppercase tracking-[0.24em] text-muted-foreground">
                      Sheltr Cloud
                    </p>
                    <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
                  </div>
                </div>
                <p className="max-w-3xl text-sm leading-6 text-muted-foreground md:text-base">{description}</p>
              </div>

              <div className="flex flex-col gap-3 lg:items-end">
                <nav className="flex flex-wrap gap-2">
                  <NavButton to="/" label="Home" />
                  <NavButton to="/config" label="Config" />
                </nav>
                {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
              </div>
            </div>
          </header>
        ) : null}

        {children}

        {showFooter ? (
          <footer className="px-2 pb-2 text-center text-sm text-muted-foreground">
            not all those who wander are lost ~{" "}
            <a href="https://filippo.im" target="_blank" rel="noreferrer" className="underline underline-offset-4">
              filippo.im
            </a>
          </footer>
        ) : null}
      </div>
    </div>
  )
}
