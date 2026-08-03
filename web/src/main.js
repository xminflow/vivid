import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import ProductList from './views/ProductList.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: ProductList },
    // 详情已改成右侧抽屉。旧的直链仍然可用：转成列表页 + 自动展开抽屉，只留一个入口。
    { path: '/product/:id', redirect: (to) => ({ path: '/', query: { spu: to.params.id } }) },
  ],
})

createApp(App).use(router).use(ElementPlus).mount('#app')
