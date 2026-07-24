(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.R201Outbox = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function storageKey(userId) {
    if (userId === null || userId === undefined || userId === "") return null;
    return `r201-event-outbox-v2:user:${String(userId)}`;
  }

  function classifyHttpStatus(status) {
    if (!status || status === 429 || status >= 500) return "retry";
    if (status === 409) return "conflict";
    return "discard";
  }

  return {storageKey, classifyHttpStatus};
});
