const shanghai = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
export function formatTime(value) {
  if (!value) return "—";
  let normalized = String(value).replace(" ", "T");
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(normalized)) normalized += "Z";
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "—";
  const parts = Object.fromEntries(
    shanghai.formatToParts(date).map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
export const ticketNumber = (id) => "#" + String(id || "").slice(0, 8);
export const focusLabel = (points) =>
  points?.length ? points.join("、") : "全部条款";
