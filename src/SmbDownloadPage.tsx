import { Children, Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentProps, CSSProperties, ReactNode } from "react";
import { ConfirmModal, showModal, DialogButton, Dropdown, Focusable, PanelSection, PanelSectionRow, TextField, ToggleField, Tabs } from "@decky/ui";
import { FileSelectionType, openFilePicker } from "@decky/api";
import { smbDownloadCall, SmbDownloadEntry, SmbDownloadSettings, SmbDownloadTask } from "./smbDownloadApi";
import { importSmbDownloadShortcut } from "./smbDownloadSteamImport";
import { PowerState } from "./smbDownloadPower";
import { deleteDownloadedGame } from "./smbDownloadDelete";
import SMBIcon from "./SMBIcon";

const emptySettings: SmbDownloadSettings = { host: "", port: 445, share: "", username: "", source_path: "",
  install_path: "/home/deck/Games", has_password: false, smb_available: false, keep_awake: true };
const labels: Record<string, string> = { scanning: "扫描文件", downloading: "下载中", pausing: "正在暂停",
  cancelling: "正在取消并清理临时文件", cancelled: "已取消，临时文件已清除", cancel_error: "临时文件清理失败",
  paused: "已暂停", error: "下载失败", downloaded: "等待入库", import_pending: "等待入库",
  importing: "等待完成入库", complete: "已添加到 Steam" };
