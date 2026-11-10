import { computed, ref } from "vue";
import { defineStore } from "pinia";

export const useUserStore = defineStore("user", () => {
  // Session persistence is owned by the store, never individual views.
  const token = ref(sessionStorage.getItem("access_token") || "");
  const role = ref(sessionStorage.getItem("role") || "");
  const orgName = ref(null);
  function setProfile(profile) { orgName.value = profile.org_name || null; }
  const isLoggedIn = computed(() => Boolean(token.value));
  function setSession(session) {
    token.value = session.access_token;
    role.value = session.role;
    sessionStorage.setItem("access_token", token.value);
    sessionStorage.setItem("role", role.value);
  }
  function logout() {
    token.value = "";
    role.value = "";
    orgName.value = null;
    for (const key of ["access_token", "role", "username", "conversation_id"])
      sessionStorage.removeItem(key);
  }
  return { token, role, orgName, isLoggedIn, setSession, setProfile, logout };
});
