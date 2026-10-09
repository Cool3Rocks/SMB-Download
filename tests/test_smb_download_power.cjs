const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const ts = require("typescript");
const { test } = require("node:test");
const compiled = new Module("smbDownloadPower");
compiled._compile(ts.transpileModule(fs.readFileSync("src/smbDownloadPower.ts", "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, "smbDownloadPower.cjs");
const { applyPowerTimers, DownloadPowerController } = compiled.exports;
const original = [123, 456, 789, 0];
function fixture(snapshot = null) {
  const state = { wanted: true, snapshot, active: false, error: "" };
  const applied = [];
  const call = async p => {
    if (p.action === "power_state") return { ...state };
    if (p.action === "power_begin") return state.snapshot ??= [...original];
    if (p.action === "power_report") {
      state.active = !!p.active; state.error = p.error || "";
      if (p.restored) state.snapshot = null;
    }
  };
  return { state, applied, call, controller: new DownloadPowerController(call, async v => applied.push([...v])) };
}
test("all terminal download conditions restore exact values", async () => {
  for (const terminal of ["downloaded", "paused", "cancelled", "error"]) {
    const f = fixture();
    await f.controller.tick(); await f.controller.tick();
    assert.deepEqual(f.applied, [[0, 0, 0, 0]], terminal);
    f.state.wanted = false;
    await f.controller.tick();
    assert.deepEqual(f.applied.at(-1), original, terminal);
    assert.equal(f.state.snapshot, null);
    assert.equal(f.state.active, false);
  }
});
test("restart recovers pending snapshot before clearing journal", async () => {
  const f = fixture([...original]); f.state.wanted = false;
  await f.controller.tick();
  assert.deepEqual(f.applied, [original]);
  assert.equal(f.state.snapshot, null);
});
test("unload restores timers and stops polling", async () => {
  const f = fixture();
  const tick = f.controller.tick();
  await tick;
  await f.controller.stop(); await f.controller.tick();
  assert.deepEqual(f.applied, [[0, 0, 0, 0], original]);
  assert.equal(f.state.snapshot, null);
});
test("unload waits for an in-flight Steam override before restoring", async () => {
  const f = fixture(); const applied = [];
  let unblock;
  const blocked = new Promise(resolve => { unblock = resolve; });
  let entered;
  const ready = new Promise(resolve => { entered = resolve; });
  const c = new DownloadPowerController(f.call, async values => {
    applied.push([...values]);
    if (values.every(v => v === 0)) { entered(); await blocked; }
  });
  const ticking = c.tick(); await ready;
  const stopping = c.stop(); unblock();
  await Promise.all([ticking, stopping]);
  assert.deepEqual(applied, [[0, 0, 0, 0], original]);
  assert.equal(f.state.snapshot, null);
});
test("partial Steam failure restores and retains journal if restore fails", async () => {
  const f = fixture(); let failed = true;
  const applied = [];
  const c = new DownloadPowerController(f.call, async values => {
    applied.push([...values]); if (failed) throw Error("Steam unavailable");
  });
  await c.tick();
  assert.deepEqual(f.state.snapshot, original);
  assert.deepEqual(applied, [[0, 0, 0, 0], original]);
  failed = false; f.state.wanted = false;
  await c.tick();
  assert.equal(f.state.snapshot, null);
});
test("backend failure releases local override while retaining recovery", async () => {
  const f = fixture(); let offline = false;
  const applied = [];
  const c = new DownloadPowerController(async p => {
    if (offline) throw Error("offline"); return f.call(p);
  }, async values => applied.push([...values]));
  await c.tick(); offline = true;
  await c.tick();
  assert.deepEqual(applied, [[0, 0, 0, 0], original]);
  assert.deepEqual(f.state.snapshot, original);
});
test("older and newer Steam protobuf fields preserve seconds", async () => {
  const messages = [];
  await applyPowerTimers({ System: { RegisterForOnSuspendRequest() {}, UpdateSettings: async p => messages.push(Buffer.from(p, "base64")) } }, original);
  assert.equal(messages[0][0], 13);
  assert.equal(messages[0].readFloatLE(1), 123);
  assert.equal(messages[1][0], 29);
  assert.equal(messages[1].readFloatLE(1), 789);
  const modern = [];
  await applyPowerTimers({ System: { UpdateSettings: async () => {} }, Settings: { SetSetting: async p => modern.push(Buffer.from(p, "base64")) } }, original);
  assert.deepEqual([...modern[0]], [152, 220, 11, 149, 6, 160, 220, 11, 0]);
});
