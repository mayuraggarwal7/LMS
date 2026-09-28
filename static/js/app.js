// Small progressive enhancements; every page works without JS.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-mark-all]");
  if (btn) {
    const value = btn.dataset.markAll;
    document.querySelectorAll(`.att-toggle input[value="${value}"]`).forEach((r) => (r.checked = true));
    e.preventDefault();
  }
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    navigator.clipboard?.writeText(copy.dataset.copy);
    const old = copy.textContent;
    copy.textContent = "Copied!";
    setTimeout(() => (copy.textContent = old), 1200);
    e.preventDefault();
  }
  const confirmEl = e.target.closest("[data-confirm]");
  if (confirmEl && !window.confirm(confirmEl.dataset.confirm)) {
    e.preventDefault();
  }
});

// Live rubric total on the grading screen
function updateRubricTotal() {
  const box = document.getElementById("rubric-total");
  if (!box) return;
  let earned = 0;
  let possible = 0;
  document.querySelectorAll("[data-criterion]").forEach((row) => {
    possible += parseFloat(row.dataset.max);
    const custom = row.querySelector("input[data-points]");
    const checked = row.querySelector("input[type=radio]:checked");
    if (custom && custom.value !== "") earned += parseFloat(custom.value) || 0;
    else if (checked) earned += parseFloat(checked.dataset.pts);
  });
  const max = parseFloat(box.dataset.max);
  const scaled = possible ? (earned / possible) * max : 0;
  box.textContent = `${scaled.toFixed(2)} / ${max}`;
}
document.addEventListener("change", (e) => {
  if (e.target.closest("[data-criterion]")) {
    if (e.target.type === "radio") {
      const custom = e.target.closest("[data-criterion]").querySelector("input[data-points]");
      if (custom) custom.value = "";
    }
    updateRubricTotal();
  }
});
document.addEventListener("input", (e) => {
  if (e.target.matches("input[data-points]")) updateRubricTotal();
});
document.addEventListener("DOMContentLoaded", updateRubricTotal);
