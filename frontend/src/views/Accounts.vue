<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Play, FileText, Edit2, Trash2, Plus, QrCode, Phone, Zap, AlertTriangle } from '@lucide/vue'
import { listAccounts, deleteAccount, checkAccountsStatus } from '../lib/api'
import { useI18n } from '../composables/useI18n'
import { useToast } from '../composables/useToast'
import { confirmDialog } from '../composables/useConfirm'
import AddAccountModal from '../components/accounts/AddAccountModal.vue'
import EditAccountModal from '../components/accounts/EditAccountModal.vue'

const router = useRouter()
const { t } = useI18n()
const toast = useToast()
const accounts = ref<any[]>([])
const pageLoading = ref(true)
const showAddModal = ref(false)
const showEditModal = ref(false)
const showAddMenu = ref(false)
const initialMethod = ref<'code' | 'qr'>('code')
const initialAccountName = ref('')
const editingAccount = ref<any>(null)

const loadAccounts = async () => {
  const token = localStorage.getItem('tg-signer-token') || ''
  if (!token) return

  try {
    const res = await listAccounts(token)
    accounts.value = res.accounts.map(acc => {
      let uiStatus = 'active'
      let message = ''

      if (acc.needs_relogin || acc.status === 'invalid') {
        uiStatus = 'error'
        message = t('accounts.loginExpired')
      } else if (acc.status === 'error') {
        uiStatus = 'error'
        message = acc.status_message || ''
      } else if (acc.status === 'checking') {
        uiStatus = 'empty'
        message = t('accounts.checking')
      } else if (acc.status_message?.includes('流量') || acc.status_message?.includes('额度')) {
        uiStatus = 'empty'
        message = acc.status_message
      }

      return {
        id: acc.name,
        name: acc.name,
        remark: acc.remark,
        status: uiStatus,
        needsRelogin: Boolean(acc.needs_relogin || acc.status === 'invalid'),
        message: message,
        avatarUrl: '',
        avatarLoaded: false,
        raw: acc
      }
    })
    // Load avatars with auth token
    for (const acc of accounts.value) {
      loadAvatar(acc)
    }
  } catch (e) {
    console.error('Failed to fetch accounts', e)
  } finally {
    pageLoading.value = false
  }
}

const loadAvatar = async (acc: any) => {
  const token = localStorage.getItem('tg-signer-token') || ''
  try {
    const res = await fetch(`/api/accounts/${encodeURIComponent(acc.name)}/avatar`, {
      headers: { Authorization: `Bearer ${token}` }
    })
    if (res.ok) {
      const blob = await res.blob()
      acc.avatarUrl = URL.createObjectURL(blob)
    }
  } catch {
    // No avatar available, keep fallback
  }
  acc.avatarLoaded = true
}

onMounted(() => {
  loadAccounts()
})

const handleDelete = async (name: string) => {
  const confirmed = await confirmDialog(`${t('accounts.deleteConfirm')} ${name} ?`, {
    danger: true,
  })
  if (!confirmed) return
  const token = localStorage.getItem('tg-signer-token') || ''
  try {
    await deleteAccount(token, name)
    await loadAccounts()
  } catch (e) {
    toast.error(t('accounts.deleteFailed'))
  }
}

const checkingAccount = ref('')

// 失效账号提醒卡：一眼看到需要重新登录的账号
const invalidAccounts = computed(() =>
  accounts.value.filter((acc: any) => acc.needsRelogin)
)
const handleReloginFirst = () => {
  const first = invalidAccounts.value[0]
  if (first) handleRelogin(first.name)
}

const handleCheck = async (name: string) => {
  const token = localStorage.getItem('tg-signer-token') || ''
  checkingAccount.value = name
  try {
    const res = await checkAccountsStatus(token, { account_names: [name] })
    await loadAccounts()
    // Show result
    const result = res.results?.[0]
    if (result) {
      if (result.ok) {
        toast.success(`${name}: ${t('accounts.checkOk')}`)
      } else {
        toast.error(`${name}: ${result.message || t('accounts.loginExpired')}`)
      }
    }
  } catch (e) {
    toast.error(t('accounts.checkFailed'))
  } finally {
    checkingAccount.value = ''
  }
}

