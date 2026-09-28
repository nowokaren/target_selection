document.addEventListener("DOMContentLoaded", function () {
  if (typeof mermaid === "undefined") return;
  mermaid.initialize({ startOnLoad: true, securityLevel: "loose", theme: "default" });
});
