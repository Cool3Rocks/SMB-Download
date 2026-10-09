import { callable } from "@decky/api";
export type SmbDownloadSettings = {
  host: string; port: number; share: string; username: string;
  source_path: string; install_path: string; has_password: boolean; smb_available: boolean;
  smb_error?: string; python_version?: string; platform?: string; vendor_path?: string; vendor_exists?: boolean;
  keep_awake: boolean;
};
export type SmbDownloadEntry = { name: string; path: string; directory: boolean; size: number; blocked: boolean; mtime?: number };
export type SmbDownloadTask = {
  id: string; title: string; source: string; install_dir: string; status: string;
  total: number; completed: number; progress: number; current_file: string; error: string;
  exe: string; working_dir: string; launch_options: string; compat_tool: string; appid: number;
  steam_removed?: boolean;
};
const action = callable<[Record<string, unknown>], { status: string; data?: any; message?: string }>("smb_download_action");
export async function smbDownloadCall<T = any>(request: Record<string, unknown>): Promise<T> {
  const response = await action(request);
  if (response.status !== "success") throw new Error(response.message || "SMB 操作失败");
  return response.data as T;
}
