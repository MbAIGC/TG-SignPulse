<script setup lang="ts">
import { watch } from 'vue'
import { useConfirm } from '../composables/useConfirm'

const { state, answer } = useConfirm()

// 打开弹层时锁定滚动，避免移动端背景跟着滑动
watch(state, (value) => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = value ? 'hidden' : ''
})

const onKeydown = (event: KeyboardEvent) => {
  if (!state.value) return
  if (event.key === 'Escape') answer(false)
  if (event.key === 'Enter') answer(true)
}
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition duration-100 ease-in"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="state"
        class="fixed inset-0 z-[300] flex items-center justify-center p-4 bg-black/50"
        role="dialog"
        aria-modal="true"
        tabindex="-1"
        @keydown="onKeydown"
        @click.self="answer(false)"
      >
        <div
          class="w-full max-w-sm rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800/60 shadow-xl p-5"
        >
          <h3 v-if="state.title" class="text-base font-semibold text-gray-900 dark:text-gray-100">
            {{ state.title }}
          </h3>
          <p class="mt-1 text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words">
            {{ state.message }}
          </p>
          <div class="mt-5 flex justify-end gap-2">
            <button
              type="button"
              class="px-4 py-2 text-sm rounded-full border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800"
              @click="answer(false)"
            >
              {{ state.cancelText || '取消' }}
            </button>
            <button
              type="button"
              class="px-4 py-2 text-sm rounded-full text-white"
              :class="state.danger ? 'bg-rose-600 hover:bg-rose-700' : 'bg-blue-600 hover:bg-blue-700'"
              @click="answer(true)"
            >
              {{ state.confirmText || '确定' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
