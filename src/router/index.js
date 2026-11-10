import { createRouter, createWebHistory } from "vue-router";
import { useUserStore } from "../stores/user.js";
import AppLayout from "../components/AppLayout.vue";
const ALL = ['admin', 'reviewer', 'requester'];

export function safeRedirect(value) {
  return typeof value === "string" &&
    /^\/workspace(?:$|\/$|\?|\/(contracts|results|reports|tickets|new-ticket|knowledge-help|knowledge|agent|admin\/users)(\/|\?|$))/.test(
      value,
    )
    ? value
    : "/workspace";
}
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/workspace", meta: { roles: ALL } },
    {
      path: "/workspace/login",
      name: "login",
      component: () => import("../views/LoginView.vue"),
      meta: { public: true, roles: ALL },
    },
    {
      path: "/workspace",
      component: AppLayout,
      meta: { roles: ALL },
      children: [
        { path: 'agent', name: 'agent', component: () => import('../views/AgentView.vue'), meta: { roles: ['admin', 'reviewer'] } },
        {
          path: "knowledge-help",
          name: "knowledge-help",
          component: () => import("../views/KnowledgeHelpView.vue"),
          meta: { roles: ['admin'] },
        },
        {
          path: "new-ticket",
          name: "new-ticket",
          component: () => import("../views/NewTicketView.vue"),
          meta: { roles: ALL },
        },
        {
          path: "tickets",
          name: "tickets",
          component: () => import("../views/TicketListView.vue"),
          meta: { roles: ALL },
        },
        {
          path: "tickets/:id",
          name: "ticket",
          component: () => import("../views/TicketDetailView.vue"),
          meta: { roles: ALL },
        },
        { path: "", name: "dashboard", component: () => import("../views/DashboardView.vue"), meta: { roles: ALL } },
        { path: "knowledge", name: "knowledge", component: () => import("../views/KnowledgeView.vue"), meta: { roles: ['admin'] } },
        { path: "admin/users", name: "users", component: () => import("../views/UsersView.vue"), meta: { roles: ['admin'] } },
        {
          path: "contracts",
          name: "contracts",
          component: () => import("../views/ContractListView.vue"),
          meta: { roles: ALL },
        },
        {
          path: "contracts/:id",
          name: "contract",
          component: () => import("../views/ContractDetailView.vue"),
          meta: { roles: ALL },
        },
        {
          path: "results/:id",
          name: "result",
          component: () => import("../views/ResultView.vue"),
          meta: { roles: ALL },
        },
        {
          path: "reports/:id",
          name: "report",
          component: () => import("../views/ReportView.vue"),
          meta: { roles: ALL },
        },
      ],
    },
    {
      path: "/:pathMatch(.*)*",
      component: () => import("../views/NotFoundView.vue"),
      meta: { roles: ALL },
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
});
router.beforeEach((to) => {
  const user = useUserStore();
  if (!to.meta.public && !user.isLoggedIn)
    return { name: "login", query: { redirect: to.fullPath } };
  if (!to.meta.public && !ALL.includes(user.role)) {
    user.logout();
    return { name: 'login' };
  }
  if (!to.meta.public && to.matched.some(record => record.meta.roles && !record.meta.roles.includes(user.role)))
    return { name: 'dashboard' };
});
export default router;
