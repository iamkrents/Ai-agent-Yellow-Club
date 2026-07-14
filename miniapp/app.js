const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  // Apply Telegram colour scheme as data-theme so CSS :root[data-theme="dark"] selectors fire
  (function _applyTgTheme() {
    const scheme = tg.colorScheme;
    if (scheme === "dark" || scheme === "light") {
      document.documentElement.setAttribute("data-theme", scheme);
    }
    if (typeof tg.onEvent === "function") {
      tg.onEvent("themeChanged", () => {
        const s = tg.colorScheme;
        if (s === "dark" || s === "light") document.documentElement.setAttribute("data-theme", s);
      });
    }
  })();
  try {
    if (typeof tg.setHeaderColor === "function") tg.setHeaderColor("#ffd84d");
    if (typeof tg.setBackgroundColor === "function") tg.setBackgroundColor("#f6f7fb");
    if (typeof tg.requestFullscreen === "function") tg.requestFullscreen();
    else if (typeof tg.expand === "function") tg.expand();
    if (typeof tg.enableClosingConfirmation === "function") tg.enableClosingConfirmation();
  } catch (e) {
    console.warn("Telegram fullscreen request failed", e);
  }
}

// v7.0.8: set --tg-vh from Telegram viewportStableHeight so app-shell can use it as scroll container
function _updateTgViewportHeight() {
  const h = (tg && (tg.viewportStableHeight || tg.viewportHeight)) || window.innerHeight;
  if (h > 50) document.documentElement.style.setProperty("--tg-vh", h + "px");
}
_updateTgViewportHeight();
if (tg && typeof tg.onEvent === "function") {
  tg.onEvent("viewportChanged", _updateTgViewportHeight);
}
window.addEventListener("resize", _updateTgViewportHeight, { passive: true });

// Mark body/html for Telegram CSS and immediately set hardcoded top offset.
// We set --app-top-safe-offset directly on <html> so it wins over any CSS body-class rule.
if (tg) {
  document.body.classList.add("is-telegram-webapp");
  document.documentElement.classList.add("is-telegram-webapp");
  document.documentElement.style.setProperty(
    "--app-top-safe-offset",
    "calc(env(safe-area-inset-top, 0px) + 56px)"
  );
}

// Refine offset when Bot API 8.0+ safeAreaInsets gives the precise value
function _applySafeArea() {
  const top = tg?.safeAreaInsets?.top;
  if (typeof top === "number" && top > 60) {
    // API includes physical notch + Telegram chrome — use directly with small buffer
    document.documentElement.style.setProperty("--tg-safe-top", top + "px");
    document.documentElement.style.setProperty("--app-top-safe-offset", (top + 8) + "px");
  }
}
_applySafeArea();
tg?.onEvent?.("safeAreaChanged", _applySafeArea);

// Prevent iOS/Telegram WebView zoom (pinch, gesture, double-tap)
["gesturestart", "gesturechange", "gestureend"].forEach(t =>
  document.addEventListener(t, e => e.preventDefault(), { passive: false })
);
let _lastTap = 0;
document.addEventListener("touchend", e => {
  const now = Date.now();
  if (now - _lastTap < 300) e.preventDefault();
  _lastTap = now;
}, { passive: false });

const initData = tg?.initData || "";
const unsafeUserId = tg?.initDataUnsafe?.user?.id ? String(tg.initDataUnsafe.user.id) : "";
const urlParams = new URLSearchParams(window.location.search);
const devUserId = urlParams.get("dev_user_id") || "";
const launchUserId = urlParams.get("yc_user_id") || "";
const launchTs = urlParams.get("yc_ts") || "";
const launchSig = urlParams.get("yc_sig") || "";

console.log("MiniApp version: v7.0.92.2");
window.addEventListener("error", (ev) => {
  console.error("[uncaught]", ev.message, (ev.filename || "") + ":" + ev.lineno, ev.error);
});
window.addEventListener("unhandledrejection", (ev) => {
  console.error("[unhandled rejection]", ev.reason);
});

const state = {
  me: null,
  lessons: [],
  tasks: [],
  workSchedule: [],
  workScheduleWeek: "current",
  openSlots: [],
  openSlotsWeek: "current",
  openSlotsLocationFilter: "all",
  openSlotsTimeFilter: "all",
  openSlotsMeta: {},
  clientTasks: [],
  clientTaskTypeFilter: "all",
  clientTaskStatusFilter: "active",
  clientTaskFormOpen: false,
  clientTaskEditingId: "",
  clientTaskExpandedId: "",
  clientTaskAutoSync: null,
  clientTasksSyncing: false,
  clientTaskSlotsLoading: {},
  clientTaskSlotResults: {},
  clientTaskSlotNotes: {},
  clientTaskSelectedSlots: {},
  clientTaskGeneratedMessages: {},
  clientTaskOpenSlotsCache: {},
  helpTeacherHtml: "",
  reportsMonth: "",
  reportsData: null,
  reportsBusy: false,
  childrenReportMonth: "",
  childrenReportData: null,
  childrenReportBusy: false,
  bepaidStatus: null,
  bepaidData: null,
  bepaidBusy: false,
  bepaidImportBusy: false,
  bepaidImportResult: null,
  bepaidMonth: "",
  kpiData: null,
  kpiBusy: false,
  adminWorkScheduleWeek: "current",
  askMessages: [],
  askBusy: false,
  selectedLesson: null,
  lessonCache: {},
  lessonFetches: {},
  lessonPreloadTimer: null,
  admin: null,
  adminTab: "overview",
  adminWorkTypeFilter: "all",
  adminWorkLocationFilter: "all",
  adminKpiPeriod: "month",
  adminKpiData: null,
  adminKpiBusy: false,
  internTrack: null,
  internBusy: false,
  internSection: null,
  internOpenStep: null,
  internUpcomingLessons: [],
  internAdminData: null,
  internAdminBusy: false,
  internAdminFilter: "all",
  internAdminOpenUid: null,
  foodDebugLastResult: null,
  campChildrenData: null,
  myChildren: null,
  activeMenus: null,
  myOrders: null,
  selectedChildId: null,
  foodOrderExpanded: {},
  foodMenuData: null,
  foodMenuSelected: null,
  foodMenuView: "list",
  foodMenuSummaryMenuId: null,
  isEditingFoodOrder: false,
  foodMenuDrafts: {},
  kitchenMenus: null,
  kitchenSelectedMenuId: null,
  kitchenSummaryData: null,
  kitchenSummaryBusy: false,
  kitchenCopyNotice: "",
  kitchenEditorData: null,
  kitchenEditorSelected: null,
  kitchenAuditData: null,
  kitchenAuditMenuId: null,
  foodAdminAuditData: null,
  foodAdminAuditMenuId: null,
};

function $(id) { return document.getElementById(id); }
const ROLE_LABELS = {
  owner: "Владелец",
  admin: "Администратор",
  teacher: "Преподаватель",
  methodist: "Старший преподаватель",
  intern: "Стажер",
  client_manager: "Клиент-менеджер",
  director: "Директор",
  operations: "Операционный менеджер",
  other: "Сотрудник",
  parent: "Родитель",
  kitchen: "Кухня",
  restaurant: "Кухня",
};
function roleLabel(role) { return ROLE_LABELS[role] || role || "роль"; }
function roleCaps() { return state.me?.capabilities || {}; }
function canUseAdmin() { return !!roleCaps().canUseAdmin; }
function canUseLessons() { return !!roleCaps().canUseLessons; }
function canUseSchedule() { return !!roleCaps().canUseSchedule; }
function canUseOpenSlots() { return !!roleCaps().canUseOpenSlots; }
function canUseReports() { return !!roleCaps().canUseReports; }
function canUseChildrenReport() { const r = state.me?.role || ""; return ["owner","admin","director","client_manager","operations"].includes(r); }
function canUseInternship() { return !!roleCaps().canUseInternship; }
function canAskAgent() { return roleCaps().canAskAgent !== false; }
function canUseFoodKitchenSummary() { return !!roleCaps().canUseFoodKitchenSummary; }
function canSeeFoodPrices() { return !!roleCaps().canSeeFoodPrices; }
function canSeeFoodCostReport() { return !!roleCaps().canSeeFoodCostReport; }
function canCreateFoodMenu() { return !!roleCaps().canCreateFoodMenu; }
function canEditFoodMenuDraft() { return !!roleCaps().canEditFoodMenuDraft; }
function canPublishFoodMenu() { return !!roleCaps().canPublishFoodMenu; }
function canEditFoodDeadline() { return !!roleCaps().canEditFoodDeadline; }
function canDeleteFoodMenu() { return !!roleCaps().canDeleteFoodMenu; }
function canAdminFoodOrders() { return !!roleCaps().canAdminFoodOrders; }
function canUseFoodMenuOcr() {
  return Boolean(
    state.me?.foodMenuOcrEnabled ||
    state.me?.capabilities?.foodMenuOcrEnabled ||
    state.capabilities?.foodMenuOcrEnabled
  );
}
function isAdminRole(role) { return ["owner", "methodist", "operations"].includes(role || ""); }
const MVP_TABS_BY_ROLE = {
  intern:         ["intern", "help", "ask", "my-lunch"],
  teacher:        ["lessons", "tasks", "help", "ask", "my-lunch"],
  methodist:      ["lessons", "tasks", "help", "ask", "admin", "my-lunch"],
  owner:          ["lessons", "tasks", "reports", "help", "ask", "admin", "my-lunch"],
  admin:          ["lessons", "tasks", "reports", "help", "ask", "admin", "my-lunch"],
  operations:     ["lessons", "tasks", "reports", "help", "ask", "admin", "my-lunch"],
  client_manager: ["reports", "my-lunch"],
  director:       ["reports", "my-lunch"],
  parent:         ["my-children", "food", "help"],
};
const MVP_ADMIN_TABS = ["interns", "prep-results", "lesson-control", "teachers", "users", "notifications", "food-debug", "food-children", "food-menu", "food-report"];
function isMvpMode() { return !!state.me?.mvpReleaseMode; }
function availableAdminTabs() {
  const tabs = roleCaps().adminTabs || [];
  const all = Array.isArray(tabs) ? tabs : [];
  if (isMvpMode()) return all.filter(t => MVP_ADMIN_TABS.includes(t));
  return all;
}
function escapeHtml(s) { return String(s || "").replace(/[&<>\"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }
function fmtByn(value) {
  const n = Number(value == null ? 0 : value);
  if (isNaN(n)) return "— BYN";
  return n.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " BYN";
}
function paymentIntentAmountByn(intent) {
  if (intent && intent.amount_byn != null) return Number(intent.amount_byn || 0);
  if (intent && intent.amount_minor != null) return Number(intent.amount_minor || 0) / 100;
  return 0;
}
function formatFileSize(bytes) {
  const n = Number(bytes || 0);
  if (!n || n < 1) return "";
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} КБ`;
  return `${(n / 1024 / 1024).toFixed(n >= 10 * 1024 * 1024 ? 0 : 1)} МБ`;
}
function nl2br(s) { return escapeHtml(s).replace(/\n/g, "<br>"); }
function escapeAttr(s) { return escapeHtml(s).replace(/`/g, "&#96;"); }
function sanitizeChatUrl(raw) {
  const url = String(raw || "").trim();
  if (!/^https?:\/\//i.test(url)) return "";
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) return "";
    return parsed.href;
  } catch (e) {
    return "";
  }
}
function shortLinkLabel(url) {
  try {
    const parsed = new URL(url);
    if (parsed.hostname.includes("notion.")) return "Открыть Notion";
    if (parsed.hostname.includes("youtu.be") || parsed.hostname.includes("youtube.")) return "Открыть YouTube";
    return parsed.hostname.replace(/^www\./, "");
  } catch (e) {
    return url;
  }
}
function formatChatTextPart(text) {
  return escapeHtml(text)
    .replace(/\*\*([^*\n][\s\S]*?[^*\n])\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n][\s\S]*?[^_\n])__/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}
function formatChatMessage(text) {
  const raw = String(text || "").replace(/<br\s*\/?\s*>/gi, "\n");
  const re = /\[([^\]\n]{1,160})\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>'"]+)/gi;
  let out = "";
  let last = 0;
  let match;
  while ((match = re.exec(raw)) !== null) {
    out += formatChatTextPart(raw.slice(last, match.index));
    let label = match[1] || "";
    let url = match[2] || match[3] || "";
    let suffix = "";
    while (/[.,!?;:)}\]]$/.test(url)) {
      suffix = url.slice(-1) + suffix;
      url = url.slice(0, -1);
    }
    const safe = sanitizeChatUrl(url);
    if (safe) {
      const visible = label ? label : shortLinkLabel(safe);
      out += `<a class="chat-link" href="${escapeAttr(safe)}" target="_blank" rel="noopener noreferrer">${escapeHtml(visible)}</a>`;
      out += formatChatTextPart(suffix);
    } else {
      out += formatChatTextPart(match[0]);
    }
    last = match.index + match[0].length;
  }
  out += formatChatTextPart(raw.slice(last));
  return out;
}
function bindChatLinks(root) {
  root?.querySelectorAll?.("a.chat-link")?.forEach(link => {
    link.addEventListener("click", (event) => {
      const href = link.getAttribute("href") || "";
      if (tg && typeof tg.openLink === "function" && href) {
        event.preventDefault();
        tg.openLink(href);
      }
    });
  });
}
function autoResizeChatInput() {
  const input = $("askInput");
  if (!input) return;
  input.style.height = "auto";
  const max = 132;
  input.style.height = `${Math.min(input.scrollHeight, max)}px`;
}
function setChatInputFocused(isFocused) {
  document.body.classList.toggle("chat-input-focused", Boolean(isFocused));
}
function setChatSubmitBusy(button, busy) {
  if (!button) return;
  button.disabled = Boolean(busy);
  button.classList.toggle("loading", Boolean(busy));
  button.setAttribute("aria-label", busy ? "Агент готовит ответ" : "Отправить");
  button.innerHTML = '<span class="chat-send-icon" aria-hidden="true">➤</span>';
}
function stripReportMarkup(text) {
  const div = document.createElement("div");
  div.innerHTML = String(text || "")
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<\/p\s*>/gi, "\n");
  return (div.textContent || div.innerText || "")
    .replace(/\*\*/g, "")
    .replace(/__+/g, "")
    .replace(/`+/g, "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
function pill(text, cls = "") { return `<span class="pill ${cls}">${escapeHtml(text)}</span>`; }
function cssEscapeValue(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value));
  return String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}


const CHAT_QUICK_PROMPTS = {
  // Teacher
  single_student:      "Что делать, если на занятие пришёл только один ученик? Дай порядок действий для преподавателя и что написать в рабочий чат.",
  parent_report:       "Помоги составить отчёт родителям после занятия. Сначала спроси тему, что реально сделали и чему научились, если этих данных не хватает.",
  moyklass:            "Как правильно заполнить МойКласс после занятия? Напомни, что отметить и как записать тему следующего занятия.",
  trial_lesson:        "Как провести пробное занятие и что делать, если пробный ученик не пришёл?",
  no_topic:            "Что делать, если в МойКласс не указана тема занятия и материал Notion не подобрался?",
  prep:                "Как правильно подготовиться к занятию и что именно отправить старшему преподавателю на проверку?",
  revision:            "Старший преподаватель отправил работу на доработку. Что мне сделать и как правильно исправить подготовку?",
  notion_material:     "Что делать, если материал Notion подобран неверно или не соответствует теме занятия в МойКласс?",
  close_lesson:        "Как правильно закрыть занятие? Что нужно сделать после занятия: отчёт родителям, МойКласс, работы учеников, кабинет.",
  // Intern
  intern_next_step:          "Что мне делать дальше по стажировке? Учти мой текущий шаг и что уже выполнено.",
  intern_step_blocked:       "Почему следующий шаг заблокирован? Что нужно сделать, чтобы он открылся?",
  intern_observation_comment:"Как правильно написать комментарий к наблюдению занятия? Что нужно отметить?",
  intern_prep_demo:          "Как мне подготовиться к пробному занятию со старшим преподавателем?",
  intern_work_rejected:      "Мою подготовительную работу отклонили. Что делать дальше и как правильно исправить?",
  intern_feedback_form:      "Как правильно заполнить обратную связь после пробного занятия? Что написать?",
  intern_admission:          "Что значит допуск? Как я узнаю о финальном решении методиста?",
  // Admin / methodist / operations
  admin_needs_review:    "Что сейчас требует моего внимания? Дай список: стажёры, работы преподавателей, незакрытые занятия.",
  admin_interns_pending: "Какие стажёры ждут решения? Кто прислал работу или записался на пробное?",
  admin_prep_review:     "Какие работы преподавателей сейчас ждут проверки?",
  admin_unclosed_lessons:"Какие занятия не закрыты после проведения?",
  admin_lesson_problems: "Есть ли проблемы по занятиям? Кто не подготовился или не закрыл занятие?",
  admin_not_prepared:    "Кто из преподавателей не подготовился к занятию? Дай список с датами.",
  admin_staff_roles:     "Кто из сотрудников в какой роли? Дай краткий список: преподаватели, стажёры, методисты.",
  admin_today_tasks:     "Что мне нужно сделать сегодня как методисту или администратору? Дай список приоритетов.",
};

const CHAT_CHIPS_BY_ROLE = {
  intern: [
    ["intern_next_step",          "Что делать дальше?"],
    ["intern_step_blocked",       "Почему шаг заблокирован?"],
    ["intern_observation_comment","Как написать комментарий?"],
    ["intern_prep_demo",          "Как подготовиться к пробному?"],
    ["intern_work_rejected",      "Работу отклонили"],
    ["intern_feedback_form",      "Как заполнить ОС?"],
    ["intern_admission",          "Что значит допуск?"],
  ],
  teacher: [
    ["single_student",  "Один ученик"],
    ["parent_report",   "Отчёт родителям"],
    ["moyklass",        "МойКласс"],
    ["no_topic",        "Нет темы"],
    ["prep",            "Подготовка"],
    ["revision",        "Доработка"],
    ["close_lesson",    "Закрыть занятие"],
    ["notion_material", "Материал Notion"],
  ],
  admin: [
    ["admin_needs_review",    "Что требует проверки?"],
    ["admin_interns_pending", "Стажёры ждут решения"],
    ["admin_prep_review",     "Работы на проверке"],
    ["admin_unclosed_lessons","Незакрытые занятия"],
    ["admin_lesson_problems", "Проблемы по занятиям"],
    ["admin_not_prepared",    "Кто не подготовился?"],
    ["admin_staff_roles",     "Сотрудники и роли"],
    ["admin_today_tasks",     "Что сделать сегодня?"],
  ],
};

function shortLessonForChat(lesson) {
  if (!lesson) return null;
  return {
    id: String(lesson.id || ""),
    title: String(lesson.title || lesson.group || "Занятие"),
    date: String(lesson.date || ""),
    time: String(lesson.time || ""),
    topic: String(lesson.topic || ""),
    room: String(lesson.room || ""),
    teacher: String(lesson.teacher || ""),
    prepStatus: String(lesson.prepStatus || lesson.prep_status || ""),
    lessonStatus: String(lesson.lessonStatus || lesson.lesson_status || ""),
    reportStatus: String(lesson.reportStatus || lesson.report_status || ""),
    moyklassStatus: String(lesson.moyklassStatus || lesson.moyklass_status || ""),
    workStatus: String(lesson.workStatus || lesson.work_status || ""),
    roomStatus: String(lesson.roomStatus || lesson.room_status || ""),
  };
}

function buildChatWorkContext() {
  const lessons = (state.lessons || []).slice(0, 10).map(shortLessonForChat).filter(Boolean);
  const tasks = (state.tasks || []).slice(0, 8).map(t => ({
    title: String(t.title || ""),
    text: String(t.text || ""),
    taskType: String(t.task_type || t.type || ""),
    dueAt: String(t.due_at || ""),
    lessonId: String(t.lesson_id || ""),
    priority: String(t.priority || ""),
  }));
  const selected = state.selectedLesson ? shortLessonForChat(state.selectedLesson.lesson || state.selectedLesson) : null;
  const workSchedule = (state.workSchedule || []).slice(0, 40).map(slot => ({
    dayOfWeek: Number(slot.day_of_week ?? 0),
    startTime: String(slot.start_time || ""),
    endTime: String(slot.end_time || ""),
    location: String(slot.location || ""),
    note: String(slot.note || ""),
  }));
  const openSlots = (state.openSlots || []).slice(0, 60).map(slot => ({
    teacherName: String(slot.teacher_name || slot.teacherName || ""),
    date: String(slot.date || ""),
    dateLabel: String(slot.date_label || ""),
    dayName: String(slot.day_name || ""),
    dayOfWeek: Number(slot.day_of_week ?? 0),
    startTime: String(slot.start_time || ""),
    endTime: String(slot.end_time || ""),
    location: String(slot.location || ""),
    note: String(slot.note || ""),
  }));
  const clientTasks = (state.clientTasks || []).slice(0, 20).map(task => ({
    id: String(task.id || ""),
    type: String(task.task_type || ""),
    status: String(task.status || ""),
    priority: String(task.priority || ""),
    clientName: String(task.client_name || ""),
    childName: String(task.child_name || ""),
    desiredDate: String(task.desired_date || ""),
    desiredTime: String(task.desired_time || ""),
    location: String(task.location || ""),
    amount: String(task.amount || ""),
    paymentFor: String(task.payment_for || ""),
    deadline: String(task.deadline || ""),
    comment: String(task.comment || ""),
  }));
  const report = state.reportsData?.report || null;
  const reportContext = report ? {
    month: String(state.reportsData?.month || report.month || ""),
    activeStudents: String(report.keyMetrics?.activeStudents ?? ""),
    lessons: String(report.keyMetrics?.lessons ?? ""),
    visits: String(report.keyMetrics?.visits ?? ""),
    missed: String(report.keyMetrics?.missed ?? ""),
    trialRecords: String(report.keyMetrics?.trialRecords ?? ""),
    paymentsCount: String(report.keyMetrics?.paymentsCount ?? ""),
    paymentsSum: String(report.keyMetrics?.paymentsSum ?? ""),
  } : null;
  return {
    role: state.me?.role || "",
    roleLabel: state.me?.roleLabel || roleLabel(state.me?.role),
    realRole: state.me?.realRole || "",
    fullName: state.me?.fullName || "",
    mkTeacherId: state.me?.mkTeacherId || "",
    mkTeacherName: state.me?.mkTeacherName || "",
    lessons,
    tasks,
    workSchedule,
    openSlots,
    clientTasks,
    reportContext,
    selectedLesson: selected,
  };
}

function chatHistoryForApi() {
  return (state.askMessages || [])
    .slice(-8)
    .map(m => ({ role: m.role === "user" ? "user" : "assistant", text: String(m.text || "").slice(0, 1400) }))
    .filter(m => m.text.trim());
}

function sendQuickChatPrompt(key) {
  const prompt = CHAT_QUICK_PROMPTS[key] || "";
  const input = $("askInput");
  if (!prompt || !input || state.askBusy) return;
  input.value = prompt;
  input.blur?.();
  setChatInputFocused(false);
  window.setTimeout(() => sendAskQuestion(), 30);
}

function apiAuthPairs() {
  const pairs = [["initData", initData]];
  if (devUserId) pairs.push(["dev_user_id", devUserId]);
  if (unsafeUserId) pairs.push(["unsafe_user_id", unsafeUserId]);
  if (launchUserId) pairs.push(["yc_user_id", launchUserId]);
  if (launchTs) pairs.push(["yc_ts", launchTs]);
  if (launchSig) pairs.push(["yc_sig", launchSig]);
  return pairs;
}
function apiQuery() { return apiAuthPairs().map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&"); }
function apiUrl(path) { return `${path}${path.includes("?") ? "&" : "?"}${apiQuery()}`; }
function apiDownloadUrl(fileId) { return apiUrl(`/api/prep-result-download?fileId=${encodeURIComponent(fileId || "")}`); }
function apiInternWorkDownloadUrl(id) { return apiUrl(`/api/intern/work-download?id=${encodeURIComponent(id || "")}`); }
function appendAuthForm(form) { apiAuthPairs().forEach(([k, v]) => form.append(k, v)); }


function setNotice(text, type = "") {
  const el = $("notice");
  if (!el) return;
  el.textContent = text;
  el.className = `notice ${type}`.trim();
  // Restart CSS entry animation on each call
  el.style.animation = "none";
  void el.offsetHeight;
  el.style.animation = "";
}
function safeUserError(e) {
  const msg = (typeof e === "string" ? e : (e?.message || "")).trim();
  if (!msg) return "Не удалось выполнить операцию. Попробуйте ещё раз.";
  if (/the string did not match|typeerror|domexception|cannot read prop|unexpected token|undefined is not|null is not|invalidstateerror|is not a function|network error|failed to fetch|script error/i.test(msg)) {
    return "Не удалось выполнить операцию. Попробуйте ещё раз.";
  }
  return msg;
}
async function apiGet(path) {
  const res = await fetch(apiUrl(path), { cache: "no-store" });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "Ошибка API");
  return data;
}
async function apiPost(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, initData, dev_user_id: devUserId, unsafe_user_id: unsafeUserId, yc_user_id: launchUserId, yc_ts: launchTs, yc_sig: launchSig }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "Ошибка API");
  return data;
}

// Like apiPost but does NOT throw when data.ok === false — caller checks data.ok itself.
// Use for cases where error details in the response body must be inspected (e.g. multiple_locations).
async function _apiPostRaw(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, initData, dev_user_id: devUserId, unsafe_user_id: unsafeUserId, yc_user_id: launchUserId, yc_ts: launchTs, yc_sig: launchSig }),
  });
  return res.json();
}

function labelStatus(value, type = "Статус") {
  const map = {
    ready: ["Подготовка: готов", "ok"],
    needs_help: ["Нужна помощь", "bad"],
    done: [`${type}: выполнено`, "ok"],
    closed: ["Занятие закрыто", "ok"],
    problem: ["Есть проблема", "bad"],
    submitted: [`${type}: на проверке`, "warn"],
    rejected: [`${type}: отклонено`, "bad"],
    not_started: [`${type}: не отмечено`, "warn"],
    not_checked: [`${type}: не отмечено`, "warn"],
    none: [`${type}: нет`, "ok"],
    "": [`${type}: не отмечено`, "warn"],
  };
  return map[value] || [String(value || "не отмечено"), "warn"];
}


function parentHelpHtml() {
  return `<div class="section-head help-head"><div>
    <h2>Помощь родителю</h2>
    <p>Как привязать ребёнка и пользоваться модулем питания.</p>
  </div></div>
  <div class="help-guide">
    <details class="help-accordion" open>
      <summary class="help-accordion-title">Как привязать ребёнка?</summary>
      <div class="help-accordion-body">
        <p>Перейдите на вкладку <b>Мои дети</b> и введите код YC-XXXX, который вы получили от администратора смены. Нажмите «Привязать» — ребёнок появится в вашем списке.</p>
      </div>
    </details>
    <details class="help-accordion">
      <summary class="help-accordion-title">Где взять код?</summary>
      <div class="help-accordion-body">
        <p>Код выдаётся администратором Yellow Club. Он имеет формат <b>YC-XXXX</b> (4 символа после дефиса). Если у вас нет кода, обратитесь в администрацию.</p>
      </div>
    </details>
    <details class="help-accordion">
      <summary class="help-accordion-title">Что делать, если код не работает?</summary>
      <div class="help-accordion-body">
        <p>Проверьте правильность кода — он чувствителен к написанию, но не к регистру. Если код верный, но не принимается, обратитесь к администратору: возможно, код уже был использован или деактивирован.</p>
      </div>
    </details>
    <details class="help-accordion">
      <summary class="help-accordion-title">Можно ли привязать несколько детей?</summary>
      <div class="help-accordion-body">
        <p>Да. Для каждого ребёнка у вас должен быть отдельный код. После привязки первого ребёнка вы увидите кнопку «Добавить ещё ребёнка» на вкладке «Мои дети».</p>
      </div>
    </details>
    <details class="help-accordion">
      <summary class="help-accordion-title">Когда будет доступно меню питания?</summary>
      <div class="help-accordion-body">
        <p>Меню питания и заказы появятся в ближайшее время. Следите за обновлениями приложения.</p>
      </div>
    </details>
  </div>`;
}

function clientManagerHelpHtml() {
  return `
        <div class="section-head help-head">
          <div>
            <h2>Помощь клиент-менеджера</h2>
            <p>Как работать с окнами, задачами, отчётами и чатом, чтобы быстрее ставить занятия клиентам.</p>
          </div>
        </div>

        <div class="help-guide">
          <article class="card help-card help-hero">
            <div class="help-hero-icon">🧭</div>
            <div>
              <h3>Главный принцип</h3>
              <p>Работайте от задачи клиента: система создаёт задачи из МойКласс, менеджер подбирает окно, согласует вариант с родителем и переводит задачу в правильный статус.</p>
            </div>
          </article>

          <article class="card help-card help-card-soft">
            <div class="help-card-title-row">
              <div>
                <h3>Быстрый маршрут</h3>
                <p>Короткий порядок работы клиент-менеджера в приложении.</p>
              </div>
            </div>
            <div class="help-route">
              <div class="help-route-step">
                <span class="help-route-num">1</span>
                <div class="help-route-body">
                  <b>Откройте “Задачи”</b>
                  <small>Посмотрите новые отработки, пробные и оплаты. Большая часть задач создаётся автоматически из МойКласс.</small>
                </div>
              </div>
              <div class="help-route-step">
                <span class="help-route-num">2</span>
                <div class="help-route-body">
                  <b>Откройте задачу</b>
                  <small>Проверьте ученика, дату, учебный класс, комментарий и источник задачи.</small>
                </div>
              </div>
              <div class="help-route-step">
                <span class="help-route-num">3</span>
                <div class="help-route-body">
                  <b>Подберите окно</b>
                  <small>Для отработки или пробного занятия нажмите “Подобрать окна” и выберите подходящий вариант.</small>
                </div>
              </div>
              <div class="help-route-step">
                <span class="help-route-num">4</span>
                <div class="help-route-body">
                  <b>Согласуйте с клиентом</b>
                  <small>Используйте черновик сообщения, отредактируйте текст при необходимости и отправьте родителю.</small>
                </div>
              </div>
              <div class="help-route-step">
                <span class="help-route-num">5</span>
                <div class="help-route-body">
                  <b>Обновите статус</b>
                  <small>Поставьте “Ждём ответа клиента”, “В работе” или другой актуальный статус, чтобы задача не потерялась.</small>
                </div>
              </div>
              <div class="help-route-step">
                <span class="help-route-num">6</span>
                <div class="help-route-body">
                  <b>Проверьте отчёты</b>
                  <small>Используйте “Отчёты”, чтобы видеть оплаты, пропуски, отработки и создавать новые задачи из МойКласс.</small>
                </div>
              </div>
            </div>
          </article>

          <article class="card help-card">
            <h3>Что означают страницы</h3>
            <div class="help-page-list help-page-grid">
              <div class="help-page-item"><span>🪟</span><div><b>Окна</b><p>Свободные возможности преподавателей. Используйте для подбора времени под пробное, отработку, замену или регулярное занятие.</p></div></div>
              <div class="help-page-item"><span>📌</span><div><b>Задачи</b><p>Основная рабочая зона менеджера. Здесь задачи по отработкам, пробным и оплатам, созданные автоматически или вручную.</p></div></div>
              <div class="help-page-item"><span>📊</span><div><b>Отчёты</b><p>Статистика из МойКласс по оплатам, посещениям, пропускам, пробным и задачам.</p></div></div>
              <div class="help-page-item"><span>❓</span><div><b>Помощь</b><p>Памятка по процессам клиент-менеджера и работе в приложении.</p></div></div>
              <div class="help-page-item"><span>💬</span><div><b>Чат</b><p>Рабочий агент. Можно попросить подобрать окна, объяснить отчёт, составить сообщение клиенту или проверить порядок действий.</p></div></div>
            </div>
          </article>

          <article class="card help-card">
            <h3>Как работать с задачами</h3>
            <div class="help-status-grid help-status-legend">
              <div class="help-status yellow"><b>Новая</b><span>Задача создана, но менеджер ещё не начал работу.</span></div>
              <div class="help-status blue"><b>В работе</b><span>Менеджер уже открыл задачу, подбирает окно или готовит сообщение.</span></div>
              <div class="help-status white"><b>Ждём клиента</b><span>Вариант отправлен родителю, ожидается ответ.</span></div>
              <div class="help-status green"><b>Выполнена</b><span>Задача закрыта: отработка поставлена, оплата решена или пробное обработано.</span></div>
              <div class="help-status red"><b>Отменена</b><span>Задача неактуальна или клиент отказался.</span></div>
            </div>
            <p class="help-note">Ближайшая доработка: агент будет лучше понимать, что задача закрыта или выполнена, и не будет предлагать по ней лишние действия.</p>
          </article>

          <div class="help-workflow-grid">
            <div class="help-workflow-card before">
              <div class="help-workflow-head">
                <span>Отработка</span>
                <h4>Если ученик пропустил занятие</h4>
              </div>
              <ul class="help-checklist">
                <li>Откройте задачу “Отработка”.</li>
                <li>Проверьте ученика, группу, тему и срок до следующего занятия.</li>
                <li>Нажмите “Подобрать окна”.</li>
                <li>Выберите удобное окно преподавателя.</li>
                <li>Скопируйте или отредактируйте сообщение родителю.</li>
                <li>После отправки поставьте статус “Ждём ответа клиента”.</li>
                <li>После записи проверьте МойКласс и закройте задачу.</li>
              </ul>
            </div>

            <div class="help-workflow-card after">
              <div class="help-workflow-head">
                <span>Оплата</span>
                <h4>Если закончились занятия</h4>
              </div>
              <ul class="help-checklist">
                <li>Откройте задачу “Оплата”.</li>
                <li>Проверьте ученика, сумму и дедлайн до следующего занятия.</li>
                <li>Сформируйте сообщение клиенту.</li>
                <li>Проверьте текст и отправьте родителю.</li>
                <li>Поставьте статус “Ждём ответа клиента” или “В работе”.</li>
                <li>После оплаты проверьте МойКласс.</li>
                <li>Переведите задачу в “Выполнена”.</li>
              </ul>
            </div>
          </div>

          <details class="card help-card help-section-details" open>
            <summary>Как работать со страницей “Окна”</summary>
            <div class="help-section-content">
              <p>Страница показывает свободные возможности преподавателей. Преподаватели универсальные, поэтому курс и тип занятия выбирать не нужно.</p>
              <div class="help-page-list compact">
                <div class="help-page-item"><span>📍</span><div><b>Филиал / формат</b><p>Фильтруйте по Кульман 1/1, Мстиславца 6, онлайн или любому формату.</p></div></div>
                <div class="help-page-item"><span>🕒</span><div><b>Время дня</b><p>Можно быстро посмотреть утро, день или вечер.</p></div></div>
                <div class="help-page-item"><span>💬</span><div><b>Использовать</b><p>Передаёт выбранное окно в чат, чтобы агент помог оформить сообщение клиенту.</p></div></div>
              </div>
              <p class="help-note">Перед финальной записью всегда проверьте МойКласс: занятость преподавателя, кабинет и актуальность клиента.</p>
            </div>
          </details>

          <details class="card help-card help-section-details" open>
            <summary>Как работать с отчётами</summary>
            <div class="help-section-content">
              <p>Страница “Отчёты” нужна для управленческой и клиентской работы: оплаты, посещения, пропуски, пробные и задачи.</p>
              <div class="help-question-grid">
                <span>Сформируйте отчёт за нужный месяц.</span>
                <span>Проверьте оплаты и сумму оплат.</span>
                <span>Проверьте пропуски и задачи по отработкам.</span>
                <span>Создайте задачи по оплатам или отработкам.</span>
                <span>Спросите агента по отчёту, если нужен вывод.</span>
                <span>Сверяйте точные действия с МойКласс.</span>
              </div>
            </div>
          </details>

          <details class="card help-card help-section-details" open>
            <summary>Как пользоваться чатом</summary>
            <div class="help-section-content">
              <p>Чат помогает с быстрыми решениями, но рабочие действия лучше фиксировать в задачах. Пишите конкретно: ученик, дата, филиал, что нужно сделать.</p>
              <div class="help-question-grid">
                <span>Подбери окно для отработки 30.06.</span>
                <span>Составь сообщение родителю по оплате.</span>
                <span>Проанализируй отчёт за июнь.</span>
                <span>Что проверить в МойКласс перед записью?</span>
                <span>Кто свободен на Мстиславца вечером?</span>
                <span>Как закрыть задачу по отработке?</span>
              </div>
            </div>
          </details>

          <article class="card help-card">
            <h3>Ближайшие доработки</h3>
            <div class="help-page-list compact">
              <div class="help-page-item"><span>✅</span><div><b>Понимание закрытых задач</b><p>Агент должен учитывать, что задача уже выполнена или закрыта, и не предлагать по ней повторные действия.</p></div></div>
              <div class="help-page-item"><span>📈</span><div><b>KPI сотрудников</b><p>Позже добавим показатели по сотрудникам: выполненные задачи, скорость реакции, закрытые оплаты, поставленные отработки и другие метрики.</p></div></div>
            </div>
          </article>
        </div>`;
}

function teacherHelpHtml() {
  return `<div class="section-head help-head"><div>
    <h2>Инструкция преподавателя</h2>
    <p>Быстрый маршрут, регламенты и ответы на типовые ситуации.</p>
  </div></div>
  <div class="help-guide">
    <details class="card help-card help-section-details" open>
      <summary>🧭 Быстрый маршрут</summary>
      <div class="help-section-content help-route">
        <div class="help-route-step"><span class="help-route-num">1</span><div class="help-route-body"><b>Занятия</b><small>Расписание на 7 дней. Откройте карточку нужного занятия.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">2</span><div class="help-route-body"><b>Подготовка</b><small>Notion → изучить → практика → прикрепить файл результата → отправить методисту.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">3</span><div class="help-route-body"><b>Занятие</b><small>Провести, сохранить работы учеников.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">4</span><div class="help-route-body"><b>Закрытие</b><small>Отчёт → МойКласс → работы → кабинет → «Закрыть занятие».</small></div></div>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>📅 До занятия</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Откройте карточку занятия, проверьте тему и кабинет.</li>
          <li>В разделе «Подготовка» откройте материал Notion.</li>
          <li>Изучите тему, видео/инструкцию, выполните практику.</li>
          <li>Прикрепите файл результата и нажмите «Отправить».</li>
          <li>Дождитесь подтверждения — статус появится в карточке.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>🌙 После занятия</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Опишите в поле «Что сделали за занятие» и нажмите «Сгенерировать отчёт».</li>
          <li>Проверьте текст и отправьте в родительский чат.</li>
          <li>Заполните МойКласс: посещаемость, тема следующего урока.</li>
          <li>Сохраните работы учеников на Яндекс Диск.</li>
          <li>Проверьте кабинет, технику и расходники.</li>
          <li>Нажмите «Закрыть занятие».</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>👤 Один ученик</summary>
      <div class="help-section-content">
        <p>Если через 5 минут пришёл только один ученик — офлайн-занятие длится 1 час. Напишите в рабочий чат: «На занятии 14:00 YC2 только Вася — занятие 1 час. Сообщите родителям».</p>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>🎓 Пробное занятие</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Проверьте тему заранее в МойКласс.</li>
          <li>Подготовьтесь по Notion-материалу как обычно.</li>
          <li>Если пробный не пришёл в течение 15 минут — сообщите в рабочий чат.</li>
          <li>Напишите отчёт с впечатлениями от первого урока.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>🗂️ МойКласс после занятия</summary>
      <div class="help-section-content">
        <p>Отметьте занятие проведённым, проставьте посещаемость. У отсутствующих укажите «имя — отработка». Поставьте тему следующего занятия. Если не закончили тему — напишите, где остановились.</p>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>📤 Подготовка и доработка</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Откройте карточку занятия → раздел «Подготовка».</li>
          <li>Выберите файл через кнопку и нажмите «Отправить результат».</li>
          <li>Статус изменится на «На проверке».</li>
          <li>Если методист отклонил — прочитайте комментарий в карточке и отправьте исправленный файл.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>📝 Отчёт родителям</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Откройте карточку прошедшего занятия → «Закрытие занятия».</li>
          <li>Опишите, что сделали за занятие, в поле ввода.</li>
          <li>Нажмите «Сгенерировать отчёт» — агент составит текст.</li>
          <li>Проверьте и скопируйте текст в родительский чат группы.</li>
        </ul>
      </div>
    </details>
  </div>`;
}

function internHelpHtml() {
  return `<div class="section-head help-head"><div>
    <h2>Инструкция стажёра</h2>
    <p>Маршрут стажировки по шагам. Каждый шаг открывается после выполнения предыдущего.</p>
  </div></div>
  <div class="help-guide">
    <details class="card help-card help-section-details" open>
      <summary>🧭 Быстрый маршрут</summary>
      <div class="help-section-content help-route">
        <div class="help-route-step"><span class="help-route-num">1</span><div class="help-route-body"><b>Наблюдение</b><small>Посетить 2 занятия и написать комментарий к каждому.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">2</span><div class="help-route-body"><b>Подготовительная работа</b><small>Изучить материал Notion, выполнить задание и загрузить файл.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">3</span><div class="help-route-body"><b>Проверка</b><small>Методист проверяет работу. Принята — открывается шаг 4. Отклонена — нужно исправить.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">4</span><div class="help-route-body"><b>Пробное занятие</b><small>Записаться на пробное со старшим преподавателем через приложение.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">5</span><div class="help-route-body"><b>Допуск</b><small>Написать ОС после пробного. Методист принимает финальное решение.</small></div></div>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>👁️ Наблюдение занятий</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Откройте вкладку «Стажировка» → шаг «Наблюдение».</li>
          <li>В списке занятий нажмите «Записаться» на подходящее.</li>
          <li>После посещения напишите комментарий — что заметили, чему научились.</li>
          <li>Наблюдение засчитывается только после сохранения комментария.</li>
          <li>Нужно 2 засчитанных наблюдения, чтобы открылся шаг 2.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>📤 Подготовительная работа</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Откройте материал через кнопку «Открыть материал» в карточке шага 2.</li>
          <li>Изучите тему, выполните задание и подготовьте файл-результат.</li>
          <li>Прикрепите файл через кнопку и нажмите «Отправить».</li>
          <li>Статус изменится на «На проверке» — ждите ответа методиста.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>🔄 Если работу отклонили</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Прочитайте комментарий методиста в разделе подготовки.</li>
          <li>Исправьте работу согласно замечаниям.</li>
          <li>Загрузите исправленный файл и снова нажмите «Отправить».</li>
          <li>Спросите в чате, если непонятно, что именно нужно исправить.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>🎓 Пробное занятие</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>После принятия работы в шаге 4 появится форма записи.</li>
          <li>Укажите дату, время и подтвердите запись.</li>
          <li>Подготовьтесь: изучите тему, которую будете вести, как обычное занятие.</li>
          <li>После проведения пробного — заполните обратную связь.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>📝 Обратная связь (ОС) после пробного</summary>
      <div class="help-section-content">
        <p>После пробного занятия заполните форму ОС: что получилось, что было сложно, какие вопросы остались. Методист читает ОС перед принятием финального решения.</p>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>✅ Допуск</summary>
      <div class="help-section-content">
        <p>После пробного методист принимает решение. При отказе — будет комментарий с объяснением. При допуске в приложении появится финальный статус «Допущен».</p>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>💬 Как пользоваться чатом</summary>
      <div class="help-section-content">
        <p>Задавайте конкретные вопросы по стажировке: что делать дальше, почему шаг заблокирован, как заполнить ОС. Агент знает ваш текущий шаг и статус.</p>
      </div>
    </details>
  </div>`;
}

function adminHelpHtml() {
  return `<div class="section-head help-head"><div>
    <h2>Инструкция методиста / администратора</h2>
    <p>Быстрый маршрут проверки: стажёры, работы преподавателей, контроль занятий.</p>
  </div></div>
  <div class="help-guide">
    <details class="card help-card help-section-details" open>
      <summary>🧭 Быстрый маршрут</summary>
      <div class="help-section-content help-route">
        <div class="help-route-step"><span class="help-route-num">1</span><div class="help-route-body"><b>Стажёры</b><small>Админ → Стажёры: проверить работы и пробные, принять решение по допуску.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">2</span><div class="help-route-body"><b>Работы</b><small>Админ → Проверка работ: принять или отклонить подготовку преподавателей.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">3</span><div class="help-route-body"><b>Занятия</b><small>Админ → Контроль занятий: незакрытые и проблемные занятия.</small></div></div>
        <div class="help-route-step"><span class="help-route-num">4</span><div class="help-route-body"><b>Сотрудники</b><small>Админ → Сотрудники: роли и привязка МойКласс.</small></div></div>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>🎓 Проверка работ стажёров</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Откройте Админ → Стажёры.</li>
          <li>Найдите стажёра со статусом «Ждёт проверки».</li>
          <li>Откройте карточку, скачайте файл работы и оцените его.</li>
          <li>Нажмите «Принять» или «Отклонить» — при отклонении комментарий обязателен.</li>
          <li>После принятия стажёру открывается запись на пробное занятие.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>✅ Допуск / недопуск стажёра</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>После пробного занятия стажёр заполняет ОС.</li>
          <li>Откройте карточку стажёра в разделе «Стажёры».</li>
          <li>Прочитайте ОС и нажмите «Допустить» или «Не допустить».</li>
          <li>При недопуске комментарий обязателен — стажёр его увидит в приложении.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>📤 Проверка работ преподавателей</summary>
      <div class="help-section-content">
        <ul class="help-checklist">
          <li>Откройте Админ → Проверка работ.</li>
          <li>Скачайте файл подготовки преподавателя.</li>
          <li>Нажмите «Принять» или «Отклонить» с комментарием.</li>
          <li>Преподаватель видит статус и комментарий в карточке занятия.</li>
        </ul>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>📋 Контроль занятий</summary>
      <div class="help-section-content">
        <p>В разделе «Контроль занятий» отображаются: незакрытые после проведения, занятия с проблемой в подготовке, непринятые работы. Используйте чат для быстрого обзора всех открытых задач.</p>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>👥 Сотрудники</summary>
      <div class="help-section-content">
        <p>В разделе «Сотрудники» видны все пользователи, их роли и привязки к MoyKlass. Изменить роль можно через карточку сотрудника.</p>
      </div>
    </details>
    <details class="card help-card help-section-details">
      <summary>💬 Как пользоваться чатом</summary>
      <div class="help-section-content">
        <p>Спросите: «Что требует проверки?», «Какие стажёры ждут решения?», «Какие занятия не закрыты?» — агент видит открытые задачи и даст список приоритетов.</p>
      </div>
    </details>
  </div>`;
}

function renderRoleHelp() {
  const root = $("tab-help");
  if (!root) return;
  const role = state.me?.role || "";
  if (role === "intern") {
    root.innerHTML = internHelpHtml();
  } else if (role === "teacher") {
    root.innerHTML = teacherHelpHtml();
  } else if (role === "owner" || role === "methodist" || role === "operations") {
    root.innerHTML = adminHelpHtml();
  } else if (role === "client_manager") {
    root.innerHTML = clientManagerHelpHtml();
  } else if (role === "parent") {
    root.innerHTML = parentHelpHtml();
  } else {
    if (!state.helpTeacherHtml) state.helpTeacherHtml = root.innerHTML;
    root.innerHTML = state.helpTeacherHtml;
  }
}

function renderChatChips() {
  const grid = $("chatQuickGrid");
  if (!grid) return;
  const role = state.me?.role || "";
  const chips = ["owner", "methodist", "operations"].includes(role)
    ? CHAT_CHIPS_BY_ROLE.admin
    : role === "intern"
      ? CHAT_CHIPS_BY_ROLE.intern
      : CHAT_CHIPS_BY_ROLE.teacher;
  grid.innerHTML = chips.map(([key, label]) =>
    `<button type="button" class="chat-chip" data-chat-prompt="${escapeHtml(key)}">${escapeHtml(label)}</button>`
  ).join("");
}

function setupRoleUi() {
  const role = state.me?.role || "";
  const testMode = state.me?.testMode || {};
  const label = state.me?.roleLabel || roleLabel(role);
  $("roleBadge").textContent = testMode.enabled ? `${label} · тест` : label;
  const titles = {
    owner: "Yellow Club OPS",
    methodist: "Контроль методиста",
    teacher: "Кабинет преподавателя",
    intern: "Кабинет стажера",
    client_manager: "Кабинет клиент-менеджера",
    operations: "Yellow Club OPS",
  };
  $("appTitle").textContent = titles[role] || "Кабинет сотрудника";

  document.querySelectorAll(".admin-only").forEach(el => el.classList.toggle("hidden", !canUseAdmin()));
  document.querySelectorAll(".role-lessons").forEach(el => el.classList.toggle("hidden", !canUseLessons()));
  document.querySelectorAll(".role-schedule").forEach(el => el.classList.toggle("hidden", !canUseSchedule()));
  document.querySelectorAll(".role-open-slots").forEach(el => el.classList.toggle("hidden", !canUseOpenSlots()));
  document.querySelectorAll(".role-reports").forEach(el => el.classList.toggle("hidden", !canUseReports()));
  document.querySelectorAll(".role-intern").forEach(el => el.classList.toggle("hidden", !canUseInternship()));
  const askTab = document.querySelector('.tab[data-tab="ask"]');
  if (askTab) askTab.classList.toggle("hidden", !canAskAgent());

  if (canUseInternship()) {
    const internOnlyTabs = ["intern", "help", "ask", "my-lunch"];
    document.querySelectorAll(".tab[data-tab]").forEach(t => {
      if (!internOnlyTabs.includes(t.dataset.tab)) t.classList.add("hidden");
    });
  }

  if (isMvpMode()) {
    const mvpRole = state.me?.role || "";
    const allowedMvpTabs = MVP_TABS_BY_ROLE[mvpRole] || [];
    document.querySelectorAll(".tab[data-tab]").forEach(t => {
      if (!allowedMvpTabs.includes(t.dataset.tab)) t.classList.add("hidden");
    });
    const stub = $("mvp-cm-stub");
    if (stub) stub.classList.add("hidden");
  }

  const allowedAdminTabs = availableAdminTabs();
  document.querySelectorAll("[data-admin-tab]").forEach(el => {
    el.classList.toggle("hidden", !allowedAdminTabs.includes(el.dataset.adminTab));
  });
  if (allowedAdminTabs.length && !allowedAdminTabs.includes(state.adminTab)) {
    state.adminTab = allowedAdminTabs[0];
  }
  document.querySelectorAll(".subtab").forEach(el => el.classList.toggle("active", el.dataset.adminTab === state.adminTab));

  // Parent role: override all tab visibility and show only parent tabs
  if (role === "parent") {
    const parentAllowed = ["my-children", "food", "help"];
    document.querySelectorAll(".tab[data-tab]").forEach(t => {
      t.classList.toggle("hidden", !parentAllowed.includes(t.dataset.tab));
    });
    document.querySelectorAll(".staff-lunch-tab").forEach(el => el.classList.add("hidden"));
    $("appTitle").textContent = "Питание · Yellow Club";
    $("roleBadge").textContent = "Родитель";
  }

  // Kitchen role: show kitchen + kitchen-editor tabs
  if (role === "kitchen") {
    document.querySelectorAll(".tab[data-tab]").forEach(t => {
      t.classList.toggle("hidden", t.dataset.tab !== "kitchen" && t.dataset.tab !== "kitchen-editor");
    });
    document.querySelectorAll(".staff-lunch-tab").forEach(el => el.classList.add("hidden"));
    document.querySelectorAll(".kitchen-only").forEach(el => el.classList.remove("hidden"));
    document.querySelectorAll(".kitchen-editor-only").forEach(el => el.classList.remove("hidden"));
    $("appTitle").textContent = "Кухня · Yellow Club";
    $("roleBadge").textContent = "Кухня";
  }

  // Restaurant role: backward-compatible alias — shows kitchen screen
  if (role === "restaurant") {
    document.querySelectorAll(".tab[data-tab]").forEach(t => {
      t.classList.toggle("hidden", t.dataset.tab !== "kitchen" && t.dataset.tab !== "kitchen-editor");
    });
    document.querySelectorAll(".staff-lunch-tab").forEach(el => el.classList.add("hidden"));
    document.querySelectorAll(".kitchen-only").forEach(el => el.classList.remove("hidden"));
    document.querySelectorAll(".kitchen-editor-only").forEach(el => el.classList.remove("hidden"));
    $("appTitle").textContent = "Кухня · Yellow Club";
    $("roleBadge").textContent = "Кухня";
  }

  // Staff lunch tab: show for ALL staff (not parent/kitchen/restaurant), runs LAST to override intern/MVP hiding
  if (role && !["parent", "kitchen", "restaurant"].includes(role)) {
    document.querySelectorAll(".staff-lunch-tab").forEach(el => el.classList.remove("hidden"));
  } else {
    document.querySelectorAll(".staff-lunch-tab").forEach(el => el.classList.add("hidden"));
  }

  renderRoleHelp();
  renderChatChips();
  renderTestRolePanel();
  ensureVisibleActiveTab();
}

function ensureVisibleActiveTab() {
  const active = document.querySelector(".tab.active");
  if (active && !active.classList.contains("hidden")) return;
  const first = Array.from(document.querySelectorAll(".tab")).find(x => !x.classList.contains("hidden"));
  if (first) activateTab(first.dataset.tab);
}

function activateTab(name) {
  const askInput = $("askInput");
  if (name !== "ask") {
    askInput?.blur?.();
    setChatInputFocused(false);
  }

  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(x => x.classList.remove("active"));
  const tab = document.querySelector(`.tab[data-tab="${cssEscapeValue(name)}"]`);
  const panel = $(`tab-${name}`);
  if (!tab || !panel) return;
  // Allow programmatic navigation (e.g., from admin "📊 Отчёты" button) even if tab button is
  // still hidden — as long as the panel itself is accessible. Role checks are inside render fns.
  if (tab.classList.contains("hidden") && panel.classList.contains("hidden")) return;
  tab.classList.add("active");
  panel.classList.add("active");

  if (name === "ask" && document.activeElement !== askInput) {
    setChatInputFocused(false);
  }
  if (name === "intern") loadInternTrack();
  if (name === "admin") loadAdmin();
  if (name === "schedule") loadWorkSchedule();
  if (name === "windows") loadOpenSlots();
  if (name === "reports") { loadReports(); loadKpi(); renderChildrenReport(); renderBepaid(); loadBepaidStatus(); }
  if (name === "ask") renderAskMessages();
  if (name === "my-children") { if (state.myChildren === null) loadMyChildren(); else renderMyChildren(); }
  if (name === "food") { if (state.activeMenus === null) loadActiveMenus(); else renderParentFoodMenu(); }
  if (name === "my-lunch") { renderStaffFoodLunch($("myLunchContent")); }
  if (name === "kitchen-editor") {
    const root = $("kitchenEditorContent");
    if (root && !state.kitchenEditorData) loadKitchenEditor(root);
  }
}

function renderTestRolePanel() {
  const panel = $("testRolePanel");
  if (!panel || !state.me?.capabilities?.canUseTestRoles) {
    if (panel) panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const roleSelect = $("testRoleSelect");
  const teacherSelect = $("testTeacherSelect");
  const teacherLabel = $("testTeacherLabel");
  const options = state.me.roleOptions || [];
  const teachers = state.me.testTeachers || [];
  const testMode = state.me.testMode || {};
  const currentRole = testMode.enabled ? (testMode.role || state.me.role || "owner") : (state.me.realRole || state.me.role || "owner");

  roleSelect.innerHTML = options.map(o => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join("");
  roleSelect.value = currentRole;

  teacherSelect.innerHTML = teachers.length
    ? teachers.map(t => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.name || t.id)} · ${escapeHtml(t.id)}</option>`).join("")
    : `<option value="">Нет привязанных teacherId</option>`;
  teacherSelect.value = testMode.mk_teacher_id || state.me.mkTeacherId || (teachers[0]?.id || "");

  const updateTeacherVisibility = () => {
    const selected = options.find(o => o.value === roleSelect.value);
    teacherLabel.classList.toggle("hidden", !selected?.needsTeacher);
  };
  roleSelect.onchange = updateTeacherVisibility;
  updateTeacherVisibility();

  const status = testMode.enabled
    ? `Сейчас включён тест: ${roleLabel(testMode.role)}. Реальная роль: ${state.me.realRoleLabel || roleLabel(state.me.realRole)}.`
    : `Сейчас реальная роль: ${state.me.realRoleLabel || roleLabel(state.me.realRole || state.me.role)}.`;
  $("testRoleStatus").textContent = status;
}

function lessonCardClass(item) {
  if (!item) return "";
  if (item.lessonStatus === "closed") return "lesson-card-closed";
  if (isLessonPast(item)) return "lesson-card-overdue";
  return "";
}

function lessonCardNote(item) {
  if (!item) return "";
  if (item.lessonStatus === "closed") return `<div class="lesson-card-note ok">✅ Занятие закрыто и завершено</div>`;
  if (isLessonPast(item)) return `<div class="lesson-card-note bad">⚠️ Занятие прошло, но ещё не закрыто</div>`;
  return "";
}

function _teacherLessonStatus(lesson) {
  if (!lesson) return {label: "—", cls: ""};
  const closed = lesson.lessonStatus === "closed";
  const past = isLessonPast(lesson);
  const prepStatus = String(lesson.prepResultStatus || "");
  if (closed) return {label: "Закрыто", cls: "ok"};
  if (past) return {label: "Нужно закрыть", cls: "bad"};
  if (prepStatus === "rejected") return {label: "На доработке", cls: "bad"};
  if (prepStatus === "submitted") return {label: "На проверке", cls: "info"};
  if (lesson.preparationStatus === "ready") return {label: "Готово к занятию", cls: "ok"};
  const topic = String(lesson.topic || "").trim().toLowerCase();
  if (!topic || topic === "тема не указана" || topic === "тема") return {label: "Нет темы", cls: "warn"};
  return {label: "Нужно подготовиться", cls: "warn"};
}

function renderLessons() {
  const root = $("lessonsList");
  if (!state.lessons.length) {
    const role = state.me?.role || "";
    const hasMkId = !!(state.me?.mkTeacherId);
    let emptyMsg = "На ближайшую неделю занятий не найдено.";
    if (role === "teacher" && hasMkId) {
      emptyMsg = "Профиль преподавателя связан с МойКласс, но занятия на ближайшие дни не найдены. " +
                 "Если занятия должны быть, попросите администратора обновить расписание.";
    } else if (role === "teacher" && !hasMkId) {
      emptyMsg = "Профиль преподавателя не связан с МойКласс. Обратитесь к администратору для привязки teacherId.";
    }
    root.innerHTML = `<div class="empty">${escapeHtml(emptyMsg)}</div>`;
    return;
  }
  root.innerHTML = state.lessons.map(item => {
    const st = _teacherLessonStatus(item);
    const topic = String(item.topic || "").trim();
    const topicLow = topic.toLowerCase();
    const showTopic = topic && topicLow !== "тема не указана" && topicLow !== "тема";
    const past = isLessonPast(item);
    const closed = item.lessonStatus === "closed";
    const accentCls = closed ? " lc-closed" : past ? " lc-overdue" : "";
    return `<article class="lc-card${accentCls}" data-lesson-id="${escapeHtml(item.id)}">
      <div class="lc-header">
        <span class="lc-datetime">${escapeHtml(item.date || "")} · ${escapeHtml(item.time || "")}</span>
        <span class="yc-badge yc-badge-${st.cls}">${escapeHtml(st.label)}</span>
      </div>
      <div class="lc-group">${escapeHtml(item.group || "Занятие")}</div>
      ${showTopic ? `<div class="lc-topic">${escapeHtml(topic)}</div>` : ""}
      ${item.room ? `<div class="lc-room">${escapeHtml(item.room)}</div>` : ""}
      <button class="lc-btn" data-id="${escapeHtml(item.id)}">Открыть →</button>
    </article>`;
  }).join("");
  document.querySelectorAll(".lc-btn").forEach(btn => btn.addEventListener("click", () => openLesson(btn.dataset.id)));
}

function renderLessonsUnavailable() {
  const root = $("lessonsList");
  if (!root) return;
  const role = state.me?.role || "";
  const msg = role === "client_manager"
    ? "Для клиент-менеджера пока доступны задачи и инструкция. Клиентские сценарии добавим отдельным экраном."
    : "Для выбранной роли экран занятий недоступен.";
  root.innerHTML = `<div class="empty">${escapeHtml(msg)}</div>`;
}

function lessonStartDate(lesson) {
  const d = String(lesson?.date || "").slice(0, 10);
  const time = String(lesson?.time || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null;
  let start = "00:00";
  const m = time.match(/(\d{1,2}:\d{2})/);
  if (m) start = m[1];
  const dt = new Date(`${d}T${start}:00`);
  return isNaN(dt.getTime()) ? null : dt;
}

function dayStart(dateObj) {
  const d = new Date(dateObj || Date.now());
  d.setHours(0, 0, 0, 0);
  return d;
}

function diffDaysFromToday(dateObj) {
  if (!dateObj) return 999;
  const today = dayStart(new Date());
  const target = dayStart(dateObj);
  return Math.round((target.getTime() - today.getTime()) / 86400000);
}

function taskLessonLine(lesson) {
  const parts = [];
  if (lesson?.date) parts.push(lesson.date);
  if (lesson?.time) parts.push(lesson.time);
  const topic = String(lesson?.topic || "").trim();
  if (topic) parts.push(topic);
  return parts.join(" · ");
}

function prepMissingParts(lesson) {
  const missing = [];
  if (lesson.prepMaterialStatus !== "done") missing.push("изучить Notion");
  if (lesson.prepVideoStatus !== "done") missing.push("посмотреть видео/инструкцию");
  if (lesson.prepPracticeStatus !== "done") missing.push("выполнить практику");
  if (lesson.prepResultStatus === "rejected") missing.push("исправить файл подготовки");
  else if (!["done", "approved", "submitted"].includes(String(lesson.prepResultStatus || ""))) missing.push("прикрепить файл результата");
  return missing;
}

function taskCard({ section, level = "normal", title, subtitle, text, chips = [], lessonId = "", source = "auto", actionLabel = "Открыть занятие", kind = "", groupItems = [], groupId = "" }) {
  const chipHtml = chips.filter(Boolean).map(c => `<span>${escapeHtml(c)}</span>`).join("");
  const safeGroupId = groupId ? escapeHtml(groupId) : "";
  const isGroupedList = kind === "missing_topic_group" && Array.isArray(groupItems) && groupItems.length;
  const action = isGroupedList
    ? `<button class="primary expand-task-group" data-group="${safeGroupId}">Показать занятия</button>`
    : (lessonId ? `<button class="primary open-task-lesson" data-id="${escapeHtml(lessonId)}">${escapeHtml(actionLabel || "Открыть занятие")}</button>` : "");
  const groupedHtml = isGroupedList ? `<div class="task-group-list hidden" data-group-list="${safeGroupId}">
    ${groupItems.map((item, index) => `<div class="task-group-item">
      <div class="task-group-index">${index + 1}</div>
      <div class="task-group-info">
        <b>${escapeHtml(item.title || "Занятие")}</b>
        <span>${escapeHtml(item.meta || "")}</span>
      </div>
      <button class="secondary open-group-lesson" data-id="${escapeHtml(item.lessonId || "")}">Открыть</button>
    </div>`).join("")}
  </div>` : "";
  return `<article class="task-card ${escapeHtml(level)}" data-task-source="${escapeHtml(source)}">
    <div class="task-main">
      <div class="task-kicker">${escapeHtml(section || "Задача")}</div>
      <div class="task-title">${escapeHtml(title || "Задача")}</div>
      ${subtitle ? `<div class="task-subtitle">${escapeHtml(subtitle)}</div>` : ""}
      ${text ? `<div class="task-text">${nl2br(text)}</div>` : ""}
      ${chipHtml ? `<div class="task-chips">${chipHtml}</div>` : ""}
      ${groupedHtml}
    </div>
    ${action ? `<div class="task-actions">${action}</div>` : ""}
  </article>`;
}

function buildLessonTasks() {
  const tasks = [];
  const lessons = Array.isArray(state.lessons) ? state.lessons : [];

  for (const lesson of lessons) {
    if (!lesson?.id) continue;
    const start = lessonStartDate(lesson);
    const days = diffDaysFromToday(start);
    const past = isLessonPast(lesson);
    const closed = lesson.lessonStatus === "closed";
    const topic = String(lesson.topic || "").trim();
    const line = taskLessonLine(lesson);
    const prepMissing = prepMissingParts(lesson);
    const missingClose = closeMissing(lesson);

    if (!closed && past) {
      tasks.push({
        section: "Срочно",
        level: "urgent",
        sort: 10 + Math.max(days, -30),
        title: "Занятие прошло, но не закрыто",
        subtitle: lesson.group || "Занятие",
        text: missingClose.length ? `Осталось отметить: ${missingClose.join(", ")}.` : "Все пункты отмечены. Нажмите “Закрыть занятие” в карточке.",
        chips: [line, lesson.room ? `Кабинет: ${lesson.room}` : ""],
        lessonId: lesson.id,
      });
      continue;
    }

    if (lesson.prepResultStatus === "rejected") {
      tasks.push({
        section: "Срочно",
        level: "danger",
        sort: 20 + Math.max(days, -30),
        title: "Работа отправлена на доработку",
        subtitle: lesson.group || "Подготовка к занятию",
        text: lesson.preparationComment || "Старший преподаватель отклонил работу. Откройте карточку, посмотрите комментарий и отправьте новый файл.",
        chips: [line, "нужно исправить"],
        lessonId: lesson.id,
      });
      continue;
    }

    if (!topic || topic.toLowerCase() === "тема не указана") {
      tasks.push({
        section: days <= 0 ? "Срочно" : days <= 1 ? "Сегодня" : "Скоро",
        level: "warning",
        sort: 30 + days,
        kind: "missing_topic",
        title: "В МойКласс не указана тема",
        subtitle: lesson.group || "Занятие",
        text: "Без темы агент не сможет точно подобрать материал Notion. Нужно уточнить тему до подготовки.",
        chips: [line || `${lesson.date || ""} ${lesson.time || ""}`.trim(), "тема не указана"],
        compactChip: `${lesson.group || "Занятие"} · ${lesson.date || ""} ${lesson.time || ""}`.trim(),
        lessonId: lesson.id,
      });
      continue;
    }

    if (lesson.prepResultStatus === "submitted") {
      tasks.push({
        section: "В ожидании",
        level: "wait",
        sort: 200 + days,
        title: "Работа ждёт проверки",
        subtitle: lesson.group || "Подготовка отправлена",
        text: "Файл результата отправлен старшему преподавателю. После проверки статус появится в карточке занятия.",
        chips: [line, "ожидает методиста"],
        lessonId: lesson.id,
      });
      continue;
    }

    if (!past && lesson.preparationStatus !== "ready" && prepMissing.length) {
      const section = days <= 0 ? "Сегодня" : days <= 2 ? "Скоро" : "На неделе";
      tasks.push({
        section,
        level: days <= 0 ? "warning" : "normal",
        sort: 100 + days,
        title: days <= 0 ? "Подготовиться к занятию сегодня" : "Подготовиться к занятию",
        subtitle: lesson.group || "Занятие",
        text: `Осталось: ${prepMissing.join(", ")}.`,
        chips: [line, lesson.room ? `Кабинет: ${lesson.room}` : ""],
        lessonId: lesson.id,
      });
    }
  }

  return tasks.sort((a, b) => (a.sort || 999) - (b.sort || 999));
}

function buildSystemTasks() {
  return (state.tasks || []).map(t => ({
    section: "Системные",
    level: t.priority === "high" ? "warning" : "normal",
    sort: 300,
    title: t.title || "Задача",
    subtitle: t.due_at ? `Дедлайн: ${t.due_at}` : "",
    text: t.text || "",
    chips: [t.task_type ? `Тип: ${t.task_type}` : "", t.priority ? `Приоритет: ${t.priority}` : ""],
    lessonId: t.lesson_id || "",
    source: "system",
  }));
}

function compactRepeatedLessonTasks(tasks) {
  const result = [];
  const missingTopicBySection = {};

  for (const task of tasks) {
    if (task.kind === "missing_topic") {
      const key = task.section || "Скоро";
      if (!missingTopicBySection[key]) missingTopicBySection[key] = [];
      missingTopicBySection[key].push(task);
      continue;
    }
    result.push(task);
  }

  for (const [section, items] of Object.entries(missingTopicBySection)) {
    items.sort((a, b) => (a.sort || 999) - (b.sort || 999));
    if (items.length <= 2) {
      result.push(...items);
      continue;
    }
    const first = items[0];
    const previewChips = items.slice(0, 3).map(t => t.compactChip || t.subtitle || "Занятие");
    result.push({
      section,
      level: "warning",
      sort: Math.min(...items.map(t => t.sort || 999)),
      kind: "missing_topic_group",
      groupId: `missing-topic-${section}`.replace(/[^a-zA-Zа-яА-Я0-9_-]+/g, "-"),
      title: "В МойКласс не указана тема",
      subtitle: `${items.length} занятий требуют уточнения темы`,
      text: "Проверьте темы в МойКласс до подготовки. Кнопка покажет список занятий, а не откроет карточку сразу.",
      chips: [...previewChips, items.length > 3 ? `ещё ${items.length - 3}` : ""],
      groupItems: items.map(t => ({
        lessonId: t.lessonId || "",
        title: t.subtitle || "Занятие",
        meta: (t.chips || []).filter(Boolean).join(" · "),
      })),
    });
  }

  return result.sort((a, b) => (a.sort || 999) - (b.sort || 999));
}


function groupTasks(tasks) {
  const order = ["Срочно", "Сегодня", "Скоро", "На неделе", "В ожидании", "Системные"];
  const grouped = {};
  for (const task of tasks) {
    const key = task.section || "Системные";
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(task);
  }
  return order.filter(k => grouped[k]?.length).map(k => [k, grouped[k]]);
}

function renderTeacherTasks() {
  const root = $("tasksList");
  if (!root) return;
  const autoTasks = compactRepeatedLessonTasks(buildLessonTasks());
  const systemTasks = buildSystemTasks();
  const allTasks = [...autoTasks, ...systemTasks];

  const summary = $("tasksSummary");
  if (summary) {
    const urgent = allTasks.filter(t => ["urgent", "danger", "warning"].includes(t.level)).length;
    const waiting = allTasks.filter(t => t.level === "wait").length;
    summary.innerHTML = `
      <div class="task-summary-card"><b>${allTasks.length}</b><span>активных задач</span></div>
      <div class="task-summary-card"><b>${urgent}</b><span>срочно</span></div>
      <div class="task-summary-card"><b>${waiting}</b><span>на проверке</span></div>`;
  }

  if (!allTasks.length) {
    root.innerHTML = `<div class="empty task-empty"><b>Открытых задач нет.</b><span>Когда появится подготовка, доработка или незакрытое занятие, задача появится здесь автоматически.</span></div>`;
    return;
  }

  root.innerHTML = groupTasks(allTasks).map(([section, items]) => `
    <section class="task-section">
      <div class="task-section-title">${escapeHtml(section)}</div>
      <div class="task-section-list">${items.map(taskCard).join("")}</div>
    </section>
  `).join("");
  document.querySelectorAll(".open-task-lesson").forEach(btn => btn.addEventListener("click", () => openLesson(btn.dataset.id)));
  document.querySelectorAll(".open-group-lesson").forEach(btn => btn.addEventListener("click", () => {
    const id = btn.dataset.id || "";
    if (id) openLesson(id);
  }));
  document.querySelectorAll(".expand-task-group").forEach(btn => btn.addEventListener("click", () => {
    const groupId = btn.dataset.group || "";
    const list = Array.from(document.querySelectorAll("[data-group-list]")).find(el => el.dataset.groupList === groupId);
    if (!list) return;
    const isHidden = list.classList.toggle("hidden");
    btn.textContent = isHidden ? "Показать занятия" : "Скрыть занятия";
  }));
}


const CLIENT_TASK_TYPE_LABELS = {
  makeup: "Отработка",
  trial: "Пробное",
  payment: "Оплата",
};
const CLIENT_TASK_STATUS_LABELS = {
  new: "Новая",
  in_work: "В работе",
  waiting_client: "Ждём клиента",
  done: "Выполнена",
  cancelled: "Отменена",
};
const CLIENT_TASK_CLOSED_STATUSES = new Set(["done", "cancelled"]);
const CLIENT_TASK_PRIORITY_LABELS = {
  normal: "Обычная",
  high: "Важная",
  urgent: "Срочная",
};
function isClientManagerRole() { return (state.me?.role || "") === "client_manager"; }
function clientTaskTypeLabel(type) { return CLIENT_TASK_TYPE_LABELS[type] || type || "Задача"; }
function clientTaskStatusLabel(status) {
  const clean = String(status || "new").trim();
  if (clean === "waiting_confirm") return "Ждёт подтверждения";
  return CLIENT_TASK_STATUS_LABELS[clean] || clean || "Новая";
}
function isClientTaskClosed(taskOrStatus) {
  const status = typeof taskOrStatus === "string" ? taskOrStatus : String(taskOrStatus?.status || "new");
  return CLIENT_TASK_CLOSED_STATUSES.has(status);
}
function clientTaskPriorityLabel(priority) { return CLIENT_TASK_PRIORITY_LABELS[priority] || priority || "Обычная"; }
function clientTaskPerson(task) {
  const child = String(task.child_name || "").trim();
  const client = String(task.client_name || "").trim();
  if (child && client) return `${child} / ${client}`;
  return child || client || "Клиент не указан";
}
function clientTaskDateLine(task) {
  const parts = [];
  if (task.desired_date) parts.push(`Дата: ${task.desired_date}`);
  if (task.desired_time) parts.push(`Время: ${task.desired_time}`);
  if (task.deadline && task.task_type === "payment") parts.push(`Дедлайн: ${task.deadline}`);
  if (task.location) parts.push(task.location);
  return parts.join(" · ") || "Условия не указаны";
}
function clientTaskNextStep(task) {
  const type = String(task?.task_type || "");
  const status = String(task?.status || "new");
  if (status === "done") return "Задача завершена. Она скрывается из активного списка и остаётся в фильтре «Завершённые» или «Все».";
  if (status === "cancelled") return "Задача отменена. Она не требует действий, но остаётся в истории.";
  if (status === "new") {
    if (type === "payment") return "Следующий шаг: подготовить сообщение по оплате и перевести задачу в «Ждём клиента».";
    return "Следующий шаг: подобрать окно, выбрать подходящий вариант и подготовить сообщение родителю.";
  }
  if (status === "in_work") {
    if (type === "payment") return "Следующий шаг: отправить сообщение клиенту по оплате и поставить статус «Ждём клиента».";
    return "Следующий шаг: согласовать предложенное окно с родителем и поставить статус «Ждём клиента».";
  }
  if (status === "waiting_client") {
    if (type === "payment") return "Следующий шаг: если клиент оплатил, отметьте «Оплачено». Если отказался или задача неактуальна - отмените.";
    if (type === "trial") return "Следующий шаг: если время согласовано и занятие поставлено, отметьте «Пробное записано». Если клиент отказался - отмените.";
    return "Следующий шаг: если отработка поставлена в МойКласс, отметьте «Отработка поставлена». Если клиент отказался - отмените.";
  }
  return "Следующий шаг: проверьте данные задачи и выберите действие ниже.";
}
function clientTaskDoneButtonLabel(task) {
  const type = String(task?.task_type || "");
  if (type === "payment") return "Оплачено";
  if (type === "trial") return "Пробное записано";
  if (type === "makeup") return "Отработка поставлена";
  return "Выполнено";
}
function clientTaskCountByStatus(statuses) {
  const set = new Set(statuses);
  return (state.clientTasks || []).filter(t => set.has(String(t.status || "new"))).length;
}
function renderClientTaskTypeFilters() {
  const types = [
    ["all", "Все"],
    ["makeup", "Отработки"],
    ["trial", "Пробные"],
    ["payment", "Оплаты"],
  ];
  return `<div class="cm-task-filters">${types.map(([value, label]) => `<button type="button" class="cm-task-filter ${state.clientTaskTypeFilter === value ? "active" : ""}" data-cm-task-filter="${value}">${label}</button>`).join("")}</div>`;
}
function renderClientTaskStatusFilters() {
  const statuses = [
    ["active", "Активные"],
    ["new", "Новые"],
    ["in_work", "В работе"],
    ["waiting_client", "Ждут клиента"],
    ["done", "Завершённые"],
    ["all", "Все"],
  ];
  return `<div class="cm-task-filters compact">${statuses.map(([value, label]) => `<button type="button" class="cm-task-filter ${state.clientTaskStatusFilter === value ? "active" : ""}" data-cm-task-status-filter="${value}">${label}</button>`).join("")}</div>`;
}
function clientTaskFormHtml() {
  const editing = (state.clientTasks || []).find(t => String(t.id || "") === String(state.clientTaskEditingId || "")) || {};
  const type = editing.task_type || "makeup";
  const isPayment = type === "payment";
  return `<article class="card cm-task-form-card ${state.clientTaskFormOpen ? "" : "hidden"}">
    <div class="cm-task-form-head">
      <div>
        <h3>${editing.id ? "Изменить задачу" : "Новая задача"}</h3>
        <p>Создайте рабочую задачу по клиенту. Агент дальше поможет подобрать окно или составить сообщение.</p>
      </div>
      <button type="button" class="icon-button" id="closeClientTaskForm">×</button>
    </div>
    <form id="clientTaskForm" class="cm-task-form">
      <input type="hidden" id="cmTaskId" value="${escapeAttr(editing.id || "")}" />
      <label>
        <span>Тип задачи</span>
        <select id="cmTaskType">
          <option value="makeup" ${type === "makeup" ? "selected" : ""}>Отработка</option>
          <option value="trial" ${type === "trial" ? "selected" : ""}>Пробное</option>
          <option value="payment" ${type === "payment" ? "selected" : ""}>Оплата</option>
        </select>
      </label>
      <label>
        <span>Приоритет</span>
        <select id="cmTaskPriority">
          <option value="normal" ${(editing.priority || "normal") === "normal" ? "selected" : ""}>Обычная</option>
          <option value="high" ${editing.priority === "high" ? "selected" : ""}>Важная</option>
          <option value="urgent" ${editing.priority === "urgent" ? "selected" : ""}>Срочная</option>
        </select>
      </label>
      <label>
        <span>Клиент / родитель</span>
        <input id="cmClientName" type="text" value="${escapeAttr(editing.client_name || "")}" placeholder="Например: Мария, мама Артёма" />
      </label>
      <label>
        <span>Ученик</span>
        <input id="cmChildName" type="text" value="${escapeAttr(editing.child_name || "")}" placeholder="Имя ребёнка" />
      </label>
      <label>
        <span>Контакт</span>
        <input id="cmContact" type="text" value="${escapeAttr(editing.contact || "")}" placeholder="Телефон или Telegram" />
      </label>
      <label class="cm-date-field">
        <span>${isPayment ? "Дедлайн оплаты" : "Желаемая дата"}</span>
        <input id="cmDesiredDate" type="date" value="${escapeAttr((isPayment ? editing.deadline : editing.desired_date) || "")}" />
      </label>
      <label class="cm-slot-field ${isPayment ? "hidden" : ""}">
        <span>Желаемое время</span>
        <input id="cmDesiredTime" type="text" value="${escapeAttr(editing.desired_time || "")}" placeholder="Например: после 16:00, вечер, 12:00-15:00" />
      </label>
      <label class="cm-slot-field ${isPayment ? "hidden" : ""}">
        <span>Филиал / формат</span>
        <select id="cmLocation">
          ${["Любой формат", "Кульман 1/1", "Мстиславца 6", "Онлайн"].map(loc => `<option value="${escapeAttr(loc)}" ${(editing.location || "Любой формат") === loc ? "selected" : ""}>${escapeHtml(loc)}</option>`).join("")}
        </select>
      </label>
      <label class="cm-payment-field ${isPayment ? "" : "hidden"}">
        <span>Сумма</span>
        <input id="cmAmount" type="text" value="${escapeAttr(editing.amount || "")}" placeholder="Например: 239 BYN" />
      </label>
      <label class="cm-payment-field ${isPayment ? "" : "hidden"}">
        <span>За что оплата</span>
        <input id="cmPaymentFor" type="text" value="${escapeAttr(editing.payment_for || "")}" placeholder="Например: 4 занятия" />
      </label>
      <label class="cm-task-comment-field">
        <span>Комментарий</span>
        <textarea id="cmComment" rows="3" placeholder="Что важно учесть по клиенту, времени или ситуации">${escapeHtml(editing.comment || "")}</textarea>
      </label>
      <div class="schedule-form-actions">
        <button class="primary" type="submit">Сохранить задачу</button>
        <button class="secondary" type="button" id="resetClientTaskForm">Очистить</button>
      </div>
    </form>
  </article>`;
}
function clientTaskActionPrompt(task, mode) {
  const person = clientTaskPerson(task);
  const typeLabel = clientTaskTypeLabel(task.task_type).toLowerCase();
  if (mode === "payment") {
    return `Помоги закрыть задачу по оплате.

Клиент/ученик: ${person}
Сумма: ${task.amount || "не указана"}
За что: ${task.payment_for || "не указано"}
Дедлайн: ${task.deadline || task.desired_date || "не указан"}
Комментарий: ${task.comment || "нет"}

Составь короткое и аккуратное сообщение клиенту без давления. Также дай чек-лист, что отметить в задаче после ответа клиента.`;
  }
  const deadlineMode = String(task.desired_time || "").toLowerCase().includes("до следующ");
  const dateLabel = deadlineMode ? "Срок/желательно до" : "Желаемая дата";
  return `Нужно подобрать свободное окно для задачи: ${typeLabel}.

Клиент/ученик: ${person}
${dateLabel}: ${task.desired_date || "не указано"}
Желаемое время: ${task.desired_time || "не указано"}
Филиал/формат: ${task.location || "любой формат"}
Комментарий: ${task.comment || "нет"}

Подбери подходящие будущие окна из данных преподавателей. Это операционная задача клиент-менеджера, не месячный отчёт МойКласс. Если срок уже прошёл, покажи ближайшие будущие варианты и отдельно отметь, что срок просрочен. Если точного окна нет, предложи ближайшие варианты и напиши, что проверить в МойКласс перед записью.`;
}

function clientTaskRawId(task) {
  return String(task?.id || "");
}

function minutesToTimeLabel(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  const h = Math.floor(n / 60);
  const m = n % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function localIsoDate(dateObj) {
  const d = new Date(dateObj || Date.now());
  if (isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function parseIsoDateOnly(value) {
  const raw = String(value || "").trim();
  const m = raw.match(/^(20\d{2})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const d = new Date(`${m[1]}-${m[2]}-${m[3]}T00:00:00`);
  return isNaN(d.getTime()) ? null : d;
}

function formatShortRuDate(value) {
  const d = parseIsoDateOnly(value);
  if (!d) return String(value || "");
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function clientTaskDesiredDateInfo(task) {
  const raw = String(task?.desired_date || task?.deadline || "").slice(0, 10);
  const d = parseIsoDateOnly(raw);
  const deadlineMode = String(task?.desired_time || "").toLowerCase().replace("ё", "е").includes("до следующ");
  if (!d) return { raw: "", date: null, past: false, deadlineMode, today: localIsoDate(new Date()) };
  const todayStart = dayStart(new Date());
  return { raw, date: d, past: dayStart(d).getTime() < todayStart.getTime(), deadlineMode, today: localIsoDate(todayStart) };
}

function clientTaskLocationFilter(task) {
  const raw = String(task?.location || "").toLowerCase().replace("ё", "е");
  if (raw.includes("кульман")) return "Кульман 1/1";
  if (raw.includes("мстислав")) return "Мстиславца 6";
  if (raw.includes("онлайн") || raw.includes("online")) return "Онлайн";
  return "all";
}

function slotLocationMatchesTask(slot, task) {
  const filter = clientTaskLocationFilter(task);
  if (filter === "all") return true;
  const loc = String(slot?.location || "Любой формат").toLowerCase();
  return loc === "любой формат".toLowerCase() || loc === filter.toLowerCase();
}

function clientTaskTimeFilters(task) {
  const raw = String(task?.desired_time || "").toLowerCase().replace("ё", "е");
  if (!raw || raw.includes("до следующ")) return { after: null, before: null, part: "all" };
  const result = { after: null, before: null, part: "all" };
  let m = raw.match(/после\s+(\d{1,2})(?::(\d{2}))?/);
  if (m) result.after = Number(m[1]) * 60 + Number(m[2] || 0);
  m = raw.match(/до\s+(\d{1,2})(?::(\d{2}))?/);
  if (m) result.before = Number(m[1]) * 60 + Number(m[2] || 0);
  m = raw.match(/(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})/);
  if (m) {
    result.after = workTimeToMinutes(m[1]);
    result.before = workTimeToMinutes(m[2]);
  }
  if (raw.includes("вечер")) result.part = "evening";
  else if (raw.includes("утро")) result.part = "morning";
  else if (raw.includes("день")) result.part = "day";
  return result;
}

function slotTimeMatchesTask(slot, task) {
  const filters = clientTaskTimeFilters(task);
  const start = workTimeToMinutes(slot.start_time || slot.startTime);
  const end = workTimeToMinutes(slot.end_time || slot.endTime);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return true;
  if (filters.after !== null && end <= filters.after) return false;
  if (filters.before !== null && start >= filters.before) return false;
  if (filters.part === "morning" && start >= 12 * 60) return false;
  if (filters.part === "day" && !(start < 17 * 60 && end > 12 * 60)) return false;
  if (filters.part === "evening" && end <= 17 * 60) return false;
  return true;
}

function slotTaskSortKey(slot) {
  return `${slot.date || "9999-99-99"} ${slot.start_time || slot.startTime || "99:99"} ${slot.teacher_name || ""}`;
}

function filterSlotsForClientTask(task, slots) {
  const info = clientTaskDesiredDateInfo(task);
  const today = info.today;
  const notes = [];
  let items = Array.isArray(slots) ? slots.slice() : [];
  items = items.filter(slot => String(slot.date || "") >= today);
  if (info.raw && !info.past && info.deadlineMode) {
    const withinDeadline = items.filter(slot => String(slot.date || "") <= info.raw);
    if (withinDeadline.length) {
      items = withinDeadline;
      notes.push(`Ищу окна до следующего занятия: до ${formatShortRuDate(info.raw)} включительно.`);
    } else {
      notes.push(`До ${formatShortRuDate(info.raw)} свободных окон не найдено. Показываю ближайшие будущие варианты.`);
    }
  } else if (info.raw && !info.past) {
    const exact = items.filter(slot => String(slot.date || "") === info.raw);
    if (exact.length) items = exact;
    else notes.push(`На ${formatShortRuDate(info.raw)} свободных окон не найдено. Показываю ближайшие будущие варианты.`);
  } else if (info.raw && info.past) {
    notes.push(`Срок ${formatShortRuDate(info.raw)} уже прошёл. Показываю ближайшие будущие варианты.`);
  }
  const beforeLocation = items;
  items = items.filter(slot => slotLocationMatchesTask(slot, task));
  if (!items.length && beforeLocation.length) {
    notes.push("Точного совпадения по филиалу/формату нет. Показываю ближайшие окна любого формата.");
    items = beforeLocation;
  }
  const beforeTime = items;
  items = items.filter(slot => slotTimeMatchesTask(slot, task));
  if (!items.length && beforeTime.length) {
    notes.push("Точного совпадения по желаемому времени нет. Показываю ближайшие окна без фильтра по времени.");
    items = beforeTime;
  }
  const seen = new Set();
  items = items.filter(slot => {
    const key = [slot.id, slot.date, slot.start_time, slot.end_time, slot.user_id, slot.teacher_name].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => slotTaskSortKey(a).localeCompare(slotTaskSortKey(b)));
  return { items: items.slice(0, 8), notes };
}

function slotLabelForMessage(slot) {
  const teacher = slot?.teacher_name || "преподаватель";
  const day = slot?.day_name || slot?.day_short || "день";
  const date = slot?.date_label || formatShortRuDate(slot?.date || "");
  const time = openSlotTimeRange(slot || {});
  const location = slot?.location || "Любой формат";
  return `${teacher}, ${day} ${date}, ${time}, ${location}`;
}

function buildClientTaskParentMessage(task, slot = null) {
  const type = String(task?.task_type || "");
  const child = String(task?.child_name || task?.client_name || "ребёнка").trim();
  const slotLine = slot ? slotLabelForMessage(slot) : "вариант времени уточним и предложим дополнительно";
  if (type === "payment") {
    const amount = task.amount || "239 BYN";
    const paymentFor = task.payment_for || "4 занятия";
    const deadline = task.deadline || task.desired_date || "до следующего занятия";
    return `Здравствуйте! Напоминаем по оплате за занятия.\n\nУченик: ${child}\nСумма: ${amount}\nЗа что: ${paymentFor}\nЖелательно оплатить: ${deadline}.\n\nПодскажите, пожалуйста, когда будет удобно внести оплату?`;
  }
  if (type === "trial") {
    return `Здравствуйте! Можем записать ребёнка на пробное занятие в Yellow Club.\n\nПредлагаем вариант: ${slotLine}.\n\nПодойдёт ли вам это время?`;
  }
  return `Здравствуйте! У ${child} есть занятие для отработки.\n\nМожем предложить вариант: ${slotLine}.\n\nПодойдёт ли вам это время?`;
}

function clientTaskWorkflowHtml(task) {
  const id = clientTaskRawId(task);
  const isPayment = String(task.task_type || "") === "payment";
  const loading = !!state.clientTaskSlotsLoading[id];
  const slots = state.clientTaskSlotResults[id] || [];
  const notes = state.clientTaskSlotNotes[id] || [];
  const message = state.clientTaskGeneratedMessages[id] || "";
  const selectedSlotId = String(state.clientTaskSelectedSlots[id] || "");
  const slotsHtml = !isPayment && (loading || slots.length || notes.length) ? `<div class="cm-task-workflow">
    <div class="cm-task-workflow-head"><b>Подбор окон</b><span>${loading ? "ищу варианты..." : slots.length ? `найдено ${slots.length}` : "вариантов нет"}</span></div>
    ${notes.length ? `<div class="cm-task-notes">${notes.map(n => `<p>${escapeHtml(n)}</p>`).join("")}</div>` : ""}
    ${loading ? `<div class="cm-task-loading">Проверяю свободные окна преподавателей...</div>` : ""}
    ${!loading && slots.length ? `<div class="cm-task-slot-list">${slots.map(slot => {
      const sid = String(slot.id || "");
      const selected = selectedSlotId && selectedSlotId === sid;
      return `<article class="cm-task-slot ${selected ? "selected" : ""}">
        <div><b>${escapeHtml(openSlotTimeRange(slot))}</b><span>${escapeHtml((slot.day_name || "День") + (slot.date_label ? ` · ${slot.date_label}` : ""))}</span></div>
        <div><b>${escapeHtml(slot.teacher_name || "Преподаватель")}</b><span>${escapeHtml(slot.location || "Любой формат")}</span></div>
        ${slot.note ? `<p>${escapeHtml(slot.note)}</p>` : ""}
        <button type="button" class="secondary cm-task-use-slot" data-task-id="${escapeAttr(id)}" data-slot-id="${escapeAttr(sid)}">${selected ? "Выбрано" : "Использовать окно"}</button>
      </article>`;
    }).join("")}</div>` : ""}
    ${!loading && !slots.length ? `<div class="cm-task-loading">Свободных окон не найдено. Попробуйте расширить дату/время или попросить преподавателей заполнить вкладку “Время”.</div>` : ""}
  </div>` : "";
  const messageHtml = message ? `<div class="cm-task-message-box">
    <div class="cm-task-workflow-head"><b>${isPayment ? "Сообщение по оплате" : "Сообщение родителю"}</b><span>черновик</span></div>
    <pre>${escapeHtml(message)}</pre>
    <div class="cm-task-message-actions">
      <button type="button" class="primary cm-task-copy-message" data-task-id="${escapeAttr(id)}">Скопировать</button>
      <button type="button" class="secondary cm-task-waiting-client" data-task-id="${escapeAttr(id)}">Ждём ответа</button>
    </div>
  </div>` : "";
  return slotsHtml + messageHtml;
}

function clientTaskActionButtons(task) {
  const id = clientTaskRawId(task);
  const type = String(task.task_type || "");
  const status = String(task.status || "new");
  if (status === "done") {
    return `<div class="cm-task-final-note ok">Задача выполнена. Агент и отчёты будут считать её закрытой.</div>`;
  }
  if (status === "cancelled") {
    return `<div class="cm-task-final-note muted">Задача отменена. Действий не требуется.</div>`;
  }
  const primary = [];
  if (status === "new") {
    primary.push(`<button type="button" class="primary cm-task-action-status" data-task-id="${escapeAttr(id)}" data-status="in_work">Взять в работу</button>`);
  }
  if (status === "in_work" || status === "new") {
    if (type === "payment") {
      primary.push(`<button type="button" class="primary cm-task-message" data-task-id="${escapeAttr(id)}">Сообщение клиенту</button>`);
    } else {
      primary.push(`<button type="button" class="primary cm-task-slots" data-task-id="${escapeAttr(id)}">Подобрать окна</button>`);
    }
  }
  if (status === "waiting_client") {
    primary.push(`<button type="button" class="primary cm-task-action-status" data-task-id="${escapeAttr(id)}" data-status="done">${escapeHtml(clientTaskDoneButtonLabel(task))}</button>`);
  }
  primary.push(`<button type="button" class="secondary cm-task-edit" data-task-id="${escapeAttr(id)}">Изменить</button>`);
  primary.push(`<button type="button" class="secondary danger cm-task-action-status" data-task-id="${escapeAttr(id)}" data-status="cancelled">Отменить</button>`);
  return `<div class="cm-task-actions">${primary.join("")}</div>`;
}

function clientTaskCard(task) {
  const type = String(task.task_type || "");
  const status = String(task.status || "new");
  const priority = String(task.priority || "normal");
  const id = clientTaskRawId(task);
  const isPayment = type === "payment";
  const expanded = String(state.clientTaskExpandedId || "") === id;
  const compactComment = task.comment ? String(task.comment).replace(/\s+/g, " ").trim().slice(0, 120) : "";
  const nextStepHtml = `<div class="cm-task-next-step"><b>Следующий шаг</b><span>${escapeHtml(clientTaskNextStep(task))}</span></div>`;
  const detailsHtml = expanded ? `
    <div class="cm-task-details">
      ${nextStepHtml}
      ${isPayment && (task.amount || task.payment_for) ? `<p class="cm-task-payment">${escapeHtml([task.amount, task.payment_for].filter(Boolean).join(" · "))}</p>` : ""}
      ${task.contact ? `<p class="cm-task-contact">Контакт: ${escapeHtml(task.contact)}</p>` : ""}
      ${task.comment ? `<p class="cm-task-comment">${nl2br(task.comment)}</p>` : ""}
      ${task.source_type ? `<p class="cm-task-source">Создано автоматически из МойКласс</p>` : ""}
      ${clientTaskWorkflowHtml(task)}
      ${clientTaskActionButtons(task)}
      <button class="secondary cm-task-toggle cm-task-collapse" data-task-id="${escapeAttr(id)}" data-expanded="1">Свернуть задачу</button>
    </div>` : `
    ${compactComment ? `<p class="cm-task-compact-comment">${escapeHtml(compactComment)}${String(task.comment).length > 120 ? "..." : ""}</p>` : ""}
    ${task.source_type ? `<p class="cm-task-source compact-source">Автоматически из МойКласс</p>` : ""}
    <button class="primary cm-task-toggle cm-task-open" data-task-id="${escapeAttr(id)}" data-expanded="0">Открыть задачу</button>`;
  return `<article class="cm-task-card ${escapeAttr(type)} ${escapeAttr(priority)} ${escapeAttr(status)} ${expanded ? "expanded" : "collapsed"}" data-client-task-id="${escapeAttr(id)}">
    <div class="cm-task-top">
      <span class="cm-task-type">${escapeHtml(clientTaskTypeLabel(type))}</span>
      <span class="cm-task-status ${escapeAttr(status)}">${escapeHtml(clientTaskStatusLabel(status))}</span>
    </div>
    <h3>${escapeHtml(clientTaskPerson(task))}</h3>
    <p class="cm-task-meta">${escapeHtml(clientTaskDateLine(task))}</p>
    ${detailsHtml}
  </article>`;
}
function filteredClientTasks() {
  let items = (state.clientTasks || []).slice();
  const type = state.clientTaskTypeFilter || "all";
  const status = state.clientTaskStatusFilter || "active";
  if (type !== "all") items = items.filter(t => String(t.task_type || "") === type);
  if (status === "active") items = items.filter(t => !isClientTaskClosed(t));
  else if (status !== "all") items = items.filter(t => String(t.status || "new") === status);
  return items;
}
function renderClientTasks() {
  const h2 = document.querySelector("#tab-tasks .section-head h2");
  const p = document.querySelector("#tab-tasks .section-head p");
  if (h2) h2.textContent = "Задачи клиент-менеджера";
  if (p) p.textContent = "Автоматические задачи из МойКласс. Используем простые статусы: новая, в работе, ждём клиента, выполнена или отменена.";
  const summary = $("tasksSummary");
  const note = document.querySelector("#tab-tasks .task-help-note");
  const root = $("tasksList");
  if (!root) return;
  const active = (state.clientTasks || []).filter(t => !isClientTaskClosed(t)).length;
  const urgent = (state.clientTasks || []).filter(t => ["urgent", "high"].includes(String(t.priority || "normal")) && !isClientTaskClosed(t)).length;
  const waiting = clientTaskCountByStatus(["waiting_client"]);
  if (summary) summary.innerHTML = `
    <div class="task-summary-card"><b>${active}</b><span>активных задач</span></div>
    <div class="task-summary-card"><b>${urgent}</b><span>важных</span></div>
    <div class="task-summary-card"><b>${waiting}</b><span>в ожидании</span></div>`;
  if (note) {
    const sync = state.clientTaskAutoSync || {};
    const syncLine = sync.syncedAt ? ` Последняя синхронизация: ${escapeHtml(sync.syncedAt)}.` : "";
    note.innerHTML = `Задачи появляются автоматически: пропуск ученика создаёт отработку, нулевой остаток создаёт задачу оплаты. Выполненные и отменённые задачи не появляются в активном списке.${syncLine}`;
  }
  const items = filteredClientTasks();
  root.innerHTML = `
    <div class="cm-task-toolbar">
      <button type="button" class="primary" id="syncClientTasks" ${state.clientTasksSyncing ? "disabled" : ""}>${state.clientTasksSyncing ? "Обновляю..." : "Обновить из МойКласс"}</button>
      <button type="button" class="secondary" id="newClientTask">Ручная задача</button>
      ${renderClientTaskTypeFilters()}
      ${renderClientTaskStatusFilters()}
    </div>
    ${clientTaskFormHtml()}
    ${items.length ? `<div class="cm-task-list">${items.map(clientTaskCard).join("")}</div>` : `<div class="empty task-empty"><b>Автоматических задач нет.</b><span>Если после занятий были пропуски или закончилась оплата, нажмите «Обновить из МойКласс». Ручную задачу создавайте только для исключений.</span></div>`}`;
  bindClientTaskEvents();
}
function renderTasks() {
  if (isClientManagerRole()) return renderClientTasks();
  const h2 = document.querySelector("#tab-tasks .section-head h2");
  const p = document.querySelector("#tab-tasks .section-head p");
  const note = document.querySelector("#tab-tasks .task-help-note");
  if (h2) h2.textContent = "Мои задачи";
  if (p) p.textContent = "Автоматическая лента: подготовка, проверка работ, доработки и незакрытые занятия.";
  if (note) note.textContent = "Задачи не нужно закрывать вручную. Они исчезают сами, когда вы выполняете действие в карточке занятия.";
  return renderTeacherTasks();
}
function updateClientTaskFormFields() {
  const type = $("cmTaskType")?.value || "makeup";
  const isPayment = type === "payment";
  document.querySelectorAll(".cm-slot-field").forEach(el => el.classList.toggle("hidden", isPayment));
  document.querySelectorAll(".cm-payment-field").forEach(el => el.classList.toggle("hidden", !isPayment));
  const dateLabel = document.querySelector(".cm-date-field span");
  if (dateLabel) dateLabel.textContent = isPayment ? "Дедлайн оплаты" : "Желаемая дата";
}
function bindClientTaskEvents() {
  $("syncClientTasks")?.addEventListener("click", syncClientTasks);
  $("newClientTask")?.addEventListener("click", () => {
    state.clientTaskFormOpen = true;
    state.clientTaskEditingId = "";
    renderClientTasks();
  });
  $("closeClientTaskForm")?.addEventListener("click", () => {
    state.clientTaskFormOpen = false;
    state.clientTaskEditingId = "";
    renderClientTasks();
  });
  $("resetClientTaskForm")?.addEventListener("click", () => {
    state.clientTaskEditingId = "";
    state.clientTaskFormOpen = true;
    renderClientTasks();
  });
  $("clientTaskForm")?.addEventListener("submit", saveClientTask);
  $("cmTaskType")?.addEventListener("change", updateClientTaskFormFields);
  document.querySelectorAll("[data-cm-task-filter]").forEach(btn => btn.addEventListener("click", async () => {
    state.clientTaskTypeFilter = btn.dataset.cmTaskFilter || "all";
    await loadTasks();
  }));
  document.querySelectorAll("[data-cm-task-status-filter]").forEach(btn => btn.addEventListener("click", async () => {
    state.clientTaskStatusFilter = btn.dataset.cmTaskStatusFilter || "active";
    await loadTasks();
  }));
  document.querySelectorAll(".cm-task-toggle").forEach(btn => btn.addEventListener("click", () => {
    const id = String(btn.dataset.taskId || "");
    const wasExpanded = btn.dataset.expanded === "1";
    state.clientTaskExpandedId = wasExpanded ? "" : id;
    renderClientTasks();
  }));
  document.querySelectorAll(".cm-task-edit").forEach(btn => btn.addEventListener("click", () => {
    state.clientTaskEditingId = btn.dataset.taskId || "";
    state.clientTaskExpandedId = btn.dataset.taskId || state.clientTaskExpandedId || "";
    state.clientTaskFormOpen = true;
    renderClientTasks();
  }));
  document.querySelectorAll(".cm-task-status-select").forEach(sel => sel.addEventListener("change", () => updateClientTaskStatus(sel.dataset.taskId, sel.value)));
  document.querySelectorAll(".cm-task-action-status").forEach(btn => btn.addEventListener("click", () => updateClientTaskStatus(btn.dataset.taskId, btn.dataset.status)));
  document.querySelectorAll(".cm-task-slots").forEach(btn => btn.addEventListener("click", () => loadClientTaskSlots(btn.dataset.taskId)));
  document.querySelectorAll(".cm-task-message").forEach(btn => btn.addEventListener("click", () => generateClientTaskMessage(btn.dataset.taskId)));
  document.querySelectorAll(".cm-task-use-slot").forEach(btn => btn.addEventListener("click", () => useClientTaskSlot(btn.dataset.taskId, btn.dataset.slotId)));
  document.querySelectorAll(".cm-task-copy-message").forEach(btn => btn.addEventListener("click", () => copyClientTaskMessage(btn.dataset.taskId)));
  document.querySelectorAll(".cm-task-waiting-client").forEach(btn => btn.addEventListener("click", () => updateClientTaskStatus(btn.dataset.taskId, "waiting_client")));
}
async function syncClientTasks() {
  if (state.clientTasksSyncing) return;
  state.clientTasksSyncing = true;
  renderClientTasks();
  try {
    const sync = await apiPost("/api/client-tasks-sync", {});
    state.clientTaskAutoSync = sync;
    const data = await apiGet(`/api/client-tasks?status=${encodeURIComponent(state.clientTaskStatusFilter || "active")}&type=${encodeURIComponent(state.clientTaskTypeFilter || "all")}&sync=0`);
    state.clientTasks = data.items || [];
    state.tasks = state.clientTasks;
    setNotice(`МойКласс проверен: задач создано/обновлено ${sync.createdOrUpdated || 0}`, "ok");
  } catch (e) {
    setNotice(safeUserError(e), "error");
  } finally {
    state.clientTasksSyncing = false;
    renderClientTasks();
  }
}

async function saveClientTask(event) {
  event?.preventDefault?.();
  const type = $("cmTaskType")?.value || "makeup";
  const isPayment = type === "payment";
  const payload = {
    id: $("cmTaskId")?.value || "",
    taskType: type,
    priority: $("cmTaskPriority")?.value || "normal",
    clientName: $("cmClientName")?.value || "",
    childName: $("cmChildName")?.value || "",
    contact: $("cmContact")?.value || "",
    desiredDate: isPayment ? "" : ($("cmDesiredDate")?.value || ""),
    desiredTime: isPayment ? "" : ($("cmDesiredTime")?.value || ""),
    location: isPayment ? "" : ($("cmLocation")?.value || "Любой формат"),
    amount: isPayment ? ($("cmAmount")?.value || "") : "",
    paymentFor: isPayment ? ($("cmPaymentFor")?.value || "") : "",
    deadline: isPayment ? ($("cmDesiredDate")?.value || "") : "",
    comment: $("cmComment")?.value || "",
    status: "new",
  };
  try {
    const data = await apiPost("/api/client-task-save", payload);
    state.clientTasks = data.items || [];
    state.tasks = state.clientTasks;
    state.clientTaskFormOpen = false;
    state.clientTaskEditingId = "";
    setNotice("Задача сохранена", "ok");
    renderClientTasks();
  } catch (e) { setNotice(safeUserError(e), "error"); }
}
async function updateClientTaskStatus(taskId, status) {
  try {
    const data = await apiPost("/api/client-task-status", { id: taskId, status });
    state.clientTasks = data.items || [];
    state.tasks = state.clientTasks;
    const cleanStatus = String(status || "");
    if (CLIENT_TASK_CLOSED_STATUSES.has(cleanStatus)) {
      state.clientTaskExpandedId = "";
      delete state.clientTaskSlotResults[String(taskId || "")];
      delete state.clientTaskSlotNotes[String(taskId || "")];
      delete state.clientTaskSelectedSlots[String(taskId || "")];
      delete state.clientTaskGeneratedMessages[String(taskId || "")];
    }
    setNotice(cleanStatus === "done" ? "Задача отмечена выполненной" : cleanStatus === "cancelled" ? "Задача отменена" : "Статус задачи обновлён", "ok");
    renderClientTasks();
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function loadClientTaskOpenSlotsForWeek(week) {
  const key = week === "next" ? "next" : "current";
  const cached = state.clientTaskOpenSlotsCache[key];
  if (cached && Date.now() - cached.ts < 60000) return cached.items || [];
  const data = await apiGet(`/api/open-slots?week=${encodeURIComponent(key)}`);
  const items = data.items || [];
  state.clientTaskOpenSlotsCache[key] = { ts: Date.now(), items };
  return items;
}

async function loadClientTaskSlots(taskId) {
  const id = String(taskId || "");
  const task = (state.clientTasks || []).find(t => String(t.id || "") === id);
  if (!task) return;
  state.clientTaskExpandedId = id;
  state.clientTaskSlotsLoading[id] = true;
  state.clientTaskSlotNotes[id] = [];
  renderClientTasks();
  try {
    const [current, next] = await Promise.all([
      loadClientTaskOpenSlotsForWeek("current"),
      loadClientTaskOpenSlotsForWeek("next"),
    ]);
    const result = filterSlotsForClientTask(task, [...current, ...next]);
    state.clientTaskSlotResults[id] = result.items;
    state.clientTaskSlotNotes[id] = result.notes;
    if (!result.items.length) setNotice("Подходящих окон не найдено", "error");
    else setNotice(`Найдено окон: ${result.items.length}`, "ok");
  } catch (e) {
    state.clientTaskSlotResults[id] = [];
    state.clientTaskSlotNotes[id] = [e.message || String(e)];
    setNotice(safeUserError(e), "error");
  } finally {
    state.clientTaskSlotsLoading[id] = false;
    renderClientTasks();
  }
}

function useClientTaskSlot(taskId, slotId) {
  const id = String(taskId || "");
  const task = (state.clientTasks || []).find(t => String(t.id || "") === id);
  const slot = (state.clientTaskSlotResults[id] || []).find(x => String(x.id || "") === String(slotId || ""));
  if (!task || !slot) return;
  state.clientTaskExpandedId = id;
  state.clientTaskSelectedSlots[id] = String(slot.id || "");
  state.clientTaskGeneratedMessages[id] = buildClientTaskParentMessage(task, slot);
  if (String(task.status || "new") === "new") {
    updateClientTaskStatus(id, "in_work");
    return;
  }
  setNotice("Окно выбрано, черновик сообщения подготовлен", "ok");
  renderClientTasks();
}

function generateClientTaskMessage(taskId) {
  const id = String(taskId || "");
  const task = (state.clientTasks || []).find(t => String(t.id || "") === id);
  if (!task) return;
  state.clientTaskExpandedId = id;
  const selectedId = String(state.clientTaskSelectedSlots[id] || "");
  const slot = selectedId ? (state.clientTaskSlotResults[id] || []).find(x => String(x.id || "") === selectedId) : null;
  state.clientTaskGeneratedMessages[id] = buildClientTaskParentMessage(task, slot);
  if (String(task.status || "new") === "new") {
    updateClientTaskStatus(id, "in_work");
    return;
  }
  setNotice("Черновик сообщения подготовлен", "ok");
  renderClientTasks();
}

async function copyClientTaskMessage(taskId) {
  const text = state.clientTaskGeneratedMessages[String(taskId || "")] || "";
  if (!text.trim()) return setNotice("Нет текста для копирования", "error");
  try {
    await navigator.clipboard.writeText(text);
    setNotice("Сообщение скопировано", "ok");
  } catch (_) {
    setNotice("Не удалось скопировать автоматически. Выделите текст вручную.", "error");
  }
}
function useClientTaskInChat(taskId, mode) {
  const task = (state.clientTasks || []).find(t => String(t.id || "") === String(taskId || ""));
  if (!task) return;
  const input = $("askInput");
  if (input) {
    input.value = clientTaskActionPrompt(task, mode || (task.task_type === "payment" ? "payment" : "slots"));
    autoResizeChatInput();
    input.blur?.();
  }
  setChatInputFocused(false);
  activateTab("ask");
}


function currentMonthValue() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function isValidMonthValue(v) {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(v);
}

function formatMonthLabel(value) {
  if (!isValidMonthValue(value)) return "—";
  const [y, m] = value.split("-");
  const d = new Date(Number(y), Number(m) - 1, 1);
  return d.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
}

function syncMonthPicker(input) {
  if (!input) return;
  if (!isValidMonthValue(input.value)) input.value = currentMonthValue();
  const wrap = input.closest(".yc-month-picker");
  const span = wrap?.querySelector(".yc-month-picker__value");
  if (span) span.textContent = formatMonthLabel(input.value);
}

function initMonthPicker(input, preferred) {
  if (!input) return;
  if (!isValidMonthValue(input.value)) {
    input.value = isValidMonthValue(preferred) ? preferred : currentMonthValue();
  }
  syncMonthPicker(input);
  if (!input.dataset.monthPickerBound) {
    input.dataset.monthPickerBound = "1";
    input.addEventListener("change", () => syncMonthPicker(input));
    input.addEventListener("input", () => syncMonthPicker(input));
  }
}

function renderReportsUnavailable() {
  const summary = $("reportsSummary");
  const details = $("reportsDetailCards");
  const sections = $("reportsSections");
  const textCard = $("reportsTextCard");
  if (summary) summary.innerHTML = "";
  if (details) details.innerHTML = "";
  if (sections) sections.innerHTML = `<div class="empty">Для выбранной роли отчёты МойКласс пока недоступны.</div>`;
  if (textCard) textCard.classList.add("hidden");
}

function reportMetric(value, fallback = "0") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function renderReportRows(rows) {
  const list = Array.isArray(rows) ? rows : [];
  return list.map(row => `<div class="report-row">
    <span>${escapeHtml(row.label || "")}</span>
    <b>${escapeHtml(reportMetric(row.value, "н/д"))}</b>
    ${row.note ? `<small>${escapeHtml(row.note)}</small>` : ""}
  </div>`).join("");
}

function renderReportDetailCards(cards) {
  const list = Array.isArray(cards) ? cards : [];
  if (!list.length) return "";
  return list.map(card => {
    const metrics = Array.isArray(card.metrics) ? card.metrics : [];
    const actions = Array.isArray(card.actions) ? card.actions : [];
    const accent = escapeAttr(card.accent || card.kind || "default");
    return `<article class="report-detail-card report-detail-${accent}">
      <div class="report-detail-head">
        <div>
          <h3>${escapeHtml(card.title || "Раздел")}</h3>
          ${card.subtitle ? `<p>${escapeHtml(card.subtitle)}</p>` : ""}
        </div>
      </div>
      <div class="report-detail-metrics">
        ${metrics.map(m => `<div><span>${escapeHtml(m.label || "")}</span><b>${escapeHtml(reportMetric(m.value, "н/д"))}</b></div>`).join("")}
      </div>
      ${actions.length ? `<ul class="report-detail-actions">${actions.map(a => `<li>${escapeHtml(a)}</li>`).join("")}</ul>` : ""}
    </article>`;
  }).join("");
}

function renderReports() {
  const summary = $("reportsSummary");
  const details = $("reportsDetailCards");
  const sections = $("reportsSections");
  const text = $("reportsText");
  const textCard = $("reportsTextCard");
  const monthInput = $("reportsMonth");
  if (!summary || !sections) return;
  if (!canUseReports()) return renderReportsUnavailable();
  const data = state.reportsData || {};
  const report = data.report || null;
  initMonthPicker(monthInput, state.reportsMonth);
  if (state.reportsBusy) {
    summary.innerHTML = `<div class="reports-loading">Формирую отчёт из МойКласс...</div>`;
    if (details) details.innerHTML = "";
    sections.innerHTML = "";
    if (textCard) textCard.classList.add("hidden");
    return;
  }
  if (!report) {
    summary.innerHTML = "";
    if (details) details.innerHTML = "";
    sections.innerHTML = `<div class="empty">Выберите месяц и нажмите «Сформировать отчёт».</div>`;
    if (textCard) textCard.classList.add("hidden");
    return;
  }
  const metrics = report.keyMetrics || {};
  summary.innerHTML = `
    <div class="report-stat"><b>${escapeHtml(reportMetric(metrics.activeStudents))}</b><span>активных учеников</span></div>
    <div class="report-stat"><b>${escapeHtml(reportMetric(metrics.lessons))}</b><span>занятий</span></div>
    <div class="report-stat"><b>${escapeHtml(reportMetric(metrics.visits))}</b><span>посещений</span></div>
    <div class="report-stat"><b>${escapeHtml(reportMetric(metrics.missed))}</b><span>пропусков</span></div>
    <div class="report-stat"><b>${escapeHtml(reportMetric(metrics.paymentsSum))}</b><span>сумма оплат</span></div>
    <div class="report-stat"><b>${escapeHtml(reportMetric(metrics.clientTasks))}</b><span>активных задач</span></div>`;
  if (details) details.innerHTML = renderReportDetailCards(report.detailCards || []);
  const sectionsHtml = (report.sections || []).map(section => `<article class="card report-section-card">
    <h3>${escapeHtml(section.title || "Раздел")}</h3>
    <div class="report-rows">${renderReportRows(section.rows || [])}</div>
  </article>`).join("");
  sections.innerHTML = sectionsHtml || `<div class="empty">Разделов отчёта нет.</div>`;
  if (text && report.text) text.textContent = report.text;
  if (textCard) textCard.classList.toggle("hidden", !report.text);
}

function _canUseKpi() {
  const role = state.me?.role || "";
  return ["owner", "operations", "methodist", "client_manager"].includes(role);
}

function renderKpi() {
  const el = $("kpiBlock");
  if (!el) return;
  if (!_canUseKpi()) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  if (state.kpiBusy) {
    el.innerHTML = `<div class="kpi-loading">Загружаю KPI...</div>`;
    return;
  }
  const kpi = state.kpiData;
  if (!kpi) {
    el.innerHTML = `<div class="kpi-unavailable">KPI задач пока недоступен.</div>`;
    return;
  }
  const bt = kpi.by_type || {};
  const total = kpi.total ?? 0;
  const emptyNote = total === 0
    ? `<div class="kpi-empty-note">Пока нет данных по задачам.</div>`
    : "";
  el.innerHTML = `
    <div class="kpi-head"><h3>KPI задач</h3></div>
    ${emptyNote}
    <div class="kpi-stats">
      <div class="kpi-stat kpi-stat--done"><b>${kpi.done ?? 0}</b><span>Выполнено</span></div>
      <div class="kpi-stat kpi-stat--progress"><b>${kpi.in_progress ?? 0}</b><span>В работе</span></div>
      <div class="kpi-stat kpi-stat--waiting"><b>${kpi.waiting_client ?? 0}</b><span>Ждут клиента</span></div>
      <div class="kpi-stat kpi-stat--canceled"><b>${kpi.canceled ?? 0}</b><span>Отменено</span></div>
    </div>
    <div class="kpi-types">
      <div class="kpi-type"><span>Отработки</span><b>${bt.makeup ?? 0}</b></div>
      <div class="kpi-type"><span>Пробные</span><b>${bt.trial ?? 0}</b></div>
      <div class="kpi-type"><span>Оплаты</span><b>${bt.payment ?? 0}</b></div>
    </div>`;
}

async function loadKpi() {
  if (!_canUseKpi()) return;
  state.kpiBusy = true;
  renderKpi();
  try {
    const data = await apiGet("/api/client-tasks-kpi");
    state.kpiData = data.ok ? data.kpi : null;
  } catch (_) {
    state.kpiData = null;
  } finally {
    state.kpiBusy = false;
    renderKpi();
  }
}

function canUseEmployeeKpi() {
  const role = state.me?.role || "";
  return ["owner", "operations", "methodist"].includes(role);
}

function _kpiDateRange(period) {
  const today = new Date();
  const pad = n => String(n).padStart(2, "0");
  const fmt = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const todayStr = fmt(today);
  if (period === "today") return { date_from: todayStr, date_to: todayStr };
  if (period === "week") {
    const mon = new Date(today);
    const diff = today.getDay() === 0 ? -6 : 1 - today.getDay();
    mon.setDate(today.getDate() + diff);
    return { date_from: fmt(mon), date_to: todayStr };
  }
  const monthStart = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-01`;
  return { date_from: monthStart, date_to: todayStr };
}

async function loadEmployeeKpi(period) {
  if (!canUseEmployeeKpi()) return;
  state.adminKpiPeriod = period || state.adminKpiPeriod || "month";
  state.adminKpiBusy = true;
  document.querySelectorAll("[data-kpi-period]").forEach(btn =>
    btn.classList.toggle("active", btn.dataset.kpiPeriod === state.adminKpiPeriod));
  const contentEl = $("kpiAdminContent");
  if (contentEl) contentEl.innerHTML = `<div class="kpi-loading">Загружаю KPI...</div>`;
  const { date_from, date_to } = _kpiDateRange(state.adminKpiPeriod);
  try {
    const data = await apiGet(`/api/client-tasks-kpi?date_from=${encodeURIComponent(date_from)}&date_to=${encodeURIComponent(date_to)}`);
    state.adminKpiData = data.ok ? data.kpi : null;
  } catch (_) {
    state.adminKpiData = null;
  } finally {
    state.adminKpiBusy = false;
    const el = $("kpiAdminContent");
    if (el) {
      el.innerHTML = state.adminKpiData
        ? renderKpiAdminContent(state.adminKpiData)
        : `<div class="kpi-unavailable">Не удалось загрузить KPI.</div>`;
    }
  }
}

function renderKpiAdminContent(kpi) {
  const bt = kpi.by_type || {};
  const actors = kpi.by_actor || [];
  const total = kpi.total ?? 0;
  const emptyNote = total === 0 ? `<div class="kpi-empty-note">Пока нет данных за выбранный период.</div>` : "";
  const avgStr = kpi.avg_completion_hours != null
    ? `<div class="kpi-stat"><b>${kpi.avg_completion_hours}ч</b><span>Ср. закрытие</span></div>` : "";
  const summaryCards = `<div class="kpi-stats">
    <div class="kpi-stat kpi-stat--done"><b>${kpi.done ?? 0}</b><span>Выполнено</span></div>
    <div class="kpi-stat kpi-stat--progress"><b>${kpi.in_progress ?? 0}</b><span>В работе</span></div>
    <div class="kpi-stat kpi-stat--waiting"><b>${kpi.waiting_client ?? 0}</b><span>Ждут клиента</span></div>
    <div class="kpi-stat kpi-stat--canceled"><b>${kpi.canceled ?? 0}</b><span>Отменено</span></div>
    ${avgStr}
  </div>`;
  const typesBlock = `<article class="card kpi-admin-card">
    <h3>По типам задач</h3>
    <div class="kpi-types">
      <div class="kpi-type"><span>Отработки</span><b>${bt.makeup ?? 0}</b></div>
      <div class="kpi-type"><span>Пробные</span><b>${bt.trial ?? 0}</b></div>
      <div class="kpi-type"><span>Оплаты</span><b>${bt.payment ?? 0}</b></div>
    </div>
  </article>`;
  const actorsBlock = actors.length === 0
    ? `<article class="card kpi-admin-card"><p class="kpi-empty-note">Нет данных по сотрудникам.</p></article>`
    : `<article class="card kpi-admin-card">
    <h3>По сотрудникам</h3>
    <div class="kpi-actors">
      ${actors.map(a => `<div class="kpi-actor">
        <div class="kpi-actor-name">${escapeHtml(a.name || `#${a.user_id}`)}</div>
        <div class="kpi-actor-stats">
          <span class="kpi-actor-done">✅ ${a.done ?? 0}</span>
          <span class="kpi-actor-ip">🔄 ${a.in_progress ?? 0}</span>
          <span class="kpi-actor-wc">⏳ ${a.waiting_client ?? 0}</span>
          <span class="kpi-actor-canceled">❌ ${a.canceled ?? 0}</span>
          <span class="kpi-actor-total">📌 ${a.total_events ?? 0}</span>
        </div>
      </div>`).join("")}
    </div>
  </article>`;
  return emptyNote + summaryCards + typesBlock + actorsBlock;
}

// ── Intern test tools ─────────────────────────────────────────────────────────

function canUseInternTest() {
  return !!state.me?.capabilities?.canUseTestRoles;
}

const INTERN_TEST_STAGES = [
  ["start",            "🔄 Сбросить"],
  ["one_observation",  "1 наблюдение"],
  ["two_observations", "2 наблюдения"],
  ["work_pending",     "Работа ждёт"],
  ["work_accepted",    "Работа принята"],
  ["work_rejected",    "Работа отклонена"],
  ["demo_booked",      "Записан на пробное"],
  ["demo_rejected",    "Пробное не принято"],
  ["admitted",         "Допущен"],
];

function renderInternTestPanel(internUserId) {
  const uid = String(internUserId || "");
  const btns = INTERN_TEST_STAGES.map(([s, l]) =>
    `<button type="button" class="intern-test-btn" data-intern-test-stage="${escapeHtml(s)}" data-intern-test-uid="${escapeHtml(uid)}">${escapeHtml(l)}</button>`
  ).join("");
  return `<details class="intern-test-panel">
    <summary>🔧 Тест стажировки</summary>
    <div class="intern-test-btns">${btns}</div>
  </details>`;
}

function _bindInternTestEvents(internUserId) {
  document.querySelectorAll(".intern-test-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const stage = btn.dataset.internTestStage;
      const uid = btn.dataset.internTestUid || String(internUserId || "");
      btn.disabled = true;
      setNotice("Устанавливаю этап...", "");
      try {
        const isReset = stage === "start";
        const data = await apiPost(
          isReset ? "/api/intern/test-reset" : "/api/intern/test-set-stage",
          isReset ? { intern_user_id: uid } : { intern_user_id: uid, stage }
        );
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice(isReset ? "✓ Прогресс стажёра сброшен." : `✓ Этап установлен: ${stage}.`, "ok");
        state.internTrack = data.track ?? null;
        state.internSection = null;
        state.internOpenStep = null;
        renderInternTrack();
      } catch (e) {
        setNotice(`Ошибка тест-режима: ${e.message}`, "error");
        btn.disabled = false;
      }
    });
  });
}

// ── Intern track ──────────────────────────────────────────────────────────────

async function loadInternTrack() {
  if (!canUseInternship()) return;
  state.internBusy = true;
  renderInternTrack();
  try {
    const [trackData, lessonsData] = await Promise.all([
      apiGet("/api/intern/track"),
      apiGet("/api/intern/upcoming-lessons").catch(() => ({ ok: false })),
    ]);
    state.internTrack = trackData.ok ? trackData.track : null;
    state.internUpcomingLessons = lessonsData.ok ? (lessonsData.lessons || []) : [];
  } catch (_) {
    state.internTrack = null;
    state.internUpcomingLessons = [];
  } finally {
    state.internBusy = false;
    renderInternTrack();
  }
}

function _getRecommendedLessons(upcoming, obs) {
  const signedUpKeys = new Set(obs.map(o => `${o.lesson_date}|${o.lesson_time}`));
  const valid = upcoming.filter(l => {
    if (signedUpKeys.has(`${l.lesson_date}|${l.lesson_time}`)) return false;
    const t = (l.teacher_names || "").trim();
    return t && t !== "—" && t !== "-";
  });
  return valid.slice(0, 5).map((l, i) => {
    const reasons = [];
    if (i === 0) reasons.push("Ближайшее");
    if (l.group_name) reasons.push("Групповое");
    if (l.lesson_topic) reasons.push(l.lesson_topic.substring(0, 35));
    return Object.assign({}, l, { _rec: reasons.join(" · ") || "Рекомендуем" });
  });
}

function _renderLessonCard(l, obs) {
  const alreadySignedUp = obs.some(o => o.lesson_date === l.lesson_date && o.lesson_time === l.lesson_time);
  const btnHtml = alreadySignedUp
    ? `<span class="intern-lesson-signed">✓ Записан</span>`
    : `<button type="button" class="intern-lesson-signup-btn"
        data-date="${escapeHtml(l.lesson_date || "")}"
        data-time="${escapeHtml(l.lesson_time || "")}"
        data-title="${escapeHtml((l.teacher_names || "") + (l.group_name ? " · " + l.group_name : ""))}"
        data-topic="${escapeHtml(l.lesson_topic || "")}">Записаться</button>`;
  const recLabel = l._rec ? `<span class="intern-rec-label">${escapeHtml(l._rec)}</span>` : "";
  return `<div class="intern-lesson-card${alreadySignedUp ? " is-signed" : ""}">
    <div class="intern-lesson-info">
      <span class="intern-lesson-dt">${escapeHtml(l.lesson_date || "")} ${escapeHtml(l.lesson_time || "")}</span>
      <span class="intern-lesson-teacher">${escapeHtml(l.teacher_names || "—")}</span>
      ${l.group_name ? `<span class="intern-lesson-group">${escapeHtml(l.group_name)}</span>` : ""}
      ${l.lesson_topic ? `<span class="intern-lesson-topic">${escapeHtml(l.lesson_topic)}</span>` : ""}
      ${recLabel}
    </div>
    ${btnHtml}
  </div>`;
}

function _renderObsItem(o, editable) {
  const statusLabel = { signed_up: "Записан", observed: "✓ Зачтено" }[o.status] || o.status;
  const commentHtml = o.comment ? `<div class="intern-obs-comment">${escapeHtml(o.comment)}</div>` : "";
  const formHtml = editable && o.status === "signed_up"
    ? `<form class="intern-obs-form" data-obs-id="${o.id}">
        <textarea class="intern-textarea" placeholder="Напишите комментарий о занятии..." rows="2" required></textarea>
        <button type="submit" class="primary">Сохранить</button>
      </form>` : "";
  return `<div class="intern-obs-item intern-obs-item--${o.status}">
    <div class="intern-obs-meta">
      <span class="intern-obs-date">${escapeHtml(o.lesson_date || "")} ${escapeHtml(o.lesson_time || "")}</span>
      <span class="intern-obs-title">${escapeHtml(o.lesson_title || "Занятие")}</span>
      <span class="intern-obs-badge intern-obs-badge--${o.status}">${escapeHtml(statusLabel)}</span>
    </div>
    ${commentHtml}${formHtml}
  </div>`;
}

function _renderObsBody(track, cs) {
  const obs = track.observations || [];
  const obs_count = track.obs_count ?? 0;
  const obs_needed = track.obs_needed ?? 2;
  const progressHtml = `<div class="intern-acc-progress">Засчитано: <b>${obs_count}</b> из <b>${obs_needed}</b></div>`;

  if (cs > 1) {
    const rows = obs.length ? obs.map(o => _renderObsItem(o, false)).join("") : `<div class="intern-acc-empty">Нет записей о наблюдениях.</div>`;
    return progressHtml + `<div class="intern-obs-list">${rows}</div>`;
  }

  const upcoming = state.internUpcomingLessons || [];
  const recs = _getRecommendedLessons(upcoming, obs);
  const allAvail = upcoming.filter(l => !obs.some(o => o.lesson_date === l.lesson_date && o.lesson_time === l.lesson_time));

  const recHtml = recs.length
    ? recs.map(l => _renderLessonCard(l, obs)).join("")
    : `<div class="intern-acc-empty">Нет ближайших занятий. Используйте ручную запись.</div>`;

  const showAllHtml = allAvail.length > recs.length
    ? `<details class="intern-acc-details">
        <summary>Все занятия (${allAvail.length})</summary>
        ${allAvail.map(l => Object.assign({}, l, { _rec: null })).map(l => _renderLessonCard(l, obs)).join("")}
      </details>` : "";

  const obsHtml = obs.length ? `<div class="intern-obs-list">${obs.map(o => _renderObsItem(o, true)).join("")}</div>` : "";

  return progressHtml
    + `<div class="intern-rec-title">Рекомендуемые занятия</div>`
    + recHtml + showAllHtml
    + (obs.length ? `<div class="intern-rec-title" style="margin-top:12px">Мои наблюдения</div>` + obsHtml : "")
    + `<details class="intern-acc-details intern-obs-manual">
        <summary>Записаться вручную</summary>
        <form class="intern-obs-signup-form">
          <label><span>Дата</span><input type="date" id="obsDate" required /></label>
          <label><span>Время</span><input type="time" id="obsTime" /></label>
          <label><span>Преподаватель / группа</span><input type="text" id="obsTitle" placeholder="Иванов А., YC2" required /></label>
          <label><span>Место (необязательно)</span><input type="text" id="obsLocation" placeholder="Кульман / Онлайн" /></label>
          <button type="submit" class="primary">Записаться</button>
        </form>
      </details>`;
}

function _renderWorkBody(track, cs, work, workStatus) {
  const obs_count = track.obs_count ?? 0;
  const obs_needed = track.obs_needed ?? 2;
  if (cs <= 1) {
    return `<div class="intern-acc-lock">🔒 Откроется после ${obs_needed} зачтённых наблюдений.<br><small>Сейчас: ${obs_count} / ${obs_needed}</small></div>`;
  }
  if (cs === 3) {
    return `<div class="intern-acc-note"><p>⏳ Работа отправлена, ожидает проверки методистом.</p>${work?.file_name ? `<p class="muted">Файл: ${escapeHtml(work.file_name)}</p>` : ""}</div>`;
  }
  if (cs >= 4 && workStatus === "accepted") {
    return `<div class="intern-acc-note"><p>✅ Работа принята.</p>${work?.reviewer_comment ? `<div class="intern-acc-review-comment">💬 ${escapeHtml(work.reviewer_comment)}</div>` : ""}</div>`;
  }
  const trialUrl = state.me?.internTrialMaterialUrl || "";
  const notionHtml = trialUrl
    ? `<button type="button" class="intern-notion-btn" id="internOpenNotion">📖 Открыть материал</button>`
    : `<p class="intern-acc-empty">Ссылка на материал не настроена. Обратитесь к методисту.</p>`;
  const rejectedHtml = workStatus === "rejected" && work?.reviewer_comment
    ? `<div class="intern-work-rejected">❌ Отклонено: ${escapeHtml(work.reviewer_comment)}</div>` : "";
  return rejectedHtml
    + `<p class="intern-acc-note-text">Изучите материал и загрузите результат подготовки к пробному занятию.</p>`
    + notionHtml
    + `<form id="internWorkForm" class="intern-work-form">
        <div class="intern-file-pick">
          <span class="intern-file-label" id="internWorkFileName">Файл не выбран</span>
          <button type="button" id="internWorkFilePick" class="secondary">📎 Выбрать файл</button>
          <input type="file" id="internWorkFile" accept=".pdf,.doc,.docx,.pptx,.png,.jpg,.jpeg,.zip" style="display:none" />
        </div>
        <button type="submit" class="primary" id="internWorkSubmitBtn" disabled>${workStatus === "rejected" ? "Отправить новую версию" : "Отправить на проверку"}</button>
      </form>`;
}

function _renderReviewBody(track, cs, work, workStatus) {
  if (cs <= 2) {
    return `<div class="intern-acc-lock">🔒 Откроется после отправки подготовительной работы.</div>`;
  }
  if (cs === 3) {
    return `<div class="intern-acc-note"><p>⏳ Методист проверяет вашу работу. Обычно 1–2 рабочих дня.</p>${work?.file_name ? `<p class="muted">Файл: ${escapeHtml(work.file_name)}</p>` : ""}</div>`;
  }
  return `<div class="intern-acc-note"><p>✅ Работа принята.</p>${work?.reviewer_comment ? `<div class="intern-acc-review-comment">💬 ${escapeHtml(work.reviewer_comment)}</div>` : ""}</div>`;
}

function _renderDemoBody(track, cs, booking, bookingStatus) {
  if (cs < 4) {
    return `<div class="intern-acc-lock">🔒 Откроется после принятия подготовительной работы.</div>`;
  }
  if (cs === 5 && booking) {
    const labels = { requested: "Заявка отправлена", approved: "Слот подтверждён", declined: "Отклонено", conducted: "Проведено, ожидает решения", passed: "Принято ✅", failed: "Не принято" };
    const statusHtml = `<div class="intern-acc-note">
      <p><b>${escapeHtml(labels[bookingStatus] || bookingStatus)}</b></p>
      <p class="muted">Дата: ${escapeHtml(booking.demo_date || "—")} ${escapeHtml(booking.demo_time || "")}</p>
      ${booking.location ? `<p class="muted">Место: ${escapeHtml(booking.location)}</p>` : ""}
      ${bookingStatus === "failed" && booking.reviewer_comment ? `<div class="intern-acc-review-comment">💬 ${escapeHtml(booking.reviewer_comment)}</div>` : ""}
    </div>`;

    const isFinal = bookingStatus === "passed" || bookingStatus === "failed";
    let fb = null;
    if (booking.trainee_feedback_json) {
      try { fb = JSON.parse(booking.trainee_feedback_json); } catch (_) {}
    }

    let feedbackHtml = "";
    if (fb) {
      const fbRows = [
        fb.how ? `<div class="intern-feedback-item"><b>Как прошло:</b> ${escapeHtml(fb.how)}</div>` : "",
        fb.plus ? `<div class="intern-feedback-item"><b>Что получилось:</b> ${escapeHtml(fb.plus)}</div>` : "",
        fb.minus ? `<div class="intern-feedback-item"><b>Сложности:</b> ${escapeHtml(fb.minus)}</div>` : "",
        fb.improve ? `<div class="intern-feedback-item"><b>Что улучшить:</b> ${escapeHtml(fb.improve)}</div>` : "",
        fb.comment ? `<div class="intern-feedback-item"><b>Комментарий:</b> ${escapeHtml(fb.comment)}</div>` : "",
      ].join("");
      feedbackHtml = `<div class="intern-feedback-submitted">
        <div class="intern-feedback-title">✅ Самооценка отправлена</div>
        ${fbRows}
        ${!isFinal ? `<details class="intern-acc-details"><summary>Редактировать самооценку</summary>${_renderFeedbackForm(fb)}</details>` : ""}
      </div>`;
    } else if (!isFinal) {
      feedbackHtml = `<div class="intern-feedback-form-wrap">
        <div class="intern-feedback-title">📝 Самооценка после пробного занятия</div>
        <p class="intern-acc-note-text">После проведения занятия заполните самооценку — методист увидит её перед решением о допуске.</p>
        ${_renderFeedbackForm(null)}
      </div>`;
    }

    return statusHtml + (feedbackHtml ? `<div class="intern-feedback-block">${feedbackHtml}</div>` : "");
  }
  return `<p class="intern-acc-note-text">Работа принята. Запишитесь на пробное занятие под наблюдением методиста.</p>
    <form id="internDemoForm" class="intern-demo-form">
      <label><span>Дата</span><input type="date" id="internDemoDate" required /></label>
      <label><span>Время</span><input type="time" id="internDemoTime" /></label>
      <label><span>Место / формат</span><input type="text" id="internDemoLocation" placeholder="Кульман / Онлайн" /></label>
      <label><span>Комментарий</span><input type="text" id="internDemoNote" placeholder="Необязательно" /></label>
      <button type="submit" class="primary">Отправить заявку</button>
    </form>`;
}

function _renderAdmissionBody(track) {
  const cs = track.current_step ?? 1;
  const booking = track.latest_booking;
  const bookingStatus = booking?.status ?? "";
  if (cs < 5) {
    return `<div class="intern-acc-lock">🔒 Откроется после проведения пробного занятия.</div>`;
  }
  if (track.admitted) {
    return `<div class="intern-acc-admitted"><div class="intern-acc-admitted-icon">🎓</div><p><b>Поздравляем! Вы допущены к проведению пробных занятий.</b></p>${booking?.reviewer_comment ? `<p class="muted">${escapeHtml(booking.reviewer_comment)}</p>` : ""}</div>`;
  }
  if (bookingStatus === "failed") {
    return `<div class="intern-acc-note"><p>⛔ Пробное занятие не принято.</p>${booking?.reviewer_comment ? `<div class="intern-acc-review-comment">💬 ${escapeHtml(booking.reviewer_comment)}</div>` : ""}<p class="muted">Свяжитесь с методистом для обсуждения следующих шагов.</p></div>`;
  }
  return `<div class="intern-acc-note"><p>⏳ Ожидаем решения после пробного занятия.</p>${booking ? `<p class="muted">Дата: ${escapeHtml(booking.demo_date || "—")} ${escapeHtml(booking.demo_time || "")}</p>` : ""}</div>`;
}

function _renderFeedbackForm(existing) {
  const fb = existing || {};
  return `<form id="internFeedbackForm" class="intern-feedback-form">
    <label><span>Как прошло занятие?</span>
      <textarea id="internFbHow" rows="2" placeholder="Общее впечатление от занятия...">${escapeHtml(fb.how || "")}</textarea>
    </label>
    <label><span>Что получилось хорошо?</span>
      <textarea id="internFbPlus" rows="2" placeholder="Плюсы...">${escapeHtml(fb.plus || "")}</textarea>
    </label>
    <label><span>Что было сложно?</span>
      <textarea id="internFbMinus" rows="2" placeholder="Сложности...">${escapeHtml(fb.minus || "")}</textarea>
    </label>
    <label><span>Что улучшить в следующий раз?</span>
      <textarea id="internFbImprove" rows="2" placeholder="Идеи...">${escapeHtml(fb.improve || "")}</textarea>
    </label>
    <label><span>Комментарий (необязательно)</span>
      <textarea id="internFbComment" rows="2" placeholder="Дополнительно...">${escapeHtml(fb.comment || "")}</textarea>
    </label>
    <button type="submit" class="primary" id="internFeedbackSubmitBtn">Отправить самооценку</button>
  </form>`;
}

function _renderStepBody(step, track) {
  const cs = track.current_step ?? 1;
  const work = track.latest_work;
  const booking = track.latest_booking;
  const workStatus = work?.status ?? "";
  const bookingStatus = booking?.status ?? "";
  switch (step.id) {
    case 1: return `<p class="intern-acc-note-text">Аккаунт стажёра создан и активирован в системе.</p>`;
    case 2: return _renderObsBody(track, cs);
    case 3: return _renderWorkBody(track, cs, work, workStatus);
    case 4: return _renderReviewBody(track, cs, work, workStatus);
    case 5: return _renderDemoBody(track, cs, booking, bookingStatus);
    case 6: return _renderAdmissionBody(track);
    default: return "";
  }
}

function _renderAccordionStep(step, track, isOpen) {
  const iconMap = { done: "✅", active: "🔵", waiting: "⏳", locked: "🔒" };
  const icon = step.state === "done" && step.id === 6 && track.admitted ? "🎓" : iconMap[step.state] || "🔒";
  const bodyHtml = isOpen ? _renderStepBody(step, track) : "";
  return `<div class="intern-acc-step intern-acc-step--${step.state}${isOpen ? " is-open" : ""}">
    <button type="button" class="intern-acc-header" data-intern-open-step="${step.id}">
      <span class="intern-acc-icon">${icon}</span>
      <div class="intern-acc-title-block">
        <span class="intern-acc-title">${escapeHtml(step.title)}</span>
        <span class="intern-acc-badge intern-acc-badge--${step.state}">${escapeHtml(step.badge)}</span>
      </div>
      <span class="intern-acc-arrow">▾</span>
    </button>
    ${isOpen ? `<div class="intern-acc-body">${bodyHtml}</div>` : ""}
  </div>`;
}

function _internStepState(track) {
  const cs = track.current_step ?? 1;
  const obs_count = track.obs_count ?? 0;
  const obs_needed = track.obs_needed ?? 2;
  const work = track.latest_work;
  const booking = track.latest_booking;
  const workStatus = work?.status ?? "";
  const bookingStatus = booking?.status ?? "";
  const admitted = track.admitted ?? false;

  const s = (n, curr, max) => n < curr ? "done" : n === curr ? "active" : "locked";

  return [
    {
      id: 1, title: "Регистрация",
      state: "done", badge: "Выполнено",
      desc: "Аккаунт стажёра создан в системе.",
      action: null,
    },
    {
      id: 2, title: "Наблюдение занятий",
      state: cs === 1 ? "active" : cs > 1 ? "done" : "locked",
      badge: cs === 1 ? `${obs_count} / ${obs_needed} наблюдений` : cs > 1 ? "Выполнено" : "Заблокировано",
      desc: `Запишитесь на просмотр занятия, посмотрите его и оставьте комментарий. Нужно ${obs_needed} засчитанных наблюдения.`,
      action: cs === 1 ? { key: "observations", label: "Перейти к наблюдениям" } : null,
    },
    {
      id: 3, title: "Подготовительная работа",
      state: cs <= 1 ? "locked" : cs === 2 ? "active" : cs === 3 ? "waiting" : "done",
      badge: cs <= 1 ? "Заблокировано" : cs === 2 ? "Загрузите работу" : cs === 3 ? "Ждёт проверки" : workStatus === "rejected" ? "Отклонена" : "Принята",
      desc: "Изучите материалы для пробного занятия, выполните задание и загрузите результат.",
      extra: workStatus === "rejected" && work?.reviewer_comment ? `Причина отклонения: ${work.reviewer_comment}` : null,
      action: cs === 2 ? { key: "submit-work", label: workStatus === "rejected" ? "Загрузить работу повторно" : "Открыть материалы / Загрузить работу" } : null,
      actionDisabled: cs === 3 ? { label: "Ждёт проверки" } : null,
    },
    {
      id: 4, title: "Проверка работы",
      state: cs <= 2 ? "locked" : cs === 3 ? "active" : "done",
      badge: cs <= 2 ? "Заблокировано" : cs === 3 ? "На проверке" : "Принята",
      desc: "Методист или администратор проверит вашу работу и напишет комментарий.",
      extra: workStatus === "accepted" && work?.reviewer_comment ? `Комментарий проверяющего: ${work.reviewer_comment}` : null,
      action: null,
      actionDisabled: cs === 3 ? { label: "Ждёт проверки" } : null,
    },
    {
      id: 5, title: "Запись на пробное занятие",
      state: cs < 4 ? "locked" : cs === 4 ? "active" : "done",
      badge: cs < 4 ? "Заблокировано" : cs === 4 ? "Запишитесь" : "Записано",
      desc: "Выберите удобное время и запишитесь на проведение пробного занятия под наблюдением.",
      extra: booking ? `Дата: ${booking.demo_date || "—"} ${booking.demo_time || ""}${booking.location ? ", " + booking.location : ""}` : null,
      action: cs === 4 ? { key: "book-demo", label: "Записаться на пробное" } : null,
      actionDisabled: cs === 5 ? { label: "Ожидает решения" } : null,
    },
    {
      id: 6, title: "Допуск",
      state: admitted ? "done" : cs === 5 ? "active" : "locked",
      badge: admitted ? "Допущен!" : cs === 5 ? "Ожидает решения" : "Заблокировано",
      desc: "После пробного занятия методист или администратор примет решение о допуске к работе.",
      extra: admitted ? "🎉 Поздравляем! Вы допущены к проведению пробных занятий."
        : bookingStatus === "failed" && booking?.reviewer_comment ? `Комментарий: ${booking.reviewer_comment}` : null,
      action: null,
      actionDisabled: cs === 5 && !admitted ? { label: "Ожидает решения" } : null,
    },
  ];
}

function renderInternTrack() {
  const el = $("internContent");
  if (!el) return;
  if (state.internBusy) {
    el.innerHTML = `<div class="kpi-loading">Загружаю данные стажировки...</div>`;
    return;
  }
  if (!state.internTrack) {
    el.innerHTML = `<div class="empty">Не удалось загрузить данные стажировки. Нажмите «Обновить».</div>`;
    return;
  }
  const track = state.internTrack;
  const steps = _internStepState(track);
  const cs = track.current_step ?? 1;
  const autoOpen = Math.min(cs + 1, 6);
  const openStep = state.internOpenStep === null ? autoOpen : state.internOpenStep;
  const stepsHtml = steps.map(step => _renderAccordionStep(step, track, openStep === step.id)).join("");
  const testHtml = canUseInternTest() ? renderInternTestPanel(state.me?.userId) : "";
  el.innerHTML = testHtml + `<div class="intern-acc">${stepsHtml}</div>`;
  _bindInternEvents(track);
  if (canUseInternTest()) _bindInternTestEvents(state.me?.userId);
}

function _bindInternEvents(track) {
  // Accordion toggle
  document.querySelectorAll("[data-intern-open-step]").forEach(btn => {
    btn.addEventListener("click", () => {
      const stepId = parseInt(btn.dataset.internOpenStep);
      const cs = state.internTrack?.current_step ?? 1;
      const autoOpen = Math.min(cs + 1, 6);
      const currentOpen = state.internOpenStep === null ? autoOpen : state.internOpenStep;
      state.internOpenStep = currentOpen === stepId ? 0 : stepId;
      renderInternTrack();
    });
  });

  // Notion open button
  $("internOpenNotion")?.addEventListener("click", () => {
    const url = state.me?.internTrialMaterialUrl || "";
    if (!url) { setNotice("Ссылка на материал не настроена. Обратитесь к методисту.", ""); return; }
    try { if (tg?.openLink) { tg.openLink(url); return; } } catch (_) {}
    window.open(url, "_blank", "noopener");
  });

  // Lesson quick-signup buttons
  document.querySelectorAll(".intern-lesson-signup-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const data = await apiPost("/api/intern/observation-signup", {
          lesson_date: btn.dataset.date || "",
          lesson_time: btn.dataset.time || "",
          lesson_title: ((btn.dataset.title || "") + (btn.dataset.topic ? " — " + btn.dataset.topic : "")).trim(),
          location: "",
        });
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice("Записан на наблюдение! Посмотрите занятие и оставьте комментарий.", "ok");
        await loadInternTrack();
      } catch (e) {
        setNotice(safeUserError(e), "error");
        btn.disabled = false;
      }
    });
  });

  // Manual observation sign-up form
  const signupForm = document.querySelector(".intern-obs-signup-form");
  if (signupForm) {
    signupForm.addEventListener("submit", async e => {
      e.preventDefault();
      const btn = signupForm.querySelector("button[type=submit]");
      if (btn) btn.disabled = true;
      try {
        const data = await apiPost("/api/intern/observation-signup", {
          lesson_date: $("obsDate")?.value || "",
          lesson_time: $("obsTime")?.value || "",
          lesson_title: $("obsTitle")?.value || "",
          location: $("obsLocation")?.value || "",
        });
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice("Запись создана. Посмотрите занятие и оставьте комментарий.", "ok");
        await loadInternTrack();
      } catch (e) {
        setNotice(safeUserError(e), "error");
        if (btn) btn.disabled = false;
      }
    });
  }

  // Observation comment forms
  document.querySelectorAll(".intern-obs-form").forEach(form => {
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const obs_id = form.dataset.obsId;
      const textarea = form.querySelector("textarea");
      const comment = textarea?.value?.trim() || "";
      if (!comment) return;
      const btn = form.querySelector("button[type=submit]");
      if (btn) btn.disabled = true;
      try {
        const data = await apiPost("/api/intern/observation-comment", { obs_id, comment });
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice("Комментарий сохранён. Наблюдение засчитано!", "ok");
        state.internTrack = data.track ?? null;
        renderInternTrack();
      } catch (e) {
        setNotice(safeUserError(e), "error");
        if (btn) btn.disabled = false;
      }
    });
  });

  // File picker for work upload
  const fileInput = $("internWorkFile");
  const filePickBtn = $("internWorkFilePick");
  const fileNameEl = $("internWorkFileName");
  const submitBtn = $("internWorkSubmitBtn");
  if (filePickBtn && fileInput) {
    filePickBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (file) {
        if (fileNameEl) fileNameEl.textContent = file.name;
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  // Work upload form (multipart)
  const workForm = $("internWorkForm");
  if (workForm) {
    workForm.addEventListener("submit", async e => {
      e.preventDefault();
      const file = $("internWorkFile")?.files?.[0];
      if (!file) { setNotice("Выберите файл для отправки.", "error"); return; }
      const btn = workForm.querySelector("button[type=submit]");
      if (btn) btn.disabled = true;
      try {
        const fd = new FormData();
        appendAuthForm(fd);
        fd.append("file", file, file.name);
        const res = await fetch("/api/intern/work-upload", { method: "POST", body: fd });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || "Ошибка загрузки");
        setNotice("Работа отправлена на проверку!", "ok");
        state.internTrack = data.track ?? null;
        state.internSection = null;
        state.internOpenStep = null;
        renderInternTrack();
      } catch (e) {
        setNotice(safeUserError(e), "error");
        if (btn) btn.disabled = false;
      }
    });
  }

  // Demo booking form
  const demoForm = $("internDemoForm");
  if (demoForm) {
    demoForm.addEventListener("submit", async e => {
      e.preventDefault();
      const btn = demoForm.querySelector("button[type=submit]");
      if (btn) btn.disabled = true;
      try {
        const data = await apiPost("/api/intern/demo-book", {
          demo_date: $("internDemoDate")?.value || "",
          demo_time: $("internDemoTime")?.value || "",
          location: $("internDemoLocation")?.value?.trim() || "",
          note: $("internDemoNote")?.value?.trim() || "",
        });
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice("Заявка на пробное занятие отправлена!", "ok");
        state.internTrack = data.track ?? null;
        state.internSection = null;
        state.internOpenStep = null;
        renderInternTrack();
      } catch (e) {
        setNotice(safeUserError(e), "error");
        if (btn) btn.disabled = false;
      }
    });
  }

  // Self-assessment (ОС) form after demo
  const feedbackForm = $("internFeedbackForm");
  if (feedbackForm) {
    feedbackForm.addEventListener("submit", async e => {
      e.preventDefault();
      const btn = $("internFeedbackSubmitBtn");
      if (btn) btn.disabled = true;
      try {
        const data = await apiPost("/api/intern/demo-feedback", {
          how: $("internFbHow")?.value?.trim() || "",
          plus: $("internFbPlus")?.value?.trim() || "",
          minus: $("internFbMinus")?.value?.trim() || "",
          improve: $("internFbImprove")?.value?.trim() || "",
          comment: $("internFbComment")?.value?.trim() || "",
        });
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice("Самооценка сохранена!", "ok");
        state.internTrack = data.track ?? null;
        renderInternTrack();
      } catch (e) {
        setNotice(safeUserError(e), "error");
        if (btn) btn.disabled = false;
      }
    });
  }
}

// ── Admin interns subtab ───────────────────────────────────────────────────────

function _internAdminStatusInfo(intern) {
  const ws = intern.latest_work_status || "";
  const bs = intern.latest_booking_status || "";
  if (intern.admitted || bs === "passed") return { label: "Допущен", cls: "success" };
  if (bs === "failed") return { label: "Не допущен", cls: "danger" };
  if (bs === "conducted") return { label: "Ждёт решения по пробному", cls: "warning" };
  if (bs === "approved" || bs === "requested") return { label: "Записан на пробное", cls: "info" };
  if (ws === "accepted") return { label: "Работа принята", cls: "success" };
  if (ws === "submitted") return { label: "Работа ждёт проверки", cls: "warning" };
  if (ws === "rejected") return { label: "Работа отклонена", cls: "danger" };
  const step = intern.current_step || 1;
  if (step === 1) return intern.obs_count > 0 ? { label: "Наблюдает занятия", cls: "info" } : { label: "Новый", cls: "muted" };
  if (step === 2) return { label: "Готовит работу", cls: "warning" };
  return { label: `Шаг ${step}`, cls: "muted" };
}

function _internAdminFilterItems(items, filter) {
  switch (filter) {
    case "pending_work": return items.filter(i => i.latest_work_status === "submitted");
    case "pending_demo": return items.filter(i => ["requested", "conducted", "approved"].includes(i.latest_booking_status || ""));
    case "admitted": return items.filter(i => i.admitted || i.latest_booking_status === "passed");
    case "problem": return items.filter(i => i.latest_work_status === "rejected" || i.latest_booking_status === "failed");
    default: return items;
  }
}

function _renderInternAdminCardBody(intern) {
  const obs = intern.observations || [];
  const works = intern.works || [];
  const booking = intern.latest_booking;
  const work = intern.latest_work;

  // Observations
  const obsHtml = `<div class="ia-section">
    <div class="ia-section-title">Наблюдения (${intern.obs_count}/${intern.obs_needed})</div>
    ${obs.length === 0
      ? `<div class="ia-empty">Нет наблюдений</div>`
      : obs.map(o => `<div class="ia-obs-item">
          <div class="ia-obs-info">
            <span class="ia-obs-date">${escapeHtml(o.lesson_date || "—")} ${escapeHtml(o.lesson_time || "")}</span>
            <span class="ia-obs-title">${escapeHtml(o.lesson_title || "Занятие")}</span>
            ${o.comment ? `<span class="ia-obs-comment">${escapeHtml(o.comment.slice(0, 200))}</span>` : ""}
          </div>
          <span class="ia-obs-badge ia-obs-badge--${escapeHtml(o.status || "signed_up")}">${o.status === "observed" ? "Зачтено" : "Записан"}</span>
        </div>`).join("")
    }
  </div>`;

  // Work
  const WORK_STATUS_LABEL = { submitted: "Ждёт проверки", accepted: "Принята", rejected: "Отклонена" };
  const WORK_STATUS_CLS   = { submitted: "warning", accepted: "success", rejected: "danger" };
  let workHtml = `<div class="ia-section"><div class="ia-section-title">Подготовительная работа</div>`;
  if (!work) {
    workHtml += `<div class="ia-empty">Работа ещё не загружена</div>`;
  } else {
    const ws = work.status || "";
    workHtml += `<div class="ia-review-block">
      <div class="ia-review-meta">
        <b>${escapeHtml(work.file_name || "—")}</b>
        <span class="muted">${escapeHtml((work.created_at || "").slice(0, 10))}</span>
        <span class="yc-badge yc-badge-${WORK_STATUS_CLS[ws] || "muted"}">${WORK_STATUS_LABEL[ws] || ws}</span>
        ${work.id ? `<a class="ia-dl-link" href="${apiInternWorkDownloadUrl(work.id)}" target="_blank" rel="noopener">⬇ Скачать</a>` : ""}
      </div>
      ${work.reviewer_comment ? `<div class="ia-review-prev-comment">Комментарий: ${escapeHtml(work.reviewer_comment)}</div>` : ""}
      ${ws === "submitted" ? `<div class="ia-review-form" data-work-id="${work.id}">
        <input type="text" class="intern-review-comment" placeholder="Комментарий стажёру (обязателен при отклонении)" />
        <div class="ia-review-btns">
          <button type="button" class="green intern-accept-work" data-work-id="${work.id}">✅ Принять</button>
          <button type="button" class="danger intern-reject-work" data-work-id="${work.id}">❌ Отклонить</button>
        </div>
      </div>` : ""}
    </div>`;
    if (works.length > 1) {
      workHtml += `<details class="ia-history"><summary class="ia-history-toggle">История работ (${works.length})</summary>
        ${works.slice(1).map(w => `<div class="ia-history-item">
          <span>${escapeHtml(w.file_name || "—")}</span>
          <span class="muted">${(w.created_at || "").slice(0, 10)}</span>
          <span class="yc-badge yc-badge-${WORK_STATUS_CLS[w.status] || "muted"}">${WORK_STATUS_LABEL[w.status] || w.status}</span>
          ${w.reviewer_comment ? `<span class="muted">${escapeHtml(w.reviewer_comment.slice(0, 80))}</span>` : ""}
        </div>`).join("")}
      </details>`;
    }
  }
  workHtml += `</div>`;

  // Demo
  const DEMO_STATUS_LABEL = { requested: "Заявка подана", approved: "Подтверждено", conducted: "Проведено, ждёт решения", passed: "Допущен", failed: "Не допущен", declined: "Отклонено" };
  const DEMO_STATUS_CLS   = { requested: "info", approved: "info", conducted: "warning", passed: "success", failed: "danger", declined: "danger" };
  let demoHtml = `<div class="ia-section"><div class="ia-section-title">Пробное занятие</div>`;
  if (!booking) {
    demoHtml += `<div class="ia-empty">Запись на пробное ещё не оформлена</div>`;
  } else {
    const bs = booking.status || "";
    let fb = null;
    if (booking.trainee_feedback_json) { try { fb = JSON.parse(booking.trainee_feedback_json); } catch (_) {} }
    const fbHtml = fb
      ? `<div class="intern-admin-feedback">
          <div class="intern-admin-feedback-title">Самооценка стажёра:</div>
          ${fb.how    ? `<div class="intern-feedback-item"><b>Как прошло:</b> ${escapeHtml(fb.how)}</div>` : ""}
          ${fb.plus   ? `<div class="intern-feedback-item"><b>Что получилось:</b> ${escapeHtml(fb.plus)}</div>` : ""}
          ${fb.minus  ? `<div class="intern-feedback-item"><b>Сложности:</b> ${escapeHtml(fb.minus)}</div>` : ""}
          ${fb.improve? `<div class="intern-feedback-item"><b>Что улучшить:</b> ${escapeHtml(fb.improve)}</div>` : ""}
          ${fb.comment? `<div class="intern-feedback-item"><b>Комментарий:</b> ${escapeHtml(fb.comment)}</div>` : ""}
        </div>`
      : (["requested", "approved", "conducted"].includes(bs)
          ? `<div class="intern-admin-feedback intern-admin-feedback--empty">Стажёр ещё не отправил самооценку. Лучше дождаться.</div>`
          : "");
    const canDecide = ["requested", "approved", "conducted"].includes(bs);
    demoHtml += `<div class="ia-review-block">
      <div class="ia-review-meta">
        <b>${escapeHtml(booking.demo_date || "—")} ${escapeHtml(booking.demo_time || "")}</b>
        ${booking.location ? `<span>${escapeHtml(booking.location)}</span>` : ""}
        <span class="yc-badge yc-badge-${DEMO_STATUS_CLS[bs] || "muted"}">${DEMO_STATUS_LABEL[bs] || bs}</span>
      </div>
      ${booking.slot_ref ? `<div class="ia-review-prev-comment">${escapeHtml(booking.slot_ref)}</div>` : ""}
      ${fbHtml}
      ${booking.reviewer_comment ? `<div class="ia-review-prev-comment">Решение: ${escapeHtml(booking.reviewer_comment)}</div>` : ""}
      ${canDecide ? `<div class="ia-review-form" data-booking-id="${booking.id}">
        <input type="text" class="intern-review-comment" placeholder="Комментарий стажёру (обязателен при отклонении)" />
        <div class="ia-review-btns">
          <button type="button" class="green intern-pass-demo" data-booking-id="${booking.id}">✅ Допустить</button>
          <button type="button" class="danger intern-fail-demo" data-booking-id="${booking.id}">❌ Не допустить</button>
        </div>
      </div>` : ""}
    </div>`;
  }
  demoHtml += `</div>`;

  // Test panel
  const testStageOptions = INTERN_TEST_STAGES.map(([s, l]) => `<option value="${escapeHtml(s)}">${escapeHtml(l)}</option>`).join("");
  const testHtml = canUseInternTest() ? `<div class="ia-section">
    <div class="ia-section-title">Тест-режим</div>
    <details class="intern-test-panel-inline">
      <summary>🔧 Установить тестовый этап</summary>
      <div class="intern-test-inline-body">
        <select class="intern-test-stage-sel" data-uid="${intern.user_id}">${testStageOptions}</select>
        <button type="button" class="intern-test-apply-btn" data-uid="${intern.user_id}">Применить</button>
      </div>
    </details>
  </div>` : "";

  return obsHtml + workHtml + demoHtml + testHtml;
}

async function loadAdminInterns() {
  state.internAdminBusy = true;
  state.internAdminOpenUid = null;
  const root = $("adminContent");
  if (root) root.innerHTML = `<div class="kpi-loading">Загружаю список стажёров...</div>`;
  try {
    const data = await apiGet("/api/admin/interns");
    state.internAdminData = data.ok ? data : null;
  } catch (_) {
    state.internAdminData = null;
  } finally {
    state.internAdminBusy = false;
  }
  return state.internAdminData;
}

function renderAdminInternsContent(data) {
  const allItems = data?.items || [];
  const filter = state.internAdminFilter || "all";
  const items = _internAdminFilterItems(allItems, filter);

  if (!allItems.length) {
    return `<div class="empty">Стажёров в системе нет. Добавьте сотрудника с ролью "Стажер" в разделе "Сотрудники".</div>`;
  }

  const filterCounts = {
    all:          allItems.length,
    pending_work: allItems.filter(i => i.latest_work_status === "submitted").length,
    pending_demo: allItems.filter(i => ["requested", "conducted", "approved"].includes(i.latest_booking_status || "")).length,
    admitted:     allItems.filter(i => i.admitted || i.latest_booking_status === "passed").length,
    problem:      allItems.filter(i => i.latest_work_status === "rejected" || i.latest_booking_status === "failed").length,
  };
  const filterDefs = [
    ["all",          "Все"],
    ["pending_work", "Ждут проверки"],
    ["pending_demo", "Ждут решения"],
    ["admitted",     "Допущены"],
    ["problem",      "Проблемные"],
  ];
  const filterBar = `<div class="ia-filter-bar">
    ${filterDefs.map(([key, label]) => {
      const cnt = filterCounts[key];
      return `<button type="button" class="ia-filter-btn${filter === key ? " ia-filter-btn--active" : ""}" data-filter="${key}">
        ${label}${cnt ? ` <span class="ia-filter-count">${cnt}</span>` : ""}
      </button>`;
    }).join("")}
  </div>`;

  if (!items.length) {
    return filterBar + `<div class="ia-empty-filter">Нет стажёров в этой категории.</div>`;
  }

  const openUid = state.internAdminOpenUid;
  const cardsHtml = items.map(intern => {
    const status = _internAdminStatusInfo(intern);
    const isOpen = openUid === intern.user_id;
    return `<div class="ia-intern-card${isOpen ? " is-open" : ""}" data-uid="${intern.user_id}">
      <div class="ia-intern-header">
        <div class="ia-intern-header-top">
          <div class="ia-intern-header-info">
            <span class="ia-intern-name">${escapeHtml(intern.full_name)}</span>
            ${intern.username ? `<span class="ia-intern-username muted">@${escapeHtml(intern.username)}</span>` : ""}
          </div>
          <button type="button" class="ia-toggle-btn" data-uid="${intern.user_id}">${isOpen ? "Закрыть ▲" : "Открыть ▼"}</button>
        </div>
        <div class="ia-intern-header-meta">
          <span class="yc-badge yc-badge-${status.cls}">${status.label}</span>
          ${intern.is_test_intern ? `<span class="yc-badge yc-badge-info">тест (${escapeHtml(intern.real_role || "?")})</span>` : ""}
          <span class="muted">Наблюдений: ${intern.obs_count}/${intern.obs_needed}</span>
          ${intern.mk_teacher_name ? `<span class="muted">МК: ${escapeHtml(intern.mk_teacher_name)}</span>` : ""}
        </div>
      </div>
      ${isOpen ? `<div class="ia-intern-body">${_renderInternAdminCardBody(intern)}</div>` : ""}
    </div>`;
  }).join("");

  let debugHtml = "";
  if (data.debug) {
    const d = data.debug;
    debugHtml = `<div class="ia-debug">
      <div class="ia-debug-title" onclick="this.nextElementSibling.classList.toggle('hidden')">⚙ Отладка (тест-режим) ▾</div>
      <div class="ia-debug-body hidden">
        <div>Стажёры по роли: <b>${d.internStaffCount}</b></div>
        <div>По активности (obs/works/demo): <b>${d.activityUserCount}</b></div>
        <div>Итого найдено: <b>${d.resolvedInternUsers}</b></div>
        <div>Вы сейчас тест-стажёр: <b>${d.callerIsTestIntern ? "Да" : "Нет"}</b></div>
      </div>
    </div>`;
  }

  return filterBar + debugHtml + `<div class="ia-interns-list">${cardsHtml}</div>`;
}

function _bindAdminInternEvents(root) {
  // Filter
  root.querySelectorAll(".ia-filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      state.internAdminFilter = btn.dataset.filter || "all";
      const adminRoot = $("adminContent");
      if (adminRoot && state.internAdminData) {
        adminRoot.innerHTML = renderAdminInternsContent(state.internAdminData);
        _bindAdminInternEvents(adminRoot);
      }
    });
  });

  // Card toggle
  root.querySelectorAll(".ia-toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const uid = parseInt(btn.dataset.uid, 10);
      state.internAdminOpenUid = state.internAdminOpenUid === uid ? null : uid;
      const adminRoot = $("adminContent");
      if (adminRoot && state.internAdminData) {
        adminRoot.innerHTML = renderAdminInternsContent(state.internAdminData);
        _bindAdminInternEvents(adminRoot);
      }
    });
  });

  // Work review
  root.querySelectorAll(".intern-accept-work, .intern-reject-work").forEach(btn => {
    btn.addEventListener("click", async () => {
      const workId = btn.dataset.workId;
      const block = btn.closest(".ia-review-form");
      const comment = block?.querySelector(".intern-review-comment")?.value?.trim() || "";
      const status = btn.classList.contains("intern-accept-work") ? "accepted" : "rejected";
      if (status === "rejected" && !comment) { setNotice("Для отклонения нужен комментарий.", "error"); return; }
      btn.disabled = true;
      try {
        const data = await apiPost("/api/admin/intern/review-work", { work_id: workId, status, comment });
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice(status === "accepted" ? "Работа принята. Стажёр уведомлён." : "Работа отклонена. Стажёр уведомлён.", "ok");
        await renderAdminContent();
      } catch (e) { setNotice(safeUserError(e), "error"); btn.disabled = false; }
    });
  });

  // Demo review
  root.querySelectorAll(".intern-pass-demo, .intern-fail-demo").forEach(btn => {
    btn.addEventListener("click", async () => {
      const bookingId = btn.dataset.bookingId;
      const block = btn.closest(".ia-review-form");
      const comment = block?.querySelector(".intern-review-comment")?.value?.trim() || "";
      const outcome = btn.classList.contains("intern-pass-demo") ? "passed" : "failed";
      if (outcome === "failed" && !comment) { setNotice("Для отклонения нужен комментарий.", "error"); return; }
      btn.disabled = true;
      try {
        const data = await apiPost("/api/admin/intern/review-demo", { booking_id: bookingId, outcome, comment });
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice(outcome === "passed" ? "Стажёр допущен! Он уведомлён." : "Пробное не принято. Стажёр уведомлён.", "ok");
        await renderAdminContent();
      } catch (e) { setNotice(safeUserError(e), "error"); btn.disabled = false; }
    });
  });

  // Test panel
  root.querySelectorAll(".intern-test-apply-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const uid = btn.dataset.uid;
      const sel = btn.closest(".intern-test-panel-inline")?.querySelector(".intern-test-stage-sel");
      const stage = sel?.value || "start";
      btn.disabled = true;
      setNotice("Устанавливаю тестовый этап...", "");
      try {
        const isReset = stage === "start";
        const data = await apiPost(
          isReset ? "/api/intern/test-reset" : "/api/intern/test-set-stage",
          isReset ? { intern_user_id: uid } : { intern_user_id: uid, stage }
        );
        if (!data.ok) throw new Error(data.error || "Ошибка");
        setNotice(isReset ? "✓ Прогресс сброшен." : `✓ Этап «${stage}» установлен.`, "ok");
        await renderAdminContent();
      } catch (e) { setNotice(`Ошибка: ${e.message}`, "error"); btn.disabled = false; }
    });
  });
}

async function loadReports() {
  if (!canUseReports()) return renderReportsUnavailable();
  const monthInput = $("reportsMonth");
  const month = monthInput?.value || state.reportsMonth || currentMonthValue();
  state.reportsMonth = month;
  if (monthInput) { monthInput.value = month; syncMonthPicker(monthInput); }
  state.reportsBusy = true;
  renderReports();
  try {
    const data = await apiGet(`/api/reports/monthly?month=${encodeURIComponent(month)}`);
    state.reportsData = data;
    state.reportsMonth = data.month || month;
    if (monthInput && isValidMonthValue(state.reportsMonth)) { monthInput.value = state.reportsMonth; syncMonthPicker(monthInput); }
    setNotice(`Отчёт за ${state.reportsMonth} сформирован`, "ok");
  } catch (e) {
    console.error("[loadReports]", e);
    state.reportsData = null;
  } finally {
    state.reportsBusy = false;
    renderReports();
  }
}

async function copyReportsText() {
  const text = state.reportsData?.report?.text || $("reportsText")?.textContent || "";
  if (!text.trim()) return setNotice("Нет текста отчёта для копирования", "error");
  try {
    await navigator.clipboard.writeText(text);
    setNotice("Отчёт скопирован", "ok");
  } catch (_) {
    setNotice("Не удалось скопировать автоматически. Выделите текст вручную.", "error");
  }
}

function _childrenReportMonthLabel(month) {
  if (!month || !/^\d{4}-\d{2}$/.test(month)) return month || "";
  try {
    const [y, m] = month.split("-");
    return new Date(Number(y), Number(m) - 1, 1).toLocaleString("ru", { month: "long", year: "numeric" });
  } catch (_) { return month; }
}

function _fmtVisitDate(iso) {
  // "2026-06-03" → "03.06"
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso || "";
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
}

function _crLocRows(byLoc) {
  return (byLoc || []).map(l =>
    `<div class="cr-loc-row"><span class="cr-loc-name">${escapeHtml(l.location_name || l.location_code)}<span class="cr-loc-code">${escapeHtml(l.location_code)}</span></span><span class="cr-loc-n">${l.unique_children}</span></div>`
  ).join("");
}

function _crSection(headText, total, byLoc, noteHtml) {
  const rows = _crLocRows(byLoc);
  return `<div class="cr-section">
    <div class="cr-section-head">${escapeHtml(headText)}</div>
    <div class="cr-total"><span class="cr-total-label">Уникальных детей</span><span class="cr-total-n">${total}</span></div>
    ${rows ? `<div style="margin-top:5px">${rows}</div>` : ""}
    ${noteHtml || ""}
  </div>`;
}

function renderChildrenReport() {
  const el = $("childrenReportResult");
  if (!el) return;
  if (!canUseChildrenReport()) { el.innerHTML = `<div class="empty">Отчёт по детям недоступен для вашей роли.</div>`; return; }
  initMonthPicker($("childrenReportMonth"), state.childrenReportMonth);
  if (state.childrenReportBusy) {
    el.innerHTML = `<div class="reports-loading">Загружаю данные из МойКласс&hellip;</div>`;
    return;
  }
  const d = state.childrenReportData;
  if (!d) {
    el.innerHTML = `<div class="empty">Выберите месяц и нажмите «Показать отчёт».</div>`;
    return;
  }
  if (!d.ok) {
    el.innerHTML = `<div class="notice notice-error">${escapeHtml(d.error || "Ошибка получения данных")}</div>`;
    return;
  }

  const month = d.month || state.childrenReportMonth || "";
  const reg   = d.regular                           || { total_unique_children: d.total_unique_children ?? 0, by_location: d.by_location || [], children: d.children || [] };
  const sum   = d.city_program || d.summer           || { total_unique_children: 0, by_location: [], children: [], groups: [] };
  const comb  = d.combined                           || { total_unique_children: (d.total_unique_children ?? 0), by_location: d.by_location || [] };
  const mkp   = d.makeups  || { unique_children: 0, visit_records: 0, unique_lessons: 0, children: [] };
  const trl   = d.trials   || { unique_children: 0, visit_records: 0, unique_lessons: 0, children: [] };
  const ovl   = d.overlaps || { regular_and_city_program_children: 0, multi_location_children: 0 };
  const excl  = d.excluded  || {};
  const diag  = d.diagnostics || {};
  const role  = state.me?.role || "";
  const isOwnerAdmin = ["owner", "admin", "director", "operations", "client_manager"].includes(role);

  const regGroups      = Array.isArray(reg.by_group)           ? reg.by_group           : [];
  const sumGroups2     = Array.isArray(sum.by_group)           ? sum.by_group           : [];
  const regGroupsByLoc = Array.isArray(reg.groups_by_location) ? reg.groups_by_location : [];
  const sumGroupsByLoc = Array.isArray(sum.groups_by_location) ? sum.groups_by_location : [];

  // ── Revenue ──
  const rev        = d.revenue || {};
  const revReg     = rev.regular || {};
  const revPay     = rev.payments || {};
  const revGroups  = Array.isArray(revReg.by_group) ? revReg.by_group : [];
  const revPrices  = rev.prices || {};
  const fmtByn = n => (n == null ? "—" : Number(n).toLocaleString("ru-RU", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " BYN");

  // ── Exclusions note ──
  const exclParts = [];
  if (excl.trial)     exclParts.push(`пробных: ${excl.trial}`);
  if (excl.makeup)    exclParts.push(`отработок: ${excl.makeup}`);
  if (excl.cancelled) exclParts.push(`отменённых: ${excl.cancelled}`);
  const exclNote = exclParts.length
    ? `<p class="cr-excl">Не учтено: ${escapeHtml(exclParts.join("; "))}.</p>` : "";

  const dedupeNote = `<p class="cr-note">В разбивке по филиалам ребёнок может учитываться в нескольких, если посещал разные места.</p>`;

  // ── Summer groups note ──
  const sumGroups = Array.isArray(sum.groups) ? sum.groups : [];
  const sumGroupsNote = sum.total_unique_children > 0
    ? `${sumGroups.length ? `<p class="cr-groups">Группы: ${escapeHtml(sumGroups.join("; "))}.</p>` : ""}
       <p class="cr-note">Если неделя городской программы началась в конце предыдущего месяца (напр. 29.06–03.07), она учитывается в том месяце, на который выпадают фактические посещения.</p>`
    : "";

  // ── Child row renderer ──
  const _mkChildRow = (c, tag) => {
    const locNames = (c.location_names || c.locations || []).join(", ");
    const grps = (c.groups || []).join("; ");
    const dates = (c.visit_dates || []).map(_fmtVisitDate).join(", ");
    return `<div class="cr-child">
      <div class="cr-child-name">${escapeHtml(c.name)}${tag} <span style="font-weight:400;font-size:10px;color:var(--muted)">${c.visits_count} пос.</span></div>
      <div class="cr-child-meta">${escapeHtml(locNames)}${grps ? " · " + escapeHtml(grps) : ""}</div>
      ${dates ? `<div class="cr-child-dates">${escapeHtml(dates)}</div>` : ""}
    </div>`;
  };

  // ── Separate verification lists: regular and city_program ──
  const SHOW_FIRST = 100;
  const regChildren = Array.isArray(reg.children) ? [...reg.children].sort((a, b) => a.name.localeCompare(b.name, "ru")) : [];
  const sumChildren = Array.isArray(sum.children) ? [...sum.children].sort((a, b) => a.name.localeCompare(b.name, "ru")) : [];

  const regVerifyHtml = regChildren.length ? `
    <details class="cr-verify">
      <summary>Проверочный список: регулярные занятия (${regChildren.length})</summary>
      <div style="margin-top:6px">${regChildren.slice(0, SHOW_FIRST).map(c => _mkChildRow(c, "")).join("")}</div>
      ${regChildren.length > SHOW_FIRST ? `<p class="cr-note">+ ещё ${regChildren.length - SHOW_FIRST} детей.</p>` : ""}
    </details>` : "";

  const sumVerifyHtml = sumChildren.length ? `
    <details class="cr-verify">
      <summary>Проверочный список: городская программа (${sumChildren.length})</summary>
      <div style="margin-top:6px">${sumChildren.slice(0, SHOW_FIRST).map(c => _mkChildRow(c, `<span style="font-size:9px;color:var(--muted);margin-left:3px">ГП</span>`)).join("")}</div>
      ${sumChildren.length > SHOW_FIRST ? `<p class="cr-note">+ ещё ${sumChildren.length - SHOW_FIRST} детей.</p>` : ""}
    </details>` : "";

  // ── Diagnostics (owner/admin only) ──
  let diagHtml = "";
  if (isOwnerAdmin && diag.lesson_records_loaded != null) {
    const srcMap = diag.location_sources || {};
    const srcRows = Object.entries(srcMap).map(([k, v]) =>
      `<div class="cr-diag-row"><span>${escapeHtml(k)}</span><b>${v}</b></div>`
    ).join("");
    const de = diag.excluded || {};
    const cpExamples = Array.isArray(diag.city_program_matched_examples) ? diag.city_program_matched_examples : [];
    const cpExHtml = cpExamples.length
      ? `<div style="margin-top:3px;font-size:10px;color:var(--muted)">${cpExamples.map(e => escapeHtml(e)).join("<br>")}</div>` : "";

    // Raw lesson examples: show first 5 for quick structure inspection
    const rawEx = Array.isArray(diag.raw_lesson_examples) ? diag.raw_lesson_examples.slice(0, 5) : [];
    const rawExHtml = rawEx.map(ex => {
      const tf = ex.text_fields || {};
      const nonEmpty = Object.entries(tf).filter(([, v]) => v).map(([k, v]) => `${k}: ${escapeHtml(String(v).slice(0, 80))}`).join("<br>");
      return `<div style="margin:4px 0;padding:4px;background:rgba(0,0,0,.04);border-radius:4px;font-size:10px">
        <b>${escapeHtml(ex.date || "?")} · ${escapeHtml(ex.student_name || "?")} · classId=${escapeHtml(ex.class_id || "—")}</b><br>
        class_name_from_map: <b>${escapeHtml(ex.class_name_from_map || "—")}</b> · is_summer: <b>${ex.is_summer}</b><br>
        group_name: ${escapeHtml(ex.group_name_resolved || "—")}<br>
        filialId: <b>${escapeHtml(String(ex.filial_id || "—"))}</b> → <b>${escapeHtml(String(ex.filial_name_resolved || "—"))}</b> | roomId: <b>${escapeHtml(String(ex.room_id || "—"))}</b> → <b>${escapeHtml(String(ex.room_name_resolved || "—"))}</b><br>
        loc: <b>${escapeHtml(ex.loc_code || "—")}</b> via <b>${escapeHtml(ex.loc_src || "—")}</b><br>
        ${nonEmpty ? `<details><summary style="cursor:pointer;color:var(--accent)">text_fields</summary>${nonEmpty}</details>` : ""}
        <details><summary style="cursor:pointer;color:var(--accent)">rec_keys</summary>${escapeHtml((ex.record_keys || []).join(", "))}</details>
      </div>`;
    }).join("");

    diagHtml = `
      <details class="cr-diag">
        <summary>Диагностика</summary>
        <div style="margin-top:4px">
          <div class="cr-diag-row"><span>Записей загружено</span><b>${diag.lesson_records_loaded}</b></div>
          <div class="cr-diag-row"><span>С visit=true</span><b>${diag.present_records}</b></div>
          <div class="cr-diag-row"><span>Regular records</span><b>${diag.regular_records ?? "—"}</b></div>
          <div class="cr-diag-row"><span>City program records</span><b>${diag.city_program_records ?? "—"}</b></div>
          <div class="cr-diag-row"><span>Рег. групп (unique)</span><b>${diag.regular_unique_groups ?? "—"}</b></div>
          <div class="cr-diag-row"><span>Групп ГП (unique)</span><b>${diag.city_program_unique_groups ?? "—"}</b></div>
          <div class="cr-diag-row"><span>Групп всего (unique)</span><b>${diag.combined_unique_groups ?? "—"}</b></div>
          <div class="cr-diag-row"><span>Рег. без филиала (записи)</span><b>${diag.regular_unknown_location_records ?? 0}</b></div>
          <div class="cr-diag-row"><span>Рег. без филиала (группы)</span><b>${diag.regular_unknown_location_groups ?? 0}</b></div>
          <div class="cr-diag-row"><span>Карта классов</span><b>${diag.classes_map_size ?? "?"} зап.</b></div>
          <div class="cr-diag-row"><span>Карта филиалов</span><b>${diag.filial_map_size ?? "?"} зап.</b></div>
          <div class="cr-diag-row"><span>Карта кабинетов</span><b>${diag.rooms_map_size ?? "?"} зап.</b></div>
          <div class="cr-diag-row"><span>Без филиала (всего записей)</span><b>${diag.unknown_location_records ?? 0}</b></div>
          ${(() => {
            const rl = diag.regular_location_sources || {};
            return Object.keys(rl).length
              ? `<div class="cr-diag-row" style="margin-top:4px;font-weight:700"><span>Источник филиала (рег.)</span></div>` +
                Object.entries(rl).map(([k, v]) => `<div class="cr-diag-row"><span>${escapeHtml(k)}</span><b>${v}</b></div>`).join("")
              : "";
          })()}
          ${(() => {
            const ex = Array.isArray(diag.examples_unknown_regular_groups) ? diag.examples_unknown_regular_groups : [];
            if (!ex.length) return "";
            return `<div class="cr-diag-row" style="margin-top:4px;font-weight:700"><span>Группы без филиала (примеры)</span></div>` +
              ex.map(g => `<div style="margin:3px 0;padding:4px;background:rgba(200,0,0,.06);border-radius:4px;font-size:10px">
                <b>${escapeHtml(g.group_name || g.group_key || "—")}</b> · ${g.unique_children} дет.<br>
                filialId: <b>${escapeHtml(String(g.filial_id || "—"))}</b> · filialName: <b>${escapeHtml(String(g.filial_name || "—"))}</b><br>
                roomId: <b>${escapeHtml(String(g.room_id || "—"))}</b> · roomName: <b>${escapeHtml(String(g.room_name || "—"))}</b><br>
                loc_src: <b>${escapeHtml(g.loc_src || "—")}</b>
              </div>`).join("");
          })()}
          ${de.trial != null ? `<div class="cr-diag-row"><span>Исключено пробных</span><b>${de.trial}</b></div>` : ""}
          ${de.makeup != null ? `<div class="cr-diag-row"><span>Исключено отработок</span><b>${de.makeup}</b></div>` : ""}
          ${de.cancelled != null ? `<div class="cr-diag-row"><span>Исключено отменённых</span><b>${de.cancelled}</b></div>` : ""}
          ${cpExamples.length ? `<div class="cr-diag-row" style="margin-top:4px;font-weight:700"><span>Городская программа примеры</span></div>${cpExHtml}` : ""}
          ${rawEx.length ? `<div class="cr-diag-row" style="margin-top:6px;font-weight:700"><span>Raw lesson examples</span></div>${rawExHtml}` : ""}
          ${srcRows ? `<div class="cr-diag-row" style="margin-top:4px;font-weight:700"><span>Источник филиала</span></div>${srcRows}` : ""}
          ${diag.payments_loaded != null ? `
            <div class="cr-diag-row" style="margin-top:6px;font-weight:700"><span>Оплаты (filialId-метод)</span></div>
            <div class="cr-diag-row"><span>Загружено</span><b>${diag.payments_loaded} (дох. ${diag.payments_income_count ?? "—"} / возвр. ${diag.payments_refund_count ?? "—"})</b></div>
            <div class="cr-diag-row"><span>Брутто / возвраты / нетто</span><b>${diag.payments_total_gross ?? "—"} / ${diag.payments_total_refunds ?? "—"} / ${diag.payments_total_net ?? "—"} BYN</b></div>
            <div class="cr-diag-row"><span>filialId: есть / ок / неизв / нет</span><b>${diag.payments_filial_present ?? 0} / ${diag.payments_filial_ok ?? 0} / ${diag.payments_filial_unknown ?? 0} / ${diag.payments_filial_missing ?? 0}</b></div>
            <div class="cr-diag-row"><span>Fallback по userId</span><b>${diag.payments_fallback_uid ?? 0}</b></div>
            <div class="cr-diag-row"><span>Не распределено</span><b>${diag.payments_unallocated_amount ?? 0} BYN · ${diag.payments_unallocated_count ?? 0} опл.</b></div>
          ` : ""}
          ${diag.workoff_revenue_enabled ? `
            <div class="cr-diag-row" style="margin-top:6px;font-weight:700"><span>Отработки (workoff_revenue)</span></div>
            <div class="cr-diag-row"><span>Метод</span><b>${diag.workoff_detection_method ?? "—"}</b></div>
            <div class="cr-diag-row"><span>Записей просканировано</span><b>${diag.workoff_records_scanned ?? 0}</b></div>
            <div class="cr-diag-row"><span>Всего визитов</span><b>${diag.workoff_visits_total ?? 0}</b></div>
            <div class="cr-diag-row"><span>paid=true / false / ? / free</span><b>${diag.workoff_paid_true_count ?? 0} / ${diag.workoff_paid_false_count ?? 0} / ${diag.workoff_paid_unknown_count ?? 0} / ${diag.workoff_free_true_count ?? 0}</b></div>
            <div class="cr-diag-row"><span>Включено / исключено</span><b>${diag.workoff_included_count ?? 0} / ${diag.workoff_excluded_count ?? 0}</b></div>
            <div class="cr-diag-row"><span>Не распределено</span><b>${diag.workoff_unallocated_count ?? 0}</b></div>
            <div class="cr-diag-row"><span>Расчётная стоимость</span><b>${diag.workoff_estimated_total ?? 0} BYN</b></div>
          ` : ""}
          ${(() => {
            const cf = d.client_flow || {};
            if (!cf.source) return "";
            return `
              <div class="cr-diag-row" style="margin-top:6px;font-weight:700"><span>Приток / отток (client_flow)</span></div>
              <div class="cr-diag-row"><span>Источник</span><b>${escapeHtml(cf.source || "—")}</b></div>
              <div class="cr-diag-row"><span>Метод</span><b>${escapeHtml(cf.method || "—")}</b></div>
              <div class="cr-diag-row"><span>stateChangedAt</span><b>${diag.client_flow_state_changed_at_from ?? "—"} → ${diag.client_flow_state_changed_at_to ?? "—"}</b></div>
              <div class="cr-diag-row"><span>Карта статусов</span><b>${diag.client_flow_status_map_size ?? "—"} зап.</b></div>
              <div class="cr-diag-row"><span>Пользователей загружено</span><b>${diag.client_flow_users_loaded ?? "—"}</b></div>
              <div class="cr-diag-row"><span>Статусы ОК</span><b>${diag.client_flow_statuses_loaded ? "да" : "нет"}</b></div>
              <div class="cr-diag-row"><span>Пользователи ОК</span><b>${diag.client_flow_users_ok ? "да" : "нет"}</b></div>
              <div class="cr-diag-row"><span>Полная история</span><b>${cf.status_history_full_available ? "да" : "нет"}</b></div>
              ${(diag.client_flow_unknown_status_ids || []).length > 0 ? `<div class="cr-diag-row"><span>Неизвестные статус-id</span><b>${(diag.client_flow_unknown_status_ids || []).join(", ")}</b></div>` : ""}`;
          })()}
        </div>
      </details>`;
  }

  // ── Overlaps HTML ──
  const ovlHtml = (ovl.regular_and_city_program_children > 0 || ovl.multi_location_children > 0) ? `
    <div class="cr-overlap">
      <p class="cr-overlap-head">Пересечения</p>
      ${ovl.regular_and_city_program_children > 0
        ? `<div class="cr-overlap-row"><span>Рег. + городская программа</span><span class="cr-overlap-n">${ovl.regular_and_city_program_children} дет.</span></div>`
        : ""}
      ${ovl.multi_location_children > 0
        ? `<div class="cr-overlap-row"><span>Несколько филиалов</span><span class="cr-overlap-n">${ovl.multi_location_children} дет.</span></div>`
        : ""}
      <p class="cr-note">Поэтому сумма по блокам может отличаться от общего итога.</p>
    </div>` : "";

  el.innerHTML = `
    <div class="cr-card" style="margin-top:10px">
      <div class="cr-title">Дети за ${escapeHtml(_childrenReportMonthLabel(month))}</div>

      <div class="cr-tiles">
        <div class="cr-tile"><span class="cr-tile-n">${reg.total_unique_children}</span><span class="cr-tile-label">Регулярные дети</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${reg.total_unique_groups ?? regGroups.length}</span><span class="cr-tile-label">Рег. групп</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${sum.total_unique_children}</span><span class="cr-tile-label">Гор. программа</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${sum.total_unique_groups ?? sumGroups2.length}</span><span class="cr-tile-label">Групп ГП</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${comb.total_unique_children}</span><span class="cr-tile-label">Итого</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${mkp.unique_children}</span><span class="cr-tile-label">Дети на отработках</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${mkp.visit_records}</span><span class="cr-tile-label">Отработок посещ.</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${trl.unique_lessons || trl.visit_records}</span><span class="cr-tile-label">Пробных занятий</span></div>
        <div class="cr-tile"><span class="cr-tile-n">${trl.unique_children}</span><span class="cr-tile-label">Детей на пробных</span></div>
        ${excl.cancelled ? `<div class="cr-tile"><span class="cr-tile-n">${excl.cancelled}</span><span class="cr-tile-label">Отменённых</span></div>` : ""}
      </div>

      ${_crSection("Регулярные занятия", reg.total_unique_children, reg.by_location, exclNote + dedupeNote)}

      ${(() => {
        const byLocHtml = regGroupsByLoc.map(l =>
          `<div class="cr-loc-row"><span>${escapeHtml(l.location_name)} / ${escapeHtml(l.location_code)}</span><b>${l.unique_groups} гр.</b></div>`
        ).join("");
        const total = reg.total_unique_groups ?? regGroups.length;
        return total > 0 ? `
          <div class="cr-groups-block">
            <div class="cr-groups-block-title">Регулярные группы по филиалам</div>
            ${byLocHtml}
            <div class="cr-groups-total">Итого регулярных групп: <b>${total}</b></div>
            <p class="cr-note">Группа считается один раз, даже если занималась несколько раз. В разбивке по филиалам группа может учитываться в нескольких, если проходила в разных местах.</p>
          </div>` : "";
      })()}

      ${sum.total_unique_children > 0
        ? _crSection("Городская программа / Summer Week", sum.total_unique_children, sum.by_location, sumGroupsNote + dedupeNote)
        : ""}

      ${(() => {
        if (sum.total_unique_children === 0) return "";
        const byLocHtml = sumGroupsByLoc.map(l =>
          `<div class="cr-loc-row"><span>${escapeHtml(l.location_name)} / ${escapeHtml(l.location_code)}</span><b>${l.unique_groups} гр.</b></div>`
        ).join("");
        const total = sum.total_unique_groups ?? sumGroups2.length;
        return total > 0 ? `
          <div class="cr-groups-block">
            <div class="cr-groups-block-title">Группы городской программы по филиалам</div>
            ${byLocHtml}
            <div class="cr-groups-total">Итого групп ГП: <b>${total}</b></div>
            <p class="cr-note">Группа считается один раз, даже если занималась несколько раз.</p>
          </div>` : "";
      })()}

      ${_crSection(
        sum.total_unique_children > 0 ? "Общий итог (рег. + городская прогр.)" : "Итого",
        comb.total_unique_children,
        comb.by_location,
        `<p class="cr-note">Один ребёнок считается один раз в общем итоге.</p>`
      )}

      ${ovlHtml}

      ${regVerifyHtml}

      ${(() => {
        const _mkGroupRow = g => `<div class="cr-group-row">
          <div class="cr-group-name">${escapeHtml(g.group_name || g.group_id || "—")}</div>
          <div class="cr-group-meta">${escapeHtml(g.location_name || g.location_code || "—")} · ${g.lessons_count || 0} зан. · ${g.unique_children} дет.</div>
        </div>`;
        return regGroups.length ? `
          <details class="cr-verify">
            <summary>Проверочный список групп: регулярные (${regGroups.length})</summary>
            <div style="margin-top:6px">${regGroups.map(_mkGroupRow).join("")}</div>
          </details>` : "";
      })()}

      ${sumVerifyHtml}

      ${(() => {
        const _mkGroupRow = g => `<div class="cr-group-row">
          <div class="cr-group-name">${escapeHtml(g.group_name || g.group_id || "—")}</div>
          <div class="cr-group-meta">${escapeHtml(g.location_name || g.location_code || "—")} · ${g.lessons_count || 0} зан. · ${g.unique_children} дет.</div>
        </div>`;
        return sumGroups2.length ? `
          <details class="cr-verify">
            <summary>Проверочный список групп: городская программа (${sumGroups2.length})</summary>
            <div style="margin-top:6px">${sumGroups2.map(_mkGroupRow).join("")}</div>
          </details>` : "";
      })()}

      ${(() => {
        // ── Revenue block ──────────────────────────────────────────────────
        const forecastByLoc = Array.isArray(rev.forecast_by_location) ? rev.forecast_by_location : [];
        const forecastTotal = rev.forecast_total || {};
        const actualByLoc   = Array.isArray(rev.actual_payments_by_location) ? rev.actual_payments_by_location : [];
        const mapAvail      = rev.actual_payments_mapping_available;
        const actTotal      = rev.actual_payments_total;
        const actUnalloc    = rev.actual_payments_unallocated;
        const payAvail      = revPay.available;
        const woffRev       = rev.workoff_revenue || {};
        const rwo           = rev.revenue_with_workoffs || {};

        if (forecastByLoc.length === 0 && revGroups.length === 0) return "";

        const _onlinePpl  = (revPrices.YC0 || {}).per_lesson ?? 52.50;
        const _offlinePpl = (revPrices.YC1 || {}).per_lesson ?? 59.75;
        const _offlineSub = (revPrices.YC1 || {}).subscription ?? 239;
        const _onlineSub  = (revPrices.YC0 || {}).subscription ?? 210;
        const les4 = rev.lessons_in_subscription ?? 4;

        // ── Forecast by location table ──
        const forecastRows = forecastByLoc.map(loc => `
          <div class="cr-rev-loc-row">
            <div class="cr-rev-loc-name">${escapeHtml(loc.location_name)} <span class="cr-rev-loc-code">${escapeHtml(loc.location_code)}</span></div>
            <div class="cr-rev-loc-meta">${loc.regular_groups} гр. · ${loc.unique_children} дет. · ${fmtByn(loc.price_per_lesson)}/зан.</div>
            <div class="cr-rev-loc-money">
              <span>По факт. посещ.:</span> <b>${fmtByn(loc.actual_visits_forecast)}</b>
              &nbsp;&nbsp;<span>По плану:</span> <b>${fmtByn(loc.planned_visits_forecast)}</b>
            </div>
          </div>`).join("");

        const forecastTotalRow = forecastTotal.actual_visits_forecast != null ? `
          <div class="cr-rev-loc-row cr-rev-loc-total">
            <div class="cr-rev-loc-name">Общий прогноз</div>
            <div class="cr-rev-loc-money">
              <span>По факт. посещ.:</span> <b>${fmtByn(forecastTotal.actual_visits_forecast)}</b>
              &nbsp;&nbsp;<span>По плану:</span> <b>${fmtByn(forecastTotal.planned_visits_forecast)}</b>
            </div>
          </div>` : "";

        // ── Actual payments by location ──
        let actualHtml = "";
        if (!payAvail) {
          actualHtml = `<p class="cr-note" style="color:var(--warn)">Фактические оплаты временно недоступны: ${escapeHtml(revPay.error || "нет данных от МойКласс")}.</p>`;
        } else if (mapAvail && actualByLoc.length > 0) {
          const hasRefunds = actualByLoc.some(loc => (loc.refunds ?? 0) > 0);
          const actualRows = actualByLoc.map(loc => {
            const gross = loc.gross_income ?? loc.actual_paid;
            const refunds = loc.refunds ?? 0;
            const net = loc.actual_paid;
            const cntStr = (loc.payments_count || 0) + " оплат" + ((loc.refunds_count || 0) > 0 ? ` · ${loc.refunds_count} возврат` : "");
            const refPart = hasRefunds && refunds > 0
              ? `<span>${fmtByn(gross)}</span> <span style="color:var(--warn)">−${fmtByn(refunds)}</span> → `
              : "";
            return `
              <div class="cr-rev-loc-row">
                <div class="cr-rev-loc-name">${escapeHtml(loc.location_name)} <span class="cr-rev-loc-code">${escapeHtml(loc.location_code)}</span></div>
                <div class="cr-rev-loc-meta">${cntStr}</div>
                <div class="cr-rev-loc-money">${refPart}<b>${fmtByn(net)}</b></div>
              </div>`;
          }).join("");
          const grossTotal = rev.actual_payments_gross_income ?? actTotal;
          const refundsTotal = rev.actual_payments_refunds ?? 0;
          const netTotal = rev.actual_payments_net_income ?? actTotal;
          const unallocRow = (actUnalloc != null && actUnalloc > 0) ? `
            <div class="cr-rev-loc-row">
              <div class="cr-rev-loc-name cr-rev-neg">Не распределено</div>
              <div class="cr-rev-loc-money cr-rev-neg"><b>${fmtByn(actUnalloc)}</b></div>
            </div>` : "";
          const totalRefPart = refundsTotal > 0
            ? `<span>${fmtByn(grossTotal)}</span> <span style="color:var(--warn)">−${fmtByn(refundsTotal)}</span> → `
            : "";
          actualHtml = `
            <div class="cr-rev-by-loc">
              ${actualRows}
              ${unallocRow}
              <div class="cr-rev-loc-row cr-rev-loc-total">
                <div class="cr-rev-loc-name">Общий факт</div>
                <div class="cr-rev-loc-money">${totalRefPart}<b>${fmtByn(netTotal)}</b></div>
              </div>
            </div>
            <p class="cr-note">Метод: filialId из платежей МойКласс → ученик из регулярных занятий (если нет filialId). Разница ориентировочная.</p>`;
        } else {
          actualHtml = `
            <p class="cr-note">Фактические оплаты из МойКласс сейчас доступны только общим итогом. В API платежей нет надёжной связи с учебным классом.</p>
            <div class="cr-rev-by-loc">
              <div class="cr-rev-loc-row cr-rev-loc-total">
                <div class="cr-rev-loc-name">Общий факт</div>
                <div class="cr-rev-loc-money"><b>${fmtByn(actTotal)}</b></div>
              </div>
            </div>`;
        }

        // ── Group detail rows (renamed) ──
        const revGroupRows = revGroups.map(g => {
          const plan = g.planned_student_visits ?? (g.unique_children * (g.lessons_count || 0));
          const pplLabel = g.price_per_lesson != null ? fmtByn(g.price_per_lesson) + "/зан." : "";
          return `<div class="cr-rev-group">
            <div class="cr-rev-group-name">${escapeHtml(g.group_name || g.group_id || "—")}</div>
            <div class="cr-rev-group-loc">${escapeHtml(g.location_name || g.location_code || "—")}${pplLabel ? ` · <span style="color:var(--muted)">${pplLabel}</span>` : ""}</div>
            <div class="cr-rev-group-stats">${g.unique_children} дет. · ${g.lessons_count || 0} зан. · ${g.actual_visit_records || 0} факт. посещ. · ${plan} план. посещ.</div>
            <div class="cr-rev-group-money">
              <span class="cr-rev-group-label">Прогноз (факт.):</span> <b>${fmtByn(g.forecast_revenue)}</b>
              &nbsp;&nbsp;<span class="cr-rev-group-label">Прогноз (план.):</span> <b>${fmtByn(g.forecast_revenue_planned)}</b>
            </div>
          </div>`;
        }).join("");

        return `
          <div class="cr-revenue-block">
            <div class="cr-revenue-title">Выручка по учебным классам</div>
            <p class="cr-note">Только регулярные занятия. Городская программа, пробные и отработки — не включены.<br>Онлайн (YC0): ${_onlineSub} BYN / ${les4} зан. = ${fmtByn(_onlinePpl)}/зан. &nbsp;·&nbsp; Офлайн (YC1/YC2): ${_offlineSub} BYN / ${les4} зан. = ${fmtByn(_offlinePpl)}/зан.</p>

            <div class="cr-rev-section-title">Прогнозируемая выручка</div>
            <div class="cr-rev-by-loc">
              ${forecastRows}
              ${forecastTotalRow}
            </div>

            ${(() => {
              // ── Workoff revenue section ──
              if ((woffRev.total_workoff_visits || 0) === 0 && rwo.workoffs_estimated == null) return "";
              const woffByLoc = woffRev.by_location || {};
              const _actByLocMap = {};
              actualByLoc.forEach(a => { _actByLocMap[a.location_code] = a; });
              const combinedRows = forecastByLoc.map(loc => {
                const wEst  = loc.workoff_estimated ?? 0;
                const wVis  = loc.workoff_visits ?? 0;
                const reg   = loc.actual_visits_forecast ?? 0;
                const total = loc.actual_with_workoffs ?? (reg + wEst);
                const actLoc = _actByLocMap[loc.location_code];
                const actNet = actLoc ? (actLoc.actual_paid ?? null) : null;
                const delta  = actNet != null ? Math.round((actNet - total) * 100) / 100 : null;
                const deltaStr = delta == null ? "—" : (delta > 0 ? `+${fmtByn(delta)}` : fmtByn(delta));
                const deltaCss = delta == null ? "" : (delta > 0 ? "color:#27ae60" : delta < 0 ? "color:#c0392b" : "");
                return `
                  <div class="cr-rev-loc-row">
                    <div class="cr-rev-loc-name">${escapeHtml(loc.location_name)} <span class="cr-rev-loc-code">${escapeHtml(loc.location_code)}</span></div>
                    <div class="cr-rev-loc-money">
                      <span>Рег.:</span> <b>${fmtByn(reg)}</b>
                      &nbsp;+&nbsp;<span>Отр.:</span> <b>${fmtByn(wEst)}</b><span style="color:var(--muted)"> (${wVis} виз.)</span>
                      → <b>${fmtByn(total)}</b>
                      ${actNet != null ? `&nbsp;|&nbsp;<span>Факт:</span> <b>${fmtByn(actNet)}</b> &nbsp;<span style="${deltaCss}">Δ ${deltaStr}</span>` : ""}
                    </div>
                  </div>`;
              }).join("");
              const rwoPlanned = rwo.planned_with_workoffs;
              const rwoActNet  = rwo.actual_payments_net;
              const rwoDelta   = rwo.delta_actual_vs_planned_with_workoffs;
              const rwoDeltaStr = rwoDelta == null ? "—" : (rwoDelta > 0 ? `+${fmtByn(rwoDelta)}` : fmtByn(rwoDelta));
              const rwoDeltaCss = rwoDelta == null ? "" : (rwoDelta > 0 ? "color:#27ae60" : rwoDelta < 0 ? "color:#c0392b" : "");
              const totalRow = `
                <div class="cr-rev-loc-row cr-rev-loc-total">
                  <div class="cr-rev-loc-name">Итого с отработками</div>
                  <div class="cr-rev-loc-money">
                    <span>Рег.:</span> <b>${fmtByn(rwo.regular_actual_visits_forecast)}</b>
                    &nbsp;+&nbsp;<span>Отр.:</span> <b>${fmtByn(rwo.workoffs_estimated)}</b><span style="color:var(--muted)"> (${woffRev.total_workoff_visits} виз.)</span>
                    → <b>${fmtByn(rwoPlanned)}</b>
                    ${rwoActNet != null ? `&nbsp;|&nbsp;<span>Факт:</span> <b>${fmtByn(rwoActNet)}</b> &nbsp;<span style="${rwoDeltaCss}">Δ ${rwoDeltaStr}</span>` : ""}
                  </div>
                </div>`;
              // Verify list
              const woffIncl = Array.isArray(woffRev.included_records) ? woffRev.included_records : [];
              const woffVerify = woffIncl.length ? `
                <details class="cr-verify" style="margin-top:6px">
                  <summary>Отработки в расчёте (${woffRev.included_count ?? woffIncl.length})</summary>
                  <div style="margin-top:4px">
                    ${woffIncl.slice(0, 100).map(r => `
                      <div class="cr-verify-row" style="font-size:11px;padding:3px 0;border-bottom:1px solid var(--line)">
                        <span style="color:var(--muted)">${escapeHtml(r.date || "—")}</span>
                        <b>${escapeHtml(r.student_name || "—")}</b>
                        · ${escapeHtml(r.location || "—")}
                        · ${escapeHtml(r.group || "—")}
                        · <span style="color:var(--muted)">${r.paid === true ? "paid" : r.paid === false ? "paid=false" : "paid=?"}</span>
                        · <b>${fmtByn(r.price)}</b>
                      </div>`).join("")}
                    ${woffIncl.length > 100 ? `<div class="cr-note" style="margin-top:4px">Показано 100 из ${woffRev.included_count ?? woffIncl.length}. Полный список в диагностике.</div>` : ""}
                  </div>
                </details>` : "";
              return `
                <div class="cr-rev-section-title">Расчётная выручка с отработками</div>
                <div class="cr-rev-by-loc">
                  ${combinedRows}
                  ${totalRow}
                </div>
                <p class="cr-note">Отработки добавлены в расчётную стоимость занятий месяца. Факт оплат — реальные платежи по дате оплаты, поэтому разница остаётся ориентировочной. Бесплатные отработки и paid=false не включены в стоимость.</p>
                ${woffVerify}`;
            })()}

            <div class="cr-rev-section-title">Фактическая выручка</div>
            ${actualHtml}

            <p class="cr-note" style="margin-top:8px;opacity:.7">Разница ориентировочная — платежи в МойКласс могут относиться к предоплатам, долгам или другим услугам.</p>

            ${revGroups.length ? `
            <details class="cr-verify" style="margin-top:12px">
              <summary>Детализация прогноза по группам (${revGroups.length})</summary>
              <div style="margin-top:6px">${revGroupRows}</div>
            </details>` : ""}
          </div>`;
      })()}

      ${(() => {
        // ── Client flow block (v7.0.64 — stateChangedAt) ─────────────────────
        const cf = d.client_flow || {};
        if (!cf.source) return "";

        const sm   = cf.summary || {};
        const items = cf.items  || {};
        const avail = cf.data_available;

        // Helper: render a verify-list details block for one category
        const _cfList = (title, arr) => {
          if (!Array.isArray(arr) || arr.length === 0) return "";
          const rows = arr.map(u => {
            const dt  = (u.state_changed_at || "").slice(0, 10);
            const nm  = escapeHtml(u.name || `id:${u.user_id || "?"}`);
            const st  = escapeHtml(u.client_state_name || u.slug || "—");
            return `<div class="cr-group-row">
              <div class="cr-group-name">${nm}</div>
              <div class="cr-group-meta">${st}${dt ? " · " + dt : ""}</div>
            </div>`;
          }).join("");
          return `<details class="cr-verify" style="margin-top:6px">
            <summary>${escapeHtml(title)} (${arr.length})</summary>
            <div style="margin-top:4px">${rows}</div>
          </details>`;
        };

        const net    = sm.net_growth ?? 0;
        const netCls = net > 0 ? " positive" : net < 0 ? " negative" : "";
        const netStr = net > 0 ? `+${net}` : String(net);

        const tilesHtml = `
          <div class="cr-cf-tiles">
            <div class="cr-cf-tile"><span class="cr-cf-tile-n positive">+${sm.new_clients ?? 0}</span><span class="cr-cf-tile-label">Новых клиентов</span></div>
            <div class="cr-cf-tile"><span class="cr-cf-tile-n negative">−${sm.churned_clients ?? 0}</span><span class="cr-cf-tile-label">Ушло клиентов</span></div>
            <div class="cr-cf-tile"><span class="cr-cf-tile-n${netCls}">${netStr}</span><span class="cr-cf-tile-label">Чистый прирост</span></div>
            <div class="cr-cf-tile"><span class="cr-cf-tile-n">${sm.trial ?? 0}</span><span class="cr-cf-tile-label">Пробных</span></div>
            <div class="cr-cf-tile"><span class="cr-cf-tile-n">${sm.refused ?? 0}</span><span class="cr-cf-tile-label">Отказов</span></div>
            <div class="cr-cf-tile"><span class="cr-cf-tile-n">${sm.bad_leads ?? 0}</span><span class="cr-cf-tile-label">Некач. лидов</span></div>
            <div class="cr-cf-tile"><span class="cr-cf-tile-n">${sm.deciding ?? 0}</span><span class="cr-cf-tile-label">Принимают решение</span></div>
            <div class="cr-cf-tile"><span class="cr-cf-tile-n">${sm.new_leads ?? 0}</span><span class="cr-cf-tile-label">Новых лидов</span></div>
          </div>`;

        const verifyHtml = avail ? [
          _cfList("Новые клиенты", items.new_clients),
          _cfList("Ушли клиенты", items.churned_clients),
          _cfList("Пробные", items.trial),
          _cfList("Отказы", items.refused),
          _cfList("Некачественные лиды", items.bad_leads),
          _cfList("Принимают решение", items.deciding),
          _cfList("Новые лиды", items.new_leads),
        ].join("") : "";

        const noDataHtml = !avail
          ? `<p class="cr-note">МойКласс не вернул пользователей с фильтром stateChangedAt. Возможно, этот параметр не поддерживается в данной версии API.</p>`
          : "";

        return `
          <div class="cr-cf-block">
            <div class="cr-cf-title">Приток / отток</div>
            <p class="cr-note cr-cf-note">По последнему изменению статуса в МойКласс за выбранный месяц (stateChangedAt). Если клиент менял статус несколько раз — учитывается только последний текущий. Полная история переходов недоступна.</p>
            ${noDataHtml}
            ${avail ? tilesHtml : ""}
            ${verifyHtml}
          </div>`;
      })()}

      ${diagHtml}

      <div style="margin-top:12px">
        <button class="secondary" id="copyChildrenReport" style="font-size:13px" type="button">Скопировать отчёт</button>
      </div>
    </div>`;

  $("copyChildrenReport")?.addEventListener("click", copyChildrenReport);
}

async function loadChildrenReport() {
  if (!canUseChildrenReport()) return;
  const monthInput = $("childrenReportMonth");
  const month = monthInput?.value || state.childrenReportMonth || currentMonthValue();
  state.childrenReportMonth = month;
  if (monthInput) { monthInput.value = month; syncMonthPicker(monthInput); }
  state.childrenReportBusy = true;
  state.childrenReportData = null;
  renderChildrenReport();
  try {
    const data = await apiGet(`/api/reports/monthly-children?month=${encodeURIComponent(month)}`);
    state.childrenReportData = data;
    setNotice(`Отчёт по детям за ${month} готов`, "ok");
  } catch (e) {
    console.error("[loadChildrenReport]", e);
    state.childrenReportData = { ok: false, error: safeUserError(e) };
  } finally {
    state.childrenReportBusy = false;
    renderChildrenReport();
  }
}

async function copyChildrenReport() {
  const d = state.childrenReportData;
  if (!d || !d.ok) return setNotice("Нет данных для копирования", "error");
  const month = d.month || state.childrenReportMonth || "";
  const label = _childrenReportMonthLabel(month);
  const reg   = d.regular                          || { total_unique_children: d.total_unique_children ?? 0, by_location: d.by_location || [] };
  const sum   = d.city_program || d.summer          || { total_unique_children: 0, by_location: [], groups: [] };
  const comb  = d.combined                          || { total_unique_children: d.total_unique_children ?? 0 };
  const mkp   = d.makeups  || { unique_children: 0, visit_records: 0, unique_lessons: 0 };
  const trl   = d.trials   || { unique_children: 0, visit_records: 0, unique_lessons: 0 };
  const ovl   = d.overlaps || {};
  const excl  = d.excluded || {};

  const fmtLoc = (byLoc) => (byLoc || []).map(l => {
    const name = l.location_name || l.location_code;
    const code = l.location_name ? ` / ${l.location_code}` : "";
    return `  • ${name}${code} — ${l.unique_children}`;
  }).join("\n");

  const fmtGrpLoc = (byLoc) => (byLoc || []).map(l => {
    const name = l.location_name || l.location_code;
    const code = l.location_name ? ` / ${l.location_code}` : "";
    return `  • ${name}${code} — ${l.unique_groups} гр.`;
  }).join("\n");

  const regGroupsByLocC = Array.isArray(reg.groups_by_location) ? reg.groups_by_location : [];
  const sumGroupsByLocC = Array.isArray(sum.groups_by_location) ? sum.groups_by_location : [];

  const lines = [
    `Отчёт по детям за ${label}`,
    "",
    "Сводка:",
    `• Регулярные дети — ${reg.total_unique_children}`,
    `• Регулярных групп — ${reg.total_unique_groups ?? 0}`,
    `• Городская программа / Summer Week — ${sum.total_unique_children}`,
    `• Групп ГП — ${sum.total_unique_groups ?? 0}`,
    `• Общий итог — ${comb.total_unique_children}`,
    `• Дети на отработках — ${mkp.unique_children}`,
    `• Отработок посещений — ${mkp.visit_records}`,
    `• Пробных занятий — ${trl.unique_lessons || trl.visit_records}`,
    `• Детей на пробных — ${trl.unique_children}`,
    "",
    "Регулярные занятия:",
    `Уникальных детей: ${reg.total_unique_children}`,
  ];
  if (reg.by_location?.length) lines.push(fmtLoc(reg.by_location));
  if (regGroupsByLocC.length) {
    lines.push("", "Группы по филиалам:");
    lines.push(fmtGrpLoc(regGroupsByLocC));
    lines.push(`Итого регулярных групп: ${reg.total_unique_groups ?? 0}`);
  }
  if (sum.total_unique_children > 0) {
    lines.push(
      "",
      "Городская программа / Summer Week:",
      `Уникальных детей: ${sum.total_unique_children}`,
    );
    if (sum.by_location?.length) lines.push(fmtLoc(sum.by_location));
    if (sum.groups?.length) lines.push(`  Группы: ${sum.groups.join(", ")}`);
    if (sumGroupsByLocC.length) {
      lines.push("", "Группы городской программы по филиалам:");
      lines.push(fmtGrpLoc(sumGroupsByLocC));
      lines.push(`Итого групп ГП: ${sum.total_unique_groups ?? 0}`);
    }
  }
  lines.push(
    "",
    "Итого:",
    `Уникальных детей: ${comb.total_unique_children}`,
  );
  if (ovl.regular_and_city_program_children > 0 || ovl.multi_location_children > 0) {
    lines.push("", "Пересечения:");
    if (ovl.regular_and_city_program_children > 0)
      lines.push(`• Рег. + городская программа — ${ovl.regular_and_city_program_children} дет.`);
    if (ovl.multi_location_children > 0)
      lines.push(`• Несколько филиалов — ${ovl.multi_location_children} дет.`);
  }
  lines.push(
    "",
    "Правило подсчёта:",
    "Один ребёнок считается один раз в общем итоге. Пробные и отработки не входят в регулярные занятия.",
  );

  // ── Revenue section ──
  const _rev = d.revenue || {};
  const _revReg = _rev.regular || {};
  const _revPay = _rev.payments || {};
  const _revPr = _rev.prices || {};
  const _offSub = (_revPr.YC1 || {}).subscription ?? 239;
  const _onSub  = (_revPr.YC0 || {}).subscription ?? 210;
  const _les4   = _rev.lessons_in_subscription ?? 4;
  const _fmtB = n => n == null ? "н/д" : Number(n).toFixed(2) + " BYN";
  const _fbl = Array.isArray(_rev.forecast_by_location) ? _rev.forecast_by_location : [];
  const _fbt = _rev.forecast_total || {};
  const _abl = Array.isArray(_rev.actual_payments_by_location) ? _rev.actual_payments_by_location : [];
  const _mapAvail = _rev.actual_payments_mapping_available;
  const _actTotal = _rev.actual_payments_total;
  const _actUnalloc = _rev.actual_payments_unallocated;
  if (_fbl.length > 0 || _fbt.actual_visits_forecast != null) {
    lines.push(
      "",
      "Выручка по учебным классам",
      `(Только регулярные. Онлайн YC0: ${_onSub} BYN / ${_les4} зан. · Офлайн YC1/YC2: ${_offSub} BYN / ${_les4} зан.)`,
      "",
      "Прогнозируемая:",
    );
    _fbl.forEach(loc => {
      lines.push(`• ${loc.location_name} (${loc.location_code}) — по факт. посещ.: ${_fmtB(loc.actual_visits_forecast)} · по плану: ${_fmtB(loc.planned_visits_forecast)} · ${loc.regular_groups} гр. · ${loc.unique_children} дет.`);
    });
    if (_fbt.actual_visits_forecast != null)
      lines.push(`• Общий прогноз — по факт. посещ.: ${_fmtB(_fbt.actual_visits_forecast)} · по плану: ${_fmtB(_fbt.planned_visits_forecast)}`);
    lines.push("", "Фактическая:");
    if (_mapAvail && _abl.length > 0) {
      const _grossTot = _rev.actual_payments_gross_income ?? _actTotal;
      const _refTot   = _rev.actual_payments_refunds ?? 0;
      const _netTot   = _rev.actual_payments_net_income ?? _actTotal;
      _abl.forEach(loc => {
        const refStr = (loc.refunds ?? 0) > 0 ? ` (брутто ${_fmtB(loc.gross_income ?? loc.actual_paid)} − возвр. ${_fmtB(loc.refunds)})` : "";
        lines.push(`• ${loc.location_name} (${loc.location_code}) — ${_fmtB(loc.actual_paid)}${refStr} · ${loc.payments_count} оплат`);
      });
      if (_actUnalloc != null && _actUnalloc > 0)
        lines.push(`• Не распределено — ${_fmtB(_actUnalloc)}`);
      const totRefStr = _refTot > 0 ? ` (брутто ${_fmtB(_grossTot)} − возвр. ${_fmtB(_refTot)})` : "";
      lines.push(`• Общий факт — ${_fmtB(_netTot)}${totRefStr}`);
      lines.push("(Метод: filialId из платежей МойКласс → ученик из регулярных занятий. Разница ориентировочная.)");
    } else {
      lines.push(`• Факт оплат доступен только общим итогом: ${_fmtB(_actTotal)}.`);
      lines.push("(В API платежей МойКласс нет надёжной связи с учебным классом.)");
    }
    // ── Workoff revenue copy section ──
    const _woffRev = _rev.workoff_revenue || {};
    const _rwoC    = _rev.revenue_with_workoffs || {};
    if ((_woffRev.total_workoff_visits || 0) > 0 || _rwoC.workoffs_estimated != null) {
      lines.push("", "Расчётная выручка с учётом отработок:");
      lines.push(`• Регулярные занятия — ${_fmtB(_rwoC.regular_actual_visits_forecast)}`);
      lines.push(`• Отработки — ${_fmtB(_rwoC.workoffs_estimated)} / ${_woffRev.total_workoff_visits ?? 0} визитов`);
      lines.push(`• Итого расчётно — ${_fmtB(_rwoC.planned_with_workoffs)}`);
      if (_rwoC.actual_payments_net != null)
        lines.push(`• Факт оплат нетто — ${_fmtB(_rwoC.actual_payments_net)}`);
      if (_rwoC.delta_actual_vs_planned_with_workoffs != null) {
        const _rwoDc = _rwoC.delta_actual_vs_planned_with_workoffs;
        lines.push(`• Разница — ${_rwoDc >= 0 ? "+" : ""}${_fmtB(_rwoDc)}`);
      }
      const _woffFbl = _fbl.filter(loc => (loc.workoff_visits ?? 0) > 0 || (loc.workoff_estimated ?? 0) > 0);
      if (_woffFbl.length) {
        lines.push("По филиалам:");
        _woffFbl.forEach(loc => {
          lines.push(`  • ${loc.location_name} (${loc.location_code}): рег. ${_fmtB(loc.actual_visits_forecast)}, отр. ${_fmtB(loc.workoff_estimated)} (${loc.workoff_visits} виз.), итого ${_fmtB(loc.actual_with_workoffs)}`);
        });
      }
      if (_woffRev.excluded_count > 0)
        lines.push(`(Исключено бесплатных/paid=false: ${_woffRev.excluded_count})`);
    }
    const _rg = Array.isArray(_revReg.by_group) ? _revReg.by_group : [];
    if (_rg.length) {
      lines.push("", "Детализация прогноза по группам:");
      _rg.forEach(g => {
        const pplStr = g.price_per_lesson != null ? ` · ${Number(g.price_per_lesson).toFixed(2)} BYN/зан.` : "";
        lines.push(`• ${g.group_name || g.group_id} · ${g.location_name || g.location_code}${pplStr} — ${g.unique_children} дет. · ${g.lessons_count || 0} зан. · прогноз ${_fmtB(g.forecast_revenue)} (факт.) / ${_fmtB(g.forecast_revenue_planned)} (план.)`);
      });
    }
  }

  // ── Client flow section ──
  const _cf = d.client_flow || {};
  if (_cf.source != null) {
    const _cfsm = _cf.summary || {};
    const _cfnet = _cfsm.net_growth ?? 0;
    lines.push("", "Приток / отток:");
    if (_cf.data_available) {
      lines.push(
        `• Новых клиентов — ${_cfsm.new_clients ?? 0}`,
        `• Ушло клиентов — ${_cfsm.churned_clients ?? 0}`,
        `• Чистый прирост — ${_cfnet >= 0 ? "+" : ""}${_cfnet}`,
        `• Пробных — ${_cfsm.trial ?? 0}`,
        `• Отказов — ${_cfsm.refused ?? 0}`,
        `• Некачественных лидов — ${_cfsm.bad_leads ?? 0}`,
        `• Принимают решение — ${_cfsm.deciding ?? 0}`,
        `• Новых лидов — ${_cfsm.new_leads ?? 0}`,
        `Метод: по stateChangedAt текущего статуса за месяц. Полная история переходов недоступна.`,
      );
    } else {
      lines.push("• Данные stateChangedAt недоступны в данной версии API МойКласс.");
    }
  }

  const text = lines.join("\n");
  try {
    await navigator.clipboard.writeText(text);
    setNotice("Отчёт скопирован", "ok");
  } catch (_) {
    setNotice("Не удалось скопировать автоматически", "error");
  }
}

// ── bePaid reconciliation ─────────────────────────────────────────────────────

function canUseBepaid() {
  const role = state.me?.role || "";
  return ["owner", "admin", "director", "operations", "client_manager"].includes(role);
}

function renderBepaid() {
  const el = $("bepaidResult");
  if (!el) return;
  if (!canUseBepaid()) {
    el.innerHTML = `<div class="empty">bePaid-сверка недоступна для вашей роли.</div>`;
    return;
  }

  const bp = state.bepaidData;
  const busy = state.bepaidBusy;

  if (busy) {
    el.innerHTML = `<div class="reports-loading">Загружаю bePaid…</div>`;
    return;
  }

  // Status header (always show if not loaded yet)
  const cfg = state.bepaidStatus || {};
  const configured = cfg.erip_configured || cfg.acquiring_configured;

  if (!configured && !bp) {
    el.innerHTML = `
      <div class="notice notice-warn" style="margin:12px 0">
        bePaid не настроен. Задайте переменные окружения:<br>
        <code>BEPAID_ERIP_SHOP_ID</code>, <code>BEPAID_ERIP_SECRET_KEY</code> (ЕРИП)<br>
        <code>BEPAID_ACQ_SHOP_ID</code>, <code>BEPAID_ACQ_SECRET_KEY</code> (эквайринг)
      </div>
      <button class="secondary" id="bepaidLoadStatus" type="button" style="margin-top:8px">Проверить статус</button>`;
    $("bepaidLoadStatus")?.addEventListener("click", loadBepaidStatus);
    return;
  }

  const statusHtml = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
      <span class="chip ${cfg.erip_configured ? "chip-ok" : "chip-warn"}">ЕРИП ${cfg.erip_configured ? "✓" : "—"}</span>
      <span class="chip ${cfg.acquiring_configured ? "chip-ok" : "chip-warn"}">Эквайринг ${cfg.acquiring_configured ? "✓" : "—"}</span>
      ${cfg.last_webhook_received_at ? `<span class="chip">Посл. webhook: ${cfg.last_webhook_received_at?.slice(0,16) || "—"}</span>` : ""}
    </div>`;

  // Import result banner
  const ir = state.bepaidImportResult;
  const importBanner = state.bepaidImportBusy
    ? `<div class="reports-loading" style="margin-bottom:8px">Импортирую bePaid…</div>`
    : ir ? (() => {
        // Helper: build per-shop status lines for diagnostics display
        const _shopLines = (() => {
          const shops = ir.shops || {};
          const diags = ir.diagnostics || {};
          return Object.entries(shops).map(([st, r]) => {
            const d = diags[st] || {};
            const status = r.response_status ?? d.response_status ?? "?";
            const reason = r.response_reason || d.response_reason || "";
            const ver = (r.versions_tried || d.versions_tried || []).join("/") || "?";
            const preview = (d.response_body_preview || r.error || "").slice(0, 150);
            const sid = d.shop_id_last4 ? `shop_id=…${d.shop_id_last4}` : "";
            const skl = d.secret_key_length ? `key_len=${d.secret_key_length}` : "";
            return `${st}: HTTP ${status}${reason ? " " + reason : ""}, X-Api-Version: ${ver}` +
              (sid || skl ? ` (${[sid, skl].filter(Boolean).join(", ")})` : "") +
              (preview ? `\n  → ${preview}` : "");
          }).join("\n");
        })();
        if (!ir.ok && ir.api_supported === false) {
          return `<div class="notice notice-warn" style="margin-bottom:8px;font-size:13px">
            ${escapeHtml(ir.message || "API-импорт не поддерживается в текущей реализации. Используйте CSV/XLSX из кабинета bePaid.")}
            ${_shopLines ? `<br><pre style="font-size:10px;margin:4px 0 0;overflow-x:auto;white-space:pre-wrap;text-align:left">${escapeHtml(_shopLines)}</pre>` : ""}
          </div>`;
        }
        if (!ir.ok && ir.auth_error) {
          const authShops = (ir.shops_with_auth_error || []).join(", ") || "магазин";
          return `<div class="notice notice-error" style="margin-bottom:8px;font-size:13px">
            bePaid отклонил авторизацию на reports API v2 (${escapeHtml(authShops)}).<br>
            Проверьте <b>Shop ID</b> и <b>Secret Key</b> именно этого магазина.<br>
            Если ключи верные — возможно, доступ к Reporting API не активирован. Обратитесь в поддержку bePaid.
            ${_shopLines ? `<br><pre style="font-size:10px;margin:4px 0 0;overflow-x:auto;white-space:pre-wrap;text-align:left">${escapeHtml(_shopLines)}</pre>` : ""}
          </div>`;
        }
        if (!ir.ok) {
          const mainMsg = ir.message || ir.error || "ошибка";
          return `<div class="notice notice-error" style="margin-bottom:8px;font-size:13px">
            Импорт bePaid: ${escapeHtml(mainMsg)}
            ${_shopLines ? `<br><pre style="font-size:10px;margin:4px 0 0;overflow-x:auto;white-space:pre-wrap;text-align:left">${escapeHtml(_shopLines)}</pre>` : ""}
          </div>`;
        }
        const warn = (ir.warnings || []).length > 0 ? `<br><span style="color:var(--warn);font-size:11px">${escapeHtml(ir.warnings.join("; "))}</span>` : "";
        return `<div class="notice notice-ok" style="margin-bottom:8px;font-size:13px">
          Импорт bePaid за ${escapeHtml(ir.month || "")}: загружено ${ir.imported ?? 0}, добавлено ${ir.inserted ?? 0}, обновлено ${ir.updated ?? 0}, пропущено ${ir.skipped ?? 0}.${warn}
        </div>`;
      })()
    : "";

  // Controls
  const controlsHtml = `
    ${importBanner}
    <div class="reports-controls" style="margin-bottom:12px;flex-wrap:wrap;gap:6px">
      <label><span>Месяц</span><div class="yc-month-picker"><span class="yc-month-picker__value">—</span><input class="yc-month-picker__native" id="bepaidMonth" type="month" value="${state.bepaidMonth || currentMonthValue()}" /></div></label>
      <select id="bepaidShopFilter" style="padding:6px 10px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--text)">
        <option value="all">Все магазины</option>
        <option value="erip">ЕРИП</option>
        <option value="acquiring">Эквайринг</option>
      </select>
      <button class="secondary" id="bepaidImportBtn" type="button" ${state.bepaidImportBusy ? "disabled" : ""}>Импортировать историю</button>
      <button class="primary" id="bepaidReconcileBtn" type="button" ${state.bepaidBusy ? "disabled" : ""}>Сверить</button>
    </div>`;

  const _bindBepaidControls = () => {
    initMonthPicker($("bepaidMonth"), state.bepaidMonth);
    $("bepaidMonth")?.addEventListener("change", e => { state.bepaidMonth = e.target.value; });
    $("bepaidImportBtn")?.addEventListener("click", runBepaidImport);
    $("bepaidReconcileBtn")?.addEventListener("click", runBepaidReconcile);
    const sf2 = $("bepaidShopFilter");
    if (sf2 && bp?.shop_type) sf2.value = bp.shop_type;
  };

  if (!bp) {
    el.innerHTML = statusHtml + controlsHtml + `<div class="empty">Нажмите «Импортировать историю» для загрузки транзакций bePaid, затем «Сверить» для сопоставления с МойКласс.</div>`;
    _bindBepaidControls();
    return;
  }

  if (!bp.ok) {
    el.innerHTML = statusHtml + controlsHtml + `<div class="notice notice-error">${escapeHtml(bp.error || "Ошибка")}</div>`;
    _bindBepaidControls();
    return;
  }

  const stats = bp.stats || {};
  const txns = bp.transactions || [];
  const fmtByn = n => (n == null ? "—" : Number(n).toLocaleString("ru-RU", {minimumFractionDigits:2, maximumFractionDigits:2}) + " BYN");

  const MATCH_LABELS = {
    "already_in_moyklass":                   { label: "В МойКласс ✓",       cls: "chip-ok" },
    "found_in_subscription":                 { label: "Абонемент ✓",         cls: "chip-ok" },
    "possible_subscription_match":           { label: "Похожий абонемент",   cls: "chip-info" },
    "historical_subscription_match":         { label: "Старый абонемент",    cls: "chip-muted chip-hist" },
    "user_found_no_payment_or_subscription": { label: "Ученик, нет оплаты", cls: "chip-warn" },
    "possible_payment_match":                { label: "Возможный платёж",    cls: "chip-info" },
    "needs_review":                          { label: "Нужна проверка",      cls: "chip-error" },
    "ignored_not_successful":                { label: "Неуспешная",          cls: "chip-muted" },
    "ignored_test":                          { label: "Тест",                cls: "chip-muted" },
    "ignored_currency":                      { label: "Не BYN",              cls: "chip-muted" },
  };
  const CONF_LABELS = { high: "Уверенно", medium: "Средне", low: "Низкая", none: "" };
  const CONF_CLS = { high: "chip-ok", medium: "chip-info", low: "chip-muted chip-hist", none: "" };

  // "Найдено в МК" = only already_in_moyklass + found_in_subscription (high confidence)
  // historical_subscription_match is NOT included in green tile
  const _foundCount = (stats.already_in_moyklass ?? 0) + (stats.found_in_subscription ?? 0);

  const tilesHtml = `
    <div class="cr-tiles" style="margin-bottom:12px">
      <div class="cr-tile"><div class="cr-tile-val">${bp.successful_byn_count ?? 0}</div><div class="cr-tile-lbl">Успешных BYN</div></div>
      <div class="cr-tile"><div class="cr-tile-val">${fmtByn(bp.successful_amount_byn)}</div><div class="cr-tile-lbl">Сумма</div></div>
      <div class="cr-tile cr-tile-ok"><div class="cr-tile-val">${_foundCount}</div><div class="cr-tile-lbl">Найдено в МК</div></div>
      <div class="cr-tile cr-tile-ok"><div class="cr-tile-val">${stats.found_in_subscription ?? 0}</div><div class="cr-tile-lbl">Через абонемент</div></div>
      <div class="cr-tile cr-tile-info"><div class="cr-tile-val">${stats.possible_subscription_match ?? 0}</div><div class="cr-tile-lbl">Похожий абонемент</div></div>
      <div class="cr-tile cr-tile-muted"><div class="cr-tile-val">${stats.historical_subscription_match ?? 0}</div><div class="cr-tile-lbl">Старый абонемент</div></div>
      <div class="cr-tile cr-tile-warn"><div class="cr-tile-val">${stats.user_found_no_payment_or_subscription ?? 0}</div><div class="cr-tile-lbl">Ученик, нет оплаты/аб.</div></div>
      <div class="cr-tile cr-tile-err"><div class="cr-tile-val">${stats.needs_review ?? 0}</div><div class="cr-tile-lbl">Нужна проверка</div></div>
      <div class="cr-tile cr-tile-muted"><div class="cr-tile-val">${(stats.ignored_not_successful ?? 0) + (stats.ignored_test ?? 0)}</div><div class="cr-tile-lbl">Неуспешных/тест</div></div>
    </div>`;

  const mkNote = bp.mk_payments_loaded
    ? `<p class="cr-note">Загружено платежей МойКласс: ${bp.mk_payments_count ?? 0}.</p>`
    : `<p class="cr-note" style="color:var(--warn)">Платежи МойКласс не загружены${bp.mk_error ? ": " + escapeHtml(bp.mk_error) : ""}. Сверка по наличию в МойКласс недоступна.</p>`;

  const copyBtnHtml = txns.length
    ? `<button class="secondary" id="bepaidCopyBtn" type="button" style="margin-bottom:10px;font-size:13px">Скопировать сверку bePaid</button>`
    : "";

  // Mobile cards
  const cards = txns.map(tx => {
    const ms = MATCH_LABELS[tx.match_status] || { label: tx.match_status || "—", cls: "" };
    const name = [tx.customer_last_name, tx.customer_first_name].filter(Boolean).join(" ") || null;
    const phone = tx.customer_phone || tx.billing_phone || null;
    const shopLabel = tx.shop_type === "erip" ? "ЕРИП" : tx.shop_type === "acquiring" ? "Эквайринг" : (tx.shop_type || "?");
    const paidAt = (tx.paid_at || tx.received_at || "").slice(0, 10);
    const isIgnored = ["ignored_not_successful", "ignored_test", "ignored_currency"].includes(tx.match_status);

    const mkUserLine = (() => {
      const uid = tx.mk_user_id || "";
      const uname = tx.mk_user_name || "";
      const src = tx.mk_user_id_source || "";
      if (!uid) return "";
      const label = uname ? `${uname} (${uid})` : `userId=${uid}`;
      const srcHint = src ? ` <span style="color:var(--muted);font-size:10px">из ${escapeHtml(src)}</span>` : "";
      return `<div style="margin-top:4px;font-size:12px">Ученик: <b>${escapeHtml(label)}</b>${srcHint}</div>`;
    })();

    const reasonLine = tx.match_reason
      ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">${escapeHtml(tx.match_reason)}</div>`
      : "";

    const subscriptionLine = (() => {
      const sid = tx.subscription_id;
      if (!sid) return "";
      const amt = tx.subscription_amount_byn != null ? fmtByn(tx.subscription_amount_byn) : "";
      const delta = tx.subscription_amount_delta_byn ? ` (Δ ${tx.subscription_amount_delta_byn} BYN)` : "";
      const sell = tx.subscription_sell_date ? `, продажа ${tx.subscription_sell_date}` : "";
      const period = (tx.subscription_begin_date || tx.subscription_end_date)
        ? `, период ${tx.subscription_begin_date || "?"}—${tx.subscription_end_date || "?"}`
        : "";
      const sname = tx.subscription_name ? ` «${tx.subscription_name}»` : "";
      return `<div style="margin-top:4px;font-size:12px;color:var(--text)">Абонемент${sname}: <b>#${escapeHtml(sid)}</b>${amt ? ", " + escapeHtml(amt) : ""}${escapeHtml(delta)}${escapeHtml(sell)}${escapeHtml(period)}</div>`;
    })();

    const mkPayId = tx.mk_payment_id
      ? `<div style="font-size:10px;color:var(--muted);margin-top:2px">Платёж МК: #${escapeHtml(tx.mk_payment_id)}</div>`
      : "";

    const possiblePayLine = (() => {
      const pm = tx.possible_matches;
      if (!pm || !pm.length) return "";
      const rows = pm.slice(0, 3).map(m => {
        const uname = m.mk_user_name || (m.mk_user_id ? `userId=${m.mk_user_id}` : "?");
        const amt = m.amount_byn != null ? fmtByn(m.amount_byn) : "";
        const dt = (m.date || "").slice(0, 10);
        return `<li>${escapeHtml(uname)}${dt ? ", " + escapeHtml(dt) : ""}${amt ? ", " + escapeHtml(amt) : ""}</li>`;
      }).join("");
      return `<div style="font-size:11px;color:var(--muted);margin-top:3px">Платежи МК: <ul style="margin:2px 0 0 12px;padding:0">${rows}</ul></div>`;
    })();

    const possibleSubsLine = (() => {
      const ps = tx.possible_subscriptions;
      if (!ps || !ps.length) return "";
      const rows = ps.slice(0, 3).map(s => {
        const sid = s.subscription_id || "?";
        const amt = s.subscription_amount_byn != null ? fmtByn(s.subscription_amount_byn) : "";
        const sell = s.subscription_sell_date ? `, ${s.subscription_sell_date}` : "";
        return `<li>Аб. #${escapeHtml(sid)}${amt ? ", " + escapeHtml(amt) : ""}${escapeHtml(sell)}</li>`;
      }).join("");
      return `<div style="font-size:11px;color:var(--muted);margin-top:3px">Абонементы МК: <ul style="margin:2px 0 0 12px;padding:0">${rows}</ul></div>`;
    })();

    const confLabel = CONF_LABELS[tx.match_confidence] || "";
    const confCls = CONF_CLS[tx.match_confidence] || "";
    const confChip = confLabel
      ? `<span class="chip ${confCls}" style="font-size:10px">${escapeHtml(confLabel)}</span>`
      : "";
    const isHistorical = tx.match_status === "historical_subscription_match";

    // Show "create draft" button for unresolved statuses
    const canCreateDraft = ["needs_review", "user_found_no_payment_or_subscription", "possible_subscription_match", "possible_payment_match"].includes(tx.match_status);
    // Use the active reconcile month (not transaction paid_at) as the billing period
    const _draftPeriodMonth = $("bepaidMonth")?.value || state.bepaidMonth || currentMonthValue();
    const createDraftBtn = canCreateDraft && tx.mk_user_id
      ? `<button class="secondary" style="font-size:12px;padding:4px 10px;margin-top:8px"
           onclick="openCreateIntentFromBepaid(${JSON.stringify({
             mk_user_id: tx.mk_user_id,
             student_name: tx.mk_user_name || '',
             amount_byn: tx.amount_byn,
             payment_method: tx.shop_type === 'erip' ? 'erip' : 'acquiring',
             period_month: _draftPeriodMonth,
             transaction_uid: tx.transaction_uid || '',
           })})"
         >📝 Создать черновик платежа</button>`
      : "";

    return `<div class="bepaid-card${isIgnored ? " bepaid-card-muted" : ""}${isHistorical ? " bepaid-card-hist" : ""}">
      <div class="bepaid-card-header">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span style="font-size:12px;color:var(--muted)">${escapeHtml(paidAt)}</span>
          <span class="chip" style="font-size:11px">${escapeHtml(shopLabel)}</span>
          <span class="chip ${ms.cls}" style="font-size:11px">${escapeHtml(ms.label)}</span>
          ${confChip}
        </div>
        <div style="font-size:16px;font-weight:700;white-space:nowrap">${fmtByn(tx.amount_byn)}</div>
      </div>
      ${name || phone ? `<div style="margin-top:4px;font-size:13px">${name ? escapeHtml(name) : ""}${phone ? `<span style="color:var(--muted);font-size:11px;margin-left:6px">${escapeHtml(phone)}</span>` : ""}</div>` : ""}
      ${mkUserLine}${subscriptionLine}${reasonLine}${mkPayId}${possiblePayLine}${possibleSubsLine}
      ${createDraftBtn}
    </div>`;
  }).join("");

  const cardsHtml = txns.length
    ? `<div id="bepaidCards" style="display:flex;flex-direction:column;gap:8px;margin-top:8px">${cards}</div>`
    : `<div class="empty" style="margin-top:4px">Транзакции bePaid за этот период не найдены.</div>`;

  const diagData = bp.diagnostics || {};
  const mkLoadRow = bp.mk_payments_loaded === false
    ? `<div class="cr-diag-row" style="color:var(--warn)"><span>МойКласс платежи — ошибка</span><b>${escapeHtml(diagData.moyklass_payments_load_error || "не загружены")}</b></div>`
    : `<div class="cr-diag-row"><span>МойКласс платежей загружено</span><b>${diagData.moyklass_payments_count ?? 0}</b></div>`;
  const diagHtml = `
    <details class="cr-diag" style="margin-top:16px">
      <summary>Диагностика bePaid</summary>
      <div class="cr-diag-grid">
        <div class="cr-diag-row"><span>ЕРИП настроен</span><b>${diagData.bepaid_configured_erip ? "Да" : "Нет"}</b></div>
        <div class="cr-diag-row"><span>Эквайринг настроен</span><b>${diagData.bepaid_configured_acquiring ? "Да" : "Нет"}</b></div>
        <div class="cr-diag-row"><span>Верификация подписи ЕРИП</span><b>${diagData.signature_verification_enabled_erip ? "Да" : "Нет"}</b></div>
        <div class="cr-diag-row"><span>Верификация подписи эквайринг</span><b>${diagData.signature_verification_enabled_acq ? "Да" : "Нет"}</b></div>
        <div class="cr-diag-row"><span>Транзакций bePaid</span><b>${diagData.transactions_loaded ?? 0}</b></div>
        ${mkLoadRow}
        <div class="cr-diag-row"><span>Уникальных userId МК (платежи)</span><b>${diagData.mk_known_uids_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Запросов абонементов (API)</span><b>${diagData.subscriptions_api_calls ?? 0}</b></div>
        <div class="cr-diag-row"><span>Пользователей с абонементами</span><b>${diagData.subscriptions_checked_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Абонементов загружено</span><b>${diagData.subscriptions_loaded_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Успешных BYN</span><b>${diagData.successful_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Найдено в платежах МК</span><b>${diagData.matched_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Абонемент (уверенно)</span><b>${diagData.found_in_subscription_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Абонемент (средне)</span><b>${diagData.possible_subscription_match_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Абонемент (старый/низкая уверенность)</span><b>${diagData.historical_subscription_match_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Ученик, нет оплаты/аб.</span><b>${diagData.user_found_no_payment_or_subscription_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Возможный платёж (нет userId)</span><b>${diagData.possible_payment_match_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Нужна проверка</span><b>${diagData.needs_review_count ?? 0}</b></div>
        <div class="cr-diag-row"><span>Проигнорировано</span><b>${diagData.ignored_count ?? 0}</b></div>
        <div class="cr-diag-row" style="margin-top:4px"><span>Уверенных совпадений (high)</span><b>${diagData.subscription_high_confidence_matches ?? 0}</b></div>
        <div class="cr-diag-row"><span>Средних совпадений (medium)</span><b>${diagData.subscription_medium_confidence_matches ?? 0}</b></div>
        <div class="cr-diag-row"><span>Старых совпадений (low)</span><b>${diagData.subscription_low_confidence_matches ?? 0}</b></div>
        <div class="cr-diag-row"><span>Посл. webhook</span><b>${diagData.last_webhook_received_at || "—"}</b></div>
        <div class="cr-diag-row" style="color:var(--warn);margin-top:6px"><span>Автосоздание в МойКласс</span><b>Выключено</b></div>
      </div>
    </details>`;

  el.innerHTML = statusHtml + controlsHtml + tilesHtml + mkNote + copyBtnHtml + cardsHtml + diagHtml;

  initMonthPicker($("bepaidMonth"), state.bepaidMonth);
  $("bepaidMonth")?.addEventListener("change", e => { state.bepaidMonth = e.target.value; });
  const sf = $("bepaidShopFilter");
  if (sf && bp?.shop_type) sf.value = bp.shop_type;
  $("bepaidImportBtn")?.addEventListener("click", runBepaidImport);
  $("bepaidReconcileBtn")?.addEventListener("click", runBepaidReconcile);
  $("bepaidCopyBtn")?.addEventListener("click", copyBepaidReconcile);
}

async function copyBepaidReconcile() {
  const bp = state.bepaidData;
  if (!bp || !bp.transactions) return;
  const txns = bp.transactions;
  const stats = bp.stats || {};
  const STATUS_TEXT = {
    "already_in_moyklass":                   "Есть в МойКласс (платёж)",
    "found_in_subscription":                 "Абонемент (уверенно)",
    "possible_subscription_match":           "Абонемент (средняя уверенность)",
    "historical_subscription_match":         "Старый абонемент (низкая уверенность)",
    "user_found_no_payment_or_subscription": "Ученик найден, нет релевантной оплаты/абонемента",
    "possible_payment_match":                "Возможный платёж (нет userId)",
    "needs_review":                          "Нужна проверка",
    "ignored_not_successful":                "Неуспешная",
    "ignored_test":                          "Тест",
    "ignored_currency":                      "Не BYN",
  };
  const summaryLines = [
    `Сверка bePaid ↔ МойКласс за ${bp.month || "?"}`,
    `Итого успешных BYN: ${bp.successful_byn_count ?? 0}, сумма: ${bp.successful_amount_byn ?? 0} BYN`,
    `Найдено в МойКласс (платежи): ${stats.already_in_moyklass ?? 0}`,
    `Найдено через абонементы (уверенно): ${stats.found_in_subscription ?? 0}`,
    `Похожие абонементы (средняя уверенность): ${stats.possible_subscription_match ?? 0}`,
    `Старые абонементы (низкая уверенность): ${stats.historical_subscription_match ?? 0}`,
    `Ученик найден, но релевантной оплаты/абонемента нет: ${stats.user_found_no_payment_or_subscription ?? 0}`,
    `Возможные платёжные совпадения: ${stats.possible_payment_match ?? 0}`,
    `Нужна проверка: ${stats.needs_review ?? 0}`,
    "",
  ];
  const CONF_COPY = { high: "Уверенно", medium: "Средне", low: "Низкая", none: "" };
  const header = ["Дата", "Магазин", "Клиент", "Телефон", "Сумма BYN", "Статус сверки", "Уверенность", "МК userId", "Причина", "Дата актуальности", "Платёж МК", "Абонемент МК", "Сумма абонемента", "Дата продажи аб.", "Период аб."].join("\t");
  const rows = txns.map(tx => {
    const name = [tx.customer_last_name, tx.customer_first_name].filter(Boolean).join(" ");
    const phone = tx.customer_phone || tx.billing_phone || "";
    const paidAt = (tx.paid_at || tx.received_at || "").slice(0, 10);
    const amt = tx.amount_byn != null ? Number(tx.amount_byn).toFixed(2) : "";
    const status = STATUS_TEXT[tx.match_status] || tx.match_status || "";
    const conf = CONF_COPY[tx.match_confidence] || "";
    const mkuid = tx.mk_user_id || "";
    const reason = tx.match_reason || "";
    const dateRel = tx.subscription_date_relevance || "";
    const mkpay = tx.mk_payment_id || "";
    const subid = tx.subscription_id || "";
    const subamt = tx.subscription_amount_byn != null ? Number(tx.subscription_amount_byn).toFixed(2) : "";
    const subsell = tx.subscription_sell_date || "";
    const subperiod = (tx.subscription_begin_date || tx.subscription_end_date)
      ? `${tx.subscription_begin_date || "?"}—${tx.subscription_end_date || "?"}`
      : "";
    const shop = tx.shop_type === "erip" ? "ЕРИП" : tx.shop_type === "acquiring" ? "Эквайринг" : (tx.shop_type || "");
    return [paidAt, shop, name, phone, amt, status, conf, mkuid, reason, dateRel, mkpay, subid, subamt, subsell, subperiod].join("\t");
  });
  const text = [...summaryLines, header, ...rows].join("\n");
  const btn = $("bepaidCopyBtn");
  try {
    await navigator.clipboard.writeText(text);
    if (btn) { btn.textContent = "Скопировано!"; setTimeout(() => { if (btn) btn.textContent = "Скопировать сверку bePaid"; }, 2000); }
  } catch {
    if (btn) { btn.textContent = "Ошибка копирования"; setTimeout(() => { if (btn) btn.textContent = "Скопировать сверку bePaid"; }, 2000); }
  }
}

async function loadBepaidStatus() {
  try {
    const data = await apiGet("/api/integrations/bepaid/status");
    state.bepaidStatus = data.ok ? data : null;
  } catch (e) {
    state.bepaidStatus = null;
  }
  renderBepaid();
}

async function runBepaidImport() {
  if (!canUseBepaid()) return;
  const month = $("bepaidMonth")?.value || state.bepaidMonth || currentMonthValue();
  const shopType = $("bepaidShopFilter")?.value || "all";
  state.bepaidMonth = month;
  state.bepaidImportBusy = true;
  state.bepaidImportResult = null;
  renderBepaid();
  try {
    const data = await _apiPostRaw("/api/integrations/bepaid/import-history", { month, shop_type: shopType });
    state.bepaidImportResult = data;
    if (data.ok) {
      const ins = data.inserted ?? 0;
      const upd = data.updated ?? 0;
      setNotice(`Импорт bePaid за ${month}: +${ins} новых, ${upd} обновлено`, "ok");
    }
  } catch (e) {
    state.bepaidImportResult = { ok: false, error: safeUserError(e), _network_error: true };
  } finally {
    state.bepaidImportBusy = false;
    renderBepaid();
  }
}

async function runBepaidReconcile() {
  if (!canUseBepaid()) return;
  const month = $("bepaidMonth")?.value || state.bepaidMonth || currentMonthValue();
  const shopType = $("bepaidShopFilter")?.value || "all";
  state.bepaidMonth = month;
  state.bepaidBusy = true;
  state.bepaidData = null;
  renderBepaid();
  try {
    const data = await apiGet(`/api/integrations/bepaid/reconcile?month=${encodeURIComponent(month)}&shop_type=${encodeURIComponent(shopType)}`);
    state.bepaidData = data;
    state.bepaidStatus = {
      erip_configured: data.diagnostics?.bepaid_configured_erip,
      acquiring_configured: data.diagnostics?.bepaid_configured_acquiring,
      last_webhook_received_at: data.diagnostics?.last_webhook_received_at,
    };
    setNotice(`bePaid сверка за ${month} готова`, "ok");
  } catch (e) {
    state.bepaidData = { ok: false, error: safeUserError(e) };
  } finally {
    state.bepaidBusy = false;
    renderBepaid();
  }
}

async function syncTasksFromReports(type = "all") {
  try {
    await syncClientTasks();
    if (type === "payment") state.clientTaskTypeFilter = "payment";
    else if (type === "makeup") state.clientTaskTypeFilter = "makeup";
    else state.clientTaskTypeFilter = "all";
    state.clientTaskStatusFilter = "active";
    activateTab("tasks");
    renderClientTasks();
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

function askReportsAgent() {
  const report = state.reportsData?.report;
  const month = state.reportsData?.month || state.reportsMonth || currentMonthValue();
  const input = $("askInput");
  const metrics = report?.keyMetrics || {};
  const prompt = report
    ? `Проанализируй отчёт МойКласс за ${month} для клиент-менеджера.

Показатели:
- активные ученики: ${metrics.activeStudents ?? "н/д"}
- занятий: ${metrics.lessons ?? "н/д"}
- посещений: ${metrics.visits ?? "н/д"}
- пропусков: ${metrics.missed ?? "н/д"}
- пробных записей: ${metrics.trialRecords ?? "н/д"}
- оплат: ${metrics.paymentsCount ?? "н/д"}
- сумма оплат: ${metrics.paymentsSum ?? "н/д"}
- активных задач по оплатам: ${metrics.paymentTasks ?? "н/д"}
- активных задач по отработкам: ${metrics.makeupTasks ?? "н/д"}
- активных задач по пробным: ${metrics.trialTasks ?? "н/д"}

Сделай рабочий вывод для менеджера по 3 блокам: оплаты, отработки, пробные. Напиши, где риски, какие задачи нужно проверить и какие действия сделать сегодня.`
    : `Сформируй управленческий отчёт МойКласс за ${month} для клиент-менеджера: оплаты, посещения, пропуски, пробные и что нужно сделать дальше.`;
  if (input) {
    input.value = prompt;
    autoResizeChatInput();
    input.blur?.();
  }
  setChatInputFocused(false);
  activateTab("ask");
}



const WEEK_DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const WEEK_DAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function formatWorkSlotTime(item) {
  return `${String(item.start_time || item.startTime || "").slice(0, 5)}-${String(item.end_time || item.endTime || "").slice(0, 5)}`;
}

function renderWorkScheduleUnavailable() {
  const list = $("workScheduleList");
  const summary = $("workScheduleSummary");
  if (summary) summary.innerHTML = "";
  if (list) list.innerHTML = `<div class="empty">Для выбранной роли рабочие возможности пока недоступны.</div>`;
}

function currentWorkWeekLabel() {
  const data = state.workScheduleMeta || {};
  if (data.weekLabel) return data.weekLabel;
  return state.workScheduleWeek === "next" ? "следующая неделя" : "эта неделя";
}

function setWorkWeek(week) {
  state.workScheduleWeek = week === "next" ? "next" : "current";
  document.querySelectorAll("[data-work-week]").forEach(btn => btn.classList.toggle("active", btn.dataset.workWeek === state.workScheduleWeek));
  loadWorkSchedule();
}

function workLocationLabel(slot) {
  return String(slot.location || "Любой формат").trim() || "Любой формат";
}

function renderWorkSchedule() {
  const list = $("workScheduleList");
  const summary = $("workScheduleSummary");
  if (!list || !summary) return;
  document.querySelectorAll("[data-work-week]").forEach(btn => btn.classList.toggle("active", btn.dataset.workWeek === state.workScheduleWeek));
  const items = Array.isArray(state.workSchedule) ? state.workSchedule : [];
  const byDay = new Map();
  for (const item of items) {
    const day = Number(item.day_of_week ?? item.dayOfWeek ?? 0);
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(item);
  }
  summary.innerHTML = WEEK_DAYS_SHORT.map((label, day) => {
    const slots = (byDay.get(day) || []).slice().sort((a, b) => String(a.start_time || "").localeCompare(String(b.start_time || "")));
    const slotHtml = slots.length
      ? slots.slice(0, 3).map(x => `<em>${escapeHtml(formatWorkSlotTime(x))}</em><small>${escapeHtml(workLocationLabel(x))}</small>`).join("") + (slots.length > 3 ? `<i>+${slots.length - 3}</i>` : "")
      : `<span>нет окон</span>`;
    return `<div class="schedule-day-card ${slots.length ? "has-slots" : ""}">
      <b>${escapeHtml(label)}</b>
      <div class="schedule-day-slots">${slotHtml}</div>
    </div>`;
  }).join("");

  if (!items.length) {
    list.innerHTML = `<div class="empty schedule-empty"><b>Окна на ${escapeHtml(currentWorkWeekLabel())} ещё не указаны.</b><span>Добавьте свободное время. Курс и тип занятия выбирать не нужно: преподаватель универсальный.</span></div>`;
    return;
  }
  list.innerHTML = `<div class="schedule-list-note">Показывается ${escapeHtml(currentWorkWeekLabel())}. Эти данные видят методист, админ и дальше сможет видеть клиент-менеджер для подбора свободных окон.</div>` + WEEK_DAYS.map((dayName, day) => {
    const slots = (byDay.get(day) || []).slice().sort((a, b) => String(a.start_time || "").localeCompare(String(b.start_time || "")));
    if (!slots.length) return "";
    return `<section class="schedule-day-section">
      <h3>${escapeHtml(dayName)}</h3>
      <div class="schedule-slot-list">
        ${slots.map(slot => `<article class="schedule-slot-card">
          <div class="schedule-slot-time">${escapeHtml(formatWorkSlotTime(slot))}</div>
          <div class="schedule-slot-info">
            <div class="schedule-slot-main"><b>${escapeHtml(workLocationLabel(slot))}</b><span>свободное окно</span></div>
            ${slot.note ? `<span>${escapeHtml(slot.note)}</span>` : `<span>Комментарий не указан</span>`}
          </div>
          <div class="schedule-slot-actions">
            <button class="secondary edit-work-slot" type="button" data-id="${escapeHtml(slot.id)}">Изменить</button>
            <button class="red delete-work-slot" type="button" data-id="${escapeHtml(slot.id)}">Удалить</button>
          </div>
        </article>`).join("")}
      </div>
    </section>`;
  }).join("");
  document.querySelectorAll(".edit-work-slot").forEach(btn => btn.addEventListener("click", () => fillWorkSlotForm(btn.dataset.id)));
  document.querySelectorAll(".delete-work-slot").forEach(btn => btn.addEventListener("click", () => deleteWorkSlot(btn.dataset.id)));
}

function fillWorkSlotForm(slotId) {
  const slot = (state.workSchedule || []).find(x => String(x.id) === String(slotId));
  if (!slot) return;
  $("workSlotId").value = slot.id || "";
  $("workDay").value = String(slot.day_of_week ?? 0);
  $("workStart").value = String(slot.start_time || "10:00").slice(0, 5);
  $("workEnd").value = String(slot.end_time || "14:00").slice(0, 5);
  $("workLocation").value = slot.location || "Любой формат";
  $("workNote").value = slot.note || "";
  $("saveWorkSlot").textContent = "Сохранить изменения";
  $("workScheduleForm")?.scrollIntoView?.({ block: "center", behavior: "smooth" });
}

function clearWorkSlotForm() {
  const now = new Date();
  const day = Math.max(0, Math.min(6, (now.getDay() + 6) % 7));
  if ($("workSlotId")) $("workSlotId").value = "";
  if ($("workDay")) $("workDay").value = String(day);
  if ($("workStart")) $("workStart").value = "10:00";
  if ($("workEnd")) $("workEnd").value = "14:00";
  if ($("workLocation")) $("workLocation").value = "Любой формат";
  if ($("workNote")) $("workNote").value = "";
  if ($("saveWorkSlot")) $("saveWorkSlot").textContent = "Сохранить";
}

async function loadWorkSchedule() {
  if (!canUseSchedule()) {
    renderWorkScheduleUnavailable();
    return;
  }
  try {
    const data = await apiGet(`/api/work-schedule?week=${encodeURIComponent(state.workScheduleWeek || "current")}`);
    state.workSchedule = data.items || [];
    state.workScheduleMeta = data.meta || {};
    renderWorkSchedule();
  } catch (e) {
    const list = $("workScheduleList");
    if (list) list.innerHTML = `<div class="empty">${escapeHtml(e.message || String(e))}</div>`;
  }
}

function workTimeToMinutes(value) {
  const m = String(value || "").match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return NaN;
  return Number(m[1]) * 60 + Number(m[2]);
}

function findWorkSlotOverlap(payload) {
  const start = workTimeToMinutes(payload.startTime);
  const end = workTimeToMinutes(payload.endTime);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  const currentId = String(payload.id || "");
  return (state.workSchedule || []).find(slot => {
    if (String(slot.id || "") === currentId) return false;
    if (Number(slot.day_of_week ?? slot.dayOfWeek ?? 0) !== Number(payload.dayOfWeek ?? 0)) return false;
    const otherStart = workTimeToMinutes(slot.start_time || slot.startTime);
    const otherEnd = workTimeToMinutes(slot.end_time || slot.endTime);
    if (!Number.isFinite(otherStart) || !Number.isFinite(otherEnd)) return false;
    return start < otherEnd && end > otherStart;
  }) || null;
}

async function saveWorkSlot(event) {
  event?.preventDefault?.();
  try {
    const payload = {
      id: $("workSlotId")?.value || "",
      week: state.workScheduleWeek || "current",
      dayOfWeek: Number($("workDay")?.value || 0),
      startTime: $("workStart")?.value || "",
      endTime: $("workEnd")?.value || "",
      location: $("workLocation")?.value || "",
      workType: "Любое",
      note: $("workNote")?.value || "",
    };
    const overlap = findWorkSlotOverlap(payload);
    if (overlap) {
      setNotice(`Это окно пересекается с уже добавленным временем: ${formatWorkSlotTime(overlap)}.`, "error");
      return;
    }
    const data = await apiPost("/api/work-schedule-save", payload);
    state.workSchedule = data.items || [];
    state.workScheduleMeta = data.meta || state.workScheduleMeta || {};
    renderWorkSchedule();
    clearWorkSlotForm();
    setNotice("Рабочее окно сохранено.", "ok");
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

async function deleteWorkSlot(slotId) {
  if (!slotId) return;
  try {
    const data = await apiPost("/api/work-schedule-delete", { id: slotId, week: state.workScheduleWeek || "current" });
    state.workSchedule = data.items || [];
    state.workScheduleMeta = data.meta || state.workScheduleMeta || {};
    renderWorkSchedule();
    clearWorkSlotForm();
    setNotice("Рабочее окно удалено.", "ok");
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

function adminWorkScheduleCard(r) {
  const teacher = r.full_name || r.mk_teacher_name || r.username || r.user_id || "Преподаватель";
  return `<article class="card schedule-slot-card admin-work-slot">
    <div class="schedule-slot-time">${escapeHtml(formatWorkSlotTime(r))}</div>
    <div class="schedule-slot-info">
      <div class="schedule-slot-main"><b>${escapeHtml(teacher)}</b><span>${escapeHtml(workLocationLabel(r))}</span></div>
      <span>${escapeHtml(WEEK_DAYS[Number(r.day_of_week ?? 0)] || "-")} · свободное окно</span>
      ${r.note ? `<span>${escapeHtml(r.note)}</span>` : ""}
    </div>
  </article>`;
}

function adminWorkScheduleSection(items, title) {
  return `<section class="schedule-day-section admin-work-section"><h3>${escapeHtml(title)}</h3><div class="schedule-slot-list">${items.map(adminWorkScheduleCard).join("")}</div></section>`;
}

function renderOpenSlotsUnavailable() {
  const list = $("openSlotsList");
  const summary = $("openSlotsSummary");
  if (summary) summary.innerHTML = "";
  if (list) list.innerHTML = `<div class="empty">Для выбранной роли свободные окна пока недоступны.</div>`;
}

function openSlotTimeRange(slot) {
  return `${String(slot.start_time || slot.startTime || "").slice(0, 5)}-${String(slot.end_time || slot.endTime || "").slice(0, 5)}`;
}

function slotMatchesTimeFilter(slot) {
  const filter = state.openSlotsTimeFilter || "all";
  if (filter === "all") return true;
  const start = workTimeToMinutes(slot.start_time || slot.startTime);
  const end = workTimeToMinutes(slot.end_time || slot.endTime);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return true;
  if (filter === "morning") return start < 12 * 60;
  if (filter === "day") return start < 17 * 60 && end > 12 * 60;
  if (filter === "evening") return end > 17 * 60;
  return true;
}

function filteredOpenSlots() {
  const location = state.openSlotsLocationFilter || "all";
  return (state.openSlots || []).filter(slot => {
    const loc = String(slot.location || "Любой формат");
    const locationOk = location === "all" || loc === location;
    return locationOk && slotMatchesTimeFilter(slot);
  });
}

function setOpenSlotsWeek(week) {
  state.openSlotsWeek = week === "next" ? "next" : "current";
  document.querySelectorAll("[data-open-slots-week]").forEach(btn => btn.classList.toggle("active", btn.dataset.openSlotsWeek === state.openSlotsWeek));
  loadOpenSlots();
}

function updateOpenSlotsFilters() {
  const loc = $("openSlotsLocationFilter")?.value || "all";
  const time = $("openSlotsTimeFilter")?.value || "all";
  state.openSlotsLocationFilter = loc;
  state.openSlotsTimeFilter = time;
  renderOpenSlots();
}

function openSlotsUseText(slot) {
  const teacher = slot.teacher_name || slot.teacherName || "Преподаватель";
  const day = slot.day_short || slot.day_name || "";
  const dateLabel = slot.date_label || "";
  const time = openSlotTimeRange(slot);
  const location = slot.location || "Любой формат";
  const note = slot.note ? `
Комментарий преподавателя: ${slot.note}` : "";
  return `${teacher}
${day} ${dateLabel}, ${time}
${location}${note}`.trim();
}

function useOpenSlot(slotId) {
  const slot = (state.openSlots || []).find(x => String(x.id) === String(slotId));
  if (!slot) return;
  const text = openSlotsUseText(slot);
  activateTab("ask");
  const input = $("askInput");
  if (input) {
    input.value = `Помоги использовать это свободное окно для записи клиента.

${text}

Составь короткое сообщение клиенту с этим вариантом и чек-лист, что проверить в МойКласс перед записью.`;
    autoResizeChatInput();
    input.blur?.();
  }
  setChatInputFocused(false);
  setNotice("Окно перенесено в чат. Уточните клиента или отправьте вопрос агенту.", "ok");
}

function renderOpenSlots() {
  const list = $("openSlotsList");
  const summary = $("openSlotsSummary");
  if (!list || !summary) return;
  document.querySelectorAll("[data-open-slots-week]").forEach(btn => btn.classList.toggle("active", btn.dataset.openSlotsWeek === state.openSlotsWeek));
  const items = filteredOpenSlots();
  const meta = state.openSlotsMeta || {};
  const teacherCount = new Set(items.map(x => String(x.user_id || x.mk_teacher_id || x.teacher_name || ""))).size;
  const nearest = items[0] ? `${items[0].day_short || ""} ${items[0].date_label || ""} · ${openSlotTimeRange(items[0])}` : "-";
  summary.innerHTML = `
    <div class="open-slots-stat"><b>${items.length}</b><span>окон найдено</span></div>
    <div class="open-slots-stat"><b>${teacherCount}</b><span>преподавателей</span></div>
    <div class="open-slots-stat wide"><b>${escapeHtml(nearest)}</b><span>ближайшее окно</span></div>
  `;
  if (!items.length) {
    list.innerHTML = `<div class="empty schedule-empty"><b>Свободных окон не найдено.</b><span>Попробуйте выбрать другую неделю, филиал или время дня. Если окон нет совсем - преподаватели ещё не заполнили вкладку “Время”.</span></div>`;
    return;
  }
  const byDate = new Map();
  for (const item of items) {
    const key = String(item.date || `${item.day_of_week || 0}`);
    if (!byDate.has(key)) byDate.set(key, []);
    byDate.get(key).push(item);
  }
  list.innerHTML = `<div class="schedule-list-note">Показываются свободные окна преподавателей на ${escapeHtml(meta.weekLabel || "выбранную неделю")}. Преподаватели универсальные: окно подходит для пробного, отработки, замены или регулярного занятия. Кнопка “Использовать” перенесёт выбранный вариант в чат, где агент поможет оформить сообщение клиенту и напомнит, что проверить в МойКласс.</div>` + Array.from(byDate.entries()).map(([dateKey, slots]) => {
    const first = slots[0] || {};
    const title = `${first.day_name || "День"}${first.date_label ? ` · ${first.date_label}` : ""}`;
    return `<section class="schedule-day-section open-slots-day">
      <h3>${escapeHtml(title)}</h3>
      <div class="schedule-slot-list">
        ${slots.map(slot => `<article class="schedule-slot-card open-slot-card">
          <div class="schedule-slot-time">${escapeHtml(openSlotTimeRange(slot))}</div>
          <div class="schedule-slot-info">
            <div class="schedule-slot-main"><b>${escapeHtml(slot.teacher_name || "Преподаватель")}</b><span>${escapeHtml(slot.location || "Любой формат")}</span></div>
            ${slot.teacher_username ? `<span>@${escapeHtml(slot.teacher_username)}</span>` : ""}
            ${slot.note ? `<span>${escapeHtml(slot.note)}</span>` : `<span>Комментарий не указан</span>`}
          </div>
          <div class="schedule-slot-actions">
            <button class="primary use-open-slot" type="button" data-id="${escapeHtml(slot.id)}">Использовать</button>
          </div>
        </article>`).join("")}
      </div>
    </section>`;
  }).join("");
  document.querySelectorAll(".use-open-slot").forEach(btn => btn.addEventListener("click", () => useOpenSlot(btn.dataset.id)));
}

async function loadOpenSlots() {
  if (!canUseOpenSlots()) {
    state.openSlots = [];
    renderOpenSlotsUnavailable();
    return;
  }
  try {
    const data = await apiGet(`/api/open-slots?week=${encodeURIComponent(state.openSlotsWeek || "current")}`);
    state.openSlots = data.items || [];
    state.openSlotsMeta = data.meta || {};
    renderOpenSlots();
  } catch (e) {
    const list = $("openSlotsList");
    if (list) list.innerHTML = `<div class="empty">${escapeHtml(e.message || String(e))}</div>`;
  }
}

function renderAskMessages() {
  const root = $("askMessages");
  if (!root) return;
  if (!state.askMessages.length) {
    root.innerHTML = `<div class="ask-empty chat-empty">
      <b>Чат с рабочим агентом</b>
      <span>Задайте вопрос или выберите быстрый сценарий. Сам чат - основная рабочая зона этой страницы.</span>
    </div>`;
    root.scrollTop = 0;
    return;
  }
  root.innerHTML = state.askMessages.map(msg => `
    <div class="ask-message ${msg.role === "user" ? "user" : "agent"}">
      <div class="ask-message-role">${msg.role === "user" ? "Вы" : "AI агент"}</div>
      <div class="ask-message-text">${formatChatMessage(msg.text || "")}</div>
    </div>
  `).join("");
  bindChatLinks(root);
  window.setTimeout(() => {
    root.scrollTop = root.scrollHeight;
    root.lastElementChild?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, 0);
}


async function sendAskQuestion(event) {
  event?.preventDefault?.();
  if (state.askBusy) return;
  const input = $("askInput");
  const button = $("askSubmit");
  const question = String(input?.value || "").trim();
  if (!question) return;
  const shouldRestoreFocus = Boolean(event) && document.activeElement === input;
  state.askMessages.push({ role: "user", text: question });
  input.value = "";
  autoResizeChatInput();
  state.askBusy = true;
  setChatSubmitBusy(button, true);
  renderAskMessages();
  try {
    const data = await apiPost("/api/ask", {
      question,
      mode: "teacher_chat",
      history: chatHistoryForApi(),
      workContext: buildChatWorkContext(),
    });
    state.askMessages.push({ role: "agent", text: data.answer || "Не удалось подготовить ответ." });
  } catch (e) {
    state.askMessages.push({ role: "agent", text: `Ошибка: ${e.message || e}` });
  } finally {
    state.askBusy = false;
    setChatSubmitBusy(button, false);
    renderAskMessages();
    if (shouldRestoreFocus) {
      input?.focus?.();
    } else {
      input?.blur?.();
      setChatInputFocused(false);
    }
    autoResizeChatInput();
  }
}

function lessonEndDate(lesson) {
  const d = String(lesson.date || "").slice(0, 10);
  const time = String(lesson.time || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null;
  let end = "23:59";
  const m = time.match(/(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})/);
  if (m) end = m[2];
  else {
    const one = time.match(/(\d{1,2}:\d{2})/);
    if (one) end = one[1];
  }
  const dt = new Date(`${d}T${end}:00`);
  return isNaN(dt.getTime()) ? null : dt;
}
function isLessonPast(lesson) {
  const dt = lessonEndDate(lesson);
  if (!dt) return false;
  return Date.now() > dt.getTime();
}
function closeMissing(lesson) {
  const missing = [];
  if (lesson.parentReportStatus !== "done") missing.push("отчёт родителям");
  if (lesson.myclassStatus !== "done") missing.push("МойКласс");
  if (lesson.worksStatus !== "done") missing.push("работы учеников");
  if (lesson.classroomStatus !== "done") missing.push("кабинет");
  return missing;
}
function stepLine(label, status) {
  const done = status === "done";
  const submitted = status === "submitted";
  const rejected = status === "rejected";
  const icon = done ? "✅" : submitted ? "⏳" : rejected ? "❌" : "☐";
  const cls = done ? "done" : submitted ? "submitted" : rejected ? "rejected" : "";
  return `<div class="step-line ${cls}"><span>${icon}</span><b>${escapeHtml(label)}</b></div>`;
}
function prepCheckbox(label, status, action = "") {
  const done = status === "done";
  const submitted = status === "submitted";
  const rejected = status === "rejected";
  const checked = done || submitted;
  // Обычные пункты подготовки можно включать и выключать.
  // Системный пункт результата не меняется вручную: он зависит от файла и проверки старшим.
  const disabled = submitted || !action;
  const cls = done ? "done" : submitted ? "submitted" : rejected ? "rejected" : "";
  const hint = submitted ? `<small>на проверке у старшего преподавателя</small>` : rejected ? `<small>отклонено, нужно отправить новый результат</small>` : "";
  const data = action ? `data-check-action="${escapeHtml(action)}"` : "";
  return `<label class="prep-check ${cls}">
    <input type="checkbox" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} ${data} />
    <span><b>${escapeHtml(label)}</b>${hint}</span>
  </label>`;
}

function closingCheckbox(label, status, action = "") {
  const done = status === "done";
  const checked = done;
  const cls = done ? "done" : "";
  const data = action ? `data-close-action="${escapeHtml(action)}"` : "";
  return `<label class="close-check ${cls}">
    <input type="checkbox" ${checked ? "checked" : ""} ${data} />
    <span><b>${escapeHtml(label)}</b></span>
  </label>`;
}

function cleanMaterialText(text) {
  return String(text || "")
    .replace(/<[^>]+>/g, " ")
    .split("\n")
    .map(line => line
      .replace(/^\s{0,3}#{1,6}\s*/g, "")
      .replace(/^\s*[-*+]\s+/g, "")
      .replace(/^\s*>\s?/g, "")
      .replace(/\*\*/g, "")
      .replace(/__+/g, "")
      .replace(/`+/g, "")
      .trim())
    .filter(line => {
      if (!line) return false;
      const low = line.toLowerCase();
      if (low.startsWith("notion last edited")) return false;
      if (low.startsWith("путь:")) return false;
      if (low.startsWith("источник:")) return false;
      if (low.startsWith("запрос:")) return false;
      if (low.includes("/ продукт / программа обучения")) return false;
      if (low.includes("_страница пуста")) return false;
      return true;
    });
}
function materialSummary(text) {
  const lines = cleanMaterialText(text);
  if (!lines.length) return "Краткое содержание пока недоступно.";
  const useful = lines
    .filter(line => line.length > 18 && !/^https?:\/\//i.test(line))
    .slice(0, 4);
  const selected = useful.length ? useful : lines.slice(0, 4);
  return selected.join("\n").slice(0, 700);
}
function materialBullets(text) {
  const summary = materialSummary(text);
  if (!summary || summary === "Краткое содержание пока недоступно.") return [summary];
  return summary.split("\n").map(x => x.trim()).filter(Boolean).slice(0, 4);
}
function prepFilesHtml(files) {
  if (!files || !files.length) return `<div class="result-status muted">Файл результата ещё не отправлен.</div>`;
  const rows = files.slice(0, 5).map(f => {
    const status = f.status === "approved" ? "✅ подтверждено" : f.status === "rejected" ? "❌ отклонено" : "⏳ ожидает проверки";
    const size = formatFileSize(f.size_bytes);
    return `<div class="file-row">
      <b>${escapeHtml(f.file_name || "файл")}</b>
      <span>${status}${size ? ` · ${escapeHtml(size)}` : ""}</span>
      <a class="file-download-link" href="${apiDownloadUrl(f.id)}" target="_blank" rel="noopener">⬇️ Скачать файл</a>
      ${f.reviewer_comment ? `<small>${escapeHtml(f.reviewer_comment)}</small>` : ""}
    </div>`;
  }).join("");
  return `<div class="file-list">${rows}</div>`;
}

function latestPrepReview(files) {
  const list = Array.isArray(files) ? files.slice() : [];
  return list.find(f => ["approved", "rejected", "submitted"].includes(String(f.status || ""))) || null;
}

function prepReviewFeedbackHtml(files, lesson) {
  const item = latestPrepReview(files);
  const controlComment = String(lesson?.preparationComment || "").trim();
  if (!item && !controlComment) return "";
  const status = String(item?.status || "");
  const cls = status === "approved" ? "ok" : status === "rejected" ? "bad" : "warn";
  const title = status === "approved"
    ? "Работа подтверждена старшим преподавателем"
    : status === "rejected"
      ? "Работа отправлена на доработку"
      : "Работа ожидает проверки";
  const text = String(item?.reviewer_comment || controlComment || (status === "submitted" ? "Старший преподаватель получит файл и оставит обратную связь после проверки." : "Комментарий не указан.")).trim();
  const fileLine = item?.file_name ? `<p class="review-file">Файл: <b>${escapeHtml(item.file_name)}</b></p>` : "";
  return `<div class="prep-feedback ${cls}">
    <div class="box-icon feedback-icon">${status === "approved" ? "✅" : status === "rejected" ? "❌" : "⏳"}</div>
    <div>
      <h3>${escapeHtml(title)}</h3>
      ${fileLine}
      <p>${nl2br(text)}</p>
    </div>
  </div>`;
}
function mkCommentHtml(lesson) {
  const text = String(lesson.mkComment || "").trim();
  if (!text) return "";
  return `<div class="lesson-mk-comment">
    <b>Общий комментарий МК:</b> ${nl2br(text)}
  </div>`;
}

function currentActionBlock(lesson, material, past) {
  let cls = "";
  let text = "";
  if (lesson.lessonStatus === "closed") {
    cls = "ok closed";
    text = "Занятие закрыто. Всё основное завершено, можно переходить к следующему занятию.";
  } else if (!String(lesson.topic || "").trim()) {
    cls = "warn";
    text = "В МойКласс не указана тема. Нужно уточнить тему до подготовки.";
  } else if (!material?.found) {
    cls = "warn";
    text = "Тема указана, но точный материал Notion не найден. Сообщите старшему преподавателю.";
  } else if (!past && lesson.preparationStatus !== "ready") {
    text = "Перед занятием: изучите материал, посмотрите видео, выполните практику и отправьте результат старшему преподавателю.";
  } else if (!past) {
    cls = "ok";
    text = "Подготовка подтверждена. После занятия откроется блок закрытия.";
  } else {
    const missing = closeMissing(lesson);
    if (missing.length) {
      cls = "warn";
      text = `После занятия нужно закрыть: ${missing.join(", ")}.`;
    } else {
      cls = "ok";
      text = "Основные пункты выполнены. Можно закрыть занятие.";
    }
  }
  return `<div class="proto-box todo-box ${cls}">
    <div class="box-icon">📋</div>
    <div><h3>Что сделать</h3><p>${escapeHtml(text)}</p></div>
  </div>`;
}


function showLessonModal() {
  const modal = $("lessonModal");
  if (!modal) return;
  modal.classList.remove("hidden", "is-closing");
  modal.classList.add("is-opening");
  window.setTimeout(() => modal.classList.remove("is-opening"), 260);
}

function closeLessonModal() {
  const modal = $("lessonModal");
  if (!modal || modal.classList.contains("hidden")) return;
  modal.classList.remove("is-opening");
  modal.classList.add("is-closing");
  window.setTimeout(() => {
    modal.classList.add("hidden");
    modal.classList.remove("is-closing");
  }, 180);
}

function renderLessonSkeleton() {
  $("lessonContent").innerHTML = `
    <div class="lesson-skeleton">
      <div class="sk-line sk-title"></div>
      <div class="sk-line sk-sub"></div>
      <div class="proto-box skeleton-card">
        <div class="sk-line"></div><div class="sk-line short"></div><div class="sk-line mid"></div>
        <div class="sk-pills"><span></span><span></span><span></span></div>
      </div>
      <div class="proto-box skeleton-card"><div class="sk-line mid"></div><div class="sk-line"></div><div class="sk-line short"></div></div>
      <div class="proto-box skeleton-card"><div class="sk-line mid"></div><div class="sk-line"></div><div class="sk-line"></div><div class="sk-line short"></div></div>
    </div>`;
  showLessonModal();
}

function setRowVisual(input, checked) {
  const row = input.closest(".prep-check, .close-check");
  if (!row) return;
  row.classList.toggle("done", !!checked);
  row.classList.toggle("saving", false);
  row.classList.toggle("saved-flash", true);
  window.setTimeout(() => row.classList.remove("saved-flash"), 450);
}

function markRowSaving(input, saving) {
  const row = input.closest(".prep-check, .close-check");
  if (row) row.classList.toggle("saving", !!saving);
}

function patchLessonForAction(lesson, action) {
  if (!lesson) return;
  const map = {
    prep_material_done: ["prepMaterialStatus", "done"],
    prep_material_not_checked: ["prepMaterialStatus", "not_checked"],
    prep_video_done: ["prepVideoStatus", "done"],
    prep_video_not_checked: ["prepVideoStatus", "not_checked"],
    prep_practice_done: ["prepPracticeStatus", "done"],
    prep_practice_not_checked: ["prepPracticeStatus", "not_checked"],
    parent_report_done: ["parentReportStatus", "done"],
    parent_report_not_checked: ["parentReportStatus", "not_checked"],
    myclass_done: ["myclassStatus", "done"],
    myclass_not_checked: ["myclassStatus", "not_checked"],
    works_done: ["worksStatus", "done"],
    works_not_checked: ["worksStatus", "not_checked"],
    classroom_done: ["classroomStatus", "done"],
    classroom_not_checked: ["classroomStatus", "not_checked"],
    close: ["lessonStatus", "closed"],
    problem: ["lessonStatus", "problem"],
  };
  const pair = map[action];
  if (pair) lesson[pair[0]] = pair[1];
  const prepReady = lesson.prepMaterialStatus === "done" && lesson.prepVideoStatus === "done" && lesson.prepPracticeStatus === "done" && ["done", "submitted", "approved"].includes(lesson.prepResultStatus || "");
  if (prepReady) lesson.preparationStatus = "ready";
  else if (["prep_material", "prep_video", "prep_practice"].some(x => action.startsWith(x))) lesson.preparationStatus = "not_checked";
}

function applyControlPatch(lesson, control) {
  if (!lesson || !control) return;
  const map = {
    preparation_status: "preparationStatus",
    lesson_status: "lessonStatus",
    parent_report_status: "parentReportStatus",
    myclass_status: "myclassStatus",
    works_status: "worksStatus",
    classroom_status: "classroomStatus",
    problem_status: "problemStatus",
    problem_comment: "problemComment",
    prep_material_status: "prepMaterialStatus",
    prep_video_status: "prepVideoStatus",
    prep_practice_status: "prepPracticeStatus",
    prep_result_status: "prepResultStatus",
    prep_result_file_id: "prepResultFileId",
    preparation_comment: "preparationComment",
    lesson_comment: "lessonComment",
  };
  Object.entries(map).forEach(([from, to]) => {
    if (Object.prototype.hasOwnProperty.call(control, from)) lesson[to] = control[from] || lesson[to] || "";
  });
}

function applyLocalActionPatch(lessonId, action) {
  if (state.selectedLesson?.lesson?.id === lessonId) patchLessonForAction(state.selectedLesson.lesson, action);
  if (state.lessonCache[lessonId]?.data?.lesson) patchLessonForAction(state.lessonCache[lessonId].data.lesson, action);
  const lessonListItem = state.lessons.find(item => item.id === lessonId);
  if (lessonListItem) patchLessonForAction(lessonListItem, action);
}

function updateCloseSummary(lesson) {
  const box = document.querySelector(".close-ready");
  if (!box || !lesson) return;
  const missing = closeMissing(lesson);
  const ready = missing.length === 0;
  box.classList.toggle("ready", ready);
  box.classList.toggle("todo", !ready);
  const title = box.querySelector("h3");
  const text = box.querySelector("p");
  if (title) title.textContent = ready ? "Готово к закрытию" : "Нужно закрыть занятие";
  if (text) text.textContent = ready ? "Все обязательные пункты отмечены. Можно закрыть занятие." : `Осталось отметить: ${missing.join(", ")}.`;
}

function renderLessonsQuietly() {
  const activeId = state.selectedLesson?.lesson?.id || "";
  renderLessons();
  if (activeId) {
    const card = document.querySelector(`[data-lesson-id="${cssEscapeValue(activeId)}"]`);
    if (card) card.classList.add("card-muted-update");
  }
}

function fetchLessonDetail(lessonId) {
  if (!lessonId) return Promise.reject(new Error("lessonId пустой"));
  if (!state.lessonFetches[lessonId]) {
    state.lessonFetches[lessonId] = apiGet(`/api/lesson?id=${encodeURIComponent(lessonId)}`)
      .finally(() => { delete state.lessonFetches[lessonId]; });
  }
  return state.lessonFetches[lessonId];
}

async function refreshLessonSilently(lessonId) {
  if (!lessonId) return;
  try {
    const data = await fetchLessonDetail(lessonId);
    state.lessonCache[lessonId] = { data, ts: Date.now() };
    if (state.selectedLesson?.lesson?.id === lessonId) state.selectedLesson = data;
  } catch (_) {}
}

async function runCheckboxAction(lessonId, action, input) {
  const previous = !input.checked;
  const target = input.checked;
  input.disabled = true;
  markRowSaving(input, true);
  try {
    applyLocalActionPatch(lessonId, action);
    setRowVisual(input, target);
    if (state.selectedLesson?.lesson?.id === lessonId) updateCloseSummary(state.selectedLesson.lesson);
    renderLessonsQuietly();

    await apiPost("/api/action", { lessonId, action, comment: "" });
    setNotice("Сохранено", "ok");
    input.disabled = false;
    window.setTimeout(() => refreshLessonSilently(lessonId), 250);
  } catch (e) {
    input.checked = previous;
    const rollbackAction = action.endsWith("_done") ? action.replace(/_done$/, "_not_checked") : action.replace(/_not_checked$/, "_done");
    applyLocalActionPatch(lessonId, rollbackAction);
    setRowVisual(input, previous);
    if (state.selectedLesson?.lesson?.id === lessonId) updateCloseSummary(state.selectedLesson.lesson);
    renderLessonsQuietly();
    input.disabled = false;
    setNotice(safeUserError(e), "error");
  }
}

function renderLessonModal(data) {
  const lesson = data.lesson || {};
  const material = data.material || {};
  const prepFiles = data.prepFiles || [];
  const feedbackBlock = prepReviewFeedbackHtml(prepFiles, lesson);
  const past = isLessonPast(lesson);
  const lessonClosed = lesson.lessonStatus === "closed";
  const st = _teacherLessonStatus(lesson);

  // ─── Action hint ───────────────────────────────────────────────────────────
  let hintCls = "";
  let hintText = "";
  if (lessonClosed) {
    hintCls = "ok"; hintText = "Занятие закрыто. Всё завершено.";
  } else if (!String(lesson.topic || "").trim()) {
    hintCls = "warn"; hintText = "В МойКласс не указана тема. Уточните до подготовки.";
  } else if (!material?.found && !past) {
    hintCls = "warn"; hintText = "Тема есть, но материал Notion не найден. Сообщите старшему преподавателю.";
  } else if (!past && lesson.preparationStatus !== "ready") {
    hintText = "Изучите материал, выполните практику и прикрепите файл результата.";
  } else if (!past) {
    hintCls = "ok"; hintText = "Подготовка подтверждена. Проводите занятие.";
  } else {
    const missing = closeMissing(lesson);
    if (missing.length) { hintCls = "warn"; hintText = `После занятия нужно: ${missing.join(", ")}.`; }
    else { hintCls = "ok"; hintText = "Все пункты выполнены. Нажмите «Закрыть занятие»."; }
  }

  // ─── Info ──────────────────────────────────────────────────────────────────
  const mkCom = String(lesson.mkComment || "").trim();
  const infoBody = `
    ${lesson.topic ? `<div class="lm-row"><span>📖</span><div><b>Тема</b><span>${escapeHtml(lesson.topic)}</span></div></div>` : ""}
    <div class="lm-row"><span>📅</span><div><b>Дата и время</b><span>${escapeHtml(lesson.date || "-")} · ${escapeHtml(lesson.time || "-")}</span></div></div>
    ${lesson.room ? `<div class="lm-row"><span>🖥</span><div><b>Кабинет</b><span>${escapeHtml(lesson.room)}</span></div></div>` : ""}
    ${lesson.teacher ? `<div class="lm-row"><span>👤</span><div><b>Преподаватель</b><span>${escapeHtml(lesson.teacher)}</span></div></div>` : ""}
    ${mkCom ? `<div class="lm-row"><span>💬</span><div><b>Комментарий МК</b><span>${nl2br(mkCom)}</span></div></div>` : ""}`;

  // ─── Prep section ──────────────────────────────────────────────────────────
  const prepStatus = String(lesson.prepResultStatus || "");
  const prepVisible = !lessonClosed && (!past || prepStatus === "rejected");
  const prepBody = `
    <div class="prep-checklist">
      ${prepCheckbox("Материал в Notion изучен", lesson.prepMaterialStatus, "prep_material_done")}
      ${prepCheckbox("Видео / инструкция просмотрены", lesson.prepVideoStatus, "prep_video_done")}
      ${prepCheckbox("Практическая работа выполнена", lesson.prepPracticeStatus, "prep_practice_done")}
      ${prepCheckbox("Результат отправлен старшему преподавателю", lesson.prepResultStatus, "")}
    </div>
    <div class="upload-title">Прикрепить файл результата</div>
    <label class="file-upload-box">
      <span class="file-icon">📄</span>
      <span id="prepFileName">Файл не выбран</span>
      <strong>Выбрать файл</strong>
      <input id="prepResultFile" type="file" />
    </label>
    <button class="dark wide" id="uploadPrepResult">✈️ Отправить результат</button>
    ${prepFilesHtml(prepFiles)}
    <button class="primary wide" id="markStudyDone">✅ Отметить изучение выполненным</button>
    <button class="red wide" data-action="prepare_help">🛟 Нужна помощь с темой</button>`;

  // ─── Close section ─────────────────────────────────────────────────────────
  let closeBody = "";
  if (lessonClosed) {
    closeBody = `<div class="lm-closed-banner">✅ Занятие закрыто — всё готово!</div>`;
  } else if (past) {
    const missing = closeMissing(lesson);
    const ready = missing.length === 0;
    closeBody = `
      <div class="close-ready ${ready ? "ready" : "todo"}">
        <h3>${ready ? "Готово к закрытию" : "Нужно закрыть занятие"}</h3>
        <p>${ready ? "Все пункты отмечены. Можно закрыть занятие." : `Осталось: ${escapeHtml(missing.join(", "))}.`}</p>
      </div>
      <div class="close-checklist">
        ${closingCheckbox("Отчёт родителям отправлен", lesson.parentReportStatus, "parent_report")}
        ${closingCheckbox("МойКласс заполнен", lesson.myclassStatus, "myclass")}
        ${closingCheckbox("Работы учеников сохранены", lesson.worksStatus, "works")}
        ${closingCheckbox("Кабинет, техника, расходники", lesson.classroomStatus, "classroom")}
      </div>
      <label class="field-label" for="reportDetails"><b>Что сделали за занятие</b><span>Попадёт в отчёт родителям.</span></label>
      <textarea id="reportDetails" class="text-input" rows="3" placeholder="Например: настроили джойстик, провели тест игры..."></textarea>
      <div class="close-actions">
        <button class="dark wide" id="generateReport">🧾 Сгенерировать отчёт</button>
        <button class="red wide" data-action="problem">⚠️ Есть проблема</button>
        <button class="primary wide" data-action="close">✅ Закрыть занятие</button>
      </div>`;
  } else {
    closeBody = `<div class="lm-locked">⏳ Кнопки закрытия появятся после занятия.</div>`;
  }

  // ─── Conduct reminder ──────────────────────────────────────────────────────
  const conductBody = `<ul class="lm-conduct-list">
    <li>Прийти заранее, проверить кабинет</li>
    <li>Провести занятие по материалу Notion</li>
    <li>Сохранить или сфотографировать работы учеников</li>
    <li>После занятия — вернуться и закрыть занятие в приложении</li>
  </ul>`;

  // ─── Material ──────────────────────────────────────────────────────────────
  const matBody = material.found
    ? `${material.notionUrl ? `<a class="notion-button" target="_blank" href="${escapeHtml(material.notionUrl)}">Открыть Notion ↗</a>` : `<span class="muted">Ссылка не найдена</span>`}
       <div class="material-summary"><ul>${materialBullets(material.preview).map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul></div>`
    : `<p class="muted">${escapeHtml(material.error || "Точный материал не найден")}</p>`;

  $("lessonContent").innerHTML = `
    <div class="lm-header">
      <div class="lm-group">${escapeHtml(lesson.group || "Занятие")}</div>
      <div class="lm-datetime">${escapeHtml(lesson.date || "-")} · ${escapeHtml(lesson.time || "-")}</div>
    </div>
    <div class="lm-badge-row">
      <span class="yc-badge yc-badge-${st.cls}">${escapeHtml(st.label)}</span>
    </div>
    ${hintText ? `<div class="lm-hint${hintCls ? " lm-hint-" + hintCls : ""}">${escapeHtml(hintText)}</div>` : ""}
    ${feedbackBlock}
    <details class="lm-section" ${!past && !lessonClosed ? "open" : ""}>
      <summary class="lm-section-head"><span>ℹ️</span> Информация о занятии</summary>
      <div class="lm-section-body lm-info-rows">${infoBody}</div>
    </details>
    ${prepVisible ? `<details class="lm-section" ${!past ? "open" : ""}>
      <summary class="lm-section-head"><span>📚</span> Подготовка</summary>
      <div class="lm-section-body">${prepBody}</div>
    </details>` : ""}
    ${!past && !lessonClosed ? `<details class="lm-section">
      <summary class="lm-section-head"><span>🎓</span> Проведение занятия</summary>
      <div class="lm-section-body">${conductBody}</div>
    </details>` : ""}
    <details class="lm-section" ${past ? "open" : ""}>
      <summary class="lm-section-head"><span>${lessonClosed ? "✅" : past ? "🌙" : "⏳"}</span> Закрытие занятия</summary>
      <div class="lm-section-body">${closeBody}</div>
    </details>
    <div id="reportBox" class="hidden"></div>
    <details class="lm-section">
      <summary class="lm-section-head"><span>📖</span> Материал Notion</summary>
      <div class="lm-section-body lm-material-body">${matBody}</div>
    </details>`;

  document.querySelectorAll("[data-action]").forEach(btn => btn.addEventListener("click", () => runAction(lesson.id, btn.dataset.action)));
  document.querySelectorAll("[data-check-action]").forEach(input => input.addEventListener("change", () => {
    const baseAction = input.dataset.checkAction || "";
    const action = input.checked ? baseAction : baseAction.replace(/_done$/, "_not_checked");
    runCheckboxAction(lesson.id, action, input);
  }));
  document.querySelectorAll("[data-close-action]").forEach(input => input.addEventListener("change", () => {
    const baseAction = input.dataset.closeAction || "";
    const action = input.checked ? `${baseAction}_done` : `${baseAction}_not_checked`;
    runCheckboxAction(lesson.id, action, input);
  }));
  const reportBtn = $("generateReport");
  if (reportBtn) reportBtn.addEventListener("click", () => generateReport(lesson.id, "normal"));
  const uploadBtn = $("uploadPrepResult");
  if (uploadBtn) uploadBtn.addEventListener("click", () => uploadPrepResult(lesson.id));
  const fileInput = $("prepResultFile");
  if (fileInput) fileInput.addEventListener("change", () => {
    const name = fileInput.files?.[0]?.name || "Файл не выбран";
    const label = $("prepFileName");
    if (label) label.textContent = name;
  });
  const markStudyDone = $("markStudyDone");
  if (markStudyDone) markStudyDone.addEventListener("click", async () => {
    const actions = [];
    if (lesson.prepMaterialStatus !== "done") actions.push("prep_material_done");
    if (lesson.prepVideoStatus !== "done") actions.push("prep_video_done");
    if (lesson.prepPracticeStatus !== "done") actions.push("prep_practice_done");
    if (!actions.length) { setNotice("Пункты изучения уже отмечены", "ok"); return; }
    try {
      for (const action of actions) await apiPost("/api/action", { lessonId: lesson.id, action, comment: "" });
      setNotice("Пункты изучения отмечены", "ok");
      await openLesson(lesson.id, { force: true });
      window.setTimeout(loadLessons, 250);
      await loadTasks();
      if (canUseAdmin()) await safeRefresh("lesson-study-done", loadAdmin);
    } catch (e) { setNotice(safeUserError(e), "error"); }
  });
  showLessonModal();
}

async function openLesson(id, opts = {}) {
  const force = !!opts.force;
  const silent = !!opts.silent;
  const cached = state.lessonCache[id];
  const cacheFresh = cached && (Date.now() - cached.ts < 120000);

  if (cached && !force) {
    state.selectedLesson = cached.data;
    renderLessonModal(cached.data);
    if (!silent) setNotice("Занятие открыто", "ok");
    if (!cacheFresh) window.setTimeout(() => refreshLessonSilently(id), 150);
    return;
  }

  try {
    if (!silent) {
      setNotice("Открываю занятие...", "");
      renderLessonSkeleton();
    }
    const data = await fetchLessonDetail(id);
    state.selectedLesson = data;
    state.lessonCache[id] = { data, ts: Date.now() };
    renderLessonModal(data);
    if (!silent) setNotice("Занятие открыто", "ok");
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

async function uploadPrepResult(lessonId) {
  const input = $("prepResultFile");
  const file = input?.files?.[0];
  if (!file) {
    setNotice("Сначала выберите файл результата", "error");
    return;
  }
  try {
    setNotice("Отправляю результат...", "");
    const form = new FormData();
    appendAuthForm(form);
    form.append("lessonId", lessonId);
    form.append("file", file, file.name);
    const res = await fetch("/api/prep-result-upload", { method: "POST", body: form });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Ошибка загрузки файла");
    setNotice(data.message || "Результат отправлен", "ok");
    await openLesson(lessonId, { force: true });
    await loadTasks();
    if (canUseAdmin()) await safeRefresh("prep-upload", loadAdmin);
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

async function generateReport(lessonId, variant = "normal") {
  try {
    const details = $("reportDetails") ? $("reportDetails").value.trim() : "";
    const data = await apiGet(`/api/report?lessonId=${encodeURIComponent(lessonId)}&variant=${encodeURIComponent(variant)}&details=${encodeURIComponent(details)}`);
    const box = $("reportBox");
    const reportText = stripReportMarkup(data.report || "");
    box.className = "proto-box report-box";
    box.innerHTML = `<div class="report-head">
        <div class="box-icon report-icon">🧾</div>
        <div>
          <h3>Отчёт родителям</h3>
          <p>Готовый текст без HTML-разметки. Его можно скопировать и отправить в родительский чат.</p>
        </div>
      </div>
      <div id="reportText" class="report-text">${escapeHtml(reportText)}</div>
      <div class="report-actions">
        <button class="green" id="copyReport">Скопировать отчёт</button>
        <button class="blue report-variant" data-v="short">Короче</button>
        <button class="blue report-variant" data-v="detailed">Подробнее</button>
        <button class="blue report-variant" data-v="soft">Мягче</button>
        <button class="green" data-action="parent_report_done">Отчёт отправлен</button>
      </div>`;
    const copyBtn = $("copyReport");
    if (copyBtn) copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(reportText);
        setNotice("Отчёт скопирован", "ok");
      } catch (_) {
        setNotice("Не удалось скопировать автоматически. Выделите текст вручную.", "error");
      }
    });
    box.querySelectorAll(".report-variant").forEach(b => b.addEventListener("click", () => generateReport(lessonId, b.dataset.v)));
    box.querySelectorAll("[data-action]").forEach(btn => btn.addEventListener("click", () => runAction(lessonId, btn.dataset.action)));
    setNotice("Отчёт сформирован", "ok");
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

async function runAction(lessonId, action) {
  let comment = "";
  if (action === "problem") {
    comment = prompt("Кратко опишите проблему:") || "";
    if (!comment.trim()) return;
  }
  if (action === "prepare_help") {
    comment = prompt("Что непонятно по теме или какая нужна помощь?") || "";
    if (!comment.trim()) return;
  }
  if (action === "lesson_comment") {
    comment = prompt("Комментарий для МойКласс / замены. Например: где остановились, кто отстаёт, что важно знать следующему преподавателю.") || "";
    if (!comment.trim()) return;
  }
  try {
    const result = await apiPost("/api/action", { lessonId, action, comment });
    applyLocalActionPatch(lessonId, action);
    if (result.control) {
      if (state.selectedLesson?.lesson?.id === lessonId) applyControlPatch(state.selectedLesson.lesson, result.control);
      if (state.lessonCache[lessonId]?.data?.lesson) applyControlPatch(state.lessonCache[lessonId].data.lesson, result.control);
      const lessonListItem = state.lessons.find(item => item.id === lessonId);
      if (lessonListItem) applyControlPatch(lessonListItem, result.control);
    }

    if (action === "close" && state.selectedLesson?.lesson?.id === lessonId) {
      setNotice("Занятие закрыто", "ok");
      state.lessonCache[lessonId] = { data: state.selectedLesson, ts: Date.now() };
      renderLessonModal(state.selectedLesson);
      renderLessonsQuietly();
      window.setTimeout(() => loadLessons(), 250);
      window.setTimeout(() => loadTasks(), 250);
      if (canUseAdmin()) window.setTimeout(() => safeRefresh("lesson-close", loadAdmin), 300);
      return;
    }

    setNotice("Статус сохранён", "ok");
    await openLesson(lessonId, { force: true });
    await loadLessons();
    await loadTasks();
    if (canUseAdmin()) await safeRefresh("lesson-action", loadAdmin);
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

async function loadMe() {
  const data = await apiGet("/api/me");
  state.me = data.me;
  setupRoleUi();
  const roleText = state.me.roleLabel || roleLabel(state.me.role);
  const testText = state.me.testMode?.enabled ? " · тестовая роль" : "";
  const displayName = state.me.resolvedDisplayName || state.me.mkTeacherName || state.me.fullName || "Сотрудник";
  setNotice(`${displayName}: ${roleText}${testText}${data.me.devMode ? " · dev" : ""}`, "ok");
}
async function loadLessons() {
  if (!canUseLessons()) {
    state.lessons = [];
    renderLessonsUnavailable();
    return;
  }
  try {
    const data = await apiGet("/api/lessons?days=7");
    state.lessons = data.items || [];
    renderLessons();
    renderTasks();
    scheduleLessonPreload(state.lessons);
  } catch (e) {
    console.error("[loadLessons]", e);
  }
}

function scheduleLessonPreload(items) {
  const lessons = (items || []).filter(item => item?.id && !state.lessonCache[item.id] && !state.lessonFetches[item.id]).slice(0, 2);
  if (!lessons.length) return;
  if (state.lessonPreloadTimer) window.clearTimeout(state.lessonPreloadTimer);
  const run = async () => {
    for (const item of lessons) {
      try {
        const data = await fetchLessonDetail(item.id);
        state.lessonCache[item.id] = { data, ts: Date.now() };
      } catch (_) {}
    }
  };
  const schedule = window.requestIdleCallback
    ? () => window.requestIdleCallback(run, { timeout: 1400 })
    : () => { state.lessonPreloadTimer = window.setTimeout(run, 500); };
  schedule();
}
async function loadTasks() {
  try {
    if (isClientManagerRole()) {
      const data = await apiGet(`/api/client-tasks?status=${encodeURIComponent(state.clientTaskStatusFilter || "active")}&type=${encodeURIComponent(state.clientTaskTypeFilter || "all")}`);
      state.clientTasks = data.items || [];
      state.clientTaskAutoSync = data.autoSync || state.clientTaskAutoSync;
      state.tasks = state.clientTasks;
      renderTasks();
      return;
    }
    const data = await apiGet("/api/tasks");
    state.tasks = data.items || [];
    renderTasks();
  } catch (e) {
    console.error("[loadTasks]", e);
  }
}

function renderAdmin() {
  const data = state.admin || {};
  const stats = data.stats || {};
  $("adminStats").innerHTML = `
    <div class="stat-card"><b>${stats.openTasks || 0}</b><span>открытых задач</span></div>
    <div class="stat-card"><b>${stats.teachers || 0}</b><span>преподавателей</span></div>
    <div class="stat-card"><b>${stats.lessonControls || 0}</b><span>занятий в контроле</span></div>
    <div class="stat-card"><b>${stats.waitingReview || 0}</b><span>работ на проверке</span></div>
    <div class="stat-card"><b>${stats.notClosedPast || 0}</b><span>не закрыто после занятия</span></div>
    <div class="stat-card"><b>${stats.problems || 0}</b><span>проблем</span></div>`;
  renderAdminContent();
}
function adminCard(title, rows) { return `<article class="card"><div class="card-title">${escapeHtml(title)}</div><div class="meta">${rows.map(r => `<div>${r}</div>`).join("")}</div></article>`; }
function adminControlPill(label, value, type = label) {
  const [text, cls] = labelStatus(value, type);
  return pill(text.replace(`${type}: `, `${label}: `), cls);
}

function adminControlAttentionText(value) {
  const map = {
    problem: "Требует внимания",
    review: "Работа на проверке",
    overdue: "Занятие прошло - не закрыто",
    prep: "Подготовка не завершена",
    ok: "В работе",
    closed: "Закрыто",
  };
  return map[value] || "В работе";
}

function adminLessonControlCard(r) {
  const attention = String(r.attention || "ok");
  const prepMissing = Array.isArray(r.prep_missing) ? r.prep_missing : [];
  const closeMissing = Array.isArray(r.close_missing) ? r.close_missing : [];
  const prepLine = prepMissing.length ? `Не готово: ${prepMissing.join(", ")}` : "Подготовка завершена.";
  const closeLine = closeMissing.length ? `Осталось закрыть: ${closeMissing.join(", ")}` : "Закрытие заполнено.";
  const statusRow = [
    adminControlPill("Подготовка", r.preparation_status, "Подготовка"),
    adminControlPill("Файл", r.prep_result_status, "Файл"),
    adminControlPill("Занятие", r.lesson_status, "Занятие"),
    adminControlPill("Отчёт", r.parent_report_status, "Отчёт"),
    adminControlPill("МК", r.myclass_status, "МК"),
    adminControlPill("Работы", r.works_status, "Работы"),
    adminControlPill("Кабинет", r.classroom_status, "Кабинет"),
  ].join("");
  const download = r.prep_result_file_id ? `<a class="download-button" href="${apiDownloadUrl(r.prep_result_file_id)}" target="_blank" rel="noopener">⬇️ Скачать работу</a>` : "";
  return `<article class="card control-card control-${escapeHtml(attention)}" data-lesson-id="${escapeHtml(r.lesson_id || "")}">
    <div class="control-topline">
      <div>
        <div class="control-attention">${escapeHtml(adminControlAttentionText(attention))}</div>
        <div class="card-title">${escapeHtml(r.group_name || "Занятие")}</div>
      </div>
      <span class="control-date">${escapeHtml((r.lesson_date || "-") + " " + (r.lesson_time || ""))}</span>
    </div>
    <div class="meta">
      <div><b>Преподаватель:</b> ${escapeHtml(r.teacher_name || r.teacher_user_id || "-")}</div>
      <div><b>Тема:</b> ${escapeHtml(r.lesson_topic || "тема не указана")}</div>
      <div><b>Подготовка:</b> ${escapeHtml(prepLine)}</div>
      <div><b>Закрытие:</b> ${escapeHtml(closeLine)}</div>
      ${r.preparation_comment ? `<div><b>Комментарий подготовки:</b> ${escapeHtml(r.preparation_comment)}</div>` : ""}
      ${r.problem_comment ? `<div><b>Проблема:</b> ${escapeHtml(r.problem_comment)}</div>` : ""}
    </div>
    <div class="status-row control-status-row">${statusRow}</div>
    <div class="actions control-actions">
      <button class="secondary admin-open-lesson" data-id="${escapeHtml(r.lesson_id || "")}">Открыть карточку</button>
      ${download}
    </div>
  </article>`;
}

async function renderAdminContent() {
  const root = $("adminContent");
  const tab = state.adminTab;
  try {
    if (tab === "overview") {
      const tasks = state.admin?.tasks || [];
      const problems = state.admin?.problems || [];
      root.innerHTML = [
        adminCard("Открытые задачи", tasks.slice(0, 8).map(t => `<b>${escapeHtml(t.title || "Задача")}</b><br>${nl2br(t.text || "")}`) || ["Нет задач"]),
        adminCard("Проблемы", problems.slice(0, 8).map(p => `${escapeHtml(p.group_name || p.lesson_id)}: ${escapeHtml(p.problem_comment || p.problem_status || "проблема")}`) || ["Нет проблем"]),
      ].join("");
      return;
    }
    if (tab === "lesson-control") {
      const data = await apiGet("/api/admin/lesson-controls");
      const stats = data.stats || {};
      const items = data.items || [];
      const summary = `<div class="control-summary">
        <div><b>${stats.waitingReview || 0}</b><span>работ на проверке</span></div>
        <div><b>${stats.notClosedPast || 0}</b><span>прошли, но не закрыты</span></div>
        <div><b>${stats.problems || 0}</b><span>проблем / доработок</span></div>
        <div><b>${stats.closed || 0}</b><span>закрыто</span></div>
      </div>`;
      root.innerHTML = summary + (items.map(adminLessonControlCard).join("") || `<div class="empty">Нет занятий в контроле. Нажмите «Проверить МойКласс», чтобы подтянуть расписание.</div>`);
      root.querySelectorAll(".admin-open-lesson").forEach(btn => btn.addEventListener("click", () => openLesson(btn.dataset.id, { force: true })));
      return;
    }
    if (tab === "teachers") {
      const data = await apiGet("/api/admin/teachers");
      root.innerHTML = (data.items || []).map(p => adminCard(p.full_name || p.username || String(p.user_id), [`<b>Статус:</b> ${escapeHtml(p.teacher_status || "-")}`, `<b>Тип:</b> ${escapeHtml(p.profile_type || "-")}`, `<b>Роль:</b> ${escapeHtml(p.role || "-")}`, `<b>МК teacherId:</b> ${escapeHtml(p.mk_teacher_id || "-")}`])).join("") || `<div class="empty">Нет профилей преподавателей.</div>`;
      return;
    }
    if (tab === "prep-results") {
      const data = await apiGet("/api/admin/prep-results");
      root.innerHTML = (data.items || []).map(r => {
        const status = r.status === "approved" ? "✅ подтверждено" : r.status === "rejected" ? "❌ отклонено" : "⏳ ожидает проверки";
        const size = formatFileSize(r.size_bytes);
        return `<article class="card prep-review-card" data-file-id="${escapeHtml(r.id)}">
          <div class="card-title">${escapeHtml(r.file_name || "Файл результата")}</div>
          <div class="meta">
            <div><b>Статус:</b> ${status}</div>
            <div><b>Преподаватель:</b> ${escapeHtml(r.full_name || r.teacher_name || r.teacher_user_id || "-")}</div>
            <div><b>Группа:</b> ${escapeHtml(r.group_name || "-")}</div>
            <div><b>Дата/время:</b> ${escapeHtml((r.lesson_date || "-") + " " + (r.lesson_time || ""))}</div>
            <div><b>Тема:</b> ${escapeHtml(r.lesson_topic || "-")}</div>
            <div><b>Размер:</b> ${escapeHtml(size || "-")}</div>
            ${r.reviewer_comment ? `<div><b>Комментарий:</b> ${escapeHtml(r.reviewer_comment)}</div>` : ""}
          </div>
          <div class="actions">
            <a class="download-button" href="${apiDownloadUrl(r.id)}" target="_blank" rel="noopener">⬇️ Скачать файл</a>
            <button class="green review-result" data-id="${escapeHtml(r.id)}" data-decision="approved">✅ Подтвердить допуск</button>
            <button class="red review-result" data-id="${escapeHtml(r.id)}" data-decision="rejected">❌ Отклонить</button>
          </div>
        </article>`;
      }).join("") || `<div class="empty">Файлов на проверку пока нет.</div>`;
      root.querySelectorAll(".review-result").forEach(btn => btn.addEventListener("click", () => reviewPrepResult(btn.dataset.id, btn.dataset.decision)));
      return;
    }
    if (tab === "work-schedule") {
      const data = await apiGet(`/api/admin/work-schedule?week=${encodeURIComponent(state.adminWorkScheduleWeek || "current")}`);
      const items = data.items || [];
      const locationFilter = state.adminWorkLocationFilter || "all";
      const filteredItems = items.filter(item => {
        const location = String(item.location || "Любой формат");
        return locationFilter === "all" || location.includes(locationFilter);
      });
      const byDay = new Map();
      for (const item of filteredItems) {
        const day = Number(item.day_of_week ?? 0);
        if (!byDay.has(day)) byDay.set(day, []);
        byDay.get(day).push(item);
      }
      const switcher = `<div class="week-switch admin-week-switch">
        <button type="button" class="${state.adminWorkScheduleWeek === "current" ? "active" : ""}" data-admin-work-week="current">Эта неделя</button>
        <button type="button" class="${state.adminWorkScheduleWeek === "next" ? "active" : ""}" data-admin-work-week="next">Следующая неделя</button>
      </div>`;
      const filters = `<div class="admin-work-filters">
        <select id="adminWorkLocationFilter" aria-label="Формат или место">
          <option value="all">Все форматы</option>
          <option value="Кульман" ${locationFilter === "Кульман" ? "selected" : ""}>Кульман 1/1</option>
          <option value="Мстиславца" ${locationFilter === "Мстиславца" ? "selected" : ""}>Мстиславца 6</option>
          <option value="Онлайн" ${locationFilter === "Онлайн" ? "selected" : ""}>Онлайн</option>
        </select>
      </div>`;
      const summary = `<div class="control-summary">
        <div><b>${filteredItems.length}</b><span>окон по фильтру</span></div>
        <div><b>${new Set(filteredItems.map(x => x.user_id || x.mk_teacher_id).filter(Boolean)).size}</b><span>преподавателей</span></div>
        <div><b>${items.filter(x => String(x.location || "").includes("Кульман")).length}</b><span>Кульман 1/1</span></div>
        <div><b>${items.filter(x => String(x.location || "").includes("Мстиславца")).length}</b><span>Мстиславца 6</span></div>
      </div>`;
      root.innerHTML = switcher + filters + summary + (WEEK_DAYS.map((name, day) => {
        const slots = (byDay.get(day) || []).slice().sort((a, b) => String(a.start_time || "").localeCompare(String(b.start_time || "")));
        return slots.length ? adminWorkScheduleSection(slots, name) : "";
      }).join("") || `<div class="empty">На выбранную неделю по выбранному формату нет рабочих возможностей.</div>`);
      root.querySelectorAll("[data-admin-work-week]").forEach(btn => btn.addEventListener("click", () => {
        state.adminWorkScheduleWeek = btn.dataset.adminWorkWeek === "next" ? "next" : "current";
        renderAdminContent();
      }));
      root.querySelector("#adminWorkLocationFilter")?.addEventListener("change", (event) => {
        state.adminWorkLocationFilter = event.target.value || "all";
        renderAdminContent();
      });
      return;
    }
    if (tab === "tasks") {
      const data = await apiGet("/api/admin/tasks");
      root.innerHTML = (data.items || []).map(t => adminCard(t.title || "Задача", [`<b>Тип:</b> ${escapeHtml(t.task_type || "-")}`, `<b>userId:</b> ${escapeHtml(t.user_id || "-")}`, `<b>Дедлайн:</b> ${escapeHtml(t.due_at || "-")}`, nl2br(t.text || "")])).join("") || `<div class="empty">Нет открытых задач.</div>`;
      return;
    }
    if (tab === "users") {
      const data = await apiGet("/api/admin/users");
      const canManage = !!roleCaps().canManageUsers;
      const myUid = Number(state.me?.userId || state.me?.user_id || 0);
      console.log("Staff UI version: 7.0.26");

      const STAFF_ROLE_DISPLAY = {
        owner: "Владелец",
        admin: "Администратор",
        teacher: "Преподаватель",
        methodist: "Методист",
        intern: "Стажёр",
        client_manager: "Клиентский менеджер",
        director: "Директор",
        operations: "Операционный менеджер",
        kitchen: "Кухня",
        restaurant: "Кухня (alias)",
        other: "Другой",
      };
      const roleOptions = [
        {v:"teacher",        l:"Преподаватель"},
        {v:"methodist",      l:"Методист"},
        {v:"intern",         l:"Стажёр"},
        {v:"client_manager", l:"Клиентский менеджер"},
        {v:"director",       l:"Директор"},
        {v:"operations",     l:"Операционный менеджер"},
        {v:"kitchen",        l:"Кухня"},
        {v:"admin",          l:"Администратор"},
        {v:"owner",          l:"Владелец"},
        {v:"other",          l:"Другой"},
      ];

      root.innerHTML = (data.items || []).map(u => {
        const uid = u.user_id;
        const isSelf = Number(uid) === myUid;
        const isOwner = u.role === "owner";
        const isInactive = u.status === "inactive";
        const mkTeacherId = u.mk_teacher_id || "";
        const mkTeacherName = u.mk_teacher_name || "";
        // Backend supplies resolved_display_name (mk_teacher_name > full_name > username > fallback)
        const displayName = u.resolved_display_name || u.mk_teacher_name || u.full_name || u.username || String(uid);
        const roleLbl = STAFF_ROLE_DISPLAY[u.role] || (u.role ? `Неизвестная роль: ${u.role}` : "-");
        const statusLbl = isInactive ? "Отключён" : (u.status || "active");
        const selectVal = u.role === "restaurant" ? "kitchen" : (u.role || "");

        // Warnings from backend
        const warningsHtml = (u.warnings || []).map(w =>
          `<div style="font-size:12px;color:#c07000;margin-top:2px">⚠️ ${escapeHtml(w)}</div>`
        ).join("");

        // MK teacher name row (show only if different from display name or if source is moyklass)
        const mkNameRow = mkTeacherId
          ? `<div style="font-size:12px;color:#555;margin-top:2px"><b>Имя из МК:</b> ${escapeHtml(mkTeacherName || "не синхронизировано")}</div>`
          : "";

        let roleChangeHtml = "";
        if (canManage && !isSelf) {
          const opts = roleOptions.map(o =>
            `<option value="${escapeAttr(o.v)}"${selectVal === o.v ? " selected" : ""}>${escapeHtml(o.l)}</option>`
          ).join("");
          const unknownNote = (!roleOptions.find(o => o.v === selectVal) && selectVal)
            ? `<div style="font-size:12px;color:#888;margin-top:2px">Текущая роль «${escapeHtml(u.role)}» — выберите из списка для изменения</div>`
            : "";
          roleChangeHtml = `<div class="admin-user-role-change" data-uid="${escapeAttr(String(uid))}" style="margin-top:8px">
            <select class="admin-role-select">${opts}</select>
            <button type="button" class="admin-role-save-btn secondary" data-uid="${escapeAttr(String(uid))}">Сохранить</button>
          </div>${unknownNote}`;
        }

        // MK sync / unlink / picker buttons
        let mkActionsHtml = "";
        if (canManage) {
          const syncUnlinkBtns = mkTeacherId ? `
            <button type="button" class="admin-sync-mk-btn secondary btn-sm" style="margin-top:6px;margin-right:4px"
              data-uid="${escapeAttr(String(uid))}"
              data-name="${escapeAttr(displayName)}">Обновить имя из МК</button>
            <button type="button" class="admin-unlink-teacher-btn btn-sm" style="margin-top:6px;margin-right:4px;background:#e67e22;color:#fff;border:none;border-radius:8px;padding:6px 12px;cursor:pointer"
              data-uid="${escapeAttr(String(uid))}"
              data-mk-id="${escapeAttr(mkTeacherId)}"
              data-name="${escapeAttr(displayName)}"
              data-role="${escapeAttr(u.role || "")}">Отвязать MK teacherId</button>` : "";
          const pickerBtn = `<button type="button" class="admin-mk-picker-btn secondary btn-sm" style="margin-top:6px"
              data-uid="${escapeAttr(String(uid))}"
              data-current-mk-id="${escapeAttr(mkTeacherId || "")}"
              data-name="${escapeAttr(displayName)}">🔗 Выбрать преподавателя из МойКласс</button>
            <div class="mk-picker-container" data-picker-for="${escapeAttr(String(uid))}" style="display:none"></div>`;
          mkActionsHtml = syncUnlinkBtns + pickerBtn;
        }

        // Teacher diagnostics button (only for teacher-like roles)
        const isTeacherLike = ["teacher", "methodist", "intern"].includes(u.role || "");
        const teacherDiagHtml = canManage && isTeacherLike
          ? `<button type="button" class="admin-teacher-diag-btn secondary btn-sm" style="margin-top:6px"
              data-uid="${escapeAttr(String(uid))}">🔍 Проверить доступ преподавателя</button>
             <div class="teacher-diag-container" data-diag-for="${escapeAttr(String(uid))}" style="display:none"></div>`
          : "";

        let deactivateHtml = "";
        if (canManage && !isSelf && !isOwner) {
          if (isInactive) {
            deactivateHtml = `<button type="button" class="admin-activate-btn secondary btn-sm" style="margin-top:6px"
              data-uid="${escapeAttr(String(uid))}"
              data-name="${escapeAttr(displayName)}">Восстановить доступ</button>`;
          } else {
            deactivateHtml = `<button type="button" class="admin-deactivate-btn btn-sm" style="margin-top:6px;background:#c0392b;color:#fff;border:none;border-radius:8px;padding:6px 12px;cursor:pointer"
              data-uid="${escapeAttr(String(uid))}"
              data-role="${escapeAttr(u.role || "")}"
              data-name="${escapeAttr(displayName)}">Отключить доступ</button>`;
          }
        }

        return adminCard(displayName, [
          `<b>Роль:</b> ${escapeHtml(roleLbl)}`,
          `<b>Статус:</b> ${escapeHtml(statusLbl)}`,
          `<b>Telegram ID:</b> ${escapeHtml(String(uid || "-"))}`,
          `<b>МК teacherId:</b> ${escapeHtml(mkTeacherId || "-")}`,
          mkNameRow,
          warningsHtml,
          roleChangeHtml,
          mkActionsHtml,
          deactivateHtml,
          teacherDiagHtml,
        ]);
      }).join("") || `<div class="empty">Нет сотрудников.</div>`;

      if (canManage) {
        root.querySelectorAll(".admin-role-save-btn").forEach(btn => {
          btn.addEventListener("click", async () => {
            const uid = btn.dataset.uid;
            const sel = btn.closest(".admin-user-role-change")?.querySelector(".admin-role-select");
            if (!sel) return;
            btn.disabled = true;
            try {
              const res = await apiPost("/api/admin/set-user-role", { user_id: Number(uid), role: sel.value });
              if (!res.ok) throw new Error(res.error || "Ошибка");
              setNotice(`Роль изменена на «${sel.options[sel.selectedIndex].text}»`, "ok");
              await renderAdminContent();
            } catch (e) { setNotice(safeUserError(e), "error"); }
            btn.disabled = false;
          });
        });

        root.querySelectorAll(".admin-sync-mk-btn").forEach(btn => {
          btn.addEventListener("click", async () => {
            const uid = btn.dataset.uid;
            const name = btn.dataset.name;
            btn.disabled = true;
            try {
              const res = await apiPost(`/api/admin/staff/${uid}/sync-mk-name`, {});
              if (!res.ok) throw new Error(res.error || "Ошибка");
              setNotice(`Имя обновлено из МойКласс: ${res.new_name}`, "ok");
              await renderAdminContent();
            } catch (e) { setNotice(safeUserError(e), "error"); btn.disabled = false; }
          });
        });

        root.querySelectorAll(".admin-unlink-teacher-btn").forEach(btn => {
          btn.addEventListener("click", () => {
            const uid = btn.dataset.uid;
            const name = btn.dataset.name;
            const mkId = btn.dataset.mkId;
            const role = btn.dataset.role;
            _confirmUnlinkTeacher(name, uid, mkId, STAFF_ROLE_DISPLAY[role] || role, async () => {
              try {
                const res = await apiPost(`/api/admin/staff/${uid}/unlink-teacher`, {});
                if (!res.ok) throw new Error(res.error || "Ошибка");
                const warn = (res.warnings || []).join(" ");
                setNotice(`MK teacherId отвязан.${warn ? " " + warn : ""}`, "ok");
                await renderAdminContent();
              } catch (e) { setNotice(safeUserError(e), "error"); }
            });
          });
        });

        root.querySelectorAll(".admin-deactivate-btn").forEach(btn => {
          btn.addEventListener("click", () => {
            const uid = btn.dataset.uid;
            const name = btn.dataset.name;
            const role = btn.dataset.role;
            _confirmStaffDeactivate(name, uid, STAFF_ROLE_DISPLAY[role] || role, async () => {
              try {
                const res = await apiPost(`/api/admin/staff/${uid}/deactivate`, {});
                if (!res.ok) throw new Error(res.error || "Ошибка");
                setNotice(`Доступ отключён: ${name}`, "ok");
                await renderAdminContent();
              } catch (e) { setNotice(safeUserError(e), "error"); }
            });
          });
        });

        root.querySelectorAll(".admin-activate-btn").forEach(btn => {
          btn.addEventListener("click", async () => {
            const uid = btn.dataset.uid;
            const name = btn.dataset.name;
            btn.disabled = true;
            try {
              const res = await apiPost(`/api/admin/staff/${uid}/activate`, {});
              if (!res.ok) throw new Error(res.error || "Ошибка");
              setNotice(`Доступ восстановлен: ${name}`, "ok");
              await renderAdminContent();
            } catch (e) { setNotice(safeUserError(e), "error"); btn.disabled = false; }
          });
        });

        root.querySelectorAll(".admin-teacher-diag-btn").forEach(btn => {
          btn.addEventListener("click", () => {
            const uid = btn.dataset.uid;
            const container = root.querySelector(`.teacher-diag-container[data-diag-for="${CSS.escape(uid)}"]`);
            if (!container) return;
            if (container.style.display === "none") {
              container.style.display = "";
              _loadTeacherDiagnostics(uid, container);
            } else {
              container.style.display = "none";
            }
          });
        });

        root.querySelectorAll(".admin-mk-picker-btn").forEach(btn => {
          btn.addEventListener("click", () => {
            const uid = btn.dataset.uid;
            const displayName = btn.dataset.name || "";
            const currentMkId = btn.dataset.currentMkId || "";
            const container = root.querySelector(`.mk-picker-container[data-picker-for="${CSS.escape(uid)}"]`);
            if (!container) return;
            if (container.style.display === "none") {
              container.style.display = "";
              _loadMkTeacherPicker(uid, displayName, currentMkId, container);
            } else {
              container.style.display = "none";
            }
          });
        });
      }
      // ── MoyKlass staff binding panel (reverse direction) ──
      if (canManage) {
        const mkBindPanel = document.createElement("div");
        mkBindPanel.style.marginTop = "20px";
        root.appendChild(mkBindPanel);
        _initMkStaffBindPanel(mkBindPanel);
      }
      return;
    }
    if (tab === "notion") {
      const data = await apiGet("/api/admin/notion-status");
      root.innerHTML = adminCard("Notion / база знаний", [`<b>Файлов Notion:</b> ${escapeHtml(data.files)}`, `<b>Manifest страниц:</b> ${escapeHtml(data.manifestCount)}`, `<b>Файлов KB:</b> ${escapeHtml(data.kbFiles)}`, `<b>Фрагментов KB:</b> ${escapeHtml(data.kbChunks)}`, `<b>Папка:</b> ${escapeHtml(data.syncDir)}`]);
      return;
    }
    if (tab === "notifications") {
      const data = await apiGet("/api/admin/notifications");
      root.innerHTML = (data.items || []).map(n => adminCard(`${n.event_type || "event"} · ${n.created_at || ""}`, [`<b>userId:</b> ${escapeHtml(n.user_id || "-")}`, `<b>lessonId:</b> ${escapeHtml(n.lesson_id || "-")}`, `<b>Отправлено:</b> ${n.sent_ok ? "да" : "нет"}`, nl2br(n.text || ""), n.error ? `<b>Ошибка:</b> ${escapeHtml(n.error)}` : ""])).join("") || `<div class="empty">Журнал уведомлений пуст.</div>`;
      return;
    }
    if (tab === "kpi") {
      const period = state.adminKpiPeriod || "month";
      const periods = [
        { key: "today", label: "Сегодня" },
        { key: "week", label: "Неделя" },
        { key: "month", label: "Месяц" },
      ];
      const periodBtns = periods.map(p =>
        `<button type="button" class="kpi-period-btn${period === p.key ? " active" : ""}" data-kpi-period="${p.key}">${p.label}</button>`
      ).join("");
      const bodyHtml = state.adminKpiData
        ? renderKpiAdminContent(state.adminKpiData)
        : state.adminKpiBusy
          ? `<div class="kpi-loading">Загружаю KPI...</div>`
          : `<div class="empty">Нажмите «Обновить» для загрузки данных.</div>`;
      root.innerHTML = `<div class="kpi-admin-controls">
        <div class="kpi-period-group">${periodBtns}</div>
        <button type="button" class="secondary" id="kpiAdminRefresh">Обновить</button>
      </div>
      <div id="kpiAdminContent">${bodyHtml}</div>`;
      root.querySelectorAll("[data-kpi-period]").forEach(btn =>
        btn.addEventListener("click", () => loadEmployeeKpi(btn.dataset.kpiPeriod)));
      root.querySelector("#kpiAdminRefresh")?.addEventListener("click", () =>
        loadEmployeeKpi(state.adminKpiPeriod));
      if (!state.adminKpiData && !state.adminKpiBusy) loadEmployeeKpi(period);
      return;
    }
    if (tab === "interns") {
      root.innerHTML = `<div class="kpi-loading">Загружаю стажёров...</div>`;
      const data = await loadAdminInterns();
      root.innerHTML = data ? renderAdminInternsContent(data) : `<div class="empty">Не удалось загрузить данные стажёров.</div>`;
      _bindAdminInternEvents(root);
      return;
    }
    if (tab === "food-debug") {
      renderFoodDebugPanel(root);
      return;
    }
    if (tab === "food-children") {
      renderCampChildrenPanel(root);
      return;
    }
    if (tab === "food-menu") {
      await renderFoodMenuPanel(root);
      return;
    }
    if (tab === "food-report") {
      await renderFoodReportPanel(root);
      return;
    }
    if (tab === "food-lunch") {
      await renderStaffFoodLunch(root);
      return;
    }
  } catch (e) { root.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
}
function _renderActiveCampWeekInfo(aw) {
  if (!aw || !aw.startDate) return "";
  const reason = _foodActiveWeekReasonLabel(aw.reason);
  const kids = aw.childrenUniqueCount != null ? ` · детей: <b>${aw.childrenUniqueCount}</b>` : "";
  const lessons = aw.lessonsCount != null ? ` · занятий: <b>${aw.lessonsCount}</b>` : "";
  const groups = Array.isArray(aw.groups) && aw.groups.length ? ` · группы: ${aw.groups.map(g => escapeHtml(g)).join(", ")}` : "";
  return `<div class="food-debug-summary">
    📅 Активная неделя: <b>${escapeHtml(aw.startDate)} — ${escapeHtml(aw.endDate)}</b>
    &nbsp;|&nbsp; Режим: <b>${escapeHtml(_foodWeekModeLabel(aw.mode || "auto"))}</b>
    &nbsp;|&nbsp; Причина: ${escapeHtml(reason)}${lessons}${kids}${groups}
  </div>`;
}

function _renderCampWeeksList(campWeeks) {
  if (!Array.isArray(campWeeks) || !campWeeks.length) return "";
  const rows = campWeeks.map(w => {
    const groups = Array.isArray(w.groups) && w.groups.length ? w.groups.join(", ") : "—";
    const kids = w.childrenUniqueCount != null ? `, детей: ${w.childrenUniqueCount}` : "";
    return `<div class="food-debug-user"><b>${escapeHtml(w.key)}</b> · ${escapeHtml(w.startDate || "")}–${escapeHtml(w.endDate || "")} · занятий: ${w.lessonsCount || 0}${kids} · группы: ${escapeHtml(groups)}</div>`;
  }).join("");
  return `<div class="food-debug-class"><b>Все найденные недели смены:</b>${rows}</div>`;
}

function _foodWeekModeLabel(mode) {
  if (mode === "manual") return "ручной (manual)";
  return "авто (auto)";
}
function _foodActiveWeekReasonLabel(reason) {
  const map = {
    current_week: "текущая неделя",
    nearest_future: "ближайшая будущая",
    last_past: "последняя прошедшая",
    manual_override: "ручной режим",
    no_weeks_found: "недели не найдены",
  };
  return map[reason] || reason || "";
}

function renderFoodDebugPanel(root) {
  const filter = escapeHtml(state.me?.campClassNameFilter || "Summer Camp");
  const weekMode = state.me?.campActiveWeekMode || "auto";
  const lastResult = state.foodDebugLastResult;
  const activeWeekHtml = lastResult?.activeCampWeek
    ? _renderActiveCampWeekInfo(lastResult.activeCampWeek)
    : `<p class="food-debug-summary">Режим недели: <b>${escapeHtml(_foodWeekModeLabel(weekMode))}</b> · запустите диагностику для определения активной недели</p>`;
  const caps = state.me?.capabilities || {};
  const adminTabs = Array.isArray(caps.adminTabs) ? caps.adminTabs : [];
  const uiFlagsHtml = `
    <details style="margin:10px 0">
      <summary style="font-size:13px;color:var(--color-text-secondary,#555);cursor:pointer">UI flags (debug)</summary>
      <div class="food-debug-summary" style="margin-top:6px">
        foodModuleEnabled: <b>${escapeHtml(String(state.me?.foodModuleEnabled ?? "?"))}</b><br>
        foodMenuOcrEnabled (me): <b>${escapeHtml(String(state.me?.foodMenuOcrEnabled ?? "?"))}</b><br>
        foodMenuOcrEnabled (caps): <b>${escapeHtml(String(state.me?.capabilities?.foodMenuOcrEnabled ?? "?"))}</b><br>
        canUseFoodMenuOcr(): <b>${escapeHtml(String(canUseFoodMenuOcr()))}</b><br>
        mvpReleaseMode: <b>${escapeHtml(String(state.me?.mvpReleaseMode ?? "?"))}</b><br>
        currentRole: <b>${escapeHtml(state.me?.role || "?")}</b><br>
        adminTabs: <b>${escapeHtml(adminTabs.join(", ") || "нет")}</b><br>
        activeAdminTab: <b>${escapeHtml(state.adminTab || "?")}</b>
      </div>
    </details>`;
  root.innerHTML = `
    <div class="food-debug-card">
      <h3>Диагностика · Городская программа дети</h3>
      <p class="food-debug-summary">Фильтр занятий МойКласс: <code>${filter}</code></p>
      ${activeWeekHtml}
      ${uiFlagsHtml}
      <div class="food-debug-data-status" id="foodDataStatus">
        <div class="food-debug-data-status-head">
          <span>Состояние food data</span>
          <button class="secondary btn-sm" id="foodDataStatusRefresh">Обновить</button>
        </div>
        <div id="foodDataStatusBody" class="food-debug-data-status-body">
          <span class="food-debug-rawkeys">Нажмите «Обновить» для загрузки</span>
        </div>
      </div>
      <div class="food-debug-data-status" id="foodAutoReminderStatus" style="margin-top:8px">
        <div class="food-debug-data-status-head">
          <span>Авто-напоминания (watcher)</span>
          <button class="secondary btn-sm" id="foodAutoReminderRefresh">Обновить</button>
        </div>
        <div id="foodAutoReminderBody" class="food-debug-data-status-body">
          <span class="food-debug-rawkeys">Нажмите «Обновить» для загрузки</span>
        </div>
      </div>
      <div class="food-debug-field-row">
        <label for="foodDebugLessonId">Проверить lessonId вручную:</label>
        <input type="text" id="foodDebugLessonId" class="food-debug-input" placeholder="8472607">
      </div>
      <label class="food-debug-checkbox-row">
        <input type="checkbox" id="foodDebugSave"> Сохранить найденных детей в БД
      </label>
      <div class="food-debug-actions" style="flex-wrap:wrap;gap:8px;">
        <button class="primary" id="foodDebugRun">Запустить диагностику</button>
        <button class="secondary" id="foodDebugClear">Очистить сохранённых детей смены</button>
        <button class="secondary" id="foodDebugCleanupDupes">Убрать дубли детей</button>
      </div>
      <div id="foodDebugResult">${lastResult ? renderFoodDebugResult(lastResult) : ""}</div>
      <div class="food-debug-data-status" id="foodTeacherAccess" style="margin-top:10px">
        <div class="food-debug-data-status-head">
          <span>Доступ преподавателей к питанию (завтра)</span>
          <button class="secondary btn-sm" id="foodTeacherAccessLoad">Загрузить</button>
        </div>
        <div id="foodTeacherAccessBody" class="food-debug-data-status-body">
          <span class="food-debug-rawkeys">Нажмите «Загрузить»</span>
        </div>
      </div>
    </div>`;
  root.querySelector("#foodDebugRun")?.addEventListener("click", runFoodDebugSync);
  root.querySelector("#foodDebugClear")?.addEventListener("click", runFoodDebugClear);
  root.querySelector("#foodDebugCleanupDupes")?.addEventListener("click", runFoodDebugCleanupDuplicates);
  root.querySelector("#foodDataStatusRefresh")?.addEventListener("click", () => loadFoodDataStatus(root.querySelector("#foodDataStatusBody")));
  root.querySelector("#foodAutoReminderRefresh")?.addEventListener("click", () => loadFoodAutoReminderStatus(root.querySelector("#foodAutoReminderBody")));
  root.querySelector("#foodTeacherAccessLoad")?.addEventListener("click", () => loadFoodTeacherAccess(root.querySelector("#foodTeacherAccessBody")));
  if (lastResult) {
    root.querySelector("#foodDebugCopyJson")?.addEventListener("click", _foodDebugCopyJson);
  }
  loadFoodDataStatus(root.querySelector("#foodDataStatusBody"));
  loadFoodAutoReminderStatus(root.querySelector("#foodAutoReminderBody"));
}

async function loadFoodTeacherAccess(el) {
  if (!el) return;
  el.innerHTML = `<span class="food-debug-rawkeys">Загрузка...</span>`;
  try {
    const data = await apiGet("/api/food/staff/tomorrow-teachers");
    if (!data.ok) {
      el.innerHTML = `<span class="food-debug-error" style="display:inline;padding:0">${escapeHtml(data.error || "Ошибка")}</span>`;
      return;
    }
    const teachers = data.teachers || [];
    const dateStr = escapeHtml(data.tomorrowDate || "");
    if (!teachers.length) {
      el.innerHTML = `<div class="food-debug-data-status-row"><span>Завтра ${dateStr}</span><b>нет занятий в БД</b></div>`;
      return;
    }
    const rows = teachers.map(t => {
      const statusIcon = t.hasStaffUser ? "✅" : "❌";
      const statusText = t.hasStaffUser ? "доступ есть" : "нет Telegram-привязки";
      const userNote = t.username ? ` · @${escapeHtml(t.username)}` : (t.userId ? ` · id ${escapeHtml(String(t.userId))}` : "");
      const locNote = Array.isArray(t.locationCodes) && t.locationCodes.length ? ` · <b>${t.locationCodes.map(c => escapeHtml(c)).join(", ")}</b>` : "";
      return `<div class="food-debug-data-status-row">
        <span>${escapeHtml(t.teacherName || t.mkTeacherId || "")}${userNote}${locNote}</span>
        <b>${statusIcon} ${escapeHtml(statusText)}</b>
      </div>`;
    }).join("");
    el.innerHTML = `<div class="food-debug-data-status-row" style="font-weight:700"><span>Завтра ${dateStr}: преподавателей с занятием</span><b>${teachers.length}</b></div>${rows}`;
  } catch (e) {
    el.innerHTML = `<span class="food-debug-error" style="display:inline;padding:0">${escapeHtml(e.message)}</span>`;
  }
}

async function loadFoodDataStatus(el) {
  if (!el) return;
  el.innerHTML = `<span class="food-debug-rawkeys">Загрузка...</span>`;
  try {
    const data = await apiGet("/api/food/debug/data-status");
    if (!data.ok) {
      el.innerHTML = `<span class="food-debug-error" style="display:inline;padding:0">${escapeHtml(data.error || "Ошибка")}</span>`;
      return;
    }
    el.innerHTML = `
      <div class="food-debug-data-status-row"><span>Дети городской программы в БД</span><b>${data.campChildren ?? 0}</b></div>
      <div class="food-debug-data-status-row"><span>Коды привязки (активные)</span><b>${data.activeLinkCodes ?? 0}</b></div>
      <div class="food-debug-data-status-row"><span>Привязок родителей</span><b>${data.parentLinks ?? 0}</b></div>
      <div class="food-debug-data-status-row"><span>Меню</span><b>${data.foodMenus ?? 0}</b></div>
      <div class="food-debug-data-status-row"><span>Блюд (доступных)</span><b>${data.foodItems ?? 0}</b></div>
      <div class="food-debug-data-status-row"><span>Заказов</span><b>${data.foodOrders ?? 0}</b></div>`;
  } catch (e) {
    el.innerHTML = `<span class="food-debug-error" style="display:inline;padding:0">${escapeHtml(e.message)}</span>`;
  }
}
async function loadFoodAutoReminderStatus(el) {
  if (!el) return;
  el.innerHTML = `<span class="food-debug-rawkeys">Загрузка...</span>`;
  try {
    const data = await apiGet("/api/food/debug/auto-reminder-status");
    if (!data.ok) {
      el.innerHTML = `<span class="food-debug-error" style="display:inline;padding:0">${escapeHtml(data.error || "Ошибка")}</span>`;
      return;
    }
    const enabledLabel = data.enabled ? `<b style="color:var(--color-ok,#2a7a2a)">Включено</b>` : `<b style="color:var(--color-text-secondary,#888)">Отключено</b>`;
    const lastRun = data.lastRunAt ? _fmtDate(data.lastRunAt.slice(0,10)) + " " + (data.lastRunAt.slice(11,16) || "") : "Ещё не запускался";
    const lastRes = data.lastResult ? `отправлено: ${data.lastResult.sentCount ?? 0}, проверено меню: ${data.lastResult.menusChecked ?? 0}` : "—";
    el.innerHTML = `
      <div class="food-debug-data-status-row"><span>Статус</span>${enabledLabel}</div>
      <div class="food-debug-data-status-row"><span>Окно до дедлайна</span><b>${data.minutesBeforeDeadline ?? 120} мин</b></div>
      <div class="food-debug-data-status-row"><span>Интервал проверки</span><b>${data.checkIntervalMinutes ?? 15} мин</b></div>
      <div class="food-debug-data-status-row"><span>Запусков</span><b>${data.runCount ?? 0}</b></div>
      <div class="food-debug-data-status-row"><span>Последний запуск</span><b>${escapeHtml(lastRun)}</b></div>
      <div class="food-debug-data-status-row"><span>Последний результат</span><b>${escapeHtml(lastRes)}</b></div>`;
  } catch (e) {
    el.innerHTML = `<span class="food-debug-error" style="display:inline;padding:0">${escapeHtml(e.message)}</span>`;
  }
}
function _foodDebugUserHtml(u) {
  const uid = String(u.userId ?? u.user_id ?? u.id ?? u.studentId ?? u.clientId ?? "?");
  const resolvedName = u.resolvedFullName || "";
  const resolveErr = u.resolveError || "";
  const recKeys = Array.isArray(u.rawKeys) ? u.rawKeys.join(", ") : "";
  const userKeys = Array.isArray(u.userRawKeys) && u.userRawKeys.length ? u.userRawKeys.join(", ") : "";
  const nameLabel = resolvedName
    ? `<b>${escapeHtml(resolvedName)}</b>`
    : `<span style="color:var(--color-muted,#888)">Без имени</span>${resolveErr ? ` · <span class="food-debug-error" style="display:inline;padding:0">${escapeHtml(resolveErr)}</span>` : ""}`;
  return `<div class="food-debug-user">${nameLabel} · userId: ${escapeHtml(uid)}${recKeys ? `<br><span class="food-debug-rawkeys">record: ${escapeHtml(recKeys)}</span>` : ""}${userKeys ? `<br><span class="food-debug-rawkeys">user obj: ${escapeHtml(userKeys)}</span>` : ""}</div>`;
}
function renderFoodDebugResult(data) {
  if (!data) return "";
  if (data.error === "food_module_disabled") {
    return `<div class="food-debug-disabled">Модуль питания отключён (<code>FOOD_MODULE_ENABLED=false</code>).</div>`;
  }
  if (!data.ok) {
    return `<div class="food-debug-error">Ошибка: ${escapeHtml(data.error || "неизвестная ошибка")}</div>`;
  }

  const parts = [];

  // Overview block
  const range = data.dateRange ? `${data.dateRange.from} — ${data.dateRange.to}` : "";
  const totalFetched = data.totalLessonsFetched ?? "?";
  const filters = Array.isArray(data.activeFilters) ? data.activeFilters : [data.filter || ""];
  parts.push(`<div class="food-debug-summary">
    📅 Диапазон API: <b>${escapeHtml(range)}</b> &nbsp;|&nbsp;
    📋 Получено занятий: <b>${totalFetched}</b><br>
    🔍 Фильтры: ${filters.map(f => `<code>${escapeHtml(f)}</code>`).join(", ")}
  </div>`);

  // Active camp week block
  if (data.activeCampWeek) {
    parts.push(_renderActiveCampWeekInfo(data.activeCampWeek));
  }
  if (data.campWeeks) {
    parts.push(_renderCampWeeksList(data.campWeeks));
  }

  // userResolve block
  if (data.userResolve) {
    const ur = data.userResolve;
    const allResolved = ur.resolvedCount > 0 && ur.unresolvedCount === 0;
    const noneResolved = ur.uniqueUserIds > 0 && ur.resolvedCount === 0;
    const statusIcon = allResolved ? "✅" : noneResolved ? "❌" : "⚠";
    const sampleHtml = Array.isArray(ur.sample) && ur.sample.length
      ? ur.sample.map(s => {
          const fn = s.fullName || "";
          const keys = Array.isArray(s.rawKeys) ? s.rawKeys.join(", ") : "";
          return `<div class="food-debug-user">${fn ? `<b>${escapeHtml(fn)}</b>` : `<span style="color:var(--color-muted,#888)">Без имени</span>`} · userId: ${escapeHtml(String(s.userId))}${keys ? `<br><span class="food-debug-rawkeys">user obj: ${escapeHtml(keys)}</span>` : ""}</div>`;
        }).join("")
      : "";
    const urErrHtml = Array.isArray(ur.errors) && ur.errors.length
      ? `<div class="food-debug-error">${ur.errors.map(e => escapeHtml(String(e))).join("<br>")}</div>` : "";
    parts.push(`<div class="food-debug-class">
      ${statusIcon} <b>Имена учеников (userId resolve):</b>
      уникальных userId: ${ur.uniqueUserIds} · получено: <b>${ur.resolvedCount}</b> · не найдено: ${ur.unresolvedCount}
      ${sampleHtml}${urErrHtml}
    </div>`);
  }

  // Warnings (room-field matches, save guards, etc.)
  if (Array.isArray(data.warnings) && data.warnings.length) {
    parts.push(data.warnings.map(w => `<div class="food-debug-warning">⚠ ${escapeHtml(String(w))}</div>`).join("\n"));
  }

  // Direct lessonId check
  if (data.directLessonId) {
    const cnt = data.directLessonRecordsCount ?? 0;
    const err = data.directLessonRecordsError || "";
    const ok = data.directLessonRecordsOk;
    const sample = Array.isArray(data.directLessonRecordsSample) ? data.directLessonRecordsSample : [];
    parts.push(`<div class="food-debug-class">
      🎯 <b>Прямая проверка lessonId ${escapeHtml(data.directLessonId)}</b>:
      ${ok ? `<span style="color:var(--color-ok,green)">✓ API ответил</span>` : `<span style="color:var(--color-error,#c00)">✗ Ошибка</span>`}
      · записей: <b>${cnt}</b>
      ${err ? `<div class="food-debug-error" style="margin-top:4px">${escapeHtml(err)}</div>` : ""}
      ${sample.map(_foodDebugUserHtml).join("")}
    </div>`);
  }

  // Save warning when nothing found
  const count = data.lessonsFoundCount ?? data.classesFoundCount ?? 0;
  if (count === 0 && data.savedToDB) {
    parts.push(`<div class="food-debug-warning">Нечего сохранять: занятия не найдены по фильтру.</div>`);
  }

  // Title samples when filter found nothing — most useful for diagnosis
  if (count === 0 && Array.isArray(data.lessonTitleSamples) && data.lessonTitleSamples.length > 0) {
    const samplesHtml = data.lessonTitleSamples.map(s => {
      const rawKeys = Array.isArray(s.rawKeys) ? s.rawKeys.join(", ") : "";
      const preview = s.allTextPreview || [s.topic, s.name, s.title].filter(Boolean).join(" / ") || "(нет текста)";
      return `<div class="food-debug-user">id: <b>${escapeHtml(s.id || "?")}</b> · дата: ${escapeHtml(s.date || "?")} · <span class="food-debug-rawkeys">${escapeHtml(preview)}</span><br>ключи: <span class="food-debug-rawkeys">${escapeHtml(rawKeys)}</span></div>`;
    }).join("");
    parts.push(`<div class="food-debug-class">
      <b>По фильтру занятия не найдены.</b> Первые ${data.lessonTitleSamples.length} из всех полученных:
      ${samplesHtml}
    </div>`);
  }

  // Matched lessons
  if (count > 0) {
    parts.push(`<div class="food-debug-summary">✅ Найдено занятий по фильтру: <b>${count}</b></div>`);
    if (data.savedToDB && (data.savedCount || data.skippedCount)) {
      parts.push(`<p class="food-debug-summary">Сохранено в БД: ${data.savedCount || 0}, пропущено: ${data.skippedCount || 0}</p>`);
    }
    const items = Array.isArray(data.lessons) ? data.lessons : (Array.isArray(data.classes) ? data.classes : []);
    parts.push(items.map(item => {
      const users = Array.isArray(item.usersSample) ? item.usersSample : [];
      const rawKeys = Array.isArray(item.rawKeys) ? item.rawKeys.join(", ") : "";
      const usersCount = item.usersCount ?? "?";
      const nameLabel = escapeHtml(item.lessonName || item.className || "Без имени");
      const meta = [item.date, item.time, item.groupName ? `группа: ${item.groupName}` : "", item.classroom].filter(Boolean).map(escapeHtml).join(" · ");
      const matchHtml = Array.isArray(item.matchedBy) && item.matchedBy.length
        ? `<div class="food-debug-rawkeys">Match: ${item.matchedBy.map(m => `${escapeHtml(m.field)}="${escapeHtml(String(m.value||"").slice(0,60))}"`).join(", ")}</div>`
        : "";
      return `<div class="food-debug-class">
        <b>${nameLabel}</b>${meta ? ` · <span class="food-debug-rawkeys">${meta}</span>` : ""} — ${usersCount} уч.
        ${matchHtml}
        ${rawKeys ? `<div class="food-debug-rawkeys">Ключи: ${escapeHtml(rawKeys)}</div>` : ""}
        ${users.map(_foodDebugUserHtml).join("")}
      </div>`;
    }).join(""));
  }

  // Errors
  if (Array.isArray(data.errors) && data.errors.length) {
    parts.push(`<div class="food-debug-error">Ошибки: ${data.errors.map(e => escapeHtml(String(e))).join("<br>")}</div>`);
  }

  parts.push(`<div class="food-debug-copy-row"><button class="secondary" id="foodDebugCopyJson">Скопировать JSON</button></div>`);
  return parts.join("\n");
}
function _foodDebugCopyJson() {
  const json = JSON.stringify(state.foodDebugLastResult, null, 2);
  if (navigator.clipboard) {
    navigator.clipboard.writeText(json).then(() => setNotice("JSON скопирован", "ok"));
  } else {
    prompt("Скопируйте JSON:", json);
  }
}
async function runFoodDebugSync() {
  const saveCheckbox = document.querySelector("#foodDebugSave");
  const save = saveCheckbox ? saveCheckbox.checked : false;
  const lessonIdInput = document.querySelector("#foodDebugLessonId");
  const lessonId = lessonIdInput ? lessonIdInput.value.trim() : "";
  const btn = document.querySelector("#foodDebugRun");
  const resultEl = document.querySelector("#foodDebugResult");
  if (btn) btn.disabled = true;
  if (resultEl) resultEl.innerHTML = `<div class="kpi-loading">Загружаю данные из МойКласс…</div>`;
  try {
    const body = { save };
    if (lessonId) body.lessonId = lessonId;
    const data = await apiPost("/api/food/debug/sync-camp-children", body);
    state.foodDebugLastResult = data;
    if (resultEl) {
      resultEl.innerHTML = renderFoodDebugResult(data);
      resultEl.querySelector("#foodDebugCopyJson")?.addEventListener("click", _foodDebugCopyJson);
    }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<div class="food-debug-error">${escapeHtml(e.message)}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}
async function runFoodDebugClear() {
  const confirmed = confirm("Очистить всех сохранённых детей смены?\n\nЭто установит active=0 для всех записей camp_children. Действие обратимо через повторную синхронизацию.");
  if (!confirmed) return;
  const btn = document.querySelector("#foodDebugClear");
  const resultEl = document.querySelector("#foodDebugResult");
  if (btn) btn.disabled = true;
  try {
    const data = await apiPost("/api/food/debug/clear-camp-children", {});
    if (data.ok) {
      if (resultEl) resultEl.innerHTML = `<div class="food-debug-summary">Очищено записей camp_children: <b>${data.affectedCount ?? 0}</b></div>`;
    } else {
      if (resultEl) resultEl.innerHTML = `<div class="food-debug-error">Ошибка очистки: ${escapeHtml(data.error || "неизвестная ошибка")}</div>`;
    }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<div class="food-debug-error">${escapeHtml(e.message)}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}
async function runFoodDebugCleanupDuplicates() {
  const confirmed = confirm("Убрать дубли детей?\n\nБудут отключены повторные записи детей и повторные коды привязки. Для каждого ребёнка останется одна запись и один код. Продолжить?");
  if (!confirmed) return;
  const btn = document.querySelector("#foodDebugCleanupDupes");
  const resultEl = document.querySelector("#foodDebugResult");
  if (btn) btn.disabled = true;
  try {
    const data = await apiPost("/api/food/debug/cleanup-duplicates", {});
    if (data.ok) {
      const msg = `Дубли убраны: детей деактивировано ${data.childrenDeactivated ?? 0} (из ${data.duplicateChildrenFound ?? 0}), кодов деактивировано ${data.linksDeactivated ?? 0} (из ${data.duplicateLinksFound ?? 0})`;
      if (resultEl) resultEl.innerHTML = `<div class="food-debug-summary">✅ ${escapeHtml(msg)}</div>`;
      state.campChildrenData = null;
    } else {
      if (resultEl) resultEl.innerHTML = `<div class="food-debug-error">Ошибка: ${escapeHtml(data.error || "неизвестная ошибка")}</div>`;
    }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<div class="food-debug-error">${escapeHtml(e.message)}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---- Camp children panel (food-children tab) ----
function renderCampChildrenPanel(root) {
  const data = state.campChildrenData;
  const total = data ? (data.count ?? 0) : 0;
  const withCode = data ? (data.withCode ?? 0) : 0;
  const linked = data ? (data.linked ?? 0) : 0;
  const _aw = state.foodDebugLastResult?.activeCampWeek;
  const activeStart = _aw?.startDate || state.me?.campActiveStartDate || "";
  const activeEnd = _aw?.endDate || state.me?.campActiveEndDate || "";
  const activeWeekLine = (activeStart && activeEnd)
    ? `Активная неделя: <b>${escapeHtml(activeStart)} — ${escapeHtml(activeEnd)}</b> &nbsp;|&nbsp; `
    : "";
  const statsHtml = data
    ? `<div class="camp-children-stats">
        ${activeWeekLine}Детей: <b>${total}</b> &nbsp;|&nbsp; с кодом: <b>${withCode}</b> &nbsp;|&nbsp; родитель привязан: <b>${linked}</b>
      </div>`
    : `<div class="camp-children-stats">Нажмите «Обновить» для загрузки данных.</div>`;
  const childrenHtml = data && Array.isArray(data.children) && data.children.length
    ? data.children.map(_renderCampChildCard).join("")
    : (data ? `<div class="empty">Детей смены не найдено (запустите диагностику и сохраните детей).</div>` : "");

  root.innerHTML = `
    <div class="food-debug-card">
      <h3>Дети городской программы · Yellow Club</h3>
      ${statsHtml}
      <div class="food-debug-actions" style="flex-wrap:wrap;gap:8px;">
        <button class="secondary" id="campChildrenRefresh">Обновить</button>
        <button class="primary" id="campChildrenGenAll">Сгенерировать коды всем</button>
        <button class="secondary" id="campChildrenCopyList" ${!withCode ? "disabled" : ""}>Скопировать список кодов</button>
      </div>
      <div id="campChildrenList">${childrenHtml}</div>
    </div>`;

  root.querySelector("#campChildrenRefresh")?.addEventListener("click", loadCampChildren);
  root.querySelector("#campChildrenGenAll")?.addEventListener("click", generateCampCodesAll);
  root.querySelector("#campChildrenCopyList")?.addEventListener("click", copyCampCodesList);
  root.querySelectorAll(".camp-child-gen-btn").forEach(btn => {
    btn.addEventListener("click", () => generateCodeForChild(btn.dataset.mkId));
  });
  root.querySelectorAll(".camp-child-copy-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const code = btn.dataset.code;
      if (navigator.clipboard) navigator.clipboard.writeText(code).then(() => setNotice(`Код ${code} скопирован`, "ok"));
      else prompt("Скопируйте код:", code);
    });
  });
  root.querySelectorAll(".camp-child-relink-btn").forEach(btn => {
    btn.addEventListener("click", () => relinkChild(btn.dataset.mkId, btn.dataset.childName, btn.dataset.hasParent === "1"));
  });

  if (!data) loadCampChildren();
}

function _campChildStatusBadge(child) {
  if (child.parent_telegram_id) return `<span class="camp-child-badge camp-child-badge--linked">Родитель привязан</span>`;
  if (child.link_code) return `<span class="camp-child-badge camp-child-badge--code">Код создан</span>`;
  return `<span class="camp-child-badge camp-child-badge--none">Без кода</span>`;
}

function _renderCampChildCard(child) {
  const name = escapeHtml(child.full_name || "Без имени");
  const group = escapeHtml(child.group_name || child.mk_class_name || "");
  const date = escapeHtml(child.camp_lesson_date || "");
  const room = escapeHtml(child.classroom || "");
  const code = child.link_code || "";
  const mkId = String(child.mk_student_id || "");
  const badge = _campChildStatusBadge(child);
  const hasParent = Boolean(child.parent_telegram_id);
  const codeHtml = code
    ? `<span class="camp-child-code">${escapeHtml(code)}</span>
       <button class="secondary camp-child-copy-btn" data-code="${escapeHtml(code)}" style="padding:3px 8px;font-size:12px">Копировать</button>`
    : `<button class="secondary camp-child-gen-btn" data-mk-id="${escapeHtml(mkId)}" style="padding:3px 8px;font-size:12px">Создать код</button>`;
  const parentInfo = hasParent
    ? `<div class="camp-child-parent-row">
        <span class="camp-child-parent-label">Родитель:</span>
        <span class="camp-child-parent-id">tg:${escapeHtml(String(child.parent_telegram_id))}</span>
        ${child.link_confirmed_at ? `<span class="camp-child-parent-date">с ${escapeHtml(String(child.link_confirmed_at).slice(0,10))}</span>` : ""}
       </div>`
    : "";
  const relinkBtn = code
    ? `<button class="secondary btn-sm camp-child-relink-btn${hasParent ? " camp-child-relink-btn--has-parent" : ""}" data-mk-id="${escapeHtml(mkId)}" data-child-name="${escapeAttr(child.full_name || "Без имени")}" data-has-parent="${hasParent ? "1" : "0"}" style="margin-top:6px">
        ${hasParent ? "Отвязать и выдать новый код" : "Выдать новый код"}
       </button>`
    : "";
  return `<div class="food-debug-class camp-child-card">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;flex-wrap:wrap;">
      <div><b>${name}</b> ${badge}</div>
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">${codeHtml}</div>
    </div>
    ${group ? `<div class="food-debug-rawkeys">${group}${date ? ` · ${date}` : ""}${room ? ` · ${room}` : ""}</div>` : ""}
    ${parentInfo}
    ${relinkBtn}
    <div class="camp-child-relink-result" id="relinkResult-${escapeAttr(mkId)}" style="display:none"></div>
  </div>`;
}

async function loadCampChildren() {
  const listEl = document.querySelector("#campChildrenList");
  const btn = document.querySelector("#campChildrenRefresh");
  if (btn) btn.disabled = true;
  if (listEl) listEl.innerHTML = `<div class="kpi-loading">Загружаю…</div>`;
  try {
    const data = await apiPost("/api/food/camp-children", {});
    state.campChildrenData = data;
    const panel = document.querySelector("#adminContent");
    if (panel) renderCampChildrenPanel(panel);
  } catch (e) {
    if (listEl) listEl.innerHTML = `<div class="food-debug-error">${escapeHtml(e.message)}</div>`;
    if (btn) btn.disabled = false;
  }
}

async function generateCampCodesAll() {
  const btn = document.querySelector("#campChildrenGenAll");
  if (btn) btn.disabled = true;
  try {
    const data = await apiPost("/api/food/camp-children/generate-codes", {});
    if (data.ok) {
      setNotice(`Сгенерировано кодов: ${data.generatedCount ?? 0}`, "ok");
      await loadCampChildren();
    } else {
      setNotice(data.error || "Ошибка генерации кодов", "error");
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    setNotice(safeUserError(e), "error");
    if (btn) btn.disabled = false;
  }
}

async function generateCodeForChild(mkId) {
  try {
    const data = await apiPost(`/api/food/camp-children/${encodeURIComponent(mkId)}/generate-code`, {});
    if (data.ok) {
      setNotice(`Код создан: ${data.link_code}`, "ok");
      await loadCampChildren();
    } else {
      setNotice(data.error || "Ошибка создания кода", "error");
    }
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

async function relinkChild(mkId, childName, hasParent) {
  const msg = hasParent
    ? `Отвязать ребёнка «${childName}» от текущего родителя и выдать новый код привязки?\n\nСтарый код перестанет работать. Старый родитель больше не увидит этого ребёнка.`
    : `Выдать новый код привязки для «${childName}»?\n\nСтарый код перестанет работать.`;
  if (!confirm(msg)) return;
  const resultEl = document.querySelector(`#relinkResult-${mkId}`);
  try {
    const data = await apiPost("/api/food/camp-children/relink", { mk_student_id: mkId });
    if (!data.ok) {
      if (resultEl) { resultEl.textContent = data.error || "Ошибка"; resultEl.className = "camp-child-relink-result camp-child-relink-result--error"; resultEl.style.display = ""; }
      setNotice(data.error || "Ошибка перепривязки", "error");
      return;
    }
    const newCode = data.new_code || "";
    if (resultEl) {
      resultEl.innerHTML = `${hasParent ? "Ребёнок отвязан. " : ""}Новый код: <b class="camp-child-code">${escapeHtml(newCode)}</b>`;
      resultEl.className = "camp-child-relink-result camp-child-relink-result--ok";
      resultEl.style.display = "";
    }
    setNotice(`${hasParent ? "Ребёнок отвязан. " : ""}Новый код: ${newCode}`, "ok");
    await loadCampChildren();
  } catch (e) {
    if (resultEl) { resultEl.textContent = e.message; resultEl.className = "camp-child-relink-result camp-child-relink-result--error"; resultEl.style.display = ""; }
    setNotice(safeUserError(e), "error");
  }
}

function copyCampCodesList() {
  const data = state.campChildrenData;
  if (!data || !Array.isArray(data.children)) return;
  const withCodes = data.children.filter(c => c.link_code);
  if (!withCodes.length) { setNotice("Нет детей с кодами", "error"); return; }
  const lines = withCodes.map(c => `${c.full_name || "Без имени"} — ${c.link_code}`).join("\n");
  const text = `Питание смены Yellow Club\n\n${lines}\n\nИнструкция для родителя:\nКоды подготовлены. Родительский ввод кода будет добавлен следующим шагом.`;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => setNotice("Список кодов скопирован", "ok"));
  } else {
    prompt("Скопируйте список:", text);
  }
}

// ---- Parent interface (my-children tab) ----
function renderMyChildren() {
  const root = $("myChildrenContent");
  if (!root) return;
  const children = state.myChildren;
  if (!Array.isArray(children)) {
    root.innerHTML = `<div class="kpi-loading">Загружаю…</div>`;
    return;
  }

  const linkFormHtml = `
    <div class="parent-link-card">
      <h3>${children.length ? "Добавить ещё ребёнка" : "Привязать ребёнка"}</h3>
      <p class="parent-link-hint">Введите код YC-XXXX, который вы получили от администратора Yellow Club.</p>
      <div class="parent-link-form">
        <input type="text" id="parentLinkCodeInput" class="parent-code-input" placeholder="YC-XXXX" maxlength="7" autocomplete="off" autocorrect="off" autocapitalize="characters" spellcheck="false">
        <button type="button" class="primary" id="parentLinkBtn">Привязать</button>
      </div>
      <div id="parentLinkError" class="parent-link-error hidden"></div>
      <p class="parent-link-footnote">Код выдаётся администратором Yellow Club.</p>
    </div>`;

  if (!children.length) {
    root.innerHTML = linkFormHtml;
  } else {
    const cardsHtml = children.map(c => {
      const name = escapeHtml(c.full_name || "Ребёнок");
      const group = escapeHtml(c.group_name || c.mk_class_name || "");
      const room = escapeHtml(c.classroom || "");
      const since = c.confirmed_at ? escapeHtml(String(c.confirmed_at).slice(0, 10)) : "";
      return `<div class="parent-child-card">
        <div class="parent-child-name">${name}</div>
        ${group ? `<div class="parent-child-meta">${group}${room ? ` · ${room}` : ""}</div>` : ""}
        ${since ? `<div class="parent-child-meta">Привязан: ${since}</div>` : ""}
      </div>`;
    }).join("");
    root.innerHTML = `<div class="parent-children-list">${cardsHtml}</div>${linkFormHtml}`;
  }

  const input = $("parentLinkCodeInput");
  const btn = $("parentLinkBtn");
  const errEl = $("parentLinkError");
  if (input) {
    input.addEventListener("input", () => {
      if (errEl) errEl.classList.add("hidden");
    });
    input.addEventListener("keydown", e => { if (e.key === "Enter") btn?.click(); });
  }
  if (btn) btn.addEventListener("click", linkChild);
}

async function loadMyChildren() {
  state.myChildren = null;
  renderMyChildren();
  try {
    const data = await apiGet("/api/food/my-children");
    state.myChildren = Array.isArray(data.children) ? data.children : [];
  } catch (e) {
    state.myChildren = [];
  }
  renderMyChildren();
}

async function linkChild() {
  const input = $("parentLinkCodeInput");
  const btn = $("parentLinkBtn");
  const errEl = $("parentLinkError");
  const code = (input?.value || "").trim().toUpperCase();
  if (!code) {
    if (errEl) { errEl.textContent = "Введите код ребёнка"; errEl.classList.remove("hidden"); }
    return;
  }
  if (btn) btn.disabled = true;
  if (errEl) errEl.classList.add("hidden");
  try {
    const data = await apiPost("/api/food/link-child", { code });
    if (data.ok) {
      if (data.already_linked) {
        setNotice("Ребёнок уже привязан к вашему аккаунту", "ok");
      } else {
        const name = data.child?.full_name || "Ребёнок";
        setNotice(`${name} успешно привязан`, "ok");
      }
      state.myChildren = null;
      await loadMyChildren();
    } else {
      const msg = data.message || data.error || "Ошибка привязки";
      if (errEl) { errEl.textContent = msg; errEl.classList.remove("hidden"); }
    }
  } catch (e) {
    if (errEl) { errEl.textContent = e.message || "Ошибка сети"; errEl.classList.remove("hidden"); }
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---- Parent: active menus ----

async function loadActiveMenus() {
  const root = $("foodContent");
  if (root) root.innerHTML = `<div class="empty">Загрузка меню...</div>`;
  try {
    const [menusData, ordersData] = await Promise.all([
      apiGet("/api/food/active-menus"),
      apiGet("/api/food/my-orders").catch(() => ({ ok: false, orders: [] })),
    ]);
    state.activeMenus = menusData.ok ? menusData : { ok: false, menus: [], childrenRequired: false, children: [] };
    state.myOrders = ordersData.ok ? ordersData.orders : [];
  } catch (e) {
    state.activeMenus = { ok: false, menus: [], childrenRequired: false, children: [], error: e.message };
    state.myOrders = [];
  }
  renderParentFoodMenu();
}

function _isDeadlinePassed(deadline_at) {
  if (!deadline_at) return false;
  try { return new Date(deadline_at) < new Date(); } catch (e) { return false; }
}

function _getOrderForMenu(menuId, mkStudentId) {
  const orders = Array.isArray(state.myOrders) ? state.myOrders : [];
  return orders.find(o => String(o.menu_id) === String(menuId) && String(o.mk_student_id) === String(mkStudentId)) || null;
}

function _orderStatusBadge(order, deadlinePassed) {
  if (!order) return deadlinePassed ? `<span class="food-order-status food-order-status--closed">Не выбрано (закрыто)</span>` : `<span class="food-order-status food-order-status--none">Не выбрано</span>`;
  if (order.status === "submitted") return `<span class="food-order-status food-order-status--submitted">Выбор отправлен</span>`;
  if (order.status === "skipped") return `<span class="food-order-status food-order-status--skipped">Без питания</span>`;
  return `<span class="food-order-status food-order-status--none">Не выбрано</span>`;
}

function _isMenuExpanded(childId, menuId, order) {
  const key = `${childId}_${menuId}`;
  if (Object.prototype.hasOwnProperty.call(state.foodOrderExpanded, key)) return state.foodOrderExpanded[key];
  return !order;
}

function renderParentFoodMenu() {
  const root = $("foodContent");
  if (!root) return;
  const data = state.activeMenus;

  if (!data || data.childrenRequired) {
    root.innerHTML = `<div class="parent-link-card"><p>Сначала привяжите ребёнка на вкладке <b>Мои дети</b>, чтобы увидеть меню питания.</p></div>`;
    return;
  }

  const children = Array.isArray(data.children) ? data.children : [];
  const menus = Array.isArray(data.menus) ? data.menus : [];

  if (!state.selectedChildId || !children.find(c => c.mk_student_id === state.selectedChildId)) {
    state.selectedChildId = children[0]?.mk_student_id || null;
  }
  const childId = state.selectedChildId;

  // Filter to menus eligible for the currently selected child
  const childMenus = menus.filter(m =>
    Array.isArray(m.eligibleChildIds) &&
    m.eligibleChildIds.map(String).includes(String(childId))
  );
  const selectedChild = children.find(c => c.mk_student_id === childId);

  // Build child context line: name, shift dates, location
  function _fmtShiftDate(iso) {
    if (!iso || iso.length < 10) return "";
    const [, mm, dd] = iso.split("-");
    return `${parseInt(dd, 10)}.${parseInt(mm, 10)}`;
  }
  let childContextHtml = "";
  if (selectedChild) {
    const ws = selectedChild.weekStart, we = selectedChild.weekEnd, loc = selectedChild.locationCode;
    const shiftStr = (ws && we) ? `Смена: ${_fmtShiftDate(ws)}–${_fmtShiftDate(we)}` : "";
    const locStr = loc && loc !== "unknown" ? `Филиал: ${escapeHtml(loc)}` : "";
    const parts = [shiftStr, locStr].filter(Boolean).join(" · ");
    childContextHtml = parts
      ? `<div class="food-child-context">${escapeHtml(selectedChild.full_name || selectedChild.first_name || "")}${parts ? ` <span class="food-child-context-meta">${parts}</span>` : ""}</div>`
      : "";
  }

  const childTabsHtml = children.length > 1
    ? `<div class="food-child-tabs">${children.map(c => `<button class="food-child-tab${c.mk_student_id === childId ? " active" : ""}" data-child-id="${escapeHtml(c.mk_student_id)}">${escapeHtml(c.full_name || c.first_name || c.mk_student_id)}</button>`).join("")}</div>`
    : "";

  if (!childMenus.length) {
    const msg = menus.length > 0
      ? "Для смены этого ребёнка меню ещё не опубликовано."
      : "Меню ещё не опубликовано.";
    root.innerHTML = `<div class="food-debug-card"><div class="food-menu-panel-head"><h3>Меню питания</h3><button class="secondary" id="parentMenuRefresh">Обновить</button></div>${childTabsHtml}${childContextHtml}<div class="parent-food-soon"><p>${escapeHtml(msg)}</p><p>Когда меню появится, здесь можно будет выбрать питание.</p></div></div>`;
    _wireParentRefreshAndTabs(root);
    return;
  }

  const catOrder = ["Супы", "Салаты", "Второе", "Гарниры", "Сладкое", "Напитки", "Другое"];

  const menusHtml = childMenus.map(menu => {
    const dateStr = _formatMenuDate(menu.menu_date);
    const deadlinePassed = _isDeadlinePassed(menu.deadline_at);
    const order = _getOrderForMenu(menu.id, childId);
    const expanded = _isMenuExpanded(childId, menu.id, order);
    const rawCats = menu.itemsByCategory || {};
    const cats = {};
    Object.entries(rawCats).forEach(([rc, items]) => items.forEach(it => {
      const nc = _normalizeFoodCategory(it.name, rc);
      (cats[nc] = cats[nc] || []).push(it);
    }));
    const allCats = [...new Set([...catOrder, ...Object.keys(cats)])].filter(c => cats[c] && cats[c].length);
    const titleHtml = `<div class="parent-food-card-title">${escapeHtml(menu.title || dateStr)}</div><div class="parent-food-card-meta">${escapeHtml(dateStr)}</div>`;

    if (!expanded && order) {
      // Collapsed view
      const changeBtn = !deadlinePassed
        ? `<button class="secondary btn-sm food-order-change-btn" data-expand-order="${menu.id}">Изменить выбор</button>`
        : "";
      let body = "";
      if (order.status === "submitted") {
        const names = (order.items || []).map(i => {
          const qty = parseInt(i.quantity || 1, 10);
          return escapeHtml(i.name || "") + (qty > 1 ? ` × ${qty}` : "");
        }).filter(Boolean).join(", ");
        body = names ? `<div class="food-order-summary-items">${names}</div>` : "";
      } else if (order.status === "skipped") {
        body = `<div class="food-order-summary-note">Вы отметили, что питание в этот день не нужно.</div>`;
      }
      return `<div class="food-order-card food-order-card--collapsed" data-menu-card="${menu.id}">
        <div class="food-order-card-head">
          <div>${titleHtml}</div>
          ${_orderStatusBadge(order, deadlinePassed)}
        </div>
        ${body}
        ${changeBtn}
      </div>`;
    }

    // Expanded view — build quantity map from existing order
    const qtyMap = {};
    (order?.items || []).forEach(i => { qtyMap[String(i.item_id)] = parseInt(i.quantity || 1, 10); });
    const deadlineNote = menu.deadline_at
      ? (deadlinePassed
          ? `<div class="food-order-deadline-passed" style="margin-top:4px">Дедлайн прошёл — ${escapeHtml(_formatDeadline(menu.deadline_at))}</div>`
          : `<div class="parent-food-deadline">Дедлайн выбора: до ${escapeHtml(_formatDeadline(menu.deadline_at))}</div>`)
      : "";
    const hintHtml = deadlinePassed ? "" : `<div class="food-order-hint">Выберите нужные позиции и количество.</div>`;
    const itemsHtml = allCats.map(cat => {
      const catItems = cats[cat] || [];
      const rows = catItems.map(item => {
        const qty = qtyMap[String(item.id)] || 0;
        const isActive = qty > 0;
        return `<div class="food-order-qty-row${isActive ? " food-order-qty-row--active" : ""}" data-qty-item="${item.id}" data-menu-id="${menu.id}">
          <div class="food-order-qty-label">${escapeHtml(item.name || "")}${item.weight ? `<span class="food-order-qty-weight"> · ${escapeHtml(item.weight)}</span>` : ""}</div>
          <div class="food-order-qty-ctrl">
            <button class="food-order-qty-btn" data-qty-dec="${item.id}"${deadlinePassed ? " disabled" : ""}>−</button>
            <span class="food-order-qty-val">${qty}</span>
            <button class="food-order-qty-btn" data-qty-inc="${item.id}"${deadlinePassed ? " disabled" : ""}>+</button>
          </div>
        </div>`;
      }).join("");
      return `<div class="parent-food-category">${escapeHtml(cat)}</div><div class="food-order-qty-list">${rows}</div>`;
    }).join("");
    const actionsHtml = deadlinePassed ? "" : `
      <div class="food-order-actions">
        <button class="primary" data-submit-order="${menu.id}">Отправить выбор</button>
        <button class="secondary" data-skip-order="${menu.id}">Без питания в этот день</button>
      </div>`;
    return `<div class="food-order-card" data-menu-card="${menu.id}">
      <div class="food-order-card-head">
        <div>${titleHtml}${deadlineNote}</div>
        ${_orderStatusBadge(order, deadlinePassed)}
      </div>
      ${hintHtml}${itemsHtml || `<div class="empty">Блюда не добавлены</div>`}
      ${actionsHtml}
    </div>`;
  }).join("");

  root.innerHTML = `
    <div class="food-debug-card">
      <div class="food-menu-panel-head">
        <h3>Меню питания</h3>
        <button class="secondary" id="parentMenuRefresh">Обновить</button>
      </div>
      ${childTabsHtml}
      ${childContextHtml}
      ${menusHtml}
    </div>`;

  _wireParentRefreshAndTabs(root);

  root.querySelectorAll("[data-expand-order]").forEach(btn => {
    btn.addEventListener("click", () => {
      const menuId = btn.dataset.expandOrder;
      state.foodOrderExpanded[`${childId}_${menuId}`] = true;
      renderParentFoodMenu();
    });
  });
  root.querySelectorAll("[data-submit-order]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const menuId = parseInt(btn.dataset.submitOrder);
      const items = [];
      root.querySelectorAll(`[data-qty-item][data-menu-id="${menuId}"]`).forEach(row => {
        const iid = parseInt(row.dataset.qtyItem);
        const valEl = row.querySelector(".food-order-qty-val");
        const qty = valEl ? parseInt(valEl.textContent, 10) : 0;
        if (qty > 0) items.push({ id: iid, quantity: qty });
      });
      await submitFoodOrder(menuId, childId, items);
    });
  });
  root.querySelectorAll("[data-skip-order]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const menuId = parseInt(btn.dataset.skipOrder);
      await skipFoodOrder(menuId, childId);
    });
  });
  // Quantity +/- buttons
  root.querySelectorAll("[data-qty-inc]").forEach(btn => {
    btn.addEventListener("click", () => {
      const iid = btn.dataset.qtyInc;
      const row = root.querySelector(`[data-qty-item="${iid}"]`);
      if (!row) return;
      const valEl = row.querySelector(".food-order-qty-val");
      if (!valEl) return;
      let v = parseInt(valEl.textContent, 10) || 0;
      if (v < 99) { v++; valEl.textContent = v; }
      row.classList.toggle("food-order-qty-row--active", v > 0);
    });
  });
  root.querySelectorAll("[data-qty-dec]").forEach(btn => {
    btn.addEventListener("click", () => {
      const iid = btn.dataset.qtyDec;
      const row = root.querySelector(`[data-qty-item="${iid}"]`);
      if (!row) return;
      const valEl = row.querySelector(".food-order-qty-val");
      if (!valEl) return;
      let v = parseInt(valEl.textContent, 10) || 0;
      if (v > 0) { v--; valEl.textContent = v; }
      row.classList.toggle("food-order-qty-row--active", v > 0);
    });
  });
}

function _wireParentRefreshAndTabs(root) {
  root.querySelector("#parentMenuRefresh")?.addEventListener("click", () => { state.activeMenus = null; state.myOrders = null; loadActiveMenus(); });
  root.querySelectorAll(".food-child-tab").forEach(tab => {
    tab.addEventListener("click", () => { state.selectedChildId = tab.dataset.childId; renderParentFoodMenu(); });
  });
}

async function submitFoodOrder(menuId, mkStudentId, items) {
  try {
    const data = await apiPost("/api/food/orders", { menu_id: menuId, mk_student_id: mkStudentId, items: items });
    if (!data.ok) { setNotice(data.error || "Ошибка отправки", "error"); return; }
    const orders = Array.isArray(state.myOrders) ? state.myOrders : [];
    const idx = orders.findIndex(o => String(o.menu_id) === String(menuId) && String(o.mk_student_id) === String(mkStudentId));
    if (idx >= 0) orders[idx] = { ...orders[idx], ...data.order };
    else orders.push(data.order);
    state.myOrders = orders;
    state.foodOrderExpanded[`${mkStudentId}_${menuId}`] = false;
    setNotice("Выбор питания сохранён. Вы можете изменить его до дедлайна.", "ok");
    renderParentFoodMenu();
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function skipFoodOrder(menuId, mkStudentId) {
  try {
    const data = await apiPost("/api/food/orders/skip", { menu_id: menuId, mk_student_id: mkStudentId });
    if (!data.ok) { setNotice(data.error || "Ошибка", "error"); return; }
    const orders = Array.isArray(state.myOrders) ? state.myOrders : [];
    const idx = orders.findIndex(o => String(o.menu_id) === String(menuId) && String(o.mk_student_id) === String(mkStudentId));
    if (idx >= 0) orders[idx] = { ...orders[idx], ...data.order };
    else orders.push(data.order);
    state.myOrders = orders;
    state.foodOrderExpanded[`${mkStudentId}_${menuId}`] = false;
    setNotice("Отметка сохранена. Вы можете изменить выбор до дедлайна.", "ok");
    renderParentFoodMenu();
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

function _formatMenuDate(dateStr) {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
  } catch (e) { return dateStr; }
}

function _formatDeadline(dt) {
  if (!dt) return "";
  try {
    const d = new Date(dt);
    return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }) + " " +
      d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  } catch (e) { return dt; }
}

function _dlLocalDateStr(dt) {
  if (!dt) return "";
  try {
    const d = new Date(dt);
    const p = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
  } catch (e) { return ""; }
}

function _dlLocalTimeStr(dt) {
  if (!dt) return "";
  try {
    const d = new Date(dt);
    const p = n => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch (e) { return ""; }
}

function _deadlineEditFormHtml(menuId, deadlineAt) {
  return `<div class="food-deadline-form">
    <div class="food-deadline-form-title">Изменить дедлайн</div>
    <div class="food-deadline-row">
      <div class="food-menu-form-row"><label>Дата</label><input type="date" class="fmDlDate" data-mid="${menuId}" value="${escapeAttr(_dlLocalDateStr(deadlineAt))}"></div>
      <div class="food-menu-form-row"><label>Время</label><input type="time" class="fmDlTime" data-mid="${menuId}" value="${escapeAttr(_dlLocalTimeStr(deadlineAt))}"></div>
    </div>
    <div class="food-deadline-quick">
      <button class="secondary btn-sm" data-dl-quick="30m" data-mid="${menuId}">+30 мин</button>
      <button class="secondary btn-sm" data-dl-quick="1h" data-mid="${menuId}">+1 час</button>
      <button class="secondary btn-sm" data-dl-quick="today20" data-mid="${menuId}">Сегодня 20:00</button>
      <button class="secondary btn-sm" data-dl-quick="tmr09" data-mid="${menuId}">Завтра 09:00</button>
    </div>
    <div class="food-deadline-form-actions">
      <button class="primary btn-sm" data-save-dl="${menuId}">Сохранить дедлайн</button>
      <button class="secondary btn-sm" data-cancel-dl="${menuId}">Отмена</button>
    </div>
    <div class="food-deadline-result" id="fmDlResult-${menuId}" style="display:none"></div>
  </div>`;
}

function _applyDeadlineQuick(root, menuId, quick) {
  const dateEl = root.querySelector(`.fmDlDate[data-mid="${menuId}"]`);
  const timeEl = root.querySelector(`.fmDlTime[data-mid="${menuId}"]`);
  if (!dateEl || !timeEl) return;
  const now = new Date();
  const p = n => String(n).padStart(2, "0");
  const ds = d => `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
  const ts = d => `${p(d.getHours())}:${p(d.getMinutes())}`;
  if (quick === "30m") {
    const d = new Date(now.getTime() + 30 * 60 * 1000);
    dateEl.value = ds(d); timeEl.value = ts(d);
  } else if (quick === "1h") {
    const d = new Date(now.getTime() + 60 * 60 * 1000);
    dateEl.value = ds(d); timeEl.value = ts(d);
  } else if (quick === "today20") {
    dateEl.value = ds(now); timeEl.value = "20:00";
  } else if (quick === "tmr09") {
    const tmr = new Date(now); tmr.setDate(tmr.getDate() + 1);
    dateEl.value = ds(tmr); timeEl.value = "09:00";
  }
}

async function saveMenuDeadline(root, menuId) {
  const dateEl = root.querySelector(`.fmDlDate[data-mid="${menuId}"]`);
  const timeEl = root.querySelector(`.fmDlTime[data-mid="${menuId}"]`);
  const resultEl = root.querySelector(`#fmDlResult-${menuId}`);
  const dateVal = (dateEl?.value || "").trim();
  const timeVal = (timeEl?.value || "").trim();
  if (!dateVal || !timeVal) {
    if (resultEl) { resultEl.textContent = "Укажите дату и время"; resultEl.className = "food-deadline-result food-deadline-result--error"; resultEl.style.display = ""; }
    return;
  }
  const deadline_at = `${dateVal}T${timeVal}:00`;
  if (resultEl) resultEl.style.display = "none";
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/update-deadline`, { deadline_at });
    if (!data.ok) {
      if (resultEl) { resultEl.textContent = data.error || "Ошибка"; resultEl.className = "food-deadline-result food-deadline-result--error"; resultEl.style.display = ""; }
      return;
    }
    if (state.foodMenuData) {
      const idx = state.foodMenuData.findIndex(m => m.id === menuId);
      if (idx !== -1) state.foodMenuData[idx] = data.menu;
    }
    if (state.foodMenuSelected && state.foodMenuSelected.id === menuId) {
      state.foodMenuSelected = data.menu;
    }
    const newDl = _formatDeadline(data.menu?.deadline_at);
    setNotice(newDl ? `Дедлайн обновлён. Заказы доступны до ${newDl}.` : "Дедлайн обновлён", "ok");
    if (state.foodMenuSelected) {
      _renderFoodMenuDetail(root, state.foodMenuSelected);
    } else {
      _renderFoodMenuList(root);
    }
  } catch (e) {
    if (resultEl) { resultEl.textContent = e.message; resultEl.className = "food-deadline-result food-deadline-result--error"; resultEl.style.display = ""; }
  }
}

// ---- Admin: food menu panel ----

function _normalizeFoodCategory(name, cat) {
  const n = (name || "").toLowerCase();
  if (/сырник/.test(n) || /трубоч/.test(n)) return "Сладкое";
  if (/чизбургер/.test(n) || /бургер/.test(n) || /шаурм/.test(n) || /гриль.?ролл/.test(n)) return "Второе";
  if ((/зеро/.test(n) || /zero/.test(n)) && /кол/.test(n)) return "Напитки";
  if (cat === "Фастфуд") return "Второе";
  return cat || "Другое";
}

function normalizeFoodCategoryByName(name, currentCategory) {
  const n = (name || "").toLowerCase();
  if (/борщ|суп|рассольник|щи|холодник|свекольник/.test(n)) return "Супы";
  if (/салат|цезарь|бело.?зел|греческий/.test(n)) return "Салаты";
  if (/сырник|трубоч|десерт|булоч|блин|панкейк/.test(n)) return "Сладкое";
  if (/гуляш|котлет|кармашек|филе|курин|курица|говядин|тефтел|биточек|отбивн|мяс|рыба/.test(n)) return "Второе";
  if (/чизбургер|чибургер|бургер|шаурм|гриль.?ролл|ролл/.test(n)) return "Второе";
  if (/картоф|картошка|каша|греч|рис|макарон|пюре|овощи/.test(n)) return "Гарниры";
  if (/сок|вода|чай|компот|кола|кока|coca|zero|зеро/.test(n)) return "Напитки";
  return currentCategory || "Другое";
}

const FOOD_CATEGORIES = ["Супы", "Салаты", "Второе", "Гарниры", "Сладкое", "Напитки", "Другое"];
const FOOD_STATUS_LABELS = { draft: "Черновик", published: "Опубликовано", closed: "Закрыто" };
const FOOD_STATUS_CSS = { draft: "food-menu-status--draft", published: "food-menu-status--published", closed: "food-menu-status--closed" };

function _foodMenuStatusBadge(status) {
  const label = FOOD_STATUS_LABELS[status] || status;
  const cls = FOOD_STATUS_CSS[status] || "food-menu-status--draft";
  return `<span class="food-menu-status ${cls}">${escapeHtml(label)}</span>`;
}

async function renderFoodMenuPanel(root) {
  const view = state.foodMenuView || "list";
  console.log("[food-nav] renderFoodMenuPanel view=" + view + " editing=" + state.isEditingFoodOrder + " menuId=" + state.foodMenuSummaryMenuId);
  // Block background re-render while the admin form is open
  if (state.isEditingFoodOrder && view === "summary") {
    console.log("[food-nav] renderFoodMenuPanel: skipped — admin form is open");
    return;
  }
  // Restore summary view
  if (view === "summary" && state.foodMenuSummaryMenuId) {
    await loadFoodMenuSummary(root, state.foodMenuSummaryMenuId);
    return;
  }
  // Restore detail view
  if (view === "detail" && state.foodMenuSelected) {
    _renderFoodMenuDetail(root, state.foodMenuSelected);
    return;
  }
  // Fall back to list
  if (!state.foodMenuData) {
    root.innerHTML = `<div class="food-debug-card"><div class="empty">Загрузка меню...</div></div>`;
    await loadFoodMenus(root);
    return;
  }
  _renderFoodMenuList(root);
}

async function loadFoodMenus(root) {
  state.foodMenuView = "list";
  state.foodMenuSelected = null;
  state.foodMenuSummaryMenuId = null;
  console.log("[food-nav] loadFoodMenus: resetting to list");
  try {
    const d = await apiGet("/api/food/menus");
    if (d.ok) state.foodMenuData = d.menus || [];
    else state.foodMenuData = [];
  } catch (e) {
    state.foodMenuData = [];
    if (root) root.innerHTML = `<div class="food-debug-card"><div class="food-debug-error">Ошибка загрузки: ${escapeHtml(e.message)}</div></div>`;
    return;
  }
  if (root) _renderFoodMenuList(root);
}

function _renderFoodMenuList(root) {
  state.foodMenuView = "list";
  const menus = state.foodMenuData || [];
  const todayLocal = localIsoDate(new Date());
  const aw = state.foodDebugLastResult?.activeCampWeek;
  let defaultMenuDate = todayLocal;
  let menuDateWarning = "";
  if (aw?.startDate && aw?.endDate) {
    if (todayLocal < aw.startDate) defaultMenuDate = aw.startDate;
    else if (todayLocal > aw.endDate) defaultMenuDate = aw.endDate;
    if (todayLocal < aw.startDate || todayLocal > aw.endDate) {
      menuDateWarning = `<div class="food-menu-date-warning">Дата меню не входит в активную смену (${escapeHtml(aw.startDate)} — ${escapeHtml(aw.endDate)}).</div>`;
    }
  }
  const createFormHtml = `
    <div class="food-menu-create-form" id="foodMenuCreateForm" style="display:none">
      <h4>Создать меню</h4>
      <div class="food-menu-form-row"><label>Дата меню</label><input type="date" id="fmDate" value="${defaultMenuDate}"></div>
      ${menuDateWarning}
      <div class="food-menu-form-row"><label>Название (например: Понедельник YC1)</label><input type="text" id="fmTitle" placeholder="Понедельник YC1" maxlength="100"></div>
      <div class="food-menu-form-row"><label>Филиал (определяет, для какой локации меню)</label><select id="fmLocationCode"><option value="">— Общее (все филиалы) —</option><option value="YC1">YC1 · Кульман 1/1</option><option value="YC2">YC2 · Мстиславца 6</option><option value="YC3">YC3</option></select></div>
      <div class="food-menu-form-row"><label>Дедлайн выбора (необязательно)</label><input type="datetime-local" id="fmDeadline"></div>
      <div class="food-menu-actions">
        <button class="primary" id="fmCreateBtn">Создать</button>
        <button class="secondary" id="fmCancelBtn">Отмена</button>
      </div>
      <div id="fmCreateError" style="display:none" class="food-debug-error"></div>
    </div>`;

  const menuCardsHtml = menus.length
    ? menus.map(m => {
        const dateStr = _formatMenuDate(m.menu_date);
        const dlPassed = _isMenuDeadlinePassed(m.deadline_at);
        const dlStatusHtml = m.deadline_at
          ? (dlPassed
              ? `<div class="food-menu-dl-status food-menu-dl-status--passed">Дедлайн прошёл. Заказы закрыты.</div>`
              : `<div class="food-menu-dl-status food-menu-dl-status--active">Заказы доступны до <b>${escapeHtml(_formatDeadline(m.deadline_at))}</b></div>`)
          : `<div class="food-menu-dl-status food-menu-dl-status--none">Дедлайн не установлен</div>`;
        const locBadge = m.location_code ? `<span class="food-loc-badge">${escapeHtml(m.location_code)}</span>` : "";
        const canPublish = m.status === "draft";
        const canClose = m.status === "published";
        return `<div class="food-menu-card">
          <div class="food-menu-card-head">
            <div>
              <div class="food-menu-card-title">${escapeHtml(m.title || dateStr)} ${_foodMenuStatusBadge(m.status)}${locBadge}</div>
              <div class="food-menu-card-meta">${escapeHtml(dateStr)} · блюд: ${m.items_count ?? 0}</div>
              ${dlStatusHtml}
            </div>
          </div>
          <div class="food-menu-card-actions">
            <button class="secondary btn-sm" data-open-menu="${m.id}">Открыть</button>
            ${canPublish ? `<button class="primary btn-sm" data-publish-menu="${m.id}">Опубликовать</button>` : ""}
            ${canClose ? `<button class="secondary btn-sm" data-close-menu="${m.id}">Закрыть</button>` : ""}
            <button class="secondary btn-sm" data-edit-deadline="${m.id}">Изменить дедлайн</button>
            ${canDeleteFoodMenu() ? `<button class="secondary danger btn-sm" data-delete-menu="${m.id}" data-delete-menu-title="${escapeAttr(m.title || dateStr)}" data-delete-menu-date="${escapeAttr(dateStr)}" data-delete-menu-published="${m.status === 'published' ? '1' : '0'}">Удалить</button>` : ""}
          </div>
          <div id="fmDlForm-${m.id}" style="display:none">${_deadlineEditFormHtml(m.id, m.deadline_at)}</div>
        </div>`;
      }).join("")
    : `<div class="empty">Меню ещё не создано. Нажмите «Создать меню».</div>`;

  root.innerHTML = `<div class="food-debug-card">
    <div class="food-menu-panel-head">
      <h3>Питание · меню</h3>
      <div style="display:flex;gap:8px">
        <button class="secondary" id="fmRefresh">Обновить</button>
        <button class="primary" id="fmNewBtn">+ Создать меню</button>
      </div>
    </div>
    ${createFormHtml}
    <div id="fmMenuList">${menuCardsHtml}</div>
  </div>`;

  root.querySelector("#fmRefresh")?.addEventListener("click", () => { state.foodMenuData = null; loadFoodMenus(root); });
  root.querySelector("#fmNewBtn")?.addEventListener("click", () => {
    const f = root.querySelector("#foodMenuCreateForm");
    if (f) f.style.display = f.style.display === "none" ? "" : "none";
  });
  root.querySelector("#fmCancelBtn")?.addEventListener("click", () => {
    const f = root.querySelector("#foodMenuCreateForm");
    if (f) f.style.display = "none";
  });
  root.querySelector("#fmCreateBtn")?.addEventListener("click", () => createFoodMenu(root));
  root.querySelectorAll("[data-open-menu]").forEach(btn => {
    btn.addEventListener("click", () => openFoodMenu(root, parseInt(btn.dataset.openMenu)));
  });
  root.querySelectorAll("[data-publish-menu]").forEach(btn => {
    btn.addEventListener("click", () => publishFoodMenu(root, parseInt(btn.dataset.publishMenu)));
  });
  root.querySelectorAll("[data-close-menu]").forEach(btn => {
    btn.addEventListener("click", () => closeFoodMenu(root, parseInt(btn.dataset.closeMenu)));
  });
  root.querySelectorAll("[data-edit-deadline]").forEach(btn => {
    const mid = parseInt(btn.dataset.editDeadline);
    btn.addEventListener("click", () => {
      const form = root.querySelector(`#fmDlForm-${mid}`);
      if (form) form.style.display = form.style.display === "none" ? "" : "none";
    });
  });
  root.querySelectorAll("[data-save-dl]").forEach(btn => {
    btn.addEventListener("click", () => saveMenuDeadline(root, parseInt(btn.dataset.saveDl)));
  });
  root.querySelectorAll("[data-cancel-dl]").forEach(btn => {
    const mid = parseInt(btn.dataset.cancelDl);
    btn.addEventListener("click", () => {
      const form = root.querySelector(`#fmDlForm-${mid}`);
      if (form) form.style.display = "none";
    });
  });
  root.querySelectorAll("[data-dl-quick]").forEach(btn => {
    btn.addEventListener("click", () => _applyDeadlineQuick(root, parseInt(btn.dataset.mid), btn.dataset.dlQuick));
  });
  root.querySelectorAll("[data-delete-menu]").forEach(btn => {
    btn.addEventListener("click", () => {
      const menuId = parseInt(btn.dataset.deleteMenu);
      const title = btn.dataset.deleteMenuTitle || "";
      const date = btn.dataset.deleteMenuDate || "";
      const isPublished = btn.dataset.deleteMenuPublished === "1";
      _confirmFoodMenuDelete(title, date, isPublished, async () => {
        btn.disabled = true;
        const result = await _doDeleteFoodMenu(menuId);
        if (!result.ok) {
          btn.disabled = false;
          if (result.error === "has_orders") {
            setNotice(result.message || "Нельзя удалить меню: по нему уже есть заказы.", "error");
          } else {
            setNotice(result.error || "Ошибка удаления меню", "error");
          }
          return;
        }
        setNotice("Меню удалено", "success");
        state.foodMenuData = null;
        loadFoodMenus(root);
      });
    });
  });
}

async function createFoodMenu(root) {
  const menuDate = root.querySelector("#fmDate")?.value || "";
  const title = root.querySelector("#fmTitle")?.value || "";
  const deadline = root.querySelector("#fmDeadline")?.value || "";
  const locationCode = root.querySelector("#fmLocationCode")?.value || "";
  const errEl = root.querySelector("#fmCreateError");
  if (!menuDate) { if (errEl) { errEl.textContent = "Укажите дату меню"; errEl.style.display = ""; } return; }
  try {
    const data = await apiPost("/api/food/menus", { menu_date: menuDate, title, deadline_at: deadline || null, location_code: locationCode || null });
    if (!data.ok) { if (errEl) { errEl.textContent = data.error || "Ошибка"; errEl.style.display = ""; } return; }
    state.foodMenuData = null;
    await loadFoodMenus(root);
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.style.display = ""; }
  }
}

async function openFoodMenu(root, menuId) {
  root.innerHTML = `<div class="food-debug-card"><div class="empty">Загрузка меню...</div></div>`;
  try {
    const resp = await fetch(`/api/food/menus/${menuId}?` + new URLSearchParams({ initData }), { headers: { "X-Init-Data": initData } });
    const data = await resp.json();
    if (!data.ok) { root.innerHTML = `<div class="food-debug-card"><div class="food-debug-error">${escapeHtml(data.error || "Ошибка")}</div></div>`; return; }
    state.foodMenuSelected = data.menu;
    _renderFoodMenuDetail(root, data.menu);
  } catch (e) {
    root.innerHTML = `<div class="food-debug-card"><div class="food-debug-error">${escapeHtml(e.message)}</div></div>`;
  }
}

async function publishFoodMenu(root, menuId) {
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/publish`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка публикации", "error"); return; }
    setNotice("Меню опубликовано", "ok");
    state.foodMenuData = null;
    await loadFoodMenus(root);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function closeFoodMenu(root, menuId) {
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/close`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка", "error"); return; }
    setNotice("Меню закрыто", "ok");
    state.foodMenuData = null;
    await loadFoodMenus(root);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

function _renderFoodMenuDetail(root, menu) {
  if (!menu) { state.foodMenuSelected = null; state.foodMenuView = "list"; loadFoodMenus(root); return; }
  state.foodMenuView = "detail";
  const dateStr = _formatMenuDate(menu.menu_date);
  const items = Array.isArray(menu.items) ? menu.items : [];
  const catOrder = [...FOOD_CATEGORIES];
  const cats = {};
  items.forEach(item => {
    const cat = _normalizeFoodCategory(item.name, item.category || "Другое");
    cats[cat] = cats[cat] || [];
    cats[cat].push(item);
  });
  const allCats = [...new Set([...catOrder, ...Object.keys(cats)])].filter(c => cats[c] && cats[c].length);
  const catHtml = allCats.length
    ? allCats.map(cat => {
        const catItems = cats[cat] || [];
        const itemsHtml = catItems.map(item => {
          const hasOrders = item.order_count > 0;
          const hideBtn = item.is_available
            ? `<button class="secondary btn-sm" data-hide-item="${item.id}">${hasOrders ? "Скрыть (в заказах)" : "Скрыть"}</button>`
            : `<button class="secondary btn-sm" data-restore-item="${item.id}">Показать</button>`;
          return `
          <div class="food-item-row${item.is_available ? "" : " food-item-hidden"}" data-item-id="${item.id}">
            <span class="food-item-name">${escapeHtml(item.name || "")}</span>
            ${item.weight ? `<span class="food-item-weight">${escapeHtml(item.weight)}</span>` : ""}
            ${item.price ? `<span class="food-item-price">${Number(item.price).toFixed(2)}&nbsp;BYN</span>` : ""}
            <div class="food-item-actions">
              <button class="secondary btn-sm food-item-edit-inline-btn" data-edit-item="${item.id}" data-edit-name="${escapeAttr(item.name || "")}" data-edit-cat="${escapeAttr(item.category || "Другое")}" data-edit-weight="${escapeAttr(item.weight || "")}" data-edit-price="${escapeAttr(String(item.price || ""))}">Изменить</button>
              ${hideBtn}
            </div>
          </div>`;
        }).join("");
        return `<div class="food-category-block">
          <div class="food-category-label">${escapeHtml(cat)}</div>
          ${itemsHtml}
        </div>`;
      }).join("")
    : `<div class="empty">Блюд пока нет. Добавьте через форму ниже.</div>`;

  const catOptions = FOOD_CATEGORIES.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");

  root.innerHTML = `<div class="food-debug-card">
    <div class="food-menu-detail-head">
      <button class="secondary btn-sm" id="fmBackBtn">← Назад</button>
      <span class="food-menu-detail-title">${escapeHtml(menu.title || dateStr)} ${_foodMenuStatusBadge(menu.status)}</span>
      <span style="font-size:13px;color:var(--color-text-secondary)">${escapeHtml(dateStr)}</span>
    </div>
    <div class="food-menu-card-actions" style="margin-bottom:8px">
      ${menu.status === "draft" ? `<button class="primary btn-sm" data-publish-menu="${menu.id}">Опубликовать</button>` : ""}
      ${menu.status === "published" ? `<button class="secondary btn-sm" data-close-menu="${menu.id}">Закрыть меню</button>` : ""}
      ${(menu.status === "published" || menu.status === "closed") ? `<button class="secondary btn-sm" data-summary-menu="${menu.id}">Сводка заказов</button>` : ""}
    </div>
    ${menu.status === "published" ? `<div class="food-published-warning">⚠️ Меню уже опубликовано. Изменения блюд повлияют на новые сводки и отчёты. Существующие заказы не ломаются.</div>` : ""}
    <div class="food-menu-deadline-block">
      ${menu.deadline_at
        ? (_isMenuDeadlinePassed(menu.deadline_at)
            ? `<div class="food-menu-dl-status food-menu-dl-status--passed">Дедлайн прошёл. Заказы закрыты.</div>`
            : `<div class="food-menu-dl-status food-menu-dl-status--active">Заказы доступны до <b>${escapeHtml(_formatDeadline(menu.deadline_at))}</b></div>`)
        : `<div class="food-menu-dl-status food-menu-dl-status--none">Дедлайн не установлен</div>`}
      <button class="secondary btn-sm" id="fmDetailEditDlBtn" style="margin-top:6px">Изменить дедлайн</button>
      <div id="fmDetailDlForm" style="display:none;margin-top:8px">${_deadlineEditFormHtml(menu.id, menu.deadline_at)}</div>
    </div>
    ${menu.status === "published" && !_isMenuDeadlinePassed(menu.deadline_at) ? `
    <div class="food-notify-block" style="margin-bottom:12px">
      <button class="secondary btn-sm" id="fmNotifyBtn">Уведомить родителей</button>
      <div id="fmNotifyResult" style="display:none;margin-top:8px"></div>
    </div>` : ""}
    <div id="fmDetailItems">${catHtml}</div>
    <div class="food-item-add-form">
      <h4>Добавить блюдо</h4>
      <div class="food-menu-form-row">
        <label>Категория</label>
        <select id="fiCategory">${catOptions}</select>
      </div>
      <div class="food-item-form-grid">
        <div class="food-item-form-name"><input type="text" id="fiName" placeholder="Название блюда" maxlength="200"></div>
        <input type="text" id="fiWeight" placeholder="Вес (250/20 г)" style="grid-column:1/-1">
        <input type="text" id="fiPrice" placeholder="Стоимость для отчёта (BYN, необяз.)" style="grid-column:1/-1">
      </div>
      <div class="food-menu-actions">
        <button class="primary" id="fiAddBtn">Добавить блюдо</button>
      </div>
      <div id="fiAddError" style="display:none" class="food-debug-error"></div>
    </div>
    <div class="food-item-add-form" style="margin-top:14px">
      <h4>Быстро добавить меню</h4>
      <textarea id="fiBulkText" rows="10" placeholder="СУПЫ&#10;Борщ холодный на кефире — 250 г&#10;Борщ украинский — 250 г&#10;&#10;САЛАТЫ&#10;Салат греческий — 190 г&#10;Цезарь с курицей — 190 г&#10;&#10;ВТОРОЕ&#10;Котлета из птицы с сыром — 105 г&#10;Чизбургер — 200 г&#10;Шаурма — 280 г&#10;Гриль ролл с курицей — 230 г&#10;&#10;ГАРНИРЫ&#10;Картофель запечённый — 150 г&#10;&#10;СЛАДКОЕ&#10;Сырники со сметаной — 150 г&#10;Трубочка со сгущёнкой — 90 г&#10;&#10;НАПИТКИ&#10;Сок яблочный — 0.2 л&#10;Кока-кола Zero — 0.33 л" style="width:100%;box-sizing:border-box;font-size:16px;min-height:160px;resize:vertical;border:1px solid var(--border,#ccc);border-radius:8px;padding:8px 10px;background:var(--card-bg,#fff);color:var(--color-text,#222)"></textarea>
      <div class="food-menu-actions" style="margin-top:8px;gap:8px">
        <button class="secondary" id="fiBulkParseBtn">Разобрать</button>
        <button class="secondary" id="fiBulkClearBtn">Очистить черновик</button>
      </div>
      <div id="fiBulkPreview" style="display:none;margin-top:10px"></div>
    </div>
    ${canUseFoodMenuOcr() ? `<div class="food-ocr-section">
      <h4>Распознать меню по фото</h4>
      <div class="food-ocr-inputs">
        <input type="file" id="fiOcrInput" accept="image/*" style="font-size:16px;flex:1 1 auto;min-width:0">
        <button class="secondary" id="fiOcrBtn">Распознать фото</button>
      </div>
      <div id="fiOcrStatus" class="food-ocr-status" style="display:none;margin-top:8px;font-size:13px"></div>
    </div>` : ""}
  </div>`;

  // Restore draft state after innerHTML
  const draft = state.foodMenuDrafts[menu.id];
  const bulkTextEl = root.querySelector("#fiBulkText");
  if (draft?.bulkText && bulkTextEl) bulkTextEl.value = draft.bulkText;
  if (draft?.ocrStatus) {
    const ocrStatusEl = root.querySelector("#fiOcrStatus");
    if (ocrStatusEl) {
      ocrStatusEl.textContent = draft.ocrStatus.message;
      ocrStatusEl.className = `food-ocr-status food-ocr-status--${draft.ocrStatus.type}`;
      ocrStatusEl.style.display = "";
    }
  }
  if (draft?.parsedItems?.length) _renderBulkEditablePreview(root, menu.id, draft.parsedItems);

  root.querySelector("#fmBackBtn")?.addEventListener("click", () => { state.foodMenuSelected = null; state.foodMenuData = null; loadFoodMenus(root); });
  root.querySelector("[data-publish-menu]")?.addEventListener("click", e => publishFoodMenu(root, parseInt(e.currentTarget.dataset.publishMenu)));
  root.querySelector("[data-close-menu]")?.addEventListener("click", e => closeFoodMenuDetail(root, parseInt(e.currentTarget.dataset.closeMenu), menu));
  root.querySelector("[data-summary-menu]")?.addEventListener("click", e => loadFoodMenuSummary(root, parseInt(e.currentTarget.dataset.summaryMenu)));
  root.querySelectorAll("[data-hide-item]").forEach(btn => {
    btn.addEventListener("click", () => hideFoodItem(root, parseInt(btn.dataset.hideItem), menu.id));
  });
  root.querySelectorAll("[data-restore-item]").forEach(btn => {
    btn.addEventListener("click", () => restoreFoodItem(root, parseInt(btn.dataset.restoreItem), menu.id));
  });
  root.querySelectorAll(".food-item-edit-inline-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const itemId = btn.dataset.editItem;
      const existing = root.querySelector(`#fmItemEditForm_${itemId}`);
      if (existing) { existing.remove(); return; }
      const catOptions = FOOD_CATEGORIES.map(c => `<option value="${escapeHtml(c)}"${c === btn.dataset.editCat ? " selected" : ""}>${escapeHtml(c)}</option>`).join("");
      const div = document.createElement("div");
      div.id = `fmItemEditForm_${itemId}`;
      div.className = "food-item-edit-form-inline";
      div.innerHTML = `
        <div class="food-item-edit-form-row">
          <label>Категория</label><select class="edit-item-cat">${catOptions}</select>
        </div>
        <div class="food-item-edit-form-row">
          <label>Название</label><input class="edit-item-name" type="text" value="${escapeAttr(btn.dataset.editName)}" placeholder="Название блюда" maxlength="200">
        </div>
        <div class="food-item-edit-form-row">
          <label>Вес</label><input class="edit-item-weight" type="text" value="${escapeAttr(btn.dataset.editWeight)}" placeholder="250 г">
        </div>
        <div class="food-item-edit-form-row">
          <label>Цена (BYN)</label><input class="edit-item-price" type="text" value="${escapeAttr(btn.dataset.editPrice || "")}" placeholder="0.00">
        </div>
        <div class="food-item-edit-form-actions">
          <button class="primary btn-sm edit-item-save">Сохранить</button>
          <button class="secondary btn-sm edit-item-cancel">Отмена</button>
        </div>
        <div class="food-item-edit-error" style="display:none"></div>`;
      btn.closest(".food-item-row").after(div);
      div.querySelector(".edit-item-cancel")?.addEventListener("click", () => div.remove());
      div.querySelector(".edit-item-save")?.addEventListener("click", async () => {
        const name = (div.querySelector(".edit-item-name")?.value || "").trim();
        const category = div.querySelector(".edit-item-cat")?.value || "Другое";
        const weight = (div.querySelector(".edit-item-weight")?.value || "").trim() || null;
        const priceRaw = (div.querySelector(".edit-item-price")?.value || "").replace(",", ".").replace(/руб\.?/gi, "").trim();
        const price = parseFloat(priceRaw) || 0;
        const errEl = div.querySelector(".food-item-edit-error");
        if (!name) { errEl.textContent = "Укажите название блюда"; errEl.style.display = ""; return; }
        const saveBtn = div.querySelector(".edit-item-save");
        if (saveBtn) saveBtn.disabled = true;
        try {
          const data = await apiPost(`/api/food/items/${itemId}/update`, { name, category, weight, price });
          if (!data.ok) { errEl.textContent = data.error || "Ошибка"; errEl.style.display = ""; if (saveBtn) saveBtn.disabled = false; return; }
          await openFoodMenu(root, menu.id);
        } catch (e) { errEl.textContent = e.message; errEl.style.display = ""; if (saveBtn) saveBtn.disabled = false; }
      });
    });
  });
  root.querySelector("#fiAddBtn")?.addEventListener("click", () => addFoodItem(root, menu.id));
  root.querySelector("#fiBulkParseBtn")?.addEventListener("click", () => _parseFoodBulkPreview(root, menu.id));
  root.querySelector("#fiBulkClearBtn")?.addEventListener("click", () => {
    delete state.foodMenuDrafts[menu.id];
    if (bulkTextEl) bulkTextEl.value = "";
    const preview = root.querySelector("#fiBulkPreview");
    if (preview) { preview.innerHTML = ""; preview.style.display = "none"; }
    const ocrInput = root.querySelector("#fiOcrInput");
    if (ocrInput) ocrInput.value = "";
    const ocrStatusEl = root.querySelector("#fiOcrStatus");
    if (ocrStatusEl) { ocrStatusEl.textContent = ""; ocrStatusEl.style.display = "none"; }
  });
  root.querySelector("#fiBulkText")?.addEventListener("input", e => {
    if (!state.foodMenuDrafts[menu.id]) state.foodMenuDrafts[menu.id] = {};
    state.foodMenuDrafts[menu.id].bulkText = e.target.value;
  });
  root.querySelector("#fiOcrBtn")?.addEventListener("click", () => _uploadFoodMenuOcr(root, menu.id));
  root.querySelector("#fmNotifyBtn")?.addEventListener("click", () => sendFoodPublishNotification(root, menu.id));
  root.querySelector("#fmDetailEditDlBtn")?.addEventListener("click", () => {
    const f = root.querySelector("#fmDetailDlForm");
    if (f) f.style.display = f.style.display === "none" ? "" : "none";
  });
  root.querySelector(`[data-save-dl="${menu.id}"]`)?.addEventListener("click", () => saveMenuDeadline(root, menu.id));
  root.querySelector(`[data-cancel-dl="${menu.id}"]`)?.addEventListener("click", () => {
    const f = root.querySelector("#fmDetailDlForm");
    if (f) f.style.display = "none";
  });
  root.querySelectorAll(`[data-dl-quick][data-mid="${menu.id}"]`).forEach(btn => {
    btn.addEventListener("click", () => _applyDeadlineQuick(root, menu.id, btn.dataset.dlQuick));
  });
}

async function _uploadFoodMenuOcr(root, menuId) {
  const input = root.querySelector("#fiOcrInput");
  const statusEl = root.querySelector("#fiOcrStatus");
  const btn = root.querySelector("#fiOcrBtn");
  if (!input || !statusEl) return;
  const file = input.files?.[0];
  if (!file) {
    statusEl.textContent = "Выберите файл изображения.";
    statusEl.className = "food-ocr-status food-ocr-status--error";
    statusEl.style.display = "";
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    statusEl.textContent = "Файл слишком большой (максимум 5 МБ).";
    statusEl.className = "food-ocr-status food-ocr-status--error";
    statusEl.style.display = "";
    return;
  }
  if (btn) btn.disabled = true;
  statusEl.textContent = "Распознавание...";
  statusEl.className = "food-ocr-status";
  statusEl.style.display = "";
  try {
    const fd = new FormData();
    appendAuthForm(fd);
    fd.append("image", file, file.name);
    const resp = await fetch(`/api/food/menus/${menuId}/ocr-preview`, { method: "POST", body: fd });
    const data = await resp.json();
    if (!data.ok) {
      let errMsg = data.message || data.error || "Ошибка распознавания.";
      if (data.error === "ocr_language_missing") {
        const langs = Array.isArray(data.availableLanguages) && data.availableLanguages.length
          ? " Доступные языки: " + data.availableLanguages.join(", ") + "."
          : "";
        errMsg = "В Tesseract не установлен русский язык. Нужно добавить rus.traineddata." + langs;
      }
      statusEl.textContent = errMsg;
      statusEl.className = "food-ocr-status food-ocr-status--error";
      return;
    }
    const rawText = data.rawText || "";
    const bulkEl = root.querySelector("#fiBulkText");
    if (bulkEl) bulkEl.value = rawText;
    const lowQuality = Array.isArray(data.warnings) && data.warnings.some(w => w.code === "ocr_low_quality");
    const ocrStatusType = lowQuality ? "warn" : "ok";
    const ocrStatusMsg = lowQuality
      ? "Текст распознан, но качество низкое. Проверьте фото или исправьте текст вручную."
      : "Текст распознан. Проверьте список перед добавлением.";
    if (!state.foodMenuDrafts[menuId]) state.foodMenuDrafts[menuId] = {};
    state.foodMenuDrafts[menuId].ocrStatus = { type: ocrStatusType, message: ocrStatusMsg };
    state.foodMenuDrafts[menuId].bulkText = rawText;
    statusEl.textContent = ocrStatusMsg;
    statusEl.className = `food-ocr-status food-ocr-status--${ocrStatusType}`;
    _parseFoodBulkPreview(root, menuId);
  } catch (e) {
    statusEl.textContent = "Ошибка соединения: " + e.message;
    statusEl.className = "food-ocr-status food-ocr-status--error";
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function closeFoodMenuDetail(root, menuId, menu) {
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/close`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка", "error"); return; }
    setNotice("Меню закрыто", "ok");
    state.foodMenuSelected = data.menu;
    _renderFoodMenuDetail(root, data.menu);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function addFoodItem(root, menuId) {
  const category = root.querySelector("#fiCategory")?.value || "Другое";
  const name = (root.querySelector("#fiName")?.value || "").trim();
  const weight = (root.querySelector("#fiWeight")?.value || "").trim();
  const priceRaw = (root.querySelector("#fiPrice")?.value || "").replace(",", ".").replace(/руб\.?/gi, "").trim();
  const price = parseFloat(priceRaw) || 0;
  const errEl = root.querySelector("#fiAddError");
  if (!name) { if (errEl) { errEl.textContent = "Укажите название блюда"; errEl.style.display = ""; } return; }
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/items`, { category, name, weight: weight || null, price });
    if (!data.ok) { if (errEl) { errEl.textContent = data.error || "Ошибка"; errEl.style.display = ""; } return; }
    // reload menu detail
    await openFoodMenu(root, menuId);
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.style.display = ""; }
  }
}

async function hideFoodItem(root, itemId, menuId) {
  try {
    const data = await apiPost(`/api/food/items/${itemId}/hide`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка", "error"); return; }
    await openFoodMenu(root, menuId);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function restoreFoodItem(root, itemId, menuId) {
  try {
    const data = await apiPost(`/api/food/items/${itemId}/restore`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка", "error"); return; }
    await openFoodMenu(root, menuId);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function loadFoodMenuSummary(root, menuId) {
  const savedScrollY = window.scrollY;
  state.foodMenuView = "summary";
  state.foodMenuSummaryMenuId = menuId;
  root.innerHTML = `<div class="food-debug-card"><div class="empty">Загрузка сводки...</div></div>`;
  try {
    const data = await apiGet(`/api/food/menus/${menuId}/summary`);
    if (!data.ok) { setNotice(data.error || "Ошибка загрузки сводки", "error"); state.foodMenuSelected && _renderFoodMenuDetail(root, state.foodMenuSelected); return; }
    _renderFoodMenuSummary(root, menuId, data);
    if (savedScrollY > 0) setTimeout(() => window.scrollTo({ top: savedScrollY }), 0);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

function _renderFoodMenuSummary(root, menuId, data) {
  const menu = data.menu || {};
  const dateStr = _formatMenuDate(menu.menu_date);
  const title = menu.title || dateStr;
  const catOrder = ["Супы", "Салаты", "Второе", "Гарниры", "Сладкое", "Напитки", "Другое"];

  function _sortChildren(arr) {
    return [...arr].sort((a, b) => (a.childName || "").localeCompare(b.childName || "", "ru"));
  }

  function _itemsBlock(byItems, utensils) {
    if ((!Array.isArray(byItems) || !byItems.length) && !utensils) return `<div class="food-summary-empty">Нет выбранных блюд</div>`;
    const byCat = {};
    (byItems || []).forEach(it => { const _c = _normalizeFoodCategory(it.name, it.category); byCat[_c] = byCat[_c] || []; byCat[_c].push(it); });
    const cats = [...new Set([...catOrder, ...Object.keys(byCat)])].filter(c => byCat[c]);
    const itemsHtml = cats.map(cat =>
      `<div class="parent-food-category">${escapeHtml(cat)}</div>` +
      byCat[cat].map(it => `<div class="food-summary-item-row"><span class="food-summary-item-name">${escapeHtml(it.name)}${it.weight ? ` · ${escapeHtml(it.weight)}` : ""}</span><span class="food-summary-item-count">${it.count} порц.</span></div>`).join("")
    ).join("");
    const utensilsHtml = utensils > 0
      ? `<div class="food-summary-item-row food-summary-utensils-row"><span class="food-summary-item-name">🍴 Столовые приборы</span><span class="food-summary-item-count">${utensils} компл.</span></div>`
      : "";
    return itemsHtml + utensilsHtml;
  }

  const canDeleteOrders = ["owner", "admin", "operations"].includes(state.me?.role || "");
  const isAdminEdit = canAdminFoodOrders();

  function _adminBadge(adminSource, adminCreatedAt, adminUpdatedAt, adminComment) {
    const hasAdminAction = adminSource || adminCreatedAt || adminUpdatedAt;
    if (!hasAdminAction) return "";
    const ts = adminUpdatedAt || adminCreatedAt;
    let label;
    if (adminUpdatedAt && adminCreatedAt && adminUpdatedAt !== adminCreatedAt) {
      label = "изменено админом";
    } else if (adminSource === "admin_manual" || adminSource === "admin_edit") {
      label = adminUpdatedAt && adminUpdatedAt !== adminCreatedAt ? "изменено админом" : "добавлено админом";
    } else if (adminUpdatedAt) {
      label = "изменено админом";
    } else {
      label = "добавлено админом";
    }
    const dtStr = ts ? (() => { try { const d = new Date(ts); return `${String(d.getDate()).padStart(2,"0")}.${String(d.getMonth()+1).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; } catch { return ""; } })() : "";
    const commentHtml = adminComment ? `<span class="food-admin-badge-comment"> · ${escapeHtml(adminComment)}</span>` : "";
    return `<div class="food-admin-badge-wrap"><span class="food-admin-badge">${escapeHtml(label)}${dtStr ? " " + dtStr : ""}</span>${commentHtml}</div>`;
  }

  function _childOrderCard(ch) {
    const badge = ch.status === "submitted"
      ? `<span class="food-order-status food-order-status--submitted">Отправлен</span>`
      : ch.status === "skipped"
      ? `<span class="food-order-status food-order-status--skipped">Без питания</span>`
      : `<span class="food-order-status food-order-status--none">Не выбрано</span>`;
    let cardBody = "";
    if (ch.status === "submitted") {
      const details = Array.isArray(ch.itemDetails) && ch.itemDetails.length ? ch.itemDetails : [];
      cardBody = details.length
        ? `<ul class="food-child-order-items">${details.map(it => { const q = parseInt(it.quantity||1,10); return `<li>${escapeHtml(it.name)}${it.weight ? ` · ${escapeHtml(it.weight)}` : ""}${q > 1 ? ` <b>× ${q}</b>` : ""}</li>`; }).join("")}</ul>`
        : `<div class="food-child-order-note">Нет блюд в заказе</div>`;
    } else if (ch.status === "skipped") {
      cardBody = `<div class="food-child-order-note">Питание не нужно</div>`;
    } else {
      cardBody = `<div class="food-child-order-note food-child-order-note--missing">Родитель ещё не отправил выбор</div>`;
    }
    const deleteBtn = canDeleteOrders && ch.orderId
      ? `<button class="food-order-delete-btn" data-order-id="${ch.orderId}" data-order-type="child" data-display-name="${escapeAttr(ch.childName)}" data-menu-date="${escapeAttr(dateStr)}" title="Удалить заказ">✕</button>`
      : "";
    const editBtn = isAdminEdit && ch.orderId
      ? `<button class="food-admin-edit-btn" data-order-id="${ch.orderId}" data-order-type="child" data-menu-id="${menuId}" data-display-name="${escapeAttr(ch.childName)}" data-items-json="${escapeAttr(JSON.stringify(ch.itemDetails || []))}" title="Редактировать заказ (администратор)">Редактировать</button>`
      : "";
    const addBtn = isAdminEdit && !ch.orderId && ch.mk_student_id
      ? `<button class="food-admin-add-btn" data-mk-student-id="${escapeAttr(ch.mk_student_id)}" data-menu-id="${menuId}" data-display-name="${escapeAttr(ch.childName)}" title="Добавить заказ вручную">+ Добавить</button>`
      : "";
    const adminBadge = _adminBadge(ch.adminSource, ch.adminCreatedAt, ch.adminUpdatedAt, ch.adminComment);
    return `<div class="food-child-order-card"><div class="food-child-order-head"><span class="food-child-order-name">${escapeHtml(ch.childName)}</span>${badge}${editBtn}${addBtn}${deleteBtn}</div>${adminBadge}${cardBody}</div>`;
  }

  const overallStats = `
    <div class="food-summary-stats">
      <div class="food-summary-stat"><div class="food-summary-stat-val">${data.totalChildren || 0}</div><div class="food-summary-stat-lbl">Всего детей</div></div>
      <div class="food-summary-stat"><div class="food-summary-stat-val">${data.submittedOrders || 0}</div><div class="food-summary-stat-lbl">Отправили выбор</div></div>
      <div class="food-summary-stat"><div class="food-summary-stat-val">${data.skippedOrders || 0}</div><div class="food-summary-stat-lbl">Без питания</div></div>
      <div class="food-summary-stat"><div class="food-summary-stat-val">${data.missingOrders || 0}</div><div class="food-summary-stat-lbl">Не выбрали</div></div>
    </div>`;

  const byLocations = Array.isArray(data.byLocations) && data.byLocations.length ? data.byLocations : null;
  let bodyHtml = "";
  if (byLocations) {
    bodyHtml = byLocations.map(loc => {
      const sorted = _sortChildren(loc.byChildren || []);
      const childCards = sorted.length ? sorted.map(_childOrderCard).join("") : `<div class="food-summary-empty">Детей нет</div>`;
      return `<div class="food-location-section">
        <div class="food-location-header">
          <span class="food-location-code">${escapeHtml(loc.groupCode)}</span>
          <span class="food-location-address">${escapeHtml(loc.location)}</span>
        </div>
        <div class="food-summary-stats" style="margin:8px 0">
          <div class="food-summary-stat"><div class="food-summary-stat-val">${loc.totalChildren}</div><div class="food-summary-stat-lbl">Детей</div></div>
          <div class="food-summary-stat"><div class="food-summary-stat-val">${loc.submittedOrders}</div><div class="food-summary-stat-lbl">Выбрали</div></div>
          <div class="food-summary-stat"><div class="food-summary-stat-val">${loc.skippedOrders}</div><div class="food-summary-stat-lbl">Без питания</div></div>
          <div class="food-summary-stat"><div class="food-summary-stat-val">${loc.missingOrders}</div><div class="food-summary-stat-lbl">Не выбрали</div></div>
        </div>
        <div class="food-summary-section" style="margin-top:10px">Заказы по детям</div>
        ${childCards}
        ${_staffSummaryBlock(loc.byStaff || [])}
        <div class="food-summary-section" style="margin-top:10px">Итог по блюдам</div>
        ${_itemsBlock(loc.byItems, loc.utensils)}
      </div>`;
    }).join("");
  } else {
    const sorted = _sortChildren(data.byChildren || []);
    bodyHtml = `
      <div class="food-summary-section">Заказы по детям</div>
      ${sorted.length ? sorted.map(_childOrderCard).join("") : `<div class="food-summary-empty">Детей нет</div>`}
      ${_staffSummaryBlock(data.byStaff)}
      <div class="food-summary-section" style="margin-top:10px">Итог по блюдам</div>
      ${_itemsBlock(data.byItems, data.totalUtensils)}`;
  }

  function _staffSummaryBlock(byStaff) {
    if (!Array.isArray(byStaff) || !byStaff.length) return "";
    const cards = byStaff.map(s => {
      const badge = s.status === "submitted"
        ? `<span class="food-staff-status-badge food-staff-status-badge--submitted">Выбор отправлен</span>`
        : `<span class="food-staff-status-badge food-staff-status-badge--skipped">Без питания</span>`;
      const items = s.status === "submitted" && s.itemDetails && s.itemDetails.length
        ? `<ul class="food-child-order-items">${s.itemDetails.map(it => { const q = parseInt(it.quantity||1,10); return `<li>${escapeHtml(it.name)}${q > 1 ? ` <b>× ${q}</b>` : ""}</li>`; }).join("")}</ul>`
        : "";
      const teacherTag = s.isTeacher ? `<span class="kitchen-teacher-tag">преп.</span>` : "";
      const deleteBtn = canDeleteOrders && s.orderId
        ? `<button class="food-order-delete-btn" data-order-id="${s.orderId}" data-order-type="staff" data-display-name="${escapeAttr(s.staffName)}" data-menu-date="${escapeAttr(dateStr)}" title="Удалить заказ">✕</button>`
        : "";
      const editBtn = isAdminEdit && s.orderId
        ? `<button class="food-admin-edit-btn" data-order-id="${s.orderId}" data-order-type="staff" data-menu-id="${menuId}" data-display-name="${escapeAttr(s.staffName)}" data-items-json="${escapeAttr(JSON.stringify(s.itemDetails || []))}" data-location-code="${escapeAttr(s.locationCode || "")}" title="Редактировать заказ (администратор)">Редактировать</button>`
        : "";
      const adminBadge = _adminBadge(s.adminSource, s.adminCreatedAt, s.adminUpdatedAt, s.adminComment);
      return `<div class="food-child-order-card"><div class="food-child-order-head"><span class="food-child-order-name">${escapeHtml(s.staffName)}${teacherTag}</span>${badge}${editBtn}${deleteBtn}</div>${adminBadge}${items}</div>`;
    }).join("");
    return `<div class="food-summary-section" style="margin-top:10px">Заказы сотрудников</div>${cards}`;
  }

  const missingCount = data.missingOrders || 0;
  const deadlinePassed = _isMenuDeadlinePassed(menu.deadline_at);
  let remindBlockHtml = "";
  if (missingCount > 0) {
    if (deadlinePassed) {
      remindBlockHtml = `<div class="food-remind-deadline-passed">Дедлайн прошёл. Напоминания не отправляются.</div>`;
    } else {
      remindBlockHtml = `
        <div class="food-remind-block">
          <div class="food-remind-hint">Сообщение уйдёт только родителям, у которых ребёнок привязан в кабинете.</div>
          <button class="secondary" id="fmRemindBtn">Напомнить тем, кто не выбрал (${missingCount})</button>
          <div id="fmRemindResult" style="display:none;margin-top:10px"></div>
        </div>`;
    }
  }

  root.innerHTML = `<div class="food-debug-card">
    <div class="food-menu-detail-head">
      <button class="secondary btn-sm" id="fmSummaryBack">← Назад к меню</button>
      <span class="food-menu-detail-title">${escapeHtml(title)} — Сводка</span>
    </div>
    <div class="food-summary-warm-warning">⚠️ ВАЖНО: еда должна быть тёплой при доставке</div>
    ${overallStats}
    ${remindBlockHtml}
    ${bodyHtml}
    <div class="food-menu-actions" style="margin-top:16px">
      <button class="secondary" id="fmSummaryRefresh">Обновить сводку</button>
      <button class="secondary" id="fmSummaryCopy">Скопировать сводку</button>
      <button class="secondary" id="fmAuditBtn">✅ Проверить сводку</button>
      ${isAdminEdit ? `<button class="secondary" id="fmAdminAddOrderBtn">+ Добавить заказ</button>` : ""}
    </div>
    <div id="fmAuditResult" style="display:none;margin-top:10px"></div>
    <div id="fmAdminOrderFormWrap"></div>
  </div>`;

  root.querySelector("#fmSummaryBack")?.addEventListener("click", () => { if (state.foodMenuSelected) _renderFoodMenuDetail(root, state.foodMenuSelected); else { state.foodMenuData = null; loadFoodMenus(root); } });
  root.querySelector("#fmSummaryRefresh")?.addEventListener("click", () => loadFoodMenuSummary(root, menuId));
  root.querySelector("#fmSummaryCopy")?.addEventListener("click", () => _copyFoodSummary(title, dateStr, data));
  root.querySelector("#fmRemindBtn")?.addEventListener("click", () => sendFoodReminder(root, menuId));
  root.querySelector("#fmAuditBtn")?.addEventListener("click", async () => {
    const btn = root.querySelector("#fmAuditBtn");
    const resultEl = root.querySelector("#fmAuditResult");
    if (btn) btn.disabled = true;
    if (resultEl) { resultEl.style.display = ""; resultEl.innerHTML = `<div style="color:#888;font-size:13px">Проверка...</div>`; }
    try {
      const auditData = await apiGet(`/api/food/menus/${menuId}/audit`);
      if (resultEl) { resultEl.innerHTML = _renderAuditBlock(auditData); resultEl.style.display = ""; }
      resultEl?.querySelector("#auditCopyBtn")?.addEventListener("click", () => _copyAuditReport(auditData));
    } catch (e) {
      if (resultEl) { resultEl.innerHTML = `<div style="color:#c0392b;font-size:13px">Ошибка проверки: ${escapeHtml(e.message)}</div>`; resultEl.style.display = ""; }
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  // Attach delete order handlers
  root.querySelectorAll(".food-order-delete-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      const orderId = btn.dataset.orderId;
      const orderType = btn.dataset.orderType;
      const displayName = btn.dataset.displayName;
      const menuDate = btn.dataset.menuDate;
      if (!orderId || !orderType) return;
      const confirmed = await _confirmFoodOrderDelete(displayName, menuDate);
      if (!confirmed) return;
      btn.disabled = true;
      try {
        const endpoint = orderType === "staff"
          ? `/api/food/staff-orders/${orderId}/delete`
          : `/api/food/orders/${orderId}/delete`;
        const resp = await apiPost(endpoint, {});
        if (!resp.ok) {
          setNotice(resp.error || "Ошибка удаления", "error");
          btn.disabled = false;
          return;
        }
        setNotice("Заказ удалён", "success");
        loadFoodMenuSummary(root, menuId);
      } catch (err) {
        setNotice("Ошибка сети при удалении", "error");
        btn.disabled = false;
      }
    });
  });

  // Admin edit handlers
  root.querySelectorAll(".food-admin-edit-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const formWrap = root.querySelector("#fmAdminOrderFormWrap");
      let existingItems = [];
      try { existingItems = JSON.parse(btn.dataset.itemsJson || "[]"); } catch {}
      const existingLocation = btn.dataset.locationCode || "";
      _openFoodAdminOrderForm(formWrap || root, menuId, "edit", btn.dataset.orderId, btn.dataset.orderType, btn.dataset.displayName, () => loadFoodMenuSummary(root, menuId), { existingItems, existingLocation });
    });
  });
  root.querySelectorAll(".food-admin-add-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const formWrap = root.querySelector("#fmAdminOrderFormWrap");
      _openFoodAdminOrderForm(formWrap || root, menuId, "add-child", null, "child", btn.dataset.displayName, () => loadFoodMenuSummary(root, menuId), { mkStudentId: btn.dataset.mkStudentId });
    });
  });
  root.querySelector("#fmAdminAddOrderBtn")?.addEventListener("click", () => {
    const formWrap = root.querySelector("#fmAdminOrderFormWrap");
    _openFoodAdminOrderForm(formWrap || root, menuId, "create", null, null, null, () => loadFoodMenuSummary(root, menuId));
  });
}

async function _loadFoodAdminPersons(menuId) {
  const data = await apiGet(`/api/food/menus/${menuId}/admin-persons`);
  if (!data.ok) throw new Error(data.error || "Ошибка загрузки списка");
  return data;
}

async function _openFoodAdminOrderForm(container, menuId, mode, orderId, orderType, displayName, onSuccess, extra) {
  state.isEditingFoodOrder = true;
  container.innerHTML = `<div class="food-admin-form-wrap"><div style="color:#888;font-size:13px;padding:12px 0">Загрузка данных меню...</div></div>`;
  container.scrollIntoView({ behavior: "smooth", block: "nearest" });
  let persons;
  try {
    persons = await _loadFoodAdminPersons(menuId);
  } catch (e) {
    container.innerHTML = `<div class="food-admin-form-wrap"><div style="color:#c0392b;font-size:13px;padding:8px 0">Ошибка: ${escapeHtml(safeUserError(e))}</div><button class="secondary btn-sm" id="fmAdminFormClose">Закрыть</button></div>`;
    container.querySelector("#fmAdminFormClose")?.addEventListener("click", () => { state.isEditingFoodOrder = false; container.innerHTML = ""; });
    return;
  }

  const menuItems = persons.menuItems || [];
  const children = persons.children || [];
  const staffList = persons.staff || [];
  const menuTitle = persons.menu?.title || persons.menu?.menuDate || `Меню #${menuId}`;

  const isEdit = mode === "edit";
  const isAddChild = mode === "add-child";
  const isCreate = mode === "create";

  let formTitle = isEdit
    ? `Редактировать заказ — ${escapeHtml(displayName || "")}`
    : isAddChild
    ? `Добавить заказ ребёнка — ${escapeHtml(displayName || "")}`
    : "Добавить заказ вручную";

  const personTypeOptions = isEdit
    ? ""
    : `<div class="food-admin-form-row">
        <label class="food-admin-form-label">Тип заказа</label>
        <select id="fmAdminPersonType" class="food-admin-select">
          <option value="child"${isAddChild ? " selected" : ""}>Ребёнок</option>
          <option value="staff">Сотрудник / Преподаватель</option>
        </select>
      </div>`;

  const childSelectHtml = `<div class="food-admin-form-row" id="fmAdminChildRow">
    <label class="food-admin-form-label">Ребёнок</label>
    <select id="fmAdminChildSelect" class="food-admin-select">
      <option value="">— выберите ребёнка —</option>
      ${children.map(c => `<option value="${escapeAttr(c.mkStudentId)}"${(extra?.mkStudentId === c.mkStudentId || (isAddChild && extra?.mkStudentId === c.mkStudentId)) ? " selected" : ""}>${escapeHtml(c.fullName)}${c.groupName ? " · " + escapeHtml(c.groupName) : ""}</option>`).join("")}
    </select>
  </div>`;

  const staffSelectHtml = `<div class="food-admin-form-row" id="fmAdminStaffRow" style="${isEdit && orderType === "child" ? "display:none" : (isCreate || (!isEdit && !isAddChild) ? "display:none" : "")}">
    <label class="food-admin-form-label">Сотрудник</label>
    <select id="fmAdminStaffSelect" class="food-admin-select">
      <option value="">— выберите сотрудника —</option>
      ${staffList.map(s => `<option value="${escapeAttr(String(s.userId))}">${escapeHtml(s.displayName)}${s.role ? " · " + escapeHtml(s.role) : ""}</option>`).join("")}
    </select>
  </div>`;

  const locSelectHtml = `<div class="food-admin-form-row" id="fmAdminLocRow" style="${(isEdit && orderType === "child") || isAddChild ? "display:none" : ""}">
    <label class="food-admin-form-label">Локация</label>
    <select id="fmAdminLocSelect" class="food-admin-select">
      <option value="">— не указана —</option>
      <option value="YC1">YC1</option>
      <option value="YC2">YC2</option>
      <option value="YC3">YC3</option>
    </select>
  </div>`;

  const existingQtyMap = {};
  if (isEdit && extra?.existingItems) {
    for (const it of extra.existingItems) {
      const iid = it.item_id ?? it.itemId ?? it.id;
      if (iid) existingQtyMap[String(iid)] = parseInt(it.quantity || 1, 10);
    }
  }

  const itemsHtml = menuItems.length
    ? menuItems.map(it => {
        const iid = String(it.id);
        const qty = existingQtyMap[iid] ?? 0;
        const priceStr = it.price ? ` — ${_fmtBYN(it.price)}` : "";
        return `<div class="food-admin-item-row">
          <div class="food-admin-item-info">${escapeHtml(it.name)}${it.weight ? `<br><span style="color:#888;font-size:12px">${escapeHtml(it.weight)}${priceStr}</span>` : (priceStr ? `<br><span style="color:#888;font-size:12px">${priceStr.trim()}</span>` : "")}</div>
          <div class="food-admin-item-qty-wrap"><input type="number" class="food-admin-item-qty" data-item-id="${escapeAttr(iid)}" min="0" max="10" value="${qty}"></div>
        </div>`;
      }).join("")
    : `<div style="color:#888;font-size:13px">В этом меню нет доступных блюд</div>`;

  container.innerHTML = `<div class="food-admin-form-wrap">
    <div class="food-admin-form-title">${formTitle}</div>
    <div style="color:#888;font-size:12px;margin-bottom:10px">Меню: ${escapeHtml(menuTitle)}</div>
    ${personTypeOptions}
    ${isEdit ? "" : childSelectHtml}
    ${isEdit ? "" : staffSelectHtml}
    ${locSelectHtml}
    <div class="food-admin-form-row">
      <label class="food-admin-form-label">Блюда</label>
      <div class="food-admin-items-list">${itemsHtml}</div>
    </div>
    <div class="food-admin-form-row">
      <label class="food-admin-form-label">Комментарий</label>
      <input type="text" id="fmAdminComment" class="food-admin-text-input" placeholder="Причина изменения (необязательно)" maxlength="200">
    </div>
    <div class="food-admin-form-actions">
      <button class="primary" id="fmAdminFormSubmit">${isEdit ? "Сохранить изменения" : "Добавить заказ"}</button>
      <button class="secondary" id="fmAdminFormCancel">Отмена</button>
    </div>
    <div id="fmAdminFormError" style="display:none;color:#c0392b;font-size:13px;margin-top:8px"></div>
  </div>`;

  const personTypeEl = container.querySelector("#fmAdminPersonType");
  const childRowEl = container.querySelector("#fmAdminChildRow");
  const staffRowEl = container.querySelector("#fmAdminStaffRow");
  const locRowEl = container.querySelector("#fmAdminLocRow");

  function _updateFormVisibility() {
    if (!personTypeEl) return;
    const t = personTypeEl.value;
    if (childRowEl) childRowEl.style.display = t === "child" ? "" : "none";
    if (staffRowEl) staffRowEl.style.display = t === "staff" ? "" : "none";
    if (locRowEl) locRowEl.style.display = t === "staff" ? "" : "none";
  }
  personTypeEl?.addEventListener("change", _updateFormVisibility);
  if (isCreate) _updateFormVisibility();

  // Pre-populate location select for staff edit
  if (isEdit && orderType === "staff" && extra?.existingLocation) {
    const locEl = container.querySelector("#fmAdminLocSelect");
    if (locEl) locEl.value = extra.existingLocation;
  }

  container.querySelector("#fmAdminFormCancel")?.addEventListener("click", () => { state.isEditingFoodOrder = false; container.innerHTML = ""; });

  container.querySelector("#fmAdminFormSubmit")?.addEventListener("click", async () => {
    const submitBtn = container.querySelector("#fmAdminFormSubmit");
    const errorEl = container.querySelector("#fmAdminFormError");
    if (errorEl) { errorEl.style.display = "none"; errorEl.textContent = ""; }

    const comment = (container.querySelector("#fmAdminComment")?.value || "").trim();
    const qtyEls = container.querySelectorAll(".food-admin-item-qty");
    const items = [];
    qtyEls.forEach(el => {
      const qty = parseInt(el.value || "0", 10);
      if (qty > 0) items.push({ id: parseInt(el.dataset.itemId, 10), quantity: qty });
    });
    if (!items.length) {
      if (errorEl) { errorEl.textContent = "Выберите хотя бы одно блюдо (количество > 0)"; errorEl.style.display = ""; }
      return;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
      let resp;
      if (isEdit) {
        const endpoint = orderType === "staff"
          ? `/api/food/staff-orders/${orderId}/admin-edit`
          : `/api/food/orders/${orderId}/admin-edit`;
        const locCode = orderType === "staff" ? (container.querySelector("#fmAdminLocSelect")?.value || "") : undefined;
        const payload = { items, comment };
        if (locCode !== undefined) payload.location_code = locCode;
        resp = await apiPost(endpoint, payload);
      } else {
        const personType = personTypeEl ? personTypeEl.value : (isAddChild ? "child" : orderType || "child");
        if (personType === "child") {
          const mkStudentId = isAddChild ? (extra?.mkStudentId || "") : (container.querySelector("#fmAdminChildSelect")?.value || "");
          if (!mkStudentId) {
            if (errorEl) { errorEl.textContent = "Выберите ребёнка"; errorEl.style.display = ""; }
            if (submitBtn) submitBtn.disabled = false;
            return;
          }
          resp = await apiPost("/api/food/orders/admin-manual-child", { menu_id: parseInt(menuId, 10), mk_student_id: mkStudentId, items, comment });
        } else {
          const staffUserId = parseInt(container.querySelector("#fmAdminStaffSelect")?.value || "0", 10);
          if (!staffUserId) {
            if (errorEl) { errorEl.textContent = "Выберите сотрудника"; errorEl.style.display = ""; }
            if (submitBtn) submitBtn.disabled = false;
            return;
          }
          const locCode = container.querySelector("#fmAdminLocSelect")?.value || "";
          resp = await apiPost("/api/food/staff-orders/admin-manual-staff", { menu_id: parseInt(menuId, 10), staff_user_id: staffUserId, location_code: locCode, items, comment });
        }
      }
      if (!resp.ok) {
        if (errorEl) { errorEl.textContent = resp.error || "Ошибка при сохранении"; errorEl.style.display = ""; }
        if (submitBtn) submitBtn.disabled = false;
        return;
      }
      setNotice(isEdit ? "Заказ обновлён" : "Заказ добавлен", "success");
      state.isEditingFoodOrder = false;
      container.innerHTML = "";
      if (onSuccess) onSuccess();
    } catch (e) {
      if (errorEl) { errorEl.textContent = safeUserError(e); errorEl.style.display = ""; }
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

async function _confirmFoodOrderDelete(displayName, menuDate) {
  return new Promise(resolve => {
    const overlay = document.createElement("div");
    overlay.className = "food-delete-overlay";
    overlay.innerHTML = `
      <div class="food-delete-dialog">
        <div class="food-delete-dialog-title">Удалить заказ?</div>
        <div class="food-delete-dialog-name">${escapeHtml(displayName || "")}</div>
        ${menuDate ? `<div class="food-delete-dialog-date">Меню: ${escapeHtml(menuDate)}</div>` : ""}
        <div class="food-delete-dialog-warn">Это действие уберёт заказ из сводки и итогов по блюдам.</div>
        <div class="food-delete-dialog-btns">
          <button class="secondary food-delete-cancel-btn">Отмена</button>
          <button class="food-delete-confirm-btn">Удалить</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector(".food-delete-cancel-btn").addEventListener("click", () => { overlay.remove(); resolve(false); });
    overlay.querySelector(".food-delete-confirm-btn").addEventListener("click", () => { overlay.remove(); resolve(true); });
    overlay.addEventListener("click", e => { if (e.target === overlay) { overlay.remove(); resolve(false); } });
  });
}

function _isMenuDeadlinePassed(deadline_at) {
  return _isDeadlinePassed(deadline_at);
}

async function sendFoodPublishNotification(root, menuId) {
  const btn = root.querySelector("#fmNotifyBtn");
  const resultEl = root.querySelector("#fmNotifyResult");
  if (btn) btn.disabled = true;
  if (resultEl) { resultEl.style.display = ""; resultEl.innerHTML = `<span class="food-debug-rawkeys">Отправка...</span>`; }
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/notify-published`, {});
    if (!data.ok) {
      const msg = data.message || data.error || "Ошибка";
      if (resultEl) resultEl.innerHTML = `<div class="food-remind-result food-remind-result--error">${escapeHtml(msg)}</div>`;
      if (btn) btn.disabled = false;
      return;
    }
    let lines = [];
    if (data.sentCount === 0 && data.childrenCount === 0 && data.alreadyNotifiedCount === 0 && data.message) {
      lines.push(data.message);
    } else if (data.alreadyNotifiedCount > 0 && data.sentCount === 0) {
      lines.push("Родители уже были уведомлены.");
      if (data.alreadyNotifiedCount > 0) lines.push(`Уже уведомляли: ${data.alreadyNotifiedCount}`);
      if (data.noParentCount > 0) lines.push(`Без привязанного родителя: ${data.noParentCount}`);
    } else {
      lines.push(`Отправлено родителям: ${data.sentCount}`);
      lines.push(`Детей в уведомлении: ${data.childrenCount}`);
      if (data.alreadyNotifiedCount > 0) lines.push(`Уже уведомляли: ${data.alreadyNotifiedCount}`);
      if (data.noParentCount > 0) lines.push(`Без привязанного родителя: ${data.noParentCount}`);
      if (data.failedCount > 0) lines.push(`Ошибок отправки: ${data.failedCount}`);
    }
    let html = `<div class="food-remind-result ${data.sentCount > 0 ? "food-remind-result--ok" : "food-remind-result--info"}">${lines.map(l => escapeHtml(l)).join("<br>")}</div>`;
    if (Array.isArray(data.noParentChildren) && data.noParentChildren.length) {
      const names = data.noParentChildren.map(c => `• ${c.childName}${c.groupCode && c.groupCode !== "unknown" ? ", " + c.groupCode : ""}`).join("\n");
      html += `<div class="food-remind-no-parent"><b>Нет привязанного родителя:</b><pre style="margin:4px 0;font-size:12px;white-space:pre-wrap">${escapeHtml(names)}</pre></div>`;
    }
    if (resultEl) resultEl.innerHTML = html;
    if (btn) { btn.textContent = data.sentCount > 0 ? "Уведомлено" : "Уведомлено ранее"; }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<div class="food-remind-result food-remind-result--error">${escapeHtml(e.message)}</div>`;
    if (btn) btn.disabled = false;
  }
}

async function sendFoodReminder(root, menuId) {
  const btn = root.querySelector("#fmRemindBtn");
  const resultEl = root.querySelector("#fmRemindResult");
  if (btn) btn.disabled = true;
  if (resultEl) { resultEl.style.display = ""; resultEl.innerHTML = `<span class="food-debug-rawkeys">Отправка...</span>`; }
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/remind-missing`, {});
    if (!data.ok) {
      const msg = data.message || data.error || "Ошибка";
      if (resultEl) resultEl.innerHTML = `<div class="food-remind-result food-remind-result--error">${escapeHtml(msg)}</div>`;
      if (btn) btn.disabled = false;
      return;
    }
    let lines = [];
    if (data.sentCount === 0 && data.childrenCount === 0 && data.alreadyRemindedCount === 0 && data.message) {
      lines.push(data.message);
    } else {
      lines.push(`Отправлено родителям: ${data.sentCount}`);
      lines.push(`Детей в напоминании: ${data.childrenCount}`);
      if (data.alreadyRemindedCount > 0) lines.push(`Уже напоминали недавно: ${data.alreadyRemindedCount}`);
      if (data.noParentCount > 0) lines.push(`Без привязанного родителя: ${data.noParentCount}`);
      if (data.failedCount > 0) lines.push(`Ошибок отправки: ${data.failedCount}`);
    }
    let html = `<div class="food-remind-result food-remind-result--ok">${lines.map(l => escapeHtml(l)).join("<br>")}</div>`;
    if (Array.isArray(data.noParentChildren) && data.noParentChildren.length) {
      const names = data.noParentChildren.map(c => `• ${c.childName}${c.groupCode && c.groupCode !== "unknown" ? ", " + c.groupCode : ""}`).join("\n");
      html += `<div class="food-remind-no-parent"><b>Нет привязанного родителя:</b><pre style="margin:4px 0;font-size:12px;white-space:pre-wrap">${escapeHtml(names)}</pre></div>`;
    }
    if (resultEl) resultEl.innerHTML = html;
    if (btn) { btn.textContent = "Напоминание отправлено"; }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<div class="food-remind-result food-remind-result--error">${escapeHtml(e.message)}</div>`;
    if (btn) btn.disabled = false;
  }
}

function _formatStaffBlockText(byStaff) {
  if (!Array.isArray(byStaff) || !byStaff.length) return "";
  const submitted = byStaff.filter(s => s.status === "submitted");
  if (!submitted.length) return "";
  const lines = submitted.map(s => {
    const details = Array.isArray(s.itemDetails) && s.itemDetails.length ? s.itemDetails : [];
    const itemsText = details.map(it => { const q = parseInt(it.quantity||1,10); return `• ${it.name}${q > 1 ? ` × ${q}` : ""}`; }).join(", ") || "нет блюд";
    return `${s.staffName}: ${itemsText}`;
  }).join("\n");
  return `\n\nСОТРУДНИКИ:\n${lines}`;
}

function _copyFoodSummary(title, dateStr, data) {
  const catOrder = ["Супы", "Салаты", "Второе", "Гарниры", "Сладкое", "Напитки", "Другое"];

  function _sortChildren(arr) {
    return [...arr].sort((a, b) => (a.childName || "").localeCompare(b.childName || "", "ru"));
  }

  function _locationBlock(loc, idx) {
    const sorted = _sortChildren(Array.isArray(loc.byChildren) ? loc.byChildren : []);
    const childLines = sorted.map(ch => {
      if (ch.status === "submitted") {
        const details = Array.isArray(ch.itemDetails) && ch.itemDetails.length ? ch.itemDetails : [];
        return `${ch.childName}:\n${details.map(it => { const q = parseInt(it.quantity||1,10); return `• ${it.name}${it.weight ? ` · ${it.weight}` : ""}${q > 1 ? ` × ${q}` : ""}`; }).join("\n") || "• (нет блюд)"}`;
      }
      if (ch.status === "skipped") return `${ch.childName}:\nБез питания`;
      return `${ch.childName}:\nНе выбрано`;
    }).join("\n\n");
    const staffBlock = _formatStaffBlockText(loc.byStaff);
    const byItems = Array.isArray(loc.byItems) ? loc.byItems : [];
    const byCat = {};
    byItems.forEach(it => { const _c = _normalizeFoodCategory(it.name, it.category); byCat[_c] = byCat[_c] || []; byCat[_c].push(it); });
    const cats = [...new Set([...catOrder, ...Object.keys(byCat)])].filter(c => byCat[c]);
    const orderLines = cats.map(cat =>
      cat.toUpperCase() + "\n" + byCat[cat].map(it => `${it.name}${it.weight ? ` · ${it.weight}` : ""} — ${it.count} шт.`).join("\n")
    ).join("\n\n") || "нет выбранных блюд";
    const utensilsLine = loc.utensils > 0 ? `\nСтоловые приборы × ${loc.utensils}` : "";
    const skipped = sorted.filter(c => c.status === "skipped").map(c => `• ${c.childName}`).join("\n") || "нет";
    return `ЗАКАЗ ${idx + 1} — ${loc.groupCode}, ${loc.location}\n\nЗАКАЗЫ ПО ДЕТЯМ:\n\n${childLines || "нет детей"}${staffBlock}\n\nИТОГ ПО БЛЮДАМ:\n${orderLines}${utensilsLine}\n\nБез питания:\n${skipped}`;
  }

  const byLocations = Array.isArray(data.byLocations) && data.byLocations.length ? data.byLocations : null;
  let bodyText;
  if (byLocations) {
    bodyText = byLocations.map((loc, i) => _locationBlock(loc, i)).join("\n\n---\n\n");
  } else {
    const sorted = _sortChildren(Array.isArray(data.byChildren) ? data.byChildren : []);
    const childLines = sorted.map(ch => {
      if (ch.status === "submitted") {
        const details = Array.isArray(ch.itemDetails) && ch.itemDetails.length ? ch.itemDetails : [];
        return `${ch.childName}:\n${details.map(it => { const q = parseInt(it.quantity||1,10); return `• ${it.name}${it.weight ? ` · ${it.weight}` : ""}${q > 1 ? ` × ${q}` : ""}`; }).join("\n") || "• (нет блюд)"}`;
      }
      if (ch.status === "skipped") return `${ch.childName}:\nБез питания`;
      return `${ch.childName}:\nНе выбрано`;
    }).join("\n\n");
    const staffBlock = _formatStaffBlockText(data.byStaff);
    const byItems = Array.isArray(data.byItems) ? data.byItems : [];
    const byCat = {};
    byItems.forEach(it => { const _c = _normalizeFoodCategory(it.name, it.category); byCat[_c] = byCat[_c] || []; byCat[_c].push(it); });
    const cats = [...new Set([...catOrder, ...Object.keys(byCat)])].filter(c => byCat[c]);
    const orderLines = cats.map(cat =>
      cat.toUpperCase() + "\n" + byCat[cat].map(it => `${it.name}${it.weight ? ` · ${it.weight}` : ""} — ${it.count} шт.`).join("\n")
    ).join("\n\n") || "(нет заказов)";
    const totalUtensils = data.totalUtensils || 0;
    const utensilsLine = totalUtensils > 0 ? `\nСтоловые приборы × ${totalUtensils}` : "";
    const skipped = sorted.filter(c => c.status === "skipped").map(c => `• ${c.childName}`).join("\n") || "нет";
    bodyText = `ЗАКАЗЫ ПО ДЕТЯМ:\n\n${childLines || "нет детей"}${staffBlock}\n\nИТОГ ПО БЛЮДАМ:\n${orderLines}${utensilsLine}\n\nБез питания:\n${skipped}`;
  }

  const text = [`Питание Yellow Club`, `${title}, ${dateStr}`, `⚠️ ВАЖНО: еда должна быть тёплой при доставке`, ``, bodyText].join("\n");
  navigator.clipboard?.writeText(text).then(() => setNotice("Сводка скопирована", "ok")).catch(() => setNotice("Не удалось скопировать", "error"));
}

// ---- Teacher diagnostics (admin) ----
const TEACHER_DIAG_REASONS = {
  ok: "Всё в порядке. Привязка корректна, занятия найдены.",
  no_user_record: "Пользователь не найден в базе. Нужно зарегистрироваться через бот.",
  inactive_user: "Пользователь деактивирован. Восстановите доступ в этой панели.",
  no_mk_teacher_id: "Роль Преподаватель, но MK teacherId не привязан. Привяжите teacherId через кнопку ниже.",
  no_lessons_in_snapshots: "Привязка есть, но занятия в локальной базе не найдены. Нажмите «Обновить расписание» — это загрузит все страницы МойКласс.",
  lessons_exist_without_location: "Занятия найдены, но учебный класс/филиал (YC1/YC2) не определён по названию группы.",
  duplicate_staff_records: "Найдены дубли записей сотрудника. Проверьте активную роль.",
  server_error: "Ошибка сервера при диагностике. Проверьте логи.",
};

const _REFRESH_ERROR_HINTS = {
  moyklass_timeout: "МойКласс отвечал слишком медленно. Попробуйте ещё раз позже или проверьте подключение к МойКласс.",
  moyklass_api_error: "МойКласс вернул ошибку. Проверьте статус API и правильность API-ключа.",
  no_lessons_from_api: "МойКласс не вернул ни одного занятия. Возможно, ещё нет расписания на этот период.",
  no_lessons_in_date_range: "Занятия в МойКласс за указанный период не найдены. Убедитесь, что занятия созданы.",
  teacher_not_found_in_lessons: "teacherId не найден ни в одном занятии. Проверьте правильность MK teacherId.",
  pagination_failed: "Пагинация МойКласс не работает — API возвращает одни и те же страницы. Попробуйте повторить позже.",
  sync_failed: "Не удалось сохранить занятия в базу. Проверьте состояние сервера.",
  unknown_error: "Неизвестная ошибка. Проверьте логи сервера.",
};

function _renderRefreshBlock(refresh) {
  if (!refresh) return "";
  const r = refresh;
  const loaded = r.total_loaded ?? 0;
  const pages = r.pages_loaded ?? 0;
  const synced = r.synced_for_teacher ?? 0;
  const inRange = r.total_in_range ?? 0;
  const strategy = r.strategy_used || "";
  const stage = r.stage || "";
  const dr = r.date_range || {};
  const reasonZ = r.reason_if_zero || "";
  const mismatch = r.id_mismatch_warning || "";
  const nameIds = Array.isArray(r.name_matched_ids) && r.name_matched_ids.length ? r.name_matched_ids.join(", ") : "";
  const timedOut = !!r.timed_out;
  const errCode = r.error_code || "";
  const elapsedMs = r.elapsed_ms || 0;
  const paginationAttempts = Array.isArray(r.pagination_attempts) ? r.pagination_attempts : [];

  // Error / partial result block
  if (errCode && errCode !== "") {
    const hint = _REFRESH_ERROR_HINTS[errCode] || "";
    const syncColor = synced > 0 ? "var(--green)" : "var(--orange,#f90)";
    let html = `<div class="teacher-diag-refresh-error" style="margin-top:8px">`;
    html += `<b>${timedOut ? "⏱ Превышено время ожидания" : "⚠️ Не удалось синхронизировать расписание"}</b><br>`;
    html += `<div style="font-size:12px;margin-top:4px">`;
    html += `Код: <span style="font-family:monospace">${escapeHtml(errCode)}</span>`;
    if (stage) html += ` · Этап: ${escapeHtml(stage)}`;
    html += `<br>Загружено занятий: <b>${loaded}</b> (страниц: ${pages})`;
    if (inRange) html += ` · В периоде: <b>${inRange}</b>`;
    if (synced > 0) html += `<br>Сохранено для этого преподавателя: <b style="color:${syncColor}">${synced}</b>`;
    if (elapsedMs) html += `<br>Время выполнения: ${(elapsedMs/1000).toFixed(1)} с`;
    if (r.last_status) html += `<br>Последний HTTP статус МойКласс: ${r.last_status}`;
    if (hint) html += `<br><span style="color:var(--muted,#657089)">${escapeHtml(hint)}</span>`;
    if (reasonZ) html += `<br><span style="color:var(--orange,#f90)">Причина: ${escapeHtml(reasonZ)}</span>`;
    if (mismatch) html += `<br><span style="color:var(--orange,#f90)">⚠️ ${escapeHtml(mismatch)}</span>`;
    if (nameIds) html += `<br><span style="color:#888">ID по имени в МК: ${escapeHtml(nameIds)}</span>`;
    if (paginationAttempts.length) {
      html += `<details style="margin-top:6px"><summary style="cursor:pointer;font-size:11px;color:#888">Попытки пагинации</summary>`;
      paginationAttempts.forEach(a => {
        html += `<div style="font-size:11px;font-family:monospace">${escapeHtml(a.param)}+${escapeHtml(a.date_key||"")}: `;
        html += `стр.${a.pages_loaded} · найдено: ${a.items_found}`;
        if (a.repeated_page) html += ` · ⚠️ повтор страниц`;
        if (a.timed_out) html += ` · ⏱ timeout`;
        html += `</div>`;
      });
      html += `</details>`;
    }
    html += `</div></div>`;
    return html;
  }

  // Success block
  const syncColor = synced > 0 ? "var(--green)" : "var(--orange,#f90)";
  let html = `<div class="teacher-diag-refresh-info" style="margin-top:8px">`;
  html += `<b>${synced > 0 ? "✅ Расписание обновлено" : "Обновление завершено"}:</b><br>`;
  html += `загружено: <b>${loaded}</b> (страниц: ${pages})`;
  if (dr.from) html += `, период: ${escapeHtml(dr.from)} — ${escapeHtml(dr.to || "")}`;
  html += `<br>в периоде (все): <b>${inRange}</b>`;
  html += `<br>сохранено для преподавателя: <b style="color:${syncColor}">${synced}</b>`;
  if (strategy) html += `<br><span style="font-size:11px;color:#888">Стратегия: ${escapeHtml(strategy)}</span>`;
  if (elapsedMs) html += ` · <span style="font-size:11px;color:#888">${(elapsedMs/1000).toFixed(1)} с</span>`;
  if (timedOut) html += `<br><span style="color:var(--orange,#f90)">⚠️ Частичный результат — закончилось время</span>`;
  if (reasonZ && synced === 0) html += `<br><span style="color:var(--orange,#f90)">Причина 0: ${escapeHtml(reasonZ)}</span>`;
  if (mismatch) html += `<br><span style="color:var(--orange,#f90);font-size:12px">⚠️ ${escapeHtml(mismatch)}</span>`;
  if (nameIds && synced === 0) html += `<br><span style="color:#888;font-size:12px">ID по имени в МК: ${escapeHtml(nameIds)}</span>`;
  html += `</div>`;
  return html;
}

function _renderRawFieldStats(stats) {
  if (!stats) return "";
  const fields = stats.fields || {};
  const ids = (stats.unique_ids_sample || []);
  const shapes = (stats.sample_shapes || []);
  if (!Object.keys(fields).length && !ids.length) return "";
  let html = `<details class="teacher-diag-tech"><summary>Техническая диагностика (raw поля МойКласс)</summary>`;
  html += `<div class="teacher-diag-tech-body">`;
  if (Object.keys(fields).length) {
    html += `<div style="margin-bottom:4px"><b>Поля с данными о преподавателе (сколько занятий содержат поле):</b></div>`;
    html += Object.entries(fields).sort((a,b) => b[1]-a[1]).map(([k,v]) =>
      `<div style="font-size:12px;font-family:monospace">${escapeHtml(k)}: ${v}</div>`
    ).join("");
  }
  if (ids.length) {
    html += `<div style="margin:6px 0 2px"><b>Уникальные ID преподавателей в загруженных занятиях (до 20):</b></div>`;
    html += `<div style="font-size:12px;font-family:monospace;word-break:break-all">${ids.map(escapeHtml).join(", ")}</div>`;
  }
  if (shapes.length) {
    html += `<div style="margin:6px 0 2px"><b>Пример структур занятий:</b></div>`;
    shapes.slice(0,3).forEach(s => {
      const keys = (s.keys || []).join(", ");
      html += `<div style="font-size:11px;font-family:monospace;margin-bottom:6px;padding:4px;background:var(--surface2,rgba(0,0,0,.07));border-radius:4px">`;
      html += `id=${escapeHtml(s.lesson_id||"?")} date=${escapeHtml(s.date||"?")}<br>`;
      html += `ключи: ${escapeHtml(keys)}<br>`;
      for (const [k,v] of Object.entries(s)) {
        if (["lesson_id","date","keys"].includes(k)) continue;
        html += `${escapeHtml(k)}: <span style="color:#88f">${escapeHtml(JSON.stringify(v))}</span><br>`;
      }
      html += `</div>`;
    });
  }
  html += `</div></details>`;
  return html;
}

function _renderSnapshotStats(ss) {
  if (!ss) return "";
  const totalDb = ss.total_in_db_next14 ?? 0;
  if (totalDb === 0) return "";
  const withIds = ss.with_teacher_ids ?? 0;
  const found = ss.id_found_in_snapshots;
  const searchId = ss.searched_id || "";
  const uniqueIds = (ss.unique_ids_sample || []);
  const foundColor = found ? "var(--green)" : "var(--orange,#f90)";
  let html = `<details class="teacher-diag-tech"><summary>Снэпшоты в базе (следующие 14 дней)</summary>`;
  html += `<div class="teacher-diag-tech-body">`;
  html += `<div style="font-size:12px">Занятий в базе: <b>${totalDb}</b>, из них с teacher_ids: <b>${withIds}</b></div>`;
  html += `<div style="font-size:12px">Искали ID: <b style="font-family:monospace">${escapeHtml(searchId)}</b> → <b style="color:${foundColor}">${found ? "НАЙДЕН ✓" : "НЕ НАЙДЕН ✗"}</b></div>`;
  if (uniqueIds.length) {
    html += `<div style="margin-top:4px;font-size:11px;color:#888">IDs в базе: ${uniqueIds.map(escapeHtml).join(", ")}</div>`;
  }
  html += `</div></details>`;
  return html;
}

function _renderTeacherDiagnosticsHtml(d, uid) {
  if (!d || !d.ok) {
    return `<div class="teacher-diag-panel teacher-diag-panel--error">
      <b>Ошибка диагностики:</b> ${escapeHtml(d?.error || "неизвестная ошибка")}
    </div>`;
  }
  const reasonText = TEACHER_DIAG_REASONS[d.reason] || escapeHtml(d.reason || "неизвестно");
  const statusOk = d.reason === "ok";
  const statusIcon = statusOk ? "✅" : "⚠️";
  const locs = (d.locations || []).join(", ") || "не определены";
  const les = d.lessons || {};
  const ss = d.snapshot_stats || null;
  const refreshBlock = _renderRefreshBlock(d.refresh);
  const rawFieldStats = d.refresh?.raw_teacher_field_stats ? _renderRawFieldStats(d.refresh.raw_teacher_field_stats) : "";
  const snapshotStatsHtml = _renderSnapshotStats(ss);
  const sampleHtml = (d.sample_lessons || []).length
    ? `<div class="teacher-diag-section" style="margin-top:8px">Примеры занятий из базы:</div>` +
      (d.sample_lessons || []).slice(0, 5).map(s =>
        `<div class="teacher-diag-row">${escapeHtml(s.date)} ${escapeHtml(s.time)} · ${escapeHtml(s.title || "(нет темы)")} · ${escapeHtml(s.location_code || "?")} · <span style="font-size:11px;color:#888">${escapeHtml(s.source)}</span></div>`
      ).join("")
    : `<div class="teacher-diag-empty" style="margin-top:6px">Занятий в локальной базе не найдено.</div>`;

  // ID mismatch warning: stored ID not in lessons but name found under a different ID
  const nameMatchedIds = d.refresh?.name_matched_ids || [];
  const storedId = String(d.resolved_mk_teacher_id || "");
  const mismatchIds = nameMatchedIds.filter(mid => String(mid) !== storedId);
  let mismatchHtml = "";
  if (mismatchIds.length > 0 && storedId) {
    const suggestId = String(mismatchIds[0]);
    mismatchHtml = `<div class="teacher-diag-mismatch">
      ⚠️ Хранится teacherId <b>${escapeHtml(storedId)}</b>, но имя преподавателя в занятиях МойКласс соответствует ID: <b>${escapeHtml(mismatchIds.join(", "))}</b>.
      <br><button class="secondary btn-sm diag-quick-link-btn" style="margin-top:6px"
        data-uid="${escapeAttr(String(uid))}"
        data-mk-id="${escapeAttr(suggestId)}"
        data-mk-name="${escapeAttr(d.teacher_name || "")}">🔗 Привязать найденный ID (${escapeHtml(suggestId)})</button>
    </div>`;
  }

  return `<div class="teacher-diag-panel">
    <div class="teacher-diag-status">${statusIcon} ${escapeHtml(reasonText)}</div>
    <div class="teacher-diag-grid">
      <span>Telegram ID</span><span>${escapeHtml(String(d.telegram_user_id || uid))}</span>
      <span>Роль</span><span>${escapeHtml(d.resolved_role || "—")}</span>
      <span>Статус</span><span>${escapeHtml(d.status === "active" ? "активен" : d.status || "—")}</span>
      <span>MK teacherId</span><span>${escapeHtml(d.resolved_mk_teacher_id || "не привязан")}${d.mk_teacher_id_has_spaces ? " ⚠️ есть пробелы" : ""}</span>
      <span>Имя</span><span>${escapeHtml(d.teacher_name || "—")}</span>
      <span>Занятий сегодня</span><span>${escapeHtml(String(les.today ?? 0))}</span>
      <span>Занятий завтра</span><span>${escapeHtml(String(les.tomorrow ?? 0))}</span>
      <span>Занятий 7 дн.</span><span>${escapeHtml(String(les.next_7_days ?? 0))}</span>
      <span>Занятий 14 дн.</span><span>${escapeHtml(String(les.next_14_days ?? 0))}</span>
      <span>В контроле</span><span>${escapeHtml(String(les.from_teacher_lesson_control ?? 0))}</span>
      <span>В снэпшотах</span><span>${escapeHtml(String(les.from_lesson_snapshots ?? 0))}</span>
      <span>Филиалы</span><span>${escapeHtml(locs)}</span>
      <span>Доступ к обеду</span><span>${d.food_access ? "да" : "нет"}</span>
    </div>
    ${snapshotStatsHtml}
    ${mismatchHtml}
    ${refreshBlock}
    ${rawFieldStats}
    ${sampleHtml}
    <button class="secondary btn-sm teacher-diag-refresh-btn" style="margin-top:12px" data-diag-uid="${escapeAttr(String(uid))}">🔄 Обновить расписание (все страницы)</button>
  </div>`;
}

// ── MoyKlass teacher picker ──

async function _loadMkTeacherPicker(uid, displayName, currentMkId, containerEl) {
  containerEl.innerHTML = `<div class="mk-picker-panel"><div class="empty">Загружаю список преподавателей МойКласс...</div></div>`;
  let allTeachers = [];
  try {
    const data = await apiGet("/api/admin/moyklass/teachers?include_with_no_lessons=true");
    if (!data.ok) throw new Error(data.error || "Ошибка загрузки");
    allTeachers = data.teachers || [];
    containerEl.innerHTML = _renderMkPickerHtml(uid, displayName, currentMkId, allTeachers, data);
    _attachMkPickerHandlers(uid, displayName, currentMkId, allTeachers, containerEl);
  } catch (e) {
    containerEl.innerHTML = `<div class="mk-picker-panel mk-picker-panel--error">Ошибка: ${escapeHtml(e.message)}</div>`;
  }
}

function _renderMkPickerHtml(uid, displayName, currentMkId, teachers, meta) {
  const dateRange = meta?.date_range ? `${meta.date_range.from} — ${meta.date_range.to}` : "";
  const timedOut = meta?.timed_out ? `<span style="color:#e67e22;font-size:11px"> (данные неполные, МойКласс отвечал медленно)</span>` : "";
  const headerNote = `<div style="font-size:12px;color:var(--muted,#657089);margin-bottom:8px">
    Занятия МойКласс за ${escapeHtml(dateRange)}${timedOut}. Найдено: ${teachers.length} преподавателей.
  </div>`;
  const searchBar = `<input type="search" class="mk-picker-search" placeholder="Поиск по имени или ID..." style="width:100%;box-sizing:border-box;padding:7px 10px;margin-bottom:8px;border:1px solid var(--border,#dce3ed);border-radius:8px;font-size:14px">`;
  const listHtml = teachers.length
    ? `<div class="mk-teacher-list">${teachers.map(t => _renderMkTeacherCard(t, currentMkId)).join("")}</div>`
    : `<div class="empty">Преподаватели не найдены.</div>`;
  return `<div class="mk-picker-panel">
    <div style="font-size:13px;font-weight:600;margin-bottom:6px">Выбор преподавателя МойКласс для ${escapeHtml(displayName)}</div>
    ${headerNote}
    ${searchBar}
    ${listHtml}
  </div>`;
}

function _renderMkTeacherCard(t, currentMkId) {
  const tid = String(t.id || "");
  const isCurrent = tid === String(currentMkId || "");
  const isLinked = t.already_linked_to != null && !isCurrent;
  const locs = (t.locations || []).join(", ") || "—";
  const nearest = t.nearest_lesson_date ? ` · ближайшее ${escapeHtml(t.nearest_lesson_date)}` : "";
  const srcBadge = t.source === "direct_api"
    ? `<span style="font-size:10px;background:#eef2ff;color:#5c6bc0;border-radius:4px;padding:1px 5px;margin-left:4px">API</span>`
    : "";
  const linkedNote = isLinked
    ? `<div style="font-size:11px;color:#e67e22;margin-top:2px">⚠️ Уже привязан к Telegram ${escapeHtml(String(t.already_linked_to))}</div>`
    : "";
  const currentNote = isCurrent
    ? `<div style="font-size:11px;color:#27ae60;margin-top:2px">✅ Текущий привязанный ID</div>`
    : "";
  const btn = !isCurrent
    ? `<button class="secondary btn-sm mk-teacher-select-btn" style="margin-top:6px"
        data-mk-id="${escapeAttr(tid)}"
        data-mk-name="${escapeAttr(String(t.name || ""))}"
        data-already-linked="${escapeAttr(t.already_linked_to != null ? String(t.already_linked_to) : "")}">Привязать</button>`
    : `<button class="btn-sm" disabled style="margin-top:6px;opacity:0.5">Привязан</button>`;
  return `<div class="mk-teacher-card${isCurrent ? " mk-teacher-card--current" : ""}">
    <div style="font-weight:600;font-size:14px">${escapeHtml(t.name || `ID ${tid}`)}${srcBadge}</div>
    <div style="font-size:12px;color:var(--muted,#657089)">ID: ${escapeHtml(tid)} · ${t.lesson_count} занятий${nearest} · ${escapeHtml(locs)}</div>
    ${linkedNote}${currentNote}
    ${btn}
  </div>`;
}

function _attachMkPickerHandlers(uid, displayName, currentMkId, allTeachers, containerEl) {
  const search = containerEl.querySelector(".mk-picker-search");
  if (search) {
    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      const filtered = q
        ? allTeachers.filter(t => String(t.name || "").toLowerCase().includes(q) || String(t.id || "").includes(q))
        : allTeachers;
      const list = containerEl.querySelector(".mk-teacher-list");
      if (list) list.innerHTML = filtered.map(t => _renderMkTeacherCard(t, currentMkId)).join("");
      containerEl.querySelectorAll(".mk-teacher-select-btn").forEach(sb => _attachMkSelectBtn(sb, uid, displayName, containerEl));
    });
  }
  containerEl.querySelectorAll(".mk-teacher-select-btn").forEach(sb => _attachMkSelectBtn(sb, uid, displayName, containerEl));
}

function _attachMkSelectBtn(sb, uid, displayName, containerEl) {
  sb.addEventListener("click", () => {
    const mkId = sb.dataset.mkId || "";
    const mkName = sb.dataset.mkName || "";
    const alreadyLinked = sb.dataset.alreadyLinked || "";
    if (!mkId) return;
    _confirmLinkTeacher(mkId, mkName, uid, async () => {
      sb.disabled = true;
      sb.textContent = "Привязываю...";
      try {
        const res = await apiPost(`/api/admin/staff/${uid}/link-moyklass-teacher`, {
          mk_teacher_id: mkId,
          mk_teacher_name: mkName,
          source: "admin_picker",
        });
        if (!res.ok) throw new Error(res.error || "Ошибка");
        containerEl.innerHTML = `<div class="mk-picker-panel" style="color:var(--green,#27ae60)">
          ✅ Привязан MK teacherId <b>${escapeHtml(mkId)}</b> (${escapeHtml(mkName)}).
          <div style="font-size:12px;margin-top:4px;color:var(--muted,#657089)">Перезагрузите страницу или откройте диагностику для проверки.</div>
        </div>`;
        setTimeout(() => renderAdminContent(), 1500);
      } catch (e) {
        sb.disabled = false;
        sb.textContent = "Привязать";
        setNotice(safeUserError(e), "error");
      }
    }, alreadyLinked);
  });
}

// ── MoyKlass staff binding panel ──────────────────────────────────────────────
// Reverse-direction: show MK teachers, let admin type Telegram user_id + pick role.

const _MK_BIND_ROLE_OPTIONS = [
  {v:"director",       l:"Директор"},
  {v:"client_manager", l:"Клиент-менеджер"},
  {v:"teacher",        l:"Преподаватель"},
  {v:"methodist",      l:"Методист"},
  {v:"intern",         l:"Стажёр"},
  {v:"operations",     l:"Operations"},
  {v:"kitchen",        l:"Кухня"},
  {v:"admin",          l:"Администратор"},
  {v:"owner",          l:"Владелец"},
  {v:"other",          l:"Другой"},
];

const _MK_BIND_ROLE_DISPLAY = Object.fromEntries(_MK_BIND_ROLE_OPTIONS.map(o => [o.v, o.l]));

function _initMkStaffBindPanel(container) {
  container.innerHTML = `
    <details class="mk-staff-bind-panel">
      <summary class="mk-staff-bind-summary">👥 Привязка сотрудников из МойКласс</summary>
      <div class="mk-staff-bind-body">
        <p class="mk-staff-bind-hint">
          Введите числовой Telegram user_id сотрудника (не @username).
          Сотрудник должен открыть бота хотя бы один раз, иначе его данные могут не отображаться корректно.
        </p>
        <div class="mk-staff-bind-controls">
          <input type="search" id="mkBindSearch" placeholder="Поиск по имени или ID..." autocomplete="off">
          <select id="mkBindFilter">
            <option value="all">Все сотрудники</option>
            <option value="unlinked">Только непривязанные</option>
            <option value="linked">Только привязанные</option>
          </select>
          <button type="button" class="secondary" id="mkBindLoad">Загрузить список</button>
        </div>
        <div id="mkBindList" class="mk-bind-list-empty">Нажмите «Загрузить список» для получения сотрудников МойКласс.</div>
      </div>
    </details>`;

  let _allTeachers = [];

  const bindLoad = container.querySelector("#mkBindLoad");
  const bindSearch = container.querySelector("#mkBindSearch");
  const bindFilter = container.querySelector("#mkBindFilter");
  const bindList = container.querySelector("#mkBindList");

  async function loadList() {
    bindLoad.disabled = true;
    bindLoad.textContent = "Загружаю...";
    bindList.innerHTML = `<div class="empty">Загружаю сотрудников МойКласс...</div>`;
    try {
      const data = await apiGet("/api/admin/moyklass/teachers?include_with_no_lessons=true");
      if (!data.ok) throw new Error(data.error || "Ошибка загрузки");
      _allTeachers = data.teachers || [];
      renderList();
    } catch (e) {
      bindList.innerHTML = `<div class="empty" style="color:#c0392b">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
    bindLoad.disabled = false;
    bindLoad.textContent = "Обновить список";
  }

  function renderList() {
    const q = (bindSearch?.value || "").trim().toLowerCase();
    const filter = bindFilter?.value || "all";
    let items = _allTeachers;
    if (q) items = items.filter(t => String(t.name || "").toLowerCase().includes(q) || String(t.id || "").includes(q));
    if (filter === "unlinked") items = items.filter(t => !t.already_linked_to);
    if (filter === "linked")   items = items.filter(t =>  t.already_linked_to);
    if (!items.length) {
      bindList.innerHTML = `<div class="empty">Сотрудники не найдены.</div>`;
      return;
    }
    bindList.innerHTML = items.map(t => _renderMkBindCard(t)).join("");
    bindList.querySelectorAll(".mk-bind-save-btn").forEach(btn => _attachMkBindSaveBtn(btn, bindList));
  }

  bindLoad?.addEventListener("click", loadList);
  bindSearch?.addEventListener("input", renderList);
  bindFilter?.addEventListener("change", renderList);
}

function _renderMkBindCard(t) {
  const tid = String(t.id || "");
  const linked = t.already_linked_to != null;
  const badge = linked
    ? `<span class="mk-bind-badge mk-bind-badge--linked">Привязан</span>`
    : `<span class="mk-bind-badge mk-bind-badge--none">Не привязан</span>`;
  const locs = (t.locations || []).join(", ") || "";
  const nearestDate = t.nearest_lesson_date ? ` · ближайшее ${escapeHtml(t.nearest_lesson_date)}` : "";
  const srcBadge = t.source === "direct_api"
    ? `<span class="mk-src-badge">API</span>`
    : "";
  const linkedInfo = linked
    ? `<div class="mk-bind-current">Telegram ID: <b>${escapeHtml(String(t.already_linked_to))}</b> · Роль: <b>${escapeHtml(_MK_BIND_ROLE_DISPLAY[t.already_linked_role || ""] || t.already_linked_role || "—")}</b>${t.already_linked_name ? ` · ${escapeHtml(t.already_linked_name)}` : ""}</div>`
    : "";
  const roleOpts = _MK_BIND_ROLE_OPTIONS.map(o =>
    `<option value="${escapeAttr(o.v)}"${t.already_linked_role === o.v ? " selected" : ""}>${escapeHtml(o.l)}</option>`
  ).join("");
  return `<div class="mk-bind-card" data-mk-id="${escapeAttr(tid)}">
    <div class="mk-bind-card-header">
      <span class="mk-bind-card-name">${escapeHtml(t.name || `ID ${tid}`)}${srcBadge}</span>
      ${badge}
    </div>
    <div class="mk-bind-card-meta">MK ID: ${escapeHtml(tid)} · ${t.lesson_count || 0} занятий${nearestDate}${locs ? ` · ${escapeHtml(locs)}` : ""}</div>
    ${linkedInfo}
    <div class="mk-bind-form">
      <input type="number" class="mk-bind-tg-input" placeholder="Telegram user_id (число)"
        value="${linked ? escapeAttr(String(t.already_linked_to)) : ""}"
        data-mk-id="${escapeAttr(tid)}" data-mk-name="${escapeAttr(String(t.name || ""))}">
      <select class="mk-bind-role-select" data-mk-id="${escapeAttr(tid)}">
        <option value="">— выберите роль —</option>
        ${roleOpts}
      </select>
      <button type="button" class="secondary mk-bind-save-btn btn-sm"
        data-mk-id="${escapeAttr(tid)}"
        data-mk-name="${escapeAttr(String(t.name || ""))}">Сохранить</button>
    </div>
  </div>`;
}

function _attachMkBindSaveBtn(btn, listEl) {
  btn.addEventListener("click", async () => {
    const mkId   = btn.dataset.mkId || "";
    const mkName = btn.dataset.mkName || "";
    const card   = btn.closest(".mk-bind-card");
    const tgInput   = card?.querySelector(".mk-bind-tg-input");
    const roleSelect = card?.querySelector(".mk-bind-role-select");
    const tgRaw = (tgInput?.value || "").trim();
    const role  = roleSelect?.value || "";
    if (!tgRaw || !/^\d{5,15}$/.test(tgRaw)) {
      setNotice("Введите корректный Telegram user_id (только цифры, 5–15 символов).", "error");
      tgInput?.focus();
      return;
    }
    if (!role) {
      setNotice("Выберите роль.", "error");
      roleSelect?.focus();
      return;
    }
    const tgId = parseInt(tgRaw, 10);
    btn.disabled = true;
    btn.textContent = "Сохраняю...";
    try {
      const res = await apiPost("/api/admin/moyklass/staff-link", {
        mk_teacher_id: mkId,
        mk_teacher_name: mkName,
        telegram_user_id: tgId,
        role,
      });
      if (!res.ok && res.conflict) {
        const confirmed = window.confirm(res.error + "\n\nПерепривязать?");
        if (!confirmed) { btn.disabled = false; btn.textContent = "Сохранить"; return; }
        const res2 = await apiPost("/api/admin/moyklass/staff-link", {
          mk_teacher_id: mkId, mk_teacher_name: mkName, telegram_user_id: tgId, role, force: true,
        });
        if (!res2.ok) throw new Error(res2.error || "Ошибка");
        setNotice(`Привязка обновлена: ${mkName} → TG ${tgId} · ${_MK_BIND_ROLE_DISPLAY[role] || role}`, "ok");
      } else if (!res.ok) {
        throw new Error(res.error || "Ошибка");
      } else {
        setNotice(`${res.was_new ? "Создан" : "Обновлён"}: ${mkName} → TG ${tgId} · ${_MK_BIND_ROLE_DISPLAY[role] || role}`, "ok");
      }
      await renderAdminContent();
    } catch (e) {
      setNotice(safeUserError(e), "error");
      btn.disabled = false;
      btn.textContent = "Сохранить";
    }
  });
}

async function _loadTeacherDiagnostics(uid, containerEl) {
  containerEl.innerHTML = `<div class="teacher-diag-panel"><div class="empty">Загружаю диагностику...</div></div>`;
  try {
    const d = await apiGet(`/api/admin/teacher-diagnostics/${uid}`);
    containerEl.innerHTML = _renderTeacherDiagnosticsHtml(d, uid);
    _attachDiagRefreshBtn(uid, containerEl);
  } catch (e) {
    containerEl.innerHTML = `<div class="teacher-diag-panel teacher-diag-panel--error">Ошибка загрузки диагностики: ${escapeHtml(e.message)}</div>`;
  }
}

function _attachDiagRefreshBtn(uid, containerEl) {
  // Wire "Привязать найденный ID" quick-link buttons inside diagnostics panel
  containerEl.querySelectorAll(".diag-quick-link-btn").forEach(qb => {
    qb.addEventListener("click", () => {
      const mkId = qb.dataset.mkId || "";
      const mkName = qb.dataset.mkName || "";
      const targetUid = qb.dataset.uid || uid;
      if (!mkId) return;
      _confirmLinkTeacher(mkId, mkName, targetUid, async () => {
        qb.disabled = true;
        try {
          const res = await apiPost(`/api/admin/staff/${targetUid}/link-moyklass-teacher`, {
            mk_teacher_id: mkId,
            mk_teacher_name: mkName,
            source: "diag_mismatch_fix",
          });
          if (!res.ok) throw new Error(res.error || "Ошибка привязки");
          containerEl.innerHTML = `<div class="teacher-diag-panel"><div class="empty">ID ${escapeHtml(mkId)} привязан. Загружаю диагностику...</div></div>`;
          const d = await apiGet(`/api/admin/teacher-diagnostics/${targetUid}`);
          containerEl.innerHTML = _renderTeacherDiagnosticsHtml(d, targetUid);
          _attachDiagRefreshBtn(targetUid, containerEl);
        } catch (e) {
          qb.disabled = false;
          setNotice(safeUserError(e), "error");
        }
      });
    });
  });

  const btn = containerEl.querySelector(".teacher-diag-refresh-btn");
  if (!btn) return;
  btn.addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;

    // Animate through progress steps while waiting
    const steps = [
      "1/4 · Проверяю прямой фильтр по teacherId...",
      "2/4 · Загружаю страницы расписания МойКласс...",
      "3/4 · Проверяю поля преподавателей...",
      "4/4 · Сохраняю занятия...",
    ];
    let stepIdx = 0;
    b.textContent = steps[0];
    const stepTimer = setInterval(() => {
      stepIdx = Math.min(stepIdx + 1, steps.length - 1);
      if (!b.disabled) { clearInterval(stepTimer); return; }
      b.textContent = steps[stepIdx];
    }, 5000);

    try {
      const d2 = await apiPost(`/api/admin/teacher-diagnostics/${uid}/refresh`, {});
      clearInterval(stepTimer);
      // Backend returned ok:false with error_code (fatal error, no diag)
      if (!d2.ok && !d2.telegram_user_id) {
        const errCode = d2.error_code || "unknown_error";
        const errMsg = d2.error || "Не удалось выполнить обновление.";
        const hint = _REFRESH_ERROR_HINTS[errCode] || "";
        const details = d2.refresh || {};
        let errHtml = `<div class="teacher-diag-panel teacher-diag-panel--error">`;
        errHtml += `<b>⚠️ Не удалось обновить расписание</b><br>`;
        errHtml += `<div style="font-size:13px;margin-top:6px">${escapeHtml(errMsg)}</div>`;
        if (hint) errHtml += `<div style="font-size:12px;color:var(--muted,#657089);margin-top:4px">${escapeHtml(hint)}</div>`;
        if (errCode) errHtml += `<div style="font-size:11px;color:#888;margin-top:4px;font-family:monospace">Код: ${escapeHtml(errCode)}</div>`;
        if (details.stage) errHtml += `<div style="font-size:11px;color:#888;font-family:monospace">Этап: ${escapeHtml(details.stage)}</div>`;
        if (details.exception_type) errHtml += `<div style="font-size:11px;color:#888;font-family:monospace">Тип: ${escapeHtml(details.exception_type)}</div>`;
        if (details.exception_message) errHtml += `<div style="font-size:11px;color:#888;font-family:monospace;word-break:break-all">Детали: ${escapeHtml(details.exception_message)}</div>`;
        errHtml += `<button class="secondary btn-sm teacher-diag-refresh-btn" style="margin-top:10px" data-diag-uid="${escapeAttr(String(uid))}">🔄 Попробовать ещё раз</button>`;
        errHtml += `</div>`;
        containerEl.innerHTML = errHtml;
        _attachDiagRefreshBtn(uid, containerEl);
        return;
      }
      // Normal response (ok:true with diag, or ok:true with partial refresh)
      containerEl.innerHTML = _renderTeacherDiagnosticsHtml(d2, uid);
      _attachDiagRefreshBtn(uid, containerEl);
    } catch (e2) {
      clearInterval(stepTimer);
      // Network-level error (gunicorn timeout, connection refused, etc.)
      const errMsg = e2.message || String(e2);
      const isTimeout = /timeout|timed out|network|failed to fetch/i.test(errMsg);
      let errHtml = `<div class="teacher-diag-panel teacher-diag-panel--error">`;
      errHtml += `<b>${isTimeout ? "⏱ Время ожидания истекло" : "⚠️ Ошибка соединения"}</b><br>`;
      errHtml += `<div style="font-size:12px;margin-top:4px">`;
      if (isTimeout) {
        errHtml += `Сервер не успел обработать запрос за отведённое время. `;
        errHtml += `Возможно, МойКласс отвечает очень медленно. Попробуйте повторить через минуту.`;
      } else {
        errHtml += `Не удалось связаться с сервером: ${escapeHtml(errMsg)}`;
      }
      errHtml += `</div>`;
      errHtml += `<div style="font-size:11px;color:#888;margin-top:4px">Код: ${isTimeout ? "network_timeout" : "network_error"}</div>`;
      errHtml += `<button class="secondary btn-sm teacher-diag-refresh-btn" style="margin-top:10px" data-diag-uid="${escapeAttr(String(uid))}">🔄 Попробовать ещё раз</button>`;
      errHtml += `</div>`;
      containerEl.innerHTML = errHtml;
      _attachDiagRefreshBtn(uid, containerEl);
    }
  });
}

// ---- Teacher class orders ----
async function _loadAndRenderTeacherClassOrders(root, menuDate, locationCodes) {
  const wrap = document.createElement("div");
  wrap.className = "food-debug-card food-teacher-class-section";
  wrap.innerHTML = `<div class="food-menu-panel-head"><h3>Заказы детей</h3></div><div class="empty">Загрузка...</div>`;
  root.appendChild(wrap);
  try {
    const params = new URLSearchParams();
    if (menuDate) params.set("date", menuDate);
    if (locationCodes) params.set("location_code", locationCodes);
    const qs = params.toString() ? `?${params.toString()}` : "";
    const data = await apiGet(`/api/food/teacher/class-orders${qs}`);
    if (!data.ok) {
      if (data.error === "no_lesson" || data.error === "forbidden") {
        wrap.remove();
        return;
      }
      wrap.innerHTML = `<div class="food-debug-card food-teacher-class-section"><div class="food-menu-panel-head"><h3>Заказы детей</h3></div><div class="food-debug-error">${escapeHtml(data.message || data.error || "Ошибка")}</div></div>`;
      return;
    }
    const locations = data.locations || [];
    const _tco = new Date().toISOString().slice(0, 10);
    const _tct = new Date(); _tct.setDate(_tct.getDate() + 1); const _tcts = _tct.toISOString().slice(0, 10);
    if (!locations.length) {
      const _when0 = menuDate === _tco ? " на сегодня" : (menuDate === _tcts ? " на завтра" : "");
      wrap.innerHTML = `<div class="food-debug-card food-teacher-class-section"><div class="food-menu-panel-head"><h3>Заказы детей${_when0}</h3></div><div class="empty">Заказов на эту дату в вашем учебном классе не найдено.</div></div>`;
      return;
    }
    let html = `<div class="food-debug-card food-teacher-class-section">`;
    for (const loc of locations) {
      const _when = loc.menu_date === _tco ? " на сегодня" : (loc.menu_date === _tcts ? " на завтра" : "");
      const locTitle = locations.length > 1 ? `Заказы детей${_when} · ${escapeHtml(loc.location_name)}` : `Заказы детей${_when}`;
      const dateStr = _formatMenuDate(loc.menu_date);
      const _dlPassed = loc.deadline_at ? _isDeadlinePassed(loc.deadline_at) : false;
      const _dlNote = _dlPassed ? `<div class="food-order-deadline-passed" style="margin-top:4px;margin-bottom:8px">Заказы закрыты для изменений · список для выдачи питания</div>` : "";
      html += `<div class="food-menu-panel-head"><h3>${locTitle}</h3><span style="font-size:13px;color:var(--color-text-secondary)">${escapeHtml(dateStr)}</span></div>${_dlNote}`;
      const children = loc.children || [];
      const ordered = children.filter(c => c.status === "ordered");
      const noFood = children.filter(c => c.status === "no_food");
      const missing = children.filter(c => c.status === "missing");
      if (!children.length) {
        html += `<div class="empty">Нет детей в вашем учебном классе для этого меню.</div>`;
      } else {
        if (ordered.length) {
          html += `<div class="food-summary-section">Заказали (${ordered.length})</div>`;
          for (const ch of ordered) {
            html += `<div class="food-teacher-child-card">
              <div class="food-teacher-child-name">${escapeHtml(ch.child_name)}</div>
              <ul class="food-child-order-items">${(ch.items || []).map(it => { const q = parseInt(it.quantity||1,10); return `<li>${escapeHtml(it.name)}${it.weight ? ` · ${escapeHtml(it.weight)}` : ""}${q > 1 ? ` × ${q}` : ""}</li>`; }).join("")}</ul>
            </div>`;
          }
        }
        if (noFood.length) {
          html += `<div class="food-summary-section">Без питания (${noFood.length})</div>`;
          for (const ch of noFood) {
            html += `<div class="food-teacher-child-card food-teacher-child-card--nofood"><div class="food-teacher-child-name">${escapeHtml(ch.child_name)}</div><div class="food-child-order-note">Без питания</div></div>`;
          }
        }
        if (missing.length) {
          html += `<div class="food-summary-section">Не сделали заказ (${missing.length})</div>`;
          for (const ch of missing) {
            html += `<div class="food-teacher-child-card food-teacher-child-card--missing"><div class="food-teacher-child-name">${escapeHtml(ch.child_name)}</div><div class="food-child-order-note food-child-order-note--missing">Заказ не сделан</div></div>`;
          }
        }
      }
    }
    html += `</div>`;
    wrap.outerHTML = html;
  } catch (e) {
    wrap.innerHTML = `<div class="food-debug-card food-teacher-class-section"><div class="food-debug-error">Не удалось загрузить заказы детей: ${escapeHtml(e.message)}</div></div>`;
  }
}

// ---- Staff food lunch (food-lunch tab) ----
function _ycLocationLabel(code) {
  const map = { YC1: "Кульман 1/1", YC2: "Мстиславца 6", YC3: "Адрес 3" };
  return map[String(code).toUpperCase()] || code;
}

function _renderUpfrontLocPicker(lessonContexts, menuId) {
  if (!lessonContexts || !lessonContexts.length) return "";
  const uniqueLocs = [];
  const seen = new Set();
  for (const ctx of lessonContexts) {
    const lc = ctx.location_code || "";
    if (!lc || seen.has(lc)) continue;
    seen.add(lc);
    uniqueLocs.push(ctx);
  }
  if (uniqueLocs.length <= 1) return "";
  const btns = uniqueLocs.map(ctx => {
    const timeStr = ctx.lesson_time ? `${escapeHtml(ctx.lesson_time)} · ` : "";
    const groupStr = ctx.group_name ? `${escapeHtml(ctx.group_name)} · ` : "";
    const locName = escapeHtml(ctx.location_name || _ycLocationLabel(ctx.location_code || ""));
    return `<button class="teacher-loc-btn secondary btn-sm"
      data-loc="${escapeAttr(ctx.location_code)}"
      data-sl-menu="${escapeAttr(String(menuId))}">${timeStr}${groupStr}${locName}</button>`;
  }).join("");
  return `<div class="teacher-loc-choice" data-menu-id="${escapeAttr(String(menuId))}">
    <div class="teacher-loc-choice-label">Выберите занятие для обеда:</div>
    <div class="teacher-loc-choice-btns">${btns}</div>
  </div>`;
}

function _showStaffLocationPicker(root, menuId, availableLocations, lessonContexts, onPick) {
  const existing = root.querySelector(".staff-location-picker");
  if (existing) existing.remove();
  const card = root.querySelector(`[data-sl-menu-card="${menuId}"]`);
  if (!card) return;
  const div = document.createElement("div");
  div.className = "staff-location-picker";
  // If we have lesson contexts, show them with time+group detail
  const ctxMap = {};
  if (Array.isArray(lessonContexts)) {
    for (const ctx of lessonContexts) {
      if (ctx.location_code) ctxMap[ctx.location_code] = ctx;
    }
  }
  const btns = availableLocations.map(lc => {
    const ctx = ctxMap[lc];
    const timeStr = ctx?.lesson_time ? `${escapeHtml(ctx.lesson_time)} · ` : "";
    const groupStr = ctx?.group_name ? `${escapeHtml(ctx.group_name)} · ` : "";
    const locName = ctx?.location_name ? escapeHtml(ctx.location_name) : escapeHtml(_ycLocationLabel(lc));
    return `<button class="secondary" data-loc="${escapeAttr(lc)}">${timeStr}${groupStr}${locName}</button>`;
  }).join("");
  div.innerHTML = `<div class="staff-location-picker-label">Выберите занятие для обеда:</div>
    <div class="staff-location-picker-btns">${btns}</div>`;
  div.querySelectorAll("[data-loc]").forEach(b => {
    b.addEventListener("click", () => { div.remove(); onPick(b.dataset.loc); });
  });
  card.appendChild(div);
}

function _staffLunchDebugHtml(d) {
  const role = escapeHtml(state.me?.role || "?");
  const uid = escapeHtml(String(state.me?.userId || state.me?.user_id || "?"));
  const mkId = escapeHtml(String(state.me?.mkTeacherId || "—"));
  const tomorrow = d ? escapeHtml(d.tomorrowDate || "?") : "?";
  const lesson = d ? (d.teacherNotLinked ? "нет teacherId" : d.hasTomorrowLesson ? "да" : "нет") : "загрузка";
  const locCodes = d ? (Array.isArray(d.teacherLocationCodes) && d.teacherLocationCodes.length ? d.teacherLocationCodes.join(", ") : "—") : "загрузка";
  const menus = d ? ((d.menus || []).length > 0 ? `найдено (${(d.menus || []).length})` : "не найдено") : "загрузка";
  const resolveMethod = d ? escapeHtml(d.mkResolveMethod || "—") : "загрузка";
  return `<details class="staff-lunch-debug"><summary>debug</summary>
    <div>Роль: <b>${role}</b></div>
    <div>Telegram ID: <b>${uid}</b></div>
    <div>MoyKlass teacherId: <b>${mkId}</b></div>
    <div>Способ определения teacherId: <b>${resolveMethod}</b></div>
    <div>Завтра (${tomorrow}) — занятие: <b>${lesson}</b></div>
    <div>Филиал (YC-код): <b>${escapeHtml(locCodes)}</b></div>
    <div>Меню на завтра: <b>${menus}</b></div>
  </details>`;
}

async function renderStaffFoodLunch(root) {
  root.innerHTML = `<div class="food-debug-card"><div class="empty">Загрузка меню...</div></div>`;
  try {
    const menusData = await apiGet("/api/food/staff/active-menus");
    if (!menusData.ok) {
      root.innerHTML = `<div class="food-debug-card"><h3>Мой обед</h3><div class="food-debug-error">${escapeHtml(menusData.error || "Ошибка загрузки меню")}</div></div>`;
      return;
    }
    if (menusData.teacherNotLinked) {
      root.innerHTML = `<div class="food-debug-card"><h3>Мой обед</h3><div class="parent-food-soon"><p>Ваш профиль преподавателя не связан с МойКласс. Обратитесь к администратору.</p></div></div>`;
      return;
    }
    if (menusData.hasTomorrowLesson === false) {
      let noLessonMsg;
      if (menusData.hasOnlineLessons) {
        noLessonMsg = "На дату меню есть только онлайн-занятия. Обед преподавателя доступен только для офлайн-занятий в учебном классе.";
      } else if (menusData.isTeacherBranch === false) {
        noLessonMsg = "На завтра у вас нет занятий в городской программе — заказ питания недоступен.";
      } else {
        noLessonMsg = "На дату меню не найдено занятие в МойКласс. Обед преподавателя недоступен.";
      }
      root.innerHTML = `<div class="food-debug-card"><h3>Мой обед</h3><div class="parent-food-soon"><p>${escapeHtml(noLessonMsg)}</p></div></div>`;
      // Distribution day: teacher may have a lesson TODAY and need class orders for food distribution
      const _rl0 = state.me?.role;
      if ((_rl0 === "teacher" || _rl0 === "methodist" || _rl0 === "intern") && menusData.todayDate) {
        const _cr0 = document.createElement("div");
        root.appendChild(_cr0);
        _loadAndRenderTeacherClassOrders(_cr0, menusData.todayDate, "").catch(e => console.warn("[teacher-class-orders]", e.message));
      }
      return;
    }
    const menus = Array.isArray(menusData.menus) ? menusData.menus : [];
    if (!menus.length) {
      root.innerHTML = `<div class="food-debug-card"><h3>Мой обед</h3><div class="parent-food-soon"><p>Меню на завтра ещё не опубликовано.</p></div><button class="secondary" id="staffLunchRefresh">Обновить</button></div>`;
      root.querySelector("#staffLunchRefresh")?.addEventListener("click", () => renderStaffFoodLunch(root));
      // Distribution day: also try to show today's class orders (menu for today may already be closed)
      const _rl1 = state.me?.role;
      if ((_rl1 === "teacher" || _rl1 === "methodist" || _rl1 === "intern") && menusData.todayDate) {
        const _cr1 = document.createElement("div");
        root.appendChild(_cr1);
        _loadAndRenderTeacherClassOrders(_cr1, menusData.todayDate, "").catch(e => console.warn("[teacher-class-orders]", e.message));
      }
      return;
    }
    // Split contexts: food-eligible (offline) vs online
    const allLessonContexts = Array.isArray(menusData.lessonContexts) ? menusData.lessonContexts : [];
    const foodLessonContexts = allLessonContexts.filter(c => c.is_food_eligible !== false && !c.is_online);
    const onlineLessonContexts = allLessonContexts.filter(c => c.is_online);
    // Teacher branch context banner with lesson detail
    let teacherBannerHtml = "";
    if (menusData.isTeacherBranch) {
      const nameHtml = menusData.teacherDisplayName ? ` · ${escapeHtml(menusData.teacherDisplayName)}` : "";
      const requiresChoice = menusData.requiresLocationChoice;
      let locDetailHtml = "";
      if (foodLessonContexts.length === 1) {
        const ctx = foodLessonContexts[0];
        const timeStr = ctx.lesson_time ? ` · ${escapeHtml(ctx.lesson_time)}` : "";
        const groupStr = ctx.group_name ? ` · ${escapeHtml(ctx.group_name)}` : "";
        locDetailHtml = `<br><span class="staff-teacher-branch-loc">Занятие: ${escapeHtml(menusData.tomorrowDate || "")}${timeStr}${groupStr}</span>`
          + `<br><span class="staff-teacher-branch-loc">Учебный класс: ${escapeHtml(ctx.location_name || _ycLocationLabel(ctx.location_code || ""))}</span>`;
      } else if (foodLessonContexts.length > 1 && !requiresChoice) {
        // Multiple lessons but all same location
        const loc0 = foodLessonContexts[0];
        locDetailHtml = `<br><span class="staff-teacher-branch-loc">Учебный класс: ${escapeHtml(loc0.location_name || _ycLocationLabel(loc0.location_code || ""))}</span>`;
      } else if (requiresChoice) {
        locDetailHtml = `<br><span class="staff-teacher-branch-loc" style="color:var(--amber,#e67e22)">Несколько занятий на эту дату — выберите учебный класс ниже</span>`;
      }
      teacherBannerHtml = `<div class="staff-teacher-branch-banner">Обед преподавателя${nameHtml}${locDetailHtml}</div>`;
    }
    // Load existing staff orders for all menus
    const staffOrders = {};
    await Promise.all(menus.map(async m => {
      try {
        const r = await apiGet(`/api/food/staff/my-order?menu_id=${m.id}`);
        if (r.ok && r.order) staffOrders[m.id] = r.order;
      } catch (e) {}
    }));

    const catOrder = ["Супы", "Салаты", "Второе", "Гарниры", "Сладкое", "Напитки", "Другое"];
    const lessonContextsAll = foodLessonContexts; // only food-eligible contexts for picker
    const menusHtml = menus.map(menu => {
      const dateStr = _formatMenuDate(menu.menu_date);
      const deadlinePassed = _isDeadlinePassed(menu.deadline_at);
      const order = staffOrders[menu.id] || null;
      const locBadge = menu.location_code ? `<span class="food-loc-badge">${escapeHtml(menu.location_code)}</span> ` : "";
      const titleHtml = `<div class="parent-food-card-title">${locBadge}${escapeHtml(menu.title || dateStr)}</div><div class="parent-food-card-meta">${escapeHtml(dateStr)}</div>`;
      // Determine if this menu card needs an upfront location choice
      const menuLoc = (menu.location_code || "").toUpperCase();
      const teacherLocs = menusData.teacherLocationCodes || [];
      // If menu has a location and teacher has lesson there → auto, no choice needed
      const menuAutoResolved = menuLoc && teacherLocs.includes(menuLoc);
      // Show upfront picker when: multiple teacher locations AND menu has no specific location OR menu location not in teacher locations
      const showLocPicker = menusData.requiresLocationChoice && !menuAutoResolved && !deadlinePassed;
      const upfrontLocPickerHtml = showLocPicker ? _renderUpfrontLocPicker(lessonContextsAll, menu.id) : "";
      const deadlineNote = menu.deadline_at
        ? (deadlinePassed
            ? `<div class="food-order-deadline-passed" style="margin-top:4px">Заказы закрыты для изменений</div>`
            : `<div class="parent-food-deadline">Дедлайн: до ${escapeHtml(_formatDeadline(menu.deadline_at))}</div>`)
        : "";
      const statusBadge = !order ? `<span class="food-staff-status-badge food-staff-status-badge--none">Не выбрано</span>`
        : order.status === "submitted" ? `<span class="food-staff-status-badge food-staff-status-badge--submitted">Выбор отправлен</span>`
        : `<span class="food-staff-status-badge food-staff-status-badge--skipped">Без питания</span>`;

      const rawCats = menu.itemsByCategory || {};
      const cats = {};
      Object.entries(rawCats).forEach(([rc, items]) => items.forEach(it => {
        const nc = _normalizeFoodCategory(it.name, rc);
        (cats[nc] = cats[nc] || []).push(it);
      }));
      const allCats = [...new Set([...catOrder, ...Object.keys(cats)])].filter(c => cats[c] && cats[c].length);
      const qtyMap = {};
      (order?.items || []).forEach(i => { qtyMap[String(i.item_id)] = parseInt(i.quantity || 1, 10); });

      const itemsHtml = deadlinePassed
        ? (order && order.status === "submitted"
            ? `<div class="food-order-summary-items">${(order.items||[]).map(i => { const q = parseInt(i.quantity||1,10); return escapeHtml(i.name||"") + (q > 1 ? ` × ${q}` : ""); }).filter(Boolean).join(", ")}</div>`
            : order && order.status === "skipped" ? `<div class="food-order-summary-note">Без питания</div>` : `<div class="food-order-summary-note">Выбор не сделан (дедлайн прошёл)</div>`)
        : allCats.map(cat => {
            const catItems = cats[cat] || [];
            const rows = catItems.map(item => {
              const qty = qtyMap[String(item.id)] || 0;
              return `<div class="food-order-qty-row${qty > 0 ? " food-order-qty-row--active" : ""}" data-sl-item="${item.id}" data-sl-menu="${menu.id}">
                <div class="food-order-qty-label">${escapeHtml(item.name || "")}${item.weight ? `<span class="food-order-qty-weight"> · ${escapeHtml(item.weight)}</span>` : ""}</div>
                <div class="food-order-qty-ctrl">
                  <button class="food-order-qty-btn" data-sl-dec="${item.id}">−</button>
                  <span class="food-order-qty-val">${qty}</span>
                  <button class="food-order-qty-btn" data-sl-inc="${item.id}">+</button>
                </div>
              </div>`;
            }).join("");
            return `<div class="parent-food-category">${escapeHtml(cat)}</div><div class="food-order-qty-list">${rows}</div>`;
          }).join("");

      const actionsHtml = deadlinePassed ? "" : `
        <div class="food-order-actions">
          <button class="primary" data-sl-submit="${menu.id}">Сохранить мой выбор</button>
          <button class="secondary" data-sl-skip="${menu.id}">Без питания</button>
        </div>`;

      return `<div class="food-staff-section" data-sl-menu-card="${menu.id}" data-sl-menu-auto-loc="${escapeAttr(menuAutoResolved ? menuLoc : (menusData.resolvedLocationCode || ""))}">
        <div class="food-order-card-head" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:10px">
          <div>${titleHtml}${deadlineNote}</div>${statusBadge}
        </div>
        ${upfrontLocPickerHtml}
        ${itemsHtml || `<div class="empty">Блюда не добавлены</div>`}
        ${actionsHtml}
      </div>`;
    }).join("");

    root.innerHTML = `<div class="food-debug-card">
      <div class="food-menu-panel-head"><h3>Мой обед</h3><button class="secondary" id="staffLunchRefresh">Обновить</button></div>
      ${teacherBannerHtml}
      ${menusHtml}
    </div>`;

    root.querySelector("#staffLunchRefresh")?.addEventListener("click", () => renderStaffFoodLunch(root));

    // Wire upfront location choice buttons
    root.querySelectorAll(".teacher-loc-btn").forEach(lb => {
      lb.addEventListener("click", () => {
        const menuId = lb.dataset.slMenu;
        const loc = lb.dataset.loc;
        const picker = lb.closest(".teacher-loc-choice");
        if (picker) {
          picker.querySelectorAll(".teacher-loc-btn").forEach(b => b.classList.remove("teacher-loc-btn--selected"));
          lb.classList.add("teacher-loc-btn--selected");
          picker.dataset.chosenLoc = loc;
        }
        const card = root.querySelector(`[data-sl-menu-card="${menuId}"]`);
        if (card) card.dataset.slMenuAutoLoc = loc;
      });
    });

    function _getMenuLocCode(menuId) {
      const card = root.querySelector(`[data-sl-menu-card="${menuId}"]`);
      return (card?.dataset.slMenuAutoLoc || "").trim().toUpperCase();
    }

    const _staffSaveErrorMsg = {
      deadline_passed: "Дедлайн прошёл. Заказ закрыт.",
      menu_not_available: "Меню недоступно.",
      no_lesson_on_menu_date: "На дату меню не найдено занятие в МойКласс.",
      forbidden: "Нет доступа.",
    };

    async function _doStaffSave(endpoint, menuId, payload, btn) {
      const locCode = _getMenuLocCode(menuId);
      const fullPayload = locCode ? { ...payload, location_code: locCode } : payload;
      const data = await _apiPostRaw(endpoint, fullPayload);
      if (!data.ok) {
        if (data.error === "multiple_locations" && Array.isArray(data.availableLocations)) {
          _showStaffLocationPicker(root, menuId, data.availableLocations, lessonContextsAll, async (chosenLoc) => {
            const card = root.querySelector(`[data-sl-menu-card="${menuId}"]`);
            if (card) card.dataset.slMenuAutoLoc = chosenLoc;
            btn.disabled = true;
            try {
              const d2 = await _apiPostRaw(endpoint, { ...payload, location_code: chosenLoc });
              if (!d2.ok) {
                setNotice(_staffSaveErrorMsg[d2.error] || d2.error || "Ошибка при сохранении выбора", "error");
              } else {
                setNotice(endpoint.includes("skip") ? "Отмечено: без питания." : "Выбор сохранён.", "ok");
                renderStaffFoodLunch(root).catch(e => console.warn("[staff-lunch] reload:", e.message));
              }
            } catch (e2) { setNotice("Не удалось сохранить выбор. Попробуйте ещё раз.", "error"); }
            finally { btn.disabled = false; }
          });
        } else if (data.error === "need_location_choice") {
          setNotice("Выберите занятие для обеда.", "error");
        } else {
          setNotice(_staffSaveErrorMsg[data.error] || data.error || "Ошибка при сохранении выбора", "error");
        }
      } else {
        setNotice(endpoint.includes("skip") ? "Отмечено: без питания." : "Выбор сохранён.", "ok");
        renderStaffFoodLunch(root).catch(e => console.warn("[staff-lunch] reload:", e.message));
      }
    }

    root.querySelectorAll("[data-sl-submit]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const menuId = parseInt(btn.dataset.slSubmit);
        const items = [];
        root.querySelectorAll(`[data-sl-item][data-sl-menu="${menuId}"]`).forEach(row => {
          const iid = parseInt(row.dataset.slItem);
          const valEl = row.querySelector(".food-order-qty-val");
          const qty = valEl ? parseInt(valEl.textContent, 10) : 0;
          if (qty > 0) items.push({ id: iid, quantity: qty });
        });
        btn.disabled = true;
        try {
          await _doStaffSave("/api/food/staff/orders", menuId, { menu_id: menuId, items }, btn);
        } catch (e) { setNotice("Не удалось сохранить выбор. Попробуйте ещё раз.", "error"); }
        finally { btn.disabled = false; }
      });
    });
    root.querySelectorAll("[data-sl-skip]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const menuId = parseInt(btn.dataset.slSkip);
        btn.disabled = true;
        try {
          await _doStaffSave("/api/food/staff/orders/skip", menuId, { menu_id: menuId }, btn);
        } catch (e) { setNotice("Не удалось сохранить выбор. Попробуйте ещё раз.", "error"); }
        finally { btn.disabled = false; }
      });
    });
    root.querySelectorAll("[data-sl-inc]").forEach(btn => {
      btn.addEventListener("click", () => {
        const iid = btn.dataset.slInc;
        const row = root.querySelector(`[data-sl-item="${iid}"]`);
        if (!row) return;
        const valEl = row.querySelector(".food-order-qty-val");
        if (!valEl) return;
        let v = parseInt(valEl.textContent, 10) || 0;
        if (v < 99) { v++; valEl.textContent = v; }
        row.classList.toggle("food-order-qty-row--active", v > 0);
      });
    });
    root.querySelectorAll("[data-sl-dec]").forEach(btn => {
      btn.addEventListener("click", () => {
        const iid = btn.dataset.slDec;
        const row = root.querySelector(`[data-sl-item="${iid}"]`);
        if (!row) return;
        const valEl = row.querySelector(".food-order-qty-val");
        if (!valEl) return;
        let v = parseInt(valEl.textContent, 10) || 0;
        if (v > 0) { v--; valEl.textContent = v; }
        row.classList.toggle("food-order-qty-row--active", v > 0);
      });
    });
    // For teachers/methodists: show children's orders filtered by menu date
    const myRole = state.me?.role;
    if (myRole === "teacher" || myRole === "methodist" || myRole === "intern") {
      const classOrderDate = menusData.tomorrowDate || "";
      const classOrderLocs = (menusData.teacherLocationCodes || []).join(",");
      _loadAndRenderTeacherClassOrders(root, classOrderDate, classOrderLocs).catch(e => console.warn("[teacher-class-orders]", e.message));
    }
  } catch (e) {
    console.error("[staff-lunch] render error:", e.message);
    root.innerHTML = `<div class="food-debug-card"><div class="food-debug-error">Не удалось загрузить меню. Проверьте соединение и обновите страницу.</div><button class="secondary" id="staffLunchRetry" style="margin-top:8px">Обновить</button></div>`;
    root.querySelector("#staffLunchRetry")?.addEventListener("click", () => renderStaffFoodLunch(root));
  }
}

// ---- Food shift report (food-report tab) ----
async function renderFoodReportPanel(root) {
  const today = new Date();
  const mondayOffset = (today.getDay() + 6) % 7;
  const monday = new Date(today); monday.setDate(today.getDate() - mondayOffset);
  const friday = new Date(monday); friday.setDate(monday.getDate() + 4);
  const toISO = d => d.toISOString().slice(0, 10);
  const defaultStart = toISO(monday);
  const defaultEnd = toISO(friday);

  root.innerHTML = `<div class="food-debug-card">
    <h3>Питание · отчёт по стоимости</h3>
    <div class="food-menu-form-row" style="gap:8px;flex-wrap:wrap;align-items:center">
      <label style="margin:0">Период:</label>
      <input type="date" id="frStartDate" value="${defaultStart}" style="font-size:16px">
      <span>—</span>
      <input type="date" id="frEndDate" value="${defaultEnd}" style="font-size:16px">
      <button class="primary btn-sm" id="frLoadBtn">Показать</button>
    </div>
    <div id="frResult" style="margin-top:12px"></div>
  </div>`;

  root.querySelector("#frLoadBtn")?.addEventListener("click", () => {
    const start = root.querySelector("#frStartDate")?.value || "";
    const end = root.querySelector("#frEndDate")?.value || "";
    loadFoodShiftReport(root.querySelector("#frResult"), start, end);
  });

  loadFoodShiftReport(root.querySelector("#frResult"), defaultStart, defaultEnd);
}

async function loadFoodShiftReport(resultEl, startDate, endDate) {
  if (!resultEl) return;
  resultEl.innerHTML = `<div class="empty">Загрузка...</div>`;
  try {
    const params = {};
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    const qs = new URLSearchParams({ initData, ...params }).toString();
    const resp = await fetch(`/api/food/reports/shift?${qs}`, { headers: { "X-Init-Data": initData } });
    const data = await resp.json();
    if (!data.ok) { resultEl.innerHTML = `<div class="food-debug-error">${escapeHtml(data.error || "Ошибка")}</div>`; return; }
    _renderFoodReportResult(resultEl, data, startDate, endDate);
  } catch (e) {
    resultEl.innerHTML = `<div class="food-debug-error">${escapeHtml(e.message)}</div>`;
  }
}

// --- Kitchen / Restaurant read-only summary panel ---

function _fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function _fmtDateWeekday(iso) {
  if (!iso) return "";
  const days = ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];
  const [y, m, d] = iso.split("-");
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  return `${days[date.getDay()]}, ${d}.${m}.${y}`;
}

function _fmtBYN(val) {
  return Number(val || 0).toFixed(2) + " BYN";
}

async function loadKitchenMenus() {
  try {
    const data = await apiGet("/api/food/kitchen/menus");
    state.kitchenMenus = data.menus || [];
    if (!state.kitchenSelectedMenuId && state.kitchenMenus.length > 0) {
      state.kitchenSelectedMenuId = String(state.kitchenMenus[0].id);
    }
    renderKitchenPanel();
    if (state.kitchenSelectedMenuId) await loadKitchenSummary(state.kitchenSelectedMenuId);
  } catch (e) {
    const el = $("kitchenPanelContent");
    if (el) el.innerHTML = `<div class="kitchen-panel-notice">${escapeHtml(e.message)}</div>`;
  }
}

async function loadKitchenSummary(menuId) {
  if (!menuId) return;
  state.kitchenSummaryBusy = true;
  state.kitchenSummaryData = null;
  renderKitchenPanel();
  try {
    const data = await apiGet(`/api/food/kitchen/menus/${encodeURIComponent(menuId)}/summary`);
    state.kitchenSummaryData = data;
    state.kitchenSummaryBusy = false;
    renderKitchenPanel();
  } catch (e) {
    state.kitchenSummaryBusy = false;
    state.kitchenSummaryData = { ok: false, error: e.message };
    renderKitchenPanel();
  }
}

function renderKitchenPanel() {
  const root = $("kitchenPanelContent");
  if (!root) return;

  const menus = state.kitchenMenus || [];
  const showPrices = !!(state.kitchenSummaryData?.showPrices);

  let menuSelectHtml = "";
  if (menus.length === 0) {
    menuSelectHtml = `<div class="kitchen-panel-notice">Нет опубликованных меню.</div>`;
  } else {
    const opts = menus.map(m => {
      const label = m.title ? `${escapeHtml(m.title)} (${_fmtDate(m.menu_date)})` : _fmtDate(m.menu_date);
      const sel = String(m.id) === String(state.kitchenSelectedMenuId) ? " selected" : "";
      return `<option value="${escapeHtml(String(m.id))}"${sel}>${label}</option>`;
    }).join("");
    menuSelectHtml = `<div class="kitchen-menu-selector"><select id="kitchenMenuSelect">${opts}</select></div>`;
  }

  let actionsHtml = `<div class="kitchen-panel-actions">
    <button type="button" id="kitchenRefreshBtn">🔄 Обновить</button>
    <button type="button" id="kitchenCopyBtn">📋 Скопировать заказ</button>
    ${canSeeFoodCostReport() ? `<button type="button" id="kitchenCopyPriceBtn">📊 Отчёт по стоимости</button>` : ""}
    ${state.kitchenSelectedMenuId ? `<button type="button" id="kitchenAuditBtn" class="secondary">✅ Проверить сводку</button>` : ""}
  </div>`;

  let summaryHtml = "";
  if (state.kitchenSummaryBusy) {
    summaryHtml = `<div class="kitchen-panel-notice">Загружаю данные...</div>`;
  } else if (!state.kitchenSummaryData) {
    summaryHtml = menus.length > 0 ? `<div class="kitchen-panel-notice">Выберите меню.</div>` : "";
  } else if (!state.kitchenSummaryData.ok) {
    summaryHtml = `<div class="kitchen-panel-notice">${escapeHtml(state.kitchenSummaryData.error || "Ошибка загрузки")}</div>`;
  } else {
    summaryHtml = _renderKitchenSummaryHtml(state.kitchenSummaryData, showPrices);
  }

  const copyNotice = state.kitchenCopyNotice ? `<div class="kitchen-copy-ok">${escapeHtml(state.kitchenCopyNotice)}</div>` : "";
  const auditHtml = (state.kitchenAuditData && state.kitchenAuditMenuId === state.kitchenSelectedMenuId)
    ? `<div id="kitchenAuditResult" style="margin:8px 0">${_renderAuditBlock(state.kitchenAuditData)}</div>` : "";

  root.innerHTML = `
    <div class="kitchen-panel">
      <div class="kitchen-panel-header">
        <h2 class="kitchen-panel-title">Кухня</h2>
        <p class="kitchen-panel-subtitle">Итоговый заказ для приготовления</p>
      </div>
      ${menuSelectHtml}
      ${actionsHtml}
      ${copyNotice}
      ${auditHtml}
      ${summaryHtml}
    </div>`;

  root.querySelector("#kitchenMenuSelect")?.addEventListener("change", e => {
    state.kitchenSelectedMenuId = e.target.value;
    state.kitchenCopyNotice = "";
    state.kitchenAuditData = null;
    loadKitchenSummary(state.kitchenSelectedMenuId);
  });
  root.querySelector("#kitchenRefreshBtn")?.addEventListener("click", () => {
    state.kitchenCopyNotice = "";
    state.kitchenAuditData = null;
    loadKitchenMenus();
  });
  root.querySelector("#kitchenCopyBtn")?.addEventListener("click", () => copyKitchenOrder(false));
  root.querySelector("#kitchenCopyPriceBtn")?.addEventListener("click", () => copyKitchenOrder(true));
  root.querySelector("#kitchenAuditBtn")?.addEventListener("click", async () => {
    const menuId = state.kitchenSelectedMenuId;
    if (!menuId) return;
    const btn = root.querySelector("#kitchenAuditBtn");
    if (btn) btn.disabled = true;
    try {
      const data = await apiGet(`/api/food/menus/${menuId}/audit`);
      state.kitchenAuditData = data;
      state.kitchenAuditMenuId = menuId;
      renderKitchenPanel();
    } catch (e) {
      state.kitchenAuditData = { ok: false, error: e.message };
      state.kitchenAuditMenuId = menuId;
      renderKitchenPanel();
    }
  });
  root.querySelector("#kitchenAuditCopyBtn")?.addEventListener("click", () => {
    if (state.kitchenAuditData?.ok) _copyAuditReport(state.kitchenAuditData);
  });
}

function _renderKitchenSummaryHtml(data, showPrices) {
  const menu = data.menu || {};
  const byLocations = data.byLocations || [];
  let html = `<div class="kitchen-warm-warning">⚠️ ВАЖНО<br>Еда должна быть тёплой при доставке</div>`;
  html += `<div class="kitchen-deadline-info">Дедлайн: ${menu.deadline_at ? new Date(menu.deadline_at).toLocaleString("ru-RU") : "не задан"}</div>`;
  for (const loc of byLocations) {
    html += _renderKitchenLocationHtml(loc, showPrices);
  }
  if (showPrices && data.overallTotal !== undefined) {
    html += `<div class="kitchen-overall-total"><span>Общая сумма</span><span>${_fmtBYN(data.overallTotal)}</span></div>`;
  }
  if (data.totalUtensils > 0) {
    html += `<div class="kitchen-overall-total"><span>Столовые приборы (всего)</span><span>${data.totalUtensils} комплект</span></div>`;
  }
  return html;
}

function _renderKitchenLocationHtml(loc, showPrices) {
  const byItems = loc.byItems || [];
  const children = (loc.byChildren || []).filter(c => c.status === "submitted");
  const skipped = (loc.noFoodChildren || []);
  const missing = (loc.missingChildren || []);
  const staffOrders = (loc.byStaff || []).filter(s => s.status === "submitted");
  const staffSkipped = (loc.byStaff || []).filter(s => s.status === "skipped");

  let html = `<div class="kitchen-location-block">
    <h3 class="kitchen-location-title">${escapeHtml(loc.location || loc.groupCode)}</h3>
    <div class="kitchen-location-meta">
      <span>Заказали: <b>${loc.submittedOrders || 0}</b></span>
      <span>Без питания: <b>${loc.skippedOrders || 0}</b></span>
      ${loc.missingOrders ? `<span>Ожидаем: <b>${loc.missingOrders}</b></span>` : ""}
    </div>`;

  if (byItems.length > 0 || loc.utensils > 0) {
    html += `<div class="kitchen-section-title">Итог по блюдам</div><div class="kitchen-items-list">`;
    for (const it of byItems) {
      html += `<div class="kitchen-item-row">
        <span class="kitchen-item-name">${escapeHtml(it.name || "")}</span>
        <span class="kitchen-item-count">${it.count} шт.</span>`;
      if (showPrices && it.price !== undefined) {
        html += `<span class="kitchen-item-price">${_fmtBYN(it.price)}</span><span class="kitchen-item-total">= ${_fmtBYN(it.total)}</span>`;
      }
      html += `</div>`;
    }
    if (loc.utensils > 0) {
      html += `<div class="kitchen-item-row kitchen-item-row--utensils">
        <span class="kitchen-item-name">🍴 Столовые приборы</span>
        <span class="kitchen-item-count">${loc.utensils} компл.</span>
      </div>`;
    }
    html += `</div>`;
    if (showPrices && loc.locationTotal !== undefined) {
      html += `<div class="kitchen-location-total"><span>Итого по филиалу</span><span>${_fmtBYN(loc.locationTotal)}</span></div>`;
    }
  }

  if (children.length > 0) {
    html += `<div class="kitchen-section-title">Заказы по детям</div>`;
    for (const ch of children) {
      html += `<div class="kitchen-person-block">
        <div class="kitchen-person-name">${escapeHtml(ch.name)}</div>
        <ul class="kitchen-person-items">`;
      for (const it of (ch.items || [])) {
        let line = escapeHtml(it.name || "");
        if (it.quantity > 1) line += ` × ${it.quantity}`;
        if (showPrices && it.price !== undefined) {
          line += it.quantity > 1 ? ` × ${_fmtBYN(it.price)} = ${_fmtBYN(it.total)}` : ` — ${_fmtBYN(it.price)}`;
        }
        html += `<li>${line}</li>`;
      }
      html += `</ul>`;
      if (showPrices && ch.total !== undefined) {
        html += `<div class="kitchen-person-total">Итого: ${_fmtBYN(ch.total)}</div>`;
      }
      html += `</div>`;
    }
  }

  if (staffOrders.length > 0) {
    const hasTeachers = staffOrders.some(s => s.isTeacher);
    const staffSectionTitle = hasTeachers ? "Сотрудники и преподаватели" : "Сотрудники";
    html += `<div class="kitchen-section-title">${staffSectionTitle}</div>`;
    for (const s of staffOrders) {
      const teacherTag = s.isTeacher ? `<span class="kitchen-teacher-tag">преп.</span>` : "";
      html += `<div class="kitchen-person-block">
        <div class="kitchen-person-name">${escapeHtml(s.name)}${teacherTag}</div>
        <ul class="kitchen-person-items">`;
      for (const it of (s.items || [])) {
        let line = escapeHtml(it.name || "");
        if (it.quantity > 1) line += ` × ${it.quantity}`;
        if (showPrices && it.price !== undefined) {
          line += it.quantity > 1 ? ` × ${_fmtBYN(it.price)} = ${_fmtBYN(it.total)}` : ` — ${_fmtBYN(it.price)}`;
        }
        html += `<li>${line}</li>`;
      }
      html += `</ul>`;
      if (showPrices && s.total !== undefined) {
        html += `<div class="kitchen-person-total">Итого: ${_fmtBYN(s.total)}</div>`;
      }
      html += `</div>`;
    }
  }

  if (skipped.length > 0 || staffSkipped.length > 0) {
    html += `<div class="kitchen-section-title">Без питания</div><div class="kitchen-no-food-list">`;
    for (const name of skipped) html += `<div class="kitchen-no-food-item">${escapeHtml(name)}</div>`;
    for (const s of staffSkipped) html += `<div class="kitchen-no-food-item">${escapeHtml(s.name)}</div>`;
    html += `</div>`;
  }

  html += `</div>`;
  return html;
}

function _buildKitchenCopyText(withPrices) {
  const data = state.kitchenSummaryData;
  if (!data || !data.ok) return "";
  const menu = data.menu || {};
  const dateStr = _fmtDateWeekday(menu.menu_date);
  let lines = withPrices
    ? [`Питание Yellow Club — отчёт по стоимости`, dateStr, "", `⚠️ ВАЖНО: еда должна быть тёплой при доставке`, ""]
    : [`Питание Yellow Club`, dateStr, "", `⚠️ ВАЖНО: еда должна быть тёплой при доставке`, ""];
  for (const loc of (data.byLocations || [])) {
    lines.push(loc.location || loc.groupCode);
    lines.push("");
    const byItems = loc.byItems || [];
    if (byItems.length > 0 || loc.utensils > 0) {
      lines.push("ИТОГ ПО БЛЮДАМ:");
      for (const it of byItems) {
        let line = `${it.name} - ${it.count} шт.`;
        if (withPrices && it.price !== undefined) line += ` × ${_fmtBYN(it.price)} = ${_fmtBYN(it.total)}`;
        lines.push(line);
      }
      if (loc.utensils > 0) lines.push(`Столовые приборы × ${loc.utensils}`);
      if (withPrices && loc.locationTotal !== undefined) {
        lines.push("");
        lines.push(`ИТОГО ПО ФИЛИАЛУ:`);
        lines.push(_fmtBYN(loc.locationTotal));
      }
      lines.push("");
    }
    const children = (loc.byChildren || []).filter(c => c.status === "submitted");
    if (children.length > 0) {
      lines.push("ЗАКАЗЫ ПО ДЕТЯМ:");
      for (const ch of children) {
        lines.push(`${ch.name}:`);
        for (const it of (ch.items || [])) {
          let line = `• ${it.name}`;
          if (it.quantity > 1) {
            line = withPrices && it.price !== undefined
              ? `• ${it.name} - ${it.quantity} шт. × ${_fmtBYN(it.price)} = ${_fmtBYN(it.total)}`
              : `• ${it.name} - ${it.quantity} шт.`;
          } else if (withPrices && it.price !== undefined) {
            line = `• ${it.name} - ${_fmtBYN(it.price)}`;
          }
          lines.push(line);
        }
        if (withPrices && ch.total !== undefined) lines.push(`Итого: ${_fmtBYN(ch.total)}`);
      }
      lines.push("");
    }
    const staffOrders = (loc.byStaff || []).filter(s => s.status === "submitted");
    if (staffOrders.length > 0) {
      const hasTeachersCopy = staffOrders.some(s => s.isTeacher);
      lines.push(hasTeachersCopy ? "СОТРУДНИКИ И ПРЕПОДАВАТЕЛИ:" : "СОТРУДНИКИ:");
      for (const s of staffOrders) {
        const teacherMark = s.isTeacher ? " [преп.]" : "";
        lines.push(`${s.name}${teacherMark}:`);
        for (const it of (s.items || [])) {
          let line = `• ${it.name}`;
          if (it.quantity > 1) {
            line = withPrices && it.price !== undefined
              ? `• ${it.name} - ${it.quantity} шт. × ${_fmtBYN(it.price)} = ${_fmtBYN(it.total)}`
              : `• ${it.name} - ${it.quantity} шт.`;
          } else if (withPrices && it.price !== undefined) {
            line = `• ${it.name} - ${_fmtBYN(it.price)}`;
          }
          lines.push(line);
        }
        if (withPrices && s.total !== undefined) lines.push(`Итого: ${_fmtBYN(s.total)}`);
      }
      lines.push("");
    }
    const noFood = [...(loc.noFoodChildren || []), ...(loc.byStaff || []).filter(s => s.status === "skipped").map(s => s.name)];
    if (noFood.length > 0) {
      lines.push("БЕЗ ПИТАНИЯ:");
      for (const name of noFood) lines.push(name);
      lines.push("");
    }
    lines.push("---");
    lines.push("");
  }
  if (withPrices && data.overallTotal !== undefined) {
    lines.push(`ОБЩАЯ СУММА:`);
    lines.push(_fmtBYN(data.overallTotal));
  }
  return lines.join("\n").trim();
}

async function copyKitchenOrder(withPrices) {
  const text = _buildKitchenCopyText(withPrices);
  if (!text) { state.kitchenCopyNotice = "Нет данных для копирования"; renderKitchenPanel(); return; }
  try {
    await navigator.clipboard.writeText(text);
    state.kitchenCopyNotice = "✓ Скопировано";
  } catch (e) {
    state.kitchenCopyNotice = "Не удалось скопировать: " + e.message;
  }
  renderKitchenPanel();
  setTimeout(() => { state.kitchenCopyNotice = ""; renderKitchenPanel(); }, 3000);
}

// ============================================================
// Kitchen editor — create / edit food menus (v7.0.20)
// ============================================================

function _kitchenTomorrow() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

function _kitchenDefaultDeadline() {
  const d = new Date();
  const y = d.getFullYear(), m = String(d.getMonth() + 1).padStart(2, "0"), day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}T20:00`;
}

async function loadKitchenEditor(root) {
  if (!root) return;
  root.innerHTML = `<div class="kitchen-panel-notice">Загрузка меню...</div>`;
  try {
    const data = await apiGet("/api/food/menus");
    state.kitchenEditorData = data.menus || [];
    state.kitchenEditorSelected = null;
    _renderKitchenEditorList(root);
  } catch (e) {
    root.innerHTML = `<div class="kitchen-panel-notice">Ошибка: ${escapeHtml(e.message)}</div>`;
  }
}

function _renderKitchenEditorList(root) {
  const menus = state.kitchenEditorData || [];
  const tomorrow = _kitchenTomorrow();
  const defaultDeadline = _kitchenDefaultDeadline();

  const createFormHtml = canCreateFoodMenu() ? `
    <div class="kitchen-editor-create-form" id="kitchenEditorCreateForm" style="display:none">
      <h4 style="margin:0 0 12px;font-size:16px;font-weight:700">Создать меню</h4>
      <div class="food-menu-form-row"><label>Дата меню</label><input type="date" id="keDate" value="${tomorrow}" min="${localIsoDate(new Date())}"></div>
      <div class="food-menu-form-row"><label>Название (необязательно)</label><input type="text" id="keTitle" placeholder="Например: Пятница" maxlength="100"></div>
      <div class="food-menu-form-row"><label>Филиал</label><select id="keLocation"><option value="">— Общее (все филиалы) —</option><option value="YC1">YC1 · Кульман 1/1</option><option value="YC2">YC2 · Мстиславца 6</option><option value="YC3">YC3</option></select></div>
      <div class="food-menu-form-row"><label>Дедлайн заказа</label><input type="datetime-local" id="keDeadline" value="${defaultDeadline}"></div>
      <div class="food-menu-actions" style="margin-top:10px">
        <button class="primary" id="keCreateBtn">Создать</button>
        <button class="secondary" id="keCancelBtn">Отмена</button>
      </div>
      <div id="keCreateError" style="display:none;color:#c0392b;font-size:13px;margin-top:6px"></div>
    </div>` : "";

  const menuCardsHtml = menus.length
    ? menus.map(m => {
        const dateStr = _formatMenuDate(m.menu_date);
        const statusBadge = _foodMenuStatusBadge(m.status);
        const locBadge = m.location_code ? `<span class="food-loc-badge">${escapeHtml(m.location_code)}</span>` : "";
        const canPub = m.status === "draft";
        const deadlineHtml = m.deadline_at
          ? `<div style="font-size:12px;color:#888;margin-top:2px">Дедлайн: ${escapeHtml(_formatDeadline(m.deadline_at))}</div>`
          : "";
        return `<div class="kitchen-editor-menu-card">
          <div style="flex:1 1 auto;min-width:0">
            <div style="font-size:15px;font-weight:600">${escapeHtml(m.title || dateStr)} ${statusBadge}${locBadge}</div>
            <div style="font-size:13px;color:#888;margin-top:2px">${escapeHtml(dateStr)} · блюд: ${m.items_count ?? 0}</div>
            ${deadlineHtml}
          </div>
          <div style="display:flex;gap:6px;flex-shrink:0;align-items:center;flex-wrap:wrap">
            <button class="secondary btn-sm" data-ke-open="${m.id}">Открыть</button>
            ${canPub && canPublishFoodMenu() ? `<button class="primary btn-sm" data-ke-publish="${m.id}">Опубликовать</button>` : ""}
            ${canDeleteFoodMenu() ? `<button class="secondary danger btn-sm" data-ke-delete="${m.id}" data-ke-delete-title="${escapeAttr(m.title || dateStr)}" data-ke-delete-date="${escapeAttr(dateStr)}" data-ke-delete-published="${m.status === 'published' ? '1' : '0'}">Удалить</button>` : ""}
          </div>
        </div>`;
      }).join("")
    : `<div class="kitchen-panel-notice">Меню ещё нет. Нажмите «+ Добавить меню».</div>`;

  root.innerHTML = `<div class="kitchen-panel">
    <div class="kitchen-panel-header">
      <h2 class="kitchen-panel-title">Добавить меню</h2>
      <p class="kitchen-panel-subtitle">Создание и подготовка меню питания</p>
    </div>
    <div style="padding:0 16px 8px;display:flex;gap:8px">
      <button type="button" id="keRefreshBtn" class="secondary btn-sm">🔄 Обновить</button>
      ${canCreateFoodMenu() ? `<button type="button" id="keNewBtn" class="primary btn-sm">+ Добавить меню</button>` : ""}
    </div>
    ${createFormHtml}
    <div id="keMenuList" style="padding:0 16px 16px">${menuCardsHtml}</div>
  </div>`;

  root.querySelector("#keRefreshBtn")?.addEventListener("click", () => { state.kitchenEditorData = null; loadKitchenEditor(root); });
  root.querySelector("#keNewBtn")?.addEventListener("click", () => {
    const f = root.querySelector("#kitchenEditorCreateForm");
    if (f) f.style.display = f.style.display === "none" ? "" : "none";
  });
  root.querySelector("#keCancelBtn")?.addEventListener("click", () => {
    const f = root.querySelector("#kitchenEditorCreateForm");
    if (f) f.style.display = "none";
  });
  root.querySelector("#keCreateBtn")?.addEventListener("click", () => _kitchenCreateMenu(root));
  root.querySelectorAll("[data-ke-open]").forEach(btn => {
    btn.addEventListener("click", () => _kitchenOpenMenuDetail(root, parseInt(btn.dataset.keOpen)));
  });
  root.querySelectorAll("[data-ke-publish]").forEach(btn => {
    btn.addEventListener("click", () => _kitchenPublishMenu(root, parseInt(btn.dataset.kePublish)));
  });
  root.querySelectorAll("[data-ke-delete]").forEach(btn => {
    btn.addEventListener("click", () => {
      const menuId = parseInt(btn.dataset.keDelete);
      const title = btn.dataset.keDeleteTitle || "";
      const date = btn.dataset.keDeleteDate || "";
      const isPublished = btn.dataset.keDeletePublished === "1";
      _confirmFoodMenuDelete(title, date, isPublished, async () => {
        btn.disabled = true;
        const result = await _doDeleteFoodMenu(menuId);
        if (!result.ok) {
          btn.disabled = false;
          if (result.error === "has_orders") {
            setNotice(result.message || "Нельзя удалить меню: по нему уже есть заказы.", "error");
          } else {
            setNotice(result.error || "Ошибка удаления", "error");
          }
          return;
        }
        setNotice("Меню удалено", "success");
        state.kitchenEditorData = null;
        state.kitchenMenus = null;
        loadKitchenEditor(root);
        loadKitchenMenus();
      });
    });
  });
}

async function _kitchenCreateMenu(root) {
  const menuDate = root.querySelector("#keDate")?.value || "";
  const title = (root.querySelector("#keTitle")?.value || "").trim();
  const deadline = root.querySelector("#keDeadline")?.value || "";
  const locationCode = root.querySelector("#keLocation")?.value || "";
  const errEl = root.querySelector("#keCreateError");
  if (!menuDate) { if (errEl) { errEl.textContent = "Укажите дату меню"; errEl.style.display = ""; } return; }

  // Check for existing menu on same date
  const existing = (state.kitchenEditorData || []).find(m => m.menu_date === menuDate);
  if (existing) {
    if (existing.status === "published") {
      if (errEl) { errEl.textContent = `Меню на эту дату уже опубликовано (${_formatMenuDate(menuDate)}). Откройте его в списке.`; errEl.style.display = ""; }
    } else {
      if (errEl) { errEl.textContent = `Меню на эту дату уже существует (черновик). Откройте его в списке.`; errEl.style.display = ""; }
    }
    return;
  }
  if (errEl) errEl.style.display = "none";
  try {
    const data = await apiPost("/api/food/menus", { menu_date: menuDate, title: title || null, deadline_at: deadline || null, location_code: locationCode || null });
    if (!data.ok) { if (errEl) { errEl.textContent = data.error || "Ошибка"; errEl.style.display = ""; } return; }
    state.kitchenEditorData = null;
    await _kitchenOpenMenuDetail(root, data.menu.id);
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.style.display = ""; }
  }
}

async function _kitchenOpenMenuDetail(root, menuId) {
  root.innerHTML = `<div class="kitchen-panel-notice">Загрузка...</div>`;
  try {
    const resp = await fetch(`/api/food/menus/${menuId}?` + new URLSearchParams({ initData }), { headers: { "X-Init-Data": initData } });
    const data = await resp.json();
    if (!data.ok) { root.innerHTML = `<div class="kitchen-panel-notice">${escapeHtml(data.error || "Ошибка")}</div>`; return; }
    state.kitchenEditorSelected = data.menu;
    _renderKitchenEditorDetail(root, data.menu);
  } catch (e) {
    root.innerHTML = `<div class="kitchen-panel-notice">Ошибка: ${escapeHtml(e.message)}</div>`;
  }
}

function _renderKitchenEditorDetail(root, menu) {
  if (!menu) { state.kitchenEditorSelected = null; loadKitchenEditor(root); return; }
  const dateStr = _formatMenuDate(menu.menu_date);
  const items = Array.isArray(menu.items) ? menu.items : [];
  const catOrder = [...FOOD_CATEGORIES];
  const cats = {};
  items.forEach(item => {
    const cat = _normalizeFoodCategory(item.name, item.category || "Другое");
    cats[cat] = cats[cat] || [];
    cats[cat].push(item);
  });
  const allCats = [...new Set([...catOrder, ...Object.keys(cats)])].filter(c => cats[c] && cats[c].length);

  const catHtml = allCats.length
    ? allCats.map(cat => {
        const catItems = cats[cat] || [];
        const itemsHtml = catItems.map(item => `
          <div class="food-item-row${item.is_available ? "" : " food-item-hidden"}" data-item-id="${item.id}">
            <span class="food-item-name">${escapeHtml(item.name || "")}</span>
            ${item.weight ? `<span class="food-item-weight">${escapeHtml(item.weight)}</span>` : ""}
            ${item.price ? `<span class="food-item-price">${Number(item.price).toFixed(2)}&nbsp;BYN</span>` : ""}
            <div class="food-item-actions">
              ${item.is_available
                ? `<button class="secondary btn-sm" data-ke-hide="${item.id}">Скрыть</button>`
                : `<button class="secondary btn-sm" data-ke-restore="${item.id}">Вернуть</button>`}
            </div>
          </div>`).join("");
        return `<div class="food-category-block"><div class="food-category-label">${escapeHtml(cat)}</div>${itemsHtml}</div>`;
      }).join("")
    : `<div style="padding:12px 0;color:#888;font-size:14px">Блюд пока нет. Добавьте через форму ниже или загрузите фото.</div>`;

  const catOptions = FOOD_CATEGORIES.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");

  const publishBtn = (menu.status === "draft" && canPublishFoodMenu())
    ? `<button class="primary btn-sm" id="keDetailPublishBtn">Опубликовать</button>` : "";
  const deleteMenuBtn = canDeleteFoodMenu()
    ? `<button class="secondary danger btn-sm" id="keDetailDeleteBtn">Удалить меню</button>` : "";
  const statusNote = menu.status === "published"
    ? `<div style="color:#1a7a3a;font-size:13px;font-weight:600;margin-top:4px">✓ Меню опубликовано — родители могут делать заказы</div>`
    : `<div style="color:#888;font-size:12px;margin-top:4px">Черновик — не виден родителям до публикации</div>`;
  const deadlineNote = menu.deadline_at
    ? `<div style="font-size:12px;color:#888;margin-top:4px">Дедлайн заказа: ${escapeHtml(_formatDeadline(menu.deadline_at))}</div>` : "";
  const itemsWarning = (!items.length && menu.status === "draft")
    ? `<div style="color:#c0392b;font-size:13px;margin:8px 0">⚠ Добавьте блюда перед публикацией</div>` : "";

  const ocrSection = canUseFoodMenuOcr() ? `
    <div class="food-ocr-section" style="margin-top:14px">
      <h4>Распознать меню по фото</h4>
      <div class="food-ocr-inputs">
        <input type="file" id="keOcrInput" accept="image/*" style="font-size:16px;flex:1 1 auto;min-width:0">
        <button class="secondary" id="keOcrBtn">Распознать фото</button>
      </div>
      <div id="keOcrStatus" class="food-ocr-status" style="display:none;margin-top:8px;font-size:13px"></div>
    </div>` : "";

  root.innerHTML = `<div class="kitchen-panel" style="padding-bottom:32px">
    <div style="display:flex;align-items:center;gap:10px;padding:14px 16px 8px">
      <button class="secondary btn-sm" id="keBackBtn">← Назад</button>
      <span style="font-size:16px;font-weight:700">${escapeHtml(menu.title || dateStr)} ${_foodMenuStatusBadge(menu.status)}</span>
    </div>
    <div style="padding:0 16px 10px">
      <div style="font-size:13px;color:#888">${escapeHtml(dateStr)}</div>
      ${statusNote}
      ${deadlineNote}
      ${itemsWarning}
      <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
        ${publishBtn}
        ${deleteMenuBtn}
      </div>
    </div>
    <div style="padding:0 16px">${catHtml}</div>
    <div class="food-item-add-form" style="margin:14px 16px 0">
      <h4>Добавить блюдо</h4>
      <div class="food-menu-form-row"><label>Категория</label><select id="keItemCat">${catOptions}</select></div>
      <div class="food-item-form-grid">
        <div class="food-item-form-name"><input type="text" id="keItemName" placeholder="Название блюда" maxlength="200"></div>
        <input type="text" id="keItemWeight" placeholder="Вес (250/20 г)" style="grid-column:1/-1">
        <input type="text" id="keItemPrice" placeholder="Стоимость (BYN, необяз.)" style="grid-column:1/-1">
      </div>
      <div class="food-menu-actions"><button class="primary" id="keAddItemBtn">Добавить блюдо</button></div>
      <div id="keAddItemError" style="display:none;color:#c0392b;font-size:13px;margin-top:4px"></div>
    </div>
    <div class="food-item-add-form" style="margin:14px 16px 0">
      <h4>Быстро добавить список</h4>
      <textarea id="keBulkText" rows="10" placeholder="СУПЫ&#10;Борщ холодный на кефире — 250 г&#10;&#10;ВТОРОЕ&#10;Котлета куриная — 105 г&#10;&#10;НАПИТКИ&#10;Сок яблочный — 0.2 л" style="width:100%;box-sizing:border-box;font-size:16px;min-height:160px;resize:vertical;border:1px solid var(--border,#ccc);border-radius:8px;padding:8px 10px;background:var(--card-bg,#fff);color:var(--color-text,#222)"></textarea>
      <div class="food-menu-actions" style="margin-top:8px;gap:8px">
        <button class="secondary" id="keBulkParseBtn">Разобрать</button>
        <button class="secondary" id="keBulkClearBtn">Очистить</button>
      </div>
      <div id="keBulkPreview" style="display:none;margin-top:10px"></div>
    </div>
    ${ocrSection}
  </div>`;

  // Restore draft state
  const draft = state.foodMenuDrafts[menu.id];
  const bulkEl = root.querySelector("#keBulkText");
  if (draft?.bulkText && bulkEl) bulkEl.value = draft.bulkText;
  if (draft?.ocrStatus) {
    const ocrStatusEl = root.querySelector("#keOcrStatus");
    if (ocrStatusEl) { ocrStatusEl.textContent = draft.ocrStatus.message; ocrStatusEl.className = `food-ocr-status food-ocr-status--${draft.ocrStatus.type}`; ocrStatusEl.style.display = ""; }
  }
  if (draft?.parsedItems?.length) _kitchenRenderBulkPreview(root, menu.id, draft.parsedItems);

  root.querySelector("#keBackBtn")?.addEventListener("click", () => { state.kitchenEditorSelected = null; state.kitchenEditorData = null; loadKitchenEditor(root); });
  root.querySelector("#keDetailPublishBtn")?.addEventListener("click", () => _kitchenPublishMenu(root, menu.id));
  root.querySelector("#keDetailDeleteBtn")?.addEventListener("click", () => {
    const title = menu.title || _formatMenuDate(menu.menu_date);
    const dateStr = _formatMenuDate(menu.menu_date);
    const isPublished = menu.status === "published";
    _confirmFoodMenuDelete(title, dateStr, isPublished, async () => {
      const result = await _doDeleteFoodMenu(menu.id);
      if (!result.ok) {
        if (result.error === "has_orders") {
          setNotice(result.message || "Нельзя удалить меню: по нему уже есть заказы.", "error");
        } else {
          setNotice(result.error || "Ошибка удаления", "error");
        }
        return;
      }
      setNotice("Меню удалено", "success");
      state.kitchenEditorData = null;
      state.kitchenEditorSelected = null;
      state.kitchenMenus = null;
      loadKitchenEditor(root);
      loadKitchenMenus();
    });
  });
  root.querySelectorAll("[data-ke-hide]").forEach(btn => btn.addEventListener("click", () => _kitchenHideItem(root, parseInt(btn.dataset.keHide), menu.id)));
  root.querySelectorAll("[data-ke-restore]").forEach(btn => btn.addEventListener("click", () => _kitchenRestoreItem(root, parseInt(btn.dataset.keRestore), menu.id)));
  root.querySelector("#keAddItemBtn")?.addEventListener("click", () => _kitchenAddFoodItem(root, menu.id));
  root.querySelector("#keBulkParseBtn")?.addEventListener("click", () => _kitchenParseBulkPreview(root, menu.id));
  root.querySelector("#keBulkClearBtn")?.addEventListener("click", () => {
    delete state.foodMenuDrafts[menu.id];
    if (bulkEl) bulkEl.value = "";
    const preview = root.querySelector("#keBulkPreview");
    if (preview) { preview.innerHTML = ""; preview.style.display = "none"; }
    const ocrInput = root.querySelector("#keOcrInput");
    if (ocrInput) ocrInput.value = "";
    const ocrStatusEl = root.querySelector("#keOcrStatus");
    if (ocrStatusEl) { ocrStatusEl.textContent = ""; ocrStatusEl.style.display = "none"; }
  });
  root.querySelector("#keBulkText")?.addEventListener("input", e => {
    if (!state.foodMenuDrafts[menu.id]) state.foodMenuDrafts[menu.id] = {};
    state.foodMenuDrafts[menu.id].bulkText = e.target.value;
  });
  root.querySelector("#keOcrBtn")?.addEventListener("click", () => _kitchenUploadOcr(root, menu.id));
}

async function _kitchenAddFoodItem(root, menuId) {
  const category = root.querySelector("#keItemCat")?.value || "Другое";
  const name = (root.querySelector("#keItemName")?.value || "").trim();
  const weight = (root.querySelector("#keItemWeight")?.value || "").trim();
  const priceRaw = (root.querySelector("#keItemPrice")?.value || "").replace(",", ".").replace(/BYN|руб\.?/gi, "").trim();
  const price = parseFloat(priceRaw) || 0;
  const errEl = root.querySelector("#keAddItemError");
  if (!name) { if (errEl) { errEl.textContent = "Укажите название блюда"; errEl.style.display = ""; } return; }
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/items`, { category, name, weight: weight || null, price });
    if (!data.ok) { if (errEl) { errEl.textContent = data.error || "Ошибка"; errEl.style.display = ""; } return; }
    await _kitchenOpenMenuDetail(root, menuId);
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.style.display = ""; }
  }
}

async function _kitchenHideItem(root, itemId, menuId) {
  try {
    const data = await apiPost(`/api/food/items/${itemId}/hide`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка", "error"); return; }
    await _kitchenOpenMenuDetail(root, menuId);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function _kitchenRestoreItem(root, itemId, menuId) {
  try {
    const data = await apiPost(`/api/food/items/${itemId}/restore`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка", "error"); return; }
    await _kitchenOpenMenuDetail(root, menuId);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function _kitchenPublishMenu(root, menuId) {
  if (!canPublishFoodMenu()) { setNotice("Нет прав для публикации меню", "error"); return; }
  try {
    const data = await apiPost(`/api/food/menus/${menuId}/publish`, {});
    if (!data.ok) { setNotice(data.error || "Ошибка публикации", "error"); return; }
    setNotice("Меню опубликовано — родители могут делать заказы", "ok");
    state.kitchenEditorData = null;
    // Reload the kitchen menus list (summary tab) so new menu appears there
    state.kitchenMenus = null;
    await _kitchenOpenMenuDetail(root, menuId);
    await loadKitchenMenus();
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function _kitchenAddFoodItemsBulk(root, menuId) {
  const btn = root.querySelector("#keBulkAddAllBtn");
  const statusEl = root.querySelector("#keBulkAddStatus");
  const items = [...root.querySelectorAll(".ke-bulk-item-row")].map(row => {
    const priceRaw = (row.querySelector(".keBulkPrice")?.value || "").replace(",", ".").replace(/BYN|руб\.?/gi, "").trim();
    return {
      category: row.querySelector(".keBulkCat")?.value || "Другое",
      name: (row.querySelector(".keBulkName")?.value || "").trim(),
      weight: (row.querySelector(".keBulkWeight")?.value || "").trim() || null,
      price: parseFloat(priceRaw) || 0,
    };
  }).filter(it => it.name.length >= 1);
  if (!items.length) { if (statusEl) statusEl.textContent = "Нет блюд для добавления."; return; }
  if (btn) btn.disabled = true;
  let added = 0;
  for (const it of items) {
    try {
      const data = await apiPost(`/api/food/menus/${menuId}/items`, { category: it.category, name: it.name, weight: it.weight || null, price: it.price || 0 });
      if (data.ok) added++;
    } catch (_) { /* continue */ }
  }
  if (statusEl) statusEl.textContent = `Добавлено блюд: ${added}`;
  delete state.foodMenuDrafts[menuId];
  await _kitchenOpenMenuDetail(root, menuId);
}

function _kitchenRenderBulkPreview(root, menuId, items) {
  const preview = root.querySelector("#keBulkPreview");
  if (!preview) return;
  if (!items || !items.length) {
    preview.innerHTML = `<div style="color:#c0392b;font-size:13px">Блюда не найдены. Проверьте формат текста.</div>`;
    preview.style.display = "";
    return;
  }
  const inStyle = "font-size:16px;border:1px solid var(--border,#ccc);border-radius:6px;padding:4px 8px;background:var(--card-bg,#fff);color:var(--color-text,#222)";
  const rowsHtml = items.map((it, idx) => `
    <div class="ke-bulk-item-row" data-idx="${idx}">
      <select class="keBulkCat" style="${inStyle};flex:0 0 auto;padding:4px 6px">
        ${FOOD_CATEGORIES.map(c => `<option value="${escapeAttr(c)}"${c === it.category ? " selected" : ""}>${escapeHtml(c)}</option>`).join("")}
      </select>
      <input type="text" class="keBulkName" value="${escapeAttr(it.name)}" placeholder="Название" maxlength="200" style="${inStyle};flex:1 1 auto;min-width:0">
      <input type="text" class="keBulkWeight" value="${escapeAttr(it.weight || "")}" placeholder="Вес" style="${inStyle};width:90px;flex:0 0 auto">
      <input type="text" class="keBulkPrice" value="${it.internalPrice != null ? escapeAttr(String(it.internalPrice)) : ""}" placeholder="BYN" title="Стоимость (BYN)" style="${inStyle};width:72px;flex:0 0 auto">
      <button class="secondary btn-sm keBulkDel" style="flex:0 0 auto;padding:4px 8px;font-size:13px">✕</button>
    </div>`).join("");
  preview.innerHTML = `
    <div style="margin:0 0 8px;font-size:13px;color:#888">Найдено блюд: <b>${items.length}</b></div>
    <div id="keBulkItemsContainer">${rowsHtml}</div>
    <div style="font-size:12px;color:#888;margin:6px 0">Можно изменить категорию, название, вес и стоимость.</div>
    <div class="food-menu-actions" style="margin-top:10px">
      <button class="primary" id="keBulkAddAllBtn">Добавить все блюда</button>
    </div>
    <div id="keBulkAddStatus" style="font-size:13px;margin-top:6px"></div>`;
  preview.style.display = "";
  preview.querySelectorAll(".keBulkDel").forEach(btn => {
    btn.addEventListener("click", () => { btn.closest(".ke-bulk-item-row")?.remove(); });
  });
  preview.querySelector("#keBulkAddAllBtn")?.addEventListener("click", () => _kitchenAddFoodItemsBulk(root, menuId));
}

function _kitchenParseBulkPreview(root, menuId) {
  const text = root.querySelector("#keBulkText")?.value || "";
  if (!state.foodMenuDrafts[menuId]) state.foodMenuDrafts[menuId] = {};
  state.foodMenuDrafts[menuId].bulkText = text;
  const items = parseFoodMenuText(text);
  state.foodMenuDrafts[menuId].parsedItems = items;
  _kitchenRenderBulkPreview(root, menuId, items);
}

async function _kitchenUploadOcr(root, menuId) {
  const input = root.querySelector("#keOcrInput");
  const statusEl = root.querySelector("#keOcrStatus");
  const btn = root.querySelector("#keOcrBtn");
  if (!input || !statusEl) return;
  const file = input.files?.[0];
  if (!file) { statusEl.textContent = "Выберите файл изображения."; statusEl.className = "food-ocr-status food-ocr-status--error"; statusEl.style.display = ""; return; }
  if (file.size > 5 * 1024 * 1024) { statusEl.textContent = "Файл слишком большой (максимум 5 МБ)."; statusEl.className = "food-ocr-status food-ocr-status--error"; statusEl.style.display = ""; return; }
  if (btn) btn.disabled = true;
  statusEl.textContent = "Распознавание..."; statusEl.className = "food-ocr-status"; statusEl.style.display = "";
  try {
    const fd = new FormData();
    appendAuthForm(fd);
    fd.append("image", file, file.name);
    const resp = await fetch(`/api/food/menus/${menuId}/ocr-preview`, { method: "POST", body: fd });
    const data = await resp.json();
    if (!data.ok) {
      let errMsg = data.message || data.error || "Ошибка распознавания.";
      if (data.error === "ocr_language_missing") {
        const langs = Array.isArray(data.availableLanguages) && data.availableLanguages.length ? " Доступные языки: " + data.availableLanguages.join(", ") + "." : "";
        errMsg = "В Tesseract не установлен русский язык." + langs;
      }
      statusEl.textContent = errMsg; statusEl.className = "food-ocr-status food-ocr-status--error"; return;
    }
    const rawText = data.rawText || "";
    const bulkEl = root.querySelector("#keBulkText");
    if (bulkEl) bulkEl.value = rawText;
    const lowQuality = Array.isArray(data.warnings) && data.warnings.some(w => w.code === "ocr_low_quality");
    const ocrStatusType = lowQuality ? "warn" : "ok";
    const ocrStatusMsg = lowQuality ? "Текст распознан, но качество низкое. Проверьте фото или исправьте вручную." : "Текст распознан. Проверьте список перед добавлением.";
    if (!state.foodMenuDrafts[menuId]) state.foodMenuDrafts[menuId] = {};
    state.foodMenuDrafts[menuId].ocrStatus = { type: ocrStatusType, message: ocrStatusMsg };
    state.foodMenuDrafts[menuId].bulkText = rawText;
    statusEl.textContent = ocrStatusMsg; statusEl.className = `food-ocr-status food-ocr-status--${ocrStatusType}`;
    _kitchenParseBulkPreview(root, menuId);
  } catch (e) {
    statusEl.textContent = "Ошибка соединения: " + e.message; statusEl.className = "food-ocr-status food-ocr-status--error";
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Shared helpers for food menu deletion (used by kitchen editor and admin panel)
async function _doDeleteFoodMenu(menuId) {
  try {
    return await apiPost(`/api/food/menus/${menuId}/delete`, {});
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

function _confirmFoodMenuDelete(menuTitle, menuDate, isPublished, onConfirm) {
  const warningExtra = isPublished
    ? `<div class="food-delete-dialog-warn">Меню уже опубликовано. Удалить можно только если по нему ещё нет заказов.</div>`
    : `<div class="food-delete-dialog-warn">Это действие уберёт меню из списка и оно не будет доступно для заказов.</div>`;
  const overlay = document.createElement("div");
  overlay.className = "food-delete-overlay";
  overlay.innerHTML = `
    <div class="food-delete-dialog">
      <div class="food-delete-dialog-title">Удалить меню?</div>
      <div class="food-delete-dialog-name">${escapeHtml(menuTitle)}</div>
      <div class="food-delete-dialog-date">${escapeHtml(menuDate)}</div>
      ${warningExtra}
      <div class="food-delete-dialog-btns">
        <button class="secondary food-delete-cancel-btn">Отмена</button>
        <button class="food-delete-confirm-btn" style="background:#c0392b">Удалить меню</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector(".food-delete-cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector(".food-delete-confirm-btn").addEventListener("click", () => { overlay.remove(); onConfirm(); });
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
}

function _confirmLinkTeacher(mkId, mkName, targetUid, onConfirm, alreadyLinkedTo = "") {
  const overlay = document.createElement("div");
  overlay.className = "food-delete-overlay";
  const warnHtml = alreadyLinkedTo
    ? `<div class="food-delete-dialog-warn">⚠️ Этот ID уже привязан к Telegram ${escapeHtml(alreadyLinkedTo)}. Тот сотрудник потеряет привязку.</div>`
    : "";
  overlay.innerHTML = `
    <div class="food-delete-dialog">
      <div class="food-delete-dialog-title">Привязать MK teacherId?</div>
      <div class="food-delete-dialog-name">${escapeHtml(mkName || `ID ${mkId}`)}</div>
      <div class="food-delete-dialog-date">MK teacherId: ${escapeHtml(mkId)}</div>
      <div class="food-delete-dialog-date">Telegram ID: ${escapeHtml(String(targetUid))}</div>
      ${warnHtml}
      <div class="food-delete-dialog-btns">
        <button class="secondary food-delete-cancel-btn">Отмена</button>
        <button class="food-delete-confirm-btn" style="background:#2980b9">Привязать</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector(".food-delete-cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector(".food-delete-confirm-btn").addEventListener("click", () => { overlay.remove(); onConfirm(); });
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
}

function _confirmUnlinkTeacher(name, uid, mkId, roleLabel, onConfirm) {
  const overlay = document.createElement("div");
  overlay.className = "food-delete-overlay";
  overlay.innerHTML = `
    <div class="food-delete-dialog">
      <div class="food-delete-dialog-title">Отвязать MK teacherId?</div>
      <div class="food-delete-dialog-name">${escapeHtml(name)}</div>
      <div class="food-delete-dialog-date">Telegram ID: ${escapeHtml(String(uid))}</div>
      <div class="food-delete-dialog-date">MK teacherId: ${escapeHtml(mkId)}</div>
      <div class="food-delete-dialog-date">Роль: ${escapeHtml(roleLabel)}</div>
      <div class="food-delete-dialog-warn">Пользователь останется в текущей роли, но больше не будет считаться преподавателем в питании.<br>История заказов сохранится.</div>
      <div class="food-delete-dialog-btns">
        <button class="secondary food-delete-cancel-btn">Отмена</button>
        <button class="food-delete-confirm-btn" style="background:#e67e22">Отвязать</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector(".food-delete-cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector(".food-delete-confirm-btn").addEventListener("click", () => { overlay.remove(); onConfirm(); });
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
}

function _confirmStaffDeactivate(name, uid, roleLabel, onConfirm) {
  const overlay = document.createElement("div");
  overlay.className = "food-delete-overlay";
  overlay.innerHTML = `
    <div class="food-delete-dialog">
      <div class="food-delete-dialog-title">Отключить доступ сотруднику?</div>
      <div class="food-delete-dialog-name">${escapeHtml(name)}</div>
      <div class="food-delete-dialog-date">Telegram ID: ${escapeHtml(String(uid))}</div>
      <div class="food-delete-dialog-date">Роль: ${escapeHtml(roleLabel)}</div>
      <div class="food-delete-dialog-warn">Сотрудник больше не сможет использовать Mini App.<br>История заказов и действий сохранится.</div>
      <div class="food-delete-dialog-btns">
        <button class="secondary food-delete-cancel-btn">Отмена</button>
        <button class="food-delete-confirm-btn" style="background:#c0392b">Отключить доступ</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector(".food-delete-cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector(".food-delete-confirm-btn").addEventListener("click", () => { overlay.remove(); onConfirm(); });
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
}

// ============================================================
// Food summary audit (v7.0.22)
// ============================================================

function _renderAuditBlock(data) {
  if (!data || !data.ok) {
    const msg = data?.error || "Ошибка проверки";
    return `<div class="food-audit-block food-audit-block--error"><span class="food-audit-icon">❌</span> ${escapeHtml(msg)}</div>`;
  }
  const status = data.auditStatus || "passed";
  const icon = status === "passed" ? "✅" : status === "warning" ? "⚠️" : "❌";
  const statusLabel = status === "passed" ? "Сводка проверена — всё сходится" : status === "warning" ? "Сводка требует проверки" : "В сводке есть ошибки";
  const blockClass = status === "passed" ? "food-audit-block--ok" : status === "warning" ? "food-audit-block--warn" : "food-audit-block--error";
  const s = data.summary || {};
  const dateLabel = data.menuDate ? _formatMenuDate(data.menuDate) : "";
  const titleLabel = data.menuTitle ? `${escapeHtml(data.menuTitle)} · ` : "";

  let detailsHtml = `<details class="food-audit-details"><summary>Показать детали</summary>`;
  // Location rows
  if ((data.locations || []).length) {
    detailsHtml += `<div class="food-audit-section">`;
    for (const loc of data.locations) {
      if (loc.locationCode === "unknown" && !loc.childOrders && !loc.staffOrders && !loc.noFood) continue;
      detailsHtml += `<div class="food-audit-loc">
        <div class="food-audit-loc-name">${escapeHtml(loc.locationName || loc.locationCode)}</div>
        <div class="food-audit-loc-row">заказов детей: <b>${loc.childOrders}</b> · сотрудников: <b>${loc.staffOrders}</b> · без питания: <b>${loc.noFood}</b></div>
        <div class="food-audit-loc-row">блюд всего: <b>${loc.totalItemsQty}</b> · сумма: <b>${loc.totalAmount.toFixed(2)} BYN</b></div>`;
      if ((loc.items || []).length) {
        detailsHtml += `<ul class="food-audit-items">` + loc.items.map(it => `<li>${escapeHtml(it.itemName)} × ${it.qty}${it.amount ? ` = ${it.amount.toFixed(2)} BYN` : ""}</li>`).join("") + `</ul>`;
      }
      detailsHtml += `</div>`;
    }
    detailsHtml += `</div>`;
  }
  // Checks
  const failedChecks = (data.checks || []).filter(c => c.status !== "passed");
  if (failedChecks.length) {
    detailsHtml += `<div class="food-audit-section">`;
    for (const ch of failedChecks) {
      const cl = ch.status === "error" ? "food-audit-check--error" : "food-audit-check--warn";
      detailsHtml += `<div class="food-audit-check ${cl}">${escapeHtml(ch.message)}</div>`;
    }
    detailsHtml += `</div>`;
  }
  // All items summary
  if ((data.items || []).length) {
    detailsHtml += `<div class="food-audit-section"><div class="food-audit-section-title">Итог по блюдам (все адреса)</div><ul class="food-audit-items">` +
      data.items.map(it => `<li>${escapeHtml(it.itemName)} × ${it.qty}${it.amount ? ` = ${it.amount.toFixed(2)} BYN` : ""}</li>`).join("") +
      `</ul></div>`;
  }
  detailsHtml += `</details>`;

  const errorsHtml = (data.errors || []).length
    ? `<div class="food-audit-errors">${data.errors.map(e => `<div class="food-audit-err-row">❌ ${escapeHtml(e)}</div>`).join("")}</div>` : "";
  const warningsHtml = (data.warnings || []).length
    ? `<div class="food-audit-warnings">${data.warnings.map(w => `<div class="food-audit-warn-row">⚠️ ${escapeHtml(w)}</div>`).join("")}</div>` : "";

  return `<div class="food-audit-block ${blockClass}">
    <div class="food-audit-header"><span class="food-audit-icon">${icon}</span> <b>${escapeHtml(statusLabel)}</b></div>
    <div class="food-audit-meta">${titleLabel}${escapeHtml(dateLabel)}</div>
    <div class="food-audit-totals">
      людей с заказом: <b>${s.totalPeople || 0}</b> · детей: <b>${s.childOrders || 0}</b> · сотр.: <b>${s.staffOrders || 0}</b> · без питания: <b>${s.noFood || 0}</b>
    </div>
    <div class="food-audit-totals">блюд всего: <b>${s.totalItemsQty || 0}</b> · сумма: <b>${(s.totalAmount || 0).toFixed(2)} BYN</b>${s.deletedChildOrders ? ` · удал. заказов: ${s.deletedChildOrders}` : ""}</div>
    ${errorsHtml}${warningsHtml}
    ${detailsHtml}
    <div style="margin-top:8px"><button class="secondary btn-sm" id="auditCopyBtn">📋 Скопировать проверку</button></div>
  </div>`;
}

function _copyAuditReport(data) {
  if (!data || !data.ok) { setNotice("Нет данных для копирования", "error"); return; }
  const s = data.summary || {};
  const dateLabel = data.menuDate ? _formatMenuDate(data.menuDate) : "";
  const titleLabel = data.menuTitle || dateLabel;
  const statusText = data.auditStatus === "passed" ? "✅ всё сходится" : data.auditStatus === "warning" ? "⚠️ есть предупреждения" : "❌ есть ошибки";
  let lines = [`Проверка сводки питания`, titleLabel, ``, `Статус: ${statusText}`, ``];
  for (const loc of (data.locations || [])) {
    if (loc.locationCode === "unknown" && !loc.childOrders && !loc.staffOrders && !loc.noFood) continue;
    lines.push(`📍 ${loc.locationName || loc.locationCode}:`);
    lines.push(`  Детей: ${loc.childOrders}`);
    lines.push(`  Сотрудников/преподавателей: ${loc.staffOrders}`);
    lines.push(`  Без питания: ${loc.noFood}`);
    lines.push(`  Блюд всего: ${loc.totalItemsQty}`);
    lines.push(`  Сумма: ${loc.totalAmount.toFixed(2)} BYN`);
    if ((loc.items || []).length) {
      for (const it of loc.items) {
        lines.push(`    - ${it.itemName} × ${it.qty}${it.amount ? ` = ${it.amount.toFixed(2)} BYN` : ""}`);
      }
    }
    lines.push(``);
  }
  lines.push(`ИТОГО: людей ${s.totalPeople || 0} (детей ${s.childOrders || 0} + сотр. ${s.staffOrders || 0})`);
  lines.push(`ИТОГО: блюд ${s.totalItemsQty || 0}, сумма ${(s.totalAmount || 0).toFixed(2)} BYN`);
  lines.push(``);
  lines.push(`Ошибки: ${(data.errors || []).length ? data.errors.join("; ") : "нет"}`);
  lines.push(`Предупреждения: ${(data.warnings || []).length ? data.warnings.join("; ") : "нет"}`);
  const text = lines.join("\n");
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => setNotice("Проверка скопирована", "success")).catch(() => setNotice("Ошибка копирования", "error"));
  } else {
    setNotice("Буфер обмена недоступен", "error");
  }
}

function _renderFoodReportResult(el, data, startDate, endDate) {
  const totals = data.totals || {};
  const byDays = Array.isArray(data.byDays) ? data.byDays : [];
  const byLocs = Array.isArray(data.byLocations) ? data.byLocations : [];
  const byChildren = Array.isArray(data.byChildren) ? data.byChildren : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];

  const periodStr = (startDate && endDate) ? `${_fmtDate(startDate)} – ${_fmtDate(endDate)}` : "";

  const totalsHtml = `<div class="food-report-totals">
    <div class="food-report-total-main">${_fmtBYN(totals.totalCost)}</div>
    <div class="food-report-total-sub">расходы на питание</div>
    <div class="food-summary-stats" style="margin-top:10px">
      <div class="food-summary-stat"><div class="food-summary-stat-val">${totals.childrenCount || 0}</div><div class="food-summary-stat-lbl">Детей</div></div>
      <div class="food-summary-stat"><div class="food-summary-stat-val">${totals.submittedCount || 0}</div><div class="food-summary-stat-lbl">Заказов</div></div>
      <div class="food-summary-stat"><div class="food-summary-stat-val">${totals.skippedCount || 0}</div><div class="food-summary-stat-lbl">Без питания</div></div>
      <div class="food-summary-stat"><div class="food-summary-stat-val">${totals.missingCount || 0}</div><div class="food-summary-stat-lbl">Не выбрали</div></div>
    </div>
  </div>`;

  const daysHtml = byDays.length ? `
    <div class="food-summary-section" style="margin-top:14px">По дням</div>
    <table class="food-report-table">
      <thead><tr><th>Дата</th><th>Меню</th><th>Стоимость</th><th>Заказов</th></tr></thead>
      <tbody>${byDays.map(d => `<tr>
        <td>${escapeHtml(_fmtDate(d.date))}</td>
        <td>${escapeHtml(d.menuTitle || d.date)}</td>
        <td>${escapeHtml(_fmtBYN(d.cost))}</td>
        <td>${d.submittedCount}</td>
      </tr>`).join("")}</tbody>
    </table>` : `<div class="empty" style="margin-top:12px">Меню за период не найдено</div>`;

  const locsHtml = byLocs.length ? `
    <div class="food-summary-section" style="margin-top:14px">По адресам</div>
    <table class="food-report-table">
      <thead><tr><th>Код</th><th>Адрес</th><th>Детей</th><th>Стоимость</th></tr></thead>
      <tbody>${byLocs.map(loc => `<tr>
        <td>${escapeHtml(loc.groupCode)}</td>
        <td>${escapeHtml(loc.locationName || loc.groupCode)}</td>
        <td>${loc.childrenCount || 0}</td>
        <td>${escapeHtml(_fmtBYN(loc.cost))}</td>
      </tr>`).join("")}</tbody>
    </table>` : "";

  const childrenHtml = byChildren.length ? `
    <div class="food-summary-section" style="margin-top:14px">По детям</div>
    <table class="food-report-table">
      <thead><tr><th>Ребёнок</th><th>Адрес</th><th>Заказов</th><th>Без пит.</th><th>Стоимость</th></tr></thead>
      <tbody>${byChildren.map(c => `<tr>
        <td>${escapeHtml(c.childName)}</td>
        <td>${escapeHtml(c.locationName || c.groupCode)}</td>
        <td>${c.submittedCount}</td>
        <td>${c.skippedCount}</td>
        <td>${escapeHtml(_fmtBYN(c.totalCost))}</td>
      </tr>`).join("")}</tbody>
    </table>` : "";

  const warningsHtml = warnings.length ? `<div class="food-report-warning">${warnings.map(w => escapeHtml(w)).join("<br>")}</div>` : "";

  el.innerHTML = `
    ${periodStr ? `<div class="food-report-period">Период: ${escapeHtml(periodStr)}</div>` : ""}
    ${totalsHtml}
    ${daysHtml}
    ${locsHtml}
    ${childrenHtml}
    ${warningsHtml}
    <div class="food-menu-actions" style="margin-top:16px">
      <button class="secondary" id="frCopyBtn">Скопировать отчёт</button>
    </div>`;

  el.querySelector("#frCopyBtn")?.addEventListener("click", () => _copyFoodShiftReport(data, startDate, endDate));
}

function _copyFoodShiftReport(data, startDate, endDate) {
  const totals = data.totals || {};
  const byDays = Array.isArray(data.byDays) ? data.byDays : [];
  const byLocs = Array.isArray(data.byLocations) ? data.byLocations : [];
  const byChildren = Array.isArray(data.byChildren) ? data.byChildren : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];

  const periodStr = (startDate && endDate) ? `${_fmtDate(startDate)}–${_fmtDate(endDate)}` : "";
  const menus = Array.isArray(data.menus) ? data.menus : [];
  const weekTitle = menus.length ? (menus[0].title || _fmtDate(menus[0].date)) : "";

  const lines = [
    "Отчёт по питанию Yellow Club",
    weekTitle ? `Городская программа: ${weekTitle}` : "",
    periodStr ? `Период: ${periodStr}` : "",
    "",
    "ИТОГО:",
    `Расходы на питание: ${_fmtBYN(totals.totalCost)}`,
    `Отправленных заказов: ${totals.submittedCount || 0}`,
    `Без питания: ${totals.skippedCount || 0}`,
    `Не выбрали: ${totals.missingCount || 0}`,
  ].filter(l => l !== undefined);

  if (byDays.length) {
    lines.push("", "ПО ДНЯМ:");
    byDays.forEach(d => lines.push(`${_fmtDate(d.date)} — ${_fmtBYN(d.cost)}`));
  }

  if (byLocs.length) {
    lines.push("", "ПО АДРЕСАМ:");
    byLocs.forEach(loc => lines.push(`${loc.groupCode}, ${loc.locationName || loc.groupCode} — ${_fmtBYN(loc.cost)}`));
  }

  if (byChildren.length) {
    lines.push("", "ПО ДЕТЯМ:");
    byChildren.forEach(c => lines.push(`${c.childName}, ${c.locationName || c.groupCode} — ${_fmtBYN(c.totalCost)}`));
  }

  if (warnings.length) {
    lines.push("", "ВНИМАНИЕ:");
    warnings.forEach(w => lines.push(w));
  }

  const text = lines.join("\n");
  navigator.clipboard?.writeText(text).then(() => setNotice("Отчёт скопирован", "ok")).catch(() => setNotice("Не удалось скопировать", "error"));
}

const _FOOD_DAYS_OF_WEEK = new Set([
  "понедельник","вторник","среда","четверг","пятница","суббота","воскресенье",
]);
const _FOOD_CATEGORY_MAP = {
  "супы": "Супы", "суп": "Супы",
  "салат": "Салаты", "салаты": "Салаты",
  "второе": "Второе", "вторые": "Второе",
  "гарниры": "Гарниры", "гарнир": "Гарниры",
  "сладкое": "Сладкое", "десерты": "Сладкое", "десерт": "Сладкое",
  "фастфуд": "Второе",
  "напитки": "Напитки", "напиток": "Напитки",
  "другое": "Другое",
};

function _parsePrice(str) {
  const m = str.match(/(\d+[.,]\d+|\d+)\s*руб\.?/i);
  if (m) return parseFloat(m[1].replace(",", "."));
  return null;
}

function parseFoodLine(rawLine, currentCategory) {
  let line = rawLine.trim();
  if (!line) return null;

  // Extract and remove price
  let internalPrice = null;
  const priceMatch = line.match(/(\d+[.,]\d+|\d+)\s*руб\.?/i);
  if (priceMatch) {
    internalPrice = parseFloat(priceMatch[1].replace(",", "."));
    line = (line.slice(0, priceMatch.index) + line.slice(priceMatch.index + priceMatch[0].length)).trim();
  }

  if (!line) return null;

  let name = "", weight = null;

  // Separator-based split: "Борщ — 250 г"
  const sepMatch = line.match(/^(.+?)(?:\s*[—–]\s*|\s+-\s+|\s*\|\s*|\s{2,})(\S.*)$/);
  if (sepMatch) {
    name = sepMatch[1].trim();
    weight = sepMatch[2].replace(/\s*[—–\-|]\s*[\d.,]+.*$/, "").trim() || null;
  } else {
    // OCR no-separator: "Гуляш 75/100 гр." or "Сырники 150 гр. (2 шт.)"
    const wMatch = line.match(/^(.+?)\s+(\d[\d\s/.,]*(?:гр?\.?|мл\.?|л\.?|кг\.?)(?:\s*\([^)]*\))?)\s*$/i);
    if (wMatch) {
      name = wMatch[1].trim();
      weight = wMatch[2].replace(/\bгр\b\.?/gi, "г").trim();
    } else if (line.length >= 2 && !/^\d/.test(line)) {
      name = line;
    }
  }

  if (name) name = name.replace(/\d+[.,]\d+\s*руб\.?/gi, "").trim();
  if (weight) weight = weight.replace(/\d+[.,]\d+\s*руб\.?/gi, "").trim() || null;
  if (!name || name.length < 2) return null;

  return {
    category: normalizeFoodCategoryByName(name, currentCategory),
    name,
    weight: weight || null,
    internalPrice,
  };
}

function parseFoodMenuText(text) {
  const items = [];
  let currentCat = "Другое";
  for (const rawLine of text.split(/\r?\n/)) {
    const trimmed = rawLine.trim();
    if (!trimmed) continue;

    // Day-of-week filter
    const dayKey = trimmed.toLowerCase().replace(/[^а-яё]/gi, "");
    if (_FOOD_DAYS_OF_WEEK.has(dayKey)) continue;

    // Category header detection (strip trailing colon/punctuation from OCR)
    const catKey = trimmed.replace(/[:.]\s*$/, "").toLowerCase().trim();
    if (_FOOD_CATEGORY_MAP[catKey]) { currentCat = _FOOD_CATEGORY_MAP[catKey]; continue; }
    const catKeyClean = catKey.replace(/[^а-яёa-z]/gi, "");
    if (_FOOD_CATEGORY_MAP[catKeyClean]) { currentCat = _FOOD_CATEGORY_MAP[catKeyClean]; continue; }

    const parsed = parseFoodLine(trimmed, currentCat);
    if (parsed) items.push(parsed);
  }
  return items;
}

function _renderBulkEditablePreview(root, menuId, items) {
  const preview = root.querySelector("#fiBulkPreview");
  if (!preview) return;
  if (!items || !items.length) {
    preview.innerHTML = `<div class="food-debug-error">Блюда не найдены. Проверьте формат текста.</div>`;
    preview.style.display = "";
    return;
  }
  const catOptions = FOOD_CATEGORIES.map(c => `<option value="${escapeAttr(c)}">${escapeHtml(c)}</option>`).join("");
  const inStyle = "font-size:16px;border:1px solid var(--border,#ccc);border-radius:6px;padding:4px 8px;background:var(--card-bg,#fff);color:var(--color-text,#222)";
  const rowsHtml = items.map((it, idx) => `
    <div class="food-bulk-item-row" data-idx="${idx}">
      <select class="fiBulkCat" style="${inStyle};flex:0 0 auto;padding:4px 6px">
        ${FOOD_CATEGORIES.map(c => `<option value="${escapeAttr(c)}"${c === it.category ? " selected" : ""}>${escapeHtml(c)}</option>`).join("")}
      </select>
      <input type="text" class="fiBulkName" value="${escapeAttr(it.name)}" placeholder="Название" maxlength="200" style="${inStyle};flex:1 1 auto;min-width:0">
      <input type="text" class="fiBulkWeight" value="${escapeAttr(it.weight || "")}" placeholder="Вес" style="${inStyle};width:90px;flex:0 0 auto">
      <input type="text" class="fiBulkPrice" value="${it.internalPrice != null ? escapeAttr(String(it.internalPrice)) : ""}" placeholder="BYN" title="Стоимость для отчёта (BYN)" style="${inStyle};width:72px;flex:0 0 auto">
      <button class="secondary btn-sm fiBulkDel" style="flex:0 0 auto;padding:4px 8px;font-size:13px">✕</button>
    </div>`).join("");
  preview.innerHTML = `
    <div id="fiBulkCount" style="margin:0 0 8px;font-size:13px;color:var(--color-text-secondary)">Найдено блюд: <b>${items.length}</b></div>
    <div id="fiBulkItemsContainer">${rowsHtml}</div>
    <div style="font-size:12px;color:var(--color-text-secondary,#888);margin:6px 0">Перед добавлением можно изменить категорию, название, вес и стоимость для отчёта.</div>
    <div class="food-menu-actions" style="margin-top:10px">
      <button class="primary" id="fiBulkAddAllBtn">Добавить все блюда</button>
    </div>
    <div id="fiBulkAddStatus" style="font-size:13px;margin-top:6px"></div>`;
  preview.style.display = "";
  preview.querySelectorAll(".fiBulkDel").forEach(btn => {
    btn.addEventListener("click", () => {
      btn.closest(".food-bulk-item-row")?.remove();
      const remaining = preview.querySelectorAll(".food-bulk-item-row").length;
      const countEl = preview.querySelector("#fiBulkCount b");
      if (countEl) countEl.textContent = remaining;
    });
  });
  preview.querySelector("#fiBulkAddAllBtn")?.addEventListener("click", () => addFoodItemsBulk(root, menuId));
}

function _parseFoodBulkPreview(root, menuId) {
  const text = root.querySelector("#fiBulkText")?.value || "";
  if (!state.foodMenuDrafts[menuId]) state.foodMenuDrafts[menuId] = {};
  state.foodMenuDrafts[menuId].bulkText = text;
  const items = parseFoodMenuText(text);
  state.foodMenuDrafts[menuId].parsedItems = items;
  _renderBulkEditablePreview(root, menuId, items);
}

async function addFoodItemsBulk(root, menuId) {
  const btn = root.querySelector("#fiBulkAddAllBtn");
  const statusEl = root.querySelector("#fiBulkAddStatus");
  const items = [...root.querySelectorAll(".food-bulk-item-row")].map(row => {
    const priceRaw = (row.querySelector(".fiBulkPrice")?.value || "").replace(",", ".").replace(/руб\.?/gi, "").trim();
    return {
      category: row.querySelector(".fiBulkCat")?.value || "Другое",
      name: (row.querySelector(".fiBulkName")?.value || "").trim(),
      weight: (row.querySelector(".fiBulkWeight")?.value || "").trim() || null,
      price: parseFloat(priceRaw) || 0,
    };
  }).filter(it => it.name.length >= 1);
  if (!items.length) {
    if (statusEl) statusEl.textContent = "Нет блюд для добавления.";
    return;
  }
  if (btn) btn.disabled = true;
  let added = 0;
  for (const it of items) {
    try {
      const data = await apiPost(`/api/food/menus/${menuId}/items`, { category: it.category, name: it.name, weight: it.weight || null, price: it.price || 0 });
      if (data.ok) added++;
    } catch (_) { /* continue on error */ }
  }
  if (statusEl) statusEl.textContent = `Добавлено блюд: ${added}`;
  delete state.foodMenuDrafts[menuId];
  await openFoodMenu(root, menuId);
}

async function reviewPrepResult(fileId, decision) {
  const comment = decision === "rejected" ? (prompt("Почему отклонить результат? Что исправить преподавателю?") || "") : (prompt("Комментарий для преподавателя, если нужен:") || "");
  if (decision === "rejected" && !comment.trim()) {
    setNotice("Для отклонения нужно написать комментарий для преподавателя", "error");
    return;
  }
  try {
    await apiPost("/api/admin/prep-result-review", { fileId, decision, comment });
    setNotice(decision === "approved" ? "Результат подтверждён, преподавателю отправлена обратная связь" : "Результат отклонён, преподавателю отправлена обратная связь", "ok");
    await safeRefresh("prep-review", loadAdmin);
  } catch (e) { setNotice(safeUserError(e), "error"); }
}
// ---- v7.0.35: Navigation state protection ----

function captureNavigationState() {
  const activeTabEl = document.querySelector(".tab.active");
  const snapshot = {
    mainTab: activeTabEl?.dataset?.tab ?? null,
    adminTab: state.adminTab,
    foodMenuView: state.foodMenuView,
    foodMenuSummaryMenuId: state.foodMenuSummaryMenuId,
    foodMenuSelectedId: state.foodMenuSelected?.id ?? null,
    isEditingFoodOrder: state.isEditingFoodOrder,
    kitchenSelectedMenuId: state.kitchenSelectedMenuId,
    kitchenEditorSelectedId: state.kitchenEditorSelected?.id ?? null,
    internSection: state.internSection,
    internOpenStep: state.internOpenStep,
    selectedLessonId: state.selectedLesson?.id ?? null,
    clientTaskExpandedId: state.clientTaskExpandedId,
    scrollY: window.scrollY,
  };
  console.log("[nav] capture tab=" + snapshot.mainTab + " adminTab=" + snapshot.adminTab
    + " foodView=" + snapshot.foodMenuView + " editing=" + snapshot.isEditingFoodOrder
    + " scrollY=" + Math.round(snapshot.scrollY));
  return snapshot;
}

function restoreNavigationState(snapshot) {
  if (!snapshot) return;
  // Restore admin sub-tab if it drifted (defensive)
  if (snapshot.adminTab && state.adminTab !== snapshot.adminTab) {
    state.adminTab = snapshot.adminTab;
    document.querySelectorAll(".subtab").forEach(el =>
      el.classList.toggle("active", el.dataset.adminTab === state.adminTab)
    );
  }
  // Best-effort scroll restore after DOM settles
  if (snapshot.scrollY > 0) {
    requestAnimationFrame(() => window.scrollTo({ top: snapshot.scrollY }));
  }
  console.log("[nav] restore tab=" + snapshot.mainTab + " foodView=" + snapshot.foodMenuView
    + " editing=" + snapshot.isEditingFoodOrder + " scrollY=" + Math.round(snapshot.scrollY));
}

async function safeRefresh(reason, refreshFn) {
  const snapshot = captureNavigationState();
  console.log("[refresh] start reason=" + reason + " tab=" + snapshot.mainTab
    + " adminTab=" + snapshot.adminTab + " foodView=" + snapshot.foodMenuView);
  if (state.isEditingFoodOrder) {
    console.log("[refresh] skipped — food order form is open (reason=" + reason + ")");
    return;
  }
  try {
    await refreshFn();
    restoreNavigationState(snapshot);
    console.log("[refresh] done reason=" + reason);
  } catch (e) {
    console.error("[refresh] failed reason=" + reason, e);
    // Don't reset the screen on error — keep current state
  }
}

async function loadAdmin() {
  if (!canUseAdmin()) return;
  try {
    const data = await apiGet("/api/admin/overview");
    state.admin = data;
    renderAdmin();
  } catch (e) {
    console.error("[loadAdmin]", e);
  }
}
async function runScheduleCheck(notify) {
  try {
    setNotice("Проверяю МойКласс...", "");
    const data = await apiPost("/api/admin/schedule-check", { days: 30, notify });
    setNotice(`Проверка МойКласс: новых ${data.new?.length || 0}, изменённых ${data.changed?.length || 0}, задач ${data.tasks?.length || 0}, уведомлений ${data.sent || 0}`, "ok");
    await safeRefresh("schedule-check", loadAdmin); await loadTasks();
  } catch (e) { setNotice(safeUserError(e), "error"); }
}

async function reloadCabinetAfterRoleChange() {
  state.lessons = [];
  state.tasks = [];
  state.workSchedule = [];
  state.openSlots = [];
  state.clientTasks = [];
  state.reportsData = null;
  state.admin = null;
  state.adminKpiData = null;
  state.internTrack = null;
  state.internSection = null;
  state.internOpenStep = null;
  state.internAdminData = null;
  state.selectedLesson = null;
  state.lessonCache = {};
  state.lessonFetches = {};
  state.myChildren = null;
  state.activeMenus = null;
  state.myOrders = null;
  state.selectedChildId = null;
  state.foodOrderExpanded = {};
  state.foodMenuData = null;
  state.foodMenuSelected = null;
  state.foodMenuView = "list";
  state.foodMenuSummaryMenuId = null;
  state.isEditingFoodOrder = false;
  state.kitchenMenus = null;
  state.kitchenSelectedMenuId = null;
  state.kitchenSummaryData = null;
  state.kitchenCopyNotice = "";
  state.kitchenEditorData = null;
  state.kitchenEditorSelected = null;
  renderLessons();
  renderTasks();
  await loadMe();
  const _role = state.me?.role || "";
  if (_role === "kitchen" || _role === "restaurant") {
    await loadKitchenMenus();
    return;
  }
  await Promise.all([
    canUseLessons() ? loadLessons() : Promise.resolve(renderLessonsUnavailable()),
    canUseSchedule() ? loadWorkSchedule() : Promise.resolve(renderWorkScheduleUnavailable()),
    canUseOpenSlots() ? loadOpenSlots() : Promise.resolve(renderOpenSlotsUnavailable()),
    Promise.resolve(renderChildrenReport()),
    loadTasks(),
  ]);
  if (canUseAdmin()) await loadAdmin();
}

async function applyTestRole() {
  const role = $("testRoleSelect")?.value || "owner";
  const mkTeacherId = $("testTeacherSelect")?.value || "";
  try {
    setNotice("Переключаю тестовую роль...", "");
    await apiPost("/api/test-role", { role, mkTeacherId, enabled: true });
    await reloadCabinetAfterRoleChange();
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

async function clearTestRole() {
  try {
    setNotice("Сбрасываю тестовую роль...", "");
    await apiPost("/api/test-role", { enabled: false });
    await reloadCabinetAfterRoleChange();
  } catch (e) {
    setNotice(safeUserError(e), "error");
  }
}

function initTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });
  document.querySelectorAll(".subtab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".subtab").forEach(x => x.classList.remove("active"));
      tab.classList.add("active");
      state.adminTab = tab.dataset.adminTab;
      renderAdminContent();
    });
  });
}
function setupKeyboardDismiss() {
  const INTERACTIVE = 'input, textarea, select, button, a, label, [role="button"], [contenteditable]';
  document.addEventListener("touchstart", (e) => {
    const active = document.activeElement;
    if (!active) return;
    const tag = (active.tagName || "").toLowerCase();
    if (!["input", "textarea", "select"].includes(tag) && active.getAttribute("contenteditable") !== "true") return;
    if (e.target.closest(INTERACTIVE)) return;
    active.blur();
  }, { passive: true });
}

async function boot() {
  setupKeyboardDismiss();
  initTabs();
  $("refreshLessons").addEventListener("click", loadLessons);
  $("refreshTasks").addEventListener("click", loadTasks);
  $("refreshSchedule")?.addEventListener("click", loadWorkSchedule);
  $("refreshOpenSlots")?.addEventListener("click", loadOpenSlots);
  $("refreshReports")?.addEventListener("click", loadReports);
  $("refreshIntern")?.addEventListener("click", () => { state.internSection = null; state.internOpenStep = null; loadInternTrack(); });
  $("tab-intern")?.addEventListener("focusin", e => {
    if (e.target.matches("input,textarea,select")) setChatInputFocused(true);
  });
  $("tab-intern")?.addEventListener("focusout", e => {
    if (e.target.matches("input,textarea,select")) window.setTimeout(() => setChatInputFocused(false), 120);
  });
  $("loadReports")?.addEventListener("click", loadReports);
  $("reportsMonth")?.addEventListener("change", () => { state.reportsMonth = $("reportsMonth")?.value || ""; });
  $("copyReportsText")?.addEventListener("click", copyReportsText);
  $("loadChildrenReport")?.addEventListener("click", loadChildrenReport);
  $("childrenReportMonth")?.addEventListener("change", () => { state.childrenReportMonth = $("childrenReportMonth")?.value || ""; });
  $("goToReportsFromAdmin")?.addEventListener("click", () => activateTab("reports"));
  $("syncTasksFromReports")?.addEventListener("click", () => syncTasksFromReports("all"));
  $("syncPaymentTasksFromReports")?.addEventListener("click", () => syncTasksFromReports("payment"));
  $("syncMakeupTasksFromReports")?.addEventListener("click", () => syncTasksFromReports("makeup"));
  $("askReportsAgent")?.addEventListener("click", askReportsAgent);
  $("workScheduleForm")?.addEventListener("submit", saveWorkSlot);
  $("clearWorkSlot")?.addEventListener("click", clearWorkSlotForm);
  document.querySelectorAll("[data-work-week]").forEach(btn => btn.addEventListener("click", () => setWorkWeek(btn.dataset.workWeek)));
  document.querySelectorAll("[data-open-slots-week]").forEach(btn => btn.addEventListener("click", () => setOpenSlotsWeek(btn.dataset.openSlotsWeek)));
  $("openSlotsLocationFilter")?.addEventListener("change", updateOpenSlotsFilters);
  $("openSlotsTimeFilter")?.addEventListener("change", updateOpenSlotsFilters);
  $("refreshAdmin").addEventListener("click", loadAdmin);
  $("runScheduleCheck").addEventListener("click", () => runScheduleCheck(false));
  $("runScheduleCheckNotify").addEventListener("click", () => runScheduleCheck(true));
  $("closeLesson").addEventListener("click", closeLessonModal);
  $("applyTestRole")?.addEventListener("click", applyTestRole);
  $("clearTestRole")?.addEventListener("click", clearTestRole);
  $("askForm")?.addEventListener("submit", sendAskQuestion);
  $("askInput")?.addEventListener("input", autoResizeChatInput);
  $("askInput")?.addEventListener("focus", () => setChatInputFocused(true));
  $("askInput")?.addEventListener("blur", () => window.setTimeout(() => setChatInputFocused(false), 120));
  setChatSubmitBusy($("askSubmit"), false);
  $("askInput")?.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") sendAskQuestion(event);
  });
  autoResizeChatInput();
  $("chatQuickGrid")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-chat-prompt]");
    if (btn) sendQuickChatPrompt(btn.dataset.chatPrompt);
  });
  try {
    await loadMe();
    const role = state.me?.role || "";
    if (role === "kitchen" || role === "restaurant") {
      await loadKitchenMenus();
      return;
    }
    clearWorkSlotForm();
    await Promise.all([
      canUseLessons() ? loadLessons() : Promise.resolve(renderLessonsUnavailable()),
      canUseSchedule() ? loadWorkSchedule() : Promise.resolve(renderWorkScheduleUnavailable()),
      canUseOpenSlots() ? loadOpenSlots() : Promise.resolve(renderOpenSlotsUnavailable()),
      Promise.resolve(renderChildrenReport()),
      loadTasks(),
    ]);
    if (canUseAdmin()) await loadAdmin();
  } catch (e) { console.error("[boot]", e); setNotice("Не удалось загрузить данные. Обновите страницу.", "error"); }
}
boot();

// ── Payment Intents v7.0.77 ───────────────────────────────────────────────

const PI_PURPOSE_LABELS = {
  current_month:        "Текущий месяц",
  previous_month_debt:  "Долг прошлого периода",
  old_debt:             "Старый долг",
  advance:              "Аванс",
  city_program:         "Городская программа",
  other:                "Другое",
};

const PI_METHOD_LABELS = { erip: "ЕРИП", acquiring: "Эквайринг", manual: "Ручной" };

const PI_STATUS_LABELS = {
  draft:                  { label: "Черновик",              cls: "chip-pi-draft" },
  ready:                  { label: "Готов",                  cls: "chip-pi-ready" },
  bepaid_creating:        { label: "Создаётся...",           cls: "chip-pi-creating" },
  bepaid_created:         { label: "Ожидает оплаты",        cls: "chip-pi-bepaid" },
  bepaid_requires_check:  { label: "Требует проверки",       cls: "chip-pi-requires-check" },
  paid:                   { label: "Оплачено bePaid",        cls: "chip-pi-paid" },
  posted_to_moyklass:     { label: "Внесено в МойКласс",    cls: "chip-pi-posted" },
  cancelled:              { label: "Отменён",                cls: "chip-pi-cancel" },
  error:                  { label: "Ошибка",                 cls: "chip-pi-error" },
};

const PI_PURPOSE_CLS = {
  previous_month_debt: "chip-purpose-debt",
  old_debt: "chip-purpose-debt",
  advance: "chip-purpose-adv",
  city_program: "chip-purpose-city",
};

let _piCancelTarget = null; // public_id being cancelled

function canUsePaymentIntents() {
  const r = state.me?.role || "";
  return ["owner", "admin", "director", "operations", "client_manager"].includes(r);
}

async function loadPaymentIntents() {
  if (!canUsePaymentIntents()) {
    const listEl = $("piList");
    if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--red)">Нет доступа (роль: ${escapeHtml(state.me?.role || "(не загружена)")})</div>`;
    return;
  }
  const month = $("piMonthFilter")?.value || "";
  const status = $("piStatusFilter")?.value || "all";
  const params = new URLSearchParams();
  if (month) params.set("month", month);
  if (status !== "all") params.set("status", status);
  const qs = params.toString();
  const listEl = $("piList");
  const statsEl = $("piStats");
  const debugEl = $("piDebug");
  const refreshBtn = $("loadPaymentIntents");
  if (refreshBtn) { refreshBtn.disabled = true; refreshBtn.textContent = "Загрузка..."; }
  if (listEl) listEl.innerHTML = `<div class="pi-empty">Загрузка...</div>`;
  if (debugEl) debugEl.textContent = "";
  try {
    const data = await apiGet("/api/payments/intents" + (qs ? "?" + qs : ""));
    renderPaymentIntentStats(statsEl, data.stats || {});
    const intents = data.intents || data.items || [];
    renderPaymentIntentList(listEl, intents, { month, status });
    if (refreshBtn) refreshBtn.textContent = `Обновить (${intents.length})`;
    // Debug line visible to admins
    if (debugEl) {
      const d = data.debug || {};
      debugEl.textContent = `Загружено: ${intents.length} · month=${d.applied_month || month || "all"} · status=${d.applied_status || status}`;
    }
  } catch (e) {
    if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--red)">Ошибка загрузки: ${escapeHtml(String(e))}</div>`;
    if (refreshBtn) refreshBtn.textContent = "Обновить";
    if (debugEl) debugEl.textContent = `Ошибка: ${String(e)}`;
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function renderPaymentIntentStats(el, stats) {
  if (!el) return;
  const chips = [
    { key: "total",                 label: "Всего" },
    { key: "draft",                 label: "Черновик" },
    { key: "ready",                 label: "Готово" },
    { key: "bepaid_creating",       label: "Создаётся" },
    { key: "bepaid_created",        label: "Ожидает оплаты" },
    { key: "bepaid_requires_check", label: "Проверка" },
    { key: "paid",                  label: "Оплачено bePaid" },
    { key: "posted_to_moyklass",    label: "В МойКласс" },
    { key: "cancelled",             label: "Отменено" },
    { key: "error",                 label: "Ошибка" },
  ];
  el.innerHTML = chips.map(c => {
    const v = stats[c.key] ?? 0;
    return `<span class="pi-stat-chip${v > 0 ? " has-value" : ""}">${escapeHtml(c.label)}: ${v}</span>`;
  }).join("");
}

function renderPaymentIntentList(el, intents, filters = {}) {
  if (!el) return;
  if (!intents.length) {
    const parts = [];
    if (filters.month) parts.push(`за ${filters.month}`);
    if (filters.status && filters.status !== "all") parts.push(`со статусом «${filters.status}»`);
    const hint = parts.length ? ` ${parts.join(", ")}` : "";
    el.innerHTML = `<div class="pi-empty">Черновики${hint} не найдены.</div>`;
    return;
  }
  el.innerHTML = intents.map(pi => {
    try {
      return renderPaymentIntentCard(pi);
    } catch (err) {
      const pid = (pi && pi.public_id) ? escapeHtml(pi.public_id) : "?";
      return `<div class="pi-card pi-card-error"><div class="pi-card-id">${pid}</div><div style="color:var(--red);font-size:12px">Ошибка отрисовки: ${escapeHtml(String(err))}</div></div>`;
    }
  }).join("");
}

function renderPaymentIntentCard(pi) {
  const st = PI_STATUS_LABELS[pi.status] || { label: pi.status || "unknown", cls: "chip-pi-draft" };
  const purposeLabel = PI_PURPOSE_LABELS[pi.purpose] || pi.purpose || "—";
  const methodLabel = PI_METHOD_LABELS[pi.payment_method] || pi.payment_method || "—";
  const purposeCls = PI_PURPOSE_CLS[pi.purpose] || "";
  const amountVal = paymentIntentAmountByn(pi);   // uses amount_byn first, then amount_minor/100
  const amount = fmtByn(amountVal);
  const name = pi.student_name ? escapeHtml(pi.student_name) : `userId=${pi.mk_user_id}`;
  const period = pi.period_month ? `<span class="chip chip-info" style="font-size:10px">${escapeHtml(pi.period_month)}</span>` : "";
  const method = `<span class="chip" style="font-size:10px">${escapeHtml(methodLabel)}</span>`;
  const purposeChip = `<span class="chip ${purposeCls}" style="font-size:10px">${escapeHtml(purposeLabel)}</span>`;
  const statusChip = `<span class="chip ${st.cls}" style="font-size:10px">${escapeHtml(st.label)}</span>`;
  const comment = pi.comment ? `<div class="pi-card-comment">${escapeHtml(pi.comment)}</div>` : "";
  const createdBy = pi.created_by_name ? `<span style="font-size:10px;color:var(--muted)">создал: ${escapeHtml(pi.created_by_name)}</span>` : "";
  const createdAt = pi.created_at ? `<span style="font-size:10px;color:var(--muted)">${escapeHtml(String(pi.created_at).slice(0,10))}</span>` : "";

  const canCancel = ["draft", "ready"].includes(pi.status);
  const cancelSafeName = escapeHtml(String(pi.student_name || pi.mk_user_id || "?"));
  const cancelBtn = canCancel
    ? `<button class="secondary" style="font-size:12px;padding:4px 10px" onclick="openCancelIntent('${escapeHtml(pi.public_id)}','${cancelSafeName}',${amountVal})">Отменить</button>`
    : "";

  const canCreateBePaid = pi.payment_method === "erip"
    && ["draft", "ready"].includes(pi.status)
    && !pi.bepaid_uid
    && !["bepaid_creating", "bepaid_requires_check"].includes(pi.status)
    && canUsePaymentIntents();
  const bePaidBtn = canCreateBePaid
    ? `<button class="primary" style="font-size:12px;padding:4px 10px" onclick="openBePaidConfirm('${escapeHtml(pi.public_id)}','${cancelSafeName}',${amountVal})">Выставить счёт bePaid</button>`
    : "";

  const bePaidCreatingBlock = pi.status === "bepaid_creating"
    ? `<div class="pi-bepaid-creating">⏳ Счёт bePaid создаётся... Обновите страницу через несколько секунд.</div>`
    : "";

  const bePaidRequiresCheckBlock = pi.status === "bepaid_requires_check"
    ? `<div class="pi-bepaid-requires-check">
        <strong>Требуется проверка в bePaid</strong>
        <div style="font-size:11px;margin-top:4px">Статус счёта неизвестен. Проверьте операцию вручную в личном кабинете bePaid до создания нового счёта.</div>
        ${pi.bepaid_order_id ? `<div style="font-size:10px;color:var(--muted);margin-top:2px">order_id: ${escapeHtml(pi.bepaid_order_id)}</div>` : ""}
        ${pi.bepaid_tracking_id ? `<div style="font-size:10px;color:var(--muted)">tracking_id: ${escapeHtml(pi.bepaid_tracking_id)}</div>` : ""}
        ${pi.bepaid_account_number ? `<div style="font-size:10px;color:var(--muted)">account_number: ${escapeHtml(pi.bepaid_account_number)}</div>` : ""}
       </div>`
    : "";

  const bePaidInfo = pi.bepaid_uid
    ? `<div class="pi-bepaid-info">
        <span>Счёт ERIP: <strong>${escapeHtml(pi.bepaid_account_number || "—")}</strong></span>
        <span style="color:var(--muted);font-size:10px">UID: ${escapeHtml(pi.bepaid_uid)}</span>
       </div>`
    : "";

  const cancelInfo = pi.status === "cancelled" && pi.cancel_reason
    ? `<div style="font-size:11px;color:var(--muted);margin-top:4px">Причина: ${escapeHtml(pi.cancel_reason)}</div>`
    : "";

  const bePaidPaidBlock = (pi.status === "paid" || pi.status === "posted_to_moyklass")
    ? `<div class="pi-bepaid-paid">
        <strong>Оплачено в bePaid</strong>
        ${pi.paid_at ? `<div style="font-size:11px;margin-top:4px">Дата: ${escapeHtml(String(pi.paid_at).slice(0, 19))}</div>` : ""}
        ${(pi.paid_amount_byn != null) ? `<div style="font-size:11px">Сумма: ${fmtByn(pi.paid_amount_byn)}</div>` : ""}
        ${pi.paid_transaction_uid ? `<div style="font-size:10px;color:var(--muted);margin-top:2px">UID: ${escapeHtml(pi.paid_transaction_uid)}</div>` : ""}
        ${pi.status === "paid" ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">В МойКласс ещё не внесено</div>` : ""}
       </div>`
    : "";

  const mkPostedBlock = pi.status === "posted_to_moyklass"
    ? `<div class="pi-mk-posted">
        <strong>Внесено в МойКласс</strong>
        ${pi.mk_payment_id ? `<div style="font-size:11px;margin-top:4px">MK payment ID: <strong>${escapeHtml(String(pi.mk_payment_id))}</strong></div>` : ""}
        ${pi.mk_posted_at ? `<div style="font-size:11px">Дата внесения: ${escapeHtml(String(pi.mk_posted_at).slice(0,10))}</div>` : ""}
        ${pi.mk_invoice_id ? `<div style="font-size:10px;color:var(--muted)">Счёт МК: ${escapeHtml(String(pi.mk_invoice_id))}</div>` : ""}
       </div>`
    : "";

  const canMkPost = pi.status === "paid" && canPostToMoyklass();
  const mkPostBtn = canMkPost
    ? `<button class="primary" style="font-size:12px;padding:4px 10px" onclick="openMkPostModal('${escapeHtml(pi.public_id)}','${cancelSafeName}',${amountVal})">Внести в МойКласс</button>`
    : "";

  const extraCls = pi.status === "cancelled" ? " pi-card-cancelled"
    : pi.status === "posted_to_moyklass" ? " pi-card-posted"
    : pi.status === "paid" ? " pi-card-paid"
    : pi.status === "error" ? " pi-card-error" : "";

  const sourceBadge = pi.source === "moyklass_invoice"
    ? `<span class="pi-source-badge pi-source-badge-mk">Данные проверены в МойКласс</span>`
    : `<span class="pi-source-badge pi-source-badge-manual">Ручной ввод</span>`;

  const safeId = pi.public_id ? paymentIntentDomId(pi.public_id) : "";
  return `<div class="pi-card${extraCls}"${safeId ? ` id="${escapeHtml(safeId)}" data-intent-public-id="${escapeHtml(pi.public_id)}"` : ""}>
    <div class="pi-card-head">
      <div class="pi-card-name">${name}</div>
      <div class="pi-card-amount">${amount}</div>
    </div>
    <div class="pi-card-meta">
      ${statusChip}${purposeChip}${period}${method}
    </div>
    ${sourceBadge}
    ${comment}${cancelInfo}${bePaidCreatingBlock}${bePaidRequiresCheckBlock}${bePaidInfo}${bePaidPaidBlock}${mkPostedBlock}
    <div class="pi-card-footer">${bePaidBtn}${mkPostBtn}${cancelBtn}</div>
    <div class="pi-card-id">${escapeHtml(pi.public_id)} · mk_user_id: ${pi.mk_user_id} · ${createdAt} ${createdBy}</div>
  </div>`;
}

// ── iOS-safe scroll lock (v7.0.85) ───────────────────────────────────────
let piLockedScrollY = 0;
let piOpenModalCount = 0;

function piLockPageScroll() {
  piOpenModalCount += 1;
  if (piOpenModalCount > 1) return;
  piLockedScrollY = window.scrollY || window.pageYOffset || 0;
  document.documentElement.classList.add("pi-modal-open");
  document.body.classList.add("pi-modal-open");
  document.body.style.position = "fixed";
  document.body.style.top = `-${piLockedScrollY}px`;
  document.body.style.left = "0";
  document.body.style.right = "0";
  document.body.style.width = "100%";
  document.body.style.overflow = "hidden";
}

function piUnlockPageScroll() {
  if (piOpenModalCount > 0) piOpenModalCount -= 1;
  if (piOpenModalCount > 0) return;
  document.documentElement.classList.remove("pi-modal-open");
  document.body.classList.remove("pi-modal-open");
  document.body.style.position = "";
  document.body.style.top = "";
  document.body.style.left = "";
  document.body.style.right = "";
  document.body.style.width = "";
  document.body.style.overflow = "";
  window.scrollTo(0, piLockedScrollY);
}

// ── modal helpers (animated open / close) ────────────────────────────────

function piModalOpen(el) {
  if (!el) return;
  if (el.dataset.piOpen === "1") return;
  // Ensure modal lives in #piModalRoot (direct child of body) to fix iOS viewport bug
  const root = document.getElementById("piModalRoot") || document.body;
  if (el.parentElement !== root) root.appendChild(el);
  el.dataset.piOpen = "1";
  el.classList.remove("hidden", "pi-closing");
  piLockPageScroll();
}

function piModalClose(el, cb) {
  if (!el) { if (cb) cb(); return; }
  if (el.dataset.piOpen !== "1") { if (cb) cb(); return; }
  el.dataset.piOpen = "0";
  el.classList.add("pi-closing");
  const mobile = !window.matchMedia("(min-width:600px)").matches;
  const dur = mobile ? 225 : 165;
  const sheet = el.querySelector(".pi-modal-sheet");
  let done = false;
  const onEnd = () => {
    if (done) return; done = true;
    if (sheet) sheet.removeEventListener("animationend", onEnd);
    el.classList.add("hidden");
    el.classList.remove("pi-closing");
    piUnlockPageScroll();
    if (cb) cb();
  };
  if (sheet) sheet.addEventListener("animationend", onEnd);
  setTimeout(onEnd, dur + 55);
}

// ── Create intent modal ───────────────────────────────────────────────────

function openCreateIntentModal(prefill) {
  const modal = $("piCreateModal");
  if (!modal) return;
  // Always reset all fields to avoid stale values from previous open
  $("piUserId").value = prefill?.mk_user_id || "";
  $("piStudentName").value = prefill?.student_name || "";
  $("piAmount").value = prefill?.amount_byn != null ? prefill.amount_byn : "";
  $("piPurpose").value = prefill?.purpose || "current_month";
  // Default period_month: use current filter month if set, else current month
  const filterMonth = $("piMonthFilter")?.value || "";
  $("piPeriodMonth").value = prefill?.period_month || filterMonth || currentMonthValue();
  syncMonthPicker($("piPeriodMonth"));
  $("piPaymentMethod").value = prefill?.payment_method || "erip";
  $("piComment").value = prefill?.comment || "";
  $("piCreateError").classList.add("hidden");
  $("piCreateError").textContent = "";
  $("piCreateWarning").classList.add("hidden");
  $("piCreateWarning").textContent = "";
  modal._prefillContext = prefill || null;
  piModalOpen(modal);
}

function closeCreateIntentModal() {
  piModalClose($("piCreateModal"));
}

// Called from bePaid card button
window.openCreateIntentFromBepaid = function(prefill) {
  const comment = prefill.transaction_uid
    ? `Создано из bePaid transaction ${prefill.transaction_uid}`
    : "";
  openCreateIntentModal({ ...prefill, comment });
  // Open the accordion if not already open
  const acc = $("paymentIntentsAccordion");
  if (acc && !acc.open) acc.open = true;
  // Scroll to modal (it's fixed, so just remove hidden)
};

async function submitCreateIntent() {
  const errEl = $("piCreateError");
  const warnEl = $("piCreateWarning");
  errEl.classList.add("hidden");
  warnEl.classList.add("hidden");

  const mk_user_id = parseInt($("piUserId").value);
  const student_name = $("piStudentName").value.trim();
  const amount_byn = parseFloat($("piAmount").value);
  const purpose = $("piPurpose").value;
  const period_month = $("piPeriodMonth").value;
  const payment_method = $("piPaymentMethod").value;
  const comment = $("piComment").value.trim();

  if (!mk_user_id || mk_user_id <= 0) {
    errEl.textContent = "Укажите МойКласс userId (числовой ID ученика).";
    errEl.classList.remove("hidden");
    return;
  }
  if (!amount_byn || amount_byn <= 0) {
    errEl.textContent = "Укажите сумму больше нуля.";
    errEl.classList.remove("hidden");
    return;
  }

  const submitBtn = $("piModalSubmit");
  submitBtn.disabled = true;
  submitBtn.textContent = "Создаю...";

  try {
    const ctx = $("piCreateModal")._prefillContext;
    const body = {
      mk_user_id, student_name, amount_byn, purpose,
      period_month: period_month || undefined,
      payment_method, comment: comment || undefined,
      raw_context: ctx ? { source: "bepaid_card", ...ctx } : undefined,
    };
    const data = await apiPost("/api/payments/intents", body);
    if (!data.ok) {
      errEl.textContent = data.error || "Ошибка создания черновика.";
      errEl.classList.remove("hidden");
      return;
    }
    // Show any warnings before closing the modal
    const allWarnings = [
      data.student_name_mismatch,
      data.duplicate_warning,
      ...(data.warnings || []),
    ].filter(Boolean);
    if (allWarnings.length) {
      warnEl.textContent = allWarnings.join(" | ");
      warnEl.classList.remove("hidden");
      // Keep modal open briefly so user sees the warning
      await new Promise(r => setTimeout(r, 2200));
    }
    // Success — sync month filter so new intent is visible in the list
    if (data.intent?.period_month) {
      const mf = $("piMonthFilter");
      if (mf) mf.value = data.intent.period_month;
    }
    closeCreateIntentModal();
    showToast(data.message || "Черновик создан.");
    // Ensure accordion is open so user sees the new intent
    const acc = $("paymentIntentsAccordion");
    if (acc && !acc.open) acc.open = true;
    await loadPaymentIntents();
  } catch (e) {
    errEl.textContent = String(e);
    errEl.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Создать черновик";
  }
}

// ── Cancel intent modal ───────────────────────────────────────────────────

function openCancelIntent(publicId, nameOrId, amountByn) {
  _piCancelTarget = publicId;
  const info = $("piCancelInfo");
  if (info) info.textContent = `Отменить черновик ${publicId} (${nameOrId}, ${fmtByn(amountByn)})?`;
  $("piCancelReason").value = "";
  $("piCancelError").classList.add("hidden");
  piModalOpen($("piCancelModal"));
}

function closeCancelIntentModal() {
  piModalClose($("piCancelModal"), () => { _piCancelTarget = null; });
}

async function confirmCancelIntent() {
  if (!_piCancelTarget) return;
  const errEl = $("piCancelError");
  errEl.classList.add("hidden");
  const reason = $("piCancelReason").value.trim() || "Отменён пользователем";
  const btn = $("piCancelModalConfirm");
  btn.disabled = true;
  btn.textContent = "Отменяю...";
  try {
    const data = await apiPost(`/api/payments/intents/${_piCancelTarget}/cancel`, { reason });
    if (!data.ok) {
      errEl.textContent = data.error || "Ошибка отмены.";
      errEl.classList.remove("hidden");
      return;
    }
    closeCancelIntentModal();
    showToast("Черновик отменён.");
    await loadPaymentIntents();
  } catch (e) {
    errEl.textContent = String(e);
    errEl.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Отменить черновик";
  }
}

// ── bePaid ERIP confirm modal ─────────────────────────────────────────────

let _piBePaidTarget = null;

window.openBePaidConfirm = function(publicId, nameOrId, amountByn) {
  _piBePaidTarget = publicId;
  const info = $("piBePaidInfo");
  if (info) info.textContent = `Выставить счёт bePaid ERIP для ${nameOrId} на ${fmtByn(amountByn)}?`;
  const resultEl = $("piBePaidResult");
  if (resultEl) { resultEl.classList.add("hidden"); resultEl.textContent = ""; }
  $("piBePaidError")?.classList.add("hidden");
  const btn = $("piBePaidModalConfirm");
  if (btn) { btn.disabled = false; btn.textContent = "Выставить счёт"; }
  piModalOpen($("piBePaidModal"));
};

function closeBePaidModal() {
  piModalClose($("piBePaidModal"), () => { _piBePaidTarget = null; });
}

async function confirmCreateBePaid() {
  if (!_piBePaidTarget) return;
  const errEl = $("piBePaidError");
  const resultEl = $("piBePaidResult");
  errEl.classList.add("hidden");
  resultEl.classList.add("hidden");
  const btn = $("piBePaidModalConfirm");
  btn.disabled = true;
  btn.textContent = "Выставляю...";
  try {
    const data = await apiPost(`/api/payments/intents/${_piBePaidTarget}/create-bepaid`, {});
    if (!data.ok) {
      if (data.requires_check) {
        errEl.textContent = data.error || "Таймаут bePaid. Проверьте вручную в личном кабинете.";
      } else {
        errEl.textContent = data.error || "Ошибка выставления счёта.";
      }
      errEl.classList.remove("hidden");
      return;
    }
    if (data.already_exists) {
      resultEl.textContent = data.message || "Счёт уже выставлен.";
      resultEl.classList.remove("hidden");
    } else {
      resultEl.textContent = data.message || "Счёт bePaid выставлен.";
      resultEl.classList.remove("hidden");
      showToast(data.message || "Счёт bePaid ERIP выставлен.");
    }
    await loadPaymentIntents();
    setTimeout(() => closeBePaidModal(), 2000);
  } catch (e) {
    errEl.textContent = String(e);
    errEl.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Выставить счёт";
  }
}

// ── Toast helper ─────────────────────────────────────────────────────────

function showToast(msg) {
  let t = document.getElementById("piToast");
  if (!t) {
    t = document.createElement("div");
    t.id = "piToast";
    t.className = "pi-toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  clearTimeout(t._timeout);
  // Use rAF so browser processes the initial opacity:0 state before adding visible class
  requestAnimationFrame(() => {
    t.classList.add("pi-toast-visible");
    t._timeout = setTimeout(() => { t.classList.remove("pi-toast-visible"); }, 3000);
  });
}

// ── MoyKlass Payment Type Discovery (v7.0.92.1) ──────────────────────────

let _mkPaymentTypesData = null;

async function loadMkPaymentTypes() {
  const section = $("mkPaymentTypeSection");
  const statusEl = $("mkPaymentTypeStatus");
  const listEl = $("mkPaymentTypeList");
  const refreshBtn = $("refreshMkPaymentTypes");

  if (!section) return;

  if (!canPostToMoyklass()) {
    section.style.display = "none";
    return;
  }

  section.style.display = "";

  // Double-click guard: bail out if a request is already in flight
  if (refreshBtn?.disabled) return;

  if (statusEl) statusEl.textContent = "Загрузка…";
  if (listEl) listEl.innerHTML = "";
  if (refreshBtn) refreshBtn.disabled = true;

  try {
    // apiGet (line ~568) fetches, parses JSON, and throws if data.ok===false
    const data = await apiGet("/api/payments/moyklass/payment-types");
    _mkPaymentTypesData = data;
    renderMkPaymentTypes(data);
  } catch (err) {
    console.error("MoyKlass payment types load failed", {
      name: err?.name || "",
      message: err?.message || String(err),
    });
    if (statusEl) {
      statusEl.textContent = "Ошибка загрузки: " + (err?.message || String(err));
    }
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function _renderPaymentTypeBlock(label, channelData, candidates, envKey) {
  const pt = (channelData && channelData.payment_type) || {};
  const configuredId = channelData && channelData.configured_payment_type_id;
  const found = channelData && channelData.configured_payment_type_found;
  const valid = pt.valid;
  let html = `<div class="mk-pt-channel-block"><div class="mk-pt-channel-label">${escapeHtml(label)}</div>`;
  if (!configuredId) {
    html += `<div class="mk-pt-warn">${escapeHtml(envKey)} не задан.</div>`;
  } else if (!found) {
    html += `<div class="mk-pt-warn">ID ${configuredId} не найден в МойКласс.</div>`;
  } else if (!valid) {
    const reason = (pt.blocking_reasons || []).join(", ") || "тип недоступен";
    html += `<div class="mk-pt-warn">ID ${configuredId} (${escapeHtml(pt.payment_type_name || "")}) — ${escapeHtml(reason)}</div>`;
  } else {
    html += `<div class="mk-pt-ok">ID ${configuredId} — <strong>${escapeHtml(pt.payment_type_name || "")}</strong></div>`;
  }
  if (candidates && candidates.length === 1 && candidates[0].id !== configuredId) {
    html += `<div class="mk-pt-hint"><code class="mk-pt-env-hint">${escapeHtml(envKey)}=${candidates[0].id}</code> (${escapeHtml(candidates[0].name)})</div>`;
  } else if (candidates && candidates.length > 1) {
    html += `<div class="mk-pt-hint">Кандидаты: ` + candidates.map(c => `ID ${c.id} — ${escapeHtml(c.name)}`).join(", ") + `</div>`;
  }
  html += `</div>`;
  return html;
}

function renderMkPaymentTypes(data) {
  const statusEl = $("mkPaymentTypeStatus");
  const listEl = $("mkPaymentTypeList");
  if (!statusEl || !listEl) return;

  if (!data.ok) {
    statusEl.innerHTML = `<span class="mk-pt-error">Ошибка загрузки: ${escapeHtml(data.error || "")}</span>`;
    return;
  }

  const diag = data.diagnostics || {};
  const erip = data.erip || {};
  const acquiring = data.acquiring || {};

  let statusHtml = `<div class="mk-pt-dual-channels">`;
  statusHtml += _renderPaymentTypeBlock("bePaid ЕРИП", erip, diag.erip_candidates || [], "MOYKLASS_ERIP_PAYMENT_TYPE_ID");
  statusHtml += _renderPaymentTypeBlock("bePaid Эквайринг", acquiring, diag.acquiring_candidates || [], "MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID");
  statusHtml += `</div>`;
  statusEl.innerHTML = statusHtml;

  const items = data.items || [];
  const eripCandidates = diag.erip_candidates || [];
  const acqCandidates = diag.acquiring_candidates || [];
  const erip_id = erip.configured_payment_type_id;
  const acq_id = acquiring.configured_payment_type_id;

  let html = `<div class="mk-pt-diag">Всего типов: ${diag.total || 0} · Активных: ${diag.active || 0} · ЕРИП-кандидатов: ${diag.possible_erip_matches || 0} · Эквайринг-кандидатов: ${diag.possible_acquiring_matches || 0}</div>`;

  if (items.length) {
    html += `<div class="mk-pt-list-title">Все типы оплаты:</div><ul class="mk-pt-list">`;
    for (const item of items) {
      const isErip = item.id === erip_id;
      const isAcq = item.id === acq_id;
      const isEripCand = eripCandidates.some(c => c.id === item.id);
      const isAcqCand = acqCandidates.some(c => c.id === item.id);
      const badges = (isErip ? ` <span class="mk-pt-badge-active">ЕРИП</span>` : isEripCand ? ` <span class="mk-pt-badge-likely">ЕРИП?</span>` : "") +
        (isAcq ? ` <span class="mk-pt-badge-acq">Эквайринг</span>` : isAcqCand ? ` <span class="mk-pt-badge-likely">Эквайринг?</span>` : "");
      const cls = isErip || isAcq ? " mk-pt-item-configured" : "";
      html += `<li class="mk-pt-item${cls}"><span class="mk-pt-id">ID ${item.id}</span> ${escapeHtml(item.name)}${badges}</li>`;
    }
    html += `</ul>`;
  }
  listEl.innerHTML = html;
}

// ── MoyKlass Manual Payment Posting (v7.0.92) ────────────────────────────

function canPostToMoyklass() {
  const r = state.me?.role || "";
  return ["owner", "admin"].includes(r);
}

let _piMkPostTarget = null;  // { publicId, name, amountByn }
let _piMkPostFingerprint = "";
let _piMkPostPending = false;

window.openMkPostModal = function(publicId, name, amountByn) {
  if (!canPostToMoyklass()) return;
  _piMkPostTarget = { publicId, name, amountByn };
  _piMkPostFingerprint = "";
  _piMkPostPending = false;

  const readinessPanel = $("piMkPostReadinessPanel");
  const confirmPanel = $("piMkPostConfirmPanel");
  const successPanel = $("piMkPostSuccessPanel");
  const errEl = $("piMkPostError");
  const readinessBtn = $("piMkPostReadinessBtn");
  const confirmBtn = $("piMkPostConfirmBtn");
  const reconcileBtn = $("piMkReconcileBtn");

  if (readinessPanel) { readinessPanel.innerHTML = ""; readinessPanel.classList.add("hidden"); }
  if (confirmPanel) { confirmPanel.innerHTML = ""; confirmPanel.classList.add("hidden"); }
  if (successPanel) { successPanel.innerHTML = ""; successPanel.classList.add("hidden"); }
  if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }
  if (readinessBtn) readinessBtn.classList.remove("hidden");
  if (confirmBtn) confirmBtn.classList.add("hidden");
  if (reconcileBtn) reconcileBtn.classList.add("hidden");

  piModalOpen($("piMkPostModal"));
};

function closeMkPostModal() {
  _piMkPostTarget = null;
  _piMkPostFingerprint = "";
  _piMkPostPending = false;
  piModalClose($("piMkPostModal"), () => {});
}

async function loadMkPostReadiness() {
  if (!_piMkPostTarget) return;
  const { publicId } = _piMkPostTarget;
  const readinessBtn = $("piMkPostReadinessBtn");
  const readinessPanel = $("piMkPostReadinessPanel");
  const errEl = $("piMkPostError");

  if (readinessBtn) { readinessBtn.disabled = true; readinessBtn.textContent = "Проверяю..."; }
  if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }

  try {
    const data = await apiGet(`/api/payments/intents/${encodeURIComponent(publicId)}/moyklass-post-readiness`);
    if (readinessPanel) {
      readinessPanel.classList.remove("hidden");
      const checks = data.checks || [];
      const warnings = data.warnings || [];
      const preview = data.preview || {};
      const allOk = data.ready === true;

      const checksHtml = checks.map(c => `
        <div class="mk-readiness-check ${c.ok ? "ok" : "fail"}">
          <span class="mk-readiness-icon">${c.ok ? "✓" : "✗"}</span>
          <span class="mk-readiness-label">${escapeHtml(c.label || c.code || "")}</span>
          ${!c.ok ? `<span class="mk-readiness-detail">${escapeHtml(c.detail || "")}</span>` : ""}
        </div>
      `).join("");

      const warningsHtml = warnings.length
        ? `<div class="mk-readiness-warnings">${warnings.map(w => `<div class="mk-readiness-warning">⚠ ${escapeHtml(w)}</div>`).join("")}</div>`
        : "";

      let previewHtml = "";
      if (allOk && preview) {
        const paidByn = preview.paid_amount_byn != null
          ? fmtByn(preview.paid_amount_byn)
          : (preview.paid_amount_minor ? fmtByn(preview.paid_amount_minor / 100) : "—");
        const remainByn = preview.invoice_remaining_minor != null
          ? fmtByn(preview.invoice_remaining_minor / 100) : "—";
        previewHtml = `
          <div class="mk-post-preview">
            <div class="mk-post-preview-row"><span>Ученик</span><strong>${escapeHtml(preview.student_name || String(preview.mk_user_id || "?"))}</strong></div>
            <div class="mk-post-preview-row"><span>Счёт МК</span><strong>#${escapeHtml(String(preview.mk_invoice_id || "—"))}</strong></div>
            ${preview.mk_user_subscription_id ? `<div class="mk-post-preview-row"><span>Абонемент</span><strong>#${escapeHtml(String(preview.mk_user_subscription_id))}</strong></div>` : ""}
            <div class="mk-post-preview-row"><span>Остаток счёта</span><strong>${remainByn}</strong></div>
            <div class="mk-post-preview-row"><span>Сумма bePaid</span><strong>${paidByn}</strong></div>
            <div class="mk-post-preview-row"><span>Дата оплаты</span><strong>${escapeHtml((preview.paid_at || "").slice(0,10))}</strong></div>
            <div class="mk-post-preview-row"><span>Transaction UID</span><code style="font-size:10px">${escapeHtml((preview.transaction_uid || "").slice(0,16))}...</code></div>
            <div class="mk-post-preview-row"><span>Метод</span><strong>${escapeHtml(preview.payment_method_label || "bePaid ЕРИП")}</strong></div>
          </div>
          <div class="mk-post-warning-serious">
            ⚠ Будет создана реальная оплата в МойКласс. Операцию нельзя повторять.
          </div>
        `;
      }

      readinessPanel.innerHTML = `
        <div class="mk-readiness-checks">${checksHtml}</div>
        ${warningsHtml}
        ${previewHtml}
        ${data.invoice_error ? `<div style="color:var(--red);font-size:12px;margin-top:6px">${escapeHtml(data.invoice_error)}</div>` : ""}
      `;
    }

    if (data.ready) {
      _piMkPostFingerprint = data.snapshot_fingerprint || "";
      const confirmBtn = $("piMkPostConfirmBtn");
      if (confirmBtn) {
        const byn = _piMkPostTarget.amountByn;
        confirmBtn.textContent = `Внести ${fmtByn(byn)}`;
        confirmBtn.classList.remove("hidden");
        confirmBtn.disabled = false;
      }
      if (readinessBtn) readinessBtn.classList.add("hidden");
    } else {
      if (readinessBtn) { readinessBtn.disabled = false; readinessBtn.textContent = "Проверить снова"; }
    }
  } catch (err) {
    if (errEl) {
      errEl.textContent = `Ошибка: ${err.message || String(err)}`;
      errEl.classList.remove("hidden");
    }
    if (readinessBtn) { readinessBtn.disabled = false; readinessBtn.textContent = "Повторить проверку"; }
  }
}

async function confirmPostToMoyklass() {
  if (!_piMkPostTarget || !_piMkPostFingerprint) return;
  if (_piMkPostPending) return;
  const { publicId, amountByn } = _piMkPostTarget;
  const confirmBtn = $("piMkPostConfirmBtn");
  const errEl = $("piMkPostError");
  const successPanel = $("piMkPostSuccessPanel");

  _piMkPostPending = true;
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = "Отправляю..."; }
  if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }

  try {
    const data = await apiPost(`/api/payments/intents/${encodeURIComponent(publicId)}/post-to-moyklass`, {
      confirm: true,
      snapshot_fingerprint: _piMkPostFingerprint,
    });

    if (data.ok || data.idempotent) {
      const mkId = data.mk_payment_id || data.mk_payment_id;
      const postedAt = (data.posted_at || data.summary?.posted_at || "").slice(0, 10);
      if (successPanel) {
        successPanel.classList.remove("hidden");
        successPanel.innerHTML = `
          <div class="mk-post-success">
            <div class="mk-post-success-icon">✓</div>
            <div class="mk-post-success-title">Внесено в МойКласс</div>
            ${mkId ? `<div style="font-size:13px;margin-top:6px">Payment ID: <strong>${escapeHtml(String(mkId))}</strong></div>` : ""}
            ${postedAt ? `<div style="font-size:12px;color:var(--muted)">Дата: ${escapeHtml(postedAt)}</div>` : ""}
          </div>
        `;
      }
      if (confirmBtn) confirmBtn.classList.add("hidden");
      if ($("piMkPostReadinessPanel")) $("piMkPostReadinessPanel").classList.add("hidden");
      if ($("piMkPostConfirmPanel")) $("piMkPostConfirmPanel").classList.add("hidden");
      loadPaymentIntents();  // refresh card list
    } else {
      _piMkPostPending = false;
      if (errEl) {
        errEl.textContent = data.error || "Ошибка внесения в МойКласс";
        errEl.classList.remove("hidden");
      }
      if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = `Внести ${fmtByn(amountByn)}`; }
      if (data.error_code === "ambiguous_requires_reconciliation" || data.block_reason === "ambiguous_requires_reconciliation") {
        if ($("piMkReconcileBtn")) $("piMkReconcileBtn").classList.remove("hidden");
        if (confirmBtn) confirmBtn.classList.add("hidden");
      }
    }
  } catch (err) {
    _piMkPostPending = false;
    if (errEl) {
      errEl.textContent = `Ошибка запроса: ${err.message || String(err)}`;
      errEl.classList.remove("hidden");
    }
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = `Внести ${fmtByn(amountByn)}`; }
  }
}

async function reconcileMkPayment() {
  if (!_piMkPostTarget) return;
  const { publicId } = _piMkPostTarget;
  const errEl = $("piMkPostError");
  const reconcileBtn = $("piMkReconcileBtn");
  const successPanel = $("piMkPostSuccessPanel");

  if (reconcileBtn) { reconcileBtn.disabled = true; reconcileBtn.textContent = "Ищу..."; }
  if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }

  try {
    const data = await apiPost(`/api/payments/intents/${encodeURIComponent(publicId)}/reconcile-moyklass-payment`, {});
    if (data.reconciled || data.already_posted) {
      if (successPanel) {
        successPanel.classList.remove("hidden");
        successPanel.innerHTML = `
          <div class="mk-post-success">
            <div class="mk-post-success-icon">✓</div>
            <div class="mk-post-success-title">Оплата найдена в МойКласс</div>
            ${data.mk_payment_id ? `<div style="font-size:13px;margin-top:4px">Payment ID: <strong>${escapeHtml(String(data.mk_payment_id))}</strong></div>` : ""}
          </div>
        `;
      }
      if (reconcileBtn) reconcileBtn.classList.add("hidden");
      loadPaymentIntents();
    } else {
      if (errEl) {
        errEl.textContent = data.message || data.error || "Оплата в МойКласс не найдена.";
        errEl.classList.remove("hidden");
      }
      if (reconcileBtn) { reconcileBtn.disabled = false; reconcileBtn.textContent = "Проверить в МойКласс"; }
    }
  } catch (err) {
    if (errEl) {
      errEl.textContent = `Ошибка: ${err.message || String(err)}`;
      errEl.classList.remove("hidden");
    }
    if (reconcileBtn) { reconcileBtn.disabled = false; reconcileBtn.textContent = "Проверить в МойКласс"; }
  }
}

// ── MK Invoices (v7.0.90) ────────────────────────────────────────────────

let _mkInvoicesLoading = false;

async function loadMkInvoices(mode) {
  if (!canUsePaymentIntents()) return;
  if (_mkInvoicesLoading) return;
  _mkInvoicesLoading = true;

  const listEl = $("mkInvoicesList");
  const debugEl = $("mkInvoicesDebug");
  const diagEl = $("mkInvoicesDiag");
  const btnAll = $("loadMkInvoices");
  const btnById = $("loadMkInvoiceById");
  const btnByUser = $("loadMkInvoiceByUser");
  const activeBtn = mode === "byId" ? btnById : mode === "byUser" ? btnByUser : btnAll;
  if (btnAll) { btnAll.disabled = true; }
  if (btnById) { btnById.disabled = true; }
  if (btnByUser) { btnByUser.disabled = true; }
  if (activeBtn) { activeBtn.textContent = "Загрузка…"; }
  if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--muted)">Загрузка счетов МойКласс…</div>`;
  if (debugEl) debugEl.textContent = "";
  if (diagEl) diagEl.innerHTML = "";

  // Build URL: invoiceId → userId → general cached scan
  const invoiceId = ($("mkInvoiceSearchId")?.value || "").trim();
  const userId = ($("mkInvoiceSearchUserId")?.value || "").trim();
  let url = "/api/payments/moyklass/invoices?status=unpaid_partial&limit=50";
  if (mode === "byId" && invoiceId) url += "&invoiceId=" + encodeURIComponent(invoiceId);
  else if (mode === "byUser" && userId) url += "&userId=" + encodeURIComponent(userId);
  else if (!mode) {
    // legacy single-button path: read both fields
    if (invoiceId) url += "&invoiceId=" + encodeURIComponent(invoiceId);
    else if (userId) url += "&userId=" + encodeURIComponent(userId);
  }

  // 120-second timeout — long enough for 83-page scans; still finite
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 120000);

  const _isAdminDiag = () => {
    const r = state?.me?.role || "";
    return ["owner", "admin", "operations"].includes(r);
  };

  let data;
  try {
    const res = await fetch(apiUrl(url), { cache: "no-store", signal: controller.signal });
    clearTimeout(timeoutId);

    // ── JSON parse with diagnostic fallback ────────────────────────────────
    let rawText = "";
    let contentType = "";
    try {
      contentType = res.headers?.get("Content-Type") || "";
      rawText = await res.text();
      data = JSON.parse(rawText);
    } catch (_parseErr) {
      const adminLines = _isAdminDiag()
        ? `\nstage=json_parse | HTTP ${res.status} | ${contentType} | length=${rawText.length}\nerror=${_parseErr?.name}: ${String(_parseErr?.message||"").slice(0,120)}\npreview=${rawText.slice(0,200)}`
        : "";
      if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--red);white-space:pre-wrap">${escapeHtml("Сервер вернул некорректный ответ." + adminLines)}</div>`;
      return;
    }

    if (!data.ok) {
      const stageHint = data.stage ? ` [${data.stage}]` : "";
      if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--red)">${escapeHtml((data.error || "Ошибка загрузки счетов") + stageHint)}</div>`;
      return;
    }
  } catch (err) {
    clearTimeout(timeoutId);
    if (err && err.name === "AbortError") {
      if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--red)">Загрузка счетов заняла слишком много времени. Повторите попытку или выполните поиск по ученику/счёту.</div>`;
    } else {
      const errName = err?.name || "Error";
      const errMsg = String(err?.message || err || "").slice(0, 120);
      const detail = _isAdminDiag() ? `\nstage=fetch | ${errName}: ${errMsg}` : "";
      if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--red);white-space:pre-wrap">${escapeHtml("Сервер не смог загрузить счета МойКласс." + detail)}</div>`;
    }
    return;
  } finally {
    _mkInvoicesLoading = false;
    if (btnAll) { btnAll.disabled = false; btnAll.textContent = "Показать все неоплаченные"; }
    if (btnById) { btnById.disabled = false; btnById.textContent = "Найти счёт"; }
    if (btnByUser) { btnByUser.disabled = false; btnByUser.textContent = "Найти счета ученика"; }
  }

  // ── Response received ─────────────────────────────────────────────────────
  // Support both "invoices" and legacy "items" key (cache-hit/cache-miss parity)
  const invoices = Array.isArray(data.invoices) ? data.invoices
    : Array.isArray(data.items) ? data.items
    : null;

  if (invoices === null) {
    const diagMsg = _isAdminDiag()
      ? `stage=payload_validation: response ok=true but no invoices/items array. keys=${Object.keys(data).join(",")}`
      : "Сервер вернул неожиданный формат ответа.";
    if (listEl) listEl.innerHTML = `<div class="pi-empty" style="color:var(--red)">${escapeHtml(diagMsg)}</div>`;
    return;
  }

  const diag = data.diagnostics;
  const debtWarn = data.subscription_debt_warning;

  console.info("MK invoices received", {
    count: invoices.length,
    diagnostics: { cacheHit: diag?.cache_hit, pages: diag?.pages_loaded }
  });

  // Diagnostics block (admin/owner/operations only — server controls who gets this field)
  if (diag && diagEl) {
    const stoppedNote = diag.stopped_reason && diag.stopped_reason !== "total_reached" && diag.stopped_reason !== "direct_lookup"
      ? `<span style="color:var(--muted)">стоп: ${escapeHtml(diag.stopped_reason)}</span>` : "";
    const cacheNote = diag.cache_hit
      ? `<span style="color:var(--green,#1fa56b)">кеш ${diag.cache_age_seconds ?? ""}с</span>`
      : (diag.scan_duration_ms != null ? `<span>скан ${diag.scan_duration_ms}мс</span>` : "");
    diagEl.innerHTML = `<div class="mk-invoices-diag">
      <span>Всего в МК: ${diag.total_items_reported ?? "?"}</span>
      <span>Просмотрено: ${diag.raw_invoices_scanned ?? diag.normalised_count ?? "?"}</span>
      <span>Страниц: ${diag.pages_loaded ?? 1}</span>
      <span>Неоплаченных: ${diag.returned_count}</span>
      ${(diag.filtered_paid_count || 0) > 0 ? `<span>Оплаченных скрыто: ${diag.filtered_paid_count}</span>` : ""}
      ${(diag.filtered_invalid_count || 0) > 0 ? `<span class="mk-diag-warn">Невалидных: ${diag.filtered_invalid_count}</span>` : ""}
      ${stoppedNote}${cacheNote}
    </div>`;
  }

  if (debugEl) {
    debugEl.textContent = invoices.length > 0 ? `Найдено неоплаченных счетов: ${invoices.length}` : "";
  }

  if (!invoices.length) {
    let emptyHtml;
    if (debtWarn && debtWarn.warning === "subscription_debt_without_invoice") {
      const debtByn = Number(debtWarn.total_debt_byn || 0).toFixed(2);
      const subCount = debtWarn.subscriptions_with_debt || 0;
      emptyHtml = `<div class="pi-empty mk-empty-debt-warn">В МойКласс нет отдельных счетов, но найден долг по абонементу ${escapeHtml(debtByn)} BYN (${subCount} аб.). Отдельный счёт не создан — обратитесь к администратору.</div>`;
    } else if (diag && (diag.raw_invoices_scanned || 0) > 0 &&
        ["total_reached", "empty_page", "partial_page"].includes(diag.stopped_reason)) {
      emptyHtml = `<div class="pi-empty">В МойКласс нет неоплаченных счетов (просмотрено ${diag.raw_invoices_scanned})</div>`;
    } else {
      emptyHtml = `<div class="pi-empty">В МойКласс нет неоплаченных счетов</div>`;
    }
    if (listEl) listEl.innerHTML = emptyHtml;
    return;
  }

  // Per-card safe rendering — one broken card doesn't break the list
  const cardHtmls = invoices.map((inv, _idx) => {
    try {
      return renderMkInvoiceCard(inv);
    } catch (_cardErr) {
      console.error("MK invoice card render failed", {
        invoiceId: inv?.invoice_id ?? null,
        errorName: _cardErr?.name || "",
        errorMessage: String(_cardErr?.message || _cardErr).slice(0, 200),
      });
      return `<div class="mk-invoice-card" style="border-left:3px solid var(--red);opacity:.7"><span style="font-size:12px;color:var(--muted)">Счёт #${escapeHtml(String(inv?.invoice_id || _idx + 1))}: ошибка отображения</span></div>`;
    }
  });
  if (listEl) listEl.innerHTML = cardHtmls.join("");
}

function _fmtDate(dateStr) {
  if (!dateStr) return "";
  const s = String(dateStr).slice(0, 10);
  if (!s.includes("-")) return s;
  const parts = s.split("-");
  if (parts.length !== 3) return s;
  return `${parts[2]}.${parts[1]}.${parts[0]}`;
}

function renderMkInvoiceCard(inv) {
  const remaining = Number(inv.remaining || 0);
  const price = Number(inv.price || 0);
  const payed = Number(inv.payed || 0);
  const userId = inv.mk_user_id || "?";
  const status = inv.invoice_status || "—";
  const statusLabel = status === "unpaid" ? "Не оплачен"
    : status === "partial" ? "Частично оплачен"
    : status === "paid" ? "Оплачен" : status;
  const statusCls = status === "unpaid" ? "chip-pi-draft"
    : status === "partial" ? "chip-pi-bepaid"
    : "chip-pi-paid";

  // Student name — main heading
  const studentName = inv.student_name
    ? escapeHtml(inv.student_name)
    : `<span style="color:var(--muted)">Ученик userId ${escapeHtml(String(userId))}</span>`;

  // Dates
  const payUntilFmt = inv.pay_until ? _fmtDate(inv.pay_until) : "";
  const dateCreatedFmt = _fmtDate(inv.date || inv.created_at || "");

  // Subscription
  const subId = inv.user_subscription_id || inv.subscription?.id;
  const subLine = subId
    ? `<div class="mk-invoice-detail-row">Абонемент №<strong>${escapeHtml(String(subId))}</strong></div>`
    : "";

  const comment = inv.comment ? `<div class="pi-card-comment">${escapeHtml(inv.comment)}</div>` : "";

  // Active intent state
  const hasActive = !!inv.active_intent_id;
  const hasBePaid = !!inv.active_bepaid_uid;

  const bePaidRow = hasBePaid
    ? `<div class="mk-invoice-bepaid-row">
        <span class="chip chip-pi-paid" style="font-size:10px">В bePaid</span>
        ${inv.active_bepaid_account ? `<span>ERIP: <strong>${escapeHtml(String(inv.active_bepaid_account))}</strong></span>` : ""}
        <span style="font-size:10px;color:var(--muted)">UID: ${escapeHtml(inv.active_bepaid_uid)}</span>
      </div>`
    : "";

  const activeIntentBadge = hasActive
    ? `<div class="mk-invoice-intent-badge">
        <span class="chip chip-pi-bepaid" style="font-size:10px">Черновик создан</span>
        <strong>${escapeHtml(inv.active_intent_id)}</strong>
        <span class="mk-invoice-intent-status">(${escapeHtml(inv.active_intent_status || "")})</span>
        <button class="secondary" style="font-size:11px;padding:3px 9px"
          data-action="show-payment-intent"
          data-intent-public-id="${escapeHtml(String(inv.active_intent_id))}"
          data-period-month="${escapeHtml(String(inv.active_intent_period_month || ""))}"
        >${["bepaid_created","paid","posted_to_moyklass","bepaid_requires_check","bepaid_creating"].includes(inv.active_intent_status) ? "Открыть платёж" : "Показать черновик"}</button>
      </div>${bePaidRow}`
    : "";

  const blockedNote = hasActive && !hasBePaid
    ? `<div class="mk-invoice-blocked-note">Для повторного создания черновика отмените существующий.</div>`
    : "";

  const createBtn = !hasActive
    ? `<button class="primary" style="font-size:12px;padding:4px 12px" onclick="openMkInvoiceCreate(${escapeHtml(JSON.stringify({invoice_id: inv.invoice_id, mk_user_id: userId, remaining, price, pay_until: inv.pay_until, student_name: inv.student_name || ""}))})">Подготовить черновик bePaid</button>`
    : "";

  return `<div class="mk-invoice-card${hasActive ? " has-active-intent" : ""}">
    <div class="mk-invoice-student-name">${studentName}</div>
    <div class="mk-invoice-card-head">
      <div>
        <div class="mk-invoice-number">Счёт МойКласс №${escapeHtml(String(inv.invoice_id))}</div>
        <div class="mk-invoice-status-row">
          <span class="chip ${statusCls}" style="font-size:10px">${escapeHtml(statusLabel)}</span>
          ${payUntilFmt ? `<span class="mk-invoice-pay-until">до ${escapeHtml(payUntilFmt)}</span>` : ""}
        </div>
      </div>
      <div class="mk-invoice-card-amount">${fmtByn(remaining)}</div>
    </div>
    <div class="mk-invoice-finance">
      <div class="mk-invoice-finance__item">
        <span class="mk-invoice-finance__label">Выставлено</span>
        <strong class="mk-invoice-finance__value">${fmtByn(price)}</strong>
      </div>
      <div class="mk-invoice-finance__item">
        <span class="mk-invoice-finance__label">Оплачено</span>
        <strong class="mk-invoice-finance__value">${fmtByn(payed)}</strong>
      </div>
      <div class="mk-invoice-finance__item">
        <span class="mk-invoice-finance__label">Остаток</span>
        <strong class="mk-invoice-finance__value mk-invoice-finance__value--remaining">${fmtByn(remaining)}</strong>
      </div>
    </div>
    ${subLine}
    ${dateCreatedFmt ? `<div class="mk-invoice-detail-row" style="color:var(--muted)">Создан: ${escapeHtml(dateCreatedFmt)}</div>` : ""}
    ${comment}
    <div class="mk-invoice-user-id">userId: ${escapeHtml(String(userId))}</div>
    ${activeIntentBadge}
    ${blockedNote}
    <div class="mk-invoice-card-footer">${createBtn}</div>
  </div>`;
}

// ── Canonical DOM id for payment-intent cards ────────────────────────────

function paymentIntentDomId(publicId) {
  return "payment-intent-" + String(publicId || "").trim().replace(/[^a-zA-Z0-9_-]/g, "-");
}

// ── Navigate to an existing payment intent ────────────────────────────────
// Opens the accordion, sets month+status filters, loads the list, then
// scrolls to the target card.  No payment operations are performed.

async function showPaymentIntent(publicId, periodMonth) {
  const normalizedId = String(publicId || "").trim();
  if (!normalizedId) {
    try { showToast("Не удалось определить черновик."); } catch (_) {}
    return;
  }

  // 1. Ensure reports tab is visible
  const reportsPanel = $("tab-reports");
  if (reportsPanel && !reportsPanel.classList.contains("active")) {
    if (typeof activateTab === "function") activateTab("reports");
    await new Promise(r => requestAnimationFrame(r));
  }

  // 2. Open accordion if closed
  const acc = $("paymentIntentsAccordion");
  if (acc && !acc.open) {
    acc.open = true;
    // Tiny delay so toggle-listener fires (it also calls loadPaymentIntents,
    // but we will call it ourselves below with the correct filters)
    await new Promise(r => setTimeout(r, 30));
  }

  // 3. Set filters: target month + status=all
  //    Do this AFTER opening the accordion so initMonthPicker in toggle handler
  //    does not overwrite our value (initMonthPicker only sets value when it's empty/invalid)
  const monthInput = $("piMonthFilter");
  const statusInput = $("piStatusFilter");
  const targetMonth = (periodMonth && periodMonth.match(/^\d{4}-\d{2}$/))
    ? periodMonth
    : (typeof currentMonthValue === "function" ? currentMonthValue() : "");
  if (monthInput && targetMonth) {
    monthInput.value = targetMonth;
    if (typeof syncMonthPicker === "function") syncMonthPicker(monthInput);
  }
  if (statusInput) statusInput.value = "all";

  // 4. Reload with the correct filters (awaited — list is ready when resolved)
  await loadPaymentIntents();

  // 5. Two rAF frames so the browser commits the new innerHTML to layout
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  // 6. Find the card and scroll
  const domId = paymentIntentDomId(normalizedId);
  const target = document.getElementById(domId);
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    // Remove first so the ::after animation restarts even if called twice
    target.classList.remove("pi-card-highlight");
    void target.offsetWidth;  // force reflow
    target.classList.add("pi-card-highlight");
    setTimeout(() => target.classList.remove("pi-card-highlight"), 2000);
  } else {
    // Not found after correct filters — show diagnostic
    const listEl = $("piList");
    const loadedCount = listEl ? listEl.querySelectorAll(".pi-card").length : 0;
    const msg = `Черновик ${normalizedId} существует, но не попал в текущий список (загружено: ${loadedCount}).`;
    try { showToast(msg); } catch (_) {}
    console.warn("showPaymentIntent: target not found", {
      publicId: normalizedId, domId, month: targetMonth, loadedCount,
    });
  }
}

async function openMkInvoiceCreate(inv) {
  if (!canUsePaymentIntents()) return;
  const nameLabel = inv.student_name || `userId=${inv.mk_user_id}`;
  const confirmMsg = `Создать черновик bePaid для счёта МойКласс #${inv.invoice_id}?\n\nУченик: ${nameLabel}\nСумма: ${fmtByn(Number(inv.remaining))}\n\nДанные будут подтверждены в МойКласс.`;
  if (!confirm(confirmMsg)) return;

  const debugEl = $("mkInvoicesDebug");
  if (debugEl) debugEl.textContent = "Создание черновика...";

  let data;
  try {
    data = await apiPost("/api/payments/intents/from-moyklass-invoice", {
      invoice_id: String(inv.invoice_id),
      mk_user_id: inv.mk_user_id,
      payment_method: "erip",
    });
  } catch (err) {
    if (debugEl) debugEl.textContent = "";
    try { showToast("Ошибка связи с сервером"); } catch (_) {}
    return;
  }

  if (!data.ok) {
    if (debugEl) debugEl.textContent = "";
    // Duplicate intent — friendly message, not system alert
    const dupId = data.duplicate_intent_id || data.existing_intent_id;
    if (dupId) {
      try { showToast(`Черновик уже существует: ${dupId}`); } catch (_) {}
    } else {
      try { showToast("Ошибка: " + (data.error || "Неизвестная ошибка")); } catch (_) {}
    }
    return;
  }

  const intent = data.intent;
  const publicId = intent?.public_id || data.public_id || "";
  if (debugEl) debugEl.textContent = publicId ? `Черновик ${publicId} создан.` : "Черновик создан.";

  try { showToast(data.message || "Черновик создан!"); } catch (_toastErr) {
    console.warn("Toast failed after intent creation", _toastErr);
  }

  // Refresh invoice list; navigate to the new intent
  loadMkInvoices();
  if (publicId) {
    showPaymentIntent(publicId, "").catch(e => console.warn("showPaymentIntent after create failed", e));
  } else {
    loadPaymentIntents();
  }
}

// ── Wire up event listeners ───────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Load intents when accordion opens
  $("paymentIntentsAccordion")?.addEventListener("toggle", e => {
    if (e.target.open && canUsePaymentIntents()) {
      initMonthPicker($("piMonthFilter"), "");
      loadPaymentIntents();
      if (canPostToMoyklass()) loadMkPaymentTypes();
    }
  });

  $("loadPaymentIntents")?.addEventListener("click", loadPaymentIntents);
  $("openCreateIntent")?.addEventListener("click", () => openCreateIntentModal());
  $("piMonthFilter")?.addEventListener("change", loadPaymentIntents);
  $("piStatusFilter")?.addEventListener("change", loadPaymentIntents);
  $("loadMkInvoices")?.addEventListener("click", () => loadMkInvoices());
  $("loadMkInvoiceById")?.addEventListener("click", () => loadMkInvoices("byId"));
  $("loadMkInvoiceByUser")?.addEventListener("click", () => loadMkInvoices("byUser"));

  // Create modal
  $("piModalSubmit")?.addEventListener("click", submitCreateIntent);
  $("piModalCancel")?.addEventListener("click", closeCreateIntentModal);
  $("piModalClose")?.addEventListener("click", closeCreateIntentModal);
  // backdrop click — only fires when clicking the dark overlay (not the sheet itself)
  $("piCreateModal")?.addEventListener("click", e => { if (e.target === $("piCreateModal")) closeCreateIntentModal(); });

  // Cancel modal
  $("piCancelModalConfirm")?.addEventListener("click", confirmCancelIntent);
  $("piCancelModalBack")?.addEventListener("click", closeCancelIntentModal);
  $("piCancelModalClose")?.addEventListener("click", closeCancelIntentModal);
  $("piCancelModal")?.addEventListener("click", e => { if (e.target === $("piCancelModal")) closeCancelIntentModal(); });

  // bePaid ERIP modal
  $("piBePaidModalConfirm")?.addEventListener("click", confirmCreateBePaid);
  $("piBePaidModalBack")?.addEventListener("click", closeBePaidModal);
  $("piBePaidModalClose")?.addEventListener("click", closeBePaidModal);
  $("piBePaidModal")?.addEventListener("click", e => { if (e.target === $("piBePaidModal")) closeBePaidModal(); });

  // Payment type discovery (v7.0.92.1)
  $("refreshMkPaymentTypes")?.addEventListener("click", loadMkPaymentTypes);

  // MoyKlass post modal (v7.0.92)
  $("piMkPostModalBack")?.addEventListener("click", closeMkPostModal);
  $("piMkPostModalClose")?.addEventListener("click", closeMkPostModal);
  $("piMkPostModal")?.addEventListener("click", e => { if (e.target === $("piMkPostModal")) closeMkPostModal(); });
  $("piMkPostReadinessBtn")?.addEventListener("click", loadMkPostReadiness);
  $("piMkPostConfirmBtn")?.addEventListener("click", confirmPostToMoyklass);
  $("piMkReconcileBtn")?.addEventListener("click", reconcileMkPayment);

  // Event delegation for dynamically rendered invoice-card buttons
  document.addEventListener("click", e => {
    const btn = e.target.closest("[data-action='show-payment-intent']");
    if (!btn) return;
    const intentId = btn.dataset.intentPublicId || "";
    const periodMonth = btn.dataset.periodMonth || "";
    if (intentId) showPaymentIntent(intentId, periodMonth);
  });
});
