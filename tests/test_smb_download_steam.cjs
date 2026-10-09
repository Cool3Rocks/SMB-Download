const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const ts = require("typescript");
const { test } = require("node:test");
const output = ts.transpileModule(fs.readFileSync("src/smbDownloadSteamImport.ts", "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiled = new Module("smbDownloadSteamImport");
compiled._compile(output, "smbDownloadSteamImport.cjs");
const { importSmbDownloadShortcut: importShortcut } = compiled.exports;
const readyOverview = { GetAppOverviewByAppID: () => ({}) };
function importSmbDownloadShortcut(task, apps, call, store, overview = readyOverview) {
  return importShortcut(task, apps, call, store, overview);
}
function steamApps(methods) {
  return { SetShortcutName: async () => {}, SetShortcutExe: async () => {},
    SetShortcutStartDir: async () => {}, SetShortcutLaunchOptions: async () => {}, ...methods };
}
const task = { id: "task", status: "import_pending", title: "中文游戏", appid: 0,
  executable: "/home/deck/Games/test/bin/game.exe", start_dir: "/home/deck/Games/test/bin",
  launch_options: "--fullscreen", compat_tool: "proton_experimental" };
function storage() {
  const map = new Map();
  return { getItem: k => map.get(k), setItem: (k, v) => map.set(k, v), removeItem: k => map.delete(k) };
}
test("configuration failure retries reuse the shortcut ID", async () => {
  const store = storage();
  let creates = 0, fail = true;
  const actions = [];
  const apps = steamApps({ AddShortcut: async (...args) => { creates++; assert.equal(args[1], `"${task.executable}"`); return -123; },
    SpecifyCompatTool: async () => { if (fail) throw Error("configuration failed"); } });
  const call = async payload => actions.push(payload);
  await assert.rejects(importSmbDownloadShortcut(task, apps, call, store), /configuration failed/);
  assert.equal(actions.some(p => p.action === "finish_import"), false);
  fail = false;
  await importSmbDownloadShortcut(task, apps, call, store);
  assert.equal(creates, 1);
  assert.equal(actions[0].appid, -123 >>> 0);
  assert.equal(actions.at(-1).action, "finish_import");
  assert.equal(store.getItem("smb-download-appid:task"), undefined);
});
test("lost backend response retains ID and does not recreate shortcut", async () => {
  const store = storage(); let creates = 0;
  const apps = steamApps({ AddShortcut: async () => { creates++; return 2147483649; }, SpecifyCompatTool: async () => {} });
  await assert.rejects(importSmbDownloadShortcut(task, apps, async () => { throw Error("offline"); }, store));
  await importSmbDownloadShortcut(task, apps, async () => {}, store);
  assert.equal(creates, 1);
});
test("unavailable Steam or invalid AddShortcut response never completes", async () => {
  let calls = 0;
  await assert.rejects(importSmbDownloadShortcut(task, null, async () => calls++, storage()));
  await assert.rejects(importSmbDownloadShortcut(task, steamApps({ AddShortcut: async () => undefined, SpecifyCompatTool: () => {} }), async () => calls++, storage()));
  assert.equal(calls, 0);
});
test("completed tasks do not call Steam again", async () => {
  await importSmbDownloadShortcut({ ...task, status: "complete" }, null, async () => { throw Error("unexpected"); }, storage());
});
test("paths with spaces are quoted for creation and subsequent setters", async () => {
  const spaced = { ...task, executable: "/home/deck/Games/我的 游戏/Game.exe", start_dir: "/home/deck/Games/我的 游戏" };
  const expectedExe = '"/home/deck/Games/我的 游戏/Game.exe"';
  const expectedDir = '"/home/deck/Games/我的 游戏"';
  let created = 0;
  const apps = steamApps({
    AddShortcut: async (title, exe, dir, options) => {
      created++; assert.equal(exe, expectedExe); assert.equal(dir, "");
      assert.equal(options, ""); return 2147483650;
    },
    SetShortcutExe: async (id, exe) => assert.equal(exe, expectedExe),
    SetShortcutStartDir: async (id, dir) => assert.equal(dir, expectedDir),
    SpecifyCompatTool: async () => {},
  });
  await importSmbDownloadShortcut(spaced, apps, async () => {}, storage());
  await importSmbDownloadShortcut({ ...spaced, appid: 2147483650, executable: expectedExe, start_dir: expectedDir }, apps, async () => {}, storage());
  assert.equal(created, 1);
});

test("waits for native library registration before writing any settings", async () => {
  const events = [];
  let registered = false, polls = 0;
  const overview = { GetAppOverviewByAppID: id => {
    assert.equal(id, 2147483651);
    if (++polls >= 3) registered = true;
    return registered ? { appid: id } : undefined;
  } };
  const write = name => async () => { assert.equal(registered, true); events.push(name); };
  const apps = steamApps({ AddShortcut: async () => 2147483651,
    SetShortcutName: write("name"), SetShortcutExe: write("exe"),
    SetShortcutStartDir: write("dir"), SetAppLaunchOptions: write("options"),
    SetShortcutLaunchOptions: async () => { throw Error("use current API"); },
    SpecifyCompatTool: write("proton") });
  await importSmbDownloadShortcut(task, apps, async p => events.push(p.action), storage(), overview);
  assert.deepEqual(events, ["record_appid", "name", "exe", "dir", "options", "proton", "finish_import"]);
});

test("library synchronization timeout retains ID and retry waits without duplicating", async () => {
  const store = storage(), actions = [];
  let creates = 0, writes = 0;
  const apps = steamApps({ AddShortcut: async () => { creates++; return 2147483652; },
    SetShortcutExe: async () => writes++, SpecifyCompatTool: async () => {} });
  const originalNow = Date.now;
  let clock = 0;
  try {
    Date.now = () => (clock += 6000);
    await assert.rejects(importSmbDownloadShortcut(task, apps, async p => actions.push(p.action), store,
      { GetAppOverviewByAppID: () => undefined }), /同步超时/);
  } finally { Date.now = originalNow; }
  assert.equal(writes, 0);
  assert.equal(actions.includes("finish_import"), false);
  assert.equal(store.getItem("smb-download-appid:task"), "2147483652");
  await importSmbDownloadShortcut(task, apps, async p => actions.push(p.action), store);
  assert.equal(creates, 1);
  assert.equal(writes, 1);
  assert.equal(actions.at(-1), "finish_import");
});

test("missing library interface does not create an unverified shortcut", async () => {
  let creates = 0;
  const apps = steamApps({ AddShortcut: async () => { creates++; return 1; } });
  await assert.rejects(importShortcut(task, apps, async () => {}, storage()), /游戏库接口/);
  assert.equal(creates, 0);
});
