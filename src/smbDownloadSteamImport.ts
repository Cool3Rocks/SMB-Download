import type { SmbDownloadTask } from "./smbDownloadApi";

function quotedPath(path: string): string {
  return path.startsWith('"') && path.endsWith('"') ? path : `"${path}"`;
}

export async function importSmbDownloadShortcut(
  task: SmbDownloadTask & { executable: string; start_dir: string },
  apps: any,
  call: (payload: Record<string, unknown>) => Promise<any>,
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem">,
  overview?: { GetAppOverviewByAppID: (appid: number) => unknown },
) {
  if (task.status === "complete") return;
  if (!apps?.AddShortcut) throw new Error("Steam 入库接口不可用，请在 Steam Deck 游戏模式中操作。");
  if (!overview?.GetAppOverviewByAppID) throw new Error("Steam 游戏库接口尚未就绪，请稍后重试入库。");
  if (!apps.SetShortcutExe || !apps.SetShortcutStartDir || !apps.SetShortcutName) {
    throw new Error("Steam 启动设置接口不可用，请稍后重试入库。");
  }
  if (task.compat_tool && !apps.SpecifyCompatTool) throw new Error("当前 Steam 无法设置 Proton，请取消自动设置后在 Steam 属性中手动配置。");
  const executable = quotedPath(task.executable);
  const startDir = quotedPath(task.start_dir);
  const recoveryKey = `smb-download-appid:${task.id}`;
  let appid = task.appid || Number(storage.getItem(recoveryKey) || 0);
  if (!appid) {
    // Some Steam builds ignore these fields during creation. Apply them only
    // after the native app store has registered the new shortcut.
    const created = Number(await apps.AddShortcut(task.title, executable, "", ""));
    if (!Number.isInteger(created) || !(created >>> 0)) throw new Error("Steam 未返回有效快捷方式 ID，请检查 Steam 库后重试。");
    appid = created >>> 0;
    // Keep a recovery ID even if the backend response is lost after saving.
    storage.setItem(recoveryKey, String(appid));
  }
  await call({ action: "record_appid", id: task.id, appid });
  const deadline = Date.now() + 5000;
  while (!overview.GetAppOverviewByAppID(appid)) {
    if (Date.now() >= deadline) {
      throw new Error("Steam 游戏库同步超时，任务已保留。稍后重试入库，不会重复创建。");
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (apps.SetShortcutName) await apps.SetShortcutName(appid, task.title);
  if (apps.SetShortcutExe) await apps.SetShortcutExe(appid, executable);
  if (apps.SetShortcutStartDir) await apps.SetShortcutStartDir(appid, startDir);
  if (apps.SetAppLaunchOptions) await apps.SetAppLaunchOptions(appid, task.launch_options);
  else if (apps.SetShortcutLaunchOptions) await apps.SetShortcutLaunchOptions(appid, task.launch_options);
  else if (task.launch_options) throw new Error("Steam 启动参数接口不可用，任务已保留，请稍后重试。");
  if (task.compat_tool) await apps.SpecifyCompatTool(appid, task.compat_tool);
  await call({ action: "finish_import", id: task.id });
  storage.removeItem(recoveryKey);
}
