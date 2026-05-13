import { useCallback, useEffect, useMemo, useState } from "react"
import {
  CheckIcon,
  ClipboardCheckIcon,
  ListChecksIcon,
  PlusIcon,
  RefreshCwIcon,
  SaveIcon,
  SendIcon,
  SlidersHorizontalIcon,
  ShoppingCartIcon,
  StickyNoteIcon,
} from "lucide-react"
import { toast } from "sonner"

import {
  api,
  type BalanceState,
  type BootstrapPayload,
  type CatalogItem,
  type Category,
  type ActiveEffect,
  type Cabin,
  type Expedition,
  type PrimeStatus,
  type ShopItem,
  type ShopPayload,
  type ShopPurchase,
  type Wallet,
  type TaskPayload,
  type TrackerStatus,
  type TrackerTask,
  type VectorItem,
} from "@/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { Toaster } from "@/components/ui/sonner"

const TOKEN_KEY = "yellka-web-token"
const HIDDEN_SHOP_SECTIONS_KEY = "yellka-hidden-shop-sections"
const NO_VALUE = "__none"
const STATUS_LABEL: Record<TrackerStatus, string> = {
  draft: "Draft",
  done: "Done",
  submitted: "Submitted",
}

const SHOP_TABS = [
  { value: "terminal", label: "Terminal" },
  { value: "hub", label: "Hub" },
  { value: "expedition", label: "Expedition" },
  { value: "world", label: "World" },
  { value: "recreation", label: "Recreation" },
  { value: "genesis", label: "Genesis" },
  { value: "prime", label: "PRIME" },
  { value: "noctur", label: "НОКТУР" },
  { value: "history", label: "History" },
]

const EMPTY_BALANCE: BalanceState = {
  balance: "0",
  base_rate: "0.2",
  cashback_level: 0,
  cashback_percent: 0,
  retroactive_indexing_enabled: false,
  vector_levels: {},
  next_core_cost: "2",
}

const EMPTY_WALLET: Wallet = {
  currencies: { ap: "0.000", shadow_ap: "0.000", singularity_shard: "0.000", neural_shard: "0.000" },
  base_rate: "0.200",
  cashback_level: 0,
  cashback_percent: 0,
  vector_levels: {},
}

const EMPTY_PAYLOAD: TaskPayload = {
  title: "",
  category: "",
  vector: "code",
  units: 1,
  catalog_key: "",
  catalog_value: "1",
  priority: false,
  full_close: false,
  note: "",
}

