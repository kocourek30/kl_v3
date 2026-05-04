document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-menu-builder-root]");
  if (!root) return;

  const urlTemplate = root.dataset.jidloMetaUrlTemplate;
  if (!urlTemplate) return;
  const tabStorageKey = `menu-builder-tab:${window.location.pathname}`;

  function summaryMarkup(data, expectedKindLabel = "") {
    if (!data || !data.id) {
      if (expectedKindLabel) {
        return `
          <div class="menu-builder-summary is-missing">
            <span class="menu-builder-pill kind">Slot: ${expectedKindLabel}</span>
            <span class="menu-builder-pill">Vyber jídlo odpovídající tomuto druhu.</span>
          </div>
        `;
      }
      return '<div class="menu-builder-summary is-missing"><span class="menu-builder-pill">Vyber jídlo a souhrn se doplní automaticky.</span></div>';
    }

    if (!data.druh_id) {
      return '<div class="menu-builder-summary is-missing"><span class="menu-builder-pill">Vybrané jídlo nemá nastavený druh. Doplň ho nejdřív v katalogu jídel.</span></div>';
    }

    const allergens = data.alergeny?.length ? data.alergeny.join(", ") : "Bez alergenů";
    const groups = data.visible_groups?.length ? data.visible_groups.join(", ") : "Všichni";

    return `
      <div class="menu-builder-summary" data-menu-builder-summary>
        <span class="menu-builder-pill kind">Druh: ${data.druh}</span>
        <span class="menu-builder-pill price">Cena: ${data.cena} Kč</span>
        <span class="menu-builder-pill allergens">Alergeny: ${allergens}</span>
        <span class="menu-builder-pill groups">Uvidí: ${groups}</span>
      </div>
    `;
  }

  function mismatchMarkup(expectedKindLabel, actualKindLabel) {
    return `
      <div class="menu-builder-summary is-missing">
        <span class="menu-builder-pill kind">Slot: ${expectedKindLabel}</span>
        <span class="menu-builder-pill allergens">Vybrané jídlo patří do: ${actualKindLabel}</span>
        <span class="menu-builder-pill groups">Tuhle kombinaci nelze uložit.</span>
      </div>
    `;
  }

  function rowGroups() {
    return Array.from(document.querySelectorAll("#polozkajidelnicku_set-group .inline-related"));
  }

  function findField(group, suffix) {
    return group.querySelector(`[name$="-${suffix}"]`);
  }

  function findSummaryTarget(group) {
    return group.querySelector(".field-menu_item_summary .readonly") || group.querySelector(".field-menu_item_summary");
  }

  function metaUrlFor(id) {
    return urlTemplate.replace(/0\/?$/, `${id}/`);
  }

  function getSelectedOptionLabel(field) {
    return field?.options?.[field.selectedIndex]?.text?.trim() || "";
  }

  function syncRow(group) {
    const jidloField = findField(group, "jidlo");
    const druhField = findField(group, "druh_jidla");
    const summaryTarget = findSummaryTarget(group);

    if (!jidloField || !druhField || !summaryTarget) return;

    const jidloId = jidloField.value;
    const expectedKindLabel = getSelectedOptionLabel(druhField);
    if (!jidloId) {
      jidloField.setCustomValidity("");
      summaryTarget.innerHTML = summaryMarkup(null, expectedKindLabel);
      return;
    }

    fetch(metaUrlFor(jidloId))
      .then((response) => response.json())
      .then((data) => {
        if (data.druh_id && druhField.value && String(data.druh_id) !== String(druhField.value)) {
          jidloField.setCustomValidity("Vybrané jídlo neodpovídá druhu tohoto slotu.");
          summaryTarget.innerHTML = mismatchMarkup(expectedKindLabel, data.druh);
          return;
        }

        jidloField.setCustomValidity("");
        if (data.druh_id && !druhField.value) {
          druhField.value = String(data.druh_id);
        }
        summaryTarget.innerHTML = summaryMarkup(data, expectedKindLabel);
      })
      .catch(() => {
        summaryTarget.innerHTML = '<div class="menu-builder-summary is-missing"><span class="menu-builder-pill">Souhrn se nepodařilo načíst.</span></div>';
      });
  }

  function bindRow(group) {
    const jidloField = findField(group, "jidlo");
    const druhField = findField(group, "druh_jidla");

    if (jidloField?.dataset.menuBuilderBound === "true") return;

    if (jidloField) {
      disableSelect2ForField(jidloField);
      jidloField.dataset.menuBuilderBound = "true";
      jidloField.addEventListener("change", () => syncRow(group));
      syncRow(group);
    }

    if (druhField) {
      druhField.title = "Druh jídla se doplní automaticky podle vybraného jídla.";
    }
  }

  function bindAllRows() {
    rowGroups().forEach(bindRow);
  }

  bindAllRows();

  document.querySelectorAll('.nav-tabs a, [data-toggle="tab"], [data-bs-toggle="tab"]').forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.getAttribute("href") || tab.dataset.target || tab.dataset.bsTarget;
      if (target) {
        window.sessionStorage.setItem(tabStorageKey, target);
      }
    });
  });

  const rememberedTab = window.sessionStorage.getItem(tabStorageKey);
  if (rememberedTab) {
    const rememberedLink = document.querySelector(
      `.nav-tabs a[href="${rememberedTab}"], [data-toggle="tab"][href="${rememberedTab}"], [data-bs-toggle="tab"][href="${rememberedTab}"]`,
    );
    rememberedLink?.click();
  }

  const formsetGroup = document.getElementById("polozkajidelnicku_set-group");
  if (!formsetGroup) return;

  const observer = new MutationObserver(() => {
    bindAllRows();
  });
  observer.observe(formsetGroup, { childList: true, subtree: true });
});
