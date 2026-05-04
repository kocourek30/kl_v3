document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-bulk-admin]");
  if (!root) return;

  const calendarGrid = document.querySelector("[data-bulk-calendar-grid]");
  const monthLabel = document.querySelector("[data-bulk-month-label]");
  const prevBtn = document.querySelector("[data-bulk-prev]");
  const nextBtn = document.querySelector("[data-bulk-next]");
  const selectedDateInput = document.getElementById("selectedDate");
  const submitBtn = document.getElementById("submitBtn");
  const menuContainer = document.getElementById("menu-items-container");
  const userSelectContainer = document.getElementById("user-select-container");
  const searchInput = document.getElementById("user-search");
  const groupField = document.getElementById("id_skupina");
  const statsDateNodes = document.querySelectorAll("[data-bulk-stat-date]");
  const statsItemsNodes = document.querySelectorAll("[data-bulk-stat-items]");
  const statsUsersNodes = document.querySelectorAll("[data-bulk-stat-users]");
  const statsTotalNodes = document.querySelectorAll("[data-bulk-stat-total]");
  const selectedHint = document.querySelector("[data-bulk-selected-hint]");
  const selectedItemsPills = document.querySelector("[data-bulk-selected-items]");
  const selectedUsersPills = document.querySelector("[data-bulk-selected-users]");
  const selectAllItemsBtn = document.querySelector("[data-bulk-select-all-items]");
  const clearItemsBtn = document.querySelector("[data-bulk-clear-items]");
  const selectAllUsersBtn = document.querySelector("[data-bulk-select-all-users]");
  const clearUsersBtn = document.querySelector("[data-bulk-clear-users]");

  const menuItemsUrl = root.dataset.menuItemsUrl;
  const menuDaysUrl = root.dataset.menuDaysUrl;
  const usersUrl = root.dataset.usersUrl;
  const todayIso = root.dataset.today;

  if (selectedDateInput) {
    selectedDateInput.value = "";
  }

  let availableDates = [];
  let currentMonth = new Date();
  currentMonth.setDate(1);

  function formatDateISO(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function setText(nodes, value) {
    nodes.forEach((node) => {
      node.textContent = value;
    });
  }

  function formatDateLabel(dateString) {
    if (!dateString) return "Nevybráno";
    const date = new Date(`${dateString}T00:00:00`);
    return date.toLocaleDateString("cs-CZ", {
      day: "2-digit",
      month: "long",
      year: "numeric",
    });
  }

  function formatMonthLabel(date) {
    return date.toLocaleDateString("cs-CZ", {
      month: "long",
      year: "numeric",
    });
  }

  function renderCalendar() {
    if (!calendarGrid || !monthLabel) return;
    monthLabel.textContent = formatMonthLabel(currentMonth);
    calendarGrid.innerHTML = "";

    const year = currentMonth.getFullYear();
    const month = currentMonth.getMonth();
    const firstDay = new Date(year, month, 1);
    const startOffset = (firstDay.getDay() + 6) % 7;
    const gridStart = new Date(year, month, 1 - startOffset);

    for (let i = 0; i < 42; i += 1) {
      const cellDate = new Date(gridStart);
      cellDate.setDate(gridStart.getDate() + i);
      const iso = formatDateISO(cellDate);
      const isCurrentMonth = cellDate.getMonth() === month;
      const isAvailable = availableDates.includes(iso);
      const isSelected = selectedDateInput.value === iso;

      const day = document.createElement("button");
      day.type = "button";
      day.className = "bulk-day";
      if (!isCurrentMonth) day.classList.add("is-other-month");
      if (isAvailable) day.classList.add("is-available");
      if (isSelected) day.classList.add("is-selected");
      day.dataset.date = iso;
      day.innerHTML = `
        <div class="bulk-day-number">${cellDate.getDate()}</div>
        ${isAvailable ? '<div class="bulk-day-tag" aria-hidden="true"></div>' : ""}
      `;
      day.addEventListener("click", () => {
        if (!isAvailable) return;
        selectedDateInput.value = iso;
        setText(statsDateNodes, formatDateLabel(iso));
        selectedHint.textContent = `Vybraný den: ${formatDateLabel(iso)}`;
        renderCalendar();
        loadMenuItems(iso);
        updateSubmitState();
      });
      calendarGrid.appendChild(day);
    }
  }

  function selectedMenuItemCount() {
    return menuContainer.querySelectorAll('input[name="menu_items"]:checked').length;
  }

  function selectedUserCount() {
    return userSelectContainer.querySelectorAll('input[name="uzivatele"]:checked').length;
  }

  function selectedEligibleTotal() {
    return Array.from(userSelectContainer.querySelectorAll('input[name="uzivatele"]:checked'))
      .reduce((sum, input) => {
        const card = input.closest(".bulk-user-card");
        const action = card?.dataset.action || "";
        if (!["create", "replace"].includes(action)) return sum;
        return sum + Number(card?.dataset.totalPrice || 0);
      }, 0);
  }

  function renderSelectionPills() {
    if (selectedItemsPills) {
      const checkedItems = Array.from(menuContainer.querySelectorAll('input[name="menu_items"]:checked'))
        .map((checkbox) => checkbox.closest(".bulk-menu-item")?.querySelector(".bulk-menu-name")?.textContent?.trim())
        .filter(Boolean);
      selectedItemsPills.innerHTML = checkedItems.length
        ? checkedItems.map((name) => `<span class="bulk-selection-pill">${name}</span>`).join("")
        : '<span class="bulk-note">Zatím nejsou vybrané žádné položky.</span>';
    }

    if (selectedUsersPills) {
      const checkedUsers = Array.from(userSelectContainer.querySelectorAll('input[name="uzivatele"]:checked'))
        .map((checkbox) => checkbox.closest(".bulk-user-card")?.querySelector(".bulk-user-name")?.textContent?.trim())
        .filter(Boolean);
      selectedUsersPills.innerHTML = checkedUsers.length
        ? checkedUsers.map((name) => `<span class="bulk-selection-pill">${name}</span>`).join("")
        : '<span class="bulk-note">Zatím nejsou vybraní žádní uživatelé.</span>';
    }
  }

  function updateSubmitState() {
    setText(statsItemsNodes, String(selectedMenuItemCount()));
    setText(statsUsersNodes, String(selectedUserCount()));
    setText(
      statsTotalNodes,
      `${selectedEligibleTotal().toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Kč`,
    );
    renderSelectionPills();
    submitBtn.disabled = !selectedDateInput.value || selectedMenuItemCount() === 0 || selectedUserCount() === 0;
  }

  function bindMenuSelectionEvents() {
    menuContainer.querySelectorAll('input[name="menu_items"]').forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        updateSubmitState();
        loadUsers(searchInput?.value || "");
      });
    });
  }

  function bindUserSelectionEvents() {
    userSelectContainer.querySelectorAll('input[name="uzivatele"]').forEach((checkbox) => {
      checkbox.addEventListener("change", updateSubmitState);
    });
  }

  function loadMenuItems(datum) {
    menuContainer.innerHTML = '<div class="bulk-menu-empty">Načítám položky jídelníčku…</div>';
    fetch(`${menuItemsUrl}?datum=${datum}`)
      .then((res) => res.json())
      .then((data) => {
        if (!Array.isArray(data) || data.length === 0) {
          menuContainer.innerHTML = '<div class="bulk-menu-empty">Pro zvolené datum není žádný jídelníček.</div>';
          updateSubmitState();
          return;
        }

        let html = '<div class="bulk-menu-grid">';
        data.forEach((item) => {
          html += `
            <label class="bulk-menu-item" for="menu_item_${item.id}">
              <input type="checkbox" name="menu_items" value="${item.id}" id="menu_item_${item.id}">
              <span class="bulk-menu-copy">
                <span class="bulk-menu-type">🍽 ${item.druh_jidla || "Položka"}</span>
                <span class="bulk-menu-name">${item.nazev}</span>
                <span class="bulk-menu-meta">Položka jídelníčku pro vybraný den</span>
              </span>
              <span class="bulk-pill"><strong>#${item.id}</strong></span>
            </label>
          `;
        });
        html += "</div>";
        menuContainer.innerHTML = html;
        bindMenuSelectionEvents();
        updateSubmitState();
      });
  }

  function loadUsers(query = "") {
    const previouslyCheckedUsers = new Set(
      Array.from(userSelectContainer.querySelectorAll('input[name="uzivatele"]:checked')).map((input) => input.value),
    );
    userSelectContainer.innerHTML = '<div class="bulk-user-empty">Načítám uživatele…</div>';
    const params = new URLSearchParams();
    if (groupField.value) params.set("skupina", groupField.value);
    if (query.trim()) params.set("q", query.trim());
    if (selectedDateInput.value) params.set("datum", selectedDateInput.value);
    menuContainer.querySelectorAll('input[name="menu_items"]:checked').forEach((input) => {
      params.append("menu_items", input.value);
    });

    fetch(`${usersUrl}?${params.toString()}`)
      .then((res) => res.json())
      .then((users) => {
        if (!Array.isArray(users) || users.length === 0) {
          userSelectContainer.innerHTML = '<div class="bulk-user-empty">Tomuto filtru neodpovídají žádní aktivní uživatelé.</div>';
          updateSubmitState();
          return;
        }

        let html = '<div class="bulk-user-grid">';
        users.forEach((user) => {
          const personal = user.osobni_cislo ? `${user.osobni_cislo} · ` : "";
          const action = user.action || "create";
          const isSelectable = !action.startsWith("skip");
          let statusLabel = "Připraveno";
          let statusClass = "good";
          if (action === "replace") {
            statusLabel = "Nahradí rozpracovanou";
            statusClass = "warn";
          } else if (action.startsWith("skip")) {
            statusLabel = "Přeskočí se";
            statusClass = "bad";
          }
          html += `
            <label class="bulk-user-card" data-action="${action}" data-total-price="${user.total_price || 0}">
              <input type="checkbox" name="uzivatele" value="${user.id}" ${previouslyCheckedUsers.has(String(user.id)) && isSelectable ? "checked" : ""} ${isSelectable ? "" : "disabled"}>
              <span>
                <span class="bulk-user-name">${user.name || user.username}</span>
                <span class="bulk-user-meta">${personal}${user.username}</span>
                <span class="bulk-user-extra">
                  <span class="bulk-tag ${statusClass}">${statusLabel}</span>
                  <span class="bulk-tag">Cena ${Number(user.total_price || 0).toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Kč</span>
                  ${user.existing_items ? `<span class="bulk-tag">Stávající položky ${user.existing_items}</span>` : ""}
                  ${user.reason ? `<span class="bulk-tag ${statusClass}">${user.reason}</span>` : ""}
                </span>
              </span>
              <span class="bulk-pill"><strong>#${user.id}</strong></span>
            </label>
          `;
        });
        html += "</div>";
        userSelectContainer.innerHTML = html;
        bindUserSelectionEvents();
        updateSubmitState();
      });
  }

  fetch(menuDaysUrl)
    .then((res) => res.json())
    .then((days) => {
      availableDates = Array.isArray(days) ? [...days] : [];
      const baseToday = todayIso ? new Date(`${todayIso}T00:00:00`) : new Date();
      const today = new Date(baseToday);
      today.setDate(1);
      currentMonth = new Date(today);
      const effectiveTodayIso = todayIso || formatDateISO(new Date());

      // Dnešek chceme v kalendáři nabízet vždy, i kdyby pro něj API
      // zrovna nevrátilo jídelníček. Obsluha tak může cíleně pracovat
      // s dnešním dnem bez ručního přepisování data.
      if (effectiveTodayIso && !availableDates.includes(effectiveTodayIso)) {
        availableDates.push(effectiveTodayIso);
      }
      availableDates.sort();

      if (availableDates.length && !selectedDateInput.value) {
        const currentMonthPrefix = effectiveTodayIso.slice(0, 7);
        const selectedDate =
          (availableDates.includes(effectiveTodayIso) && effectiveTodayIso) ||
          availableDates.find((day) => day.startsWith(currentMonthPrefix));

        if (selectedDate) {
          selectedDateInput.value = selectedDate;
          setText(statsDateNodes, formatDateLabel(selectedDate));
          selectedHint.textContent = `Výchozí den: ${formatDateLabel(selectedDate)}`;
          loadMenuItems(selectedDate);
        } else {
          selectedDateInput.value = "";
          setText(statsDateNodes, "Nevybráno");
          selectedHint.textContent = "V aktuálním měsíci není žádný den s jídelníčkem. Vyber ho ručně v kalendáři.";
          menuContainer.innerHTML = '<div class="bulk-menu-empty">V aktuálním měsíci není dostupný jídelníček. Přepni měsíc nebo vyber jiný den.</div>';
        }
      }
      renderCalendar();
      updateSubmitState();
    });

  prevBtn?.addEventListener("click", () => {
    currentMonth.setMonth(currentMonth.getMonth() - 1);
    currentMonth = new Date(currentMonth);
    renderCalendar();
  });

  nextBtn?.addEventListener("click", () => {
    currentMonth.setMonth(currentMonth.getMonth() + 1);
    currentMonth = new Date(currentMonth);
    renderCalendar();
  });

  groupField?.addEventListener("change", () => loadUsers(searchInput.value));
  searchInput?.addEventListener("input", () => loadUsers(searchInput.value));

  selectAllItemsBtn?.addEventListener("click", () => {
    menuContainer.querySelectorAll('input[name="menu_items"]').forEach((input) => {
      input.checked = true;
    });
    updateSubmitState();
    loadUsers(searchInput.value);
  });

  clearItemsBtn?.addEventListener("click", () => {
    menuContainer.querySelectorAll('input[name="menu_items"]').forEach((input) => {
      input.checked = false;
    });
    updateSubmitState();
    loadUsers(searchInput.value);
  });

  selectAllUsersBtn?.addEventListener("click", () => {
    userSelectContainer.querySelectorAll('input[name="uzivatele"]:not(:disabled)').forEach((input) => {
      input.checked = true;
    });
    updateSubmitState();
  });

  clearUsersBtn?.addEventListener("click", () => {
    userSelectContainer.querySelectorAll('input[name="uzivatele"]').forEach((input) => {
      input.checked = false;
    });
    updateSubmitState();
  });

  groupField?.addEventListener("change", () => {
    window.localStorage.setItem("bulk-order-group", groupField.value || "");
  });

  const storedGroup = window.localStorage.getItem("bulk-order-group");
  if (storedGroup && groupField) {
    groupField.value = storedGroup;
  }

  loadUsers();
});
