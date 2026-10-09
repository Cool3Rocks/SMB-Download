export type PowerState = { wanted: boolean; snapshot: number[] | null; active: boolean; error: string };

// Steam's system settings protobuf uses fixed32 seconds; newer clients moved
// automatic suspend to Settings.SetSetting with integer fields 24003/24004.
export function encodeTimer(field: number, seconds: number, integer = false): string {
  if (!Number.isInteger(seconds) || seconds < 0 || seconds > 86400) throw Error("无效的电源计时");
  const bytes: number[] = [];
  const varint = (n: number) => {
    do { const low = n & 127; n >>>= 7; bytes.push(low | (n ? 128 : 0)); } while (n);
  };
  varint(field * 8 + (integer ? 0 : 5));
  if (integer) varint(seconds);
  else {
    const buffer = new ArrayBuffer(4);
    new DataView(buffer).setFloat32(0, seconds, true);
    bytes.push(...new Uint8Array(buffer));
  }
  return String.fromCharCode(...bytes);
}

export async function applyPowerTimers(client: any, values: number[]): Promise<void> {
  if (!client?.System?.UpdateSettings || values.length !== 4) throw Error("当前 Steam 客户端不支持下载亮屏控制");
  const modern = !client.System.RegisterForOnSuspendRequest;
  if (modern && !client.Settings?.SetSetting) throw Error("当前 Steam 客户端缺少自动休眠设置接口");
  const encode = (field: number, value: number, integer = false) => encodeTimer(field, value, integer);
  const idle = encode(1, values[0]) + encode(2, values[1]);
  const suspend = encode(modern ? 24003 : 3, values[2], modern) + encode(modern ? 24004 : 4, values[3], modern);
  await client.System.UpdateSettings(btoa(idle));
  if (modern) await client.Settings.SetSetting(btoa(suspend));
  else await client.System.UpdateSettings(btoa(suspend));
}

export class DownloadPowerController {
  private snapshot: number[] | null = null;
  private applied = false;
  private stopped = false;
  private pending: Promise<void> = Promise.resolve();
  constructor(private call: (payload: any) => Promise<any>, private apply: (values: number[]) => Promise<void>) {}

  tick(): Promise<void> {
    this.pending = this.pending.then(async () => {
      if (this.stopped) return;
      try {
        const state: PowerState = await this.call({ action: "power_state" });
        this.snapshot = state.snapshot;
        if (state.wanted && !this.stopped) {
          if (!this.applied) {
            this.snapshot = await this.call({ action: "power_begin" });
            await this.apply([0, 0, 0, 0]);
            this.applied = true;
          }
          await this.call({ action: "power_report", active: true });
        } else await this.restore();
      } catch (error: any) {
        console.warn("[SMB Download] 下载亮屏控制失败", error);
        // If the backend disappears, release the local override as well. Keep
        // the journal until restoration is acknowledged, for restart recovery.
        if (this.snapshot) {
          try { await this.restore(); } catch {}
        }
        try { await this.call({ action: "power_report", active: this.applied, error: error.message || String(error) }); } catch {}
      }
    });
    return this.pending;
  }

  private async restore() {
    if (!this.snapshot) return;
    await this.apply(this.snapshot);
    this.applied = false;
    // Retain the recovery journal if either Steam call or the RPC fails.
    await this.call({ action: "power_report", restored: true });
    this.snapshot = null;
  }

  async stop(): Promise<void> {
    this.stopped = true;
    await this.pending;
    try { await this.restore(); } catch (error) { console.warn("[SMB Download] 电源设置待恢复", error); }
  }
}
