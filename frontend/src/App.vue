<script setup lang="ts">
import GlobalConfirm from './components/GlobalConfirm.vue'
import GlobalToast from './components/GlobalToast.vue'
</script>

<template>
  <router-view />
  <GlobalToast />
  <GlobalConfirm />
</template>
