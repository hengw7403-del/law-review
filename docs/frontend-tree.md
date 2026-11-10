# Vue frontend directory tree

```text
law-review/
  package.json
  package-lock.json
  vite.config.js
  index.html
  src/
    api/
      index.js
    components/
      AppShell.vue
      EmptyState.vue
      RiskCard.vue
      StatBar.vue
      StatusBadge.vue
      Toast.vue
    router/
      index.js
    stores/
      user.js
    styles/
      base.css
      tokens.css
    utils/
      markdown.js
    views/
      ContractDetailView.vue
      ContractListView.vue
      LoginView.vue
      NotFoundView.vue
      ReportView.vue
      ResultView.vue
    App.vue
    main.js
  dist/                  # npm run build output
  docs/frontend-notes.md
  docs/frontend-example.md
  app/static/            # retained native version
```