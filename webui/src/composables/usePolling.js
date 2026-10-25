import { ref, onUnmounted } from 'vue'

// 轮询 composable: start/stop/toggle 控制, 组件卸载时自动停止
export function usePolling(fn, interval = 5000) {
  const running = ref(false)
  const loading = ref(false)
  let timer = null

  async function run() {
    loading.value = true
    try {
      await fn()
    } catch (e) {
      console.error('polling error:', e)
    } finally {
      loading.value = false
    }
  }

  function start() {
    if (timer) return
    running.value = true
    run()
    timer = setInterval(run, interval)
  }

  function stop() {
    running.value = false
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  function toggle() {
    if (running.value) {
      stop()
    } else {
      start()
    }
  }

  onUnmounted(stop)

  return { running, loading, run, start, stop, toggle }
}
