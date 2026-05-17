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

export type RetroBufferTask = {
  id: number
  created_at: string
  category: string
  title: string
  units: number
  vector: string
  paid_base_rate: string
  current_base_rate: string
  current_reward: string
  gross_delta: string
  fee_share: string
  net_delta: string
  eligible: boolean
}

export type RetroBuffer = {
  eligible_count: number
  limit: number
  gross: string
  fee: string
  net: string
  commission_rate: string
  activation_allowed: boolean
  tasks: RetroBufferTask[]
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
  history: HistoryEntry[]
  effects: ActiveEffect[]
  prime: PrimeStatus
  crew_upkeep: CrewUpkeep
  expeditions: Expedition[]
  cabins: Cabin[]
  retro_buffer: RetroBuffer
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

export type HistoryEntry = {
  id: string
  kind: "purchase" | "task_submit"
  created_at: string
  title: string
  section: string
  amount: string
  currency: string
  target: string
  note: string
  purchase_id: number | null
  tracker_task_id: number | null
  economy_task_id: number | null
  revertible: boolean
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
  active_since: string
  weeks_purchased: number
  loyalty_weeks: number
}

export type CrewUpkeep = {
  active_count: number
  base_total: string
  discount_total: string
  effective_total: string
  discount_rate: string
  prime_active: boolean
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
  sample_code: string
  name: string
  universe: string
  rank: string
  tags: string
  full_tags: string
  sedative_dose: string
  upkeep: string
  base_upkeep: string
  upkeep_discount: string
  effective_upkeep: string
  subscription_tier: string
  subscription_started_at: string
  recessive_name: string
  recessive_description: string
  dominants: string
  dominant_max_level: number
  active: number
  note: string
  sr_promotion: SrPromotion
}

export type SrPromotion = {
  available: boolean
  reason: string
  cost: string
  currency: string
  required_dominant_level: number
}

export type CabinUpgradeResult = Cabin & {
  dominant_name: string
  level_before: number
  level_after: number
  upgrade_cost: string
  balance_before: string
  balance_after: string
  balance_delta: string
}

export type CabinDefectExciseResult = Cabin & {
  excised_defect: string
  excision_cost: string
  excision_currency: string
  purchase_id: number
  balance_before: string
  balance_after: string
}

export type CabinSrPromotionResult = Cabin & {
  promotion_cost: string
  promotion_currency: string
  purchase_id: number
  ap_balance_before: string
  ap_balance_after: string
  shadow_balance_before: string
  shadow_balance_after: string
}

export type DominantTraitPayload = {
  name: string
  level: number
}

export type CabinPayload = {
  sample_code: string
  name: string
  universe: string
  rank: string
  tags: string
  full_tags: string
  sedative_dose: string
  upkeep: string
  subscription_tier: string
  subscription_started_at: string
  recessive_name: string
  recessive_description: string
  dominants: DominantTraitPayload[]
  active: boolean
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
  revertTaskSubmit: (token: string, id: number) =>
    request<TrackerTask>(`/api/tasks/${id}/revert-submit`, token, { method: "POST" }),
  history: (token: string) =>
    request<HistoryEntry[]>("/api/history", token),
  retroBuffer: (token: string) =>
    request<RetroBuffer>("/api/retro-buffer", token),
  activateRetroBuffer: (token: string) =>
    request<RetroBuffer>("/api/retro-buffer/activate", token, { method: "POST" }),
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
  createCabin: (token: string, payload: CabinPayload) =>
    request<Cabin>("/api/cabins", token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateCabin: (token: string, id: number, payload: CabinPayload) =>
    request<Cabin>(`/api/cabins/${id}`, token, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  upgradeCabinDominant: (token: string, id: number, dominantIndex: number) =>
    request<CabinUpgradeResult>(`/api/cabins/${id}/dominants/${dominantIndex}/upgrade`, token, {
      method: "POST",
    }),
  exciseCabinDefect: (token: string, id: number) =>
    request<CabinDefectExciseResult>(`/api/cabins/${id}/defect/excise`, token, {
      method: "POST",
    }),
  promoteCabinToSr: (token: string, id: number) =>
    request<CabinSrPromotionResult>(`/api/cabins/${id}/promote/sr`, token, {
      method: "POST",
    }),
  deleteCabin: (token: string, id: number) =>
    request<Cabin>(`/api/cabins/${id}`, token, {
      method: "DELETE",
    }),
  prestige: (token: string, prime = false) =>
    request<Record<string, unknown>>("/api/prestige", token, {
      method: "POST",
      body: JSON.stringify({ prime }),
    }),
}
