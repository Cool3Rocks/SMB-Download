import type { SmbDownloadTask } from "./smbDownloadApi";

export async function deleteDownloadedGame(
  task: SmbDownloadTask, apps: any, call: (payload: Record<string, unknown>) => Promise<any>,
  storage: Pick<Storage, "getItem" | "removeItem">,
  overview?: { GetAppOverviewByAppID: (appid: number) => unknown },
) {
  const key = `smb-download-appid:${task.id}`;
  const recovery = Number(storage.getItem(key) || 0);
  const pending: SmbDownloadTask = await call({ action: "begin_delete", id: task.id, recovery_appid: recovery });
  try {
    if (pending.appid && !pending.steam_removed) {
      if (!apps?.RemoveShortcut || !overview?.GetAppOverviewByAppID) {
        throw Error("Steam 移除或验证接口不可用，请在游戏模式中重试；本地文件已保留");
      }
      if (overview.GetAppOverviewByAppID(pending.appid)) {
        await apps.RemoveShortcut(pending.appid);
        for (let attempt = 0; attempt < 30 && overview.GetAppOverviewByAppID(pending.appid); attempt++) {
          await new Promise(resolve => setTimeout(resolve, 100));
        }
        if (overview.GetAppOverviewByAppID(pending.appid)) throw Error("Steam 条目尚未移除，本地文件已保留，请稍后重试删除");
      }
      await call({ action: "shortcut_removed", id: task.id });
    }
    await call({ action: "delete_game", id: task.id });
    storage.removeItem(key);
  } catch (error: any) {
    try { await call({ action: "delete_failed", id: task.id, message: error.message || "删除失败，请重试" }); } catch {}
    throw error;
  }
}
