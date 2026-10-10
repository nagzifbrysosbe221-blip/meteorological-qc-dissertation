/* Presentation-only filter help. The original select values still drive the saved-results viewer. */
(() => {
  'use strict';
  const copy = {
    table: {
      purpose: 'Choose which kind of saved result to inspect. This viewer contains selected table sections; the complete datasets are available on the Complete datasets page in the sidebar.',
      choices: {
        root_summary: 'How many constructed fault cases were detected or missed, grouped by fault family and configuration. This view shows annual summaries, including coverage and reasons for misses.',
        background_points: 'Flags and scoring counts on the unchanged background, using each configuration’s own eligible hours or the hours shared by the comparison. The background is assumed normal, not certified healthy.',
        background_burden: 'How often background alarm segments occurred relative to eligible exposure. Raw alarm load and eligible false segments are different measures; these are not measured staff workload.',
        contrasts: 'Saved paired comparisons: H versus B0, R versus B0, and H versus R. Compare detection and common-hit delay while keeping other hits and misses visible.',
        point_annual_examples: 'Annual scoring counts and ratios for exact fault variants and affected tasks in the primary pass. These are individual scored points or records, not whole-case detection rates. Other strata remain in the complete downloads.',
        monthly_gradual_bias: 'Month-by-month descriptions of gradual-bias detection. Look for variation across the saved months; these views do not refit the model or run new experiments.',
        fixed_roots: 'Individual fault examples chosen by a fixed rule. Inspect hits, kinds of miss, available checks and detection delay. A null delay means no detection delay is available, not zero delay.',
        fixed_recovery: 'Saved behaviour after the intervention for the fixed examples, including the unchanged counterpart. Later flags do not turn an earlier miss into a detection or enter the primary normal-background pool.',
        fixed_diagnostics: 'A stricter paired-response check for the fixed examples: the injected series must flag while its unchanged counterpart is evaluated and unflagged. This differs from the main case-detection measure.'
      }
    },
    pass: {
      purpose: 'Choose one of the four completed comparisons. This only filters saved results; it does not change settings or run the detector. Figures keep their own labelled scope.',
      choices: {
        primary: 'The main comparison, using statistical calibration α = 0.01 and temperature limits of −40 to 50 °C. The observed false-alarm rate is not guaranteed by α.',
        'alpha-0005': 'The saved stricter statistical-calibration comparison, with α = 0.005. The selected model and EWMA weighting stay fixed. Inspect both detection and background flags rather than treating α as a guaranteed false-alarm rate.',
        'alpha-002': 'The saved more sensitive statistical-calibration comparison, with α = 0.02. The selected model and EWMA weighting stay fixed. Greater sensitivity can also increase background flags; α is not a guaranteed false-alarm rate.',
        'T-range': 'The saved temperature-range comparison, using narrower limits of −30 to 45 °C in all three configurations. EWMA settings stay unchanged; this is a sensitivity check, not a newly selected best result.'
      }
    },
    variable: {
      purpose: 'Choose the measured variable, or a record-level result where the table provides one. Temperature and humidity have different scientific qualifications.',
      choices: {
        T: 'Air temperature, expressed in degrees Celsius. The selected temperature model passed the original eligibility checks. That does not certify the sensor or background as healthy.',
        U: 'Relative humidity, expressed as a percentage. These results are exploratory under researcher-approved R1 because the model failed its original eligibility checks. The negative validation finding is retained; supervisor approval is not recorded.',
        __null: 'A result about the record or timestamp rather than one measured variable. The saved variable field is null; this does not mean a zero measurement or missing research result.'
      }
    },
    family: {
      purpose: 'Choose the kind of artificial fault introduced in the saved cases. Each family tests a different problem; they are not pooled into one overall performance ranking.',
      choices: {
        absent_row: 'The entire target observation row was removed. This tests detection of an absent expected observation, which is different from a blank value in a row that arrived.',
        duplicate_receipt: 'Extra copies of a target observation were delivered. The extra receipts are the injected fault; the original first receipt is not retrospectively marked as faulty.',
        gradual_bias: 'A temperature or humidity value was gradually shifted, then held at the shifted level. A case remains gradual bias even if its values cross a fixed range limit.',
        invalid_timestamp: 'A received date or hour was made invalid. R and H can check the malformed timestamp directly; B0 may only receive credit through a linked absent-slot consequence.',
        missing_cell: 'A selected temperature or humidity value was replaced with a missing token while its row remained present. This is different from removing the entire observation.',
        off_grid_timestamp: 'A valid received time was shifted away from the expected hourly grid. R and H check it directly; it may also leave an expected observation slot absent.',
        out_of_range: 'A value was replaced with one outside the prescribed physical plausibility limits. All three configurations include the fixed-range check.'
      }
    },
    configuration: {
      purpose: 'Choose which set of checks produced the saved results. B0, R and H are the three configurations compared in the study.',
      choices: {
        B0: 'The baseline: checks for absent observations, explicit missing values and values outside fixed limits. It has no direct duplicate or timestamp check and no statistical drift monitor.',
        R: 'The baseline plus integrity rules for invalid timestamps, off-grid timestamps and additional duplicate receipts. It does not add the statistical drift monitor.',
        H: 'The hybrid: all R checks plus EWMA monitoring of departures from a fitted weather model. Statistical checks can be unavailable during warm-up or when required inputs are missing; null is not a clean result.'
      }
    }
  };
  const ids = Object.keys(copy), controls = new Map();
  const tooltip = document.createElement('div');
  tooltip.id = 'filter-description';
  tooltip.className = 'filter-tooltip';
  tooltip.setAttribute('role', 'tooltip');
  tooltip.hidden = true;
  document.body.append(tooltip);
  let owner = null, hideTimer = null, openControl = null;

  function description(field, value) {
    if (value === '*') return `Show every ${field === 'pass' ? 'saved pass' : field === 'variable' ? 'variable or record-level result' : field === 'family' ? 'fault family' : 'configuration'} available in this table, subject to the other filters. “All” does not combine rows into a new score.`;
    return copy[field].choices[value] || 'This saved category is retained as recorded. Select a record ID for its full scientific labels and scope.';
  }
  function hideHelp() {
    clearTimeout(hideTimer);
    tooltip.hidden = true;
    if (owner) owner.removeAttribute('aria-describedby');
    owner = null;
  }
  function scheduleHide(event) {
    if (event && (tooltip.contains(event.relatedTarget))) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hideHelp, 110);
  }
  function showHelp(control, element, title, message) {
    clearTimeout(hideTimer);
    if (owner) owner.removeAttribute('aria-describedby');
    owner = element;
    owner.setAttribute('aria-describedby', tooltip.id);
    tooltip.replaceChildren();
    const heading = document.createElement('strong'), body = document.createElement('p');
    heading.textContent = title;
    body.textContent = message;
    tooltip.append(heading, body);
    tooltip.hidden = false;
    const anchor = control.open ? control.menu.getBoundingClientRect() : control.button.getBoundingClientRect();
    const width = Math.min(340, window.innerWidth - 24);
    tooltip.style.width = `${width}px`;
    const height = tooltip.getBoundingClientRect().height;
    let left = anchor.right + 12, top = anchor.top;
    if (left + width > window.innerWidth - 12) {
      if (anchor.left - width - 12 >= 12) left = anchor.left - width - 12;
      else {
        left = Math.max(12, Math.min(anchor.left, window.innerWidth - width - 12));
        top = anchor.bottom + 10;
        if (top + height > window.innerHeight - 12 && anchor.top - height - 10 >= 12) top = anchor.top - height - 10;
      }
    }
    tooltip.style.left = `${Math.max(12, left)}px`;
    tooltip.style.top = `${Math.max(12, Math.min(top, window.innerHeight - height - 12))}px`;
  }
  tooltip.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  tooltip.addEventListener('pointerleave', scheduleHide);

  function close(control) {
    control.typed = '';
    control.open = false;
    control.menu.hidden = true;
    control.button.setAttribute('aria-expanded', 'false');
    control.button.removeAttribute('aria-activedescendant');
    if (openControl === control) openControl = null;
    hideHelp();
  }
  function activate(control, index, explain = true, scroll = true) {
    control.active = Math.max(0, Math.min(index, control.options.length - 1));
    control.options.forEach((option, i) => {
      option.classList.toggle('is-active', i === control.active);
      option.setAttribute('aria-selected', String(option.dataset.value === control.select.value));
    });
    const option = control.options[control.active];
    if (!option) return;
    control.button.setAttribute('aria-activedescendant', option.id);
    if (scroll && option.scrollIntoView) option.scrollIntoView({block: 'nearest'});
    if (explain) showHelp(control, option, option.textContent, description(control.id, option.dataset.value));
  }
  function open(control) {
    if (openControl && openControl !== control) close(openControl);
    control.open = true;
    openControl = control;
    control.menu.hidden = false;
    control.button.setAttribute('aria-expanded', 'true');
    activate(control, Math.max(0, control.select.selectedIndex));
  }
  function commit(control) {
    const option = control.options[control.active];
    if (!option) return;
    const value = option.dataset.value;
    close(control);
    if (control.select.value !== value) {
      control.select.value = value;
      control.select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    syncAll();
  }
  function sync(control) {
    const selected = control.select.selectedOptions[0];
    control.value.textContent = selected ? selected.textContent : 'Choose';
    control.menu.replaceChildren();
    control.options = Array.from(control.select.options, (native, index) => {
      const option = document.createElement('div');
      option.id = `${control.id}-choice-${index}`;
      option.className = 'filter-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(native.selected));
      option.dataset.value = native.value;
      option.textContent = native.textContent;
      option.addEventListener('pointerenter', () => activate(control, index, true, false));
      option.addEventListener('pointerleave', scheduleHide);
      option.addEventListener('pointerdown', event => event.preventDefault());
      option.addEventListener('click', () => { control.active = index; commit(control); });
      control.menu.append(option);
      return option;
    });
    control.active = Math.max(0, control.select.selectedIndex);
    if (control.open) activate(control, control.active, false);
  }
  function syncAll() { controls.forEach(sync); }

  ids.forEach(id => {
    const select = document.getElementById(id), oldLabel = select.parentElement;
    const wrapper = document.createElement('div'), label = document.createElement('span');
    const button = document.createElement('div'), value = document.createElement('span');
    const arrow = document.createElement('span'), menu = document.createElement('div');
    const control = {id, select, wrapper, button, value, menu, options: [], active: 0, open: false, typed: '', typedAt: 0};
    wrapper.className = 'filter-control';
    label.className = 'filter-label';
    label.id = `${id}-label`;
    label.textContent = select.getAttribute('aria-label');
    button.id = `${id}-control`;
    button.className = 'filter-button';
    button.setAttribute('role', 'combobox');
    button.setAttribute('tabindex', '0');
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', `${id}-choices`);
    button.setAttribute('aria-labelledby', label.id);
    arrow.className = 'filter-chevron';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '⌄';
    button.append(value, arrow);
    menu.id = `${id}-choices`;
    menu.className = 'filter-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-labelledby', label.id);
    menu.hidden = true;
    // Keep the existing select as the data source and no-enhancement fallback.
    oldLabel.replaceWith(wrapper);
    wrapper.append(label, select, button, menu);
    select.hidden = true;
    controls.set(id, control);
    const fieldHelp = () => showHelp(control, button, label.textContent, `${copy[id].purpose} Current choice: ${value.textContent}. ${description(id, select.value)}`);
    wrapper.addEventListener('pointerenter', () => { if (!control.open) fieldHelp(); });
    wrapper.addEventListener('pointerleave', scheduleHide);
    button.addEventListener('focus', () => { if (button.matches(':focus-visible')) fieldHelp(); });
    button.addEventListener('click', () => control.open ? close(control) : open(control));
    button.addEventListener('blur', () => { if (control.open) close(control); else hideHelp(); });
    button.addEventListener('keydown', event => {
      const key = event.key;
      if (Date.now() - control.typedAt > 700) control.typed = '';
      if (key === 'Escape') { event.preventDefault(); close(control); return; }
      if (key === 'Tab') { if (control.open) commit(control); hideHelp(); return; }
      if (event.ctrlKey || event.metaKey) return;
      if (key === 'Enter' || (key === ' ' && !control.typed)) {
        event.preventDefault();
        if (control.open) commit(control); else open(control);
        return;
      }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp'].includes(key)) {
        event.preventDefault();
        if (event.altKey && key === 'ArrowUp') { if (control.open) commit(control); return; }
        const wasOpen = control.open;
        if (!wasOpen) open(control);
        if (key === 'Home') activate(control, 0);
        else if (key === 'End') activate(control, control.options.length - 1);
        else if (wasOpen) activate(control, control.active + (key === 'ArrowDown' ? 1 : key === 'ArrowUp' ? -1 : key === 'PageDown' ? 10 : -10));
        return;
      }
      if (key.length === 1 && !event.altKey) {
        event.preventDefault();
        const time = Date.now();
        control.typed = time - control.typedAt > 700 ? key : control.typed + key;
        control.typedAt = time;
        if (!control.open) open(control);
        const query = control.typed.toLowerCase();
        const repeated = query.split('').every(char => char === query[0]);
        const prefix = repeated ? query[0] : query;
        const ordered = control.options.map((_, i) => (control.active + 1 + i) % control.options.length);
        const index = ordered.find(i => control.options[i].textContent.toLowerCase().startsWith(prefix));
        if (index !== undefined) activate(control, index);
      }
    });
    sync(control);
  });
  document.addEventListener('pointerdown', event => {
    if (openControl && !openControl.wrapper.contains(event.target) && !tooltip.contains(event.target)) close(openControl);
    if (!event.target.closest('.filter-control') && !tooltip.contains(event.target)) hideHelp();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hideHelp(); });
  window.addEventListener('resize', () => { if (openControl) close(openControl); hideHelp(); });
  window.addEventListener('scroll', hideHelp);
  document.addEventListener('assessment:rendered', syncAll);
  window.assessmentFilterHelp = {description, copy};
})();