labels.deleting = "正在删除";
labels.delete_error = "删除失败，可重试";
const size = (n: number) => n >= 1073741824 ? `${(n / 1073741824).toFixed(2)} GiB`
  : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MiB` : `${n} B`;
const parent = (p: string) => p.split("/").slice(0, -1).join("/");
const caption: CSSProperties = { fontSize: 12, lineHeight: "18px", color: "#b8c5d3" };
const card: CSSProperties = { width: "100%", borderRadius: 6, padding: "10px 12px",
  background: "rgba(255,255,255,0.04)", boxSizing: "border-box" };
const listButton: CSSProperties = { width: 104, flex: "0 0 104px", minWidth: 0, margin: 0,
  height: 44, minHeight: 44, borderRadius: 6, fontSize: 14, fontFamily: "inherit" };

function ActionRow({ children }: { children: ReactNode }) {
  const buttons = Children.toArray(children);
  if (!buttons.length) return null;
  return <Focusable flow-children="horizontal" style={{ display: "flex", flexWrap: "wrap", gap: 8,
    width: "100%", alignItems: "stretch", padding: "4px 0" }}>{buttons}</Focusable>;
}

function ActionButton({ prominent = false, ...props }: ComponentProps<typeof DialogButton> & { prominent?: boolean }) {
  const [focused, setFocused] = useState(false);
  const highlighted = focused && !props.disabled;
  return <DialogButton {...props}
    onGamepadFocus={event => { setFocused(true); props.onGamepadFocus?.(event); }}
    onGamepadBlur={event => { setFocused(false); props.onGamepadBlur?.(event); }}
    style={{ flex: "1 1 160px", minWidth: 0, minHeight: 44,
      height: "auto", padding: "10px 12px", margin: 0, borderRadius: 6, fontSize: 14,
      fontFamily: "inherit", lineHeight: "20px", boxSizing: "border-box", ...props.style,
      ...(prominent ? {
        background: highlighted ? "#FFFFFF" : "#167B94",
        color: highlighted ? "#102C38" : "#FFFFFF",
        boxShadow: highlighted ? "inset 0 0 0 3px #66D9FF" : "none",
        opacity: props.disabled ? 0.45 : 1,
      } : {}),
    }} />;
}

function Input({ label, value, onChange, password = false, description }: {
  label: string; value: string; onChange: (value: string) => void; password?: boolean; description?: string;
}) {
  return <PanelSectionRow><TextField label={label} description={description} value={value}
    bIsPassword={password} onChange={(event: any) => onChange(event.target.value)} /></PanelSectionRow>;
}

export default function SmbDownloadPage() {
  const [tab, setTab] = useState("browse");
  const [settings, setSettings] = useState(emptySettings);
  const [password, setPassword] = useState("");
  const [passwordEdited, setPasswordEdited] = useState(false);
  const [tasks, setTasks] = useState<SmbDownloadTask[]>([]);
  const [power, setPower] = useState<PowerState | null>(null);
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<SmbDownloadEntry[]>([]);
  const [folderSearch, setFolderSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [browseSort, setBrowseSort] = useState("name_asc");
  const [browsed, setBrowsed] = useState(false);
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [importTask, setImportTask] = useState<SmbDownloadTask | null>(null);
  const [localEntries, setLocalEntries] = useState<SmbDownloadEntry[]>([]);
  const [exe, setExe] = useState("");
  const [useProton, setUseProton] = useState(true);
  const [compatTool, setCompatTool] = useState("proton_experimental");
  const [log, setLog] = useState("");
  const [diagnostics, setDiagnostics] = useState("");
  const mounted = useRef(true);
  const operation = useRef(false);
  const page = useRef<HTMLDivElement>(null);
  const visibleEntries = useMemo(() => {
    const keyword = folderSearch.trim().normalize("NFC").toLocaleLowerCase();
    const compareName = (a: SmbDownloadEntry, b: SmbDownloadEntry) => a.name.localeCompare(b.name, "zh-CN", { numeric: true, sensitivity: "base" })
      || a.path.localeCompare(b.path, "zh-CN");
    return entries.filter(entry => entry.directory && (!keyword || entry.name.normalize("NFC").toLocaleLowerCase().includes(keyword)))
      .sort((a, b) => {
        if (a.directory !== b.directory) return a.directory ? -1 : 1;
        if (browseSort.startsWith("date_")) {
          const aTime = Number.isFinite(a.mtime) && a.mtime! > 0 ? a.mtime! : 0;
          const bTime = Number.isFinite(b.mtime) && b.mtime! > 0 ? b.mtime! : 0;
          if (!aTime || !bTime) return aTime === bTime ? compareName(a, b) : aTime ? -1 : 1;
          return (browseSort === "date_desc" ? bTime - aTime : aTime - bTime) || compareName(a, b);
        }
        return browseSort === "name_desc" ? compareName(b, a) : compareName(a, b);
      });
  }, [entries, folderSearch, browseSort]);

  const refresh = useCallback(async (loadSettings = false) => {
    const data = await smbDownloadCall<{ settings: SmbDownloadSettings; tasks: SmbDownloadTask[]; power: PowerState }>({ action: "state" });
    if (!mounted.current) return;
    setTasks(data.tasks);
    setPower(data.power);
    if (loadSettings) setSettings(data.settings);
  }, []);
  useEffect(() => {
    mounted.current = true;
    refresh(true).catch(e => { if (mounted.current) setError(e.message); });
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(); } catch (e: any) { if (mounted.current) setError(e.message); }
      if (mounted.current) timer = setTimeout(poll, 1500);
    };
    timer = setTimeout(poll, 1500);
    return () => { mounted.current = false; clearTimeout(timer); };
  }, [refresh]);

  async function run(fn: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    setBusy(true); setError(""); setMessage("");
    try { await fn(); } catch (e: any) { if (mounted.current) setError(e.message || "操作失败"); }
    finally { operation.current = false; if (mounted.current) setBusy(false); }
  }
  async function browse(nextPath: string) {
    const data = await smbDownloadCall<{ path: string; entries: SmbDownloadEntry[] }>({ action: "browse", path: nextPath });
    if (data.path !== path) { setFolderSearch(""); setSearchInput(""); }
    setPath(data.path); setEntries(data.entries); setBrowsed(true);
  }
  async function save() {
    const values: any = { ...settings };
    delete values.has_password; delete values.smb_available;
    if (passwordEdited) values.password = password;
    const saved = await smbDownloadCall<SmbDownloadSettings>({ action: "settings", settings: values });
    setSettings(saved); setPassword(""); setPasswordEdited(false);
    setBrowsed(false); setEntries([]); setPath(""); setFolderSearch(""); setSearchInput("");
    setMessage("设置已保存。");
  }
  async function startDownload(folder: string, name: string) {
    setTab("tasks");
    page.current?.scrollTo({ top: 0, behavior: "auto" });
    setMessage(`正在创建下载任务：${name}…`);
    const task = await smbDownloadCall<SmbDownloadTask>({ action: "start", path: folder, title: name });
    if (!mounted.current) return;
    // Show the returned task immediately; the regular poll will update its progress.
    setTasks(previous => [task, ...previous.filter(item => item.id !== task.id)]);
    setMessage(`已开始下载：${name}`);
  }
  async function editImport(task: SmbDownloadTask) {
    const data = await smbDownloadCall<{ entries: SmbDownloadEntry[] }>({ action: "local_browse", id: task.id, path: "" });
    setLocalEntries(data.entries.filter(entry => !entry.directory));
    setImportTask(task); setTitle(task.title); setExe(task.exe || "");
    setUseProton(Boolean(task.compat_tool)); setCompatTool(task.compat_tool || "proton_experimental");
  }
  async function addToSteam() {
    if (!importTask) return;
    const prepared = await smbDownloadCall<SmbDownloadTask & { executable: string; start_dir: string }>({
      action: "prepare_import", id: importTask.id, title, exe,
      launch_options: importTask.launch_options || "", compat_tool: useProton ? compatTool : "",
    });
    if (prepared.status === "complete") { setImportTask(null); await refresh(); return; }
    const apps = (window as any).SteamClient?.Apps;
    await importSmbDownloadShortcut(prepared, apps, smbDownloadCall, localStorage, (window as any).appStore);
    setImportTask(null); setMessage("已添加到 Steam。");
    await refresh();
  }

  function confirmDelete(task: SmbDownloadTask) {
    const appid = task.appid || Number(localStorage.getItem(`smb-download-appid:${task.id}`) || 0);
    const modal = showModal(<ConfirmModal strTitle={`删除游戏：${task.title}`}
      strDescription={<div style={{ overflowWrap: "anywhere" }}>
        <p>{appid ? "将移除该任务关联的 Steam 游戏条目，并删除本地游戏文件夹及任务记录。" : "将删除本地游戏文件夹及任务记录。"}</p>
        <p>{task.install_dir}</p><p>此操作不可恢复，SMB 源文件不会删除。</p>
      </div>}
      strOKButtonText="删除游戏和文件" strCancelButtonText="取消" bDestructiveWarning
      onCancel={() => modal.Close()} onOK={() => {
        modal.Close();
        if (importTask?.id === task.id) setImportTask(null);
        void run(async () => {
          try {
            await deleteDownloadedGame(task, (window as any).SteamClient?.Apps, smbDownloadCall, localStorage, (window as any).appStore);
            if (importTask?.id === task.id) setImportTask(null);
            setMessage(`已删除：${task.title}`);
          } finally { await refresh(); }
        });
      }} />);
  }

  const settingsTab = <><PanelSection title="连接">
    <Input label="SMB 地址" value={settings.host} onChange={host => setSettings(s => ({ ...s, host }))}
      description="IP 或主机名，如 192.168.1.10" />
    <Input label="SMB 端口" value={String(settings.port)} onChange={port => setSettings(s => ({ ...s, port: Number(port) }))} />
    <Input label="共享名称" value={settings.share} onChange={share => setSettings(s => ({ ...s, share }))} description="例如 Games" />
    <Input label="用户名" value={settings.username} onChange={username => setSettings(s => ({ ...s, username }))} />
    <Input label="密码" value={password} password onChange={value => { setPassword(value); setPasswordEdited(true); }}
      description={settings.has_password ? "已保存，未修改则保留" : "输入密码"} />
    {settings.has_password && <PanelSectionRow><ActionRow><ActionButton disabled={busy} style={{ flex: "0 1 auto" }}
      onClick={() => { setPassword(""); setPasswordEdited(true); }}>清除密码</ActionButton></ActionRow></PanelSectionRow>}
  </PanelSection>
  <PanelSection title="目录">
    <Input label="SMB 游戏源目录" value={settings.source_path} onChange={source_path => setSettings(s => ({ ...s, source_path }))}
      description="共享内路径，留空为根目录" />
    <Input label="本地安装目录" value={settings.install_path} onChange={install_path => setSettings(s => ({ ...s, install_path }))} />
    <PanelSectionRow><ActionRow>
      <ActionButton disabled={busy} onClick={() => run(async () => {
        const chosen = await openFilePicker(FileSelectionType.FOLDER, settings.install_path || "/home/deck", false, true);
        const value = chosen?.realpath || chosen?.path;
        if (value) setSettings(s => ({ ...s, install_path: value }));
      })}>选择安装目录</ActionButton>
    </ActionRow></PanelSectionRow>
  </PanelSection>
  <PanelSection title="下载">
    <PanelSectionRow><ToggleField label="下载时保持亮屏" checked={settings.keep_awake}
      description="下载结束后恢复原电源设置。"
      onChange={keep_awake => setSettings(s => ({ ...s, keep_awake }))} /></PanelSectionRow>
    {(power?.active || power?.error) && <PanelSectionRow><div style={caption}>{power.error ? `亮屏控制失败：${power.error}` : "正在保持亮屏"}</div></PanelSectionRow>}
    <PanelSectionRow><ActionRow>
      <ActionButton disabled={busy} onClick={() => run(save)}>保存设置</ActionButton>
      <ActionButton disabled={busy} onClick={() => run(async () => {
      await save(); await browse(""); setMessage("已连接。"); setTab("browse");
      })}>保存并连接</ActionButton>
    </ActionRow></PanelSectionRow>
  </PanelSection>
  <PanelSection title="诊断">
    <PanelSectionRow><ActionRow><ActionButton disabled={busy} onClick={() => run(async () => {
      const data = await smbDownloadCall<{ log: string }>({ action: "log" }); setLog(data.log);
    })}>查看日志</ActionButton>
    <ActionButton disabled={busy} onClick={() => run(async () => {
      const data = await smbDownloadCall({ action: "diagnostics" });
      setDiagnostics(JSON.stringify(data, null, 2)); await refresh(true);
    })}>检查 SMB 依赖</ActionButton></ActionRow></PanelSectionRow>
    {diagnostics && <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, overflowWrap: "anywhere", userSelect: "text" }}>{diagnostics}</pre>}
    {log && <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, overflowWrap: "anywhere" }}>{log}</pre>}
  </PanelSection></>;

  const browserTab = <PanelSection title="文件夹">
    <PanelSectionRow><div style={{ ...card, display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ ...caption, flexShrink: 0 }}>当前目录</span>
      <span title={`/${path}`} style={{ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>/{path}</span>
      {browsed && <span style={{ ...caption, flexShrink: 0 }}>{visibleEntries.length} 个文件夹</span>}
    </div></PanelSectionRow>
    <PanelSectionRow><ActionRow>
      <ActionButton prominent style={{ flex: "1 1 100%", width: "100%", minHeight: 44, fontWeight: 600 }} disabled={busy}
        onClick={() => run(() => browse(path))}>{browsed ? "刷新目录" : "连接并浏览"}</ActionButton>
    </ActionRow></PanelSectionRow>
    <PanelSectionRow><div style={{ width: "100%" }}>
      <div style={{ marginBottom: 4 }}>搜索文件夹</div>
      <Focusable flow-children="horizontal" style={{ display: "flex", flexWrap: "wrap", gap: 8, width: "100%", alignItems: "center" }}>
        <div style={{ flex: "1 1 180px", minWidth: 0 }}><TextField aria-label="搜索文件夹" value={searchInput}
          onChange={event => setSearchInput(event.target.value)} style={{ width: "100%", boxSizing: "border-box" }} /></div>
        <ActionButton style={{ flex: "0 0 104px", width: 104 }} disabled={busy || !browsed}
          onClick={() => setFolderSearch(searchInput.trim())}>搜索</ActionButton>
        <ActionButton style={{ flex: "0 0 104px", width: 104 }} disabled={!searchInput && !folderSearch}
          onClick={() => { setFolderSearch(""); setSearchInput(""); }}>取消</ActionButton>
        <div style={{ flex: "0 0 180px", minWidth: 0 }}><Dropdown menuLabel="排序" selectedOption={browseSort}
          onChange={option => setBrowseSort(option.data)} rgOptions={[
            { data: "name_asc", label: "名称排序：升序" }, { data: "name_desc", label: "名称排序：降序" },
            { data: "date_desc", label: "日期排序：最新优先" }, { data: "date_asc", label: "日期排序：最早优先" },
          ]} /></div>
      </Focusable>

    </div></PanelSectionRow>
    {path && <PanelSectionRow><ActionRow>
      <ActionButton disabled={busy} onClick={() => run(() => startDownload(path, path.split("/").pop() || path))}>
        下载当前文件夹
      </ActionButton>
      <ActionButton disabled={busy} onClick={() => run(() => browse(parent(path)))}>返回上级目录</ActionButton>
      <ActionButton disabled={busy} onClick={() => {
        setSettings(s => ({ ...s, source_path: [s.source_path, path].filter(Boolean).join("/") }));
        setTab("settings"); setMessage("源目录已选择，请保存。");
      }}>设为游戏源目录</ActionButton>
    </ActionRow></PanelSectionRow>}
    {visibleEntries.length > 0 && <PanelSectionRow><div style={{ display: "flex", flexDirection: "column", gap: 3, width: "100%" }}>
    {visibleEntries.map(entry => <div key={entry.path} style={{ width: "100%", border: "1px dashed rgba(184,197,211,0.32)",
      background: "rgba(255,255,255,0.025)", borderRadius: 6, padding: "6px 8px", boxSizing: "border-box" }}>
      {entry.directory ? <Focusable flow-children="horizontal" style={{ display: "flex", gap: 10, width: "100%", alignItems: "center" }}>
        <div style={{ flex: "1 1 0", minWidth: 0 }}>
          <span title={entry.name} style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {entry.name}{entry.blocked ? "（不支持链接）" : ""}
          </span>
          {browseSort.startsWith("date_") && <span style={{ display: "block", fontSize: 12, opacity: 0.7 }}>
            {entry.mtime ? new Date(entry.mtime / 1000000).toLocaleString("zh-CN", { hour12: false }) : "未知"}
          </span>}
        </div>
        <DialogButton disabled={busy || entry.blocked} style={listButton}
          onClick={() => run(() => browse(entry.path))} onOKActionDescription="打开文件夹">打开</DialogButton>
        <DialogButton disabled={busy || entry.blocked} style={listButton}
          onClick={() => run(() => startDownload(entry.path, entry.name))} onOKActionDescription="下载文件夹">
          下载
        </DialogButton>
      </Focusable> : <div style={{ display: "flex", gap: 10, width: "100%", alignItems: "center", padding: "4px 0" }}>
        <span title={entry.name} style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          文件：{entry.name}{entry.blocked ? "（不支持链接）" : ""}
        </span>
        <span style={{ flexShrink: 0, opacity: 0.7 }}>{size(entry.size)}</span>
      </div>}
    </div>)}
    </div></PanelSectionRow>}
    {browsed && !visibleEntries.length && <PanelSectionRow>{folderSearch.trim()
      ? "没有匹配的文件夹"
      : "暂无子文件夹"}</PanelSectionRow>}
  </PanelSection>;

  const importPanel = importTask && <PanelSection title="添加到 Steam">
      <Input label="Steam 游戏名称" value={title} onChange={setTitle} />
      <Input label="启动程序" value={exe} onChange={setExe} description="选择下方 EXE，或输入相对路径" />
      {localEntries.length > 0 && <PanelSectionRow><div style={{ display: "flex", flexDirection: "column", gap: 3, width: "100%" }}>
      {localEntries.map(entry =>
        <Focusable key={entry.path} flow-children="horizontal" style={{ display: "flex", gap: 10, width: "100%", alignItems: "center",
          border: `1px dashed ${exe === entry.path ? "#66D9FF" : "rgba(184,197,211,0.32)"}`,
          background: exe === entry.path ? "rgba(22,123,148,0.18)" : "rgba(255,255,255,0.025)", borderRadius: 6, padding: "6px 8px", boxSizing: "border-box" }}>
          <div style={{ flex: "1 1 0", minWidth: 0 }}>
            <div style={{ overflowWrap: "anywhere" }}>{entry.name}{exe === entry.path ? "（已选择）" : ""}</div>
            <div style={{ fontSize: 12, opacity: 0.7, overflowWrap: "anywhere" }}>{entry.path}</div>
          </div>
          <DialogButton disabled={busy} style={listButton} onOKActionDescription="选择启动程序"
            onClick={() => { setExe(entry.path); setUseProton(true); }}>选择</DialogButton>
        </Focusable>)}
      </div></PanelSectionRow>}
      {!localEntries.length && <PanelSectionRow>未找到 EXE 文件</PanelSectionRow>}
      <PanelSectionRow><ToggleField label="使用 Proton" checked={useProton} onChange={setUseProton} /></PanelSectionRow>
      {useProton && <Input label="Proton 内部名称" value={compatTool} onChange={setCompatTool} description="可在 Steam 属性中调整" />}
      <PanelSectionRow><ActionRow>
        <ActionButton disabled={busy || !exe.trim() || !title.trim() || (useProton && !compatTool.trim())}
          onClick={() => run(addToSteam)}>添加到 Steam</ActionButton>
        <ActionButton disabled={busy} onClick={() => setImportTask(null)}>关闭设置</ActionButton>
      </ActionRow></PanelSectionRow>
    </PanelSection>;

  const taskTab = <>
    <PanelSection title="下载任务">
      {(power?.active || power?.error) && <PanelSectionRow>{power.error
        ? `亮屏控制失败：${power.error}` : "正在保持亮屏"}</PanelSectionRow>}
      {!tasks.length && <PanelSectionRow>暂无下载任务</PanelSectionRow>}
      {tasks.map(task => <Fragment key={task.id}><PanelSectionRow><div style={card}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 4 }}>
          <span style={{ flex: 1, minWidth: 0, fontWeight: 600, overflowWrap: "anywhere" }}>{task.title}</span>
          <span style={{ ...caption, flexShrink: 0, borderRadius: 6, padding: "3px 8px", background: "rgba(255,255,255,0.06)",
            color: task.status === "complete" ? "#a8e6bd" : "#b8c5d3" }}>{labels[task.status] || task.status}</span>
        </div>
        <div title={`${task.source} → ${task.install_dir}`} style={{ ...caption, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{task.install_dir}</div>
        {!["complete", "cancelled"].includes(task.status) && <><div>{size(task.completed)} / {size(task.total)} · {task.progress}%</div>
          <progress value={task.completed} max={task.total || 1} style={{ width: "100%", height: 6, accentColor: "#66D9FF", margin: "6px 0" }} />
          <div title={task.current_file} style={{ ...caption, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{task.current_file}</div></>}
        {task.error && <div style={{ color: "#ffb6b6" }}>{task.error}</div>}
        <ActionRow>
        {["scanning", "downloading", "pausing"].includes(task.status) && <ActionButton disabled={busy || task.status === "pausing"}
          onClick={() => run(async () => { await smbDownloadCall({ action: "pause", id: task.id }); await refresh(); })}>暂停下载</ActionButton>}
        {["paused", "error"].includes(task.status) && <ActionButton disabled={busy}
          onClick={() => run(async () => { await smbDownloadCall({ action: "resume", id: task.id }); await refresh(); })}>继续下载</ActionButton>}
        {["scanning", "downloading", "pausing", "paused", "error", "cancelling", "cancel_error"].includes(task.status) &&
          <ActionButton disabled={busy || task.status === "cancelling"} onClick={() => run(async () => {
            const result = await smbDownloadCall<SmbDownloadTask>({ action: "cancel", id: task.id });
            await refresh();
            if (result.status === "cancel_error") throw new Error(result.error);
            setMessage(result.status === "cancelled" ? "已取消，临时文件已清理。" : "正在取消并清理…");
          })}>{task.status === "cancel_error" ? "重试清理临时文件" : "取消并清理"}</ActionButton>}
        {["downloaded", "import_pending", "importing"].includes(task.status) && <ActionButton disabled={busy}
          onClick={() => run(() => editImport(task))}>设置启动程序</ActionButton>}
        {task.status === "complete" && <ActionButton disabled={busy}
          onClick={() => run(() => editImport(task))}>修正 Steam 启动设置</ActionButton>}
        {["downloaded", "import_pending", "importing", "complete", "deleting", "delete_error"].includes(task.status) &&
          <ActionButton disabled={busy} onClick={() => confirmDelete(task)}>
            {["deleting", "delete_error"].includes(task.status) ? "重试删除游戏" : "删除游戏"}
          </ActionButton>}
        </ActionRow>
      </div></PanelSectionRow>
      {importTask?.id === task.id && <div style={{ ...card, margin: "3px 0 12px", borderLeft: "2px solid #167B94" }}>{importPanel}</div>}
      </Fragment>)}
    </PanelSection>

  </>;

  return <div ref={page} style={{ padding: "24px 20px 16px", fontSize: 14, lineHeight: "20px", height: "calc(100vh - 64px)", boxSizing: "border-box", overflowY: "auto" }}>
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
      <SMBIcon width={40} height={40} style={{ flexShrink: 0 }} />
      <h2 style={{ margin: 0, fontSize: 20, lineHeight: "28px", fontWeight: 600 }}>SMB Download</h2>
      <span style={{ ...caption, marginLeft: "auto" }}>0.3.29</span>
    </div>
    {error && <div role="alert" style={{ ...card, color: "#ffb4ab", margin: "8px 0", whiteSpace: "pre-wrap" }}>{error}</div>}
    {message && <div role="status" style={{ ...card, color: "#a8e6bd", margin: "8px 0" }}>{message}</div>}
    {busy && <div role="status">处理中…</div>}
    {!settings.smb_available && <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
      <p style={{ color: "#ffb4ab" }}>SMB 依赖不可用，请在设置中检查。</p>
      {settings.smb_error && <p>{settings.smb_error}</p>}
    </div>}
    <Tabs activeTab={tab} onShowTab={setTab} tabs={[
      { id: "browse", title: "浏览 SMB", content: browserTab },
      { id: "tasks", title: "下载任务", content: taskTab },
      { id: "settings", title: "设置", content: settingsTab },
    ]} />
  </div>;
}
