/* Run against an isolated local server and a new browser profile. */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { chromium } = require(process.env.MOYU_PLAYWRIGHT_MODULE || "playwright");

const root = path.resolve(process.argv[2] || process.cwd());
const output = __dirname;
const results = {};
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "moyu-root-browser-audit-"));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let processHandle;
let browser;

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, "127.0.0.1", resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

async function main() {
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  processHandle = spawn(process.env.MOYU_AUDIT_PYTHON || "python3", ["server.py"], {
    cwd: root,
    env: {
      ...process.env, MOYU_DB: path.join(temporary, "audit.sqlite3"),
      MOYU_HOST: "127.0.0.1", MOYU_PORT: String(port),
      MOYU_AI_BASE_URL: "", MOYU_AI_MODEL: "", MOYU_AI_API_KEY: "",
    },
    stdio: ["ignore", "ignore", "pipe"],
  });
  let errorLog = "";
  processHandle.stderr.on("data", chunk => { errorLog += chunk.toString(); });
  for (let n = 0; n < 100; n++) {
    try {
      if ((await fetch(base + "/api/health")).ok) break;
    } catch {}
    if (n === 99) throw new Error("Local server did not start: " + errorLog.slice(-1500));
    await pause(30);
  }
  async function api(route, method = "GET", body) {
    const response = await fetch(base + route, {
      method, headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { http: response.status, data: await response.json() };
  }
  const initial = (await api("/api/state")).data;
  const firstId = initial.chapters[0].id;
  initial.project.title = "审查用隔离作品";
  initial.project.summary = "林岚寻找旧码头的钥匙。";
  initial.chapters[0].body = "林岚走到旧码头，把钥匙交给守门人。她决定沿着回城路线追查灯塔。" .repeat(6);
  initial.chapters[0].status = "进行中";
  initial.chapters[0].next_brief = "下一章从守门人收到钥匙开始。";
  initial.chapters.push({
    id: "audit_other_chapter", title: "第二章 隔离样本",
    body: "苍遥站在火星的观测室里。", status: "草稿",
  });
  const seeded = await api("/api/state", "PUT", initial);
  if (seeded.http !== 200) throw new Error(JSON.stringify(seeded.data));
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.MOYU_CHROME_PATH || undefined,
  });
  async function open(viewport) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(base);
    await page.waitForFunction(() => backendOnline === true);
    await page.locator('[data-view="editor"]').click();
    return { context, page, errors };
  }

  const desktop = await open({ width: 1440, height: 1000 });
  await desktop.page.screenshot({ path: path.join(output, "desktop-editor.png"), fullPage: true });
  const beforeFormat = await desktop.page.locator("#chapterBody").inputValue();
  await desktop.page.locator("#chapterBody").evaluate(element => {
    element.focus();
    element.setSelectionRange(0, 2);
  });
  await desktop.page.locator('[data-format="bold"]').click();
  results.desktop = {
    viewport: [1440, 1000], page_errors: desktop.errors,
    horizontal_overflow: await desktop.page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    bold_button_changed_body: beforeFormat !== await desktop.page.locator("#chapterBody").inputValue(),
  };

  await desktop.page.locator('[data-view="plot"]').click();
  await desktop.page.locator('[data-action="newPlot"]').click();
  await desktop.page.locator("#plotText").fill("旧钥匙必须在结尾回收。");
  await desktop.page.locator("[data-ok]").click();
  await pause(500);
  const plotCard = desktop.page.locator(".plot-item").filter({ hasText: "旧钥匙" });
  await plotCard.click();
  results.plot_workflow = {
    card_text: await plotCard.innerText(),
    card_has_action: await plotCard.locator("button,a,input,select").count() > 0,
    movable_stage_control_count: await desktop.page.locator("[data-plot-stage], [data-plot-move]").count(),
  };

  await desktop.page.locator('[data-view="editor"]').click();
  await desktop.page.locator('[data-chapter="' + firstId + '"]').click();
  const beforeAdoption = (await api("/api/state")).data;
  let delayedRoute;
  await desktop.page.route("**/api/ai", async route => { delayedRoute = route; });
  await desktop.page.locator("#aiGenerate").click();
  for (let n = 0; n < 100 && !delayedRoute; n++) await pause(10);
  if (!delayedRoute) throw new Error("AI route was not requested");
  await desktop.page.locator('[data-chapter="audit_other_chapter"]').click();
  await delayedRoute.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      mode: "provider", model: "free-browser-mock", text: "第一章专属候选哨兵",
      ai_run_id: "local_audit_wrong_chapter", truncated: false,
    }),
  });
  await desktop.page.waitForFunction(() => aiBusy === false);
  await desktop.page.locator("#aiAdopt").click();
  await desktop.page.waitForFunction(() => state.chapters.find(c => c.id === "audit_other_chapter").body.includes("第一章专属候选哨兵"));
  await pause(450);
  const afterAdoption = (await api("/api/state")).data;
  await desktop.page.locator("#aiAdopt").click();
  const duplicateCount = await desktop.page.evaluate(
    () => state.chapters.find(c => c.id === "audit_other_chapter").body.split("第一章专属候选哨兵").length - 1
  );
  results.ai_switch_chapter = {
    generation_chapter_id: firstId,
    actual_local_target_id: "audit_other_chapter",
    wrong_chapter_has_candidate_in_browser: true,
    wrong_chapter_has_candidate_in_database: afterAdoption.chapters.find(c => c.id === "audit_other_chapter").body.includes("第一章专属候选哨兵"),
    original_version: beforeAdoption.version, server_version: afterAdoption.version,
    backend_online_after_adoption: await desktop.page.evaluate(() => backendOnline),
    displayed_save_status: await desktop.page.locator("#saveState").innerText(),
    same_candidate_count_after_second_adoption: duplicateCount,
    scope: "Real page + local server; AI response replaced by a free browser mock",
  };
  await desktop.context.close();

  const mobile = await open({ width: 390, height: 844 });
  await mobile.page.screenshot({ path: path.join(output, "mobile-editor.png"), fullPage: true });
  results.mobile = {
    viewport: [390, 844], page_errors: mobile.errors,
    horizontal_overflow: await mobile.page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    controls_with_overflowing_text: await mobile.page.locator("button").evaluateAll(
      buttons => buttons.filter(button => button.clientHeight > 0 && button.scrollHeight > button.clientHeight + 2)
        .map(button => ({ text: button.innerText, client_height: button.clientHeight, content_height: button.scrollHeight }))
    ),
    ai_instruction_width: (await mobile.page.locator("#aiInstruction").boundingBox()).width,
  };
  await mobile.context.close();

  const slow = await open({ width: 1280, height: 900 });
  let firstSaveRoute;
  let firstSaveResponse;
  const saveVersions = [];
  let firstSaveReady;
  const firstSaveObserved = new Promise(resolve => { firstSaveReady = resolve; });
  await slow.page.route("**/api/state", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    saveVersions.push(route.request().postDataJSON().version);
    if (!firstSaveRoute) {
      firstSaveRoute = route;
      firstSaveResponse = await route.fetch();
      firstSaveReady();
      return;
    }
    await route.continue();
  });
  const original = await slow.page.locator("#chapterBody").inputValue();
  await slow.page.locator("#chapterBody").fill(original + "\n第一次输入。");
  await firstSaveObserved;
  await slow.page.locator("#chapterBody").fill(original + "\n第一次输入。\n保存等待中继续输入。");
  await firstSaveRoute.fulfill({ response: firstSaveResponse });
  for (let n = 0; n < 100 && saveVersions.length < 2; n++) await pause(20);
  await pause(300);
  const persistedAfterSlowSave = (await api("/api/state")).data;
  results.typing_during_save = {
    transmitted_versions: saveVersions,
    database_contains_newest_input: persistedAfterSlowSave.chapters[0].body.includes("保存等待中继续输入。"),
    browser_contains_newest_input: (await slow.page.locator("#chapterBody").inputValue()).includes("保存等待中继续输入。"),
    backend_online_after_second_save: await slow.page.evaluate(() => backendOnline),
    scope: "Real browser + real HTTP; first successful response delivery delayed",
  };
  await slow.context.close();

  const longState = (await api("/api/state")).data;
  const longBody = "不可删除的章首哨兵" + "完整章节内容。".repeat(2200) + "章尾。";
  longState.chapters[0].body = longBody;
  const longSeeded = await api("/api/state", "PUT", longState);
  if (longSeeded.http !== 200) throw new Error(JSON.stringify(longSeeded.data));
  const preview = await api("/api/ai/prompt-preview", "POST", {
    task: "polish", chapter_id: firstId,
  });
  const polish = await open({ width: 1440, height: 1000 });
  await polish.page.locator('[data-chapter="' + firstId + '"]').click();
  await polish.page.locator("#aiTask").selectOption("polish");
  const polishCandidate = "润色后的章尾候选。".repeat(10);
  await polish.page.route("**/api/ai", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      mode: "provider", model: "free-browser-mock",
      text: polishCandidate, ai_run_id: "local_audit_polish", truncated: false,
    }),
  }));
  await polish.page.locator("#aiGenerate").click();
  await polish.page.waitForFunction(() => aiDraft !== null && aiBusy === false);
  polish.page.once("dialog", dialog => dialog.accept());
  await polish.page.locator("#aiAdopt").click();
  await polish.page.waitForFunction(
    ({ chapterId, candidate }) => state.chapters.find(c => c.id === chapterId).body === candidate,
    { chapterId: firstId, candidate: polishCandidate }
  );
  await pause(450);
  const replacedBody = await polish.page.locator("#chapterBody").inputValue();
  const afterPolish = (await api("/api/state")).data;
  results.long_chapter_polish = {
    original_characters: longBody.length,
    preview_http: preview.http,
    real_prompt_contains_chapter_head: String(preview.data.user || "").includes("不可删除的章首哨兵"),
    local_body_characters_after_adoption: replacedBody.length,
    local_body_contains_chapter_head: replacedBody.includes("不可删除的章首哨兵"),
    database_body_characters_after_adoption: afterPolish.chapters.find(c => c.id === firstId).body.length,
    database_body_contains_chapter_head: afterPolish.chapters.find(c => c.id === firstId).body.includes("不可删除的章首哨兵"),
    backend_online_after_adoption: await polish.page.evaluate(() => backendOnline),
    scope: "Actual prompt-preview + real browser adoption; generated output mocked; browser and database checked separately",
  };
  await polish.context.close();

  const rejection = await api("/api/memory", "POST", {
    id: "root_rejected_memory", type: "人物", name: "审查角色",
  });
  if (rejection.http !== 201) throw new Error(JSON.stringify(rejection.data));
  await api("/api/memory/root_rejected_memory", "PATCH", { status: "rejected" });
  const rejectedPageContext = await browser.newContext();
  const rejectedPage = await rejectedPageContext.newPage();
  await rejectedPage.goto(base);
  await pause(350);
  results.rejected_memory_reload = {
    database_title: (await api("/api/state")).data.project.title,
    page_title: await rejectedPage.locator("#projectTitle").innerText(),
    backend_online: await rejectedPage.evaluate(() => backendOnline),
    source_data_overwritten: false,
    scope: "New browser cache, actual GET /api/state, frontend hydration",
  };
  await rejectedPageContext.close();
}

main().catch(error => {
  results.probe_error = { message: error.message, stack: error.stack };
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (processHandle && processHandle.exitCode === null) {
    const stopped = new Promise(resolve => processHandle.once("exit", resolve));
    processHandle.kill("SIGTERM");
    await Promise.race([stopped, pause(3000)]);
    if (processHandle.exitCode === null) processHandle.kill("SIGKILL");
    await stopped;
  }
  fs.writeFileSync(path.join(output, "root-browser.json"), JSON.stringify(results, null, 2) + "\n");
  fs.rmSync(temporary, { recursive: true, force: true });
  console.log(JSON.stringify(results, null, 2));
});
