const loginForm = document.getElementById("login-form");
loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("login-submit");
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = "正在登录…";
  Api.message(document.getElementById("login-error"), "");
  try {
    const username = document.getElementById("username").value;
    const result = await Api.request("/auth/login", { method: "POST", json: {
      username, password: document.getElementById("password").value
    }});
    sessionStorage.setItem("access_token", result.access_token);
    sessionStorage.setItem("role", result.role);
    sessionStorage.setItem("username", username);
    document.getElementById("password").value = "";
    window.location.replace("/contract-list.html");
  } catch (error) {
    Api.message(document.getElementById("login-error"), error.status ? "账号或密码错误" : error.message);
  } finally { button.disabled = false; button.textContent = "登录"; }
});
