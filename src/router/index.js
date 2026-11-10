import { createRouter, createWebHistory } from "vue-router";
import { useUserStore } from "../stores/user.js";
import AppShell from "../components/AppShell.vue";

export function safeRedirect(value) {
  return typeof value === "string" &&
    /^\/workspace\/(contracts|results|reports|tickets|new-ticket|knowledge-help)(\/|\?|$)/.test(
      value,
    )
    ? value
    : "/workspace/contracts";
}
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/workspace/contracts" },
    {
      path: "/workspace/login",
      name: "login",
      component: () => import("../views/LoginView.vue"),
      meta: { public: true },
    },
    {
      path: "/workspace",
      component: AppShell,
      children: [
        {
          path: "knowledge-help",
          name: "knowledge-help",
          component: () => import("../views/KnowledgeHelpView.vue"),
        },
        {
          path: "new-ticket",
          name: "new-ticket",
          component: () => import("../views/NewTicketView.vue"),
        },
        {
          path: "tickets",
          name: "tickets",
          component: () => import("../views/TicketListView.vue"),
        },
        {
          path: "tickets/:id",
          name: "ticket",
          component: () => import("../views/TicketDetailView.vue"),
        },
        { path: "", redirect: "/workspace/contracts" },
        {
          path: "contracts",
          name: "contracts",
          component: () => import("../views/ContractListView.vue"),
        },
        {
          path: "contracts/:id",
          name: "contract",
          component: () => import("../views/ContractDetailView.vue"),
        },
        {
          path: "results/:id",
          name: "result",
          component: () => import("../views/ResultView.vue"),
        },
        {
          path: "reports/:id",
          name: "report",
          component: () => import("../views/ReportView.vue"),
        },
      ],
    },
    {
      path: "/:pathMatch(.*)*",
      component: () => import("../views/NotFoundView.vue"),
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
});
router.beforeEach((to) => {
  const user = useUserStore();
  if (!to.meta.public && !user.isLoggedIn)
    return { name: "login", query: { redirect: to.fullPath } };
});
export default router;
