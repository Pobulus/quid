/* QUID — phone UI state machine.
   One Alpine component, reads/writes the full GameState JSON.
   Server endpoints return { state, ... }; we replace localStorage wholesale.
   Endpoints not yet wired: catch and surface a toast. */

const SCHEMA_VERSION = 1;
const STORAGE_KEY = "quid.state.v1";
const PREFETCH_TARGET = 3;  // keep at least this many pre-generated events buffered

// Mirror of balance.UNLOCK_TIERS — [min_credit_score|null, min_net_worth_grosze|null].
// Keep in sync if balance.py changes; Phase 2 may replace with a server call.
const UNLOCK_TIERS = {
  cc_starter:         [600, null],
  personal_loan:      [650, null],
  savings_premium:    [null, 500000],
  cc_better:          [700, 300000],
  deposit:            [null, 1000000],
  investments:        [750, 2000000],
  mortgage:           [750, 5000000],
  move_decent_rental: [null, 300000],
  move_nice_rental:   [null, 1500000],
};

const PRODUCT_LABELS = {
  bnpl:               { name: "Buy now, pay later",        blurb: "0% for 30 days, then 40% APR." },
  cc_starter:         { name: "Starter credit card",       blurb: "Low limit, high APR." },
  cc_better:          { name: "Premium credit card",       blurb: "Higher limit, lower APR." },
  personal_loan:      { name: "Personal loan",             blurb: "Standard APR, fixed term." },
  savings_premium:    { name: "Premium savings account",   blurb: "Higher monthly interest." },
  deposit:            { name: "Fixed-term deposit",        blurb: "Lockup, best savings rate." },
  investments:        { name: "Investment funds",          blurb: "Stubbed — visible milestone." },
  mortgage:           { name: "Mortgage eligibility",      blurb: "Out of MVP, visible goal." },
  move_decent_rental: { name: "Move to a decent rental",   blurb: "Better tier in Home app." },
  move_nice_rental:   { name: "Move to a nice rental",     blurb: "Top tier in Home app." },
};

// Mirror of balance.FOOD_TIERS — keep in sync with src/game/balance.py.
// `cost` is grosze per DAY. Stat deltas apply every day tick (clamped 0..100).
const FOOD_TIERS = {
  cheap:   { cost: 1100, daily_hunger: 3, health: -1, sanity: -1, energy:  0 },
  normal:  { cost: 2150, daily_hunger: 4, health:  0, sanity:  0, energy:  1 },
  premium: { cost: 4300, daily_hunger: 5, health:  1, sanity:  1, energy:  1 },
};
const FOOD_TIER_ORDER = ["cheap", "normal", "premium"];
const FOOD_DEFAULT_TIER = "normal";

const HOUSE_LABELS = {
  shoddy_rental:  "Shoddy rental",
  decent_rental:  "Decent rental",
  nice_rental:    "Nice rental",
};

const HOUSE_FLAVOR = {
  shoddy_rental:
    "Third floor walk-up. The hallway smells like wet paper. Pipes groan at 3 AM.",
  decent_rental:
    "A block with a concierge who remembers your name. Windows that actually close.",
  nice_rental:
    "South-facing balcony. Quiet courtyard. A building whose boiler you never hear.",
};

const CALENDAR_LABELS = {
  payday:        "Payday",
  rent_due:      "Rent due",
  cc_due:        "Credit card due",
  loan_due:      "Loan payment",
  heating_bill:  "Heating bill",
};

const CALENDAR_TONE = {
  payday:       "pill",       // neon
  rent_due:     "pill warn",
  cc_due:       "pill warn",
  loan_due:     "pill warn",
  heating_bill: "pill dim",
};

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = [
  "", "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function fmtMoney(grosze, { decimals = true } = {}) {
  const pln = grosze / 100;
  const s = decimals
    ? pln.toFixed(2)
    : Math.round(pln).toString();
  const [intPart, decPart] = s.split(".");
  const spaced = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return decPart ? `${spaced}.${decPart} zł` : `${spaced} zł`;
}

// Deterministic d20 via Park-Miller on state.seed.
function rollD20FromState(state) {
  const next = (state.seed * 48271) % 0x7fffffff;
  state.seed = next;
  return (next % 20) + 1;
}

