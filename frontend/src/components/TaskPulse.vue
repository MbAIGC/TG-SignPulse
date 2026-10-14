<script setup lang="ts">
import { computed } from 'vue'

export interface PulseDay {
  date: string
  success: number
  failed: number
}

const props = withDefaults(
  defineProps<{
    days: PulseDay[]
    height?: number
    /** 悬浮提示语言：zh | en */
    locale?: string
  }>(),
  { height: 28, locale: 'zh' },
)

const bars = computed(() =>
  (props.days || []).map((day) => {
    const total = (day.success || 0) + (day.failed || 0)
    let state: 'none' | 'ok' | 'bad' | 'mixed' = 'none'
    if (total > 0) {
      if ((day.failed || 0) === 0) state = 'ok'
      else if ((day.success || 0) === 0) state = 'bad'
      else state = 'mixed'
    }
    const label =
      props.locale === 'en'
        ? `${day.date}: ${day.success} success / ${day.failed} failed`
        : `${day.date}：成功 ${day.success} / 失败 ${day.failed}`
    return { key: day.date, state, label }
  }),
)
</script>

<template>
  <div class="flex items-end gap-[3px] w-full" :style="{ height: `${height}px` }">
    <div
      v-for="bar in bars"
      :key="bar.key"
      class="flex-1 rounded-[3px] transition-colors"
      :class="{
        'bg-gray-200 dark:bg-gray-800': bar.state === 'none',
        'bg-emerald-500': bar.state === 'ok',
        'bg-rose-500': bar.state === 'bad',
        'bg-amber-400': bar.state === 'mixed',
      }"
      :style="{ height: `${height}px` }"
      :title="bar.label"
    />
  </div>
</template>
