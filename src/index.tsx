import { definePlugin, routerHook } from "@decky/api";
import { ButtonItem, Navigation, PanelSection, PanelSectionRow } from "@decky/ui";
import SmbDownloadPage from "./SmbDownloadPage";
import { smbDownloadCall } from "./smbDownloadApi";
import { applyPowerTimers, DownloadPowerController } from "./smbDownloadPower";
import SMBIcon from "./SMBIcon";
const SMB_ROUTE = "/smb-download/smb";
export default definePlugin(() => {
  const power = new DownloadPowerController(smbDownloadCall, values => applyPowerTimers((window as any).SteamClient, values));
  let mounted = true;
  let timer: ReturnType<typeof setTimeout>;
  const pollPower = async () => {
    await power.tick();
    if (mounted) timer = setTimeout(pollPower, 1500);
  };
  void pollPower();
  routerHook.addRoute(SMB_ROUTE, SmbDownloadPage);
  return {
    name: "SMB Download", title: "SMB Download", icon: <SMBIcon monochrome width="1em" height="1em"
      style={{ display: "block", flexShrink: 0 }} />,
    content: <PanelSection title="SMB 下载"><PanelSectionRow>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <SMBIcon width={36} height={36} style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 16, lineHeight: "24px", fontWeight: 600 }}>SMB Download</span>
      </div>
      <ButtonItem layout="below" onClick={() => Navigation.Navigate(SMB_ROUTE)}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><SMBIcon width={24} height={24} />打开 SMB 下载</span>
      </ButtonItem>
      </PanelSectionRow></PanelSection>,
    onDismount() {
      mounted = false; clearTimeout(timer); void power.stop();
      routerHook.removeRoute(SMB_ROUTE);
    },
  };
});
