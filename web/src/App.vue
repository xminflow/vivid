<script setup>
import { ref, onMounted } from 'vue'

const stats = ref(null)

onMounted(async () => {
  stats.value = await (await fetch('/api/stats')).json()
})
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <router-link to="/" class="brand">安选 · 材料库</router-link>
      <!-- 把采集口径直接摆在页头：核对数据时随时能看到库里到底有多少，
           以及本地图片覆盖了多少（其余会回落到原站 CDN）。 -->
      <div v-if="stats" class="stats">
        <span>{{ stats.categories }} 分类</span>
        <span>{{ stats.spus.toLocaleString() }} SPU</span>
        <span>{{ stats.skus.toLocaleString() }} SKU</span>
        <span>{{ stats.shops }} 商家</span>
        <span class="dim">本地图 {{ stats.localImages.toLocaleString() }} 张</span>
      </div>
    </header>
    <router-view />
  </div>
</template>

<style>
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.6 system-ui, -apple-system, "Microsoft YaHei", sans-serif;
       color: #1f2329; background: #f5f6f7; }
a { color: inherit; text-decoration: none; }

.topbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center;
          justify-content: space-between; height: 56px; padding: 0 20px;
          background: #fff; border-bottom: 1px solid #e5e6eb; }
.brand { font-size: 17px; font-weight: 600; letter-spacing: .5px; }
.stats { display: flex; gap: 16px; font-size: 13px; color: #646a73; }
.stats .dim { color: #a3a8b0; }
</style>
