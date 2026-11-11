<script setup>
defineProps({ name: { type: String, required: true } });

const paths = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  tickets: 'M5 3h14v18H5z M8 8h8 M8 12h8 M8 16h5',
  document: 'M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h6',
  assistant: 'M12 2l2.7 7.3L22 12l-7.3 2.7L12 22l-2.7-7.3L2 12l7.3-2.7z',
  book: 'M12 5C9 3 5 3 2 5v14c3-2 7-2 10 0 3-2 7-2 10 0V5c-3-2-7-2-10 0z M12 5v14',
  user: 'M16 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0z M4 21a8 8 0 0 1 16 0',
  pending: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z M12 7v5l3 2',
  risk: 'M12 3 2 20h20L12 3z M12 9v4 M12 16v.5',
};
</script>

<template>
  <svg class="app-icon" viewBox="0 0 24 24" width="16" height="16"
    fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path :d="paths[name]" />
  </svg>
</template>
