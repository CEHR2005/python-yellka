export type BalanceState = {
  balance: string
  base_rate: string
  cashback_level: number
  cashback_percent: number
  retroactive_indexing_enabled: boolean
  vector_levels: Record<string, number>
  next_core_cost: string
}

export type Category = {
  category: string
  completed: number
  task_count: number
  premium_pending_count: number
  reward_formula: string
  reward_total: string
  premium_total: string
  premium_pending_total: string
}

export type CatalogItem = {
  key: string
  title: string
  value: string
}

export type VectorItem = {
  key: string
  title: string
}

export type TrackerStatus = "draft" | "done" | "submitted"

export type TrackerTask = {
  id: number
  created_at: string
  updated_at: string
  category: string
  title: string
  status: TrackerStatus
  vector: string
  units: number
  catalog_key: string
  catalog_value: string
  priority: boolean
  full_close: boolean
  note: string
  economy_task_id: number | null
  submitted_reward: string
  submitted_retro_bonus: string
  submitted: boolean
}

export type BootstrapPayload = {
  balance: BalanceState
  categories: Category[]
  tasks: TrackerTask[]
  catalog: CatalogItem[]
  vectors: VectorItem[]
  wallet: Wallet
  shop_catalog: ShopItem[]
  shop_purchases: ShopPurchase[]
  effects: ActiveEffect[]
  prime: PrimeStatus
  expeditions: Expedition[]
  cabins: Cabin[]
}

export type TaskPayload = {
  title: string
  category: string
  vector: string
  units: number
  catalog_key: string
  catalog_value: string
  priority: boolean
  full_close: boolean
  note: string
}

export type Wallet = {
  currencies: Record<string, string>
  base_rate: string
  cashback_level: number
  cashback_percent: string | number
  vector_levels: Record<string, number>
}

export type ShopItem = {
  key: string
  title: string
  section: string
  currency: string
  base_cost: string
  cost_formula: string
  max_level: number | null
  discount_tags: string[]
  gate: string
  effect_kind: string
  description: string
}

export type ShopQuote = {
  item_key: string
  title: string
  section: string
  target: string
  quantity: number
  currency: string
  full_cost: string
  discount: string
  final_cost: string
  available: boolean
  reason: string
  metadata: Record<string, unknown>
}

export type ShopPurchase = {
  id: number
  created_at: string
  item_key: string
  title: string
  section: string
  target: string
  quantity: number
  currency: string
  full_cost: string
  discount: string
  final_cost: string
  note: string
  effect_kind: string
  metadata: string
}

export type ActiveEffect = {
  key: string
  title: string
  value: string
  expires_at: string
  note: string
}

export type PrimeStatus = {
  active: boolean
  weeks_purchased: number
  loyalty_weeks: number
}

export type Expedition = {
  id: number
  created_at: string
  title: string
  status: string
  difficulty: string
  note: string
  cached_until: string
  rotten: number
}

export type Cabin = {
  id: number
  created_at: string
  name: string
  rank: string
  tags: string
  sedative_dose: string
  active: number
  note: string
}

export type ShopPayload = {
  item_key: string
  target: string
  quantity: number
  note?: string
  options?: Record<string, unknown>
}

async function request<T>(
  path: string,
  token: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  })

  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    throw new Error(Array.isArray(detail) ? detail[0]?.msg : detail || response.statusText)
  }

  return response.json() as Promise<T>
}

export const api = {
  bootstrap: (token: string) => request<BootstrapPayload>("/api/bootstrap", token),
  createCategory: (token: string, category: string) =>
    request<Category>("/api/categories", token, {
      method: "POST",
      body: JSON.stringify({ category }),
    }),
  completeCategory: (token: string, category: string) =>
    request<Category>(`/api/categories/${encodeURIComponent(category)}/complete`, token, {
      method: "POST",
    }),
  reopenCategory: (token: string, category: string) =>
    request<Category>(`/api/categories/${encodeURIComponent(category)}/reopen`, token, {
      method: "POST",
    }),
  createTask: (token: string, payload: TaskPayload) =>
    request<TrackerTask>("/api/tasks", token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTask: (token: string, id: number, payload: TaskPayload) =>
    request<TrackerTask>(`/api/tasks/${id}`, token, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  markDone: (token: string, id: number) =>
    request<TrackerTask>(`/api/tasks/${id}/done`, token, { method: "POST" }),
  submitTask: (token: string, id: number) =>
    request<TrackerTask>(`/api/tasks/${id}/submit`, token, { method: "POST" }),
  quoteShop: (token: string, payload: ShopPayload) =>
    request<ShopQuote>("/api/shop/quote", token, {
      method: "POST",
      body: JSON.stringify({ ...payload, options: payload.options || {} }),
    }),
  purchaseShop: (token: string, payload: ShopPayload) =>
    request<ShopPurchase>("/api/shop/purchase", token, {
      method: "POST",
      body: JSON.stringify({ ...payload, options: payload.options || {} }),
    }),
  createExpedition: (token: string, payload: { title: string; difficulty: string; note: string }) =>
    request<Expedition>("/api/expeditions", token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createCabin: (token: string, payload: { name: string; rank: string; tags: string; note: string }) =>
    request<Cabin>("/api/cabins", token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  prestige: (token: string, prime = false) =>
    request<Record<string, unknown>>("/api/prestige", token, {
      method: "POST",
      body: JSON.stringify({ prime }),
    }),
}