// Simple markdown → HTML for event bodies. Paragraphs + **bold** + *italic* + line breaks.
// Intentionally tiny; events come from the SAGE prompt which forbids raw HTML.
function tinyMarkdown(src) {
  if (!src) return "";
  const esc = src
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const bold = esc.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const italic = bold.replace(/\*(.+?)\*/g, "<em>$1</em>");
  return italic
    .split(/\n{2,}/)
    .map((p) => `<p>${p.replace(/\n/g, "<br/>")}</p>`)
    .join("");
}

const STAT_ICONS = {
  health:  "/static/img/stat_health.png",
  hunger:  "/static/img/stat_hunger.png",
  sanity:  "/static/img/stat_sanity.png",
  energy:  "/static/img/stat_energy.png",
};

const SKILL_ICONS = {
  cooking:   "/static/img/skill_cook.png",
  handiwork: "/static/img/skill_hadniwork.png",   // filename typo in the source asset
  charisma:  "/static/img/skill_charisma.png",
  physique:  "/static/img/skill_physique.png",
};

function quid() {
  return {
    state: null,
    activeApp: "home",
    toast: null,
    toastTimer: null,
    openEventId: null,
    lastResolution: null,     // { option_id, rolled, dc, passed, effects }
    rollingEventId: null,     // while animating
    transferModalOpen: false,
    transferDraft: { direction: "to_savings", amount_pln: 0 },
    transferSaving: false,
    loanModalOpen: false,
    loanDraft: { kind: "personal", amount_pln: 0 },
    loanSaving: false,
    budgetModalOpen: false,
    budgetModalRequired: false,   // true when server gated on budget_required — modal can't be dismissed
    budgetDraft: { food_tier: FOOD_DEFAULT_TIER, leisure: 0, bills_buffer: 0 },
    budgetSaving: false,
    dayPulse: false,
    dayPulseTimer: null,
    dayAdvanceAnim: null,     // { from: {day,month,dow}, to: {day,month,dow} } while popup is up
    dayAdvanceTimer: null,
    prefetching: false,       // guard: only one /api/sage/prefetch in flight at a time

    statList: ["health","hunger","sanity","energy"].map(k => ({ key: k, icon: STAT_ICONS[k] })),
    skillList: ["cooking","handiwork","charisma","physique"].map(k => ({ key: k, icon: SKILL_ICONS[k] })),

    // ---- lifecycle ----

    async boot() {
      const cached = localStorage.getItem(STORAGE_KEY);
      if (cached) {
        try {
          const parsed = JSON.parse(cached);
          if (parsed.schema_version === SCHEMA_VERSION) {
            this.state = parsed;
            this.prefetchEvent();
            return;
          }
          this.showToast("Incompatible save — starting new game.");
        } catch (_) {
          // fall through to new game
        }
        localStorage.removeItem(STORAGE_KEY);
      }
      await this.newGame();
    },

    async newGame(confirmReset = false) {
      if (confirmReset && !confirm("Start a new game? Current run will be lost.")) return;
      const r = await fetch("/api/new-game", { method: "POST" });
      const data = await r.json();
      this.state = data.state;
      this.openEventId = null;
      this.lastResolution = null;
      this.activeApp = "home";
      this.save();
      this.prefetchEvent();
    },

    async newDemoGame() {
      const r = await fetch("/api/new-game", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ demo: true }),
      });
      const data = await r.json();
      this.state = data.state;
      this.openEventId = null;
      this.lastResolution = null;
      this.activeApp = "home";
      this.save();
      this.showToast("Demo run loaded. Check the Email app.");
      this.prefetchEvent();
    },

    async loadFakeState() {
      const r = await fetch("/static/fake_state.json");
      if (!r.ok) { this.showToast("fake_state.json not found"); return; }
      this.state = await r.json();
      this.openEventId = null;
      this.lastResolution = null;
      this.save();
      this.showToast("Fake state loaded.");
    },

    save() {
      if (this.state) localStorage.setItem(STORAGE_KEY, JSON.stringify(this.state));
    },

    // ---- UI ----

    setApp(name) {
      this.activeApp = name;
      this.openEventId = null;
      this.lastResolution = null;
    },

    showToast(msg) {
      this.toast = msg;
      clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => { this.toast = null; }, 2600);
    },

    // ---- formatters ----

    money(g)     { return fmtMoney(g, { decimals: true }); },
    moneyBig(g)  { return fmtMoney(g, { decimals: false }); },
    dayName(dow) { return DOW[dow] ?? "—"; },
    monthName(m) { return MONTHS[m] ?? "—"; },
    houseName(tier) { return HOUSE_LABELS[tier] ?? tier; },
    houseFlavor(tier) { return HOUSE_FLAVOR[tier] ?? ""; },
    calendarLabel(kind) { return CALENDAR_LABELS[kind] ?? kind; },
    calendarTone(kind) { return CALENDAR_TONE[kind] ?? "pill dim"; },

    // Display amount for an upcoming calendar entry. Payday entries are seeded
    // with amount=0 because workdays aren't known yet — estimate from salary.
    calendarAmount(c) {
      if (c.kind === "payday" && !c.amount && this.state?.player) {
        const { salary_gross_monthly, tax_rate } = this.state.player;
        return Math.round(salary_gross_monthly * (1 - tax_rate));
      }
      return c.amount;
    },

    calendarAmountSuffix(c) { return c.kind === "payday" ? " est." : ""; },

    // ---- derived ----

    get checking() { return this.state?.accounts.checking ?? 0; },
    get savings()  { return this.state?.accounts.savings ?? 0; },

    get totalDebt() {
      if (!this.state) return 0;
      const loans = this.state.loans.reduce((sum, l) => sum + l.remaining, 0);
      const cc = this.state.credit_card?.balance ?? 0;
      return loans + cc;
    },

    get netWorth() {
      return this.checking + this.savings - this.totalDebt;
    },

    get unreadCount() {
      return this.state?.inbox.filter((e) => e.status === "unread").length ?? 0;
    },

    get inboxSorted() {
      if (!this.state) return [];
      return [...this.state.inbox].sort((a, b) => {
        if (a.status !== b.status) return a.status === "unread" ? -1 : 1;
        if (a.received_month !== b.received_month) return b.received_month - a.received_month;
        return b.received_day - a.received_day;
      });
    },

    get upcomingCalendar() {
      if (!this.state) return [];
      const { day, month } = this.state;
      return [...this.state.calendar]
        .map((c) => ({ ...c, _sort: (c.month - month) * 31 + (c.day - day) }))
        .filter((c) => c._sort >= 0)
        .sort((a, b) => a._sort - b._sort)
        .slice(0, 10);
    },

    get monthlyExpenses() {
      return this.state?.flags?.monthly_expenses ?? [];
    },

    get monthlyExpensesTotal() {
      return this.monthlyExpenses.reduce((s, e) => s + (e.amount || 0), 0);
    },

    get openEvent() {
      if (!this.state || !this.openEventId) return null;
      return this.state.inbox.find((e) => e.event_id === this.openEventId) ?? null;
    },

    openEventRef(ref) {
      this.openEventId = ref.event_id;
      this.lastResolution = ref.resolution ?? null;
    },

    // ---- credit score gauge ----

    scorePct(score) {
      const clamped = Math.max(300, Math.min(850, score));
      return ((clamped - 300) / 550) * 100;
    },

    scoreBand(score) {
      if (score >= 750) return "var(--neon)";
      if (score >= 650) return "var(--gold)";
      if (score >= 550) return "var(--warn)";
      return "var(--danger)";
    },

    // ---- products / unlocks ----

    productRequirement(key) {
      const tiers = UNLOCK_TIERS[key];
      if (!tiers) return "";
      const [csReq, nwReq] = tiers;
      const parts = [];
      if (csReq != null) parts.push(`Credit score ${csReq}`);
      if (nwReq != null) parts.push(`${this.moneyBig(nwReq)} net worth`);
      return parts.join(" + ");
    },

    productLabel(key)  { return PRODUCT_LABELS[key]?.name ?? key; },
    productBlurb(key)  { return PRODUCT_LABELS[key]?.blurb ?? ""; },

    productOwned(key) {
      if (key === "cc_starter" || key === "cc_better") {
        return !!this.state?.credit_card;
      }
      return false;
    },

    productStatus(key) {
      // bnpl has no unlock row — always active.
      if (key === "bnpl") return "active";
      const tiers = UNLOCK_TIERS[key];
      if (!tiers) return "active";
      const [csReq, nwReq] = tiers;
      const csOk = csReq == null || this.state.credit_score >= csReq;
      const nwOk = nwReq == null || this.netWorth >= nwReq;
      return csOk && nwOk ? "active" : "locked_visible";
    },

    productClickable(key) {
      const actionable = ["cc_starter", "cc_better", "personal_loan", "bnpl"];
      if (!actionable.includes(key)) return false;
      if (this.productStatus(key) !== "active") return false;
      return !this.productOwned(key);
    },

    onProductClick(key) {
      if (key === "cc_starter")    return this.applyForCreditCard("starter");
      if (key === "cc_better")     return this.applyForCreditCard("better");
      if (key === "personal_loan") return this.openLoanModal("personal");
      if (key === "bnpl")          return this.openLoanModal("bnpl");
    },

    async applyForCreditCard(tier) {
      const label = tier === "starter" ? "starter credit card" : "premium credit card";
      if (!confirm(`Apply for the ${label}?`)) return;
      try {
        const r = await fetch("/api/apply-cc", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: this.state, tier }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.showToast(data.detail || `Error ${r.status} from /api/apply-cc.`);
          return;
        }
        if (data.state) { this.state = data.state; this.save(); }
        this.showToast(data.message || "Credit card approved.");
      } catch (_) {
        this.showToast("Network error.");
      }
    },

    // ---- skill checks ----

    skillValue(skill) { return this.state?.player.skills[skill] ?? 0; },

    successProb(option) {
      if (!option.skill_check) return 1.0;
      const sv = this.skillValue(option.skill_check.skill);
      const needed = option.skill_check.difficulty_class - sv;
      return Math.max(0, Math.min(20, 21 - needed)) / 20;
    },

    successPct(option) { return Math.round(this.successProb(option) * 100); },

    tinyMd(src) { return tinyMarkdown(src); },

    // ---- actions (endpoint calls, graceful on 404) ----

    async postAction(path, payload = {}) {
      try {
        const r = await fetch(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: this.state, ...payload }),
        });
        if (!r.ok) {
          if (r.status === 404) this.showToast(`${path} not wired yet.`);
          else this.showToast(`Error ${r.status} from ${path}.`);
          return null;
        }
        const data = await r.json();
        if (data.state) {
          this.state = data.state;
          this.save();
        }
        return data;
      } catch (e) {
        this.showToast("Network error.");
        return null;
      }
    },

    // Keep the local event queue topped up. Fires one background LLM call
    // at a time; server returns {event} only, we splice it into our queue.
    // Endpoint is slow (single-event Ollama generation), so we never block the
    // UI on it — errors are swallowed and retried next tick.
    async prefetchEvent() {
      if (!this.state || this.prefetching) return;
      const queue = (this.state.flags && this.state.flags.event_queue) || [];
      if (queue.length >= PREFETCH_TARGET) return;
      this.prefetching = true;
      try {
        const r = await fetch("/api/sage/prefetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: this.state }),
        });
        if (!r.ok) return;
        const data = await r.json();
        if (!data || !data.event) return;
        if (!this.state) return;
        if (!this.state.flags) this.state.flags = {};
        const q = this.state.flags.event_queue || [];
        q.push(data.event);
        this.state.flags.event_queue = q;
        this.save();
        if ((this.state.flags.event_queue.length) < PREFETCH_TARGET) {
          setTimeout(() => this.prefetchEvent(), 0);
        }
      } catch (_) {
        // network errors are fine — the queue just doesn't fill this round
      } finally {
        this.prefetching = false;
      }
    },

    dateSnapshot() {
      if (!this.state) return null;
      return { day: this.state.day, month: this.state.month, dow: this.state.day_of_week };
    },

    triggerDayAdvanceAnim(before) {
      if (!before || !this.state) return;
      const after = this.dateSnapshot();
      if (before.day === after.day && before.month === after.month) return;

      // Pulse date in status bar.
      clearTimeout(this.dayPulseTimer);
      this.dayPulse = false;
      // Re-trigger CSS animation on next frame.
      requestAnimationFrame(() => {
        this.dayPulse = true;
        this.dayPulseTimer = setTimeout(() => { this.dayPulse = false; }, 1200);
      });

      // Persona3-style popup.
      clearTimeout(this.dayAdvanceTimer);
      this.dayAdvanceAnim = { from: before, to: after };
      this.dayAdvanceTimer = setTimeout(() => { this.dayAdvanceAnim = null; }, 1800);
    },

    async advanceDay() {
      const before = this.dateSnapshot();
      const data = await this.postAction("/api/advance-day");
      if (data && data.reason === "budget_required") {
        this.openRequiredBudgetModal();
        return;
      }
      this.triggerDayAdvanceAnim(before);
    },
    async advanceUntilEvent() {
      const before = this.dateSnapshot();
      this.prefetchEvent();
      const data = await this.postAction("/api/advance-until-event");
      if (data && data.reason === "budget_required") {
        this.triggerDayAdvanceAnim(before);
        this.openRequiredBudgetModal();
        return;
      }
      this.triggerDayAdvanceAnim(before);
      if (!data) return;
      if (data.event) {
        this.activeApp = "email";
        this.openEventId = data.event.event_id;
        this.lastResolution = null;
        this.showToast("A new event arrived.");
        this.prefetchEvent();
        return;
      }
      if (data.reason === "calendar_event") {
        this.showToast("Scheduled event fired.");
      } else if (data.reason === "month_rollover") {
        this.showToast("New month.");
      } else if (data.reason === "game_over") {
        this.showToast("Game over.");
      } else {
        this.showToast("Quiet days passed.");
      }
    },
    async summonEvent() {
      // Explicit "spawn an event now" — bound to the Email empty-state CTA.
      const data = await this.postAction("/api/sage/event", { force: true });
      if (data && data.event) {
        this.openEventId = data.event.event_id;
        this.lastResolution = null;
        this.prefetchEvent();
      }
    },
    async rest()              { await this.postAction("/api/rest"); },
    async practiceSkill(skill){ await this.postAction("/api/practice-skill", { skill }); },

    async resolveOption(option) {
      const ev = this.openEvent;
      if (!ev || this.rollingEventId) return;
      if (ev.status === "resolved" || ev.resolution) return;
      this.rollingEventId = ev.event_id;
      const rolled = rollD20FromState(this.state);
      this.save();

      const data = await this.postAction("/api/event/resolve", {
        event_id: ev.event_id,
        option_id: option.id,
        roll_d20: rolled,
      });

      // Fallback local resolution so the UI stays useful before Track B is wired.
      if (!data) {
        const sc = option.skill_check;
        const dc = sc?.difficulty_class ?? 0;
        const skillName = sc?.skill ?? null;
        const skillValue = sc ? this.skillValue(sc.skill) : 0;
        const total = rolled + skillValue;
        const passed = sc ? total >= dc : true;
        const effects = passed ? option.effects_on_success : option.effects_on_failure;
        this.lastResolution = {
          option_id: option.id, rolled, dc, skill: skillName, skillValue, total,
          passed, effects, local: true,
        };
      } else {
        const r = data.resolution ?? {};
        this.lastResolution = {
          option_id: option.id,
          rolled,
          dc: r.dc ?? option.skill_check?.difficulty_class ?? 0,
          skill: r.skill ?? option.skill_check?.skill ?? null,
          skillValue: r.skill_value ?? 0,
          total: r.total ?? rolled,
          passed: r.passed ?? false,
          effects: r.effects_applied ?? {},
          local: false,
        };
      }
      ev.status = "resolved";
      ev.resolution = this.lastResolution;
      this.save();
      this.rollingEventId = null;
      this.prefetchEvent();
    },

    // ---- budget modal ----

    foodTierOrder() { return FOOD_TIER_ORDER; },
    foodTier(key)   { return FOOD_TIERS[key]; },
    statIcon(key)   { return STAT_ICONS[key]; },
    skillIcon(key)  { return SKILL_ICONS[key]; },

    currentBudget() {
      return this.state?.flags?.budget ?? {};
    },

    openBudgetModal() {
      const cur = this.currentBudget();
      this.budgetDraft = {
        food_tier: cur.food_tier ?? FOOD_DEFAULT_TIER,
        leisure: cur.leisure ?? 0,
        bills_buffer: cur.bills_buffer ?? 0,
      };
      this.budgetModalOpen = true;
    },

    openRequiredBudgetModal() {
      this.activeApp = "bank";
      this.openBudgetModal();
      this.budgetModalRequired = true;
      this.showToast("Set a budget for the new month to continue.");
    },

    closeBudgetModal() {
      if (this.budgetSaving) return;
      if (this.budgetModalRequired) return;
      this.budgetModalOpen = false;
    },

    async saveBudget() {
      if (this.budgetSaving) return;
      this.budgetSaving = true;
      const d = this.budgetDraft;
      const budget = {
        food_tier: d.food_tier,
        leisure: Math.max(0, parseInt(d.leisure, 10) || 0),
        bills_buffer: Math.max(0, parseInt(d.bills_buffer, 10) || 0),
      };
      const data = await this.postAction("/api/set-budget", { budget });
      this.budgetSaving = false;
      if (data) {
        this.budgetModalOpen = false;
        this.budgetModalRequired = false;
        this.showToast(data.message || "Budget saved.");
      }
    },

    // ---- loan modal ----

    loanCap(kind) {
      // Mirrors balance.MAX_PERSONAL_LOAN / MAX_BNPL. Keep in sync.
      return kind === "personal" ? 2000000 : 300000;
    },
    loanApr(kind) { return kind === "personal" ? 0.14 : 0.40; },
    loanLabel(kind) { return kind === "personal" ? "Personal loan" : "Buy now, pay later"; },

    openLoanModal(kind) {
      this.loanDraft = { kind, amount_pln: 0 };
      this.loanModalOpen = true;
    },

    closeLoanModal() {
      if (this.loanSaving) return;
      this.loanModalOpen = false;
    },

    async saveLoan() {
      if (this.loanSaving) return;
      const pln = Number(this.loanDraft.amount_pln) || 0;
      const amount = Math.round(pln * 100);
      if (amount <= 0) { this.showToast("Enter a positive amount."); return; }
      if (amount > this.loanCap(this.loanDraft.kind)) {
        this.showToast(`Max ${this.loanCap(this.loanDraft.kind) / 100} PLN.`);
        return;
      }
      if (!confirm(`Take a ${this.loanLabel(this.loanDraft.kind)} for ${pln.toFixed(2)} PLN?`)) return;
      this.loanSaving = true;
      try {
        const r = await fetch("/api/take-loan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            state: this.state,
            kind: this.loanDraft.kind,
            amount,
          }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.showToast(data.detail || `Error ${r.status} from /api/take-loan.`);
          return;
        }
        if (data.state) { this.state = data.state; this.save(); }
        this.loanModalOpen = false;
        this.showToast(data.message || "Loan taken.");
      } catch (_) {
        this.showToast("Network error.");
      } finally {
        this.loanSaving = false;
      }
    },

    // ---- transfer modal ----

    openTransferModal() {
      this.transferDraft = { direction: "to_savings", amount_pln: 0 };
      this.transferModalOpen = true;
    },

    flipTransferDirection() {
      this.transferDraft.direction =
        this.transferDraft.direction === "to_savings" ? "to_checking" : "to_savings";
    },

    closeTransferModal() {
      if (this.transferSaving) return;
      this.transferModalOpen = false;
    },

    async saveTransfer() {
      if (this.transferSaving) return;
      const pln = Number(this.transferDraft.amount_pln) || 0;
      const amount = Math.round(pln * 100);
      if (amount <= 0) { this.showToast("Enter a positive amount."); return; }
      this.transferSaving = true;
      try {
        const r = await fetch("/api/transfer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            state: this.state,
            direction: this.transferDraft.direction,
            amount,
          }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.showToast(data.detail || `Error ${r.status} from /api/transfer.`);
          return;
        }
        if (data.state) { this.state = data.state; this.save(); }
        this.transferModalOpen = false;
        this.showToast(data.message || "Transfer complete.");
      } catch (_) {
        this.showToast("Network error.");
      } finally {
        this.transferSaving = false;
      }
    },

    // ---- save/export ----

    exportSave() {
      const blob = new Blob([JSON.stringify(this.state, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `quid-save-m${this.state.month}d${this.state.day}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },

    triggerImport() {
      this.$refs.importFile.click();
    },

    async importSave(evt) {
      const file = evt.target.files && evt.target.files[0];
      evt.target.value = "";
      if (!file) return;
      let parsed;
      try {
        parsed = JSON.parse(await file.text());
      } catch (_) {
        this.showToast("Import failed: not valid JSON.");
        return;
      }
      if (!parsed || typeof parsed !== "object" || parsed.schema_version !== SCHEMA_VERSION) {
        this.showToast(`Import failed: schema_version must be ${SCHEMA_VERSION}.`);
        return;
      }
      // Round-trip through /api/echo to catch schema drift the client can't see.
      try {
        const r = await fetch("/api/echo", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: parsed }),
        });
        if (!r.ok) { this.showToast("Import rejected by server."); return; }
        const data = await r.json();
        this.state = data.state;
      } catch (_) {
        this.showToast("Import failed: server unreachable.");
        return;
      }
      this.openEventId = null;
      this.lastResolution = null;
      this.activeApp = "home";
      this.save();
      this.showToast("Save imported.");
    },
  };
}

// Register with Alpine before it starts processing the DOM.
document.addEventListener('alpine:init', () => {
  Alpine.data('quid', quid);
});
