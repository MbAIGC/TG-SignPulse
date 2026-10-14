import { ref } from 'vue'

export interface ConfirmOptions {
  title?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}

interface ConfirmState extends ConfirmOptions {
  message: string
  resolve: ((value: boolean) => void) | null
}

const state = ref<ConfirmState | null>(null)

/**
 * 全局确认弹层：替代原生 confirm()，保证深色模式与移动端体验一致。
 * 用法：`if (!(await confirmDialog(message, { danger: true }))) return`
 */
export const confirmDialog = (
  message: string,
  options: ConfirmOptions = {}
): Promise<boolean> => {
  // 上一个弹层未关闭时按取消处理，避免 Promise 悬挂
  state.value?.resolve?.(false)
  return new Promise<boolean>((resolve) => {
    state.value = { message, ...options, resolve }
  })
}

export const useConfirm = () => {
  const answer = (value: boolean) => {
    const current = state.value
    state.value = null
    current?.resolve?.(value)
  }

  return { state, answer }
}
