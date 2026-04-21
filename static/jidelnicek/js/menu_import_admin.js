document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-menu-import-root]");
  if (!root) return;

  const input = root.querySelector('input[type="file"]');
  const preview = root.querySelector("[data-menu-import-file-preview]");
  const dropzone = root.querySelector(".menu-import-dropzone");

  if (!input || !preview || !dropzone) return;

  function renderFiles(files) {
    if (!files || files.length === 0) {
      preview.innerHTML = '<span class="menu-import-pill">Zatím není vybraný žádný soubor.</span>';
      return;
    }

    preview.innerHTML = Array.from(files)
      .map((file) => {
        const sizeKb = Math.max(1, Math.round(file.size / 1024));
        return `
          <span class="menu-import-pill accent">${file.name}</span>
          <span class="menu-import-pill">${sizeKb} KB</span>
        `;
      })
      .join("");
  }

  input.addEventListener("change", () => {
    renderFiles(input.files);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    const files = event.dataTransfer?.files;
    if (!files || files.length === 0) return;
    input.files = files;
    renderFiles(files);
  });

  renderFiles(input.files);
});