const openEdit = (acc: any) => {
  editingAccount.value = acc
  showEditModal.value = true
}

const handleRelogin = (name: string) => {
  showEditModal.value = false
  setTimeout(() => {
    initialAccountName.value = name
    initialMethod.value = 'code'
    showAddModal.value = true
  }, 300)
}

const openAddModal = (method: 'code' | 'qr') => {
  initialAccountName.value = ''
  initialMethod.value = method
  showAddMenu.value = false
  showAddModal.value = true
}

const goLogs = (name: string) => {
  router.push({ name: 'logs', query: { account: name } })
}

const goTasks = (name: string) => {
  router.push({ name: 'tasks', query: { account: name } })
}
</script>

<template>
  <div class="relative min-h-[80vh]">
    <!-- Page Loading -->
    <div v-if="pageLoading" class="flex items-center justify-center py-20">
      <svg class="animate-spin w-6 h-6 text-gray-400" viewBox="0 0 24 24" fill="none"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
    </div>

    <!-- 失效账号提醒 -->
    <div
      v-else-if="invalidAccounts.length > 0"
      class="mb-5 flex items-start sm:items-center gap-3 rounded-xl border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-500/10 p-4"
    >
      <div class="w-9 h-9 shrink-0 rounded-lg bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400 flex items-center justify-center">
        <AlertTriangle class="w-5 h-5" stroke-width="1.5" />
      </div>
      <div class="flex-1 min-w-0">
        <p class="text-sm font-medium text-amber-900 dark:text-amber-200">
          {{ invalidAccounts.length }} {{ t('accounts.reloginNeeded') }}
        </p>
        <p class="mt-0.5 text-xs text-amber-800/80 dark:text-amber-300/80 break-all">
          {{ invalidAccounts.map((acc: any) => acc.name).join('、') }}
        </p>
      </div>
      <button
        @click="handleReloginFirst"
        class="shrink-0 px-3 py-1.5 text-xs rounded-full bg-blue-600 text-white hover:bg-blue-700 transition-colors"
      >
        {{ t('accounts.relogin') }}
      </button>
    </div>

    <!-- Empty State -->
    <div v-else-if="accounts.length === 0" class="flex flex-col items-center justify-center py-20 text-center">
      <div class="w-16 h-16 bg-gray-100 dark:bg-gray-900 border border-gray-200 dark:border-gray-800/60 flex items-center justify-center mb-4">
        <svg class="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
      </div>
      <p class="text-sm text-gray-900 dark:text-gray-100 font-medium mb-1">{{ t('accounts.empty') }}</p>
      <p class="text-xs text-gray-500">{{ t('accounts.emptyHint') }}</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4 pb-20">
    <div 
      v-for="acc in accounts" :key="acc.id"
      class="group relative flex flex-col p-5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800/60 hover:border-gray-300 dark:hover:border-gray-700 transition-colors"
    >
      <div class="flex justify-between items-start mb-4">
        <div class="flex items-center gap-3 truncate max-w-[70%]">
          <div class="w-9 h-9 shrink-0 bg-gray-50 dark:bg-gray-950 flex items-center justify-center text-xs text-gray-500 font-mono border border-gray-200 dark:border-gray-800/40 overflow-hidden rounded-sm">
            <img 
              v-if="acc.avatarUrl" 
              :src="acc.avatarUrl" 
              :alt="acc.name"
              class="w-full h-full object-cover"
            />
            <span v-else>{{ acc.name.substring(0, 2) }}</span>
          </div>
          <div class="truncate">
            <div class="text-sm font-medium text-gray-900 dark:text-gray-200 truncate" :title="acc.name">{{ acc.name }}</div>
            <div class="text-xs text-gray-500 mt-0.5 font-mono truncate" :title="acc.remark || t('accounts.noRemark')">{{ acc.remark || t('accounts.noRemark') }}</div>
          </div>
        </div>
        
        <!-- Status Indicator -->
        <div class="flex items-center gap-2 shrink-0">
          <span v-if="acc.status !== 'active'" class="text-[10px]" :class="acc.status === 'empty' ? 'text-amber-600 dark:text-amber-500/70' : 'text-rose-600 dark:text-rose-500'">
            {{ acc.message }}
          </span>
          <div 
            class="w-1.5 h-1.5 rounded-full"
            :class="{
              'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]': acc.status === 'active',
              'bg-amber-500/60': acc.status === 'empty',
              'bg-rose-500': acc.status === 'error'
            }"
          ></div>
        </div>
      </div>

      <!-- Actions -->
      <div class="mt-auto pt-3 border-t border-gray-100 dark:border-gray-800/40 grid grid-cols-5 gap-1">
        <button @click="handleCheck(acc.name)" :disabled="checkingAccount === acc.name" class="flex flex-col items-center gap-0.5 py-1.5 text-gray-500 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded transition-colors disabled:opacity-50" :title="t('accounts.checkStatus')">
          <svg v-if="checkingAccount === acc.name" class="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
          <Play v-else class="w-3.5 h-3.5" />
          <span class="text-[10px]">{{ t('accounts.check') }}</span>
        </button>
        <button @click="goTasks(acc.name)" class="flex flex-col items-center gap-0.5 py-1.5 text-gray-500 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded transition-colors" :title="t('accounts.viewTasks')">
          <Zap class="w-3.5 h-3.5" />
          <span class="text-[10px]">{{ t('accounts.tasks') }}</span>
        </button>
        <button @click="goLogs(acc.name)" class="flex flex-col items-center gap-0.5 py-1.5 text-gray-500 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded transition-colors" :title="t('accounts.viewLogs')">
          <FileText class="w-3.5 h-3.5" />
          <span class="text-[10px]">{{ t('accounts.logs') }}</span>
        </button>
        <button @click="openEdit(acc.raw)" class="flex flex-col items-center gap-0.5 py-1.5 text-gray-500 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded transition-colors" :title="t('accounts.edit')">
          <Edit2 class="w-3.5 h-3.5" />
          <span class="text-[10px]">{{ t('accounts.editBtn') }}</span>
        </button>
        <button @click="handleDelete(acc.name)" class="flex flex-col items-center gap-0.5 py-1.5 text-gray-500 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-500/10 rounded transition-colors" :title="t('accounts.deleteBtn')">
          <Trash2 class="w-3.5 h-3.5" />
          <span class="text-[10px]">{{ t('accounts.deleteBtn') }}</span>
        </button>
      </div>
    </div>
    </div>
    
    <!-- FAB for Adding Account -->
    <div class="fixed bottom-6 right-6 lg:bottom-8 lg:right-8 z-40 flex flex-col items-end gap-2">
      <transition enter-active-class="transition duration-200 ease-out" enter-from-class="opacity-0 translate-y-2" enter-to-class="opacity-100 translate-y-0" leave-active-class="transition duration-150 ease-in" leave-from-class="opacity-100 translate-y-0" leave-to-class="opacity-0 translate-y-2">
        <div v-if="showAddMenu" class="flex flex-col gap-1.5 mb-2">
          <button @click="openAddModal('qr')" class="flex items-center gap-2.5 px-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800/60 shadow-lg text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-700 transition-all">
            <QrCode class="w-4 h-4 text-gray-500" /> {{ t('accounts.qrLogin') }}
          </button>
          <button @click="openAddModal('code')" class="flex items-center gap-2.5 px-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800/60 shadow-lg text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-700 transition-all">
            <Phone class="w-4 h-4 text-gray-500" /> {{ t('accounts.codeLogin') }}
          </button>
        </div>
      </transition>
      
      <button 
        @click="showAddMenu = !showAddMenu"
        class="w-11 h-11 flex items-center justify-center bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-950 border border-gray-800 dark:border-gray-200 shadow-md hover:shadow-lg hover:-translate-y-0.5 active:scale-95 transition-all duration-200"
      >
        <Plus class="w-5 h-5 transition-transform duration-200" :class="{ 'rotate-45': showAddMenu }" />
      </button>
    </div>

    <!-- Modals -->
    <AddAccountModal :isOpen="showAddModal" :initialMethod="initialMethod" :initialAccountName="initialAccountName" @close="showAddModal = false" @success="loadAccounts" />
    <EditAccountModal :isOpen="showEditModal" :account="editingAccount" @close="showEditModal = false" @success="loadAccounts" @relogin="handleRelogin" />
  </div>
</template>
