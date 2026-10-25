<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  options: { type: Array, required: true }, // [{value, label}]
  minWidth: { type: String, default: '96px' }
})

const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const selected = computed(
  () => props.options.find((o) => o.value === props.modelValue) || { label: '请选择' }
)

function choose(option) {
  emit('update:modelValue', option.value)
  open.value = false
}
</script>

<template>
  <div class="app-select" :style="{ minWidth }">
    <button
      type="button"
      class="app-select-trigger"
      :class="{ open }"
      @click="open = !open"
    >
      <span class="app-select-label">{{ selected.label }}</span>
      <svg
        class="app-select-arrow"
        :class="{ open }"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>
    <div v-if="open" class="app-select-backdrop" @click="open = false"></div>
    <transition name="select-fade">
      <ul v-if="open" class="app-select-menu">
        <li
          v-for="opt in options"
          :key="opt.value"
          class="app-select-option"
          :class="{ active: opt.value === modelValue }"
          @click="choose(opt)"
        >
          <span>{{ opt.label }}</span>
          <span v-if="opt.value === modelValue" class="app-select-check">✓</span>
        </li>
      </ul>
    </transition>
  </div>
</template>
