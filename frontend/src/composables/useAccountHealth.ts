import { onMounted, onUnmounted, ref } from 'vue'
import { listAccounts } from '../lib/api'

export interface AccountHealthItem {
  name: string
  status: string
  statusMessage: string
  needsRelogin: boolean
}

/**
 * 账号健康状态（失效账号可见性）：
 * 侧栏角标与账号页提醒卡共用，避免各自重复请求。
 */
const accounts = ref<AccountHealthItem[]>([])
const loaded = ref(false)
let inflight: Promise<void> | null = null

const normalize = (raw: any): AccountHealthItem => ({
  name: String(raw?.name || ''),
  status: String(raw?.status || ''),
  statusMessage: String(raw?.status_message || ''),
  needsRelogin: Boolean(raw?.needs_relogin),
})

export const refreshAccountHealth = async (): Promise<void> => {
  if (inflight) return inflight
  const token = localStorage.getItem('tg-signer-token') || ''
  if (!token) return
  inflight = (async () => {
    try {
      const res: any = await listAccounts(token)
      const items = Array.isArray(res) ? res : res?.accounts || []
      accounts.value = items.map(normalize)
      loaded.value = true
    } catch {
      // 健康状态是辅助信息，请求失败不影响主流程
    } finally {
      inflight = null
    }
  })()
  return inflight
}

export const useAccountHealth = () => {
  const invalidAccounts = () => accounts.value.filter((item) => item.needsRelogin)
  const invalidCount = () => invalidAccounts().length

  onMounted(() => {
    void refreshAccountHealth()
  })

  return {
    accounts,
    loaded,
    invalidAccounts,
    invalidCount,
    refresh: refreshAccountHealth,
  }
}

/** 供非组件场景（如轮询）清理定时器 */
export const useAccountHealthPolling = (intervalMs = 60_000) => {
  let timer: ReturnType<typeof setInterval> | null = null
  onMounted(() => {
    timer = setInterval(() => void refreshAccountHealth(), intervalMs)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
    timer = null
  })
}
