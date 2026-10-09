const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const ts = require("typescript");
const { test } = require("node:test");
const compiled = new Module("smbDownloadDelete");
compiled._compile(ts.transpileModule(fs.readFileSync("src/smbDownloadDelete.ts", "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, "smbDownloadDelete.cjs");
const { deleteDownloadedGame } = compiled.exports;
const task = { id: "game", appid: 0x80000001, status: "complete", steam_removed: false };
function fixture(overrides = {}) {
  const actions = [], store = new Map();
  const pending = { ...task, ...overrides };
  return { actions, pending, store,
    storage: { getItem: key => store.get(key), removeItem: key => store.delete(key) },
    call: async p => { actions.push(p); return p.action === "begin_delete" ? pending : {}; } };
}
test("Steam shortcut removal is verified before files and task are deleted", async () => {
  const f = fixture(); let exists = true;
  const apps = { RemoveShortcut: async id => {
    assert.equal(id, task.appid); f.actions.push({ action: "remove" }); exists = false;
  } };
  await deleteDownloadedGame(task, apps, f.call, f.storage, { GetAppOverviewByAppID: () => exists });
  assert.deepEqual(f.actions.map(p => p.action), ["begin_delete", "remove", "shortcut_removed", "delete_game"]);
});
test("Steam failure preserves files and exposes retry", async () => {
  const f = fixture();
  await assert.rejects(deleteDownloadedGame(task, { RemoveShortcut: async () => { throw Error("Steam failed"); } },
    f.call, f.storage, { GetAppOverviewByAppID: () => true }), /Steam failed/);
  assert.equal(f.actions.some(p => p.action === "delete_game"), false);
  assert.equal(f.actions.at(-1).action, "delete_failed");
});
test("missing Steam verification interface never deletes imported game files", async () => {
  const f = fixture();
  await assert.rejects(deleteDownloadedGame(task, {}, f.call, f.storage), /接口不可用/);
  assert.equal(f.actions.some(p => p.action === "delete_game"), false);
});
test("already removed shortcut and already journalled removal support retry", async () => {
  for (const removed of [false, true]) {
    const f = fixture({ steam_removed: removed });
    await deleteDownloadedGame(task, { RemoveShortcut: () => { throw Error("must not remove twice"); } },
      f.call, f.storage, { GetAppOverviewByAppID: () => null });
    assert.equal(f.actions.at(-1).action, "delete_game");
  }
});
test("download-only task deletes without Steam", async () => {
  const f = fixture({ appid: 0 });
  await deleteDownloadedGame({ ...task, appid: 0 }, null, f.call, f.storage);
  assert.equal(f.actions[0].recovery_appid, 0);
  assert.equal(f.actions.at(-1).action, "delete_game");
  assert.equal(f.store.size, 0);
});
test("saved recovery ID also removes a partially imported shortcut", async () => {
  const f = fixture(); let exists = true;
  f.store.set("smb-download-appid:game", String(task.appid));
  await deleteDownloadedGame({ ...task, appid: 0 }, { RemoveShortcut: id => {
    assert.equal(id, task.appid); exists = false;
  } }, f.call, f.storage, { GetAppOverviewByAppID: () => exists });
  assert.equal(f.actions[0].recovery_appid, task.appid);
  assert.equal(f.actions.at(-1).action, "delete_game");
  assert.equal(f.store.size, 0);
});
test("Steam returning without removing the shortcut does not allow file deletion", async () => {
  const f = fixture();
  await assert.rejects(deleteDownloadedGame(task, { RemoveShortcut: () => {} }, f.call,
    f.storage, { GetAppOverviewByAppID: () => true }), /尚未移除/);
  assert.equal(f.actions.some(p => p.action === "delete_game"), false);
  assert.equal(f.actions.at(-1).action, "delete_failed");
});