function App() {
  const [token, setToken] = useState(() => {
    return localStorage.getItem(TOKEN_KEY) || import.meta.env.VITE_YELLKA_WEB_TOKEN || "dev-token"
  })
  const [balance, setBalance] = useState<BalanceState>(EMPTY_BALANCE)
  const [tasks, setTasks] = useState<TrackerTask[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [catalog, setCatalog] = useState<CatalogItem[]>([])
  const [vectors, setVectors] = useState<VectorItem[]>([])
  const [wallet, setWallet] = useState<Wallet>(EMPTY_WALLET)
  const [shopCatalog, setShopCatalog] = useState<ShopItem[]>([])
  const [shopPurchases, setShopPurchases] = useState<ShopPurchase[]>([])
  const [effects, setEffects] = useState<ActiveEffect[]>([])
  const [prime, setPrime] = useState<PrimeStatus>({ active: false, weeks_purchased: 0, loyalty_weeks: 0 })
  const [expeditions, setExpeditions] = useState<Expedition[]>([])
  const [cabins, setCabins] = useState<Cabin[]>([])
  const [loading, setLoading] = useState(true)
  const [taskOpen, setTaskOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<TrackerTask | null>(null)
  const [categoryOpen, setCategoryOpen] = useState(false)
  const [newCategory, setNewCategory] = useState("")
  const [activeStatus, setActiveStatus] = useState<"all" | TrackerStatus>("all")
  const [categoryFilter, setCategoryFilter] = useState("all")
  const [mainTab, setMainTab] = useState("tasks")
  const [sectionsOpen, setSectionsOpen] = useState(false)
  const [hiddenShopSections, setHiddenShopSections] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem(HIDDEN_SHOP_SECTIONS_KEY)
      return stored ? JSON.parse(stored) as string[] : []
    } catch {
      return []
    }
  })

  useEffect(() => {
    document.documentElement.classList.add("dark")
  }, [])

  useEffect(() => {
    localStorage.setItem(HIDDEN_SHOP_SECTIONS_KEY, JSON.stringify(hiddenShopSections))
    if (mainTab !== "tasks" && hiddenShopSections.includes(mainTab)) {
      const nextVisible = SHOP_TABS.find((tab) => !hiddenShopSections.includes(tab.value))
      setMainTab(nextVisible?.value || "tasks")
    }
  }, [hiddenShopSections, mainTab])

  const applyBootstrap = useCallback((payload: BootstrapPayload) => {
    setBalance(payload.balance)
    setTasks(payload.tasks)
    setCategories(payload.categories)
    setCatalog(payload.catalog)
    setVectors(payload.vectors)
    setWallet(payload.wallet || EMPTY_WALLET)
    setShopCatalog(payload.shop_catalog || [])
    setShopPurchases(payload.shop_purchases || [])
    setEffects(payload.effects || [])
    setPrime(payload.prime || { active: false, weeks_purchased: 0, loyalty_weeks: 0 })
    setExpeditions(payload.expeditions || [])
    setCabins(payload.cabins || [])
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      applyBootstrap(await api.bootstrap(token))
      localStorage.setItem(TOKEN_KEY, token)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load tracker")
    } finally {
      setLoading(false)
    }
  }, [applyBootstrap, token])

  useEffect(() => {
    void load()
  }, [load])

  const visibleTasks = useMemo(() => {
    return tasks.filter((task) => {
      const statusMatches = activeStatus === "all" || task.status === activeStatus
      const categoryMatches =
        categoryFilter === "all" ||
        (categoryFilter === NO_VALUE ? !task.category : task.category === categoryFilter)
      return statusMatches && categoryMatches
    })
  }, [activeStatus, categoryFilter, tasks])

  const counts = useMemo(() => {
    return {
      draft: tasks.filter((task) => task.status === "draft").length,
      done: tasks.filter((task) => task.status === "done").length,
      submitted: tasks.filter((task) => task.status === "submitted").length,
    }
  }, [tasks])

  const visibleShopTabs = useMemo(() => {
    return SHOP_TABS.filter((tab) => !hiddenShopSections.includes(tab.value))
  }, [hiddenShopSections])

  function toggleShopSection(section: string, visible: boolean) {
    setHiddenShopSections((current) => {
      if (visible) {
        return current.filter((item) => item !== section)
      }
      return current.includes(section) ? current : [...current, section]
    })
  }

  async function refreshAfter(action: () => Promise<unknown>, message: string) {
    try {
      await action()
      applyBootstrap(await api.bootstrap(token))
      toast.success(message)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Action failed")
    }
  }

  async function saveTask(payload: TaskPayload) {
    await refreshAfter(
      () => editingTask ? api.updateTask(token, editingTask.id, payload) : api.createTask(token, payload),
      editingTask ? "Task updated" : "Task created",
    )
    setTaskOpen(false)
    setEditingTask(null)
  }

  async function createCategory() {
    const category = newCategory.trim()
    if (!category) {
      toast.error("Category name is required")
      return
    }
    await refreshAfter(() => api.createCategory(token, category), "Category created")
    setNewCategory("")
    setCategoryOpen(false)
  }

  function openEditTask(task: TrackerTask) {
    setEditingTask(task)
    setTaskOpen(true)
  }

  function openNewTask() {
    setEditingTask(null)
    setTaskOpen(true)
  }

  return (
    <main className="min-h-svh bg-background text-foreground">
      <Toaster richColors closeButton />
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-5 md:px-6">
        <header className="flex flex-col gap-4 border-b pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <ListChecksIcon data-icon="inline-start" />
              Yellka Terminal
            </div>
            <h1 className="text-3xl font-semibold tracking-normal md:text-4xl">
              Task Tracker
            </h1>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Input
              className="h-8 w-full sm:w-64"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              aria-label="Web token"
            />
            <Button variant="outline" onClick={() => void load()} disabled={loading}>
              <RefreshCwIcon data-icon="inline-start" />
              Refresh
            </Button>
            <Dialog open={taskOpen} onOpenChange={setTaskOpen}>
              <DialogTrigger asChild>
                <Button onClick={openNewTask}>
                  <PlusIcon data-icon="inline-start" />
                  Task
                </Button>
              </DialogTrigger>
              <TaskDialog
                task={editingTask}
                categories={categories}
                catalog={catalog}
                vectors={vectors}
                onSave={(payload) => void saveTask(payload)}
              />
            </Dialog>
          </div>
        </header>

        <section className="grid gap-3 md:grid-cols-4">
          <Metric label="AP" value={wallet.currencies.ap ? `${wallet.currencies.ap} AP` : `${balance.balance} AP`} />
          <Metric label="Shadow" value={wallet.currencies.shadow_ap || "0.000"} />
          <Metric label="Base" value={`${balance.base_rate} AP`} />
          <Metric label="Next core" value={`${balance.next_core_cost} AP`} />
        </section>

        <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
          <Tabs value={mainTab} onValueChange={setMainTab}>
            <TabsList className="w-full justify-start overflow-x-auto">
              <TabsTrigger value="tasks">Tasks</TabsTrigger>
              {visibleShopTabs.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value}>
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <Dialog open={sectionsOpen} onOpenChange={setSectionsOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm">
                <SlidersHorizontalIcon data-icon="inline-start" />
                Sections
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Visible Sections</DialogTitle>
                <DialogDescription className="sr-only">
                  Choose which shop sections are shown in the navigation.
                </DialogDescription>
              </DialogHeader>
              <div className="flex max-h-80 flex-col gap-2 overflow-y-auto">
                {SHOP_TABS.map((tab) => (
                  <label
                    key={tab.value}
                    className="flex items-center justify-between gap-3 rounded-lg border bg-background px-3 py-2 text-sm"
                  >
                    <span>{tab.label}</span>
                    <Checkbox
                      checked={!hiddenShopSections.includes(tab.value)}
                      onCheckedChange={(checked) => toggleShopSection(tab.value, checked === true)}
                    />
                  </label>
                ))}
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setHiddenShopSections([])}>
                  Show all
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>

        {mainTab === "tasks" ? (
        <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="flex min-w-0 flex-col gap-4">
            <div className="flex flex-col gap-3 border-b pb-4 xl:flex-row xl:items-center xl:justify-between">
              <Tabs
                value={activeStatus}
                onValueChange={(value) => setActiveStatus(value as "all" | TrackerStatus)}
              >
                <TabsList>
                  <TabsTrigger value="all">All {tasks.length}</TabsTrigger>
                  <TabsTrigger value="draft">Draft {counts.draft}</TabsTrigger>
                  <TabsTrigger value="done">Done {counts.done}</TabsTrigger>
                  <TabsTrigger value="submitted">Submitted {counts.submitted}</TabsTrigger>
                </TabsList>
              </Tabs>
              <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                <SelectTrigger className="w-full xl:w-64">
                  <SelectValue placeholder="Category" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="all">All categories</SelectItem>
                    <SelectItem value={NO_VALUE}>Uncategorized</SelectItem>
                    {categories.map((category) => (
                      <SelectItem key={category.category} value={category.category}>
                        {category.category}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>

            <div className="overflow-hidden rounded-lg border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10"></TableHead>
                    <TableHead>Task</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Formula</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visibleTasks.map((task) => (
                    <TableRow key={task.id}>
                      <TableCell>
                        <Checkbox
                          checked={task.status !== "draft"}
                          disabled={task.status === "submitted"}
                          onCheckedChange={(checked) => {
                            if (checked) {
                              void refreshAfter(
                                () => api.markDone(token, task.id),
                                "Task marked done",
                              )
                            }
                          }}
                          aria-label={`Mark ${task.title} done`}
                        />
                      </TableCell>
                      <TableCell>
                        <div className="flex min-w-48 flex-col gap-1">
                          <span className="font-medium">{task.title}</span>
                          {task.note ? (
                            <span className="line-clamp-1 text-xs text-muted-foreground">
                              {task.note}
                            </span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>{task.category || "Uncategorized"}</TableCell>
                      <TableCell>
                        <span className="text-sm text-muted-foreground">
                          {task.units}u · {task.vector} · x{task.catalog_value}
                        </span>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={task.status} />
                        {task.status === "submitted" ? (
                          <span className="ml-2 text-xs text-muted-foreground">
                            +{task.submitted_reward} AP
                          </span>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={task.status === "submitted"}
                            onClick={() => openEditTask(task)}
                          >
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            disabled={task.status !== "done"}
                            onClick={() => {
                              void refreshAfter(
                                () => api.submitTask(token, task.id),
                                "Task submitted",
                              )
                            }}
                          >
                            <SendIcon data-icon="inline-start" />
                            Submit
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!visibleTasks.length ? (
                    <TableRow>
                      <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                        No tasks
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </div>
          </div>

          <aside className="flex flex-col gap-4">
            <div className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-base font-medium">Categories</h2>
                <Dialog open={categoryOpen} onOpenChange={setCategoryOpen}>
                  <DialogTrigger asChild>
                    <Button variant="outline" size="sm">
                      <PlusIcon data-icon="inline-start" />
                      Category
                    </Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>New Category</DialogTitle>
                      <DialogDescription className="sr-only">
                        Create a task category.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor="category-name">Name</Label>
                      <Input
                        id="category-name"
                        value={newCategory}
                        onChange={(event) => setNewCategory(event.target.value)}
                      />
                    </div>
                    <DialogFooter>
                      <Button onClick={() => void createCategory()}>
                        <SaveIcon data-icon="inline-start" />
                        Save
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </div>
              <div className="mt-4 flex flex-col gap-2">
                {categories.map((category) => (
                  <div
                    key={category.category}
                    className="flex items-center justify-between gap-3 rounded-lg border bg-background p-3"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{category.category}</div>
                      <div className="text-xs text-muted-foreground">
                        {category.task_count} tasks · {category.premium_pending_total} AP pending
                      </div>
                    </div>
                    <Button
                      variant={category.completed ? "outline" : "secondary"}
                      size="sm"
                      onClick={() => {
                        void refreshAfter(
                          () =>
                            category.completed
                              ? api.reopenCategory(token, category.category)
                              : api.completeCategory(token, category.category),
                          category.completed ? "Category reopened" : "Category completed",
                        )
                      }}
                    >
                      {category.completed ? "Open" : "Close"}
                    </Button>
                  </div>
                ))}
                {!categories.length ? (
                  <div className="rounded-lg border bg-background p-4 text-sm text-muted-foreground">
                    No categories
                  </div>
                ) : null}
              </div>
            </div>

            <div className="rounded-lg border bg-card p-4">
              <h2 className="text-base font-medium">System</h2>
              <div className="mt-4 grid gap-3 text-sm">
                <InfoLine label="Discount" value={`${balance.cashback_percent}%`} />
                <InfoLine
                  label="Retro"
                  value={balance.retroactive_indexing_enabled ? "enabled" : "off"}
                />
                <InfoLine
                  label="Vectors"
                  value={Object.values(balance.vector_levels).reduce((sum, level) => sum + level, 0).toString()}
                />
              </div>
            </div>
          </aside>
        </section>
        ) : (
          <ShopWorkspace
            tab={mainTab}
            token={token}
            catalog={shopCatalog}
            purchases={shopPurchases}
            wallet={wallet}
            effects={effects}
            prime={prime}
            expeditions={expeditions}
            cabins={cabins}
            vectors={vectors.filter((vector) => vector.key !== "media")}
            onChanged={() => refreshAfter(async () => {}, "Updated")}
          />
        )}
      </div>
    </main>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  )
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b pb-2 last:border-b-0 last:pb-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  )
}

function StatusBadge({ status }: { status: TrackerStatus }) {
  if (status === "submitted") {
    return (
      <Badge>
        <ClipboardCheckIcon data-icon="inline-start" />
        {STATUS_LABEL[status]}
      </Badge>
    )
  }
  if (status === "done") {
    return (
      <Badge variant="secondary">
        <CheckIcon data-icon="inline-start" />
        {STATUS_LABEL[status]}
      </Badge>
    )
  }
  return <Badge variant="outline">{STATUS_LABEL[status]}</Badge>
}

function ShopWorkspace({
  tab,
  token,
  catalog,
  purchases,
  wallet,
  effects,
  prime,
  expeditions,
  cabins,
  vectors,
  onChanged,
}: {
  tab: string
  token: string
  catalog: ShopItem[]
  purchases: ShopPurchase[]
  wallet: Wallet
  effects: ActiveEffect[]
  prime: PrimeStatus
  expeditions: Expedition[]
  cabins: Cabin[]
  vectors: VectorItem[]
  onChanged: () => Promise<void>
}) {
  const sectionMap: Record<string, string[]> = {
    terminal: ["terminal"],
    hub: ["hub"],
    expedition: ["expedition"],
    world: ["world"],
    recreation: ["recreation"],
    genesis: ["genesis"],
    prime: ["prime"],
    noctur: ["noctur"],
  }
  const items = catalog.filter((item) => sectionMap[tab]?.includes(item.section))
  const [selectedKey, setSelectedKey] = useState("")
  const selectedItem = items.find((item) => item.key === selectedKey) || items[0]

  useEffect(() => {
    if (!items.length) {
      setSelectedKey("")
      return
    }
    if (!items.some((item) => item.key === selectedKey)) {
      setSelectedKey(items[0].key)
    }
  }, [items, selectedKey])

  if (tab === "history") {
    return (
      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="overflow-hidden rounded-lg border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Purchase</TableHead>
                <TableHead>Section</TableHead>
                <TableHead>Cost</TableHead>
                <TableHead>Target</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {purchases.map((purchase) => (
                <TableRow key={purchase.id}>
                  <TableCell>{purchase.title}</TableCell>
                  <TableCell>{purchase.section}</TableCell>
                  <TableCell>{purchase.final_cost} {purchase.currency}</TableCell>
                  <TableCell>{purchase.target || "-"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <StatusPanel wallet={wallet} effects={effects} prime={prime} expeditions={expeditions} cabins={cabins} />
      </section>
    )
  }
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-3">
        {items.length ? (
          <>
            <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div className="flex min-w-0 flex-col gap-1">
                <h2 className="text-base font-medium">Purchase</h2>
                <span className="text-xs text-muted-foreground">{items.length} entries</span>
              </div>
              <Select value={selectedItem?.key || ""} onValueChange={setSelectedKey}>
                <SelectTrigger className="w-full md:w-80">
                  <SelectValue placeholder="Pick item" />
                </SelectTrigger>
                <SelectContent className="max-h-72">
                  <SelectGroup>
                    {items.map((item) => (
                      <SelectItem key={item.key} value={item.key}>
                        {item.title} · {item.base_cost} {item.currency}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
            {selectedItem ? (
              <ShopPurchasePanel
                item={selectedItem}
                token={token}
                vectors={vectors}
                onChanged={onChanged}
              />
            ) : null}
            <div className="max-h-80 overflow-y-auto rounded-lg border">
              <Table>
                <TableBody>
                  {items.map((item) => (
                    <TableRow
                      key={item.key}
                      className={item.key === selectedItem?.key ? "bg-muted/50" : ""}
                      onClick={() => setSelectedKey(item.key)}
                    >
                      <TableCell className="py-2">
                        <div className="flex min-w-0 flex-col">
                          <span className="truncate text-sm font-medium">{item.title}</span>
                          <span className="truncate text-xs text-muted-foreground">
                            {item.description || item.effect_kind}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="w-28 py-2 text-right text-sm">
                        {item.base_cost} {item.currency}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        ) : (
          <div className="rounded-lg border bg-background p-5 text-sm text-muted-foreground">
            No catalog entries in this section yet
          </div>
        )}
      </div>
      <StatusPanel wallet={wallet} effects={effects} prime={prime} expeditions={expeditions} cabins={cabins} />
    </section>
  )
}

function ShopPurchasePanel({
  item,
  token,
  vectors,
  onChanged,
}: {
  item: ShopItem
  token: string
  vectors: VectorItem[]
  onChanged: () => Promise<void>
}) {
  const [target, setTarget] = useState("")
  const [quantity, setQuantity] = useState(1)
  const [note, setNote] = useState("")
  const [showNote, setShowNote] = useState(false)
  const [quote, setQuote] = useState<string>(item.base_cost)
  const [available, setAvailable] = useState(true)

  const needsTarget = ["terminal.vector", "hub.genre_focus", "hub.attribute", "hub.optimization"].includes(item.key)

  useEffect(() => {
    setTarget("")
    setQuantity(1)
    setNote("")
    setShowNote(false)
  }, [item.key])

  const refreshQuote = useCallback(async (next?: Partial<ShopPayload>) => {
    try {
      const payload = {
        item_key: item.key,
        target: item.key === "terminal.vector" ? target || vectors[0]?.key || "" : target,
        quantity,
        options: {},
        ...next,
      }
      const result = await api.quoteShop(token, payload)
      setQuote(`${result.final_cost} ${result.currency}`)
      setAvailable(result.available)
    } catch (error) {
      setAvailable(false)
      setQuote(error instanceof Error ? error.message : "quote failed")
    }
  }, [item.key, quantity, target, token, vectors])

  useEffect(() => {
    void refreshQuote()
  }, [refreshQuote])

  async function purchase() {
    try {
      await api.purchaseShop(token, {
        item_key: item.key,
        target: item.key === "terminal.vector" ? target || vectors[0]?.key || "" : target,
        quantity,
        note,
        options: {},
      })
      toast.success("Purchase recorded")
      await onChanged()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Purchase failed")
    }
  }

  return (
    <div className="rounded-lg border bg-background p-3">
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-medium">{item.title}</h2>
              <Badge variant="outline">{item.currency}</Badge>
            </div>
            {item.description ? (
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{item.description}</p>
            ) : null}
          </div>
          <div className="text-sm font-medium">{quote}</div>
        </div>

        <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_96px_auto_auto] md:items-end">
          {needsTarget ? (
            item.key === "terminal.vector" ? (
              <Select value={target || vectors[0]?.key || ""} onValueChange={setTarget}>
                <SelectTrigger className="h-8">
                  <SelectValue placeholder="Vector" />
                </SelectTrigger>
                <SelectContent className="max-h-64">
                  <SelectGroup>
                    {vectors.map((vector) => (
                      <SelectItem key={vector.key} value={vector.key}>{vector.title}</SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            ) : (
              <Input className="h-8" value={target} onChange={(event) => setTarget(event.target.value)} placeholder="Target" />
            )
          ) : (
            <div className="hidden md:block" />
          )}
          {item.cost_formula !== "core" && item.cost_formula !== "vector" && item.cost_formula !== "cashback" && item.cost_formula !== "retro_buffer" ? (
            <Input
              className="h-8"
              type="number"
              min={1}
              value={quantity}
              onChange={(event) => setQuantity(Number(event.target.value || 1))}
            />
          ) : (
            <div className="hidden md:block" />
          )}
          <Button
            variant={showNote ? "secondary" : "outline"}
            size="sm"
            onClick={() => setShowNote((current) => !current)}
          >
            <StickyNoteIcon data-icon="inline-start" />
            Note
          </Button>
          <Button size="sm" disabled={!available || (needsTarget && !target && item.key !== "terminal.vector")} onClick={() => void purchase()}>
            <ShoppingCartIcon data-icon="inline-start" />
            Buy
          </Button>
          {showNote ? (
            <Textarea
              className="min-h-16 md:col-span-4"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Optional note"
            />
          ) : null}
        </div>
      </div>
    </div>
  )
}

function StatusPanel({
  wallet,
  effects,
  prime,
  expeditions,
  cabins,
}: {
  wallet: Wallet
  effects: ActiveEffect[]
  prime: PrimeStatus
  expeditions: Expedition[]
  cabins: Cabin[]
}) {
  return (
    <aside className="flex flex-col gap-4">
      <div className="rounded-lg border bg-card p-4">
        <h2 className="text-base font-medium">Wallet</h2>
        <div className="mt-4 grid gap-3 text-sm">
          {Object.entries(wallet.currencies).map(([key, value]) => (
            <InfoLine key={key} label={key} value={value} />
          ))}
          <InfoLine label="PRIME" value={prime.active ? `${prime.weeks_purchased} weeks` : "off"} />
        </div>
      </div>
      <div className="rounded-lg border bg-card p-4">
        <h2 className="text-base font-medium">State</h2>
        <div className="mt-4 grid gap-2 text-sm text-muted-foreground">
          <div>{effects.length} active modifiers</div>
          <div>{expeditions.length} expeditions</div>
          <div>{cabins.length} cabins</div>
        </div>
      </div>
    </aside>
  )
}

function TaskDialog({
  task,
  categories,
  catalog,
  vectors,
  onSave,
}: {
  task: TrackerTask | null
  categories: Category[]
  catalog: CatalogItem[]
  vectors: VectorItem[]
  onSave: (payload: TaskPayload) => void
}) {
  const [form, setForm] = useState<TaskPayload>(EMPTY_PAYLOAD)

  useEffect(() => {
    if (!task) {
      setForm(EMPTY_PAYLOAD)
      return
    }
    setForm({
      title: task.title,
      category: task.category,
      vector: task.vector,
      units: task.units,
      catalog_key: task.catalog_key,
      catalog_value: task.catalog_value,
      priority: task.priority,
      full_close: task.full_close,
      note: task.note,
    })
  }, [task])

  function update<K extends keyof TaskPayload>(key: K, value: TaskPayload[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function chooseCatalog(value: string) {
    if (value === NO_VALUE) {
      setForm((current) => ({ ...current, catalog_key: "" }))
      return
    }
    const item = catalog.find((candidate) => candidate.key === value)
    setForm((current) => ({
      ...current,
      catalog_key: value,
      catalog_value: item?.value || current.catalog_value,
    }))
  }

  function submit() {
    if (!form.title.trim()) {
      toast.error("Task title is required")
      return
    }
    onSave({ ...form, title: form.title.trim(), note: form.note.trim() })
  }

  return (
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{task ? "Edit Task" : "New Task"}</DialogTitle>
        <DialogDescription className="sr-only">
          Configure task reward inputs.
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-2 md:col-span-2">
          <Label htmlFor="task-title">Title</Label>
          <Input
            id="task-title"
            value={form.title}
            onChange={(event) => update("title", event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label>Category</Label>
          <Select
            value={form.category || NO_VALUE}
            onValueChange={(value) => update("category", value === NO_VALUE ? "" : value)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={NO_VALUE}>Uncategorized</SelectItem>
                {categories.map((category) => (
                  <SelectItem key={category.category} value={category.category}>
                    {category.category}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-2">
          <Label>Vector</Label>
          <Select value={form.vector} onValueChange={(value) => update("vector", value)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {vectors.map((vector) => (
                  <SelectItem key={vector.key} value={vector.key}>
                    {vector.title}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="task-units">Units</Label>
          <Input
            id="task-units"
            type="number"
            min={1}
            value={form.units}
            onChange={(event) => update("units", Number(event.target.value || 1))}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label>Catalog</Label>
          <Select
            value={form.catalog_key || NO_VALUE}
            onValueChange={chooseCatalog}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={NO_VALUE}>Manual value</SelectItem>
                {catalog.map((item) => (
                  <SelectItem key={item.key} value={item.key}>
                    {item.title} · {item.value}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="task-value">Value</Label>
          <Input
            id="task-value"
            value={form.catalog_value}
            disabled={Boolean(form.catalog_key)}
            onChange={(event) => update("catalog_value", event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-3 rounded-lg border bg-background p-3">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={form.priority}
              onCheckedChange={(checked) => update("priority", checked === true)}
            />
            Priority
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={form.full_close}
              onCheckedChange={(checked) => update("full_close", checked === true)}
            />
            Full close
          </label>
        </div>
        <div className="flex flex-col gap-2 md:col-span-2">
          <Label htmlFor="task-note">Note</Label>
          <Textarea
            id="task-note"
            value={form.note}
            onChange={(event) => update("note", event.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button onClick={submit}>
          <SaveIcon data-icon="inline-start" />
          Save
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

export default App
