type TokenConfig = {
  token?: string | null
  headerName: string
  queryName: string
  bodyField: string
}

type ApiBody = BodyInit | Record<string, unknown> | null

type ApiOptions = Omit<RequestInit, "body"> & {
  body?: ApiBody
  tokenConfig?: TokenConfig
}

export class ApiError extends Error {
  status: number
  data: unknown

  constructor(message: string, status: number, data: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.data = data
  }
}

function addTokenToRequest(
  path: string,
  body: ApiBody | undefined,
  method: string,
  tokenConfig?: TokenConfig
): { path: string; body: ApiBody } {
  if (!tokenConfig?.token) {
    return { path, body: body ?? null }
  }

  const url = new URL(path, window.location.origin)
  url.searchParams.set(tokenConfig.queryName, tokenConfig.token)

  let nextBody: ApiBody = body ?? null
  if (method !== "GET" && method !== "HEAD") {
    if (typeof nextBody === "string") {
      try {
        const parsed = JSON.parse(nextBody) as Record<string, unknown>
        if (!(tokenConfig.bodyField in parsed)) {
          parsed[tokenConfig.bodyField] = tokenConfig.token
          nextBody = JSON.stringify(parsed)
        }
      } catch {
        // Keep non-JSON bodies untouched.
      }
    } else if (nextBody == null) {
      nextBody = { [tokenConfig.bodyField]: tokenConfig.token }
    } else if (
      typeof nextBody === "object" &&
      !(nextBody instanceof FormData) &&
      !(nextBody instanceof Blob) &&
      !(nextBody instanceof URLSearchParams)
    ) {
      const record = nextBody as Record<string, unknown>
      if (!(tokenConfig.bodyField in record)) {
        nextBody = { ...record, [tokenConfig.bodyField]: tokenConfig.token }
      }
    }
  }

  return { path: `${url.pathname}${url.search}`, body: nextBody }
}

export async function apiJson<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const method = String(options.method ?? "GET").toUpperCase()
  const tokenConfig = options.tokenConfig
  const headers = new Headers(options.headers ?? {})

  let body = options.body ?? null
  const tokenized = addTokenToRequest(path, body, method, tokenConfig)
  let finalPath = tokenized.path
  body = tokenized.body

  if (tokenConfig?.token) {
    headers.set(tokenConfig.headerName, tokenConfig.token)
  }

  if (body != null && !(body instanceof FormData) && typeof body !== "string") {
    headers.set("Content-Type", "application/json")
    body = JSON.stringify(body)
  } else if (typeof body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }

  const response = await fetch(finalPath, {
    ...options,
    method,
    headers,
    body,
    cache: options.cache ?? "no-store",
  })

  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message =
      data && typeof data === "object" && "error" in data && typeof data.error === "string"
        ? data.error
        : `HTTP ${response.status}`
    throw new ApiError(message, response.status, data)
  }

  return data as T
}

export function instanceTokenConfig(token?: string | null): TokenConfig {
  return {
    token,
    headerName: "X-Instance-Token",
    queryName: "token",
    bodyField: "token",
  }
}

export function configTokenConfig(token?: string | null): TokenConfig {
  return {
    token,
    headerName: "X-Config-Token",
    queryName: "configToken",
    bodyField: "configToken",
  }
}
